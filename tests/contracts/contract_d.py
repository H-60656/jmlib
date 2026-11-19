"""Offline contract for the D slice: name→Aid resolution + tag backfill.

No network, no real library: every path is redirected into ``.tmp/d_contracts``
and the upstream calls are injected.  The point of this file is to pin the four
promises the slice makes:

1. **Grouping matches the archive's real shape** — a folder of chapter zips is one
   book, a flat zip in a generation root is its own book, and a generation root is
   never a book.
2. **A filename Aid is free evidence** — that unit is decided without a request.
3. **Nothing is decided on a maybe** — below ``min_score``, below ``min_margin``,
   or with conflicting filename Aids the unit stays ``pending``.
4. **Dry run writes nothing** — and the real run writes decisions, not files.
"""
# Moved verbatim from the ``.tmp`` exploration scripts so CI runs it.
# Runs as its own process: every contract redirects ``jmlib.paths`` onto a
# scratch tree, and those redirects must not leak between suites.

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

BASE = ROOT / ".tmp" / "d_contracts"
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

from jmlib import idres, indexer, ingest, resolve, store  # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED
    if cond:
        PASSED += 1
        print(f"PASS {name}" + (f" {detail}" if detail else ""))
    else:
        FAILED.append(name)
        print(f"FAIL {name} {detail}")


def library_bytes() -> dict[str, int]:
    out: dict[str, int] = {}
    for p in LIB.rglob("*"):
        if p.is_file():
            out[p.relative_to(LIB).as_posix()] = p.stat().st_size
    return out


def make_zip(rel: str, pages: int = 1) -> Path:
    target = LIB / "_unidentified" / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w") as zf:
        for i in range(1, pages + 1):
            zf.writestr(f"{i:05d}.jpg", b"X" * 10)
    return target


# ---------------------------------------------------------------- fixture
FOLDER_ZIPS = [
    "长篇/[漫漫长夜翻译组] [よのき] 鬼畜英雄/001-[漫漫长夜翻译组] [よのき] 鬼畜英雄.zip",
    "长篇/[漫漫长夜翻译组] [よのき] 鬼畜英雄/002-鬼畜英雄 2.zip",
    "长篇/[漫漫长夜翻译组] [よのき] 鬼畜英雄/003-鬼畜英雄 3.zip",
]
FLAT_WITH_AID = "JM(Android)/1096890[CunnyFunky] [AI Generated] [Uncensored].zip"
FLAT_PLAIN = "JM(Android)/某个没有车号前缀的标题.zip"
FLAT_CONFLICT = "JM(Android)/1234500 1234600两个车号的标题.zip"

ROWS = []
for rel in FOLDER_ZIPS + [FLAT_WITH_AID, FLAT_PLAIN, FLAT_CONFLICT]:
    make_zip(rel)
    ROWS.append(
        {
            "rel_path": "library/_unidentified/" + rel,
            "reason": "no aid",
            "generation": rel.split("/")[0],
            "size": 10,
        }
    )
for row in ROWS:
    store.insert_unresolved(row)

UNITS = resolve.book_units()
BY_KEY = {u["key"]: u for u in UNITS}

FOLDER_KEY = "library/_unidentified/长篇/[漫漫长夜翻译组] [よのき] 鬼畜英雄"
FLAT_AID_KEY = "library/_unidentified/" + FLAT_WITH_AID
FLAT_PLAIN_KEY = "library/_unidentified/" + FLAT_PLAIN
FLAT_CONFLICT_KEY = "library/_unidentified/" + FLAT_CONFLICT


