"""Offline A.3 contract: id-resolution records (no network, no real library).

Redirects every ``jmlib.paths`` constant onto a scratch tree, then checks that
:mod:`jmlib.idres` records *decisions* only:

  * recording the same directory twice upserts one row (never piles up);
  * ``created_at`` survives a re-record while ``updated_at`` moves;
  * a chosen Aid / a skip are visible in list/get/decided/summary;
  * unknown states are rejected instead of silently stored;
  * candidates survive a JSON round-trip with score and reasons intact;
  * a v1 database without the ``id_resolution`` table degrades to ``[]``;
  * nothing here touches books/chapters/unresolved or any byte on disk.

Run:  $env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH='src'; python .tmp/test_idres.py
"""

# Moved verbatim from the ``.tmp`` exploration scripts so CI runs it.
# Runs as its own process: every contract redirects ``jmlib.paths`` onto a
# scratch tree, and those redirects must not leak between suites.
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jmlib import idres, paths, store  # noqa: E402

BASE = ROOT / ".tmp" / "a3_contracts"
if BASE.exists():
    shutil.rmtree(BASE)
BASE.mkdir(parents=True, exist_ok=True)  # default mode (0o700 is denied by sandbox)

DATA = BASE / "data"
LIB = BASE / "library"
paths.ROOT = BASE
paths.DATA = DATA
paths.LIBRARY = LIB
paths.ARCHIVE = LIB / "archive"
paths.UNIDENTIFIED = LIB / "_unidentified"
paths.COVERS = LIB / "covers"
paths.INCOMING = LIB / "_incoming"
paths.STAGING = DATA / "staging"
paths.DB_PATH = DATA / "jmlib.db"
paths.INGEST_METADATA = DATA / "ingest_metadata.json"
paths.ensure_dirs()

fails: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("PASS " if cond else "FAIL ") + name + (f" {extra}" if extra else ""))
    if not cond:
        fails.append(name)


REL_A = "JM/[汉化组] 小海和暑假 1440001"
REL_B = "JM/漫画 期号样本 (COMIC LO 2017年7月号)"
CANDIDATES = [
    {"id": "1440001", "name": "小海和暑假", "score": 0.9123, "hits": ["标题 bigram 重合 0.91"]},
    {"id": "1440002", "name": "海妹妹和暑假", "score": 0.804, "hits": ["标题 bigram 重合 0.80"]},
]

# --------------------------------------------------------------------------- #
# 1. schema
# --------------------------------------------------------------------------- #
schema = store.stats()["schemaVersion"]
check("schema version follows store.SCHEMA_VERSION", schema == store.SCHEMA_VERSION, schema)
with sqlite3.connect(paths.DB_PATH) as conn:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(id_resolution)").fetchall()]
check("id_resolution table exists", bool(cols), str(cols))
check(
    "id_resolution columns match the plan",
    cols == ["rel_path", "query", "candidates_json", "chosen_aid", "state", "note", "created_at", "updated_at"],
    str(cols),
)

# --------------------------------------------------------------------------- #
# 2. record / upsert
# --------------------------------------------------------------------------- #
created = idres.record(REL_A, "小海和暑假 1440001", CANDIDATES)
first = idres.get(REL_A)
check("record creates one row", created is True and first is not None)
check("record keeps the query", first["query"] == "小海和暑假 1440001", first["query"])
check(
    "record stores candidates with score and reasons",
    [c["id"] for c in first["candidates"]] == ["1440001", "1440002"]
    and first["candidates"][0]["score"] == 0.9123
    and first["candidates"][0]["hits"] == ["标题 bigram 重合 0.91"],
    json.dumps(first["candidates"][:1], ensure_ascii=False),
)
check("new record starts pending", first["state"] == "pending", first["state"])

again = idres.record(REL_A, "小海和暑假 1440001 v2", CANDIDATES[:1])
second = idres.get(REL_A)
check("re-record is not a new row", again is False)
check(
    "re-record keeps created_at",
    second["created_at"] == first["created_at"],
    f"{first['created_at']} vs {second['created_at']}",
)
check(
    "re-record refreshes updated_at",
    second["updated_at"] >= first["updated_at"],
    f"{first['updated_at']} -> {second['updated_at']}",
)
check("re-record replaces the candidates", len(second["candidates"]) == 1, str(len(second["candidates"])))
check("re-record does not duplicate rows", len(idres.rows()) == 1, str(len(idres.rows())))

# --------------------------------------------------------------------------- #
# 3. decisions
# --------------------------------------------------------------------------- #
ok = idres.set_choice(REL_A, "1440001", "封面与首图一致")
chosen = idres.get(REL_A)
check("set_choice returns True", ok is True)
check("set_choice records the Aid", chosen["chosen_aid"] == "1440001", str(chosen["chosen_aid"]))
check("set_choice moves state to chosen", chosen["state"] == "chosen", chosen["state"])
check("set_choice keeps the note", chosen["note"] == "封面与首图一致", chosen["note"])
check("set_choice keeps the candidates", len(chosen["candidates"]) == 1)

