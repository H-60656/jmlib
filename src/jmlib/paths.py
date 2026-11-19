"""Single owner of the jmlib filesystem layout.

All project-internal paths live here.  External (pre-migration) source roots are
never hard-coded: they are supplied to ``migrate --plan`` via --source/--loose
and recorded in the generated manifest.

This module is also the single owner of the *import-root whitelist* and of the
containment rule that goes with it.  Both used to live in the HTTP layer, which
meant a library module (``import_apply``) accepted a client-supplied path without
being able to check it; the rule now sits here so every caller — API, CLI, or
test — enforces the same thing.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parents[2]

DATA: Path = ROOT / "data"
LIBRARY: Path = ROOT / "library"
ARCHIVE: Path = LIBRARY / "archive"
UNIDENTIFIED: Path = LIBRARY / "_unidentified"
COVERS: Path = LIBRARY / "covers"
INCOMING: Path = LIBRARY / "_incoming"
STAGING: Path = DATA / "staging"
WEB: Path = ROOT / "web"
EXPORTS: Path = ROOT / "exports"

DB_PATH: Path = DATA / "jmlib.db"
MIGRATION_MANIFEST: Path = DATA / "migration_manifest.json"
ROLLBACK_MANIFEST: Path = DATA / "rollback_manifest.json"
SIGNATURES: Path = LIBRARY / "_zip_signatures.json"
BLACKLIST: Path = LIBRARY / "黑名单.csv"
INGEST_METADATA: Path = DATA / "ingest_metadata.json"
LOOSE_INGEST_MANIFEST: Path = DATA / "loose_ingest_manifest.json"
GOVERNANCE_MANIFEST: Path = DATA / "governance_manifest.json"

# A/B 段（见 plans/2026-11-17-jmlib-import-and-reader.md）
IMPORT_MANIFEST: Path = DATA / "import_manifest.json"
IMPORT_ROLLBACK: Path = DATA / "import_rollback.json"
UPSTREAM_CACHE: Path = DATA / "upstream_cache.json"
# B.1 hand-add keeps its own rollback ledger: adding one book by hand must not
# overwrite the batch import's rollback record (and vice versa).
SINGLE_ROLLBACK: Path = DATA / "single_rollback.json"


def rel(path: str | Path) -> str:
    """Return a project-relative posix path when possible, else the posix path itself."""
    p = Path(path)
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix()


# ---------------------------------------------------------------------------
# Import roots: which directories may the server read book sources from?
# ---------------------------------------------------------------------------
#
# The whitelist exists so "scan a folder" can never become "read any path on
# disk".  It is one of allowed roots under the environment variable plus the
# documented drop folder, and every read of a client-supplied directory must go
# through :func:`resolve_inside_roots` below.


def allowed_import_roots() -> list[Path]:
    """The resolved directories an import may read from.

    ``JMLIB_IMPORT_ROOTS`` is a path-separator list; ``library/_incoming`` is
    always present so a drop folder works with no configuration at all.
    """
    roots: list[Path] = []
    for chunk in os.environ.get("JMLIB_IMPORT_ROOTS", "").split(os.pathsep):
        chunk = chunk.strip().strip('"')
        if chunk:
            roots.append(Path(chunk))
    roots.append(INCOMING)
    out: list[Path] = []
    for root in roots:
        try:
            out.append(root.resolve())
        except OSError:
            continue
    return out


def normalize_untrusted(raw: str | Path) -> Path:
    """Turn untrusted text into a normalised absolute path **without** touching disk.

    ``expanduser`` + ``normpath`` + ``abspath`` are pure string operations: no
    ``stat``, no symlink resolution, nothing that could be talked into reading a
    file.  That matters for the order of operations below — the containment test
    must be able to run *before* anything the filesystem would act on.
    """
    return Path(os.path.abspath(os.path.normpath(os.path.expanduser(str(raw)))))


def contained_in(target: Path | str, roots: list[Path]) -> bool:
    """True when ``target`` already lives under one of ``roots``.

    Written with ``str.startswith`` rather than ``Path.is_relative_to``: both
    express the same relation, but only ``startswith`` is modelled as a
    ``SafeAccessCheck`` by CodeQL.  ``os.sep`` is appended so ``/a/bc`` is not
    mistaken for a child of ``/a/b``.

    Callers that perform a filesystem call on the result must re-state the
    predicate inline instead of calling this function — see
    :func:`resolve_inside_roots`.
    """
    text = str(target)
    for root in roots:
        base = str(root)
        if text == base or text.startswith(base + os.sep):
            return True
    return False


def resolve_inside_roots(raw: str | Path) -> Path | None:
    """Resolve one directory and return it only if it is inside the whitelist.

    Returns ``None`` — never a path — when the input is outside the allowed
    roots, so a caller cannot accidentally use an unvalidated value.  The
    caller decides whether "outside the roots" is a 403 or a refused plan; this
    function only answers the containment question.

    Order matters and is not cosmetic: the containment check runs on the
    *normalised* path first (string maths only), and only a path that already
    provably lives under an allowed root is resolved — i.e. allowed to touch the
    filesystem.  Resolving first would mean asking the disk about a path chosen
    by the caller, which is exactly what the whitelist exists to prevent.

    The check is spelled out inline at each filesystem call — ``str.startswith``
    on the normalised text, not a call to :func:`contained_in` — because
    ``py/path-injection`` only clears taint when the guard sits in the same
    function as the call; ``resolve()`` counts as one.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    candidate = normalize_untrusted(text)
    roots = allowed_import_roots()
    checked = str(candidate)
    for root in roots:
        base = str(root)
        if checked == base or checked.startswith(base + os.sep):
            break
    else:
        return None
    try:
        target = candidate.resolve()
    except (OSError, RuntimeError):
        return None
    resolved = str(target)
    for root in roots:
        base = str(root)
        if resolved == base or resolved.startswith(base + os.sep):
            break
    else:
        # a symlink inside an allowed root pointing outside it
        return None
    return target


def ensure_dirs() -> None:
    for directory in (DATA, LIBRARY, ARCHIVE, UNIDENTIFIED, COVERS, STAGING):
        directory.mkdir(parents=True, exist_ok=True)