# ---------------------------------------------------------------- A. grouping
check("one folder of chapter zips is one book", FOLDER_KEY in BY_KEY, str(list(BY_KEY)))
check("folder unit carries all its zips", BY_KEY[FOLDER_KEY]["zip_count"] == 3, str(BY_KEY[FOLDER_KEY]["zip_count"]))
check("folder unit is kind=folder", BY_KEY[FOLDER_KEY]["kind"] == "folder")
check(
    "folder unit query is the folder's cleaned title",
    BY_KEY[FOLDER_KEY]["query"] == "鬼畜英雄",
    BY_KEY[FOLDER_KEY]["query"],
)
check("a flat zip in a generation root is its own book", FLAT_AID_KEY in BY_KEY)
check("flat unit is kind=flat", BY_KEY[FLAT_AID_KEY]["kind"] == "flat")
check("flat unit names itself after the zip", BY_KEY[FLAT_AID_KEY]["zip_count"] == 1)
check(
    "a generation root never becomes a book",
    "library/_unidentified/JM(Android)" not in BY_KEY,
    str([k for k in BY_KEY if k.endswith("JM(Android)")]),
)
check(
    "the folder's filenames carry no leading Aid",
    BY_KEY[FOLDER_KEY]["aid_hint"] == "",
    str(BY_KEY[FOLDER_KEY]["leading_aids"]),
)
check(
    "a leading Aid is read off the filename",
    BY_KEY[FLAT_AID_KEY]["aid_hint"] == "1096890",
    BY_KEY[FLAT_AID_KEY]["aid_hint"],
)
check(
    "two conflicting leading Aids are flagged, not picked",
    BY_KEY[FLAT_CONFLICT_KEY]["conflicting_aids"] is True,
    str(BY_KEY[FLAT_CONFLICT_KEY]["leading_aids"]),
)
check("conflicting unit offers no hint", BY_KEY[FLAT_CONFLICT_KEY]["aid_hint"] == "")

SUM = resolve.summary(UNITS)
check("summary counts units", SUM["units"] == 4, str(SUM["units"]))
check("summary counts zips", SUM["zips"] == 6, str(SUM["zips"]))
check(
    "summary separates folder and flat units",
    (SUM["folders"], SUM["flat"]) == (1, 3),
    str((SUM["folders"], SUM["flat"])),
)
check("summary counts units with a free Aid", SUM["with_aid_hint"] == 1)
check("summary counts generations", SUM["byGeneration"] == {"长篇": 1, "JM(Android)": 3}, str(SUM["byGeneration"]))

check("leading_aid ignores a non-Aid prefix", resolve.leading_aid("abc.zip") == "")
check("leading_aid needs 4-7 digits", resolve.leading_aid("123x.zip") == "")
check("generation_of reads the shelf", resolve.generation_of(FLAT_AID_KEY) == "JM(Android)")

# A bare number is not a title: searching it returns that album at a score that
# means nothing, so it must never become a query.
check("a numeric-only name is not a query", resolve._query_for("255") == "", resolve._query_for("255"))
check(
    "a numeric-only name with punctuation is not a query",
    resolve._query_for("255 (COMIC LO)") == "",
    resolve._query_for("255 (COMIC LO)"),
)
check("a real title still becomes a query", resolve._query_for("鬼畜英雄") == "鬼畜英雄")
check(
    "a numeric name is reported as needing a human, not looked up",
    resolve.book_units(
        [{"rel_path": "library/_unidentified/JM(1)/255.zip", "reason": "", "generation": "JM(1)", "size": 1}]
    )[0]["query"]
    == "",
)


# ---------------------------------------------------------------- B. dry run
def fake_search(title, **kwargs):
    """Answer by title so each unit exercises one branch."""
    table = {
        "鬼畜英雄": {
            "ok": True,
            "failure": None,
            "reason": "ok",
            "candidates": [
                {"id": "221462", "name": "鬼畜英雄", "score": 0.99},
                {"id": "648881", "name": "鬼畜英雄 2", "score": 0.40},
            ],
        },
        "某个没有车号前缀的标题": {
            "ok": True,
            "failure": None,
            "reason": "ok",
            "candidates": [
                {"id": "118983", "name": "某个没有车号前缀的标题", "score": 0.62},
                {"id": "118984", "name": "别的书", "score": 0.10},
            ],
        },
    }
    return table.get(title, {"ok": False, "failure": "empty", "reason": "上游没有返回任何结果", "candidates": []})


