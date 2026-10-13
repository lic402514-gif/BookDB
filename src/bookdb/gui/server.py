"""GUI 内部 HTTP 端点（PRD §16.4）。

pywebview 在**内部**托管一个仅绑定 `127.0.0.1`、**不对外暴露**的 HTTP 端点，
作为 WebView 与 Python Core 之间的传输层 —— 它不是"对外 FastAPI 服务"（§19）。

设计要点：
  - 零第三方依赖：标准库 `http.server.ThreadingHTTPServer`；
  - 所有处理函数**只调用 services 层**，不直接操作 dao / 写 SQL（PRD §17）；
  - 每个请求打开一次 Context（连接），用完即关 —— 天然线程安全；
  - 前端用普通 `fetch('/api/...')` 交互。
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..config import Config
from ..paths import LibraryRootMissingError
from ..paths import resolve as resolve_path
from ..db import dao
from ..services import batch as batch_service
from ..services import books as book_service
from ..services import citation as citation_service
from ..services import enrich as enrich_service
from ..services import enrich_batch
from ..services import import_export as ie
from ..services import model as model_service
from ..services import rename as rename_service
from ..services import scan as scan_service
from ..services import search as search_service
from ..services import setup as setup_service
from ..services.context import Context
from ..services.llm import ModelUnavailableError
from . import library
from . import settings


def _web_dir() -> Path:
    """前端资源目录。

    兼容 PyInstaller：onefile 模式下资源解压在 `sys._MEIPASS`，
    故优先探测打包位置，再回退到源码位置。
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidate = Path(base) / "bookdb" / "gui" / "web"
        if candidate.is_dir():
            return candidate
    return Path(__file__).parent / "web"


WEB_DIR = _web_dir()

#: 导出文件目录名（位于书库根下的子目录）。
#: ⚠️ 不用浏览器下载 —— pywebview 的下载落点不可控（可能进系统「下载」文件夹），
#: 用户会"找不到导出到哪了"。直接写进书库目录，路径确定且好找。
EXPORT_DIRNAME = "exports"

#: 无 Library 时**仍可访问**的接口（欢迎页 + 应用级设置需要它们）。
#: 其余数据类接口在无库时返回 409 + no_library，前端据此回欢迎页。
#:
#: ⚠️ `/api/model/*` 必须在这里：模型是**应用级**资源（存在 %APPDATA%，
#:    与具体 Library 无关）。不进白名单的话，用户在欢迎页点「🧠 AI 模型」
#:    会拿到 409，AI 面板整个不可用。（踩过）
NO_LIBRARY_PATHS = frozenset({
    "/api/meta",
    "/api/library/current",
    "/api/library/recent",
    "/api/library/probe",
    "/api/library/create",
    "/api/library/open",
    "/api/model/list",
    "/api/model/status",
    "/api/model/progress",
    "/api/model/download",
    "/api/model/cancel",
    "/api/model/remove",
    "/api/model/dir",
})


class ApiError(Exception):
    """带 HTTP 状态码与附加负载的接口错误。"""

    def __init__(self, message: str, status: int = 400, payload: dict | None = None):
        self.status = status
        self.payload = payload or {}
        super().__init__(message)


# ═══════════════════════════════════════════════════════════════════ 处理函数
# 签名：(config, match, query, body) → 可 JSON 序列化的负载
def _library_dir(config: Config | None) -> Path | None:
    """当前 Library 目录（= book.toml 所在目录）。"""
    if config is None or config.config_path is None:
        return None
    return config.config_path.parent


def _library_payload(config: Config | None) -> dict:
    """当前 Library 的概览。**只有 config 为 None 才算「无库」**。

    `path`/`name` 可能为 None（配置不是从 `book.toml` 加载的，例如 CLI 默认配置），
    此时仍是「有库」状态 —— 前端不会误显示欢迎页。
    """
    if config is None:
        return {
            "no_library": True, "path": None, "name": None,
            "db_path": None, "library_root": None, "count": 0,
        }
    directory = _library_dir(config)
    with Context.open(config) as ctx:
        count = len(book_service.list_books(ctx))
    return {
        "no_library": False,
        "path": str(directory) if directory else None,
        "name": directory.name if directory else None,
        "db_path": str(config.db_path),
        "library_root": str(config.library_root) if config.library_root else None,
        "count": count,
    }


def _h_meta(config: Config | None, match, query, body) -> dict:
    """程序与当前 Library 概览。**无 Library 时也必须能返回**（用作就绪探测）。"""
    if config is None:
        return {
            "no_library": True, "count": 0, "db_path": None,
            "library_root": None, "library_path": None, "library_name": None,
            "model": None, "endpoint": None,
        }
    payload = _library_payload(config)
    return {
        "no_library": False,
        "count": payload["count"],
        "db_path": payload["db_path"],
        "library_root": payload["library_root"],
        "library_path": payload["path"],
        "library_name": payload["name"],
        "model": config.vlm.model,
        "endpoint": config.vlm.endpoint,
    }


# ═══════════════════════════════════════════════════ Library 管理（P0 产品化）
def _h_library_current(config: Config | None, match, query, body) -> dict:
    """当前 Library。"""
    return _library_payload(config)


