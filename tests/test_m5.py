"""M5 验收测试 —— GUI 内部 HTTP 端点（PRD §16）。

不开窗口，只测 HTTP 层：起真实服务（127.0.0.1:随机端口）→ urllib 打接口 → 断言。
pywebview 窗口本身需图形环境，不在单测范围。
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bookdb.config import Config  # noqa: E402
from bookdb.db import database  # noqa: E402
from bookdb.gui.server import create_server, server_url  # noqa: E402


@pytest.fixture()
def api(tmp_path: Path):
    config = Config(db_path=tmp_path / "b.db", library_root=tmp_path / "lib")
    config.library_root.mkdir()
    conn = database.connect(config.db_path)
    database.initialize(conn)
    conn.close()

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
            raw = exc.read().decode("utf-8")
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, {"raw": raw}

    def raw_get(path: str):
        try:
            with urllib.request.urlopen(base + path.lstrip("/"), timeout=5) as resp:
                return resp.status, resp.headers.get("Content-Type", ""), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Content-Type", ""), exc.read()

    yield type("Api", (), {"call": staticmethod(call), "raw": staticmethod(raw_get),
                           "config": config, "tmp": tmp_path})

    server.shutdown()
    server.server_close()


SAMPLE = {
    "title": "否定辩证法", "author": "阿多尔诺", "translator": "王凤才",
    "publisher": "商务印书馆", "publication_place": "北京",
    "publication_year": "2019", "edition": "第1版",
    "isbn": "978-7-100-16318-0", "clc_code": "B0", "literature_type": "M",
}


def add(api, **kw):
    status, payload = api.call("POST", "/api/books", {**SAMPLE, **kw})
    assert status == 200, payload
    return payload["id"]


# ───────────────────────────────────────────────────────────── 静态与元信息
def test_serves_index_html(api):
    status, ctype, body = api.raw("/")
    assert status == 200
    assert "text/html" in ctype
    assert b"bookdb" in body


def test_serves_css_and_js(api):
    for path, needle in (("/style.css", b"--accent"), ("/app.js", b"loadList")):
        status, _ctype, body = api.raw(path)
        assert status == 200 and needle in body


def test_static_path_traversal_blocked(api):
    status, _ctype, _body = api.raw("/../pyproject.toml")
    assert status == 404


def test_meta(api):
    status, payload = api.call("GET", "/api/meta")
    assert status == 200
    assert payload["count"] == 0
    assert payload["library_root"]
    assert payload["model"]


# ─────────────────────────────────────────────────────────────── CRUD
def test_add_list_detail(api):
    book_id = add(api, abstract="一段摘要")

    status, payload = api.call("GET", "/api/books")
    assert status == 200 and len(payload["items"]) == 1
    assert "abstract" not in payload["items"][0]        # 列表不含摘要（§8.3）

    status, book = api.call("GET", f"/api/books/{book_id}")
    assert status == 200
    assert book["title"] == "否定辩证法"
    assert book["edition"] == "1"                       # 规范化
    assert book["isbn"] == "9787100163180"
    assert book["publisher_name"] == "商务印书馆"
    assert book["abstract"] == "一段摘要"                # 详情含摘要


def test_add_duplicate_returns_409(api):
    add(api)
    status, payload = api.call("POST", "/api/books", SAMPLE)
    assert status == 409
    assert payload["duplicates"]


def test_add_duplicate_with_flag_succeeds(api):
    add(api)
    status, payload = api.call("POST", "/api/books", {**SAMPLE, "allow_duplicate": True})
    assert status == 200 and payload["id"]


def test_update_and_delete(api):
    book_id = add(api)
    status, book = api.call("PUT", f"/api/books/{book_id}", {"clc_code": "B516"})
    assert status == 200 and book["clc_code"] == "B516"
    assert book["title"] == "否定辩证法"                 # 未提供字段保留

    status, _ = api.call("DELETE", f"/api/books/{book_id}")
    assert status == 200
    status, _ = api.call("GET", f"/api/books/{book_id}")
    assert status == 404


def test_get_missing_returns_404(api):
    status, _ = api.call("GET", "/api/books/999")
    assert status == 404


# ─────────────────────────────────────────────────────────────── 检索
def test_search(api):
    add(api)
    add(api, title="资本论", author="马克思", isbn="9787010041155",
        publisher="人民出版社", publication_year="2004", clc_code="A81")

    status, payload = api.call("GET", "/api/search?q=" + urllib.parse.quote("资本"))
    assert status == 200 and len(payload["items"]) == 1

    status, payload = api.call("GET", "/api/search?clc=A&year=2000-2020")
    assert len(payload["items"]) == 1

    status, payload = api.call("GET", "/api/search?year=abc")
    assert status == 400                                # 非法年份 → 400


# ─────────────────────────────────────────────────────────────── 导出
def test_export_json_and_csv(api):
    add(api)
    status, payload = api.call("GET", "/api/export?format=json")
    assert status == 200 and payload["format"] == "json"
    assert json.loads(payload["content"])["mode"] == "full"

    status, payload = api.call("GET", "/api/export?format=csv")
    assert status == 200 and payload["content"].startswith("id,title")

    status, payload = api.call("GET", "/api/export?format=xml")
    assert status == 400


# ─────────────────────────────────────────────────────────────── 引用
def test_cite(api):
    book_id = add(api)                     # SAMPLE 的 edition="第1版" → 规范化为 "1"
    status, payload = api.call("GET", f"/api/cite/{book_id}")
    assert status == 200
    # 国标：第 1 版不著录（PRD §15 定稿）
    assert payload["citation"] == "阿多尔诺. 否定辩证法[M]. 王凤才, 译. 北京: 商务印书馆, 2019."


def test_cite_non_first_edition(api):
    book_id = add(api, edition="2")
    status, payload = api.call("GET", f"/api/cite/{book_id}")
    assert status == 200
    # 国标：非第1版写作「2版」
    assert payload["citation"] == "阿多尔诺. 否定辩证法[M]. 王凤才, 译. 2版. 北京: 商务印书馆, 2019."


def test_cite_without_edition(api):
    book_id = add(api, edition=None)
    status, payload = api.call("GET", f"/api/cite/{book_id}")
    assert status == 200
    assert payload["citation"] == "阿多尔诺. 否定辩证法[M]. 王凤才, 译. 北京: 商务印书馆, 2019."


# ─────────────────────────────────────────────────────────────── 导入
def test_import_preview_and_apply(api):
    content = json.dumps({"mode": "full", "items": [
        {"title": "资本论", "author": "马克思"},
    ]})
    status, plan = api.call("POST", "/api/import/preview", {"format": "json", "content": content})
    assert status == 200 and plan["ok"] and plan["total"] == 1

    status, result = api.call("POST", "/api/import/apply", {"format": "json", "content": content})
    assert status == 200 and result["inserts"] == 1

    status, payload = api.call("GET", "/api/books")
    assert len(payload["items"]) == 1


def test_import_preview_reports_errors(api):
    content = json.dumps({"mode": "patch", "items": [{"id": 999, "clc_code": "B0"}]})
    status, plan = api.call("POST", "/api/import/preview", {"format": "json", "content": content})
    assert status == 200 and plan["ok"] is False and plan["errors"]


def test_import_apply_rejects_invalid(api):
    content = json.dumps({"items": [{"title": "x"}]})   # 缺 mode
    status, payload = api.call("POST", "/api/import/apply", {"format": "json", "content": content})
    assert status == 400 and payload["errors"]


def test_import_csv(api):
    content = "title,author\n资本论,马克思\n"
    status, result = api.call("POST", "/api/import/apply", {"format": "csv", "content": content})
    assert status == 200 and result["inserts"] == 1


# ─────────────────────────────────────────────────────────────── 重命名
def test_rename_preview_and_apply(api):
    (api.tmp / "lib" / "old.pdf").write_bytes(b"%PDF-1.4 dummy")
    book_id = add(api, file_path="old.pdf")

    status, plan = api.call("GET", f"/api/rename/{book_id}")
    assert status == 200 and plan["changed"]
    assert plan["new_name"] == "阿多尔诺_否定辩证法[M]_王凤才_北京：商务印书馆_2019(1).pdf"

    status, result = api.call("POST", f"/api/rename/{book_id}/apply", {})
    assert status == 200 and result["ok"]
    assert (api.tmp / "lib" / plan["new_name"]).exists()
    assert not (api.tmp / "lib" / "old.pdf").exists()


def test_rename_without_file_returns_409(api):
    book_id = add(api)
    status, payload = api.call("GET", f"/api/rename/{book_id}")
    assert status == 409
    assert "未关联文件" in payload["error"]


# ─────────────────────────────────────────────────────────────── 版权页提取
def test_enrich_and_save(api, monkeypatch):
    from bookdb.services import enrich as enrich_service

    monkeypatch.setattr(
        enrich_service, "extract_metadata",
        lambda vlm, image: {"title": "否定辩证法", "author": "阿多尔诺",
                            "translator": None, "publisher": "商务印书馆",
                            "publication_place": "北京", "publication_year": 2019,
                            "edition": "1", "isbn": "9787100163180"},
    )
    img = api.tmp / "page.jpg"
    img.write_bytes(b"fake-image-bytes")

    status, result = api.call("POST", "/api/enrich", {"path": str(img)})
    assert status == 200 and result["metadata"]["title"] == "否定辩证法"
    assert result["source"] == "image"

    status, saved = api.call("POST", "/api/enrich/save", {"metadata": result["metadata"]})
    assert status == 200 and saved["id"]

    status, payload = api.call("GET", "/api/books")
    assert len(payload["items"]) == 1


def test_enrich_missing_path_400(api):
    status, _ = api.call("POST", "/api/enrich", {})
    assert status == 400


# ─────────────────────────────────────────────────────────────── 协议行为
def test_unknown_api_404(api):
    status, _ = api.call("GET", "/api/nope")
    assert status == 404


def test_wrong_method_405(api):
    status, _ = api.call("DELETE", "/api/books")
    assert status == 405


# ─────────────────────────────────────────────────── 打开文件（系统默认程序）
def test_open_file(api, monkeypatch):
    from bookdb.gui import server as gui_server

    (api.tmp / "lib" / "book.pdf").write_bytes(b"%PDF-1.4 dummy")
    book_id = add(api, file_path="book.pdf")

    calls = []
    monkeypatch.setattr(gui_server, "launch", lambda p: calls.append(p))

    status, payload = api.call("POST", f"/api/open/{book_id}", {})
    assert status == 200 and payload["ok"]
    assert len(calls) == 1 and calls[0].name == "book.pdf"


def test_open_file_without_attachment_409(api, monkeypatch):
    from bookdb.gui import server as gui_server

    monkeypatch.setattr(gui_server, "launch", lambda p: None)
    book_id = add(api)                                  # 无 file_path
    status, payload = api.call("POST", f"/api/open/{book_id}", {})
    assert status == 409 and "未关联文件" in payload["error"]


def test_open_file_missing_on_disk_404(api, monkeypatch):
    from bookdb.gui import server as gui_server

    monkeypatch.setattr(gui_server, "launch", lambda p: None)
    book_id = add(api, file_path="nope.pdf")            # 记录有路径，磁盘没有
    status, payload = api.call("POST", f"/api/open/{book_id}", {})
    assert status == 404 and "文件不存在" in payload["error"]


def test_open_file_unknown_id_404(api, monkeypatch):
    from bookdb.gui import server as gui_server

    monkeypatch.setattr(gui_server, "launch", lambda p: None)
    status, _ = api.call("POST", "/api/open/999", {})
    assert status == 404


def test_reveal_file(api, monkeypatch):
    from bookdb.gui import server as gui_server

    (api.tmp / "lib" / "book.pdf").write_bytes(b"%PDF-1.4 dummy")
    book_id = add(api, file_path="book.pdf")

    calls = []
    monkeypatch.setattr(gui_server, "reveal", lambda p: calls.append(p))

    status, payload = api.call("POST", f"/api/reveal/{book_id}", {})
    assert status == 200 and payload["ok"]
    assert len(calls) == 1 and calls[0].name == "book.pdf"


# ─────────────────────────────────────────── 文件路径双模式（走 GUI 接口）
def test_gui_file_path_absolute_inside_root_stored_relative(api):
    lib = Path(api.config.library_root)
    p = lib / "in.pdf"
    p.write_bytes(b"%PDF-1.4")
    book_id = add(api, title="内", file_path=str(p))
    _status, detail = api.call("GET", f"/api/books/{book_id}")
    assert detail["file_path"] == "in.pdf"


def test_gui_file_path_absolute_outside_root_kept_absolute(api):
    lib = Path(api.config.library_root)
    p = lib.parent / "out.pdf"
    p.write_bytes(b"%PDF-1.4")
    book_id = add(api, title="外", file_path=str(p))
    _status, detail = api.call("GET", f"/api/books/{book_id}")
    assert detail["file_path"] == str(p)


def test_edit_form_absolute_outside_root_kept_absolute(api):
    """编辑表单（PUT）填入库根之外的绝对路径 → 原样保留。"""
    book_id = add(api)
    lib = Path(api.config.library_root)
    p = lib.parent / "out.pdf"
    p.write_bytes(b"%PDF-1.4")
    status, _ = api.call("PUT", f"/api/books/{book_id}", {**SAMPLE, "file_path": str(p)})
    assert status == 200
    _s, detail = api.call("GET", f"/api/books/{book_id}")
    assert detail["file_path"] == str(p), detail["file_path"]


def test_edit_form_absolute_inside_root_becomes_relative(api):
    """编辑表单填入库根内的绝对路径 → 归一化为相对（PRD §6.1 推荐用法）。"""
    book_id = add(api)
    lib = Path(api.config.library_root)
    p = lib / "in.pdf"
    p.write_bytes(b"%PDF-1.4")
    status, _ = api.call("PUT", f"/api/books/{book_id}", {**SAMPLE, "file_path": str(p)})
    assert status == 200
    _s, detail = api.call("GET", f"/api/books/{book_id}")
    assert detail["file_path"] == "in.pdf"
