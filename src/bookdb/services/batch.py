"""批量操作（PRD §14 扩展）—— 多选后对多本执行同一动作。

四种操作，**风险递增，确认强度也不同**：

    rename       改文件名 + 更新 file_path       低（可改回）
    move         移动文件位置 + 更新 file_path   中
    delete_meta  只删数据库记录（文件保留）      中高（不可逆）
    delete_files 删文件本体 + 记录（**送回收站**）🔴 最高

统一骨架（与 §10 导入预览、§14 重命名一致）：

    build_*_plan()  → **只读**，不动文件、不改库
          ↓ 预览（逐条列出影响）
    apply_plan()    → 逐条执行
          ↓
    逐条回报（成功 / 失败原因）

安全约定：
  - **绝不用永久删除**：删文件一律经 `send2trash` 送回收站；未安装则**明确报错**
    （不提供"静默永久删除"的降级路径）—— 对齐用户「不确定就报错，不静默降级」。
  - **逐条独立**：一条失败不影响其余（不做全有全无）；失败原因逐条回报。
  - **目标冲突不自动覆盖**：预览时标记，默认不勾选，`overwrite=True` 才覆盖。
  - **批次内冲突检测**：两本算出同一目标名、或 A 的新名正是 B 的旧名（连锁改名）→ 标记冲突。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..db import dao
from ..paths import resolve, to_stored
from . import rename as rename_service
from .context import Context

#: 可执行的状态（其余为「不可操作」，如无文件 / 已规范 / 冲突）
ACTIONABLE = ("rename", "move", "delete")


class BatchError(RuntimeError):
    """批量操作的通用错误（参数不合法、环境不满足等）。"""


class TrashUnavailableError(BatchError):
    """未安装 send2trash —— 无法送回收站，因此拒绝删除（**不降级为永久删除**）。"""


@dataclass
class BatchItem:
    """一条待处理记录。"""

    book_id: int
    title: str
    status: str                      # 见下
    old: str | None = None           # 旧路径（绝对）或旧名
    new: str | None = None           # 新路径（绝对）
    size: int | None = None          # 文件字节数（删除/移动时展示）
    error: str | None = None

    @property
    def actionable(self) -> bool:
        return self.status in ACTIONABLE


@dataclass
class BatchPlan:
    """一次批量操作的完整计划（只读产物）。"""

    operation: str                   # 'rename' | 'move' | 'delete_meta' | 'delete_files'
    items: list[BatchItem] = field(default_factory=list)

    @property
    def actionable(self) -> list[BatchItem]:
        return [i for i in self.items if i.actionable]

    @property
    def blocked(self) -> list[BatchItem]:
        return [i for i in self.items if not i.actionable]

    @property
    def total_size(self) -> int:
        return sum(i.size or 0 for i in self.items if i.actionable)

    def by_id(self, book_id: int) -> BatchItem | None:
        return next((i for i in self.items if i.book_id == book_id), None)


# ────────────────────────────────────────────────────────────── 内部工具
def _load_books(ctx: Context, ids: list[int]) -> list[dict]:
    """按 id 取记录；**任一不存在即报错**（不静默跳过，避免误操作范围）。"""
    books: list[dict] = []
    for book_id in ids:
        book = dao.get_book(ctx.conn, book_id, with_abstract=False)
        if book is None:
            raise BatchError(f"记录不存在：#{book_id}")
        books.append(book)
    return books


def _resolve_file(ctx: Context, book: dict) -> Path | None:
    stored = book.get("file_path")
    if not stored:
        return None
    try:
        return resolve(stored, ctx.config.library_root)
    except Exception:  # noqa: BLE001 - library_root 缺失等
        return None


def _mark_batch_collisions(items: list[BatchItem], status: str) -> None:
    """标记**批次内部**冲突：多个条目算出同一目标，或目标是另一条的旧路径。"""
    targets: dict[str, list[int]] = {}
    for item in items:
        if item.status == status and item.new:
            targets.setdefault(item.new, []).append(item.book_id)

    olds = {item.old for item in items if item.old}

    for item in items:
        if item.status != status:
            continue
        if len(targets.get(item.new or "", [])) > 1:
            item.status = "conflict"
            item.error = "批次内多本目标同名"
        elif item.new in olds:
            item.status = "conflict"
            item.error = "目标正是批次内另一条的旧文件名（连锁改名）"


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)


# ────────────────────────────────────────────────────────── ① 重命名
def build_rename_plan(ctx: Context, ids: list[int]) -> BatchPlan:
    """生成批量重命名计划（只读）。"""
    from .format import build_filename

    items: list[BatchItem] = []
    for book in _load_books(ctx, ids):
        old = _resolve_file(ctx, book)
        if old is None:
            items.append(BatchItem(book["id"], book["title"], "no_file",
                                   error="未关联文件"))
            continue
        if not old.is_file():
            items.append(BatchItem(book["id"], book["title"], "missing", old=str(old),
                                   error=f"文件不存在：{old}"))
            continue

        new_name = build_filename(book, suffix=old.suffix)
        new = old.parent / new_name
        if old.name == new_name:
            items.append(BatchItem(book["id"], book["title"], "unchanged",
                                   old=str(old), new=str(new),
                                   error="已是规范文件名"))
            continue

        conflict = new.exists()
        items.append(BatchItem(
            book["id"], book["title"], "conflict" if conflict else "rename",
            old=str(old), new=str(new),
            error="目标文件已存在" if conflict else None,
        ))

    _mark_batch_collisions(items, "rename")
    return BatchPlan("rename", items)


# ────────────────────────────────────────────────────────── ② 移动
def build_move_plan(ctx: Context, ids: list[int], destination: str | Path) -> BatchPlan:
    """生成批量移动计划（只读）。`destination` 必须是**已存在**的目录。"""
    dest = Path(destination).expanduser()
    if not dest.exists():
        raise BatchError(f"目标目录不存在：{dest}")
    if not dest.is_dir():
        raise BatchError(f"目标不是目录：{dest}")

    items: list[BatchItem] = []
    for book in _load_books(ctx, ids):
        old = _resolve_file(ctx, book)
        if old is None:
            items.append(BatchItem(book["id"], book["title"], "no_file",
                                   error="未关联文件"))
            continue
        if not old.is_file():
            items.append(BatchItem(book["id"], book["title"], "missing", old=str(old),
                                   error=f"文件不存在：{old}"))
            continue

        size = old.stat().st_size
        new = dest / old.name                     # 只挪位置，**不改名**

        if _same_path(old.parent, dest):
            items.append(BatchItem(book["id"], book["title"], "unchanged",
                                   old=str(old), new=str(new), size=size,
                                   error="已在目标目录中"))
            continue

        conflict = new.exists() and not _same_path(new, old)
        items.append(BatchItem(
            book["id"], book["title"], "conflict" if conflict else "move",
            old=str(old), new=str(new), size=size,
            error="目标目录已有同名文件" if conflict else None,
        ))

    _mark_batch_collisions(items, "move")
    return BatchPlan("move", items)


# ──────────────────────────────────────────────────── ③④ 删除
def build_delete_plan(ctx: Context, ids: list[int], *, with_files: bool) -> BatchPlan:
    """生成删除计划（只读）。

    `with_files=False` → 只删数据库记录（文件保留）。
    `with_files=True`  → 同时把文件**送回收站**（不可恢复的只有回收站那一步，
                         但从用户视角等同「删除」，故要求二次确认）。
    """
    items: list[BatchItem] = []
    for book in _load_books(ctx, ids):
        if not with_files:
            path = _resolve_file(ctx, book)
            items.append(BatchItem(
                book["id"], book["title"], "delete",
                old=str(path) if path else book.get("file_path"),
            ))
            continue

        path = _resolve_file(ctx, book)
        if path is None:
            items.append(BatchItem(book["id"], book["title"], "no_file",
                                   error="未关联文件"))
            continue
        if not path.is_file():
            items.append(BatchItem(book["id"], book["title"], "missing", old=str(path),
                                   error=f"文件不存在：{path}"))
            continue
        items.append(BatchItem(book["id"], book["title"], "delete",
                               old=str(path), size=path.stat().st_size))

    return BatchPlan("delete_files" if with_files else "delete_meta", items)


# ────────────────────────────────────────────────────────────── 执行
def _send_to_trash(path: Path) -> None:
    """把单个文件送回收站。**未安装 send2trash 时明确报错，绝不永久删除。**"""
    try:
        from send2trash import send2trash
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise TrashUnavailableError(
            "删除文件需要 send2trash（送回收站，可恢复）。\n"
            '请安装：uv pip install -e ".[trash]"\n'
            "本程序不会永久删除文件，因此没有降级路径。"
        ) from exc
    send2trash(str(path))


def _do_rename(ctx: Context, item: BatchItem, *, overwrite: bool) -> None:
    plan = rename_service.RenamePlan(
        book_id=item.book_id,
        old_path=Path(item.old or ""),
        new_path=Path(item.new or ""),
    )
    rename_service.apply_plan(ctx, plan, overwrite=overwrite)


def _do_move(ctx: Context, item: BatchItem, *, overwrite: bool) -> None:
    old, new = Path(item.old or ""), Path(item.new or "")
    if not old.is_file():
        raise FileNotFoundError(f"源文件不存在：{old}")
    if new.exists():
        if not overwrite:
            raise rename_service.TargetExistsError(f"目标文件已存在：{new}")
        new.unlink()                     # 明确覆盖
    shutil.move(str(old), str(new))      # 跨盘自动 copy+delete；同盘走 rename
    dao.update_book_fields(
        ctx.conn, item.book_id,
        {"file_path": to_stored(new, ctx.config.library_root)},
    )


def _do_delete(ctx: Context, item: BatchItem, *, with_files: bool) -> None:
    if with_files:
        path = Path(item.old or "")
        if not path.is_file():
            raise FileNotFoundError(f"文件不存在：{path}")
        _send_to_trash(path)             # ★ 先确认能送回收站，再删记录
    dao.delete_book(ctx.conn, item.book_id)


def apply_plan(
    ctx: Context,
    plan: BatchPlan,
    *,
    selected: set[int] | None = None,
    overwrite: bool = False,
    confirm: bool = False,
) -> dict:
    """逐条执行计划。

    `selected`  —— 要执行的 book_id 集合；None = 全部可执行条目。
    `overwrite` —— 目标存在时是否覆盖（重命名 / 移动）。
    `confirm`   —— 删除类操作**必须**显式传 True，否则拒绝（后端第二道闸）。

    返回 `{"done": [ids], "failed": [{id, error}], "skipped": n}`。
    """
    deleting = plan.operation in ("delete_meta", "delete_files")
    if deleting and not confirm:
        raise BatchError("删除操作需要显式确认（confirm=true）")

    done: list[int] = []
    failed: list[dict] = []
    skipped = 0

    for item in plan.items:
        if not item.actionable:
            skipped += 1
            continue
        if selected is not None and item.book_id not in selected:
            skipped += 1
            continue
        try:
            if plan.operation == "rename":
                _do_rename(ctx, item, overwrite=overwrite)
            elif plan.operation == "move":
                _do_move(ctx, item, overwrite=overwrite)
            else:
                _do_delete(ctx, item, with_files=plan.operation == "delete_files")
        except Exception as exc:                     # noqa: BLE001 - 逐条独立
            failed.append({"id": item.book_id, "error": f"{type(exc).__name__}: {exc}"})
        else:
            done.append(item.book_id)

    ctx.conn.commit()
    return {"done": done, "failed": failed, "skipped": skipped,
            "operation": plan.operation}