def _h_library_recent(config: Config | None, match, query, body) -> dict:
    """最近使用的 Library（含「是否仍有效」标记，供前端标灰）。"""
    state = settings.Settings.load()
    return {
        "items": [
            {
                "path": str(r.path),
                "name": r.name,
                "opened_at": r.opened_at,
                "valid": library.is_valid_path(r.path),
            }
            for r in state.recent
        ],
        "last_library": str(state.last_library) if state.last_library else None,
    }


def _h_library_probe(config: Config | None, match, query, body) -> dict:
    """校验某目录是不是 Library（**只读，不打开、不写设置**）。"""
    target = (body or {}).get("path")
    if not target or not str(target).strip():
        raise ApiError("缺少 path（要校验的目录）", 400)
    return library.probe(str(target).strip())


def _h_library_create(config: Config | None, match, query, body) -> dict:
    """在指定目录新建 Library（幂等；已存在数据库则 409）。

    建完即记入应用设置（最近列表），与 `/api/library/open` 语义一致 ——
    这样即使调用方没跟一次 `open`，下次启动也能在「最近使用」里看到它。
    """
    body = dict(body or {})
    target = str(body.get("path") or "").strip()
    if not target:
        raise ApiError("缺少 path（新建 Library 的目录）", 400)
    root = str(body.get("library_root") or "").strip() or None

    try:
        lib = library.Library.create(target, library_root=root)
    except setup_service.DatabaseExistsError as exc:
        raise ApiError(f"该目录已有书库数据库：{exc.db_path}", 409) from exc
    except (library.LibraryError, OSError, ValueError) as exc:
        raise ApiError(str(exc), 400) from exc

    state = settings.Settings.load()
    state.touch(lib.path, lib.name)
    state.save()

    return {"path": str(lib.path), "name": lib.name}


def _h_list(config: Config, match, query, body) -> dict:
    with Context.open(config) as ctx:
        return {"items": book_service.list_books(ctx)}


def _h_get(config: Config, match, query, body) -> dict:
    with Context.open(config) as ctx:
        try:
            return book_service.get_book(ctx, int(match.group("id")))
        except book_service.BookNotFoundError as exc:
            raise ApiError(str(exc), 404) from exc


def _h_add(config: Config, match, query, body) -> dict:
    body = dict(body or {})
    allow = bool(body.pop("allow_duplicate", False))
    with Context.open(config) as ctx:
        try:
            book_id = book_service.add_book(ctx, body, allow_duplicate=allow)
        except book_service.DuplicateBookError as exc:
            raise ApiError("发现疑似重复记录", 409, {"duplicates": exc.existing}) from exc
        except ValueError as exc:
            raise ApiError(str(exc), 400) from exc
    return {"id": book_id}


def _h_update(config: Config, match, query, body) -> dict:
    with Context.open(config) as ctx:
        try:
            return book_service.update_book(ctx, int(match.group("id")), body or {})
        except book_service.BookNotFoundError as exc:
            raise ApiError(str(exc), 404) from exc
        except ValueError as exc:
            raise ApiError(str(exc), 400) from exc


def _h_delete(config: Config, match, query, body) -> dict:
    with Context.open(config) as ctx:
        try:
            book_service.delete_book(ctx, int(match.group("id")))
        except book_service.BookNotFoundError as exc:
            raise ApiError(str(exc), 404) from exc
    return {"ok": True}


def _h_search(config: Config, match, query, body) -> dict:
    def one(name: str) -> str | None:
        value = query.get(name, [None])[0]
        return value or None

    has_file = one("has_file")
    with Context.open(config) as ctx:
        try:
            items = search_service.search(
                ctx,
                one("q"),
                author=one("author"),
                year=one("year"),
                clc=one("clc"),
                literature_type=one("type"),
                publisher=one("publisher"),
                has_file=None if has_file is None else has_file == "1",
            )
        except ValueError as exc:
            raise ApiError(str(exc), 400) from exc
    return {"items": items}


def _h_export(config: Config, match, query, body) -> dict:
    """导出全部记录到**书库目录**下的 `exports/`（绕过浏览器下载的不确定性）。

    返回写入的绝对路径，前端据此提示用户（并可一键打开所在文件夹）。
    文件名带时间戳，重复导出不会互相覆盖。
    """
    from datetime import datetime

    fmt = (query.get("format", ["json"])[0] or "json").lower()
    if fmt not in ("json", "csv"):
        raise ApiError(f"不支持的格式：{fmt}（仅 json / csv）", 400)
    with Context.open(config) as ctx:
        content = ie.export_json(ctx, pretty=True) if fmt == "json" else ie.export_csv(ctx)

    root = config.library_root or Path.cwd()
    out_dir = Path(root) / EXPORT_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"bookdb-export-{stamp}.{fmt}"
    try:
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise ApiError(f"写入导出文件失败：{target}（{exc}）", 500) from exc

    return {
        "format": fmt,
        "path": str(target),
        "dir": str(out_dir),
        "name": target.name,
        "bytes": len(content.encode("utf-8")),
    }


def _h_export_reveal(config: Config, match, query, body) -> dict:
    """在文件管理器中打开导出目录。"""
    root = config.library_root or Path.cwd()
    out_dir = Path(root) / EXPORT_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)
    launch(str(out_dir))
    return {"ok": True, "dir": str(out_dir)}


