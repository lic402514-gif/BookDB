#!/usr/bin/env bash
# 下载 llama.cpp 官方 Windows 预编译二进制（供内置推理使用）。
#
#     bash packaging/fetch_llama.sh [版本]
#
# 产物：vendor/llama/  （llama-server.exe + 依赖 DLL，CPU 版约 48MB 解压后）
#
# 为什么用官方二进制而不是 llama-cpp-python：
#   后者的预编译轮子（0.3.36）**不认识 qwen25vl 架构**，自编译要装 C++ 工具链。
#   官方 release 跟架构跟进及时，且提供 OpenAI 兼容 HTTP 接口。
set -euo pipefail

cd "$(dirname "$0")/.."

DEST="vendor/llama"
ASSET="llama-bin-win-cpu-x64.zip"

echo "[1/4] 查最新版本"
TAG="${1:-}"
if [ -z "$TAG" ]; then
  # 注：releases/latest 指向一个只含 nightly-tag.txt 的 release，
  # 真正的二进制版本要从 releases 列表取
  TAG=$(curl -sL -m 30 "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=1" \
        | .venv/Scripts/python.exe -c "import json,sys; print(json.load(sys.stdin)[0]['tag_name'])")
fi
echo "  → $TAG"

# 资产名形如 llama-b11557-bin-win-cpu-x64.zip
URL="https://github.com/ggml-org/llama.cpp/releases/download/${TAG}/llama-${TAG}-bin-win-cpu-x64.zip"

echo "[2/4] 下载 $ASSET"
mkdir -p "$DEST"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# GitHub releases 在国内经常不稳定 —— 依次尝试多个源（均为同一文件的镜像/代理）
GH="https://github.com/ggml-org/llama.cpp/releases/download/${TAG}"
SOURCES=(
  "${GH}/llama-${TAG}-bin-win-cpu-x64.zip"
  "https://ghfast.top/${GH}/llama-${TAG}-bin-win-cpu-x64.zip"
  "https://gh-proxy.com/${GH}/llama-${TAG}-bin-win-cpu-x64.zip"
  "https://hf-mirror.com/ggml-org/llama.cpp/resolve/main/llama-${TAG}-bin-win-cpu-x64.zip"
)

ok=""
for url in "${SOURCES[@]}"; do
  echo "  试：${url:0:80}…"
  if curl -sL -m 600 --retry 2 --retry-delay 2 -o "$TMP/llama.zip" "$url" \
       -w "     HTTP %{http_code}  %{size_download} 字节\n" \
       && [ -s "$TMP/llama.zip" ] && [ "$(stat -c%s "$TMP/llama.zip")" -gt 1000000 ]; then
    ok="$url"; break
  fi
done
if [ -z "$ok" ]; then
  echo "  ✗ 所有下载源都失败。请检查网络后重试，或手动下载：" >&2
  echo "     $GH/llama-${TAG}-bin-win-cpu-x64.zip" >&2
  echo "     解压到 vendor/llama/" >&2
  exit 1
fi
echo "  ✓ 来自：${ok:0:70}…"

echo "[3/4] 解压到 $DEST"
rm -rf "$DEST"
mkdir -p "$DEST"
.venv/Scripts/python.exe - "$TMP/llama.zip" "$DEST" <<'PY'
import sys, zipfile
from pathlib import Path
src, dest = Path(sys.argv[1]), Path(sys.argv[2])
with zipfile.ZipFile(src) as z:
    z.extractall(dest)
print(f"  解压 {len(list(dest.rglob('*')))} 个条目")
PY

echo "[4/4] 校验"
EXE="$DEST/llama-server.exe"
if [ ! -f "$EXE" ]; then
  echo "  ✗ 未找到 llama-server.exe" >&2
  exit 1
fi
"$EXE" --version 2>&1 | head -2
du -sh "$DEST" | sed 's/^/  体积: /'
echo
echo "完成：$EXE"
echo "（打包脚本会自动带上它；开发时也可用 BOOKDB_LLAMA_SERVER 指向别处）"
