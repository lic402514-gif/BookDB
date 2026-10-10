"""文件路径解析（见 PRD §6 / §6.1）。

规则：
  - 绝对路径：原样使用；
  - 相对路径：以 config.library_root 为基准拼接解析；
  - library_root 缺失而路径又是相对路径 → 明确报错，不静默猜测；
  - 显示时优先给出相对于 library_root 的相对路径，便于整库迁移。
"""

from __future__ import annotations

import ntpath
import os
import posixpath
from pathlib import Path


class LibraryRootMissingError(RuntimeError):
    """存在相对路径记录但未配置 library_root。"""


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
    p = Path(path)
    if library_root is not None:
        try:
            rel = p.resolve().relative_to(library_root.resolve())
            return os.fspath(rel)
        except ValueError:
            pass
    return os.fspath(p)