def _h_cite(config: Config, match, query, body) -> dict:
    with Context.open(config) as ctx:
        try:
            text = citation_service.cite(ctx, int(match.group("id")))
        except book_service.BookNotFoundError as exc:
            raise ApiError(str(exc), 404) from exc
    return {"citation": text}


# ─────────────────────────────────────────────────── 打开文件（系统默认程序）
def launch(path: Path) -> None:
    """用系统默认程序打开文件（Windows / macOS / Linux 兜底）。

    独立成模块级函数，便于测试时替换（避免真的弹出外部程序）。
    """
    if hasattr(os, "startfile"):                       # Windows：等价于双击
        os.startfile(str(path))                        # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def reveal(path: Path) -> None:
    """在文件管理器中定位到该文件。"""
    if hasattr(os, "startfile"):                       # Windows：打开并选中
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


def _resolve_book_file(config: Config, book_id: int) -> Path:
    """取图书记录关联的文件路径并校验存在性。"""
    with Context.open(config) as ctx:
        try:
            book = book_service.get_book(ctx, book_id)
        except book_service.BookNotFoundError as exc:
            raise ApiError(str(exc), 404) from exc

    stored = book.get("file_path")
    if not stored:
        raise ApiError("该记录未关联文件", 409)
    try:
        path = resolve_path(stored, config.library_root)
    except LibraryRootMissingError as exc:
        raise ApiError(str(exc), 409) from exc
    except ValueError as exc:                      # 含 PathSyntaxError
        raise ApiError(str(exc), 400) from exc
    if path is None or not path.exists():
        raise ApiError(f"文件不存在：{path}", 404)
    return path


def _h_open_file(config: Config, match, query, body) -> dict:
    path = _resolve_book_file(config, int(match.group("id")))
    try:
        launch(path)
    except OSError as exc:
        raise ApiError(f"无法打开文件：{exc}", 500) from exc
    return {"ok": True, "path": str(path)}


def _h_open_folder(config: Config, match, query, body) -> dict:
    path = _resolve_book_file(config, int(match.group("id")))
    try:
        reveal(path)
    except OSError as exc:
        raise ApiError(f"无法打开文件夹：{exc}", 500) from exc
    return {"ok": True, "path": str(path)}


# ─────────────────────────────────────────────────────────── 导入（预览 / 执行）
def _build_plan(ctx: Context, body: dict) -> ie.ImportPlan:
    fmt = (body.get("format") or "json").lower()
    content = body.get("content") or ""
    if fmt == "json":
        return ie.build_json_plan(ctx, ie.parse_json_text(content), mode_override=body.get("mode"))
    if fmt == "csv":
        return ie.build_plan(ctx, "full", ie.parse_csv_text(content))
    raise ApiError(f"不支持的导入格式：{fmt}", 400)


def _plan_payload(plan: ie.ImportPlan) -> dict:
    return {
        "ok": plan.ok,
        "errors": plan.errors,
        "total": plan.total,
        "inserts": plan.inserts,
        "updates": [
            {"id": u["id"], "title": (u["before"] or {}).get("title"),
             "before": u["before"], "fields": u["fields"]}
            for u in plan.updates
        ],
    }


def _h_import_preview(config: Config, match, query, body) -> dict:
    with Context.open(config) as ctx:
        try:
            plan = _build_plan(ctx, body or {})
        except ie.ImportValidationError as exc:
            return {"ok": False, "errors": exc.errors, "total": 0, "inserts": [], "updates": []}
    return _plan_payload(plan)


def _h_import_apply(config: Config, match, query, body) -> dict:
    with Context.open(config) as ctx:
        try:
            plan = _build_plan(ctx, body or {})
        except ie.ImportValidationError as exc:
            raise ApiError("导入数据校验失败", 400, {"errors": exc.errors}) from exc
        if not plan.ok:
            raise ApiError("导入数据校验失败", 400, {"errors": plan.errors})
        ie.apply_plan(ctx, plan)
    return {"applied": plan.total, "inserts": len(plan.inserts), "updates": len(plan.updates)}


# ─────────────────────────────────────────────────────────── 重命名（预览 / 执行）
def _build_rename(ctx: Context, book_id: int) -> rename_service.RenamePlan:
    try:
        return rename_service.build_plan(ctx, book_id)
    except book_service.BookNotFoundError as exc:
        raise ApiError(str(exc), 404) from exc
    except rename_service.NoFileError as exc:
        raise ApiError(str(exc), 409) from exc


def _h_rename_preview(config: Config, match, query, body) -> dict:
    with Context.open(config) as ctx:
        plan = _build_rename(ctx, int(match.group("id")))
    return {"old_name": plan.old_name, "new_name": plan.new_name, "changed": plan.changed}


def _h_rename_apply(config: Config, match, query, body) -> dict:
    overwrite = bool((body or {}).get("overwrite", False))
    with Context.open(config) as ctx:
        plan = _build_rename(ctx, int(match.group("id")))
        try:
            rename_service.apply_plan(ctx, plan, overwrite=overwrite)
        except FileNotFoundError as exc:
            raise ApiError(str(exc), 404) from exc
        except rename_service.TargetExistsError as exc:
            raise ApiError(str(exc), 409) from exc
    return {"ok": True, "changed": plan.changed, "new_name": plan.new_name}


