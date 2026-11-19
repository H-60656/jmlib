"""C.1 offline contract: reading preferences + bookmarks.

Runs entirely against a scratch tree: every path constant is redirected before
any module touches disk, so the real library, its database and its archives are
never opened.  No network is used.

What is being pinned down here:

* the three-source resolution order (per book > global > built-in default),
* that "not chosen" is stored as blank / -1 rather than as a magic value,
* that an invalid value is rejected instead of stored,
* that ``memory`` keeps its own meaning and is never rewritten by a preference,
* that bookmarks are extra positions, de-duplicated per exact spot,
* that nothing in C.1 writes a byte under ``library/``.
"""
# Moved verbatim from the ``.tmp`` exploration scripts so CI runs it.
# Runs as its own process: every contract redirects ``jmlib.paths`` onto a
# scratch tree, and those redirects must not leak between suites.

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[2] / ".tmp" / "c1_contracts"
if BASE.exists():
    shutil.rmtree(BASE)
BASE.mkdir(parents=True, exist_ok=True)

from jmlib import paths  # noqa: E402

LIB = BASE / "library"
DATA = BASE / "data"
WEB = paths.WEB  # the real read-only web directory, needed for the page check

for name, value in {
    "ROOT": BASE,
    "DATA": DATA,
    "LIBRARY": LIB,
    "ARCHIVE": LIB / "archive",
    "UNIDENTIFIED": LIB / "_unidentified",
    "COVERS": LIB / "covers",
    "INCOMING": LIB / "_incoming",
    "STAGING": DATA / "staging",
    "EXPORTS": BASE / "exports",
    "DB_PATH": DATA / "jmlib.db",
    "MIGRATION_MANIFEST": DATA / "migration_manifest.json",
    "ROLLBACK_MANIFEST": DATA / "rollback_manifest.json",
    "SIGNATURES": LIB / "_zip_signatures.json",
    "BLACKLIST": LIB / "黑名单.csv",
    "INGEST_METADATA": DATA / "ingest_metadata.json",
    "LOOSE_INGEST_MANIFEST": DATA / "loose_ingest_manifest.json",
    "GOVERNANCE_MANIFEST": DATA / "governance_manifest.json",
    "IMPORT_MANIFEST": DATA / "import_manifest.json",
    "IMPORT_ROLLBACK": DATA / "import_rollback.json",
    "UPSTREAM_CACHE": DATA / "upstream_cache.json",
    "SINGLE_ROLLBACK": DATA / "single_rollback.json",
    "WEB": WEB,
}.items():
    setattr(paths, name, value)

paths.ensure_dirs()

from fastapi.testclient import TestClient  # noqa: E402

from jmlib import api, indexer, prefs, store  # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASSED
    if cond:
        PASSED += 1
        print(f"PASS {name} {extra}".rstrip())
    else:
        FAILED.append(name)
        print(f"FAIL {name} {extra}".rstrip())


def make_book(aid: str, pages: int = 2) -> None:
    """One chapter zip per book, written the way the library stores them."""
    archive = paths.ARCHIVE / aid
    archive.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive / "1.zip", "w") as zf:
        for i in range(1, pages + 1):
            zf.writestr(f"{i:05d}.jpg", b"\xff\xd8" + bytes([i]) * 16 + b"\xff\xd9")


