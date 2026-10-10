"""引用输出服务（PRD §15）。

GB/T 7714-2015 专著。模板与缺省规则由 `services/format.py` 提供（与重命名共用）。
"""

from __future__ import annotations

from . import books as book_service
from .context import Context
from .format import format_citation


def cite(ctx: Context, book_id: int) -> str:
    """输出指定图书的 GB/T 7714 引用。"""
    return format_citation(book_service.get_book(ctx, book_id))
