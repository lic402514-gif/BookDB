"""M1 验收测试 —— 数据库与 CLI 核心闭环（PRD §27 闭环一/二）。

覆盖：init / add / list / show / edit / delete / search
以及字段规范化、重复检测、路径解析、abstract 不出现在列表。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bookdb.config import Config  # noqa: E402
from bookdb.db import database  # noqa: E402
from bookdb.normalize import (  # noqa: E402
    normalize_edition,
    normalize_isbn,
    parse_year_range,
)
from bookdb.paths import PathSyntaxError, resolve, to_stored  # noqa: E402
from bookdb.services.format import format_edition  # noqa: E402
from bookdb.services import books as book_service  # noqa: E402
from bookdb.services import search as search_service  # noqa: E402
from bookdb.services.context import Context  # noqa: E402


# ------------------------------------------------------------------- fixtures
@pytest.fixture()
def ctx(tmp_path: Path) -> Context:
    config = Config(db_path=tmp_path / "book.db", library_root=tmp_path / "books")
    config.library_root.mkdir(parents=True, exist_ok=True)
    conn = database.connect(config.db_path)
    database.initialize(conn)
    context = Context(config=config, conn=conn)
    yield context
    context.close()


SAMPLE = {
    "title": "否定辩证法",
    "author": "阿多尔诺",
    "translator": "王凤才",
    "publisher": "商务印书馆",
    "publication_place": "北京",
    "publication_year": "2019",
    "edition": "第1版",
    "isbn": "978-7-100-16318-0",
    "clc_code": "B0",
    "abstract": "这是一段很长的摘要" * 50,
}


# ───────────────────────────────────────────────────────────── normalization
def test_edition_normalization():
    assert normalize_edition("第2版") == "2"
    assert normalize_edition("2版") == "2"
    assert normalize_edition("2") == "2"
    assert normalize_edition("第二版") == "2"
    assert normalize_edition(None) is None
    assert normalize_edition("") is None
    assert format_edition("2") == "第2版"
    assert format_edition(None) is None


def test_isbn_normalization():
    assert normalize_isbn("978-7-100-16318-0") == "9787100163180"
    assert normalize_isbn(" 978 7100163180 ") == "9787100163180"
    assert normalize_isbn(None) is None


def test_year_range_syntax():
    assert parse_year_range("2019") == (2019, 2019)
    assert parse_year_range("1800-1900") == (1800, 1900)
    assert parse_year_range("1800-") == (1800, None)
    with pytest.raises(ValueError):
        parse_year_range("1900-1800")
    with pytest.raises(ValueError):
        parse_year_range("abc")


def test_path_resolution(tmp_path: Path):
    root = tmp_path / "lib"
    root.mkdir()
    assert to_stored(root / "a.pdf", root) == "a.pdf"          # 库内 → 相对
    absolute = tmp_path / "outside" / "b.pdf"
    assert to_stored(absolute, root) == str(absolute)          # 库外 → 绝对
    assert resolve("a.pdf", root) == (root / "a.pdf").resolve()
    assert resolve(str(absolute), root) == absolute


def test_path_strips_surrounding_whitespace(tmp_path: Path):
    """首尾空白（复制粘贴常见）应被去掉，而不是当成相对路径拼接。"""
    root = tmp_path / "lib"
    root.mkdir()
    raw = str(root / "a.pdf")
    assert to_stored(f"  {raw}  ", root) == "a.pdf"
    assert resolve(f"  {raw}  ", root) == (root / "a.pdf").resolve()


def test_path_strips_surrounding_quotes(tmp_path: Path):
    """资源管理器「复制路径」带引号 → 自动去掉，不误判为相对路径。"""
    root = tmp_path / "lib"
    root.mkdir()
    raw = str(root / "a.pdf")
    assert to_stored(f'"{raw}"', root) == "a.pdf"
    assert resolve(f'"{raw}"', root) == (root / "a.pdf").resolve()


def test_path_strips_cjk_quotes_and_invisible_chars(tmp_path: Path):
    """中文引号 “…”（左右不同字符）与零宽字符 → 无损去除，绝不叠加。"""
    root = tmp_path / "lib"
    root.mkdir()
    raw = str(root / "a.pdf")
    for wrapped in (f"“{raw}”", f"‘{raw}’", f"\u200b{raw}", f"\ufeff{raw}"):
        assert to_stored(wrapped, root) == "a.pdf", wrapped
        assert resolve(wrapped, root) == (root / "a.pdf").resolve(), wrapped


def test_path_rejects_fullwidth_separators(tmp_path: Path):
    """全角分隔符 ＼ ／ → 明确报错（否则静默存成错误路径）。"""
    root = tmp_path / "lib"
    root.mkdir()
    for bad in ("E:＼Books＼x.pdf", "E:/Books／x.pdf"):
        with pytest.raises(PathSyntaxError):
            to_stored(bad, root)


def test_path_rejects_fullwidth_colon(tmp_path: Path):
    """盘符用全角冒号（中文输入法）→ 明确报错，绝不静默拼接成 E:\\Books\\E:\\...。"""
    root = tmp_path / "lib"
    root.mkdir()
    bad = "E：\\Books\\example.pdf"
    with pytest.raises(PathSyntaxError) as ei:
        to_stored(bad, root)
    assert "全角" in str(ei.value)
    with pytest.raises(PathSyntaxError):
        resolve(bad, root)


def test_path_rejects_fullwidth_drive_letter(tmp_path: Path):
    """全角字母盘符（Ｅ：，输入法全角模式）→ 报错，不叠加。"""
    root = tmp_path / "lib"
    root.mkdir()
    for bad in ("Ｅ：\\Books\\x.pdf", "Ｅ:\\Books\\x.pdf", "ｅ：\\Books\\x.pdf"):
        with pytest.raises(PathSyntaxError):
            to_stored(bad, root)


def test_path_never_doubles_prefix(tmp_path: Path):
    """回归：任何输入都不应产出「库根 + 绝对路径」的叠加路径。"""
    root = tmp_path / "lib"
    root.mkdir()
    for raw in (str(root / "a.pdf"), "a.pdf", f"  {str(root / 'a.pdf')}  "):
        stored = to_stored(raw, root)
        got = resolve(stored, root)
        assert str(got).count(str(root)) <= 1, f"路径被叠加：{got}"


# ───────────────────────────────────────────────────────────────── business
def test_add_and_get(ctx: Context):
    book_id = book_service.add_book(ctx, SAMPLE)
    book = book_service.get_book(ctx, book_id)

    assert book["title"] == "否定辩证法"
    assert book["edition"] == "1"                    # 规范化
    assert book["isbn"] == "9787100163180"           # 去连字符
    assert book["publication_year"] == 2019
    assert book["publisher_name"] == "商务印书馆"    # 出版社自动创建
    assert book["literature_type"] == "M"            # 默认值


def test_duplicate_detection(ctx: Context):
    book_service.add_book(ctx, SAMPLE)
    with pytest.raises(book_service.DuplicateBookError):
        book_service.add_book(ctx, SAMPLE)           # 同 ISBN → 重复
    again = book_service.add_book(ctx, SAMPLE, allow_duplicate=True)
    assert again > 0


def test_list_excludes_abstract(ctx: Context):
    book_service.add_book(ctx, SAMPLE)
    rows = book_service.list_books(ctx)
    assert rows and "abstract" not in rows[0]        # §8.3：列表不含摘要


def test_edit_partial(ctx: Context):
    book_id = book_service.add_book(ctx, SAMPLE)
    updated = book_service.update_book(ctx, book_id, {"clc_code": "B516"})
    assert updated["clc_code"] == "B516"
    assert updated["title"] == "否定辩证法"          # 未传字段保持原值


def test_edit_title_cannot_be_empty(ctx: Context):
    book_id = book_service.add_book(ctx, SAMPLE)
    with pytest.raises(ValueError):
        book_service.update_book(ctx, book_id, {"title": "   "})


def test_delete(ctx: Context):
    book_id = book_service.add_book(ctx, SAMPLE)
    book_service.delete_book(ctx, book_id)
    with pytest.raises(book_service.BookNotFoundError):
        book_service.get_book(ctx, book_id)


def test_search_filters(ctx: Context):
    book_service.add_book(ctx, SAMPLE)
    book_service.add_book(ctx, {**SAMPLE, "title": "资本论", "author": "马克思",
                                "isbn": "9787010041155", "publication_year": "2004",
                                "clc_code": "A81", "publisher": "人民出版社"})

    assert len(search_service.search(ctx, "否定")) == 1
    assert len(search_service.search(ctx, author="马克思")) == 1
    assert len(search_service.search(ctx, clc="A81")) == 1
    # 年份区间：库里是 2019 与 2004
    assert len(search_service.search(ctx, year="2000-2020")) == 2
    assert len(search_service.search(ctx, year="1900-2000")) == 0
    assert len(search_service.search(ctx, year="2004-")) == 2
    assert len(search_service.search(ctx, year="2019")) == 1
    assert len(search_service.search(ctx, publisher="商务")) == 1
    assert len(search_service.search(ctx, publisher="人民")) == 1
    assert len(search_service.search(ctx, has_file=False)) == 2
    assert len(search_service.search(ctx)) == 2