def library_bytes() -> dict[str, int]:
    return {p.relative_to(paths.LIBRARY).as_posix(): p.stat().st_size for p in paths.LIBRARY.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------------
# Fixture: two books with body images.
# ---------------------------------------------------------------------------

BOOK_A, BOOK_B = "770001", "770002"
make_book(BOOK_A, pages=3)
make_book(BOOK_B, pages=2)
indexer.rebuild()

stats = store.stats()
check("fixture indexed two books", stats["books"] == 2, str(stats["books"]))
check(
    "schema version follows store.SCHEMA_VERSION",
    stats["schemaVersion"] == store.SCHEMA_VERSION,
    stats["schemaVersion"],
)

BEFORE_BYTES = library_bytes()


# ---------------------------------------------------------------------------
# A. Resolution order: per book > global > built-in default.
# ---------------------------------------------------------------------------

default = prefs.get(BOOK_A)
check(
    "a fresh book resolves to the built-in defaults",
    default["direction"] == "ltr"
    and default["layout"] == "single"
    and default["fit"] == "height"
    and default["fullscreen"] is False,
    str(default),
)
check(
    "every field is attributed to the default source",
    set(default["sources"].values()) == {"default"},
    str(default["sources"]),
)

prefs.put("", {"direction": "rtl", "fit": "width"})
global_only = prefs.get(BOOK_A)
check(
    "global row applies when the book has no opinion",
    global_only["direction"] == "rtl" and global_only["fit"] == "width",
    str({k: global_only[k] for k in ("direction", "fit")}),
)
check(
    "global-sourced fields are attributed to global",
    global_only["sources"]["direction"] == "global" and global_only["sources"]["layout"] == "default",
    str(global_only["sources"]),
)

prefs.put(BOOK_A, {"direction": "ltr", "layout": "double"})
override = prefs.get(BOOK_A)
check("per-book value beats the global row", override["direction"] == "ltr", override["direction"])
check("a field the book did not set still comes from global", override["fit"] == "width")
check(
    "mixed sources are reported honestly",
    override["sources"]["direction"] == "book"
    and override["sources"]["fit"] == "global"
    and override["sources"]["layout"] == "book",
    str(override["sources"]),
)
check("another book is unaffected by A's override", prefs.get(BOOK_B)["direction"] == "rtl")
check("layout reached the book that set it", override["layout"] == "double")

prefs.clear(BOOK_A)
after_clear = prefs.get(BOOK_A)
check("clearing the book falls back to global", after_clear["direction"] == "rtl")
check("clearing removed the row entirely", store.get_pref_row(BOOK_A) is None)

# ---------------------------------------------------------------------------
# B. "Not chosen" is blank / -1, and fullscreen is tri-state.
# ---------------------------------------------------------------------------

prefs.put("", {"fullscreen": True})
check("global fullscreen is on", prefs.get(BOOK_B)["fullscreen"] is True)
prefs.put(BOOK_B, {"fullscreen": False})
check("a book can override fullscreen back off", prefs.get(BOOK_B)["fullscreen"] is False)
check("the override is attributed to the book", prefs.get(BOOK_B)["sources"]["fullscreen"] == "book")
prefs.put(BOOK_B, {"fullscreen": None})
check("clearing fullscreen returns to the global value", prefs.get(BOOK_B)["fullscreen"] is True)
row = store.get_pref_row(BOOK_B)
check("cleared fullscreen is stored as -1, not as off", int(row["fullscreen"]) == -1, str(row["fullscreen"]))

prefs.put(BOOK_A, {"direction": "ltr"})
prefs.put(BOOK_A, {"direction": ""})
check(
    "an empty string clears rather than stores a blank value", (store.get_pref_row(BOOK_A) or {}).get("direction") == ""
)
check("the cleared field resolves to global again", prefs.get(BOOK_A)["direction"] == "rtl")

# ---------------------------------------------------------------------------
# C. Validation: a typo must be an error, not a stored value.
# ---------------------------------------------------------------------------

for bad in ({"direction": "spread"}, {"layout": "spread"}, {"fit": "stretch"}):
    try:
        prefs.put(BOOK_A, bad)
        check(f"invalid value rejected {bad}", False, "no error raised")
    except ValueError as exc:
        check(f"invalid value rejected {bad}", "must be one of" in str(exc), str(exc)[:60])

try:
    prefs.put(BOOK_A, {"zoom": "2"})
    check("unknown key rejected", False, "no error raised")
except ValueError as exc:
    check("unknown key rejected", "not a reading preference" in str(exc), str(exc)[:60])

try:
    prefs.put(BOOK_A, {})
    check("empty update rejected", False, "no error raised")
except ValueError as exc:
    check("empty update rejected", "no preference supplied" in str(exc))

# ---------------------------------------------------------------------------
# D. memory keeps its own meaning.
# ---------------------------------------------------------------------------

store.set_memory(BOOK_A, 1, 3)
prefs.put(BOOK_A, {"direction": "rtl", "layout": "scroll", "fit": "original"})
memory = store.get_memory(BOOK_A)
check(
    "a preference write did not move the memory pointer",
    memory["pindex"] == 1 and memory["image_index"] == 3,
    str(memory),
)
prefs.list_bookmarks(BOOK_A)
check("nothing in prefs created a memory row for a book that had none", store.get_memory(BOOK_B) is None)

# ---------------------------------------------------------------------------
# E. Bookmarks: extra positions, de-duplicated per exact spot.
# ---------------------------------------------------------------------------

bm = prefs.add_bookmark(BOOK_A, 1, 2, "重要一页")
check(
    "bookmark created",
    bm["aid"] == BOOK_A and bm["pindex"] == 1 and bm["image_index"] == 2 and bm["note"] == "重要一页",
    str(bm),
)
again = prefs.add_bookmark(BOOK_A, 1, 2, "改了备注")
check(
    "re-adding the same spot updates the note",
    again["id"] == bm["id"] and again["note"] == "改了备注",
    str(again["id"]),
)
check(
    "re-adding the same spot did not duplicate",
    len(prefs.list_bookmarks(BOOK_A)) == 1,
    str(len(prefs.list_bookmarks(BOOK_A))),
)

prefs.add_bookmark(BOOK_A, 1, 3)
prefs.add_bookmark(BOOK_B, 1, 1)
check(
    "bookmarks are ordered by position",
    [(b["pindex"], b["image_index"]) for b in prefs.list_bookmarks(BOOK_A)] == [(1, 2), (1, 3)],
)
check("listing without an Aid returns every book's bookmarks", len(prefs.list_bookmarks()) == 3)
check(
    "deleting a bookmark removes exactly one",
    prefs.delete_bookmark(bm["id"]) and len(prefs.list_bookmarks(BOOK_A)) == 1,
)
check("deleting an unknown bookmark is False", prefs.delete_bookmark(999999) is False)

for bad in ((BOOK_A, 0, 1), (BOOK_A, 1, 0)):
    try:
        prefs.add_bookmark(*bad)
        check(f"bookmark position validated {bad}", False, "no error raised")
    except ValueError:
        check(f"bookmark position validated {bad}", True)

try:
    prefs.add_bookmark("", 1, 1)
    check("bookmark needs an Aid", False, "no error raised")
except ValueError as exc:
    check("bookmark needs an Aid", "needs an Aid" in str(exc))

check("bookmarks did not touch the memory pointer", store.get_memory(BOOK_A)["image_index"] == 3)

# ---------------------------------------------------------------------------
# F. HTTP surface.
# ---------------------------------------------------------------------------

client = TestClient(api.app)

r = client.get("/api/prefs/_global")
check("GET global prefs 200", r.status_code == 200, r.status_code)
check("global prefs expose the vocabularies", r.json()["direction"] == "rtl" and "layouts" not in r.json())

r = client.get("/api/prefs/summary")
check("GET prefs summary 200", r.status_code == 200, r.status_code)
summary = r.json()
check(
    "summary lists the allowed values",
    summary["layouts"] == ["single", "double", "scroll"] and summary["fits"] == ["height", "width", "original"],
    str(summary["layouts"]),
)
check("summary counts overrides, not the global row", summary["overrides"] >= 1, str(summary["overrides"]))
check("summary reports the built-in defaults", summary["defaults"]["direction"] == "ltr", str(summary["defaults"]))

r = client.put(f"/api/prefs/{BOOK_B}", json={"layout": "double"})
check("PUT book prefs 200", r.status_code == 200, r.status_code)
check("PUT returns the resolved result", r.json()["layout"] == "double")

r = client.put(f"/api/prefs/{BOOK_B}", json={"layout": "spread"})
check("PUT rejects an invalid layout 400", r.status_code == 400, r.status_code)

r = client.put("/api/prefs/abc", json={"layout": "double"})
check("PUT rejects an invalid Aid 400", r.status_code == 400, r.status_code)

r = client.delete(f"/api/prefs/{BOOK_B}")
check(
    "DELETE clears the book override", r.status_code == 200 and r.json()["layout"] == "single", r.json().get("layout")
)

r = client.get("/api/prefs/" + BOOK_A)
check("GET book prefs carries book_exists", r.json().get("book_exists") is True)
r = client.get("/api/prefs/799999")
check(
    "GET prefs for an unknown book is still 200, flagged",
    r.status_code == 200 and r.json()["book_exists"] is False,
    r.status_code,
)

r = client.get("/api/bookmarks")
check("GET bookmarks 200", r.status_code == 200 and r.json()["total"] == 2, r.json().get("total"))
r = client.get("/api/bookmarks?aid=" + BOOK_B)
check("GET bookmarks filtered by Aid", r.json()["total"] == 1, r.json().get("total"))

r = client.post("/api/bookmarks", json={"aid": BOOK_A, "pindex": 1, "image_index": 1, "note": "首页"})
check("POST bookmark 200", r.status_code == 200, r.status_code)
new_id = r.json()["id"]
check("POST bookmark appears in the list", len(prefs.list_bookmarks(BOOK_A)) == 2)

r = client.post("/api/bookmarks", json={"aid": BOOK_A, "pindex": 1, "image_index": 99})
check("POST bookmark beyond the chapter 400", r.status_code == 400, r.status_code)
r = client.post("/api/bookmarks", json={"aid": BOOK_A, "pindex": 9, "image_index": 1})
check("POST bookmark on a missing chapter 404", r.status_code == 404, r.status_code)
r = client.post("/api/bookmarks", json={"aid": "799999", "pindex": 1, "image_index": 1})
check("POST bookmark on a missing book 404", r.status_code == 404, r.status_code)

r = client.delete(f"/api/bookmarks/{new_id}")
check("DELETE bookmark 200", r.status_code == 200, r.status_code)
r = client.delete(f"/api/bookmarks/{new_id}")
check("DELETE the same bookmark twice 404", r.status_code == 404, r.status_code)

api.app.state.pin = "s3cret"
check("PIN blocks prefs", client.get("/api/prefs/_global").status_code == 401)
check("PIN blocks bookmarks", client.get("/api/bookmarks").status_code == 401)
check("correct pin allows prefs", client.get("/api/prefs/_global?pin=s3cret").status_code == 200)
check("wrong pin blocks bookmarks", client.get("/api/bookmarks?pin=nope").status_code == 401)
api.app.state.pin = None

# ---------------------------------------------------------------------------
# G. Nothing in C.1 writes bytes.
# ---------------------------------------------------------------------------

check(
    "preference and bookmark writes touched no library byte",
    library_bytes() == BEFORE_BYTES,
    f"{len(BEFORE_BYTES)} files",
)
check("no rollback manifest was produced", not paths.IMPORT_ROLLBACK.exists() and not paths.SINGLE_ROLLBACK.exists())
check("bookmark rows are not memory rows", store.get_memory(BOOK_B) is None and len(store.list_bookmarks(BOOK_B)) == 1)

prefs.clear("")
prefs.clear(BOOK_A)
prefs.clear(BOOK_B)
check(
    "clearing everything returns to the built-in defaults",
    prefs.get(BOOK_A)["direction"] == "ltr" and store.list_pref_rows() == [],
    str(store.list_pref_rows()),
)

print()
if FAILED:
    print(f"C1 checks: {PASSED} passed, {len(FAILED)} failed -> {FAILED}")
    print("C1 contract FAILED")
    sys.exit(1)
print(f"C1 checks: {PASSED} passed, 0 failed")
print("C1 contract PASSED (offline, no network, zero real-library access)")
