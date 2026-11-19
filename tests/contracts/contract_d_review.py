"""Offline contract for the D-slice review surface (`/resolve`, `/api/resolve*`).

No network, no real library.  What this pins down is the *boundary* of the review
page, because that is where a "convenient" change would do real damage:

1. It never moves a byte — no promote route is reachable from here, and every
   path constant points into the scratch tree.
2. It never calls upstream — opening the page must not spend a rate-limit slot,
   so ``upstream.search`` is replaced with a function that fails the test.
3. Clearing a decision keeps the candidate evidence; only the verdict is dropped.
4. A recorded override is what the governance step would act on, so it has to
   survive a round trip through the JSON payload unchanged.
"""
# Moved verbatim from the ``.tmp`` exploration scripts so CI runs it.
# Runs as its own process: every contract redirects ``jmlib.paths`` onto a
# scratch tree, and those redirects must not leak between suites.

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

BASE = ROOT / ".tmp" / "resolve_api_contracts"
if BASE.exists():
    shutil.rmtree(BASE)
BASE.mkdir(parents=True, exist_ok=True)

LIB = BASE / "library"
DATA = BASE / "data"
for sub in ("archive", "_unidentified", "covers", "_incoming"):
    (LIB / sub).mkdir(parents=True, exist_ok=True)
DATA.mkdir(parents=True, exist_ok=True)

from jmlib import paths  # noqa: E402

paths.ROOT = BASE
paths.DATA = DATA
paths.LIBRARY = LIB
paths.ARCHIVE = LIB / "archive"
paths.UNIDENTIFIED = LIB / "_unidentified"
paths.COVERS = LIB / "covers"
paths.INCOMING = LIB / "_incoming"
paths.STAGING = DATA / "staging"
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
paths.SINGLE_ROLLBACK = DATA / "single_rollback.json"

from fastapi.testclient import TestClient  # noqa: E402

from jmlib import api, idres, indexer, store, upstream  # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED
    if cond:
        PASSED += 1
        print(f"PASS {name}" + (f" {detail}" if detail else ""))
    else:
        FAILED.append(name)
        print(f"FAIL {name}" + (f" {detail}" if detail else ""))