# ══════════════════════════════════════════════════ 批量操作（多选）
def _batch_ids(body) -> list[int]:
    raw = (body or {}).get("ids")
    if not isinstance(raw, list) or not raw:
        raise ApiError("缺少 ids（要操作的记录 id 列表）", 400)
    try:
        return [int(i) for i in raw]
    except (TypeError, ValueError) as exc:
        raise ApiError("ids 必须是整数数组", 400) from exc


def _batch_payload(plan) -> dict:
    return {
        "operation": plan.operation,
        "items": [
            {
                "id": i.book_id, "title": i.title, "status": i.status,
                "old": i.old, "new": i.new, "size": i.size,
                "error": i.error, "actionable": i.actionable,
            }
            for i in plan.items
        ],
        "actionable": len(plan.actionable),
        "blocked": len(plan.blocked),
        "total_size": plan.total_size,
    }


def _selected(body) -> set[int] | None:
    raw = (body or {}).get("selected")
    if not isinstance(raw, list):
        return None
    return {int(i) for i in raw}


def _h_batch_rename_preview(config: Config, match, query, body) -> dict:
    with Context.open(config) as ctx:
        try:
            plan = batch_service.build_rename_plan(ctx, _batch_ids(body))
        except batch_service.BatchError as exc:
            raise ApiError(str(exc), 400) from exc
    return _batch_payload(plan)


def _h_batch_rename_apply(config: Config, match, query, body) -> dict:
    body = dict(body or {})
    ids = _batch_ids(body)
    with Context.open(config) as ctx:
        try:
            plan = batch_service.build_rename_plan(ctx, ids)
            return batch_service.apply_plan(
                ctx, plan, selected=_selected(body), overwrite=bool(body.get("overwrite"))
            )
        except batch_service.BatchError as exc:
            raise ApiError(str(exc), 400) from exc


def _h_batch_move_preview(config: Config, match, query, body) -> dict:
    body = dict(body or {})
    dest = str(body.get("destination") or "").strip()
    if not dest:
        raise ApiError("缺少 destination（目标目录）", 400)
    with Context.open(config) as ctx:
        try:
            plan = batch_service.build_move_plan(ctx, _batch_ids(body), dest)
        except batch_service.BatchError as exc:
            raise ApiError(str(exc), 400) from exc
    return _batch_payload(plan)


def _h_batch_move_apply(config: Config, match, query, body) -> dict:
    body = dict(body or {})
    dest = str(body.get("destination") or "").strip()
    if not dest:
        raise ApiError("缺少 destination（目标目录）", 400)
    with Context.open(config) as ctx:
        try:
            plan = batch_service.build_move_plan(ctx, _batch_ids(body), dest)
            return batch_service.apply_plan(
                ctx, plan, selected=_selected(body), overwrite=bool(body.get("overwrite"))
            )
        except batch_service.BatchError as exc:
            raise ApiError(str(exc), 400) from exc


def _h_batch_delete_preview(config: Config, match, query, body) -> dict:
    body = dict(body or {})
    with_files = bool(body.get("with_files"))
    with Context.open(config) as ctx:
        try:
            plan = batch_service.build_delete_plan(ctx, _batch_ids(body), with_files=with_files)
        except batch_service.BatchError as exc:
            raise ApiError(str(exc), 400) from exc
    return _batch_payload(plan)


def _h_batch_delete_apply(config: Config, match, query, body) -> dict:
    """删除（记录 / 记录+文件）。**必须带 `confirm: true`** —— 后端第二道闸。"""
    body = dict(body or {})
    if not body.get("confirm"):
        raise ApiError("删除操作需要 confirm=true（前端应已让用户明确确认）", 400)
    with_files = bool(body.get("with_files"))
    ids = _batch_ids(body)

    with Context.open(config) as ctx:
        try:
            plan = batch_service.build_delete_plan(ctx, ids, with_files=with_files)
            return batch_service.apply_plan(
                ctx, plan, selected=_selected(body), confirm=True
            )
        except batch_service.BatchError as exc:
            raise ApiError(str(exc), 400) from exc


# ══════════════════════════════════════════════════ 内置模型（AI）
#: 下载进度（进程内单例 —— 同一时刻只允许一个下载任务）
_DL: dict = {"key": None, "done": 0, "total": 0, "state": "idle", "error": None}
_DL_LOCK = threading.Lock()
_DL_CANCEL = threading.Event()


def _model_payload(st) -> dict:
    spec = st.spec
    return {
        "key": spec.key,
        "name": spec.name,
        "note": spec.note,
        "size": spec.total_size,
        "size_human": model_service.human_size(spec.total_size),
        "ready": st.ready,
        "source": st.source,          # 'local' | 'ollama' | 'none'
        "downloaded": st.downloaded,
    }


def _h_model_list(config, match, query, body) -> dict:
    from ..llm.adapters import llamacpp

    states = model_service.list_states()
    return {
        "items": [_model_payload(s) for s in states],
        "default": model_service.DEFAULT_KEY,
        "builtin_model": (config.vlm.builtin_model if config else None),
        "binary_ready": llamacpp.is_available(),
        "models_dir": str(model_service.models_dir()),
    }


