"""Name-based Aid resolution for the unidentified shelf (D slice).

861 zips sit under ``library/_unidentified/`` because the migration could not
prove an Aid for them.  Most of them *can* be identified: the archive records a
human-readable title, and the upstream search API answers that title.  This
module turns that into evidence and, only behind an explicit gate, into a
recorded decision.

What it deliberately does **not** do:

* It never moves, renames, deletes or rewrites a single archive byte.  Promotion
  stays where it already lives — :mod:`jmlib.governance`, with its own scoped
  token and rollback ledger.
* It never guesses silently.  A lookup produces candidates with scores; only a
  unit whose best candidate is unambiguous *and* above ``min_score`` becomes a
  ``chosen`` decision, and even that is written as a decision (reversible, in
  ``id_resolution``), not as a file operation.
* It never hits upstream concurrently.  Lookups run strictly one at a time and
  reuse :data:`jmlib.upstream.MIN_INTERVAL_SECONDS` spacing, because the API
  answers a burst with 403s and the library is not in a hurry.

The grouping rule comes from the real archive rather than from taste: 680 zips
live inside a per-book folder (``长篇/<书名>/001-….zip``) while 181 sit flat in
a generation root (``JM(Android)/1096890[…].zip``).  A generation root is *not* a
book — its folder name is literally ``JM`` or ``长篇`` — so a flat zip is its own
unit and a folder is one unit.
"""

from __future__ import annotations

import re
from pathlib import Path

from jmlib import idres, ingest, store, upstream
from jmlib.aid import HINT_RE

SCHEMA = "jmlib.resolve.v1"

# A filename that starts with an Aid is free, exact evidence: no lookup needed.
# The *run* of digits at the start is read whole, because a name like
# ``1234500 1234600两个车号的标题`` carries two contradictory numbers and that
# contradiction is itself the evidence — picking the first one would hide it.
LEADING_DIGITS_RE = re.compile(r"^[\s\d]*")
AID_TOKEN_RE = HINT_RE

# One contiguous generation root is the *shelf* the migration could not resolve;
# it is never the book itself.
GENERATION_ROOTS = ("长篇", "JM", "JM(Android)")

# A title made only of digits is not a title.  ``255`` searches for album 255 and
# comes back with album 255 at a nonsense score — treating that as evidence would
# hand a human a confident-looking wrong answer, so it goes to the review queue.
NUMERIC_ONLY_RE = re.compile(r"^[\d\s\W_]+$")

# A unit is only auto-accepted when the answer is unambiguous.  Anything less
# stays ``pending`` and waits for a human — that is the whole point of keeping a
# review queue instead of guessing.
DEFAULT_MIN_SCORE = 0.90
DEFAULT_MIN_MARGIN = 0.15


def _utc() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _segments(rel_path: str) -> list[str]:
    """``library/_unidentified/<generation>/<rest…>`` → ``[generation, rest…]``."""
    parts = Path(rel_path).as_posix().split("/")
    if "_unidentified" in parts:
        index = parts.index("_unidentified")
        return parts[index + 1 :]
    return parts


def generation_of(rel_path: str) -> str:
    parts = _segments(rel_path)
    return parts[0] if parts else ""


def leading_aids(name: str) -> list[str]:
    """Every Aid-looking number at the start of a filename, in order.

    ``1096890[CunnyFunky] ….zip`` → ``["1096890"]``.  A name may start with two
    numbers, and then the truthful answer is "I don't know which one is the Aid",
    so both are returned and the caller treats the unit as conflicting.
    """
    stem = Path(name).stem
    lead = LEADING_DIGITS_RE.match(stem)
    run = lead.group(0) if lead else ""
    return AID_TOKEN_RE.findall(run)


def leading_aid(name: str) -> str:
    """The single Aid at the start of a filename, or ``""`` when absent/ambiguous."""
    found = leading_aids(name)
    return found[0] if len(found) == 1 else ""


def _query_for(name: str) -> str:
    """The search string for a unit, or ``""`` when the name cannot be a title.

    Two names must never reach upstream as a query: one that cleans down to
    nothing, and a bare number.  Searching ``255`` returns album 255 at a score
    that means nothing, which is worse than no answer — the reviewer would be
    shown a confident wrong book.
    """
    cleaned = upstream.clean_title(name)
    if not cleaned or NUMERIC_ONLY_RE.match(cleaned):
        return ""
    return cleaned


