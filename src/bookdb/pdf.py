"""PDF 处理（PRD §12.2 / §12.3）—— 版权页定位、文本层读取与渲染。

依赖 PyMuPDF（可选依赖 `.[pdf]`），未安装时给出明确提示。
不使用 MinerU，不做 OCR 文本重建 —— 视觉转文本由 VLM 自身完成（§13）。

两条输入路线都从这里取料：
  · 扫描件 / 视觉后端：`render_page_png()` 渲染成图再喂 VLM；
  · 有文本层的 PDF / 文本后端（如 DeepSeek）：`read_pages_text()` 直接取文字。
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


def read_page_text(path: str | Path, page: int) -> str:
    """读第 page 页（1-based）的文本层；无文本层（扫描件）返回空字符串。"""
    fitz = _fitz()
    with fitz.open(str(path)) as doc:
        if not 1 <= page <= len(doc):
            raise ValueError(f"页码越界：{page}（共 {len(doc)} 页）")
        return (doc[page - 1].get_text() or "").strip()


def read_pages_text(path: str | Path, *, pages: int = SCAN_FIRST) -> list[tuple[int, str]]:
    """读前 `pages` 页的文本层，返回 [(页号, 文字), …]。

    **只返回有文字的页** —— 扫描件的页会得到空串，从而被自动跳过；
    调用方据此判断「有没有文本层」。不在这里做截断，截断由业务层负责。
    """
    fitz = _fitz()
    with fitz.open(str(path)) as doc:
        out: list[tuple[int, str]] = []
        for i in range(min(len(doc), pages)):
            text = (doc[i].get_text() or "").strip()
            if text:
                out.append((i + 1, text))
        return out


def render_page_png(path: str | Path, page: int, *, dpi: int = 150) -> bytes:
    """把第 page 页（1-based）渲染为 PNG 字节。"""
    fitz = _fitz()
    with fitz.open(str(path)) as doc:
        if not 1 <= page <= len(doc):
            raise ValueError(f"页码越界：{page}（共 {len(doc)} 页）")
        pix = doc[page - 1].get_pixmap(dpi=dpi)
        return pix.tobytes("png")
