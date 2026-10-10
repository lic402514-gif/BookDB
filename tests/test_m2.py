"""M2 验收测试 —— 数据交换（PRD §9 / §10 / §11）。

覆盖：JSON 全量导出 / full 导入（insert + upsert）/ patch 导入 /
NULL 语义 / 非法字段与 id 校验 / 显式 mode / CSV 导出与导入 / round-trip。
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bookdb.config import Config  # noqa: E402
from bookdb.db import database  # noqa: E402
from bookdb.services import books as book_service  # noqa: E402
from bookdb.services import import_export as ie  # noqa: E402
from bookdb.services.context import Context  # noqa: E402


@pytest.fixture()
def ctx(tmp_path: Path) -> Context:
    config = Config(db_path=tmp_path / "b.db", library_root=tmp_path / "lib")
    config.library_root.mkdir()
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
    "abstract": "摘要内容" * 30,
}


def _add(ctx: Context, **kw) -> int:
    return book_service.add_book(ctx, {**SAMPLE, **kw})


# ─────────────────────────────────────────────────────────────── JSON 导出
def test_export_json_structure(ctx: Context):
    _add(ctx)
    data = json.loads(ie.export_json(ctx))

    assert data["mode"] == "full"
    assert len(data["items"]) == 1

    item = data["items"][0]
    assert item["publisher"] == "商务印书馆"          # 名称形式，不是 publisher_id
    assert item["isbn"] == "9787100163180"            # 已去连字符
    assert item["edition"] == "1"                     # 纯数字
    assert "abstract" in item and item["abstract"]     # 导出含摘要
    assert item["id"] == 1


def test_export_csv_structure(ctx: Context):
    _add(ctx)
    rows = list(csv.DictReader(io.StringIO(ie.export_csv(ctx))))
    assert len(rows) == 1
    assert rows[0]["publisher"] == "商务印书馆"
    assert set(rows[0].keys()) == set(ie.EXPORT_FIELDS)


# ─────────────────────────────────────────────────────────────── full 导入
def test_import_full_inserts(ctx: Context):
    plan = ie.build_json_plan(ctx, {"mode": "full", "items": [
        {"title": "资本论", "author": "马克思", "publisher": "人民出版社",
         "publication_year": "2004", "clc_code": "A81"},
        {"title": "存在与时间", "author": "海德格尔"},
    ]})
    assert plan.ok and len(plan.inserts) == 2
    ie.apply_plan(ctx, plan)
    assert len(book_service.list_books(ctx)) == 2


def test_import_full_upsert(ctx: Context):
    bid = _add(ctx)
    plan = ie.build_json_plan(ctx, {"mode": "full", "items": [
        {"id": bid, "clc_code": "B516"},               # 已存在 → 更新
        {"title": "新书", "author": "某人"},            # 无 id → 新增
    ]})
    assert plan.ok
    assert len(plan.updates) == 1 and len(plan.inserts) == 1
    ie.apply_plan(ctx, plan)

    book = book_service.get_book(ctx, bid)
    assert book["clc_code"] == "B516"
    assert book["title"] == "否定辩证法"               # 未提供字段保留
    assert len(book_service.list_books(ctx)) == 2


# ─────────────────────────────────────────────────────────────── patch 导入
def test_patch_updates_only_present_fields(ctx: Context):
    bid = _add(ctx)
    plan = ie.build_json_plan(ctx, {"mode": "patch", "items": [
        {"id": bid, "clc_code": "A81", "abstract": "新摘要"},
    ]})
    assert plan.ok and len(plan.updates) == 1
    ie.apply_plan(ctx, plan)

    book = book_service.get_book(ctx, bid)
    assert book["clc_code"] == "A81"
    assert book["abstract"] == "新摘要"
    assert book["title"] == "否定辩证法"               # 未出现字段保持原值
    assert book["isbn"] == "9787100163180"


def test_patch_null_clears_field(ctx: Context):
    bid = _add(ctx)
    plan = ie.build_json_plan(ctx, {"mode": "patch", "items": [
        {"id": bid, "abstract": None},
    ]})
    assert plan.ok
    ie.apply_plan(ctx, plan)
    assert book_service.get_book(ctx, bid)["abstract"] is None


def test_patch_field_absent_keeps_value(ctx: Context):
    bid = _add(ctx)
    # abstract 键完全不出现 → 保持原值（区别于显式 null）
    plan = ie.build_json_plan(ctx, {"mode": "patch", "items": [
        {"id": bid, "clc_code": "A81"},
    ]})
    ie.apply_plan(ctx, plan)
    assert book_service.get_book(ctx, bid)["abstract"] is not None


def test_preview_before_includes_abstract(ctx: Context):
    # 预览的「旧值」必须含摘要（回归：曾用 with_abstract=False 导致旧值显示为空）
    bid = _add(ctx)
    plan = ie.build_json_plan(ctx, {"mode": "patch", "items": [
        {"id": bid, "abstract": None},
    ]})
    assert plan.updates[0]["before"]["abstract"] is not None


# ─────────────────────────────────────────────────────────────── 校验与拒绝
def test_patch_missing_id_rejected(ctx: Context):
    plan = ie.build_json_plan(ctx, {"mode": "patch", "items": [{"clc_code": "B0"}]})
    assert not plan.ok
    assert any("必须提供 id" in e for e in plan.errors)


def test_patch_unknown_id_rejected(ctx: Context):
    plan = ie.build_json_plan(ctx, {"mode": "patch", "items": [{"id": 999, "clc_code": "B0"}]})
    assert not plan.ok
    assert any("不存在" in e for e in plan.errors)


def test_invalid_field_rejected(ctx: Context):
    plan = ie.build_json_plan(ctx, {"mode": "full", "items": [{"title": "x", "bogus": 1}]})
    assert not plan.ok
    assert any("非法字段" in e for e in plan.errors)


def test_missing_mode_rejected(ctx: Context):
    with pytest.raises(ie.ImportValidationError):
        ie.build_json_plan(ctx, {"items": [{"title": "x"}]})


def test_import_title_required(ctx: Context):
    plan = ie.build_json_plan(ctx, {"mode": "full", "items": [{"author": "某人"}]})
    assert not plan.ok
    assert any("title 为必填" in e for e in plan.errors)


# ─────────────────────────────────────────────────────────────── CSV 导入
def test_csv_import_full(ctx: Context, tmp_path: Path):
    csv_path = tmp_path / "books.csv"
    csv_path.write_text(
        "id,title,author,publisher,publication_year,clc_code\n"
        "1,资本论,马克思,人民出版社,2004,A81\n"
        ",否定辩证法,阿多尔诺,商务印书馆,2019,B0\n",   # 空 id → 新增
        encoding="utf-8",
    )
    rows = ie.load_csv(csv_path)
    plan = ie.build_plan(ctx, "full", rows)
    assert plan.ok and len(plan.inserts) == 2
    ie.apply_plan(ctx, plan)
    assert len(book_service.list_books(ctx)) == 2


def test_csv_unknown_column_rejected(ctx: Context, tmp_path: Path):
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("title,bogus\nx,1\n", encoding="utf-8")
    with pytest.raises(ie.ImportValidationError) as exc:
        ie.load_csv(csv_path)
    assert any("未知列" in e for e in exc.value.errors)


# ─────────────────────────────────────────────────────────────── round-trip
def test_export_import_roundtrip(ctx: Context):
    _add(ctx)
    book_service.add_book(ctx, {**SAMPLE, "title": "资本论", "author": "马克思",
                                "isbn": "9787010041155", "publisher": "人民出版社"})

    exported = json.loads(ie.export_json(ctx))
    assert len(exported["items"]) == 2

    # 清空后重新导入，数据应还原
    for row in book_service.list_books(ctx):
        book_service.delete_book(ctx, row["id"])
    assert book_service.list_books(ctx) == []

    plan = ie.build_json_plan(ctx, exported)
    assert plan.ok and len(plan.inserts) == 2
    ie.apply_plan(ctx, plan)

    rows = book_service.list_books(ctx)
    assert len(rows) == 2
    titles = {r["title"] for r in rows}
    assert titles == {"否定辩证法", "资本论"}