def book_units(rows: list[dict] | None = None) -> list[dict]:
    """Group unresolved rows into *books*: one folder unit or one flat zip unit.

    The unit key is the folder that owns the zips, or the zip itself when it lies
    flat in a generation root.  That key is what a decision is recorded against,
    so it must be stable across runs — it is derived from the path alone, never
    from anything upstream returned.
    """
    import builtins

    if rows is None:
        rows = store.list_unresolved()

    units: dict[str, dict] = {}
    for row in rows:
        rel_path = row.get("rel_path") or ""
        if not rel_path:
            continue
        segments = _segments(rel_path)
        if len(segments) < 2:
            continue
        generation = segments[0]
        if len(segments) == 2:
            # Flat in the generation root: the zip is the book.
            key = rel_path
            name = Path(rel_path).stem
            kind = "flat"
        else:
            key = "library/_unidentified/" + "/".join(segments[:-1])
            name = segments[-2]
            kind = "folder"

        unit = units.get(key)
        if unit is None:
            unit = {
                "key": key,
                "kind": kind,
                "generation": generation,
                "name": name,
                "query": _query_for(name),
                "rel_paths": [],
                "zips": [],
                "leading_aids": [],
            }
            units[key] = unit
        unit["rel_paths"].append(rel_path)
        unit["zips"].append(Path(rel_path).stem)
        for aid in leading_aids(rel_path):
            if aid not in unit["leading_aids"]:
                unit["leading_aids"].append(aid)

    out = builtins.list(units.values())
    for unit in out:
        unit["zip_count"] = len(unit["rel_paths"])
        # A folder of chapters is one book; a flat zip is one book too.  The
        # distinction only matters when we later expand a decision back to zips.
        unit["aid_hint"] = unit["leading_aids"][0] if len(unit["leading_aids"]) == 1 else ""
        unit["conflicting_aids"] = len(unit["leading_aids"]) > 1
    out.sort(key=lambda u: (u["generation"], u["key"]))
    return out


def summary(units: list[dict] | None = None) -> dict:
    """Counts a reviewer (or a CLI) can act on, without touching the network."""

    units = book_units() if units is None else units
    by_generation: dict[str, int] = {}
    for unit in units:
        by_generation[unit["generation"]] = by_generation.get(unit["generation"], 0) + 1
    return {
        "units": len(units),
        "zips": sum(u["zip_count"] for u in units),
        "folders": sum(1 for u in units if u["kind"] == "folder"),
        "flat": sum(1 for u in units if u["kind"] == "flat"),
        "with_aid_hint": sum(1 for u in units if u["aid_hint"]),
        "conflicting_aids": sum(1 for u in units if u["conflicting_aids"]),
        "no_query": sum(1 for u in units if not u["query"]),
        "byGeneration": by_generation,
    }


def _unit_note(unit: dict, chosen: str, reason: str) -> str:
    """A reviewable sentence: what was picked and why, in the reviewer's terms."""
    bits = [f"{unit['zip_count']} 个包", f"世代 {unit['generation']}"]
    if unit["aid_hint"]:
        bits.append(f"文件名自带车号 {unit['aid_hint']}")
    if unit["conflicting_aids"]:
        bits.append("文件名里的车号互相矛盾")
    bits.append(reason)
    return "；".join(bits)


