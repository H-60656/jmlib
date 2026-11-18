"""Chapter-cover cache: exact bytes of a zip's first image member, no re-encode.

Covers are a rebuildable cache at ``library/covers/{Aid}/{pindex}<suffix>``.
The file suffix follows the source member (jpg / webp / gif) so the bytes stay
unchanged and a static server can send the right Content-Type.  A book cover is
only a pointer to its first chapter's cover: no extra file is created.
"""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path

from jmlib import paths

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
_NUM = re.compile(r"\d+")


def image_members(zf: zipfile.ZipFile) -> list[str]:
    """Image entries in reading order (numeric stem first, then name)."""
    names = [
        info.filename
        for info in zf.infolist()
        if not info.is_dir() and Path(info.filename).suffix.lower() in IMAGE_SUFFIXES
    ]

    def key(name: str) -> tuple[int, str]:
        match = _NUM.search(Path(name).stem)
        return (int(match.group()) if match else 0, name)

    return sorted(names, key=key)


def cover_target(aid: str, pindex: int, first_name: str) -> Path:
    suffix = Path(first_name).suffix.lower() or ".jpg"
    return paths.COVERS / aid / f"{pindex}{suffix}"


def write_cover(aid: str, pindex: int, first_name: str, data: bytes) -> str:
    """Write the first image's bytes verbatim; return a project-relative posix path."""
    target = cover_target(aid, pindex, first_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.stat().st_size != len(data):
        part = target.with_name(target.name + ".part")
        part.write_bytes(data)
        os.replace(part, target)
    return paths.rel(target)


def inspect_chapter(zip_path: Path, aid: str, pindex: int) -> dict:
    """One zip open yields both page count and first-image cover (cache)."""
    with zipfile.ZipFile(zip_path) as zf:
        members = image_members(zf)
        cover_path = None
        if members:
            cover_path = write_cover(aid, pindex, members[0], zf.read(members[0]))
    return {"page_count": len(members), "cover_path": cover_path}
