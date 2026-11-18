# 贡献指南

感谢你愿意改进 jmlib。这份文档说明本项目的**硬性约束**、开发环境、以及提交前的检查清单。

jmlib 管理的是真实存在、且**不可再生**的本地数据（几万本书、几十 GB 归档）。所以本项目对
「会不会毁数据」的敏感度远高于对功能多少的追求。请先读完[硬性约束](#硬性约束)再动手。

---

## 目录

- [硬性约束](#硬性约束)
- [开发环境](#开发环境)
- [项目结构](#项目结构)
- [提交前检查](#提交前检查)
- [测试怎么写](#测试怎么写)
- [提交信息](#提交信息)
- [Pull Request](#pull-request)
- [报告问题](#报告问题)

---

## 硬性约束

以下几条不是风格偏好，是**架构契约**。破坏它们的 PR 会被要求重做，理由写在这几条下面。

### 1. `store.py` 是唯一的数据库写入入口

任何模块都不许自己 `sqlite3.connect(paths.DB_PATH)` 然后写。读也走 `store` 暴露的函数。

**为什么**：索引、治理、阅读三个模块都会改库。一旦出现第二个写入者，「重建不丢用户数据」
这条保证就只能靠运气。历史上标签全丢就是因为写入路径分散、重建时覆盖了用户数据。

### 2. `library/` 的字节是真相，索引是可再生的

`jmlib index` 必须能在任意时刻从磁盘重建出完整索引，且**不丢失**这些用户手写列：

`local_title`、`aliases`、`favorite`、`collection`（见 `store.USER_COLUMNS`）。

**为什么**：用户可能重装、换机器、删库重来。索引一旦成为真相，删库就等于删数据。

### 3. 破坏性动作永不自动执行

删除散装源目录、去重复页、批量反向迁移、合并世代目录 —— 只允许产出**守卫清单**
（`governance.destruction_guard`），状态恒为 `awaiting scoped confirmation`。
`governance.py` 里不允许出现任何能把破坏性 kind 翻成「已执行」的代码路径。

**为什么**：这几类动作动辄影响上千个文件，且不可逆。把它们放在「需要人签名」的一侧，
是刻意的不方便。

### 4. 会动文件的操作必须拿服务端重算的精确 token

`governance.promote_apply(plan, confirm)` 不信任客户端传上来的 `plan`：
它会从 identity 字段重新推导一份新计划，用**新计划的 token** 比对，还会重校验
`plan.get("to")`、`schemaVersion`、`status`。笼统的「我同意」永远被拒。

**为什么**：这是 TOCTOU 防线。计划生成到执行之间，磁盘状态可能已经变了。

### 5. 源目录零删除

迁移和转正只做同卷 `rename`。任何一步失败都要**结构化报错**（含 `WinError 5` 这类
权限失败），不许吞异常、不许猜、不许覆盖已存在的目标。

### 6. Aid 是唯一身份键

`Aid` 1–7 位数字。标题只是展示数据，永远不能当身份用。
改 Aid 契约前先读 `src/jmlib/aid.py` 的注释：**身份位**（1–7 位）与**自由文本提示位**
（4 位起）是两个不同的问题，故意用两套正则。

---

## 开发环境

需要 Python 3.10+（推荐 3.12+）。

```bash
# 用 uv（推荐）
uv sync

# 或者 venv + pip
python -m venv .venv
.venv/Scripts/activate        # Windows
pip install -e ".[dev]"
```

跑起来：

```bash
$env:PYTHONPATH='src'         # PowerShell
export PYTHONPATH=src         # bash

python -m jmlib --help
python -m jmlib serve         # http://127.0.0.1:8765
```

> ⚠️ 入口是 `python -m jmlib`，**不是** `python -m jmlib.cli`。
> `cli.py` 没有 `__main__` 守卫，`-m jmlib.cli` 只会导入模块然后 exit 0，什么都不打印。

### 不要拿真库做实验

`library/` 和 `data/` 都在 `.gitignore` 里，且是真实数据。写测试时**必须**把路径重定向到
临时目录，例如：

```python
from jmlib import paths

paths.ROOT = tmp
paths.LIBRARY = tmp / "library"
paths.ARCHIVE = paths.LIBRARY / "archive"
paths.UNIDENTIFIED = paths.LIBRARY / "_unidentified"
paths.DB_PATH = tmp / "data" / "jmlib.db"
```

`tests/test_migrate.py` 是现成的样板。

## 项目结构

```
src/jmlib/
  aid.py           Aid 契约（身份位 / 提示位 / 封面文件名），全项目唯一来源
  paths.py         所有路径常量；改布局只改这里
  store.py         SQLite 唯一写入口 + 全部 schema
  indexer.py       从磁盘重建索引与封面（幂等）
  migrate.py       归档结构迁移（plan / apply / rollback）
  ingest.py        散装目录打包入库 + 元数据侧车
  resolve.py       未识别书架识别（串行节流回查上游）
  idres.py         识别裁决的读写（pending / chosen / skipped）
  governance.py    治理台账、破坏性守卫、promote 计划与执行
  import_scan.py / import_apply.py   导入扫描与落地
  api*.py          FastAPI 路由（library / resolve / import / prefs）
  health.py        存储体检
  upstream.py      上游查询（唯一联网模块）
web/               六个页面 + assets（无构建步骤，原生 JS）
tests/             契约测试
docs/aegis/        设计计划与证据归档（历史记录，非用户文档）
```

## 提交前检查

四条必须全绿。CI 跑的就是这四条，本地先过一遍能省一轮 review：

```bash
ruff check src/ tests/          # 或：ruff check --fix src/ tests/
ruff format --check src/ tests/  # 或：ruff format src/ tests/
mypy src/
pytest -q                        # 或：python -m unittest discover -s tests -t . -v
```

配置在 `pyproject.toml`：`line-length = 120`、`target-version = "py310"`、
`select = ["E","F","W","I","UP"]`、`ignore = ["E501"]`。

装了 pre-commit 的话：

```bash
pre-commit install
pre-commit run --all-files
```

**另外**：提交前确认没有夹带这些东西 —— 日志、`data/` 下的库文件、`library/` 里的归档、
Cookie / token、本地绝对路径（`E:\github\...` 这类）。`.gitignore` 挡住了大部分，但
`git add -f` 能绕过它，别那么干。

## 测试怎么写

- **不许联网。** 需要上游数据的地方，用手写的假响应，别打真接口。
- **不许碰真库。** 见上面「不要拿真库做实验」。
- **覆盖契约而不是覆盖行数。** 本项目最重要的测试是「这条规则没被绕过」，例如：
  - 破坏性动作拿不到 executed 状态
  - 伪造的 plan 自己算 token 会被拒
  - 短 Aid（`121`）能转正、重建后仍存在
  - 重建后用户列（收藏、书单、本地标题）原样保留

`tests/test_migrate.py` 的 `check_*` 函数是双跑设计：`python tests/test_migrate.py` 用
`.tmp/contracts` 下的临时树（受限沙箱里也能跑），`pytest` 则用 `tmp_path` fixture。
新测试建议沿用这个形状。

## 提交信息

格式：`<type>: <中文摘要>`，一行说清改了什么、为什么。

```
feat: 支持按社团名搜索未识别书架
fix: 标签在 jmlib index 后消失
docs: 补 migrate --rollback 的反向语义说明
test: 补短 Aid 转正后重建存活的契约
ci: 补 ruff 检查工作流
```

发版用 `vX.Y.Z: <中文摘要> (#PR)`。破坏性变更在正文里单独写一段，说明影响哪些已有数据。

## Pull Request

- 目标分支：`master` 或 `dev`。功能开发请开 `feat/**`、修 bug 开 `fix/**`。
- 一个 PR 只做一件事。混了重构和功能改动的 PR 很难评审，会被拆开。
- 描述里写清楚：**改了什么 / 为什么 / 怎么验证的**。贴关键命令输出，别写「已测试」。
- 动了数据行为的 PR，必须说明**怎么回滚**。
- CI 全绿才算就绪。

## 报告问题

- Bug 与功能建议走 issue 模板。
- 安全问题**不要**开公开 issue，走 [SECURITY.md](SECURITY.md)。
- 涉及具体书籍内容的问题（标题对不上、页数不符）请只提供 **Aid、标题、页数**，
  不要附图片或内容片段。
