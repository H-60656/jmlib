"""Album-id resolution records for import: which ``Aid`` a directory means.

This module owns **decisions and evidence only**.  It stores the query used, the
candidates upstream returned with their scores, and the Aid a human picked (or
the reason they skipped the directory).  It never moves bytes, never writes the
catalog, and never calls the network — :mod:`jmlib.import_scan` produces the
candidate list and the caller decides when to look one up.

Keeping the decision separate from the execution is deliberate: a review session
can be interrupted, resumed, or revised without any library file being touched,
and the actual import stays behind its own scoped confirmation.
"""

from __future__ import annotations

import builtins
import json

from jmlib import import_scan, store

STATES = ("pending", "chosen", "skipped")

#: Directory-level hints that a human must look at.  Re-exported from
#: :mod:`jmlib.import_scan` rather than redefined: the scan discovers them, so it
#: owns the strings, and anything that consumes hints must agree with the producer.
HINT_NO_AID = import_scan.HINT_NO_AID
HINT_INVALID_AID = import_scan.HINT_INVALID_AID
HINT_WEAK_SOURCE = import_scan.HINT_WEAK_SOURCE
HINT_SUSPECTED_SAME = import_scan.HINT_SUSPECTED_SAME
HINT_MAGAZINE = import_scan.HINT_MAGAZINE
HINT_COVER_ONLY = import_scan.HINT_COVER_ONLY
HINT_DUPLICATE_AID = import_scan.HINT_DUPLICATE_AID
HINT_ALREADY_IN_LIBRARY = import_scan.HINT_ALREADY_IN_LIBRARY
REVIEW_HINTS = import_scan.REVIEW_HINTS


def _candidates_json(candidates) -> str:
    rows = []
    for cand in candidates or []:
        if not isinstance(cand, dict):
            continue
        rows.append(
            {
                "id": str(cand.get("id") or ""),
                "name": str(cand.get("name") or cand.get("title") or ""),
                "score": round(float(cand.get("score") or 0.0), 4),
                "hits": builtins.list(cand.get("hits") or []),
            }
        )
    return json.dumps(rows, ensure_ascii=False)


def _row(
    rel_path: str,
    query: str = "",
    candidates=None,
    state: str = "pending",
    note: str = "",
    chosen_aid: str | None = None,
) -> dict:
    if state not in STATES:
        raise ValueError(f"unknown id-resolution state: {state}")
    return {
        "rel_path": rel_path,
        "query": query,
        "candidates_json": _candidates_json(candidates),
        "chosen_aid": str(chosen_aid) if chosen_aid else None,
        "state": state,
        "note": note,
    }


def record(rel_path: str, query: str = "", candidates=None, state: str = "pending", note: str = "") -> bool:
    """Store one directory's candidate set.  Returns True when the row is new.

    Re-recording the same ``rel_path`` refreshes the candidates and keeps
    ``created_at``, so re-running a lookup never piles up duplicate rows.
    """
    return store.upsert_id_resolution(_row(rel_path, query, candidates, state, note))


def set_choice(rel_path: str, aid: str, note: str = "") -> bool:
    """Record the Aid a human picked.  Returns False when the row does not exist."""
    aid = str(aid or "").strip()
    if not aid:
        raise ValueError("chosen Aid must not be empty")
    return store.set_id_resolution_choice(rel_path, aid, "chosen", note)


def skip(rel_path: str, reason: str = "", candidates=None) -> bool:
    """Mark a directory as deliberately not imported.

    Candidates are recorded when supplied because "skipped after looking" and
    "skipped without looking" are different decisions worth telling apart later.
    Returns False only when no row exists to update.
    """
    existing = store.get_id_resolution(rel_path)
    if existing is None:
        store.upsert_id_resolution(_row(rel_path, "", candidates, state="skipped", note=reason))
        return True
    if candidates is not None:
        store.upsert_id_resolution(
            {
                **existing,
                "candidates_json": _candidates_json(candidates),
            }
        )
    return store.set_id_resolution_choice(rel_path, None, "skipped", reason)


def get(rel_path: str) -> dict | None:
    row = store.get_id_resolution(rel_path)
    if row is None:
        return None
    return _decode(row)


def rows(state: str | None = None) -> builtins.list[dict]:
    """All resolutions (optionally filtered by state), decoded for callers.

    Named ``rows`` rather than ``list`` on purpose: a module-level ``list`` would
    shadow the builtin for every annotation in this file.
    """
    return [_decode(r) for r in _all_rows(state)]


def _all_rows(state: str | None = None) -> builtins.list[dict]:
    """Raw rows, tolerating a v1 database that predates the ``id_resolution`` table."""
    try:
        return store.list_id_resolutions(state)
    except Exception:  # sqlite3.OperationalError: no such table on an old library
        return []


def decided(state: str | None = None) -> dict[str, str]:
    """``{rel_path: chosen_aid}`` for every row that can actually be imported."""
    out: dict[str, str] = {}
    for row in rows(state=state or "chosen"):
        if row["state"] == "chosen" and row["chosen_aid"]:
            out[row["rel_path"]] = row["chosen_aid"]
    return out


def summary() -> dict:
    rows = _all_rows()
    counts = {name: 0 for name in STATES}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return {"total": len(rows), "byState": counts}


def clear(rel_path: str) -> None:
    """Drop one resolution row (used by tests and by a user who wants a redo)."""
    store.delete_id_resolution(rel_path)


def reset(rel_path: str, note: str = "") -> bool:
    """Forget the *decision*, keep the *evidence*.

    Clearing a choice and deleting the row are different acts.  The candidate
    set cost an upstream request; throwing it away because the reviewer changed
    their mind would force re-querying a title that was already searched, and
    would erase the reason the reviewer was looking at those candidates at all.
    So the row goes back to ``pending`` with its candidates intact, and the
    chosen Aid is dropped.
    """
    existing = store.get_id_resolution(rel_path)
    if existing is None:
        return False
    return store.set_id_resolution_choice(rel_path, None, "pending", note)


def _decode(row: dict) -> dict:
    try:
        candidates = json.loads(row.get("candidates_json") or "[]")
    except (TypeError, ValueError):
        candidates = []
    if not isinstance(candidates, builtins.list):
        candidates = []
    return {
        "rel_path": row["rel_path"],
        "query": row.get("query", ""),
        "candidates": candidates,
        "chosen_aid": row.get("chosen_aid"),
        "state": row.get("state", "pending"),
        "note": row.get("note", ""),
        "created_at": row.get("created_at", ""),
        "updated_at": row.get("updated_at", ""),
    }
