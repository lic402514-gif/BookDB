"""LLM 服务（PRD §12 / §13）—— 版权页图像 → 结构化元数据。

职责边界（§12.1）：**只**做「版权页图像 → 结构化元数据」。
不做 CLC 分类、网络搜索、全书摘要、RAG、在线书目匹配、自动写库。

适配层可替换（§13）：程序不绑定具体模型，默认对接 Ollama。
"""

from __future__ import annotations

import json
import re

from ..config import VlmConfig
from ..llm.adapters.ollama import OllamaAdapter
from ..llm.base import ModelUnavailableError, VLMAdapter
from ..normalize import normalize_edition, normalize_isbn, normalize_year

#: 字段清单即为要提取的全部内容；不得要求模型猜测（§12.4）
PROMPT = """你是图书版权页信息提取助手。请从这张版权页图片中提取书目信息。

只输出一个 JSON 对象，不要任何解释文字、不要 markdown 代码块。

字段说明：
- title: 书名（字符串）
- author: 作者（字符串数组）
- translator: 译者（字符串数组）
- publisher: 出版社（字符串）
- publication_place: 出版地（字符串）
- publication_year: 出版年份（整数）
- edition: 版次（字符串，如 "1"）
- isbn: ISBN（字符串）

无法确定的字段一律输出 null，**绝对不要猜测**。

示例输出：
{"title": "否定辩证法", "author": ["阿多尔诺"], "translator": ["王凤才"], "publisher": "商务印书馆", "publication_place": "北京", "publication_year": 2019, "edition": "1", "isbn": "9787100163180"}
"""

_FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

#: 模型输出中允许出现的字段（其余忽略）
_FIELDS = (
    "title", "author", "translator", "publisher",
    "publication_place", "publication_year", "edition", "isbn",
)


def get_adapter(vlm: VlmConfig) -> VLMAdapter:
    """按配置返回适配器（默认 Ollama）。"""
    return OllamaAdapter(endpoint=vlm.endpoint, model=vlm.model)


# ------------------------------------------------------------------ parsing
def _extract_json(text: str) -> str:
    match = _FENCED.search(text)
    if match:
        return match.group(1)
    match = _OBJECT.search(text)
    if match:
        return match.group(0)
    raise ValueError("模型输出中未找到 JSON 对象")


def _as_text(value: object) -> str | None:
    """标量转非空字符串；空 → None。"""
    if value is None:
        return None
    if isinstance(value, list):
        parts = [str(x).strip() for x in value if str(x).strip()]
        return "；".join(parts) or None
    text = str(value).strip()
    return text or None


def _as_year(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return normalize_year(value)
    except ValueError:
        return None


def _as_edition(value: object) -> str | None:
    if value in (None, ""):
        return None
    return normalize_edition(value)


def parse_metadata(raw_text: str) -> dict:
    """解析模型文本输出为库内字段字典（§12.4）。

    容错：接受裸 JSON、```json 围栏、以及夹带说明文字的输出。
    作者/译者数组以「；」拼接为字符串；无法确定的字段为 None。
    """
    try:
        data = json.loads(_extract_json(raw_text))
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型输出不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("模型输出不是 JSON 对象")

    return {
        "title": _as_text(data.get("title")),
        "author": _as_text(data.get("author")),
        "translator": _as_text(data.get("translator")),
        "publisher": _as_text(data.get("publisher")),
        "publication_place": _as_text(data.get("publication_place")),
        "publication_year": _as_year(data.get("publication_year")),
        "edition": _as_edition(data.get("edition")),
        "isbn": normalize_isbn(data.get("isbn")) if data.get("isbn") not in (None, "") else None,
    }


def extract_metadata(vlm: VlmConfig, image: bytes) -> dict:
    """图像 → 结构化元数据（规范化后的库内字段）。

    模型不可用时抛 `ModelUnavailableError`（§13）。
    """
    adapter = get_adapter(vlm)
    raw = adapter.describe_image(image, PROMPT)
    return parse_metadata(raw)
