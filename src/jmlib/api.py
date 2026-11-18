"""Local web library: catalog + single-member zip reader + health + governance.

No uploads and no unpacking to disk: /api/page opens one chapter zip and streams
one image member. ``--lan`` is gated by a simple PIN supplied via the
``JMLIB_PIN`` environment variable; the default bind is loopback only.

Health endpoints are read-only except for the findings state machine, which only
records decisions.  P5 governance adds ONE reversible action (promote) that
requires an exact scoped confirmation token; every destructive class has only a
read-only Data Destruction Guard and no execution path at all.

The A-phase import panel lives in :mod:`jmlib.api_import` and is mounted below.
It is review-first: scanning a drop folder is read-only, and the only write path
requires the exact token issued by ``POST /api/import/plan``.  Its one network
call (upstream title search) happens only when a user asks for candidates for one
specific directory.
"""

from __future__ import annotations

import csv
import io
import mimetypes
import os
import secrets
import zipfile
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from jmlib import __version__, cover, governance, health, paths, store
from jmlib.aid import AID_RE

app = FastAPI(title="jmlib", version=__version__)
app.state.pin = None

# Rebuildable cache only; the archive zips themselves are never served raw.
app.mount("/covers", StaticFiles(directory=str(paths.COVERS), check_dir=False), name="covers")

# Front-end scripts live under web/assets and are served as plain static files.
# Only that subdirectory is mounted: the pages themselves stay behind their own
# routes so nothing else in web/ becomes browsable.
app.mount(
    "/assets",
    StaticFiles(directory=str(paths.WEB / "assets"), check_dir=False),
    name="assets",
)

COVER_PREFIX = "library/covers/"


class MemoryIn(BaseModel):
    pindex: int
    image_index: int


class FindingIn(BaseModel):
    kind: str
    target: str
    detail: str = ""


class DecideIn(BaseModel):
    status: str


class PromoteIn(BaseModel):
    rel_path: str
    aid: str
    pindex: int | None = None
    title: str = ""


class PromoteApplyIn(BaseModel):
    plan: dict
    confirm: str


class BookPatchIn(BaseModel):
    """Only the user-editable columns — anything else is rejected by store."""

    local_title: str | None = None
    aliases: list[str] | None = None
    favorite: bool | None = None
    collection: str | None = None


class FavoriteIn(BaseModel):
    favorite: bool = True


class CollectionIn(BaseModel):
    name: str
    note: str = ""


class CollectionItemIn(BaseModel):
    aid: str


class ReorderIn(BaseModel):
    aids: list[str] = []


def _cover_url(cover_path: str | None) -> str | None:
    if not cover_path:
        return None
    p = Path(cover_path).as_posix()
    if not p.startswith(COVER_PREFIX):
        return None
    return "/covers/" + p[len(COVER_PREFIX) :]


@app.middleware("http")
async def pin_gate(request: Request, call_next):
    pin = getattr(app.state, "pin", None)
    if not pin:
        return await call_next(request)
    supplied = request.query_params.get("pin") or request.cookies.get("jmlib_pin") or request.headers.get("x-jmlib-pin")
    if not supplied or not secrets.compare_digest(str(supplied), pin):
        return JSONResponse(
            {"error": "PIN required", "hint": "open /?pin=<JMLIB_PIN>"},
            status_code=401,
        )
    response = await call_next(request)
    if request.query_params.get("pin"):
        response.set_cookie("jmlib_pin", pin, httponly=True, samesite="lax")
    return response


@app.get("/")
def index() -> FileResponse:
    return FileResponse(paths.WEB / "index.html", media_type="text/html")


@app.get("/health")
def health_page() -> FileResponse:
    return FileResponse(paths.WEB / "health.html", media_type="text/html")


