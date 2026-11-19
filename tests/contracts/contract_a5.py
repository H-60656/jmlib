"""Offline A.5 import-apply contract checks (no network, no real archive touched).

Monkeypatches EVERY ``jmlib.paths`` constant onto a scratch tree, then builds a
fake import root with the shapes the real queue has:

  * a nested book (``{Aid}/{Pindex}/*.jpg``),
  * a flat book (images sitting directly in the directory),
  * the 1454103 split (simplified-named dir with only a cover + traditional-named
    dir with the body) which must merge into ONE book,
  * a directory whose Aid is already in the library (must be skipped, never
    overwritten),
  * a no-Aid directory (must be reported as a conflict, not silently imported).

Run:  $env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH='src'; python .tmp/test_import_apply.py
"""

# Moved verbatim from the ``.tmp`` exploration scripts so CI runs it.
# Runs as its own process: every contract redirects ``jmlib.paths`` onto a
# scratch tree, and those redirects must not leak between suites.
from __future__ import annotations

import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jmlib import import_apply, import_scan, indexer, ingest, paths, store  # noqa: E402

BASE = ROOT / ".tmp" / "a5_contracts"
if BASE.exists():
    shutil.rmtree(BASE)
BASE.mkdir(parents=True, exist_ok=True)  # default mode (0o700 is denied by sandbox)

DATA = BASE / "data"
LIB = BASE / "library"
SRC = BASE / "incoming_root"
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
    """Path -> (size, bytes) for every file under root, to prove zero source edits."""
    out: dict[str, tuple[int, bytes]] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = (p.stat().st_size, p.read_bytes())
    return out


# ---------------------------------------------------------------------------
# Fixture: the source queue
# ---------------------------------------------------------------------------

# 1) nested book: {Aid}/{Pindex}/NNN.jpg + a cover file
NESTED_AID = "777001"
img(BOOKS / "nested book one" / NESTED_AID / "1" / "001.jpg", b"n1p1")
img(BOOKS / "nested book one" / NESTED_AID / "1" / "002.jpg", b"n1p2")
img(BOOKS / "nested book one" / NESTED_AID / "2" / "001.jpg", b"n2p1")
img(BOOKS / "nested book one" / f"{NESTED_AID}_cover.jpg", b"cover1")

# 2) flat book: images directly under a directory named with its Aid
FLAT_AID = "777002"
img(BOOKS / f"flat book {FLAT_AID}" / "001.jpg", b"f1")
img(BOOKS / f"flat book {FLAT_AID}" / "002.jpg", b"f2")
img(BOOKS / f"flat book {FLAT_AID}" / "010.jpg", b"f10")

# 3) the 1454103 split: cover-only simplified dir + body-carrying traditional dir
SPLIT_AID = "1454103"
img(BOOKS / "同级生にそして教师に汚されて……" / f"{SPLIT_AID}_cover.jpg", b"splitcover")
img(BOOKS / "同级生にそして教师に污されて……" / SPLIT_AID / "1" / "001.jpg", b"s1p1")
img(BOOKS / "同级生にそして教师に污されて……" / SPLIT_AID / "1" / "002.jpg", b"s1p2")

# 4) already in the library: same Aid tree exists under archive/
EXISTING_AID = "777003"
img(BOOKS / f"already imported {EXISTING_AID}" / EXISTING_AID / "1" / "001.jpg", b"dup")
existing_zip = paths.ARCHIVE / EXISTING_AID / "1.zip"
existing_zip.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(existing_zip, "w") as zf:
    zf.writestr("00001.jpg", b"ORIGINAL-BYTES-MUST-SURVIVE")
existing_bytes = existing_zip.read_bytes()

# 5) no Aid anywhere in the name and no numeric subdirectory
img(BOOKS / "a book with no id at all" / "1" / "001.jpg", b"noid")

SOURCE_BEFORE = snapshot(SRC)
STORE_BEFORE = store.stats()
LIB_BEFORE = snapshot(LIB)

manifest = import_scan.scan(SRC)
by_rel = {e["rel_path"]: e for e in manifest["entries"]}
check("fixture scanned 6 directories", manifest["summary"]["dirs"] == 6, str(manifest["summary"]["dirs"]))

# ---------------------------------------------------------------------------
# build_plan: read-only, merges the split, skips the existing Aid
# ---------------------------------------------------------------------------

