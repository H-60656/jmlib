"""Read-only review surface for the D slice (name → Aid resolution).

The D slice records a *decision* per book unit into ``id_resolution`` and never
moves a byte.  This router is how a human inspects those decisions: it lists the
units with their upstream candidates and lets the reviewer confirm, override or
skip one.

Three boundaries are deliberate and must not drift:

* **Nothing here promotes.**  Moving a zip out of ``_unidentified`` is
  :mod:`jmlib.governance`'s job, behind its own exact scoped token and rollback
  ledger.  This module has no file-system call at all.
* **Nothing here calls upstream.**  Candidates are whatever the last resolve run
  recorded, so opening the page never spends a request or a rate-limit slot.
* **An override is a new decision, not an edit of the old one.**  ``set_choice``
  rewrites the same row, so the history lives in the governance ledger rather
  than here.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from jmlib import idres, paths, resolve
from jmlib.aid import AID_RE

router = APIRouter()


class DecideIn(BaseModel):
    key: str
    aid: str
    note: str = ""


class SkipIn(BaseModel):
    key: str
    reason: str = ""


def _unit_index() -> dict[str, dict]:
    return {u["key"]: u for u in resolve.book_units()}


def _archive_aids() -> set[str]:
    if not paths.ARCHIVE.is_dir():
        return set()
    return {p.name for p in paths.ARCHIVE.iterdir() if p.is_dir()}


def _merged(unit: dict, row: dict | None, decided: dict[str, str], archive: set[str]) -> dict:
    """One review row: the unit, what was recorded, and what a promote would do."""
    chosen = decided.get(unit["key"], "")
    if row is None:
        state = "unresolved"
    else:
        state = row["state"]
    return {
        "key": unit["key"],
        "name": unit["name"],
        "generation": unit["generation"],
        "kind": unit["kind"],
        "zipCount": unit["zip_count"],
        "zips": unit["rel_paths"],
        "query": (row or {}).get("query") or unit["query"],
        "state": state,
        "chosen": chosen,
        "note": (row or {}).get("note") or "",
        "candidates": (row or {}).get("candidates") or [],
        "aidHint": unit["aid_hint"],
        "conflictingAids": unit["conflicting_aids"],
        # A promote of this unit would create a new book or append chapters to one
        # that is already in the library.  Purely informational: no file moves here.
        "wouldCreateBook": bool(chosen) and chosen not in archive,
    }


@router.get("/resolve")
def resolve_page():
    from fastapi.responses import FileResponse

    return FileResponse(paths.WEB / "resolve.html")


@router.get("/api/resolve")
def resolve_overview(state: str = "", generation: str = "", q: str = "") -> dict:
    """Every unit with its recorded decision.  Read-only; no upstream call."""
    units = resolve.book_units()
    rows = {r["rel_path"]: r for r in idres.rows()}
    decided = idres.decided()
    archive = _archive_aids()

    merged = [_merged(u, rows.get(u["key"]), decided, archive) for u in units]

    needle = q.strip().lower()
    if needle:
        merged = [
            m for m in merged if needle in m["name"].lower() or needle in m["key"].lower() or needle == m["chosen"]
        ]
    if generation:
        merged = [m for m in merged if m["generation"] == generation]
    if state:
        merged = [m for m in merged if m["state"] == state]

    counts: dict[str, int] = {}
    for m in merged:
        counts[m["state"]] = counts.get(m["state"], 0) + 1

    all_units = [_merged(u, rows.get(u["key"]), decided, archive) for u in units]
    return {
        "total": len(merged),
        "units": len(all_units),
        "zips": sum(u["zip_count"] for u in units),
        "counts": counts,
        "byGeneration": resolve.summary(units)["byGeneration"],
        "promotable": sum(1 for u in all_units if u["chosen"]),
        "promotableZips": sum(u["zipCount"] for u in all_units if u["chosen"]),
        "states": list(idres.STATES),
        "note": "只读复核：本页只显示已记录的名字→车号裁决，不发请求、不移动文件。转正要走「受控治理」的精确确认串。",
        "items": merged,
    }


@router.post("/api/resolve/decide")
def resolve_decide(body: DecideIn) -> dict:
    """Record the Aid a human picked for one unit (overrides an earlier choice).

    A unit that was never searched has no row yet, so the row is created first:
    recording a decision must not depend on a lookup having happened, otherwise
    "I already know this one" would silently do nothing.
    """
    key = body.key.strip()
    aid = body.aid.strip()
    if not AID_RE.fullmatch(aid):
        raise HTTPException(status_code=400, detail="aid must be 1-7 digits")
    unit = _unit_index().get(key)
    if unit is None:
        raise HTTPException(status_code=404, detail="unknown unit")
    try:
        if idres.get(key) is None:
            idres.record(key, unit["query"], [])
        stored = idres.set_choice(key, aid, note=body.note.strip() or "人工指定")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not stored:
        raise HTTPException(status_code=409, detail="could not store the decision")
    return {
        "ok": True,
        "key": key,
        "aid": aid,
        "zipCount": unit["zip_count"],
        "message": f"已记录 {aid}；转正仍需在受控治理里逐项确认",
    }


@router.post("/api/resolve/skip")
def resolve_skip(body: SkipIn) -> dict:
    """Mark one unit as knowingly left unidentified."""
    key = body.key.strip()
    if key not in _unit_index():
        raise HTTPException(status_code=404, detail="unknown unit")
    idres.skip(key, reason=body.reason.strip() or "人工跳过")
    return {"ok": True, "key": key, "message": "已标记为跳过"}


@router.post("/api/resolve/clear")
def resolve_clear(body: SkipIn) -> dict:
    """Forget the decision while keeping the recorded candidates.

    The candidates cost an upstream request, so clearing a choice must not force
    a re-query; the row returns to ``pending`` and stays visible.
    """
    key = body.key.strip()
    if key not in _unit_index():
        raise HTTPException(status_code=404, detail="unknown unit")
    if not idres.reset(key):
        raise HTTPException(status_code=404, detail="no decision recorded for this unit")
    return {"ok": True, "key": key, "message": "已清除裁决（候选证据保留），下次识别会重新查询"}
