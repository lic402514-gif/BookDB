"""数据库 DDL（见 PRD §5）。

两张表：books（主表）+ publishers（独立实体）。
纯标准库 sqlite3，手写 SQL，不使用 ORM。
"""

from __future__ import annotations

SCHEMA_VERSION = 1

#: 图书主表 —— 字段与 PRD §5.1 一一对应
CREATE_BOOKS = """
CREATE TABLE IF NOT EXISTS books (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    title             TEXT NOT NULL,
    author            TEXT,
    translator        TEXT,
    publisher_id      INTEGER REFERENCES publishers(id) ON DELETE SET NULL,
    publication_place TEXT,
    publication_year  INTEGER,
    edition           TEXT,            -- 纯数字字符串，如 "2"
    isbn              TEXT,
    clc_code          TEXT,
    literature_type   TEXT DEFAULT 'M',
    abstract          TEXT,            -- 长文本；列表查询不读取
    file_path         TEXT,            -- 绝对或相对（相对 library_root）
    created_at        TEXT,
    updated_at        TEXT
);
"""

CREATE_PUBLISHERS = """
CREATE TABLE IF NOT EXISTS publishers (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);
"""

CREATE_META = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

#: 索引 —— 服务于 §8 检索
CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_books_title ON books(title);",
    "CREATE INDEX IF NOT EXISTS idx_books_author ON books(author);",
    "CREATE INDEX IF NOT EXISTS idx_books_isbn ON books(isbn);",
    "CREATE INDEX IF NOT EXISTS idx_books_clc ON books(clc_code);",
    "CREATE INDEX IF NOT EXISTS idx_books_year ON books(publication_year);",
    "CREATE INDEX IF NOT EXISTS idx_books_publisher ON books(publisher_id);",
]

#: 列表查询显式列出的列 —— 绝不 SELECT *，且**不含 abstract**（PRD §8.3）
LIST_COLUMNS = (
    "b.id, b.title, b.author, b.translator, b.publication_place, "
    "b.publication_year, b.edition, b.isbn, b.clc_code, b.literature_type, "
    "b.file_path, b.created_at, b.updated_at, "
    "p.name AS publisher_name"
)

#: 详情查询的列 —— 含 abstract
DETAIL_COLUMNS = LIST_COLUMNS + ", b.abstract"

#: books 表中允许通过 CLI / JSON 写入的字段（用于 patch 校验，见 §10.1）
WRITABLE_FIELDS = {
    "title",
    "author",
    "translator",
    "publisher",
    "publication_place",
    "publication_year",
    "edition",
    "isbn",
    "clc_code",
    "literature_type",
    "abstract",
    "file_path",
}
