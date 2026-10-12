# BookDB

本地优先的**个人图书元数据管理工具** —— 把你电脑里的电子书整理成结构化的书目数据
（书名 / 作者 / 译者 / 出版社 / 年份 / ISBN / CLC 分类…）。

数据**完全存在你自己的电脑上**，不联网、不上传、不需要注册。

---

## ⬇️ 下载安装（普通用户）

**支持 Windows 10 / 11（64 位）。不需要 Python、uv、pip、Git 或任何其他环境。**

### 方式一：安装版（推荐）

1. 打开 **[最新版本 Releases](../../releases/latest)**
2. 下载 **`BookDB-0.1.0-Setup.exe`**
3. **双击**运行，一路「下一步」→「完成」
4. 从**开始菜单**或**桌面快捷方式**启动 BookDB
5. 首次启动会看到欢迎页 → 点「**新建 Library**」，选一个目录存放你的电子书

> 安装过程无需管理员权限；界面所需的 Microsoft WebView2 运行环境
> 若缺失，安装程序会**自动处理**，你不必做任何额外配置。

### 🤖 AI 提取元数据（可选）

BookDB **内置了 AI 推理引擎**，你**不需要**安装 Ollama 或任何其他 AI 软件。

需要用「版权页提取」或「批量补全」时，打开工具栏的 **🧠 AI 模型**，下载一个即可：

| 模型 | 体积 | 说明 |
|---|---|---|
| **Qwen2.5-VL 3B** | 约 2.8 GB | 推荐 |
| **Qwen2.5-VL 7B** | 约 5.5 GB | 质量更好 |

- **已经装过 Ollama**？若你拉取过 `qwen2.5vl:3b`，BookDB 会**自动识别并复用**，零下载。
- 不下载也**不影响其它全部功能**（图书管理、扫描建库、批量重命名、导入导出…）。

### 方式二：便携版（免安装）

1. 下载 **`BookDB-0.1.0-win64.zip`**
2. 解压到任意位置（例如 `D:\Tools\BookDB`）
3. 双击其中的 **`BookDB.exe`**

适合放在 U 盘或不想安装的场景。

> 想校验下载是否完整？Release 里附有 `SHA256SUMS.txt`，可执行
> `sha256sum -c SHA256SUMS.txt`，或在 PowerShell 里
> `Get-FileHash .\BookDB-0.1.0-Setup.exe -Algorithm SHA256` 后手工比对。

### 🔒 你的数据在哪

BookDB 把「程序」和「你的书库」**彻底分开**：

| | 位置 | 说明 |
|---|---|---|
| **程序** | 安装目录（方式一）或你解压的目录（方式二）| —— |
| **书库** | **由你在首次启动时自己选** | 一个目录 = 一个书库，含 `book.db` + 你的电子书 |
| 应用设置 | `%APPDATA%\BookDB\settings.json` | 只记「最近打开的书库」，不含书籍数据 |

- **卸载 BookDB 不会删除你的书库数据**；
- 想备份？直接拷贝整个书库目录即可；
- 想建多个书库（如「藏书」「论文」）？各自选目录，互不影响。

---

## 从源码运行（开发者）

> 以下内容**只对想改代码的人**有意义。普通用户请用上面的安装包。

### 特性