def _h_model_status(config, match, query, body) -> dict:
    """当前实际会使用哪个后端（内置 / Ollama / 都不可用）。

    ⚠️ 必须容忍 `config is None`（尚未打开 Library 时的欢迎页状态）——
    模型是应用级资源，无库时也要能查看/下载。
    """
    from ..config import VlmConfig
    from ..llm.adapters import llamacpp
    from ..llm.base import ModelUnavailableError
    from ..services import llm as llm_svc

    vlm = config.vlm if config is not None else VlmConfig()
    out = {
        "backend": vlm.backend,
        "builtin_model": vlm.builtin_model,
        "binary_ready": llamacpp.is_available(),
        "ollama_endpoint": vlm.endpoint,
        "ollama_reachable": llm_svc._ollama_reachable(vlm.endpoint),
        "active": None,
        "hint": None,
        "no_library": config is None,
    }
    try:
        adapter = llm_svc.get_adapter(vlm)
    except ModelUnavailableError as exc:
        out["hint"] = str(exc)
    else:
        name = adapter.__class__.__name__
        out["active"] = "builtin" if "Llama" in name else "ollama"
    return out


def _h_model_select(config, match, query, body) -> dict:
    """选择使用哪款内置模型（写回 book.toml 的 [vlm].builtin_model）。"""
    key = str((body or {}).get("key") or "").strip()
    if not key:
        raise ApiError("缺少 key", 400)
    try:
        spec = model_service.find(key)
    except model_service.ModelError as exc:
        raise ApiError(str(exc), 400) from exc

    if config is None or config.config_path is None:
        raise ApiError("当前 Library 缺少 book.toml，无法保存设置", 409)

    config.vlm.builtin_model = spec.key
    config.save()
    return {"ok": True, "builtin_model": spec.key, "name": spec.name}


def _h_model_download(config, match, query, body) -> dict:
    """启动后台下载（幂等：已在下载则拒绝）。进度用 /api/model/progress 查。"""
    from ..llm.adapters import llamacpp

    if not llamacpp.is_available():
        raise ApiError(
            "未找到内置推理组件（llama-server）。安装版应自带；"
            "源码运行请先执行 bash packaging/fetch_llama.sh", 409,
        )

    key = str((body or {}).get("key") or "").strip()
    try:
        spec = model_service.find(key)
    except model_service.ModelError as exc:
        raise ApiError(str(exc), 400) from exc

    with _DL_LOCK:
        if _DL["state"] == "running":
            raise ApiError(f"已有下载任务在进行中（{_DL['key']}）", 409)
        _DL_CANCEL.clear()
        _DL.update(key=spec.key, done=0, total=spec.total_size,
                   state="running", error=None)

    def worker() -> None:
        try:
            model_service.download(
                spec,
                on_progress=lambda got, total: _DL.update(done=got, total=total),
                cancel=_DL_CANCEL.is_set,
            )
        except Exception as exc:  # noqa: BLE001 - 错误要回传给前端
            _DL.update(state="error", error=f"{type(exc).__name__}: {exc}")
        else:
            _DL.update(state="done", done=spec.total_size)

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "key": spec.key, "total": spec.total_size,
            "total_human": model_service.human_size(spec.total_size)}


def _h_model_progress(config, match, query, body) -> dict:
    with _DL_LOCK:
        snap = dict(_DL)
    total = snap["total"] or 0
    snap["percent"] = round(snap["done"] / total * 100, 1) if total else 0.0
    snap["done_human"] = model_service.human_size(snap["done"])
    snap["total_human"] = model_service.human_size(total)
    return snap


def _h_model_cancel(config, match, query, body) -> dict:
    if _DL["state"] != "running":
        return {"ok": True, "state": _DL["state"]}
    _DL_CANCEL.set()
    return {"ok": True, "state": "cancelling"}


def _h_model_remove(config, match, query, body) -> dict:
    key = str((body or {}).get("key") or "").strip()
    try:
        spec = model_service.find(key)
    except model_service.ModelError as exc:
        raise ApiError(str(exc), 400) from exc
    freed = model_service.remove(spec)
    return {"ok": True, "freed": freed, "freed_human": model_service.human_size(freed)}


def _h_model_dir_get(config, match, query, body) -> dict:
    """当前模型目录 + 默认目录 + 占用大小（供 GUI 展示/修改）。"""
    from ..gui.settings import Settings, default_models_dir

    current = model_service.models_dir()
    try:
        used = sum(f.stat().st_size for f in current.rglob("*") if f.is_file())
    except OSError:
        used = 0
    return {
        "current": str(current),
        "default": str(default_models_dir()),
        "configured": Settings.load().models_dir or "",
        "used": used,
        "used_human": model_service.human_size(used),
        "is_custom": str(current) != str(default_models_dir()),
    }


def _h_model_dir_open(config, match, query, body) -> dict:
    """在系统文件管理器中打开模型目录。"""
    path = model_service.models_dir()
    path.mkdir(parents=True, exist_ok=True)
    launch(str(path))              # 模块级 launch()，测试可替换
    return {"ok": True, "path": str(path)}