plan = import_apply.build_plan(manifest)
items = {i["aid"]: i for i in plan["items"]}
check("build_plan is read-only (no library bytes)", snapshot(LIB) == LIB_BEFORE, str(sorted(snapshot(LIB))))
check("build_plan leaves the source untouched", snapshot(SRC) == SOURCE_BEFORE)
check("build_plan does not write the catalog", store.stats() == STORE_BEFORE)
check("split directories merged into one book", len([i for i in plan["items"] if i["aid"] == SPLIT_AID]) == 1)
check(
    "split book kept both source dirs", len(items[SPLIT_AID]["source_dirs"]) == 2, str(items[SPLIT_AID]["source_dirs"])
)
check(
    "split book took the body-carrying directory as title",
    items[SPLIT_AID]["title"] == "同级生にそして教师に污されて……",
    items[SPLIT_AID]["title"],
)
check("split book has the body chapter", items[SPLIT_AID]["chapter_count"] == 1, str(items[SPLIT_AID]["chapter_count"]))
check(
    "split book has 2 body pages (cover not counted)", items[SPLIT_AID]["images"] == 2, str(items[SPLIT_AID]["images"])
)
check(
    "nested book keeps both chapters",
    items[NESTED_AID]["chapter_count"] == 2,
    str(items[NESTED_AID]["chapters"].keys()),
)
check(
    "nested book counts 3 pages (cover file excluded)",
    items[NESTED_AID]["images"] == 3,
    str(items[NESTED_AID]["images"]),
)
check(
    "flat book becomes one chapter with 3 pages",
    items[FLAT_AID]["chapter_count"] == 1 and items[FLAT_AID]["images"] == 3,
    f"{items[FLAT_AID]['chapter_count']}/{items[FLAT_AID]['images']}",
)
check(
    "existing Aid is marked skip-existing",
    items[EXISTING_AID]["action"] == "skip-existing",
    items[EXISTING_AID]["action"],
)
check(
    "no-Aid directory is a conflict, not an item",
    not any(i["title"] == "a book with no id at all" for i in plan["items"])
    and any("a book with no id at all" in c and "no_aid" in c for c in plan["conflicts"]),
    str(plan["conflicts"]),
)
check(
    "sweep conflict explains how to override it",
    any("select this directory explicitly" in c for c in plan["conflicts"]),
    str(plan["conflicts"]),
)
check("summary counts only the 3 importable books", plan["summary"]["books"] == 3, str(plan["summary"]))
check(
    "summary reports 1 skipped existing",
    plan["summary"]["skipped_existing"] == 1,
    str(plan["summary"]["skipped_existing"]),
)
check("plan is awaiting scoped confirmation", plan["status"] == "awaiting scoped confirmation", plan["status"])
check(
    "plan carries a token naming the batch",
    plan["confirm_token"].startswith("IMPORT 3 books / 4 chapters / 8 images #"),
    plan["confirm_token"],
)
check("plan declares zero deletion", plan["safety"]["deletion"].startswith("none"), "")

# ---------------------------------------------------------------------------
# apply gates
# ---------------------------------------------------------------------------

generic = import_apply.apply(plan, "yes, go ahead")
check(
    "generic agreement refused",
    generic["ok"] is False and "scoped confirmation mismatch" in generic["error"],
    generic.get("error", ""),
)
check("refused plan wrote nothing", snapshot(LIB) == LIB_BEFORE and store.stats() == STORE_BEFORE)

forged = dict(plan)
forged["confirm_token"] = "IMPORT 3 books / 4 chapters / 8 images #deadbeef0000"
res = import_apply.apply(forged, plan["confirm_token"])
check(
    "client-minted token refused", res["ok"] is False and "server-issued manifest" in res["error"], res.get("error", "")
)

tampered = json.loads(json.dumps(plan))
tampered["items"][0]["chapters"]["9"] = ["Z:/nope/9.jpg"]
tampered["items"][0]["images"] = 99
res = import_apply.apply(tampered, plan["confirm_token"])
check("tampered plan refused (token recomputation)", res["ok"] is False, res.get("error", ""))
check("still nothing written after all refusals", snapshot(LIB) == LIB_BEFORE and store.stats() == STORE_BEFORE)

# ---------------------------------------------------------------------------
# apply: real write, source untouched, existing Aid intact
# ---------------------------------------------------------------------------

done = import_apply.apply(plan, plan["confirm_token"])
check("exact token executes", done["ok"] is True and done["status"] == "executed", done.get("message", ""))
check("execution reports 3 books", len(done["books"]) == 3, str(len(done["books"])))
check(
    "execution skipped the existing Aid", any(s["aid"] == EXISTING_AID for s in done["skipped"]), str(done["skipped"])
)
check("existing archive zip bytes untouched", existing_zip.read_bytes() == existing_bytes, "")

nested_zips = sorted((paths.ARCHIVE / NESTED_AID).glob("*.zip"))
check("nested book wrote one zip per chapter", len(nested_zips) == 2, str([p.name for p in nested_zips]))
with zipfile.ZipFile(paths.ARCHIVE / NESTED_AID / "1.zip") as zf:
    names = zf.namelist()
    payload = zf.read(names[0])
check("chapter members are zero-padded sequence", names == ["00001.jpg", "00002.jpg"], str(names))
check("first member bytes preserved exactly", payload == b"JPEG" + b"n1p1" + b"\xff\xd9", "")

