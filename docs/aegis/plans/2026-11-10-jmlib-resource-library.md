# jmlib 资源库实施计划（2026-11-10）

> Aegis Visibility: 本计划由 brainstorming（设计已定稿）→ writing-plans 生成，组合 ui-ux-governance（新 UI 表面）与 anti-entropy-governance（旧路径退役 / 25.3 GB persistent-state 迁移，confirmation-first）。

## Goal

给用户 25.3 GB 的章节 zip 归档 + 16 本散装待入库队列，建一个纯本地、无 AI 层的「资源库」：物理上全部收进项目目录统一管理，逻辑上只给有车号的建书，提供封面墙 / 搜索 / 阅读 / 续读 / 存储体检，并让以后每次 jmcomic 下载自动按同规范入库。

## Architecture

- 独立新项目 `jmlib/`（不塞进两个已克隆 upstream 仓库）。目录：
  - `jmlib/library/archive/{Aid}/{Pindex}.zip` — 规范化章节包
  - `jmlib/library/_unidentified/<原相对路径>` — 无车号包
  - `jmlib/library/covers/{Aid}/{Pindex}.jpg` — 章节封面（zip 首成员字节原样）
  - `jmlib/data/jmlib.db` — SQLite 索引 + 记忆点 + 未识别 + 体检
  - `jmlib/src/` — store / indexer / migrate / cover / api / cli / jmcomic 插件
  - `jmlib/web/` — 单页前端；`jmlib/docs/` — 计划与规格
- canonical owner：`jmlib.db` 是「书 / 章节 / 记忆点 / 未识别 / 体检」唯一逻辑真相源；`library/archive` 是字节唯一真相源；标题仅作展示与搜索、永不作键。
- 唯一写入口 `src/store.py`：迁移器与新下载插件都经它写 DB / 落 zip，禁止各自拼 SQL 或路径（消除双 producer）。
- 阅读器用 `zipfile` 流式读单个成员，不整包解压、不落盘解包。
- 8 个 `(1)` 世代目录在 P0 一次性重整时拉平：1455 有车号包按 `{Aid}/{Pindex}.zip` 归位，861 无车号包保留 `<原相对路径>` 进 `_unidentified/`；`(1)` 世代名不再作任何逻辑键、只留在 manifest / 来源字段供追溯。

## Tech Stack

- Python 3.13 + FastAPI + uvicorn + 标准库 sqlite3 / zipfile / hashlib；前端单页原生 HTML/JS/CSS，无 npm 构建。
- jmcomic（本机已装 2.7.6 / 上游 2.7.11；jmlib 用 `uv` 固定 `jmcomic>=2.7.11`）提供下载 + ZipPlugin + 自定义入库插件。
- 运行 `jmlib serve`（默认 127.0.0.1:8765，可选 `--lan` + PIN），按需启动、非常驻、不联网上传。

## Baseline / Authority Refs

- 用户需求：m00245（做资源库，明确排除 AI 层）、m00269（还要管理存储）、m00285（两个脚本处理过）；Q1 接管落盘规范 / Q2 纯车号+浏览器书库 / Q3 每章节一 zip + 记忆点 + 封面复用 / Q4 先只做有车号的 / 「要全部挪到项目下管理」/ Q5 搬进来并一次性重整；设计规格 m00322 + 最终修正 m00335。
- 用户资产（均为只读副本，改动须先复制到可写位置）：
  - `option.yml`（sha256:dc82987f）—— 用户实际在用配置；注释段含邮箱明文凭据，**敏感，不入库 / 不进日志 / 不进任何提交**。
  - `JM打包.py`（sha256:d1450872）、`JM重复检查.py`（sha256:5a5ba93b）—— 章节打包 + 内容签名去重 / 文件夹名相似度查重。
