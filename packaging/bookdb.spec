# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（P0 产品化）。

    pyinstaller packaging/bookdb.spec --noconfirm

产物：`dist/BookDB/BookDB.exe`（**目录版 `--onedir`**）

为什么用目录版而不是 `--onefile`：
  - `--onefile` 每次启动都要把整个运行时解压到临时目录 → 启动慢，且杀软误报多；
  - 目录版启动快、行为可预期，配合安装器即可。

打包要点（踩过的坑都在这）：
  - `pathex=[src]` —— 直接从**源码**找 `bookdb` 包，不依赖 editable 安装的
    路径 hook（PyInstaller 对 editable 安装的解析并不可靠）。
  - `collect_all("webview")` —— pywebview 需要它的 JS/平台模块等包内数据，
    否则运行时找不到后端。
  - ⚠️ **必须补齐 pywebview 三个架构的 WebView2Loader.dll**：
    `webview/platforms/edgechromium.py` 会**无条件**遍历
    `('win-arm64', 'win-x64', 'win-x86')` 逐个调用 `interop_dll_path()`，
    任一路径不存在就抛 `FileNotFoundError: Cannot find win-<arch>`。
    而 PyInstaller 默认**只收集当前架构**的 DLL（x64 机器只留 win-x64），
    导致 x64 机器上启动即崩。三个 DLL 加起来仅 ~360 KB，索性全带上。
  - `datas` 带上 `bookdb/gui/web` —— 前端资源；运行时由
    `gui/server._web_dir()` 从 `sys._MEIPASS/bookdb/gui/web` 读取。
  - `console=False` —— 双击不弹黑窗（用户明确要求）。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, get_package_paths

ROOT = Path(SPECPATH).parent          # packaging/ 的上一级 = 项目根
SRC = ROOT / "src"

webview_datas, webview_binaries, webview_hidden = collect_all("webview")

# ── 内置推理组件 llama.cpp（见 vendor/ 说明）────────────────────────────
#: `vendor/llama/` 由 packaging/fetch_llama.sh 获取（48MB，不入 Git）。
#: 打进 `_internal/llama/`，运行时由 `llamacpp.find_server_exe()` 从
#: `sys._MEIPASS/llama/llama-server.exe` 找到 —— 用户无需再装 Ollama。
_llama_dir = ROOT / "vendor" / "llama"
if not (_llama_dir / "llama-server.exe").is_file():
    raise SystemExit(
        "缺少内置推理组件：vendor/llama/llama-server.exe\n"
        "请先运行： bash packaging/fetch_llama.sh"
    )
llama_datas = [(str(_llama_dir), "llama")]

# ── 补齐三个架构的 WebView2Loader.dll（见文件头说明）────────────────────
_, _wv_pkg = get_package_paths("webview")
_runtimes = Path(_wv_pkg) / "lib" / "runtimes"
webview_arch_datas = [
    (str(_runtimes / arch / "native" / "WebView2Loader.dll"),
     f"webview/lib/runtimes/{arch}/native")
    for arch in ("win-arm64", "win-x64", "win-x86")
    if (_runtimes / arch / "native" / "WebView2Loader.dll").is_file()
]

a = Analysis(
    [str(ROOT / "packaging" / "BookDB.py")],
    pathex=[str(SRC)],
    binaries=webview_binaries,
    datas=[
        *webview_datas,
        *webview_arch_datas,
        *llama_datas,
        (str(SRC / "bookdb" / "gui" / "web"), "bookdb/gui/web"),
    ],
    hiddenimports=[
        *webview_hidden,
        # Windows 后端（WebView2 / Edge）与 .NET 互操作
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "clr_loader",
        "pythonnet",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 明确不用的重型依赖，避免无谓体积
    excludes=[
        "matplotlib", "numpy", "pandas", "scipy",
        "IPython", "pytest", "tkinter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BookDB",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                    # ★ 无控制台窗口
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="BookDB",
)
