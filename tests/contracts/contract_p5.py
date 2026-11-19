"""Offline P5 governance contract checks (no network, no real archive touched).

Monkeypatches EVERY ``jmlib.paths`` constant onto a scratch tree (including the
new GOVERNANCE_MANIFEST), then exercises:

  * Data Destruction Guards: 4 destructive classes are read-only, always refuse
    execution, and expose Status = "awaiting scoped confirmation";
  * change_manifest() for all 4 kinds is read-only and exact;
  * promote_plan() is read-only and rejects bad aid / traversal / unknown zip /
    chapter collisions;
  * promote_apply() refuses a generic "yes", a forged client-minted token, a
    tampered plan, and a stale plan (TOCTOU); only the exact server-issued
    scoped token executes;
  * execution is a byte-exact same-volume rename with zero deletion, rebuilds
    the cover, registers catalog rows, and survives ``jmlib index`` rebuild;
  * a second promote into an EXISTING book adds one chapter without overwriting
    rich metadata;
  * promote_rollback() reverses each action exactly (file + rows + unresolved),
    refuses double/unknown rollback, and never deletes the derived cover cache;
  * the 7 HTTP endpoints (incl. PIN gate) behave through FastAPI TestClient.

Run:  $env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH='src'; python .tmp/test_p5_governance.py
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

from jmlib import cover, governance, health, indexer, ingest, paths, store  # noqa: E402

BASE = ROOT / ".tmp" / "p5_contracts"
if BASE.exists():
    shutil.rmtree(BASE)
BASE.mkdir(parents=True, exist_ok=True)  # default mode (0o700 is denied by sandbox)

DATA = BASE / "data"
LIB = BASE / "library"
LOOSE = BASE / "loose"

# Redirect EVERY path constant to the scratch tree.  WEB stays on the real,
# read-only web dir so GET /governance serves the actual page.
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
paths.ensure_dirs()

fails: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("PASS " if cond else "FAIL ") + name + (f" {extra}" if extra else ""))
    if not cond:
        fails.append(name)


def make_zip(p: Path, members: dict[str, bytes]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)


# ---------------------------------------------------------------------------
# Fixture: 2 unresolved zips, a loose queue, migration/rollback manifests.
# ---------------------------------------------------------------------------
A_ALPHA = b"\xff\xd8\xffALPHA-IMAGE-ONE\x00\x11" * 7
A_BETA = b"\xff\xd8\xffALPHA-IMAGE-TWO\x00\x22" * 9
B_ONE = b"\xff\xd8\xffBETA-IMAGE-ONE\x00\x33" * 5

REL_A = "library/_unidentified/JM(1)/alpha.zip"
REL_B = "library/_unidentified/JM(1)/beta.zip"
SRC_A = BASE / REL_A
SRC_B = BASE / REL_B
make_zip(SRC_A, {"00001.jpg": A_ALPHA, "00002.jpg": A_BETA})
make_zip(SRC_B, {"00001.jpg": B_ONE})
ORIGINAL_BYTES = {"A": SRC_A.read_bytes(), "B": SRC_B.read_bytes()}

row_a = {"rel_path": REL_A, "reason": "no reliable Aid in path", "generation": "JM(1)", "size": SRC_A.stat().st_size}
row_b = {"rel_path": REL_B, "reason": "no reliable Aid in path", "generation": "JM(1)", "size": SRC_B.stat().st_size}
store.insert_unresolved(row_a)
store.insert_unresolved(row_b)

for name, payload in (("Book One", b"loose-one"), ("Book Two", b"loose-two")):
    d = LOOSE / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "00001.jpg").write_bytes(payload)

paths.MIGRATION_MANIFEST.write_text(
    json.dumps(
        {
            "loose": {"root": str(LOOSE)},
            "entries": [
                {"kind": "strict", "aid": "999999", "old": "JM/999999/1.zip", "generation": "JM"},
                {"kind": "strict", "aid": "999998", "old": "长篇/999998/1.zip", "generation": "长篇"},
                {"kind": "unresolved", "new": "_unidentified/JM(1)/whatever.zip", "reason": "no reliable Aid in path"},
            ],
        },
        ensure_ascii=False,
    ),
    encoding="utf-8",
)
paths.ROLLBACK_MANIFEST.write_text(
    json.dumps(
        {
            "entries": [
                {"from": "library/archive/999999/1.zip", "to": "JM/999999/1.zip"},
                {"from": "library/_unidentified/x.zip", "to": "JM(1)/x.zip"},
            ],
        },
        ensure_ascii=False,
    ),
    encoding="utf-8",
)

# ---------------------------------------------------------------------------
# A. Guards and read-only change manifests.
# ---------------------------------------------------------------------------
s0 = governance.status()
check("status is read_only", s0["read_only"] is True and s0["executed"] == 0 and not s0["actions"])
check("status exposes 4 destructive guards", len(s0["destructive"]) == 4, str(len(s0["destructive"])))
g = governance.destruction_guard("loose_cleanup", "C:\\a\\b", "detail-x")
check("guard Status awaiting scoped confirmation", g["Status"] == "awaiting scoped confirmation")
check("guard Confirmation Required yes", str(g["Confirmation Required"]).startswith("yes"))
check("guard blocks any deletion", any("os.remove" in s for s in g["Blocked Destructive Steps"]))
check("guard names exact target", g["Exact Target(s)"] == "C:\\a\\b" and g["detail"] == "detail-x")
check("unknown guard kind returns error", "error" in governance.destruction_guard("nope"))
r = governance.refuse_destructive("loose_cleanup", "x", "y")
check("refuse_destructive always refused", r["ok"] is False and r["status"] == "refused" and "guard" in r)

stats_before = store.stats()
zips_before = len(list(paths.ARCHIVE.rglob("*.zip")))
manifests = {}
for kind in governance.DESTRUCTIVE_KINDS:
    m = governance.change_manifest(kind)
    manifests[kind] = m
    check(
        f"change_manifest[{kind}] read_only", m.get("read_only") is True and "error" not in m, str(m.get("error", ""))
    )
    check(f"change_manifest[{kind}] guard awaiting", m["guard"]["Status"] == "awaiting scoped confirmation")
check(
    "loose_cleanup total = 2 scratch dirs",
    manifests["loose_cleanup"]["total"] == 2,
    str(manifests["loose_cleanup"]["total"]),
)
check("loose_cleanup items capped/exact", len(manifests["loose_cleanup"]["items"]) == 2)
check("migration_rollback total = 2 rows", manifests["migration_rollback"]["total"] == 2)
check("migration_rollback items capped", len(manifests["migration_rollback"]["items"]) == 2)
dup = health._duplicate_pages([])
check("duplicate_pages total matches health", manifests["duplicate_pages"]["total"] == dup["confirmed_groups"])
# 2 strict generations (JM / 长篇) + 1 unresolved entry with a blank generation.
check("generations enumerated exactly", manifests["generations"]["total"] == 3, str(manifests["generations"]["total"]))
check(
    "change_manifest wrote nothing",
    not paths.GOVERNANCE_MANIFEST.exists()
    and store.stats() == stats_before
    and len(list(paths.ARCHIVE.rglob("*.zip"))) == zips_before,
)
check("change_manifest unknown kind", "error" in governance.change_manifest("bogus"))

# ---------------------------------------------------------------------------
# B. promote_plan is read-only and validates identity.
# ---------------------------------------------------------------------------
p1 = governance.promote_plan(REL_A, "777001", 1, "治理转正标题")
check("promote_plan ok", p1["ok"] is True, str(p1.get("error", "")))
plan1 = p1["plan"]
token1 = plan1["confirm_token"]
check("token names exact action", token1 == f"PROMOTE {REL_A} -> 777001/1", token1)
check(
    "plan target + page_count",
    plan1["to"] == "library/archive/777001/1.zip" and plan1["page_count"] == 2 and plan1["first_member"] == "00001.jpg",
)
check("plan would create a new book", plan1["would_create_book"] is True)
check("plan safety zero deletion", "none" in plan1["safety"]["deletion"] and "never" in plan1["safety"]["overwrite"])
check(
    "plan is read-only",
    SRC_A.is_file()
    and not (BASE / plan1["to"]).exists()
    and store.get_unresolved(REL_A) is not None
    and store.get_book("777001") is None
    and not paths.GOVERNANCE_MANIFEST.exists(),
)
check("bad aid rejected", governance.promote_plan(REL_A, "abc")["ok"] is False)
check(
    "path traversal rejected",
    governance.promote_plan("library/../_unidentified/JM(1)/alpha.zip", "777001")["error"]
    == "invalid unresolved rel_path",
)
check("non-unresolved prefix rejected", governance.promote_plan("library/archive/x/1.zip", "777001")["ok"] is False)
check(
    "unknown rel_path rejected",
    governance.promote_plan("library/_unidentified/JM(1)/ghost.zip", "777001")["ok"] is False,
)

# ---------------------------------------------------------------------------
# C. promote_apply gates: generic consent / forged token / tampered plan.
# ---------------------------------------------------------------------------
r = governance.promote_apply(plan1, "yes, go ahead")
check(
    "generic consent refused",
    r["ok"] is False and r["status"] == "refused" and "scoped confirmation mismatch" in r["error"],
)
forged = dict(plan1, confirm_token="PROMOTE forged -> 1/1")
r = governance.promote_apply(forged, token1)
check("client-forged token refused", r["ok"] is False and "server-issued manifest" in r["error"], str(r))
tampered = dict(plan1, to="library/archive/666666/9.zip")
r = governance.promote_apply(tampered, token1)
check("tampered target refused", r["ok"] is False and "server-issued manifest" in r["error"])
check(
    "no gates wrote anything",
    SRC_A.is_file() and store.get_book("777001") is None and not paths.GOVERNANCE_MANIFEST.exists(),
)

# A confined server may lack DELETE on P0-era zips that kept their old ACL.
# The first mutation must then fail as an actionable refusal, never a 500 and
# never a partial catalog write.
_before_denied = store.stats()
real_rename = os.rename


def deny_rename(_src, _dst):
    raise PermissionError("[WinError 5] 拒绝访问。")


os.rename = deny_rename
try:
    denied = governance.promote_apply(plan1, token1)
finally:
    os.rename = real_rename
check(
    "OSError rename refused, not raised",
    denied["ok"] is False and denied["status"] == "refused" and "file permissions" in denied["error"],
    str(denied),
)
check(
    "OSError refusal leaves no partial write",
    SRC_A.is_file()
    and not (BASE / plan1["to"]).exists()
    and not (BASE / plan1["to"]).parent.exists()
    and store.stats() == _before_denied
    and not paths.GOVERNANCE_MANIFEST.exists(),
)
applied = governance.promote_apply(plan1, token1)
check("OSError refusal is retryable", applied["ok"] is True, str(applied))

# ---------------------------------------------------------------------------
# D. Exact scoped execution: zero-deletion rename + catalog + cover + sidecar.
# ---------------------------------------------------------------------------
check("exact token executes", applied["ok"] is True and applied["status"] == "executed", str(applied))
act1 = applied["action"]
check("action records created_book", act1["created_book"] is True and act1["meta_added"] is True)
dst1 = BASE / plan1["to"]
check("source moved away (zero copy)", not SRC_A.exists())
check("target zip bytes identical", dst1.is_file() and dst1.read_bytes() == ORIGINAL_BYTES["A"])
with zipfile.ZipFile(dst1) as zf:
    check("target members intact", cover.image_members(zf) == ["00001.jpg", "00002.jpg"])
book1 = store.get_book("777001")
check(
    "book + chapter registered",
    book1 is not None and book1["title"] == "治理转正标题" and store.chapter_count("777001") == 1,
)
cov = BASE / book1["cover_path"]
check("cover = first image bytes", cov.is_file() and cov.read_bytes() == A_ALPHA)
check("unresolved row removed", store.get_unresolved(REL_A) is None)
check("sidecar added for new book", "777001" in ingest.load_meta())
check(
    "ledger recorded executed", any(a["id"] == act1["id"] and a["status"] == "executed" for a in governance.actions())
)
check("stale plan refused (TOCTOU)", governance.promote_apply(plan1, token1)["ok"] is False)

# chapter collision must never overwrite
check("existing chapter rejected", governance.promote_plan(REL_B, "777001", 1)["error"].startswith("chapter 777001/1"))

# ---------------------------------------------------------------------------
# E. Second promote into an EXISTING book: add one chapter, never overwrite.
# ---------------------------------------------------------------------------
p2 = governance.promote_plan(REL_B, "777001", None, "不应覆盖")
plan2 = p2["plan"]
check("auto pindex = 2", plan2["pindex"] == 2 and plan2["would_create_book"] is False)
applied2 = governance.promote_apply(plan2, plan2["confirm_token"])
check("second promote executes", applied2["ok"] is True, str(applied2))
act2 = applied2["action"]
check("second action did NOT create a book", act2["created_book"] is False and act2["meta_added"] is False)
check("two chapters registered", store.chapter_count("777001") == 2)
check("existing book title untouched", store.get_book("777001")["title"] == "治理转正标题")
check("no extra sidecar entry", list(ingest.load_meta()) == ["777001"])

# A catalog rebuild must not lose the governance-created book / rich title.
rc = indexer.rebuild()
check("indexer rebuild PASS", rc == 0)
check("rebuild keeps governance title", (store.get_book("777001") or {}).get("title") == "治理转正标题")
check("rebuild keeps both chapters", store.chapter_count("777001") == 2)

# ---------------------------------------------------------------------------
# F. Exact rollback: file back, rows gone, unresolved restored, zero deletion.
# ---------------------------------------------------------------------------
_before_rollback_denied = store.stats()
os.rename = deny_rename
try:
    denied_rb = governance.promote_rollback(act2["id"])
finally:
    os.rename = real_rename
check(
    "rollback OSError refused, not raised",
    denied_rb["ok"] is False and denied_rb["status"] == "refused" and "file permissions" in denied_rb["error"],
    str(denied_rb),
)
check(
    "rollback OSError leaves action executed + DB unchanged",
    store.stats() == _before_rollback_denied
    and any(a["id"] == act2["id"] and a["status"] == "executed" for a in governance.actions())
    and (BASE / plan2["to"]).is_file(),
)

rb2 = governance.promote_rollback(act2["id"])
check("rollback #2 ok", rb2["ok"] is True and rb2["status"] == "rolled_back", str(rb2))
check("beta zip restored byte-exact", SRC_B.is_file() and SRC_B.read_bytes() == ORIGINAL_BYTES["B"])
check("chapter 2 removed", store.chapter_count("777001") == 1)
check("beta unresolved restored", store.get_unresolved(REL_B) is not None)
check("alpha zip still intact after #2 rollback", dst1.is_file())
check("double rollback refused", governance.promote_rollback(act2["id"])["ok"] is False)

rb1 = governance.promote_rollback(act1["id"])
check("rollback #1 ok", rb1["ok"] is True, str(rb1))
check("alpha zip restored byte-exact", SRC_A.is_file() and SRC_A.read_bytes() == ORIGINAL_BYTES["A"])
check("target zip gone after rollback", not dst1.exists())
check("book removed", store.get_book("777001") is None)
check("alpha unresolved restored", store.get_unresolved(REL_A) is not None)
check("sidecar entry removed", "777001" not in ingest.load_meta())
check("derived cover cache kept (no deletion)", cov.is_file())
check("unknown rollback refused", governance.promote_rollback("g999")["ok"] is False)
sf = governance.status()
check(
    "ledger: 0 executed / 2 rolled_back",
    sf["executed"] == 0 and sf["rolled_back"] == 2,
    f"{sf['executed']}/{sf['rolled_back']}",
)
check("no archive zips remain", len(list(paths.ARCHIVE.rglob("*.zip"))) == 0)

# ---------------------------------------------------------------------------
# G. HTTP surface (real FastAPI app, scratch state) + PIN gate.
# ---------------------------------------------------------------------------
from fastapi.testclient import TestClient  # noqa: E402

from jmlib import api  # noqa: E402

with TestClient(api.app) as c:
    check(
        "GET /governance serves real page",
        c.get("/governance").status_code == 200 and "受控治理" in c.get("/governance").text,
    )
    r = c.get("/api/governance")
    check(
        "GET /api/governance 200",
        r.status_code == 200 and r.json()["read_only"] is True and len(r.json()["destructive"]) == 4,
    )
    r = c.get("/api/governance/manifest", params={"kind": "loose_cleanup"})
    check("GET manifest 200 read_only", r.status_code == 200 and r.json()["read_only"] is True)
    check(
        "GET manifest unknown kind 400", c.get("/api/governance/manifest", params={"kind": "nope"}).status_code == 400
    )
    r = c.post("/api/governance/destructive", json={"kind": "loose_cleanup", "target": "x"})
    check(
        "POST destructive always refused",
        r.status_code == 200 and r.json()["ok"] is False and r.json()["status"] == "refused",
    )
    bad = c.post("/api/governance/promote/plan", json={"rel_path": REL_A, "aid": "abc"})
    check("POST plan invalid aid", bad.status_code == 200 and bad.json()["ok"] is False)
    good = c.post(
        "/api/governance/promote/plan", json={"rel_path": REL_A, "aid": "777002", "pindex": 1, "title": "HTTP 转正"}
    )
    jp = good.json()
    check("POST plan valid", jp["ok"] is True and jp["plan"]["confirm_token"].startswith("PROMOTE"))
    tok = jp["plan"]["confirm_token"]
    r = c.post("/api/governance/promote/apply", json={"plan": jp["plan"], "confirm": "yes"})
    check("POST apply wrong token refused", r.json()["ok"] is False)
    r = c.post("/api/governance/promote/apply", json={"plan": jp["plan"], "confirm": tok})
    check("POST apply exact token executes", r.json()["ok"] is True, str(r.json()))
    aid = r.json()["action"]["id"]
    r = c.post(f"/api/governance/rollback/{aid}", json={})
    check("POST rollback executes", r.json()["ok"] is True and r.json()["status"] == "rolled_back")
    check("HTTP roundtrip zero deletion", SRC_A.is_file() and SRC_A.read_bytes() == ORIGINAL_BYTES["A"])

    api.app.state.pin = "s3cret"
    check("PIN blocks /governance", c.get("/governance").status_code == 401)
    check("PIN blocks /api/governance", c.get("/api/governance").status_code == 401)
    check(
        "PIN blocks POST destructive",
        c.post("/api/governance/destructive", json={"kind": "loose_cleanup"}).status_code == 401,
    )
    check("PIN query allows /governance", c.get("/governance", params={"pin": "s3cret"}).status_code == 200)
    check("PIN wrong value blocks", c.get("/api/governance", params={"pin": "wrong"}).status_code == 401)
    api.app.state.pin = None

check(
    "final state clean",
    store.get_book("777001") is None
    and store.get_book("777002") is None
    and store.get_unresolved(REL_A) is not None
    and store.get_unresolved(REL_B) is not None,
)

print(
    f"\nP5 governance checks: {'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}"
    f" (offline, zero real-archive writes)"
)
raise SystemExit(1 if fails else 0)
