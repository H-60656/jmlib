"""Offline A.6 contract: conflicts and near-duplicate queueing (no network, no real archive).

A.6 is about *questions*, not writes.  The three shapes the real queue has:

  * the ``1454103`` split — one book dropped as a cover-only directory plus a
    body-carrying directory under a differently-spelled name.  Structural evidence
    (same Aid) settles it, so it must merge silently and must NOT be asked about as
    "are these the same book?";
  * the ``小海和暑假`` / ``海妹妹和暑假`` pair — two directories whose names are almost
    identical but whose Aids differ.  That is a real question: same book, or two?
    It must be flagged ``suspected_same`` and kept out of an unpicked batch;
  * magazine-style names — a book published in a magazine with a structural
    ``{Aid}/{Pindex}`` tree is ordinary (the Aid is proven); a number read out of
    ``COMIC LO 2017年7月号`` is not an Aid and must be blocked.

Run:  $env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH='src'; python .tmp/test_import_a6.py
"""

# Moved verbatim from the ``.tmp`` exploration scripts so CI runs it.
# Runs as its own process: every contract redirects ``jmlib.paths`` onto a
# scratch tree, and those redirects must not leak between suites.
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jmlib import idres, import_apply, import_scan, paths, store  # noqa: E402

BASE = ROOT / ".tmp" / "a6_contracts"
if BASE.exists():
    shutil.rmtree(BASE)
BASE.mkdir(parents=True, exist_ok=True)

DATA = BASE / "data"
LIB = BASE / "library"
SRC = BASE / "queue"
BOOKS = SRC / "JM"

paths.ROOT = BASE
paths.DATA = DATA
paths.LIBRARY = LIB
paths.ARCHIVE = LIB / "archive"
paths.UNIDENTIFIED = LIB / "_unidentified"
paths.COVERS = LIB / "covers"
paths.INCOMING = LIB / "_incoming"
paths.STAGING = DATA / "staging"
paths.WEB = ROOT / "web"
paths.EXPORTS = BASE / "exports"
paths.DB_PATH = DATA / "jmlib.db"
paths.MIGRATION_MANIFEST = DATA / "migration_manifest.json"
paths.ROLLBACK_MANIFEST = DATA / "rollback_manifest.json"
paths.SIGNATURES = LIB / "_zip_signatures.json"
paths.BLACKLIST = LIB / "黑名单.csv"
paths.INGEST_METADATA = DATA / "ingest_metadata.json"
paths.LOOSE_INGEST_MANIFEST = DATA / "loose_ingest_manifest.json"
paths.GOVERNANCE_MANIFEST = DATA / "governance_manifest.json"
paths.IMPORT_MANIFEST = DATA / "import_manifest.json"
paths.IMPORT_ROLLBACK = DATA / "import_rollback.json"
paths.UPSTREAM_CACHE = DATA / "upstream_cache.json"
paths.ensure_dirs()

# import_scan.scan enforces the import-root whitelist in the library layer now
# (it used to be checked only at the HTTP edge), so the scratch tree must be
# declared readable before the first scan call.
os.environ["JMLIB_IMPORT_ROOTS"] = str(SRC)

fails: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("PASS " if cond else "FAIL ") + name + (f" {extra}" if extra else ""))
    if not cond:
        fails.append(name)


def img(path: Path, marker: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"JPEG" + marker + b"\xff\xd9")


def snapshot(root: Path) -> dict[str, tuple[int, bytes]]:
    out: dict[str, tuple[int, bytes]] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = (p.stat().st_size, p.read_bytes())
    return out


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

# 1) the 1454103 split: cover-only simplified dir + body-carrying traditional dir
SPLIT = "1454103"
img(BOOKS / "同级生にそして教师に汚されて……" / f"{SPLIT}_cover.jpg", b"splitcover")
img(BOOKS / "同级生にそして教师に污されて……" / SPLIT / "1" / "001.jpg", b"s1")
img(BOOKS / "同级生にそして教师に污されて……" / SPLIT / "1" / "002.jpg", b"s2")

# 2) near-identical names, different structural Aids -> a real question.
#    The two halves carry *different* chapter numbers on purpose: if the user
#    answers "same book", the merge must keep both chapters rather than be
#    reduced to the one chapter that happens to collide.
SAME_A = "777010"
SAME_B = "777011"
img(BOOKS / f"小海和暑假 有一点长名字 {SAME_A}" / SAME_A / "1" / "001.jpg", b"a1")
img(BOOKS / f"海妹妹和暑假 有一点长名字 {SAME_B}" / SAME_B / "2" / "001.jpg", b"b1")

# 3) magazine-style name WITH a structural tree: ordinary book, not a question
MAG_STRUCT = "777020"
img(BOOKS / "[汉化组] [作者] 某作品 (COMIC アンスリウム 2025年6月号)" / MAG_STRUCT / "1" / "001.jpg", b"m1")

