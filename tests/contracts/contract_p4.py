"""Offline P4 ingest contract checks (no network, no real archive touched).

Monkeypatches every ``jmlib.paths`` constant onto a scratch tree, then exercises:
  * loose_plan dual-source Aid detection + the 1454103 simplified/traditional split;
  * loose_apply: one zip per chapter, continuous 00001.jpg members, first-image
    covers, DB upsert, metadata sidecar, and ZERO deletion of loose sources;
  * no-overwrite protection on an existing {Aid}/ tree;
  * indexer.scan_catalog preserving P4-rich metadata across a rebuild;
  * JmlibIngestPlugin with a synthetic download_success_dict (success / failed
    download / missing album);
  * download.build_option_dict overlay (no .zip in dir_rule, no download_cover)
    and _register_and_construct on the installed jmcomic;
  * loose_rollback reversing zips/covers/DB rows while still keeping sources.

Run:  $env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH='src'; python .tmp/test_p4_ingest.py
"""

# Moved verbatim from the ``.tmp`` exploration scripts so CI runs it.
# Runs as its own process: every contract redirects ``jmlib.paths`` onto a
# scratch tree, and those redirects must not leak between suites.
from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jmlib import download, indexer, ingest, jm_plugin, paths, store  # noqa: E402


class _Obj:
    """Hashable-by-identity stand-in for JmAlbumDetail / JmPhotoDetail / downloader.

    types.SimpleNamespace is unhashable, but download_success_dict keys are real
    entity objects (hashable by identity), so the fixture must be too.
    """

    def __init__(self, **kw):
        self.__dict__.update(kw)


BASE = ROOT / ".tmp" / "p4_contracts"
if BASE.exists():
    shutil.rmtree(BASE)
BASE.mkdir(parents=True, exist_ok=True)  # default mode (0o700 is denied by sandbox)

LOOSE = BASE / "loose"
DATA = BASE / "data"
LIB = BASE / "library"

# Redirect EVERY path constant (incl. the P4 additions) to the scratch tree.
paths.ROOT = BASE
paths.DATA = DATA
paths.LIBRARY = LIB
paths.ARCHIVE = LIB / "archive"
paths.UNIDENTIFIED = LIB / "_unidentified"
paths.COVERS = LIB / "covers"
paths.INCOMING = LIB / "_incoming"
paths.STAGING = DATA / "staging"
paths.WEB = BASE / "web"
paths.EXPORTS = BASE / "exports"
paths.DB_PATH = DATA / "jmlib.db"
paths.MIGRATION_MANIFEST = DATA / "migration_manifest.json"
paths.ROLLBACK_MANIFEST = DATA / "rollback_manifest.json"
paths.SIGNATURES = LIB / "_zip_signatures.json"
paths.BLACKLIST = LIB / "黑名单.csv"
paths.INGEST_METADATA = DATA / "ingest_metadata.json"
paths.LOOSE_INGEST_MANIFEST = DATA / "loose_ingest_manifest.json"
paths.ensure_dirs()

fails: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    if cond:
        print(f"PASS {name}" + (f" {extra}" if extra else ""))
    else:
        fails.append(name)
        print(f"FAIL {name}" + (f" {extra}" if extra else ""), file=sys.stderr)


def img(rel: str, data: bytes) -> Path:
    p = LOOSE / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


# --- synthetic loose tree ---------------------------------------------------
# Known payloads so we can assert byte-exact covers / members.
P: dict[str, bytes] = {}
# Split 1454103: traditional folder has ONLY a cover file; simplified has the body.
img("Book A (Traditional)/1454103_cover.jpg", b"traditional-cover-bytes")
for i in range(1, 4):
    P[f"1454103/1/{i:05d}.jpg"] = f"body-{i}".encode()
    img(f"Book A (Simplified)/1454103/1/{i:05d}.jpg", P[f"1454103/1/{i:05d}.jpg"])
