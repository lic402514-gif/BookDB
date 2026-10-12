"""GUI 的 AI 模型接口测试（/api/model/*）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bookdb.config import Config  # noqa: E402
from bookdb.db import database  # noqa: E402
from bookdb.gui.server import create_server, server_url  # noqa: E402
from bookdb.services import model as model_service  # noqa: E402
from bookdb.services import setup as setup_service  # noqa: E402


class Api:
    def __init__(self, server, config):
        self.url = server_url(server).rstrip("/")
        self.config = config

    def call(self, method, path, body=None):
        import json
        import urllib.error
        import urllib.request

        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.url}{path}", data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def text(self, path):
        import urllib.request

        with urllib.request.urlopen(f"{self.url}{path}", timeout=30) as r:
            return r.read().decode()


@pytest.fixture()
def api(tmp_path: Path):
    root = tmp_path / "Library"
    root.mkdir()
    cfg = Config(config_path=root / "book.toml", library_root=root, db_path=root / "book.db")
    setup_service.initialize(cfg)
    cfg.save(base=root)

    server = create_server(cfg, port=0)
    import threading

    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield Api(server, cfg)
    finally:
        server.shutdown()
        server.server_close()


# ─────────────────────────────────────────────────── 列表 / 状态
def test_model_list_returns_catalog(api):
    status, data = api.call("GET", "/api/model/list")
    assert status == 200
    keys = [i["key"] for i in data["items"]]
    assert keys == ["3b", "7b"]
    assert data["default"] == "3b"
    for item in data["items"]:
        assert item["size_human"] and item["source"] in ("local", "ollama", "none")


def test_model_list_reports_binary_availability(api):
    _s, data = api.call("GET", "/api/model/list")
    assert isinstance(data["binary_ready"], bool)
    assert data["models_dir"]


def test_model_status_reports_active_backend(api, monkeypatch):
    from bookdb.services import llm as llm_service

    monkeypatch.setattr(llm_service, "_builtin_adapter", lambda vlm: None)
    monkeypatch.setattr(llm_service, "_ollama_reachable", lambda ep: False)
    status, data = api.call("GET", "/api/model/status")
    assert status == 200
    assert data["active"] is None and data["hint"]


def test_model_status_reports_builtin(api, monkeypatch):
    from bookdb.services import llm as llm_service

    class Fake:
        __class__ = type("LlamaServerAdapter", (), {})

    monkeypatch.setattr(
        llm_service, "_builtin_adapter",
        lambda vlm: type("LlamaServerAdapter", (), {})())
    _s, data = api.call("GET", "/api/model/status")
    assert data["active"] == "builtin"


# ─────────────────────────────────────────────────── 选择
def test_model_select_writes_config(api):
    status, r = api.call("POST", "/api/model/select", {"key": "7b"})
    assert status == 200 and r["builtin_model"] == "7b"
    assert Config.load(api.config.config_path).vlm.builtin_model == "7b"


def test_model_select_rejects_unknown_key(api):
    status, r = api.call("POST", "/api/model/select", {"key": "99b"})
    assert status == 400 and "未知模型" in r["error"]
    assert Config.load(api.config.config_path).vlm.builtin_model == "3b"  # 未改


def test_model_select_requires_key(api):
    status, r = api.call("POST", "/api/model/select", {})
    assert status == 400 and "key" in r["error"]


# ─────────────────────────────────────────────────── 下载
def test_model_download_rejects_unknown_key(api):
    status, r = api.call("POST", "/api/model/download", {"key": "99b"})
    assert status == 400 and "未知模型" in r["error"]


def test_model_download_requires_binary(api, monkeypatch):
    from bookdb.llm.adapters import llamacpp

    monkeypatch.setattr(llamacpp, "is_available", lambda: False)
    status, r = api.call("POST", "/api/model/download", {"key": "3b"})
    assert status == 409 and "llama-server" in r["error"]


def test_model_download_starts_and_reports_progress(api, monkeypatch):
    """后台线程：进度可查；完成后 state=done。"""
    from bookdb.llm.adapters import llamacpp
    from bookdb.gui import server as server_mod

    monkeypatch.setattr(llamacpp, "is_available", lambda: True)

    def fake_download(spec, *, mirrors=None, on_progress=None, cancel=None):
        if on_progress:
            on_progress(spec.total_size // 2, spec.total_size)
            on_progress(spec.total_size, spec.total_size)
        return (Path("a"), Path("b"))

    monkeypatch.setattr(model_service, "download", fake_download)
    server_mod._DL.update(key=None, done=0, total=0, state="idle", error=None)

    status, r = api.call("POST", "/api/model/download", {"key": "3b"})
    assert status == 200 and r["key"] == "3b"

    import time

    for _ in range(50):
        _s, p = api.call("GET", "/api/model/progress")
        if p["state"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert p["state"] == "done", p
    assert p["percent"] == 100.0
    assert p["done_human"] and p["total_human"]


def test_model_download_rejects_concurrent(api, monkeypatch):
    from bookdb.gui import server as server_mod
    from bookdb.llm.adapters import llamacpp

    monkeypatch.setattr(llamacpp, "is_available", lambda: True)
    server_mod._DL.update(key="3b", done=0, total=100, state="running", error=None)
    try:
        status, r = api.call("POST", "/api/model/download", {"key": "7b"})
        assert status == 409 and "进行中" in r["error"]
    finally:
        server_mod._DL.update(state="idle")


def test_model_progress_idle_defaults(api):
    _s, p = api.call("GET", "/api/model/progress")
    assert p["percent"] == 0.0 and p["state"] in ("idle", "done", "error")


def test_model_cancel_when_idle_is_safe(api):
    status, r = api.call("POST", "/api/model/cancel", {})
    assert status == 200 and r["ok"] is True


# ─────────────────────────────────────────────────── 删除
def test_model_remove_reports_freed_bytes(api, monkeypatch):
    monkeypatch.setattr(model_service, "remove", lambda spec: 12345)
    status, r = api.call("POST", "/api/model/remove", {"key": "3b"})
    assert status == 200 and r["freed"] == 12345 and r["freed_human"]


def test_model_remove_rejects_unknown_key(api):
    status, r = api.call("POST", "/api/model/remove", {"key": "nope"})
    assert status == 400


# ─────────────────────────────────────────────────── 前端资源
def test_frontend_has_ai_panel(api):
    html = api.text("/index.html")
    js = api.text("/app.js")
    css = api.text("/style.css")
    assert 'id="btn-ai"' in html
    assert "openAiPanel" in js and "startDlPoll" in js
    assert "selectModel" in js and "removeModel" in js
    assert ".dl-fill" in css and ".model-row" in css


def test_frontend_has_first_run_ai_hint(api):
    """AI 未就绪时给一次提示（可关掉，不打扰）。"""
    js = api.text("/app.js")
    assert "checkAiReady" in js
    assert "aiHintDismissed" in js           # 会话内记住"以后再说"
    assert "ai-hint-go" in js and "开放设置" not in js
    assert "checkAiReady()" in js            # 启动流程里被调用


# ──────────────────────────────── 无库时也要能管理模型（应用级资源）
@pytest.fixture()
def api_no_library(tmp_path: Path):
    """没有打开任何 Library 的服务（欢迎页状态）。"""
    server = create_server(None, port=0)
    import threading

    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield Api(server, None)
    finally:
        server.shutdown()
        server.server_close()


def test_model_endpoints_work_without_library(api_no_library):
    """⚠️ 回归：模型是应用级资源（存 %APPDATA%），无库时也必须可用。

    否则用户在欢迎页点「🧠 AI 模型」会拿到 409，面板整个用不了。
    """
    for path in ("/api/model/list", "/api/model/status", "/api/model/progress"):
        status, data = api_no_library.call("GET", path)
        assert status == 200, f"{path} → {status} {data}"
    _s, listing = api_no_library.call("GET", "/api/model/list")
    assert [i["key"] for i in listing["items"]] == ["3b", "7b"]
    _s, st = api_no_library.call("GET", "/api/model/status")
    assert st["no_library"] is True          # 明确告知前端当前无库
    assert "backend" in st and "active" in st


def test_model_select_still_requires_library(api_no_library):
    """选用模型要写 book.toml —— 无库时被守卫拒绝（不是静默失败）。"""
    status, r = api_no_library.call("POST", "/api/model/select", {"key": "7b"})
    assert status == 409
    assert r.get("no_library") is True or "book.toml" in r.get("error", "")


def test_data_endpoints_still_blocked_without_library(api_no_library):
    """数据类接口在无库时仍应 409（守卫没被放宽过头）。"""
    status, r = api_no_library.call("GET", "/api/books")
    assert status == 409 and r.get("no_library") is True
