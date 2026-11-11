"""DeepSeek API 适配器（PRD §13 的可选云端后端）。

DeepSeek 的 API 兼容 OpenAI 的 `POST /chat/completions`，但**只有文本模型**
（`deepseek-chat` / `deepseek-reasoner`），没有视觉模型 —— 因此：

  · 本适配器实现 `chat_text()`（纯文本 → 文本），供「PDF 文本层 → 元数据」流程使用；
  · `describe_image()` 明确报错，并指引改用本地 VLM（不静默降级、不假装能读图）。

API Key 的两种来源（优先级：环境变量 > GUI 保存）：
  · 环境变量 `DEEPSEEK_API_KEY`；
  · GUI「AI 模型」面板填写 —— 以 **Windows DPAPI 密文**存进
    `%APPDATA%\\BookDB\\settings.json`（明文永不落盘，见 `gui/secret.py`）。
  Key **绝不**写进 `book.toml`：书库目录常被整体复制/分享。

设计取舍：
  · 不依赖 `openai` SDK —— 与项目其余适配器一致，只用标准库 `urllib`；
  · 错误按 HTTP 状态码分类（401 Key 失效 / 402 余额不足 / 429 限流 / 5xx 服务端），
    全部转成 `ModelUnavailableError`，上层（CLI / GUI）照常给用户可操作的提示；
  · 默认超时 120s：云端首 token 可能较慢，但也不能无限等。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from ..base import ModelUnavailableError, VLMAdapter

#: 环境变量名（优先级高于 GUI 保存的 Key；本身不落盘）
ENV_API_KEY = "DEEPSEEK_API_KEY"

#: DeepSeek 官方端点（也可写成带 `/v1` 的形式，拼接时都兼容）
DEFAULT_ENDPOINT = "https://api.deepseek.com"
#: 默认模型：`deepseek-chat` 快且便宜，抽取结构化字段足够
DEFAULT_MODEL = "deepseek-chat"


class DeepSeekAdapter(VLMAdapter):
    """DeepSeek 云端**文本**推理（不支持读图）。"""

    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        timeout: float = 120.0,
        max_tokens: int = 1024,
    ):
        super().__init__(endpoint=endpoint, model=model)
        key = api_key if api_key is not None else os.environ.get(ENV_API_KEY, "")
        key = key.strip()
        if not key:
            raise ModelUnavailableError(
                "未配置 DeepSeek API Key。两种设置方式：\n"
                "  ① GUI：打开「AI 模型」→ DeepSeek 卡片 →「设置 Key…」\n"
                "     （Windows DPAPI 加密后存在本机设置里，明文不落盘）\n"
                f"  ② 环境变量 {ENV_API_KEY}（优先级更高；需与 BookDB 在同一终端启动）：\n"
                '     PowerShell： $env:DEEPSEEK_API_KEY = "sk-..."\n'
                "     cmd：        set DEEPSEEK_API_KEY=sk-..."
            )
        self._api_key = key
        self.timeout = timeout
        self.max_tokens = max_tokens

    # ---------------------------------------------------------------- 接口
    def chat_text(self, prompt: str, *, max_tokens: int | None = None) -> str:
        """**纯文本**对话（不发图）—— 本适配器唯一真正支持的调用。"""
        url = f"{self.endpoint.rstrip('/')}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "temperature": 0,
            "max_tokens": max_tokens or self.max_tokens,
        }
        body = self._post(url, payload)
        try:
            return body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelUnavailableError(
                f"DeepSeek 响应格式异常：{json.dumps(body, ensure_ascii=False)[:200]}"
            ) from exc

    def describe_image(self, image: bytes, prompt: str) -> str:
        """DeepSeek 没有视觉模型 —— 明确拒绝，而不是把图当文字发出去。"""
        raise ModelUnavailableError(
            "DeepSeek API 只有文本模型（deepseek-chat / deepseek-reasoner），不能读图。\n"
            "版权页图像提取请用本地视觉模型：\n"
            "  · 在「AI 模型」面板切回本地，或把 book.toml 的 [vlm] backend 改为 auto；\n"
            "  · 有文本层的 PDF 可以用 `book enrich --pdf ... --text` 走 DeepSeek 文字流程。"
        )

    # ---------------------------------------------------------------- 内部
    def _post(self, url: str, payload: dict) -> dict:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")
            raise ModelUnavailableError(self._explain_http(exc.code, detail)) from exc
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            raise ModelUnavailableError(
                f"无法连接 DeepSeek API（{self.endpoint}）：{exc}\n"
                "请检查网络、代理或防火墙设置。"
            ) from exc

    def _explain_http(self, code: int, detail: str) -> str:
        message = _error_message(detail)
        if code in (401, 403):
            return (
                f"DeepSeek 认证失败（HTTP {code}）：{message}\n"
                f"请检查环境变量 {ENV_API_KEY} 是否有效、是否已过期。"
            )
        if code == 402:
            return f"DeepSeek 账户余额不足（HTTP 402）：{message}\n请到 platform.deepseek.com 充值。"
        if code == 429:
            return f"DeepSeek 请求过于频繁或额度受限（HTTP 429）：{message}\n请稍后再试。"
        if code >= 500:
            return f"DeepSeek 服务端错误（HTTP {code}）：{message}\n请稍后重试。"
        return f"DeepSeek 返回错误（HTTP {code}）：{message}"


def _error_message(detail: str) -> str:
    """从错误响应里取可读信息；拿不到就截断原文。"""
    try:
        data = json.loads(detail)
    except (ValueError, TypeError):
        return (detail or "").strip()[:200] or "（无错误详情）"
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:200]
        if isinstance(error, str) and error.strip():
            return error.strip()[:200]
        if data.get("message"):
            return str(data["message"])[:200]
    return (detail or "").strip()[:200] or "（无错误详情）"