@app.get("/api/library")
def library(q: str = "", tag: str = "", collection: str = "", favorite: bool = False, sort: str = "aid") -> dict:
    # A bad sort is a client bug, not an empty shelf: refuse it plainly instead of
    # silently falling back to a different order than the one that was asked for.
    if sort not in store.BOOK_SORTS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown sort: {sort}; use one of {', '.join(store.BOOK_SORTS)}",
        )
    items = store.list_books(q=q, tag=tag, collection=collection, favorite=favorite, sort=sort)
    for item in items:
        item["cover_url"] = _cover_url(item.get("cover_path"))
    all_books = store.list_books()
    tags = sorted({t for b in all_books for t in (b.get("tags") or [])})
    shelves = sorted({b["collection"] for b in all_books if b.get("collection")})
    # ``display_title`` already falls back to the folder title, so a book the
    # user renamed by hand stops counting as "title missing" here.
    partial = sum(1 for b in items if not b.get("cover_path") or not b.get("display_title"))
    active = [
        name
        for name, on in (("搜索词", bool(q)), ("标签", bool(tag)), ("书架", bool(collection)), ("只看收藏", favorite))
        if on
    ]
    return {
        "items": items,
        "total": len(items),
        "partial": partial,
        "tags": tags,
        "shelves": shelves,
        "sort": sort,
        "sorts": list(store.BOOK_SORTS),
        "active_filters": active,
        # An empty shelf has three very different meanings, and a bare "0 本" tells
        # the user none of them.  Say which filter produced nothing, and what is
        # left to try.
        "empty_hint": (
            ""
            if items
            else (
                f"共 {len(all_books)} 本，但没有符合当前筛选的书（已启用：{'、'.join(active)}）。"
                "清掉筛选或换个搜索词再试。"
                if active
                else "书库里还没有书。先用「导入」扫描一个投放目录，或用「手工添加」加一本。"
            )
        ),
        "stats": store.stats(),
    }


@app.get("/api/books/{aid}")
def book_detail(aid: str) -> dict:
    book = store.get_book(aid)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    book["cover_url"] = _cover_url(book.get("cover_path"))
    for chapter in book["chapters"]:
        chapter["cover_url"] = _cover_url(chapter.get("cover_path"))
    return book


@app.patch("/api/books/{aid}")
def book_patch(aid: str, body: BookPatchIn) -> dict:
    """Edit the user-owned columns of one book (B.3).

    Only fields the user actually sent are touched, so a request that renames a
    book cannot silently clear its aliases.  A book that is not on disk is a 404
    rather than an upsert: metadata must never conjure a catalog row.
    """
    if not AID_RE.fullmatch(aid):
        raise HTTPException(status_code=400, detail="invalid aid")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="no editable field supplied")
    try:
        book = store.set_user_metadata(aid, fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    book["cover_url"] = _cover_url(book.get("cover_path"))
    for chapter in book["chapters"]:
        chapter["cover_url"] = _cover_url(chapter.get("cover_path"))
    return book


@app.post("/api/books/{aid}/favorite")
def book_favorite(aid: str, body: FavoriteIn) -> dict:
    """Toggle one book's favourite flag — no token: it edits a flag, not files."""
    if not AID_RE.fullmatch(aid):
        raise HTTPException(status_code=400, detail="invalid aid")
    book = store.set_favorite(aid, body.favorite)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    return {"aid": aid, "favorite": book["favorite"]}


@app.get("/read")
def read() -> FileResponse:
    return FileResponse(paths.WEB / "read.html", media_type="text/html")


# ---------------------------------------------------------------------------
# Collections / reading lists (B.4).  A list is a naming device: every endpoint
# here edits membership rows only.  No endpoint deletes a book or a byte, so a
# mis-click on "delete list" can never cost library data.
# ---------------------------------------------------------------------------


@app.get("/api/collections")
def collections_list() -> dict:
    return {"items": store.list_collections()}


@app.post("/api/collections")
def collections_create(body: CollectionIn) -> dict:
    try:
        row = store.create_collection(body.name, body.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "collection": row}


@app.get("/api/collections/{collection_id}")
def collections_detail(collection_id: int) -> dict:
    row = store.get_collection(collection_id)
    if row is None:
        raise HTTPException(status_code=404, detail="collection not found")
    for item in row["items"]:
        item["cover_url"] = _cover_url(item.get("cover_path"))
    return row


@app.post("/api/collections/{collection_id}/items")
def collections_add(collection_id: int, body: CollectionItemIn) -> dict:
    if not AID_RE.fullmatch(body.aid):
        raise HTTPException(status_code=400, detail="invalid aid")
    if store.get_book(body.aid) is None:
        raise HTTPException(status_code=404, detail="book not found")
    if not store.add_to_collection(collection_id, body.aid):
        raise HTTPException(status_code=404, detail="collection not found")
    return {"ok": True, "collection": store.get_collection(collection_id)}


@app.delete("/api/collections/{collection_id}/items/{aid}")
def collections_remove(collection_id: int, aid: str) -> dict:
    if not store.remove_from_collection(collection_id, aid):
        raise HTTPException(status_code=404, detail="book is not in this collection")
    return {"ok": True, "collection": store.get_collection(collection_id)}


@app.post("/api/collections/{collection_id}/order")
def collections_reorder(collection_id: int, body: ReorderIn) -> dict:
    if store.get_collection(collection_id) is None:
        raise HTTPException(status_code=404, detail="collection not found")
    order = store.reorder_collection(collection_id, body.aids)
    return {"ok": True, "order": order, "collection": store.get_collection(collection_id)}


@app.delete("/api/collections/{collection_id}")
def collections_delete(collection_id: int) -> dict:
    """Delete one list.  Its member books stay exactly where they are."""
    if not store.delete_collection(collection_id):
        raise HTTPException(status_code=404, detail="collection not found")
    return {"ok": True, "deleted": collection_id}


@app.get("/api/books/{aid}/collections")
def book_collections(aid: str) -> dict:
    if not AID_RE.fullmatch(aid):
        raise HTTPException(status_code=400, detail="invalid aid")
    if store.get_book(aid) is None:
        raise HTTPException(status_code=404, detail="book not found")
    return {"aid": aid, "items": store.list_collections_for_book(aid)}


@app.get("/api/page")
def page(aid: str, pindex: int, i: int) -> Response:
    """Stream exactly one image member from one chapter zip; never unpack it."""
    if not AID_RE.fullmatch(aid) or pindex < 1 or i < 1:
        raise HTTPException(status_code=400, detail="invalid aid/pindex/image index")
    chapter = store.get_chapter(aid, pindex)
    if chapter is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    if i > chapter["page_count"]:
        raise HTTPException(status_code=404, detail="image index out of range")

    rel_path = str(chapter["zip_path"])
    if Path(rel_path).is_absolute() or ".." in Path(rel_path).parts:
        raise HTTPException(status_code=400, detail="invalid zip path")
    zip_path = paths.ROOT / Path(rel_path)
    if not zip_path.is_file():
        raise HTTPException(status_code=404, detail="zip missing on disk")

    try:
        with zipfile.ZipFile(zip_path) as zf:
            members = cover.image_members(zf)
            if i > len(members):
                raise HTTPException(status_code=404, detail="image index out of range")
            name = members[i - 1]
            data = zf.read(name)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=500, detail="chapter zip is unreadable")

    media_type, _ = mimetypes.guess_type(name)
    return Response(
        content=data,
        media_type=media_type or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=3600", "X-Image-Member": name},
    )