- **图书管理**：`init` / `add` / `list` / `show` / `edit` / `delete`
- **扫描建库**：`book scan <dir>` 扫描目录（递归），从**文件名**解析书名 / 作者 / 版次并批量建记录；先预览后写入
- **批量补全**：`book enrich --missing` / `--dir <目录>` 逐本跑本地 VLM 读版权页，补齐作者 / 出版社 / 年份 / ISBN；先复核后写入，**幂等**（补过的不再重跑）
- **检索**：关键词 + 作者 / 年份区间 / CLC 前缀 / 文献类型 / 出版社 / 是否有文件
- **数据交换**：JSON / CSV 导入导出 + 字段级 patch（显式 `mode`、变更预览、NULL 语义）
- **文件重命名**：按元数据生成规范化文件名，`--dry-run` 预览 / `--apply` 执行，同步更新库内路径
- **引用输出**：GB/T 7714-2015 专著（`book cite`）；版本项按国标 —— 第 1 版不著录，其余写作 `2版`
- **版权页提取**：本地 VLM 从版权页图像提取元数据，人工确认后写入；**支持扫描件**（无文本层时逐页渲染喂 VLM）。**内置 llama.cpp 推理引擎**，也兼容已有 Ollama
- **桌面 GUI**：pywebview + 原生 HTML/CSS/JS，内部仅 127.0.0.1 的 HTTP 端点，不对外暴露
- **独立桌面入口**：`BookDB` 自带 GUI 入口，不经 CLI；程序、应用设置、用户数据三者分离
- **Library 管理**：GUI 自己维护「当前书库」——首次启动显示欢迎页（新建 / 打开 / 最近使用），自动打开上次书库，支持运行中切换
- **多选 + 批量操作**：列表多选（复选框 / Ctrl / Shift / 全选）后批量**重命名 / 移动 / 删除元数据 / 连文件删除**；一律**先预览后执行**，删除文件**送回收站**（可恢复）
- **打开文件**：GUI 里一键用系统默认程序打开关联的电子书 / 定位到所在文件夹（不内嵌阅读器）
- **字段规范化**：版次存纯数字（`第2版`→`2`）、ISBN 去连字符、年份容错
- **疑似重复检测**：ISBN 优先，无则 书名+作者+年份
- **文件路径**：绝对路径原样 / 相对路径按 `library_root` 解析
- 纯标准库 `sqlite3`，**无 ORM**

### 开发环境

```bash
git clone https://github.com/lic402514-gif/BookDB.git
cd BookDB
uv venv
uv pip install -e ".[gui,pdf,dev]"    # 只要 CLI 可省去 gui/pdf
uv run pytest -q                       # 185 passed
```

### 打包（生成 Release 产物）

```bash
uv pip install -e ".[build]"
bash packaging/fetch_llama.sh    # 取内置推理组件 → vendor/llama/（约 20 MB 下载）
bash packaging/build.sh          # → dist/BookDB/BookDB.exe
bash packaging/make_zip.sh       # → dist/BookDB-<版本>-win64.zip（便携版）
bash packaging/make_installer.sh # → dist/BookDB-<版本>-Setup.exe（需 Inno Setup 6）
bash packaging/make_checksums.sh # → dist/SHA256SUMS.txt
```

安装包脚本见 `packaging/installer.iss`；图标由 `packaging/make_icon.py` 用标准库生成。

> **llama.cpp 二进制从哪来？** 它约 48 MB（解压后），**不进 Git 历史**
> （否则仓库永久膨胀）。已作为 Release 附件发布（锁定 build ，
> 保证构建可复现）；`fetch_llama.sh` 会自动取用，依次尝试
> GitHub API 端点 → github.com 直连 → 公共镜像。
> 若网络受限，可设 `GITHUB_TOKEN` 环境变量提高成功率。
>
> 维护者换版本：`bash packaging/make_llama_bundle.sh` 打包当前二进制，
> 传为新 Release 附件，再更新 `fetch_llama.sh` 里的 `LLAMA_BUILD`。

### CLI 用法