LIBS_BEFORE = library_bytes()
DRY = resolve.resolve(units=UNITS, search=fake_search, dry_run=True)

check("dry run reports a schema", DRY["schemaVersion"] == resolve.SCHEMA)
check("dry run is flagged", DRY["dryRun"] is True)
check(
    "dry run decided the free-Aid unit without a request",
    next(i for i in DRY["items"] if i["key"] == FLAT_AID_KEY)["status"] == "from_filename",
)
check(
    "dry run accepted the unambiguous folder",
    next(i for i in DRY["items"] if i["key"] == FOLDER_KEY)["status"] == "chosen",
)
check(
    "dry run kept the weak match pending",
    next(i for i in DRY["items"] if i["key"] == FLAT_PLAIN_KEY)["status"] == "pending",
)
check(
    "dry run kept the conflicting unit pending",
    next(i for i in DRY["items"] if i["key"] == FLAT_CONFLICT_KEY)["status"] == "pending",
)
check("dry run tallies decided units", DRY["summary"]["decided"] == 2, str(DRY["summary"]))
check("dry run wrote no decision row", idres.rows() == [], str(idres.summary()))
check("dry run touched no library byte", library_bytes() == LIBS_BEFORE)

FOLDER_ITEM = next(i for i in DRY["items"] if i["key"] == FOLDER_KEY)
check("accepted item names the winning Aid", FOLDER_ITEM["chosen"] == "221462")
check("accepted item records the margin", FOLDER_ITEM["margin"] > 0.1, str(FOLDER_ITEM["margin"]))
check("accepted item keeps candidates for review", len(FOLDER_ITEM["candidates"]) == 2)
check(
    "accepted item explains itself", "车号" in FOLDER_ITEM["note"] or "候选" in FOLDER_ITEM["note"], FOLDER_ITEM["note"]
)


# ---------------------------------------------------------------- C. real run
calls: list[str] = []


def counting_search(title, **kwargs):
    calls.append(title)
    return fake_search(title, **kwargs)


REAL = resolve.resolve(units=UNITS, search=counting_search, dry_run=False)

check("real run decided two units", REAL["summary"]["decided"] == 2, str(REAL["summary"]))
check(
    "real run skipped the network for the free-Aid unit",
    FLAT_AID_KEY not in str(calls) and "1096890" not in " ".join(calls),
    str(calls),
)
check("real run queried only the units that needed it", len(calls) == 3, str(calls))

ROWS_AFTER = idres.rows()
BY_REL = {r["rel_path"]: r for r in ROWS_AFTER}
check("real run wrote one row per unit", len(ROWS_AFTER) == 4, str(len(ROWS_AFTER)))
check("accepted unit is stored as chosen", BY_REL[FOLDER_KEY]["state"] == "chosen")
check("accepted unit stored the Aid", BY_REL[FOLDER_KEY]["chosen_aid"] == "221462")
check("accepted unit kept its candidates", len(BY_REL[FOLDER_KEY]["candidates"]) == 2)
check("free-Aid unit is stored as chosen", BY_REL[FLAT_AID_KEY]["state"] == "chosen")
check("weak unit stays pending", BY_REL[FLAT_PLAIN_KEY]["state"] == "pending")
check("pending unit stored no Aid", not BY_REL[FLAT_PLAIN_KEY]["chosen_aid"], str(BY_REL[FLAT_PLAIN_KEY]["chosen_aid"]))
check("conflicting unit stays pending", BY_REL[FLAT_CONFLICT_KEY]["state"] == "pending")
check("decided map now answers the accepted units", idres.decided().get(FOLDER_KEY) == "221462")
check("real run touched no library byte", library_bytes() == LIBS_BEFORE)

