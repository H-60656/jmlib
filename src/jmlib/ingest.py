"""P4 ingest core: turn loose chapter images into one zip per chapter + catalog.

This module owns the *only* physical + logical write path for newly ingested
books (both new downloads via ``jm_plugin.py`` and the 16 loose books).  It does
not import jmcomic at all, so it can be exercised offline with synthetic zips.

Invariants:
  * one chapter -> exactly one ``library/archive/{Aid}/{Pindex}.zip`` whose
    members are continuously numbered ``00001<suffix>`` ... (reading order);
  * covers come from that zip's first member (``cover.inspect_chapter``); a book
    cover is only a pointer to chapter 1's cover, never a new file;
  * an existing ``{Aid}/`` tree is NEVER overwritten — the whole book is skipped
    and reported as a conflict instead (identity must never be guessed);
  * zero deletion: source images are always left in place (staging cleanup and
    loose-source cleanup are P5 governance actions, not this module);
  * ``data/ingest_metadata.json`` is the durable sidecar for P4-rich metadata
    (title/author/tags) so ``jmlib index`` can rebuild without losing it.
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from jmlib import cover, paths, store
from jmlib.aid import AID_RE, cover_aid

PINDEX_RE = re.compile(r"^\d+$")
SCHEMA = "jmlib.loose_ingest.v1"
_NUM = re.compile(r"\d+")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _numeric_key(p: Path) -> tuple[int, str]:
    m = _NUM.search(Path(p).stem)
    return (int(m.group()) if m else 0, Path(p).name)


# ---------------------------------------------------------------------------
# P4 metadata sidecar (canonical source for rich titles/authors/tags)
# ---------------------------------------------------------------------------


def load_meta() -> dict:
    if not paths.INGEST_METADATA.exists():
        return {}
    try:
        loaded = json.loads(paths.INGEST_METADATA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _save_meta(meta: dict) -> None:
    paths.DATA.mkdir(parents=True, exist_ok=True)
    part = paths.INGEST_METADATA.with_suffix(".json.part")
    part.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(part, paths.INGEST_METADATA)


def put_meta(aid: str, info: dict) -> None:
    """P5 promote: record rich display metadata so ``jmlib index`` keeps it.

    Only used when governance creates a *brand new* book; an existing book's
    sidecar entry is never overwritten (identity/metadata must not be guessed).
    """
    meta = load_meta()
    meta[aid] = info
    _save_meta(meta)


def drop_meta(aid: str) -> None:
    """P5 rollback: remove only a sidecar entry this governance action added."""
    meta = load_meta()
    if aid in meta:
        del meta[aid]
        _save_meta(meta)


def merge_meta(aid: str, fields: dict) -> None:
    """Update some sidecar fields for one Aid, keeping everything else.

    Tags and author learned from upstream belong to the *book*, not to one
    ingest run, and ``jmlib index`` rebuilds ``books.tags``/``books.author``
    from this sidecar.  Writing them only to the database would therefore lose
    them on the next rebuild; writing them here too keeps one owner.
    """
    meta = load_meta()
    entry = dict(meta.get(aid) or {})
    entry.update({k: v for k, v in fields.items() if v is not None})
    entry["updated_at"] = _utc()
    meta[aid] = entry
    _save_meta(meta)


# ---------------------------------------------------------------------------
# Single-book ingest (shared by the download plugin and loose ingest)
# ---------------------------------------------------------------------------


def pack_album(
    aid: str,
    chapter_images: dict[int, list[Path]],
    *,
    title: str = "",
    author: str = "",
    tags: list[str] | None = None,
    generation: str = "download",
    overwrite: bool = False,
) -> dict:
    """Write one zip per chapter, extract covers, and upsert the catalog.

    ``chapter_images`` maps a 1-based chapter index to its (unordered) image
    paths; this function sorts them by numeric filename stem and renumbers the
    zip members to a continuous ``00001..N`` sequence.
    """
    paths.ensure_dirs()
    aid = str(aid)
    tags = list(tags or [])
    result: dict = {
        "aid": aid,
        "written": 0,
        "skipped": 0,
        "pages": 0,
        "chapters": [],
        "conflicts": [],
        "book": None,
    }

    if not AID_RE.match(aid):
        result["conflicts"].append(f"invalid Aid: {aid!r}")
        return result

    aid_dir = paths.ARCHIVE / aid
    existing = sorted(aid_dir.glob("*.zip")) if aid_dir.exists() else []
    if existing and not overwrite:
        result["skipped"] = len(existing)
        result["conflicts"].append(f"Aid {aid} already has {len(existing)} chapter zip(s); skipped (no overwrite)")
        return result

    chapter_rows: list[dict] = []
    for pindex in sorted(chapter_images):
        if pindex < 1:
            result["conflicts"].append(f"skipped non-positive pindex {pindex}")
            continue
        srcs = sorted((Path(p) for p in chapter_images[pindex]), key=_numeric_key)
        srcs = [p for p in srcs if p.is_file()]
        if not srcs:
            result["conflicts"].append(f"chapter {pindex}: no readable images")
            continue

        zip_path = aid_dir / f"{pindex}.zip"
        part = aid_dir / f"{pindex}.zip.part"
        aid_dir.mkdir(parents=True, exist_ok=True)
        if zip_path.exists() and not overwrite:
            result["skipped"] += 1
            result["conflicts"].append(f"refusing to overwrite {paths.rel(zip_path)}")
            continue
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, src in enumerate(srcs, start=1):
                zf.write(src, f"{i:05d}{src.suffix.lower()}")
        os.replace(part, zip_path)

        info = cover.inspect_chapter(zip_path, aid, pindex)
        chapter_rows.append(
            {
                "aid": aid,
                "pindex": pindex,
                "zip_path": paths.rel(zip_path),
                "page_count": info["page_count"],
                "cover_path": info["cover_path"],
            }
        )
        result["written"] += 1
        result["pages"] += info["page_count"]

    if not chapter_rows:
        result["conflicts"].append("no chapters were written; catalog untouched")
        return result

    chapter_rows.sort(key=lambda c: c["pindex"])
    book = {
        "aid": aid,
        "title": title or "",
        "author": author or "",
        "tags": json.dumps(tags, ensure_ascii=False),
        "generation": generation or "",
        # Book cover is a pointer to chapter 1's cover — no extra file.
        "cover_path": chapter_rows[0]["cover_path"],
        "created_at": _utc(),
    }
    store.upsert_book_with_chapters(book, chapter_rows)

    # Durable rich metadata for `jmlib index` rebuilds.  Written only after a
    # successful DB upsert, so the sidecar always describes an ingested book.
    try:
        meta = load_meta()
        meta[aid] = {
            "title": book["title"],
            "author": book["author"],
            "tags": tags,
            "generation": book["generation"],
            "source": generation,
            "updated_at": book["created_at"],
        }
        _save_meta(meta)
    except OSError as exc:
        print(f"warning: could not update ingest metadata sidecar: {exc}")

    result["chapters"] = chapter_rows
    result["book"] = book
    return result


# ---------------------------------------------------------------------------
# Loose-book ingest (16 books, plan-first, zero deletion)
# ---------------------------------------------------------------------------


def _loose_root(root: Path | None) -> Path:
    if root is not None:
        return Path(root)
    if paths.MIGRATION_MANIFEST.exists():
        m = json.loads(paths.MIGRATION_MANIFEST.read_text(encoding="utf-8"))
        loose = m.get("loose", {}).get("root")
        if loose:
            return Path(loose)
    raise FileNotFoundError("loose root unknown: pass --root or keep migration_manifest.json")


def loose_plan(root: Path | None = None) -> dict:
    """Read-only scan: build a full old->new manifest, then save it to data/."""
    src = _loose_root(root)
    if not src.is_dir():
        raise FileNotFoundError(f"loose root is not a directory: {src}")

    by_aid: dict[str, dict] = {}
    conflicts: list[str] = []
    for d in sorted(p for p in src.iterdir() if p.is_dir()):
        files = [f for f in d.iterdir() if f.is_file()]
        # ``cover_aid`` does the match-and-extract in one step, so the Aid comes
        # back already narrowed to a ``str`` instead of a possibly-``None`` group.
        covers = [(f, aid_of) for f in files if (aid_of := cover_aid(f.name))]
        cover_aids = {aid_of for _, aid_of in covers}
        aid_dirs = [sub for sub in d.iterdir() if sub.is_dir() and AID_RE.match(sub.name)]
        aids = cover_aids | {sub.name for sub in aid_dirs}
        for aid in sorted(aids):
            entry = by_aid.setdefault(
                aid,
                {
                    "aid": aid,
                    "title": d.name,
                    "source_dirs": [],
                    "cover_files": [],
                    "chapters": {},
                    "chapter_sources": {},
                },
            )
            if d.name not in entry["source_dirs"]:
                entry["source_dirs"].append(d.name)
            if not cover_aid(d.name) and not any(AID_RE.match(s.name) for s in aid_dirs):
                # a title-like folder with an Aid subdir is the natural title
                entry["title"] = d.name
            for f, aid_of in covers:
                if aid_of == aid:
                    entry["cover_files"].append(str(f))
            for sub in aid_dirs:
                if sub.name != aid:
                    continue
                for chap in sorted(sub.iterdir(), key=_numeric_key):
                    if not chap.is_dir() or not PINDEX_RE.match(chap.name):
                        continue
                    pindex = int(chap.name)
                    imgs = sorted(
                        (p for p in chap.iterdir() if p.is_file() and p.suffix.lower() in cover.IMAGE_SUFFIXES),
                        key=_numeric_key,
                    )
                    if not imgs:
                        continue
                    prev = entry["chapter_sources"].get(pindex)
                    if prev and prev != d.name:
                        conflicts.append(
                            f"Aid {aid} chapter {pindex} appears in both {prev!r} and {d.name!r}; kept the first"
                        )
                        continue
                    entry["chapter_sources"][pindex] = d.name
                    entry["chapters"].setdefault(str(pindex), []).extend(str(p) for p in imgs)

    books: list[dict] = []
    total_images = 0
    total_chapters = 0
    for aid in sorted(by_aid):
        e = by_aid[aid]
        chapters = {k: e["chapters"][k] for k in sorted(e["chapters"], key=int)}
        total_chapters += len(chapters)
        total_images += sum(len(v) for v in chapters.values())
        # The title folder with actual chapter images wins (split 1454103: the
        # simplified-title folder has the body text, the traditional one only a
        # cover file).
        body_dirs = list(dict.fromkeys(e["chapter_sources"].values()))
        title = body_dirs[0] if body_dirs else (e["source_dirs"][0] if e["source_dirs"] else "")
        books.append(
            {
                "aid": aid,
                "title": title,
                "source_dirs": e["source_dirs"],
                "cover_files": e["cover_files"],
                "chapters": chapters,
                "db_action": "insert" if chapters else "skip-no-images",
            }
        )

    manifest = {
        "schemaVersion": SCHEMA,
        "kind": "loose_ingest",
        "createdAt": _utc(),
        "root": str(src),
        "summary": {
            "dirs": sum(1 for _ in src.iterdir() if _.is_dir()),
            "aids": len(books),
            "books_with_body": sum(1 for b in books if b["chapters"]),
            "chapters": total_chapters,
            "images": total_images,
            "cover_files": sum(len(b["cover_files"]) for b in books),
            "collisions": len(conflicts),
        },
        "safety": {
            "deletion": "none — loose source directories and cover files are left untouched",
            "overwrite": "never — an existing {Aid}/ tree is skipped and reported",
            "covers": "book/chapter covers are rebuilt from each zip's first image; loose *_cover.jpg files are NOT imported",
            "reversible": "newly written zips/covers + DB rows are listed here; loose_rollback() reverses them",
        },
        "books": books,
        "conflicts": conflicts,
    }
    paths.DATA.mkdir(parents=True, exist_ok=True)
    paths.LOOSE_INGEST_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def loose_apply(manifest: dict | None = None) -> int:
    """Execute a (just-generated or on-disk) loose manifest; zero source deletion."""
    if manifest is None:
        if not paths.LOOSE_INGEST_MANIFEST.exists():
            print("loose ingest: no manifest; run `jmlib ingest --plan-only` first")
            return 2
        manifest = json.loads(paths.LOOSE_INGEST_MANIFEST.read_text(encoding="utf-8"))

    written = skipped = pages = 0
    conflicts: list[str] = []
    for b in manifest["books"]:
        if b["db_action"] != "insert":
            skipped += 1
            continue
        r = pack_album(
            b["aid"],
            {int(k): [Path(p) for p in v] for k, v in b["chapters"].items()},
            title=b["title"],
            generation="loose",
        )
        written += r["written"]
        pages += r["pages"]
        conflicts.extend(f"{b['aid']}: {c}" for c in r["conflicts"])
        if not r["written"]:
            skipped += 1

    print(
        f"loose ingest: books={len(manifest['books'])} written_zips={written} "
        f"pages={pages} skipped_books={skipped} conflicts={len(conflicts)}"
    )
    for c in conflicts[:20]:
        print("  !", c)
    print("loose ingest: source directories left untouched (zero deletion)")
    return 0 if not conflicts else 1


def loose_rollback() -> int:
    """Reverse a loose ingest using its manifest (explicit P5-style action)."""
    if not paths.LOOSE_INGEST_MANIFEST.exists():
        print("loose ingest: nothing to roll back")
        return 2
    manifest = json.loads(paths.LOOSE_INGEST_MANIFEST.read_text(encoding="utf-8"))
    conn = store.connect()
    removed = 0
    try:
        with conn:
            for b in manifest["books"]:
                aid = b["aid"]
                conn.execute("DELETE FROM chapters WHERE aid = ?", (aid,))
                conn.execute("DELETE FROM books WHERE aid = ?", (aid,))
                for pindex in b["chapters"]:
                    z = paths.ARCHIVE / aid / f"{pindex}.zip"
                    if z.is_file():
                        z.unlink()
                        removed += 1
                cdir = paths.COVERS / aid
                if cdir.is_dir():
                    for c in cdir.iterdir():
                        if c.is_file():
                            c.unlink()
                    cdir.rmdir()
        meta = load_meta()
        for b in manifest["books"]:
            meta.pop(b["aid"], None)
        _save_meta(meta)
    finally:
        conn.close()
    print(f"loose ingest rollback: removed {removed} zips + related catalog rows")
    return 0
