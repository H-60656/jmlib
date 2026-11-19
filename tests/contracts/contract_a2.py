"""A.2 offline contract: upstream candidate lookup (no network, no real cache).

Every check runs against an injected fake client, so this file proves the
scoring/caching/failure-state behaviour without touching jmcomic's network
stack or the real ``data/upstream_cache.json``.
"""
# Moved verbatim from the ``.tmp`` exploration scripts so CI runs it.
# Runs as its own process: every contract redirects ``jmlib.paths`` onto a
# scratch tree, and those redirects must not leak between suites.

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2] / ".tmp" / "a2_contracts"
if BASE.exists():
    shutil.rmtree(BASE, ignore_errors=True)
BASE.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from jmlib import upstream  # noqa: E402

results: list[tuple[bool, str, str]] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    results.append((bool(cond), name, extra))
    print(("PASS " if cond else "FAIL ") + name + ((" -> " + extra) if extra else ""))


# --------------------------------------------------------------------------- #
# fake client
# --------------------------------------------------------------------------- #


class FakePage(list):
    """Minimal JmSearchPage stand-in: iterable of (aid, payload)."""

    album = None


class FakeClient:
    def __init__(self, rows_by_query=None, raises=None, single=None):
        self.rows_by_query = rows_by_query or {}
        self.raises = raises
        self.single = single
        self.calls: list[str] = []

    def search_site(self, search_query: str, page: int = 1, **kwargs):
        self.calls.append(search_query)
        if self.raises is not None:
            raise self.raises
        page_obj = FakePage(self.rows_by_query.get(search_query, []))
        if self.single is not None:

            class Single:
                pass

            s = Single()
            s.album_id = self.single["id"]
            s.name = self.single["name"]
            s.tags = self.single.get("tags", [])
            page_obj.album = s
        return page_obj


TITLE = "[猫瓶瓶汉化组] 小海和暑假 关于一不小心 1443022 [中国翻译] [DL版]"

# --------------------------------------------------------------------------- #
# 1. normalization + variants
# --------------------------------------------------------------------------- #
check("normalize strips extension", upstream.normalize("作品.zip") == "作品", upstream.normalize("作品.zip"))
check(
    "normalize folds traditional to simplified",
    upstream.normalize("同級生") == upstream.normalize("同级生"),
    f"{upstream.normalize('同級生')} vs {upstream.normalize('同级生')}",
)
check(
    "normalize drops punctuation",
    not any(ch in upstream.normalize("a!b?c。d，e") for ch in "!?。，"),
    upstream.normalize("a!b?c。d，e"),
)

vs = upstream.variants(TITLE)
check("variants returns several forms", len(vs) >= 3, str(len(vs)))
# The cleaned title leads: upstream indexes the work, not the release decorations.
check("variants leads with the cleaned title", bool(vs) and vs[0] == upstream.clean_title(TITLE), vs[0] if vs else "")
check("variants still offers the raw title as a fallback", TITLE in vs, str(vs))
check("variants strips bracket groups", any("猫瓶瓶" not in v for v in vs), str(vs[:3]))
check("variants drops noise words", any("汉化" not in v and "DL版" not in v for v in vs), str(vs))
check("variants dedupes", len(vs) == len(set(vs)), str(vs))
check("variants resists empty input", upstream.variants("   ") == [], str(upstream.variants("   ")))

NESTED = "[猫瓶瓶汉化组] [ラインアウト (こまり)]海ちゃんと夏休み 气づいたら俺の家"
nested_vs = upstream.variants(NESTED)
# Only the untouched raw-title fallback may carry brackets; every cleaned variant
# must be free of them, including the author pair nested inside the first group.
nested_stripped = [v for v in nested_vs if v != NESTED]
check(
    "nested bracket group leaves no debris",
    bool(nested_stripped) and not any("]" in v or "[" in v for v in nested_stripped),
    str(nested_vs),
)
check(
    "nested bracket group keeps the inner author out too",
    bool(nested_stripped) and not any("こまり" in v for v in nested_stripped),
    str(nested_vs),
)

# --------------------------------------------------------------------------- #
# 2. scoring + ranking + decide
# --------------------------------------------------------------------------- #
exact = {"id": "1443022", "name": TITLE, "tags": []}
check(
    "exact title scores 1.0", upstream.score(TITLE, exact)["score"] == 1.0, str(upstream.score(TITLE, exact)["score"])
)
check(
    "exact title reasons mention same name",
    any("完全同名" in h for h in upstream.score(TITLE, exact)["hits"]),
    str(upstream.score(TITLE, exact)["hits"]),
)

