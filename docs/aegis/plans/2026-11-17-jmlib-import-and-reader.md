# jmlib 全量导入 + 自添加 + 阅读增强 规格与实施计划（2026-11-17）

> Aegis Visibility: brainstorming（需求发现已交用户确认，用户选「按 A/B/C 写规格计划」）→ writing-plans 生成。
> 组合 ui-ux-governance（导入面板、ID 候选确认、阅读器新增交互）与 anti-entropy-governance（导入=持久化写入、
> 源目录退役属 confirmation-first 破坏性类别）。
> 本计划**只到规格与实施边界**：不搬文件、不写实现代码、不执行任何治理动作。实施需用户再次授权。

## Goal

在已交付的 jmlib 资源库（185 本 / 1474 章 / 861 未识别 / 61199 页）之上，补三段能力：

- **A. 全量导入 + 上游 ID 治理**：把 `E:\my world\pictures\JM` 里剩下 143 个目录（123 个新车号、3 个无 ID、1 个简繁分裂、16 个已入库）按同一落盘规范入库；名字可能变化，因此「连接上游搜索取 ID」必须产出**候选 + 分数 + 证据**，由用户逐本确认，绝不盲取第一条。
- **B. 自行添加漫画**：让用户不写脚本也能把新内容加进库（手工 Aid + 本地路径 / 投放到待入库目录后点扫描），并具备元数据编辑、收藏、合集、阅读列表。
- **C. 阅读增强**：阅读方向（RTL/LTR）、单页/双页、适应与缩放、进度条拖跳、上下章、预加载、连续滚动/Webtoon、多书签、阅读设置持久化、书库筛选与排序。

不改的：`{Aid}` 仍是唯一书主键；`library/archive` 仍是字节真相源；上游两仓库零改动；不引入 AI 层。

## Architecture

### 现有主干（不变）

- `library/archive/{Aid}/{Pindex}.zip` 字节真相源；`library/_unidentified/<原相对路径>` 无车号包；`library/covers/{Aid}/{Pindex}<suffix>` 派生封面。
- `data/jmlib.db` 逻辑真相源（books / chapters / memory / unresolved / findings）；唯一写入口 `src/jmlib/store.py`。
- `api.py` 单进程 FastAPI，`/api/page` 流式读单成员，不解压落盘；`--lan` 才有 PIN。

### 新增表面（四个新 owner，均需 Existence Check 批准）

| 新 owner | 职责 | 不做什么 |
| --- | --- | --- |
| `src/jmlib/import_scan.py` | 只读扫描待入库根目录 → 分类清单（new / existing / no_id / duplicate_group / magazine_like / flat / subdir），写 `data/import_manifest.json` | 不写库、不动文件、不联网 |
| `src/jmlib/upstream.py` | 封装 jmcomic `search_site/search_work` 取候选；query 变体生成、打分、缓存、限流、失败态；写 `data/upstream_cache.json` | 不自动定案、不下载、不在服务启动时联网 |
| `src/jmlib/idres.py` | ID 裁决记录（pending / resolved / skipped / manual），落 `data/jmlib.db` 新表 `id_resolution` | 不代替用户选择 |
| `src/jmlib/prefs.py` | 阅读设置与多书签（全局默认 + 每本覆盖），落新表 `reading_prefs` / `bookmarks` | 不碰 memory 语义 |

其余为既有 owner 的扩展：`store.py`（新表 + 元数据列）、`ingest.py`（flat 目录打包）、`api.py`（新端点）、`web/read.html`（阅读器重写）、`web/index.html`（筛选排序）、新页面 `web/import.html`。

### 边界变更（必须显式记录）

旧计划 `E:\github\jmcomic\jmlib\docs\aegis\plans\2026-11-10-jmlib-resource-library.md:218` 的 Scope Fence 写「不联网」。本计划**改写为**：

> 默认本地只读、不联网；**仅当用户在导入/解析流程中主动触发**时才访问上游搜索接口，且只发标题/关键字，不上传归档字节；候选不自动定案；请求有超时、限流与失败态；服务启动与普通浏览绝不联网。

## Tech Stack

- 沿用 Python 3.13 + FastAPI + uvicorn + 标准库 sqlite3 / zipfile / hashlib；前端原生 HTML/JS/CSS，无 npm 构建。
- 上游搜索用已克隆的 `E:\github\jmcomic\JMComic-Crawler-Python\src`（2.7.11）或已装 2.7.6，均提供 `search_site`。
- **不新增第三方依赖**：简繁/标点归一不用 opencc，改为「query 变体 + 词元重叠打分」（见 A.2）。

## Baseline / Authority Refs

