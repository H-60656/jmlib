"""Import panel API: scan a drop folder, resolve Aids, review, then execute.

Everything here is *review-first*.  The read-only endpoints (scan, manifest,
candidates) never write a library file and never write the catalog; the only
writes happen in ``POST /api/import/apply`` after the exact server-issued token
from ``POST /api/import/plan`` is echoed back.

Network access is confined to ``POST /api/import/candidates``: it is the one
endpoint that calls upstream search, and it does so only because a human pressed
a button for one specific directory.  Nothing here runs at startup or on a timer.

The router is mounted by :mod:`jmlib.api`, so every route sits behind the same
PIN gate as the rest of the app.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from jmlib import idres, import_apply, import_scan, paths, upstream

router = APIRouter()


class ScanIn(BaseModel):
    root: str
    rescan: bool = True


class ManifestIn(BaseModel):
    root: str = ""


class CandidatesIn(BaseModel):
    rel_path: str
    query: str = ""
    limit: int = upstream.DEFAULT_LIMIT
    force: bool = False


class ResolveIn(BaseModel):
    rel_path: str
    aid: str | None = None
    state: str = "pending"
    note: str = ""
    query: str = ""
    candidates: list[dict] | None = None


class PlanIn(BaseModel):
    selected: list[str] | None = None
    ids: list[str] | None = None
    root: str = ""


class ApplyIn(BaseModel):
    plan: dict
    confirm: str


class RollbackIn(BaseModel):
    confirm: str


class AddPlanIn(BaseModel):
    local_path: str
    aid: str
    title: str = ""


class SingleApplyIn(BaseModel):
    plan: dict
    confirm: str


class SingleRollbackIn(BaseModel):
    confirm: str
    aid: str = ""


# ---------------------------------------------------------------------------
# Root whitelist: an import root must be one the user put in the environment or
# the documented drop folder.  This blocks "read any path on disk" through the
# scan endpoint.
# ---------------------------------------------------------------------------


def allowed_roots() -> list[Path]:
    raw = os.environ.get("JMLIB_IMPORT_ROOTS", "")
    roots: list[Path] = []
    for chunk in raw.split(os.pathsep):
        chunk = chunk.strip().strip('"')
        if chunk:
            roots.append(Path(chunk))
    roots.append(paths.INCOMING)
    out: list[Path] = []
    for root in roots:
        try:
            out.append(root.resolve())
        except OSError:
            continue
    return out


def resolve_root(raw: str = "") -> Path:
    """Resolve a requested import root, refusing anything outside the whitelist."""
    roots = allowed_roots()
    if not raw.strip():
        for candidate in roots:
            if candidate.is_dir():
                return candidate
        raise HTTPException(status_code=400, detail="no import root configured")
    try:
        target = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=f"bad import root: {exc}") from exc
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"import root is not a directory: {target}")
    for root in roots:
        if target == root or root in target.parents:
            return target
    raise HTTPException(
        status_code=403,
        detail=(
            "import root is outside the allowed roots; set JMLIB_IMPORT_ROOTS "
            f"(allowed: {', '.join(str(r) for r in roots)})"
        ),
    )


def _manifest_or_404() -> dict:
    manifest = import_scan.load_manifest()
    if manifest is None:
        raise HTTPException(status_code=404, detail="no import manifest yet — run a scan first")
    return manifest


# ---------------------------------------------------------------------------
# Read-only endpoints
# ---------------------------------------------------------------------------


@router.get("/import")
def import_page() -> FileResponse:
    return FileResponse(paths.WEB / "import.html", media_type="text/html")


@router.get("/api/import")
def import_overview() -> dict:
    manifest = import_scan.load_manifest()
    pending = import_apply.load_rollback()
    return {
        "roots": [str(r) for r in allowed_roots()],
        "manifest": None
        if manifest is None
        else {
            "root": manifest.get("root"),
            "requested_root": manifest.get("requested_root"),
            "createdAt": manifest.get("generated_at"),
            "summary": manifest.get("summary"),
        },
        "resolutions": idres.summary(),
        "rollback": None
        if pending is None
        else {
            "books": len(pending.get("books") or []),
            "createdAt": pending.get("createdAt"),
        },
    }


@router.post("/api/import/scan")
def import_scan_root(body: ScanIn) -> dict:
    root = resolve_root(body.root)
    manifest = import_scan.scan(root)
    if body.rescan:
        import_scan.write_manifest(manifest)
    return {
        "root": manifest["root"],
        "requested_root": manifest.get("requested_root"),
        "summary": manifest["summary"],
        "written": bool(body.rescan),
    }


@router.get("/api/import/manifest")
def import_manifest() -> dict:
    manifest = _manifest_or_404()
    chosen = idres.decided()
    resolved = idres.rows()
    by_rel = {r["rel_path"]: r for r in resolved}
    entries = []
    for entry in manifest.get("entries", []):
        row = by_rel.get(entry["rel_path"]) or {}
        entries.append(
            {
                **{
                    k: entry[k]
                    for k in (
                        "dir",
                        "rel_path",
                        "kind",
                        "aid",
                        "aid_source",
                        "aid_valid",
                        "chapter_count",
                        "image_count",
                        "flat_images",
                        "bytes",
                        "magazine_like",
                        "in_library",
                        "needs_id",
                        "needs_review",
                        "hints",
                    )
                    if k in entry
                },
                "effective_aid": chosen.get(entry["rel_path"]) or entry.get("aid") or "",
                "resolution_state": row.get("state", ""),
                "resolution_aid": row.get("chosen_aid") or "",
            }
        )
    return {
        "root": manifest.get("root"),
        "requested_root": manifest.get("requested_root"),
        "createdAt": manifest.get("generated_at"),
        "summary": manifest.get("summary"),
        "duplicate_groups": manifest.get("duplicate_groups") or {},
        "near_duplicates": manifest.get("near_duplicates") or [],
        "series_pairs": manifest.get("series") or [],
        "entries": entries,
        "resolutions": idres.summary(),
    }


@router.post("/api/import/candidates")
def import_candidates(body: CandidatesIn) -> dict:
    """Upstream search for ONE directory — the only network call in the panel."""
    manifest = _manifest_or_404()
    entry = next((e for e in manifest.get("entries", []) if e["rel_path"] == body.rel_path.strip()), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="unknown directory in the manifest")
    query = body.query.strip() or entry["dir"]
    limit = max(1, min(int(body.limit or upstream.DEFAULT_LIMIT), 20))
    result = upstream.search(query, limit=limit, force=body.force)
    if result.get("candidates"):
        idres.record(
            entry["rel_path"],
            query=result.get("query") or query,
            candidates=result["candidates"],
            note=entry["dir"],
        )
    return {**result, "rel_path": entry["rel_path"], "dir": entry["dir"]}


@router.post("/api/import/resolve")
def import_resolve(body: ResolveIn) -> dict:
    rel = body.rel_path.strip()
    if not rel:
        raise HTTPException(status_code=400, detail="rel_path is required")
    state = (body.state or "pending").strip()
    if state not in idres.STATES:
        raise HTTPException(status_code=400, detail=f"unknown state: {state}")
    try:
        if state == "chosen":
            aid = (body.aid or "").strip()
            if not aid:
                raise HTTPException(status_code=400, detail="chosen state requires an Aid")
            if not import_scan.AID_RE.match(aid):
                raise HTTPException(status_code=400, detail="Aid must be 1-7 digits")
            idres.set_choice(rel, aid, body.note.strip())
        elif state == "skipped":
            idres.skip(rel, body.note.strip(), body.candidates)
        else:
            idres.record(rel, query=body.query, candidates=body.candidates, note=body.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"rel_path": rel, "state": state, "resolutions": idres.summary()}


@router.post("/api/import/plan")
def import_plan(body: PlanIn) -> dict:
    manifest = import_scan.load_manifest()
    if manifest is None and body.root.strip():
        manifest = import_scan.scan(resolve_root(body.root))
    if manifest is None:
        raise HTTPException(status_code=404, detail="no import manifest yet — run a scan first")
    chosen = idres.decided()
    selected = body.selected if body.selected is not None else body.ids
    plan = import_apply.build_plan(manifest, selected=selected, chosen=chosen)
    return plan


# ---------------------------------------------------------------------------
# The one write path
# ---------------------------------------------------------------------------


@router.post("/api/import/apply")
def import_execute(body: ApplyIn) -> dict:
    return import_apply.apply(body.plan, body.confirm)


@router.get("/api/import/rollback")
def import_rollback_preview() -> dict:
    payload = import_apply.load_rollback()
    if payload is None:
        return {"ok": True, "pending": None}
    return {
        "ok": True,
        "pending": {
            "createdAt": payload.get("createdAt"),
            "books": payload.get("books") or [],
            "skipped": payload.get("skipped") or [],
            "confirm_token": import_apply.rollback_token(payload),
        },
    }


@router.post("/api/import/rollback")
def import_rollback_execute(body: RollbackIn) -> dict:
    return import_apply.rollback(body.confirm)


# ---------------------------------------------------------------------------
# B.1 — hand-add one local directory
# ---------------------------------------------------------------------------
#
# The path is checked against the same whitelist as a scan root, because the
# server must never be talked into reading an arbitrary directory: a hand-add is
# "point at a folder I already dropped somewhere I allowed", not "read anywhere".


def _resolve_inside_roots(raw: str) -> Path:
    """Resolve one directory and require it to live under an allowed root."""
    text = (raw or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="local_path is required")
    try:
        target = Path(text).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=f"bad local_path: {exc}") from exc
    roots = allowed_roots()
    for root in roots:
        if target == root or root in target.parents:
            return target
    raise HTTPException(
        status_code=403,
        detail=(
            "local_path is outside the allowed roots; set JMLIB_IMPORT_ROOTS "
            f"(allowed: {', '.join(str(r) for r in roots)})"
        ),
    )


@router.get("/api/import/incoming")
def import_incoming() -> dict:
    """Read-only listing of the drop folder (B.2 shares this shape)."""
    root = paths.INCOMING
    empty_hint = "把文件放到 library/_incoming/ 后重新扫描"
    if not root.is_dir():
        # The folder is created on first use, so "not there yet" is not an error —
        # but it must still tell the user what to do instead of returning a bare 0.
        return {"root": str(root), "exists": False, "items": [], "count": 0, "hint": empty_hint}
    items = []
    skeletons = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if child.is_dir():
            # Empty generation folders are P0 rollback skeletons, not drop items:
            # listing them as "0 images" would look like a broken book.  The test is
            # "no file anywhere in the tree", not "no entries": a skeleton keeps its
            # original nested directory structure, so ``rglob('*')`` still finds
            # plenty of empty folders and would misreport it as a real book.
            if not any(p.is_file() for p in child.rglob("*")):
                skeletons.append(child.name)
                continue
            entry = import_scan.classify_entry(child)
            items.append(
                {
                    "name": child.name,
                    "kind": "dir",
                    "aid": entry["aid"],
                    "aid_valid": entry["aid_valid"],
                    "chapter_count": entry["chapter_count"],
                    "image_count": entry["image_count"],
                    "bytes": entry["bytes"],
                    "hints": import_scan.hints_for(entry),
                    "path": str(child),
                }
            )
        elif child.suffix.lower() == ".zip":
            items.append(
                {
                    "name": child.name,
                    "kind": "zip",
                    "aid": import_scan.aid_from_dirname(child.stem),
                    "bytes": child.stat().st_size,
                    "path": str(child),
                }
            )
    return {
        "root": str(root),
        "exists": True,
        "items": items,
        "count": len(items),
        "skeletons": skeletons,
        "hint": "" if items else empty_hint,
    }


@router.post("/api/import/add/plan")
def import_add_plan(body: AddPlanIn) -> dict:
    """Preview a hand-add.  Read-only: classifies the directory, writes nothing."""
    target = _resolve_inside_roots(body.local_path)
    return import_apply.plan_single(target, body.aid, body.title)


@router.post("/api/import/add/apply")
def import_add_apply(body: SingleApplyIn) -> dict:
    """The one hand-add write path — exact token, recomputed from the directory."""
    plan = body.plan or {}
    raw = str((plan.get("item") or {}).get("local_path") or "").strip()
    # Re-check the whitelist here too: the plan round-trips through the client, so
    # its path must be validated again before anything is written.
    _resolve_inside_roots(raw)
    return import_apply.apply_single(plan, body.confirm)


@router.get("/api/import/add/rollback")
def import_add_rollback_preview() -> dict:
    payload = import_apply.load_single_rollback()
    if payload is None:
        return {"ok": True, "pending": None}
    books = payload.get("books") or []
    return {
        "ok": True,
        "pending": {
            "createdAt": payload.get("createdAt"),
            "books": books,
            "confirm_token": import_apply.single_rollback_token(payload),
            "per_book": [
                {
                    "aid": b.get("aid"),
                    "title": b.get("title"),
                    "confirm_token": import_apply.single_rollback_token(payload, b.get("aid")),
                }
                for b in books
            ],
        },
    }


@router.post("/api/import/add/rollback")
def import_add_rollback_execute(body: SingleRollbackIn) -> dict:
    return import_apply.rollback_single(body.confirm, (body.aid or "").strip())
