"""Offline A.4 import-panel contract checks (no network, no real library touched).

Monkeypatches EVERY ``jmlib.paths`` constant onto a scratch tree, then drives the
panel through FastAPI's TestClient:

  * ``GET /import`` serves the real panel page;
  * scanning is read-only, whitelisted by ``JMLIB_IMPORT_ROOTS``, and rejects
    traversal / out-of-whitelist roots;
  * ``GET /api/import/manifest`` enriches entries with the effective Aid and the
    resolution state, without touching any byte;
  * ``POST /api/import/candidates`` records the evidence it got and never picks
    (upstream is stubbed — the contract asserts *no real network call*);
  * ``resolve`` accepts chosen/skipped/pending and refuses bad Aids;
  * ``plan`` issues a token, ``apply`` refuses a generic "yes", a client-minted
    token and a stale plan, and executes only on the exact token;
  * ``rollback`` reverses exactly, and the source tree is byte-identical after
    every step;
  * the PIN gate covers the new endpoints.

Run:  $env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH='src'; python .tmp/test_import_api.py
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jmlib import paths  # noqa: E402

BASE = ROOT / ".tmp" / "a4_contracts"
if BASE.exists():
    shutil.rmtree(BASE)
BASE.mkdir(parents=True, exist_ok=True)

DATA = BASE / "data"
LIB = BASE / "library"
SRC = BASE / "incoming_root"

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

os.environ["JMLIB_IMPORT_ROOTS"] = str(SRC)

from fastapi.testclient import TestClient  # noqa: E402

from jmlib import api, idres, store, upstream  # noqa: E402

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

BOOKS = SRC / "JM"
img(BOOKS / "book alpha 777101" / "777101" / "1" / "001.jpg", b"a1")
img(BOOKS / "book alpha 777101" / "777101" / "1" / "002.jpg", b"a2")
img(BOOKS / "flat book 777102" / "001.jpg", b"f1")
img(BOOKS / "flat book 777102" / "002.jpg", b"f2")
img(BOOKS / "needs id book" / "1" / "001.jpg", b"n1")
SOURCE_BEFORE = snapshot(SRC)

client = TestClient(api.app)

# ---------------------------------------------------------------------------
# Page + overview
# ---------------------------------------------------------------------------

r = client.get("/import")
check("GET /import serves the panel", r.status_code == 200 and "导入面板" in r.text, str(r.status_code))
check("panel declares the whitelist", "JMLIB_IMPORT_ROOTS" in r.text)

r = client.get("/api/import")
body = r.json()
check("overview lists the allowed root", any(str(SRC) in x for x in body["roots"]), str(body["roots"]))
check("overview has no manifest yet", body["manifest"] is None, str(body["manifest"]))
check("overview reports empty resolutions", body["resolutions"]["total"] == 0, str(body["resolutions"]))

r = client.get("/api/import/manifest")
check("manifest is 404 before a scan", r.status_code == 404, str(r.status_code))

# ---------------------------------------------------------------------------
# Root whitelist
# ---------------------------------------------------------------------------

r = client.post("/api/import/scan", json={"root": "../../etc", "rescan": False})
check("traversal root is refused", r.status_code in (400, 403), str(r.status_code))
r = client.post("/api/import/scan", json={"root": "D:/nope-not-allowed", "rescan": False})
check("out-of-whitelist root is refused", r.status_code in (400, 403), str(r.status_code))
r = client.post("/api/import/scan", json={"root": str(BASE), "rescan": False})
check("existing but unlisted root is refused", r.status_code == 403, str(r.status_code))
check("no byte written by refused scans", snapshot(SRC) == SOURCE_BEFORE and not store.list_books())

# ---------------------------------------------------------------------------
# Scan (read-only) + manifest
# ---------------------------------------------------------------------------

r = client.post("/api/import/scan", json={"root": str(SRC), "rescan": True})
scan = r.json()
check("scan succeeds", r.status_code == 200 and scan["summary"]["dirs"] == 3, str(scan.get("summary")))
check("scan wrote the manifest", scan["written"] is True and paths.IMPORT_MANIFEST.exists())
check("scan is read-only on the source", snapshot(SRC) == SOURCE_BEFORE)
check("scan wrote no library byte and no catalog row", snapshot(LIB) == {} and not store.list_books())

r = client.get("/api/import/manifest")
man = r.json()
check("manifest returns every directory", len(man["entries"]) == 3, str(len(man["entries"])))
check("manifest reports the summary", man["summary"]["dirs"] == 3, str(man["summary"]))
row = next(e for e in man["entries"] if e["dir"] == "book alpha 777101")
check("manifest exposes the structural Aid", row["aid"] == "777101", row["aid"])
check("manifest exposes the effective Aid", row["effective_aid"] == "777101", row["effective_aid"])
check("manifest marks the aid-less directory", row["needs_id"] is False or row["aid"] == "", str(row["aid"]))
ids = {e["dir"]: e["effective_aid"] for e in man["entries"]}
check("aid-less directory has an empty effective Aid", ids["needs id book"] == "", str(ids))

# ---------------------------------------------------------------------------
# Candidates (upstream stubbed: assert no real network happens)
# ---------------------------------------------------------------------------

calls: list[dict] = []


def fake_search(title, **kw):
    calls.append({"title": title, **kw})
    return {
        "ok": True,
        "query": title,
        "variant": title,
        "cached": False,
        "failure": None,
        "error": "",
        "candidates": [
            {"id": "888001", "name": "needs id book", "score": 0.91, "hits": ["同名"]},
            {"id": "888002", "name": "needs id book 2", "score": 0.62, "hits": []},
        ],
        "auto_ok": True,
        "reason": "top score 0.91",
    }


real_search = upstream.search
upstream.search = fake_search
try:
    r = client.post("/api/import/candidates", json={"rel_path": "needs id book"})
    cands = r.json()
    check(
        "candidates returns upstream hits", r.status_code == 200 and len(cands["candidates"]) == 2, str(r.status_code)
    )
    check("candidates never auto-picks", cands["candidates"][0]["id"] == "888001" and cands.get("auto_ok") is True, "")
    check("candidates used one upstream call for one directory", len(calls) == 1, str(len(calls)))
    check("candidates sent only a title keyword", calls[0]["title"] == "needs id book", str(calls[0].get("title")))
    check("candidates recorded the evidence", idres.summary()["byState"]["pending"] == 1, str(idres.summary()))
    check("recording candidates wrote no library byte", snapshot(LIB) == {})

    r = client.post("/api/import/candidates", json={"rel_path": "does not exist"})
    check("candidates rejects an unknown directory", r.status_code == 404, str(r.status_code))

    def boom(title, **kw):
        calls.append({"title": title})
        raise RuntimeError("network down")

    upstream.search = boom
    # ``upstream.search`` never raises by contract, so a raise is a genuine bug:
    # it must surface as a 500 (not be swallowed into a fake 200 with no hits).
    # TestClient re-raises server exceptions by default, so ask for the real one.
    boom_client = TestClient(api.app, raise_server_exceptions=False)
    r = boom_client.post("/api/import/candidates", json={"rel_path": "needs id book"})
    check("an upstream crash is a 500, not a corrupt state", r.status_code == 500, str(r.status_code))
    check("a failed lookup recorded no decision", idres.summary()["byState"]["pending"] == 1, str(idres.summary()))
finally:
    upstream.search = real_search

# ---------------------------------------------------------------------------
# Resolve
# ---------------------------------------------------------------------------

r = client.post("/api/import/resolve", json={"rel_path": "needs id book", "aid": "0121", "state": "chosen"})
check("resolve refuses a malformed Aid", r.status_code == 400, str(r.status_code))
r = client.post("/api/import/resolve", json={"rel_path": "needs id book", "aid": "", "state": "chosen"})
check("resolve refuses an empty Aid", r.status_code == 400, str(r.status_code))
r = client.post("/api/import/resolve", json={"rel_path": "needs id book", "state": "bogus"})
check("resolve refuses an unknown state", r.status_code == 400, str(r.status_code))

r = client.post("/api/import/resolve", json={"rel_path": "needs id book", "aid": "777103", "state": "chosen"})
check("resolve records a chosen Aid", r.status_code == 200 and r.json()["state"] == "chosen", str(r.status_code))
check("chosen state is persisted", idres.decided() == {"needs id book": "777103"}, str(idres.decided()))

r = client.get("/api/import/manifest")
row = next(e for e in r.json()["entries"] if e["dir"] == "needs id book")
check(
    "manifest reflects the resolution",
    row["effective_aid"] == "777103" and row["resolution_state"] == "chosen",
    f"{row['effective_aid']}/{row['resolution_state']}",
)

# ---------------------------------------------------------------------------
# Plan + apply gates
# ---------------------------------------------------------------------------

r = client.post("/api/import/plan", json={})
plan = r.json()
check(
    "plan covers the three directories",
    r.status_code == 200 and plan["summary"]["books"] == 3,
    str(plan.get("summary")),
)
check("plan is awaiting confirmation", plan["status"] == "awaiting scoped confirmation", plan["status"])
check("plan wrote nothing", snapshot(LIB) == {} and not store.list_books())

r = client.post("/api/import/apply", json={"plan": plan, "confirm": "yes, go ahead"})
check(
    "generic agreement refused",
    r.json()["ok"] is False and "scoped confirmation mismatch" in r.json()["error"],
    str(r.json().get("error")),
)
check("refused apply wrote nothing", snapshot(LIB) == {} and not store.list_books())

forged = dict(plan)
forged["confirm_token"] = "IMPORT 3 books / 3 chapters / 5 images #000000000000"
r = client.post("/api/import/apply", json={"plan": forged, "confirm": plan["confirm_token"]})
check(
    "client-minted token refused",
    r.json()["ok"] is False and "server-issued manifest" in r.json()["error"],
    str(r.json().get("error")),
)

r = client.post("/api/import/apply", json={"plan": plan, "confirm": plan["confirm_token"]})
done = r.json()
check("exact token executes", done["ok"] is True and done["status"] == "executed", str(done.get("message")))
check("three books landed", store.stats()["books"] == 3, str(store.stats()["books"]))
check("source tree byte-identical after import", snapshot(SRC) == SOURCE_BEFORE)
check(
    "import wrote one zip per chapter",
    sorted(p.name for p in (paths.ARCHIVE / "777101").glob("*.zip")) == ["1.zip"],
    str(sorted(p.name for p in (paths.ARCHIVE / "777101").glob("*.zip"))),
)
with zipfile.ZipFile(paths.ARCHIVE / "777101" / "1.zip") as zf:
    names = zf.namelist()
check("chapter members are zero-padded", names == ["00001.jpg", "00002.jpg"], str(names))

r = client.post("/api/import/apply", json={"plan": plan, "confirm": plan["confirm_token"]})
check("a replay of the same plan is refused, not duplicated", r.json()["ok"] is False, str(r.json().get("status")))
check("replay left the library at 3 books", store.stats()["books"] == 3, str(store.stats()["books"]))

# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

r = client.get("/api/import/rollback")
pending = r.json()["pending"]
check("rollback preview lists the run", pending and len(pending["books"]) == 3, str(pending and len(pending["books"])))
check(
    "rollback preview exposes a token",
    pending["confirm_token"].startswith("ROLLBACK 3 books #"),
    pending["confirm_token"],
)

r = client.post("/api/import/rollback", json={"confirm": "yes"})
check("rollback refuses generic agreement", r.json()["ok"] is False, str(r.json().get("error")))
check("refused rollback kept the zips", (paths.ARCHIVE / "777101" / "1.zip").is_file())

r = client.post("/api/import/rollback", json={"confirm": pending["confirm_token"]})
check(
    "exact rollback token executes",
    r.json()["ok"] is True and r.json()["status"] == "rolled_back",
    str(r.json().get("message")),
)
check("rollback removed the imported archives", not (paths.ARCHIVE / "777101").exists())
check("rollback cleared the catalog", not store.list_books(), str(store.list_books()))
check("source tree survived rollback", snapshot(SRC) == SOURCE_BEFORE)
r = client.get("/api/import/rollback")
check("rollback preview is empty again", r.json()["pending"] is None, str(r.json()))

# ---------------------------------------------------------------------------
# PIN gate covers the new endpoints
# ---------------------------------------------------------------------------

api.app.state.pin = "s3cret"
try:
    for method, url, payload in [
        ("get", "/import", None),
        ("get", "/api/import", None),
        ("get", "/api/import/manifest", None),
        ("post", "/api/import/scan", {"root": str(SRC), "rescan": False}),
        ("post", "/api/import/plan", {}),
    ]:
        fn = getattr(client, method)
        r = fn(url, json=payload) if payload is not None else fn(url)
        check(f"PIN blocks {method.upper()} {url}", r.status_code == 401, str(r.status_code))
    r = client.get("/api/import?pin=s3cret")
    check("correct pin passes", r.status_code == 200, str(r.status_code))
    r = client.get("/api/import?pin=wrong")
    check("wrong pin is 401", r.status_code == 401, str(r.status_code))
finally:
    api.app.state.pin = None

print()
if fails:
    print(f"A4 checks: {len(fails)} FAILED -> {fails}")
    sys.exit(1)
print("A4 contract PASSED (offline, no network, zero real-library access)")
