#!/usr/bin/env bash
# 编译 BookDB Windows 安装包（Inno Setup 6）。
#
#     bash packaging/make_installer.sh
#
# 前置：
#   1) 已运行 packaging/build.sh（生成 dist/BookDB）
#   2) 已安装 Inno Setup 6 → https://jrsoftware.org/isdl.php
#
# 产物：dist/BookDB-<version>-Setup.exe
set -euo pipefail

cd "$(dirname "$0")/.."
export PYTHONPATH=""

ISCC_CANDIDATES=(
  "/c/Program Files (x86)/Inno Setup 6/ISCC.exe"
  "/c/Program Files/Inno Setup 6/ISCC.exe"
  "$LOCALAPPDATA/Programs/Inno Setup 6/ISCC.exe"
  "/d/Tools/Inno Setup 6/ISCC.exe"
  "/d/Inno Setup 6/ISCC.exe"
)

# 环境变量优先（用户可 export ISCC=... 覆盖）
if [ -n "${ISCC:-}" ]; then
  ISCC_CANDIDATES=("$ISCC" "${ISCC_CANDIDATES[@]}")
fi

echo "[1/4] 定位 ISCC.exe"
ISCC=""
for c in "${ISCC_CANDIDATES[@]}"; do
  if [ -x "$c" ]; then ISCC="$c"; break; fi
done
if [ -z "$ISCC" ]; then
  cat >&2 <<'MSG'
✗ 未找到 Inno Setup 6 的编译器 ISCC.exe。

请安装（官方、免费）：
    https://jrsoftware.org/isdl.php
下载 innosetup-6.x.x.exe，默认选项安装即可（务必勾选 Inno Setup Preprocessor）。
装完重新运行本脚本。
MSG
  exit 1
fi
echo "  ✓ $ISCC"
"$ISCC" /? >/dev/null 2>&1 || true

echo "[2/4] 校验前置产物"
if [ ! -f "dist/BookDB/BookDB.exe" ]; then
  echo "  ✗ 缺少 dist/BookDB/BookDB.exe —— 请先运行 bash packaging/build.sh" >&2
  exit 1
fi
for f in app.js index.html style.css; do
  a=$(wc -c < "src/bookdb/gui/web/$f")
  b=$(wc -c < "dist/BookDB/_internal/bookdb/gui/web/$f")
  [ "$a" = "$b" ] || { echo "  ✗ $f 与源码不一致 —— 请重新打包" >&2; exit 1; }
done
echo "  ✓ 前端资源一致"

echo "[3/4] 确保图标与 WebView2 bootstrapper 就位"
[ -f packaging/assets/bookdb.ico ] || .venv/Scripts/python.exe packaging/make_icon.py
if [ ! -f packaging/bootstrapper/MicrosoftEdgeWebview2Setup.exe ]; then
  echo "  下载 WebView2 bootstrapper（微软官方）…"
  curl -sL -m 180 -o packaging/bootstrapper/MicrosoftEdgeWebview2Setup.exe \
    "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
fi
echo "  ✓ 就位"

echo "[4/4] 编译安装包"
VERSION=$(.venv/Scripts/python.exe -c "import tomllib,pathlib;print(tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8'))['project']['version'])")
echo "  版本：$VERSION"
"$ISCC" /DAppVersion="$VERSION" packaging/installer.iss

OUT="dist/BookDB-${VERSION}-Setup.exe"
ls -la "$OUT"
echo
echo "完成：$OUT"
