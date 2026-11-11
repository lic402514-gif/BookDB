"""DeepSeek API 适配器（PRD §13 的可选云端后端）。

DeepSeek 的 API 兼容 OpenAI 的 `POST /chat/completions`，当前有两个模型：

  · `deepseek-flash` —— **支持图片输入**（官方 `/guides/vision`，格式 JPEG/PNG/GIF/WebP，
    单图 ≤32 MiB，base64 内联计入 48 MiB 请求体限制）；
  · `deepseek-v4-pro` —— 纯文本，不支持 Vision。

因此本适配器两条路都实现：

  · `chat_text()`     —— 纯文本 → 文本，供「PDF 文本层 → 元数据」流程；
  · `describe_image()` —— 图片（base64 data URL）→ 文本，供扫描件/版权页图像流程。

旧模型名（`deepseek-chat` / `deepseek-reasoner`）已下线，会自动映射到
`deepseek-flash`（见 `LEGACY_MODELS`）；若用户把模型配成 `deepseek-v4-pro`，
`describe_image()` 会**明确报「不支持读图」**，而不是发出去等 400。

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

import base64
import json
import os
import urllib.error
import urllib.request

from ..base import ModelUnavailableError, VLMAdapter

#: 环境变量名（优先级高于 GUI 保存的 Key；本身不落盘）
ENV_API_KEY = "DEEPSEEK_API_KEY"

#: DeepSeek 官方端点（也可写成带 `/v1` 的形式，拼接时都兼容）
DEFAULT_ENDPOINT = "https://api.deepseek.com"
#: 默认模型：`deepseek-flash` 文本 + 视觉都支持，价格最低
DEFAULT_MODEL = "deepseek-flash"

#: 支持图片输入的模型（官方文档：Vision 一栏只有 Flash 打勾，Pro 不支持）
VISION_MODELS = frozenset({
    "deepseek-flash",
    "deepseek-v4-flash",
    "deepseek-v4-flash-vision-exp",
})

#: 已下线的旧模型名 → 现役模型（官方文档：旧名仍可调用，请求由 Flash 承接）
LEGACY_MODELS = {
    "deepseek-chat": DEFAULT_MODEL,
    "deepseek-reasoner": DEFAULT_MODEL,
    "deepseek-v4-flash": DEFAULT_MODEL,
    "deepseek-v4-flash-vision-exp": DEFAULT_MODEL,
}


def _normalize_model(model: str) -> str:
    """旧模型名映射到现役模型；未知名字原样保留（可能指向兼容代理）。"""
    return LEGACY_MODELS.get((model or "").strip().lower(), (model or "").strip())


def _image_mime(image: bytes) -> str:
    """按**文件头**判断图片类型（官方也按内容判断，而不是扩展名）。"""
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if image[:4] == b"RIFF" and image[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


class DeepSeekAdapter(VLMAdapter):
    """DeepSeek 云端推理（`deepseek-flash`：文本 + 图片）。"""

    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        timeout: float = 120.0,
        max_tokens: int = 1024,
    ):
        requested = (model or DEFAULT_MODEL).strip()
        super().__init__(endpoint=endpoint, model=_normalize_model(requested))
        self.requested_model = requested
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

    # ---------------------------------------------------------------- 能力
    @property
    def supports_vision(self) -> bool:
        """当前模型是否支持图片输入（只有 Flash 系支持）。"""
        return self.model.lower() in VISION_MODELS

    # ---------------------------------------------------------------- 接口
    def chat_text(self, prompt: str, *, max_tokens: int | None = None) -> str:
        """**纯文本**对话（不发图）。"""
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "temperature": 0,
            "max_tokens": max_tokens or self.max_tokens,
        }
        return self._content(self._post(self._url(), payload))

    def describe_image(self, image: bytes, prompt: str) -> str:
        """图片 + 提示 → 文本（OpenAI 兼容的 `content` 块数组，图片走 data URL）。"""
        if not self.supports_vision:
            raise ModelUnavailableError(
                f"模型 {self.model} 不支持图片输入。\n"
                "DeepSeek 当前只有 deepseek-flash 支持 Vision（deepseek-v4-pro 是纯文本）。\n"
                "请在 book.toml 里把 [vlm] deepseek_model 改为 deepseek-flash，"
                "或改用本地视觉模型。"
            )
        if not image:
            raise ValueError("图片内容为空")

        data_url = f"data:{_image_mime(image)};base64,{base64.b64encode(image).decode('ascii')}"
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "stream": False,
            "temperature": 0,
            "max_tokens": self.max_tokens,
        }
        return self._content(self._post(self._url(), payload))

    # ---------------------------------------------------------------- 内部
    def _url(self) -> str:
        return f"{self.endpoint.rstrip('/')}/chat/completions"

    @staticmethod
    def _content(body: dict) -> str:
        try:
            return body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelUnavailableError(
                f"DeepSeek 响应格式异常：{json.dumps(body, ensure_ascii=False)[:200]}"
            ) from exc

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
                f"请检查 {ENV_API_KEY}（或 GUI 保存的 Key）是否有效、是否已过期。"
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
