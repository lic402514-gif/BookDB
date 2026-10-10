"""M3 验收测试 —— 文件重命名 + GB/T 7714 引用（PRD §14 / §15）。

覆盖：文件名模板（含缺省省略 / 非法字符 / 截断）、GB/T 7714 示例与变体、
rename dry-run 计划、apply 后文件与数据库同步、目标存在不覆盖、已是规范名 no-op。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bookdb.config import Config  # noqa: E402
from bookdb.db import database  # noqa: E402
from bookdb.services import books as book_service  # noqa: E402
from bookdb.services import citation as citation_service  # noqa: E402
from bookdb.services import rename as rename_service  # noqa: E402
from bookdb.services.context import Context  # noqa: E402
from bookdb.services.format import build_filename, format_citation  # noqa: E402


@pytest.fixture()
def ctx(tmp_path: Path) -> Context:
    config = Config(db_path=tmp_path / "b.db", library_root=tmp_path / "lib")
    config.library_root.mkdir()
    conn = database.connect(config.db_path)
    database.initialize(conn)
    context = Context(config=config, conn=conn)
    yield context
    context.close()


def _add(ctx: Context, **kw) -> int:
    return book_service.add_book(ctx, {"title": "否定辩证法", "author": "阿多尔诺", **kw})


# ────────────────────────────────────────────────────────────── 文件名模板
def test_build_filename_full():
    book = {
        "author": "阿多尔诺", "title": "否定辩证法", "translator": "王凤才",
        "publication_place": "北京", "publisher_name": "商务印书馆",
        "publication_year": 2019, "edition": "1", "literature_type": "M",
    }
    assert build_filename(book) == "阿多尔诺_否定辩证法[M]_王凤才_北京：商务印书馆_2019(1).pdf"


def test_build_filename_omits_empty_segments():
    book = {"author": "阿多尔诺", "title": "否定辩证法", "literature_type": "M"}
    assert build_filename(book) == "阿多尔诺_否定辩证法[M].pdf"


def test_build_filename_defaults_type_and_formats_edition():
    book = {"title": "书", "publication_year": 2019, "edition": "2"}   # 无作者、无类型
    assert build_filename(book) == "书[M]_2019(2).pdf"


def test_build_filename_sanitizes_illegal_chars():
    book = {"title": "A/B:C*D?", "literature_type": "M"}
    name = build_filename(book)
    for ch in '\\/:*?"<>|':
        assert ch not in name


def test_build_filename_truncates_long_name():
    book = {"title": "长" * 500, "literature_type": "M"}
    name = build_filename(book)
    assert len(name) <= 200 + len(".pdf")


# ──────────────────────────────────────────────────────────── GB/T 7714 引用
def test_format_citation_matches_prd_example():
    book = {
        "author": "阿多尔诺", "title": "否定辩证法", "translator": "王凤才",
        "publication_place": "北京", "publisher_name": "商务印书馆",
        "publication_year": 2019, "literature_type": "M",
    }
    assert format_citation(book) == "阿多尔诺. 否定辩证法[M]. 王凤才, 译. 北京: 商务印书馆, 2019."


def test_format_citation_without_translator():
    book = {"author": "马克思", "title": "资本论", "publisher_name": "人民出版社",
            "publication_place": "北京", "publication_year": 2004, "literature_type": "M"}
    assert format_citation(book) == "马克思. 资本论[M]. 北京: 人民出版社, 2004."


def test_format_citation_multi_author_joined():
    book = {"author": "马克思；恩格斯", "title": "共产党宣言",
            "literature_type": "M", "publication_year": 1848}
    assert format_citation(book) == "马克思, 恩格斯. 共产党宣言[M]. 1848."


def test_format_citation_with_edition():
    book = {"author": "阿多尔诺", "title": "否定辩证法", "publisher_name": "商务印书馆",
            "publication_place": "北京", "publication_year": 2019, "edition": "2",
            "literature_type": "M"}
    assert format_citation(book) == "阿多尔诺. 否定辩证法[M]. 第2版. 北京: 商务印书馆, 2019."


# ──────────────────────────────────────────────────────────────── rename 服务
def test_rename_without_file_errors(ctx: Context):
    bid = _add(ctx)
    with pytest.raises(rename_service.NoFileError):
        rename_service.build_plan(ctx, bid)


def test_rename_apply_moves_file_and_updates_db(ctx: Context):
    lib = ctx.config.library_root
    src = lib / "scan001.pdf"
    src.write_bytes(b"%PDF-1.4")
    bid = _add(ctx, translator="王凤才", publisher="商务印书馆", publication_place="北京",
               publication_year="2019", edition="1", file_path=str(src))

    plan = rename_service.build_plan(ctx, bid)
    assert plan.old_name == "scan001.pdf"
    assert plan.new_name == "阿多尔诺_否定辩证法[M]_王凤才_北京：商务印书馆_2019(1).pdf"
    assert plan.changed

    rename_service.apply_plan(ctx, plan)
    assert not src.exists()
    assert (lib / plan.new_name).exists()
    # 数据库 file_path 已同步（库内 → 相对路径）
    assert book_service.get_book(ctx, bid)["file_path"] == plan.new_name


def test_rename_noop_when_already_normalized(ctx: Context):
    lib = ctx.config.library_root
    bid = _add(ctx, translator="王凤才", publisher="商务印书馆", publication_place="北京",
               publication_year="2019", edition="1")
    name = build_filename(book_service.get_book(ctx, bid), ".pdf")
    target = lib / name
    target.write_bytes(b"x")
    book_service.update_book(ctx, bid, {"file_path": str(target)})

    plan = rename_service.build_plan(ctx, bid)
    assert not plan.changed
    rename_service.apply_plan(ctx, plan)          # 不应报错、不改动
    assert target.exists()


def test_rename_refuses_to_overwrite_target(ctx: Context):
    lib = ctx.config.library_root
    src = lib / "scan001.pdf"
    src.write_bytes(b"a")
    bid = _add(ctx, translator="王凤才", publisher="商务印书馆", publication_place="北京",
               publication_year="2019", edition="1", file_path=str(src))

    plan = rename_service.build_plan(ctx, bid)
    (lib / plan.new_name).write_bytes(b"b")       # 占位目标

    with pytest.raises(rename_service.TargetExistsError):
        rename_service.apply_plan(ctx, plan)

    rename_service.apply_plan(ctx, plan, overwrite=True)   # 显式覆盖才允许
    assert (lib / plan.new_name).exists()


# ──────────────────────────────────────────────────────────────── cite 服务
def test_cite_service(ctx: Context):
    bid = _add(ctx, translator="王凤才", publisher="商务印书馆", publication_place="北京",
               publication_year="2019", literature_type="M")
    assert citation_service.cite(ctx, bid) == (
        "阿多尔诺. 否定辩证法[M]. 王凤才, 译. 北京: 商务印书馆, 2019."
    )
