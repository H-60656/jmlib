"""Single owner of the jmlib filesystem layout.

All project-internal paths live here.  External (pre-migration) source roots are
never hard-coded: they are supplied to ``migrate --plan`` via --source/--loose
and recorded in the generated manifest.
"""

from __future__ import annotations

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


def ensure_dirs() -> None:
    for directory in (DATA, LIBRARY, ARCHIVE, UNIDENTIFIED, COVERS, STAGING):
        directory.mkdir(parents=True, exist_ok=True)
