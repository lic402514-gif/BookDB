# 图书元数据管理与索引软件 PRD

**版本：** v0.4
**日期：** 2026-10-10
**状态：** 初稿（已定稿：① GUI 内部传输层；② 纯 VLM + Ollama 适配；③ 数据访问去 ORM；④ edition/年份/摘要约定；⑤ 路径双模式 + `library_root`；⑥ rename/cite 共用 `format.py`；⑦ 导入显式 `mode`；⑧ 版权页定位 30 页上限）
**项目形态：** 本地优先、单用户、SQLite 数据库 + CLI + 可选桌面 WebView

------

# 1. 项目概述

## 1.1 项目定位

本项目是一个**本地优先的个人图书元数据数据库工具**，用于管理当前计算机上的电子书及其元数据。

核心职责只有：

1. 保存图书元数据；
2. 查询、编辑和维护图书记录；
3. 关联本地电子书文件；
4. 根据元数据生成规范化文件名；
5. 输出 GB/T 7714 引用；
6. 通过 CSV / JSON 与外部程序交换数据；
7. 使用一个可选的本地视觉语言模型（VLM）从版权页提取结构化元数据。

项目**不以 AI 为核心，不试图成为在线书库、阅读器或复杂的文献管理平台**。

------

# 2. 设计原则

## 2.1 大道至简

系统只负责自己擅长的事情：

> **可靠地存储、查询、修改和交换图书元数据。**

复杂的理解、分类、批处理和知识处理工作可以交给外部工具。

例如：

```text
SQLite
  ↓
export JSON / CSV
  ↓
外部 AI / Python / 人工处理
  ↓
JSON
  ↓
import
  ↓
SQLite
```

系统不要求知道外部 AI 是什么。

------

## 2.2 数据库优先

SQLite 是项目的核心数据源。

CLI 和 GUI 都只能通过统一的核心业务层访问数据库：

```text
CLI ─────┐
         ├── Core / Service Layer ── SQLite
WebView ─┘
```

CLI 和 GUI 不得各自实现一套业务逻辑。

------

## 2.3 AI 可选、可替换

本地 VLM 只负责：

> **版权页图像 → 结构化元数据**

模型不是数据库的一部分，也不参与普通 CRUD。

模型应通过独立的适配层调用，避免将程序绑定到某一个具体模型运行框架。

------

## 2.4 标准数据交换

CSV / JSON 是系统与外部世界之间的主要接口。

尤其支持：

> **对已有图书记录进行字段级 JSON 覆盖。**

这样可以让外部 AI 完成分类、摘要、元数据校正等工作，再将结果导回数据库。

------

# 3. 用户与典型场景

## 3.1 目标用户

个人研究者、学生、藏书者以及需要管理大量 PDF 电子书的个人用户。

## 3.2 典型场景

### 场景 A：录入一本新书

```text
手工输入
↓
book add
↓
SQLite
```

### 场景 B：版权页自动提取

```text
PDF / 版权页图片
↓
本地 VLM
↓
结构化 JSON
↓
人工确认
↓
SQLite
```

### 场景 C：批量进行 CLC 分类

```text
数据库
↓
export JSON / CSV
↓
网页端 AI
↓
生成分类结果 JSON
↓
import
↓
CLC 字段更新
```

### 场景 D：整理文件名

```text
数据库元数据
↓
rename
↓
生成规范化 PDF 文件名
```

### 场景 E：写作时获取引用

```text
book cite
↓
GB/T 7714
```

------

# 4. 功能范围

| 模块       | 功能                 | 优先级 |
| ---------- | -------------------- | ------ |
| 数据库     | 初始化、数据存储     | P0     |
| 图书管理   | 增删改查             | P0     |
| 检索       | 字段检索、组合过滤   | P0     |
| 数据交换   | JSON / CSV 导入导出  | P0     |
| 文件关联   | 本地 PDF 路径        | P0     |
| 文件重命名 | 根据元数据生成文件名 | P1     |
| 引用输出   | GB/T 7714            | P1     |
| 本地 VLM   | 版权页结构化提取     | P1     |
| GUI        | pywebview 桌面界面   | P1     |
| 全文检索   | SQLite FTS5          | P2     |
| 其他格式   | BibTeX / RIS         | P2     |

------

# 5. 数据模型

## 5.1 books

图书主表。

| 字段              | 类型          | 说明                 |
| ----------------- | ------------- | -------------------- |
| id                | INTEGER PK    | 唯一 ID              |
| title             | TEXT NOT NULL | 书名                 |
| author            | TEXT          | 作者                 |
| translator        | TEXT          | 译者，可为空         |
| publisher_id      | INTEGER FK    | 出版社               |
| publication_place | TEXT          | 出版地               |
| publication_year  | INTEGER       | 出版年份             |
| edition           | TEXT          | 版次，存纯数字字符串         |
| isbn              | TEXT          | ISBN                 |
| clc_code          | TEXT          | 中图法分类号         |
| literature_type   | TEXT          | 文献类型，默认 M     |
| abstract          | TEXT          | 摘要，可为空；列表页不显示   |
| file_path         | TEXT          | 本地文件路径，可为空 |
| created_at        | TEXT          | 创建时间             |
| updated_at        | TEXT          | 更新时间             |

