# jmlib 全量导入 + 自添加 + 阅读增强 实施 - Evidence


## EvidenceBundleDraft: a-b-import-suite

- Artifact key: a-b-import-suite
- Slice ID: a-b-import-suite
- Type: test-run
- Source: .tmp/run_a_suite.py
- Summary: A1-A6 + B1/B2 + B3 + B4 离线契约全绿：A1 77/A2 54/A3 all/A4/A5/A6/B1/B3 32/B4 43 checks，覆盖扫描分类、上游候选打分与失败分类、裁决持久化、导入面板、批量入库、手工添加、元数据编辑、合集
- Verifier: python .tmp/run_a_suite.py
- Evidence status: evidence-finalized

## EvidenceBundleDraft: c-reader-suite

- Artifact key: c-reader-suite
- Slice ID: c-reader-suite
- Type: test-run
- Source: .tmp/run_a_suite.py
- Summary: C1/C2/C4 契约全绿：C1 68（prefs/bookmarks 解析优先级与零字节）、C2 37（阅读器外壳/控件一致/预取上限）、C4 58（五档排序、筛选一致、空的三种含义）
- Verifier: python .tmp/run_a_suite.py
- Evidence status: evidence-finalized

## EvidenceBundleDraft: p1-p5-regression

- Artifact key: p1-p5-regression
- Slice ID: p1-p5-regression
- Type: test-run
- Source: .tmp/test_p*_api.py + .tmp/test_p4_ingest.py + .tmp/test_p5_governance.py + tests/test_migrate.py
- Summary: P1 18/P2 29/P3 40/P4 ALL/P5 ALL/migrate 3 全绿；schemaVersion jmlib.db.v5；真实库 185 本/1474 章/memory 1/861 未识别/61199 页
- Verifier: python .tmp/test_p1_api.py ...
- Evidence status: evidence-finalized

## EvidenceBundleDraft: browser-c4-and-c3

- Artifact key: browser-c4-and-c3
- Slice ID: browser-c4-and-c3
- Type: manual-check
- Source: pilot_* 真实浏览器
- Summary: C.4 排序五档/空结果说明/收藏空结果/标签书架空状态；C.3 连续滚动：首屏加载、跳页状态行、超时重试、恢复位置 155 精确、模式切换保位、章边界
- Verifier: 真实浏览器 pilot_open/pilot_eval
- Evidence status: evidence-finalized

## EvidenceBundleDraft: final-state-receipt

- Artifact key: final-state-receipt
- Slice ID: final-state-receipt
- Type: state-receipt
- Source: jmlib.db + library/
- Summary: STATS schemaVersion jmlib.db.v5 books 185 chapters 1474 memory 1 unresolved 861 findings 0 page_total 61199；PREFS [] BM 0 COLLS [] GOV 0/0 IDRES [] 无回滚清单；ZIPS 1474 FILES 3813；memory 仅剩用户真实痕迹 1229478
- Verifier: python -c store.stats() 只读
- Evidence status: evidence-finalized

## EvidenceBundleDraft: D

- Artifact key: d-resolve-and-tags
- Slice ID: D
- Type: test-run
- Source: .tmp/test_resolve_d.py, .tmp/test_resolve_api.py, .tmp/run_a_suite.py
- Summary: D 段离线契约全绿：D 82 checks（分组/文件名 Aid 捷径/纯数字名不当 query/分数与 margin 闸门/标签回填写库+sidecar/rebuild 后存活）+ D-review 47 checks（复核面板只读、不调上游、决定落库、清除保留证据、PIN）；15 批套件 SUITE_OK
- Verifier: python .tmp/run_a_suite.py
- Evidence status: evidence-finalized

## EvidenceBundleDraft: D-tag-fix