- 用户需求：m03509（原文见下）、m03512（承诺只调研）、用户确认答复「确认，按 A/B/C 写规格计划（推荐）」。
- 需求原文：`E:\my world\pictures\JM内也全部移入书库，自动通过连接上游搜索名字获取id，名字可能有变化，E:\my world\pictures，同时要方便自行添加漫画到书库，阅读要增强，管理与阅读的需求你自己去寻找，找到后先让我确认`
- 上一轮交付基线：`docs/aegis/plans/2026-11-10-jmlib-resource-library.md`（P0–P5 已完成）+ `docs/aegis/work/2026-11-10-jmlib-resource-library/proof-bundle.md`。
- 现状实测（只读）：`store.stats()` = books 185 / chapters 1474 / memory **1** / unresolved 861 / findings 0 / page_total 61199；物理 archive zip 1474、unidentified 861、covers 1474、library 3,813 文件 / 27,726,245,828 bytes；治理 ledger 为空（executed 0 / rolled_back 0）。
- 待入库实测（只读）：`E:\my world\pictures` 顶层只有 `JM`；`JM` 下 143 个漫画目录 / 11,890 图 / 3,393,179,797 bytes（≈3.16 GiB）/ 0 zip；140 目录有显式 ID（139 唯一）、16 个已入库、123 个新车号、3 个无 ID、1 个重复 ID 组（`1454103` 简繁分裂）、7 个杂志期号风格目录、142 个 flat image 目录；章节形状 `{1:135, 3:1, 0:4, 6:1, 5:1, 2:1}`。
- 上游搜索契约（本地源码，精确行号）：
  - `JMComic-Crawler-Python/src/jmcomic/jm_client_interface.py:513` `class JmSearchAlbumClient`；`:546 def search_site(self, search_query: str, page: int = 1, order_by=ORDER_BY_LATEST, time=TIME_ALL, category=CATEGORY_ALL, sub_category=None)`；`:559 search_work` / `:572 search_author` / `:585 search_tag` / `:598 search_actor`；`:542` 原文「移动端不支持 category, sub_category」。
  - `jm_client_impl.py:354`（HTML）与 `:783`（APP）：`:383-385` 命中单本时 `wrap_single_album`；APP 侧 `:813-815` 用 `redirect_aid`。
  - `jm_entity.py:767 class JmSearchPage`、`:784 wrap_single_album`；`jm_toolkit.py:789 parse_api_to_search_page`（返回 `id/name/author/category`）。
  - 教程 `assets/docs/sources/tutorial/0_common_usage.md:142-153` 有 `client.search_site(search_query='+MANA +无修正', page=1)` 与直接搜车号 `search_site('427413')` 两种用法；`14_async_usage.md:141` 推荐多页用 `search_gen`。