### 说明

第一版不追求完整的图书馆编目模型。

作者和译者允许直接保存为字符串，例如：

```text
author = "马克思；恩格斯"
translator = "张一兵；陈学明"
```

暂不建立 `contributors` / `book_contributors` 这样的通用关系模型。

### 版次（edition）约定

- 数据库**只存纯数字字符串**，如 `"1"`、`"2"`、`"3"`，不存 `"第1版"` / `"第一版"`；
- 输入容错：`--edition "第2版"` / `"2版"` / `"2"` 一律规范化为 `"2"`；无法提取数字时原样保留并给出警告；
- 显示与引用时由呈现层格式化为 `第N版`；
- 为空（NULL）时，显示/重命名/引用环节一律省略该字段。

------

## 5.2 publishers

出版社作为独立实体保存。

| 字段 | 类型          | 说明       |
| ---- | ------------- | ---------- |
| id   | INTEGER PK    | 主键       |
| name | TEXT NOT NULL | 出版社名称 |

出版地不作为出版社固定属性，而属于具体图书：

```text
publishers
    ↓
books.publication_place
```

原因是同一出版社可能对应不同出版地或不同出版版本。

------

# 6. 文件模型

第一版不建立复杂的文件资产管理系统。

文件直接通过：

```text
books.file_path
```

关联。

原则：

> **一本图书对应一个主文件。**

暂不支持一个图书记录对应多个 PDF。

如果未来确实产生多文件需求，再扩展独立 `files` 表。

因此第一版不保存：

- SHA-256；
- 原始文件名；
- 规范化文件名；
- 导入时间；
- 文件哈希；
- 文件历史。

## 6.1 路径存储规则（定稿）

`file_path` **同时支持绝对路径与相对路径**：

```text
绝对路径：  D:\Books\否定辩证法.pdf
            C:\Users\Lucian\Documents\books\资本论.pdf

相对路径：  books\否定辩证法.pdf
            否定辩证法.pdf
```

解析规则：

- **绝对路径**：按原样直接使用；
- **相对路径**：以配置中的**根目录**（`library_root`）为基准拼接解析；
- `library_root` 保存在 TOML 配置中（默认 `book.toml` 的 `[paths] library_root`），初始化时写入；
- 是否为绝对路径按平台判定（Windows 识别盘符 `D:\` 与 UNC `\\`，POSIX 识别前导 `/`）；
- 显示时统一展示**相对于 `library_root` 的相对路径**（更简洁、可迁移）；`book show` 可附带显示解析后的绝对路径。

推荐用法：

> **默认存相对路径**，便于整库迁移；仅当文件在库根之外时才存绝对路径。

**输入规范化与校验（定稿）**：

- 首尾空白、**成对引号**（资源管理器「复制路径」产出 `"E:\Books\x.pdf"`）→ 自动去除；
- 盘符误用**全角冒号**（`E：\Books\x.pdf`，中文输入法常见）→ **明确报错并给出正确写法**，
  绝不静默当作相对路径拼接 —— 否则会产出 `E:\Books\E:\Books\x.pdf` 这类叠加路径；
- 原则：**能无损规范化就规范化，不能识别的就报错，绝不猜**。

如果 `library_root` 缺失而记录又是相对路径，则解析失败并明确报错提示用户设置根目录，不做静默猜测。

------

# 7. 图书管理

## 7.1 初始化

```bash
book init
```

创建 SQLite 数据库及所需表。

如果数据库已经存在：

```text
提示数据库已存在
↓
默认退出
↓
--force 时允许重新初始化
```

### 初始化语义（定稿）

`--force` 为**幂等重新初始化**，语义严格约束如下：

- **严禁**删除、覆盖或清空已有数据库数据；
- 仅重新执行初始化逻辑（`CREATE TABLE IF NOT EXISTS` 等）；
- **不执行任何 `DROP` / `DELETE`**，`--force` 亦如此；
- 数据库已存在且未加 `--force` → 直接报错退出。

关于配置文件 `book.toml`：

- 若 `book.toml` 已存在，**普通 `book init` 不覆盖**（仅提示，不做改动）；
- 只有显式使用 `--force` 时才允许重新生成 / 覆盖；
- `--force` 重新生成时，**不得清空已有配置项**——配置值先读入再写回，`library_root`、VLM 设置等保持原值（除非命令行显式覆盖）。
- 本项目**只保证保留已知配置项**（`library_root`、`db_path`、`vlm.*`）；用户手动添加的自定义 / 未知配置项**不保证保留**。

未配置 `library_root` 时，`init` 给出提示（相对路径将无法解析），但不阻断。

------

## 7.2 添加

```bash
book add \
  --title "否定辩证法" \
  --author "阿多尔诺" \
  --translator "王凤才" \
  --publisher "商务印书馆" \
  --place "北京" \
  --year 2019 \
  --edition "1" \
  --isbn "9787100163180" \
  --clc "B0" \
  --file "D:\Books\否定辩证法.pdf"