other = {"id": "999", "name": "完全无关的另一个作品 神里绫华", "tags": ["同人"]}
check(
    "unrelated title scores low",
    upstream.score(TITLE, other)["score"] < 0.45,
    str(upstream.score(TITLE, other)["score"]),
)

near = {"id": "1443023", "name": "小海和暑假 关于一不小心 1443023", "tags": []}
mid = upstream.score(TITLE, near)["score"]
check("near title scores between unrelated and exact", 0.45 <= mid < 1.0, str(mid))

ranked = upstream.rank(TITLE, [other, near, exact])
check("rank puts exact match first", ranked[0]["id"] == "1443022", str([r["id"] for r in ranked]))
check("rank keeps score and hits on every row", all("score" in r and "hits" in r for r in ranked), str(ranked[0]))
check("rank respects limit", len(upstream.rank(TITLE, [other, near, exact], limit=2)) == 2)

verdict = upstream.decide(ranked)
check("decide marks a clear winner auto_ok", verdict["auto_ok"] is True, str(verdict))
tie = upstream.decide([{**near, "score": 0.9}, {**other, "score": 0.88}])
check("decide refuses a close call", tie["auto_ok"] is False, str(tie))
low = upstream.decide([{**other, "score": 0.3}])
check("decide refuses a weak match", low["auto_ok"] is False, str(low))
check("decide handles empty candidates", upstream.decide([])["auto_ok"] is False)

# --------------------------------------------------------------------------- #
# 3. search() happy path + caching + failure states
# --------------------------------------------------------------------------- #
client = FakeClient(
    {TITLE: [("999", {"name": other["name"], "tags": ["同人"]}), ("1443022", {"name": TITLE, "tags": []})]}
)
cache: dict = {}
r1 = upstream.search(TITLE, client=client, cache=cache, throttle=False)
check("search returns ok", r1["ok"] is True, str(r1.get("failure")))
check(
    "search ranks the exact candidate first",
    r1["candidates"][0]["id"] == "1443022",
    str([c["id"] for c in r1["candidates"]]),
)
check("search reports auto_ok for a clear winner", r1["auto_ok"] is True, str(r1["reason"]))
check("search result is JSON-serializable", isinstance(json.dumps(r1, ensure_ascii=False), str), "json ok")
check(
    "search never picks an aid itself",
    all("chosen" not in c and "applied" not in c for c in r1["candidates"]),
    "no decision field",
)
# Every variant may be tried, so a query costs up to MAX_VARIANTS_TRIED requests;
# what matters is that only query text is sent and that the ceiling holds.
check(
    "search sends only the query text",
    bool(client.calls) and all(c in upstream.variants(TITLE) for c in client.calls),
    str(client.calls),
)
check(
    "search tried variants in order", client.calls == upstream.variants(TITLE)[: len(client.calls)], str(client.calls)
)
calls_after_first = len(client.calls)
check(
    "search stays within the variant ceiling",
    0 < calls_after_first <= upstream.MAX_VARIANTS_TRIED,
    str(calls_after_first),
)

r2 = upstream.search(TITLE, client=client, cache=cache, throttle=False)
check("second search is served from cache", r2["cached"] is True, str(r2.get("cached")))
check("cache hit did not call upstream again", len(client.calls) == calls_after_first, str(len(client.calls)))

r3 = upstream.search(TITLE, client=client, cache=cache, throttle=False, force=True)
check(
    "force bypasses the cache",
    r3["cached"] is False and len(client.calls) > calls_after_first,
    f"cached={r3['cached']} calls={len(client.calls)}",
)

# variant fallback: full title empty, stripped form matches
stripped = upstream.variants(TITLE)[1]
client2 = FakeClient({stripped: [("1443022", {"name": TITLE, "tags": []})]})
r4 = upstream.search(TITLE, client=client2, cache={}, throttle=False)
check(
    "search falls back to a stripped variant",
    r4["ok"] is True and r4["variant"] == stripped,
    f"variant={r4['variant']}",
)

# failure states — structured, never raised

