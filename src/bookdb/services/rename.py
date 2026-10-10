"""文件重命名服务（PRD §14）。

流程：build_plan（生成新旧路径，不改动任何文件）→ 预览 → apply_plan（真正 rename）。
文件名模板由 `services/format.py` 提供（与引用共用）。

规则（§14.2）：目标文件存在时默认不覆盖；重命名成功后同步更新数据库中的 file_path。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..db import dao
from ..paths import resolve, to_stored
from . import books as book_service
from .context import Context
from .format import build_filename


class NoFileError(RuntimeError):
    """该记录未关联文件，无法重命名。"""


class TargetExistsError(RuntimeError):
    """目标文件已存在（且未指定覆盖）。"""


@dataclass
class RenamePlan:
    book_id: int
    old_path: Path
    new_path: Path

    @property
    def old_name(self) -> str:
        return self.old_path.name

    @property
    def new_name(self) -> str:
        return self.new_path.name

    @property
    def changed(self) -> bool:
        return self.old_name != self.new_name


def build_plan(ctx: Context, book_id: int) -> RenamePlan:
    """生成重命名计划（只读，不动文件）。"""
    book = book_service.get_book(ctx, book_id)          # 不存在 → BookNotFoundError
    stored = book.get("file_path")
    if not stored:
        raise NoFileError(f"#{book_id} 未关联文件，无法重命名")

    old_path = resolve(stored, ctx.config.library_root)  # 相对路径按 library_root 解析
    new_name = build_filename(book, suffix=old_path.suffix)
    return RenamePlan(book_id=book_id, old_path=old_path, new_path=old_path.parent / new_name)


def apply_plan(ctx: Context, plan: RenamePlan, *, overwrite: bool = False) -> None:
    """执行重命名，并把数据库 file_path 更新为新路径。"""
    if not plan.old_path.exists():
        raise FileNotFoundError(f"源文件不存在：{plan.old_path}")
    if not plan.changed:
        return  # 已是规范名，无需操作
    if plan.new_path.exists() and not overwrite:
        raise TargetExistsError(f"目标文件已存在：{plan.new_path}")

    # Windows 的 os.rename 不能覆盖已存在目标；覆盖场景用 os.replace（原子替换）
    if overwrite:
        os.replace(plan.old_path, plan.new_path)
    else:
        plan.old_path.rename(plan.new_path)

    dao.update_book_fields(
        ctx.conn,
        plan.book_id,
        {"file_path": to_stored(plan.new_path, ctx.config.library_root)},
    )