```

支持通过交互式表单添加。

出版社不存在时自动创建。

------

## 7.3 查看

```bash
book show 123
```

显示完整元数据。

------

## 7.4 编辑

```bash
book edit 123
```

允许修改单个或多个字段。

------

## 7.5 删除

```bash
book delete 123
```

删除前必须确认。

默认只删除数据库记录，不删除实际 PDF 文件。

如未来支持删除文件，必须单独确认。

------

## 7.6 列表

```bash
book list
```

显示图书列表。

------

# 8. 检索

## 8.1 基础搜索

```bash
book search "马克思"
```

支持：

- 书名；
- 作者；
- 译者；
- 出版社；
- ISBN；
- CLC；
- 摘要（命中时才读取，见 8.3）。

------

## 8.2 条件过滤

例如：

```bash
book search "哲学" \
  --author "黑格尔" \
  --year 1800-1900 \
  --clc "B" \
  --publisher "商务印书馆"
```

支持：

- 年份范围；
- CLC 前缀；
- 文献类型；
- 出版社；
- 是否存在文件。

### 年份参数语法（定稿）

`--year` 接受三种写法，均为**闭区间**语义：

```text
--year 2019        单年，等价于 2019-2019
--year 1800-1900   起年-止年，含两端
--year 1800-       起年及以后（开区间，无上界）
```

非法写法（如 `--year abc`、`--year 1900-1800`）直接报错退出，不做猜测。

第一版使用 SQLite 普通查询。

如果未来确有全文检索需求，再启用 SQLite FTS5。

不引入独立搜索服务器。

------

## 8.3 摘要字段的读取策略（定稿）

`abstract` 保存在数据库中，但**列表类输出（`book list`、搜索结果的列表视图、GUI 列表页）一律不读取、不显示摘要**。

理由：`abstract` 为 TEXT 无长度上限，大库下随列表 `SELECT *` 会显著拖慢响应。

规则：

- 列表查询**显式列出需要的列**，绝不 `SELECT *`，不包含 `abstract`；
- 仅 `book show`（单条详情）与 `book search` 的**详情展示**才读取 `abstract`；
- 搜索本身命中 `abstract` 字段（作为过滤条件）不受此限制——过滤在 SQL 层完成，不把摘要内容取回列表。

------

# 9. JSON 数据交换

JSON 是本项目的重要外部接口。

## 9.1 全量导出

```bash
book export --format json --output books.json
```

导出图书记录。

------

## 9.2 全量导入

用于迁移或恢复数据。

```bash
book import books.json
```

具体导入模式需要根据 JSON 类型区分：

```text
完整数据库数据
```

与：

```text
字段修改数据
```

不能混淆。

### 导入模式标记（定稿）

JSON 顶层**必须显式声明模式**，不做内容猜测。两种结构互斥：

```json
{
  "mode": "full",
  "items": [
    { "title": "否定辩证法", "author": "阿多尔诺", "...": "..." }
  ]
}
```

```json
{
  "mode": "patch",
  "items": [
    { "id": 123, "clc_code": "B0" },
    { "id": 124, "clc_code": "A81", "abstract": "……" }
  ]
}
```

规则：

- `mode` 缺省或取值非法 → **直接报错退出**，不做任何猜测；
- `mode = "full"`：按完整记录导入（迁移 / 恢复）；
- `mode = "patch"`：按 `id` 定位做字段级覆盖（见第 10 节）；
- CLI 允许 `book import --mode patch x.json` 覆盖文件内声明，以便纠正外部文件；
- `book export` 默认输出 `"mode": "full"`。

------

# 10. JSON 字段覆盖

这是本项目的重要功能。

允许外部程序只提交需要修改的字段。

例如：

```json
[
  {
    "id": 123,
    "clc_code": "B0"
  },
  {
    "id": 124,
    "clc_code": "A81",
    "abstract": "……"
  }
]
```

执行：

```bash
book import patch.json
```

系统根据 `id` 定位记录，只修改 JSON 中实际出现的字段。

例如：

```text
ID 123
CLC:
NULL → B0

ID 124
CLC:
B1 → A81

