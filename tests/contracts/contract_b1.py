"""Offline B.1/B.2 contract: hand-add one directory + list the drop folder.

What is being proven here:

  * a hand-add preview is read-only and its token binds the *resolved absolute
    path*, so a plan cannot be replayed against a different folder;
  * the write path recomputes the item from the directory, so a client editing
    the plan's chapter list cannot point the writer at other files;
  * the source directory is never modified, and an Aid that already exists is
    skipped rather than overwritten;
  * the hand-add rollback ledger is separate from the batch import's, so adding
    one book by hand cannot destroy the batch's rollback record;
  * ``/api/import/incoming`` lists the drop folder read-only and says what to do
    when it is empty.

Run:  $env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH='src'; python .tmp/test_import_b1.py
"""

# Moved verbatim from the ``.tmp`` exploration scripts so CI runs it.
# Runs as its own process: every contract redirects ``jmlib.paths`` onto a
# scratch tree, and those redirects must not leak between suites.
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jmlib import import_apply, paths, store  # noqa: E402

BASE = ROOT / ".tmp" / "b1_contracts"
if BASE.exists():
    shutil.rmtree(BASE)
BASE.mkdir(parents=True, exist_ok=True)

DATA = BASE / "data"
LIB = BASE / "library"
SRC = BASE / "roots"
ALLOWED = SRC / "allowed"
OUTSIDE = SRC / "outside"

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
paths.SINGLE_ROLLBACK = DATA / "single_rollback.json"
paths.UPSTREAM_CACHE = DATA / "upstream_cache.json"
paths.ensure_dirs()

os.environ["JMLIB_IMPORT_ROOTS"] = str(ALLOWED)

fails: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("PASS " if cond else "FAIL ") + name + (f" {extra}" if extra else ""))
    if not cond:
        fails.append(name)


def img(path: Path, marker: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"JPEG" + marker + b"\xff\xd9")


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# a nested book inside the allowed root
NESTED = ALLOWED / "my nested book"
NESTED_AID = "888001"
img(NESTED / NESTED_AID / "1" / "001.jpg", b"n1")
img(NESTED / NESTED_AID / "1" / "002.jpg", b"n2")
img(NESTED / NESTED_AID / "2" / "001.jpg", b"n3")

# a flat book: images directly in the folder
FLAT = ALLOWED / "my flat book"
FLAT_AID = "888002"
img(FLAT / "001.jpg", b"f1")
img(FLAT / "002.jpg", b"f2")

# a folder with no body images at all
EMPTY = ALLOWED / "empty folder"
EMPTY.mkdir(parents=True, exist_ok=True)

# a book outside the allowed root: must be refused
OUT_BOOK = OUTSIDE / "outside book"
OUT_AID = "888003"
img(OUT_BOOK / OUT_AID / "1" / "001.jpg", b"o1")

ALLOWED_BEFORE = snapshot(ALLOWED)
OUTSIDE_BEFORE = snapshot(OUTSIDE)


# ---------------------------------------------------------------------------
# 1. preview is read-only and binds the resolved path
# ---------------------------------------------------------------------------

plan = import_apply.plan_single(NESTED, NESTED_AID)
check(
    "preview succeeds", plan["ok"] is True and plan["status"] == "awaiting scoped confirmation", str(plan.get("status"))
)
check(
    "preview counts chapters and pages",
    plan["item"]["chapter_count"] == 2 and plan["item"]["images"] == 3,
    str({k: plan["item"][k] for k in ("chapter_count", "images")}),
)
check(
    "preview token names the Aid and the batch size",
    plan["confirm_token"].startswith(f"ADD {NESTED_AID} / 2 chapters / 3 images #"),
    plan["confirm_token"],
)
check(
    "preview resolves the absolute path",
    plan["item"]["local_path"] == str(NESTED.resolve()),
    plan["item"]["local_path"],
)
check("preview wrote no library byte", snapshot(LIB) == {})
check("preview left the source untouched", snapshot(ALLOWED) == ALLOWED_BEFORE)

flat_plan = import_apply.plan_single(FLAT, FLAT_AID)
check(
    "a flat folder becomes one chapter",
    flat_plan["item"]["chapter_count"] == 1 and flat_plan["item"]["images"] == 2,
    str({k: flat_plan["item"][k] for k in ("chapter_count", "images")}),
)
check(
    "a flat folder keeps filename order",
    [Path(p).name for p in flat_plan["item"]["chapters"]["1"]] == ["001.jpg", "002.jpg"],
    str([Path(p).name for p in flat_plan["item"]["chapters"]["1"]]),
)

