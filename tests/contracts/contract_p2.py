"""In-process HTTP contract checks for P2 reader + memory (no listening socket)."""

# Moved verbatim from the ``.tmp`` exploration scripts so CI runs it.
# Runs as its own process: every contract redirects ``jmlib.paths`` onto a
# scratch tree, and those redirects must not leak between suites.
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from jmlib import api, cover, store  # noqa: E402

# Reader navigation is checked against a populated catalog; a fresh clone has no
# ``library/`` at all (25 GB, gitignored), so "find a multi-chapter book" has no
# answer — skip rather than fail.
if not store.list_books():
    print("SKIP P2 contract: empty catalog (no indexed library in this checkout)")
    raise SystemExit(0)

fails: list[str] = []
TOTAL = 0
original_memory: dict = {}


def check(name: str, cond: bool, extra: str = "") -> None:
    global TOTAL
    TOTAL += 1
    print(("PASS " if cond else "FAIL ") + name + ((" " + extra) if extra else ""))
    if not cond:
        fails.append(name)


with TestClient(api.app) as c:
    r = c.get("/read")
    check("GET /read 200 html", r.status_code == 200 and "阅读器" in r.text, str(r.status_code))

    # a book with >= 2 chapters and a first chapter with >= 2 pages (for i=2 / jump tests)
    books = store.list_books()
    target = None
    chapters: list[dict] = []
    for b in books:
        chs = store.list_chapters(b["aid"])
        if len(chs) >= 2 and chs[0]["page_count"] >= 2:
            target, chapters = b, chs
            break
    check("found multi-chapter book for navigation", target is not None)
    if target is None:
        raise SystemExit("no suitable multi-chapter book; cannot run P2 checks")
    aid = target["aid"]
    first = chapters[0]

    r = c.get("/api/page", params={"aid": aid, "pindex": first["pindex"], "i": 1})
    check(
        "page 200 image",
        r.status_code == 200 and r.headers["content-type"].startswith("image/"),
        f"{r.status_code} {r.headers.get('content-type')}",
    )
    with zipfile.ZipFile(ROOT / first["zip_path"]) as zf:
        members = cover.image_members(zf)
        expected = zf.read(members[0])
        expected2 = zf.read(members[1])
    check("page bytes are exact single zip member", r.content == expected, f"{len(r.content)} vs {len(expected)}")
    check(
        "page exposes member name", r.headers.get("x-image-member") == members[0], str(r.headers.get("x-image-member"))
    )
    check("page sets private cache", "private" in r.headers.get("cache-control", ""))

    r2 = c.get("/api/page", params={"aid": aid, "pindex": first["pindex"], "i": 2})
    check("page i=2 is second member", r2.status_code == 200 and r2.content == expected2 and r2.content != r.content)

    check("invalid i=0 -> 400", c.get("/api/page", params={"aid": aid, "pindex": 1, "i": 0}).status_code == 400)
    check("invalid aid -> 400", c.get("/api/page", params={"aid": "../etc", "pindex": 1, "i": 1}).status_code == 400)
    check("missing chapter -> 404", c.get("/api/page", params={"aid": aid, "pindex": 99999, "i": 1}).status_code == 404)
    check(
        "page out of range -> 404",
        c.get("/api/page", params={"aid": aid, "pindex": first["pindex"], "i": first["page_count"] + 1}).status_code
        == 404,
    )
    check(
        "missing book page -> 404",
        c.get("/api/page", params={"aid": "9999999", "pindex": 1, "i": 1}).status_code == 404,
    )

    # non-jpg cover chapter: suffix preserved, Content-Type matches, bytes exact
    special = None
    for b in books:
        for ch in store.list_chapters(b["aid"]):
            if Path(ch["cover_path"] or "").suffix.lower() in {".webp", ".gif"}:
                special = (b["aid"], ch)
                break
        if special:
            break
    check("non-jpg first page chapter exists", special is not None)
    if special:
        sa, sch = special
        rs = c.get("/api/page", params={"aid": sa, "pindex": sch["pindex"], "i": 1})
        suffix = Path(sch["cover_path"]).suffix.lower()
        ctype = rs.headers.get("content-type", "")
        ok_type = (suffix == ".webp" and "webp" in ctype) or (suffix == ".gif" and "gif" in ctype)
        with zipfile.ZipFile(ROOT / sch["zip_path"]) as zf:
            ok_bytes = rs.content == zf.read(cover.image_members(zf)[0])
        check(
            f"non-jpg page serves {suffix} bytes/type",
            rs.status_code == 200 and ok_type and ok_bytes,
            f"{rs.status_code} {ctype}",
        )

    # memory: empty -> validate -> persist -> read back -> catalog badge -> overwrite
    original_memory = c.get("/api/memory/" + aid).json()
    check("GET memory 200 (may be empty)", "has_memory" in original_memory)
    check("invalid memory aid -> 400", c.get("/api/memory/abc").status_code == 400)
    check("missing book memory -> 404", c.get("/api/memory/9999999").status_code == 404)

    r = c.put("/api/memory/" + aid, json={"pindex": first["pindex"], "image_index": 2})
    check(
        "PUT memory 200",
        r.status_code == 200 and r.json() == {"aid": aid, "pindex": first["pindex"], "image_index": 2},
        str(r.status_code),
    )
    check(
        "PUT memory bad chapter -> 404",
        c.put("/api/memory/" + aid, json={"pindex": 99999, "image_index": 1}).status_code == 404,
    )
    check(
        "PUT memory bad page -> 400",
        c.put(
            "/api/memory/" + aid, json={"pindex": first["pindex"], "image_index": first["page_count"] + 5}
        ).status_code
        == 400,
    )
    m = c.get("/api/memory/" + aid).json()
    check("memory persisted", m["has_memory"] and m["pindex"] == first["pindex"] and m["image_index"] == 2, str(m))
    check("store.get_memory agrees", (store.get_memory(aid) or {}).get("image_index") == 2)
    detail = c.get("/api/books/" + aid).json()
    check("book detail carries memory", (detail.get("memory") or {}).get("image_index") == 2)
    lib = c.get("/api/library", params={"q": aid}).json()
    check(
        "cover wall shows resume badge data",
        lib["total"] >= 1
        and any(
            x["aid"] == aid and x["resume_pindex"] == first["pindex"] and x["resume_image"] == 2 for x in lib["items"]
        ),
    )

    # re-index preserves memory: run the REAL store.replace_catalog against a scratch
    # sqlite file whose memory row has been seeded, then reopen it to verify survival.
    import sqlite3
    from unittest import mock

    scratch_dir = ROOT / ".tmp" / "p2_contracts"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    dbfile = scratch_dir / "catalog.sqlite"
    if dbfile.exists():
        dbfile.unlink()
    setup = sqlite3.connect(dbfile)
    setup.executescript(store._DDL)
    setup.execute(
        "INSERT INTO memory(aid,pindex,image_index,updated_at) VALUES(?,?,?,?)", (aid, first["pindex"], 2, "t0")
    )
    setup.commit()
    setup.close()

    def fake_connect():
        conn = sqlite3.connect(dbfile)
        conn.row_factory = sqlite3.Row
        return conn

    with mock.patch.object(store, "connect", fake_connect):
        store.replace_catalog([], [], [])
    verify = sqlite3.connect(dbfile)
    kept = verify.execute("SELECT image_index FROM memory WHERE aid = ?", (aid,)).fetchone()
    verify.close()
    dbfile.unlink()
    check("replace_catalog keeps memory rows", kept is not None and kept[0] == 2)

    r = c.put("/api/memory/" + aid, json={"pindex": first["pindex"], "image_index": 1})
    check("memory updated in place", r.status_code == 200 and c.get("/api/memory/" + aid).json()["image_index"] == 1)

# PIN gate also covers reader endpoints
api.app.state.pin = "reader-pin"
try:
    with TestClient(api.app) as c:
        check("pin blocks /read", c.get("/read").status_code == 401)
        check(
            "pin blocks /api/page",
            c.get("/api/page", params={"aid": "1456762", "pindex": 1, "i": 1}).status_code == 401,
        )
        check("pin allows /read via query", c.get("/read?pin=reader-pin").status_code == 200)
finally:
    api.app.state.pin = None

# never leave artificial reading progress in the real DB
conn = store.connect()
if original_memory.get("has_memory"):
    conn.execute(
        "INSERT OR REPLACE INTO memory(aid,pindex,image_index,updated_at) VALUES(?,?,?,?)",
        (aid, original_memory["pindex"], original_memory["image_index"], store._utc()),
    )
else:
    conn.execute("DELETE FROM memory WHERE aid = ?", (aid,))
conn.commit()
conn.close()

print("P2 API checks:", f"{TOTAL - len(fails)}/{TOTAL} passed")
raise SystemExit(1 if fails else 0)
