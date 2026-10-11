"""批量补全测试（services/enrich_batch.py + GUI 接口）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bookdb import pdf as pdf_mod  # noqa: E402
from bookdb.config import Config  # noqa: E402
from bookdb.db import database  # noqa: E402
from bookdb.services import books as book_service  # noqa: E402
from bookdb.services import enrich_batch  # noqa: E402
from bookdb.services import llm as llm_service  # noqa: E402
from bookdb.services.context import Context  # noqa: E402


class _Scripted:
    """按调用次序回放回复；用完重复最后一个。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def describe_image(self, image, prompt):
        r = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return r


_NULL = ('{"title":null,"author":null,"publisher":null,"publication_place":null,'
         '"publication_year":null,"edition":null,"isbn":null}')
_COPYRIGHT = ('{"title":"改造的誕生","author":["王良卿"],'
              '"publisher":"國立政治大學歷史學系","publication_place":"臺北市",'
              '"publication_year":2010,"edition":"1","isbn":"9789860232028"}')


def _blank_pdf(path: Path, pages: int = 3) -> Path:
    """无文本层的 PDF（模拟扫描件）。"""
    import pymupdf
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture()
def ctx(tmp_path: Path, monkeypatch):
    lib = tmp_path / "lib"
    lib.mkdir()
    config = Config(db_path=tmp_path / "book.db", library_root=lib)
    conn = database.connect(config.db_path)
    database.initialize(conn)
    conn.close()
    # 注意：必须返回**同一个** adapter 实例 —— 每页都会 get_adapter()，
    # 若每次 new 一个，脚本化回复的调用计数会被重置。
    adapter = _Scripted([_NULL, _COPYRIGHT])
    monkeypatch.setattr(llm_service, "get_adapter", lambda vlm: adapter)
    with Context.open(config) as c:
        c.adapter = adapter
        yield c


# ────────────────────────────────────────────────────────── 缺失字段判定
def test_needs_enrich_excludes_year():
    """年份缺失**不**触发补全 —— 否则版权页无年份的书会被无限重跑。"""
    assert enrich_batch.needs_enrich({"title": "x"}) == ["author", "publisher_name"]
    assert enrich_batch.needs_enrich({"title": "x", "publication_year": None}) == [
        "author", "publisher_name"
    ]
    # 有作者和出版社、只缺年份 → 不需要补
    assert enrich_batch.needs_enrich(
        {"author": "甲", "publisher_name": "社", "publication_year": None}
    ) == []


def test_missing_fields_includes_year_for_display():
    assert enrich_batch.missing_fields({"title": "x"}) == [
        "author", "publisher_name", "publication_year"
    ]
    assert enrich_batch.missing_fields(
        {"author": "a", "publisher_name": "p", "publication_year": 2020}
    ) == []


# ────────────────────────────────────────────────────────────── 计划
def test_plan_skips_complete(ctx: Context):
    _blank_pdf(ctx.config.library_root / "a.pdf")
    book_service.add_book(ctx, {"title": "缺元数据", "file_path": "a.pdf"})
    book_service.add_book(ctx, {"title": "已齐全", "author": "甲", "publisher": "社",
                                "publication_year": 2020})

    plan = enrich_batch.build_batch_plan(ctx, books=book_service.list_books(ctx))

    assert [i.title for i in plan.todo] == ["缺元数据"]      # 只处理缺元数据那条
    skipped = {i.title: i.skipped for i in plan.skipped}
    assert skipped == {"已齐全": "元数据已齐"}


def test_plan_reports_missing_file(ctx: Context):
    book_service.add_book(ctx, {"title": "文件没了", "file_path": "gone.pdf"})
    plan = enrich_batch.build_batch_plan(ctx, books=book_service.list_books(ctx))
    item = plan.items[0]
    assert item.error and "不存在" in item.error


def test_plan_directory_scope(ctx: Context):
    """--dir 递归时，子目录内的记录也算在范围内。"""
    sub = ctx.config.library_root / "sub"
    _blank_pdf(sub / "in.pdf")
    book_service.add_book(ctx, {"title": "里面", "file_path": "sub/in.pdf"})

    plan = enrich_batch.build_batch_plan(
        ctx, books=book_service.list_books(ctx), directory=sub
    )
    assert [i.title for i in plan.todo] == ["里面"]
    assert [p.name for p in plan.unresolved_files] == []      # in.pdf 已匹配

    # 根目录 + 递归 → 子目录里的记录同样在范围内
    plan_root = enrich_batch.build_batch_plan(
        ctx, books=book_service.list_books(ctx), directory=ctx.config.library_root
    )
    assert [i.title for i in plan_root.todo] == ["里面"]

    # 根目录 + 不递归 → 子目录里的记录被排除
    plan_flat = enrich_batch.build_batch_plan(
        ctx, books=book_service.list_books(ctx),
        directory=ctx.config.library_root, recursive=False,
    )
    assert not plan_flat.todo


def test_plan_directory_reports_unresolved(ctx: Context):
    """目录里存在、但库里没有对应记录的 PDF → 列入 unresolved。"""
    _blank_pdf(ctx.config.library_root / "orphan.pdf")
    plan = enrich_batch.build_batch_plan(
        ctx, books=book_service.list_books(ctx), directory=ctx.config.library_root
    )
    assert [p.name for p in plan.unresolved_files] == ["orphan.pdf"]


def test_plan_limit(ctx: Context):
    for n in range(3):
        _blank_pdf(ctx.config.library_root / f"{n}.pdf")
        book_service.add_book(ctx, {"title": f"书{n}", "file_path": f"{n}.pdf"})
    plan = enrich_batch.build_batch_plan(
        ctx, books=book_service.list_books(ctx), limit=2
    )
    assert len(plan.items) == 2


