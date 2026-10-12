"""llama.cpp（llama-server）适配器 —— **内置本地视觉模型**，不依赖 Ollama。

为什么用 llama-server 子进程而不是 Python 绑定：
  · `llama-cpp-python` 最新预编译轮子（0.3.36）**不认识 qwen25vl 架构**（实测报
    `unknown model architecture: 'qwen25vl'`），自编译又要求用户装 C++ 工具链；
  · llama.cpp 官方 release 提供 **Windows 预编译二进制**（CPU 版仅 ~20MB），
    对最新架构跟进及时；
  · 它提供 **OpenAI 兼容**的 `/v1/chat/completions`，本适配器只发 HTTP。

⚠️ 实测踩坑（务必保留这段逻辑）：
  端口被别的进程占用时，llama-server **既不报错也不退出**，而且仍然打印
  `listening on http://...`（谎报）。因此就绪判定**不能只看日志或端口连通性**，
  必须「健康检查 + 响应体校验」双重确认；超时后换端口重试。
"""

from __future__ import annotations

import atexit
import base64
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..base import ModelUnavailableError, VLMAdapter

#: 启动到就绪的最长等待（秒）。模型加载实测 ~8s（3B/CPU），留足余量。
BOOT_TIMEOUT = 120.0

#: 端口冲突时的重试次数
PORT_RETRIES = 3

#: 保留一份全局引用，保证进程退出时能收尸（异常路径也不留孤儿进程）
_LIVE: list["LlamaServerAdapter"] = []
_LOCK = threading.Lock()


def _repo_root() -> Path | None:
    """源码仓库根（`<repo>/src/bookdb/llm/adapters/llamacpp.py` 上溯 4 级）。

    打包后该路径不再有意义，故仅作开发期兜底。
    """
    try:
        root = Path(__file__).resolve().parents[4]
    except IndexError:            # pragma: no cover
        return None
    return root if (root / "src" / "bookdb").is_dir() else None


def find_server_exe() -> Path | None:
    """定位 llama-server 可执行文件。

    查找顺序：
      1. 环境变量 `BOOKDB_LLAMA_SERVER`（测试 / 自定义）
      2. 打包位置：`sys._MEIPASS/llama/llama-server.exe`（安装版）
      3. 源码仓库：`<repo>/vendor/llama/llama-server.exe`（开发用，
         由 `packaging/fetch_llama.sh` 放置）
    """
    import os

    env = os.environ.get("BOOKDB_LLAMA_SERVER")
    if env and Path(env).is_file():
        return Path(env)

    candidates: list[Path] = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidates.append(Path(base) / "llama" / "llama-server.exe")
    root = _repo_root()
    if root is not None:
        candidates.append(root / "vendor" / "llama" / "llama-server.exe")

    for path in candidates:
        if path.is_file():
            return path
    return None


def is_available() -> bool:
    """内置推理后端是否可用（有二进制即算可用；模型另需下载）。"""
    return find_server_exe() is not None


