#!/usr/bin/env bash
# 生成 Release 附件的校验文件（SHA256SUMS.txt）。
#
#     bash packaging/make_checksums.sh
#
# 产物：dist/SHA256SUMS.txt（内容形如 "<hash>  <filename>"，供 sha256sum -c 校验）
set -euo pipefail

cd "$(dirname "$0")/.."

OUT="dist/SHA256SUMS.txt"
: > "$OUT"

found=0
for f in dist/BookDB-*-Setup.exe dist/BookDB-*-win64.zip; do
  [ -f "$f" ] || continue
  hash=$(sha256sum "$f" | cut -d' ' -f1)
  printf '%s  %s\n' "$hash" "$(basename "$f")" >> "$OUT"
  found=$((found + 1))
done

if [ "$found" -eq 0 ]; then
  echo "✗ dist/ 下没有可校验的产物（先跑 build.sh / make_zip.sh / make_installer.sh）" >&2
  exit 1
fi

echo "✓ $OUT（$found 个文件）"
cat "$OUT"
