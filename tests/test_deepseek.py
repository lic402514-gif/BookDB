"""DeepSeek 云端**文本**后端测试（PRD §13 的显式例外）。

覆盖四层：
  1. 适配器：Key 来源、请求体、HTTP 错误分类、拒绝读图；
  2. 配置：`[vlm] deepseek_*` 往返；
  3. 文本流程：PDF 文本层 → 元数据；扫描件明确报错；后端决定取料方式；
  4. GUI：后端切换接口与 status 信息（**不发网络请求**，只有用户点「测试连接」才发）。

所有网络调用一律 monkeypatch 掉 `urllib.request.urlopen`，测试不依赖外网。
"""

from __future__ import annotations

import io
import json
import sys
import threading
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bookdb import pdf as pdf_mod  # noqa: E402
from bookdb.config import Config, VlmConfig  # noqa: E402
from bookdb.gui import secret as secret_store  # noqa: E402
from bookdb.gui import settings as gui_settings  # noqa: E402
from bookdb.gui.server import create_server, server_url  # noqa: E402
from bookdb.llm.adapters import deepseek as deepseek_mod  # noqa: E402
from bookdb.llm.adapters.deepseek import (  # noqa: E402
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    ENV_API_KEY,
    DeepSeekAdapter,
)
from bookdb.llm.base import ModelUnavailableError  # noqa: E402
from bookdb.services import enrich as enrich_service  # noqa: E402
from bookdb.services import llm as llm_service  # noqa: E402
from bookdb.services import setup as setup_service  # noqa: E402


