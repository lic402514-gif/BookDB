#!/usr/bin/env bash
# 获取 llama.cpp 推理组件（内置 AI 提取用）→ vendor/llama/
#
#     bash packaging/fetch_llama.sh            # 从本仓库 Release 附件获取（推荐）
#     bash packaging/fetch_llama.sh b11557     # 指定 build
#
# 产物：vendor/llama/（llama-server.exe + 依赖 DLL，解压后约 48 MB）
#
# ── 为什么要从「本仓库 Release」而不是 llama.cpp 上游下载 ──────────────
# ① 二进制太大（48 MB），**不进 Git 历史**（否则仓库永久膨胀、无法回收）；
# ② 上游资源在 github.com 主域，实测在部分网络下**长期超时**（12s+），
#    而 Release 附件走 objects.githubusercontent.com / api 域，可达性更好；
# ③ 上游每个 nightly 都换 build 号，直接引用会**随上游漂移**；
#    本仓库锁定一个已知可用的 build，保证构建可复现。
#
# 想换版本：先用本脚本的 `--pack` 模式打包新二进制并传为 Release 附件，
# 再更新下面的 LLAMA_BUILD。
set -euo pipefail

cd "$(dirname "$0")/.."

#: 随本仓库发布的 llama.cpp build（见 Release 附件 llama-server-<build>-win64.zip）
LLAMA_BUILD="${LLAMA_BUILD:-11557}"
REPO="lic402514-gif/BookDB"
ASSET="llama-server-${LLAMA_BUILD}-win64.zip"
DEST="vendor/llama"

if [ "${1:-}" = "--pack" ]; then
  exec bash packaging/make_llama_bundle.sh
fi
[ "$#" -ge 1 ] && [ "$1" != "--pack" ] && LLAMA_BUILD="$1" && ASSET="llama-server-${LLAMA_BUILD}-win64.zip"

echo "[1/4] 目标：$DEST（build $LLAMA_BUILD）"
if [ -f "$DEST/llama-server.exe" ]; then
  echo "  已存在，跳过。要强制重下请先删除 $DEST"
  "$DEST/llama-server.exe" --version 2>&1 | head -1 | sed 's/^/  /'
  exit 0
fi

echo "[2/4] 下载 $ASSET"
# ⚠️ 必须用 **Windows 原生路径**做临时目录：
#    MSYS 的 `mktemp -d` 返回 `/tmp/...`，而 curl 是原生程序不认，
#    会「HTTP 200 但写不出文件（size=0）」—— 踩过。
TMP=".tmp/fetch_llama"
rm -rf "$TMP"
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT

TAG="v0.2.2"

# ── 下载来源（按实测可达性排序）─────────────────────────────────────
# ⚠️ 实测：`github.com` 主域在部分网络下**长期超时**（20s+ 连接失败），
#    而 **`api.github.com` 稳定可达**（0.6s）。
#    API 的 `releases/assets/<id>` 端点配合 `Accept: application/octet-stream`
#    可直接下载附件本体 —— 这是**最可靠**的取法，故放在第一位。
TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}"
if [ -z "$TOKEN" ]; then
  # 复用 git 已缓存的凭据（本机常见），拿不到也不影响后面的公开下载源
  TOKEN=$(git credential fill <<<'protocol=https
host=github.com' 2>/dev/null | grep '^password=' | cut -d= -f2- || true)
fi

try_download() {   # $1=url  $2=额外的 curl 参数
  local url="$1"; shift
  curl -sL -m 600 --retry 2 --retry-delay 3 "$@" -o "$TMP/llama.zip" "$url" \
       -w "     HTTP %{http_code}  %{size_download} 字节\n" || true
  [ "$(stat -c%s "$TMP/llama.zip" 2>/dev/null || echo 0)" -gt 1000000 ]
}

# ① API asset 端点（最稳）
if [ -n "$TOKEN" ]; then
  echo "  ① GitHub API asset 端点…"
  asset_id=$(curl -s -m 40 -H "Authorization: token $TOKEN" \
             "https://api.github.com/repos/${REPO}/releases/tags/${TAG}" \
             | .venv/Scripts/python.exe -c "
import json,sys
try:
    for a in json.load(sys.stdin).get('assets',[]):
        if a['name'] == '${ASSET}':
            print(a['id']); break
except Exception:
    pass
" 2>/dev/null || true)
  if [ -n "$asset_id" ]; then
    try_download "https://api.github.com/repos/${REPO}/releases/assets/${asset_id}" \
      -H "Authorization: token $TOKEN" -H "Accept: application/octet-stream" && ok="$asset_id"
  fi
fi

# ② 直连 github.com（网络好时最快）
if [ -z "${ok:-}" ]; then
  echo "  ② github.com 直连…"
  try_download "https://github.com/${REPO}/releases/download/${TAG}/${ASSET}" && ok="github.com"
fi

# ③ 公共加速镜像
if [ -z "${ok:-}" ]; then
  for host in ghfast.top gh-proxy.com; do
    echo "  ③ 镜像 $host…"
    try_download "https://${host}/https://github.com/${REPO}/releases/download/${TAG}/${ASSET}" \
      && ok="$host" && break
  done
fi

if [ -z "${ok:-}" ]; then
  cat >&2 <<MSG
  ✗ 所有来源都失败。请手动下载后解压到 $DEST：
      https://github.com/${REPO}/releases/download/${TAG}/${ASSET}
  可能的原因：需要能访问 github.com 或 api.github.com。
  若已设置 GITHUB_TOKEN 环境变量会显著提高成功率。
MSG
  exit 1
fi
echo "  ✓ 下载完成（来源：$ok）"

echo "[3/4] 解压到 $DEST"
mkdir -p "$DEST"
.venv/Scripts/python.exe - "$TMP/llama.zip" "$DEST" <<'PY'
import sys, zipfile
from pathlib import Path

src, dest = Path(sys.argv[1]), Path(sys.argv[2])
with zipfile.ZipFile(src) as z:
    z.extractall(dest)
print(f"  {sum(1 for p in dest.rglob('*') if p.is_file())} 个文件")
PY

echo "[4/4] 校验"
EXE="$DEST/llama-server.exe"
if [ ! -f "$EXE" ]; then
  echo "  ✗ 未找到 llama-server.exe" >&2
  exit 1
fi
"$EXE" --version 2>&1 | head -2 | sed 's/^/  /'
du -sh "$DEST" | sed 's/^/  体积: /'

echo
echo "完成：$EXE"
echo "（打包脚本会自动带上它；开发时也可用 BOOKDB_LLAMA_SERVER 指向别处）"
