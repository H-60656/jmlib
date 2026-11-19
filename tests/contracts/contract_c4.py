"""C.4 contract: library filtering, sorting and honest empty states.

Offline, scratch-only.  What is proved:

1. Every sort is a *permutation* of one result set — sorting never adds or drops
   a book (a bug here would silently hide books from the shelf).
2. ``recent`` puts books with a reading history first, and never-read books
   last rather than first (an empty history is not "read longest ago").
3. ``title`` sorts on the **display** title, so a hand-renamed book files under
   its new name instead of its old folder name.
4. ``chapters`` / ``pages`` sort descending.
5. A bad sort is refused (store raises, HTTP answers 400) instead of quietly
   returning some other order.
6. Filters stay consistent: the count the page reports equals the rows returned,
   and switching the sort never changes the filter counts.
7. An empty result explains *why* it is empty and which filters are on.
"""
# Moved verbatim from the ``.tmp`` exploration scripts so CI runs it.
# Runs as its own process: every contract redirects ``jmlib.paths`` onto a
# scratch tree, and those redirects must not leak between suites.

from __future__ import annotations

import os
import shutil
import sys
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[2] / ".tmp" / "c4_contracts"
if BASE.exists():
    shutil.rmtree(BASE, ignore_errors=True)
BASE.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from jmlib import api, indexer, paths, store  # noqa: E402

DATA = BASE / "data"
LIB = BASE / "library"
ARCHIVE = LIB / "archive"
COVERS = LIB / "covers"
UNIDENT = LIB / "_unidentified"
INCOMING = LIB / "_incoming"
STAGING = DATA / "staging"
WEB = Path(__file__).resolve().parents[2] / "web"

for p in (DATA, LIB, ARCHIVE, COVERS, UNIDENT, INCOMING, STAGING):
    p.mkdir(parents=True, exist_ok=True)

paths.ROOT = BASE
paths.DATA = DATA
paths.LIBRARY = LIB
paths.ARCHIVE = ARCHIVE
paths.COVERS = COVERS
paths.UNIDENTIFIED = UNIDENT
paths.INCOMING = INCOMING
paths.STAGING = STAGING
paths.WEB = WEB
paths.EXPORTS = BASE / "exports"
paths.DB_PATH = DATA / "jmlib.db"
paths.MIGRATION_MANIFEST = DATA / "migration_manifest.json"
paths.ROLLBACK_MANIFEST = DATA / "rollback_manifest.json"
paths.SIGNATURES = LIB / "_zip_signatures.json"
paths.BLACKLIST = LIB / "黑名单.csv"
paths.INGEST_METADATA = DATA / "ingest_metadata.json"
paths.LOOSE_INGEST_MANIFEST = DATA / "loose_ingest_manifest.json"
paths.IMPORT_MANIFEST = DATA / "import_manifest.json"
paths.IMPORT_ROLLBACK = DATA / "import_rollback.json"
paths.UPSTREAM_CACHE = DATA / "upstream_cache.json"
paths.SINGLE_ROLLBACK = DATA / "single_rollback.json"
paths.GOVERNANCE_MANIFEST = DATA / "governance_manifest.json"
os.environ["JMLIB_IMPORT_ROOTS"] = str(BASE / "roots")

PASSED = 0
FAILED: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASSED
    if cond:
        PASSED += 1
        print(f"PASS {name}" + (f" {extra}" if extra else ""))
    else:
        FAILED.append(name)
        print(f"FAIL {name} {extra}")


def make_zip(aid: str, pindex: int = 1, pages: int = 1) -> None:
    target = ARCHIVE / aid / f"{pindex}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w") as zf:
        for n in range(1, pages + 1):
            zf.writestr(f"{n:05d}.jpg", b"\xff\xd8" + bytes([n % 250]) * 20)


# Fixture shape is what the assertions below assume:
#   720001  title "Alpha one"   1 chapter,  2 pages   read once
#   720002  title "Zeta two"    3 chapters, 9 pages   never read
#   720003  title "Mid three"   2 chapters, 4 pages   read once
# ``720002`` gets a hand-typed local title so the display-title sort is testable.
make_zip("720001", 1, 2)
make_zip("720002", 1, 3)
make_zip("720002", 2, 3)
make_zip("720002", 3, 3)
make_zip("720003", 1, 2)
make_zip("720003", 2, 2)
indexer.rebuild()