empty_plan = import_apply.plan_single(EMPTY, "888009")
check(
    "a folder with no images is refused, not half-imported",
    empty_plan["ok"] is False and "no body images" in empty_plan["error"],
    empty_plan.get("error", ""),
)
short_aid = import_apply.plan_single(NESTED, "12")
check("a short Aid is a real Aid", short_aid["ok"] is True, short_aid.get("error", ""))
bad_aid = import_apply.plan_single(NESTED, "12345678")
check("a non-Aid is refused", bad_aid["ok"] is False and "1-7 digits" in bad_aid["error"], bad_aid.get("error", ""))


# ---------------------------------------------------------------------------
# 2. refusals write nothing
# ---------------------------------------------------------------------------

refused = import_apply.apply_single(plan, "yes, go ahead")
check(
    "generic agreement is refused",
    refused["ok"] is False and "scoped confirmation mismatch" in refused["error"],
    refused.get("error", ""),
)

forged = json.loads(json.dumps(plan))
# Replace the *plan's* token but keep the wrong confirm value: the recomputed
# token is what gets compared, so this must fail as "not a server-issued manifest".
forged["confirm_token"] = "ADD 888001 / 2 chapters / 3 images #deadbeef"
forged_result = import_apply.apply_single(forged, plan["confirm_token"])
check(
    "a client-minted token is refused",
    forged_result["ok"] is False and "not a server-issued manifest" in forged_result["error"],
    forged_result.get("error", ""),
)

# Point the plan at another directory while keeping the original token: the token
# is recomputed from the path, so this must fail.
swapped = json.loads(json.dumps(plan))
swapped["item"]["local_path"] = str(OUT_BOOK.resolve())
swapped_result = import_apply.apply_single(swapped, plan["confirm_token"])
check(
    "a plan cannot be replayed against another directory",
    swapped_result["ok"] is False,
    str(swapped_result.get("status")),
)

check("no refusal wrote a library byte", snapshot(LIB) == {})
check("no refusal touched a source", snapshot(ALLOWED) == ALLOWED_BEFORE and snapshot(OUTSIDE) == OUTSIDE_BEFORE)


# ---------------------------------------------------------------------------
# 3. exact token executes, source untouched
# ---------------------------------------------------------------------------

done = import_apply.apply_single(plan, plan["confirm_token"])
check("exact token executes", done["ok"] is True and done["status"] == "executed", done.get("message", ""))
check(
    "one zip per chapter",
    (paths.ARCHIVE / NESTED_AID / "1.zip").is_file() and (paths.ARCHIVE / NESTED_AID / "2.zip").is_file(),
    "",
)
check(
    "members are zero-padded",
    (lambda z: z == ["00001.jpg", "00002.jpg"])(
        __import__("zipfile").ZipFile(paths.ARCHIVE / NESTED_AID / "1.zip").namelist()
    ),
    str(__import__("zipfile").ZipFile(paths.ARCHIVE / NESTED_AID / "1.zip").namelist()),
)
check("cover derived from the first image", (paths.COVERS / NESTED_AID / "1.jpg").is_file(), "")
check(
    "catalog knows the book",
    (store.get_book(NESTED_AID) or {}).get("aid") == NESTED_AID,
    str((store.get_book(NESTED_AID) or {}).get("aid")),
)
check(
    "catalog chapter count is 2", len(store.list_chapters(NESTED_AID)) == 2, str(len(store.list_chapters(NESTED_AID)))
)
check("source directory untouched by the write", snapshot(ALLOWED) == ALLOWED_BEFORE)
check(
    "hand-add wrote its own rollback ledger", paths.SINGLE_ROLLBACK.is_file() and not paths.IMPORT_ROLLBACK.exists(), ""
)
check(
    "hand-add did not consume the batch ledger", import_apply.load_rollback() is None, str(import_apply.load_rollback())
)
check("hand-add ledger records one book", len((import_apply.load_single_rollback() or {}).get("books") or []) == 1, "")

replay = import_apply.apply_single(plan, plan["confirm_token"])
check("a replay is refused, not duplicated", replay["ok"] is False, str(replay.get("status")))
check(
    "replay did not add a chapter", len(store.list_chapters(NESTED_AID)) == 2, str(len(store.list_chapters(NESTED_AID)))
)


# ---------------------------------------------------------------------------
# 4. an existing Aid is skipped, never overwritten
# ---------------------------------------------------------------------------