- 功能参照（需求发现证据，非实现依赖）：[Kavita Comic/Manga Reader](https://wiki.kavitareader.com/guides/readers/comic-manga/)、[Komga 功能清单](https://raw.githubusercontent.com/openappssh/openapps/refs/heads/main/projects/media/komga.mdx)、[Tachiyomi Reading Modes](https://tachiyoomi.com/reading-modes-explained/)。
- 用户资产（只读副本，改动须先复制）：`option.yml`（sha256:dc82987f，注释段含邮箱明文凭据——**敏感，不读值、不落库、不进日志、不进提交**）、`JM打包.py`（sha256:d1450872）、`JM重复检查.py`（sha256:5a5ba93b，`SequenceMatcher` 阈值 0.8 仅作文件夹名近似先例）。

## Approved Scope（需求模型 A/B/C）

### A. 全量导入与 ID 治理

| 编号 | 需求 | 验收 |
| --- | --- | --- |
| A-1 | 扫描待入库根目录，产出分类清单（不写库） | 清单 JSON 含 123 new / 16 existing / 3 no_id / 1 dup group / 7 magazine_like；重跑结果稳定 |
| A-2 | 对无可靠车号或名字变化的目录，连接上游搜索取候选 | 每本列出 ≥1 候选（id / 上游名 / 分数 / 命中理由），可人工改 Aid、可跳过、可重试；失败给出原因而非静默 |
| A-3 | 用户逐本确认后入库，源目录零删除 | 每章一 zip、封面复用首图、`unresolved` 不再含该书、`data/import_rollback.json` 记录 old→new |
| A-4 | 冲突必须逐项显式决策 | 16 个已入库 ID 默认 `skip`，可选「仅补元数据 / 追加新章节 / 另存新 ID」；绝不静默覆盖 |
| A-5 | 简繁分裂与近似重复进合并队列 | `1454103` 两组归并为一本；「小海和暑假…」/「海妹妹和暑假…」列为疑似同本待确认 |

### B. 自行添加漫画

| 编号 | 需求 | 验收 |
| --- | --- | --- |
| B-1 | 手工添加：填 Aid（可自动搜）+ 本地路径 → 预览清单 → 确认入库 | 从填表到入库全程可取消；入库后书库立即可见可读 |
| B-2 | 投放式添加：把文件放进 `library/_incoming/`，点「扫描待入库」 | 扫描只读、列出待办；确认后入库；原始文件按约定保留 |
| B-3 | 元数据编辑：本地标题 / 别名 / 作者 / 标签 / 收藏 | 可改可清空；标题仍不作身份键；重索引不丢编辑 |
| B-4 | 合集 / 阅读列表 / 系列 | 可建、可加书、可排序；不影响 archive 字节 |

### C. 阅读增强

| 编号 | 需求 | 验收 |
| --- | --- | --- |
| C-1 | 阅读设置：方向 RTL/LTR、单/双页、适应/原尺寸/宽度、全屏、亮度（可选） | 设置为全局默认 + 每本覆盖；刷新后保持 |
| C-2 | 导航：进度条拖跳、直接跳页、上一章/下一章、首/末页、键盘与触屏 | 全部可用且有加载/错误/取消态 |
| C-3 | 预加载与性能：相邻页预取、切页不闪白 | 首次加载不退化；慢网有可行动错误与重试 |
| C-4 | 连续滚动 / Webtoon 模式 | 与单页模式可切换；记忆点在两种模式下都正确 |
| C-5 | 多书签 + 阅读历史 | 可增删、可跳转；与 memory（续读点）语义分离 |
| C-6 | 书库增强：标签/收藏/合集筛选，排序（最近读、标题、章节数） | 筛选结果与计数一致；空结果有明确说明 |

**明确不在本计划范围**：OPDS / 外部阅读器对接、多用户与账号、AI/MCP/jmcomic-ai、上传服务、后台常驻监控目录（watch daemon）、重打 25 GB 归档。

## Requirement Ready Check

```text
Requirement Ready Check:
- Requirement source refs: m03509（用户原文）、m03512（只调研承诺）、用户确认「按 A/B/C 写规格计划」
- Goals and scope refs: 本文件 Approved Scope A-1..A-5 / B-1..B-4 / C-1..C-6
- User / scenario refs: 单人本地使用；从"手动脚本 + 散落目录"迁到"面板里逐本确认入库 + 增强阅读"
- Requirement item refs: A/B/C 三组共 15 条，逐条带验收
- Acceptance / verification criteria refs: 见 Verification 与每条验收列
- Open blocker questions: 见 Open Decisions D-1..D-5（不阻塞规格定稿，阻塞对应任务的执行方式）
- Decision: ready
```

Open Decisions 只改「怎么做」，不改「做什么」；每条在执行前以 scoped confirmation 落地，故不阻断本规格批准。

## Change Necessity

```text
Change Necessity:
- User-visible need: 123 本待入库 + 名字变化的 ID 识别 + 自行添加 + 阅读增强，现有 CLI/页面均无入口
- No-change / non-code option: 让用户继续手写脚本 + 手工改 DB —— 已被用户明确否决（要求"方便自行添加"）
- Why code change is necessary: 需要新的扫描/候选/裁决/设置持久化 owner，且必须复用唯一写入口以免出现第二个 producer
- Minimum change boundary: 4 个新模块 + 4 张新表 + 约 12 个新端点 + 2 个页面改造；不动 archive 字节格式、不动 {Aid} 主键
- Decision: code-change
```

## Existence Check

```text
Existence Check:
- Proposed new surface: src/jmlib/import_scan.py
- Existing owner / reuse candidate: indexer.scan_catalog（只扫 library/archive）、ingest.loose_plan（只处理 P4 规格的散装目录，Aid 来自封面文件名）
- Why existing surface is insufficient: 待入库源在库外、含 flat 目录/无 ID/杂志目录/已入库冲突，indexer 只看库内；loose_plan 无分类与冲突模型
- Creation proof: 需要一份"库外 → 分类 → 清单"的只读投影，现有两个 owner 都锁定在库内或 P4 单一形态
- Entropy / retirement impact: 无退役；P4 的 loose_plan 仍服务于旧 16 本，新扫描器不复制其 Aid 解析逻辑（改为调用同一 `cover`/命名规则判定）
- Decision: add-with-proof

Existence Check:
- Proposed new surface: src/jmlib/upstream.py
- Existing owner / reuse candidate: 直接调 jmcomic `new_jm_client().search_site(...)`；仓库内无封装
- Why existing surface is insufficient: 需要 query 变体、打分、缓存、限流、超时、失败分类与"候选不定案"契约；直接调会把网络与业务判定散进 api.py
- Creation proof: 上游只给原始搜索结果，不含标题变化/汉化组后缀/简繁的匹配策略；该策略必须单点拥有并可测
- Entropy / retirement impact: 若将来换搜索源，只换此模块；不新增第二处联网点（api.py 只调它）
- Decision: add-with-proof

Existence Check:
- Proposed new surface: src/jmlib/idres.py + 表 id_resolution
- Existing owner / reuse candidate: findings 表（kind=... ）可勉强复用
- Why existing surface is insufficient: findings 语义是"体检发现 + 批准/驳回"，导入裁决需要 query/候选集/选中 id/时间/失败原因等结构化字段，混用会污染体检统计与 P3 面板
- Creation proof: 需要可重放的裁决历史（同一目录多次尝试、改判、跳过）
- Entropy / retirement impact: 不替代 findings；两者若冲突以 findings 为体检真相、idres 为导入真相，边界写进 docstring
- Decision: add-with-proof

Existence Check:
- Proposed new surface: src/jmlib/prefs.py + 表 reading_prefs / bookmarks
- Existing owner / reuse candidate: memory 表（单点续读）
- Why existing surface is insufficient: memory 是"读到哪里"的单一游标，被续读逻辑依赖；设置与多书签是不同生命周期，混入会破坏续读优先级与 P2 契约
- Creation proof: P2 的续读优先级（显式参数 > memory > 第一章）必须保持；书签可以有 N 条
- Entropy / retirement impact: memory 保持唯一续读真相；prefs 不得写 memory
- Decision: add-with-proof

Existence Check:
- Proposed new surface: 上传接口（接收用户浏览器拖入的 zip/图片）
- Existing owner / reuse candidate: library/_incoming/ 目录 + 扫描（B-2）
- Why existing surface is insufficient: 若用户不在本机、或想从别处传文件，投放目录不方便
- Creation proof: 暂无——且会打破 api.py「No uploads」边界、引入体积/解压炸弹/路径穿越风险
- Entropy / retirement impact: 高（新增公网可达写入口与配额治理）
- Decision: defer（本计划不做；B-1/B-2 已覆盖本机场景。若后续需要，单独立项并先做安全设计）
```

## Architecture Integrity Lens

- 不变量：`{Aid}` 是书身份；`library/archive` 是字节真相；`store.py` 是唯一 DB 写入口；标题只作展示/搜索。
- 责任重叠检查：导入的三个模块各自单一——扫描（只读投影）/ 上游（只取候选）/ 裁决（只记选择）；真正写库仍只有 `store.upsert_book_with_chapters` + `ingest.pack_album`。
- 更高层简化：A 与 B 共用同一条「清单 → 候选 → 确认 → 入库」管线，只是来源不同（库外目录 vs 用户手填 / `_incoming`）。计划把它们实现为同一组端点 + 两个 `source` 适配器，而不是两套流程。
- 旧路径退役：P4 的 `ingest --plan-only/--apply` CLI 被新管线覆盖后，保留为脚本入口但不再扩展；`loose_plan` 的 Aid 解析逻辑抽到共享函数，避免两处判定漂移。
- 证伪条件：若实现后仍存在"人肉改 DB 才能入库"的路径，则 A/B 失败。

## Compatibility Boundary

- 已有 185 本书、1474 章、861 unresolved、61199 页的 id/路径/封面**不得变化**；`memory=1` 是真实阅读痕迹，**不得静默覆盖或清空**（任何迁移脚本禁止 `DELETE FROM memory`）。
- 新表一律 `CREATE TABLE IF NOT EXISTS`；`schemaVersion` 从 `jmlib.db.v1` 升到 `jmlib.db.v2`，读取端对 v1 库必须能直接打开（缺表按空处理）。
- `findings` 与 P3 体检统计口径不变；P5 治理 ledger 不变。
- 上游两仓库（`JMComic-Crawler-Python`、`jmcomic-ai`）保持零改动、`git status` 干净。
- `option.yml` 只读：不解析注释段、不打印值、不写副本到库内。
- 源目录零删除：A 段只**读** `E:\my world\pictures\JM`；清理属 P5 型破坏性动作，需单独 scoped confirmation。

## Anti-Entropy Declaration

- 单一 owner：扫描 / 上游 / 裁决 / 设置四个新 owner，边界写进各自模块 docstring 首段；`store.py` 仍是唯一 DB 写入口。
- 无 fallback 扩张：上游搜索失败不静默降级为"猜 ID"；失败即失败态 + 可重试，人工输入是显式路径而非兜底。
- 无重复判定：Aid 解析只有一个实现，落地为 `src/jmlib/aid.py`（`AID_RE` 身份 1–7 位 / `HINT_RE` 自由文本弱证据 ≥4 位 / `COVER_FILE_RE`），其余模块只准导入，禁止第二次定义正则（2026-11-18 修正：原 4–7 位下限把真实专辑 121、579 拒于转正门外）。
- 删除零新增：本计划不新增任何删除逻辑；清理仍走 P5 governance。

```text
Data Destruction Guard:
- Target Class: persistent-state / irreplaceable source-of-truth bytes
- Exact Target(s): E:\my world\pictures\JM\{143 dirs}（用户原始素材，无第二份）；library/archive 既有 1474 zip
- Environment: 本机 Windows，DSH 沙箱 workspace-write；旧文件可能不带本进程授权
- Why Irreversible: 素材无备份，删除不可恢复
- Backup-Rollback Note: A 段全程只读源目录；入库写 data/import_rollback.json（old→new + 尺寸 + 校验）；回滚只移除本次新增
- Allowed Read-Only Next Steps: 扫描、候选查询、生成清单、预览
- Blocked Destructive Steps: 删除/移动源目录、覆盖既有 zip、清空 memory/findings、批量自动确认
- Confirmation Required: yes — 每个导入批次执行前逐项确认
- Status: awaiting scoped confirmation
```

## Retirement Decision

- 退役候选：`JM打包.py` 的人工打包步骤、`ingest --plan-only/--apply` 的手工散装流程、旧 `dir_rule` 直下（P4 已改）。
- 退役触发条件：A 段全量导入完成且 `jmlib index` 对账通过（books = 既有 185 + 新增确认数，无 broken zip）。
- 退役前不得删除用户脚本与旧目录；只标记"不再扩展"。

## Verification Plan

```text
Verification Plan:
- Main-path: 每个 Phase 结束后跑 python -m compileall -q src + P1/P2/P3/P4/P5 离线契约 + tests/test_migrate.py；
  新模块各配一个离线契约脚本（.tmp/test_import_scan.py / test_upstream.py / test_idres.py / test_prefs.py），
  全部用 scratch monkeypatch 重定向新 path 常量，绝不读写真实 25 GB 库
- Lingering-reference: 全仓 grep 确认无第二处 os.rename/网络调用/DB 写入；grep schemaVersion 处理分支；
  grep 'no external calls' 类 docstring 已随边界更新
- Negative: 上游超时/403/多条候选/零候选/名字完全不同；导入中途取消；重复确认；对已存在 Aid 再次导入；
  设置表缺失（v1 库）时页面不崩
- Boundary check: 导入前后 store.stats()/物理 zip 计数逐项对比；源目录 sha256 抽样不变；上游仓库 git status 干净；
  option.yml 未被读取值
```

## TDD Route

`off`（沿用项目既有 `off|skipped` 记录方式）。新模块仍须先写离线契约脚本再实现，作为回归护栏，但不作为 TDD 门禁。

## Complexity Budget

```text
Complexity Budget:
- Artifact class: source module / web page / schema
- Target files / artifacts: src/jmlib/{import_scan,upstream,idres,prefs}.py（新）、store.py(382)、api.py(334)、
  ingest.py(399)、web/read.html(295)、web/index.html、web/import.html（新）
- Current pressure: api.py 已有 20 条路由 / 334 行；read.html 295 行单页逻辑；store.py 382 行 7 个 P5 窄口
- Projected post-change pressure: api.py ≈ +12 路由 → 约 520 行（at-risk）；read.html 重写 ≈ 600 行（at-risk）；
  store.py ≈ +90 行（within-budget，因新表放独立模块，store 只加速查）
- Budget result: at-risk
- Planned governance: api.py 新路由抽到 `api_import.py` / `api_prefs.py` 并用 `app.include_router` 注册，
  保持 api.py 只做 app 装配与既有端点；read.html 拆为 read.html + read.js（纯静态，无构建）；
  每 Phase 结束跑一次文件体积复核，超 600 行即拆
```

```text
Plan-Time Complexity Check:
- Target files: src/jmlib/api.py, web/read.html, src/jmlib/store.py
- Existing size / shape signals: api.py 334 行单一文件、路由线性堆叠；read.html 内联 IIFE 全部逻辑；store.py 已含 P1–P5 五段
- Owner fit: 路由与页面都不适合再无限追加
- Add-in-place risk: 高（api.py 会变成上帝文件；read.html 会难以维护）
- Better file boundary: APIRouter 分模块（import/prefs）+ 阅读器脚本外置
- Recommendation: extract helper（新 owner 文件 + 路由拆分）
```

## Tasks

> 编号约定：**需求**用带连字符的 `A-1`…`C-6`（见 Approved Scope）；**任务**用带点的 `A.1`…`C.4`（本节）。
> 每批结束即可独立验收与回滚；批次之间不共享未完成状态。所有命令在 `E:\github\jmcomic\jmlib` 下、
> `$env:PYTHONPATH='src'; $env:PYTHONIOENCODING='utf-8'` 执行。

### Phase A — 全量导入与 ID 治理

**A.1 只读扫描与分类（新 `src/jmlib/import_scan.py`）**
- 目的：把「库外目录」投影成可审阅清单，零写入。
- 改动：`classify(root) -> dict`（逐目录判定 explicit_id / flat / subdir / chapter_shape / magazine_like / bytes），`write_manifest(manifest, path)`，Aid 解析复用共享函数（从 `ingest` 抽出 `aid_from_dirname`）。
- 输出：`data/import_manifest.json`（含 generated_at、root、逐本 class/suggested_aid/evidence、summary 计数）。
- 验证：`.tmp/test_import_scan.py`（scratch 合成 5 类目录）→ 期望 123 new / 16 existing / 3 no_id / 1 dup / 7 magazine_like 与真实盘点一致；重跑幂等。
- 兼容：不触碰 library 与 DB。

**A.2 上游候选服务（新 `src/jmlib/upstream.py`）**
- 目的：给定目录名，产出**带分数与理由**的候选列表。
- 改动：`variants(title) -> list[str]`（去 `[...]` 汉化组/期号括号、去扩展名、去 `(C102)`、简繁常见字映射表 ≤50 对、去标点重复）；`search(title, limit=8) -> dict`（调 `search_site`，`timeout`/重试 1 次/限流 ≥1.5s/失败分类 `timeout|blocked|empty|error`）；`score(query, candidate) -> dict`（字符 bigram 重叠 + 词元包含 + 作者/标签命中 + 长度惩罚，返回 0..1 与 hits）；缓存键 = 归一化 query，落 `data/upstream_cache.json`（含时间戳，24h 内复用）。
- 验证：`.tmp/test_upstream.py` 用注入的 fake client（不打网络）验证：变体生成、打分排序把完全同名排第一、完全不同返回低分且 `auto_ok=False`、超时/异常返回结构化失败而非抛栈。
- 边界：模块 docstring 首段写明「仅用户触发；不自动定案；不下载」。

**A.3 裁决持久化（新 `src/jmlib/idres.py` + 表 `id_resolution`）**
- 改动：`store.py` 加 `CREATE TABLE IF NOT EXISTS id_resolution(id, rel_path, query, candidates_json, chosen_aid, state, note, created_at, updated_at)`；`idres.record(rel_path, query, candidates, state)`、`idres.set_choice(rel_path, aid, note)`、`idres.list(state=None)`、`idres.skip(rel_path, reason)`。
- 验证：`.tmp/test_idres.py`：同一 rel_path 二次记录不重复堆行（upsert）、改判留痕、跳过不放候选。
- 兼容：v1 库缺表时 `idres.list()` 返回 `[]` 不抛。

**A.4 导入面板（新 `web/import.html` + `src/jmlib/api_import.py`）**
- 端点：`GET /import`、`POST /api/import/scan`、`GET /api/import/manifest`、`POST /api/import/candidates`、`POST /api/import/resolve`、`POST /api/import/plan`（服务端签发 confirmation token）、`POST /api/import/apply`（要求精确 token，复用 governance 的门禁模式）。
- UI：清单表（分类 chip + 建议 Aid + 状态）、候选卡片（id/上游名/分数/理由 + 采用/改填/跳过/重试）、底部"生成导入清单 → 显示 token → 输入 token 才可执行"。
- 交互态：扫描中禁用按钮、空结果说明、失败可重试、`dialog` 可 Esc 关闭且不丢已填。
- 验证：真实浏览器（`/import`）+ `.tmp/test_import_api.py`（TestClient，scratch 路径）。
- 兼容：所有新端点位于 `pin_gate` 之后。

**A.5 执行入库（新 `src/jmlib/import_apply.py`）**
- 改动：`apply(plan, confirm)`：逐本 `ingest.pack_album`（扩展支持 flat 目录：按文件名自然序生成 `00001<suffix>`）、`store.upsert_book_with_chapters`、`ingest.put_meta`、写 `data/import_rollback.json`；已存在 `{Aid}/` 默认 skip 并记录；简繁重复目录合并为一次打包；失败即停并回报已成功/未处理清单。
- 验证：`.tmp/test_import_apply.py`（scratch）：flat 22 图 → 1 章 zip 22 成员；已存在 Aid 不覆盖；rollback 仅移除本次新增且源目录仍在；`store.stats()` 增量与清单一致。
- 兼容：源目录零删除（执行前后递归 listing 与总字节一致）。

**A.6 冲突与近似重复队列（并入 A.3/A.4）**
- `1454103` 两组 → 合并为一本（正文 + 首图补封面）。
- 「小海和暑假…」/「海妹妹和暑假…」→ 标记 `suspected_same`，默认不自动合并，等用户选「同一本 / 两本」。
- 7 个杂志期号目录 → 强制人工确认（不得把期号当车号）。

### Phase B — 自行添加漫画

**B.1 手工添加（`web/import.html` 第二个 pane + `api_import.py`）**
- 表单：Aid（可点"搜索确认"复用 A.2）+ 本地绝对路径（服务端校验该路径在允许根白名单内，防任意路径读取）+ 标题。
- 输出：预览（章节数/图数）→ 生成清单 → token → 执行。
- 验证：路径越界被拒（`../../` 与白名单外盘符）；不存在的路径给出明确错误；成功后书库可见。

**B.2 投放式添加（`library/_incoming/`）**
- `POST /api/import/incoming/scan`：只读列举 `_incoming` 下的 zip 与图片目录，识别 Aid（名字含 1–7 位数字，见 `jmlib.aid`）或标 `unidentified`；纳入同一管线。
- 验证：空目录返回空清单且提示"把文件放到 library/_incoming/ 后重扫"。

**B.3 元数据编辑（`books` 表加列 + `api_import.py`/`api.py`）**
- 新列（`ALTER TABLE ... ADD COLUMN`，缺失则补）：`local_title`、`aliases`(JSON)、`favorite`(0/1)、`collection`。
- 端点：`PATCH /api/books/{aid}`（仅白名单字段）、`POST /api/books/{aid}/favorite`。
- 保证：`indexer.rebuild()` 的 `upsert_book_with_chapters` 不得清空用户编辑（与 P4 的 sidecar overlay 同思路：DB 列为准，sidecar 仅补缺）。
- 验证：改标题后 `jmlib index` 不丢；搜索能命中别名。

**B.4 合集 / 阅读列表（新表 `collections` / `collection_items`）**
- 端点：列表 CRUD + 加减书 + 排序；书库页筛选入口。
- 验证：加书重复幂等；删合集不删书。

### Phase C — 阅读增强

**C.1 设置与书签持久化（新 `src/jmlib/prefs.py` + 表 `reading_prefs` / `bookmarks`）**
- `reading_prefs(aid PK|'' 全局, direction, layout, fit, fullscreen, updated_at)`；`bookmarks(id, aid, pindex, image_index, note, created_at)`。
- 端点：`GET/PUT /api/prefs/{aid|_global}`、`GET/POST/DELETE /api/bookmarks`。
- 验证：全局默认 + 每本覆盖的解析优先级（每本 > 全局 > 内置默认）；memory 语义不变。

**C.2 阅读器重写（`web/read.html` + `web/read.js`）**
- 保留：单页流式加载、键盘（←/→/空格/Home/End/Esc）、触屏滑动、加载/错误/取消态、续读优先级。
- 新增：方向切换（RTL 时 ←/→ 语义反转、双页时 RTL 先右后左）、单/双页（`double_cut` 不做）、fit 三档、全屏、进度条（拖动/点击跳页）、跳页输入、上下章按钮、相邻页预取（`<link rel=prefetch>` 或 `new Image()`，上限 2）。
- 验证：真实浏览器逐项手测 + 窄屏 750px 无横向溢出；RTL 下键盘与滑动方向一致；错误态可重试。

**C.3 连续滚动 / Webtoon（`web/read.js` 第二模式）**
- 竖向容器懒加载（IntersectionObserver），滚动位置映射回 image_index 并节流写 memory（≥2s）。
- 验证：切模式后位置不丢；长章（177 页）滚动流畅；退出再进续读正确。

**C.4 书库增强（`web/index.html` + `api.py`）**
- 筛选：标签 / 收藏 / 合集；排序：最近读（memory）、标题、章节数。
- 验证：筛选计数与结果一致；空结果有说明；不影响既有封面墙性能（185 本，无需分页）。

## Verification

1. 每批：`python -m compileall -q src` + 该批 `.tmp/test_*.py` 全绿。
2. 每 Phase 收口：`test_p1_api.py` / `test_p2_api.py` / `test_p3_api.py` / `test_p4_ingest.py` / `test_p5_governance.py` / `tests/test_migrate.py` 全绿。
3. `jmlib index` 重建后 `store.stats()` 与物理计数一致，`BAD_MEMBERS=[]`、`COVER_MISMATCH=[]`。
4. 真实浏览器：`/`（筛选用例）、`/import`（扫描→候选→token→执行→取消）、`/read`（各模式与错误态）、`/health`、`/governance` 不回归。
5. 边界：源目录零变化；上游仓库 `git status --porcelain` 为空；option.yml 未被读取值。

## Risks

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 上游搜索被反爬/限流/改域名 | 候选拿不到 | 结构化失败 + 可重试 + 手工录入 Aid；缓存 24h；请求间隔 ≥1.5s |
| 名字变化导致误配 | 错书入库 | 候选必须人工确认；分数与理由可见；`auto_ok` 仅在同名且唯一时为真，仍不自动执行 |
| 16 个已入库 ID 被覆盖 | 丢数据 | 默认 skip；覆盖需显式选择且先写 rollback |
| 3 个无 ID 目录判错 | 错 Aid | 强制人工；期号样式禁止当车号 |
| 阅读器重写回归 | 已有功能坏 | 先冻结 P2 契约测试；重写期间逐项对照键盘/触屏/续读用例 |
| 旧 zip ACL 限制（P5 已遇） | 转正/改名失败 | 复用 P5 的结构化拒绝文案；需要时以完全权限启动 |
| api.py 膨胀 | 难维护 | Router 拆分 + 600 行阈值 |
| 25 GB 唯一副本 | 不可逆 | 全程零删除 + rollback manifest + confirmation-first |

## Open Decisions（执行前需用户裁决，不阻断本规格批准）

- **D-1 导入根范围**：`E:\my world\pictures`（含未来其它子目录）还是只 `...\pictures\JM`？默认：只 `JM`，其余需显式添加。
- **D-2 源目录去向**：入库后源目录保持原样（默认，零删除）／由用户确认后归档到 `library/_incoming/`／删除（需 P5 级 scoped confirmation）。
- **D-3 16 个已入库 ID 默认策略**：skip（默认）／仅补元数据／按章节数比较后追加缺章。
- **D-4 两个近似无 ID 目录**：「小海和暑假…」与「海妹妹和暑假…」是同一本还是两本？
- **D-5 上传接口是否需要**：本计划 `defer`；如需要，另立项做安全设计（体积上限、类型白名单、解压炸弹防护、配额）。

## Repair Track

- 导入失败可原地重跑（清单幂等）；已写入的章节可用 `import_rollback.json` 精确反向（移除新增 zip/封面/DB 行，恢复 unresolved）。
- 阅读器新增模式的显示异常回退到单页模式即可用，不阻塞其他功能。

## Execution Readiness View

```text
Execution Readiness View:
- Intent Lock: 用户 m03509 原文 + 确认「按 A/B/C 写规格计划」；本文件 Approved Scope 为唯一范围
- Scope Fence: 不做 OPDS/多用户/AI/上传/常驻监控/重打 25 GB；不自动删除源目录；不自动定案 ID
- Baseline Lock: 2026-11-10 plan + proof-bundle；store.stats() 185/1474/1/861/0/61199；archive zip 1474；unresolved 861
- Approved Behavior: A-1..A-5 / B-1..B-4 / C-1..C-6 的验收列
- Owner / Contract Constraints: store.py 唯一 DB 写入口；四个新 owner 各自单一职责；memory/findings/ledger 语义不变
- Compatibility Boundary: schemaVersion v1→v2 可读；既有 185 本零改动；上游仓库零改动；option.yml 只读
- Retirement Boundary: JM打包.py 与 ingest 手工流程标"不再扩展"，触发条件=全量导入对账通过；不删除
- Task Batches: A.1–A.6 / B.1–B.4 / C.1–C.4，每批独立验收
- Test Obligations: 每批离线契约 + 每 Phase 全量回归 + 真实浏览器证据 + 边界检查（源目录/仓库/凭据）
- Review Gates: 用户批准本规格 → 每批执行前 scoped confirmation（导入批次尤其）→ 每 Phase 收口复核
- Drift / Rewind Rules: 出现"必须改 archive 字节格式"或"必须自动删除"即停，回设计
- Evidence Required Before Completion: 全量回归输出、浏览器手测记录、对账数字、未验准则声明
- Advisory Boundary: method-pack execution guidance only; not GateDecision, PolicySnapshot, or completion authority
```

## Execution Route

```text
Execution Route:
- Decision: inline
- Evidence: 单仓单进程、批次间共享 DB 与 library 状态、需串行验证；无独立可并行子任务
- Fallback: 若某批需长时间真实扫描，可拆出一次只读扫描作为独立步骤，但仍由主会话执行
- User confirmation required: yes — 实施批准（本文件仅为规格）+ D-1..D-5 默认值裁决 + 每个导入批次的 scoped confirmation
```

## Self-Review

- 每条需求都映射到任务与验证：A-1→A.1、A-2→A.2、A-3→A.5、A-4→A.5/A.6、A-5→A.6、B-1→B.1、B-2→B.2、B-3→B.3、B-4→B.4、C-1→C.1/C.2、C-2→C.2、C-3→C.2、C-4→C.3、C-5→C.1、C-6→C.4。无悬空需求。
- 无 placeholder：所有新文件路径、表名、端点、列名、阈值（600 行 / 1.5s / 24h / 预取 2 页）均具体。
- 未批准范围未扩张：上传、OPDS、多用户、AI 明确排除。
- 破坏性动作未进入任务：A 段只有读取与写入新书，无删除；清理另立 P5 流程。
