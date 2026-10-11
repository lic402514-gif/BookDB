"""Library 的管理（P0 产品化）—— **一个 Library = 一个目录**。

GUI 通过本模块**显式**维护「当前打开的是哪个 Library」，
不再依赖「当前工作目录就是 Library」这一开发期机制（CLI 仍保留 cwd 发现方式）。

Library 目录布局（与数据架构保持一致，未做任何改动）：

    E:\\Books\\                 ← Library 根（本模块管理的对象）
      ├── book.toml            ← 该 Library 的配置（library_root / db_path / vlm）
      ├── book.db              ← 该 Library 的数据库
      └── *.pdf

本模块**不新造配置机制** —— 打开 Library 的本质就是 `Config.load(<dir>/book.toml)`。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import CONFIG_FILENAME, Config
from ..db import database
from ..services import setup as setup_service


class LibraryError(RuntimeError):
    """Library 相关错误的基类。"""


class LibraryNotFoundError(LibraryError):
    """目录不存在 / 不是目录。"""


class NotALibraryError(LibraryError):
    """目录存在，但不是 BookDB Library（缺少 book.toml）。"""


def _looks_like_library(path: Path) -> bool:
    """目录里是否有 book.toml（**只看文件在不在**，不解析内容）。"""
    return (path / CONFIG_FILENAME).is_file()


def _normalize(path: str | Path) -> Path:
    """把用户给的目标规整成 Library 目录。

    允许直接传 `book.toml` 的完整路径（例如从「复制路径」拿到）。
    """
    p = Path(path).expanduser()
    if p.is_file() and p.name == CONFIG_FILENAME:
        p = p.parent
    return p


@dataclass
class Library:
    """一个已打开（已加载配置）的 Library。"""

    path: Path                 # Library 目录（book.toml 所在）
    config: Config             # 从该目录加载的配置

    # ------------------------------------------------------------ 打开
    @classmethod
    def open(cls, path: str | Path, *, ensure_db: bool = True) -> "Library":
        """打开已有 Library：校验 + 加载配置（+ 确保 book.db 存在）。

        `ensure_db=True`（默认）时，若目录里有 `book.toml` 但缺 `book.db`，
        则**幂等地**补建数据库（只 `CREATE TABLE IF NOT EXISTS`，绝不 DROP/DELETE）。
        `probe()` 用 `ensure_db=False` 做**无副作用**校验。

        不合法时抛 `LibraryNotFoundError` / `NotALibraryError`（带可读中文原因）。
        """
        target = _normalize(path)
        if not target.exists():
            raise LibraryNotFoundError(f"目录不存在：{target}")
        if not target.is_dir():
            raise NotALibraryError(f"不是目录：{target}")
        if not _looks_like_library(target):
            raise NotALibraryError(
                f"该目录不是 BookDB 书库（找不到 {CONFIG_FILENAME}）：{target}"
            )
        config = Config.load(target / CONFIG_FILENAME)
        if ensure_db and not database.database_exists(config.db_path):
            setup_service.initialize(config)      # 幂等补建，不碰已有数据
        return cls(path=target.resolve(), config=config)

    # ------------------------------------------------------------ 新建
    @classmethod
    def create(
        cls,
        path: str | Path,
        *,
        library_root: str | Path | None = None,
        force: bool = False,
    ) -> "Library":
        """在指定目录新建 Library（目录不存在则创建）。

        `library_root` 缺省 = 该目录自身（书就放在 Library 目录里）。
        复用 `services/setup.initialize` —— 幂等，绝不 DROP/DELETE 已有数据。
        """
        target = _normalize(path)
        target = target.resolve()
        target.mkdir(parents=True, exist_ok=True)

        root = Path(library_root).resolve() if library_root else target
        config = Config(
            config_path=target / CONFIG_FILENAME,
            library_root=root,
            db_path=target / "book.db",
        )
        setup_service.initialize(config, force=force)

        # 归一化 book.toml：把落在 Library 内的路径写成**相对路径**，便于整库搬迁
        config.save(base=target)
        return cls.open(target)

    # ------------------------------------------------------------ 属性
    @property
    def name(self) -> str:
        """显示名 = 目录名（如 `E:\\Books` → `Books`）。"""
        return self.path.name

    @property
    def config_path(self) -> Path:
        return self.path / CONFIG_FILENAME

    @property
    def db_path(self) -> Path:
        return self.config.db_path

    @property
    def has_db(self) -> bool:
        """book.db 是否已存在。"""
        return database.database_exists(self.config.db_path)

    def is_valid(self) -> bool:
        """当前是否仍然是个可用 Library（目录与 book.toml 都还在）。"""
        return self.path.is_dir() and self.config_path.is_file()


def probe(path: str | Path) -> dict:
    """只做校验、不打开 —— 供欢迎页「打开已有 Library」预览用。

    返回 {valid, reason, name, has_db, library_root}。**不抛异常。**
    """
    try:
        lib = Library.open(path, ensure_db=False)      # 只读校验，不建库、不写盘
    except LibraryError as exc:
        return {
            "valid": False,
            "reason": str(exc),
            "path": str(_normalize(path)),
            "name": None,
            "has_db": False,
            "library_root": None,
        }
    return {
        "valid": True,
        "reason": None,
        "path": str(lib.path),
        "name": lib.name,
        "has_db": lib.has_db,
        "library_root": str(lib.config.library_root) if lib.config.library_root else None,
    }


def is_valid_path(path: str | Path) -> bool:
    """轻量校验（最近列表标灰用）—— 只看 book.toml 在不在。"""
    target = _normalize(path)
    return target.is_dir() and _looks_like_library(target)
