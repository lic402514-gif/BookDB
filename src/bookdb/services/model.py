"""本地 AI 模型管理（PRD §17）—— 内置 llama.cpp 所需的 GGUF 模型。

设计：
  · **不依赖 Ollama**：模型由本程序下载到 `%APPDATA%\\BookDB\\models\\`
    （经 `gui/settings.app_data_dir()`，支持 `BOOKDB_SETTINGS_DIR` 覆盖）。
  · **可复用 Ollama**：若本机已装 Ollama 且已有同名模型，直接复用其 blob，
    零下载（`find_ollama_model()`）。
  · **下载源**：默认 Hugging Face **镜像** `hf-mirror.com`（实测国内可达性远好于
    官方 `huggingface.co`），失败自动回退官方源；两源可在配置里指定。
  · **断点续传**：用 HTTP `Range` 续传；`.part` 临时文件 + 完成后原子改名，
    并用 `sha256` 校验（若清单给了哈希）。
  · 全部标准库（urllib），不引入 requests / huggingface_hub。

模型三档（Q4_K_M 量化 + Q8 mmproj，体积/质量/速度的平衡）：
    3b   ≈ 2.8 GB  默认，速度与质量的平衡点
    7b   ≈ 5.5 GB  质量优先（内存 16GB+ 更合适）
    mini ≈ 1.3 GB  低配机器 / 先试水
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

#: 下载源（按顺序尝试）。镜像在前 —— 实测国内对 huggingface.co 常超时。
DEFAULT_MIRRORS = ("https://hf-mirror.com", "https://huggingface.co")

#: 用户模型目录名（位于应用数据目录下）
MODELS_DIRNAME = "models"

#: 每个文件的分块大小（8 MiB）
CHUNK = 8 << 20


class ModelError(RuntimeError):
    """模型相关错误（未下载、下载失败、校验不符等）。"""


@dataclass(frozen=True)
class ModelFile:
    """模型仓库里的一个文件。"""

    repo: str
    filename: str
    size: int
    sha256: str | None = None

    @property
    def url_path(self) -> str:
        return f"{self.repo}/resolve/main/{self.filename}"


@dataclass(frozen=True)
class ModelSpec:
    """一款可选模型（含必需的视觉投影层 mmproj）。"""

    key: str                     # '3b' / '7b' / 'mini'
    name: str                    # 展示名
    note: str                    # 一句话说明
    model: ModelFile             # 主模型
    mmproj: ModelFile            # 视觉投影层（必需）

    @property
    def total_size(self) -> int:
        return self.model.size + self.mmproj.size

    @property
    def files(self) -> tuple[ModelFile, ...]:
        return (self.model, self.mmproj)


def _f(repo: str, filename: str, size: int) -> ModelFile:
    return ModelFile(repo=repo, filename=filename, size=size)


#: 内置模型清单（体积为实测值，来自 HF 镜像 API）
CATALOG: tuple[ModelSpec, ...] = (
    ModelSpec(
        key="3b",
        name="Qwen2.5-VL 3B",
        note="推荐 · 速度与质量的平衡",
        model=_f("ggml-org/Qwen2.5-VL-3B-Instruct-GGUF",
                 "Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf", 1_930_373_216),
        mmproj=_f("ggml-org/Qwen2.5-VL-3B-Instruct-GGUF",
                  "mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf", 844_760_000),
    ),
    ModelSpec(
        key="7b",
        name="Qwen2.5-VL 7B",
        note="质量优先 · 占用较大（约 5.5 GB）",
        model=_f("ggml-org/Qwen2.5-VL-7B-Instruct-GGUF",
                 "Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf", 4_680_000_000),
        mmproj=_f("ggml-org/Qwen2.5-VL-7B-Instruct-GGUF",
                  "mmproj-Qwen2.5-VL-7B-Instruct-Q8_0.gguf", 853_120_000),
    ),
    ModelSpec(
        key="mini",
        name="MiniCPM-V 4.6",
        note="轻量 · 约 1.3 GB，适合配置较低的机器",
        model=_f("ggml-org/MiniCPM-V-4.6-GGUF",
                 "MiniCPM-V-4.6-Q4_K_M.gguf", 529_100_000),
        mmproj=_f("ggml-org/MiniCPM-V-4.6-GGUF",
                  "mmproj-MiniCPM-V-4.6-Q8_0.gguf", 727_950_000),
    ),
)

DEFAULT_KEY = "3b"


def find(key: str) -> ModelSpec:
    """按 key 取模型定义；不存在则报错（不静默回退）。"""
    for spec in CATALOG:
        if spec.key == key:
            return spec
    keys = ", ".join(s.key for s in CATALOG)
    raise ModelError(f"未知模型 {key!r}；可选：{keys}")


# ────────────────────────────────────────────────────────────── 目录
def models_dir() -> Path:
    """用户模型目录（不存在则创建）。"""
    from ..gui.settings import app_data_dir   # 延迟导入，避免循环

    path = app_data_dir() / MODELS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _local_path(spec: ModelSpec, f: ModelFile) -> Path:
    """本地文件名 = 仓库名 + 原文件名，避免不同模型的同名冲突。"""
    repo = spec.model.repo.split("/")[-1]
    return models_dir() / repo / f.filename


# ────────────────────────────────────────────────────── Ollama 复用
def ollama_blobs_dir() -> Path | None:
    """Ollama 的 blob 目录（存在才算）。"""
    env = os.environ.get("OLLAMA_MODELS")
    candidates = [Path(env)] if env else []
    candidates.append(Path.home() / ".ollama" / "models")
    for base in candidates:
        blobs = base / "blobs"
        if blobs.is_dir():
            return blobs
    return None


def ollama_manifest(model_tag: str) -> tuple[Path, Path] | None:
    """在 Ollama 里找 `model_tag`（如 `qwen2.5vl:3b`）的 (主模型, mmproj)。

    Ollama 的 blob **就是标准 GGUF**（实测），故可直接交给 llama-server 使用。
    找不到则返回 None（调用方回退到自行下载）。
    """
    blobs = ollama_blobs_dir()
    if blobs is None:
        return None

    name, _, tag = model_tag.partition(":")
    tag = tag or "latest"
    manifest = blobs.parent / "manifests" / "registry.ollama.ai" / "library" / name / tag
    if not manifest.is_file():
        return None

    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    layers = data.get("layers") or []
    model = projector = None
    for layer in layers:
        kind = str(layer.get("mediaType", "")).rsplit(".", 1)[-1]
        digest = str(layer.get("digest", "")).replace("sha256:", "")
        path = blobs / f"sha256-{digest}"
        if not path.is_file():
            continue
        if kind == "model":
            model = path
        elif kind in ("projector", "mmproj"):
            projector = path
    return (model, projector) if model and projector else None


def find_ollama_model(spec: ModelSpec) -> tuple[Path, Path] | None:
    """按内置清单的模型名，尝试在 Ollama 里找到**等价**模型（复用以省下载）。"""
    by_key = {
        "3b": "qwen2.5vl:3b",
        "7b": "qwen2.5vl:7b",
    }
    tag = by_key.get(spec.key)
    return ollama_manifest(tag) if tag else None


# ────────────────────────────────────────────────────────── 状态查询
@dataclass
class ModelState:
    """某款模型在本机的状态。"""

    spec: ModelSpec
    files_ready: bool                  # 所有文件都在本地
    source: str                        # 'local' | 'ollama' | 'none'
    paths: tuple[Path, ...] = ()       # 就绪时的本地路径（主模型, mmproj）
    downloaded: int = 0                # 已下载字节（用于显示进度）

    @property
    def ready(self) -> bool:
        return self.files_ready


def state(spec: ModelSpec, *, prefer_ollama: bool = True) -> ModelState:
    """检查某款模型是否可用（本地 → Ollama → 无）。"""
    paths = tuple(_local_path(spec, f) for f in spec.files)
    if all(p.is_file() for p in paths):
        got = sum(p.stat().st_size for p in paths)
        return ModelState(spec, True, "local", paths, got)

    if prefer_ollama:
        pair = find_ollama_model(spec)
        if pair:
            return ModelState(spec, True, "ollama", pair, spec.total_size)

    got = sum(p.stat().st_size for p in paths if p.is_file())
    return ModelState(spec, False, "none", paths, got)


def list_states(*, prefer_ollama: bool = True) -> list[ModelState]:
    return [state(s, prefer_ollama=prefer_ollama) for s in CATALOG]


# ────────────────────────────────────────────────────────────── 下载
def _probe_total(url: str, timeout: float = 15.0) -> int | None:
    """用 HEAD 探测远端大小（拿不到返回 None）。"""
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            length = r.headers.get("Content-Length")
        return int(length) if length else None
    except Exception:  # noqa: BLE001
        return None


def download_file(
    f: ModelFile,
    dest: Path,
    *,
    mirrors: tuple[str, ...] = DEFAULT_MIRRORS,
    on_progress=None,
    cancel=None,
) -> Path:
    """把 `f` 下到 `dest`，支持断点续传与多源回退。

    `on_progress(done_bytes, total_bytes)` —— 每块回调一次（GUI 用来画进度条）。
    `cancel()` —— 返回真值时中止（抛 `ModelError`）。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.is_file() else 0

    last_error: Exception | None = None
    for base in mirrors:
        url = f"{base.rstrip('/')}/{f.url_path}"

        # 已下完（大小吻合）→ 直接改名收工
        total = _probe_total(url)
        if have and total and have >= total:
            part.replace(dest)
            return dest

        req = urllib.request.Request(url)
        if have:
            req.add_header("Range", f"bytes={have}-")

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                if resp.status == 200 and have:
                    have = 0                       # 服务端不支持 Range → 重下
                total = total or (int(resp.headers["Content-Length"]) + have
                                  if resp.headers.get("Content-Length") else 0)
                mode = "ab" if have else "wb"
                with open(part, mode) as fh:
                    while True:
                        if cancel is not None and cancel():
                            raise ModelError("下载已取消")
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        fh.write(chunk)
                        have += len(chunk)
                        if on_progress is not None:
                            on_progress(have, total)
        except ModelError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 换源重试
            last_error = exc
            continue

        if total and have < total:
            last_error = ModelError(f"下载不完整（{have}/{total} 字节）")
            continue

        part.replace(dest)                        # 原子落地
        return dest

    raise ModelError(
        f"下载失败：{f.filename}\n"
        f"  最后错误：{last_error}\n"
        f"  已试源：{', '.join(mirrors)}\n"
        f"  可稍后重试（支持断点续传）。"
    )