# Normal book: cover + 2 chapters (2 + 1 images).
img("Normal Book/222222_cover.jpg", b"normal-cover-bytes")
for i in range(1, 3):
    P[f"222222/1/{i:05d}.jpg"] = f"n1-{i}".encode()
    img(f"Normal Book/222222/1/{i:05d}.jpg", P[f"222222/1/{i:05d}.jpg"])
P["222222/2/00001.jpg"] = b"n2-1"
img("Normal Book/222222/2/00001.jpg", P["222222/2/00001.jpg"])
# Cover-only book (no body) -> skip-no-images.
img("Cover Only/333333_cover.jpg", b"cover-only-bytes")
# Folder with no Aid identity at all -> ignored.
img("No Identity/readme.txt", b"ignored")

all_source_images = [p for p in LOOSE.rglob("*.jpg")]
source_bytes = {p: p.read_bytes() for p in all_source_images}

# --- loose_plan -------------------------------------------------------------
m = ingest.loose_plan(root=LOOSE)
s = m["summary"]
check(
    "plan is read-only (no archive writes)",
    not (LIB / "archive").exists() or not list((LIB / "archive").glob("**/*.zip")),
    f"summary={s}",
)
check(
    "plan dirs/aids/books_with_body",
    s["dirs"] == 5 and s["aids"] == 3 and s["books_with_body"] == 2,
    f"dirs={s['dirs']} aids={s['aids']} with_body={s['books_with_body']}",
)
check(
    "plan chapters/images/covers/collisions",
    s["chapters"] == 3 and s["images"] == 6 and s["cover_files"] == 3 and s["collisions"] == 0,
    f"chapters={s['chapters']} images={s['images']} covers={s['cover_files']} collisions={s['collisions']}",
)
by_aid = {b["aid"]: b for b in m["books"]}
check(
    "split 1454103 merged into ONE book",
    "1454103" in by_aid
    and len(by_aid["1454103"]["source_dirs"]) == 2
    and set(by_aid["1454103"]["chapters"]) == {"1"}
    and len(by_aid["1454103"]["chapters"]["1"]) == 3,
    f"src={by_aid.get('1454103', {}).get('source_dirs')}",
)
check(
    "split title comes from body folder",
    by_aid["1454103"]["title"] == "Book A (Simplified)",
    by_aid["1454103"]["title"],
)
check(
    "normal book 2 chapters",
    set(by_aid["222222"]["chapters"]) == {"1", "2"}
    and len(by_aid["222222"]["chapters"]["1"]) == 2
    and len(by_aid["222222"]["chapters"]["2"]) == 1,
)
check(
    "cover-only book skipped (no body)",
    by_aid["333333"]["db_action"] == "skip-no-images" and not by_aid["333333"]["chapters"],
)
check("no-Aid folder ignored", all(b["aid"] != "" for b in m["books"]) and len(m["books"]) == 3)
check("manifest persisted", paths.LOOSE_INGEST_MANIFEST.is_file())
check(
    "safety declares zero deletion",
    "none" in m["safety"]["deletion"].lower() and "never" in m["safety"]["overwrite"].lower(),
)

# --- loose_apply ------------------------------------------------------------
rc = ingest.loose_apply(m)
check("loose_apply rc=0", rc == 0, f"rc={rc}")

z_split = LIB / "archive" / "1454103" / "1.zip"
check("1454103/1.zip written", z_split.is_file())
if z_split.is_file():
    with zipfile.ZipFile(z_split) as zf:
        names = zf.namelist()
        first_ok = zf.read("00001.jpg") == P["1454103/1/00001.jpg"]
    check("split zip members continuous 00001..00003", names == ["00001.jpg", "00002.jpg", "00003.jpg"], str(names))
    check("split zip first member bytes exact", first_ok)
check(
    "222222 two chapter zips",
    (LIB / "archive" / "222222" / "1.zip").is_file() and (LIB / "archive" / "222222" / "2.zip").is_file(),
)
check("333333 NOT written", not (LIB / "archive" / "333333").exists())

