"""配置加载与保存（book.toml）。

配置项（见 PRD §6.1 / §13 / §18）：
  [paths]
  library_root = "D:/Books"     # 相对路径的根目录；留空则无法解析相对路径
  db_path      = "book.db"      # SQLite 数据库文件

  [vlm]
  endpoint = "http://127.0.0.1:11434"
  model    = "qwen2.5-vl:3b"

配置查找顺序：
  1. 命令行 --config 显式指定
  2. 当前目录的 book.toml
  3. 内置默认值（db 落在当前目录）
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILENAME = "book.toml"
DEFAULT_DB_NAME = "book.db"


@dataclass
class VlmConfig:
    """本地 VLM 适配配置（PRD §13 / §17）。

    `backend`：
      · `auto`    —— 内置 llama.cpp 就绪就用它，否则回退到已启动的 Ollama（默认）
      · `builtin` —— 只用内置模型
      · `ollama`  —— 只用 Ollama
    `builtin_model` 为内置模型的 key（见 `services/model.CATALOG`）。
    """

    endpoint: str = "http://127.0.0.1:11434"
    model: str = "qwen2.5vl:3b"
    backend: str = "auto"
    builtin_model: str = "3b"


@dataclass
class Config:
    """运行期配置。"""

    config_path: Path | None = None
    library_root: Path | None = None
    db_path: Path = field(default_factory=lambda: Path(DEFAULT_DB_NAME))
    vlm: VlmConfig = field(default_factory=VlmConfig)

    # ---------------------------------------------------------------- load
    @classmethod
    def load(cls, config_path: str | Path | None = None) -> "Config":
        """从 book.toml 加载配置；找不到则返回默认配置。"""
        path: Path | None = None
        if config_path is not None:
            path = Path(config_path)
            if not path.exists():
                raise FileNotFoundError(f"配置文件不存在：{path}")
        else:
            candidate = Path.cwd() / CONFIG_FILENAME
            if candidate.exists():
                path = candidate

        cfg = cls(config_path=path)
        if path is None:
            return cfg

        with path.open("rb") as fh:
            data = tomllib.load(fh)

        base = path.parent
        paths = data.get("paths", {}) or {}
        if paths.get("library_root"):
            cfg.library_root = _resolve(base, paths["library_root"])
        if paths.get("db_path"):
            cfg.db_path = _resolve(base, paths["db_path"])

        vlm = data.get("vlm", {}) or {}
        if vlm.get("endpoint"):
            cfg.vlm.endpoint = str(vlm["endpoint"])
        if vlm.get("model"):
            cfg.vlm.model = str(vlm["model"])
        if vlm.get("backend"):
            cfg.vlm.backend = str(vlm["backend"])
        if vlm.get("builtin_model"):
            cfg.vlm.builtin_model = str(vlm["builtin_model"])

        return cfg

    # ---------------------------------------------------------------- save
    @property
    def effective_config_path(self) -> Path:
        """实际使用的配置文件路径（未指定则落在当前目录）。"""
        return self.config_path or (Path.cwd() / CONFIG_FILENAME)

    def save(self, *, base: Path | None = None) -> Path:
        """把当前配置写回 book.toml（init / 新建 Library 时调用）。

        `base` 给定时，落在其下的路径写成**相对路径**（便于整库整体搬迁）；
        默认 `None` 保持原样，**不影响既有 CLI 行为**。
        """
        path = self.effective_config_path
        lines = [
            "# bookdb 配置文件（由 `book init` 生成）",
            "",
            "[paths]",
            f'library_root = "{_toml_escape(_rel(self.library_root, base))}"'
            if self.library_root
            else 'library_root = ""',
            f'db_path = "{_toml_escape(_rel(self.db_path, base))}"',
            "",
            "[vlm]",
            f'endpoint = "{self.vlm.endpoint}"',
            f'model = "{self.vlm.model}"',
            f'backend = "{self.vlm.backend}"',
            f'builtin_model = "{self.vlm.builtin_model}"',
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
        self.config_path = path
        return path


def _resolve(base: Path, value: str) -> Path:
    """相对路径按配置文件所在目录解析；绝对路径原样返回。"""
    p = Path(value)
    if p.is_absolute():
        return p
    return (base / p).resolve()


def _rel(value: Path | None, base: Path | None) -> Path | str:
    """`base` 给定时，落在 `base` 之下的路径转为相对路径；否则原样返回。"""
    if value is None:
        return ""
    if base is None:
        return value
    try:
        return value.resolve().relative_to(base.resolve())
    except (ValueError, OSError):
        return value


def _toml_escape(value: Path | str) -> str:
    return str(value).replace("\\", "/")
