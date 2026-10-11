"""pytest 全局夹具。

**安全网**：把所有测试的「应用设置目录」指到临时位置 ——
任何测试都**不得**读写用户真实的 `%APPDATA%\\BookDB\\settings.json`。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_app_settings(tmp_path, monkeypatch):
    """每个测试一个独立的设置目录（见 gui/settings.app_data_dir 的覆盖开关）。"""
    monkeypatch.setenv("BOOKDB_SETTINGS_DIR", str(tmp_path / "bookdb-settings"))