cover_split = LIB / "covers" / "1454103" / "1.jpg"
check(
    "cover = first image bytes (no re-encode)",
    cover_split.is_file() and cover_split.read_bytes() == P["1454103/1/00001.jpg"],
)

books_in_db = store.list_books()
aids_in_db = {b["aid"] for b in books_in_db}
check("DB has 1454103 + 222222, not 333333", aids_in_db == {"1454103", "222222"}, str(aids_in_db))
check("chapter rows = 3", len(store.list_chapters("1454103")) == 1 and len(store.list_chapters("222222")) == 2)

meta = ingest.load_meta()
check(
    "sidecar has loose books w/ generation=loose",
    meta.get("1454103", {}).get("generation") == "loose" and meta.get("222222", {}).get("generation") == "loose",
    str(sorted(meta)),
)
check("sidecar split title preserved", meta.get("1454103", {}).get("title") == "Book A (Simplified)")

check(
    "ZERO deletion of loose sources",
    all(p.exists() and p.read_bytes() == b for p, b in source_bytes.items()),
    f"{len(source_bytes)} source images checked",
)

# --- no-overwrite -----------------------------------------------------------
before = z_split.read_bytes()
r2 = ingest.pack_album("1454103", {1: [LOOSE / "Book A (Simplified)" / "1454103" / "1" / "00001.jpg"]})
check(
    "existing {Aid}/ tree is never overwritten",
    r2["written"] == 0 and r2["skipped"] >= 1 and bool(r2["conflicts"]) and z_split.read_bytes() == before,
    f"written={r2['written']} conflicts={r2['conflicts']}",
)

# --- rich metadata survives indexer rebuild ---------------------------------
r3 = ingest.pack_album(
    "444444",
    {1: [LOOSE / "Book A (Simplified)" / "1454103" / "1" / "00001.jpg"]},
    title="富标题",
    author="作者X",
    tags=["标签A", "标签B"],
    generation="download",
)
check("download-style pack_album wrote 1 chapter", r3["written"] == 1)
b, c, u, broken = indexer.scan_catalog({"entries": []})
scan_by_aid = {x["aid"]: x for x in b}
check(
    "scan_catalog keeps P4 rich title/author/tags",
    scan_by_aid.get("444444", {}).get("title") == "富标题"
    and scan_by_aid.get("444444", {}).get("author") == "作者X"
    and "标签A" in scan_by_aid.get("444444", {}).get("tags", ""),
    str(scan_by_aid.get("444444")),
)
check(
    "scan_catalog keeps loose sidecar title too", scan_by_aid.get("1454103", {}).get("title") == "Book A (Simplified)"
)
check("scan_catalog reports no broken/ unresolved", not broken and not u)

# --- JmlibIngestPlugin (synthetic download_success_dict) -------------------
plugin = jm_plugin.JmlibIngestPlugin(option=None)
plugin.log_enable = False  # silence jm_log in the offline harness

dl_imgs = BASE / "dl"
(dl_imgs / "1").mkdir(parents=True, exist_ok=True)
(dl_imgs / "2").mkdir(parents=True, exist_ok=True)
(dl_imgs / "1" / "00001.jpg").write_bytes(b"d1-1")
(dl_imgs / "1" / "00002.jpg").write_bytes(b"d1-2")
(dl_imgs / "2" / "00001.jpg").write_bytes(b"d2-1")