```bash
# 1. 初始化（数据库 + book.toml）
book init --library-root "D:/Books"

# 2. 添加一本
book add --title "否定辩证法" --author "阿多尔诺" --translator "王凤才" \
         --publisher "商务印书馆" --place "北京" --year 2019 --edition "1" \
         --isbn "9787100163180" --clc "B0" --file "D:/Books/否定辩证法.pdf"

# 3. 查看 / 列表
book show 1
book list

# 4. 检索
book search "辩证法" --author "阿多尔诺" --year 1900-2000 --clc B

# 5. 编辑 / 删除
book edit 1 --clc "B516"
book delete 1

# 6. 导出 / 导入
book export --format json --pretty -o books.json
book export --format csv  -o books.csv
book import books.json          # 先预览变更，确认后写入
book import books.csv  --yes    # CSV 固定 full 语义

# 7. 重命名 / 引用
book rename 1 --dry-run         # 预览新文件名
book rename 1 --apply           # 执行（同步更新库内路径）
book cite 1                     # 输出 GB/T 7714 引用

# 8. 版权页提取（需本机 Ollama + 视觉模型，见下）
book enrich --pdf "book.pdf"           # 自动定位版权页
book enrich --pdf "book.pdf" --page 5  # 手动指定页
book enrich --image "copyright.jpg"    # 手动指定图片

# 9. 扫描目录批量建库（按文件名解析元数据；默认只预览）
book scan "D:/Books" --recursive        # 预览：新建 / 已入库 / 疑似重复
book scan "D:/Books" --apply --yes      # 确认后写库

# 10. 批量补全元数据（VLM 逐本读版权页，补齐作者 / 出版社 / 年份 / ISBN）
book enrich --missing -y                # 全库：所有缺元数据的记录
book enrich --dir "D:/Books" -y         # 只处理该目录内的记录
book enrich --missing --limit 10        # 先试 10 条
book enrich --missing --no-overwrite    # 只填空缺，不覆盖已有值

# 11. 桌面 GUI（列表 / 搜索 / 详情 / 编辑 / 导入导出 / 重命名 / 引用 / 版权页提取 / 扫描建库 / 批量补全）
book gui
```

## 桌面入口与 Library（P0 产品化）

普通用户**不需要** CLI，也不需要理解「工作目录」：

```bash
BookDB                              # 开发期（打包后为 BookDB.exe）
BookDB "E:/Books"                   # 指定启动时打开哪个书库
```

启动优先级：

```
BookDB.exe "path"   → 打开该 Library
BookDB.exe          → 打开「上次使用的 Library」
                    → 没有 / 已失效 → 欢迎页（新建 / 打开已有 / 最近使用）
```

三者**彻底分离**，互不影响：

| | 位置 | 说明 |
|---|---|---|
| **程序本体** | `BookDB.exe`（打包后） | 不依赖 Python / venv / 源码目录 |
| **应用设置** | `%APPDATA%\BookDB\settings.json` | 最近书库、上次书库。可用 `BOOKDB_SETTINGS_DIR` 覆盖 |
| **用户数据** | `E:\Books\`（各 Library 目录） | `book.toml` + `book.db` + 电子书 |

- **一个目录 = 一个 Library**，可建任意多个，彼此完全隔离；
- 应用设置里**只存偏好与历史**，**不存**任何 Library 配置（仍在各自的 `book.toml`）；
- **CLI 不变**：仍按「当前工作目录」发现 `book.toml`，适合脚本化。

## 批量操作（GUI 多选）

列表支持多选（**复选框** / `Ctrl+点击` 加选 / `Shift+点击` 区间 / 表头全选），
选中后出现批量操作条：

| 操作 | 动什么 | 风险 |
|---|---|---|
| ✎ 批量重命名 | 文件名 + `file_path`（按 §14.1 模板规范化）| 低（可改回）|
| 📦 批量移动 | 文件位置 + `file_path`（**保留原文件名**）| 中 |
| 🗑 删除元数据 | 只删数据库记录，**文件保留** | 中高（不可逆）|
| 💥 连文件删除 | 文件 + 记录（**送回收站**，可恢复）| 最高 |

四条一致的安全约定：

- **先预览后执行** —— 逐条列出「旧→新 / 状态 / 文件大小」，可勾选子集；
- **冲突不自动覆盖** —— 目标已存在、批次内同名、连锁改名 → 预览里标记并默认跳过；
- **逐条独立** —— 一本失败不影响其余，逐条回报结果；
- **删除需显式确认** —— 前端要勾确认框，**后端 `confirm` 参数缺失直接 400**（第二道闸）；
  删文件一律经 `send2trash` 送**回收站**，未安装则**明确报错而非永久删除**。

> 批量移动后若文件**移出 `library_root`**，库内 `file_path` 会自动改记为绝对路径（§6.1 语义）。

## 导入模式（§9.2）

JSON 顶层需显式声明 `mode`：

```json
{"mode": "full",  "items": [{"title": "...", "author": "..."}]}
{"mode": "patch", "items": [{"id": 123, "clc_code": "B0", "abstract": null}]}
```

- `full`：`id` 命中则更新、否则新增；
- `patch`：仅修改出现的字段，`null` 清空、缺失保持；
- 非法字段 / 缺失 `mode` / 未知 `id` → 拒绝导入，不写入任何数据。

## 配置（book.toml）

```toml
[paths]
library_root = "D:/Books"   # 相对路径的根目录
db_path = "book.db"

