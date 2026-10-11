"""扫描建库测试（services/scan.py）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bookdb.config import Config  # noqa: E402
from bookdb.db import database  # noqa: E402
from bookdb.services import books as book_service  # noqa: E402
from bookdb.services import scan as scan_service  # noqa: E402
from bookdb.services.context import Context  # noqa: E402


@pytest.fixture()
def ctx(tmp_path: Path):
    lib = tmp_path / "lib"
    lib.mkdir()
    config = Config(db_path=tmp_path / "book.db", library_root=lib)
    conn = database.connect(config.db_path)
    database.initialize(conn)
    conn.close()
    with Context.open(config) as c:
        yield c


def _touch(path: Path, content: bytes = b"%PDF-1.4 dummy") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# ────────────────────────────────────────────────────────── 文件名解析
def test_parse_own_template():
    meta = scan_service.parse_filename(
        "韦斯利·霍菲尔德_司法推理中应用的基本法律概念（修订本）[M]_张书友_北京：商务印书馆_2022.pdf"
    )
    assert meta["title"] == "司法推理中应用的基本法律概念（修订本）"
    assert meta["author"] == "韦斯利·霍菲尔德"
    assert meta["translator"] == "张书友"
    assert meta["publication_place"] == "北京"
    assert meta["publisher"] == "商务印书馆"
    assert meta["publication_year"] == "2022"
    assert meta["literature_type"] == "M"


def test_parse_own_template_with_edition():
    meta = scan_service.parse_filename(
        "Ernest Mandel_论欧洲共产主义[M]_齐春子, 胡尔湖_武汉：湖北人民出版社_1982(1).pdf"
    )
    assert meta["title"] == "论欧洲共产主义"
    assert meta["author"] == "Ernest Mandel"
    assert meta["publication_year"] == "1982"
    assert meta["edition"] == "1"


def test_parse_heuristic_strips_noise_and_series():
    meta = scan_service.parse_filename(
        "[世界社会主义研究丛书•参考系列60]美国社会主义传统 ([美]约翰·尼古拉斯)"
        " (z-library.sk, 1lib.sk, z-lib.sk).pdf"
    )
    assert meta["title"] == "美国社会主义传统"
    assert meta["author"] == "[美]约翰·尼古拉斯"
    assert meta["series"] == "世界社会主义研究丛书•参考系列60"


def test_parse_heuristic_chinese_edition():
    meta = scan_service.parse_filename("法理学 第二版 (马工程《法理学》编写组) (Z-Library).pdf")
    assert meta["title"] == "法理学"
    assert meta["edition"] == "2"
    assert meta["author"] == "马工程《法理学》编写组"


def test_parse_heuristic_strips_role_suffix():
    meta = scan_service.parse_filename("改造的诞生 (王良卿著) (z-library.sk, 1lib.sk).pdf")
    assert meta["title"] == "改造的诞生"
    assert meta["author"] == "王良卿"


def test_parse_keeps_title_when_nothing_matches():
    meta = scan_service.parse_filename("随便一个文件名.pdf")
    assert meta["title"] == "随便一个文件名"


# ────────────────────────────────────────────────────────────── 扫描
def test_scan_directory_recursive_and_suffix(tmp_path: Path):
    _touch(tmp_path / "a.pdf")
    _touch(tmp_path / "sub" / "b.pdf")
    _touch(tmp_path / "c.txt")
    assert len(scan_service.scan_directory(tmp_path)) == 2
    assert len(scan_service.scan_directory(tmp_path, recursive=False)) == 1


def test_scan_directory_missing_raises(tmp_path: Path):
    with pytest.raises(scan_service.DirectoryNotFoundError):
        scan_service.scan_directory(tmp_path / "nope")


# ────────────────────────────────────────────────────────────── 计划
def test_build_plan_makes_candidates(ctx: Context):
    _touch(ctx.config.library_root / "改造的诞生 (王良卿著) (z-library.sk, 1lib.sk).pdf")
    plan = scan_service.build_plan(ctx, ctx.config.library_root)
    assert plan.total_files == 1 and plan.new_count == 1
    cand = plan.candidates[0]
    assert cand.meta["title"] == "改造的诞生"
    assert cand.stored == "改造的诞生 (王良卿著) (z-library.sk, 1lib.sk).pdf"
    assert cand.creatable


def test_build_plan_skips_already_imported(ctx: Context):
    _touch(ctx.config.library_root / "x.pdf")
    book_service.add_book(ctx, {"title": "已存在", "file_path": "x.pdf"})
    plan = scan_service.build_plan(ctx, ctx.config.library_root)
    assert plan.skipped_existing == 1 and plan.new_count == 0
    assert plan.candidates[0].already["title"] == "已存在"
    assert not plan.candidates[0].creatable


def test_build_plan_flags_duplicate_ignoring_year(ctx: Context):
    _touch(ctx.config.library_root / "韦斯利·霍菲尔德_司法推理[M]_北京：商务印书馆_2022.pdf")
    book_service.add_book(ctx, {"title": "司法推理", "author": "韦斯利·霍菲尔德"})
    plan = scan_service.build_plan(ctx, ctx.config.library_root)
    assert plan.dup_count == 1
    assert plan.candidates[0].creatable          # 仍可建，交用户决定


# ────────────────────────────────────────────────────────────── 执行
def test_apply_plan_honours_selection(ctx: Context):
    _touch(ctx.config.library_root / "一号 (甲).pdf")
    _touch(ctx.config.library_root / "二号 (乙).pdf")
    plan = scan_service.build_plan(ctx, ctx.config.library_root)
    assert plan.new_count == 2

    result = scan_service.apply_plan(ctx, plan, selected={0})
    assert result.created_count == 1 and result.skipped == 1
    assert [b["title"] for b in book_service.list_books(ctx)] == ["一号"]


def test_apply_plan_writes_parsed_fields(ctx: Context):
    _touch(ctx.config.library_root / "阿多尔诺_否定辩证法[M]_王凤才_北京：商务印书馆_2019(1).pdf")
    plan = scan_service.build_plan(ctx, ctx.config.library_root)
    scan_service.apply_plan(ctx, plan)

    book = book_service.list_books(ctx)[0]
    assert book["title"] == "否定辩证法"
    assert book["author"] == "阿多尔诺"
    assert book["publisher_name"] == "商务印书馆"
    assert book["publication_year"] == 2019
    assert book["edition"] == "1"
    assert book["literature_type"] == "M"
    assert book["file_path"] == "阿多尔诺_否定辩证法[M]_王凤才_北京：商务印书馆_2019(1).pdf"
