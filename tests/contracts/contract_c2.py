"""C.2 reader contract: the server side of the reader, checked without a browser.

The visible behaviour (RTL keyboard/gesture agreement, double-page order,
progress drag, fullscreen refusal, retry) was verified by hand in a real
browser; that evidence lives in the work record.  What a browser cannot prove
cheaply is the wiring: that the page loads the script from the mounted assets
directory, that every control the script addresses actually exists in the
markup, and that a stored preference is what the reader will read back.

This file asserts exactly those, offline, against a scratch library.
"""
# Moved verbatim from the ``.tmp`` exploration scripts so CI runs it.
# Runs as its own process: every contract redirects ``jmlib.paths`` onto a
# scratch tree, and those redirects must not leak between suites.

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))

BASE = Path(__file__).resolve().parents[2] / ".tmp" / "c2_contracts"

if BASE.exists():
    shutil.rmtree(BASE, ignore_errors=True)

from jmlib import paths, store  # noqa: E402

paths.DATA = BASE / "data"
paths.LIBRARY = BASE / "library"
paths.ARCHIVE = paths.LIBRARY / "archive"
paths.UNIDENTIFIED = paths.LIBRARY / "_unidentified"
paths.COVERS = paths.LIBRARY / "covers"
paths.INCOMING = paths.LIBRARY / "_incoming"
paths.STAGING = paths.DATA / "staging"
paths.EXPORTS = BASE / "exports"
paths.DB_PATH = paths.DATA / "jmlib.db"
paths.MIGRATION_MANIFEST = paths.DATA / "migration_manifest.json"
paths.ROLLBACK_MANIFEST = paths.DATA / "rollback_manifest.json"
paths.SIGNATURES = paths.LIBRARY / "_zip_signatures.json"
paths.BLACKLIST = paths.LIBRARY / "黑名单.csv"
paths.INGEST_METADATA = paths.DATA / "ingest_metadata.json"
paths.LOOSE_INGEST_MANIFEST = paths.DATA / "loose_ingest_manifest.json"
paths.GOVERNANCE_MANIFEST = paths.DATA / "governance_manifest.json"
paths.IMPORT_MANIFEST = paths.DATA / "import_manifest.json"
paths.IMPORT_ROLLBACK = paths.DATA / "import_rollback.json"
paths.SINGLE_ROLLBACK = paths.DATA / "single_rollback.json"
paths.UPSTREAM_CACHE = paths.DATA / "upstream_cache.json"
# WEB stays pointed at the real read-only directory: this contract is about the
# pages actually shipped, not about a copy of them.

paths.ensure_dirs()

from fastapi.testclient import TestClient  # noqa: E402

from jmlib import api, prefs  # noqa: E402

PASSED, FAILED = [], []


def check(name: str, cond: bool, extra: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"PASS {name} {extra}".rstrip())
    else:
        FAILED.append(name)
        print(f"FAIL {name} {extra}".rstrip())


client = TestClient(api.app)

# ---------------------------------------------------------------------------
# A. The page is a shell; behaviour lives in the mounted script
# ---------------------------------------------------------------------------

page = client.get("/read")
html = page.text
check("GET /read is html 200", page.status_code == 200 and "text/html" in page.headers["content-type"])
check("page loads the reader script from the assets mount", 'src="/assets/read.js"' in html)
check("page carries no inline behaviour script", "<script>" not in html)

script = client.get("/assets/read.js")
check("GET /assets/read.js is served 200", script.status_code == 200, str(script.status_code))
js = script.text
check("script is not the html page fallback", "<html" not in js.lower())
check("script keeps the streaming page endpoint", "/api/page" in js)
check("script keeps the memory endpoint", "/api/memory/" in js)
check("script reads and writes preferences", "/api/prefs/" in js and "direction" in js and "layout" in js)
check("script prefetches at most two pages", "PREFETCH_LIMIT = 2" in js)
check("prefetch uses the same url as the reader", '"/api/page?aid="' in js)
check("no third-party script in the page", "http://" not in html and "https://" not in html)

