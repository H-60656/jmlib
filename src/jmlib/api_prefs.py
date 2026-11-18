"""Reading preferences and bookmarks endpoints (C.1).

Mounted by :mod:`jmlib.api` with ``app.include_router`` so the main module keeps
only app assembly and the older endpoints.  Every route sits behind the PIN gate
because the router is registered on the same app.

Nothing here writes bytes: a preference is a validated string, a bookmark is a
position.  ``memory`` is untouched by design — see :mod:`jmlib.prefs`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from jmlib import prefs, store
from jmlib.aid import AID_RE

router = APIRouter()


class PrefIn(BaseModel):
    direction: str | None = None
    layout: str | None = None
    fit: str | None = None
    fullscreen: bool | None = None


class BookmarkIn(BaseModel):
    aid: str
    pindex: int
    image_index: int
    note: str = ""


def _check_aid(aid: str) -> str:
    value = (aid or "").strip()
    if not AID_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="invalid aid")
    return value


def _require_book(aid: str) -> dict:
    book = store.get_book(aid)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    return book


@router.get("/api/prefs/summary")
def prefs_summary() -> dict:
    return prefs.summary()


@router.get("/api/prefs/{aid}")
def prefs_get(aid: str) -> dict:
    """Resolve one book (or ``_global``) against the global row and the defaults."""
    key = "" if aid in ("_global", "global") else _check_aid(aid)
    out = prefs.get(key)
    out["book_exists"] = True if key == "" else store.get_book(key) is not None
    return out


@router.put("/api/prefs/{aid}")
def prefs_put(aid: str, body: PrefIn) -> dict:
    key = "" if aid in ("_global", "global") else _check_aid(aid)
    fields = body.model_dump(exclude_unset=True)
    try:
        return prefs.put(key, fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/api/prefs/{aid}")
def prefs_clear(aid: str) -> dict:
    key = "" if aid in ("_global", "global") else _check_aid(aid)
    prefs.clear(key)
    return prefs.get(key)


@router.get("/api/bookmarks")
def bookmarks_list(aid: str | None = None) -> dict:
    key = _check_aid(aid) if aid else None
    items = prefs.list_bookmarks(key)
    return {"items": items, "total": len(items), "aid": key}


@router.post("/api/bookmarks")
def bookmarks_create(body: BookmarkIn) -> dict:
    aid = _check_aid(body.aid)
    _require_book(aid)
    chapter = store.get_chapter(aid, body.pindex)
    if chapter is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    if body.image_index < 1 or body.image_index > chapter["page_count"]:
        raise HTTPException(status_code=400, detail="image index out of range")
    try:
        return prefs.add_bookmark(aid, body.pindex, body.image_index, body.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/api/bookmarks/{bookmark_id}")
def bookmarks_delete(bookmark_id: int) -> dict:
    if not prefs.delete_bookmark(bookmark_id):
        raise HTTPException(status_code=404, detail="bookmark not found")
    return {"id": bookmark_id, "deleted": True}
