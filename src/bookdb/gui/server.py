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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..config import Config
from ..paths import LibraryRootMissingError
from ..paths import resolve as resolve_path
from ..services import books as book_service
from ..services import citation as citation_service
from ..services import enrich as enrich_service
from ..services import import_export as ie
from ..services import rename as rename_service
from ..services import scan as scan_service
from ..services import search as search_service
from ..services.context import Context
from ..services.llm import ModelUnavailableError

WEB_DIR = Path(__file__).parent / "web"


class ApiError(Exception):
    """带 HTTP 状态码与附加负载的接口错误。"""

    def __init__(self, message: str, status: int = 400, payload: dict | None = None):
        self.status = status
        self.payload = payload or {}
        super().__init__(message)


# ═══════════════════════════════════════════════════════════════════ 处理函数
# 签名：(config, match, query, body) → 可 JSON 序列化的负载
def _h_meta(config: Config, match, query, body) -> dict:
    with Context.open(config) as ctx:
        count = len(book_service.list_books(ctx))
    return {
        "count": count,
        "db_path": str(config.db_path),
        "library_root": str(config.library_root) if config.library_root else None,
        "model": config.vlm.model,
        "endpoint": config.vlm.endpoint,
    }


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
    fmt = (query.get("format", ["json"])[0] or "json").lower()
    if fmt not in ("json", "csv"):
        raise ApiError(f"不支持的格式：{fmt}（仅 json / csv）", 400)
    with Context.open(config) as ctx:
        content = ie.export_json(ctx, pretty=True) if fmt == "json" else ie.export_csv(ctx)
    return {"format": fmt, "content": content}


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
    ("GET", r"^/api/books$", _h_list),
    ("POST", r"^/api/books$", _h_add),
    ("GET", r"^/api/books/(?P<id>\d+)$", _h_get),
    ("PUT", r"^/api/books/(?P<id>\d+)$", _h_update),
    ("DELETE", r"^/api/books/(?P<id>\d+)$", _h_delete),
    ("GET", r"^/api/search$", _h_search),
    ("GET", r"^/api/export$", _h_export),
    ("GET", r"^/api/cite/(?P<id>\d+)$", _h_cite),
    ("POST", r"^/api/open/(?P<id>\d+)$", _h_open_file),
    ("POST", r"^/api/reveal/(?P<id>\d+)$", _h_open_folder),
    ("POST", r"^/api/import/preview$", _h_import_preview),
    ("POST", r"^/api/import/apply$", _h_import_apply),
    ("GET", r"^/api/rename/(?P<id>\d+)$", _h_rename_preview),
    ("POST", r"^/api/rename/(?P<id>\d+)/apply$", _h_rename_apply),
    ("POST", r"^/api/enrich$", _h_enrich),
    ("POST", r"^/api/enrich/save$", _h_enrich_save),
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

    def __init__(self, addr, handler, config: Config):
        super().__init__(addr, handler)
        self.config = config


def create_server(config: Config, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """创建内部 HTTP 服务（仅绑 127.0.0.1；port=0 由系统分配空闲端口）。"""
    return _Server((host, port), Handler, config)


def server_url(server: ThreadingHTTPServer) -> str:
    host, port = server.server_address[:2]
    return f"http://{host}:{port}/"