# Second pass: decided units are not re-queried.
calls.clear()
AGAIN = resolve.resolve(units=UNITS, search=counting_search, dry_run=False)
check(
    "a second run skips already-decided units",
    next(i for i in AGAIN["items"] if i["key"] == FOLDER_KEY)["status"] == "skipped",
)
check(
    "a second run reuses the recorded Aid",
    next(i for i in AGAIN["items"] if i["key"] == FOLDER_KEY)["chosen"] == "221462",
)
check("a second run only re-queries the still-pending units", len(calls) == 2, str(calls))
check("a second run added no duplicate rows", len(idres.rows()) == 4, str(len(idres.rows())))

# force=True is honoured.
calls.clear()
resolve.resolve(units=UNITS, search=counting_search, dry_run=False, force=True)
check("force re-queries even decided units", len(calls) > 2, str(len(calls)))


# ---------------------------------------------------------------- D. gates
STRICT = resolve.resolve(units=UNITS, search=fake_search, dry_run=True, min_score=0.995, skip_decided=False)
check(
    "a stricter score gate refuses the folder",
    next(i for i in STRICT["items"] if i["key"] == FOLDER_KEY)["status"] == "pending",
)

MARGIN = resolve.resolve(units=UNITS, search=fake_search, dry_run=True, min_margin=0.7, skip_decided=False)
check(
    "a stricter margin gate refuses the folder",
    next(i for i in MARGIN["items"] if i["key"] == FOLDER_KEY)["status"] == "pending",
)

NO_TOP = resolve.resolve(
    units=UNITS,
    search=lambda t, **k: {"ok": False, "failure": "error", "reason": "boom", "candidates": []},
    dry_run=True,
    skip_decided=False,
)
check(
    "a failed lookup stays pending", next(i for i in NO_TOP["items"] if i["key"] == FOLDER_KEY)["status"] == "pending"
)
check(
    "a failed lookup records the failure",
    next(i for i in NO_TOP["items"] if i["key"] == FOLDER_KEY)["failure"] == "error",
)

LIMITED = resolve.resolve(units=UNITS, search=fake_search, dry_run=True, limit=2)
check("limit caps how many units are considered", LIMITED["considered"] == 2, str(LIMITED["considered"]))
check("limit caps the reported items", len(LIMITED["items"]) == 2)


# ---------------------------------------------------------------- E. tag fill
BOOK_A, BOOK_B = "880001", "880002"
for aid, title in ((BOOK_A, "已有书甲"), (BOOK_B, "已有书乙")):
    # A real archive zip, because ``indexer.rebuild()`` derives the catalog from
    # what is on disk: a row without bytes is correctly dropped by a rebuild.
    chapter = LIB / "archive" / aid / "1.zip"
    chapter.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(chapter, "w") as zf:
        zf.writestr("00001.jpg", b"Y" * 10)
    store.upsert_book_with_chapters(
        {
            "aid": aid,
            "title": title,
            "author": "",
            "tags": "[]",
            "generation": "test",
            "cover_path": "",
            "created_at": "2026-01-01T00:00:00+00:00",
        },
        [{"aid": aid, "pindex": 1, "zip_path": f"library/archive/{aid}/1.zip", "page_count": 1, "cover_path": ""}],
    )
store.set_book_tags(BOOK_B, ["用户自己的标签"], overwrite=True, author="原有作者")

check(
    "only the book without tags is on the backfill worklist",
    [b["aid"] for b in store.books_missing_tags()] == [BOOK_A],
    str([b["aid"] for b in store.books_missing_tags()]),
)
check("a book with tags is not on the worklist", BOOK_B not in [b["aid"] for b in store.books_missing_tags()])


def fake_album(aid):
    if aid == BOOK_A:
        return {
            "ok": True,
            "aid": aid,
            "title": "上游标题甲",
            "tags": ["萝莉", "中文"],
            "authors": ["作者甲"],
            "error": "",
        }
    return {"ok": False, "aid": aid, "title": "", "tags": [], "authors": [], "error": "boom"}


# The baseline for "metadata touched no byte" is taken *here*: the fixture books
# above legitimately created their archive zips, and only what tag fill does
# afterwards is the thing under test.
TAG_LIBS_BEFORE = library_bytes()

