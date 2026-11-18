# jmlib

> 本地漫画资源库：把一堆散装 zip 变成一个可搜索、可阅读、可治理的私人书架。

`jmlib` 不下载、不分发任何内容。它管理的是**你自己电脑上已有的** zip 归档：把散落的压缩包整理成
`library/archive/{Aid}/{pindex}.zip` 的规整结构，建一份 SQLite 索引，再用浏览器读。所有数据留在本机，
没有任何账号、云同步或遥测。

[![Python](https://img.shields.io/badge/python-%E2%89%A53.10-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## 目录

- [它解决什么问题](#它解决什么问题)
- [核心概念](#核心概念)
- [安装](#安装)
- [快速开始](#快速开始)
- [命令行](#命令行)
- [网页界面](#网页界面)
- [存储布局](#存储布局)
- [设计约束](#设计约束)
- [贡献](#贡献)
- [许可](#许可)

---

## 它解决什么问题

十多年攒下来的本子目录，通常是这样的：

```
JM(1)/[社团] 标题/1.zip  2.zip  3.zip
JM(2)(2)/[社团] 另一个标题.zip
JM(Android)/一堆.jpg
长篇/拖了三年还没看完的那本/…
```

痛点很具体：

| 痛点 | `jmlib` 的做法 |
|:---|:---|
| 想看第 2 话，得在几十层文件夹里翻 | 一本 = 一个 `Aid`，话 = `pindex`，`/read` 里直接续读 |
| 换了电脑就忘了看到第几页 | 阅读进度写进 SQLite，逐页记忆 |
| 同名文件重复下载，白占几十 GB | 页数 / 标签 / 标题三路比对，标出重复投稿 |
| 手滑改名，几百个文件全乱 | 迁移先出只读计划，`--apply` 才动文件，`--rollback` 精确反向 |
| 清理脚本删错目录，找不回来 | 破坏性动作只出「守卫」清单，永不自动执行 |
| 不知道哪个目录名对不上号 | 未识别书架按标题回查上游，人工复核后转正 |

## 核心概念

| 术语 | 含义 |
|:---|:---|
| **Aid** | 本子的唯一身份键。1–7 位数字，`121` 和 `1234567` 都合法 |
| **pindex** | 章节序号（第几话），1 起算 |
| **世代（generation）** | 来源目录的批次标签，例如 `JM(1)(2)`，用于追溯「这本是从哪堆里出来的」 |
| **转正（promote）** | 把 `library/_unidentified/` 里还没认出来的包，搬到 `library/archive/{Aid}/` 并登记成书 |
| **治理（governance）** | 所有会动文件的操作都要一条**服务端重算的精确 token**，笼统的「同意」永远被拒 |

三条不可让步的约束，贯穿整个代码库：

1. **`store.py` 是唯一的数据库写入入口**，其它模块一律不直接打开 `jmlib.db`。
2. **`library/` 里的字节是真相本身**，索引随时可以重建，重建不丢用户数据。
3. **源目录零删除**：迁移和转正只做同卷 `rename`，任何一步失败都结构化报错，不猜、不覆盖。

## 安装

需要 Python 3.10 及以上（推荐 3.12+）。

```bash
# 用 uv（推荐）
uv tool install .

# 或者 pip
pip install .
```

只想跑源码、不装进环境：

```bash
pip install -e . --group dev   # 或：uv sync
$env:PYTHONPATH='src'          # PowerShell；bash 用 export PYTHONPATH=src
python -m jmlib --help
```

> 生产依赖只有 `fastapi`、`uvicorn`、`jmcomic`。`jmcomic` 只在**联网回查标题**时用到，
> 完全离线使用（导入 / 索引 / 阅读 / 体检 / 转正）时不会发起任何网络请求。

## 快速开始

```bash
# 1. 把散装目录搬成规整结构（先看计划，再动手）
jmlib migrate --plan
jmlib migrate --apply

# 2. 建索引 + 生成封面
jmlib index

# 3. 打开书架
jmlib serve              # http://127.0.0.1:8765
```

想先把一堆松散的 `.zip` 收进库里：

```bash
jmlib ingest --plan-only        # 只看会怎么打包
jmlib ingest --apply
```

## 命令行

| 子命令 | 作用 |
|:---|:---|
| `jmlib migrate` | `--plan` / `--apply` / `--rollback`：归档结构迁移，支持精确反向 |
| `jmlib index` | 重建 SQLite 索引与封面，幂等，不触碰字节 |
| `jmlib serve` | 启动本地网页书架（`--host` / `--port` / `--lan`） |
| `jmlib check` | 只读存储体检，可 `--csv` 导出 |
| `jmlib download` | 通过 `jmcomic` 下载并自动入库 |
| `jmlib ingest` | 把散装队列打成 `library/` 结构 |
| `jmlib governance` | 只读治理视图与破坏性动作守卫 |
| `jmlib resolve` | 按标题识别未识别书架（串行节流，可 `--dry-run`） |

> `serve --lan` 必须设置 `JMLIB_PIN` 环境变量，否则拒绝监听 `0.0.0.0`：
> 裸奔在局域网上的私人书架不是「方便」，是事故。

## 网页界面

`jmlib serve` 起来后有六个页面：

| 路径 | 页面 |
|:---|:---|
| `/` | 书架：搜索、标签、收藏、书单 |
| `/read` | 阅读器：翻页、双击缩放、阅读进度 |
| `/import` | 导入面板：散装目录扫描与打包计划 |
| `/resolve` | 未识别复核：按标题核对候选并定案 |
| `/governance` | 受控治理：动作台账与破坏性动作守卫 |
| `/health` | 存储体检：缺字节、缺封面、重复页、孤立行 |

## 存储布局

```
library/
  archive/{Aid}/{pindex}.zip     # 已定案的书与章节
  covers/{Aid}_{pindex}.jpg      # 封面缓存
  _unidentified/                 # 还没认出来的包，按世代分目录
data/
  jmlib.db                       # 唯一索引（books / chapters / memory / …）
  governance_manifest.json       # 治理动作台账，append-only
```

| 表 | 作用 |
|:---|:---|
| `books` | 一本 = 一个 Aid，含标题、作者、标签、收藏、书单 |
| `chapters` | 章节 ↔ zip 路径 ↔ 页数 |
| `memory` | 每本书读到第几话第几页 |
| `unresolved` | 磁盘上有、索引里还没有的包 |
| `id_resolution` | 未识别书架的候选与裁决（pending / chosen / skipped） |
| `collections` / `collection_items` | 有序书单（一本书可在多个书单里） |
| `reading_prefs` / `bookmarks` | 阅读偏好与书签 |
| `findings` | 体检结论与人工处置记录 |

## 设计约束

这几条是踩过坑之后写死的，改动前请先读 [贡献指南](.github/CONTRIBUTING.md)：

- **破坏性动作永不自动执行。** 删除散装源、去重复页、批量反向迁移，只出守卫清单 + 待确认状态。
- **token 由服务端重算。** 客户端传上来的计划不可信，执行前重新推导再比对。
- **重建不丢用户数据。** `local_title` / `aliases` / `favorite` / `collection` 是人手写的数据，
  `jmlib index` 必须原样搬过去，而不是重新生成。
- **标签有第二份落点。** 标签既写 `books.tags`，也写 `data/ingest_metadata.json` 侧车；
  只写前者的话，一次 `jmlib index` 就会让全部标签消失（曾经真的丢过 177 本）。

## 贡献

欢迎 issue 和 PR，请先看 [.github/CONTRIBUTING.md](.github/CONTRIBUTING.md)。
发现安全问题请走 [SECURITY.md](.github/SECURITY.md)，不要开公开 issue。

## 许可

[MIT](LICENSE) © 2026 H-60656

本项目只提供本地归档管理工具，不包含、不下载、不分发任何漫画内容。
