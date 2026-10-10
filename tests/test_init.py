"""初始化语义测试（PRD §7.1 定稿）。

覆盖三条已定约束：
  1. --force 幂等：重复初始化绝不删除/清空已有数据；
  2. book.toml 已存在时，普通 init 不覆盖；
  3. --force 重新生成 book.toml 时，保留已有配置值（不清空）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bookdb.config import Config  # noqa: E402
from bookdb.services import books as book_service  # noqa: E402
from bookdb.services import setup  # noqa: E402
from bookdb.services.context import Context  # noqa: E402


def _config(tmp_path: Path) -> Config:
    return Config(
        config_path=tmp_path / "book.toml",
        db_path=tmp_path / "book.db",
        library_root=tmp_path / "lib",
    )


SAMPLE = {"title": "否定辩证法", "author": "阿多尔诺"}


# ─────────────────────────────────────────────────────── 1) --force 幂等、不丢数据
def test_force_is_idempotent_and_preserves_data(tmp_path: Path):
    config = _config(tmp_path)

    first = setup.initialize(config)
    assert first.db_existed is False

    with Context.open(config) as ctx:
        book_service.add_book(ctx, SAMPLE)

    # --force 重新初始化：不允许 DROP / DELETE
    second = setup.initialize(config, force=True)
    assert second.db_existed is True

    with Context.open(config) as ctx:
        rows = book_service.list_books(ctx)
    assert len(rows) == 1
    assert rows[0]["title"] == "否定辩证法"          # 数据仍在


def test_init_refuses_when_db_exists_without_force(tmp_path: Path):
    config = _config(tmp_path)
    setup.initialize(config)
    with pytest.raises(setup.DatabaseExistsError):
        setup.initialize(config)


# ─────────────────────────────────────────────────────── 2) 不静默覆盖 book.toml
def test_existing_config_not_overwritten_without_force(tmp_path: Path):
    config = _config(tmp_path)
    cfg_path = config.effective_config_path
    cfg_path.write_text('[paths]\nlibrary_root = "CUSTOM_MARKER"\n', encoding="utf-8")

    result = setup.initialize(config)                 # 数据库尚不存在 → 可执行
    assert result.config_existed is True
    assert result.config_written is False             # 没动配置文件
    assert "CUSTOM_MARKER" in cfg_path.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────── 3) --force 重生成保留配置值
def test_force_regenerates_config_and_keeps_values(tmp_path: Path):
    config = _config(tmp_path)
    setup.initialize(config)                          # 首次：写入 book.toml

    result = setup.initialize(config, force=True)     # 重生成
    assert result.config_written is True
    assert result.config_existed is True

    text = config.effective_config_path.read_text(encoding="utf-8")
    marker = str(tmp_path / "lib").replace("\\", "/")
    assert marker in text                             # library_root 未被清空
    assert "library_root" in text
