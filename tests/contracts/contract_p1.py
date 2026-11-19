"""In-process HTTP contract checks for P1 API (no listening socket needed)."""

# Moved verbatim from the ``.tmp`` exploration scripts so CI runs it.
# Runs as its own process: every contract redirects ``jmlib.paths`` onto a
# scratch tree, and those redirects must not leak between suites.
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from jmlib import api, store  # noqa: E402

# These P-series checks validate relationships in a *populated* catalog, so they
# only run where a real library was migrated and indexed.  A fresh clone has no
# ``library/`` (it is 25 GB of archives and gitignored), which makes "there is a
# book to sample" unanswerable rather than false — skip, do not fail.
if not store.list_books():
    print("SKIP P1 contract: empty catalog (no indexed library in this checkout)")
    raise SystemExit(0)

fails: list[str] = []
# Live baseline: the catalog legitimately grows (P0 migration -> P4 ingest),
# so never hard-code P1-era counts.  Each check validates a relationship.
S = store.stats()
ARCHIVE_ZIPS = sum(1 for _ in (ROOT / "library" / "archive").rglob("*.zip"))


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("PASS " if cond else "FAIL ") + name + ((" " + extra) if extra else ""))
    if not cond:
        fails.append(name)


with TestClient(api.app) as c:
    r = c.get("/")
    check("GET / 200 html", r.status_code == 200 and "jmlib" in r.text, str(r.status_code))

    r = c.get("/api/library")
    d = r.json()
    check("library 200", r.status_code == 200, str(r.status_code))
    check("library total matches DB", d["total"] == S["books"], str(d["total"]))
    check(
        "library stats match DB + physical archive",
        d["stats"]["chapters"] == S["chapters"] == ARCHIVE_ZIPS
        and d["stats"]["unresolved"] == S["unresolved"] == len(store.list_unresolved()),
        str(d["stats"]),
    )
    check("cover_url mapped", all(b["cover_url"] for b in d["items"]), str(sum(not b["cover_url"] for b in d["items"])))
    check(
        "partial count honest",
        d["partial"] == sum(not b["title"] or not b["cover_path"] for b in d["items"]),
        str(d["partial"]),
    )

    sample = store.list_books()[0]
    aid = sample["aid"]
    r = c.get("/api/library", params={"q": aid})
    check("search by Aid", r.status_code == 200 and r.json()["total"] == 1 and r.json()["items"][0]["aid"] == aid)
    r = c.get("/api/library", params={"q": "绝对不存在的标题zzz"})
    check("empty search result", r.status_code == 200 and r.json()["total"] == 0)

    r = c.get("/api/books/" + aid)
    b = r.json()
    check("book detail 200", r.status_code == 200 and b["aid"] == aid)
    check("book detail chapters", len(b["chapters"]) >= 1 and b["chapters"][0]["cover_url"] is not None)
    r = c.get("/api/books/999999999")
    check("missing book 404", r.status_code == 404, str(r.status_code))

    r = c.get("/api/unresolved")
    check(
        "unresolved endpoint matches DB",
        r.status_code == 200 and r.json()["total"] == S["unresolved"],
        str(r.json().get("total")),
    )

    first = store.first_chapter(aid)
    url = "/covers/" + first["cover_path"].split("library/covers/", 1)[1]
    r = c.get(url)
    check(
        "cover bytes served",
        r.status_code == 200 and len(r.content) > 100 and r.headers["content-type"].startswith("image/"),
        f"{r.status_code} {r.headers.get('content-type')} {len(r.content)}",
    )
    check(
        "cover bytes match cache",
        r.content == (ROOT / "library" / first["cover_path"].replace("library/", "", 1)).read_bytes(),
    )
    r = c.get("/covers/999999/1.jpg")
    check("missing cover 404", r.status_code == 404, str(r.status_code))

# PIN gate (LAN mode simulation)
api.app.state.pin = "s3cret"
try:
    with TestClient(api.app) as c:
        r = c.get("/api/library")
        check("lan pin blocks", r.status_code == 401, str(r.status_code))
        r = c.get("/api/library?pin=s3cret")
        check("lan pin allows", r.status_code == 200, str(r.status_code))
        r = c.get("/api/library?pin=wrong")
        check("wrong pin blocks", r.status_code == 401, str(r.status_code))
finally:
    api.app.state.pin = None

print("P1 API checks:", f"{6 + 7 + 2 + 3 - len(fails)}/{18} passed" if False else f"{18 - len(fails)}/18 passed")
raise SystemExit(1 if fails else 0)
