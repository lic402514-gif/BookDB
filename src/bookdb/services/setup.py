"""初始化服务（PRD §7.1）。

语义约定（已定稿）：

  - **幂等**：重复执行只做 `CREATE TABLE IF NOT EXISTS`，**绝不 DROP / DELETE**，
    `--force` 亦如此 —— 严禁删除、覆盖或清空已有数据库数据。
  - **数据库已存在**：非 `--force` 直接拒绝（`DatabaseExistsError`）。
  - **配置文件已存在**：非 `--force` **不覆盖**；`--force` 允许重新生成，
    但因配置值是先从现有 book.toml 读入后再写回，**已有配置项不会被清空**。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..db import database


class DatabaseExistsError(RuntimeError):
    """数据库已存在且未指定 --force。"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        super().__init__(f"数据库已存在：{db_path}")


@dataclass
class InitResult:
    db_path: Path
    db_existed: bool
    config_path: Path
    config_existed: bool
    config_written: bool
    library_root: Path | None


def initialize(config: Config, *, force: bool = False) -> InitResult:
    """按幂等语义初始化数据库与配置文件。"""
    config_path = config.effective_config_path
    db_existed = database.database_exists(config.db_path)
    config_existed = config_path.exists()

    if db_existed and not force:
        raise DatabaseExistsError(config.db_path)

    # 建表 —— 幂等，绝不做 DROP / DELETE（--force 亦如此）
    conn = database.connect(config.db_path)
    try:
        database.initialize(conn)
    finally:
        conn.close()

    # 配置文件：已存在且非 --force → 保持原样，不覆盖
    config_written = False
    if force or not config_existed:
        config.save()
        config_written = True

    return InitResult(
        db_path=config.db_path,
        db_existed=db_existed,
        config_path=config_path,
        config_existed=config_existed,
        config_written=config_written,
        library_root=config.library_root,
    )
