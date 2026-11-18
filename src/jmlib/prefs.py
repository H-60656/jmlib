"""Reading preferences and bookmarks — resolution, not storage.

:mod:`jmlib.store` owns the ``reading_prefs`` and ``bookmarks`` tables.  This
module is the *policy* layer on top of them: it knows the allowed values, the
built-in defaults, and the order in which the three sources win.

Three sources, one answer:

1. **per book** — what the reader chose while reading this specific book,
2. **global** — what the reader chose once and expects everywhere,
3. **built-in default** — what a fresh install does.

A missing key falls through to the next source; an empty string is *missing*,
because ``reading_prefs`` stores "not chosen" as a blank rather than as a magic
value.  ``fullscreen`` is the one tri-state column: ``-1`` means not chosen,
``0`` off, ``1`` on.

Deliberately **not** touched here:

* ``memory`` — the single "where I stopped" pointer.  Its meaning must not
  drift; a bookmark is an extra place, never a second memory row.
* bytes on disk — nothing in this module opens a zip, moves a file or writes a
  catalog row.  A preference is a string; a bookmark is a pair of integers.
"""

from __future__ import annotations

from typing import Any

from jmlib import store

DIRECTIONS = ("ltr", "rtl")
LAYOUTS = ("single", "double", "scroll")
FITS = ("height", "width", "original")

DEFAULTS = {
    "direction": "ltr",
    "layout": "single",
    "fit": "height",
    "fullscreen": False,
}

ALLOWED = {
    "direction": DIRECTIONS,
    "layout": LAYOUTS,
    "fit": FITS,
}


def _clean(aid: str | None) -> str:
    """The global row is the empty string; ``None`` and ``_global`` mean the same."""
    value = (aid or "").strip()
    return "" if value in ("_global", "global", "") else value


def validate(fields: dict) -> dict[str, Any]:
    """Reject unknown keys and out-of-vocabulary values before anything is stored.

    A typo like ``direction="rtl "`` or ``layout="spread"`` must be an error the
    caller sees, not a value the reader silently cannot use later.

    ``None`` means "clear this key" and is passed through as ``None`` for every
    field, ``fullscreen`` included — :func:`jmlib.store.set_pref_row` is what
    turns a cleared tri-state into ``-1``.
    """
    unknown = set(fields) - set(store.PREF_FIELDS)
    if unknown:
        raise ValueError(f"not a reading preference: {', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            out[key] = None
            continue
        if key == "fullscreen":
            out[key] = bool(value)
            continue
        text = str(value).strip()
        if not text:
            out[key] = None
            continue
        allowed = ALLOWED[key]
        if text not in allowed:
            raise ValueError(f"{key} must be one of {', '.join(allowed)}; got {text!r}")
        out[key] = text
    return out


def resolve(aid: str = "", rows: list[dict] | None = None) -> dict:
    """Merge per-book over global over built-in defaults.

    ``rows`` lets a caller that already loaded every preference row resolve many
    books from one query; otherwise the two rows are read here.
    """
    key = _clean(aid)
    if rows is None:
        rows = [r for r in (store.get_pref_row(key) if key else None, store.get_pref_row("")) if r is not None]
    by_aid = {r.get("aid", ""): r for r in rows}

    out: dict[str, Any] = dict(DEFAULTS)
    out["aid"] = key
    sources: dict[str, str] = {name: "default" for name in store.PREF_FIELDS}
    out["sources"] = sources

    for source_name, row in (("global", by_aid.get("")), ("book", by_aid.get(key) if key else None)):
        if not row:
            continue
        for field in store.PREF_FIELDS:
            raw = row.get(field)
            if field == "fullscreen":
                if raw is None or int(raw) < 0:
                    continue
                out[field] = bool(int(raw))
            else:
                text = (raw or "").strip()
                if not text:
                    continue
                out[field] = text
            sources[field] = source_name
    return out


def get(aid: str = "") -> dict:
    return resolve(aid)


def put(aid: str, fields: dict) -> dict:
    """Store validated values for one book (or the global row) and return the result."""
    key = _clean(aid)
    cleaned = validate(fields)
    if not cleaned:
        raise ValueError("no preference supplied")
    store.set_pref_row(key, cleaned)
    return resolve(key)


def clear(aid: str) -> None:
    """Forget this book's overrides; the global row and defaults still apply."""
    store.delete_pref_row(_clean(aid))


def summary() -> dict:
    """What the prefs UI needs: the global row plus how many books override it."""
    rows = store.list_pref_rows()
    overrides = [r for r in rows if r.get("aid")]
    return {
        "global": resolve("", rows=rows),
        "stored": len(rows),
        "overrides": len(overrides),
        "directions": list(DIRECTIONS),
        "layouts": list(LAYOUTS),
        "fits": list(FITS),
        "defaults": dict(DEFAULTS),
    }


# ---------------------------------------------------------------------------
# Bookmarks — extra positions a reader wants to find again.
# ---------------------------------------------------------------------------


def add_bookmark(aid: str, pindex: int, image_index: int, note: str = "") -> dict:
    if not (aid or "").strip():
        raise ValueError("bookmark needs an Aid")
    if pindex < 1:
        raise ValueError("pindex must be >= 1")
    if image_index < 1:
        raise ValueError("image_index must be >= 1")
    return store.add_bookmark((aid or "").strip(), int(pindex), int(image_index), note or "")


def list_bookmarks(aid: str | None = None) -> list[dict]:
    return store.list_bookmarks(aid.strip() if aid else None)


def delete_bookmark(bookmark_id: int) -> bool:
    return store.delete_bookmark(int(bookmark_id))