album = _Obj(id="555555", name="下载标题", author="下载作者", tags=["下载标签"])
photo1 = _Obj(index=1)
photo2 = _Obj(index=2)
dler = _Obj(
    has_download_failures=False,
    download_success_dict={
        album: {
            photo1: [(str(dl_imgs / "1" / "00001.jpg"), None), (str(dl_imgs / "1" / "00002.jpg"), None)],
            photo2: [(str(dl_imgs / "2" / "00001.jpg"), None)],
        }
    },
)
plugin.invoke(album=album, downloader=dler)
check(
    "plugin wrote 2 chapter zips",
    (LIB / "archive" / "555555" / "1.zip").is_file() and (LIB / "archive" / "555555" / "2.zip").is_file(),
)
book555 = store.get_book("555555")
check(
    "plugin stored rich album metadata",
    book555 is not None
    and book555["title"] == "下载标题"
    and book555["author"] == "下载作者"
    and "下载标签" in book555["tags"],
    str(book555),
)
check(
    "plugin left download sources intact (zero deletion)",
    all(p.exists() for p in [dl_imgs / "1" / "00001.jpg", dl_imgs / "1" / "00002.jpg", dl_imgs / "2" / "00001.jpg"]),
)

album_bad = _Obj(id="666666", name="x", author="", tags=[])
dler_bad = _Obj(has_download_failures=True, download_success_dict={album_bad: {}})
plugin.invoke(album=album_bad, downloader=dler_bad)
check("failed download does NOT ingest", not (LIB / "archive" / "666666").exists() and store.get_book("666666") is None)
check(
    "failed download logged a finding",
    any(f["kind"] == "ingest_skipped" and f["target"] == "666666" for f in store.list_findings()),
)

raised = False
try:
    plugin.invoke()  # missing album/downloader -> require_param
except Exception:
    raised = True
check("missing album/downloader raises (not silently skipped)", raised)

# --- download.build_option_dict overlay ------------------------------------
opt_yml = BASE / "option_synthetic.yml"
opt_yml.write_text(
    "client:\n  impl: api\n  domain: []\n"
    "dir_rule:\n  base_dir: /original/path\n  rule: '{Atitle}/{Aid}/{Pindex}'\n  normalize_zh: zh-cn\n"
    "download:\n  cache: true\n"
    "log: true\n"
    "plugins:\n  after_album:\n    - plugin: download_cover\n      kwargs:\n        size: _3x4\n",
    encoding="utf-8",
)
d = download.build_option_dict(option=opt_yml)
check(
    "overlay dir_rule has no .zip (staging)",
    d["dir_rule"]["rule"] == "Bd/{Aid}/{Pindex}"
    and ".zip" not in d["dir_rule"]["rule"]
    and d["dir_rule"]["base_dir"] == str(paths.STAGING),
)
check(
    "overlay replaces download_cover with jmlib_ingest",
    d["plugins"]["after_album"][0]["plugin"] == "jmlib_ingest" and "download_cover" not in json.dumps(d["plugins"]),
)
check(
    "overlay preserves client/download, drops filepath/version",
    d["client"]["impl"] == "api" and d["download"]["cache"] is True and "filepath" not in d and "version" not in d,
)

opt = download._register_and_construct(d)
check(
    "construct JmOption with staging DSL",
    type(opt).__name__ == "JmOption"
    and opt.dir_rule.rule_dsl == "Bd/{Aid}/{Pindex}"
    and ".zip" not in opt.dir_rule.rule_dsl,
)

# --- loose_rollback ---------------------------------------------------------
rc_rb = ingest.loose_rollback()
check("loose_rollback rc=0", rc_rb == 0, f"rc={rc_rb}")
check("rollback removed loose zips", not z_split.exists() and not (LIB / "archive" / "222222" / "1.zip").exists())
check("rollback removed loose DB rows", store.get_book("1454103") is None and store.get_book("222222") is None)
check("rollback kept download/plugin books", store.get_book("555555") is not None)
meta_after = ingest.load_meta()
check("rollback cleaned loose sidecar keys", "1454103" not in meta_after and "222222" not in meta_after)
check(
    "rollback STILL kept loose sources (zero deletion)",
    all(p.exists() and p.read_bytes() == b for p, b in source_bytes.items()),
)

if fails:
    print(f"\nP4 ingest checks: {len(fails)} FAILED -> {fails}", file=sys.stderr)
    raise SystemExit(1)
print("\nP4 ingest checks: ALL PASSED (offline, zero real-archive writes)")