# 4) magazine-style name whose only number is the issue number: must block
img(BOOKS / "某作品 (COMIC LO 2017年7月号)" / "1" / "001.jpg", b"m2")

# 5) an out-of-range "Aid" read from a short numeric subdir tree
img(BOOKS / "[砂漠] おしえてあげる (COMIC LO 2017年7月号)" / "187" / "1" / "001.jpg", b"short")

# 6) a clean book with nothing to ask about
CLEAN = "777030"
img(BOOKS / f"clean book {CLEAN}" / CLEAN / "1" / "001.jpg", b"c1")

SOURCE_BEFORE = snapshot(SRC)
manifest = import_scan.scan(SRC)
entries = {e["dir"]: e for e in manifest["entries"]}
rel_of = {e["dir"]: e["rel_path"] for e in manifest["entries"]}

# 8 directories: the split counts twice (its cover-only half is its own directory).
check("fixture scanned 8 directories", manifest["summary"]["dirs"] == 8, str(manifest["summary"]["dirs"]))
check("scan itself wrote no library byte", snapshot(LIB) == {})


# ---------------------------------------------------------------------------
# 1. hints are produced by the scan and re-exported by the decision layer
# ---------------------------------------------------------------------------

check(
    "idres re-exports the canonical hint strings",
    idres.REVIEW_HINTS is import_scan.REVIEW_HINTS and idres.HINT_SUSPECTED_SAME == import_scan.HINT_SUSPECTED_SAME,
    "the scan discovers hints; idres must not redefine them",
)
check("every entry carries a hints list", all(isinstance(e.get("hints"), list) for e in manifest["entries"]))
check(
    "clean structural book has no hints",
    entries[f"clean book {CLEAN}"]["hints"] == [],
    str(entries[f"clean book {CLEAN}"]["hints"]),
)


# ---------------------------------------------------------------------------
# 2. the 1454103 split is settled by the Aid, not asked about
# ---------------------------------------------------------------------------

split_cover = entries["同级生にそして教师に汚されて……"]
split_body = entries["同级生にそして教师に污されて……"]
check("split halves share the Aid", split_cover["aid"] == split_body["aid"] == SPLIT)
check(
    "split halves carry duplicate_aid evidence",
    "duplicate_aid" in split_cover["hints"] and "duplicate_aid" in split_body["hints"],
    f"{split_cover['hints']} / {split_body['hints']}",
)
check(
    "split halves are NOT flagged suspected_same",
    "suspected_same" not in split_cover["hints"] and "suspected_same" not in split_body["hints"],
    "same Aid already answered the question",
)
check(
    "the pair still appears in near_duplicates as evidence",
    any({p["a"], p["b"]} == {split_cover["dir"], split_body["dir"]} for p in manifest["near_duplicates"]),
    str([(p["a"], p["b"]) for p in manifest["near_duplicates"]]),
)


# ---------------------------------------------------------------------------
# 3. the near-identical pair is a question
# ---------------------------------------------------------------------------

name_a = f"小海和暑假 有一点长名字 {SAME_A}"
name_b = f"海妹妹和暑假 有一点长名字 {SAME_B}"
check(
    "near-identical names are a near-duplicate pair",
    any({p["a"], p["b"]} == {name_a, name_b} for p in manifest["near_duplicates"]),
    str([(p["a"], p["b"]) for p in manifest["near_duplicates"]]),
)
check(
    "both halves carry suspected_same",
    "suspected_same" in entries[name_a]["hints"] and "suspected_same" in entries[name_b]["hints"],
    f"{entries[name_a]['hints']} / {entries[name_b]['hints']}",
)
check(
    "suspected_same blocks a sweep",
    import_scan.sweep_blockers(entries[name_a]) == ["suspected_same"],
    str(import_scan.sweep_blockers(entries[name_a])),
)

plan = import_apply.build_plan(manifest)
swept_aids = {i["aid"] for i in plan["items"]}
check(
    "sweep skips both suspected halves", SAME_A not in swept_aids and SAME_B not in swept_aids, str(sorted(swept_aids))
)
check(
    "sweep still imports the settled and clean books", {SPLIT, MAG_STRUCT, CLEAN} <= swept_aids, str(sorted(swept_aids))
)
check(
    "sweep names the suspected pair as a conflict",
    sum(1 for c in plan["conflicts"] if "suspected_same" in c) == 2,
    str(plan["conflicts"]),
)


# ---------------------------------------------------------------------------
# 4. answering the question settles it — without a new state
# ---------------------------------------------------------------------------

same_plan = import_apply.build_plan(manifest, chosen={rel_of[name_a]: SAME_A, rel_of[name_b]: SAME_A})
merged = {i["aid"]: i for i in same_plan["items"]}
check(
    "answering 'same book' merges the two directories into one item",
    SAME_A in merged and SAME_B not in merged,
    str(sorted(merged)),
)
check("merged item kept both source dirs", len(merged[SAME_A]["source_dirs"]) == 2, str(merged[SAME_A]["source_dirs"]))
check("merged item kept both chapters", merged[SAME_A]["chapter_count"] == 2, str(merged[SAME_A]["chapters"].keys()))
check(
    "a recorded decision overrides the suspected_same block",
    not any("suspected_same" in c for c in same_plan["conflicts"]),
    str(same_plan["conflicts"]),
)

