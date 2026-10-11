"""P0 产品化测试 —— 应用设置 / Library 管理 / 启动优先级 / 接口。

覆盖目标：BookDB 从「依赖工作目录启动的 Python tool」变成
「自己管理应用状态、Library 和启动流程的桌面应用」。
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bookdb.config import Config  # noqa: E402
from bookdb.gui import library  # noqa: E402
from bookdb.gui import main as gui_main  # noqa: E402
from bookdb.gui import settings  # noqa: E402
from bookdb.gui.server import create_server, server_url  # noqa: E402
from bookdb.services import books as book_service  # noqa: E402
from bookdb.services.context import Context  # noqa: E402


def make_library(parent: Path, name: str = "MyLib") -> Path:
    """建一个真实 Library 目录并返回其路径。"""
    target = parent / name
    library.Library.create(target)
    return target


# ═══════════════════════════════════════════════════════════ 应用设置
def test_settings_roundtrip():
    s = settings.Settings.load()
    assert s.is_empty

    s.touch("E:/Books", "我的图书")
    s.save()

    again = settings.Settings.load()
    assert again.last_library == Path("E:/Books")
    assert [r.name for r in again.recent] == ["我的图书"]


def test_settings_touch_dedupes_and_moves_to_front():
    s = settings.Settings.load()
    s.touch("A")
    s.touch("B")
    s.touch("A")
    assert [r.path.name for r in s.recent] == ["A", "B"]      # 去重并置顶
    assert s.last_library == Path("A")


def test_settings_caps_recent_length():
    s = settings.Settings.load()
    for i in range(settings.MAX_RECENT + 4):
        s.touch(f"Lib{i}")
    assert len(s.recent) == settings.MAX_RECENT


def test_settings_corrupt_file_is_ignored():
    """设置文件损坏 → 当空设置，**绝不因此起不来**。"""
    path = settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 这不是合法 JSON", encoding="utf-8")
    assert settings.Settings.load().is_empty


def test_settings_tolerates_garbage_entries():
    path = settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 1,
        "last_library": None,
        "recent_libraries": ["不是对象", {"no_path": 1}, {"path": "E:/Ok"}],
    }, ensure_ascii=False), encoding="utf-8")

    s = settings.Settings.load()
    assert [r.path.name for r in s.recent] == ["Ok"]          # 坏的条目被跳过


def test_settings_forget():
    s = settings.Settings.load()
    s.touch("A")
    s.touch("B")
    s.forget("A")
    assert [r.path.name for r in s.recent] == ["B"]
    s.forget("B")
    assert s.last_library is None


# ═══════════════════════════════════════════════════════════ Library 管理
def test_library_create_and_reopen(tmp_path: Path):
    target = tmp_path / "NewLib"
    lib = library.Library.create(target)

    assert lib.name == "NewLib"
    assert lib.db_path.exists()
    assert lib.config.library_root == target.resolve()

    # book.toml 里路径写成**相对**形式，便于整库搬迁
    text = (target / "book.toml").read_text(encoding="utf-8")
    assert 'library_root = "."' in text
    assert 'db_path = "book.db"' in text

    again = library.Library.open(target)
    assert again.path == target.resolve()
    assert again.name == "NewLib"


def test_library_open_rejects_plain_directory(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(library.NotALibraryError) as ei:
        library.Library.open(plain)
    assert "book.toml" in str(ei.value)


def test_library_open_rejects_missing_directory(tmp_path: Path):
    with pytest.raises(library.LibraryNotFoundError):
        library.Library.open(tmp_path / "nope")


def test_library_open_accepts_book_toml_path(tmp_path: Path):
    """允许直接传 book.toml 的完整路径（从「复制路径」拿到的形态）。"""
    target = make_library(tmp_path)
    assert library.Library.open(target / "book.toml").path == target.resolve()


def test_library_open_recreates_missing_db(tmp_path: Path):
    """有 book.toml 但缺 book.db → 幂等补建（绝不 DROP 已有数据）。"""
    target = make_library(tmp_path)
    (target / "book.db").unlink()

    lib = library.Library.open(target)
    assert lib.db_path.exists()


def test_library_create_refuses_existing_db_and_keeps_data(tmp_path: Path):
    target = tmp_path / "L"
    library.Library.create(target)

    with Context.open(Config.load(target / "book.toml")) as ctx:
        book_service.add_book(ctx, {"title": "别删我"})

    with pytest.raises(Exception):                # DatabaseExistsError
        library.Library.create(target)

    with Context.open(Config.load(target / "book.toml")) as ctx:
        assert [b["title"] for b in book_service.list_books(ctx)] == ["别删我"]


def test_probe_reports_reason_without_side_effects(tmp_path: Path):
    missing = library.probe(tmp_path / "nope")
    assert missing["valid"] is False and "不存在" in missing["reason"]

    # 只有 book.toml、没有 book.db —— probe 必须**不**创建数据库
    target = tmp_path / "Half"
    target.mkdir()
    (target / "book.toml").write_text(
        f'[paths]\nlibrary_root = "{target.as_posix()}"\ndb_path = "book.db"\n',
        encoding="utf-8",
    )
    result = library.probe(target)
    assert result["valid"] is True and result["has_db"] is False
    assert not (target / "book.db").exists()


def test_library_is_valid_path(tmp_path: Path):
    target = make_library(tmp_path)
    assert library.is_valid_path(target) is True
    assert library.is_valid_path(tmp_path / "nope") is False


# ═══════════════════════════════════════════════════════ 启动优先级
def test_startup_argument_opens_library(tmp_path: Path):
    target = make_library(tmp_path)
    config, notice = gui_main.resolve_startup([str(target)])

    assert config is not None and notice is None
    assert settings.Settings.load().last_library == target.resolve()


def test_startup_bad_argument_falls_back_to_welcome(tmp_path: Path):
    config, notice = gui_main.resolve_startup([str(tmp_path / "nope")])

    assert config is None
    assert notice and "无法打开" in notice        # 明确说明原因，但不阻断


def test_startup_uses_last_library_when_no_argument(tmp_path: Path):
    target = make_library(tmp_path)
    gui_main.resolve_startup([str(target)])       # 先打开过一次

    config, notice = gui_main.resolve_startup([])
    assert config is not None and notice is None


def test_startup_shows_welcome_when_nothing_known():
    config, notice = gui_main.resolve_startup([])
    assert config is None and notice is None


def test_startup_ignores_stale_last_library(tmp_path: Path):
    """上次的 Library 已失效 → 安静地退回欢迎页，不报错。"""
    target = make_library(tmp_path)
    gui_main.resolve_startup([str(target)])
    (target / "book.toml").unlink()

    config, notice = gui_main.resolve_startup([])
    assert config is None and notice is None


def test_startup_ignores_flags():
    """`--foo` 这类开关不算路径参数。"""
    config, notice = gui_main.resolve_startup(["--debug"])
    assert config is None and notice is None


# ═══════════════════════════════════════════════════════════ 接口
def _start(config):
    server = create_server(config)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = server_url(server)

    def call(method: str, path: str, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(base + path.lstrip("/"), data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    return server, call


def test_endpoints_without_library(tmp_path: Path):
    """无 Library 时：欢迎页接口可用，数据接口 409 + no_library。"""
    server, call = _start(None)
    try:
        status, meta = call("GET", "/api/meta")
        assert status == 200 and meta["no_library"] is True

        status, current = call("GET", "/api/library/current")
        assert status == 200 and current["no_library"] is True

        status, recent = call("GET", "/api/library/recent")
        assert status == 200 and recent["items"] == []

        status, books = call("GET", "/api/books")
        assert status == 409 and books["no_library"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_endpoint_meta_reports_library(tmp_path: Path):
    target = make_library(tmp_path)
    lib = library.Library.open(target)
    server, call = _start(lib.config)
    try:
        status, meta = call("GET", "/api/meta")
        assert status == 200
        assert meta["no_library"] is False
        assert meta["library_name"] == "MyLib"
        assert meta["library_path"] == str(target.resolve())
    finally:
        server.shutdown()
        server.server_close()


def test_endpoint_probe_and_create(tmp_path: Path):
    server, call = _start(None)
    try:
        status, bad = call("POST", "/api/library/probe", {"path": str(tmp_path / "nope")})
        assert status == 200 and bad["valid"] is False

        target = tmp_path / "Created"
        status, created = call("POST", "/api/library/create", {"path": str(target)})
        assert status == 200 and created["name"] == "Created"
        assert (target / "book.toml").is_file()

        status, ok = call("POST", "/api/library/probe", {"path": str(target)})
        assert status == 200 and ok["valid"] is True and ok["has_db"] is True

        status, again = call("POST", "/api/library/create", {"path": str(target)})
        assert status == 409                       # 已有数据库 → 拒绝，不清空
    finally:
        server.shutdown()
        server.server_close()


def test_endpoint_open_switches_library_live(tmp_path: Path):
    """`/api/library/open` 立即生效：无需重启进程，后续请求即指向新库。"""
    first = make_library(tmp_path, "First")
    second = make_library(tmp_path, "Second")

    with Context.open(Config.load(second / "book.toml")) as ctx:
        book_service.add_book(ctx, {"title": "第二库的书"})

    lib = library.Library.open(first)
    server, call = _start(lib.config)
    try:
        status, meta = call("GET", "/api/meta")
        assert meta["library_name"] == "First"

        status, opened = call("POST", "/api/library/open", {"path": str(second)})
        assert status == 200 and opened["name"] == "Second"

        # 同一进程、同一服务，配置已被替换
        status, meta = call("GET", "/api/meta")
        assert meta["library_name"] == "Second"
        assert meta["count"] == 1

        _s, books = call("GET", "/api/books")
        assert [b["title"] for b in books["items"]] == ["第二库的书"]

        # 最近列表已更新
        _s, recent = call("GET", "/api/library/recent")
        assert recent["items"][0]["name"] == "Second"
        assert recent["last_library"] == str(second.resolve())
    finally:
        server.shutdown()
        server.server_close()


def test_endpoint_open_rejects_bad_path(tmp_path: Path):
    server, call = _start(None)
    try:
        status, payload = call("POST", "/api/library/open", {"path": str(tmp_path / "nope")})
        assert status == 400 and "不存在" in payload["error"]

        status, payload = call("POST", "/api/library/open", {})
        assert status == 400 and "path" in payload["error"]
    finally:
        server.shutdown()
        server.server_close()