def _h_model_dir_set(config, match, query, body) -> dict:
    """修改模型目录。`move: true` 时把已下好的模型一并搬过去。

    ⚠️ 只搬**本程序下载的**模型（Ollama 里的 blob 不动，那是 Ollama 的数据）。
    """
    import shutil

    from ..gui.settings import Settings, default_models_dir

    body = dict(body or {})
    target_raw = str(body.get("path") or "").strip()
    if not target_raw:
        raise ApiError("缺少 path", 400)
    target = Path(target_raw).expanduser()

    old = model_service.models_dir()
    if target == old:
        return {"ok": True, "path": str(target), "moved": 0}

    # 校验目标可写（不存在则尝试创建）—— 失败要明确报错，不静默
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".bookdb_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise ApiError(f"目标目录不可写：{target}（{exc}）", 400) from exc

    moved_bytes = 0
    if body.get("move") and old.is_dir() and any(old.iterdir()):
        for item in old.iterdir():
            dest = target / item.name
            if dest.exists():
                continue            # 已存在则不覆盖（保守）
            try:
                shutil.move(str(item), str(dest))
                moved_bytes += sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
            except OSError as exc:
                raise ApiError(f"迁移 {item.name} 失败：{exc}", 500) from exc

    settings = Settings.load()
    settings.models_dir = "" if target == default_models_dir() else str(target)
    settings.save()
    return {
        "ok": True, "path": str(target),
        "moved_bytes": moved_bytes,
        "moved_human": model_service.human_size(moved_bytes),
    }


# ─────────────────────────────────────────────────────────── 版权页提取（VLM）
def _h_enrich(config: Config, match, query, body) -> dict:
    body = body or {}
    raw_path = body.get("path")
    if not raw_path:
        raise ApiError("缺少 path（版权页文件路径）", 400)
    path = Path(raw_path)
    if not path.exists():
        raise ApiError(f"文件不存在：{path}", 400)

    page = body.get("page")
    try:
        if path.suffix.lower() == ".pdf":
            result = enrich_service.enrich_from_pdf(config, path, int(page) if page else None)
        else:
            result = enrich_service.enrich_from_image(config, path)
    except ModelUnavailableError as exc:
        raise ApiError(f"本地模型不可用：{exc}", 503) from exc
    except enrich_service.CopyrightPageNotFound as exc:
        raise ApiError(str(exc), 422) from exc
    except (ValueError, RuntimeError) as exc:
        raise ApiError(str(exc), 400) from exc
    return {"source": result.source, "page": result.page, "metadata": result.metadata}


def _h_enrich_save(config: Config, match, query, body) -> dict:
    body = dict(body or {})
    fields = body.get("metadata") or {}
    allow = bool(body.get("allow_duplicate", False))
    with Context.open(config) as ctx:
        try:
            book_id = book_service.add_book(ctx, fields, allow_duplicate=allow)
        except book_service.DuplicateBookError as exc:
            raise ApiError("发现疑似重复记录", 409, {"duplicates": exc.existing}) from exc
        except ValueError as exc:
            raise ApiError(str(exc), 400) from exc
    return {"id": book_id}


# ─────────────────────────────────────────────── 批量元数据补全（VLM 批量）
def _h_enrich_batch_preview(config: Config, match, query, body) -> dict:
    """列出待补全的记录（**不跑模型**）—— 供 GUI 展示"哪些需要补"。"""
    body = dict(body or {})
    directory = (body.get("directory") or "").strip() or None
    recursive = bool(body.get("recursive", True))

    with Context.open(config) as ctx:
        books = book_service.list_books(ctx)
        try:
            plan = enrich_batch.build_batch_plan(
                ctx, books=books, directory=directory, recursive=recursive
            )
        except scan_service.DirectoryNotFoundError as exc:
            raise ApiError(str(exc), 400) from exc

        return {
            "scope": directory or "全库",
            "total": len(plan.items),
            "todo": len(plan.todo),
            "items": [
                {
                    "id": i.book_id,
                    "title": i.title,
                    "file": str(i.pdf) if i.pdf else "",
                    "missing": i.missing,
                    "skip": i.skipped,
                }
                for i in plan.items
            ],
            "unresolved": [str(p) for p in plan.unresolved_files],
        }


def _h_enrich_batch_item(config: Config, match, query, body) -> dict:
    """对**单条**记录跑 VLM 补全（不写库）—— GUI 逐条调用以显示进度。"""
    body = dict(body or {})
    try:
        book_id = int(body.get("id"))
    except (TypeError, ValueError) as exc:
        raise ApiError("缺少合法的 id", 400) from exc

    with Context.open(config) as ctx:
        book = dao.get_book(ctx.conn, book_id, with_abstract=False)
        if book is None:
            raise ApiError(f"记录不存在：#{book_id}", 404)

        pdf = _resolve_for_batch(ctx, book.get("file_path"))
        if pdf is None:
            raise ApiError("该记录无文件关联或路径无法解析", 400)
        if not pdf.is_file():
            raise ApiError(f"文件不存在：{pdf}", 404)

        try:
            result = enrich_service.enrich_from_pdf(ctx.config, pdf)
        except ModelUnavailableError as exc:
            raise ApiError(f"本地模型不可用：{exc}", 503) from exc
        except enrich_service.CopyrightPageNotFound as exc:
            raise ApiError(str(exc), 422) from exc
        except (ValueError, RuntimeError) as exc:
            raise ApiError(str(exc), 400) from exc

    return {
        "id": book_id,
        "page": result.page,
        "metadata": result.metadata,
        "missing": enrich_batch.missing_fields(book),
    }