摘要：
NULL → ……
```

未出现在 JSON 中的字段保持原值。

------

## 10.1 导入规则

1. `id` 必须存在；
2. 默认只允许修改已有记录；
3. 不存在的 ID 默认报错；
4. 未提供的字段不修改；
5. 非法字段拒绝导入；
6. 导入前显示变更预览；
7. 用户确认后写入数据库。

------

## 10.2 NULL 语义

第一版规定：

```json
{
  "id": 123,
  "abstract": null
}
```

表示：

> **主动将该字段清空。**

因此：

```text
字段不存在
≠
字段值为 null
```

------

# 11. CSV 数据交换

支持：

```bash
book export --format csv --output books.csv
```

以及：

```bash
book import books.csv
```

CSV 使用标准 CSV 格式，不再使用“分号作为列分隔符、竖线作为作者分隔符”的自定义格式。

作者、译者等字段直接作为普通字符串保存。

### CSV 导入语义（定稿）

- CSV 导入固定为 **full 语义**：按 `id` 做 upsert —— `id` 存在且命中已有记录 → 更新该记录；否则 → 新增（`id` 自动分配）；
- **空单元格视为「清空该字段」**；
- `publisher` 列为出版社名称（与 JSON / `book add` 一致），不存在时自动创建；
- 表头出现未知列 → 拒绝导入。

CSV 主要用于：

- 批量查看；
- Excel 编辑；
- 交给外部 AI；
- 数据迁移。

------

# 12. 本地 VLM 版权页提取

## 12.1 定位

系统保留一个本地大模型功能，但模型职责非常明确：

> **从版权页中提取结构化书目元数据。**

模型不负责：

- CLC 分类；
- 网络搜索；
- 全书摘要；
- RAG；
- 在线书目匹配；
- 自动写入数据库。

------

## 12.2 输入方式

支持：

### PDF 自动处理

```bash
book enrich --pdf "book.pdf"
```

程序读取 PDF 前若干页，寻找可能的版权页。

### 手动指定图片

```bash
book enrich --image "copyright.jpg"
```

### 手动指定 PDF 页

```bash
book enrich --pdf "book.pdf" --page 5
```

------

## 12.3 版权页定位

**两条路径**（定稿）：

1. **文本层关键词**——默认检查 PDF 前若干页，按关键词打分：
   `ISBN`、`出版社`、`出版`、`发行`、`版次`、`印刷`、`书号`。
   适用于**文字版 PDF**，快（一次读取，不调模型）。
2. **逐页渲染喂 VLM**——**扫描件的正解**（无文本层时路径 1 必然失败）。
   按页号从小到大渲染并交给 VLM 判定，**遇到含 ISBN 的页即停**；
   途中各页结果**合并**（封面常有书名/作者，版权页才有 ISBN/年份，合并后最完整）。
   关键字段（书名+作者+出版社+年份）齐备即提前收工，最多渲染前 **8** 页。

两条路径都失败时：明确报错并提示用 `--page` 手动指定页码。
不使用 MinerU。

> **为什么需要路径 2**：实测用户的真实藏书**多数是扫描件（无文本层）**，
> 纯文本层方案会让 enrich 对绝大多数实际藏书不可用。

参数（定稿）：

- 文本层关键词扫描上限：前 **30** 页（软阈值 15 页）；不做整本扫描；
- 逐页渲染上限：前 **8** 页（每页都要跑一次 VLM，故比文本层小得多）；
- `MAX_SCAN_PAGES` 为可调常量（`services/enrich.py`）。

------

## 12.4 VLM 输出

模型要求输出结构化 JSON：

```json
{
  "title": "否定辩证法",
  "author": ["阿多尔诺"],
  "translator": ["王凤才"],
  "publisher": "商务印书馆",
  "publication_place": "北京",
  "publication_year": 2019,
  "edition": "1",
  "isbn": "9787100163180"
}
```

字段无法确定时输出：

```json
null
```

不得要求模型猜测。

------

## 12.5 人工确认

模型输出不得直接覆盖数据库。

流程：

```text
版权页
↓
VLM
↓
JSON
↓
字段校验
↓
显示差异
↓
用户确认
↓
写入数据库
```

例如：

```text
书名
否定辩证法

作者
阿多尔诺

出版社
NULL → 商务印书馆

出版年份
NULL → 2019

ISBN
NULL → 9787100163180

确认写入？ [y/N]
```

------

## 12.6 实施要点（定稿）

- `book enrich` 提取到的元数据用于**新建**图书记录（PRD 未给 book id）；确认后走与 `book add` 相同的写入路径，含疑似重复检测。
- `--pdf` 输入时自动把源 PDF 关联为 `file_path`（库内按 `library_root` 转相对路径）。
- **版权页定位**：对前 30 页（硬上限）按关键词打分取最高分页；扫描件无文本层 → 定位失败，提示用 `--page` 手动指定（**不做 OCR**）。
- **提示词**要求「只输出 JSON、无法确定输出 null、不得猜测」；解析容错：接受裸 JSON / ```json 围栏 / 夹带说明文字的输出。
- 作者、译者数组以「；」拼接入库；ISBN 去连字符；版次规范化；无法解析的年份置 null（不猜测、不崩溃）。
- 模型不可用时 `book enrich` 明确提示并退出（§13），**不影响其余功能**。

------

# 13. 本地模型技术要求

程序不绑定具体模型。