# Pre-place an archive tree for the flat Aid so "already in the library" is real
# rather than an artifact of the previous test step.
img(FLAT / "001.jpg", b"f1")  # source is already there from the fixture
existing_tree = paths.ARCHIVE / FLAT_AID
existing_tree.mkdir(parents=True, exist_ok=True)
with __import__("zipfile").ZipFile(existing_tree / "1.zip", "w") as zf:
    zf.writestr("00001.jpg", b"PRE-EXISTING")
pre_bytes = (existing_tree / "1.zip").read_bytes()

flat_again = import_apply.plan_single(FLAT, FLAT_AID)
check(
    "an existing Aid is previewed as skip-existing",
    flat_again["item"]["action"] == "skip-existing",
    flat_again["item"]["action"],
)
skip = import_apply.apply_single(flat_again, flat_again["confirm_token"])
check(
    "adding an existing Aid writes nothing",
    skip["ok"] is False and skip["status"] == "skipped",
    str(skip.get("status")),
)
check("the pre-existing zip bytes survived", (existing_tree / "1.zip").read_bytes() == pre_bytes, "")


# ---------------------------------------------------------------------------
# 5. rollback is exact and keeps the source
# ---------------------------------------------------------------------------

payload = import_apply.load_single_rollback()
token = import_apply.single_rollback_token(payload)
check("rollback token is scoped", token.startswith("ROLLBACK 1 books #"), token)
bad = import_apply.rollback_single("ok")
check(
    "rollback refuses generic agreement",
    bad["ok"] is False and "scoped confirmation mismatch" in bad["error"],
    bad.get("error", ""),
)
check("refused rollback removed nothing", (paths.ARCHIVE / NESTED_AID / "1.zip").is_file(), "")

back = import_apply.rollback_single(token)
check("exact rollback token executes", back["ok"] is True and back["status"] == "rolled_back", back.get("message", ""))
check("rollback removed the zips", not (paths.ARCHIVE / NESTED_AID).exists(), "")
check("rollback removed the catalog row", store.get_book(NESTED_AID) is None, str(store.get_book(NESTED_AID)))
check("rollback removed its own ledger", not paths.SINGLE_ROLLBACK.exists(), "")
check("rollback touched no batch ledger", not paths.IMPORT_ROLLBACK.exists(), "")
check("source survived the round trip", snapshot(ALLOWED) == ALLOWED_BEFORE)


# ---------------------------------------------------------------------------
# 6. HTTP surface: whitelist + incoming listing
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402

from jmlib import api  # noqa: E402

client = TestClient(api.app)

r = client.post("/api/import/add/plan", json={"local_path": str(NESTED), "aid": NESTED_AID})
check("HTTP plan preview works", r.status_code == 200 and r.json()["ok"] is True, str(r.status_code))

r = client.post("/api/import/add/plan", json={"local_path": str(OUT_BOOK), "aid": OUT_AID})
check("a path outside the allowed roots is refused 403", r.status_code == 403, str(r.status_code))

r = client.post(
    "/api/import/add/plan", json={"local_path": str(ALLOWED / ".." / "outside" / "outside book"), "aid": OUT_AID}
)
check("a traversal path is refused 403", r.status_code == 403, str(r.status_code))

r = client.post("/api/import/add/plan", json={"local_path": "", "aid": NESTED_AID})
check("a missing path is refused 400", r.status_code == 400, str(r.status_code))

r = client.post("/api/import/add/plan", json={"local_path": str(ALLOWED / "does not exist"), "aid": NESTED_AID})
check(
    "a non-existent path gets a clear refusal",
    r.status_code == 200 and r.json()["ok"] is False and "not a directory" in r.json()["error"],
    str(r.json().get("error", "")),
)

# a whitelisted plan executed over HTTP, then rolled back over HTTP
plan_r = client.post("/api/import/add/plan", json={"local_path": str(NESTED), "aid": NESTED_AID, "title": "手加书名"})
http_plan = plan_r.json()
check("HTTP plan exposes a token", http_plan["confirm_token"].startswith("ADD "), http_plan["confirm_token"])
r = client.post("/api/import/add/apply", json={"plan": http_plan, "confirm": "yes"})
check(
    "HTTP apply refuses generic agreement",
    r.json()["ok"] is False and "scoped confirmation mismatch" in r.json()["error"],
    str(r.json().get("error", "")),
)
r = client.post("/api/import/add/apply", json={"plan": http_plan, "confirm": http_plan["confirm_token"]})
check("HTTP apply executes with the exact token", r.json()["ok"] is True, str(r.json().get("status")))
check(
    "HTTP apply kept the typed title",
    (store.get_book(NESTED_AID) or {}).get("title") == "手加书名",
    str((store.get_book(NESTED_AID) or {}).get("title")),
)

