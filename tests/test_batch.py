"""批量操作测试（services/batch.py）。

重点覆盖：四种操作的计划/执行、目标冲突、批次内冲突，
以及**安全底线** —— 删除文件必须走回收站，缺 send2trash 时明确拒绝而非永久删除。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bookdb.config import Config  # noqa: E402
from bookdb.db import database  # noqa: E402
from bookdb.services import batch  # noqa: E402
from bookdb.services import books as book_service  # noqa: E402
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


def _pdf(ctx: Context, name: str, data: bytes = b"%PDF-1.4 dummy") -> Path:
    path = ctx.config.library_root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _add(ctx: Context, title: str, filename: str | None = None, **kw) -> int:
    data: dict = {"title": title, **kw}
    if filename:
        data["file_path"] = filename
    return book_service.add_book(ctx, data, allow_duplicate=True)


# ═════════════════════════════════════════════════════════ ① 重命名
def test_rename_plan_basic(ctx: Context):
    _pdf(ctx, "改造的诞生 (z-library.sk).pdf")
    bid = _add(ctx, "改造的诞生", "改造的诞生 (z-library.sk).pdf", author="王良卿")

    item = batch.build_rename_plan(ctx, [bid]).items[0]
    assert item.status == "rename"
    assert item.new.endswith("王良卿_改造的诞生[M].pdf")
    assert item.old.endswith("改造的诞生 (z-library.sk).pdf")


def test_rename_plan_unchanged(ctx: Context):
    _pdf(ctx, "王良卿_改造的诞生[M].pdf")
    bid = _add(ctx, "改造的诞生", "王良卿_改造的诞生[M].pdf", author="王良卿")

    item = batch.build_rename_plan(ctx, [bid]).items[0]
    assert item.status == "unchanged" and not item.actionable


def test_rename_plan_reports_no_file_and_missing(ctx: Context):
    no_file = _add(ctx, "没文件的书")
    assert batch.build_rename_plan(ctx, [no_file]).items[0].status == "no_file"

    missing = _add(ctx, "文件没了", "gone.pdf")
    item = batch.build_rename_plan(ctx, [missing]).items[0]
    assert item.status == "missing" and "不存在" in item.error


def test_rename_plan_conflict_when_target_exists(ctx: Context):
    _pdf(ctx, "旧名.pdf")
    _pdf(ctx, "王良卿_改造的诞生[M].pdf")            # 目标已被占用
    bid = _add(ctx, "改造的诞生", "旧名.pdf", author="王良卿")

    item = batch.build_rename_plan(ctx, [bid]).items[0]
    assert item.status == "conflict" and not item.actionable


def test_rename_plan_detects_same_target_inside_batch(ctx: Context):
    _pdf(ctx, "a.pdf")
    _pdf(ctx, "b.pdf")
    b1 = _add(ctx, "同书", "a.pdf", author="甲")
    b2 = _add(ctx, "同书", "b.pdf", author="甲")

    plan = batch.build_rename_plan(ctx, [b1, b2])
    assert [i.status for i in plan.items] == ["conflict", "conflict"]
    assert "同名" in plan.items[0].error


def test_rename_plan_flags_swap_as_conflict(ctx: Context):
    """两本互换名字 → 都标冲突（不做拓扑排序，交用户手动处理）。"""
    _pdf(ctx, "甲_书一[M].pdf")
    _pdf(ctx, "乙_书二[M].pdf")
    b1 = _add(ctx, "书二", "甲_书一[M].pdf", author="乙")
    b2 = _add(ctx, "书一", "乙_书二[M].pdf", author="甲")

    plan = batch.build_rename_plan(ctx, [b1, b2])
    assert all(i.status == "conflict" for i in plan.items)


def test_apply_rename_moves_file_and_updates_path(ctx: Context):
    _pdf(ctx, "旧名.pdf")
    bid = _add(ctx, "改造的诞生", "旧名.pdf", author="王良卿")

    result = batch.apply_plan(ctx, batch.build_rename_plan(ctx, [bid]))

    assert result["done"] == [bid]
    assert not (ctx.config.library_root / "旧名.pdf").exists()
    assert (ctx.config.library_root / "王良卿_改造的诞生[M].pdf").is_file()
    assert book_service.get_book(ctx, bid)["file_path"] == "王良卿_改造的诞生[M].pdf"


# ═════════════════════════════════════════════════════════ ② 移动
def test_move_plan_and_apply(ctx: Context, tmp_path: Path):
    _pdf(ctx, "书.pdf")
    bid = _add(ctx, "书", "书.pdf")
    dest = tmp_path / "out"
    dest.mkdir()

    plan = batch.build_move_plan(ctx, [bid], dest)
    assert plan.items[0].status == "move"

    result = batch.apply_plan(ctx, plan)
    assert result["done"] == [bid]
    assert (dest / "书.pdf").is_file()
    # 移出 library_root → file_path 按 §6.1 语义变**绝对路径**
    assert Path(book_service.get_book(ctx, bid)["file_path"]).is_absolute()


def test_move_plan_unchanged_when_already_there(ctx: Context):
    _pdf(ctx, "书.pdf")
    bid = _add(ctx, "书", "书.pdf")

    item = batch.build_move_plan(ctx, [bid], ctx.config.library_root).items[0]
    assert item.status == "unchanged"


def test_move_plan_conflict_on_existing_target(ctx: Context, tmp_path: Path):
    _pdf(ctx, "书.pdf")
    bid = _add(ctx, "书", "书.pdf")
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "书.pdf").write_bytes(b"occupied")

    item = batch.build_move_plan(ctx, [bid], dest).items[0]
    assert item.status == "conflict"


def test_move_plan_rejects_bad_destination(ctx: Context):
    bid = _add(ctx, "书")
    with pytest.raises(batch.BatchError):
        batch.build_move_plan(ctx, [bid], ctx.config.library_root / "nope")

    file_path = ctx.config.library_root / "afile.pdf"
    file_path.write_bytes(b"x")
    with pytest.raises(batch.BatchError):
        batch.build_move_plan(ctx, [bid], file_path)      # 不是目录


# ═══════════════════════════════════════════════ ③ 删除元数据
def test_delete_meta_removes_records_but_keeps_files(ctx: Context):
    path = _pdf(ctx, "书.pdf")
    bid = _add(ctx, "书", "书.pdf")

    plan = batch.build_delete_plan(ctx, [bid], with_files=False)
    assert plan.items[0].status == "delete"

    with pytest.raises(batch.BatchError):                 # 必须显式确认
        batch.apply_plan(ctx, plan)

    result = batch.apply_plan(ctx, plan, confirm=True)
    assert result["done"] == [bid]
    assert path.is_file()                                 # ★ 文件保留
    with pytest.raises(book_service.BookNotFoundError):   # 记录已删
        book_service.get_book(ctx, bid)


# ═══════════════════════════════════════════════ ④ 删除文件本体
def test_delete_files_plan_collects_sizes(ctx: Context):
    _pdf(ctx, "书.pdf", b"x" * 1234)
    bid = _add(ctx, "书", "书.pdf")

    plan = batch.build_delete_plan(ctx, [bid], with_files=True)
    assert plan.operation == "delete_files"
    assert plan.total_size == 1234


def test_delete_files_goes_through_trash(ctx: Context, monkeypatch):
    """文件必须走**回收站**通道，且记录随之一并删除。"""
    sent: list[Path] = []
    monkeypatch.setattr(batch, "_send_to_trash", lambda p: sent.append(p))

    path = _pdf(ctx, "书.pdf")
    bid = _add(ctx, "书", "书.pdf")
    plan = batch.build_delete_plan(ctx, [bid], with_files=True)

    result = batch.apply_plan(ctx, plan, confirm=True)

    assert result["done"] == [bid]
    assert sent == [path]                     # ★ 走了回收站
    with pytest.raises(book_service.BookNotFoundError):
        book_service.get_book(ctx, bid)


def test_delete_files_refuses_without_send2trash(ctx: Context, monkeypatch):
    """未装 send2trash → 明确报错；**绝不永久删除**，文件与记录都原封不动。"""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "send2trash":
            raise ImportError("no send2trash")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    path = _pdf(ctx, "书.pdf")
    bid = _add(ctx, "书", "书.pdf")
    plan = batch.build_delete_plan(ctx, [bid], with_files=True)

    result = batch.apply_plan(ctx, plan, confirm=True)

    assert result["done"] == [] and result["failed"]
    assert "send2trash" in result["failed"][0]["error"]
    assert path.is_file()                                 # ★ 文件还在
    assert book_service.get_book(ctx, bid)["title"] == "书"   # ★ 记录也还在


# ═══════════════════════════════════════════════════ 通用
def test_apply_honours_selection(ctx: Context):
    _pdf(ctx, "a.pdf")
    _pdf(ctx, "b.pdf")
    b1 = _add(ctx, "书一", "a.pdf", author="甲")
    b2 = _add(ctx, "书二", "b.pdf", author="乙")

    plan = batch.build_rename_plan(ctx, [b1, b2])
    result = batch.apply_plan(ctx, plan, selected={b1})

    assert result["done"] == [b1] and result["skipped"] == 1
    assert (ctx.config.library_root / "b.pdf").is_file()   # 未选中的没动


def test_unknown_id_raises(ctx: Context):
    with pytest.raises(batch.BatchError):
        batch.build_rename_plan(ctx, [999])
    with pytest.raises(batch.BatchError):
        batch.build_delete_plan(ctx, [999], with_files=False)


def test_failed_item_does_not_block_others(ctx: Context):
    """逐条独立：一条在 apply 时失败，其余照常完成。"""
    _pdf(ctx, "a.pdf")
    _pdf(ctx, "b.pdf")
    b1 = _add(ctx, "书一", "a.pdf", author="甲")
    b2 = _add(ctx, "书二", "b.pdf", author="乙")

    plan = batch.build_rename_plan(ctx, [b1, b2])
    assert len(plan.actionable) == 2
    (ctx.config.library_root / "a.pdf").unlink()           # 计划后抽掉第 1 条的文件

    result = batch.apply_plan(ctx, plan)

    assert b2 in result["done"]                            # 第 2 条照常完成
    assert [f["id"] for f in result["failed"]] == [b1]
    assert (ctx.config.library_root / "乙_书二[M].pdf").is_file()
