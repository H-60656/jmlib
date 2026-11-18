"""jmcomic ``after_album`` plugin: pack each downloaded album into jmlib.

Registered by ``download.run`` BEFORE the option is constructed: jmcomic
resolves plugin keys from ``JmModuleConfig.REGISTRY_PLUGIN`` at option-build
time, so a plugin referenced in option.yml must already be registered.

This is the T4.1 single-plugin form chosen in the P4 implementation decision.
The built-in ``ZipPlugin`` on ``after_photo`` re-packs *every chapter seen so
far* on each photo callback (``get_downloaded_photo`` returns
``download_success_dict[photo.from_album]``), which would produce duplicate and
empty zips.  Instead we read that dict once, at ``after_album``, and write one
zip per chapter through ``ingest.pack_album`` — zero extra network, one writer.

No deletion: staging images are left in place; cleanup is a P5 action.
"""

from __future__ import annotations

from pathlib import Path

from jmcomic import JmOptionPlugin


class JmlibIngestPlugin(JmOptionPlugin):
    plugin_key = "jmlib_ingest"
    plugin_dependencies = ()

    def invoke(self, album=None, downloader=None, **kwargs) -> None:
        from jmlib import ingest, store

        self.require_param(album is not None, "jmlib_ingest needs 'album' (after_album hook only)")
        self.require_param(downloader is not None, "jmlib_ingest needs 'downloader'")

        aid = str(album.id)
        if getattr(downloader, "has_download_failures", False):
            store.add_finding("ingest_skipped", aid, "download incomplete; catalog left untouched")
            self.log(f"[{aid}] download incomplete, skipped ingest", "skip")
            return

        success = downloader.download_success_dict.get(album, {})
        chapter_images: dict[int, list[Path]] = {}
        for photo, image_list in success.items():
            # JmPhotoDetail.index is 1-based within the album (jm_entity.py:326).
            pindex = int(getattr(photo, "index", 0) or 0)
            if pindex < 1:
                continue
            chapter_images.setdefault(pindex, []).extend(Path(save_path) for save_path, _image in image_list)

        if not chapter_images:
            store.add_finding("ingest_skipped", aid, "no downloaded images in download_success_dict")
            self.log(f"[{aid}] no images, skipped ingest", "skip")
            return

        result = ingest.pack_album(
            aid,
            chapter_images,
            title=str(album.name or ""),
            author=str(getattr(album, "author", "") or ""),
            tags=list(getattr(album, "tags", []) or []),
            generation="download",
        )
        for conflict in result["conflicts"]:
            store.add_finding("ingest_conflict", aid, conflict)
            self.log(f"[{aid}] {conflict}", "conflict")
        self.log(
            f"[{aid}] ingested {result['written']} chapters / {result['pages']} pages",
            "done",
        )
