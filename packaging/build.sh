#!/usr/bin/env bash
# 重新打包 BookDB.exe（P0 产品化）。
#
# ⚠️ dist/ 是**快照**，不随源码自动更新 —— 改完代码想让打包版生效，必须重跑本脚本。
#
#     bash packaging/build.sh
#
# 产物：dist/BookDB/BookDB.exe（目录版；启动快、杀软误报少）
set -euo pipefail

cd "$(dirname "$0")/.."
export PYTHONPATH=""

echo "[1/3] 打包中…"
.venv/Scripts/pyinstaller.exe packaging/bookdb.spec --noconfirm --log-level=WARN

echo
echo "[2/3] 校验前端资源已随源码更新"
SRC="src/bookdb/gui/web/app.js"
OUT="dist/BookDB/_internal/bookdb/gui/web/app.js"
src_size=$(wc -c < "$SRC")
out_size=$(wc -c < "$OUT")
echo "    源码 $src_size 字节 / 产物 $out_size 字节"
if [ "$src_size" != "$out_size" ]; then
  echo "    ✗ 大小不一致 —— 产物可能未更新！" >&2
  exit 1
fi
echo "    ✓ 一致"

echo
echo "[3/3] 完成"
ls -la dist/BookDB/BookDB.exe
echo
echo "双击运行：$(pwd)/dist/BookDB/BookDB.exe"
