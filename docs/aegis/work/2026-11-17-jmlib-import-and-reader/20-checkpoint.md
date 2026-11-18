# jmlib 全量导入 + 自添加 + 阅读增强 实施 - Checkpoint

## Current Checkpoint

- Current todo: D 段收尾完成——转正、短号契约、剩余 88 单元复核与转正全部落地；真实库 439 本 / 2333 章 / 99824 页，_unidentified 仅剩 2 个已判重复投稿的包
- Active slice: D
- Completed todos:
- 转正：657 条 promote 账本（656 executed + 1 rolled_back，174 个 Aid）；books 185->354、chapters 1474->2130、pages 61199->89944、_unidentified 861->205；逐包对账 PROBLEMS 0 / INTEGRITY OK
- 短号契约：新建 src/jmlib/aid.py（identity AID_RE 1-7 位 / 自由文本 HINT_RE >=4 位二分），12 个 Python 模块 + 3 个 web 文件改共享导入；20 批套件全绿（5 批旧断言按新契约改写）；121/579 转正后 jmlib index 重建，books/chapters/pages 不变、book 121 存活；两书回填标签后再重建 WITH_TAGS 180 -> 182
- 复核证据层：.tmp/review_sheet.py 用页面级证据（本地页数 vs 上游 photo 页数、中文/无修正标签、逐字同标题、与已入库 Aid 撞车比对 page_count）把 85 条 pending + 3 条 skipped 压成 83 条明示建议；.tmp/preflight_suggestions.py 证明 83/83 可转正（覆盖 200/205 zip）；.tmp/probe_needs_eyes.py / probe_round2.py 按社团号重搜补回 5 本
- 复核裁决层：.tmp/adopt_suggestions.py --apply（CHOSEN 84 / SKIPPED 2 / LEFT 2，WROTE 86 FAILED 0）+ .tmp/adopt_last_two.py（Ayaka Shienishi 2 = あやかしえにし2 1219830；クール系店员 = 1192982）⇒ id_resolution pending 0 / chosen 260 / skipped 2
- 复核转正：86 单元 203 zip（小样 7 + 批量 194 + 最后 2），每 zip 一个服务端重算确认串、零删除；books 354->439、chapters 2130->2333、pages 89944->99824、_unidentified 205->2；对账 PROBLEMS 0 / INTEGRITY OK，重建 books 439 / chapters 2333 / pages 99824 / broken 0
- 复核残留：_unidentified 里 2 个包是两个已判重复投稿的文件（121姊乳榨精的性爱、[白杨汉化组][蟹村饭店] ウェプト样），按零删除原则原地保留，id_resolution 记 skipped
- 标签回填：resolve.fill_tags 对 257 本无标签书抓 album_info，247 本写入 tags/author（同时写侧车），10 本上游已下架无法补（3748111、3748011、1439474、1436338、1435295、1433139、1427743、1237529、1237128、1237099）；WITH_TAGS 182 -> 429 且 jmlib index 重建后仍在
- Evidence refs:
- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-promote-batch.json
- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-short-aid-contract.json
- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-review-batch.json
- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-tag-backfill.json
- Blocked on: none
- Next step: 本轮导入 + 治理 + 复核 + 标签全部收尾，可交付；残留 2 个重复投稿包与 10 个上游已下架专辑（无标签）都是数据事实，不是待办

## Recent Checkpoint History

## Checkpoint Update

- Current todo: D 收尾：checkpoint/evidence + 交付声明
- Active slice: D
- Completed todos:
- D 未识别包按名回填 Aid（resolve.py + CLI + /resolve 复核面板，契约 82/82 + 47/47）；补标签 180 本并写入 sidecar
- Evidence refs:
- none
- Blocked on: none
- Next step: 写 evidence、打 bundle、向用户交付并声明未验项
## Checkpoint Update

- Current todo: 汇总验证：全量回归 + 浏览器复核 + checkpoint/evidence + 交付声明
- Active slice: C.4
- Completed todos:
- C.4 书库增强：BOOK_SORTS 五档排序（aid/recent/title/chapters/pages）+ 空的三种含义分开说明 + 标签/书架空状态文案 + 契约 58/58 + 浏览器复核
- Evidence refs:
- none
- Blocked on: none
- Next step: 跑全量回归（套件 12 批 + P1-P5 + migrate）、写 evidence bundle、交付声明未验项
## Checkpoint Update

- Current todo: C.2 阅读器重写：read.html 拆 read.js + 方向/单双页/fit/全屏/进度条/跳页/上下章/预取
- Active slice: C.2
- Completed todos:
- C.1 阅读设置与书签：schema v5 + prefs.py 策略层 + api_prefs.py 7 端点 + 契约 68/68 + 十批套件 SUITE_OK + P1-P5 全绿
- Evidence refs:
- none
- Blocked on: none
- Next step: 新建 web/read.js，把 read.html 的内联脚本外移并加方向/单双页/fit/全屏/进度条/跳页/上下章/预取
## Checkpoint Update

- Current todo: C.1 阅读设置与书签：reading_prefs/bookmarks 表 + GET/PUT /api/prefs/{aid|_global} + bookmarks CRUD
- Active slice: C.1
- Completed todos:
- B.4 合集：store 八函数 + 7 端点 + 书架筛选/收藏切换 UI + 契约 43/43 + 浏览器复核
- Evidence refs:
- none
- Blocked on: none
- Next step: 实现 prefs.py（reading_prefs/bookmarks）+ 端点 + 契约 .tmp/test_prefs_c1.py
## Checkpoint Update

- Current todo: B.3 books 元数据编辑列 + PATCH /api/books/{aid} + 收藏端点
- Active slice: B.3
- Completed todos:
- B.1 手工添加单目录（预览/token/执行/独立回滚账本）+ B.2 投放夹只读列举（骨架分离）
- Evidence refs:
- B1/B2 contract 74/74 + /import 真实浏览器复核 + 真实库回滚清理（185/1474/861 复原）
- Blocked on: none
- Next step: B.3：store 加 local_title/aliases/favorite/collection 列与窄口，rebuild 保留用户编辑，PATCH /api/books/{aid}

## DriftCheckDraft

- Scope status: verified: A.1-A.6/B.1-B.4/C.1-C.4/D 全部批次已实施；D 段累计 860 条 promote 动作（86 单元 203 zip 本批 + 654 早批），逐包对账 PROBLEMS 0 / INTEGRITY OK；20 批套件（含新增 aid 契约）全绿
- Compatibility status: verified: 真实库 439 本 / 2333 章 / 99824 页与磁盘 2333 包一一对应，zip 总数 2335 不变（零删除），id_resolution pending 0 / chosen 260 / skipped 2，_unidentified 仅剩 2 个已判重复投稿的包且 unresolved 行与磁盘一致，源目录零改动，option.yml 只读
- Retirement status: not-applicable: 本段不退役任何路径；旧手工脚本、download_cover、旧 dir_rule、jm-view-server 仍保留（退役 trigger 未触发）
- New risk signals:
- 2 个重复投稿包留在 _unidentified（121 与 533565 的同内容另一次上传），不进入书库是刻意的；若日后要留另一版本需先 rollback 再 promote
- クール系店员取 1192982 而非 1193191（同一作品两次上传，标题/作者/页数全同），仅凭标签无法再判，若封面不符可改判
- idres 中已转正单元仍保留 chosen 行（历史记录，不构成待办）
- Advisory decision: continue
