#!/usr/bin/env bash
# 打「免安装绿色版」zip 分发包。
#
#     bash packaging/make_zip.sh
#
# 产物：dist/BookDB-<version>-win64.zip
#
# 内容：BookDB/ 目录（含 BookDB.exe + _internal/）+ 使用说明.txt
# 使用：解压后双击 BookDB.exe 即可，**无需安装、无需 Python 环境**。
set -euo pipefail

cd "$(dirname "$0")/.."
export PYTHONPATH=""

VERSION=$(.venv/Scripts/python.exe -c "import tomllib,pathlib;print(tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8'))['project']['version'])")
DIST_DIR="dist/BookDB"
OUT="dist/BookDB-${VERSION}-win64.zip"

if [ ! -f "$DIST_DIR/BookDB.exe" ]; then
  echo "✗ 未找到 $DIST_DIR/BookDB.exe —— 请先运行 bash packaging/build.sh" >&2
  exit 1
fi

echo "[1/3] 校验产物与源码一致"
for f in app.js index.html style.css; do
  a=$(wc -c < "src/bookdb/gui/web/$f")
  b=$(wc -c < "$DIST_DIR/_internal/bookdb/gui/web/$f")
  if [ "$a" != "$b" ]; then
    echo "  ✗ $f 不一致（源码 $a / 产物 $b）—— 请重新打包" >&2
    exit 1
  fi
done
echo "  ✓ 前端资源一致"

echo "[2/3] 写使用说明"
cat > "$DIST_DIR/使用说明.txt" <<'TXT'
BookDB —— 本地优先的个人图书元数据管理工具
================================================

【这是什么】
  一个把本地电子书整理成结构化元数据（书名/作者/出版社/年份/ISBN…）的工具。
  数据完全存在你自己的电脑上，不上传任何服务器。

【怎么用】
  1. 把整个 BookDB 文件夹解压到任意位置（例如 D:\Tools\BookDB）
  2. 双击 BookDB.exe
  3. 首次启动会显示欢迎页：
       · 「新建 Library」  → 选一个目录，BookDB 会在其中建立书库
       · 「打开已有 Library」→ 选一个含 book.toml 的目录
  4. 之后每次启动会自动打开上次用的书库

【关于「Library」（书库）】
  一个目录 = 一个书库，里面有三个东西：
     book.toml   书库配置
     book.db     书库数据库（SQLite）
     *.pdf       你的电子书
  想建几个书库都行，互不影响。备份 = 直接拷贝整个目录。

【程序与数据是分开的】
  程序   BookDB.exe（本文件夹）
  设置   %APPDATA%\BookDB\settings.json（最近打开的书库）
  模型   %APPDATA%\BookDB\models\（可选，见下）
  数据   各个 Library 目录
  删掉程序不会动你的书库数据。

【AI 辅助提取元数据（可选）】
  本程序**已内置推理引擎**，不需要再安装 Ollama 之类的软件。
  首次使用 AI 功能时，到工具栏「🧠 AI 模型」页面下载一个视觉模型即可：

     Qwen2.5-VL 3B   约 2.8 GB   推荐
     Qwen2.5-VL 7B   约 5.5 GB   质量更好，占用更大

  若你电脑上**已经装过 Ollama** 并拉取过 qwen2.5vl:3b，
  BookDB 会**自动识别并直接复用**，无需重复下载。

  不下载模型也不影响其它全部功能（图书管理、扫描建库、批量重命名等）。

【系统要求】
  Windows 10/11 64 位。需要 WebView2 运行时（Win10 1803+ / Win11 通常已自带）。
  AI 提取建议 8 GB 以上内存（用 7B 模型建议 16 GB）。

【许可证】
  GPL-3.0。本程序不提供任何担保。源码与完整许可：见项目仓库。
TXT

echo "[3/3] 打包 zip"
.venv/Scripts/python.exe - "$DIST_DIR" "$OUT" <<'PY'
import sys, zipfile
from pathlib import Path

src, out = Path(sys.argv[1]), Path(sys.argv[2])
out.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for p in sorted(src.rglob("*")):
        if p.is_file():
            z.write(p, Path(src.name) / p.relative_to(src))
print(f"  {out}  ({out.stat().st_size / 1024 / 1024:.1f} MB, "
      f"{len(zipfile.ZipFile(out).namelist())} 个文件)")
PY

echo
echo "完成：$OUT"