- Artifact key: d-tag-sidecar-fix
- Slice ID: D-tag-fix
- Type: finding
- Source: src/jmlib/resolve.py fill_tags, src/jmlib/ingest.py merge_meta, .tmp/test_resolve_d.py
- Summary: 真实缺陷：fill_tags 原先只写 books.tags，而 indexer.rebuild 从 data/ingest_metadata.json 重建该列，导致一次 jmlib index 后 177 本标签全部消失。修复为同时写 sidecar（ingest.merge_meta），并新增 rebuild 后标签/作者仍在的回归断言；随后对真实库重跑补标签 180 本并验证 jmlib index 后 WITH_TAGS 仍为 180。
- Verifier: regression: rebuild after backfill
- Evidence status: evidence-finalized

## EvidenceBundleDraft: promote-batch

- Artifact key: promote-batch
- Slice ID: promote-batch
- Type: state-receipt
- Source: .tmp/run_promote.py, .tmp/verify_after_promote.py, .tmp/census_gov.py, data/governance_manifest.json, data/jmlib.db, library/
- Summary: 654 个已定案 zip 分批走 governance.promote_plan -> promote_apply（每个 zip 一个服务端重算的精确确认串，同卷 rename、零删除、可回滚）：账本 657 条 promote（656 executed + 1 rolled_back，174 个 Aid，created_book 172）；books 185 -> 354，chapters 1474 -> 2130，page_total 61199 -> 89944，_unidentified 861 -> 205 包，archive 2130 包，zip 总数 2335 不变。逐包对账全绿：ROWS_WITHOUT_BYTES 0、BYTES_WITHOUT_ROW 0、VERIFIED_ZIPS 2130 PAGES 89944 与 catalog 一致、unresolved 行 205 = 磁盘 205、PAGE_MISMATCH 0、CHAPTER_COVER_MISSING 0、DUPLICATE_PINDEX 0、PROBLEMS 0、INTEGRITY OK；仅 3 本历史数据 pindex 不连续（1436338、556734、206661）。转正后 jmlib index 重建：books 354 / chapters 2130 / pages 89944 / broken 0，与重建前逐项一致；随后为两个短号书回填标签（121=[巨乳,御姐,风的工房,中文]、579=[巨乳,中文]，同时写侧车），再重建一次 WITH_TAGS 180 -> 182 且两书标签仍在
- Verifier: python .tmp/verify_after_promote.py + python -m jmlib index
- Evidence status: evidence-finalized

## EvidenceBundleDraft: tag-backfill

- Artifact key: tag-backfill
- Slice ID: tag-backfill
- Type: finding
- Source: .tmp/fill_tags_all.py, src/jmlib/resolve.py fill_tags, src/jmlib/store.py set_book_tags, src/jmlib/ingest.py merge_meta, data/ingest_metadata.json, data/jmlib.db
- Summary: 转正只建目录行、不给标签，复核批后 439 本里只有 182 本有标签。resolve.fill_tags(dry_run=False) 对 books_missing_tags() 的 257 本逐本抓 album_info：ok 247 / written 247 / failed 10，663s（串行 + 1.5s 节流）。10 个失败是上游已下架的专辑（MissingAlbumPhotoException）：3748111、3748011、1439474、1436338、1435295、1433139、1427743、1237529、1237128、1237099 —— 本地有包、上游无专辑，属数据事实。写入同时落 books 行与 ingest 侧车，jmlib index 重建后仍在：WITH_TAGS 182 -> 429，重建前后 books 439 / chapters 2333 / pages 99824 / unresolved 2 / broken 0 完全一致，book 121 的短号标签也仍在
- Verifier: python .tmp/fill_tags_all.py + python -m jmlib index + python .tmp/census.py
- Evidence status: evidence-finalized

## EvidenceBundleDraft: short-aid-contract