- jmcomic 上游契约（已核实，精确行号）：
  - `JMComic-Crawler-Python/src/jmcomic/jm_plugin.py:451-658` ZipPlugin；after_photo→每章一包（`:525-529`）；条目 `00001.jpg`（`:544-561`）。
  - `JMComic-Crawler-Python/src/jmcomic/jm_downloader.py:232-266`：`after_album` 仅传 `album`、`after_photo` 仅传 `photo` ⇒ 每章一包必须挂 `after_photo`，`after_album` 留给自定义入库插件。
  - `JMComic-Crawler-Python/src/jmcomic/jm_plugin.py:249-278` ZipPlugin `dir_rule` 覆写；`jm_option.py:63-177` DirRule DSL 支持 `Bd/{Aid}/{Pindex}.zip`。
  - `JMComic-Crawler-Python/src/jmcomic/jm_entity.py:176-198`（`{Aid}`/`{Pindex}` 来自 get_properties_dict）、`:375-386`（`Pindex` = 1-based album_index）。
  - `JMComic-Crawler-Python/assets/docs/sources/option_file_syntax.md:264-294` 官方配置文档。
  - `JMComic-Crawler-Python/assets/docs/sources/tutorial/6_plugin.md:122-169` 自定义插件须 `JmModuleConfig.register_plugin` 先于 `create_option` ⇒ 直接 `jmcomic` CLI 不加载，需要 `jmlib download` 包装入口。
  - `JMComic-Crawler-Python/src/jmcomic/jm_plugin.py:204-219` `execute_deletion` 仅在 `delete_original_file=True` 时删、且跳过非空目录（安全）。
- 磁盘事实（只读探针）：`E:\my world\pictures(1)` = 2316 zip / ~25.3 GB / 8 世代 / 0 松散图；`STRICT_AID 1455 / 期号误判上界 85 / STILL_NONE 776`；跨世代 0 完全相同重复包；`E:\my world\pictures\JM` 16 本散装 225 MiB、与归档车号零重叠；同卷（rename 秒级）、E: 空闲 84.2 GB、2316 相对路径全唯一、`AID_IN_MULTI_GEN 0`。
- Aegis：writing-plans SKILL.md + expanded-planning-guidance.md、ui-ux-governance references、anti-entropy-governance、AEGIS_MINIMALITY_REFERENCE.md、aegis-workspace helper。

## Requirement Ready Check

- Requirement source refs: m00245 / m00269 / m00285 + Q1–Q5 + 规格 m00322 + 修正 m00335。
- Goals and scope refs: 规格 §1–§10。User / scenario refs: 本地个人库、封面墙→阅读→续读、体检面板治理。
- Acceptance / verification criteria refs: 规格 §10 + 本计划 §Verification。
- Open blocker questions: 无（Q4 / Q5 已裁决）。**Decision: ready**。

## Change Necessity

- User-visible need: 25 GB 归档目前只有文件，无索引 / 封面 / 进度 / 阅读入口，且分散在工作区外的世代目录。
- No-change / non-code option: 仅改 option.yml 或写文档无法提供索引、封面墙、阅读器、记忆点、体检，也无法把库收进项目。
- Why code change is necessary: 需要新建索引器 / 迁移器 / API / 前端 / jmcomic 插件。
- Minimum change boundary: 只新建独立 `jmlib/` 项目；对上游两仓库零改动；对用户 option.yml 只改 `dir_rule` / `plugins` 行且保留注释凭据段。
- **Decision: code-change**。

## Existence Check（新表面）

- Proposed new surface: `jmlib/` 全套（索引 DB、迁移器、serve API、单页前端、自定义 jmcomic 插件、CLI）。
- Existing owner / reuse candidate: jmcomic ZipPlugin（打包）、DirRule（落盘 DSL）、jm-view-server（阅读，Q2 用户否决）、用户两个脚本（被迁移 / 索引吸收）。
- Why existing surface is insufficient: 上游无本地持久化索引 / 记忆点 / 封面缓存 / 体检 / 统一物理布局；jm-view-server 被用户明确不采用。
- Creation proof: 磁盘事实 + 上游契约行号 + 5 轮用户裁决。
- Entropy / retirement impact: 退役 `E:\my world\pictures(1)` 旧路径（persistent-state，confirmation-first）；退役 option.yml 的 `download_cover` 与旧 `dir_rule`；退役两手工脚本的日常用途（保留为参考）。
- **Decision: add-with-proof**。

## Ripple Signal Triage