[vlm]
endpoint = "http://127.0.0.1:11434"
model = "qwen2.5vl:3b"
```

## 本地模型（版权页提取）

`book enrich` 有两种后端，**默认 `auto`**（内置可用就用内置，否则回退到已启动的 Ollama）：

```bash
# ① 内置（推荐，不依赖 Ollama）
bash packaging/fetch_llama.sh   # 获取 llama.cpp 二进制 → vendor/llama/
book model pull                 # 下载 Qwen2.5-VL 3B（约 2.8 GB）
# 若本机 Ollama 已有 qwen2.5vl:3b，会自动复用，无需下载

# ② 或者用你已有的 Ollama
ollama serve
ollama pull qwen2.5vl:3b

# 无论哪种，都需要 PDF 依赖
uv pip install -e ".[pdf]"
```

管理命令：

```bash
book model list          # 可选的模型 + 本机状态（已下载 / 复用 Ollama / 未安装）
book model status        # 当前实际会用哪个后端
book model pull 7b       # 下载更大的 7B（质量优先）
book model remove 3b     # 删除本程序下载的（不动 Ollama 里的）
```

后端由 `book.toml` 的 `[vlm].backend` 决定：`auto`（默认）/ `builtin` / `ollama`。

模型名 / 端点写在 `book.toml` 的 `[vlm]`，不硬编码；换成其他推理框架只需新增一个适配器。
**模型不可用时 `book enrich` 会明确提示并退出，其余功能不受影响。**

## 检索年份语法（§8.2）

| 写法 | 含义 |
|---|---|
| `--year 2019` | 单年 |
| `--year 1800-1900` | 闭区间（含两端） |
| `--year 1800-` | 起年及以后 |

## 开发状态

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M1 | 数据库与 CLI（init/add/list/show/edit/delete/search） | ✅ |
| M2 | JSON / CSV 导入导出 + 字段级 patch（显式 mode、变更预览、NULL 语义） | ✅ |
| M3 | 文件重命名 + GB/T 7714 引用（共用 `format.py`） | ✅ |
| M4 | 本地 VLM 版权页提取（Ollama 适配） | ✅ |
| M5 | pywebview GUI（内部 HTTP 端点） | ✅ |
| 扩展 | 扫描目录批量建库（`book scan`，按文件名解析） | ✅ |

完整需求见 [`docs/PRD.md`](docs/PRD.md)。

## 许可证

[GPL-3.0](LICENSE) © 2026 lic402514-gif

本程序为自由软件：你可以依据自由软件基金会发布的 **GNU 通用公共许可证第 3 版**
（或你选择的任何更新版本）条款重新发布和/或修改它。

本程序分发时希望它有用，但**不提供任何担保**，甚至不包含适销性或特定用途适用性的默示担保。
详见 [LICENSE](LICENSE)。
