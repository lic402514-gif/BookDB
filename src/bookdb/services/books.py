"""图书管理服务（PRD §7）。

职责：录入 / 查看 / 编辑 / 删除 / 列表，含：
  - 字段规范化（edition / isbn / publication_year）
  - 出版社自动创建
  - 疑似重复检测（§23.2）
  - 文件路径按 library_root 存储为相对路径（§6.1）
"""

from __future__ import annotations

from pathlib import Path

from ..db import dao
from ..normalize import normalize_edition, normalize_isbn, normalize_year
from ..paths import to_stored
from .context import Context


class BookNotFoundError(LookupError):
    """指定 id 的图书不存在。"""


class DuplicateBookError(RuntimeError):
    """发现疑似重复；携带候选记录，交由调用方决定是否继续。"""

    def __init__(self, existing: list[dict]):
        self.existing = existing
        super().__init__(f"发现 {len(existing)} 条疑似重复记录")


# ------------------------------------------------------------------ normalize
def prepare_fields(payload: dict, library_root: Path | None) -> dict:
    """规范化字段（add / edit / import 共用）；空字符串统一转为 None。

    只处理「已出现」的键，缺失键不写入结果（供 patch 判断字段是否存在）。
    """
    out: dict = {}
    for key, value in payload.items():
        if isinstance(value, str):
            value = value.strip() or None
        out[key] = value

    if "edition" in out:
        out["edition"] = normalize_edition(out["edition"])
    if "isbn" in out:
        out["isbn"] = normalize_isbn(out["isbn"])
    if "publication_year" in out:
        out["publication_year"] = normalize_year(out["publication_year"])
    if out.get("file_path"):
        out["file_path"] = to_stored(out["file_path"], library_root)
    if out.get("literature_type"):
        out["literature_type"] = str(out["literature_type"]).strip().upper()
    return out


# ---------------------------------------------------------------------- add
def add_book(
    ctx: Context,
    payload: dict,
    *,
    allow_duplicate: bool = False,
) -> int:
    """新增图书，返回新 id。

    payload 键使用库内字段名（publisher 为名称字符串，非 id）。
    title 必填。发现重复且未 allow_duplicate 时抛 DuplicateBookError。
    """
    data = prepare_fields(payload, ctx.config.library_root)

    if not data.get("title"):
        raise ValueError("title（书名）为必填项")

    dupes = dao.find_duplicates(
        ctx.conn,
        isbn=data.get("isbn"),
        title=data["title"],
        author=data.get("author"),
        year=data.get("publication_year"),
    )
    if dupes and not allow_duplicate:
        raise DuplicateBookError(dupes)

    return dao.insert_book(ctx.conn, data)


# --------------------------------------------------------------------- read
def get_book(ctx: Context, book_id: int) -> dict:
    """取单条完整记录（含 abstract）。"""
    book = dao.get_book(ctx.conn, book_id, with_abstract=True)
    if book is None:
        raise BookNotFoundError(f"未找到 id={book_id} 的图书")
    return book


def list_books(ctx: Context, limit: int | None = None) -> list[dict]:
    """列表（不含 abstract）。"""
    return dao.list_books(ctx.conn, limit=limit)


# ------------------------------------------------------------------- update
def update_book(ctx: Context, book_id: int, fields: dict) -> dict:
    """局部编辑：只改传入的字段。返回更新后的完整记录。"""
    if dao.get_book(ctx.conn, book_id, with_abstract=False) is None:
        raise BookNotFoundError(f"未找到 id={book_id} 的图书")

    data = prepare_fields(fields, ctx.config.library_root)
    if not data:
        return get_book(ctx, book_id)

    # 不允许把 title 改成空值
    if "title" in data and not data["title"]:
        raise ValueError("title（书名）不能为空")

    dao.update_book_fields(ctx.conn, book_id, data)
    return get_book(ctx, book_id)


# ------------------------------------------------------------------- delete
def delete_book(ctx: Context, book_id: int) -> None:
    """删除记录（仅数据库，不动 PDF 文件 —— §7.5 / §23.3）。"""
    if dao.delete_book(ctx.conn, book_id) == 0:
        raise BookNotFoundError(f"未找到 id={book_id} 的图书")
