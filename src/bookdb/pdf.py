"""PDF 处理（PRD §12.2 / §12.3）—— 版权页定位与渲染。

依赖 PyMuPDF（可选依赖 `.[pdf]`），未安装时给出明确提示。
不使用 MinerU，不做 OCR 文本重建 —— 视觉转文本由 VLM 自身完成（§13）。
"""

from __future__ import annotations

from pathlib import Path

#: 版权页候选关键词（§12.3）
COPYRIGHT_KEYWORDS = ["ISBN", "出版社", "出版", "发行", "版次", "印刷", "书号"]

#: 首次扫描页数（§12.3）
SCAN_FIRST = 15
#: 扫描硬上限（§12.3：不扩大，避免整本扫描）
SCAN_MAX = 30


def _fitz():
    """返回 PyMuPDF 模块（优先新名 `pymupdf`，回退旧名 `fitz`）。"""
    try:
        import pymupdf  # PyMuPDF >= 1.24 的推荐导入名
        return pymupdf
    except ImportError:
        pass
    try:
        import fitz
        return fitz
    except ImportError as exc:  # pragma: no cover - 取决于运行环境
        raise RuntimeError(
            "PDF 处理需要 PyMuPDF。请安装：uv pip install -e \".[pdf]\""
        ) from exc


def page_count(path: str | Path) -> int:
    fitz = _fitz()
    with fitz.open(str(path)) as doc:
        return len(doc)


def locate_copyright_page(
    path: str | Path,
    *,
    keywords: list[str] | None = None,
    max_pages: int = SCAN_MAX,
) -> int | None:
    """按关键词在前 max_pages 页内打分，返回得分最高的页号（1-based）。

    全部命中为 0（含无文本层的扫描件）时返回 None，由调用方提示改用 --page。
    """
    fitz = _fitz()
    keywords = keywords or COPYRIGHT_KEYWORDS

    with fitz.open(str(path)) as doc:
        best_page: int | None = None
        best_score = 0
        for i in range(min(len(doc), max_pages)):
            text = doc[i].get_text() or ""
            score = sum(text.count(kw) for kw in keywords)
            if score > best_score:
                best_score, best_page = score, i + 1
        return best_page if best_score > 0 else None


def render_page_png(path: str | Path, page: int, *, dpi: int = 150) -> bytes:
    """把第 page 页（1-based）渲染为 PNG 字节。"""
    fitz = _fitz()
    with fitz.open(str(path)) as doc:
        if not 1 <= page <= len(doc):
            raise ValueError(f"页码越界：{page}（共 {len(doc)} 页）")
        pix = doc[page - 1].get_pixmap(dpi=dpi)
        return pix.tobytes("png")
