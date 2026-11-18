# Proof Bundle - 2026-11-17-jmlib-import-and-reader

## Method Pack Boundary

This proof bundle is an advisory Aegis Method Pack record. It does not determine evidence sufficiency, produce authoritative `GateDecision`, or grant `completion authority`.

## Task Intent

- Requested outcome: 按 2026-11-17 计划实施 A/B/C 三段：库外 143 目录全量导入与上游 ID 候选确认、自行添加漫画、阅读增强
- Scope: 仅 E:\\github\\jmlib 下代码与文档；计划 2026-11-17-jmlib-import-and-reader.md 的 A.1-A.6/B.1-B.4/C.1-C.4

## Impact

- Compatibility boundary: 转正前真实库 185 本/1474 章/861 unresolved/61199 页；D 段累计转正 857 个 zip（早批 654 + 复核批 203）后为 439 本/2333 章/2 unresolved/99824 页，全部是同卷 rename（zip 总数 2335 不变、零删除、可回滚），DB 行与磁盘字节一一对应；剩余 2 个 unresolved 包是刻意保留的重复投稿文件；schemaVersion v1->v2 可读；上游仓库零改动；option.yml 只读
- Non-goals:
- OPDS、多用户、AI 层、浏览器上传、常驻 watch daemon、重打 25GB

## Terminal Evidence Refs

- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-a-b-import-suite.json
- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-browser-c4-and-c3.json
- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-c-reader-suite.json
- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-D-tag-fix.json
- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-D.json
- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-final-state-receipt.json
- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-p1-p5-regression.json
- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-promote-batch.json
- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-review-batch.json
docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-tag-backfill.json
docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-tag-backfill.json
docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-review-batch.json
docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-short-aid-contract.json

## Formal Evidence

- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-a-b-import-suite.json
- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-browser-c4-and-c3.json
- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-c-reader-suite.json
- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-D-tag-fix.json
- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-D.json
- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-final-state-receipt.json
- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-p1-p5-regression.json
- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-promote-batch.json
- docs/aegis/work/2026-11-17-jmlib-import-and-reader/evidence-bundle-draft-short-aid-contract.json

## Terminal Non-Passed Evidence

- none

## Legacy Unclassified Evidence

- none

## Superseded Evidence Count

- 0

## Drift Check

- Scope status: verified: A.1-A.6/B.1-B.4/C.1-C.4/D 全部批次已实施；D 段累计 860 条 promote 动作（复核批 86 单元 203 zip + 早批 654 zip），逐包对账（PROBLEMS 0 / INTEGRITY OK）；20 批套件（含新增 aid 契约）全绿
- Compatibility status: verified: 真实库 439 本/2333 章/99824 页与磁盘 2333 包一一对应，zip 总数 2335 不变（零删除），id_resolution pending 0 / chosen 260 / skipped 2，_unidentified 仅剩 2 个已判重复投稿的包且 unresolved 行与磁盘一致，源目录零改动，option.yml 只读
- Retirement status: not-applicable: 本段不退役任何路径；旧手工脚本、download_cover、旧 dir_rule、jm-view-server 仍保留（退役 trigger 未触发）
- Advisory decision: continue
