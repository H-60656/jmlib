# jmlib 资源库实施 - Checkpoint

## Current Checkpoint

- Current todo: 交付完成：jmlib 资源库 P0–P5 已验证
- Active slice: closeout
- Completed todos:
- P0–P5 全部分期完成；P4 浏览器复核；P5 受控治理端到端复核；最终全量回归；测试痕迹清理
- Evidence refs:
- p4-browser-verify,p5-real-promote-rollback,p5-ui-feedback,final-regression,final-state-receipt
- Blocked on: none
- Next step: 无；交付

## Recent Checkpoint History

## Checkpoint Update

- Current todo: P1 store/indexer/封面抽取 + 封面墙/搜索
- Active slice: P1
- Completed todos:
- P0.1 建立 jmlib 工程骨架
- P0.2 migrate.py --plan 全量清单 + 契约测试
- P0.4 执行迁移（同卷 rename、零删除、可回滚）
- Evidence refs:
- none
- Blocked on: none
- Next step: write src/jmlib/store.py (SQLite canonical owner), indexer.py (scan archive + first-zip-member covers), api.py + web/index.html; verify 170 books listed
## Checkpoint Update

- Current todo: P0.1 建立 jmlib 工程骨架
- Active slice: P0.1
- Completed todos:
- none
- Evidence refs:
- none
- Blocked on: none
- Next step: create .gitignore / pyproject.toml / src / web / data, verify uv sync and upstream git clean

## DriftCheckDraft

- Scope status: verified: P0-P5 全部落在已批准规格内；未引入 AI/MCP/jmcomic-ai、未改上游两仓库、未联网、未重打 25GB
- Compatibility status: verified: P1/P2/P3/P4/P5 全量回归 18/29/39/ALL/ALL + migrate 3/3；P4 后封面墙 185 本、阅读器续读、体检面板、治理页面真实浏览器复核通过
- Retirement status: verified: 旧 E:/my world/pictures(1) 路径已退役且保留反向回滚清单；download_cover/旧 dir_rule 由 jmlib download 内存 overlay 取代（未改用户 option.yml）；861 未识别保留在 library/_unidentified；library/_incoming 空骨架保留供 rollback
- New risk signals:
- 受限服务下旧 zip 因 ACL 不继承 DSH SID 无法 rename：已改为结构化 refused + 完全权限服务端到端验证；真实联网下载链路本机离线未实测
- Advisory decision: continue