# Every id the script touches must exist in the markup, or the reader dies at
# start with a null dereference the user sees as an empty screen.  ``retry`` is
# the one exception: it is written into the placeholder at failure time, so it
# cannot be in the static page.
DYNAMIC_IDS = {"retry"}
wanted = set(re.findall(r'\$\("([a-z0-9-]+)"\)', js)) - DYNAMIC_IDS
missing = sorted(i for i in wanted if f'id="{i}"' not in html)
check("every static control the script looks up exists in the page", not missing, str(missing))
check("retry buttons are created at failure time, not in the page", 'id="retry"' in js and 'id="retry"' not in html)
check("script looks up a realistic number of controls", len(wanted) >= 12, str(len(wanted)))

for control in ("dir", "layout", "fit", "fs", "progress", "prev-ch", "next-ch", "bm-add", "bm-list", "jump", "go"):
    check(f"page exposes #{control}", f'id="{control}"' in html)

check("page declares two page slots for the double layout", html.count('class="page"') == 2)
check("page has no external font or icon dependency", "fonts." not in html and "cdn" not in html.lower())

# ---------------------------------------------------------------------------
# B. A stored preference is what the reader reads back
# ---------------------------------------------------------------------------

book = {
    "aid": "760001",
    "title": "C2 book",
    "author": "",
    "tags": "[]",
    "generation": "test",
    "cover_path": "",
    "created_at": "2026-01-01T00:00:00+00:00",
}
store.upsert_book_with_chapters(
    book,
    [{"aid": "760001", "pindex": 1, "zip_path": "library/archive/760001/1.zip", "page_count": 5, "cover_path": ""}],
)

fresh = client.get("/api/prefs/760001").json()
check(
    "a book with no opinion resolves to the built-in defaults",
    (fresh["direction"], fresh["layout"], fresh["fit"], fresh["fullscreen"]) == ("ltr", "single", "height", False),
    str(fresh),
)
check("the reader can tell whether the book exists", fresh["book_exists"] is True)

put = client.put("/api/prefs/760001", json={"direction": "rtl", "layout": "double", "fit": "width"})
check("preferences are written 200", put.status_code == 200, str(put.status_code))
back = client.get("/api/prefs/760001").json()
check(
    "the reader reads back what it stored",
    (back["direction"], back["layout"], back["fit"]) == ("rtl", "double", "width"),
    str(back),
)
check(
    "stored values are attributed to the book, not the defaults",
    back["sources"]["direction"] == "book" and back["sources"]["fit"] == "book",
    str(back["sources"]),
)

client.put("/api/prefs/_global", json={"fit": "original"})
global_view = client.get("/api/prefs/760001").json()
check("a per-book value still wins over the global row", global_view["fit"] == "width", global_view["fit"])
client.delete("/api/prefs/_global")
client.delete("/api/prefs/760001")
check("clearing returns to the defaults", client.get("/api/prefs/760001").json()["layout"] == "single")

# ---------------------------------------------------------------------------
# C. Reading a page never writes a byte
# ---------------------------------------------------------------------------


def library_snapshot() -> dict:
    out = {}
    for path in paths.LIBRARY.rglob("*"):
        if path.is_file():
            out[path.relative_to(paths.LIBRARY).as_posix()] = path.stat().st_size
    return out


before = library_snapshot()
client.get("/api/prefs/760001")
client.put("/api/prefs/760001", json={"direction": "rtl"})
client.post("/api/bookmarks", json={"aid": "760001", "pindex": 1, "image_index": 3})
after = library_snapshot()
check("preferences and bookmarks wrote nothing under library/", before == after, f"{len(before)} files")

check("no rollback manifest was produced", not paths.IMPORT_ROLLBACK.exists() and not paths.SINGLE_ROLLBACK.exists())
check(
    "layout values accepted are the three the reader can render",
    set(prefs.LAYOUTS) == {"single", "double", "scroll"},
    str(prefs.LAYOUTS),
)

print()
print(f"C2 checks: {len(PASSED)} passed, {len(FAILED)} failed")
if FAILED:
    print("FAILED:", FAILED)
    sys.exit(1)
print("C2 reader contract PASSED (offline, no browser, zero real-library access)")
