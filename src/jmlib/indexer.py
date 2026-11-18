"""Rebuild the SQLite catalog + cover cache from ``library/`` bytes.

Physical archive is the source of truth; ``data/migration_manifest.json`` is
used only to recover display titles / generations offline (no network, no
guessing).  ``metadata.sqlite`` is deliberately NOT used: it is a failed-state
residue (2282 rows, every ``status='tag-failed'``, every ``tags='{}'``, SHA-1
hashes that do not match the archive's MD5 signature keys).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from jmlib import cover, ingest, paths, store
from jmlib.aid import AID_RE

PINDEX_RE = re.compile(r"^\d+$")
GENERATION_ONLY_RE = re.compile(r"^(?:JM(?:\(\d+\))*|JM\(Android\)|长篇)$", re.I)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _manifest() -> dict:
    if not paths.MIGRATION_MANIFEST.exists():
        return {"entries": []}
    loaded = json.loads(paths.MIGRATION_MANIFEST.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {"entries": []}


def _title_folder(old: str) -> str:
    """The user's original folder name, with a trailing {Aid} dir removed."""
    dirs = old.split("/")[:-1]
    if dirs and AID_RE.match(dirs[-1]):
        dirs = dirs[:-1]
    title = dirs[-1] if dirs else ""
    # 11 Aid-direct Android zips have no title folder, only 'JM(Android)'.
    # A generation name is not a title: leave it blank rather than mislabel.
    return "" if GENERATION_ONLY_RE.match(title) else title


def _offline_metadata(m: dict) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    titles: dict[str, str] = {}
    generations: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for e in m.get("entries", []):
        if e["kind"] == "strict":
            aid = str(e["aid"])
            generations.setdefault(aid, str(e.get("generation", "")))
            titles.setdefault(aid, _title_folder(e["old"]))
        else:
            reasons[e["new"]] = str(e.get("reason", "no reliable Aid in path"))
    return titles, generations, reasons


def scan_chapter(aid: str, pindex: int, zip_file: Path) -> dict:
    info = cover.inspect_chapter(zip_file, aid, pindex)
    return {
        "aid": aid,
        "pindex": pindex,
        "zip_path": paths.rel(zip_file),
        "page_count": info["page_count"],
        "cover_path": info["cover_path"],
    }


def scan_catalog(m: dict) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    titles, generations, reasons = _offline_metadata(m)
    # P4 sidecar: physical archive is still the source of truth for chapters,
    # but rich metadata captured at download/loose-ingest time must survive a
    # rebuild.  Import here (not at module top) keeps the dependency one-way.
    rich = ingest.load_meta()
    books: list[dict] = []
    chapters: list[dict] = []
    broken: list[str] = []

    for aid_dir in sorted(p for p in paths.ARCHIVE.iterdir() if p.is_dir()):
        aid = aid_dir.name
        if not AID_RE.match(aid):
            broken.append(f"non-Aid directory skipped: {paths.rel(aid_dir)}")
            continue
        book_chapters: list[dict] = []
        for z in sorted(aid_dir.glob("*.zip")):
            if not PINDEX_RE.match(z.stem):
                broken.append(f"non-numeric chapter skipped: {paths.rel(z)}")
                continue
            pindex = int(z.stem)
            try:
                book_chapters.append(scan_chapter(aid, pindex, z))
            except Exception as exc:  # a single bad zip must not abort the rebuild
                broken.append(f"unreadable zip {paths.rel(z)}: {type(exc).__name__}: {exc}")
        if not book_chapters:
            continue
        book_chapters.sort(key=lambda c: c["pindex"])
        now = _utc()
        info = rich.get(aid, {})
        books.append(
            {
                "aid": aid,
                "title": info.get("title") or titles.get(aid, ""),
                "author": info.get("author", ""),
                "tags": json.dumps(list(info.get("tags", [])), ensure_ascii=False),
                "generation": info.get("generation") or generations.get(aid, ""),
                # Book cover is a pointer to chapter 1's cover — no extra file.
                "cover_path": book_chapters[0]["cover_path"],
                "created_at": now,
            }
        )
        chapters.extend(book_chapters)

    unresolved: list[dict] = []
    if paths.UNIDENTIFIED.exists():
        for z in sorted(paths.UNIDENTIFIED.rglob("*.zip")):
            rel = z.relative_to(paths.LIBRARY).as_posix()  # _unidentified/...
            parts = z.relative_to(paths.UNIDENTIFIED).parts
            unresolved.append(
                {
                    "rel_path": paths.rel(z),
                    "reason": reasons.get(rel, "no reliable Aid in path"),
                    "generation": parts[0] if parts else "",
                    "size": z.stat().st_size,
                }
            )

    return books, chapters, unresolved, broken


def rebuild() -> int:
    paths.ensure_dirs()
    m = _manifest()
    books, chapters, unresolved, broken = scan_catalog(m)
    stats = store.replace_catalog(books, chapters, unresolved)
    for detail in broken:
        store.add_finding("unreadable_chapter", detail.split(":")[0], detail)
    print(
        f"index: books={stats['books']} chapters={stats['chapters']} "
        f"unresolved={stats['unresolved']} pages={stats['page_total']} "
        f"broken={len(broken)}"
    )
    if broken:
        for detail in broken[:20]:
            print("  !", detail)
    ok = stats["books"] > 0 and stats["chapters"] == len(list(paths.ARCHIVE.glob("*/*.zip"))) and not broken
    print("index rebuild", "PASS" if ok else "INCOMPLETE")
    return 0 if ok else 1