def _free_port() -> int:
    """向系统要一个空闲端口。

    注意：这里有**固有竞态**（拿到后到真正 bind 之间可能被抢），所以启动流程里
    仍要有端口冲突的重试逻辑 —— 不能只靠这里。
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _port_is_taken(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


class LlamaServerAdapter(VLMAdapter):
    """通过 llama-server 子进程做本地视觉推理。"""

    def __init__(
        self,
        *,
        model_path: str | Path,
        mmproj_path: str | Path,
        endpoint: str = "http://127.0.0.1",     # 仅用其 host 部分
        model: str = "local",                   # 请求体里的名字（无实际作用）
        threads: int | None = None,
        ctx_size: int = 16384,
        n_gpu_layers: int = 99,     # 层数 offload 到 GPU（99=全部；无 GPU 时 llama.cpp 自动回退 CPU）
        timeout: float = 300.0,
    ):
        super().__init__(endpoint=endpoint, model=model)
        self.model_path = Path(model_path)
        self.mmproj_path = Path(mmproj_path)
        self.threads = threads or max(2, (__import__("os").cpu_count() or 4) // 2)
        self.ctx_size = ctx_size
        self.n_gpu_layers = n_gpu_layers
        self.timeout = timeout

        self._proc: subprocess.Popen | None = None
        self._port: int | None = None
        self._log_path: Path | None = None

    # ────────────────────────────────────────────────── 生命周期
    @property
    def base_url(self) -> str:
        return f"{self.endpoint.rstrip('/')}:{self._port}"

    def start(self) -> None:
        """启动子进程并等到**真正就绪**（含端口冲突重试）。"""
        if self._proc is not None and self._proc.poll() is None:
            return

        exe = find_server_exe()
        if exe is None:
            raise ModelUnavailableError(
                "未找到内置推理组件（llama-server）。\n"
                "若从源码运行，请先执行：bash packaging/fetch_llama.sh"
            )
        for path, label in ((self.model_path, "模型"), (self.mmproj_path, "视觉投影层")):
            if not path.is_file():
                raise ModelUnavailableError(f"{label}文件不存在：{path}")

        last: Exception | None = None
        for attempt in range(1, PORT_RETRIES + 1):
            port = _free_port()
            while _port_is_taken(port):
                port = _free_port()

            log = self.model_path.parent / f"llama-server.{port}.log"
            proc = subprocess.Popen(
                [
                    str(exe),
                    "--model", str(self.model_path),
                    "--mmproj", str(self.mmproj_path),
                    "--host", "127.0.0.1", "--port", str(port),
                    "--ctx-size", str(self.ctx_size),
                    "--threads", str(self.threads),
                    "--n-gpu-layers", str(self.n_gpu_layers),
                    "--no-webui",
                    "-lv", "4",
                ],
                cwd=str(exe.parent),
                stdout=open(log, "w", encoding="utf-8", errors="replace"),
                stderr=subprocess.STDOUT,
            )
            self._proc, self._port, self._log_path = proc, port, log
            with _LOCK:
                _LIVE.append(self)
            atexit.register(self.close)

            try:
                self._await_ready(proc, port)
            except ModelUnavailableError as exc:
                last = exc
                hint = self._log_tail()
                self.close()
                if proc.poll() is not None:
                    raise ModelUnavailableError(
                        f"内置推理服务启动失败（进程已退出，代码 {proc.returncode}）。\n{hint}"
                    ) from exc
                # 进程活着却没就绪 → 多半是端口被抢，换端口再试
                continue
            else:
                return

        raise ModelUnavailableError(
            f"内置推理服务在 {PORT_RETRIES} 次尝试后仍未就绪（可能是端口持续被占用）。\n"
            f"最后错误：{last}\n{self._log_tail()}"
        )

    def _await_ready(self, proc: subprocess.Popen, port: int) -> None:
        """等到健康检查**且响应体正确**（见模块头的"谎报 listening"说明）。"""
        deadline = time.time() + BOOT_TIMEOUT
        while time.time() < deadline:
            if proc.poll() is not None:
                raise ModelUnavailableError("推理服务进程已退出")
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=3
                ) as resp:
                    body = resp.read().decode("utf-8", "replace")
                if resp.status == 200 and '"ok"' in body.replace(" ", ""):
                    return
            except Exception:  # noqa: BLE001 - 未就绪就是连不上/非 200
                pass
            time.sleep(1.0)
        raise ModelUnavailableError(f"等待推理服务就绪超时（{BOOT_TIMEOUT:.0f}s）")

    def _log_tail(self, lines: int = 6) -> str:
        if self._log_path is None or not self._log_path.is_file():
            return ""
        try:
            tail = self._log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        return "  日志尾部：\n" + "\n".join(f"    {ln}" for ln in tail[-lines:])

    def close(self) -> None:
        """停止子进程（幂等；窗口关闭 / 程序退出时调用）。"""
        proc, self._proc = self._proc, None
        with _LOCK:
            if self in _LIVE:
                _LIVE.remove(self)
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                pass

    # ────────────────────────────────────────────────── 推理
    def describe_image(self, image: bytes, prompt: str) -> str:
        self.start()
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": "data:image/png;base64," +
                                            base64.b64encode(image).decode("ascii")}},
                ],
            }],
            "temperature": 0.0,
            "max_tokens": 512,
        }
        req = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")
            raise ModelUnavailableError(
                f"内置推理服务返回错误 {exc.code}：{detail[:200]}"
            ) from exc
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            raise ModelUnavailableError(
                f"无法连接内置推理服务 {self.base_url}。\n{self._log_tail()}"
            ) from exc

        choices = body.get("choices") or []
        if not choices:
            raise ModelUnavailableError(f"推理服务返回了空结果：{str(body)[:200]}")
        return (choices[0].get("message") or {}).get("content", "") or ""


def shutdown_all() -> None:
    """停止所有存活的内置推理进程（程序退出时兜底调用）。"""
    with _LOCK:
        live = list(_LIVE)
    for adapter in live:
        adapter.close()
