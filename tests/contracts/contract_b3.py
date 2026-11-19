"""B.3 contract: user metadata (local_title / aliases / favorite / collection).

Offline and scratch-only: every ``jmlib.paths`` constant is redirected to
``.tmp/b3_contracts`` before any store call, so the real 25 GB library is never
touched.  What is being proved:

1. A rebuild (``replace_catalog``) carries hand edits across instead of wiping
   them — that is the whole point of storing them in their own columns.
2. ``display_title`` prefers the user's name and keeps the folder name intact.
3. Search finds a book by its alias.
4. ``PATCH`` rejects unknown fields and an Aid that has no book row.
5. A favourite toggle writes one flag and no bytes.
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

BASE = Path(__file__).resolve().parents[2] / ".tmp" / "b3_contracts"
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


def make_zip(aid: str, pindex: int, pages: int) -> None:
    target = ARCHIVE / aid / f"{pindex}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w") as zf:
        for n in range(1, pages + 1):
            zf.writestr(f"{n:05d}.jpg", b"\xff\xd8" + bytes([n]) * 40)


AID = "700001"
AID2 = "700002"
make_zip(AID, 1, 2)
make_zip(AID2, 1, 1)

rebuilt = indexer.rebuild()
stats = store.stats()
check("fixture indexed two books", stats["books"] == 2 and rebuilt == 0, str(stats))

# --- 1. hand edits -----------------------------------------------------------
folder_title = store.get_book(AID)["title"]
book = store.set_user_metadata(
    AID,
    {
        "local_title": "我给它起的名字",
        "aliases": ["别名甲", "alias-b"],
        "collection": "合集A",
    },
)
check("local_title is stored", book["local_title"] == "我给它起的名字")
check("aliases are stored as a list", book["aliases"] == ["别名甲", "alias-b"], str(book["aliases"]))
check("collection is stored", book["collection"] == "合集A")
check("display_title prefers the local title", book["display_title"] == "我给它起的名字")
check(
    "folder-derived title column is preserved", book["title"] == folder_title, f"{book['title']!r} vs {folder_title!r}"
)

book = store.set_favorite(AID, True)
check("favorite flag is on", book["favorite"] == 1)
check("favourite does not clear the local title", book["local_title"] == "我给它起的名字")

# --- 2. search by alias ------------------------------------------------------
hits = store.list_books(q="别名甲")
check("search finds a book by alias", [b["aid"] for b in hits] == [AID], str([b["aid"] for b in hits]))
hits = store.list_books(q="我给它起")
check("search finds a book by local title", [b["aid"] for b in hits] == [AID])
hits = store.list_books()
fav = [b for b in hits if b["aid"] == AID][0]
check(
    "list_books carries the decoded columns",
    fav["local_title"] == "我给它起的名字" and fav["aliases"] == ["别名甲", "alias-b"],
    str({k: fav[k] for k in ("local_title", "aliases", "favorite", "collection")}),
)

# --- 3. rebuild keeps hand edits --------------------------------------------
indexer.rebuild()
after = store.get_book(AID)
check("rebuild kept local_title", after["local_title"] == "我给它起的名字")
check("rebuild kept aliases", after["aliases"] == ["别名甲", "alias-b"], str(after["aliases"]))
check("rebuild kept favorite", after["favorite"] == 1)
check("rebuild kept collection", after["collection"] == "合集A")
check(
    "rebuild did not invent edits on an untouched book",
    store.get_book(AID2)["local_title"] == "" and store.get_book(AID2)["favorite"] == 0,
)

# a book that vanished from disk does not resurrect its edits later
make_zip(AID2, 2, 1)
indexer.rebuild()
check("rebuild after a new chapter still keeps the edit", store.get_book(AID)["local_title"] == "我给它起的名字")

# --- 4. validation ----------------------------------------------------------
try:
    store.set_user_metadata(AID, {"title": "attempt to overwrite a derived column"})
    check("store rejects a non-user column", False)
except ValueError as exc:
    check("store rejects a non-user column", "not user-editable" in str(exc), str(exc))

check(
    "an unknown Aid returns None, not a phantom row",
    store.set_user_metadata("799999", {"local_title": "ghost"}) is None,
)
check("no phantom book row was created", store.get_book("799999") is None)

# empty string clears the local title, falling back to the folder title
book = store.set_user_metadata(AID, {"local_title": ""})
check(
    "clearing local_title restores the folder title as display",
    book["display_title"] == folder_title,
    f"{book['display_title']!r} vs {folder_title!r}",
)

# --- 5. HTTP -----------------------------------------------------------------
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(api.app)

r = client.patch(f"/api/books/{AID}", json={"local_title": "接口改的名字"})
check("PATCH renames over HTTP", r.status_code == 200 and r.json()["local_title"] == "接口改的名字", str(r.status_code))
r = client.patch(f"/api/books/{AID}", json={"aliases": ["接口别名"]})
check(
    "PATCH touches only the fields it was given",
    r.json()["local_title"] == "接口改的名字" and r.json()["aliases"] == ["接口别名"],
    str(r.json().get("local_title")),
)
r = client.patch(f"/api/books/{AID}", json={})
check("PATCH with no field is refused", r.status_code == 400, str(r.status_code))
r = client.patch("/api/books/799999", json={"local_title": "ghost"})
check("PATCH on an unknown Aid is 404", r.status_code == 404, str(r.status_code))
r = client.patch("/api/books/abc", json={"local_title": "x"})
check("PATCH with an invalid Aid is 400", r.status_code == 400, str(r.status_code))
r = client.post(f"/api/books/{AID}/favorite", json={"favorite": False})
check("favorite endpoint clears the flag", r.status_code == 200 and r.json()["favorite"] == 0, str(r.json()))
r = client.post("/api/books/799999/favorite", json={"favorite": True})
check("favorite endpoint 404s an unknown Aid", r.status_code == 404, str(r.status_code))

detail = client.get(f"/api/books/{AID}").json()
check(
    "book detail exposes the user columns",
    detail["display_title"] == "接口改的名字" and detail["aliases"] == ["接口别名"],
    str({k: detail[k] for k in ("display_title", "aliases", "favorite")}),
)

lib = client.get("/api/library", params={"q": "接口别名"}).json()
check("library search hits the alias over HTTP", lib["total"] == 1 and lib["items"][0]["aid"] == AID, str(lib["total"]))

# --- 6. no bytes touched ----------------------------------------------------
check(
    "metadata writes touched no archive byte",
    sum(1 for _ in ARCHIVE.rglob("*.zip")) == 3,
    str(sorted(p.name for p in ARCHIVE.rglob("*.zip"))),
)
check(
    "metadata writes left the schema where store declares it",
    store.stats()["schemaVersion"] == store.SCHEMA_VERSION,
    store.stats()["schemaVersion"],
)

print()
if FAILED:
    print(f"B3 checks: {PASSED} passed, {len(FAILED)} failed")
    print("FAILED:", FAILED)
    raise SystemExit(1)
print(f"B3 checks: {PASSED} passed, 0 failed")
print("B3 contract PASSED (offline, no network, zero real-library access)")
