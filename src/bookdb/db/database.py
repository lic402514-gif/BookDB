"""数据库连接与初始化。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import schema


def now_iso() -> str:
    """统一的入库时间戳（UTC，秒级）。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开数据库连接。

    - row_factory = sqlite3.Row，便于按列名取值；
    - 启用外键约束（PRAGMA foreign_keys = ON）。
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def database_exists(db_path: str | Path) -> bool:
    return Path(db_path).exists()


def initialize(conn: sqlite3.Connection) -> None:
    """建表 + 建索引 + 写入 schema 版本。幂等。"""
    cur = conn.cursor()
    cur.execute(schema.CREATE_PUBLISHERS)
    cur.execute(schema.CREATE_BOOKS)
    cur.execute(schema.CREATE_META)
    for stmt in schema.CREATE_INDEXES:
        cur.execute(stmt)
    cur.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(schema.SCHEMA_VERSION),),
    )
    conn.commit()
