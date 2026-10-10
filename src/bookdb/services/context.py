"""统一业务层（Service Layer）。

CLI 与 GUI 均只调用本层；CLI / GUI 不得各自实现一套数据库逻辑，也不得
直接操作 DAO（PRD §17）。

本模块提供运行期上下文：把配置与数据库连接绑在一起，作为各 Service 的入口。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..config import Config
from ..db import database


@dataclass
class Context:
    """一次命令 / 一次请求的运行期上下文。"""

    config: Config
    conn: sqlite3.Connection

    @classmethod
    def open(cls, config: Config) -> "Context":
        """按配置打开数据库连接（不自动建表；由 init 负责）。"""
        conn = database.connect(config.db_path)
        return cls(config=config, conn=conn)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Context":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
