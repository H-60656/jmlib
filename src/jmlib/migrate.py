"""P0 archive migration: plan (read-only) -> apply (same-volume renames) -> rollback.

Three explicit operations, all same-volume ``os.rename`` (no copying, no deletion):

* ``plan``     — read-only: scan the legacy archive, write migration_manifest.json
                 (one old->new entry per zip + loose queue + sidecars) and the
                 reverse rollback_manifest.json.
* ``apply``    — one whole-tree rename into library/_incoming, then per-file
                 renames into the canonical layout. Zero unlink().
* ``rollback`` — reverse apply using rollback_manifest.json.

Safety invariants
-----------------
* No file is ever deleted; empty source directories may remain.
* Every zip maps to exactly one destination; target collisions are diverted to
  _unidentified/ and counted (never overwritten).
* File bytes are never read or rewritten on apply: os.rename on one volume only.
* Only Aid found as a *whole* parent-directory name or *whole* stem is trusted.
  Magazine issue numbers embedded in longer names are NOT treated as Aid.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from jmlib import paths
from jmlib.aid import COVER_FILE_RE

# Legacy *source* layouts only.  There a whole component that reads as an Aid may
# also be a chapter directory (``{title}/1/``, ``{title}/2/``), so this scan keeps
# the 4-digit floor instead of the identity contract in :mod:`jmlib.aid`: short
# numbers are far too common as chapter/volume names to be accepted on sight.
LEGACY_AID_RE = re.compile(r"^\d{4,7}$")
CHAP_RE = re.compile(r"^\d+$")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
SCHEMA = "jmlib.migration.v1"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _classify(old_rel: str) -> tuple[str, str | None, int | None]:
    """Return (kind, aid, pindex); kind in {'strict','unresolved'}.

    Strict Aid is only accepted from a whole parent-dir name (Aid/chapter zips)
    or a whole stem (Aid-direct zips).  This deliberately excludes issue numbers
    like ``COMIC 失乐天 2022年6月号`` / ``WEEKLY快乐天 2024 No.28``.
    """
    parts = old_rel.split("/")
    stem = Path(old_rel).stem
    parent = parts[-2] if len(parts) >= 2 else ""
    if LEGACY_AID_RE.match(parent) and CHAP_RE.match(stem):
        return "strict", parent, int(stem)
    if LEGACY_AID_RE.match(stem):
        return "strict", stem, 1
    return "unresolved", None, None


def _scan_loose(loose_root: Path | None) -> list[dict]:
    if loose_root is None or not loose_root.is_dir():
        return []
    books: list[dict] = []
    for d in sorted(p for p in loose_root.iterdir() if p.is_dir()):
        jpgs = [p for p in d.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES]
        aids: set[str] = set()
        chapters: set[str] = set()
        for p in d.iterdir():
            if p.is_dir() and LEGACY_AID_RE.match(p.name):
                aids.add(p.name)
                chapters.update(c.name for c in p.iterdir() if c.is_dir())
            m = COVER_FILE_RE.match(p.name)
            if m:
                aids.add(m.group(1))
        books.append(
            {
                "name": d.name,
                "rel": d.name,
                "jpgCount": len(jpgs),
                "aids": sorted(aids),
                "chapterDirs": sorted(chapters),
                "note": "queue only; not moved in P0 (packed/ingested in P4)",
            }
        )
    return books


def build_manifest(src: Path, loose: Path | None) -> dict:
    """Pure, read-only: build the full migration manifest from a legacy root."""
    zips = sorted(
        (p for p in src.rglob("*") if p.is_file() and p.suffix.lower() == ".zip"),
        key=lambda p: p.relative_to(src).as_posix(),
    )
    sidecars = sorted(
        p.relative_to(src).as_posix() for p in src.rglob("*") if p.is_file() and p.suffix.lower() != ".zip"
    )

    entries: list[dict] = []
    used: dict[str, str] = {}  # new_rel -> first old_rel that claimed it
    collisions = 0
    strict = 0
    total_bytes = 0
    for z in zips:
        old_rel = z.relative_to(src).as_posix()
        size = z.stat().st_size
        total_bytes += size
        kind, aid, pindex = _classify(old_rel)
        reason = None
        if kind == "strict":
            new_rel = f"archive/{aid}/{pindex}.zip"
            if new_rel in used:
                collisions += 1
                kind = "unresolved"
                reason = f"target collision with {used[new_rel]}"
                new_rel = f"_unidentified/{old_rel}"
            else:
                used[new_rel] = old_rel
        else:
            reason = "no reliable Aid in path"
            new_rel = f"_unidentified/{old_rel}"

        e: dict = {"old": old_rel, "new": new_rel, "kind": kind, "size": size}
        if kind == "strict":
            e.update(aid=aid, pindex=pindex, generation=old_rel.split("/")[0])
            strict += 1
        else:
            e["reason"] = reason
        entries.append(e)

    unresolved = len(entries) - strict
    return {
        "schemaVersion": SCHEMA,
        "kind": "migration",
        "createdAt": _utc(),
        "source": {"root": str(src), "posix": src.as_posix()},
        "loose": ({"root": str(loose), "posix": loose.as_posix()} if loose else None),
        "target": {"library": paths.LIBRARY.as_posix(), "incoming": paths.INCOMING.as_posix()},
        "summary": {
            "total": len(entries),
            "strict": strict,
            "unresolved": unresolved,
            "collisions": collisions,
            "totalBytes": total_bytes,
            "sidecars": len(sidecars),
            "looseBooks": len(_scan_loose(loose)),
        },
        "entries": entries,
        # sidecars keep their relative path under LIBRARY/ (e.g. _zip_signatures.json,
        # 黑名单.csv).  Their bytes are moved verbatim; only a derived rebound file
        # is generated separately by apply().
        "sidecars": [{"old": s, "new": s} for s in sidecars],
        "looseBooks": _scan_loose(loose),
        "signatureRebind": {
            "note": "content-hash keys stay valid; value paths are remapped via old->new into _zip_signatures.rebound.json by apply()",
        },
        "safety": {
            "deletion": "none — apply/rollback only os.rename; empty source dirs may remain",
            "overwrite": "never — a pre-existing destination aborts the operation",
            "reversible": "rollback_manifest.json reverses every rename",
        },
    }


def build_rollback(m: dict) -> dict:
    return {
        "schemaVersion": SCHEMA,
        "kind": "rollback",
        "createdAt": m["createdAt"],
        "source": m["source"],
        "target": m["target"],
        "entries": [{"from": e["new"], "to": e["old"]} for e in m["entries"]],
        "sidecars": [{"from": s["new"], "to": s["old"]} for s in m["sidecars"]],
        "note": "reverse of migration_manifest.json; run `jmlib migrate --rollback`",
    }


def plan(source, loose) -> int:
    if paths.INCOMING.exists():
        print("migrate --plan: library/_incoming exists (migration already started); --rollback first", file=sys.stderr)
        return 1
    src = Path(source) if source else None
    if src is None or not src.is_dir():
        print(f"migrate --plan: --source must be an existing directory (got {source!r})", file=sys.stderr)
        return 2
    loose_root = Path(loose) if loose else None
    m = build_manifest(src, loose_root)
    paths.ensure_dirs()
    paths.MIGRATION_MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.ROLLBACK_MANIFEST.write_text(json.dumps(build_rollback(m), ensure_ascii=False, indent=2), encoding="utf-8")
    s = m["summary"]
    print("migrate --plan (read-only; nothing moved)")
    print(f"  source      : {m['source']['posix']}")
    print(
        f"  zips        : {s['total']}  strict={s['strict']}  unresolved={s['unresolved']}  collisions={s['collisions']}"
    )
    print(f"  total bytes : {s['totalBytes']:,}  sidecars={s['sidecars']}  looseBooks={s['looseBooks']}")
    print(f"  manifest    : {paths.MIGRATION_MANIFEST}")
    print(f"  rollback    : {paths.ROLLBACK_MANIFEST}")
    return 0


def _rename(s: Path, d: Path) -> str:
    """One same-volume rename; never overwrites. Returns 'moved'|'already'."""
    if d.exists() and not s.exists():
        return "already"
    if not s.exists():
        raise FileNotFoundError(f"source missing: {s}")
    if d.exists():
        raise FileExistsError(f"refusing to overwrite: {d}")
    d.parent.mkdir(parents=True, exist_ok=True)
    os.rename(s, d)
    return "moved"


def _rebind(m: dict) -> dict:
    sig = paths.LIBRARY / "_zip_signatures.json"
    if not sig.is_file():
        return {"keys": 0, "remapped": 0, "unmatched": 0, "file": None}
    data = json.loads(sig.read_text(encoding="utf-8"))
    old_to_new = {e["old"]: e["new"] for e in m["entries"]}
    rebound: dict[str, str] = {}
    remapped = 0
    unmatched = 0
    for key, val in data.items():
        k = str(val).replace("\\", "/")
        if k in old_to_new:
            rebound[key] = old_to_new[k]
            remapped += 1
        else:
            rebound[key] = str(val)
            unmatched += 1
    out = paths.LIBRARY / "_zip_signatures.rebound.json"
    out.write_text(json.dumps(rebound, ensure_ascii=False, indent=2), encoding="utf-8")
    # Counters and the output path are different types: keeping them in one dict
    # made mypy widen the value type to ``int | str | None`` and then reject the
    # ``+=`` on what it saw as a possibly-``None`` counter.
    return {"keys": len(data), "remapped": remapped, "unmatched": unmatched, "file": paths.rel(out)}


def _verify_after(m: dict) -> tuple[list[str], list[str]]:
    missing: list[str] = []
    mismatch: list[str] = []
    for e in m["entries"]:
        d = paths.LIBRARY / e["new"]
        if not d.is_file():
            missing.append(e["new"])
        elif d.stat().st_size != e["size"]:
            mismatch.append(e["new"])
    return missing, mismatch


def apply() -> int:
    if not paths.MIGRATION_MANIFEST.is_file():
        print("migrate --apply: no manifest; run --plan first", file=sys.stderr)
        return 2
    m = json.loads(paths.MIGRATION_MANIFEST.read_text(encoding="utf-8"))
    src = Path(m["source"]["root"])
    inc = paths.INCOMING
    paths.ensure_dirs()

    # Step 1: one whole-tree rename (the only cross-workspace operation).
    if src.is_dir() and not inc.exists():
        inc.parent.mkdir(parents=True, exist_ok=True)
        os.rename(src, inc)
        print(f"staged: {src} -> {inc}")
    elif inc.is_dir():
        print(f"staged: {inc} already present (resuming)")
    else:
        print(f"migrate --apply: neither source {src} nor staging {inc} exists", file=sys.stderr)
        return 1

    # Step 2: sidecars, verbatim.
    for sc in m["sidecars"]:
        _rename(inc / sc["old"], paths.LIBRARY / sc["new"])
    # Step 3: zips, into canonical layout.
    for e in m["entries"]:
        _rename(inc / e["old"], paths.LIBRARY / e["new"])

    # Step 4: derived signature rebound (original kept pristine).
    rb = _rebind(m)

    # Step 5: verify every byte landed at exactly one destination.
    missing, mismatch = _verify_after(m)
    s = m["summary"]
    print("migrate --apply done")
    print(f"  zips expected={s['total']}  strict={s['strict']}  unresolved={s['unresolved']}")
    print(f"  signature rebound: keys={rb['keys']} remapped={rb['remapped']} unmatched={rb['unmatched']}")
    print(f"  verify: missing={len(missing)}  size_mismatch={len(mismatch)}")
    if missing or mismatch:
        for x in (missing + mismatch)[:20]:
            print(f"    ! {x}", file=sys.stderr)
        print("run `jmlib migrate --rollback` to restore", file=sys.stderr)
        return 1
    return 0


def rollback() -> int:
    if not paths.ROLLBACK_MANIFEST.is_file():
        print("migrate --rollback: no rollback manifest", file=sys.stderr)
        return 2
    rb = json.loads(paths.ROLLBACK_MANIFEST.read_text(encoding="utf-8"))
    inc = paths.INCOMING
    if not inc.is_dir():
        print("migrate --rollback: staging dir missing; nothing to reverse", file=sys.stderr)
        return 1
    for e in rb["entries"]:
        _rename(paths.LIBRARY / e["from"], inc / e["to"])
    for sc in rb["sidecars"]:
        _rename(paths.LIBRARY / sc["from"], inc / sc["to"])
    src = Path(rb["source"]["root"])
    if not src.exists():
        src.parent.mkdir(parents=True, exist_ok=True)
        os.rename(inc, src)
        print(f"restored: {inc} -> {src}")
    else:
        print(f"source {src} already exists; staging {inc} left in place", file=sys.stderr)
        return 1
    print("migrate --rollback done (zero deletion; derived rebound file left in library/)")
    return 0