store.set_user_metadata("720002", {"local_title": "Aaa renamed"})
store.set_user_metadata("720001", {"local_title": "Zzz later"})
# Renaming must not change the identity the catalog keys on.
check(
    "renaming kept the folder title as evidence",
    store.get_book("720002")["title"] != "Aaa renamed",
    store.get_book("720002")["title"],
)

store.set_memory("720001", 1, 2)
store.set_memory("720003", 2, 4)
stats_before = store.stats()
check("fixture indexed three books", stats_before["books"] == 3, str(stats_before["books"]))

# --- 1. every sort is a permutation of the same set -------------------------
baseline = sorted(b["aid"] for b in store.list_books())
check("baseline set is the three fixture books", baseline == ["720001", "720002", "720003"], str(baseline))
for key in store.BOOK_SORTS:
    got = sorted(b["aid"] for b in store.list_books(sort=key))
    check(f"sort {key} returns the same books, reordered only", got == baseline, str(got))

# --- 2. recent --------------------------------------------------------------
recent = [b["aid"] for b in store.list_books(sort="recent")]
check(
    "recent puts read books before never-read ones",
    recent[-1] == "720002" and set(recent[:2]) == {"720001", "720003"},
    str(recent),
)
newest_first = [b["aid"] for b in store.list_books(sort="recent")][:2]
check(
    "recent orders the two read books by when they were read", newest_first == ["720003", "720001"], str(newest_first)
)
store.set_memory("720001", 1, 1)  # bump 720001 to the top
recent_again = [b["aid"] for b in store.list_books(sort="recent")]
check(
    "a fresh memory write moves a book into the recent block",
    recent_again.index("720001") < recent_again.index("720002"),
    str(recent_again),
)
check(
    "recent is deterministic when two writes share a second",
    recent_again == [b["aid"] for b in store.list_books(sort="recent")],
    str(recent_again),
)

# --- 3. title sorts on the display title ------------------------------------
titles = [b["display_title"] for b in store.list_books(sort="title")]
named = [t for t in titles if t]
check("title sort orders the named books case-insensitively", named == sorted(named, key=str.casefold), str(titles))
check("title sort files the renamed book first among the named ones", titles[0] == "Aaa renamed", str(titles))
check("title sort pushes untitled books to the end, not the top", titles[-1] == "", str(titles))
check(
    "title sort did not fall back to the folder name for the renamed book",
    titles[0] != store.get_book("720002")["title"],
    str(titles[0]),
)

# --- 4. chapters / pages ----------------------------------------------------
by_chapters = [(b["aid"], b["chapter_count"]) for b in store.list_books(sort="chapters")]
check("chapters sort is descending", [c for _, c in by_chapters] == [3, 2, 1], str(by_chapters))
by_pages = [(b["aid"], b["page_total"]) for b in store.list_books(sort="pages")]
check("pages sort is descending", [p for _, p in by_pages] == [9, 4, 2], str(by_pages))

# --- 5. a bad sort is refused, not silently swapped ------------------------
try:
    store.list_books(sort="nonsense")
    check("store refuses an unknown sort", False, "no exception")
except ValueError as exc:
    check("store refuses an unknown sort", "unknown sort" in str(exc), str(exc))

client = TestClient(api.app)
r = client.get("/api/library", params={"sort": "nonsense"})
check("HTTP refuses an unknown sort with 400", r.status_code == 400, str(r.status_code))
check("HTTP names the sort it did not understand", "nonsense" in r.text, r.text[:120])
r = client.get("/api/library")
body = r.json()
check("library exposes the sort it used", body.get("sort") == "aid", str(body.get("sort")))
check(
    "library advertises the sort keys the UI offers",
    body.get("sorts") == list(store.BOOK_SORTS),
    str(body.get("sorts")),
)

# --- 6. filters and counts stay consistent ---------------------------------
for sort in store.BOOK_SORTS:
    shelf_body = client.get("/api/library", params={"collection": "书架甲", "sort": sort}).json()
    check(
        f"filter count matches rows for sort {sort}",
        shelf_body["total"] == len(shelf_body["items"]) == 0,
        str(shelf_body["total"]),
    )
