# Changelog

本文件记录 jmlib 的所有重要更新。

条目分类参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.1.0] - 2026-11-18

首个可用版本：把散装 zip 归档整理成可搜索、可阅读、可受控治理的本地书架。

### Added
- 📚 **归档迁移**：`jmlib migrate --plan/--apply/--rollback`，把 `{title}/{n}.zip` 的散装布局搬成 `library/archive/{Aid}/{pindex}.zip`；先出只读计划，`--apply` 才动文件，`--rollback` 精确反向，冲突目录改道而非覆盖。
- 🗂️ **SQLite 索引**：`jmlib index` 从 `library/` 重建 `books` / `chapters` / `memory` / `unresolved` / `findings` 等表并生成封面缓存；重建幂等，且原样保留 `local_title` / `aliases` / `favorite` / `collection` 这些用户手写数据。
- 📥 **散装入库**：`jmlib ingest --plan-only/--apply/--rollback` 把松散的图片目录与 zip 打包进库，附带 `_unresolved` 记账。
- 🔍 **未识别复核**：`jmlib resolve` 按标题串行节流回查上游，产出候选与领先幅度；`--dry-run` 只查不写，`--apply` 只写 `id_resolution` 裁决，仍不动文件。
- 🧾 **转正（promote）**：复核结果通过 `governance.promote_plan` → `promote_apply` 转正，同卷 `rename`、零删除、TOCTOU 重校验。
- 🛡️ **受控治理**：破坏性动作（删散装源、去重复页、批量反向迁移、合并世代目录）只产出守卫清单，状态恒为 `awaiting scoped confirmation`；本模块没有任何代码路径能把破坏性动作翻成已执行。
- 🩺 **存储体检**：`jmlib check [--csv]` 与 `/health` 页面报告缺字节、缺封面、重复页、孤立行、页数不一致。
- 🌐 **本地网页书架**：`jmlib serve` 提供书架、阅读器、导入面板、复核、治理、体检六个页面；`--lan` 模式强制 `JMLIB_PIN` 门禁。
- ⏯️ **阅读进度**：逐页记忆（每本书记住话号与页号），书签与阅读偏好持久化。
- ⭐ **收藏与书单**：单书架标签（`collection` 列）与有序书单（`collections` / `collection_items` 多对多）刻意分离，互不派生。
- 🔢 **短车号契约**：`src/jmlib/aid.py` 统一 Aid 契约，身份位接受 1–7 位数字（真实存在 `121`、`187` 这类短号），自由文本中的裸数字提示仍保持 4 位下限，避免把年份、期号、卷号当成车号。

### Changed
- 📐 **工程配置对齐 GitHub 仓库标准**：补齐 README / LICENSE / CHANGELOG / CONTRIBUTING / SECURITY / CODE_OF_CONDUCT / issue 与 PR 模板 / CI / Dependabot / CODEOWNERS；`pyproject.toml` 补全 readme、license、authors、urls、classifiers、ruff、mypy、dev 依赖组。
- 🧹 **代码风格统一**：ruff `select = ["E","F","W","I","UP"]`、`line-length = 120`、`target-version = "py310"`，源码与测试全部格式化通过。
- 🧪 **批次契约脚本进仓**：原先散在 `.tmp`（被 `.gitignore` 排除）的 20 个批次契约脚本（`contract_a1.py` … `contract_p5.py`）移入 `tests/contracts/`，由新增的 `tests/test_contracts.py` 逐个以独立子进程拉起 —— 它们在模块顶层就重定向 `jmlib.paths` 并清空自己的 scratch 树，同进程 import 会让一个套件的重定向泄漏到下一个。CI 的 `pytest` 与 `unittest discover` 从此真正覆盖这批契约（本地 `pytest -q` 从 14 条增至 16 条 + 20 个 subtest）。硬编码的本机绝对路径同时改为 `Path(__file__).resolve().parents[2]` 推导。

### Security
- 🛡️ **导入根白名单下沉到库层**：白名单与「路径必须落在根内」的判定原只活在 HTTP 层，`import_apply` 作为库函数仍会接受调用方给的任意路径并去读它；现在 `paths.allowed_import_roots()` / `paths.contained_in()` / `paths.resolve_inside_roots()` 成为唯一所有者，API、CLI、测试走同一条规则，越界路径在触碰文件系统之前就被拒绝。
- 🛡️ **治理层路径改为结构化校验**：`governance` 新增 `_unresolved_source()` / `_archive_target()`，把「必须位于 `library/_unidentified/`」「必须位于 `library/archive/`」写成显式包含性判定，替代原先的字符串前缀与 `parents` 成员扫描；判定发生在 `resolve()` 之前，只有已证明在根内的路径才允许触碰文件系统。
- 🛡️ **异常文本不再外泄**：`governance` 的错误串只报异常**类名**，不再拼接 `exc` 正文（正文可能带绝对路径等本机信息）。
- 🛡️ **标题卷号正则改为有界**：`_VOLUME_IN_TEXT_RE` 的每个空白段改成 `\s{0,4}`。旧式 `(?:第|第\s*)?\s*` 在 `第` 后接长空格串时，每个起始位置都要重走一遍空白，搜索退化为二次（实测 2k 空格 0.26 s、8k 空格 4.1 s）；有界后同为线性（2k 0.46 ms、8k 1.71 ms），22 条真实标题语义用例不变。
- 🧪 **包含性契约纳入仓库测试**：新增 `tests/test_paths_containment.py`（11 条），把白名单门禁钉进 CI —— 根自身/子目录放行，同名前缀兄弟目录、`..` 穿越、根外绝对路径、空路径一律拒绝，并断言 `normalize_untrusted` 不碰磁盘。判为误报的 3 条 `py/path-injection` 告警及其依据记录在 `docs/security/codeql-dismissals.md`。
- 🛡️ **扫描入口自己校验导入根**：`import_scan.scan()` 过去只信调用方（HTTP 层 `resolve_root`）已做过白名单，库层直接 `iterdir`/`rglob` 走整棵树；现在它自己归一化路径、按 `paths.allowed_import_roots()` 判定包含性，越界抛 `PermissionError`，空路径抛 `FileNotFoundError`。
- 🛡️ **手填预览不再提前 resolve**：`import_apply.plan_single()` 原先把调用方给的路径先 `resolve()` 再交给 `_single_item` 校验，等于在校验之前就问磁盘；现在只做 `expanduser()`，归一化与白名单判定统一由 `_single_item` 完成。

### Fixed
- 🏷️ **标签在重建后消失**：`fill_tags` 原先只写 `books.tags`，而 `jmlib index` 从 `data/ingest_metadata.json` 重建该列，导致一次重建后 177 本书的标签全部丢失；改为同时写侧车元数据（`ingest.merge_meta`），重建后标签存活。
- 🔢 **短 Aid 被契约误拒**：Aid 正则原为 4–7 位，导致 `121`、`579` 这类真实专辑既无法转正、又会在重建时被静默丢弃；改为身份位 1–7 位、提示位 4 位起的两套显式规则，并补上逐模块同一性测试。
- 🧭 **`python -m jmlib.cli` 静默退出**：`cli.py` 没有 `__main__` 守卫，`-m` 调用只导入模块就退出（exit 0、无输出）；正确入口为 `python -m jmlib`。

[0.1.0]: https://github.com/H-60656/jmlib/releases/tag/v0.1.0