flat_zips = sorted((paths.ARCHIVE / FLAT_AID).glob("*.zip"))
check("flat book wrote a single chapter zip", len(flat_zips) == 1, str([p.name for p in flat_zips]))
with zipfile.ZipFile(flat_zips[0]) as zf:
    flat_names = zf.namelist()
check("flat pages ordered by filename stem", flat_names == ["00001.jpg", "00002.jpg", "00003.jpg"], str(flat_names))

split_zips = sorted((paths.ARCHIVE / SPLIT_AID).glob("*.zip"))
check("split book wrote only the body chapter", len(split_zips) == 1, str([p.name for p in split_zips]))
with zipfile.ZipFile(split_zips[0]) as zf:
    split_names = zf.namelist()
    split_first = zf.read(split_names[0])
check(
    "split book page order from the traditional directory",
    split_first == b"JPEG" + b"s1p1" + b"\xff\xd9",
    str(split_names),
)

check(
    "covers were derived for the three new books",
    all((paths.COVERS / aid).is_dir() for aid in (NESTED_AID, FLAT_AID, SPLIT_AID)),
    str(sorted(p.name for p in paths.COVERS.iterdir())),
)
check("catalog registered 3 books", store.stats()["books"] == 3, str(store.stats()["books"]))
check("catalog registered 4 chapters", store.stats()["chapters"] == 4, str(store.stats()["chapters"]))
book = store.get_book(NESTED_AID)
check("book row carries the source title", book and book["title"] == "nested book one", (book or {}).get("title", ""))
check(
    "book cover points at chapter 1",
    book and book["cover_path"] and "/1." in book["cover_path"],
    (book or {}).get("cover_path", ""),
)
check(
    "sidecar metadata written for the batch",
    set(ingest.load_meta()) == {NESTED_AID, FLAT_AID, SPLIT_AID},
    str(sorted(ingest.load_meta())),
)
check("source directories still untouched after execution", snapshot(SRC) == SOURCE_BEFORE)
check("rollback manifest written", import_apply.load_rollback() is not None, "")

rebuilt = indexer.rebuild()
check("index rebuild succeeds", rebuilt == 0, str(rebuilt))
check(
    "rebuilt catalog sees 4 books (3 imported + 1 pre-existing zip)",
    store.stats()["books"] == 4,
    str(store.stats()["books"]),
)
check("rebuilt catalog sees 5 chapters", store.stats()["chapters"] == 5, str(store.stats()["chapters"]))
check(
    "rebuild keeps the imported title",
    (store.get_book(SPLIT_AID) or {}).get("title") == "同级生にそして教师に污されて……",
    str((store.get_book(SPLIT_AID) or {}).get("title")),
)

# A second identical plan now sees everything as already imported.
plan2 = import_apply.build_plan(manifest)
check("replan sees the batch already imported", plan2["summary"]["books"] == 0, str(plan2["summary"]))
check(
    "replan token reflects a zero-book batch",
    plan2["confirm_token"].startswith("IMPORT 0 books"),
    plan2["confirm_token"],
)

# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------

payload = import_apply.load_rollback()
token = import_apply.rollback_token(payload)
check("rollback token is scoped to the recorded run", token.startswith("ROLLBACK 3 books #"), token)
bad = import_apply.rollback("yes")
check(
    "rollback refuses generic agreement",
    bad["ok"] is False and "scoped confirmation mismatch" in bad["error"],
    bad.get("error", ""),
)
check("refused rollback removed nothing", (paths.ARCHIVE / NESTED_AID / "1.zip").is_file(), "")

back = import_apply.rollback(token)
check("exact rollback token executes", back["ok"] is True and back["status"] == "rolled_back", back.get("message", ""))
check(
    "rollback removed the imported archives",
    not (paths.ARCHIVE / NESTED_AID).exists()
    and not (paths.ARCHIVE / FLAT_AID).exists()
    and not (paths.ARCHIVE / SPLIT_AID).exists(),
    "",
)
check("rollback removed the derived covers", not (paths.COVERS / NESTED_AID).exists(), "")
check("rollback kept the pre-existing Aid tree", existing_zip.read_bytes() == existing_bytes, "")
check(
    "rollback cleared the imported rows, leaving only the pre-existing book",
    [b["aid"] for b in store.list_books()] == [EXISTING_AID],
    str([b["aid"] for b in store.list_books()]),
)
check("rollback cleared the sidecar keys", ingest.load_meta() == {}, str(ingest.load_meta()))
check("rollback removed its manifest", import_apply.load_rollback() is None, "")
check("source directories survive rollback", snapshot(SRC) == SOURCE_BEFORE)

again = import_apply.rollback(token)
check(
    "second rollback is refused (nothing to reverse)",
    again["ok"] is False and "no import rollback manifest" in again["error"],
    again.get("error", ""),
)

print()
if fails:
    print(f"A5 checks: {len(fails)} FAILED -> {fails}")
    sys.exit(1)
print("A5 contract PASSED (offline, zero real-archive writes)")