rb = client.get("/api/import/add/rollback").json()
check(
    "HTTP rollback preview exposes a token",
    rb["pending"] and rb["pending"]["confirm_token"].startswith("ROLLBACK 1 books #"),
    str(rb.get("pending")),
)
r = client.post("/api/import/add/rollback", json={"confirm": rb["pending"]["confirm_token"]})
check("HTTP rollback executes", r.json()["ok"] is True, str(r.json().get("status")))

# a forged plan over HTTP: mutating the chapter list must not redirect the writer.
# Use a fresh directory so the recomputed plan would otherwise be importable —
# a skip-existing Aid would short-circuit before the fingerprint check and the
# assertion would pass for the wrong reason.
TAMPER = ALLOWED / "tamper book"
TAMPER_AID = "888006"
img(TAMPER / TAMPER_AID / "1" / "001.jpg", b"t1")
img(TAMPER / TAMPER_AID / "1" / "002.jpg", b"t2")
plan_r = client.post("/api/import/add/plan", json={"local_path": str(TAMPER), "aid": TAMPER_AID})
tampered = plan_r.json()
tampered["item"]["chapters"]["1"] = [str(OUT_BOOK / OUT_AID / "1" / "001.jpg")]
r = client.post("/api/import/add/apply", json={"plan": tampered, "confirm": tampered["confirm_token"]})
check(
    "a tampered chapter list is refused (recomputed server-side)",
    r.json()["ok"] is False and "no longer matches this directory" in r.json().get("error", ""),
    str(r.json().get("error", "")),
)
check("the tampered attempt wrote no archive for the fresh Aid", not (paths.ARCHIVE / TAMPER_AID).exists(), "")
check(
    "the untouched pre-existing flat archive is still only its original zip",
    sorted(p.name for p in (paths.ARCHIVE / FLAT_AID).iterdir()) == ["1.zip"],
    str(sorted(p.name for p in (paths.ARCHIVE / FLAT_AID).iterdir())),
)

# incoming listing
r = client.get("/api/import/incoming")
body = r.json()
check("incoming endpoint returns 200", r.status_code == 200, str(r.status_code))
check(
    "empty incoming says what to do next",
    body["count"] == 0 and "library/_incoming" in body["hint"],
    str(body.get("hint")),
)

# empty generation skeletons are rollback scaffolding, not drop items
skel = paths.INCOMING / "JM(1)"
skel.mkdir(parents=True, exist_ok=True)
body = client.get("/api/import/incoming").json()
check(
    "an empty skeleton is reported separately, not as a 0-image book",
    body["count"] == 0 and body.get("skeletons") == ["JM(1)"],
    str(body),
)
skel.rmdir()

incoming_book = paths.INCOMING / "dropped book 888004" / "888004" / "1"
img(incoming_book / "001.jpg", b"d1")
r = client.get("/api/import/incoming")
body = r.json()
check("incoming lists a dropped directory", body["count"] == 1, str(body["count"]))
check(
    "incoming recognises the Aid from the name", body["items"][0]["aid"] == "888004", str(body["items"][0].get("aid"))
)
check(
    "incoming listing is read-only",
    (incoming_book / "001.jpg").is_file() and not (paths.ARCHIVE / "888004").exists(),
    "",
)

zip_path = paths.INCOMING / "888005.zip"
__import__("zipfile").ZipFile(zip_path, "w").writestr("00001.jpg", b"z1")
r = client.get("/api/import/incoming")
zips = [i for i in r.json()["items"] if i["kind"] == "zip"]
check("incoming lists a dropped zip", len(zips) == 1 and zips[0]["aid"] == "888005", str(zips))

# PIN still guards the new routes
api.app.state.pin = "s3cret"
check(
    "PIN blocks the add preview",
    TestClient(api.app).post("/api/import/add/plan", json={"local_path": str(NESTED), "aid": NESTED_AID}).status_code
    == 401,
    "",
)
check("PIN blocks the incoming listing", TestClient(api.app).get("/api/import/incoming").status_code == 401, "")
check("correct pin passes", TestClient(api.app).get("/api/import/incoming?pin=s3cret").status_code == 200, "")
api.app.state.pin = None

print()
if fails:
    print(f"B1 checks: {len(fails)} FAILED -> {fails}")
    sys.exit(1)
print("B1/B2 contract PASSED (offline, no network, zero real-library access)")
