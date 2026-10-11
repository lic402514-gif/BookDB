"""BookDB 独立 GUI 入口（P0 产品化）—— `BookDB.exe` 的代码入口。

启动优先级（用户拍板）：

    BookDB.exe "E:\\Books"   → 打开指定 Library
    BookDB.exe               → 打开上次使用的 Library
                              → 没有 / 已失效 → 欢迎页

**本模块不 import `cli`** —— GUI 不再经过 CLI 启动，也不再依赖
「当前工作目录就是 Library」这一开发期机制（CLI 侧**保留**该机制不变）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..config import Config
from . import library as library_mod
from . import settings as settings_mod


def open_library(path: str | Path) -> "library_mod.Library":
    """打开 Library 并记入「最近使用」。"""
    lib = library_mod.Library.open(path)
    state = settings_mod.Settings.load()
    state.touch(lib.path, lib.name)
    state.save()
    return lib


def resolve_startup(
    argv: list[str] | None = None,
) -> tuple[Config | None, str | None]:
    """按优先级决定启动时打开哪个 Library。

    返回 `(config, notice)`：

      - `config` 为 `None` → 显示欢迎页；
      - `notice` 非空 → 需要向用户说明的原因（例如参数路径不是书库），
        前端以提示条形式展示，不阻断进入欢迎页。

    命令行参数只是「指定启动时打开哪个 Library」，**不写入任何全局配置** ——
    Library 仍由自己的 `book.toml` / `book.db` 管理。
    """
    args = list(sys.argv[1:] if argv is None else argv)

    # ① 命令行参数优先（为将来的文件关联 / 专用快捷方式留接口）
    explicit = next((a for a in args if a and not a.startswith("-")), None)
    if explicit:
        try:
            lib = open_library(explicit)
        except library_mod.LibraryError as exc:
            return None, f"命令行指定的书库无法打开：{exc}"
        return lib.config, None

    # ② 上次使用的（仍有效才用）
    state = settings_mod.Settings.load()
    if state.last_library and library_mod.is_valid_path(state.last_library):
        try:
            lib = open_library(state.last_library)
        except library_mod.LibraryError:
            pass                      # 失效 → 落到欢迎页（不打扰用户）
        else:
            return lib.config, None

    # ③ 欢迎页
    return None, None


def main() -> None:
    """`BookDB.exe` 入口。"""
    from .app import run

    config, notice = resolve_startup()
    run(config, notice=notice)


if __name__ == "__main__":      # pragma: no cover - 手动运行
    main()