def _resolve_for_batch(ctx: Context, stored: str | None):
    from ..paths import resolve as _resolve

    if not stored:
        return None
    try:
        return _resolve(stored, ctx.config.library_root)
    except Exception:  # noqa: BLE001
        return None


def _h_enrich_batch_apply(config: Config, match, query, body) -> dict:
    """把 GUI 复核过的若干条元数据写回库（没写入的一律不动）。"""
    body = dict(body or {})
    raw = body.get("updates")
    if not isinstance(raw, list) or not raw:
        raise ApiError("缺少 updates（要写入的记录列表）", 400)

    updated, failed = [], []
    with Context.open(config) as ctx:
        for entry in raw:
            try:
                book_id = int(entry["id"])
                fields = dict(entry["metadata"])
            except (KeyError, TypeError, ValueError) as exc:
                failed.append({"id": entry.get("id"), "error": f"字段不合法：{exc}"})
                continue
            fields.pop("file_path", None)          # 绝不动路径
            patch = {k: v for k, v in fields.items() if v is not None}
            if not patch:
                continue
            try:
                dao.update_book_fields(ctx.conn, book_id, patch)
                updated.append({"id": book_id, "fields": sorted(patch)})
            except Exception as exc:               # noqa: BLE001
                failed.append({"id": book_id, "error": f"{type(exc).__name__}: {exc}"})

    return {"updated": updated, "failed": failed}


# ─────────────────────────────────────────────────── 扫描建库（预览 / 执行）
def _h_scan_preview(config: Config, match, query, body) -> dict:
    body = dict(body or {})
    directory = (body.get("directory") or "").strip()
    if not directory:
        raise ApiError("缺少扫描目录", 400)
    recursive = bool(body.get("recursive", True))

    with Context.open(config) as ctx:
        try:
            plan = scan_service.build_plan(ctx, directory, recursive=recursive)
        except scan_service.DirectoryNotFoundError as exc:
            raise ApiError(str(exc), 400) from exc

    return {
        "directory": str(plan.directory),
        "total": plan.total_files,
        "skipped": plan.skipped_existing,
        "creatable": plan.new_count,
        "duplicates": plan.dup_count,
        "items": [
            {
                "index": c.index,
                "name": c.path.name,
                "creatable": c.creatable,
                "already": bool(c.already),
                "duplicates": len(c.duplicates),
                "error": c.error,
                "title": c.meta.get("title") or (c.already or {}).get("title") or "",
                "author": c.meta.get("author") or (c.already or {}).get("author") or "",
                "year": str(c.meta.get("publication_year")
                            or (c.already or {}).get("publication_year") or ""),
            }
            for c in plan.candidates
        ],
    }


def _h_scan_apply(config: Config, match, query, body) -> dict:
    body = dict(body or {})
    directory = (body.get("directory") or "").strip()
    if not directory:
        raise ApiError("缺少扫描目录", 400)
    recursive = bool(body.get("recursive", True))
    raw_sel = body.get("selected")
    selected = {int(i) for i in raw_sel} if raw_sel is not None else None

    with Context.open(config) as ctx:
        try:
            plan = scan_service.build_plan(ctx, directory, recursive=recursive)
        except scan_service.DirectoryNotFoundError as exc:
            raise ApiError(str(exc), 400) from exc
        try:
            result = scan_service.apply_plan(ctx, plan, selected=selected)
        except ValueError as exc:
            raise ApiError(str(exc), 400) from exc

    return {"created": result.created_count, "skipped": result.skipped, "ids": result.created}