- Artifact key: short-aid-contract
- Slice ID: short-aid-contract
- Type: finding
- Source: src/jmlib/aid.py, 12 个 src/jmlib 模块, web/{import.html,assets/resolve.js,assets/read.js}, .tmp/probe_aid121.py, .tmp/test_aid.py
- Summary: 真实缺陷（契约过窄）：两个已定案单元在 promote 时被 governance 以 invalid aid 拒绝，实测 album_info('121')/('579') 均 ok=True 且标题与目录名逐字一致 ⇒ 是真实专辑，错在 AID_RE 的 4–7 位下限（import_scan 注释早已承认存在 3 位车号 187_cover.jpg、187/1/）。修复：新建 jmlib/aid.py 二分 identity（AID_RE 1–7 位，保留锚点）与弱证据（HINT_RE >=4 位），COVER_FILE_RE 接受短号；12 模块 + 3 web 文件删本地定义改共享导入（indexer.py 残留本地定义曾把规则覆盖回窄版，由 grep 抓到）；migrate.py 遗留源布局扫描改名 LEGACY_AID_RE 保留 4 位下限。20 批套件全绿，其中 5 批原「短号应被拒绝」断言改写为「短号合法 + 0121/12345678 仍拒绝」。转正 121/579 后 jmlib index 重建：books 354 / chapters 2130 / pages 89944 不变，book 121 存活（204 页 + 封面）；两书回填标签后再重建，WITH_TAGS 180 -> 182 且标签仍在
- Verifier: python .tmp/test_aid.py + python .tmp/run_promote.py --aids 121,579 + python -m jmlib index
- Evidence status: evidence-finalized

## EvidenceBundleDraft: review-batch

- Artifact key: review-batch
- Slice ID: review-batch
- Type: state-receipt
- Source: .tmp/review_sheet.py, .tmp/preflight_suggestions.py, .tmp/adopt_suggestions.py, .tmp/adopt_last_two.py, .tmp/run_promote.py, .tmp/verify_after_promote.py, data/governance_manifest.json, data/jmlib.db, library/
- Summary: 剩余 88 个未定案单元全部处置。证据层：本地页数 vs 上游 photo 页数（album_detail.episode_list[i][0] -> get_photo_detail -> len(page_arr)，album.page_count 恒为 0 不能用）、中文/无修正标签一致性、逐字同标题、已入库/同 Aid 撞车（复用 store.list_chapters 比对 page_count），把 pending 85 + skipped 3 压成 83 条明示建议，83/83 通过 governance.promote_plan 预检（覆盖 205 zip 里的 200 个）。裁决层：按用户「按名称自行判断」的授权写 id_resolution（CHOSEN 84 / SKIPPED 2 / LEFT 2 后补 2 条人眼；最终 pending 0 / chosen 260 / skipped 2）。三条例外有独立测量：121 重复投稿（库内 121/1 同为 204 页）skip；601875 与库内 50 页不同页数 → 同一专辑另一话，转正为 pindex 2；533565 两单元同一份 79 页上传，只保留 JM(1)。最后两条人眼：Ayaka Shienishi 2 是社团名罗马字 → [yam] あやかしえにし2【FANZA限定特典付き】= 1219830（上游 184 页 = 本地 184 页，同批转正的 380757 是上一卷）；クール系店员 1192982 与 1193191 同作者同标题同 55 页（同一作品两次上传），取较早的 1192982。转正：3 单元 7 zip 小样 → run_promote.py --all 194 zip 0 失败（25.3s）→ 最后 2 zip，共 86 单元 203 zip，每个 zip 一个服务端重算的精确确认串，同卷 rename、零删除、可逐条 rollback；账本累计 860 条 promote。结果：books 354 -> 439、chapters 2130 -> 2333、page_total 89944 -> 99824、_unidentified 205 -> 2 包、archive 2333 包、zip 总数 2335 不变。逐包对账全绿：ROWS_WITHOUT_BYTES 0、BYTES_WITHOUT_ROW 0、unresolved 行 2 = 磁盘 2、VERIFIED_ZIPS 2333 PAGES 99824 与 catalog 一致、PAGE_MISMATCH 0、CHAPTER_COVER_MISSING 0、DUPLICATE_PINDEX 0、PROBLEMS 0、INTEGRITY OK；jmlib index 重建 books 439 / chapters 2333 / pages 99824 / broken 0 与重建前一致。留在 _unidentified 的 2 个包是两个已判重复投稿的文件，按零删除原则原地保留、id_resolution 记 skipped
- Verifier: python .tmp/verify_after_promote.py + python -m jmlib index
- Evidence status: evidence-finalized
