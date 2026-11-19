"""Import execution: turn reviewed directories into chapter zips, reversibly.

This module is the only place that *writes* during an import.  It reuses the
existing single-book writer (:func:`jmlib.ingest.pack_album`) so the on-disk
convention stays one rule: one zip per chapter under ``library/archive/{Aid}/``,
covers extracted from each zip's first image, book cover pointing at chapter 1.

Two hard guarantees:

* **The source directories are never modified.**  Only reads happen under the
  import root; a rollback removes what this run created and nothing else.
* **Nothing runs without an exact scoped token.**  ``build_plan`` is read-only and
  issues a token derived from the server's own recomputation of the batch;
  ``apply`` recomputes it, so a client cannot mint or edit one.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from jmlib import import_scan, ingest, paths, store

SCHEMA = "jmlib.import_plan.v1"
ROLLBACK_SCHEMA = "jmlib.import_rollback.v1"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Selection -> per-book items (pure, read-only)
# ---------------------------------------------------------------------------


def _entry_index(manifest: dict) -> dict[str, dict]:
    return {e["rel_path"]: e for e in manifest.get("entries", [])}


def collect_items(
    manifest: dict, selected: list[str] | None = None, chosen: dict[str, str] | None = None
) -> tuple[list[dict], list[str]]:
    """Group selected directories into one import item per Aid.

    ``chosen`` maps a directory to the Aid a human decided on; without it the
    directory's own structural Aid is used.  Two directories resolving to the
    same Aid are merged into one book (the 1454103 split case) instead of
    producing two books that fight over ``library/archive/{Aid}/``.

    With ``selected=None`` the batch means "everything still pending", and every
    directory carrying a blocking review hint is skipped with a reason instead of
    being imported on a guess.  Naming the directories explicitly overrides that:
    an explicit selection is itself the human decision the hints were asking for.

    A recorded Aid decision (``chosen[rel]``) overrides the hints the same way, per
    directory.  That is what settles a ``suspected_same`` pair without a new state:
    the user answers "same book" by giving both directories one Aid (grouping then
    merges them into one item) or "two books" by giving them different Aids (two
    items, two books).
    """
    index = _entry_index(manifest)
    chosen = chosen or {}
    sweep_all = selected is None
    by_aid: dict[str, dict] = {}
    conflicts: list[str] = []

    for rel in list(index) if sweep_all else (selected or []):
        entry = index.get(rel)
        if entry is None:
            conflicts.append(f"unknown directory: {rel}")
            continue
        decided = bool((chosen.get(rel) or "").strip())
        blocking = [] if decided else import_scan.sweep_blockers(entry)
        if sweep_all and blocking:
            conflicts.append(
                f"{entry['dir']}: needs a human first ({', '.join(blocking)}), "
                f"skipped — record an Aid decision for it, or select this directory "
                f"explicitly to import it"
            )
            continue
        aid = (chosen.get(rel) or entry.get("aid") or "").strip()
        if not aid:
            conflicts.append(f"{entry['dir']}: no Aid resolved, skipped")
            continue
        if not import_scan.AID_RE.match(aid):
            conflicts.append(f"{entry['dir']}: Aid {aid!r} is not 1-7 digits, skipped")
            continue

        chapters: dict[int, list[str]] = {}
        if entry["kind"] == "nested":
            for key, images in entry["chapters"].items():
                chapters[int(key)] = list(images)
        elif entry["flat_images"]:
            # A flat book is one chapter; page order comes from the filename stem.
            chapters[1] = list(entry["flat_images"])
        elif entry["kind"] != "cover_only":
            conflicts.append(f"{entry['dir']}: no body images, skipped")
            continue
        # A cover-only directory is *not* an error on its own: for a split book it
        # is the sibling half that carries the cover, and the body lives in the
        # other directory of the same Aid.  It contributes no chapters here; if no
        # directory of that Aid has a body, the item is dropped below.

        item = by_aid.get(aid)
        if item is None:
            item = by_aid[aid] = {
                "aid": aid,
                "title": entry["dir"],
                "source_dirs": [],
                "chapters": {},
                "images": 0,
                "body_title": False,
            }
        if entry["dir"] not in item["source_dirs"]:
            item["source_dirs"].append(entry["dir"])
        for pindex, images in chapters.items():
            prev = item["chapters"].get(pindex)
            if prev and [str(p) for p in images] != prev:
                conflicts.append(
                    f"Aid {aid} chapter {pindex} appears in more than one directory; "
                    f"kept the first ({item['source_dirs'][0]})"
                )
                continue
            item["chapters"][pindex] = [str(p) for p in images]
            item["images"] += len(images)
            # The display title belongs to the directory that actually has body
            # pages, never to a cover-only sibling of a split directory.
            if not item["body_title"]:
                item["title"] = entry["dir"]
                item["body_title"] = True

    items = [by_aid[aid] for aid in sorted(by_aid)]
    kept: list[dict] = []
    for item in items:
        item.pop("body_title", None)
        if not item["chapters"]:
            # Every directory of this Aid was cover-only: there is nothing to pack.
            conflicts.append(f"{', '.join(item['source_dirs'])}: cover only, no body images anywhere; skipped")
            continue
        item["chapters"] = {str(k): item["chapters"][k] for k in sorted(item["chapters"])}
        existing = paths.ARCHIVE / item["aid"]
        has_tree = existing.is_dir() and any(existing.glob("*.zip"))
        item["in_library"] = has_tree
        item["action"] = "skip-existing" if has_tree else "import"
        item["chapter_count"] = len(item["chapters"])
        item["pages"] = item["images"]
        kept.append(item)
    return kept, conflicts


def _fingerprint(items: list[dict]) -> str:
    payload = [
        [i["aid"], i["source_dirs"], sorted(i["chapters"]), i["images"], i["action"]]
        for i in sorted(items, key=lambda x: x["aid"])
    ]
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _token(items: list[dict], fingerprint: str) -> str:
    n = sum(1 for i in items if i["action"] == "import")
    chapters = sum(i["chapter_count"] for i in items if i["action"] == "import")
    images = sum(i["images"] for i in items if i["action"] == "import")
    return f"IMPORT {n} books / {chapters} chapters / {images} images #{fingerprint}"


def build_plan(manifest: dict, selected: list[str] | None = None, chosen: dict[str, str] | None = None) -> dict:
    """Read-only batch plan with a server-issued scoped confirmation token."""
    items, conflicts = collect_items(manifest, selected, chosen)
    fingerprint = _fingerprint(items)
    to_import = [i for i in items if i["action"] == "import"]
    return {
        "ok": True,
        "schemaVersion": SCHEMA,
        "type": "import",
        "status": "awaiting scoped confirmation",
        "generated_at": _utc(),
        "fingerprint": fingerprint,
        "confirm_token": _token(items, fingerprint),
        "items": items,
        "conflicts": conflicts,
        "summary": {
            "books": len(to_import),
            "chapters": sum(i["chapter_count"] for i in to_import),
            "images": sum(i["images"] for i in to_import),
            "skipped_existing": sum(1 for i in items if i["action"] == "skip-existing"),
            "conflicts": len(conflicts),
        },
        "safety": {
            "deletion": "none — source directories are only read",
            "overwrite": "never — an Aid that already has archives is skipped",
            "reversible": "apply writes data/import_rollback.json; rollback removes only what this run created",
            "confirmation": "apply requires the exact token above",
        },
    }


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _write_rollback(payload: dict, path: Path | None = None) -> None:
    target = path or paths.IMPORT_ROLLBACK
    paths.DATA.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.part")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)


def load_rollback(path: Path | None = None) -> dict | None:
    target = path or paths.IMPORT_ROLLBACK
    if not target.exists():
        return None
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def apply(plan: dict, confirm: str, *, manifest: dict | None = None, rollback_path: Path | None = None) -> dict:
    """Execute a planned batch, only with the exact server-issued token.

    Preconditions are recomputed from the directories named in the plan, so an
    edited or stale plan is refused instead of importing something new.
    """
    if not isinstance(plan, dict) or plan.get("type") != "import":
        return {"ok": False, "status": "invalid", "error": "not an import plan"}
    items = plan.get("items") or []
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        return {"ok": False, "status": "invalid", "error": "plan items are malformed"}

    fingerprint = _fingerprint(
        [
            {
                "aid": i.get("aid", ""),
                "source_dirs": i.get("source_dirs") or [],
                "chapters": i.get("chapters") or {},
                "images": int(i.get("images") or 0),
                "action": i.get("action", "import"),
            }
            for i in items
        ]
    )
    token = _token(
        [
            {
                "action": i.get("action", "import"),
                "chapter_count": len(i.get("chapters") or {}),
                "images": int(i.get("images") or 0),
            }
            for i in items
        ],
        fingerprint,
    )

    if not confirm or not isinstance(confirm, str) or confirm.strip() != token:
        return {
            "ok": False,
            "status": "refused",
            "error": "scoped confirmation mismatch — generic agreement is not accepted",
            "confirm_token": token,
        }
    if (
        plan.get("confirm_token") != token
        or plan.get("fingerprint") != fingerprint
        or plan.get("schemaVersion") != SCHEMA
        or plan.get("status") != "awaiting scoped confirmation"
    ):
        return {
            "ok": False,
            "status": "refused",
            "error": "plan is not a server-issued manifest for this exact batch",
            "confirm_token": token,
        }

    created: list[dict] = []
    skipped: list[dict] = []
    conflicts: list[str] = []

    for item in items:
        aid = str(item.get("aid") or "")
        if item.get("action") != "import":
            skipped.append({"aid": aid, "reason": "already in library (never overwritten)"})
            continue
        chapters = {int(k): [Path(p) for p in v] for k, v in (item.get("chapters") or {}).items()}
        result = ingest.pack_album(
            aid,
            chapters,
            title=item.get("title") or "",
            generation="import",
        )
        if not result["written"]:
            conflicts.extend(f"{aid}: {c}" for c in (result["conflicts"] or ["nothing written"]))
            skipped.append({"aid": aid, "reason": "; ".join(result["conflicts"]) or "nothing written"})
            continue
        created.append(
            {
                "aid": aid,
                "title": item.get("title") or "",
                "source_dirs": item.get("source_dirs") or [],
                "zips": [c["zip_path"] for c in result["chapters"]],
                "pages": result["pages"],
                "cover_path": (result["book"] or {}).get("cover_path"),
            }
        )

    if not created and conflicts:
        return {
            "ok": False,
            "status": "failed",
            "error": "nothing could be imported",
            "conflicts": conflicts,
            "skipped": skipped,
        }

    payload = {
        "schemaVersion": ROLLBACK_SCHEMA,
        "kind": "import",
        "createdAt": _utc(),
        "books": created,
        "skipped": skipped,
        "conflicts": conflicts,
    }
    _write_rollback(payload, rollback_path)

    return {
        "ok": True,
        "status": "executed",
        "message": (
            f"imported {len(created)} books / "
            f"{sum(len(b['zips']) for b in created)} chapters / "
            f"{sum(b['pages'] for b in created)} pages; source directories untouched"
        ),
        "books": created,
        "skipped": skipped,
        "conflicts": conflicts,
        "rollback": paths.rel(rollback_path or paths.IMPORT_ROLLBACK),
    }


def rollback_token(payload: dict) -> str:
    aids = sorted(b["aid"] for b in payload.get("books", []))
    blob = json.dumps([aids, [b.get("zips") for b in payload.get("books", [])]], ensure_ascii=False, sort_keys=True)
    fp = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
    return f"ROLLBACK {len(aids)} books #{fp}"


def rollback(confirm: str, *, payload: dict | None = None, rollback_path: Path | None = None) -> dict:
    """Remove exactly what the recorded import run created.  Sources stay put."""
    ledger_path = rollback_path or paths.IMPORT_ROLLBACK
    payload = payload if payload is not None else load_rollback(ledger_path)
    if not payload or not payload.get("books"):
        return {"ok": False, "status": "invalid", "error": "no import rollback manifest"}
    token = rollback_token(payload)
    if not confirm or not str(confirm).strip() == token:
        return {
            "ok": False,
            "status": "refused",
            "error": "scoped confirmation mismatch — generic agreement is not accepted",
            "confirm_token": token,
        }

    removed_zips = 0
    removed_books = 0
    for book in payload["books"]:
        aid = book["aid"]
        for rel in book.get("zips", []):
            # Never reuse the ledger variable for a file path: shadowing it here
            # would silently skip the ``ledger_path.unlink()`` further down and
            # leave a stale rollback record behind.
            zip_path = paths.ROOT / Path(rel)
            if zip_path.is_file():
                zip_path.unlink()
                removed_zips += 1
        aid_dir = paths.ARCHIVE / aid
        if aid_dir.is_dir() and not any(aid_dir.iterdir()):
            aid_dir.rmdir()
        cover_dir = paths.COVERS / aid
        if cover_dir.is_dir():
            for f in cover_dir.iterdir():
                if f.is_file():
                    f.unlink()
            if not any(cover_dir.iterdir()):
                cover_dir.rmdir()
        store.remove_book(aid)
        removed_books += 1

    meta = ingest.load_meta()
    for book in payload["books"]:
        meta.pop(book["aid"], None)
    ingest._save_meta(meta)
    ledger_path.unlink(missing_ok=True)

    return {
        "ok": True,
        "status": "rolled_back",
        "message": f"removed {removed_books} books / {removed_zips} chapter zips; source directories untouched",
        "books": removed_books,
        "zips": removed_zips,
    }


# ---------------------------------------------------------------------------
# B.1 — hand-add one local directory
# ---------------------------------------------------------------------------
#
# Adding a book by hand is the same write as an import (same writer, same token
# gate, same "sources are never touched" rule), but the batch plan cannot be
# reused as-is: a batch is identified by ``rel_path`` inside a scanned manifest,
# while a hand-add points at one directory the user typed.  The token therefore
# binds the *resolved absolute path* plus the Aid, so a plan cannot be replayed
# against a different folder that happens to have the same name.

SINGLE_SCHEMA = "jmlib.import_single.v1"


def _single_fingerprint(local_path: str, aid: str, chapters: dict[str, list[str]], images: int) -> str:
    # The member lists go into the fingerprint, not just the chapter keys: a plan
    # whose chapter 1 now points at different files is a different plan, and it must
    # be refused rather than silently re-derived.  (``apply_single`` recomputes the
    # item from the directory anyway, so a tampered plan cannot redirect the writer —
    # this makes the tampering *visible* instead of quietly ignored.)
    payload = [local_path, aid, [[k, list(v)] for k, v in sorted(chapters.items())], images]
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _single_token(aid: str, chapters: int, images: int, fingerprint: str) -> str:
    return f"ADD {aid} / {chapters} chapters / {images} images #{fingerprint}"


def _single_item(local_path: Path | str, aid: str, title: str = "") -> tuple[dict | None, list[str]]:
    """Classify one directory into a single-book item (read-only).

    The path is checked against the import-root whitelist here, not only in the
    HTTP layer: ``apply_single`` may be called from the CLI or a test, and a
    caller-supplied path must never describe a directory the project was not
    told it could read.  ``paths.resolve_inside_roots`` returns ``None`` for
    anything outside the roots, so the refusal happens before any filesystem
    call — there is no unvalidated path to walk.
    """
    conflicts: list[str] = []
    aid = (aid or "").strip()
    if not import_scan.AID_RE.match(aid):
        return None, [f"Aid {aid!r} is not 1-7 digits"]
    safe_path = paths.resolve_inside_roots(local_path)
    if safe_path is None:
        return None, [f"local_path is outside the allowed import roots: {local_path}"]
    if not safe_path.is_dir():
        return None, [f"not a directory: {safe_path}"]

    local_path = safe_path
    entry = import_scan.classify_entry(local_path)
    chapters: dict[str, list[str]] = {}
    if entry["kind"] == "nested":
        for key, images in entry["chapters"].items():
            chapters[str(int(key))] = list(images)
    elif entry["flat_images"]:
        # A flat folder is one chapter; page order comes from the filename stem.
        chapters["1"] = list(entry["flat_images"])
    if not chapters:
        return None, [f"{local_path.name}: no body images found (kind={entry['kind']}); nothing to add"]

    image_total = sum(len(v) for v in chapters.values())
    existing = paths.ARCHIVE / aid
    has_tree = existing.is_dir() and any(existing.glob("*.zip"))
    item = {
        "aid": aid,
        "title": (title or "").strip() or entry["dir"],
        "source_dirs": [entry["dir"]],
        "local_path": str(local_path),
        "chapters": {k: chapters[k] for k in sorted(chapters, key=int)},
        "images": image_total,
        "chapter_count": len(chapters),
        "pages": image_total,
        "kind": entry["kind"],
        "hints": import_scan.hints_for(entry),
        "in_library": has_tree,
        "action": "skip-existing" if has_tree else "import",
    }
    if has_tree:
        conflicts.append(
            f"Aid {aid} already has chapter zips in the library; "
            f"adding by hand never overwrites (roll it back first to re-add)"
        )
    return item, conflicts


def plan_single(local_path: str | Path, aid: str, title: str = "") -> dict:
    """Read-only preview of a hand-add, with a server-issued scoped token."""
    path = Path(local_path).expanduser().resolve()
    item, conflicts = _single_item(path, aid, title)
    if item is None:
        return {
            "ok": False,
            "schemaVersion": SINGLE_SCHEMA,
            "type": "add",
            "status": "refused",
            "error": "; ".join(conflicts),
            "conflicts": conflicts,
        }
    fingerprint = _single_fingerprint(item["local_path"], item["aid"], item["chapters"], item["images"])
    return {
        "ok": True,
        "schemaVersion": SINGLE_SCHEMA,
        "type": "add",
        "status": "awaiting scoped confirmation",
        "generated_at": _utc(),
        "fingerprint": fingerprint,
        "confirm_token": _single_token(item["aid"], item["chapter_count"], item["images"], fingerprint),
        "item": item,
        "conflicts": conflicts,
        "safety": {
            "deletion": "none — the source directory is only read",
            "overwrite": "never — an Aid that already has archives is skipped",
            "reversible": f"apply writes {paths.rel(paths.SINGLE_ROLLBACK)}; rollback "
            f"removes only what this run created",
            "confirmation": "apply requires the exact token above",
        },
    }


def apply_single(plan: dict, confirm: str) -> dict:
    """Execute a hand-add by recomputing the plan from the directory itself.

    The client's copy of the plan is only used for its declared path and Aid; the
    chapter lists are re-derived server-side, so editing the plan cannot point the
    writer at different files.
    """
    if not isinstance(plan, dict) or plan.get("type") != "add":
        return {"ok": False, "status": "invalid", "error": "not a single-add plan"}
    item_in = plan.get("item") or {}
    raw_path = str(item_in.get("local_path") or "").strip()
    if not raw_path:
        return {"ok": False, "status": "invalid", "error": "plan carries no local_path"}

    item, conflicts = _single_item(Path(raw_path), str(item_in.get("aid") or ""), str(item_in.get("title") or ""))
    if item is None:
        return {"ok": False, "status": "refused", "error": "; ".join(conflicts), "conflicts": conflicts}

    fingerprint = _single_fingerprint(item["local_path"], item["aid"], item["chapters"], item["images"])
    token = _single_token(item["aid"], item["chapter_count"], item["images"], fingerprint)
    if not confirm or not isinstance(confirm, str) or confirm.strip() != token:
        return {
            "ok": False,
            "status": "refused",
            "error": "scoped confirmation mismatch — generic agreement is not accepted",
            "confirm_token": token,
        }
    if (
        plan.get("confirm_token") != token
        or plan.get("fingerprint") != fingerprint
        or plan.get("schemaVersion") != SINGLE_SCHEMA
        or plan.get("status") != "awaiting scoped confirmation"
    ):
        return {
            "ok": False,
            "status": "refused",
            "error": "plan is not a server-issued manifest for this exact directory",
            "confirm_token": token,
        }
    # The token only proves *which directory and Aid* were confirmed.  The reviewer
    # also read a chapter/image list, so a plan whose list no longer matches the
    # directory must be refused instead of silently importing the server's version —
    # otherwise "what I approved" and "what got written" can differ without a word.
    shown = plan.get("item") or {}
    if (
        shown.get("chapters") != item["chapters"]
        or shown.get("images") != item["images"]
        or shown.get("kind") != item["kind"]
        or shown.get("action") != item["action"]
    ):
        return {
            "ok": False,
            "status": "refused",
            "error": "plan no longer matches this directory — regenerate the preview and confirm the new token",
            "confirm_token": token,
        }
    if item["action"] != "import":
        return {
            "ok": False,
            "status": "skipped",
            "error": f"Aid {item['aid']} already exists in the library; nothing was written",
            "conflicts": conflicts,
        }

    chapters = {int(k): [Path(p) for p in v] for k, v in item["chapters"].items()}
    result = ingest.pack_album(
        item["aid"],
        chapters,
        title=item["title"],
        generation="manual",
    )
    if not result["written"]:
        return {
            "ok": False,
            "status": "failed",
            "error": "; ".join(result["conflicts"]) or "nothing written",
            "conflicts": result["conflicts"],
        }

    created = [
        {
            "aid": item["aid"],
            "title": item["title"],
            "source_dirs": item["source_dirs"],
            "zips": [c["zip_path"] for c in result["chapters"]],
            "pages": result["pages"],
            "cover_path": (result["book"] or {}).get("cover_path"),
            "added_at": _utc(),
        }
    ]
    # Append, never replace: adding a second book by hand must not wipe the record
    # that would let the user undo the first one.
    ledger = load_single_rollback() or {
        "schemaVersion": ROLLBACK_SCHEMA,
        "kind": "manual",
        "createdAt": _utc(),
        "books": [],
    }
    ledger["books"] = (ledger.get("books") or []) + created
    ledger["updatedAt"] = _utc()
    _write_rollback(ledger, paths.SINGLE_ROLLBACK)

    return {
        "ok": True,
        "status": "executed",
        "message": (
            f"added {item['aid']} / {item['chapter_count']} chapters / "
            f"{item['pages']} pages; source directory untouched"
        ),
        "books": created,
        "conflicts": conflicts,
        "rollback": paths.rel(paths.SINGLE_ROLLBACK),
    }


def load_single_rollback() -> dict | None:
    return load_rollback(paths.SINGLE_ROLLBACK)


def single_rollback_token(payload: dict, aid: str = "") -> str:
    """Token covering the hand-added books, optionally narrowed to one Aid."""
    books = payload.get("books") or []
    if aid:
        books = [b for b in books if b.get("aid") == aid]
    aids = sorted(b["aid"] for b in books)
    blob = json.dumps([aids, [b.get("zips") for b in books]], ensure_ascii=False, sort_keys=True)
    fp = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
    return f"ROLLBACK {len(aids)} books #{fp}"


def rollback_single(confirm: str, aid: str = "") -> dict:
    """Undo hand-added books — one Aid, or everything the ledger still records.

    The ledger keeps every hand-add; undoing one book therefore has to *narrow*
    the payload instead of deleting the whole file, otherwise rolling back the
    second add would forget the first.
    """
    payload = load_single_rollback()
    if not payload or not payload.get("books"):
        return {"ok": False, "status": "invalid", "error": "no import rollback manifest"}
    books = payload["books"]
    if aid:
        books = [b for b in books if b.get("aid") == aid]
        if not books:
            return {"ok": False, "status": "invalid", "error": f"no hand-added record for Aid {aid}"}
    scoped = {**payload, "books": books}
    token = single_rollback_token(payload, aid)
    if not confirm or not str(confirm).strip() == token:
        return {
            "ok": False,
            "status": "refused",
            "error": "scoped confirmation mismatch — generic agreement is not accepted",
            "confirm_token": token,
        }

    result = rollback(token, payload=scoped, rollback_path=paths.SINGLE_ROLLBACK)
    if not result.get("ok"):
        return result
    rest = [b for b in payload["books"] if b not in books]
    if rest:
        _write_rollback({**payload, "books": rest, "updatedAt": _utc()}, paths.SINGLE_ROLLBACK)
    return result