def resolve(
    *,
    limit: int | None = None,
    units: list[dict] | None = None,
    search=None,
    dry_run: bool = True,
    min_score: float = DEFAULT_MIN_SCORE,
    min_margin: float = DEFAULT_MIN_MARGIN,
    force: bool = False,
    skip_decided: bool = True,
) -> dict:
    """Look up every unit and record the evidence in ``id_resolution``.

    ``dry_run`` computes everything and reports it without writing a single row —
    the default, because the interesting number is "how many would be decided",
    not "how many got written".  ``search`` is injectable so contracts never touch
    the network.

    A unit whose filename already carries an Aid is *not* looked up: that number
    came from the downloader that produced the archive, which is stronger evidence
    than any title match, and it costs no request.
    """

    if search is None:
        search = upstream.search
    units = book_units() if units is None else units

    decided = idres.decided() if (skip_decided and not force) else {}
    picked: list[dict] = []
    considered = 0

    for unit in units:
        if limit is not None and considered >= limit:
            break
        considered += 1

        if skip_decided and unit["key"] in decided:
            picked.append(
                {
                    "key": unit["key"],
                    "name": unit["name"],
                    "generation": unit["generation"],
                    "status": "skipped",
                    "chosen": decided[unit["key"]],
                    "score": 0.0,
                    "margin": 0.0,
                    "query": "",
                    "candidates": [],
                    "failure": None,
                    "note": "已有裁决，未重新查询",
                }
            )
            continue

        # Free evidence first: the filename itself.
        if unit["aid_hint"] and not unit["conflicting_aids"]:
            entry = {
                "key": unit["key"],
                "name": unit["name"],
                "generation": unit["generation"],
                "status": "from_filename",
                "chosen": unit["aid_hint"],
                "score": 1.0,
                "margin": 1.0,
                "query": "",
                "candidates": [],
                "failure": None,
                "note": _unit_note(unit, unit["aid_hint"], "直接采用文件名里的车号，未联网"),
            }
            if not dry_run:
                idres.record(unit["key"], query="", candidates=[], note=entry["note"])
                idres.set_choice(unit["key"], unit["aid_hint"], note=entry["note"])
            picked.append(entry)
            continue

        if not unit["query"]:
            entry = {
                "key": unit["key"],
                "name": unit["name"],
                "generation": unit["generation"],
                "status": "no_query",
                "chosen": "",
                "score": 0.0,
                "query": "",
                "candidates": [],
                "note": "目录名里没有可用作搜索的标题，需人工指定车号",
            }
            if not dry_run:
                idres.skip(unit["key"], reason=entry["note"])
            picked.append(entry)
            continue

        result = search(unit["query"], force=force)
        candidates = result.get("candidates") or []
        top = candidates[0] if candidates else None
        second = candidates[1] if len(candidates) > 1 else None
        top_score = float(top["score"]) if top else 0.0
        margin = top_score - float(second["score"]) if second else top_score
        # Read the Aid out of the candidate *once*, as a string.  Indexing ``top``
        # at each use site is what made every branch look like it might touch a
        # ``None``, and the Aid is the one field the rest of the block needs.
        top_aid = str(top["id"]) if top else ""

        accepted = bool(
            top
            and result.get("ok")
            and top_score >= min_score
            and margin >= min_margin
            and not unit["conflicting_aids"]
        )
        entry = {
            "key": unit["key"],
            "name": unit["name"],
            "generation": unit["generation"],
            "status": "chosen" if accepted else "pending",
            "chosen": top_aid if accepted else "",
            "score": round(top_score, 4),
            "margin": round(margin, 4),
            "query": unit["query"],
            "candidates": candidates[: upstream.DEFAULT_LIMIT],
            "failure": result.get("failure"),
            "note": _unit_note(
                unit,
                top_aid if accepted else "",
                (
                    f"上游最佳候选 {top_aid}（{top_score:.3f}，领先 {margin:.3f}）"
                    if top
                    else f"上游没有候选（{result.get('reason') or '无结果'}）"
                ),
            ),
        }
        if not dry_run:
            idres.record(unit["key"], query=unit["query"], candidates=candidates, note=entry["note"])
            if accepted:
                idres.set_choice(unit["key"], top_aid, note=entry["note"])
        picked.append(entry)

    return {
        "schemaVersion": SCHEMA,
        "kind": "resolve",
        "generatedAt": _utc(),
        "dryRun": bool(dry_run),
        "minScore": min_score,
        "minMargin": min_margin,
        "considered": considered,
        "summary": _tally(picked),
        "items": picked,
    }


def _tally(items: list[dict]) -> dict:
    by_status: dict[str, int] = {}
    for item in items:
        by_status[item["status"]] = by_status.get(item["status"], 0) + 1
    return {
        "total": len(items),
        "byStatus": by_status,
        "decided": by_status.get("chosen", 0) + by_status.get("from_filename", 0),
    }


def fill_tags(
    *,
    limit: int | None = None,
    album=None,
    dry_run: bool = True,
    aids: list[str] | None = None,
) -> dict:
    """Backfill ``tags``/``author`` from the album detail endpoint.

    Two groups benefit and both are covered: books already in the library whose
    tags are empty (all 185 were, because the downloader never wrote them), and
    books that appear after a promotion.  Writing is additive — see
    :func:`jmlib.store.set_book_tags` — so a second run cannot erase a tag the
    user or an earlier run already had.

    The result is recorded in **both** places: the catalog row (which the API
    reads) and the ingest sidecar (which ``jmlib index`` rebuilds the catalog
    from).  Writing only the row would look right until the next rebuild, which
    would silently drop every tag learned here.

    Requests are serial and spaced by the same throttle as every other upstream
    call.
    """
    if album is None:
        album = upstream.album_info

    targets = list(aids) if aids else [b["aid"] for b in store.books_missing_tags()]
    if limit is not None:
        targets = targets[:limit]

    out: list[dict] = []
    for aid in targets:
        info = album(aid)
        tags = info.get("tags") or []
        authors = info.get("authors") or []
        entry: dict = {
            "aid": aid,
            "ok": bool(info.get("ok")),
            "title": info.get("title") or "",
            "tags": tags,
            "authors": authors,
            "error": info.get("error") or "",
            "written": False,
        }
        if info.get("ok") and (tags or authors):
            if not dry_run:
                author = authors[0] if authors else ""
                updated = store.set_book_tags(aid, tags, author)
                if updated is not None:
                    ingest.merge_meta(
                        aid,
                        {
                            "tags": updated.get("tags") or [],
                            "author": updated.get("author") or "",
                        },
                    )
                entry["written"] = updated is not None
        out.append(entry)

    return {
        "schemaVersion": SCHEMA,
        "kind": "tags",
        "generatedAt": _utc(),
        "dryRun": bool(dry_run),
        "requested": len(targets),
        "summary": {
            "ok": sum(1 for e in out if e["ok"]),
            "withTags": sum(1 for e in out if e["tags"]),
            "written": sum(1 for e in out if e["written"]),
            "failed": sum(1 for e in out if not e["ok"]),
        },
        "items": out,
    }