通过统一适配层调用：

```text
LLMService
    ↓
VLM Adapter
    ↓
本地推理程序
```

Demo 阶段可优先适配用户本机已有的本地模型运行环境。

目标模型规模：

> **约 3B 级视觉语言模型（VLM）即可。**

视觉输入 → 文本/结构化输出的转换由 **VLM 自身完成**，不再借助独立的 OCR 工具链（如 MinerU）。

即链路为：

```text
版权页图像
    ↓
VLM（单步：视觉 → 结构化 JSON）
    ↓
结构化 JSON
```

模型本身不作为本项目 Demo 的核心安装依赖。

### 适配目标（定稿）

第一版适配层默认对接 **Ollama**（本机 HTTP 推理服务，最省事、跨平台）：

```text
LLMService
    ↓
VLM Adapter（默认：OllamaAdapter）
    ↓
http://127.0.0.1:11434/api/chat  （本地 Ollama 服务）
    ↓
视觉语言模型（约 3B 级，如 qwen2.5-vl:3b / minicpm-v 等）
```

- 适配层提供统一接口，具体模型名与端点写在 TOML 配置中，不硬编码；
- 若本机已运行其他推理框架（llama.cpp server 等），实现一个新的 Adapter 即可接入，不改动上层；
- 模型不可用（服务未启动 / 模型未拉取）时，`book enrich` 给出明确提示并退出，不影响其余功能；
- 模型仅被 `enrich` 调用，CRUD、检索、导入导出等**完全不触碰模型**。

------

# 14. PDF 文件重命名

## 14.1 命名模板

```text
{作者}_{书名}[{文献类型}]{_译者}_{出版地}：{出版社}_{出版年份}{_版次}
```

示例：

```text
阿多尔诺_否定辩证法[M]_王凤才_北京：商务印书馆_2019(1).pdf
```

------

## 14.2 规则

- 多作者直接使用数据库中的作者字符串；
- 多译者直接使用数据库中的译者字符串；
- 译者为空时省略；
- 版次为空时省略；
- 文献类型为空时默认 `M`；
- 文件名非法字符自动清理；
- Windows 文件名非法字符必须处理；
- 文件名长度需要限制；
- 目标文件存在时默认不覆盖。

### 与引用共用格式化模块（定稿）

重命名与引用输出**共用同一个呈现格式化模块**（`services/format.py`），不得各自实现一套字段缺省规则。

该模块统一负责：

- 字段缺省策略（为空时省略 / 使用默认值）；
- 作者、译者的多值拼接；
- 版次的 `第N版` 格式化（库内存纯数字，见 §5.1）；
- 文件名非法字符清理、长度截断。

`RenameService` 与 `CitationService` 只负责**调用该模块**组装各自的最终字符串（文件模板 / GB/T 7714 模板），缺省逻辑只存在一处。

### 重命名细节（定稿）

- **空字段整段省略**：各段以 `_` 连接，空段不产生多余分隔符（如无作者则直接从书名开始）；
- **扩展名沿用源文件**（不强制 `.pdf`），重命名后文件名与源文件同目录；
- **版次在文件名中写作 `(N)`**（如 `2019(1)`），而在引用中写作 `第N版`——两者由 `format.py` 分别提供；
- **出版地：出版社** 使用全角冒号（`北京：商务印书馆`）；缺一则简化为仅存方；
- **重命名成功后同步更新数据库 `file_path`**（库内文件仍存相对路径）；
- 目标文件已存在 → **默认拒绝**，仅 `--force` 才覆盖（覆盖用 `os.replace`，因 Windows `os.rename` 不能覆盖）；
- 若已是规范文件名 → **no-op**，不改动文件；
- 记录未关联文件 → 明确报错，不静默跳过。

------

## 14.3 预览

```bash
book rename 123 --dry-run
```

显示：

```text
旧文件名
↓
新文件名
```

确认后：

```bash
book rename 123 --apply
```

------

# 15. GB/T 7714 引用

支持：

```bash
book cite 123
```

默认输出 GB/T 7714 格式。

例如：

```text
阿多尔诺. 否定辩证法[M]. 王凤才, 译. 北京: 商务印书馆, 2019.
```

第一版主要实现：

```text
GB/T 7714-2015 专著
```

其他引用类型以后扩展。

### 引用细节（定稿）

- 模板：`主要责任者. 题名[文献类型]. 其他责任者. 版本项. 出版地: 出版者, 出版年.`
- 多作者以 `, ` 拼接（库内以「；」存储，如 `马克思；恩格斯` → `马克思, 恩格斯`）；
- 译者段落为 `{译者}, 译`；
- **版本项遵循 GB/T 7714-2015**：**第 1 版不著录**（整段省略），其余写作 **`N版`**（无「第」字，如 `2版`）；
- 出版项使用半角冒号（`北京: 商务印书馆, 2019`）；缺出版地/出版社/年份时按段省略；
- 空段自动省略，不做占位。

