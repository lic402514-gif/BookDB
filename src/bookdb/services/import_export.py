"""数据交换服务（PRD §9 / §10 / §11）。

职责：
  - JSON 全量导出 / 导入（显式 mode: full | patch）
  - CSV 导出 / 导入（full 语义）
  - 导入前构建变更计划（供预览 + 确认），校验通过后才写入

关键语义（已定稿）：
  - JSON 顶层必须含 `mode`，缺省/非法直接报错，不猜。
  - patch 模式：按 id 定位，`id` 必须存在且命中已有记录，否则报错；
    字段「不存在」= 保持原值；字段「为 null」= 主动清空。
  - full 模式：id 存在且命中 → 更新（仅覆盖出现的字段）；否则 → 新增（id 自动分配）。
  - 非法字段 / 无法解析的 id / 缺失 title → 整个导入拒绝，不写入任何数据。
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..db import dao
from ..db import schema
from ..services.books import prepare_fields
from .context import Context

#: 导出字段顺序（publisher 为名称形式，与导入 / add 保持一致）
EXPORT_FIELDS = [
    "id", "title", "author", "translator", "publisher",
    "publication_place", "publication_year", "edition", "isbn",
    "clc_code", "literature_type", "abstract", "file_path",
]

#: 导入允许出现的键：可写字段 + id 定位键
IMPORT_KEYS = schema.WRITABLE_FIELDS | {"id"}

VALID_MODES = ("full", "patch")


class ImportValidationError(ValueError):
    """导入数据校验失败。携带全部错误，供 CLI 一次性展示。"""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass
class ImportPlan:
    """一次导入的变更计划。errors 非空时不得执行任何写入。"""

    inserts: list[dict] = field(default_factory=list)
    updates: list[dict] = field(default_factory=list)  # {"id", "before", "fields"}
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def total(self) -> int:
        return len(self.inserts) + len(self.updates)


# ═══════════════════════════════════════════════════════════════════ 导出
def _row_to_export(row: dict) -> dict:
    """DAO 行（列名带 publisher_name）→ 导出记录（publisher 名称形式）。"""
    return {
        "id": row["id"],
        "title": row["title"],
        "author": row["author"],
        "translator": row["translator"],
        "publisher": row["publisher_name"],
        "publication_place": row["publication_place"],
        "publication_year": row["publication_year"],
        "edition": row["edition"],
        "isbn": row["isbn"],
        "clc_code": row["clc_code"],
        "literature_type": row["literature_type"],
        "abstract": row["abstract"],
        "file_path": row["file_path"],
    }


def export_items(ctx: Context) -> list[dict]:
    """导出全部图书记录（含 abstract）。"""
    return [_row_to_export(r) for r in dao.list_books(ctx.conn, with_abstract=True)]


def export_json(ctx: Context, *, pretty: bool = False) -> str:
    data = {"mode": "full", "items": export_items(ctx)}
    return json.dumps(data, ensure_ascii=False, indent=2 if pretty else None)


def export_csv(ctx: Context) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for item in export_items(ctx):
        writer.writerow(item)
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════════ 导入
def load_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ImportValidationError([f"JSON 解析失败：{exc}"]) from exc
    except OSError as exc:
        raise ImportValidationError([f"读取文件失败：{exc}"]) from exc


def load_csv(path: Path) -> list[dict]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                raise ImportValidationError(["CSV 为空或缺少表头"])
            unknown = [c for c in reader.fieldnames if c not in IMPORT_KEYS]
            if unknown:
                raise ImportValidationError(
                    [f"CSV 存在未知列：{unknown}（允许：{sorted(IMPORT_KEYS)}）"]
                )
            return [dict(r) for r in reader]
    except ImportValidationError:
        raise
    except OSError as exc:
        raise ImportValidationError([f"读取文件失败：{exc}"]) from exc


def _parse_mode(data: dict, mode_override: str | None) -> str:
    if mode_override is not None:
        if mode_override not in VALID_MODES:
            raise ImportValidationError([f"非法的 --mode：{mode_override!r}（应为 full 或 patch）"])
        return mode_override
    mode = data.get("mode")
    if mode not in VALID_MODES:
        raise ImportValidationError([f"缺少或非法的 mode 字段：{mode!r}（应为 full 或 patch）"])
    return mode


def build_json_plan(ctx: Context, data: object, *, mode_override: str | None = None) -> ImportPlan:
    if not isinstance(data, dict):
        raise ImportValidationError(
            [f"JSON 顶层必须是含 mode 与 items 的对象，得到 {type(data).__name__}"]
        )
    mode = _parse_mode(data, mode_override)
    items = data.get("items")
    if not isinstance(items, list):
        raise ImportValidationError(["JSON 缺少 items 数组"])
    return build_plan(ctx, mode, items)


def build_plan(ctx: Context, mode: str, items: list) -> ImportPlan:
    """按 mode 构建变更计划；所有校验错误汇总到 plan.errors。"""
    plan = ImportPlan()
    library_root = ctx.config.library_root

    for idx, raw in enumerate(items):
        n = idx + 1
        if not isinstance(raw, dict):
            plan.errors.append(f"第 {n} 条不是对象，已跳过")
            continue

        unknown = [k for k in raw if k not in IMPORT_KEYS]
        if unknown:
            plan.errors.append(f"第 {n} 条：非法字段 {unknown}")
            continue

        raw_id = raw.get("id")
        book_id = _coerce_id(raw_id)
        fields = {k: v for k, v in raw.items() if k != "id"}

        if mode == "patch":
            if book_id is None:
                msg = (
                    f"第 {n} 条：patch 模式必须提供 id"
                    if "id" not in raw
                    else f"第 {n} 条：id 无法解析为整数：{raw_id!r}"
                )
                plan.errors.append(msg)
                continue
            existing = dao.get_book(ctx.conn, book_id, with_abstract=True)
            if existing is None:
                plan.errors.append(f"第 {n} 条：id={book_id} 不存在（patch 只允许修改已有记录）")
                continue
            norm, err = _normalize(fields, library_root, n)
            if err:
                plan.errors.append(err)
                continue
            if not norm:
                continue  # 无可改字段，静默跳过
            plan.updates.append({"id": book_id, "before": existing, "fields": norm})
        else:  # full
            if book_id is None and raw_id not in (None, ""):
                # id 值非空却无法解析 → 报错；空白/缺失 → 视为新增
                plan.errors.append(f"第 {n} 条：id 无法解析为整数：{raw_id!r}")
                continue
            existing = None
            if book_id is not None:
                existing = dao.get_book(ctx.conn, book_id, with_abstract=True)
            if existing is not None:
                norm, err = _normalize(fields, library_root, n)
                if err:
                    plan.errors.append(err)
                    continue
                if not norm:
                    continue
                plan.updates.append({"id": book_id, "before": existing, "fields": norm})
            else:
                norm, err = _normalize(fields, library_root, n)
                if err:
                    plan.errors.append(err)
                    continue
                if not norm.get("title"):
                    plan.errors.append(f"第 {n} 条：title 为必填")
                    continue
                plan.inserts.append(norm)

    return plan


def _normalize(fields: dict, library_root: Path | None, n: int) -> tuple[dict | None, str | None]:
    """规范化字段；title 不可被清空。返回 (结果, 错误)。"""
    try:
        norm = prepare_fields(fields, library_root)
    except ValueError as exc:
        return None, f"第 {n} 条：{exc}"
    if "title" in norm and not norm["title"]:
        return None, f"第 {n} 条：title 不能为空"
    return norm, None


def _coerce_id(raw_id: object) -> int | None:
    """把 id 规范化为 int；bool / 空 / 非法 → None。"""
    if raw_id is None or isinstance(raw_id, bool):
        return None
    if isinstance(raw_id, int):
        return raw_id
    if isinstance(raw_id, str):
        text = raw_id.strip()
        if text.isdigit():
            return int(text)
        return None
    return None


# ═══════════════════════════════════════════════════════════════════ 应用
def apply_plan(ctx: Context, plan: ImportPlan) -> int:
    """执行一个已通过校验的变更计划，返回变更条数。"""
    if not plan.ok:
        raise ImportValidationError(plan.errors)
    for item in plan.inserts:
        dao.insert_book(ctx.conn, item)
    for upd in plan.updates:
        dao.update_book_fields(ctx.conn, upd["id"], upd["fields"])
    return plan.total