@app.get("/api/memory/{aid}")
def memory_get(aid: str) -> dict:
    if not AID_RE.fullmatch(aid):
        raise HTTPException(status_code=400, detail="invalid aid")
    if store.get_book(aid) is None:
        raise HTTPException(status_code=404, detail="book not found")
    m = store.get_memory(aid)
    if m is None:
        return {"aid": aid, "has_memory": False, "pindex": None, "image_index": None}
    first = store.first_chapter(aid)
    out = {"aid": aid, "has_memory": True, "pindex": m["pindex"], "image_index": m["image_index"]}
    if first is not None:
        out["default_pindex"] = first["pindex"]
        out["chapter_count"] = len(store.list_chapters(aid))
        out["first_page_count"] = first["page_count"]
    return out


@app.put("/api/memory/{aid}")
def memory_put(aid: str, body: MemoryIn) -> dict:
    if not AID_RE.fullmatch(aid):
        raise HTTPException(status_code=400, detail="invalid aid")
    chapter = store.get_chapter(aid, body.pindex)
    if chapter is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    if body.image_index < 1 or body.image_index > chapter["page_count"]:
        raise HTTPException(status_code=400, detail="image index out of range")
    store.set_memory(aid, body.pindex, body.image_index)
    return {"aid": aid, "pindex": body.pindex, "image_index": body.image_index}


@app.get("/api/unresolved")
def unresolved() -> dict:
    items = store.list_unresolved()
    return {"items": items, "total": len(items)}


# ---------------------------------------------------------------------------
# Storage health (P3): read-only report + findings decisions (no execution)
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health_report() -> dict:
    return health.collect()


@app.get("/api/health/csv")
def health_csv() -> Response:
    data = health.collect()
    buf = io.StringIO()
    csv.writer(buf).writerows(health._csv_rows(data))
    return Response(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="jmlib-health.csv"'},
    )


