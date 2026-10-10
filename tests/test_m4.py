"""M4 验收测试 —— 本地 VLM 版权页提取（PRD §12 / §13）。

设计：VLM 用 fake adapter 注入，**不依赖 Ollama 是否安装**；
PDF 用 PyMuPDF 现场生成，验证版权页定位与渲染。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bookdb import pdf as pdf_mod  # noqa: E402
from bookdb.config import Config  # noqa: E402
from bookdb.llm.adapters.ollama import OllamaAdapter  # noqa: E402
from bookdb.llm.base import ModelUnavailableError  # noqa: E402
from bookdb.services import enrich as enrich_service  # noqa: E402
from bookdb.services import llm as llm_service  # noqa: E402


class _FakeAdapter:
    """记录入参并回放固定文本的假适配器。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.seen_image: bytes | None = None

    def describe_image(self, image: bytes, prompt: str) -> str:
        self.seen_image = image
        return self.reply


# ─────────────────────────────────────────────────────── 模型输出解析
def test_parse_bare_json():
    meta = llm_service.parse_metadata(
        '{"title":"否定辩证法","author":["阿多尔诺"],"isbn":"978-7-100-16318-0"}'
    )
    assert meta["title"] == "否定辩证法"
    assert meta["author"] == "阿多尔诺"
    assert meta["isbn"] == "9787100163180"           # 去连字符


def test_parse_fenced_json():
    raw = '```json\n{"title":"资本论"}\n```'
    assert llm_service.parse_metadata(raw)["title"] == "资本论"


def test_parse_with_surrounding_prose():
    raw = '好的，结果如下：{"title":"资本论","author":["马克思","恩格斯"]} 希望有帮助。'
    meta = llm_service.parse_metadata(raw)
    assert meta["title"] == "资本论"
    assert meta["author"] == "马克思；恩格斯"          # 多值以「；」拼接


def test_parse_nulls_become_none():
    meta = llm_service.parse_metadata(
        '{"title":null,"author":null,"publication_year":null}'
    )
    assert meta["title"] is None
    assert meta["author"] is None
    assert meta["publication_year"] is None


def test_parse_bad_year_falls_back_to_none():
    meta = llm_service.parse_metadata('{"title":"x","publication_year":"不是数字"}')
    assert meta["publication_year"] is None            # 不猜测、不崩溃


def test_parse_without_json_raises():
    with pytest.raises(ValueError):
        llm_service.parse_metadata("模型没有输出任何 JSON")


# ─────────────────────────────────────────────────────────── PDF 处理
def _pymupdf():
    try:
        import pymupdf
        return pymupdf
    except ImportError:
        return pytest.importorskip("fitz")


def _make_pdf(path: Path, pages: list[str]) -> None:
    fitz = _pymupdf()
    doc = fitz.open()
    for text in pages:
        doc.new_page().insert_text((72, 72), text, fontsize=12)
    doc.save(str(path))
    doc.close()


def test_locate_copyright_page(tmp_path: Path):
    path = tmp_path / "book.pdf"
    _make_pdf(path, ["chapter one", "chapter two", "x", "y", "ISBN 978-7-100-16318-0"])
    assert pdf_mod.locate_copyright_page(path) == 5     # 关键词命中第 5 页


def test_locate_copyright_page_not_found(tmp_path: Path):
    path = tmp_path / "plain.pdf"
    _make_pdf(path, ["hello", "world"])                 # 无任何关键词
    assert pdf_mod.locate_copyright_page(path) is None


def test_render_page_png(tmp_path: Path):
    path = tmp_path / "book.pdf"
    _make_pdf(path, ["page one", "page two"])
    data = pdf_mod.render_page_png(path, 1)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"             # PNG 魔数
    with pytest.raises(ValueError):
        pdf_mod.render_page_png(path, 99)               # 页码越界


# ──────────────────────────────────────────────────────── enrich 编排
def _config(tmp_path: Path) -> Config:
    return Config(db_path=tmp_path / "b.db", library_root=tmp_path / "lib")


def test_enrich_from_image(tmp_path: Path, monkeypatch):
    (tmp_path / "lib").mkdir()
    img = tmp_path / "copyright.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes")

    reply = ('{"title":"否定辩证法","author":["阿多尔诺"],"publisher":"商务印书馆",'
             '"publication_place":"北京","publication_year":2019,"edition":"1",'
             '"isbn":"9787100163180"}')
    fake = _FakeAdapter(reply)
    monkeypatch.setattr(llm_service, "get_adapter", lambda vlm: fake)

    result = enrich_service.enrich_from_image(_config(tmp_path), img)
    assert result.source == "image" and result.page is None
    assert result.metadata["title"] == "否定辩证法"
    assert result.metadata["publication_year"] == 2019
    assert result.metadata["edition"] == "1"
    assert fake.seen_image == img.read_bytes()          # 图片确实交给了模型


def test_enrich_from_pdf_locates_and_links_file(tmp_path: Path, monkeypatch):
    lib = tmp_path / "lib"
    lib.mkdir()
    path = lib / "book.pdf"
    _make_pdf(path, ["a", "b", "c", "d", "ISBN 9787100163180"])

    monkeypatch.setattr(llm_service, "get_adapter", lambda vlm: _FakeAdapter('{"title":"书"}'))
    result = enrich_service.enrich_from_pdf(_config(tmp_path), path)

    assert result.source == "pdf" and result.page == 5
    assert result.metadata["title"] == "书"
    assert result.metadata["file_path"] == str(path)    # 顺手关联源 PDF


def test_enrich_from_pdf_manual_page(tmp_path: Path, monkeypatch):
    lib = tmp_path / "lib"
    lib.mkdir()
    path = lib / "plain.pdf"
    _make_pdf(path, ["hello", "world", "copyright"])    # 无关键词 → 自动定位会失败

    monkeypatch.setattr(llm_service, "get_adapter", lambda vlm: _FakeAdapter('{"title":"书"}'))
    result = enrich_service.enrich_from_pdf(_config(tmp_path), path, page=3)
    assert result.page == 3


def test_enrich_from_pdf_no_copyright_page(tmp_path: Path, monkeypatch):
    lib = tmp_path / "lib"
    lib.mkdir()
    path = lib / "plain.pdf"
    _make_pdf(path, ["hello", "world"])

    monkeypatch.setattr(llm_service, "get_adapter", lambda vlm: _FakeAdapter('{"title":"x"}'))
    with pytest.raises(enrich_service.CopyrightPageNotFound):
        enrich_service.enrich_from_pdf(_config(tmp_path), path)


# ───────────────────────────────────────────── 模型不可用 → 明确报错
def test_ollama_adapter_unavailable_raises():
    # 指向必然拒绝连接的端口
    adapter = OllamaAdapter(endpoint="http://127.0.0.1:1", model="qwen2.5-vl:3b", timeout=2.0)
    with pytest.raises(ModelUnavailableError):
        adapter.describe_image(b"fake", "prompt")
