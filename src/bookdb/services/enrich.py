"""版权页元数据提取服务（PRD §12）。

流程：定位版权页 → 取料（图像 / 文字）→ 模型提取 → （预览确认）→ 写库。

**模型输出不得直接覆盖数据库**（§12.5）：本服务只产出候选元数据，
由 CLI / GUI 显示差异、经用户确认后才写入。

两条取料路线：
  1. **图像**（默认；本地视觉模型）——
     文本层关键词先定位；命中不了（扫描件）就逐页渲染喂 VLM。
     按页号从小到大试，遇到含 **ISBN** 的结果即停，并与前面页的结果**合并**
     （封面常有书名/作者、版权页才有 ISBN/年份，合并后最完整）。
  2. **文字**（`backend = "deepseek"` 或显式 `mode="text"`）——
     直接读 PDF 文本层前几页交给**文本模型**；不渲染、不发图，更快更省。
     扫描件没有文本层 → `NoTextLayer`，明确提示改用本地视觉模型（不静默降级）。

实测（用户真实扫描件 + qwen2.5vl:3b）：6~15 秒/本，字段基本准确。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import pdf as pdf_mod
from ..config import Config
from ..llm.base import ModelUnavailableError
from .llm import extract_metadata, extract_metadata_from_text, merge_metadata

#: 自动扫描时最多渲染的前 N 页。
#: （§12.3 原订 15/30 页是**文本层**扫描的规模；渲染每页都要跑一次 VLM，故收到 8。）
MAX_SCAN_PAGES = 8

#: 文字流程送入模型的字符上限（约 8~10 页）。截断而非报错 —— 控制 token 成本。
MAX_TEXT_CHARS = 12000


class CopyrightPageNotFound(RuntimeError):
    """未能自动定位版权页（多为无文本层的扫描件）。"""


class NoTextLayer(RuntimeError):
    """PDF 没有可用的文本层（扫描件 / 图片版），文字流程无从下手。"""


@dataclass
class EnrichResult:
    source: str              # 'pdf' | 'pdf-text' | 'image'
    page: int | None         # PDF 命中页号（1-based）；图片输入为 None
    metadata: dict           # 规范化后的库内字段
    pages_scanned: list[int] | None = None   # 实际渲染过的页号（便于诊断）


def enrich_from_image(config: Config, path: str | Path) -> EnrichResult:
    """从版权页图片提取。"""
    image = Path(path).read_bytes()
    metadata = extract_metadata(config.vlm, image)
    return EnrichResult(source="image", page=None, metadata=metadata)


# ---------------------------------------------------------------- 文字流程
def _text_pages(
    path: str | Path, *, max_pages: int, page: int | None = None
) -> list[tuple[int, str]]:
    """选要送给模型的页：指定页 / 前 N 页 + 文本层定位到的版权页。"""
    if page is not None:
        text = pdf_mod.read_page_text(path, page)
        return [(page, text)] if text else []

    pages = pdf_mod.read_pages_text(path, pages=max_pages)
    located = pdf_mod.locate_copyright_page(path)
    if located and all(no != located for no, _ in pages):
        extra = pdf_mod.read_page_text(path, located)
        if extra:
            pages.append((located, extra))
    return pages


def _text_block(pages: list[tuple[int, str]]) -> str:
    """拼成带页标记的一段文字；超长截断（控制 token 成本）。"""
    block = "\n\n".join(f"【第 {no} 页】\n{text}" for no, text in pages)
    return block[:MAX_TEXT_CHARS]


def enrich_from_pdf_text(
    config: Config,
    path: str | Path,
    page: int | None = None,
    *,
    max_pages: int = MAX_SCAN_PAGES,
) -> EnrichResult:
    """从 PDF **文本层**提取元数据（不发图；供 DeepSeek 等文本模型）。

    扫描件没有文本层时抛 `NoTextLayer` —— 由上层提示改用本地视觉模型，
    **不**在这里偷偷回退（用户显式选了文字流程，行为必须可预期）。
    """
    pages = _text_pages(path, max_pages=max_pages, page=page)
    if not pages:
        raise NoTextLayer(
            f"该 PDF 前 {max_pages} 页没有文本层（多为扫描件 / 图片版）。\n"
            "文字流程无法处理，请改用本地视觉模型：\n"
            "  · 把 book.toml 的 [vlm] backend 改为 auto / builtin；\n"
            "  · 或在「AI 模型」面板切回本地模型。"
        )

    metadata = extract_metadata_from_text(config.vlm, _text_block(pages))

    if not any(v is not None for v in metadata.values()):
        read = ", ".join(str(no) for no, _ in pages)
        raise CopyrightPageNotFound(
            f"已读第 {read} 页，未从文字中提取到版权页信息。\n"
            "可用 --page 指定页码，或改用本地视觉模型（扫描件的正解）。"
        )

    metadata["file_path"] = str(path)
    located = pdf_mod.locate_copyright_page(path)
    return EnrichResult(
        source="pdf-text",
        page=page or located or pages[0][0],
        metadata=metadata,
        pages_scanned=[no for no, _ in pages],
    )


def _page_order(path: str | Path, max_pages: int) -> list[int]:
    """要尝试的页号顺序：先文本层定位结果，再 1..N 补齐。"""
    total = pdf_mod.page_count(path)
    order: list[int] = []

    located = pdf_mod.locate_copyright_page(path)
    if located:
        order.append(located)
    order += [p for p in range(1, max_pages + 1) if p not in order]
    return [p for p in order if 1 <= p <= total]


def _good_enough(acc: dict) -> bool:
    """关键字段凑齐即可收工，不必为找 ISBN 扫满所有页。"""
    return all(acc.get(k) for k in ("title", "author", "publisher", "publication_year"))


def _scan_pages(config: Config, path: str | Path, order: list[int]):
    """按顺序逐页提取；命中 ISBN 或关键字段齐备即停。

    返回 (合并结果, 有产出的页, 命中 ISBN 的页)。
    """
    acc: dict = {}
    hit_pages: list[int] = []
    isbn_page: int | None = None

    for page in order:
        image = pdf_mod.render_page_png(path, page)
        meta = extract_metadata(config.vlm, image)
        if any(v is not None for v in meta.values()):
            hit_pages.append(page)
            # 有 ISBN 的那页最权威 → 它当 base；否则当前累积当 base
            acc = merge_metadata(meta, acc) if meta.get("isbn") else merge_metadata(acc, meta)
        if meta.get("isbn"):
            isbn_page = page
            break
        if _good_enough(acc):
            break

    return acc, hit_pages, isbn_page


def enrich_from_pdf(
    config: Config,
    path: str | Path,
    page: int | None = None,
    *,
    max_pages: int = MAX_SCAN_PAGES,
    mode: str | None = None,
) -> EnrichResult:
    """从 PDF 提取元数据。

    `mode` 决定取料方式：
      · `None`（默认）—— 按配置自动：`backend = "deepseek"` 走 **文字**，其余走**图像**；
      · `"text"`  —— 强制文字流程（本地多模态模型也支持，只是不发图）；
      · `"image"` —— 强制渲染喂视觉模型（扫描件的正解）。

    page 指定时只处理该页；否则自动定位（文本层关键词优先）。
    """
    if mode not in (None, "text", "image"):
        raise ValueError(f"未知的取料方式：{mode!r}（只能是 text / image）")

    if mode == "image" and getattr(config.vlm, "backend", "auto") == "deepseek":
        raise ModelUnavailableError(
            "当前后端是 DeepSeek（只有文本模型，不能读图）。\n"
            "请改用本地视觉模型（[vlm] backend = auto / builtin），"
            "或去掉强制视觉、对有文本层的 PDF 走文字流程。"
        )

    use_text = mode == "text" or (
        mode is None and getattr(config.vlm, "backend", "auto") == "deepseek"
    )
    if use_text:
        return enrich_from_pdf_text(config, path, page, max_pages=max_pages)

    if page is not None:
        image = pdf_mod.render_page_png(path, page)
        metadata = extract_metadata(config.vlm, image)
        metadata["file_path"] = str(path)
        return EnrichResult(source="pdf", page=page, metadata=metadata)

    metadata, hit_pages, isbn_page = _scan_pages(config, path, _page_order(path, max_pages))

    # 非版权页在提示词约束下返回全 null，所以「有任意字段」即视为命中；
    # 若一个字都没读到 → 认为没找到（`looks_like_copyright` 供上层标记"强/弱"结果）。
    if not any(v is not None for v in metadata.values()):
        raise CopyrightPageNotFound(
            f"前 {max_pages} 页未找到版权页（已渲染 {hit_pages or '无'} 页）。"
            f"可用 --page 手动指定页码。"
        )

    metadata["file_path"] = str(path)          # 顺手关联源 PDF（库内相对路径）
    return EnrichResult(
        source="pdf",
        page=isbn_page or (hit_pages[0] if hit_pages else None),
        metadata=metadata,
        pages_scanned=hit_pages,
    )