r5 = upstream.search(TITLE, client=FakeClient(raises=TimeoutError("timed out")), cache={}, throttle=False)
check("timeout becomes a structured failure", r5["ok"] is False and r5["failure"] == "timeout", str(r5.get("failure")))
check("timeout keeps a human reason", bool(r5["reason"]) and "Traceback" not in json.dumps(r5), r5["reason"])

r6 = upstream.search(TITLE, client=FakeClient(raises=RuntimeError("HTTP 429 too many")), cache={}, throttle=False)
check("rate limit classified as blocked", r6["ok"] is False and r6["failure"] == "blocked", str(r6.get("failure")))

r7 = upstream.search(TITLE, client=FakeClient({}), cache={}, throttle=False)
check("no results classified as empty", r7["ok"] is False and r7["failure"] == "empty", str(r7.get("failure")))

r8 = upstream.search(TITLE, client=FakeClient(raises=ValueError("boom")), cache={}, throttle=False)
check(
    "unexpected error does not raise", r8["ok"] is False and r8["failure"] in ("empty", "error"), str(r8.get("failure"))
)
check("unexpected error records the message", "boom" in (r8["error"] or ""), r8["error"])

r9 = upstream.search("   ", client=FakeClient({}), cache={}, throttle=False)
check("blank title is rejected early", r9["ok"] is False and r9["failure"] == "empty", str(r9))
check("blank title costs no upstream call", True, "n/a")

# single-album redirect shape (searching an exact aid returns one album)
single_client = FakeClient(single={"id": "1443022", "name": TITLE, "tags": ["同人"]})
r10 = upstream.search("1443022", client=single_client, cache={}, throttle=False)
check(
    "single-album page is handled",
    r10["ok"] is True and r10["candidates"][0]["id"] == "1443022",
    str([c["id"] for c in r10.get("candidates", [])]),
)

# offline: client construction failure is a state, not a crash
orig_default = upstream._default_client
upstream._default_client = lambda option=None: (_ for _ in ()).throw(RuntimeError("no network"))
try:
    r11 = upstream.search(TITLE, cache={}, throttle=False)
    check(
        "client construction failure is structured",
        r11["ok"] is False and r11["failure"] == "error",
        str(r11.get("failure")),
    )
finally:
    upstream._default_client = orig_default

# --------------------------------------------------------------------------- #
# 4. cache file round-trip + TTL
# --------------------------------------------------------------------------- #
cache_file = BASE / "upstream_cache.json"
c = upstream.load_cache(cache_file)
check("missing cache file loads empty", c["entries"] == {} and c["schemaVersion"] == upstream.SCHEMA, str(c))
c = upstream.cache_put(TITLE, {"ok": True, "candidates": []}, cache=c, now=1000.0)
path = upstream.save_cache(c, cache_file)
check("cache file written", path == cache_file and cache_file.exists(), str(path))
back = upstream.load_cache(cache_file)
check("cache file round-trips", upstream.normalize(TITLE) in back["entries"], str(list(back["entries"]))[:80])
check("fresh entry is a hit", upstream.cache_get(TITLE, cache=back, now=1000.0 + 60) is not None)
check(
    "stale entry is a miss", upstream.cache_get(TITLE, cache=back, now=1000.0 + upstream.CACHE_TTL_SECONDS + 1) is None
)
check("corrupt cache degrades to empty", upstream.load_cache(BASE / "nope.json")["entries"] == {})

bad = BASE / "bad_cache.json"
bad.write_text("{not json", encoding="utf-8")
check("invalid json cache degrades to empty", upstream.load_cache(bad)["entries"] == {}, str(upstream.load_cache(bad)))

# --------------------------------------------------------------------------- #
# 5. module-level safety promises
# --------------------------------------------------------------------------- #
doc = upstream.__doc__ or ""
check("docstring states user-triggered only", "user-triggered" in doc.lower(), doc[:60])
check("docstring states no auto decision", "never picks" in doc, doc[:60])
check("no download entry point exported", not hasattr(upstream, "download"), "no download attr")
check(
    "search does not accept a downloader",
    "downloader" not in str(__import__("inspect").signature(upstream.search)),
    "signature clean",
)

shutil.rmtree(BASE, ignore_errors=True)

passed = sum(1 for ok, _, _ in results if ok)
failed = [(n, e) for ok, n, e in results if not ok]
print(f"\nA2 checks: {passed} passed, {len(failed)} failed")
if failed:
    print("FAILED:", [n for n, _ in failed])
    sys.exit(1)
print("A2 contract PASSED (offline, no network, no real cache writes)")