# ─────────────────────────────────────────────────────────── 测试替身
class _Resp:
    """urlopen 的最小替身。"""

    status = 200

    def __init__(self, payload: dict):
        self._data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _ok(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _patch_urlopen(monkeypatch, *, result: dict | None = None, error: Exception | None = None):
    """只拦截**外网**请求（DeepSeek / 代理），本机 127.0.0.1 走真实调用。

    必须这样做：GUI 测试的 HTTP 客户端也用 `urllib.request.urlopen`，
    无差别替换会把客户端自己的请求也吃掉（踩过：'_Resp' object has no attribute 'status'）。
    """
    calls: list = []
    real_urlopen = urllib.request.urlopen

    def fake(request, timeout=0):
        url = getattr(request, "full_url", str(request))
        if "127.0.0.1" in url or "localhost" in url:
            return real_urlopen(request, timeout=timeout)
        calls.append(request)
        if error is not None:
            raise error
        return _Resp(result or _ok("可用"))

    monkeypatch.setattr(deepseek_mod.urllib.request, "urlopen", fake)
    return calls


def _http_error(code: int, message: str) -> urllib.error.HTTPError:
    body = io.BytesIO(json.dumps({"error": {"message": message}}).encode("utf-8"))
    return urllib.error.HTTPError(
        "https://api.deepseek.com/chat/completions", code, message, {}, body
    )


class _FakeTextAdapter:
    """只实现 chat_text 的假适配器；记录收到的提示词。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    def chat_text(self, prompt: str, *, max_tokens: int | None = None) -> str:
        self.prompts.append(prompt)
        return self.reply

    def describe_image(self, image: bytes, prompt: str) -> str:  # pragma: no cover
        raise AssertionError("文本流程不应调用 describe_image")


def _pymupdf():
    try:
        import pymupdf

        return pymupdf
    except ImportError:  # pragma: no cover - 取决于环境
        return pytest.importorskip("fitz")


def _make_pdf(path: Path, pages: list[str]) -> None:
    fitz = _pymupdf()
    doc = fitz.open()
    for text in pages:
        doc.new_page().insert_text((72, 72), text, fontsize=12)
    doc.save(str(path))
    doc.close()


def _blank_pdf(path: Path, pages: int) -> None:
    """无文本层的 PDF（模拟扫描件）。"""
    fitz = _pymupdf()
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(str(path))
    doc.close()


def _dense_pdf(path: Path, *, pages: int = 8) -> None:
    """每页塞满文字（约 2400 字/页）—— 用于验证超长截断。"""
    fitz = _pymupdf()
    body = "\n".join(["x" * 60] * 40)
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(40, 40, 560, 800), body, fontsize=8)
    doc.save(str(path))
    doc.close()


def _config(tmp_path: Path, *, backend: str = "deepseek") -> Config:
    return Config(
        config_path=tmp_path / "book.toml",
        db_path=tmp_path / "book.db",
        library_root=tmp_path,
        vlm=VlmConfig(backend=backend),
    )


_COPYRIGHT_JSON = (
    '{"title":"否定辩证法","author":["阿多尔诺"],"translator":["王凤才"],'
    '"publisher":"商务印书馆","publication_place":"北京","publication_year":2019,'
    '"edition":null,"isbn":"9787100163180"}'
)


# ─────────────────────────────────────────────────────────────── 适配器
def test_api_key_required(monkeypatch):
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    with pytest.raises(ModelUnavailableError, match=ENV_API_KEY):
        DeepSeekAdapter()


def test_chat_text_sends_bearer_and_no_images(monkeypatch):
    monkeypatch.setenv(ENV_API_KEY, "sk-test")
    calls = _patch_urlopen(monkeypatch, result=_ok('{"title":"书"}'))

    adapter = DeepSeekAdapter()
    out = adapter.chat_text("你好")

    assert out == '{"title":"书"}'
    req = calls[0]
    assert req.full_url == f"{DEFAULT_ENDPOINT}/chat/completions"
    assert req.get_header("Authorization") == "Bearer sk-test"
    assert req.get_header("Content-type") == "application/json"

    payload = json.loads(req.data.decode("utf-8"))
    assert payload["model"] == DEFAULT_MODEL
    assert payload["messages"] == [{"role": "user", "content": "你好"}]
    assert "images" not in payload          # 纯文本：绝不带图
    assert payload["stream"] is False
    assert payload["temperature"] == 0


def test_endpoint_with_v1_suffix_still_works(monkeypatch):
    monkeypatch.setenv(ENV_API_KEY, "sk-test")
    calls = _patch_urlopen(monkeypatch, result=_ok("ok"))
    DeepSeekAdapter(endpoint="https://api.deepseek.com/v1").chat_text("x")
    assert calls[0].full_url == "https://api.deepseek.com/v1/chat/completions"


@pytest.mark.parametrize(
    "code, keyword",
    [
        (401, "认证失败"),
        (403, "认证失败"),
        (402, "余额不足"),
        (429, "过于频繁"),
        (500, "服务端错误"),
        (400, "返回错误"),
    ],
)
def test_http_errors_are_actionable(monkeypatch, code, keyword):
    monkeypatch.setenv(ENV_API_KEY, "sk-test")
    _patch_urlopen(monkeypatch, error=_http_error(code, "boom"))

    with pytest.raises(ModelUnavailableError, match=keyword) as info:
        DeepSeekAdapter().chat_text("x")
    assert "boom" in str(info.value)        # 保留服务端原始信息


def test_network_error_mentions_endpoint(monkeypatch):
    monkeypatch.setenv(ENV_API_KEY, "sk-test")
    _patch_urlopen(monkeypatch, error=urllib.error.URLError("timed out"))

    with pytest.raises(ModelUnavailableError, match="无法连接 DeepSeek"):
        DeepSeekAdapter().chat_text("x")


def test_describe_image_refuses_instead_of_pretending(monkeypatch):
    monkeypatch.setenv(ENV_API_KEY, "sk-test")
    with pytest.raises(ModelUnavailableError, match="不能读图"):
        DeepSeekAdapter().describe_image(b"\x89PNG", "提取元数据")


def test_get_adapter_deepseek(monkeypatch):
    monkeypatch.setenv(ENV_API_KEY, "sk-test")
    adapter = llm_service.get_adapter(VlmConfig(backend="deepseek"))
    assert isinstance(adapter, DeepSeekAdapter)
    assert adapter.model == DEFAULT_MODEL


def test_auto_backend_never_silently_goes_cloud(monkeypatch):
    """⚠️ 隐私底线：`auto` 只找本地，绝不因为「配了 Key」就上云。"""
    monkeypatch.setenv(ENV_API_KEY, "sk-test")
    monkeypatch.setattr("bookdb.llm.adapters.llamacpp.is_available", lambda: False)
    monkeypatch.setattr(llm_service, "_ollama_reachable", lambda endpoint: False)

    with pytest.raises(ModelUnavailableError):
        llm_service.get_adapter(VlmConfig(backend="auto"))


# ─────────────────────────────────────────────────────────────── 配置
def test_deepseek_config_roundtrip(tmp_path: Path):
    path = tmp_path / "book.toml"
    cfg = Config(config_path=path, db_path=tmp_path / "book.db")
    cfg.vlm.backend = "deepseek"
    cfg.vlm.deepseek_model = "deepseek-reasoner"
    cfg.vlm.deepseek_endpoint = "https://proxy.example/v1"
    cfg.save()

    text = path.read_text(encoding="utf-8")
    assert 'backend = "deepseek"' in text
    assert 'deepseek_model = "deepseek-reasoner"' in text
    assert "deepseek_endpoint" in text

    loaded = Config.load(path)
    assert loaded.vlm.backend == "deepseek"
    assert loaded.vlm.deepseek_model == "deepseek-reasoner"
    assert loaded.vlm.deepseek_endpoint == "https://proxy.example/v1"


def test_old_book_toml_without_deepseek_keys_keeps_defaults(tmp_path: Path):
    path = tmp_path / "book.toml"
    path.write_text(
        '[paths]\nlibrary_root = ""\ndb_path = "book.db"\n\n'
        '[vlm]\nendpoint = "http://127.0.0.1:11434"\nmodel = "qwen2.5vl:3b"\n'
        'backend = "auto"\nbuiltin_model = "3b"\n',
        encoding="utf-8",
    )
    cfg = Config.load(path)
    assert cfg.vlm.deepseek_endpoint == DEFAULT_ENDPOINT
    assert cfg.vlm.deepseek_model == DEFAULT_MODEL


# ─────────────────────────────────────────────────────────── 文本提取流程
def test_extract_metadata_from_text(monkeypatch):
    fake = _FakeTextAdapter(f"```json\n{_COPYRIGHT_JSON}\n```")
    monkeypatch.setattr(llm_service, "get_adapter", lambda vlm: fake)

    meta = llm_service.extract_metadata_from_text(VlmConfig(), "商务印书馆 2019 ISBN ...")

    assert meta["title"] == "否定辩证法"
    assert meta["isbn"] == "9787100163180"
    assert meta["edition"] is None
    assert "版权页" in fake.prompts[0]        # 提示词强调只认版权页
    assert "商务印书馆" in fake.prompts[0]     # 原文确实带进了提示词


def test_extract_metadata_from_text_rejects_empty():
    with pytest.raises(ValueError, match="没有可用的文字"):
        llm_service.extract_metadata_from_text(VlmConfig(), "   \n  ")


def test_read_pages_text_skips_blank_pages(tmp_path: Path):
    path = tmp_path / "book.pdf"
    _make_pdf(path, ["封面", "", "ISBN 9787100163180"])
    pages = pdf_mod.read_pages_text(path, pages=5)
    assert [no for no, _ in pages] == [1, 3]          # 空白页被跳过
    assert "ISBN" in pages[1][1]


def test_text_enrich_extracts_metadata(tmp_path: Path, monkeypatch):
    path = tmp_path / "book.pdf"
    _make_pdf(path, ["否定辩证法", "阿多尔诺 著", "商务印书馆 2019 ISBN 978-7-100-16318-0"])

    fake = _FakeTextAdapter(_COPYRIGHT_JSON)
    monkeypatch.setattr(llm_service, "get_adapter", lambda vlm: fake)

    result = enrich_service.enrich_from_pdf(_config(tmp_path), path)

    assert result.source == "pdf-text"
    assert result.page == 3                            # 文本层定位到版权页
    assert result.metadata["title"] == "否定辩证法"
    assert result.metadata["file_path"] == str(path)
    assert result.pages_scanned == [1, 2, 3]


def test_text_enrich_never_renders_pages(tmp_path: Path, monkeypatch):
    """文字流程不得偷偷渲染页面（那会触发视觉模型 / 无谓的 CPU 开销）。"""
    path = tmp_path / "book.pdf"
    _make_pdf(path, ["ISBN 9787100163180"])

    def boom(*args, **kwargs):  # pragma: no cover - 被调用即失败
        raise AssertionError("文字流程不应渲染 PDF 页面")

    monkeypatch.setattr(enrich_service.pdf_mod, "render_page_png", boom)
    monkeypatch.setattr(llm_service, "get_adapter", lambda vlm: _FakeTextAdapter(_COPYRIGHT_JSON))

    assert enrich_service.enrich_from_pdf(_config(tmp_path), path).metadata["title"]


def test_scanned_pdf_gives_actionable_error(tmp_path: Path, monkeypatch):
    path = tmp_path / "scan.pdf"
    _blank_pdf(path, 3)
    monkeypatch.setattr(llm_service, "get_adapter", lambda vlm: _FakeTextAdapter("{}"))

    with pytest.raises(enrich_service.NoTextLayer, match="没有文本层"):
        enrich_service.enrich_from_pdf(_config(tmp_path), path)


def test_forced_image_mode_with_deepseek_is_rejected(tmp_path: Path):
    path = tmp_path / "book.pdf"
    _make_pdf(path, ["ISBN 9787100163180"])

    with pytest.raises(ModelUnavailableError, match="不能读图"):
        enrich_service.enrich_from_pdf(_config(tmp_path), path, mode="image")


def test_forced_text_mode_works_with_local_backend(tmp_path: Path, monkeypatch):
    """本地多模态模型也支持纯文本 —— `--text` 不必先切 DeepSeek。"""
    path = tmp_path / "book.pdf"
    _make_pdf(path, ["ISBN 9787100163180"])
    fake = _FakeTextAdapter(_COPYRIGHT_JSON)
    monkeypatch.setattr(llm_service, "get_adapter", lambda vlm: fake)

    result = enrich_service.enrich_from_pdf(_config(tmp_path, backend="auto"), path, mode="text")
    assert result.source == "pdf-text"


def test_text_chars_are_capped(tmp_path: Path, monkeypatch):
    path = tmp_path / "big.pdf"
    _dense_pdf(path, pages=8)                       # 约 1.9 万字，超过上限
    fake = _FakeTextAdapter(_COPYRIGHT_JSON)
    monkeypatch.setattr(llm_service, "get_adapter", lambda vlm: fake)

    enrich_service.enrich_from_pdf(_config(tmp_path), path)
    sent = fake.prompts[0]
    assert "x" * 60 in sent
    assert len(sent) <= len(llm_service.PROMPT_TEXT) + enrich_service.MAX_TEXT_CHARS + 100
    assert "【第 8 页】" not in sent                  # 截断后根本没送到最后一页


# ────────────────────────────────────────────────────────── GUI 接口
class _Api:
    def __init__(self, server):
        self.url = server_url(server).rstrip("/")

    def call(self, method: str, path: str, body=None):
        import urllib.request

        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.url}{path}", data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())


@pytest.fixture()
def api(tmp_path: Path):
    root = tmp_path / "Library"
    root.mkdir()
    cfg = Config(config_path=root / "book.toml", library_root=root, db_path=root / "book.db")
    setup_service.initialize(cfg)
    cfg.save(base=root)

    server = create_server(cfg, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield _Api(server), cfg
    finally:
        server.shutdown()
        server.server_close()


def test_status_reports_deepseek_without_network(api, monkeypatch):
    """status 只报「Key 是否已配置」，不得探测 DeepSeek、不得弹 Key 缺失错误。"""
    from bookdb.services import llm as llm_service

    # 屏蔽本地后端探测：本机 Ollama 里可能有模型，status 不应因此真的拉起 llama-server
    monkeypatch.setattr(llm_service, "_builtin_adapter", lambda vlm, **kw: None)
    monkeypatch.setattr(llm_service, "_ollama_reachable", lambda ep: False)

    calls = _patch_urlopen(monkeypatch, result=_ok("可用"))
    client, _cfg = api

    status, data = client.call("GET", "/api/model/status")
    assert status == 200, data
    assert data["deepseek"]["configured"] is False
    assert data["deepseek"]["model"] == DEFAULT_MODEL

    # 本机 Ollama 探测可以有；但**绝不能**打 DeepSeek 的接口
    assert not [c for c in calls if "deepseek" in c.full_url]


def test_switch_backend_writes_book_toml(api):
    client, cfg = api
    status, data = client.call("POST", "/api/model/backend", {"backend": "deepseek"})
    assert status == 200 and data["backend"] == "deepseek"

    reloaded = Config.load(cfg.config_path)
    assert reloaded.vlm.backend == "deepseek"


def test_switch_backend_rejects_unknown_value(api):
    client, _cfg = api
    status, data = client.call("POST", "/api/model/backend", {"backend": "gpt-5"})
    assert status == 400 and "未知后端" in data["error"]


def test_deepseek_test_endpoint_without_key(api):
    client, _cfg = api
    status, data = client.call("POST", "/api/llm/deepseek/test")
    assert status == 400
    assert ENV_API_KEY in data["error"]


def test_deepseek_test_endpoint_with_key(api, monkeypatch):
    monkeypatch.setenv(ENV_API_KEY, "sk-test")
    _patch_urlopen(monkeypatch, result=_ok("可用"))
    client, _cfg = api

    status, data = client.call("POST", "/api/llm/deepseek/test")
    assert status == 200
    assert data["ok"] is True and data["model"] == DEFAULT_MODEL


def test_status_after_switch_reports_hint(api):
    client, _cfg = api
    client.call("POST", "/api/model/backend", {"backend": "deepseek"})

    status, data = client.call("GET", "/api/model/status")
    assert status == 200
    assert data["backend"] == "deepseek"
    assert data["active"] is None            # 没有 Key → 不可用
    assert ENV_API_KEY in (data["hint"] or "")


# ─────────────────────────────────── GUI 填 Key（DPAPI 加密，明文不落盘）
needs_dpapi = pytest.mark.skipif(
    not secret_store.available(), reason="本机加密仅 Windows DPAPI 可用"
)


@needs_dpapi
def test_secret_roundtrip_hides_plaintext():
    token = secret_store.protect("sk-roundtrip-abcdef")
    assert token.startswith(secret_store.PREFIX)
    assert "sk-roundtrip" not in token          # 密文里看不到明文
    assert secret_store.unprotect(token) == "sk-roundtrip-abcdef"


@needs_dpapi
def test_settings_persist_key_as_ciphertext(tmp_path: Path):
    path = tmp_path / "settings.json"
    st = gui_settings.Settings(path=path)
    st.set_deepseek_key("sk-plain-secret-123456")
    st.save()

    text = path.read_text(encoding="utf-8")
    assert "sk-plain-secret" not in text        # 明文绝不落盘
    assert secret_store.PREFIX in text

    loaded = gui_settings.Settings.load(path)
    assert loaded.has_deepseek_key
    assert loaded.deepseek_key() == "sk-plain-secret-123456"

    loaded.clear_deepseek_key()
    loaded.save()
    assert gui_settings.Settings.load(path).deepseek_key() is None


def test_corrupt_saved_key_reads_as_missing(tmp_path: Path):
    """换了 Windows 账户 / 文件被改坏：按「未配置」处理，不崩。"""
    path = tmp_path / "settings.json"
    path.write_text('{"deepseek_api_key": "dpapi-v1:@@not-base64@@"}', encoding="utf-8")
    st = gui_settings.Settings.load(path)
    assert st.has_deepseek_key                  # 确实存过
    assert st.deepseek_key() is None            # 但解不开 → 当没有


@needs_dpapi
def test_gui_save_key_masks_and_never_returns_plaintext(api):
    client, _cfg = api
    key = "sk-gui-secret-abcdef123456"

    status, data = client.call("POST", "/api/llm/deepseek/key", {"key": key})
    assert status == 200
    assert data["configured"] is True and data["source"] == "file"
    assert key not in json.dumps(data)          # 响应里不得有完整 Key
    assert data["masked"].startswith("sk-gu") and data["masked"].endswith("3456")

    # 磁盘上也必须是密文
    text = gui_settings.settings_path().read_text(encoding="utf-8")
    assert key not in text

    _s, st = client.call("GET", "/api/model/status")
    assert st["deepseek"]["configured"] is True
    assert st["deepseek"]["source"] == "file"
    assert st["deepseek"]["stored"] is True
    assert st["deepseek"]["storage"] == "dpapi"

    # 清除
    status, data = client.call("POST", "/api/llm/deepseek/key", {"key": ""})
    assert status == 200 and data["cleared"] is True
    assert data["configured"] is False
    _s, st = client.call("GET", "/api/model/status")
    assert st["deepseek"]["configured"] is False
    assert st["deepseek"]["stored"] is False


@needs_dpapi
def test_env_key_wins_over_saved_key(api, monkeypatch):
    client, _cfg = api
    client.call("POST", "/api/llm/deepseek/key", {"key": "sk-file-key-000000"})

    monkeypatch.setenv(ENV_API_KEY, "sk-env-key-111111")
    _s, st = client.call("GET", "/api/model/status")
    assert st["deepseek"]["source"] == "env"
    assert st["deepseek"]["masked"].endswith("1111")


@needs_dpapi
def test_get_adapter_uses_saved_key_without_env():
    st = gui_settings.Settings()
    st.set_deepseek_key("sk-saved-only-123456")
    st.save()

    adapter = llm_service.get_adapter(VlmConfig(backend="deepseek"))
    assert isinstance(adapter, DeepSeekAdapter)


def test_gui_rejects_too_short_key(api):
    client, _cfg = api
    status, data = client.call("POST", "/api/llm/deepseek/key", {"key": "sk-1"})
    assert status == 400 and "太短" in data["error"]


def test_stale_ciphertext_shows_as_unconfigured(api):
    """存过密文但解不开（换账户）：界面应提示「重新填写」，而不是假装可用。"""
    path = gui_settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"deepseek_api_key": "dpapi-v1:broken"}', encoding="utf-8")

    client, _cfg = api
    _s, st = client.call("GET", "/api/model/status")
    assert st["deepseek"]["configured"] is False
    assert st["deepseek"]["stored"] is True