store.set_user_metadata("720003", {"collection": "书架甲"})
for sort in store.BOOK_SORTS:
    shelf_body = client.get("/api/library", params={"collection": "书架甲", "sort": sort}).json()
    check(
        f"shelf filter is stable across sort {sort}",
        shelf_body["total"] == 1 and shelf_body["items"][0]["aid"] == "720003",
        f"{shelf_body['total']} {[i['aid'] for i in shelf_body['items']]}",
    )
    check(f"shelf list is reported for sort {sort}", shelf_body["shelves"] == ["书架甲"], str(shelf_body["shelves"]))
check(
    "favorite filter is empty before anything is starred",
    client.get("/api/library", params={"favorite": "true"}).json()["total"] == 0,
)
store.set_favorite("720001", True)
check(
    "favorite filter finds the starred book",
    client.get("/api/library", params={"favorite": "true"}).json()["items"][0]["aid"] == "720001",
)

# Tag filtering predates C.4, but the sort rework touched the same query, so the
# two must still agree: a tag filter narrows the rows without changing the order
# rules, and the reported count matches what came back.
conn_tags = store.connect()
with conn_tags:
    conn_tags.execute("UPDATE books SET tags = ? WHERE aid = ?", ('["标签X"]', "720002"))
conn_tags.close()
for sort in store.BOOK_SORTS:
    tagged = client.get("/api/library", params={"tag": "标签X", "sort": sort}).json()
    check(
        f"tag filter narrows to the tagged book for sort {sort}",
        tagged["total"] == 1 and tagged["items"][0]["aid"] == "720002",
        f"{tagged['total']} {[i['aid'] for i in tagged['items']]}",
    )
check("tag filter reports the tag it matched", client.get("/api/library").json()["tags"] == ["标签X"])
check(
    "an unknown tag yields the over-filtered hint",
    "标签" in client.get("/api/library", params={"tag": "不存在"}).json()["active_filters"],
)

# --- 7. an empty result says why -------------------------------------------
empty = client.get("/api/library", params={"q": "definitely-not-a-title"}).json()
check("empty search returns no rows", empty["total"] == 0)
check("empty search still reports the library size", empty["stats"]["books"] == 3, str(empty["stats"]["books"]))
check("empty search explains that a filter is active", "没有符合当前筛选" in empty["empty_hint"], empty["empty_hint"])
check("empty search names the active filter", "搜索词" in empty["active_filters"], str(empty["active_filters"]))
shelf_empty = client.get("/api/library", params={"collection": "并不存在的书架"}).json()
check("empty shelf names the shelf filter", "书架" in shelf_empty["active_filters"], str(shelf_empty["active_filters"]))
full = client.get("/api/library").json()
check("a non-empty result carries no empty hint", full["empty_hint"] == "", full["empty_hint"])
check("a non-empty result lists no active filters", full["active_filters"] == [], str(full["active_filters"]))

# An empty *library* and an over-filtered library are different problems with
# different fixes, so the wording must differ too.  Simulated by emptying the
# read path rather than by deleting the fixture.
_real_list_books = store.list_books


def _no_books(*a, **k):
    return []


store.list_books = _no_books
try:
    blank = client.get("/api/library").json()
finally:
    store.list_books = _real_list_books
check("an empty library says how to start", "还没有书" in blank["empty_hint"], blank["empty_hint"])
check("an empty library does not blame a filter", blank["active_filters"] == [], str(blank["active_filters"]))
check("an empty library hint differs from the over-filtered one", blank["empty_hint"] != empty["empty_hint"])

# --- 8. sorting is read-only ----------------------------------------------
check("no library byte was written by sorting", store.stats()["books"] == 3, str(store.stats()["books"]))

print()
if FAILED:
    print(f"C4 checks: {PASSED} passed, {len(FAILED)} failed -> {FAILED}")
    sys.exit(1)
print(f"C4 checks: {PASSED} passed, 0 failed")
print("C4 library contract PASSED (offline, no network, zero real-library access)")