示例（同一本书，仅版次不同）：

```text
edition = 1（第1版）→ 阿多尔诺. 否定辩证法[M]. 王凤才, 译. 北京: 商务印书馆, 2019.
edition = 2（第2版）→ 阿多尔诺. 否定辩证法[M]. 王凤才, 译. 2版. 北京: 商务印书馆, 2019.
```

> 注：**文件名**中的版次仍写作 `(N)`（如 `2019(1)`，见 §14.1），与引用中的版本项格式不同 —— 两者都由 `format.py` 提供，但规则各自独立。

BibTeX / RIS 暂不属于 MVP。

------

# 16. GUI

## 16.1 技术方案

GUI 使用：

> **pywebview + HTML + CSS + JavaScript**

不使用 Electron。

不使用 React / Vue 等前端框架。

------

## 16.2 GUI 定位

GUI 是 CLI 的另一种操作入口，而不是另一套程序。

```text
CLI ─────┐
         ↓
    Python Core
         ↓
      SQLite
         ↑
WebView ─┘
```

------

## 16.3 第一版 GUI

至少提供：

- 图书列表；
- 搜索；
- 图书详情；
- 编辑图书；
- 添加图书；
- JSON 导入；
- CSV 导入导出；
- JSON 字段覆盖预览；
- 版权页 VLM 提取；
- 重命名预览；
- 引用复制。

------

## 16.4 GUI 启动

例如：

```bash
book gui
```

程序启动本地 WebView 窗口。

GUI 由 pywebview 在**内部**托管一个仅绑定 `127.0.0.1`、**不对外暴露**的 HTTP 端点，作为 WebView 与 Python Core 之间的传输层。

```text
book gui
   ↓
pywebview 启动 WebView 窗口
   ↓
内部 HTTP 端点（仅 127.0.0.1，不对外监听/不暴露）
   ↓
Python Core / Service Layer
   ↓
SQLite
```

说明：

- 该 HTTP 端点**仅服务于本进程内的 WebView**，不对外提供服务；
- 因此第 19 节排除的"作为对外服务的 FastAPI"，与这里的"内部给 webview 喂料的传输层"是两回事，不应混为一谈；
- 端点可以选用任意轻量方案（FastAPI / Starlette / 标准库 `http.server` 均可），不作为强制依赖项；
- WebView 内的前端仍使用普通 `fetch('/api/...')` 与核心层交互，无须把所有操作都改写成 `js_api` 桥。

### GUI 实现细节（定稿）

- **传输层**：标准库 `http.server.ThreadingHTTPServer`，**零第三方依赖**（不引入 FastAPI/Starlette）；
- **绑定**：仅 `127.0.0.1`，端口由系统分配（`port=0`），**绝不对外暴露**；
- **分层**：所有 `/api/*` 处理函数**只调用 services 层**，不直接操作 dao / 写 SQL（§17）；
- **连接**：每个请求打开一次 `Context`（连接），用完即关 —— 天然线程安全；
- **前端**：`HTML + CSS + JS`，无框架（§16.1），单页应用；
- **文件选择**：仅「版权页提取」需要本地路径，通过 pywebview 的 `js_api.pick_file()` 桥实现，其余交互一律 `fetch`；
- **启动顺序**：先起服务 → 健康检查轮询 `/api/meta` 就绪 → 再建窗口（否则白屏）。

接口一览（`gui/server.py`）：

```text
GET    /api/meta                     应用信息（条数 / 库根 / 模型）
GET    /api/books                    列表（不含摘要）
POST   /api/books                    新增（重复 → 409 + duplicates）
GET    /api/books/{id}               详情（含摘要）
PUT    /api/books/{id}               编辑（局部）
DELETE /api/books/{id}               删除
GET    /api/search                   检索（q/author/year/clc/type/publisher/has_file）
GET    /api/export?format=json|csv   导出（返回文本内容）
GET    /api/cite/{id}                引用
POST   /api/open/{id}                用系统默认程序打开关联文件
POST   /api/reveal/{id}              在文件管理器中定位关联文件
POST   /api/import/preview           导入预览（返回变更计划）
POST   /api/import/apply             导入执行
GET    /api/rename/{id}              重命名预览
POST   /api/rename/{id}/apply        重命名执行
POST   /api/enrich                   版权页提取（模型输出，不写库）
POST   /api/enrich/save              确认后写入数据库
POST   /api/enrich/batch/preview     批量补全：列出待补记录（不跑模型）
POST   /api/enrich/batch/item        批量补全：对单条跑 VLM（不写库；GUI 逐条调用显示进度）
POST   /api/enrich/batch/apply       批量补全：把复核后的若干条写回库
POST   /api/scan/preview             扫描目录 → 建库计划（不写库）
POST   /api/scan/apply               按勾选项批量建记录
```

------

# 17. 数据库与 GUI / CLI 的关系

核心业务层统一提供：

