"""检索服务（PRD §8）。

支持：关键词（书名/作者/译者/出版社/ISBN/CLC/摘要）+ 条件过滤。
关键词可在 SQL 层命中 abstract，但列表结果不含 abstract（§8.3）。
"""

from __future__ import annotations

from ..db import dao
from ..normalize import parse_year_range
from .context import Context


def search(
    ctx: Context,
    keyword: str | None = None,
    *,
    author: str | None = None,
    year: str | None = None,
    clc: str | None = None,
    literature_type: str | None = None,
    publisher: str | None = None,
    has_file: bool | None = None,
    limit: int | None = None,
) -> list[dict]:
    """组合检索。

    year 使用 §8.2 语法：'2019' / '1800-1900' / '1800-'。
    非法年份语法会抛 ValueError（由 CLI 负责友好提示）。
    """
    year_start = year_end = None
    if year:
        year_start, year_end = parse_year_range(year)

    return dao.search_books(
        ctx.conn,
        keyword=keyword,
        author=author,
        year_start=year_start,
        year_end=year_end,
        clc_prefix=clc,
        literature_type=literature_type,
        publisher=publisher,
        has_file=has_file,
        limit=limit,
    )
