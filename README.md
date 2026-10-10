# bookdb

本地优先、单用户的**个人图书元数据数据库工具**。SQLite + CLI，可选桌面 WebView。

> 定位：一个小型、可靠、本地优先的个人图书元数据库。
> 核心保持简单，复杂能力（CLC 分类、摘要生成等）通过 JSON/CSV 标准接口向外扩展。

## 特性（M1 已完成）

- **图书管理**：`init` / `add` / `list` / `show` / `edit` / `delete`
- **检索**：关键词 + 作者 / 年份区间 / CLC 前缀 / 文献类型 / 出版社 / 是否有文件
- **字段规范化**：版次存纯数字（`第2版`→`2`）、ISBN 去连字符、年份容错
- **疑似重复检测**：ISBN 优先，无则 书名+作者+年份
- **文件路径**：绝对路径原样 / 相对路径按 `library_root` 解析
- 纯标准库 `sqlite3`，**无 ORM**

## 安装

```bash
cd D:/Projects/book_metadata_tool
uv venv
uv pip install -e .
```

## 快速开始

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
```

## 配置（book.toml）

```toml
[paths]
library_root = "D:/Books"   # 相对路径的根目录
db_path = "book.db"

[vlm]
endpoint = "http://127.0.0.1:11434"
model = "qwen2.5-vl:3b"
```

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
| M2 | JSON / CSV 导入导出 + 字段级 patch | ⏳ |
| M3 | 文件重命名 + GB/T 7714 引用（共用 `format.py`） | ⏳ |
| M4 | 本地 VLM 版权页提取（Ollama 适配） | ⏳ |
| M5 | pywebview GUI | ⏳ |

完整需求见 [`docs/PRD.md`](docs/PRD.md)。

## 测试

```bash
uv run pytest -q
```
