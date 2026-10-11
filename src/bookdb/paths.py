"""文件路径解析（见 PRD §6 / §6.1）。

规则：
  - 绝对路径：原样使用；
  - 相对路径：以 config.library_root 为基准拼接解析；
  - library_root 缺失而路径又是相对路径 → 明确报错，不静默猜测；
  - 显示时优先给出相对于 library_root 的相对路径，便于整库迁移。

路径**语法检查**（新增）：疑似盘符但不符合平台绝对路径规则（如全角冒号 `E：`）
→ **明确报错**，绝不静默拼接 —— 否则会产生 `E:\\Books\\E:\\Books\\x.pdf` 这类灾难性路径。
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import re
from pathlib import Path


class LibraryRootMissingError(RuntimeError):
    """存在相对路径记录但未配置 library_root。"""


class PathSyntaxError(ValueError):
    """路径语法非法（典型：用中文全角冒号写盘符）。"""


#: 盘符前缀：字母（半角 / 全角）+ 冒号（半角 / 全角）
_DRIVE_PREFIX = re.compile(r"^([A-Za-zＡ-Ｚａ-ｚ])([:：])")


def _ascii_letter(ch: str) -> str:
    """全角字母 → 半角（只用于给出修正示例）。"""
    return chr(ord(ch) - 0xFEE0) if ord(ch) > 0x7F else ch


#: 不可见字符（零宽空格/连字/方向符、BOM）—— 复制粘贴常见，路径里无意义
_INVISIBLE = re.compile(r"[\u200b\u200c\u200d\u200e\u200f\ufeff]")

#: 成对引号（左 → 右）；中英文都要覆盖，否则会被误判为相对路径
_QUOTE_PAIRS = {'"': '"', "'": "'", "“": "”", "‘": "’", "「": "」", "『": "』"}

#: 全角路径分隔符
_FULLWIDTH_SEPS = ("＼", "／")


def _clean(raw: object) -> str:
    """路径输入的无损规范化：去不可见字符 + 首尾空白 + 成对引号。

    资源管理器「复制路径」产出 `"E:\\Books\\x.pdf"`；中文环境还可能带上
    `“E:\\Books\\x.pdf”`。这些会让 `is_absolute()` 误判为相对路径，
    进而拼出 `E:\\Books\\E:\\Books\\x.pdf` 这种叠加路径。
    """
    s = _INVISIBLE.sub("", str(raw)).strip()
    closer = _QUOTE_PAIRS.get(s[:1])
    if closer and len(s) >= 2 and s.endswith(closer):
        s = s[1:-1].strip()
    return s


def check_syntax(raw: str) -> None:
    """用户输入的路径语法检查：可疑但不是合法绝对路径 → 明确报错，不静默拼接。"""
    for sep in _FULLWIDTH_SEPS:
        if sep in raw:
            half = "\\" if sep == "＼" else "/"
            raise PathSyntaxError(
                f"路径含全角分隔符 {sep!r}（中文输入法常见），"
                f"应改为半角 {half!r}：{raw}"
            )
    m = _DRIVE_PREFIX.match(raw)
    if m and (m.group(2) == "：" or not m.group(1).isascii()):
        bad = m.group(1) + m.group(2)
        fixed = f"{_ascii_letter(m.group(1))}:{raw[2:]}"
        raise PathSyntaxError(
            f"盘符 {bad!r} 含全角字符「{m.group(1)}」或「{m.group(2)}」"
            f"（中文输入法全角模式常见），应为半角，例如：{fixed}"
        )


def is_absolute(raw: str) -> bool:
    """跨平台判断路径是否为绝对路径。

    Windows：盘符 `D:\\` 或 UNC `\\\\server\\share`；
    POSIX：前导 `/`。
    """
    if not raw:
        return False
    if raw.startswith("/") or raw.startswith("\\"):
        return True
    return bool(ntpath.splitdrive(raw)[0])


def resolve(raw: str | None, library_root: Path | None) -> Path | None:
    """把库内存储的路径解析为可用的绝对路径。"""
    if not raw:
        return None
    raw = _clean(raw)                       # 去空白 + 成对引号
    check_syntax(raw)                       # 坏路径明确报错，不静默拼接
    if is_absolute(raw):
        return Path(raw)
    if library_root is None:
        raise LibraryRootMissingError(
            f"记录使用相对路径 {raw!r}，但未配置 library_root。\n"
            f"请在 book.toml 的 [paths] 中设置 library_root。"
        )
    return (library_root / raw).resolve()


def display(raw: str | None, library_root: Path | None) -> str | None:
    """展示用：尽量给出相对于 library_root 的路径。"""
    if not raw:
        return None
    raw = _clean(raw)
    if not is_absolute(raw):
        return raw
    if library_root is None:
        return raw
    try:
        return posixpath.join(*Path(raw).relative_to(library_root).parts)
    except ValueError:
        return raw


def to_stored(path: str | Path, library_root: Path | None) -> str:
    """把用户输入的路径转换为库内存储形式。

    位于 library_root 之内 → 存相对路径（便于迁移）；
    之外或未配置根目录 → 存绝对路径。
    """
    raw = _clean(path)                      # 去空白 + 成对引号（复制路径常见）
    check_syntax(raw)                       # 全角冒号等 → 明确报错
    p = Path(raw)
    if library_root is not None:
        try:
            rel = p.resolve().relative_to(library_root.resolve())
            return os.fspath(rel)
        except ValueError:
            pass
    return os.fspath(p)