try:
    idres.set_choice(REL_A, "   ")
    empty_ok = False
except ValueError:
    empty_ok = True
check("set_choice rejects an empty Aid", empty_ok)

check("set_choice on an unknown dir returns False", idres.set_choice("JM/不存在", "1234567") is False)

skipped = idres.skip(REL_B, "杂志期号不是车号", [{"id": "999", "name": "疑似", "score": 0.2}])
skiprow = idres.get(REL_B)
check("skip creates a row when absent", skipped is True and skiprow is not None)
check("skip records the reason", skiprow["note"] == "杂志期号不是车号", skiprow["note"])
check("skip keeps candidates it was given", len(skiprow["candidates"]) == 1)
check("skip clears chosen aid", skiprow["chosen_aid"] is None, str(skiprow["chosen_aid"]))
check("skip does not pick a candidate", skiprow["state"] == "skipped", skiprow["state"])

check(
    "skip on an existing dir updates instead of inserting",
    idres.skip(REL_A, "先跳过") is True and len(idres.rows()) == 2,
    str(len(idres.rows())),
)
idres.set_choice(REL_A, "1440001", "改回")

try:
    idres.record("JM/bad", "q", [], state="approved")
    bad_state_ok = False
except ValueError:
    bad_state_ok = True
check("unknown state is rejected", bad_state_ok)

# --------------------------------------------------------------------------- #
# 4. views
# --------------------------------------------------------------------------- #
allrows = idres.rows()
check("list returns every row", len(allrows) == 2, str(len(allrows)))
check("list decodes candidates every time", all(isinstance(r["candidates"], list) for r in allrows))
check(
    "list filters by state",
    [r["rel_path"] for r in idres.rows("skipped")] == [REL_B],
    str([r["rel_path"] for r in idres.rows("skipped")]),
)
check("decided returns chosen only", idres.decided() == {REL_A: "1440001"}, str(idres.decided()))
check(
    "summary counts by state",
    idres.summary()["total"] == 2
    and idres.summary()["byState"]["chosen"] == 1
    and idres.summary()["byState"]["skipped"] == 1,
    json.dumps(idres.summary(), ensure_ascii=False),
)
check("get on an unknown dir is None", idres.get("JM/不存在") is None)

# reset(): forget the decision, keep the evidence
reset_ok = idres.reset(REL_A, note="改判重来")
back = idres.get(REL_A)
check("reset returns True when a row exists", reset_ok is True)
check("reset moves the row back to pending", back["state"] == "pending", back["state"])
check("reset drops the chosen Aid", back["chosen_aid"] is None, str(back["chosen_aid"]))
check("reset keeps the candidate evidence", len(back["candidates"]) == 1, str(len(back["candidates"])))
check("reset keeps the query it was searched with", back["query"] == "小海和暑假 1440001 v2", back["query"])
check("reset on a dir with no row returns False", idres.reset("JM/不存在") is False)
check("reset did not delete the row", idres.get(REL_A) is not None)
check("decided no longer lists the reset dir", idres.decided() == {}, str(idres.decided()))

# --------------------------------------------------------------------------- #
# 5. old library tolerance + zero side effects
# --------------------------------------------------------------------------- #
real_list = store.list_id_resolutions
store.list_id_resolutions = lambda state=None: (_ for _ in ()).throw(
    sqlite3.OperationalError("no such table: id_resolution")
)
tolerant = idres.rows()
tolerant_summary = idres.summary()
store.list_id_resolutions = real_list
check("v1 database without the table degrades to []", tolerant == [], str(tolerant))
check(
    "v1 database summary does not raise",
    tolerant_summary["total"] == 0,
    json.dumps(tolerant_summary, ensure_ascii=False),
)

before = store.stats()
idres.record("JM/新目录", "新目录", [])
idres.skip("JM/另一个", "无正文")
after = store.stats()
check(
    "resolution writes never touch books/chapters/unresolved",
    after["books"] == before["books"]
    and after["chapters"] == before["chapters"]
    and after["unresolved"] == before["unresolved"]
    and after["page_total"] == before["page_total"],
    f"{before} -> {after}",
)
check(
    "no byte was written under library/",
    list(paths.LIBRARY.rglob("*")) == [] or all(p.is_dir() for p in paths.LIBRARY.rglob("*")),
    str(list(paths.LIBRARY.rglob("*"))[:3]),
)

print()
if fails:
    print(f"A3 checks: {len(fails)} failed -> {fails}")
    raise SystemExit(1)
print("A3 checks: all passed")
print("A3 contract PASSED (offline, no network, no real library access)")
