"""GUI 启动（PRD §16）—— pywebview 窗口 + 内部 HTTP 端点。

pywebview **懒加载**：本模块可被导入而不要求安装 pywebview（便于测试与 `--help`）。

启动顺序（关键）：
  1. 起内部 HTTP 服务（仅 127.0.0.1）
  2. 健康检查轮询，确认就绪
  3. 再创建 WebView 窗口 —— 否则窗口会抢在服务就绪前加载 → 白屏
"""

from __future__ import annotations

import threading
import time
import urllib.request

from ..config import Config
from .server import create_server, server_url

WINDOW_TITLE = "bookdb — 图书元数据"


class JsApi:
    """暴露给前端的最小 JS 桥。

    只用于浏览器无法完成的操作（本地文件选择）—— 其余交互一律走 `fetch('/api/...')`。
    """

    def pick_file(self, kind: str = "pdf") -> str | None:
        import webview

        if kind == "image":
            file_types = ("图片 (*.png;*.jpg;*.jpeg;*.webp)", "所有文件 (*.*)")
        else:
            file_types = ("PDF (*.pdf)", "所有文件 (*.*)")

        result = webview.windows[0].create_file_dialog(
            webview.OPEN_DIALOG, file_types=file_types
        )
        return result[0] if result else None


def wait_ready(url: str, timeout: float = 5.0) -> bool:
    """轮询 /api/meta 直到服务就绪（避免 WebView 白屏）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}api/meta", timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.1)
    return False


def run(config: Config) -> None:
    """启动 GUI，阻塞直到窗口关闭。"""
    try:
        import webview
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise RuntimeError(
            'GUI 需要 pywebview。请安装：uv pip install -e ".[gui]"'
        ) from exc

    server = create_server(config)
    url = server_url(server)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    wait_ready(url)

    try:
        webview.create_window(
            WINDOW_TITLE, url, width=1200, height=820, min_size=(900, 600), js_api=JsApi()
        )
        webview.start()
    finally:
        server.shutdown()
        server.server_close()
