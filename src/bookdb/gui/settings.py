"""应用设置（P0 产品化）—— 与 Library **分离**。

存放**用户偏好与历史**（最近库 / 上次打开的库）。**绝不**存任何 Library 自己的配置
（`library_root` / `db_path` / VLM 仍只存在于各 Library 的 `book.toml`）——
这是「程序本体 / 应用状态 / 用户数据」三者分离的落地。

位置：
  Windows  %APPDATA%\\BookDB\\settings.json
  其他     $XDG_CONFIG_HOME/BookDB/settings.json（回退 ~/.config/...）

容错原则：**设置文件损坏/半截时绝不抛异常** —— 直接当空设置（下次写入时重建）。
理由：应用偏好丢了是小事，**程序起不来**才是大事。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

APP_DIR_NAME = "BookDB"
SETTINGS_FILENAME = "settings.json"

#: 最近库列表上限（超出淘汰最旧的）
MAX_RECENT = 8

#: 当前 settings 结构版本（将来迁移用）
SETTINGS_VERSION = 1


def app_data_dir() -> Path:
    """应用设置目录。

    可用环境变量 `BOOKDB_SETTINGS_DIR` 覆盖 —— 便于测试隔离，也允许用户自定义位置。
    """
    override = os.environ.get("BOOKDB_SETTINGS_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return root / APP_DIR_NAME


def settings_path() -> Path:
    """settings.json 的完整路径。"""
    return app_data_dir() / SETTINGS_FILENAME


@dataclass
class RecentLibrary:
    """最近打开过的一个 Library。"""

    path: Path
    name: str
    opened_at: str = ""

    def to_json(self) -> dict:
        return {"path": str(self.path), "name": self.name, "opened_at": self.opened_at}

    @classmethod
    def from_json(cls, raw: object) -> "RecentLibrary | None":
        """宽容解析：字段缺失/类型不对 → 返回 None（跳过该条，不报错）。"""
        if not isinstance(raw, dict):
            return None
        path = raw.get("path")
        if not isinstance(path, str) or not path.strip():
            return None
        name = raw.get("name")
        opened = raw.get("opened_at")
        return cls(
            path=Path(path),
            name=name if isinstance(name, str) and name else Path(path).name,
            opened_at=opened if isinstance(opened, str) else "",
        )


@dataclass
class Settings:
    """应用设置。"""

    last_library: Path | None = None
    recent: list[RecentLibrary] = field(default_factory=list)
    path: Path = field(default_factory=settings_path)

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: str | Path | None = None) -> "Settings":
        """读取设置；文件不存在或损坏 → 返回空设置（**不抛异常**）。"""
        target = Path(path) if path else settings_path()
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls(path=target)
        if not isinstance(raw, dict):
            return cls(path=target)

        last = raw.get("last_library")
        recent_raw = raw.get("recent_libraries")
        recent: list[RecentLibrary] = []
        if isinstance(recent_raw, list):
            for entry in recent_raw:
                item = RecentLibrary.from_json(entry)
                if item is not None:
                    recent.append(item)

        return cls(
            last_library=Path(last) if isinstance(last, str) and last.strip() else None,
            recent=recent[:MAX_RECENT],
            path=target,
        )

    # ------------------------------------------------------------------ save
    def save(self) -> Path:
        """写回 settings.json（自动建目录；先写临时文件再替换，避免半截文件）。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SETTINGS_VERSION,
            "last_library": str(self.last_library) if self.last_library else None,
            "recent_libraries": [r.to_json() for r in self.recent[:MAX_RECENT]],
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, self.path)          # Windows 上原子替换
        return self.path

    # --------------------------------------------------------------- 变更
    def touch(self, library_path: str | Path, name: str | None = None) -> None:
        """记录「刚打开过某个 Library」：更新 last + 置顶最近列表。"""
        path = Path(library_path)
        display = name or path.name
        stamp = datetime.now().replace(microsecond=0).isoformat()

        self.last_library = path
        self.recent = [r for r in self.recent if not _same_path(r.path, path)]
        self.recent.insert(0, RecentLibrary(path=path, name=display, opened_at=stamp))
        self.recent = self.recent[:MAX_RECENT]

    def forget(self, library_path: str | Path) -> None:
        """从最近列表移除（条目失效时用）。"""
        path = Path(library_path)
        self.recent = [r for r in self.recent if not _same_path(r.path, path)]
        if self.last_library and _same_path(self.last_library, path):
            self.last_library = None

    @property
    def is_empty(self) -> bool:
        return not self.recent and self.last_library is None


def _same_path(a: Path, b: Path) -> bool:
    """路径比较（Windows 不分大小写；解析失败时退化为字符串比较）。"""
    try:
        return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))
    except OSError:
        return os.path.normcase(str(a)) == os.path.normcase(str(b))