# ────────────────────────────────────────────────────────── 执行提取
def test_run_item_fills_metadata(ctx: Context):
    _blank_pdf(ctx.config.library_root / "s.pdf")
    book_service.add_book(ctx, {"title": "改造的诞生", "file_path": "s.pdf"})
    plan = enrich_batch.build_batch_plan(ctx, books=book_service.list_books(ctx))

    enrich_batch.run_item(ctx, plan.todo[0])

    item = plan.items[0]
    assert item.ok
    assert item.metadata["author"] == "王良卿"
    assert item.metadata["isbn"] == "9789860232028"
    assert item.metadata["file_path"] == str(ctx.config.library_root / "s.pdf")


def test_run_item_records_error(ctx: Context, monkeypatch):
    _blank_pdf(ctx.config.library_root / "s.pdf")
    book_service.add_book(ctx, {"title": "无版权页", "file_path": "s.pdf"})
    monkeypatch.setattr(llm_service, "get_adapter", lambda vlm: _Scripted([_NULL]))
    plan = enrich_batch.build_batch_plan(ctx, books=book_service.list_books(ctx))

    enrich_batch.run_item(ctx, plan.todo[0])
    assert plan.items[0].error and not plan.items[0].metadata
    assert not plan.items[0].ok


# ────────────────────────────────────────────────────────────── 写回
def test_apply_writes_fields_and_skips_unselected(ctx: Context):
    _blank_pdf(ctx.config.library_root / "a.pdf")
    _blank_pdf(ctx.config.library_root / "b.pdf")
    book_service.add_book(ctx, {"title": "甲书", "file_path": "a.pdf"})
    book_service.add_book(ctx, {"title": "乙书", "file_path": "b.pdf"})
    plan = enrich_batch.build_batch_plan(ctx, books=book_service.list_books(ctx))
    for item in plan.todo:
        enrich_batch.run_item(ctx, item)

    first_id = plan.todo[0].book_id
    result = enrich_batch.apply_batch_plan(ctx, plan, selected={first_id})

    assert [u["id"] for u in result["updated"]] == [first_id]
    written = {b["id"]: b for b in book_service.list_books(ctx)}
    assert written[first_id]["publisher_name"] == "國立政治大學歷史學系"
    # 未选中的那条保持原状
    other = [b for b in written.values() if b["id"] != first_id][0]
    assert other["publisher_name"] is None


def test_apply_never_touches_file_path(ctx: Context):
    _blank_pdf(ctx.config.library_root / "a.pdf")
    book_service.add_book(ctx, {"title": "甲书", "file_path": "a.pdf"})
    plan = enrich_batch.build_batch_plan(ctx, books=book_service.list_books(ctx))
    enrich_batch.run_item(ctx, plan.todo[0])
    enrich_batch.apply_batch_plan(ctx, plan)

    book = book_service.list_books(ctx)[0]
    assert book["file_path"] == "a.pdf"          # 仍是相对路径，未被绝对化


def test_apply_no_overwrite_keeps_existing(ctx: Context):
    """--no-overwrite：只填空缺，不覆盖已有值。"""
    _blank_pdf(ctx.config.library_root / "a.pdf")
    book_service.add_book(ctx, {"title": "甲书", "file_path": "a.pdf",
                                "author": "原作者", "publication_year": 1900})
    plan = enrich_batch.build_batch_plan(ctx, books=book_service.list_books(ctx))
    enrich_batch.run_item(ctx, plan.todo[0])

    enrich_batch.apply_batch_plan(ctx, plan, overwrite=False)
    book = book_service.list_books(ctx)[0]
    assert book["author"] == "原作者"                  # 保住已有
    assert book["publication_year"] == 1900
    assert book["publisher_name"] == "國立政治大學歷史學系"   # 空缺被填上


def test_apply_overwrite_replaces(ctx: Context):
    _blank_pdf(ctx.config.library_root / "a.pdf")
    book_service.add_book(ctx, {"title": "甲书", "file_path": "a.pdf", "author": "原作者"})
    plan = enrich_batch.build_batch_plan(ctx, books=book_service.list_books(ctx))
    enrich_batch.run_item(ctx, plan.todo[0])

    enrich_batch.apply_batch_plan(ctx, plan, overwrite=True)
    assert book_service.list_books(ctx)[0]["author"] == "王良卿"


# ────────────────────────────────────────────────────────── 幂等性
def test_second_run_skips_after_enrich(ctx: Context, monkeypatch):
    """补全过一次后，同样的书不再进待补清单（即使版权页没给出年份）。"""
    # 版权页**不含年份**的回复 —— 正是「无限重跑」的场景
    adapter = _Scripted([
        _NULL,
        '{"title":"某书","author":["甲作者"],"publisher":"某出版社",'
        '"publication_place":"北京","isbn":"9780000000099"}',
    ])
    monkeypatch.setattr(llm_service, "get_adapter", lambda vlm: adapter)

    _blank_pdf(ctx.config.library_root / "a.pdf")
    book_service.add_book(ctx, {"title": "甲书", "file_path": "a.pdf"})

    plan1 = enrich_batch.build_batch_plan(ctx, books=book_service.list_books(ctx))
    assert len(plan1.todo) == 1
    enrich_batch.run_item(ctx, plan1.todo[0])
    enrich_batch.apply_batch_plan(ctx, plan1)

    # 第二次：作者/出版社已填 → 不再触发（哪怕年份仍空）
    books = book_service.list_books(ctx)
    assert books[0]["publication_year"] is None       # 版权页没给年份
    plan2 = enrich_batch.build_batch_plan(ctx, books=books)
    assert plan2.todo == []
    assert [i.skipped for i in plan2.skipped] == ["元数据已齐"]