```text
BookService
SearchService
ImportExportService
RenameService
CitationService
LLMService
```

CLI 与 GUI 均调用这些服务。

禁止：

```text
CLI → 直接操作数据库

GUI → 另一套数据库逻辑
```

应该：

```text
CLI ──────────┐
              ↓
        Service Layer
              ↓
           Database
              ↑
GUI ──────────┘
```

------

# 18. 技术栈

| 层             | 技术                    |
| -------------- | ----------------------- |
| 开发语言       | Python 3.11+            |
| 数据库         | SQLite（标准库 `sqlite3`，不使用 ORM） |
| CLI            | Typer + Rich            |
| GUI            | pywebview               |
| 前端           | HTML + CSS + JavaScript |
| PDF            | PyMuPDF                 |
| 本地 AI        | 可插拔本地 VLM          |
| 数据交换       | JSON + CSV              |
| 文件操作       | pathlib / shutil        |
| 配置           | TOML（`book.toml`：`library_root`、VLM 端点/模型名等） |
| 测试           | pytest                  |
| 打包           | PyInstaller             |

------

# 19. 明确不采用的技术

以下技术不进入当前版本：

- 作为对外服务的 FastAPI（注：GUI 内部用于喂料 WebView 的传输层不在此列，见第 16.4 节）；
- Django；
- Electron；
- React；
- Vue；
- PostgreSQL；
- MySQL；
- Redis；
- Elasticsearch；
- Meilisearch；
- MinerU（视觉转文本由 VLM 自身完成，见第 13 节）；
- Tavily；
- DeepSeek API；
- 在线书目 API；
- 云端数据库；
- 多用户权限系统。

这些技术并非永远不能加入，而是当前需求没有必要。

------

# 20. 项目结构

建议结构：

```text
bookdb/
├── pyproject.toml
├── README.md
├── src/
│   └── bookdb/
│       ├── main.py
│       │
│       ├── db/
│       │   ├── database.py      # 连接、初始化、PRAGMA
│       │   ├── dao.py           # 手写数据访问层（无 ORM）
│       │   └── schema.py        # 建表 SQL
│       │
│       ├── services/
│       │   ├── books.py
│       │   ├── search.py
│       │   ├── import_export.py
│       │   ├── rename.py
│       │   ├── citation.py
│       │   ├── format.py        # rename/cite 共用的呈现格式化模块
│       │   └── llm.py
│       │
│       ├── cli/
│       │   └── commands.py
│       │
│       ├── gui/
│       │   ├── app.py
│       │   └── web/
│       │       ├── index.html
│       │       ├── style.css
│       │       └── app.js
│       │
│       └── llm/
│           ├── base.py
│           └── adapters/
│
└── tests/
```

------

# 21. CLI 命令

第一版命令统一为：

```text
book init
book add
book list
book show
book edit
book delete
book search
book import
book export
book rename
book cite
book enrich
book scan
book gui
```

其中：

### 核心 P0

```text
init
add
list
show
edit
delete
search
import
export
```

### P1

```text
rename
cite
enrich
scan
gui
```

`book scan <dir>` 扫描目录（默认递归），从**文件名**解析书名 / 作者 / 版次并生成建库计划；
默认**只预览**，`--apply` 才写库（先预览后写入，与 §10 导入预览一致）。

`book enrich` 支持**单本**与**批量**两种模式：

```text
book enrich --pdf <文件>          单本：从该 PDF 提取
book enrich --image <图片>        单本：从版权页图片提取
book enrich --missing             批量：全库缺元数据的记录
book enrich --dir <目录>          批量：只处理该目录内的记录
  --limit N           最多处理 N 条
  --no-overwrite      只填空缺字段，不覆盖已有值
```

批量模式同样**先提取、预览复核、确认后写入**（§12.5）。触发补全的条件刻意**不含出版年份**
—— 部分版权页本就不印年份，若把年份计入，这些书会被每次批量反复重跑（实测发现）。

------

# 22. 非功能需求

## 22.1 本地优先

核心功能完全离线。

不依赖：

- 网络；
- 云服务；
- API Key。

------

## 22.2 单用户

第一版只考虑个人本地使用。

不考虑：

- 多用户；
- 权限；
- 云同步；
- 网络数据库；
- 协作。

------

## 22.3 数据安全

SQLite 数据库为单文件，可直接复制备份。

JSON / CSV 可作为额外的数据备份与交换方式。

任何批量 JSON 导入操作必须先提供变更预览。

------

## 22.4 性能

以个人图书库为目标：

> 10000 本图书规模下，普通字段检索应保持即时响应。

不为了理论上的更大规模提前引入独立数据库或搜索服务。

------

# 23. 数据一致性

## 23.1 ISBN

使用单一 `isbn` 字段。

不区分：

```text
isbn10
isbn13
```

输入时进行基本规范化，例如去除 ISBN 中的连字符和空格。

------

## 23.2 重复图书

添加图书时检查：

