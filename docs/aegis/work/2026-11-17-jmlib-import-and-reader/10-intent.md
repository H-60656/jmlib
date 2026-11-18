# jmlib 全量导入 + 自添加 + 阅读增强 实施 - Intent

## TaskIntentDraft

- Requested outcome: 按 2026-11-17 计划实施 A/B/C 三段：库外 143 目录全量导入与上游 ID 候选确认、自行添加漫画、阅读增强
- Goal: 按 2026-11-17 计划实施 A/B/C 三段：库外 143 目录全量导入与上游 ID 候选确认、自行添加漫画、阅读增强
- Success evidence:
- 每批离线契约全绿 + 每 Phase 全量回归通过 + 真实浏览器证据 + 源目录零变化 + 上游仓库 clean + 未验准则声明
- Stop condition: done: A/B/C 全部批次验收通过; blocked: 需要用户裁决 D-1..D-5 或 scoped confirmation; needs-verification: 契约或回归失败
- Non-goals:
- OPDS、多用户、AI 层、浏览器上传、常驻 watch daemon、重打 25GB
- Scope: 仅 E:\\github\\jmlib 下代码与文档；计划 2026-11-17-jmlib-import-and-reader.md 的 A.1-A.6/B.1-B.4/C.1-C.4
- Change kinds:
- architecture+schema+ui
- Risk hints:
- 25.3GB 唯一副本、源目录零删除、上游搜索联网边界、memory=1 真实痕迹、旧 zip ACL

## BaselineReadSetHint

- docs/aegis/plans/2026-11-17-jmlib-import-and-reader.md
- docs/aegis/plans/2026-11-10-jmlib-resource-library.md

## BaselineUsageDraft

- Required baseline refs:
- docs/aegis/plans/2026-11-17-jmlib-import-and-reader.md
- docs/aegis/plans/2026-11-10-jmlib-resource-library.md
- Acknowledged before plan:
- none
- Cited in plan:
- none
- Missing refs:
- docs/aegis/plans/2026-11-17-jmlib-import-and-reader.md
- docs/aegis/plans/2026-11-10-jmlib-resource-library.md
- Advisory decision: needs-baseline-readback

## ImpactStatementDraft

- Compatibility boundary: 既有 185 本/1474 章/861 unresolved/61199 页零改动；schemaVersion v1->v2 可读；上游仓库零改动；option.yml 只读
- Affected layers:
- src/jmlib, web, data schema
- Owners:
- src/jmlib/store.py (DB 写入口) + 4 个新 owner
- Invariants:
- {Aid} 是书身份；library/archive 是字节真相；标题不作键；memory/findings 不丢
- Non-goals:
- OPDS、多用户、AI 层、浏览器上传、常驻 watch daemon、重打 25GB

These records are Method Pack drafts / hints, not authoritative runtime decisions.