@app.get("/api/findings")
def findings_list(status: str | None = None) -> dict:
    items = store.list_findings(status)
    return {
        "items": items,
        "total": len(items),
        "transitions": {k: sorted(v) for k, v in health.ALLOWED_TRANSITIONS.items()},
    }


@app.post("/api/findings")
def findings_create(body: FindingIn) -> dict:
    kind, target = body.kind.strip(), body.target.strip()
    if not kind or not target:
        raise HTTPException(status_code=400, detail="kind and target are required")
    # One live decision record per (kind,target); never pile up duplicates.
    for f in store.list_findings():
        if f["kind"] == kind and f["target"] == target and f["status"] in ("pending", "approved"):
            return {"id": f["id"], "status": f["status"], "created": False}
    fid = store.add_finding(kind, target, body.detail.strip())
    return {"id": fid, "status": "pending", "created": True}


@app.post("/api/findings/{fid}/decide")
def findings_decide(fid: int, body: DecideIn) -> dict:
    ok, message = health.decide(fid, body.status)
    if not ok:
        raise HTTPException(status_code=409, detail=message)
    return {"id": fid, "status": body.status, "message": message}


# ---------------------------------------------------------------------------
# P5 governance: one reversible action + read-only guards only.
# All endpoints sit behind pin_gate; destructive execution has NO route.
# ---------------------------------------------------------------------------


@app.get("/governance")
def governance_page() -> FileResponse:
    return FileResponse(paths.WEB / "governance.html", media_type="text/html")


@app.get("/api/governance")
def governance_status() -> dict:
    return governance.status()


@app.get("/api/governance/manifest")
def governance_manifest(kind: str) -> dict:
    if kind not in governance.DESTRUCTIVE_KINDS:
        raise HTTPException(status_code=400, detail=f"unknown destructive kind: {kind}")
    out = governance.change_manifest(kind)
    return out  # a non-readable root is reported in out["error"], still read-only


@app.post("/api/governance/destructive")
def governance_destructive(body: FindingIn) -> dict:
    """Hard stop: any attempt to execute a destructive class is refused (200 + guard)."""
    return governance.refuse_destructive(body.kind.strip(), body.target.strip(), body.detail.strip())


@app.post("/api/governance/promote/plan")
def governance_promote_plan(body: PromoteIn) -> dict:
    return governance.promote_plan(body.rel_path.strip(), body.aid.strip(), body.pindex, body.title.strip())


@app.post("/api/governance/promote/apply")
def governance_promote_apply(body: PromoteApplyIn) -> dict:
    return governance.promote_apply(body.plan, body.confirm)


@app.post("/api/governance/rollback/{action_id}")
def governance_promote_rollback(action_id: str) -> dict:
    return governance.promote_rollback(action_id.strip())


# ---------------------------------------------------------------------------
# A-phase import panel (review-first; single write path behind a scoped token).
# ---------------------------------------------------------------------------

from jmlib.api_import import router as import_router  # noqa: E402
from jmlib.api_prefs import router as prefs_router  # noqa: E402
from jmlib.api_resolve import router as resolve_router  # noqa: E402

app.include_router(import_router)
app.include_router(prefs_router)
app.include_router(resolve_router)


def serve(host: str = "127.0.0.1", port: int = 8765, lan: bool = False) -> int:
    paths.ensure_dirs()
    if lan:
        pin = os.environ.get("JMLIB_PIN", "").strip()
        if not pin:
            print("serve --lan requires JMLIB_PIN (simple PIN gate); refusing to bind 0.0.0.0 unprotected")
            return 2
        app.state.pin = pin
        host = "0.0.0.0"
        print(f"LAN mode: open http://<host>:{port}/?pin=<JMLIB_PIN>")
    else:
        app.state.pin = None
        print(f"jmlib serving on http://{host}:{port} (loopback only)")
    # Warm the read-only duplicate-page scan before accepting requests: the cold
    # central-directory pass over 1455 zips takes ~3 s, and first-page loads that
    # land inside that window can stall behind a throttled/hidden browser tab.
    # collect() is pure and read-only; a warm-up miss must never block startup.
    try:
        health.collect()
        print("health cache warmed")
    except Exception as exc:  # startup must survive a not-yet-indexed library
        print(f"health warm-up skipped: {exc}")
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0