# ═══════════════════════════════════════════════════════════════════ 路由表
ROUTES = [
    ("GET", r"^/api/meta$", _h_meta),
    ("GET", r"^/api/library/current$", _h_library_current),
    ("GET", r"^/api/library/recent$", _h_library_recent),
    ("POST", r"^/api/library/probe$", _h_library_probe),
    ("POST", r"^/api/library/create$", _h_library_create),
    # 注意：/api/library/open 不进本表 —— 它需要访问服务自身（见 _handle 的特殊路由）
    ("POST", r"^/api/batch/rename/preview$", _h_batch_rename_preview),
    ("POST", r"^/api/batch/rename/apply$", _h_batch_rename_apply),
    ("POST", r"^/api/batch/move/preview$", _h_batch_move_preview),
    ("POST", r"^/api/batch/move/apply$", _h_batch_move_apply),
    ("POST", r"^/api/batch/delete/preview$", _h_batch_delete_preview),
    ("POST", r"^/api/batch/delete/apply$", _h_batch_delete_apply),
    # 内置模型（AI）
    ("GET", r"^/api/model/list$", _h_model_list),
    ("GET", r"^/api/model/status$", _h_model_status),
    ("GET", r"^/api/model/progress$", _h_model_progress),
    ("POST", r"^/api/model/select$", _h_model_select),
    ("POST", r"^/api/model/download$", _h_model_download),
    ("POST", r"^/api/model/cancel$", _h_model_cancel),
    ("POST", r"^/api/model/remove$", _h_model_remove),
    ("GET", r"^/api/model/dir$", _h_model_dir_get),
    ("POST", r"^/api/model/dir$", _h_model_dir_set),
    ("POST", r"^/api/model/dir/open$", _h_model_dir_open),
    ("GET", r"^/api/books$", _h_list),
    ("POST", r"^/api/books$", _h_add),
    ("GET", r"^/api/books/(?P<id>\d+)$", _h_get),
    ("PUT", r"^/api/books/(?P<id>\d+)$", _h_update),
    ("DELETE", r"^/api/books/(?P<id>\d+)$", _h_delete),
    ("GET", r"^/api/search$", _h_search),
    ("GET", r"^/api/export$", _h_export),
    ("POST", r"^/api/export/reveal$", _h_export_reveal),
    ("GET", r"^/api/cite/(?P<id>\d+)$", _h_cite),
    ("POST", r"^/api/open/(?P<id>\d+)$", _h_open_file),
    ("POST", r"^/api/reveal/(?P<id>\d+)$", _h_open_folder),
    ("POST", r"^/api/import/preview$", _h_import_preview),
    ("POST", r"^/api/import/apply$", _h_import_apply),
    ("GET", r"^/api/rename/(?P<id>\d+)$", _h_rename_preview),
    ("POST", r"^/api/rename/(?P<id>\d+)/apply$", _h_rename_apply),
    ("POST", r"^/api/enrich$", _h_enrich),
    ("POST", r"^/api/enrich/save$", _h_enrich_save),
    ("POST", r"^/api/enrich/batch/preview$", _h_enrich_batch_preview),
    ("POST", r"^/api/enrich/batch/item$", _h_enrich_batch_item),
    ("POST", r"^/api/enrich/batch/apply$", _h_enrich_batch_apply),
    ("POST", r"^/api/scan/preview$", _h_scan_preview),
    ("POST", r"^/api/scan/apply$", _h_scan_apply),
]
_COMPILED = [(m, re.compile(p), h) for m, p, h in ROUTES]


# ═══════════════════════════════════════════════════════════════════ HTTP 层
class Handler(BaseHTTPRequestHandler):
    server_version = "bookdb-gui"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # 静默，避免污染控制台
        pass

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    # ------------------------------------------------------------------ core
    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            if method == "GET":
                return self._serve_static(parsed.path)
            return self._send(405, {"error": "method not allowed"})

        query = parse_qs(parsed.query)
        body = self._read_body()
        config = self.server.config  # type: ignore[attr-defined]

        # 无 Library 时，只放行欢迎页需要的接口；其余返回 409（前端据此回欢迎页）
        if config is None and parsed.path not in NO_LIBRARY_PATHS:
            return self._send(409, {"error": "尚未打开书库", "no_library": True})

        # 特殊路由：切换 Library 要替换服务持有的 config 引用 —— 只有它能碰到
        # `self.server`，故不进通用路由表（否则得改动全部 handler 签名）。
        if parsed.path == "/api/library/open":
            if method != "POST":
                return self._send(405, {"error": "method not allowed"})
            return self._open_library(body)

        matched_path = False

        for verb, pattern, handler in _COMPILED:
            match = pattern.match(parsed.path)
            if not match:
                continue
            matched_path = True
            if verb != method:
                continue
            try:
                return self._send(200, handler(config, match, query, body))
            except ApiError as exc:
                return self._send(exc.status, {"error": str(exc), **exc.payload})
            except Exception as exc:  # 兜底，避免裸 500
                return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

        self._send(405 if matched_path else 404, {"error": f"未知接口：{parsed.path}"})

    # ------------------------------------------------------- Library 切换
    def _open_library(self, body: dict) -> None:
        """打开指定 Library 并**立即生效**（P0 单开：同一时刻只有一个当前 Library）。

        实现 = 替换服务持有的 config 引用。每个请求都从 `server.config` 现取，
        因此替换后前端 `location.reload()` 即切到新库，**无需重启进程**。
        """
        target = str((body or {}).get("path") or "").strip()
        if not target:
            return self._send(400, {"error": "缺少 path（要打开的 Library 目录）"})
        try:
            lib = library.Library.open(target)
        except library.LibraryError as exc:
            return self._send(400, {"error": str(exc)})

        state = settings.Settings.load()
        state.touch(lib.path, lib.name)
        state.save()

        self.server.config = lib.config      # type: ignore[attr-defined]
        return self._send(200, {"path": str(lib.path), "name": lib.name})

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _send(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        target = (WEB_DIR / rel).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            body = b"Not Found"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, config: Config | None):
        super().__init__(addr, handler)
        #: 可被 `/api/library/open` 替换（P0 单开：同一时刻只有一个当前 Library）。
        #: **允许为 None** —— 程序可以「尚未打开任何 Library」的状态运行（显示欢迎页）。
        self.config = config


def create_server(
    config: Config | None = None,
    host: str = "127.0.0.1",
    port: int = 0,
) -> ThreadingHTTPServer:
    """创建内部 HTTP 服务（仅绑 127.0.0.1；port=0 由系统分配空闲端口）。

    `config=None` 表示「尚未打开 Library」—— 服务仍可运行，只有欢迎页能用的接口可用。
    """
    return _Server((host, port), Handler, config)


def server_url(server: ThreadingHTTPServer) -> str:
    host, port = server.server_address[:2]
    return f"http://{host}:{port}/"
