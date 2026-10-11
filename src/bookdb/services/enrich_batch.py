"""批量元数据补全（PRD §12 扩展）。

用途：库里已有记录只填了书名（书来自 `book scan` 的**文件名解析**），
缺作者 / 出版社 / 年份 / ISBN —— 逐条找源 PDF 跑 VLM 版权页提取，补全后写回。

两种来源：
  - 指定目录：递归找 PDF，按**解析后的绝对路径**匹配库内记录（扫描后新增的书走这条）；
  - 全库：遍历所有「有 file_path 且元数据不全」的记录（不依赖目录）。

设计原则（与 §10 导入预览一致）：
  - **先预览后写入** —— `build_batch_plan()` 只读 + 跑模型，**不写库**；
    `apply_batch_plan()` 才写，且支持只写用户勾选的子集；
  - 失败可续 —— 单条失败只记 `error`，不影响其余；已补全的自动跳过（幂等）。

注意：**逐条跑模型很慢**（实测 6~15 秒/本），故本模块只提供**同步**计划构建，
由调用方（CLI / GUI）自行决定分批与进度展示。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import paths as paths_mod
from ..db import dao
from ..services.context import Context
from ..services.enrich import CopyrightPageNotFound, enrich_from_pdf
from ..services.llm import ModelUnavailableError

#: 判定「值得跑 VLM 补全」的字段。
#:
#: ⚠️ 只放**版权页一定能提供、且文件名通常拿不到**的字段。
#: 刻意**排除 publication_year** —— 很多版权页不含出版年（实测：改造的诞生有、
#: 美国社会主义传统没有），把它算进"缺失"会导致每次 `--missing` 都重复跑同一批书
#: （补不上 → 下次仍缺失 → 无限重试）。年份缺失是常态，不作为触发条件。
#: 前端「缺失字段」列仍会展示年份，供人工判断是否用 `--pdf` 单独补。
KEY_FIELDS = ("author", "publisher_name")

#: 缺这些就算「元数据不全」（含年份，仅用于**展示**给用户看，不用于触发重跑）。
DISPLAY_FIELDS = ("author", "publisher_name", "publication_year")

#: 从文件名能可靠拿到的字段 —— 扫描建库已填，不算缺失。
_ALREADY_FROM_FILENAME = ("title", "file_path")


@dataclass
class BatchItem:
    """一条待补全记录。"""

    book_id: int
    title: str
    file_path: str                      # 库内存储值（相对或绝对）
    pdf: Path | None                    # 解析后的绝对路径；文件不存在为 None
    missing: list[str] = field(default_factory=list)   # 缺哪些关键字段
    metadata: dict | None = None        # 提取到的元数据（成功时）
    page: int | None = None
    error: str | None = None
    skipped: str | None = None          # 跳过原因（已补全 / 无文件 / 文件不存在）

    @property
    def ok(self) -> bool:
        return self.metadata is not None and not self.error


@dataclass
class BatchPlan:
    """批量补全计划（只读产物）。"""

    items: list[BatchItem]
    unresolved_files: list[Path] = field(default_factory=list)  # 目录里没匹配到记录的 PDF

    @property
    def todo(self) -> list[BatchItem]:
        return [i for i in self.items if not i.skipped]

    @property
    def done(self) -> list[BatchItem]:
        return [i for i in self.items if i.metadata is not None]

    @property
    def failed(self) -> list[BatchItem]:
        return [i for i in self.items if i.error]

    @property
    def skipped(self) -> list[BatchItem]:
        return [i for i in self.items if i.skipped]


def missing_fields(book: dict) -> list[str]:
    """列出该记录缺失的**展示用**字段（含年份，供用户看）。"""
    return [f for f in DISPLAY_FIELDS if not book.get(f)]


def needs_enrich(book: dict) -> list[str]:
    """列出触发补全的字段（不含年份 —— 见 `KEY_FIELDS` 的说明）。"""
    return [f for f in KEY_FIELDS if not book.get(f)]


def _resolve(ctx: Context, stored: str | None) -> Path | None:
    """库内路径 → 绝对路径；解析失败返回 None。"""
    if not stored:
        return None
    try:
        return paths_mod.resolve(stored, ctx.config.library_root)
    except Exception:  # noqa: BLE001 - library_root 缺失等
        return None


def _record_title(book: dict) -> str:
    return book.get("title") or f"#{book.get('id')}"


def build_batch_plan(
    ctx: Context,
    *,
    books: list[dict],
    directory: str | Path | None = None,
    limit: int | None = None,
    recursive: bool = True,
) -> BatchPlan:
    """构建批量补全计划（**不写库**）。

    参数
      books     —— 目标记录（已由调用方按需筛选）
      directory —— 给了就只处理「文件落在该目录下」的记录，并回报没匹配上的 PDF
      limit     —— 最多处理几条（防止一次跑太久）
    """
    from . import scan as scan_service          # 局部导入，避免循环

    directory_path = Path(directory).expanduser() if directory else None
    matched: set[Path] = set()
    items: list[BatchItem] = []

    for book in books:
        pdf = _resolve(ctx, book.get("file_path"))
        title = _record_title(book)
        missing = needs_enrich(book)          # 触发条件（不含年份）
        display = missing_fields(book)        # 展示用（含年份）

        if not missing:
            items.append(BatchItem(book["id"], title, book.get("file_path") or "",
                                   pdf, display, skipped="元数据已齐"))
            continue
        if pdf is None:
            items.append(BatchItem(book["id"], title, book.get("file_path") or "",
                                   None, display, skipped="无文件关联"))
            continue
        if not pdf.is_file():
            items.append(BatchItem(book["id"], title, str(book.get("file_path")),
                                   pdf, display, error=f"文件不存在：{pdf}"))
            continue
        if directory_path is not None:
            if not _under(pdf, directory_path):
                continue                                # 不在指定目录内 → 不处理
            if not recursive and pdf.parent.resolve() != directory_path.resolve():
                continue                                # 不递归时只要目录直属的文件
        if pdf.suffix.lower() != ".pdf":
            items.append(BatchItem(book["id"], title, str(book.get("file_path")),
                                   pdf, display, skipped="非 PDF"))
            continue

        matched.add(pdf)
        item = BatchItem(book["id"], title, str(book.get("file_path")), pdf, display)
        items.append(item)
        if limit is not None and len(items) >= limit:
            break

    unresolved: list[Path] = []
    if directory_path is not None:
        for pdf in scan_service.scan_directory(directory_path, recursive=recursive):
            if pdf not in matched:
                unresolved.append(pdf)

    return BatchPlan(items=items, unresolved_files=unresolved)


def _under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def run_item(ctx: Context, item: BatchItem) -> BatchItem:
    """对单条跑 VLM 补全（**不写库**），结果就地更新 `item` 并返回。"""
    if item.skipped or item.pdf is None:
        return item
    try:
        result = enrich_from_pdf(ctx.config, item.pdf)
    except (CopyrightPageNotFound, ModelUnavailableError) as exc:
        item.error = str(exc)
    except (ValueError, RuntimeError) as exc:            # noqa: BLE001
        item.error = f"{type(exc).__name__}: {exc}"
    else:
        item.metadata = result.metadata
        item.page = result.page
    return item


def apply_batch_plan(
    ctx: Context,
    plan: BatchPlan,
    *,
    selected: set[int] | None = None,
    overwrite: bool = True,
) -> dict:
    """把计划里**已提取成功**的条目写回库。

    selected —— book_id 集合；None 表示全部成功的。
    overwrite —— 是否用提取值覆盖已有非空字段（默认覆盖；只填空字段见 PRD §12.5 精神）。
    返回 {'updated': [...], 'failed': [...]}。
    """
    updated: list[dict] = []
    failed: list[dict] = []

    for item in plan.done:
        if selected is not None and item.book_id not in selected:
            continue
        patch = _patch_for(ctx, item, overwrite=overwrite)
        if not patch:
            continue
        try:
            dao.update_book_fields(ctx.conn, item.book_id, patch)
        except Exception as exc:                          # noqa: BLE001
            failed.append({"id": item.book_id, "error": f"{type(exc).__name__}: {exc}"})
        else:
            updated.append({"id": item.book_id, "title": item.title, "fields": sorted(patch)})

    ctx.conn.commit()
    return {"updated": updated, "failed": failed}


def _patch_for(ctx: Context, item: BatchItem, *, overwrite: bool) -> dict:
    """算出该条要写哪些字段。``overwrite=False`` 时只填补空缺字段。"""
    meta = dict(item.metadata or {})
    meta.pop("file_path", None)                           # 不动路径
    if overwrite:
        return {k: v for k, v in meta.items() if v is not None}

    current = dao.get_book(ctx.conn, item.book_id) or {}
    return {
        k: v
        for k, v in meta.items()
        if v is not None and not current.get(k)
    }