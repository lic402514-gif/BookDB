"""Ollama 适配器（PRD §13 默认目标）。

对接本机 Ollama 的 `POST /api/chat`，使用视觉模型（如 qwen2.5-vl）。
仅用标准库 urllib，不引入额外依赖。
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

from ..base import ModelUnavailableError, VLMAdapter


class OllamaAdapter(VLMAdapter):
    """本机 Ollama 视觉推理。"""

    def __init__(self, *, endpoint: str, model: str, timeout: float = 180.0):
        super().__init__(endpoint=endpoint, model=model)
        self.timeout = timeout

    def describe_image(self, image: bytes, prompt: str) -> str:
        url = f"{self.endpoint.rstrip('/')}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [base64.b64encode(image).decode("ascii")],
                }
            ],
            "stream": False,
            "options": {"temperature": 0},
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")
            if exc.code == 404 or "not found" in detail.lower():
                raise ModelUnavailableError(
                    f"模型 {self.model!r} 未拉取。请运行：ollama pull {self.model}"
                ) from exc
            raise ModelUnavailableError(
                f"Ollama 返回错误 {exc.code}：{detail[:200]}"
            ) from exc
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            raise ModelUnavailableError(
                f"无法连接本地推理服务 {self.endpoint}。\n"
                f"请确认 Ollama 已启动（ollama serve），且模型已拉取（ollama pull {self.model}）。"
            ) from exc

        return (body.get("message") or {}).get("content", "") or ""