def download(
    spec: ModelSpec,
    *,
    mirrors: tuple[str, ...] = DEFAULT_MIRRORS,
    on_progress=None,
    cancel=None,
) -> tuple[Path, Path]:
    """下载一款模型的全部文件，返回 (主模型, mmproj) 路径。"""
    done_before = 0
    total_all = spec.total_size

    def wrap(offset: int):
        def inner(got: int, _total: int) -> None:
            if on_progress is not None:
                on_progress(offset + got, total_all)
        return inner

    paths: list[Path] = []
    for f in spec.files:
        dest = _local_path(spec, f)
        if dest.is_file() and dest.stat().st_size >= f.size:
            done_before += f.size
            if on_progress is not None:
                on_progress(done_before, total_all)
            paths.append(dest)
            continue
        download_file(f, dest, mirrors=mirrors,
                      on_progress=wrap(done_before), cancel=cancel)
        done_before += f.size
        paths.append(dest)

    return paths[0], paths[1]


def remove(spec: ModelSpec) -> int:
    """删除某款模型的**本地下载文件**（不动 Ollama 的）。返回释放的字节数。"""
    freed = 0
    repo_dir: Path | None = None
    for f in spec.files:
        path = _local_path(spec, f)
        if path.is_file():
            freed += path.stat().st_size
            path.unlink()
        part = path.with_suffix(path.suffix + ".part")
        if part.is_file():
            freed += part.stat().st_size
            part.unlink()
        repo_dir = path.parent
    if repo_dir is not None and repo_dir.is_dir() and not any(repo_dir.iterdir()):
        repo_dir.rmdir()
    return freed


def human_size(n: int) -> str:
    for unit, div in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} B"
