"""SQLite canonical owner of all logical state — the single write entry point.

Physical bytes live in ``library/``; every logical record (books, chapters,
reading memory, unresolved items, health findings) is owned here.  No other
module opens ``jmlib.db`` directly.

Path fields (``zip_path``, ``cover_path``, ``rel_path``) are **project-relative
posix paths**, e.g. ``library/archive/123456/1.zip``.  ``Aid`` is the only key;
titles are display/search data and are never used as an identity.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TypeVar

from jmlib import paths

SCHEMA_VERSION = "jmlib.db.v5"

T = TypeVar("T")

# Columns a human edits by hand (B.3).  They are user data, not derived data, so
# a catalog rebuild must carry them across instead of regenerating them — see
# ``replace_catalog``.
#
# ``collection`` is a *single shelf label*: one book, one shelf, no ordering.
# Named lists with many-to-many membership and an explicit order are the
# ``collections``/``collection_items`` tables (B.4).  The two are deliberately
# separate owners — a book is never on a shelf *because* it is in a list, and
# neither value is derived from the other.
USER_COLUMNS = ("local_title", "aliases", "favorite", "collection")

_DDL = """
CREATE TABLE IF NOT EXISTS books (
  aid         TEXT PRIMARY KEY,
  title       TEXT NOT NULL DEFAULT '',
  author      TEXT NOT NULL DEFAULT '',
  tags        TEXT NOT NULL DEFAULT '[]',
  generation  TEXT NOT NULL DEFAULT '',
  cover_path  TEXT,
  created_at  TEXT NOT NULL,
  local_title TEXT NOT NULL DEFAULT '',
  aliases     TEXT NOT NULL DEFAULT '[]',
  favorite    INTEGER NOT NULL DEFAULT 0,
  collection  TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS chapters (
  aid         TEXT NOT NULL,
  pindex      INTEGER NOT NULL,
  zip_path    TEXT NOT NULL,
  page_count  INTEGER NOT NULL DEFAULT 0,
  cover_path  TEXT,
  PRIMARY KEY (aid, pindex)
);
CREATE TABLE IF NOT EXISTS memory (
  aid          TEXT PRIMARY KEY,
  pindex       INTEGER NOT NULL,
  image_index  INTEGER NOT NULL,
  updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS unresolved (
  rel_path    TEXT PRIMARY KEY,
  reason      TEXT NOT NULL DEFAULT '',
  generation  TEXT NOT NULL DEFAULT '',
  size        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS findings (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  kind        TEXT NOT NULL,
  target      TEXT NOT NULL,
  detail      TEXT NOT NULL DEFAULT '',
  status      TEXT NOT NULL DEFAULT 'pending',
  created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS id_resolution (
  rel_path        TEXT PRIMARY KEY,
  query           TEXT NOT NULL DEFAULT '',
  candidates_json TEXT NOT NULL DEFAULT '[]',
  chosen_aid      TEXT,
  state           TEXT NOT NULL DEFAULT 'pending',
  note            TEXT NOT NULL DEFAULT '',
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collections (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL UNIQUE,
  note        TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collection_items (
  collection_id INTEGER NOT NULL,
  aid           TEXT NOT NULL,
  position      INTEGER NOT NULL DEFAULT 0,
  added_at      TEXT NOT NULL,
  PRIMARY KEY (collection_id, aid)
);
CREATE TABLE IF NOT EXISTS reading_prefs (
  aid         TEXT PRIMARY KEY,
  direction   TEXT NOT NULL DEFAULT '',
  layout      TEXT NOT NULL DEFAULT '',
  fit         TEXT NOT NULL DEFAULT '',
  fullscreen  INTEGER NOT NULL DEFAULT -1,
  updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bookmarks (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  aid         TEXT NOT NULL,
  pindex      INTEGER NOT NULL,
  image_index INTEGER NOT NULL,
  note        TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chapters_aid ON chapters(aid);
CREATE INDEX IF NOT EXISTS idx_findings_kind ON findings(kind, status);
CREATE INDEX IF NOT EXISTS idx_idres_state ON id_resolution(state);
CREATE INDEX IF NOT EXISTS idx_citems_aid ON collection_items(aid);
CREATE INDEX IF NOT EXISTS idx_bookmarks_aid ON bookmarks(aid, pindex, image_index);
"""


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ``ALTER TABLE ADD COLUMN`` when the column is missing, so an existing library
# opens without a migration step and without losing a single row.  A v1/v2
# database simply gains four empty user columns with their defaults.
_ADDED_COLUMNS = {
    "local_title": "TEXT NOT NULL DEFAULT ''",
    "aliases": "TEXT NOT NULL DEFAULT '[]'",
    "favorite": "INTEGER NOT NULL DEFAULT 0",
    "collection": "TEXT NOT NULL DEFAULT ''",
}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    have = {row[1] for row in conn.execute("PRAGMA table_info(books)").fetchall()}
    for name, ddl in _ADDED_COLUMNS.items():
        if name not in have:
            conn.execute(f"ALTER TABLE books ADD COLUMN {name} {ddl}")


def connect() -> sqlite3.Connection:
    paths.ensure_dirs()
    conn = sqlite3.connect(paths.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    _ensure_columns(conn)
    conn.commit()
    return conn


def _write(fn: Callable[[sqlite3.Connection], T]) -> T:
    conn = connect()
    try:
        with conn:
            return fn(conn)
    finally:
        conn.close()


def _read(fn: Callable[[sqlite3.Connection], T]) -> T:
    conn = connect()
    try:
        return fn(conn)
    finally:
        conn.close()


def _pick(d: dict, keys: tuple[str, ...]) -> tuple:
    return tuple(d.get(k) for k in keys)


# ---------------------------------------------------------------------------
# Catalog rebuild (indexer).  Memory and findings are deliberately preserved:
# re-indexing must never erase reading progress or health decisions.
# ---------------------------------------------------------------------------


def replace_catalog(books: list[dict], chapters: list[dict], unresolved: list[dict]) -> dict:
    bkeys = ("aid", "title", "author", "tags", "generation", "cover_path", "created_at")
    ckeys = ("aid", "pindex", "zip_path", "page_count", "cover_path")
    ukeys = ("rel_path", "reason", "generation", "size")

    def work(conn: sqlite3.Connection) -> None:
        # Reading progress, health decisions and *hand edits* outlive a rebuild.
        # The catalog describes what is on disk; a local title, an alias list, a
        # favourite flag or a collection assignment is what the user said about
        # it, and re-indexing must not silently discard that.
        kept = {
            row["aid"]: {
                "local_title": row["local_title"],
                "aliases": row["aliases"],
                "favorite": row["favorite"],
                "collection": row["collection"],
            }
            for row in conn.execute("SELECT aid, local_title, aliases, favorite, collection FROM books").fetchall()
        }
        conn.execute("DELETE FROM chapters")
        conn.execute("DELETE FROM books")
        conn.execute("DELETE FROM unresolved")
        conn.executemany(
            "INSERT INTO books (aid,title,author,tags,generation,cover_path,created_at) VALUES (?,?,?,?,?,?,?)",
            [_pick(b, bkeys) for b in books],
        )
        for aid, user in kept.items():
            if not any(user.values()):
                continue
            conn.execute(
                "UPDATE books SET local_title = ?, aliases = ?, favorite = ?, collection = ? WHERE aid = ?",
                (user["local_title"], user["aliases"], user["favorite"], user["collection"], aid),
            )
        conn.executemany(
            "INSERT INTO chapters (aid,pindex,zip_path,page_count,cover_path) VALUES (?,?,?,?,?)",
            [_pick(c, ckeys) for c in chapters],
        )
        conn.executemany(
            "INSERT INTO unresolved (rel_path,reason,generation,size) VALUES (?,?,?,?)",
            [_pick(u, ukeys) for u in unresolved],
        )

    _write(work)
    return stats()


def upsert_book_with_chapters(book: dict, chapters: list[dict]) -> None:
    """P4 single-book ingest path (same owner, no duplicate writer)."""
    bkeys = ("aid", "title", "author", "tags", "generation", "cover_path", "created_at")
    ckeys = ("aid", "pindex", "zip_path", "page_count", "cover_path")

    def work(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO books (aid,title,author,tags,generation,cover_path,created_at)"
            " VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(aid) DO UPDATE SET"
            " title=excluded.title, author=excluded.author, tags=excluded.tags,"
            " generation=excluded.generation, cover_path=excluded.cover_path",
            _pick(book, bkeys),
        )
        conn.execute("DELETE FROM chapters WHERE aid = ?", (book["aid"],))
        conn.executemany(
            "INSERT INTO chapters (aid,pindex,zip_path,page_count,cover_path) VALUES (?,?,?,?,?)",
            [_pick(c, ckeys) for c in chapters],
        )

    _write(work)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

# Sort keys the library page offers.  ``aid`` is the default because a purely
# numeric order is the only one that never reshuffles on its own: "recent" moves
# whenever you read something, and "title" moves whenever you rename a book.
BOOK_SORTS = ("aid", "recent", "title", "chapters", "pages")


def list_books(
    q: str = "", tag: str = "", collection: str = "", favorite: bool = False, sort: str = "aid"
) -> list[dict]:
    like = f"%{q}%"
    if sort not in BOOK_SORTS:
        raise ValueError(f"unknown sort: {sort}; use one of {', '.join(BOOK_SORTS)}")

    def work(conn: sqlite3.Connection) -> list[dict]:
        sql = (
            "SELECT b.*,"
            " (SELECT COUNT(*) FROM chapters c WHERE c.aid = b.aid) AS chapter_count,"
            " (SELECT COALESCE(SUM(c.page_count), 0) FROM chapters c WHERE c.aid = b.aid) AS page_total,"
            " m.pindex AS resume_pindex, m.image_index AS resume_image,"
            " m.updated_at AS last_read_at"
            " FROM books b LEFT JOIN memory m ON m.aid = b.aid"
            " WHERE (b.title LIKE ? OR b.aid LIKE ?"
            "        OR b.local_title LIKE ? OR b.aliases LIKE ?)"
        )
        params: list = [like, like, like, like]
        if collection:
            sql += " AND b.collection = ?"
            params.append(collection)
        if favorite:
            sql += " AND b.favorite = 1"
        # ``COALESCE(NULLIF(...))`` repeats the display-title rule in SQL: the user's
        # own title wins, the folder title is the fallback.  Sorting on ``title``
        # alone would file renamed books under their old folder name.
        if sort == "recent":
            # Never-read books go last rather than first: an empty reading history
            # is not "read longest ago", and burying the shelf under 185 unread
            # rows would make the sort useless.
            sql += " ORDER BY (m.updated_at IS NULL), m.updated_at DESC, CAST(b.aid AS INTEGER) DESC"
        elif sort == "title":
            # Books with no title at all cannot be compared, so they go last
            # instead of first: an empty string sorts before every real name and
            # would otherwise fill the top of the shelf with placeholder cards.
            sql += (
                " ORDER BY (COALESCE(NULLIF(b.local_title, ''), b.title) = '') ASC,"
                " COALESCE(NULLIF(b.local_title, ''), b.title)"
                " COLLATE NOCASE ASC, CAST(b.aid AS INTEGER) DESC"
            )
        elif sort == "chapters":
            sql += " ORDER BY chapter_count DESC, CAST(b.aid AS INTEGER) DESC"
        elif sort == "pages":
            sql += " ORDER BY page_total DESC, CAST(b.aid AS INTEGER) DESC"
        else:
            sql += " ORDER BY CAST(b.aid AS INTEGER) DESC"
        rows = conn.execute(sql, tuple(params)).fetchall()
        out = []
        for r in rows:
            d = _book_row(r)
            if tag and tag not in d["tags"]:
                continue
            out.append(d)
        return out

    return _read(work)


def _book_row(row) -> dict:
    """Decode one ``books`` row: JSON columns plus the display-title rule.

    ``display_title`` is what the UI shows and searches on: the user's own name
    wins over the folder-derived one, but the original stays in ``title`` so a
    rebuild can never lose the only evidence of what the folder said.
    """
    d = dict(row)
    d["tags"] = json.loads(d.get("tags") or "[]")
    d["aliases"] = json.loads(d.get("aliases") or "[]")
    d["favorite"] = int(d.get("favorite") or 0)
    d["display_title"] = d.get("local_title") or d.get("title") or ""
    return d


def get_book(aid: str) -> dict | None:
    def work(conn: sqlite3.Connection) -> dict | None:
        r = conn.execute("SELECT * FROM books WHERE aid = ?", (aid,)).fetchone()
        if r is None:
            return None
        d = _book_row(r)
        d["chapters"] = [
            dict(x)
            for x in conn.execute(
                "SELECT aid,pindex,zip_path,page_count,cover_path FROM chapters WHERE aid = ? ORDER BY pindex", (aid,)
            ).fetchall()
        ]
        m = conn.execute("SELECT pindex,image_index,updated_at FROM memory WHERE aid = ?", (aid,)).fetchone()
        d["memory"] = dict(m) if m else None
        return d

    return _read(work)


def list_chapters(aid: str) -> list[dict]:
    return _read(
        lambda c: [
            dict(x) for x in c.execute("SELECT * FROM chapters WHERE aid = ? ORDER BY pindex", (aid,)).fetchall()
        ]
    )


def get_chapter(aid: str, pindex: int) -> dict | None:
    """One chapter (P2 page serving / memory validation)."""
    return _read(
        lambda c: (lambda r: dict(r) if r else None)(
            c.execute(
                "SELECT aid,pindex,zip_path,page_count,cover_path FROM chapters WHERE aid = ? AND pindex = ?",
                (aid, pindex),
            ).fetchone()
        )
    )


def first_chapter(aid: str) -> dict | None:
    return _read(
        lambda c: (lambda r: dict(r) if r else None)(
            c.execute("SELECT * FROM chapters WHERE aid = ? ORDER BY pindex LIMIT 1", (aid,)).fetchone()
        )
    )


def list_unresolved() -> list[dict]:
    return _read(
        lambda c: [
            dict(x)
            for x in c.execute("SELECT rel_path,reason,generation,size FROM unresolved ORDER BY rel_path").fetchall()
        ]
    )


def stats() -> dict:
    def work(conn: sqlite3.Connection) -> dict:
        out = {"schemaVersion": SCHEMA_VERSION}
        for t in ("books", "chapters", "memory", "unresolved", "findings"):
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        out["page_total"] = conn.execute("SELECT COALESCE(SUM(page_count), 0) FROM chapters").fetchone()[0]
        out["findings_pending"] = conn.execute("SELECT COUNT(*) FROM findings WHERE status = 'pending'").fetchone()[0]
        return out

    return _read(work)


# ---------------------------------------------------------------------------
# Reading memory (P2)
# ---------------------------------------------------------------------------


def set_memory(aid: str, pindex: int, image_index: int) -> None:
    _write(
        lambda c: c.execute(
            "INSERT INTO memory (aid,pindex,image_index,updated_at) VALUES (?,?,?,?)"
            " ON CONFLICT(aid) DO UPDATE SET pindex=excluded.pindex,"
            " image_index=excluded.image_index, updated_at=excluded.updated_at",
            (aid, pindex, image_index, _utc()),
        )
    )


def get_memory(aid: str) -> dict | None:
    return _read(
        lambda c: (lambda r: dict(r) if r else None)(
            c.execute("SELECT aid,pindex,image_index,updated_at FROM memory WHERE aid = ?", (aid,)).fetchone()
        )
    )


# ---------------------------------------------------------------------------
# Health findings (P3/P5)
# ---------------------------------------------------------------------------


def add_finding(kind: str, target: str, detail: str = "") -> int:
    def work(conn: sqlite3.Connection) -> int:
        cur = conn.execute(
            "INSERT INTO findings (kind,target,detail,status,created_at) VALUES (?,?,?,'pending',?)",
            (kind, target, detail, _utc()),
        )
        return int(cur.lastrowid or 0)

    return _write(work)


def list_findings(status: str | None = None) -> list[dict]:
    def work(conn: sqlite3.Connection) -> list[dict]:
        if status:
            rows = conn.execute("SELECT * FROM findings WHERE status = ? ORDER BY id", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM findings ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    return _read(work)


def get_finding(fid: int) -> dict | None:
    return _read(
        lambda c: (lambda r: dict(r) if r else None)(
            c.execute("SELECT * FROM findings WHERE id = ?", (fid,)).fetchone()
        )
    )


def set_finding_status(fid: int, status: str) -> None:
    _write(lambda c: c.execute("UPDATE findings SET status = ? WHERE id = ?", (status, fid)))


# ---------------------------------------------------------------------------
# Governance (P5): narrow row-level helpers used only by governance.py.
# Physical bytes are never touched here — the canonical owner still owns every
# logical write, so there is exactly one writer for the catalog.
# ---------------------------------------------------------------------------


def get_unresolved(rel_path: str) -> dict | None:
    return _read(
        lambda c: (lambda r: dict(r) if r else None)(
            c.execute(
                "SELECT rel_path,reason,generation,size FROM unresolved WHERE rel_path = ?",
                (rel_path,),
            ).fetchone()
        )
    )


def delete_unresolved(rel_path: str) -> None:
    _write(lambda c: c.execute("DELETE FROM unresolved WHERE rel_path = ?", (rel_path,)))


def insert_unresolved(row: dict) -> None:
    """Restore one unresolved row during a governance rollback (DB row only)."""
    _write(
        lambda c: c.execute(
            "INSERT OR REPLACE INTO unresolved (rel_path,reason,generation,size) VALUES (?,?,?,?)",
            (row["rel_path"], row.get("reason", ""), row.get("generation", ""), int(row.get("size", 0))),
        )
    )


def remove_book(aid: str) -> None:
    """Rollback of a governance-created book: drop its chapters + book row.

    Reading memory is deliberately preserved (same invariant as
    ``replace_catalog``); a freshly promoted book normally has none.
    """

    def work(conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM chapters WHERE aid = ?", (aid,))
        conn.execute("DELETE FROM books WHERE aid = ?", (aid,))

    _write(work)


def add_chapter(book: dict, chapter: dict) -> bool:
    """P5 promote: add ONE chapter without disturbing a book's other chapters.

    ``book`` is inserted with ``INSERT OR IGNORE`` (an existing book's rich
    metadata is never overwritten).  Returns True only when a new book row was
    actually created — the governance rollback uses that to decide whether it
    may remove the whole book.  ``upsert_book_with_chapters`` is deliberately
    NOT used here because it deletes all of the book's existing chapters.
    """
    bkeys = ("aid", "title", "author", "tags", "generation", "cover_path", "created_at")
    ckeys = ("aid", "pindex", "zip_path", "page_count", "cover_path")

    def work(conn: sqlite3.Connection) -> bool:
        cur = conn.execute(
            "INSERT OR IGNORE INTO books (aid,title,author,tags,generation,cover_path,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            _pick(book, bkeys),
        )
        created = cur.rowcount > 0
        conn.execute(
            "INSERT OR REPLACE INTO chapters (aid,pindex,zip_path,page_count,cover_path) VALUES (?,?,?,?,?)",
            _pick(chapter, ckeys),
        )
        return created

    return _write(work)


def remove_chapter(aid: str, pindex: int) -> None:
    """P5 rollback: remove exactly the one chapter this action added."""
    _write(lambda c: c.execute("DELETE FROM chapters WHERE aid = ? AND pindex = ?", (aid, pindex)))


def chapter_count(aid: str) -> int:
    return _read(lambda c: int(c.execute("SELECT COUNT(*) FROM chapters WHERE aid = ?", (aid,)).fetchone()[0]))


# ---------------------------------------------------------------------------
# Import id resolution (A.3): which Aid a library-external directory maps to.
# This table records *decisions and evidence only* — it never moves bytes and
# never touches ``books``/``chapters``.  Import execution is a separate, later
# confirmation-gated step, so a half-finished review leaves no partial import.
# ---------------------------------------------------------------------------


def upsert_id_resolution(row: dict) -> bool:
    """Record one directory's candidate set; re-recording replaces the row.

    ``created_at`` is preserved on re-record so the row keeps the time the
    directory was first reviewed, while ``updated_at`` tracks the last change.
    Returns True only when the row is newly created.
    """

    def work(conn: sqlite3.Connection) -> bool:
        now = _utc()
        existed = (
            conn.execute("SELECT 1 FROM id_resolution WHERE rel_path = ?", (row["rel_path"],)).fetchone() is not None
        )
        conn.execute(
            "INSERT INTO id_resolution"
            " (rel_path,query,candidates_json,chosen_aid,state,note,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(rel_path) DO UPDATE SET"
            "  query=excluded.query,"
            "  candidates_json=excluded.candidates_json,"
            "  chosen_aid=excluded.chosen_aid,"
            "  state=excluded.state,"
            "  note=excluded.note,"
            "  updated_at=excluded.updated_at",
            (
                row["rel_path"],
                row.get("query", ""),
                row.get("candidates_json", "[]"),
                row.get("chosen_aid"),
                row.get("state", "pending"),
                row.get("note", ""),
                row.get("created_at") or now,
                now,
            ),
        )
        return not existed

    return _write(work)


def get_id_resolution(rel_path: str) -> dict | None:
    return _read(
        lambda c: (lambda r: dict(r) if r else None)(
            c.execute("SELECT * FROM id_resolution WHERE rel_path = ?", (rel_path,)).fetchone()
        )
    )


def list_id_resolutions(state: str | None = None) -> list[dict]:
    def work(conn: sqlite3.Connection) -> list[dict]:
        if state:
            rows = conn.execute("SELECT * FROM id_resolution WHERE state = ? ORDER BY rel_path", (state,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM id_resolution ORDER BY rel_path").fetchall()
        return [dict(r) for r in rows]

    return _read(work)


def set_id_resolution_choice(rel_path: str, aid: str | None, state: str, note: str = "") -> bool:
    """Record a decision for one directory.  Returns False when the row is gone."""

    def work(conn: sqlite3.Connection) -> bool:
        cur = conn.execute(
            "UPDATE id_resolution SET chosen_aid = ?, state = ?, note = ?, updated_at = ? WHERE rel_path = ?",
            (aid, state, note, _utc(), rel_path),
        )
        return cur.rowcount > 0

    return _write(work)


def delete_id_resolution(rel_path: str) -> None:
    _write(lambda c: c.execute("DELETE FROM id_resolution WHERE rel_path = ?", (rel_path,)))


# ---------------------------------------------------------------------------
# User metadata (B.3): what the user says about a book, not what the folder said.
# These columns are carried across ``replace_catalog``; nothing here touches
# physical bytes, and an unknown Aid is rejected by the caller (API layer) so a
# typo cannot create a phantom book row.
# ---------------------------------------------------------------------------

USER_FIELDS = ("local_title", "aliases", "favorite", "collection")


def set_user_metadata(aid: str, fields: dict) -> dict | None:
    """Update the whitelisted user columns of one existing book.

    Returns the refreshed book, or ``None`` when the Aid has no book row — the
    API turns that into a 404 instead of upserting a row for a book that does
    not exist on disk.
    """
    unknown = set(fields) - set(USER_FIELDS)
    if unknown:
        raise ValueError(f"not user-editable: {', '.join(sorted(unknown))}")
    if not fields:
        return get_book(aid)

    sets = []
    values = []
    for key in USER_FIELDS:
        if key in fields:
            value = fields[key]
            if key == "aliases":
                value = json.dumps([str(v) for v in (value or [])], ensure_ascii=False)
            elif key == "favorite":
                value = 1 if value else 0
            else:
                value = str(value or "").strip()
            sets.append(f"{key} = ?")
            values.append(value)
    values.append(aid)

    def work(conn: sqlite3.Connection) -> bool:
        cur = conn.execute(f"UPDATE books SET {', '.join(sets)} WHERE aid = ?", values)
        return cur.rowcount > 0

    return get_book(aid) if _write(work) else None


def set_favorite(aid: str, favorite: bool) -> dict | None:
    return set_user_metadata(aid, {"favorite": favorite})


def list_favorites() -> list[dict]:
    return _read(
        lambda c: [
            _book_row(r)
            for r in c.execute("SELECT * FROM books WHERE favorite = 1 ORDER BY CAST(aid AS INTEGER) DESC").fetchall()
        ]
    )


# ---------------------------------------------------------------------------
# Upstream-learned metadata (D: tags/author filled in from an album detail)
#
# These columns describe the *work*, not the user's opinion, so they are written
# by a different path than USER_FIELDS: the resolver fills them in, the user can
# still rename/collect/favourite.  Filling is deliberately additive — an empty
# column is a gap, an occupied one is either the downloader's truth or the user's
# edit, and neither may be silently replaced by a lookup.
# ---------------------------------------------------------------------------


def set_book_tags(aid: str, tags, author: str = "", *, overwrite: bool = False) -> dict | None:
    """Merge upstream tags/author into one book.  Returns the book, or None.

    ``None`` means the Aid has no book row: metadata must never conjure one, so
    the caller reports a miss instead of creating a phantom.
    """
    current = get_book(aid)
    if current is None:
        return None

    incoming = [str(t).strip() for t in (tags or []) if str(t).strip()]
    if overwrite:
        merged = incoming
    else:
        merged = list(current.get("tags") or [])
        for tag in incoming:
            if tag not in merged:
                merged.append(tag)

    new_author = str(author or "").strip()
    if not overwrite and (current.get("author") or "").strip():
        new_author = current["author"]

    payload = json.dumps(merged, ensure_ascii=False)

    def work(conn: sqlite3.Connection) -> bool:
        cur = conn.execute("UPDATE books SET tags = ?, author = ? WHERE aid = ?", (payload, new_author, aid))
        return cur.rowcount > 0

    return get_book(aid) if _write(work) else None


def books_missing_tags(limit: int | None = None) -> list[dict]:
    """Books whose tags column is still empty or ``[]`` — the backfill worklist."""

    def work(conn: sqlite3.Connection) -> list[dict]:
        rows = conn.execute(
            "SELECT * FROM books WHERE tags IN ('', '[]') OR tags IS NULL ORDER BY CAST(aid AS INTEGER) DESC"
        ).fetchall()
        out = [_book_row(r) for r in rows]
        return out[:limit] if limit else out

    return _read(work)


# ---------------------------------------------------------------------------
# Collections / reading lists (B.4)
#
# Two shapes exist on purpose, and they answer different questions:
#
# * ``books.collection`` (B.3) is one **shelf label** typed by the user — a book
#   is on exactly one shelf, like a folder.  Cheap to set, cheap to filter.
# * ``collections`` + ``collection_items`` are **named lists** with many-to-many
#   membership and an explicit order.  A book may be in any number of them.
#
# Neither is derived from the other, and removing a list never touches a book:
# the join table points at ``books.aid`` but nothing cascades in either
# direction, so deleting a list cannot delete a book or a byte.
# ---------------------------------------------------------------------------


def create_collection(name: str, note: str = "") -> dict:
    """Create one named list.  Returns the existing row when the name is taken."""
    clean = (name or "").strip()
    if not clean:
        raise ValueError("collection name must not be empty")
    now = _utc()

    def work(conn: sqlite3.Connection) -> dict:
        conn.execute(
            "INSERT INTO collections (name, note, created_at, updated_at) VALUES (?,?,?,?)"
            " ON CONFLICT(name) DO NOTHING",
            (clean, note or "", now, now),
        )
        row = conn.execute("SELECT * FROM collections WHERE name = ?", (clean,)).fetchone()
        return dict(row)

    return _write(work)


def list_collections() -> list[dict]:
    def work(conn: sqlite3.Connection) -> list[dict]:
        rows = conn.execute(
            "SELECT c.*,"
            " (SELECT COUNT(*) FROM collection_items i WHERE i.collection_id = c.id) AS item_count"
            " FROM collections c ORDER BY c.name COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]

    return _read(work)


def get_collection(collection_id: int) -> dict | None:
    def work(conn: sqlite3.Connection) -> dict | None:
        row = conn.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        items = conn.execute(
            "SELECT i.aid, i.position, b.title, b.local_title, b.cover_path"
            " FROM collection_items i LEFT JOIN books b ON b.aid = i.aid"
            " WHERE i.collection_id = ? ORDER BY i.position, i.aid",
            (collection_id,),
        ).fetchall()
        out["items"] = [
            {
                "aid": r["aid"],
                "position": r["position"],
                "title": r["title"] or "",
                "display_title": r["local_title"] or r["title"] or "",
                "cover_path": r["cover_path"],
            }
            for r in items
        ]
        return out

    return _read(work)


def add_to_collection(collection_id: int, aid: str) -> bool:
    """Add one book; adding twice is a no-op.  False when the list is gone."""
    now = _utc()

    def work(conn: sqlite3.Connection) -> bool:
        if conn.execute("SELECT 1 FROM collections WHERE id = ?", (collection_id,)).fetchone() is None:
            return False
        next_pos = conn.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 FROM collection_items WHERE collection_id = ?",
            (collection_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO collection_items (collection_id, aid, position, added_at)"
            " VALUES (?,?,?,?) ON CONFLICT(collection_id, aid) DO NOTHING",
            (collection_id, aid, next_pos, now),
        )
        conn.execute("UPDATE collections SET updated_at = ? WHERE id = ?", (now, collection_id))
        return True

    return _write(work)


def remove_from_collection(collection_id: int, aid: str) -> bool:
    """Remove one book from one list.  Returns False when it was not there."""

    def work(conn: sqlite3.Connection) -> bool:
        cur = conn.execute(
            "DELETE FROM collection_items WHERE collection_id = ? AND aid = ?",
            (collection_id, aid),
        )
        if cur.rowcount > 0:
            conn.execute("UPDATE collections SET updated_at = ? WHERE id = ?", (_utc(), collection_id))
        return cur.rowcount > 0

    return _write(work)


def reorder_collection(collection_id: int, aids: list[str]) -> list[str]:
    """Apply an explicit order; Aids not mentioned keep their relative order after it."""
    order = [str(a) for a in (aids or [])]

    def work(conn: sqlite3.Connection) -> list[str]:
        known = [
            r[0]
            for r in conn.execute(
                "SELECT aid FROM collection_items WHERE collection_id = ? ORDER BY position, aid",
                (collection_id,),
            ).fetchall()
        ]
        if not known:
            return []
        wanted = [a for a in order if a in known]
        rest = [a for a in known if a not in wanted]
        final = wanted + rest
        for pos, aid in enumerate(final, start=1):
            conn.execute(
                "UPDATE collection_items SET position = ? WHERE collection_id = ? AND aid = ?",
                (pos, collection_id, aid),
            )
        conn.execute("UPDATE collections SET updated_at = ? WHERE id = ?", (_utc(), collection_id))
        return final

    return _write(work)


def delete_collection(collection_id: int) -> bool:
    """Delete one list *and its membership rows only* — never a book, never a zip."""

    def work(conn: sqlite3.Connection) -> bool:
        conn.execute("DELETE FROM collection_items WHERE collection_id = ?", (collection_id,))
        cur = conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
        return cur.rowcount > 0

    return _write(work)


def list_collections_for_book(aid: str) -> list[dict]:
    return _read(
        lambda c: [
            {"id": r["id"], "name": r["name"]}
            for r in c.execute(
                "SELECT col.id, col.name FROM collection_items i"
                " JOIN collections col ON col.id = i.collection_id"
                " WHERE i.aid = ? ORDER BY col.name COLLATE NOCASE",
                (aid,),
            ).fetchall()
        ]
    )


# ---------------------------------------------------------------------------
# Reading preferences and bookmarks (C.1)
#
# ``aid = ''`` is the global row: what the reader should use when a book has no
# opinion of its own.  Resolution order is per-book > global > built-in default,
# and it lives in :mod:`jmlib.prefs` — the table only stores what was said.
#
# ``fullscreen`` is tri-state on purpose: ``-1`` means "not chosen", ``0`` off,
# ``1`` on.  A two-state flag could not express "this book overrides the global
# default back to windowed" without inventing a second column.
#
# Bookmarks are *extra* positions, never the memory row: ``memory`` keeps the one
# "where I stopped" pointer whose meaning must not change, while bookmarks are
# the places a reader deliberately wants to find again.
# ---------------------------------------------------------------------------

PREF_FIELDS = ("direction", "layout", "fit", "fullscreen")
GLOBAL_PREF_KEY = ""


def get_pref_row(aid: str = GLOBAL_PREF_KEY) -> dict | None:
    def work(conn: sqlite3.Connection) -> dict | None:
        row = conn.execute("SELECT * FROM reading_prefs WHERE aid = ?", (aid or "",)).fetchone()
        return dict(row) if row is not None else None

    return _read(work)


def list_pref_rows() -> list[dict]:
    """Every stored row, global first — used by the resolver and by tests."""
    return _read(
        lambda c: [
            dict(r)
            for r in c.execute(
                "SELECT * FROM reading_prefs ORDER BY CASE WHEN aid = '' THEN 0 ELSE 1 END, aid"
            ).fetchall()
        ]
    )


def set_pref_row(aid: str, fields: dict) -> dict:
    """Upsert one row.  Only the keys present are written; ``None`` clears a key."""
    unknown = set(fields) - set(PREF_FIELDS)
    if unknown:
        raise ValueError(f"not a reading preference: {', '.join(sorted(unknown))}")
    if not fields:
        raise ValueError("no preference supplied")

    current = get_pref_row(aid) or {
        "aid": aid or "",
        "direction": "",
        "layout": "",
        "fit": "",
        "fullscreen": -1,
    }
    merged = {k: current.get(k) for k in PREF_FIELDS}
    for key, value in fields.items():
        if value is None:
            merged[key] = -1 if key == "fullscreen" else ""
        elif key == "fullscreen":
            merged[key] = 1 if value else 0
        else:
            merged[key] = str(value).strip()
    now = _utc()
    # ``fullscreen`` is the only numeric preference: -1 means "unset", 0/1 are
    # real values.  Normalising it here keeps the SQL parameter list free of
    # ``None`` — the column is NOT NULL and an unset row must stay -1.
    raw_fullscreen = merged.get("fullscreen")
    fullscreen = -1 if raw_fullscreen is None else int(raw_fullscreen)

    def work(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO reading_prefs (aid, direction, layout, fit, fullscreen, updated_at)"
            " VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(aid) DO UPDATE SET direction=excluded.direction,"
            " layout=excluded.layout, fit=excluded.fit,"
            " fullscreen=excluded.fullscreen, updated_at=excluded.updated_at",
            (aid or "", merged["direction"], merged["layout"], merged["fit"], fullscreen, now),
        )

    _write(work)
    return get_pref_row(aid) or {}


def delete_pref_row(aid: str) -> None:
    _write(lambda c: c.execute("DELETE FROM reading_prefs WHERE aid = ?", (aid or "",)))


def add_bookmark(aid: str, pindex: int, image_index: int, note: str = "") -> dict:
    """One bookmark per exact position: re-adding the same spot updates its note."""
    now = _utc()

    def work(conn: sqlite3.Connection) -> dict:
        existing = conn.execute(
            "SELECT * FROM bookmarks WHERE aid = ? AND pindex = ? AND image_index = ?",
            (aid, pindex, image_index),
        ).fetchone()
        if existing is not None:
            conn.execute("UPDATE bookmarks SET note = ? WHERE id = ?", (note or "", existing["id"]))
            row = conn.execute("SELECT * FROM bookmarks WHERE id = ?", (existing["id"],)).fetchone()
            return dict(row)
        conn.execute(
            "INSERT INTO bookmarks (aid, pindex, image_index, note, created_at) VALUES (?,?,?,?,?)",
            (aid, pindex, image_index, note or "", now),
        )
        row = conn.execute(
            "SELECT * FROM bookmarks WHERE aid = ? AND pindex = ? AND image_index = ?",
            (aid, pindex, image_index),
        ).fetchone()
        return dict(row)

    return _write(work)


def list_bookmarks(aid: str | None = None) -> list[dict]:
    def work(conn: sqlite3.Connection) -> list[dict]:
        if aid is None:
            rows = conn.execute("SELECT * FROM bookmarks ORDER BY aid, pindex, image_index").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM bookmarks WHERE aid = ? ORDER BY pindex, image_index",
                (aid,),
            ).fetchall()
        return [dict(r) for r in rows]

    return _read(work)


def get_bookmark(bookmark_id: int) -> dict | None:
    def work(conn: sqlite3.Connection) -> dict | None:
        row = conn.execute("SELECT * FROM bookmarks WHERE id = ?", (bookmark_id,)).fetchone()
        return dict(row) if row is not None else None

    return _read(work)


def delete_bookmark(bookmark_id: int) -> bool:
    return _write(lambda c: c.execute("DELETE FROM bookmarks WHERE id = ?", (bookmark_id,)).rowcount > 0)