- 命中：新 owner / schema / persistence / migration / producer-consumer / retirement。
- canonical owner：`jmlib.db`（逻辑）+ `library/archive`（字节）；producer = `jmlib index` + `jmlib download` 插件；consumer = `jmlib serve` / 前端 / 体检面板。
- 双 producer 风险：迁移器与新下载都能写 DB / 落 zip ⇒ 统一收敛到 `src/store.py` 单一写入口，不保留两套路径逻辑。
- source-of-truth 变更：物理路径从 `E:\my world\pictures(1)\<世代>\...` 退役 ⇒ 先出全量 manifest、一次确认、反向 rename 回滚。
- 验证扩展：迁移前后 zip 数量 / 大小 / 首成员数一致性、`_zip_signatures.json` 2316 key 保留 + value 重绑定、DB↔磁盘对账。

## Compatibility Boundary

- 上游两仓库：零改动（只读依赖，git 保持 clean）。
- 用户 option.yml：只改 `dir_rule` / `plugins`；注释段邮箱凭据原样保留，不入库 / 不进日志 / 不进提交；任何改动落在**用户确认后的副本**上，永不写只读附件。
- `_zip_signatures.json`：2316 条内容哈希 key 保留、value 相对路径按 manifest 重绑定，随迁保留；`黑名单.csv` 随迁保留。
- 861 无车号包：进 `_unidentified/`、不建书不抽封面不进封面墙，`unresolved` 表 + 手工指定 Aid 转正入口。
- 同 Aid 同章号冲突：绝不覆盖，记 `findings` 保留双份。
- 16 本散装：打包入库后原目录保留到用户确认才清理。

## Anti-Entropy Declaration / Retirement Decision

