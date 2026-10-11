"""目录扫描建库（PRD 扩展 —— §25 原列为"不进入 Demo"，本次经确认加入）。

**阶段 1（本模块）**：扫描目录 → 文件名解析 → 生成建库计划（**不写库**）。
**阶段 2（后续）**：VLM 版权页补全元数据。

规则：
  - 递归子目录；
  - 第一版只处理 PDF；
  - 已入库（按**解析后的绝对路径**比对）→ 跳过，不重复建；
  - 文件名解析：**先认我们自己 rename 的模板**，再退回启发式；
  - 解析是"尽力而为"，结果一律先给用户预览确认（PRD §10 / §12.5 精神）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..db import dao
from ..paths import resolve, to_stored
from . import books as book_service
from .context import Context

#: 第一版只支持 PDF
SUFFIXES: tuple[str, ...] = (".pdf",)

#: 可直接写入 books 表的字段（series 等仅用于展示）
INGEST_KEYS = (
    "title", "author", "translator", "publisher", "publication_place",
    "publication_year", "edition", "isbn", "clc_code", "literature_type",
)


class ScanError(RuntimeError):
    """扫描相关错误。"""


class DirectoryNotFoundError(ScanError):
    """扫描目录不存在或不是目录。"""


# ───────────────────────────────────────────────────────────── 文件名解析
#: z-library 之类的水印尾巴（可能多次出现）
_NOISE = re.compile(r"\s*[\(（](?:z-?library|1lib|z-lib)[^)）]*[\)）]", re.I)
#: 系列前缀 [xxx]
_SERIES = re.compile(r"^\s*\[([^\]]+)\]\s*")
#: 文献类型标记 [M]
_LIT_TYPE = re.compile(r"\[([A-Za-z])\]")
#: 尾部括号（可能是作者）
_TRAILING_PAREN = re.compile(r"\s*[\(（]([^()（）]+)[\)）]\s*$")
#: 版次：第N版 / N版 / 修订本
_EDITION = re.compile(r"\s*[\(（]?\s*第?([一二三四五六七八九十\d]+)\s*版\s*[\)）]?\s*")
#: 年份（可带版次）
_YEAR_EDITION = re.compile(r"^(\d{4})\s*(?:[\(（](.+?)[\)）])?$")
#: 作者尾部角色字（王良卿著 → 王良卿）
_ROLE_SUFFIX = re.compile(r"(著|编著|主编|编|译|等)\s*$")
_CN_NUM = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
           "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}


def _cn_edition(value: str) -> str:
    return _CN_NUM.get(value, value)


def _parse_template(stem: str) -> dict | None:
    """解析我们自己 rename 生成的模板（§14.1）。

    模板：{作者}_{书名}[{类型}]{_译者}_{出版地}：{出版社}_{年份}({版次})
    """
    parts = [p for p in stem.split("_") if p.strip()]
    idx = next((i for i, p in enumerate(parts) if _LIT_TYPE.search(p)), None)
    if idx is None or idx == 0:
        return None

    type_match = _LIT_TYPE.search(parts[idx])
    meta: dict = {
        "title": _LIT_TYPE.sub("", parts[idx]).strip(),
        "author": parts[0].strip(),
        "literature_type": type_match.group(1).upper(),
    }

    for part in parts[idx + 1:]:
        part = part.strip()
        m = _YEAR_EDITION.match(part)
        if m:
            meta["publication_year"] = m.group(1)
            if m.group(2):
                meta["edition"] = _cn_edition(m.group(2).strip())
            continue
        sep = "：" if "：" in part else (":" if ":" in part else None)
        if sep:
            place, _, publisher = part.partition(sep)
            meta["publication_place"] = place.strip() or None
            meta["publisher"] = publisher.strip() or None
            continue
        if meta.get("translator"):
            meta["translator"] += "；" + part
        else:
            meta["translator"] = part

    return meta if meta.get("title") else None


def _parse_heuristic(stem: str) -> dict:
    """z-library 之类噪声文件名的启发式解析（尽力而为）。"""
    meta: dict = {}
    m = _SERIES.match(stem)
    if m:
        meta["series"] = m.group(1).strip()      # 仅展示，不入库
        stem = _SERIES.sub("", stem)

    remaining = stem.strip()
    m = _EDITION.search(remaining)
    if m:
        meta["edition"] = _cn_edition(m.group(1))
        remaining = _EDITION.sub(" ", remaining).strip(" _-·")

    m = _TRAILING_PAREN.search(remaining)
    if m:
        inner = m.group(1).strip()
        # 尾括号是"看不懂的噪声"时不当作者（含 url / 纯数字 / 太像版次）
        if inner and not re.search(r"https?://|www\.|^\d+$", inner):
            meta["author"] = _ROLE_SUFFIX.sub("", inner).strip() or None
            remaining = _TRAILING_PAREN.sub("", remaining).strip(" _-·")

    meta["title"] = remaining.strip()
    return meta


def parse_filename(path: str | Path) -> dict:
    """从文件名解析元数据（先模板、后启发式）。

    返回键可能含：title / author / translator / publisher / publication_place /
    publication_year / edition / literature_type / series（series 仅展示）。
    """
    stem = Path(path).stem
    stem = _NOISE.sub("", stem).strip()
    meta = _parse_template(stem) or _parse_heuristic(stem)
    if not meta.get("title"):
        meta["title"] = stem.strip()
    return {k: v for k, v in meta.items() if v not in (None, "")}


# ───────────────────────────────────────────────────────────────── 扫描
def scan_directory(
    directory: str | Path,
    *,
    recursive: bool = True,
    suffixes: tuple[str, ...] = SUFFIXES,
) -> list[Path]:
    """列出目录下所有候选电子书（绝对路径，已排序）。"""
    root = Path(directory)
    if not root.is_dir():
        raise DirectoryNotFoundError(f"目录不存在或不是目录：{root}")

    wanted = {s.lower() for s in suffixes}
    it = root.rglob("*") if recursive else root.glob("*")
    files = [
        p for p in it
        if p.is_file() and p.suffix.lower() in wanted and not p.name.startswith("~$")
    ]
    return sorted(files)


@dataclass
class Candidate:
    """一本候选图书的建库计划项。"""

    index: int
    path: Path
    stored: str
    meta: dict
    already: dict | None = None              # 已入库的同文件记录
    duplicates: list[dict] = field(default_factory=list)
    error: str | None = None

    @property
    def creatable(self) -> bool:
        return self.error is None and self.already is None


@dataclass
class ScanPlan:
    directory: Path
    total_files: int
    skipped_existing: int
    candidates: list[Candidate]

    @property
    def new_count(self) -> int:
        return sum(1 for c in self.candidates if c.creatable)

    @property
    def dup_count(self) -> int:
        return sum(1 for c in self.candidates if c.duplicates)


def build_plan(ctx: Context, directory: str | Path, *, recursive: bool = True) -> ScanPlan:
    """扫描目录并生成建库计划（**不写库**）。"""
    files = scan_directory(directory, recursive=recursive)

    # 已入库的文件（解析成绝对路径后比对，避免相对/绝对写法差异）
    existing: dict[str, dict] = {}
    for book in book_service.list_books(ctx):
        stored = book.get("file_path")
        if not stored:
            continue
        try:
            abs_path = resolve(stored, ctx.config.library_root)
        except ValueError:
            continue
        if abs_path is not None:
            existing[str(abs_path).lower()] = book

    candidates: list[Candidate] = []
    skipped = 0
    for path in files:
        abs_path = path.resolve()
        already = existing.get(str(abs_path).lower())
        if already is not None:
            skipped += 1
            candidates.append(Candidate(
                index=len(candidates), path=abs_path,
                stored=already.get("file_path") or "",
                meta={}, already=already,
            ))
            continue

        try:
            stored = to_stored(abs_path, ctx.config.library_root)
        except ValueError as exc:               # 路径语法非法（如全角字符）
            candidates.append(Candidate(
                index=len(candidates), path=abs_path, stored="",
                meta={}, error=str(exc),
            ))
            continue

        meta = parse_filename(path)
        # 重复提示：按「书名+作者」精确匹配（忽略年份 —— 扫描时年份常缺失）
        dupes = dao.find_same_title_author(
            ctx.conn, title=meta.get("title", ""), author=meta.get("author")
        )
        candidates.append(Candidate(
            index=len(candidates), path=abs_path, stored=stored,
            meta=meta, duplicates=dupes,
        ))

    return ScanPlan(directory=Path(directory), total_files=len(files),
                    skipped_existing=skipped, candidates=candidates)


@dataclass
class ApplyResult:
    created: list[int] = field(default_factory=list)
    skipped: int = 0

    @property
    def created_count(self) -> int:
        return len(self.created)


def apply_plan(
    ctx: Context,
    plan: ScanPlan,
    *,
    selected: set[int] | None = None,
) -> ApplyResult:
    """按计划建记录。selected 为候选 index 集合；None 表示全部可建的。"""
    result = ApplyResult()
    for cand in plan.candidates:
        if selected is not None and cand.index not in selected:
            result.skipped += 1
            continue
        if not cand.creatable:
            result.skipped += 1
            continue

        payload = {k: v for k, v in cand.meta.items() if k in INGEST_KEYS}
        payload["file_path"] = cand.stored
        # 重复项已在计划里提示过，用户仍勾选 → 视为确认
        result.created.append(
            book_service.add_book(ctx, payload, allow_duplicate=True)
        )
    return result
