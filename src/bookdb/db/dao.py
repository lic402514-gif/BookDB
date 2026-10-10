"""数据访问层（DAO）—— 手写 SQL，无 ORM。

本层只做「取数 / 写数」，不含业务判断（业务在 services/）。
所有方法接收一个已打开的 sqlite3.Connection。

注：列表类查询一律显式列出列且不含 abstract（PRD §8.3）。
"""

from __future__ import annotations

import sqlite3

from . import schema
from .database import now_iso


# ------------------------------------------------------------------ helpers
def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def get_or_create_publisher(conn: sqlite3.Connection, name: str) -> int:
    """按名称取出版社 id，不存在则创建（PRD §7.2）。"""
    name = name.strip()
    row = conn.execute("SELECT id FROM publishers WHERE name = ?", (name,)).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute("INSERT INTO publishers(name) VALUES(?)", (name,))
    return int(cur.lastrowid)


def get_publisher_name(conn: sqlite3.Connection, publisher_id: int | None) -> str | None:
    if publisher_id is None:
        return None
    row = conn.execute("SELECT name FROM publishers WHERE id = ?", (publisher_id,)).fetchone()
    return row["name"] if row else None


# ------------------------------------------------------------------- create
def insert_book(conn: sqlite3.Connection, data: dict) -> int:
    """插入一条图书记录，返回新 id。data 中的 publisher 为名称字符串。"""
    publisher_id = None
    if data.get("publisher"):
        publisher_id = get_or_create_publisher(conn, data["publisher"])

    now = now_iso()
    cur = conn.execute(
        """
        INSERT INTO books (
            title, author, translator, publisher_id, publication_place,
            publication_year, edition, isbn, clc_code, literature_type,
            abstract, file_path, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            data["title"],
            data.get("author"),
            data.get("translator"),
            publisher_id,
            data.get("publication_place"),
            data.get("publication_year"),
            data.get("edition"),
            data.get("isbn"),
            data.get("clc_code"),
            data.get("literature_type") or "M",
            data.get("abstract"),
            data.get("file_path"),
            now,
            now,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


# --------------------------------------------------------------------- read
def get_book(conn: sqlite3.Connection, book_id: int, *, with_abstract: bool = True) -> dict | None:
    """取单条记录。默认含 abstract（详情场景）。"""
    cols = schema.DETAIL_COLUMNS if with_abstract else schema.LIST_COLUMNS
    row = conn.execute(
        f"""
        SELECT {cols}
        FROM books b
        LEFT JOIN publishers p ON p.id = b.publisher_id
        WHERE b.id = ?
        """,
        (book_id,),
    ).fetchone()
    return _row_to_dict(row)


def list_books(
    conn: sqlite3.Connection,
    limit: int | None = None,
    *,
    with_abstract: bool = False,
) -> list[dict]:
    """列表查询。默认不含 abstract（§8.3）；导出等需要完整字段时 with_abstract=True。"""
    cols = schema.DETAIL_COLUMNS if with_abstract else schema.LIST_COLUMNS
    sql = f"""
        SELECT {cols}
        FROM books b
        LEFT JOIN publishers p ON p.id = b.publisher_id
        ORDER BY b.id
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in conn.execute(sql).fetchall()]


# ------------------------------------------------------------------- update
def update_book_fields(conn: sqlite3.Connection, book_id: int, fields: dict) -> int:
    """局部更新：只写 fields 中出现的列，返回受影响行数。

    fields 中的 publisher 为名称 → 解析为 publisher_id。
    """
    if not fields:
        return 0

    sets: list[str] = []
    values: list[object] = []

    for key, value in fields.items():
        if key == "publisher":
            if value is None:
                sets.append("publisher_id = NULL")
            else:
                sets.append("publisher_id = ?")
                values.append(get_or_create_publisher(conn, str(value)))
        else:
            sets.append(f"{key} = ?")
            values.append(value)

    sets.append("updated_at = ?")
    values.append(now_iso())
    values.append(book_id)

    cur = conn.execute(f"UPDATE books SET {', '.join(sets)} WHERE id = ?", values)
    conn.commit()
    return cur.rowcount


# ------------------------------------------------------------------- delete
def delete_book(conn: sqlite3.Connection, book_id: int) -> int:
    cur = conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    conn.commit()
    return cur.rowcount


# ------------------------------------------------------------------- search
def search_books(
    conn: sqlite3.Connection,
    *,
    keyword: str | None = None,
    author: str | None = None,
    year_start: int | None = None,
    year_end: int | None = None,
    clc_prefix: str | None = None,
    literature_type: str | None = None,
    publisher: str | None = None,
    has_file: bool | None = None,
    limit: int | None = None,
) -> list[dict]:
    """组合条件检索（PRD §8）。列表视图返回不含 abstract。"""
    where: list[str] = []
    params: list[object] = []

    if keyword:
        like = f"%{keyword}%"
        # 关键词可命中 abstract（在 SQL 层过滤，但不把摘要取回列表）—— PRD §8.3
        where.append(
            "(b.title LIKE ? OR b.author LIKE ? OR b.translator LIKE ? "
            "OR p.name LIKE ? OR b.isbn LIKE ? OR b.clc_code LIKE ? OR b.abstract LIKE ?)"
        )
        params.extend([like] * 7)

    if author:
        where.append("b.author LIKE ?")
        params.append(f"%{author}%")
    if year_start is not None:
        where.append("b.publication_year >= ?")
        params.append(year_start)
    if year_end is not None:
        where.append("b.publication_year <= ?")
        params.append(year_end)
    if clc_prefix:
        where.append("b.clc_code LIKE ?")
        params.append(f"{clc_prefix}%")
    if literature_type:
        where.append("b.literature_type = ?")
        params.append(literature_type)
    if publisher:
        where.append("p.name LIKE ?")
        params.append(f"%{publisher}%")
    if has_file is True:
        where.append("b.file_path IS NOT NULL AND b.file_path != ''")
    elif has_file is False:
        where.append("(b.file_path IS NULL OR b.file_path = '')")

    sql = f"""
        SELECT {schema.LIST_COLUMNS}
        FROM books b
        LEFT JOIN publishers p ON p.id = b.publisher_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY b.id"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# --------------------------------------------------------------------- dupes
def find_duplicates(
    conn: sqlite3.Connection,
    *,
    isbn: str | None,
    title: str,
    author: str | None,
    year: int | None,
) -> list[dict]:
    """疑似重复检测（PRD §23.2）：先 ISBN，无则 书名+作者+年份。"""
    if isbn:
        rows = conn.execute(
            f"SELECT {schema.LIST_COLUMNS} FROM books b "
            f"LEFT JOIN publishers p ON p.id = b.publisher_id WHERE b.isbn = ?",
            (isbn,),
        ).fetchall()
        if rows:
            return [dict(r) for r in rows]

    rows = conn.execute(
        f"SELECT {schema.LIST_COLUMNS} FROM books b "
        f"LEFT JOIN publishers p ON p.id = b.publisher_id "
        f"WHERE b.title = ? AND IFNULL(b.author,'') = IFNULL(?,'') "
        f"AND IFNULL(b.publication_year,0) = IFNULL(?,0)",
        (title, author, year),
    ).fetchall()
    return [dict(r) for r in rows]


def count_books(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS c FROM books").fetchone()["c"])
