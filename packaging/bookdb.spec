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
  - `datas` 带上 `bookdb/gui/web` —— 前端资源；运行时由
    `gui/server._web_dir()` 从 `sys._MEIPASS/bookdb/gui/web` 读取。
  - `console=False` —— 双击不弹黑窗（用户明确要求）。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent          # packaging/ 的上一级 = 项目根
SRC = ROOT / "src"

webview_datas, webview_binaries, webview_hidden = collect_all("webview")

a = Analysis(
    [str(ROOT / "packaging" / "BookDB.py")],
    pathex=[str(SRC)],
    binaries=webview_binaries,
    datas=[
        *webview_datas,
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
