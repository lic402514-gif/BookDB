"""版权页元数据提取服务（PRD §12）。

流程：定位版权页 → 渲染图像 → VLM 提取 → （预览确认）→ 写库。

**模型输出不得直接覆盖数据库**（§12.5）：本服务只产出候选元数据，
由 CLI / GUI 显示差异、经用户确认后才写入。

自动定位有两条路：
  1. **文本层关键词**（`pdf.locate_copyright_page`）—— 快，适用于文字版 PDF；
  2. **逐页渲染喂 VLM** —— 扫描件的正解（无文本层时路 1 必然失败）。
     按页号从小到大试，遇到含 **ISBN** 的结果即停，并与前面页的结果**合并**
     （封面常有书名/作者、版权页才有 ISBN/年份，合并后最完整）。

实测（用户真实扫描件 + qwen2.5vl:3b）：6~15 秒/本，字段基本准确。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import pdf as pdf_mod
from ..config import Config
from .llm import extract_metadata, merge_metadata

#: 自动扫描时最多渲染的前 N 页。
#: （§12.3 原订 15/30 页是**文本层**扫描的规模；渲染每页都要跑一次 VLM，故收到 8。）
MAX_SCAN_PAGES = 8


class CopyrightPageNotFound(RuntimeError):
    """未能自动定位版权页（多为无文本层的扫描件）。"""


@dataclass
class EnrichResult:
    source: str              # 'pdf' | 'image'
    page: int | None         # PDF 命中页号（1-based）；图片输入为 None
    metadata: dict           # 规范化后的库内字段
    pages_scanned: list[int] | None = None   # 实际渲染过的页号（便于诊断）


def enrich_from_image(config: Config, path: str | Path) -> EnrichResult:
    """从版权页图片提取。"""
    image = Path(path).read_bytes()
    metadata = extract_metadata(config.vlm, image)
    return EnrichResult(source="image", page=None, metadata=metadata)


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
) -> EnrichResult:
    """从 PDF 提取元数据。

    page 指定时只处理该页；否则自动定位（文本层优先，失败则逐页渲染扫描件）。
    """
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
