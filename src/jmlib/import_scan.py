"""A.1 read-only scan: classify a library-external manga root into an import manifest.

Owner boundary: this module only *reads* the filesystem and writes one JSON
manifest under ``data/``.  It never touches ``library/``, never writes the DB,
never contacts the network, and never moves or deletes a source file.

Why a new owner (see the 2026-11-17 plan Existence Check): ``indexer.scan_catalog``
only walks ``library/archive`` (already-ingested books) and ``ingest.loose_plan``
is hard-wired to the P4 loose shape (Aid recovered from a ``{Aid}_cover.jpg``
sibling).  The queue scanned here lives *outside* the library and mixes flat
image dirs, ``{Aid}/{Pindex}/`` trees, cover-only dirs, split simplified/
traditional duplicates, magazine-issue folders and Aid-less dirs — none of which
either existing owner models.

Classification vocabulary (``Entry.kind``):
  * ``flat``      images directly in the directory, no Aid subdirs
  * ``nested``    one or more ``{Aid}/{Pindex}/`` chapter dirs
  * ``cover_only`` a ``{Aid}_cover.jpg`` but no chapter images (split duplicate)
  * ``empty``     no images at all

Aid evidence (``Entry.aid_source``): ``subdir`` | ``cover_file`` | ``name_digits``
| ``none``.  A magazine-issue folder (``COMIC 失乐天 2022年6月号``) yields
``name_digits`` hits that are *not* album ids, so those entries carry
``magazine_like: True`` and are forced to manual confirmation downstream.

Review hints (``Entry.hints``) are *evidence for a human*, never instructions: a
hint says "look at this", and only :mod:`jmlib.import_apply` decides what blocks
an automatic selection.  The canonical strings live here because the scan is what
discovers them; :mod:`jmlib.idres` re-exports them for the decision layer.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from jmlib import paths
from jmlib.aid import AID_RE, HINT_RE, cover_aid

SCHEMA = "jmlib.import_manifest.v1"
PINDEX_RE = re.compile(r"^\d+$")
# Looser than ``jmlib.aid.COVER_FILE_RE``: any ``<digits>_cover.<ext>`` is a cover
# file, never a page.  Some short (3-digit) album ids exist, e.g. ``187_cover.jpg``;
# counting such a file as a page would make a cover-only folder look like a flat book.
COVER_LIKE_RE = re.compile(r"^\d+_cover\.\w+$", re.I)
# Weak evidence only: ``jmlib.aid.HINT_RE`` keeps the 4-digit floor that a bare
# number in a folder name needs before it may outrank a real title.
NAME_DIGITS_RE = HINT_RE
# Magazine issue markers: these make a numeric hit *not* an album id.
MAGAZINE_RE = re.compile(
    r"(COMIC\s*[A-Za-z\u3040-\u30ff\u4e00-\u9fff]|年\s*\d{1,2}\s*月号|月号|増刊|新装版)",
    re.I,
)
_NUM = re.compile(r"\d+")

# Review hints.  Canonical strings live here (the scan is what discovers them);
# :mod:`jmlib.idres` re-exports them so the decision layer and the API agree.
HINT_NO_AID = "no_aid"
HINT_INVALID_AID = "invalid_aid"
HINT_WEAK_SOURCE = "weak_aid_source"
HINT_MAGAZINE = "magazine_like"
HINT_COVER_ONLY = "cover_only"
HINT_DUPLICATE_AID = "duplicate_aid"
HINT_SUSPECTED_SAME = "suspected_same"
HINT_ALREADY_IN_LIBRARY = "already_in_library"
HINT_EMPTY = "empty_dir"

# Hints that make an entry a question rather than a task.  None of them is an
# error: each one means "a human has to answer this before the entry can be swept
# into a batch" — an Aid read out of a free-form name, a magazine issue number that
# happens to look like an Aid, a name that is probably the same book as another
# directory, or bytes that are already in the library.
#
# ``cover_only`` and ``duplicate_aid`` are deliberately *not* here: those two are
# resolved by grouping (a cover-only half joins its body-carrying sibling to form
# one book), so they carry evidence without blocking anything.
REVIEW_HINTS = (
    HINT_NO_AID,
    HINT_INVALID_AID,
    HINT_WEAK_SOURCE,
    HINT_MAGAZINE,
    HINT_SUSPECTED_SAME,
    HINT_ALREADY_IN_LIBRARY,
)

# Subset of REVIEW_HINTS that stops a directory from joining an *unpicked* batch.
#
# ``weak_aid_source`` is deliberately excluded.  An Aid read from ``{Aid}_cover.jpg``
# or from digits in the folder name is the very evidence the P4 ingest already
# trusted, and the cover-only half of a split book has nothing *but* that evidence —
# blocking it would make the 1454103 merge impossible and force a human to tick
# every flat directory by hand.  It is a label to look at, not a gate.
#
# ``already_in_library`` is excluded for the same practical reason: the Aid is
# certain, and ``skip-existing`` already refuses to overwrite those bytes.
BLOCKING_HINTS = (
    HINT_NO_AID,
    HINT_INVALID_AID,
    HINT_MAGAZINE,
    HINT_SUSPECTED_SAME,
)


def sweep_blockers(entry: dict) -> list[str]:
    """Hints that stop ``entry`` from being swept into an unpicked batch.

    ``magazine_like`` is the interesting case: it is always a label, but it only
    *blocks* when the Aid did not come from the directory structure.  A folder named
    ``… (COMIC アンスリウム 2025年…)`` whose ``{Aid}/{Pindex}/`` tree proves the Aid
    is a normal book that happens to have been published in a magazine — refusing it
    would ask the user to confirm five identities that are already certain.  The
    thing worth blocking is a *digit read out of the issue number*, which is exactly
    ``magazine_like`` plus a non-structural Aid.
    """
    hints = entry.get("hints") or []
    blockers = [h for h in hints if h in BLOCKING_HINTS and h != HINT_MAGAZINE]
    if HINT_MAGAZINE in hints and entry.get("aid_source") != "subdir":
        blockers.append(HINT_MAGAZINE)
    return blockers


# Image suffixes duplicated from cover.IMAGE_SUFFIXES on purpose: this module must
# stay importable without touching the library, and the set is a filesystem fact.
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _numeric_key(p: Path) -> tuple[int, str]:
    m = _NUM.search(Path(p).stem)
    return (int(m.group()) if m else 0, Path(p).name)


def _images(directory: Path) -> list[Path]:
    """Page images in ``directory``.

    ``{Aid}_cover.jpg`` files are *excluded*: they are the P4 loose convention for
    a standalone cover, not a page.  Counting them would make a cover-only split
    duplicate look like a one-page flat book.
    """
    try:
        return sorted(
            (
                p
                for p in directory.iterdir()
                if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES and not COVER_LIKE_RE.match(p.name)
            ),
            key=_numeric_key,
        )
    except OSError:
        return []


def _subdirs(directory: Path) -> list[Path]:
    try:
        return sorted((p for p in directory.iterdir() if p.is_dir()), key=lambda p: p.name)
    except OSError:
        return []


def _dir_bytes(directory: Path) -> int:
    total = 0
    try:
        for p in directory.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0
    return total


def aid_from_dirname(name: str) -> str | None:
    """Extract a *structural* Aid from a directory name (``{Aid}_cover`` / ``[x] {Aid}``).

    Returns None when the only numeric hit looks like a magazine issue number.
    Shared by ``ingest`` and the import pipeline so the rule has exactly one home.
    """
    cover = cover_aid(name)
    if cover:
        return cover
    if MAGAZINE_RE.search(name):
        return None
    hits = NAME_DIGITS_RE.findall(name)
    if len(hits) == 1:
        return str(hits[0])
    return None


def classify_entry(directory: Path) -> dict:
    """Classify one top-level directory without writing anything.

    Layout is decided *structurally* (a numeric dir holding numeric chapter dirs
    is an Aid dir) while Aid *validity* is the shared contract (``jmlib.aid``,
    1–7 digits) — short ids are real albums, so ``187/1/`` below is accepted
    rather than flagged.  Structure alone is still not proof of a book: the
    review hints still decide whether a human must look.
    ``[砂漠] … (COMIC LO 2017年7月号)`` holds ``187/1/`` with 21 pages: it must
    count those pages and still be flagged for review instead of being silently
    reported as a one-page flat book.
    """
    name = directory.name
    files = []
    try:
        files = [p for p in directory.iterdir() if p.is_file()]
    except OSError:
        files = []
    cover_hits = []
    for f in files:
        structurally = cover_aid(f.name)
        if structurally:
            cover_hits.append({"aid": structurally, "file": str(f)})
        elif COVER_LIKE_RE.match(f.name):
            # A cover file with an out-of-range id still is a cover, never a page.
            cover_hits.append({"aid": f.name.split("_")[0], "file": str(f)})

    subdirs = _subdirs(directory)
    numeric_subdirs = [s for s in subdirs if PINDEX_RE.match(s.name)]
    aid_subdirs = [s for s in numeric_subdirs if any(PINDEX_RE.match(c.name) and _images(c) for c in _subdirs(s))]
    pindex_subdirs = [s for s in numeric_subdirs if s not in aid_subdirs]
    flat_images = _images(directory)

    chapters: dict[str, list[str]] = {}
    if aid_subdirs:
        for aid_dir in aid_subdirs:
            for chap in _subdirs(aid_dir):
                if not PINDEX_RE.match(chap.name):
                    continue
                imgs = _images(chap)
                if imgs:
                    chapters.setdefault(chap.name, []).extend(str(p) for p in imgs)
    elif pindex_subdirs and not flat_images:
        for chap in pindex_subdirs:
            imgs = _images(chap)
            if imgs:
                chapters.setdefault(chap.name, []).extend(str(p) for p in imgs)

    if chapters:
        kind = "nested"
    elif cover_hits and not flat_images:
        kind = "cover_only"
    elif flat_images:
        kind = "flat"
    else:
        kind = "empty"

    # --- Aid evidence, strongest first -----------------------------------
    aid: str | None = None
    aid_source = "none"
    if aid_subdirs:
        aid, aid_source = aid_subdirs[0].name, "subdir"
    elif len(cover_hits) == 1:
        aid, aid_source = cover_hits[0]["aid"], "cover_file"
    else:
        guessed = aid_from_dirname(name)
        if guessed:
            aid, aid_source = guessed, "name_digits"

    # Structural evidence with an out-of-range id (e.g. ``187``) is still useful
    # for review, but it must never be treated as a confirmed album id.
    aid_valid = bool(aid and AID_RE.match(aid))
    image_count = sum(len(v) for v in chapters.values()) or len(flat_images)
    entry = {
        "dir": name,
        "rel_path": str(directory),
        "kind": kind,
        "aid": aid,
        "aid_source": aid_source,
        "aid_valid": aid_valid,
        "cover_files": [c["file"] for c in cover_hits],
        "aid_subdirs": [s.name for s in aid_subdirs],
        "chapter_count": len(chapters),
        "image_count": image_count,
        "chapters": {k: chapters[k] for k in sorted(chapters, key=int)},
        "flat_images": [str(p) for p in flat_images] if kind == "flat" else [],
        "bytes": _dir_bytes(directory),
        "magazine_like": bool(MAGAZINE_RE.search(name)),
        "has_body": bool(chapters) or bool(flat_images),
    }
    return entry


def _entry_hints(entry: dict, duplicate_aids: set[str] | None = None, suspected: set[str] | None = None) -> list[str]:
    """Review hints for one entry, most-concrete first.

    Hints are evidence, never instructions: ``import_apply`` decides what actually
    blocks a batch, and the panel only renders these as labels.  Keeping the list
    here means there is exactly one place that decides what "needs a human" means.
    """
    hints: list[str] = []
    aid = entry.get("aid") or ""
    if not aid:
        hints.append(HINT_NO_AID)
    elif not entry.get("aid_valid", True):
        hints.append(HINT_INVALID_AID)
    elif entry.get("aid_source") != "subdir":
        # The Aid came from a cover filename or from digits in the name, not from
        # the directory structure.  Weak evidence: confirm before treating it as
        # identity.
        hints.append(HINT_WEAK_SOURCE)
    if entry.get("magazine_like"):
        hints.append(HINT_MAGAZINE)
    if aid and duplicate_aids and aid in duplicate_aids:
        hints.append(HINT_DUPLICATE_AID)
    if entry["dir"] in (suspected or set()):
        hints.append(HINT_SUSPECTED_SAME)
    if entry.get("kind") == "cover_only":
        hints.append(HINT_COVER_ONLY)
    elif entry.get("kind") == "empty":
        hints.append(HINT_EMPTY)
    if entry.get("in_library"):
        hints.append(HINT_ALREADY_IN_LIBRARY)
    return hints


def hints_for(entry: dict) -> list[str]:
    """Review hints for a single entry, without a whole-queue context.

    ``scan`` adds duplicate/suspected hints because they only exist across
    directories; a lone :func:`classify_entry` result has no such context, so this
    wrapper reports the per-directory hints (no Aid, weak/invalid Aid, magazine
    name, cover only, empty) that a hand-add preview needs.
    """
    return _entry_hints(entry)


def archive_aids() -> set[str]:
    """Aids that already have a ``library/archive/{Aid}/`` tree (read-only)."""
    try:
        return {p.name for p in paths.ARCHIVE.iterdir() if p.is_dir() and AID_RE.match(p.name)}
    except OSError:
        return set()


def _unwrap_container(base: Path, max_depth: int = 3) -> Path:
    """Descend through single-container levels (e.g. ``pictures/JM/<143 books>``).

    A level is a *container* when it holds exactly one subdirectory, no page images
    of its own, and that subdirectory holds at least two subdirectories.  Scanning
    ``E:\\my world\\pictures`` would otherwise report one bogus "empty, no Aid"
    entry named ``JM`` instead of the 143 books inside it.

    The "at least two" rule matters: a lone book folder such as
    ``[砂漠] … (COMIC LO 2017年7月号)`` holds a single ``187/`` chapter tree, so
    descending there would turn one book into a fake entry named ``187``.
    """
    current = base
    for _ in range(max_depth):
        subs = _subdirs(current)
        if len(subs) != 1 or _images(current):
            break
        child = subs[0]
        if len(_subdirs(child)) < 2:
            break
        current = child
    return current


def scan(root: Path | str, known_aids: set[str] | None = None) -> dict:
    """Classify every top-level directory under ``root`` (read-only)."""
    requested = Path(root)
    if not requested.is_dir():
        raise FileNotFoundError(f"import root is not a directory: {requested}")

    base = _unwrap_container(requested)
    known = archive_aids() if known_aids is None else set(known_aids)

    entries = []
    for d in _subdirs(base):
        entry = classify_entry(d)
        # ``rel_path`` is the stable identity of a queue entry: relative to the
        # scan root, posix-style.  Absolute paths would make every decision row
        # machine-specific and would not survive moving the drop folder.
        try:
            entry["rel_path"] = d.relative_to(base).as_posix()
        except ValueError:
            entry["rel_path"] = d.name
        entry["path"] = str(d)
        entries.append(entry)
    entries.sort(key=lambda e: e["dir"])
    for e in entries:
        e["in_library"] = bool(e["aid"]) and e["aid"] in known
        e["needs_id"] = not e["aid"]

    # Duplicate Aid groups across directories (simplified/traditional splits etc.).
    by_aid: dict[str, list[str]] = {}
    for e in entries:
        if e["aid"]:
            by_aid.setdefault(e["aid"], []).append(e["dir"])
    duplicate_groups = {aid: dirs for aid, dirs in sorted(by_aid.items()) if len(dirs) > 1}

    # Near-duplicate directory names: the same book dropped twice under slightly
    # different names (e.g. simplified vs traditional titles).  Pairs that differ
    # by a volume/part marker are a series, not a duplicate — see
    # _near_duplicate_names.  Both are *evidence for review*, never a merge.
    near_duplicates, series_pairs = _near_duplicate_names([e["dir"] for e in entries])

    # A near-duplicate pair whose two halves already resolve to the same Aid is
    # the 1454103 split case: grouping settles it, so it must not also be flagged
    # as "are these the same book?" — that would ask the user to answer a question
    # the Aid already answered.
    aid_of = {e["dir"]: e["aid"] for e in entries}
    suspected: set[str] = set()
    for pair in near_duplicates:
        aid_a, aid_b = aid_of.get(pair["a"]) or "", aid_of.get(pair["b"]) or ""
        if aid_a and aid_a == aid_b:
            continue
        suspected.update((pair["a"], pair["b"]))

    duplicate_aids = set(duplicate_groups)
    hint_counts: dict[str, int] = {}
    for e in entries:
        e["hints"] = _entry_hints(e, duplicate_aids, suspected)
        for hint in e["hints"]:
            hint_counts[hint] = hint_counts.get(hint, 0) + 1
        e["needs_review"] = any(h in REVIEW_HINTS for h in e["hints"])

    summary = {
        "dirs": len(entries),
        "with_aid": sum(1 for e in entries if e["aid"]),
        "valid_aids": sum(1 for e in entries if e["aid"] and e.get("aid_valid")),
        "invalid_aids": sum(1 for e in entries if e["aid"] and not e.get("aid_valid")),
        "unique_aids": len(by_aid),
        "unique_valid_aids": len({e["aid"] for e in entries if e["aid"] and e.get("aid_valid")}),
        "no_aid": sum(1 for e in entries if not e["aid"]),
        "already_in_library": sum(1 for e in entries if e["in_library"]),
        "already_in_library_aids": len({e["aid"] for e in entries if e["aid"] and e["in_library"]}),
        "new_aids": len({e["aid"] for e in entries if e["aid"] and not e["in_library"]}),
        "new_valid_aids": len({e["aid"] for e in entries if e["aid"] and e.get("aid_valid") and not e["in_library"]}),
        "no_reliable_aid": sum(1 for e in entries if not e["aid"] or not e.get("aid_valid")),
        "needs_review": sum(1 for e in entries if e["needs_review"]),
        "needs_id": sum(1 for e in entries if e["needs_id"]),
        "flat": sum(1 for e in entries if e["kind"] == "flat"),
        "nested": sum(1 for e in entries if e["kind"] == "nested"),
        "cover_only": sum(1 for e in entries if e["kind"] == "cover_only"),
        "empty": sum(1 for e in entries if e["kind"] == "empty"),
        "magazine_like": sum(1 for e in entries if e["magazine_like"]),
        "duplicate_aid_groups": len(duplicate_groups),
        "near_duplicate_pairs": len(near_duplicates),
        "series_pairs": len(series_pairs),
        "images": sum(e["image_count"] for e in entries),
        "bytes": sum(e["bytes"] for e in entries),
    }
    return {
        "schemaVersion": SCHEMA,
        "kind": "import_scan",
        "generated_at": _utc(),
        "root": str(base),
        "requested_root": str(requested),
        "known_aids": len(known),
        "summary": summary,
        "duplicate_groups": duplicate_groups,
        "near_duplicates": near_duplicates,
        "series": series_pairs,
        "entries": entries,
    }


NEAR_DUP_RATIO = 0.82

# Volume / part markers.  Two directories that differ *only* by one of these are
# neighbouring volumes of one series (「前篇 / 后篇」, 「1 / 2 / 3」, 「第2话 / 第4话」,
# 「第二章 / 第3章」), NOT the same book dropped twice.  Conflating them would put
# real books into the merge queue, so they are reported separately as a series.
VOLUME_TOKEN_RE = re.compile(
    r"(前篇|后篇|前編|後編|上巻|下巻|上卷|下卷|总集篇|總集篇"
    r"|续篇|續篇|续集|續集|后日谈|後日談|前日谈|番外"
    r"|续|續)"
    r"|第\s*[0-9一二三四五六七八九十百]+\s*[话話章巻卷回期]"
    r"|(?<!\d)\d{1,3}(?!\d)"
)

SERIES_STRIP_RATIO = 0.85


def _volume_markers(name: str) -> set[str]:
    """Volume/part markers inside a directory name (lowercased, order-free)."""
    return {m.group(0).strip().lower() for m in VOLUME_TOKEN_RE.finditer(name)}


def _near_duplicate_names(names: list[str], ratio: float = NEAR_DUP_RATIO) -> tuple[list[dict], list[dict]]:
    """Split similar-name pairs into "same book, different naming" vs "series".

    Mirrors the user's own ``JM重复检查.py`` precedent (``SequenceMatcher``) but on
    the digit-stripped name, then discriminates:

    * a pair whose names differ by a volume/part marker is a *series* pair —
      real distinct books that merely share a title stem;
    * everything else above ``ratio`` is a merge *candidate*.

    Both lists are questions for a human, never merge decisions: only an upstream
    Aid lookup (or the user) can settle identity.  Ambiguity is deliberately
    biased toward *series* — a wrongly-merged book is destructive, a wrongly
    split series merely asks the user one extra question.
    """
    from difflib import SequenceMatcher

    keyed = []
    for n in names:
        base = re.sub(r"\s+", " ", NAME_DIGITS_RE.sub("", n)).strip()
        markers = _volume_markers(n)
        stripped = re.sub(r"\s+", " ", VOLUME_TOKEN_RE.sub("", base)).strip()
        keyed.append((n, base, markers, stripped))

    near: list[dict] = []
    series: list[dict] = []
    for i in range(len(keyed)):
        for j in range(i + 1, len(keyed)):
            a, ka, va, sa = keyed[i]
            b, kb, vb, sb = keyed[j]
            if not ka or not kb:
                continue
            if SequenceMatcher(None, ka.lower(), kb.lower()).ratio() < ratio:
                continue
            pair = {"a": a, "b": b, "score": round(SequenceMatcher(None, ka.lower(), kb.lower()).ratio(), 3)}
            differs_by_volume = bool((va or vb) and va != vb)
            same_stem = bool(sa and sb and SequenceMatcher(None, sa.lower(), sb.lower()).ratio() >= SERIES_STRIP_RATIO)
            if differs_by_volume and same_stem:
                pair["volumes"] = sorted(va | vb)
                series.append(pair)
            else:
                near.append(pair)
    near.sort(key=lambda p: -p["score"])
    series.sort(key=lambda p: -p["score"])
    return near, series


def write_manifest(manifest: dict, path: Path | None = None) -> Path:
    """Persist the manifest (data/ only).  Read-only w.r.t. the source tree."""
    target = Path(path) if path is not None else paths.IMPORT_MANIFEST
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(".json.part")
    part.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    import os

    os.replace(part, target)
    return target


def load_manifest(path: Path | None = None) -> dict | None:
    target = Path(path) if path is not None else paths.IMPORT_MANIFEST
    if not target.exists():
        return None
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None