def make_zip(path: Path, pages: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(1, pages + 1):
            zf.writestr(f"{i:05d}.jpg", b"\xff\xd8\xff" + bytes([i]) * 16)


def snapshot(root: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    if not root.exists():
        return out
    for p in root.rglob("*"):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = p.stat().st_size
    return out


# --------------------------------------------------------------------------- #
# fixture: one folder of chapter zips (a book) + two flat zips in a root
# --------------------------------------------------------------------------- #
FOLDER = paths.UNIDENTIFIED / "长篇" / "[汉化组] [作者] 某长篇"
make_zip(FOLDER / "001-某长篇.zip", 2)
make_zip(FOLDER / "002-某长篇.zip", 3)
FLAT_A = paths.UNIDENTIFIED / "JM(Android)"
make_zip(FLAT_A / "7654321[作者] 有车号的书.zip", 2)
make_zip(FLAT_A / "没有车号的书.zip", 1)

UNID_BEFORE = snapshot(paths.UNIDENTIFIED)
LIB_BEFORE = snapshot(paths.LIBRARY)

indexer.rebuild()  # populate the unresolved table the page reads

FOLDER_KEY = "library/_unidentified/长篇/[汉化组] [作者] 某长篇"
FLAT_KEY = "library/_unidentified/JM(Android)/7654321[作者] 有车号的书.zip"
NONAME_KEY = "library/_unidentified/JM(Android)/没有车号的书.zip"

client = TestClient(api.app)


# --------------------------------------------------------------------------- #
# 1. the page and its payload are read-only and network-free
# --------------------------------------------------------------------------- #
boom_calls = {"n": 0}


def boom(*a, **k):
    boom_calls["n"] += 1
    raise AssertionError("the review page must never call upstream")


real_search = upstream.search
real_album = upstream.album_info
upstream.search = boom
upstream.album_info = boom

page = client.get("/resolve")
check("the review page is served", page.status_code == 200, str(page.status_code))
check("the page loads its script from the assets mount", "/assets/resolve.js" in page.text and "<script" in page.text)

js = client.get("/assets/resolve.js")
check("the review script is served", js.status_code == 200, str(js.status_code))
check(
    "the script never posts to a promote route", '"/api/governance' not in js.text and "'/api/governance" not in js.text
)

overview = client.get("/api/resolve")
check("the overview answers", overview.status_code == 200, str(overview.status_code))
body = overview.json()
check("the overview declares itself read-only", "只读" in body["note"], body["note"][:30])
check("opening the review page made no upstream call", boom_calls["n"] == 0, str(boom_calls["n"]))
check("every unit is listed", body["units"] == 3, str(body["units"]))
check("the payload counts the unresolved zips", body["zips"] == 4, str(body["zips"]))

by_key = {u["key"]: u for u in body["items"]}
check("a folder of chapter zips is one unit", FOLDER_KEY in by_key, str(sorted(by_key)))
check(
    "the folder unit counts both chapter zips", by_key[FOLDER_KEY]["zipCount"] == 2, str(by_key[FOLDER_KEY]["zipCount"])
)
check("a flat zip is its own unit", NONAME_KEY in by_key)
check(
    "a filename Aid is surfaced as a hint", by_key[FLAT_KEY]["aidHint"] == "7654321", str(by_key[FLAT_KEY]["aidHint"])
)
check(
    "an untouched unit reports itself unresolved",
    by_key[FOLDER_KEY]["state"] == "unresolved",
    by_key[FOLDER_KEY]["state"],
)
check("nothing is promotable before a decision", body["promotable"] == 0, str(body["promotable"]))


# --------------------------------------------------------------------------- #
# 2. deciding, skipping, clearing
# --------------------------------------------------------------------------- #
bad = client.post("/api/resolve/decide", json={"key": FOLDER_KEY, "aid": "012"})
check("a malformed Aid is refused", bad.status_code == 400, str(bad.status_code))

short = client.post("/api/resolve/decide", json={"key": FOLDER_KEY, "aid": "121"})
check("a real short Aid is accepted", short.status_code == 200, str(short.status_code))

ghost = client.post("/api/resolve/decide", json={"key": "library/_unidentified/无此书", "aid": "7654321"})
check("an unknown unit is refused", ghost.status_code == 404, str(ghost.status_code))

ok = client.post("/api/resolve/decide", json={"key": FOLDER_KEY, "aid": "7654321"})
check("a decision is recorded", ok.status_code == 200 and ok.json()["ok"] is True, str(ok.status_code))
row = idres.get(FOLDER_KEY)
check(
    "the decision lands in id_resolution",
    row["state"] == "chosen" and row["chosen_aid"] == "7654321",
    json.dumps(row, ensure_ascii=False)[:80],
)

again = client.get("/api/resolve").json()
by_key = {u["key"]: u for u in again["items"]}
check(
    "the page reflects the recorded decision", by_key[FOLDER_KEY]["chosen"] == "7654321", by_key[FOLDER_KEY]["chosen"]
)
check("a new book is flagged as such", by_key[FOLDER_KEY]["wouldCreateBook"] is True)
check("the promotable count follows the decision", again["promotable"] == 1, str(again["promotable"]))
check("the promotable zip count follows the decision", again["promotableZips"] == 2, str(again["promotableZips"]))

# an Aid that already has archives is an append, not a new book
make_zip(paths.ARCHIVE / "7654321" / "1.zip", 2)
appended = {u["key"]: u for u in client.get("/api/resolve").json()["items"]}
check(
    "an existing book is flagged as an append",
    appended[FOLDER_KEY]["wouldCreateBook"] is False,
    str(appended[FOLDER_KEY]["wouldCreateBook"]),
)
shutil.rmtree(paths.ARCHIVE / "7654321")

skip = client.post("/api/resolve/skip", json={"key": NONAME_KEY, "reason": "暂不确定"})
check("a unit can be skipped", skip.status_code == 200 and skip.json()["ok"] is True)
check("the skip is stored", idres.get(NONAME_KEY)["state"] == "skipped")
check(
    "the skip keeps the reviewer's reason", idres.get(NONAME_KEY)["note"] == "暂不确定", idres.get(NONAME_KEY)["note"]
)

# a decision with candidates must survive the clear
idres.record(
    FOLDER_KEY, "某长篇", [{"id": "7654321", "name": "某长篇", "score": 0.97, "hits": ["标题 bigram 重合 0.97"]}]
)
idres.set_choice(FOLDER_KEY, "7654321", "人工指定")
before_clear = idres.get(FOLDER_KEY)
cleared = client.post("/api/resolve/clear", json={"key": FOLDER_KEY})
check("clearing a decision succeeds", cleared.status_code == 200 and cleared.json()["ok"] is True)
after_clear = idres.get(FOLDER_KEY)
check("clearing keeps the row", after_clear is not None)
check("clearing moves the unit back to pending", after_clear["state"] == "pending", after_clear["state"])
check("clearing drops the chosen Aid", after_clear["chosen_aid"] is None, str(after_clear["chosen_aid"]))
check(
    "clearing keeps the candidate evidence",
    len(after_clear["candidates"]) == len(before_clear["candidates"]) == 1,
    str(after_clear["candidates"]),
)
check(
    "clearing keeps the query the unit was searched with",
    after_clear["query"] == before_clear["query"],
    after_clear["query"],
)

no_row = client.post("/api/resolve/clear", json={"key": NONAME_KEY})
check("clearing a pending-without-decision unit still works", no_row.status_code == 200, str(no_row.status_code))
ghost_clear = client.post("/api/resolve/clear", json={"key": "library/_unidentified/无此书"})
check("clearing an unknown unit is a 404", ghost_clear.status_code == 404, str(ghost_clear.status_code))


# --------------------------------------------------------------------------- #
# 3. filters
# --------------------------------------------------------------------------- #
all_units = client.get("/api/resolve").json()["items"]
expected_pending = [u["key"] for u in all_units if u["state"] == "pending"]
pending_only = client.get("/api/resolve?state=pending").json()
check(
    "filtering by state narrows the list",
    pending_only["total"] == len(expected_pending) < len(all_units)
    and sorted(u["key"] for u in pending_only["items"]) == sorted(expected_pending),
    f"pending={pending_only['total']} expected={len(expected_pending)} all={len(all_units)}",
)
chosen_only = client.get("/api/resolve?state=chosen").json()
check("an empty state filter is honest", chosen_only["total"] == 0, str(chosen_only["total"]))
by_gen = client.get("/api/resolve?generation=JM(Android)").json()
check("filtering by generation narrows the list", by_gen["total"] == 2, str(by_gen["total"]))
found = client.get("/api/resolve?q=7654321").json()
check(
    "search finds a unit by its Aid", found["total"] == 1 and found["items"][0]["key"] == FLAT_KEY, str(found["total"])
)
check("the summary still counts the whole shelf", found["units"] == 3, str(found["units"]))


# --------------------------------------------------------------------------- #
# 4. zero side effects on the archive, and PIN still gates the page
# --------------------------------------------------------------------------- #
upstream.search = real_search
upstream.album_info = real_album

check(
    "the unidentified shelf is byte-identical", snapshot(paths.UNIDENTIFIED) == UNID_BEFORE, f"{len(UNID_BEFORE)} files"
)
check("the library is byte-identical", snapshot(paths.LIBRARY) == LIB_BEFORE, f"{len(LIB_BEFORE)} files")
check(
    "decisions touched no catalog row",
    store.stats()["books"] == 0 and store.stats()["chapters"] == 0,
    str(store.stats()),
)

api.app.state.pin = "s3cret"
try:
    locked = TestClient(api.app)
    check("PIN blocks the review page", locked.get("/resolve").status_code == 401)
    check("PIN blocks the overview", locked.get("/api/resolve").status_code == 401)
    check(
        "PIN blocks a decision",
        locked.post("/api/resolve/decide", json={"key": FOLDER_KEY, "aid": "7654321"}).status_code == 401,
    )
    check("the correct PIN passes", locked.get("/api/resolve?pin=s3cret").status_code == 200)
finally:
    api.app.state.pin = None

print()
if FAILED:
    print(f"RESOLVE_API checks: {len(FAILED)} failed -> {FAILED}")
    raise SystemExit(1)
print(f"RESOLVE_API checks: {PASSED} passed, 0 failed")
print("D review-surface contract PASSED (offline, no network, zero real-library access)")
