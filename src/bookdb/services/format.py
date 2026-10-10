"""呈现格式化模块（PRD §14 / §15）—— rename 与 cite **共用**。

统一负责（§14「与引用共用格式化模块」定稿）：
  - 字段缺省策略（为空时省略）
  - 作者、译者的多值拼接
  - 版次的「第N版」格式化（库内存纯数字，见 §5.1）
  - 文件名非法字符清理、长度截断

两套模板：
  - `build_filename()`  —— PDF 文件命名（§14.1）
  - `format_citation()` —— GB/T 7714-2015 专著（§15）

RenameService / CitationService 只调用本模块，缺省规则只存在这里一处。
"""

from __future__ import annotations

import re

DEFAULT_LITERATURE_TYPE = "M"

#: Windows 文件名非法字符
_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')
#: 文件名主干长度上限（为目录 + 扩展名留余量）
_MAX_STEM = 200
#: 多值字段的分隔符（作者/译者等以「；」存储）
_MULTI_SEP = re.compile(r"[；;、]+")


# ────────────────────────────────────────────────────────────── 基础呈现
def format_edition(value: object | None) -> str | None:
    """纯数字 → 「第N版」；无法识别则原样返回；空 → None。"""
    if not value:
        return None
    text = str(value).strip()
    return f"第{text}版" if text.isdigit() else text


def split_multi(text: object | None) -> list[str]:
    """把多值字符串拆成列表（按「；」「;」「、」）。"""
    if not text:
        return []
    return [p.strip() for p in _MULTI_SEP.split(str(text)) if p.strip()]


def join_multi(text: object | None, sep: str = ", ") -> str | None:
    """多值拼接；空 → None。"""
    parts = split_multi(text)
    return sep.join(parts) if parts else None


def sanitize_filename(name: str) -> str:
    """清理 Windows 非法字符 + 去除首尾点/空格 + 截断超长。"""
    cleaned = _ILLEGAL_FILENAME_CHARS.sub("_", name).strip().strip(".")
    if len(cleaned) > _MAX_STEM:
        cleaned = cleaned[:_MAX_STEM].rstrip()
    return cleaned


def _pub_block(place: object | None, publisher: object | None, *, sep: str = "：") -> str | None:
    """出版地 + 出版社；缺一即简化（filename 用全角「：」，cite 用「: 」）。"""
    place = (str(place).strip() or None) if place else None
    publisher = (str(publisher).strip() or None) if publisher else None
    if place and publisher:
        return f"{place}{sep}{publisher}"
    return publisher or place


def _literature_type(book: dict) -> str:
    return (str(book.get("literature_type") or "").strip() or DEFAULT_LITERATURE_TYPE).upper()


def _publisher_name(book: dict) -> str | None:
    """详情行用 publisher_name；导入/构造 dict 时可能只有 publisher。"""
    return book.get("publisher_name") or book.get("publisher")


# ────────────────────────────────────────────────────────────── 文件名模板
def build_filename(book: dict, suffix: str = ".pdf") -> str:
    """按 §14.1 生成规范化文件名（含扩展名）。

    模板：{作者}_{书名}[{文献类型}]{_译者}_{出版地}：{出版社}_{出版年份}({版次})
    空字段整段省略；文献类型为空默认 `M`。
    """
    parts: list[str] = []

    if book.get("author"):
        parts.append(str(book["author"]).strip())

    title = (book.get("title") or "").strip()
    if title:
        parts.append(f"{title}[{_literature_type(book)}]")

    if book.get("translator"):
        parts.append(str(book["translator"]).strip())

    pub = _pub_block(book.get("publication_place"), _publisher_name(book), sep="：")
    if pub:
        parts.append(pub)

    year = book.get("publication_year")
    edition = book.get("edition")
    if year and edition:
        parts.append(f"{year}({edition})")
    elif year:
        parts.append(str(year))
    elif edition:
        parts.append(f"({edition})")

    stem = sanitize_filename("_".join(p for p in parts if p))
    return f"{stem}{suffix}"


# ────────────────────────────────────────────────────────── GB/T 7714 模板
def format_citation(book: dict) -> str:
    """GB/T 7714-2015 专著（§15）。

    模板：主要责任者. 题名[文献类型]. 其他责任者. 版本项. 出版地: 出版者, 出版年.
    示例：阿多尔诺. 否定辩证法[M]. 王凤才, 译. 北京: 商务印书馆, 2019.
    """
    segments: list[str] = []

    author = join_multi(book.get("author"))
    if author:
        segments.append(author)

    title = (book.get("title") or "").strip()
    if title:
        segments.append(f"{title}[{_literature_type(book)}]")

    translator = join_multi(book.get("translator"))
    if translator:
        segments.append(f"{translator}, 译")

    edition = format_edition(book.get("edition"))
    if edition:
        segments.append(edition)

    pub = _pub_block(book.get("publication_place"), _publisher_name(book), sep=": ")
    year = book.get("publication_year")
    tail = f"{pub}, {year}" if pub and year else (pub or (str(year) if year else None))
    if tail:
        segments.append(tail)

    return ". ".join(segments) + "." if segments else ""
