"""内置本地模型测试（services/model.py + llm/adapters/llamacpp.py）。

**不下载真实模型**（几 GB）—— 用假文件 + monkeypatch 覆盖逻辑。
真实端到端在 `.tmp/verify_adapter.py`（PoC 已跑通）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bookdb.config import VlmConfig  # noqa: E402
from bookdb.llm.adapters import llamacpp  # noqa: E402
from bookdb.llm.base import ModelUnavailableError, VLMAdapter  # noqa: E402
from bookdb.services import llm as llm_service  # noqa: E402
from bookdb.services import model as model_service  # noqa: E402


# ═══════════════════════════════════════════════════════ 清单
def test_catalog_has_two_tiers():
    """只收录实测可用的两款（MiniCPM-V 实测返回空输出，已剔除）。"""
    keys = [s.key for s in model_service.CATALOG]
    assert keys == ["3b", "7b"]
    assert model_service.DEFAULT_KEY == "3b"


def test_catalog_default_matches_ollama_common_setup():
    """默认 3b 与用户 Ollama 常用的 `qwen2.5vl:3b` 是同一模型 —— 质量一致。"""
    spec = model_service.find(model_service.DEFAULT_KEY)
    assert "Qwen2.5-VL-3B" in spec.model.filename
    assert model_service.find_ollama_model.__name__     # 存在复用能力
    assert model_service._OLLAMA_TAGS["3b"] == "qwen2.5vl:3b"


def test_catalog_entries_are_complete():
    for spec in model_service.CATALOG:
        assert spec.model.filename.endswith(".gguf")
        assert "mmproj" in spec.mmproj.filename, "视觉层是必需的"
        assert spec.total_size > 0
        assert len(spec.files) == 2


def test_find_rejects_unknown_key():
    with pytest.raises(model_service.ModelError) as e:
        model_service.find("99b")
    assert "未知模型" in str(e.value)
    assert "3b" in str(e.value)          # 提示可选值


def test_find_returns_spec():
    assert model_service.find("7b").key == "7b"


# ═══════════════════════════════════════════════════════ 状态 / Ollama 复用
def test_state_none_when_nothing_present(tmp_path, monkeypatch):
    monkeypatch.setattr(model_service, "models_dir", lambda: tmp_path / "models")
    monkeypatch.setattr(model_service, "find_ollama_model", lambda spec: None)
    st = model_service.state(model_service.find("3b"))
    assert not st.ready and st.source == "none"


def test_state_local_when_files_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(model_service, "find_ollama_model", lambda spec: None)
    spec = model_service.find("3b")
    for f in spec.files:
        p = model_service._local_path(spec, f)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 100)
    monkeypatch.setattr(model_service, "_local_path", model_service._local_path)
    st = model_service.state(spec)
    assert st.ready and st.source == "local" and st.downloaded == 200


def test_state_prefers_ollama_when_available(tmp_path, monkeypatch):
    spec = model_service.find("3b")
    fake = (tmp_path / "m.gguf", tmp_path / "p.gguf")
    monkeypatch.setattr(model_service, "find_ollama_model", lambda s: fake)
    st = model_service.state(spec)
    assert st.ready and st.source == "ollama" and st.paths == fake


def test_ollama_manifest_reads_blob_layout(tmp_path, monkeypatch):
    """Ollama 的 blob 就是标准 GGUF —— 直接按 manifest 解析出两个路径。"""
    blobs = tmp_path / "models" / "blobs"
    blobs.mkdir(parents=True)
    model_digest = "a" * 64
    proj_digest = "b" * 64
    (blobs / f"sha256-{model_digest}").write_bytes(b"GGUF")
    (blobs / f"sha256-{proj_digest}").write_bytes(b"GGUF")
    manifest = tmp_path / "models" / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5vl" / "3b"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"layers": [
        {"mediaType": "application/vnd.ollama.image.model", "digest": f"sha256:{model_digest}"},
        {"mediaType": "application/vnd.ollama.image.projector", "digest": f"sha256:{proj_digest}"},
    ]}), encoding="utf-8")

    monkeypatch.setattr(model_service, "ollama_blobs_dir", lambda: blobs)
    got = model_service.ollama_manifest("qwen2.5vl:3b")
    assert got is not None
    assert got[0].name == f"sha256-{model_digest}"
    assert got[1].name == f"sha256-{proj_digest}"


def test_ollama_manifest_recurses_into_sub_manifest(tmp_path, monkeypatch):
    """实测结构：顶层 `layers` 只有 model，视觉层在内嵌的子清单里。

    `qwen2.5vl:3b` 的真实结构：
        顶层 layers → model + template/system/license/params
                      + 三个 mediaType 含 'manifest' 的子清单
        视觉投影层在『llamacpp 引擎』那个子清单的 layers 里（projector）。
    不递归就会永远找不到 mmproj。
    """
    blobs = tmp_path / "models" / "blobs"
    blobs.mkdir(parents=True)
    main_d, proj_d, sub_d = "1" * 64, "2" * 64, "3" * 64
    for d in (main_d, proj_d, sub_d):
        (blobs / f"sha256-{d}").write_bytes(b"GGUF")

    # 子清单：含视觉投影层
    (blobs / f"sha256-{sub_d}").write_text(json.dumps({"layers": [
        {"mediaType": "application/vnd.ollama.image.model", "digest": f"sha256:{main_d}"},
        {"mediaType": "application/vnd.ollama.image.projector", "digest": f"sha256:{proj_d}"},
    ]}), encoding="utf-8")

    # 顶层清单：只有 model + 一个内嵌子清单（**没有** projector）
    manifest = tmp_path / "models" / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5vl" / "3b"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "mediaType": "application/vnd.ollama.manifest.list.v2+json",
        "layers": [
            {"mediaType": "application/vnd.ollama.image.model", "digest": f"sha256:{main_d}"},
            {"mediaType": "application/vnd.docker.distribution.manifest.v2+json",
             "digest": f"sha256:{sub_d}"},
        ],
    }), encoding="utf-8")

    monkeypatch.setattr(model_service, "ollama_blobs_dir", lambda: blobs)
    got = model_service.ollama_manifest("qwen2.5vl:3b")
    assert got is not None, "必须递归进子清单才能找到 mmproj"
    assert got[0].name == f"sha256-{main_d}"
    assert got[1].name == f"sha256-{proj_d}"


def test_media_kind_keeps_manifest_keyword():
    """⚠️ 回归：`rsplit('.', 1)[-1]` 会把 manifest 丢成 'v2+json'（踩过）。"""
    assert model_service._media_kind(
        "application/vnd.docker.distribution.manifest.v2+json") == "manifest"
    assert model_service._media_kind("application/vnd.ollama.image.projector") == "projector"
    assert model_service._media_kind("application/vnd.ollama.image.model") == "model"


def test_ollama_manifest_none_when_absent(tmp_path, monkeypatch):
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    monkeypatch.setattr(model_service, "ollama_blobs_dir", lambda: blobs)
    assert model_service.ollama_manifest("qwen2.5vl:3b") is None


def test_ollama_manifest_requires_both_parts(tmp_path, monkeypatch):
    """只有主模型、缺 mmproj → 视为不可用（视觉功能必须有投影层）。"""
    blobs = tmp_path / "models" / "blobs"
    blobs.mkdir(parents=True)
    d = "c" * 64
    (blobs / f"sha256-{d}").write_bytes(b"GGUF")
    manifest = tmp_path / "models" / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5vl" / "3b"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"layers": [
        {"mediaType": "application/vnd.ollama.image.model", "digest": f"sha256:{d}"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(model_service, "ollama_blobs_dir", lambda: blobs)
    assert model_service.ollama_manifest("qwen2.5vl:3b") is None


# ═══════════════════════════════════════════════════════ 删除
def test_remove_deletes_files_and_empty_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(model_service, "models_dir", lambda: tmp_path / "models")
    spec = model_service.find("3b")
    for f in spec.files:
        p = model_service._local_path(spec, f)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 50)
    freed = model_service.remove(spec)
    assert freed == 100
    assert not model_service._local_path(spec, spec.model).exists()
    assert not model_service._local_path(spec, spec.files[0]).parent.exists()  # 空目录也清掉


def test_remove_tolerates_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(model_service, "models_dir", lambda: tmp_path / "models")
    assert model_service.remove(model_service.find("7b")) == 0


# ═══════════════════════════════════════════════════════ 下载
def test_download_file_resumes_with_range(tmp_path, monkeypatch):
    """已有 .part → 带 Range 续传；完成后 .part 原子改名。"""
    payload = b"A" * 300
    calls: list[dict] = []

    class FakeResp:
        def __init__(self, start: int):
            self.status = 206 if start else 200
            self._body = payload[start:]
            self.headers = {"Content-Length": str(len(self._body))}

        def read(self, n=-1):
            out, self._body = self._body[:n], self._body[n:]
            return out

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=60):
        headers = {k.lower(): v for k, v in dict(req.header_items()).items()}
        start = int(headers.get("range", "bytes=0-")[6:-1]) if "range" in headers else 0
        calls.append({"range": headers.get("range")})
        return FakeResp(start)

    monkeypatch.setattr(model_service.urllib.request, "urlopen", fake_urlopen)

    spec = model_service.find("3b")
    dest = tmp_path / "repo" / "m.gguf"
    dest.parent.mkdir(parents=True)
    part = dest.with_suffix(".gguf.part")
    part.write_bytes(payload[:120])                      # 假装下载了 120 字节

    got = model_service.download_file(spec.model, dest)

    assert got == dest and dest.read_bytes() == payload
    assert not part.exists()
    # 第一次是 HEAD 探测（无 Range），续传请求带 Range
    assert any(c["range"] == "bytes=120-" for c in calls)


def test_download_reports_progress(tmp_path, monkeypatch):
    payload = b"B" * 64
    seen: list[tuple[int, int]] = []

    class FakeResp:
        status = 200

        def __init__(self):
            self._b = payload
            self.headers = {"Content-Length": str(len(payload))}

        def read(self, n=-1):
            out, self._b = self._b[:n], self._b[n:]
            return out

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(model_service.urllib.request, "urlopen",
                        lambda req, timeout=60: FakeResp())
    monkeypatch.setattr(model_service, "_probe_total", lambda url, timeout=15.0: None)

    spec = model_service.find("3b")
    model_service.download_file(
        spec.model, tmp_path / "m.gguf",
        on_progress=lambda got, total: seen.append((got, total)),
    )
    assert seen and seen[-1][0] == len(payload)


def test_download_cancel_raises(tmp_path, monkeypatch):
    payload = b"C" * (model_service.CHUNK * 2 + 10)

    class FakeResp:
        status = 200
        headers = {"Content-Length": str(len(payload))}

        def __init__(self):
            self._b = payload

        def read(self, n=-1):
            out, self._b = self._b[:n], self._b[n:]
            return out

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(model_service.urllib.request, "urlopen",
                        lambda req, timeout=60: FakeResp())
    monkeypatch.setattr(model_service, "_probe_total", lambda url, timeout=15.0: None)

    spec = model_service.find("3b")
    with pytest.raises(model_service.ModelError, match="取消"):
        model_service.download_file(spec.model, tmp_path / "m.gguf", cancel=lambda: True)


def test_download_human_size():
    assert model_service.human_size(1_930_373_216) == "1.9 GB"
    assert model_service.human_size(844_760_000) == "844.8 MB"


# ═══════════════════════════════════════════════════════ 适配器
def test_llamacpp_is_vlm_adapter():
    assert issubclass(llamacpp.LlamaServerAdapter, VLMAdapter)


def test_llamacpp_server_exe_from_env(tmp_path, monkeypatch):
    exe = tmp_path / "llama-server.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv("BOOKDB_LLAMA_SERVER", str(exe))
    assert llamacpp.find_server_exe() == exe
    assert llamacpp.is_available()


def test_llamacpp_reports_missing_binary(monkeypatch):
    monkeypatch.delenv("BOOKDB_LLAMA_SERVER", raising=False)
    monkeypatch.setattr(llamacpp, "find_server_exe", lambda: None)
    assert not llamacpp.is_available()

    adapter = llamacpp.LlamaServerAdapter(model_path="a.gguf", mmproj_path="b.gguf")
    with pytest.raises(ModelUnavailableError, match="llama-server"):
        adapter.start()


def test_llamacpp_reports_missing_model(tmp_path, monkeypatch):
    exe = tmp_path / "llama-server.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv("BOOKDB_LLAMA_SERVER", str(exe))
    adapter = llamacpp.LlamaServerAdapter(
        model_path=tmp_path / "nope.gguf", mmproj_path=tmp_path / "p.gguf")
    with pytest.raises(ModelUnavailableError, match="模型文件不存在"):
        adapter.start()


def test_llamacpp_free_port_is_unused():
    port = llamacpp._free_port()
    assert 1024 < port < 65536
    assert not llamacpp._port_is_taken(port)


def test_llamacpp_close_is_idempotent(tmp_path, monkeypatch):
    exe = tmp_path / "llama-server.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv("BOOKDB_LLAMA_SERVER", str(exe))
    adapter = llamacpp.LlamaServerAdapter(model_path="a.gguf", mmproj_path="b.gguf")
    adapter.close()          # 没启动过也不应报错
    adapter.close()


# ═══════════════════════════════════════════════════════ 适配器选择
def test_get_adapter_prefers_builtin(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(llm_service, "_builtin_adapter", lambda vlm: sentinel)
    assert llm_service.get_adapter(VlmConfig()) is sentinel


def test_get_adapter_falls_back_to_ollama(monkeypatch):
    monkeypatch.setattr(llm_service, "_builtin_adapter", lambda vlm: None)
    monkeypatch.setattr(llm_service, "_ollama_reachable", lambda ep: True)
    adapter = llm_service.get_adapter(VlmConfig())
    assert adapter.__class__.__name__ == "OllamaAdapter"


def test_get_adapter_errors_when_no_backend(monkeypatch):
    monkeypatch.setattr(llm_service, "_builtin_adapter", lambda vlm: None)
    monkeypatch.setattr(llm_service, "_ollama_reachable", lambda ep: False)
    with pytest.raises(ModelUnavailableError) as e:
        llm_service.get_adapter(VlmConfig())
    msg = str(e.value)
    assert "book model pull" in msg and "ollama serve" in msg


def test_get_adapter_builtin_forces_builtin(monkeypatch):
    monkeypatch.setattr(llm_service, "_builtin_adapter", lambda vlm: None)
    monkeypatch.setattr(llm_service, "_ollama_reachable", lambda ep: True)
    with pytest.raises(ModelUnavailableError, match="内置视觉模型"):
        llm_service.get_adapter(VlmConfig(backend="builtin"))


def test_get_adapter_ollama_forces_ollama(monkeypatch):
    monkeypatch.setattr(llm_service, "_builtin_adapter", lambda vlm: object())
    monkeypatch.setattr(llm_service, "_ollama_reachable", lambda ep: True)
    adapter = llm_service.get_adapter(VlmConfig(backend="ollama"))
    assert adapter.__class__.__name__ == "OllamaAdapter"


def test_get_adapter_builtin_none_when_binary_absent(monkeypatch):
    monkeypatch.setattr(llamacpp, "find_server_exe", lambda: None)
    assert llm_service._builtin_adapter(VlmConfig()) is None


def test_get_adapter_builtin_none_when_model_not_downloaded(monkeypatch, tmp_path):
    exe = tmp_path / "llama-server.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv("BOOKDB_LLAMA_SERVER", str(exe))
    monkeypatch.setattr(model_service, "models_dir", lambda: tmp_path / "models")
    monkeypatch.setattr(model_service, "find_ollama_model", lambda spec: None)
    assert llm_service._builtin_adapter(VlmConfig()) is None


# ═══════════════════════════════════════════════════════ 配置
def test_vlmconfig_has_backend_fields():
    vlm = VlmConfig()
    assert vlm.backend == "auto"
    assert vlm.builtin_model == "3b"


# ═══════════════════════════════════════════ 进程复用（逐页提取的关键）
def test_acquire_reuses_same_process(monkeypatch, tmp_path):
    """⚠️ 回归：同参数的 acquire() 必须复用同一个 llama-server 进程。

    修复前 `extract_metadata()` 每页都会 `get_adapter()` → 每页启一个
    llama-server（重载 2–5GB 模型），既慢又会让控制台窗口反复闪现，
    还因显存竞争导致 `failed to process mtmd chunk` 500 错误。
    """
    from bookdb.llm.adapters import llamacpp

    calls = []

    def fake_start(self):
        calls.append(id(self))
        self._proc = type("P", (), {"pid": 1234, "poll": lambda s=None: None})()

    monkeypatch.setattr(llamacpp.LlamaServerAdapter, "start", fake_start)
    llamacpp._SERVER_CACHE.clear()

    model = tmp_path / "m.gguf"; model.write_bytes(b"x")
    mmproj = tmp_path / "p.gguf"; mmproj.write_bytes(b"x")

    a1 = llamacpp.acquire(model_path=model, mmproj_path=mmproj, ctx_size=8192)
    a2 = llamacpp.acquire(model_path=model, mmproj_path=mmproj, ctx_size=8192)
    a3 = llamacpp.acquire(model_path=model, mmproj_path=mmproj, ctx_size=8192)

    assert a1 is a2 is a3, "同参数应返回同一个实例"
    assert len(calls) == 1, f"只应启动一次，实际 {len(calls)} 次"

    # 不同 ctx → 不同实例（避免参数串味）
    a4 = llamacpp.acquire(model_path=model, mmproj_path=mmproj, ctx_size=16384)
    assert a4 is not a1
    assert len(calls) == 2

    llamacpp.shutdown_all()
    assert llamacpp._SERVER_CACHE == {}


def test_acquire_rebuilds_when_process_died(monkeypatch, tmp_path):
    """进程已死 → 重新拉起（不能复用死实例）。"""
    from bookdb.llm.adapters import llamacpp

    starts = []

    def fake_start(self):
        starts.append(1)
        self._proc = type("P", (), {"pid": 999, "poll": lambda s=None: 0})()   # poll()!=None = 已退出

    monkeypatch.setattr(llamacpp.LlamaServerAdapter, "start", fake_start)
    llamacpp._SERVER_CACHE.clear()

    model = tmp_path / "m.gguf"; model.write_bytes(b"x")
    mmproj = tmp_path / "p.gguf"; mmproj.write_bytes(b"x")

    llamacpp.acquire(model_path=model, mmproj_path=mmproj)
    llamacpp.acquire(model_path=model, mmproj_path=mmproj)
    assert len(starts) == 2, "死进程应触发重启"
    llamacpp.shutdown_all()
