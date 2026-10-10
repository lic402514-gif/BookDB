"""版权页元数据提取服务（PRD §12）。

流程：定位版权页 → 渲染图像 → VLM 提取 → （CLI 预览确认）→ 写库。

**模型输出不得直接覆盖数据库**（§12.5）：本服务只产出候选元数据，
由 CLI 显示差异、经用户确认后才调用 `book_service.add_book`。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import pdf as pdf_mod
from ..config import Config
from .llm import extract_metadata


class CopyrightPageNotFound(RuntimeError):
    """未能自动定位版权页（多为无文本层的扫描件）。"""


@dataclass
class EnrichResult:
    source: str              # 'pdf' | 'image'
    page: int | None         # PDF 命中页号（1-based）；图片输入为 None
    metadata: dict           # 规范化后的库内字段


def enrich_from_image(config: Config, path: str | Path) -> EnrichResult:
    """从版权页图片提取。"""
    image = Path(path).read_bytes()
    metadata = extract_metadata(config.vlm, image)
    return EnrichResult(source="image", page=None, metadata=metadata)


def enrich_from_pdf(
    config: Config,
    path: str | Path,
    page: int | None = None,
) -> EnrichResult:
    """从 PDF 提取；page 为空时自动定位版权页（§12.3）。"""
    target = page if page is not None else pdf_mod.locate_copyright_page(path)
    if target is None:
        raise CopyrightPageNotFound(
            f"未能在前 {pdf_mod.SCAN_MAX} 页定位到版权页"
            f"（扫描件可能无文本层）。请用 --page 手动指定页码。"
        )

    image = pdf_mod.render_page_png(path, target)
    metadata = extract_metadata(config.vlm, image)
    metadata["file_path"] = str(path)          # 顺手关联源 PDF（库内相对路径）
    return EnrichResult(source="pdf", page=target, metadata=metadata)
