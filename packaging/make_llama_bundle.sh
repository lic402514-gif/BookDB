#!/usr/bin/env bash
# 把 vendor/llama/ 打包成可分发的 zip（作为 GitHub Release 附件）。
#
#     bash packaging/make_llama_bundle.sh
#
# 产物：dist/llama-server-<build>-win64.zip
#   + 同目录的 .sha256 校验值（发布时一并贴上）
#
# 为什么要有这一步：
#   llama.cpp 的二进制（约 48 MB）**不进 Git 历史**（否则仓库永久膨胀）。
#   改为作为 Release 附件发布，`packaging/fetch_llama.sh` 从那里获取。
#   这样 GitHub 主域不通的网络也能通过 api/objects 域拿到。
set -euo pipefail

cd "$(dirname "$0")/.."
export PYTHONPATH=""

SRC="vendor/llama"
if [ ! -f "$SRC/llama-server.exe" ]; then
  echo "✗ 缺少 $SRC/llama-server.exe —— 先运行 bash packaging/fetch_llama.sh" >&2
  exit 1
fi

# 从二进制自报的版本里取 build 号（形如 "build 11557"）
BUILD=$("$SRC/llama-server.exe" --version 2>&1 \
        | sed -n 's/.*build \([0-9]\+\).*/\1/p' | head -1)
BUILD=${BUILD:-unknown}
OUT="dist/llama-server-${BUILD}-win64.zip"

echo "[1/3] 打包 $SRC → $OUT"
mkdir -p dist
.venv/Scripts/python.exe - "$SRC" "$OUT" <<'PY'
import sys, zipfile
from pathlib import Path

src, out = Path(sys.argv[1]), Path(sys.argv[2])
files = sorted(p for p in src.rglob("*") if p.is_file())
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for p in files:
        z.write(p, p.relative_to(src))
print(f"  {len(files)} 个文件，{out.stat().st_size/1e6:.1f} MB")
PY

echo "[2/3] 校验（解压后比对文件数）"
.venv/Scripts/python.exe - "$SRC" "$OUT" <<'PY'
import sys, zipfile
from pathlib import Path

src, out = Path(sys.argv[1]), Path(sys.argv[2])
with zipfile.ZipFile(out) as z:
    if z.testzip() is not None:
        raise SystemExit("✗ zip 损坏")
    n_zip = sum(1 for i in z.infolist() if not i.is_dir())
n_src = sum(1 for p in src.rglob("*") if p.is_file())
if n_zip != n_src:
    raise SystemExit(f"✗ 文件数不符：zip {n_zip} vs 源 {n_src}")
print(f"  ✓ {n_zip} 个文件，完好")
PY

echo "[3/3] 生成 SHA256"
HASH=$(sha256sum "$OUT" | cut -d' ' -f1)
printf '%s  %s\n' "$HASH" "$(basename "$OUT")" > "${OUT}.sha256"
cat "${OUT}.sha256"

echo
echo "完成：$OUT"
echo
echo "上传为 Release 附件（示例，需要 gh 或 API）："
echo "  gh release upload <tag> $OUT"
echo "或："
echo "  curl -X POST -H 'Authorization: token \$TOKEN' \\"
echo "    --data-binary @$OUT \\"
echo "    https://uploads.github.com/repos/lic402514-gif/BookDB/releases/<id>/assets?name=$(basename "$OUT")"
