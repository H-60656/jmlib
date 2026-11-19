"""P3 storage-health + findings contract checks (process-internal TestClient).

No listening port.  The only writes are findings rows created by the test itself;
they are removed at the end so the real DB returns to its pre-test state.
"""
# Moved verbatim from the ``.tmp`` exploration scripts so CI runs it.
# Runs as its own process: every contract redirects ``jmlib.paths`` onto a
# scratch tree, and those redirects must not leak between suites.

from __future__ import annotations

import csv
import io
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from jmlib import api, health, paths, store  # noqa: E402

PASS = 0
FAIL = 0
CREATED_IDS: list[int] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", name, extra)
    else:
        FAIL += 1
        print("FAIL", name, extra)


def cleanup() -> None:
    conn = store.connect()
    with conn:
        for fid in CREATED_IDS:
            conn.execute("DELETE FROM findings WHERE id = ?", (fid,))
    conn.close()


try:
    c = TestClient(api.app)

    baseline_stats = store.stats()
    baseline_findings = store.list_findings()
    baseline_zips = sum(1 for _ in paths.ARCHIVE.rglob("*.zip")) + sum(1 for _ in paths.UNIDENTIFIED.rglob("*.zip"))

    # Live baseline: P4 ingested 15 loose books, so P3-era hard-coded numbers
    # (170 / 1455 / 154 / 6 generations) are stale.  Derive every expectation
    # from the DB, the migration manifest, and the physical archive instead.
    conn = store.connect()
    try:
        book_rows = [dict(r) for r in conn.execute("SELECT aid,title,cover_path,generation FROM books ORDER BY aid")]
        chapter_rows = [
            dict(r)
            for r in conn.execute("SELECT aid,pindex,zip_path,page_count,cover_path FROM chapters ORDER BY aid,pindex")
        ]
    finally:
        conn.close()
    expected_no_title = sum(1 for b in book_rows if not b["title"])
    migration = health._manifest()
    strict_gens = Counter(e.get("generation", "") for e in migration.get("entries", []) if e.get("kind") == "strict")
    book_gens = Counter(b.get("generation", "") for b in book_rows)
    expected_generations = sorted(set(strict_gens) | set(book_gens))
    expected_dup_groups = health._duplicate_pages(chapter_rows)["confirmed_groups"]
    loose_root = Path((migration.get("loose") or {}).get("root", ""))
    expected_loose_dirs = sum(1 for p in loose_root.iterdir() if p.is_dir()) if loose_root.is_dir() else 0

    # ---- read-only page ----
    r = c.get("/health")
    check(
        "GET /health serves panel", r.status_code == 200 and "存储体检" in r.text, f"{r.status_code} {len(r.content)}B"
    )

    # ---- read-only report ----
    r = c.get("/api/health")
    d = r.json()
    keys = [s["key"] for s in d["sections"]]
    check("GET /api/health 200 read_only", r.status_code == 200 and d["read_only"] is True)
    check(
        "six sections in planned order",
        keys == ["catalog", "generations", "rollback", "unresolved", "loose_queue", "duplicate_pages"],
        str(keys),
    )
    check(
        "stats match live catalog + physical archive",
        d["stats"]["books"] == baseline_stats["books"] == len(book_rows)
        and d["stats"]["chapters"] == baseline_stats["chapters"] == len(chapter_rows)
        and d["stats"]["unresolved"] == baseline_stats["unresolved"] == len(store.list_unresolved()),
        str(d["stats"]),
    )
    check(
        "warn sections reflect catalog + loose source residue",
        d["summary"]["warn"] == sum(1 for s in d["sections"] if s["level"] == "warn"),
        str(d["summary"]),
    )
    check(
        "loose queue book count honest",
        d["summary"]["loose_books"] == expected_loose_dirs,
        str(d["summary"]["loose_books"]),
    )

    by_key = {s["key"]: s for s in d["sections"]}
    check(
        "catalog reports missing titles from live DB",
        by_key["catalog"]["count"] == expected_no_title
        and by_key["catalog"]["level"] == ("warn" if expected_no_title else "ok"),
    )
    check(
        "generations flattened (ok)",
        by_key["generations"]["level"] == "ok" and by_key["generations"]["count"] == len(expected_generations),
    )
    check("rollback 5/5 ready", by_key["rollback"]["count"] == 5 and by_key["rollback"]["level"] == "ok")
    check(
        "unresolved info matches DB",
        by_key["unresolved"]["count"] == baseline_stats["unresolved"] and by_key["unresolved"]["level"] == "info",
    )
    check(
        "loose queue reports source anomalies incl. split",
        by_key["loose_queue"]["count"] >= 1 and any("1454103 分裂" in s for s in by_key["loose_queue"]["samples"]),
        str(by_key["loose_queue"]["count"]),
    )
    check(
        "P4 ingest is reflected, not still 'pending'",
        "P4 已入库" in by_key["loose_queue"]["title"]
        and "P4 已把" in by_key["loose_queue"]["detail"]
        and "P4 待入库" not in by_key["loose_queue"]["detail"],
        by_key["loose_queue"]["title"],
    )
    # The queue root keeps living: directories packed by P4 and directories that
    # appeared later must never be conflated.
    packed = len(health._loose_ingest_state()["source_dirs"])
    check(
        "loose queue separates packed from new directories",
        packed <= d["summary"]["loose_books"]
        and by_key["loose_queue"]["books"] == d["summary"]["loose_books"]
        and (packed == d["summary"]["loose_books"] or "新目录待入库" in by_key["loose_queue"]["title"]),
        f"packed={packed} books={d['summary']['loose_books']}",
    )
    check(
        "duplicate pages informational never destructive-by-default",
        by_key["duplicate_pages"]["count"] == expected_dup_groups
        and by_key["duplicate_pages"]["level"] == "info"
        and "绝不自动删除" in by_key["duplicate_pages"]["detail"],
    )
    check(
        "transitions expose no executed shortcut from pending",
        "executed" not in d["transitions"]["pending"],
        str(d["transitions"]["pending"]),
    )

    # ---- collect() must not mutate anything ----
    after = store.stats()
    after_findings = store.list_findings()
    after_zips = sum(1 for _ in paths.ARCHIVE.rglob("*.zip")) + sum(1 for _ in paths.UNIDENTIFIED.rglob("*.zip"))
    check("collect() leaves stats unchanged", after == baseline_stats)
    check("collect() leaves findings unchanged", after_findings == baseline_findings)
    check("collect() leaves zip count unchanged", after_zips == baseline_zips, f"{baseline_zips} -> {after_zips}")

    # ---- CSV export ----
    r = c.get("/api/health/csv")
    body = r.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(body)))
    check(
        "health CSV 200 + BOM + header",
        r.status_code == 200
        and r.content.startswith(b"\xef\xbb\xbf")
        and rows[0] == ["section", "level", "count", "unit", "detail", "sample"],
        f"{r.status_code} rows={len(rows)}",
    )
    check("health CSV contains all six sections", {row[0] for row in rows[1:]} == set(keys), str(len(rows) - 1))

    # ---- findings state machine ----
    r = c.get("/api/findings")
    fd = r.json()
    check(
        "GET /api/findings returns transitions",
        r.status_code == 200 and set(fd["transitions"]["pending"]) == {"approved", "rejected"},
    )

    r = c.post("/api/findings", json={"kind": "p3_contract", "target": "测试项", "detail": "contract"})
    created = r.json()
    CREATED_IDS.append(created["id"])
    check(
        "POST /api/findings creates pending",
        r.status_code == 200 and created["created"] is True and created["status"] == "pending",
        str(created),
    )

    r = c.post("/api/findings", json={"kind": "p3_contract", "target": "测试项"})
    check(
        "duplicate live finding is not piled up",
        r.status_code == 200 and r.json()["created"] is False and r.json()["id"] == created["id"],
        str(r.json()),
    )

    r = c.post("/api/findings", json={"kind": "", "target": "x"})
    check("empty kind rejected 400", r.status_code == 400, str(r.status_code))

    fid = created["id"]
    r = c.post(f"/api/findings/{fid}/decide", json={"status": "approved"})
    check("pending -> approved", r.status_code == 200 and r.json()["status"] == "approved")
    check("approved persisted in DB", store.get_finding(fid)["status"] == "approved")

    r = c.post(f"/api/findings/{fid}/decide", json={"status": "approved"})
    check(
        "illegal repeat transition rejected 409",
        r.status_code == 409 and "cannot move" in r.json()["detail"],
        str(r.status_code),
    )

    r = c.post(f"/api/findings/{fid}/decide", json={"status": "executed"})
    check("executed blocked (P5 only)", r.status_code == 409 and "P5" in r.json()["detail"], r.json().get("detail", ""))
    check("executed did not persist", store.get_finding(fid)["status"] == "approved")

    r = c.post(f"/api/findings/{fid}/decide", json={"status": "rejected"})
    check("approved -> rejected", r.status_code == 200 and store.get_finding(fid)["status"] == "rejected")

    r = c.post(f"/api/findings/{fid}/decide", json={"status": "pending"})
    check("rejected -> pending (withdraw)", r.status_code == 200 and store.get_finding(fid)["status"] == "pending")

    r = c.post("/api/findings/99999999/decide", json={"status": "approved"})
    check("unknown finding 409", r.status_code == 409 and "not found" in r.json()["detail"])

    ok, msg = health.decide(fid, "rolled_back")
    check("health.decide rejects rolled_back from pending", not ok and "cannot move" in msg, msg)

    # ---- decision appears in the read-only report ----
    d2 = c.get("/api/health").json()
    check(
        "decision visible in report findings",
        any(f["id"] == fid for f in d2["findings"]) and d2["summary"]["findings"] >= 1,
        str(d2["summary"]["findings"]),
    )

    # ---- PIN gate covers health surfaces ----
    api.app.state.pin = "secret-pin"
    try:
        check("pin blocks /health 401", c.get("/health").status_code == 401)
        check("pin blocks /api/health 401", c.get("/api/health").status_code == 401)
        check("pin blocks /api/health/csv 401", c.get("/api/health/csv").status_code == 401)
        check(
            "pin blocks POST decision 401",
            c.post(f"/api/findings/{fid}/decide", json={"status": "approved"}).status_code == 401,
        )
        check("pin allows with query", c.get("/health?pin=secret-pin").status_code == 200)
        check("wrong pin 401", c.get("/health?pin=wrong").status_code == 401)
    finally:
        api.app.state.pin = None

    print(f"\nP3 API checks: {PASS}/{PASS + FAIL} passed")
finally:
    cleanup()

print("FINAL_FINDINGS", len(store.list_findings()))
raise SystemExit(1 if FAIL else 0)