- Deletion Class: **persistent-state**；Old Path: `E:\my world\pictures(1)`（2316 zip / 25.3 GB / 8 世代）；New Canonical Owner: `E:\github\jmcomic\jmlib\library\`。
- Invalid Responsibility: 旧路径继续作为库的唯一副本与 source-of-truth。Legitimate Capability Remaining: 迁移前仍可读、可体检。
- Expected Preserved Behavior: 全部 2316 包字节、签名 key、黑名单、去重知识。Expected Retired Behavior: 世代 `(1)` 路径与工作区外的库定位。
- External Boundary Touched: 用户归档目录（工作区之外）；Source-of-Truth Data Risk: 25.3 GB 唯一副本迁移。User Confirmation Required: yes。
- **Path: confirmation-first**；Why: 退役旧路径 + 一次性重整属不可逆 source-of-truth 操作。
- **Non-edits（本计划阶段）: 不执行任何删除 / 搬移**；迁移任务 P0 只生成清单与门禁。
- Data Destruction Guard:
  - Target Class: persistent-state
  - Exact Target(s): `E:\my world\pictures(1)`（2316 zip + `_zip_signatures.json` + `黑名单.csv`）
  - Environment: 本机 E: 卷（工作区外，可能触发一次提权确认）
  - Why Irreversible: 原路径消失 + 一次性重整；未见全量 manifest 前不可执行
  - Backup / Rollback Note: 同卷 rename（秒级）+ `data/migration_manifest.json` 全量 old→new（2316 + 16）+ 反向 rename 回滚；迁移不删除任何 zip
  - Allowed Read-Only Next Steps: 生成 manifest、对账、体检报告
  - Blocked Destructive Steps: 任何 move / delete / 覆盖 / 原目录清理，直到 scoped confirmation
  - Confirmation Required: yes — Status: awaiting scoped confirmation

## Verification Plan（anti-entropy）

- Main-path check: 迁移后 `library/archive` 下 1455 zip、`_unidentified` 下 861，总数 2316，逐文件 size + 首成员数一致。
- Lingering-reference check: 全项目 grep 无 `E:\my world\pictures(1)` 硬编码（除 manifest / 回滚记录）。
- Negative check: 无车号包不出现在 books / chapters / 封面墙；冲突包不覆盖。
- Boundary check: option.yml 注释凭据段未被读取 / 写入日志；`library/`、`data/`、日志、导出全部 `.gitignore`。

## TDD Route

- mode: `off` | decision: `skipped` | authority: 无用户 / 项目显式 TDD 请求、会话无 `TDD Route: strict`。
- test posture: 最小改动 + 诊断复现 / 改后回归；verification: 各 Task 验证命令 + 全量对账 + 浏览器手测。
- 不写 RED / GREEN / REFACTOR 步骤。

## Complexity Budget / Plan-Time Complexity Check

- 新增模块约 9 个源文件（store / indexer / migrate / cover / api / cli / jmcomic 插件 / 体检 / 配置）+ 1 单页前端 + 1 DB schema。
- Owner 单一（`store` 写入口），无重复 owner、无 fallback / adapter、无 compat 分支。
- Verdict: 预算合理，分期交付（P0–P5），每期独立验收；不把复杂度藏进 add-in-place。

## Plan Pressure Test

- Owner / contract / retirement: 单一 owner + 显式退役（confirmation-first）。
- Architecture integrity / higher-level path: P0 一次性重整把 8 世代物理归位为 `{Aid}/{Pindex}.zip` 规范树 + `_unidentified/`；世代名仅留 manifest 追溯；无更简路径能同时满足「全部挪到项目下」与「只给有车号的建书」。
- Verification scope: 迁移对账 + UI 状态 / 恢复 + 续读持久化 + 未识别负向。
- Task executability: 每期精确文件 + 命令。
- **Pressure result: proceed**。

## Verification（整体验收）

1. 约 170 本（1455 包）+ 16 本散装可列可见。
2. 点开能翻页，重启 `serve` 后记忆点仍在、跳回原 (章节, 图片)。
3. 861 未识别可在体检 / 未识别页查询并导出 CSV。
4. 体检报告可读、每项修复逐项确认可回滚。
5. 新下载一本自动按 `{Aid}/{Pindex}.zip` + 封面 + DB 入库，零额外联网。
6. 全程无 zip 丢失 / 覆盖；`_zip_signatures.json` key 全保留。
7. 上游两仓库 git clean；option.yml 凭据段原样。
8. UI 状态（initial / loading / empty / partial / error / cancel / retry）与恢复、键盘 / 窄屏手测有记录，未验准则如实声明。

## Tasks

### P0 — 迁移（confirmation-first，仅清单与门禁，不执行搬移）

- **T0.1** `jmlib/` 骨架：`.gitignore`（忽略 `library/`、`data/`、日志、导出、`__pycache__`）、`pyproject.toml`（uv / fastapi / uvicorn / `jmcomic>=2.7.11`）、`src/`、`web/`、`docs/aegis/plans/`（本计划）。
  - 目的：可运行的最小工程边界。兼容性：不动上游两仓库、不动用户任何文件。
  - 验证：`uv sync` 成功；两 upstream 仓库 `git status` clean。
- **T0.2** `src/migrate.py --plan` 生成 `data/migration_manifest.json`：2316 条 old→new + 16 本散装，含 size / 首成员数 / `_zip_signatures.json` value 重绑定表 / 冲突与未识别分组；同步生成 `data/rollback_manifest.json`（反向 rename）。
  - 目的：把不可逆操作变成可审阅清单。兼容性：只读源、零写入源。
  - 验证：manifest 计数 `1455 + 861 + 16`，逐条 size 与源一致；对账用例通过。
- **T0.3 门禁**：向用户展示完整清单 + Data Destruction Guard，取得 **scoped confirmation** 后才允许 `jmlib migrate --apply`。
  - 目的：满足 confirmation-first。兼容性：未确认时 `--apply` 直接拒绝。
  - 验证：未确认执行 `--apply` 报错退出；确认后 dry-run 与实跑条数一致。
- **T0.4**（确认后）`jmlib migrate --apply`：同卷 rename 整目录进 `library/` → 重整 1455 包成 `{Aid}/{Pindex}.zip`、861 进 `_unidentified/`、签名表 / 黑名单随迁并重写 value。
  - 目的：物理全受管 + 一次性重整。兼容性：原路径消失；不删除任何 zip；冲突保留双份记 findings。
  - 验证：迁移后总数 2316、无覆盖、`library/` 不进 git；Lingering-reference check 通过。

### P1 — 索引 + 封面 + 封面墙 / 搜索

- **T1.1** `src/store.py`：SQLite schema（books / chapters / memory / unresolved / findings）+ 单一写入口。
- **T1.2** `src/indexer.py`：扫 `library/archive` 建书 / 章节（路径取 Aid / Pindex，页数取 zip 内条目数），`_unidentified` 写 unresolved。
- **T1.3** `src/cover.py`：从每个 zip 首成员 `00001.jpg` 字节原样写 `library/covers/{Aid}/{Pindex}.jpg`；书封面 = 第一章封面指针（不新增文件、不走 download_cover、不联网）。
- **T1.4** `src/api.py` + `web/index.html`：封面墙、搜索（标题 / 车号）、标签筛选；UI 状态 initial / loading / empty / error / partial。
  - 验证：`jmlib index` 后 DB `books≈170 / chapters=1455 / unresolved=861`；`jmlib serve` 封面墙显示、搜索命中、缺封面占位不崩。

### P2 — 阅读器 + 记忆点

- **T2.1** `/api/page?aid=&pindex=&i=` 流式读单个 zip 成员（不落盘解包），带 Range / 错误态。
- **T2.2** 阅读页翻页、触屏、键盘；翻页写 `memory`（一本书一条 (chapter, image)）。
- **T2.3** 重开跳回记忆点；错误 / 加载 / 取消态与恢复（ui-ux-governance）。
  - 验证：`curl /api/page` 返回正确 jpg 字节；翻页→重启→跳回同一 (章节, 图片)；UI 手测状态与恢复。

### P3 — 存储体检面板

- **T3.1** 只读报告：简繁分裂 / 缺封面 / 同本内重复页 / 8 世代已拉平 / 861 未识别 / 散装队列状态。
- **T3.2** 每项修复单独列变更清单 → 确认 → 执行 → 回滚记录（findings 状态机）。
  - 验证：报告可读、导出 CSV；未确认不执行；恢复态手测。

### P4 — 新下载链路 + 散装入库

- **T4.1** `src/jm_plugin.py` 自定义 after_album 插件（从 JmAlbumDetail 写 DB）+ `jmlib download` 包装（先 `JmModuleConfig.register_plugin` 再 `create_option` / API）；写入统一走 `store.py`。
- **T4.2** option.yml（**用户确认后的副本**）：顶层 `dir_rule` 是**图片暂存目录** `{base_dir: <jmlib/data/staging>, rule: 'Bd/{Aid}/{Pindex}'}`（目录段不带 `.zip`）；`plugins.after_photo` 挂内置 `zip` 插件，并用插件自己的 `kwargs.dir_rule = {base_dir: <jmlib/library/archive>, rule: 'Bd/{Aid}/{Pindex}.zip'}` 覆写 **zip 输出路径**；`plugins.after_album` 挂自定义 `jmlib_index` 插件（从 JmAlbumDetail 写 DB、从已生成的 zip 首成员抽封面、确认全部 zip 有效后清暂存）；停用 `download_cover`；注释凭据段原样。依据 `jm_plugin.py:249-278`（ZipPlugin 的 dir_rule 覆写优先于 filename_rule/zip_dir/suffix）、`option_file_syntax.md:264-285`（after_photo→每章一包、dir_rule 示例）、`jm_downloader.py:232-266`（两个事件的参数差异）。**切勿把 `.zip` 写进顶层 dir_rule**，否则图片会落进名为 `1.zip` 的目录里。
  - **验证门禁（先跑一次真实小下载，再定稿配置）**：`ZipPlugin.get_downloaded_photo`（`jm_plugin.py:537-542`）在 after_photo 时返回 `download_success_dict[photo.from_album]`——即**到当前为止的全部章节**，`level='photo'` 分支（`:525-529`）每次都会重打这些章节。故 `delete_original_file: true`（`:204-219` `execute_deletion`）与 after_photo 组合时，后续事件可能读到已删的空目录。先用 `delete_original_file: false` 实测：确认最终每章恰好一个 zip、条目是 `00001.jpg`…、无重复打包残留；暂存清理由 `jmlib_index` 在验证后接管。若实测发现重复打包 / 空包 / 异常，则**退回 T4.1 的自定义单插件形态**：把 `jmlib_index` 扩展为 `jmlib_ingest`——`after_album` 一个插件按 `download_success_dict[album]` 逐章写 zip + 抽封面 + 写 DB + 清暂存，同时移除 `after_photo` 的内置 zip 配置，不再依赖内置 zip。此门禁结果决定 T4.2 最终形态，属实现期裁决、不在此臆断。
- **T4.3** 16 本散装打包入库（原目录保留到确认才清理）。
  - 验证：`jmlib download <aid>` 产生 `{Aid}/{Pindex}.zip` + 封面 + DB 记录，零额外联网；UI 立即可见；T4.2 门禁结论写入 `docs/`。

### P5 — 受控治理动作

- **T5.1** 未识别转正（手工指定 Aid）、世代 / 重复 / 清理动作落地（均逐项确认可回滚）。
  - 验证：转正后建书；任一动作可回滚。

## Risks

- **P0 迁移**：工作区外 25.3 GB 唯一副本、同卷 rename 可能触发一次提权确认；`library/` 必须 `.gitignore`（否则 25 GB 进版本库）。缓解：全量 manifest + 反向 rename 回滚 + 不删除任何 zip。
- **T4.2 内置 zip 重复打包**（见上）：以一次真实小下载的实测门禁裁决，不臆断；必要时退回自定义单插件。
- **861 无车号包的 85 个期号误判**：严禁把 `COMIC 失乐天 2022年6月号` 之类期号当车号，严格口径保留在 unresolved。
- **标题简繁 / 汉化组后缀**：`normalize_zh` 曾把 Aid 1454103 拆成汚/污两夹；新规范全部数字主键、索引层按 Aid 合并，不再按标题落盘。
- **凭据**：option.yml 注释段的邮箱 / 授权码绝不读取、入库、进日志、进提交；导出 / 开源前路径脱敏。

## Repair Track

- 迁移对账不一致 → 以 manifest 反向 rename 回滚，不自动修复源。
- 封面缺失 / 损坏 → 从 zip 首成员重建（缓存可重建）。
- 简繁分裂（Aid 1454103 汚 / 污两夹、散装队列内）→ P4 打包入库时按 Aid 物理合并成单一 `{Aid}/` 树、缺封面由首图补；原目录保留到用户确认才清理。

## Retirement Track

- 退役 `E:\my world\pictures(1)` 旧路径（confirmation-first，P0 门禁）。
- 退役 option.yml 的 `download_cover` 与旧 `dir_rule`（P4，用户确认后改副本）。
- 退役两个手工脚本的日常用途（保留为参考，不删除）。
- 退役 jm-view-server 方案（Q2 已否决，不引入）。
- retirement trigger: `jmlib migrate --apply` 成功并通过全量对账；旧路径引用清零。

## Execution Readiness View

- Intent Lock: 规格 m00322 + 修正 m00335。Scope Fence: 不引入 AI / MCP / jmcomic-ai、不改上游、不联网、不重打 25 GB。
- Baseline Lock: 磁盘事实 + 上游契约行号。Approved Behavior: Q1–Q5 裁决。
- Owner / Contract Constraints: 单一写入口 `store.py`。Compatibility Boundary: 见上。Retirement Boundary: confirmation-first（P0 门禁）。
- Task Batches: P0→P5。Test Obligations: 各 Task 验证命令 + 全量对账 + UI 手测。
- Review Gates: P0.3 scoped confirmation、P4 option.yml 改动确认、各治理动作逐项确认。
- Drift / Rewind: manifest + 反向 rename 回滚；对账失败即停在当前期。
- Evidence Required Before Completion: 全量对账 + UI 手测结果 + 未验准则声明。
- Advisory Boundary: 方法包执行指导，非 GateDecision / PolicySnapshot / 完成授权。

## Self-Review

- approved-scope ✓ / 无 placeholder ✓ / owner 类型明确 ✓ / minimum-change ✓ / compatibility ✓ / exact verification ✓ / conditional triggers（P0 门禁）✓ / retirement ✓ / 需求与验收逐条映射到 Task ✓。

## Execution Handoff

- Decision: **inline**（任务强顺序依赖同一 DB / 目录，协调成本高于并行收益）。
- Evidence: 单一 canonical owner、P0 门禁、分期验收。Fallback: 任一期对账失败即停在该期、不进入下一期。
- **User confirmation required: yes — P0 迁移的 scoped confirmation（25.3 GB 唯一副本退役 + 一次性重整），以及 P4 对用户 option.yml 副本的改动。**
