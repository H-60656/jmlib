# jmlib 资源库实施 - Evidence


## EvidenceBundleDraft: p4

- Artifact key: p4-browser-verify
- Slice ID: p4
- Type: browser-verification
- Source: pilot_open / and /read and /health on P4 code
- Summary: P4 后浏览器封面墙 185 本、5 章 1443022 可阅读并记记忆点、体检面板显示 P4 已入库；测试记忆点已清理。
- Verifier: pilot + Python state check
- Evidence status: evidence-finalized

## EvidenceBundleDraft: p5

- Artifact key: p5-ui-feedback
- Slice ID: p5
- Type: ui-verification
- Source: pilot_snapshot after second promote and rollback
- Summary: 修复 load(true) 覆盖成功反馈后，真实浏览器保留 g2: promoted 888777/1 与 g2: rolled back；确认串门控、按钮禁用、4 类 Guard 只读拒绝均通过。
- Verifier: pilot
- Evidence status: evidence-finalized

## EvidenceBundleDraft: closeout

- Artifact key: final-state-receipt
- Slice ID: closeout
- Type: state-reconciliation
- Source: .tmp/cleanup_p5_traces.py + final Python state check
- Summary: 测试痕迹已清理：ledger 不存在、archive/888777 与 covers/888777 不存在、sidecar 无 888777；source zip 存在且 SHA-256 不变；FINAL_STATS books=185 chapters=1474 unresolved=861 memory=0 findings=0 page_total=61199；GOV 0 executed / 0 rolled_back / 0 actions / 4 destructive guards。
- Verifier: pwsh/Python
- Evidence status: evidence-finalized
