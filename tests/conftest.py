"""pytest 全局夹具。

**安全网**：把所有测试的「应用设置目录」与「模型目录」指到临时位置 ——
任何测试都**不得**读写用户真实的 `%APPDATA%\\BookDB\\` 或 `%LOCALAPPDATA%\\BookDB\\`。

⚠️ 模型目录尤其重要：早先 `test_state_local_when_files_exist` 直接在真实
   `%LOCALAPPDATA%\\BookDB\\models` 写了假 GGUF，污染了同批次的其他测试
   （`source` 从 ollama 变 local）。故此处**全局**隔离。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_app_settings(tmp_path, monkeypatch):
    """每个测试一个独立的设置目录（见 gui/settings.app_data_dir 的覆盖开关）。"""
    monkeypatch.setenv("BOOKDB_SETTINGS_DIR", str(tmp_path / "bookdb-settings"))
    # 模型目录同样隔离（services/model.models_dir 会读 BOOKDB_MODELS_DIR）
    monkeypatch.setenv("BOOKDB_MODELS_DIR", str(tmp_path / "bookdb-models"))