TAGS_DRY = resolve.fill_tags(album=fake_album, dry_run=True)
check(
    "tag dry run requested the untagged book", [i["aid"] for i in TAGS_DRY["items"]] == [BOOK_A], str(TAGS_DRY["items"])
)
check("tag dry run wrote nothing", store.get_book(BOOK_A)["tags"] == [])
check("tag dry run reports what it would write", TAGS_DRY["summary"]["withTags"] == 1, str(TAGS_DRY["summary"]))

TAGS_REAL = resolve.fill_tags(album=fake_album, dry_run=False)
check("tag real run wrote the book", TAGS_REAL["summary"]["written"] == 1, str(TAGS_REAL["summary"]))
check("tags are stored", store.get_book(BOOK_A)["tags"] == ["萝莉", "中文"], str(store.get_book(BOOK_A)["tags"]))
check("author is stored", store.get_book(BOOK_A)["author"] == "作者甲")
check("the fetched book leaves the worklist", BOOK_A not in [b["aid"] for b in store.books_missing_tags()])

# Additive: a second run must not erase what the user already said.
store.set_book_tags(BOOK_B, ["用户自己的标签"], overwrite=True, author="原有作者")
resolve.fill_tags(
    album=lambda aid: {"ok": True, "aid": aid, "title": "", "tags": ["新标签"], "authors": ["上游作者"], "error": ""},
    dry_run=False,
    aids=[BOOK_B],
)
AFTER_B = store.get_book(BOOK_B)
check("backfill appends rather than replaces", AFTER_B["tags"] == ["用户自己的标签", "新标签"], str(AFTER_B["tags"]))
check("backfill keeps an existing author", AFTER_B["author"] == "原有作者", AFTER_B["author"])

FAILED_TAGS = resolve.fill_tags(album=fake_album, dry_run=False, aids=["999999"])
check(
    "a failed album lookup is reported, not raised",
    FAILED_TAGS["summary"]["failed"] == 1 and FAILED_TAGS["items"][0]["written"] is False,
    str(FAILED_TAGS["summary"]),
)
check("tag fill touched no library byte", library_bytes() == TAG_LIBS_BEFORE)

# The sidecar is what `jmlib index` rebuilds from, so a backfill that only wrote
# the catalog row would look right until the next rebuild dropped every tag.
sidecar = ingest.load_meta()
check(
    "backfilled tags are recorded in the sidecar",
    sidecar.get(BOOK_A, {}).get("tags") == ["萝莉", "中文"],
    str(sidecar.get(BOOK_A)),
)
check(
    "backfilled author is recorded in the sidecar",
    sidecar.get(BOOK_A, {}).get("author") == "作者甲",
    str(sidecar.get(BOOK_A)),
)
indexer.rebuild()
check(
    "a rebuild keeps the tags learned from upstream",
    store.get_book(BOOK_A)["tags"] == ["萝莉", "中文"],
    str(store.get_book(BOOK_A)["tags"]),
)
check(
    "a rebuild keeps the author learned from upstream",
    store.get_book(BOOK_A)["author"] == "作者甲",
    str(store.get_book(BOOK_A)["author"]),
)


# ---------------------------------------------------------------- F. promises
doc = (ROOT / "src" / "jmlib" / "resolve.py").read_text(encoding="utf-8")
check("module documents that it never moves bytes", "never moves" in doc)
check("module documents the serial throttle", "concurrently" in doc or "one at a time" in doc)
check(
    "module exposes no delete/rename helper",
    not any(hasattr(resolve, n) for n in ("delete", "move", "rename", "promote")),
)
check("resolve never writes the catalog", not hasattr(resolve, "upsert_book_with_chapters"))

print()
if FAILED:
    print(f"D checks: {PASSED} passed, {len(FAILED)} failed -> {FAILED}")
    print("D resolve contract FAILED")
    raise SystemExit(1)
print(f"D checks: {PASSED} passed, 0 failed")
print("D resolve contract PASSED (offline, no network, zero real-library access)")
print("D_RC 0")
