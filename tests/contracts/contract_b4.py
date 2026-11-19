"""B.4 contract: collections / reading lists.

Offline, scratch-only.  What is proved:

1. Adding the same book twice is idempotent (no duplicate row, no error).
2. Order is explicit and stable; unknown Aids in a reorder are ignored.
3. Deleting a collection removes membership rows and **not** the books.
4. A book can sit on a shelf label (``books.collection``) and in several named
   lists at once — the two owners never infer each other.
5. HTTP layer: CRUD round-trip, 404s for unknown list/book.
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

BASE = Path(__file__).resolve().parents[2] / ".tmp" / "b4_contracts"
if BASE.exists():
    shutil.rmtree(BASE, ignore_errors=True)
BASE.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

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


def make_zip(aid: str, pages: int = 1) -> None:
    target = ARCHIVE / aid / "1.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w") as zf:
        for n in range(1, pages + 1):
            zf.writestr(f"{n:05d}.jpg", b"\xff\xd8" + bytes([n]) * 30)


AIDS = ["710001", "710002", "710003"]
for aid in AIDS:
    make_zip(aid)
indexer.rebuild()
check("fixture indexed three books", store.stats()["books"] == 3, str(store.stats()))

# --- 1. create + idempotent add ---------------------------------------------
col = store.create_collection("我的精选", "测试用")
check("collection created", col["name"] == "我的精选" and col["id"] > 0, str(col))
again = store.create_collection("我的精选")
check("creating the same name returns the same list", again["id"] == col["id"])

check("add first book", store.add_to_collection(col["id"], AIDS[0]) is True)
check("add second book", store.add_to_collection(col["id"], AIDS[1]) is True)
check("adding the same book twice is a no-op", store.add_to_collection(col["id"], AIDS[0]) is True)
detail = store.get_collection(col["id"])
check(
    "membership is not duplicated",
    [i["aid"] for i in detail["items"]] == [AIDS[0], AIDS[1]],
    str([i["aid"] for i in detail["items"]]),
)
check("item_count matches membership", store.list_collections()[0]["item_count"] == 2)
check("adding to an unknown list is refused", store.add_to_collection(999999, AIDS[0]) is False)

# --- 2. order ---------------------------------------------------------------
order = store.reorder_collection(col["id"], [AIDS[1], AIDS[0]])
check("reorder applies the explicit order", order == [AIDS[1], AIDS[0]], str(order))
detail = store.get_collection(col["id"])
check(
    "stored positions follow the order",
    [i["position"] for i in detail["items"]] == [1, 2],
    str([(i["aid"], i["position"]) for i in detail["items"]]),
)
order = store.reorder_collection(col["id"], [AIDS[2]])
check("unknown Aids in a reorder are ignored", order == [AIDS[1], AIDS[0]], str(order))

# --- 3. shelves vs lists are separate owners --------------------------------
store.set_user_metadata(AIDS[0], {"collection": "书架甲"})
shelves = {b["collection"] for b in store.list_books() if b["collection"]}
check("shelf label is its own field", shelves == {"书架甲"}, str(shelves))
check(
    "shelf label did not add list membership",
    [i["aid"] for i in store.get_collection(col["id"])["items"]] == [AIDS[1], AIDS[0]],
)
in_lists = store.list_collections_for_book(AIDS[0])
check("book knows which lists it is in", [c["name"] for c in in_lists] == ["我的精选"], str(in_lists))
check(
    "a book may be in several lists",
    store.add_to_collection(store.create_collection("第二张单子")["id"], AIDS[0]) is True,
)
check(
    "two lists, one book",
    len(store.list_collections_for_book(AIDS[0])) == 2,
    str(store.list_collections_for_book(AIDS[0])),
)
check(
    "list_books filters by shelf",
    len(store.list_books(collection="书架甲")) == 1,
    str([b["aid"] for b in store.list_books(collection="书架甲")]),
)
check("list_books filters by favorite", len(store.list_books(favorite=True)) == 0)

# --- 4. removal never touches books ----------------------------------------
check("remove one book from a list", store.remove_from_collection(col["id"], AIDS[1]) is True)
check("removing a book that is not there is False", store.remove_from_collection(col["id"], AIDS[1]) is False)

zips_before = sorted(p for p in ARCHIVE.rglob("*.zip"))
books_before = store.stats()["books"]
check("delete one collection", store.delete_collection(col["id"]) is True)
check("deleted collection is gone", store.get_collection(col["id"]) is None)
check(
    "deleting a collection kept every book",
    store.stats()["books"] == books_before,
    f"{books_before} -> {store.stats()['books']}",
)
check("deleting a collection kept every zip", sorted(p for p in ARCHIVE.rglob("*.zip")) == zips_before)
check(
    "deleting a collection kept the other list",
    len(store.list_collections()) == 1 and store.list_collections()[0]["name"] == "第二张单子",
)
check(
    "membership rows for the deleted list are gone",
    store.list_collections_for_book(AIDS[1]) == [],
    str(store.list_collections_for_book(AIDS[1])),
)
check("deleting an unknown list is False", store.delete_collection(999999) is False)

# --- 5. HTTP ----------------------------------------------------------------
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(api.app)

r = client.post("/api/collections", json={"name": "接口单子", "note": "n"})
check(
    "POST /api/collections creates",
    r.status_code == 200 and r.json()["collection"]["name"] == "接口单子",
    str(r.status_code),
)
cid = r.json()["collection"]["id"]
r = client.post("/api/collections", json={"name": ""})
check("empty name is refused", r.status_code == 400, str(r.status_code))
r = client.get("/api/collections")
check(
    "GET /api/collections lists",
    r.status_code == 200 and any(c["id"] == cid for c in r.json()["items"]),
    str(r.status_code),
)
r = client.post(f"/api/collections/{cid}/items", json={"aid": AIDS[2]})
check(
    "POST items adds a book",
    r.status_code == 200 and [i["aid"] for i in r.json()["collection"]["items"]] == [AIDS[2]],
    str(r.status_code),
)
r = client.post(f"/api/collections/{cid}/items", json={"aid": "799999"})
check("adding an unknown book is 404", r.status_code == 404, str(r.status_code))
r = client.post("/api/collections/999999/items", json={"aid": AIDS[2]})
check("adding to an unknown list is 404", r.status_code == 404, str(r.status_code))
r = client.get(f"/api/books/{AIDS[2]}/collections")
check("book -> lists endpoint", r.status_code == 200 and len(r.json()["items"]) == 1, str(r.json()))
r = client.post(f"/api/collections/{cid}/order", json={"aids": [AIDS[2]]})
check("order endpoint round-trips", r.status_code == 200 and r.json()["order"] == [AIDS[2]], str(r.json()))
r = client.delete(f"/api/collections/{cid}/items/{AIDS[2]}")
check("DELETE item removes membership", r.status_code == 200 and r.json()["collection"]["items"] == [], str(r.json()))
r = client.delete(f"/api/collections/{cid}/items/{AIDS[2]}")
check("DELETE an absent member is 404", r.status_code == 404, str(r.status_code))
r = client.delete(f"/api/collections/{cid}")
check("DELETE collection works", r.status_code == 200 and r.json()["deleted"] == cid, str(r.json()))
r = client.get(f"/api/collections/{cid}")
check("GET a deleted collection is 404", r.status_code == 404, str(r.status_code))

lib = client.get("/api/library", params={"collection": "书架甲"}).json()
check("library endpoint filters by shelf", lib["total"] == 1 and lib["items"][0]["aid"] == AIDS[0], str(lib["total"]))
check("library endpoint exposes the shelf list", lib["shelves"] == ["书架甲"], str(lib["shelves"]))

check(
    "every fixture zip survived the whole contract",
    len(sorted(ARCHIVE.rglob("*.zip"))) == 3,
    str(sorted(p.name for p in ARCHIVE.rglob("*.zip"))),
)

print()
if FAILED:
    print(f"B4 checks: {PASSED} passed, {len(FAILED)} failed")
    print("FAILED:", FAILED)
    raise SystemExit(1)
print(f"B4 checks: {PASSED} passed, 0 failed")
print("B4 contract PASSED (offline, no network, zero real-library access)")
