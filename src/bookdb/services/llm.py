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
#:   1. 非版权页一律输出全 null —— 否则模型会从封面/序言/正文里**捏造**出版社和年份
#:      （旧提示词实测会在「主编序」页编出 出版社/年份）。
#:   2. `publication_place` 只要城市 —— 否则模型会把出版社**详细地址**当真（实测）。
#:   3. 民国纪年必须换算公元（民國99年 → 2010）—— 否则模型会输出 1999（实测）。
#:   4. 「第N册/卷/集」是**册次不是版次**，不得填入 edition（实测：书名里的「第二册」
#:      被填成 edition=2，污染引用与改名）。
#:   5. ISBN 只到校验位，不带「/A·10」这类分类后缀；读不全填 null（实测）。
PROMPT = """你是图书版权页信息抽取器。从图片中提取书目元数据，只输出一个 JSON 对象。

规则：
1. 只能填写图片中明确出现的文字，不得根据常识、书名、作者或已有知识猜测或补全。
2. 图片中没有明确出现的信息，一律填 null。
3. 如果不是版权页（封面、书名页、扉页、序言、目录、正文、后记、广告、空白页等），
   所有字段均为 null。版权页的特征：「图书在版编目(CIP)数据」「ISBN」「出版社/出版者」
   「版次」「印次」「定价」「责任编辑」等。

字段：
- title：书名。
- author：作者，数组。
- translator：译者，数组，仅当出现「译 / 译者」时填。
- publisher：出版社名称，不要把地址、电话、网址当出版社。
- publication_place：出版地，只填城市（如「北京」「臺北」），不要街道门牌。
- publication_year：出版年份，公元纪年整数，只认出版信息附近标注的年份；
  民国纪年需换算（民国年 + 1911，如 民國99年 → 2010）。
- edition：版次。只有「第N版 / 第一版 / 初版 / 修订版」才算版次；
  「第N册 / 卷 / 集」是册次不是版次，不要填；「第N次印刷」是印次，也不要填。
- isbn：ISBN，只到校验位（10 位或 13 位），不要带「/分类号」后缀；读不全的 ISBN 填 null。

只输出 JSON，不要解释，不要 markdown 围栏。
示例：{"title": null, "author": null, "translator": null, "publisher": null, "publication_place": null, "publication_year": null, "edition": null, "isbn": null}"""

_FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

#: 模型输出中允许出现的字段（其余忽略）
_FIELDS = (
    "title", "author", "translator", "publisher",
    "publication_place", "publication_year", "edition", "isbn",
)


def get_adapter(vlm: VlmConfig) -> VLMAdapter:
    """按配置选择适配器（PRD §13 / §17）。

    优先级（`vlm.backend`）：
      · `auto`（默认）—— 内置 llama.cpp 就绪就用它；否则若 Ollama 在跑就用 Ollama；
        两者都不可用 → 明确报错并给出**可操作的**指引（不静默降级）。
      · `builtin` —— 强制内置；未就绪直接报错。
      · `ollama`  —— 强制 Ollama。
    """
    backend = getattr(vlm, "backend", "auto")

    if backend in ("auto", "builtin"):
        adapter = _builtin_adapter(vlm)
        if adapter is not None:
            return adapter
        if backend == "builtin":
            raise ModelUnavailableError(_builtin_hint())

    if backend in ("auto", "ollama"):
        if backend == "ollama" or _ollama_reachable(vlm.endpoint):
            return OllamaAdapter(endpoint=vlm.endpoint, model=vlm.model)

    raise ModelUnavailableError(_no_backend_hint())


def _builtin_adapter(vlm: VlmConfig) -> VLMAdapter | None:
    """内置推理就绪则返回适配器，否则 None（调用方决定回退或报错）。"""
    from ..llm.adapters import llamacpp
    from . import model as model_service

    if not llamacpp.is_available():
        return None
    try:
        spec = model_service.find(getattr(vlm, "builtin_model", "") or model_service.DEFAULT_KEY)
    except Exception:  # noqa: BLE001 - 配置里的 key 无效时退回默认
        spec = model_service.find(model_service.DEFAULT_KEY)

    st = model_service.state(spec)
    if not st.ready:
        return None
    return llamacpp.LlamaServerAdapter(
        model_path=st.paths[0], mmproj_path=st.paths[1],
    )


def _ollama_reachable(endpoint: str) -> bool:
    """Ollama 是否在跑（短超时探测，避免拖慢启动）。"""
    import urllib.request

    try:
        with urllib.request.urlopen(f"{endpoint.rstrip('/')}/api/tags", timeout=1.5) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _builtin_hint() -> str:
    return (
        "未找到可用的内置视觉模型。\n"
        "请在 BookDB 界面里打开「AI 模型」页面下载（约 1.3~5.5 GB），\n"
        "或运行命令行：book model pull"
    )


def _no_backend_hint() -> str:
    return (
        "没有可用的本地视觉模型。\n\n"
        "两个选择：\n"
        "  ① 用内置模型（推荐）—— 在 BookDB 的「AI 模型」页面下载，或运行：\n"
        "         book model pull\n"
        "  ② 用你已有的 Ollama —— 确认它已启动：\n"
        "         ollama serve\n"
        f"     并已拉取视觉模型（当前配置：{VlmConfig().model}）"
    )


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
    text = str(value).strip()
    # 「册 / 卷 / 集」是册次/卷次/集次，不是版次 —— 模型常把书名里的「第X册」
    # 误填进 edition（实测《…文稿 第二册》→ edition=2，污染引用与改名）。
    # 提示词已约束但 3B 模型仍会违反，故在此硬性兜底。
    if re.search(r"[册卷集]", text):
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