1. ISBN；
2. 无 ISBN 时检查书名 + 作者 + 出版年份。

系统发现疑似重复时提示用户，而不是自动删除或合并。

------

## 23.3 文件

删除图书记录时默认：

> **不删除实际 PDF 文件。**

避免数据库操作意外造成文件丢失。

------

# 24. 开发阶段

## M1：数据库与 CLI

完成：

- SQLite（标准库 `sqlite3`，无 ORM）；
- 数据模型 / DAO；
- `init`；
- `add`；
- `list`；
- `show`；
- `edit`；
- `delete`；
- `search`。

目标：

> 可以正常管理个人图书数据库。

------

## M2：数据交换

完成：

- JSON 全量导出；
- JSON 导入；
- JSON 字段级 patch；
- CSV 导出；
- CSV 导入；
- 导入预览。

目标：

> 数据可以自由进出数据库，并能够交给外部 AI 处理。

------

## M3：文件与引用

完成：

- PDF 路径；
- 文件名生成；
- rename dry-run；
- rename apply；
- GB/T 7714 citation。

目标：

> 元数据能够反过来服务实际电子书整理。

------

## M4：本地 VLM

完成：

- PDF 前若干页扫描；
- 版权页候选定位；
- 图片输入；
- 本地 VLM Adapter；
- JSON 结构化输出；
- 字段校验；
- 差异预览；
- 人工确认写入。

目标：

> 可以从版权页快速建立一本书的基本元数据。

------

## M5：WebView GUI

完成：

- 图书列表；
- 搜索；
- 编辑；
- JSON/CSV 导入导出；
- JSON patch 预览；
- VLM 提取界面；
- rename 预览；
- citation。

------

# 25. 后续扩展

以下功能不进入当前 Demo：

- SQLite FTS5；
- BibTeX；
- RIS；
- 多文件关联；
- OCR；
- 在线 ISBN 查询；
- 在线书目数据库；
- 自动摘要；
- 自动 CLC 分类；
- 云同步；
- 多用户；
- 阅读器；
- 独立 Web 服务。

> **例外（已实现）**：原列于此的「**批量扫描目录**」已作为后续迭代加入 —— 见 §21 `book scan`
> 与 §16 的 `/api/scan/*` 接口。它按**文件名解析**元数据，先预览再写入，不调用模型。

其中：

> **自动 CLC 分类、摘要生成等工作优先通过外部 AI + JSON 导入解决。**

如果未来发现重复操作非常频繁，再考虑将其做成插件或自动化脚本。

------

# 26. 核心工作流总结

## 手工录入

```text
用户
 ↓
CLI / WebView
 ↓
SQLite
```

## 版权页提取

```text
PDF / 图片
 ↓
PyMuPDF
 ↓
版权页
 ↓
本地 VLM
 ↓
JSON
 ↓
人工确认
 ↓
SQLite
```

## 外部 AI 批量处理

```text
SQLite
 ↓
JSON / CSV
 ↓
外部 AI
 ↓
JSON
 ↓
字段级 patch import
 ↓
SQLite
```

## PDF 整理

```text
SQLite 元数据
 ↓
命名模板
 ↓
dry-run
 ↓
apply
 ↓
PDF 重命名
```

## 引用

```text
SQLite 元数据
 ↓
GB/T 7714 Formatter
 ↓
引用文本
```

------

# 27. MVP 验收标准

Demo 至少能够完成以下完整闭环：

### 闭环一：建立数据库

```bash
book init
book add ...
book show 1
```

### 闭环二：搜索

```bash
book search "马克思"
```

能够根据书名、作者、出版社、年份、CLC 等字段检索。

### 闭环三：数据交换

```bash
book export --format json
```

修改 JSON 后：

```bash
book import patch.json
```

能够只覆盖指定字段。

### 闭环四：版权页提取

```bash
book enrich --image copyright.jpg
```

本地 VLM 能输出结构化元数据，并在写入数据库前显示修改内容。

### 闭环五：文件整理

```bash
book rename 1 --dry-run
book rename 1 --apply
```

能够根据元数据生成规范化文件名。

### 闭环六：引用

```bash
book cite 1
```

能够输出 GB/T 7714 格式引用。

------

# 28. 项目最终定位

本项目不是：

> 一个“什么都能自动完成”的 AI 图书管理软件。

而是：

> **一个小型、可靠、本地优先的个人图书元数据库。**

其核心是：

```text
                 ┌───────────────┐
                 │    SQLite     │
                 │   图书数据库   │
                 └───────┬───────┘
                         │
                  Python Core
                         │
             ┌───────────┴───────────┐
             │                       │
            CLI                    WebView
             │                       │
             └───────────┬───────────┘
                         │
                  JSON / CSV
                         │
              ┌──────────┴──────────┐
              │                     │
          外部 AI / 程序          本地 VLM
          分类/批处理            版权页提取
```

**原则：核心保持简单，复杂能力通过标准接口向外扩展。**