two_plan = import_apply.build_plan(manifest, chosen={rel_of[name_a]: SAME_A, rel_of[name_b]: SAME_B})
check(
    "answering 'two books' keeps them separate",
    {SAME_A, SAME_B} <= {i["aid"] for i in two_plan["items"]},
    str(sorted({i["aid"] for i in two_plan["items"]})),
)
check("'two books' token counts both", two_plan["summary"]["chapters"] >= 2, str(two_plan["summary"]))


# ---------------------------------------------------------------------------
# 5. magazine names: the tree decides, not the title
# ---------------------------------------------------------------------------

check(
    "magazine name with a structural tree is not blocked",
    import_scan.sweep_blockers(entries["[汉化组] [作者] 某作品 (COMIC アンスリウム 2025年6月号)"]) == [],
    "the {Aid}/{Pindex}/ tree proves the Aid",
)
# No Aid at all is the blocker; magazine_like rides along to document *why* the
# issue number was not taken as an Aid.  Both are blocking, so the row is refused.
issue_only = import_scan.sweep_blockers(entries["某作品 (COMIC LO 2017年7月号)"])
check(
    "magazine name with only an issue number is blocked",
    set(issue_only) == {"no_aid", "magazine_like"},
    str(issue_only),
)
check(
    "magazine name is still a visible label",
    "magazine_like" in entries["某作品 (COMIC LO 2017年7月号)"]["hints"],
    str(entries["某作品 (COMIC LO 2017年7月号)"]["hints"]),
)
check(
    "a real short Aid is valid, not invalid",
    "invalid_aid" not in import_scan.sweep_blockers(entries["[砂漠] おしえてあげる (COMIC LO 2017年7月号)"]),
    str(import_scan.sweep_blockers(entries["[砂漠] おしえてあげる (COMIC LO 2017年7月号)"])),
)
check("out-of-range number is still not an Aid", import_scan.AID_RE.fullmatch("12345678") is None, "12345678 accepted")
check(
    "issue number never became the Aid",
    entries["某作品 (COMIC LO 2017年7月号)"]["aid"] is None,
    str(entries["某作品 (COMIC LO 2017年7月号)"]["aid"]),
)


# ---------------------------------------------------------------------------
# 6. explicit selection is the human answer (no hint gate any more)
# ---------------------------------------------------------------------------

explicit = import_apply.build_plan(manifest, selected=[rel_of[name_a], rel_of[name_b]])
check(
    "explicit selection imports a hinted directory",
    {SAME_A, SAME_B} <= {i["aid"] for i in explicit["items"]},
    str(sorted({i["aid"] for i in explicit["items"]})),
)
check(
    "explicit selection does not resurrect the hint conflict",
    not any("suspected_same" in c for c in explicit["conflicts"]),
    str(explicit["conflicts"]),
)


# ---------------------------------------------------------------------------
# 7. the panel sees the hints (HTTP, read-only)
# ---------------------------------------------------------------------------

import_scan.write_manifest(manifest)

from fastapi.testclient import TestClient  # noqa: E402

from jmlib import api  # noqa: E402

client = TestClient(api.app)
r = client.get("/api/import/manifest")
body = r.json()
rows = {e["dir"]: e for e in body["entries"]}
check("manifest endpoint returns 200", r.status_code == 200, str(r.status_code))
check(
    "manifest endpoint includes hints",
    rows[name_a]["hints"] == entries[name_a]["hints"],
    str(rows[name_a].get("hints")),
)
check("panel source renders hint labels", "HINT_LABELS" in (paths.WEB / "import.html").read_text(encoding="utf-8"))
check("panel source mirrors the sweep rule", "sweepBlocked" in (paths.WEB / "import.html").read_text(encoding="utf-8"))
check(
    "planning over HTTP still refuses the suspected pair",
    SAME_A not in {i["aid"] for i in client.post("/api/import/plan", json={}).json()["items"]},
    "sweep over HTTP behaves like the module",
)


# ---------------------------------------------------------------------------
# 8. A.6 is still read-only
# ---------------------------------------------------------------------------

check("nothing wrote library bytes", snapshot(LIB) == {})
check("catalog untouched", store.stats()["books"] == 0, str(store.stats()))
check("source queue untouched", snapshot(SRC) == SOURCE_BEFORE)
check("no rollback manifest was produced", import_apply.load_rollback() is None)

print()
if fails:
    print(f"A6 checks: {len(fails)} FAILED -> {fails}")
    sys.exit(1)
print("A6 contract PASSED (offline, no network, zero library writes)")
