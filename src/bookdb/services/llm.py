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
#:
#: 提示词要点（实测得出，勿删）：
#:   1. 先**判定是不是版权页** —— 非版权页一律输出全 null。否则模型会从序言/正文里
#:      **捏造**出版社和年份（旧提示词实测会在「主编序」页编出 出版社/年份）。
#:   2. `publication_place` 只要城市 —— 否则模型会把出版社**详细地址**当真（实测）。
#:   3. 民国纪年必须换算公元（民國99年 → 2010）—— 否则模型会输出 1999（实测）。
PROMPT = """你要判断这张图书页面**是不是版权页**，并从版权页中提取书目信息。

判断标准：版权页含「图书在版编目(CIP)数据」「ISBN」「出版者/出版社」「版次/印次」等出版信息。
如果这一页不是版权页（例如书名页、序言、目录、正文、后记），**所有字段一律输出 null**，
绝对不要从正文里猜测出版社或年份。

字段说明：
- title: 书名；author: 作者数组；translator: 译者数组
- publisher: 出版社名称；publication_place: 出版地**只填城市**（如 北京、臺北），不要填详细地址
- publication_year: 出版年份，**公元纪年整数**。若原书用民国纪年（如「民國99年」「中華民國九十九年」），
  必须换算为公元年（民国年 + 1911），例如 民國99年 → 2010
- edition: 版次（字符串，初版填 "1"）；isbn: ISBN 字符串

无法确定的字段输出 null。**绝对不要猜测。**

只输出一个 JSON 对象，不要解释、不要 markdown 围栏。

示例（非版权页）：{"title": null, "author": null, "translator": null, "publisher": null, "publication_place": null, "publication_year": null, "edition": null, "isbn": null}"""

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


# ------------------------------------------------------------ 结果判定 / 合并
def looks_like_copyright(meta: dict) -> bool:
    """判断提取结果是否来自**真正的版权页**（而非封面/正文的偶然命中）。

    判据：有 ISBN，或同时有 出版社 + 书名。
    """
    return bool(meta.get("isbn") or (meta.get("publisher") and meta.get("title")))


def merge_metadata(base: dict, fill: dict) -> dict:
    """合并两次提取：`base` 非空的字段优先，缺失的用 `fill` 补。

    用途：封面通常有书名/作者但无 ISBN，版权页有 ISBN/年份 —— 两者合并得到完整记录。
    """
    return {
        key: (base.get(key) if base.get(key) is not None else fill.get(key))
        for key in _FIELDS
    }
