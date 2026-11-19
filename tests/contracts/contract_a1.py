"""Offline contract checks for A.1 (import_scan).  Never reads the real library.

Scratch tree mirrors every shape the real queue contains:
  * flat image dir with Aid in the name
  * nested {Aid}/{Pindex}/ dir
  * cover-only dir (split duplicate, no body)
  * Aid-less dir
  * magazine-issue style dir with a numeric hit that must NOT be an Aid
  * empty dir
  * two dirs sharing one Aid (duplicate group)
"""
# Moved verbatim from the ``.tmp`` exploration scripts so CI runs it.
# Runs as its own process: every contract redirects ``jmlib.paths`` onto a
# scratch tree, and those redirects must not leak between suites.

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

# scan() now enforces the import-root whitelist itself (previously only the HTTP
# layer did), so the scratch tree has to be declared readable before any call.
BASE = Path(__file__).resolve().parents[2] / ".tmp" / "a1_contracts"
os.environ["JMLIB_IMPORT_ROOTS"] = str(BASE)

from jmlib import import_scan  # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASSED
    if cond:
        PASSED += 1
        print(f"PASS {name}{(' ' + extra) if extra else ''}")
    else:
        FAILED.append(name)
        print(f"FAIL {name}{(' ' + extra) if extra else ''}")


def img(p: Path, data: bytes = b"\xff\xd8\xff\xd9") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def build(root: Path) -> None:
    # 1. flat dir, Aid from name, no cover file
    d = root / "[汉化组] 测试作品 1443022"
    for i in range(1, 4):
        img(d / f"{i:03d}.jpg")

    # 2. nested {Aid}/{Pindex}/
    d = root / "双子鬼"
    for i in range(1, 3):
        img(d / "1454103" / "1" / f"{i:03d}.jpg")
    for i in range(1, 4):
        img(d / "1454103" / "2" / f"{i:03d}.jpg")

    # 3. cover-only dir (no body)
    d = root / "同级生にそして教师に汚されて"
    img(d / "1454103_cover.jpg")

    # 4. Aid-less dir
    d = root / "海妹妹和暑假 无车号"
    for i in range(1, 3):
        img(d / f"{i}.png")

    # 5. magazine issue folder: the 2022/6 must NOT become an Aid
    d = root / "COMIC 失乐天 2022年6月号"
    img(d / "001.jpg")

    # 6. empty dir
    (root / "空目录").mkdir(parents=True, exist_ok=True)

    # 7. second dir sharing Aid 1443022 -> duplicate group
    d = root / "测试作品 1443022_cover.jpg"
    d.mkdir(parents=True, exist_ok=True)
    img(d / "1443022_cover.jpg")


def main() -> int:
    base = Path(__file__).resolve().parents[2] / ".tmp" / "a1_contracts"
    if base.exists():
        shutil.rmtree(base)
    root = base / "JM"
    root.mkdir(parents=True)
    build(root)

    manifest = import_scan.scan(root, known_aids=set())
    s = manifest["summary"]
    entries = {e["dir"]: e for e in manifest["entries"]}

    check("scan is read-only (manifest not written by scan)", not (base / "import_manifest.json").exists())

    check("dirs counted", s["dirs"] == 7, str(s))

    e = entries["[汉化组] 测试作品 1443022"]
    check("flat dir -> kind flat", e["kind"] == "flat", e["kind"])
    check(
        "flat dir Aid from name_digits",
        e["aid"] == "1443022" and e["aid_source"] == "name_digits",
        f"{e['aid']}/{e['aid_source']}",
    )
    check("flat dir image_count", e["image_count"] == 3, str(e["image_count"]))
    check("flat dir chapters empty", e["chapter_count"] == 0)

    e = entries["双子鬼"]
    check("nested dir -> kind nested", e["kind"] == "nested", e["kind"])
    check(
        "nested Aid from subdir", e["aid"] == "1454103" and e["aid_source"] == "subdir", f"{e['aid']}/{e['aid_source']}"
    )
    check("nested chapter_count 2", e["chapter_count"] == 2, str(e["chapter_count"]))
    check("nested image_count 5", e["image_count"] == 5, str(e["image_count"]))
    check("nested chapters keyed 1,2", list(e["chapters"]) == ["1", "2"], str(list(e["chapters"])))

    e = entries["同级生にそして教师に汚されて"]
    check("cover-only -> kind cover_only", e["kind"] == "cover_only", e["kind"])
    check(
        "cover-only Aid from cover_file",
        e["aid"] == "1454103" and e["aid_source"] == "cover_file",
        f"{e['aid']}/{e['aid_source']}",
    )
    check("cover-only has no body", e["has_body"] is False)

    e = entries["海妹妹和暑假 无车号"]
    check("aid-less dir has no Aid", e["aid"] is None and e["aid_source"] == "none", f"{e['aid']}/{e['aid_source']}")

    e = entries["COMIC 失乐天 2022年6月号"]
    check("magazine dir flagged magazine_like", e["magazine_like"] is True)
    check("magazine dir does not become an Aid", e["aid"] is None, str(e["aid"]))

    e = entries["空目录"]
    check("empty dir -> kind empty", e["kind"] == "empty" and e["image_count"] == 0, e["kind"])

    check(
        "duplicate Aid group detected",
        manifest["duplicate_groups"].get("1443022") is not None,
        str(manifest["duplicate_groups"]),
    )
    check(
        "duplicate group lists 2 dirs",
        len(manifest["duplicate_groups"]["1443022"]) == 2,
        str(manifest["duplicate_groups"]["1443022"]),
    )
    check(
        "1454103 also a duplicate group",
        "1454103" in manifest["duplicate_groups"],
        str(list(manifest["duplicate_groups"])),
    )

    check("summary no_aid counts 3", s["no_aid"] == 3, str(s["no_aid"]))
    check("summary images total 11", s["images"] == 11, str(s["images"]))
    check("summary unique_aids 2", s["unique_aids"] == 2, str(s["unique_aids"]))
    check("summary flat 3", s["flat"] == 3, str(s["flat"]))
    check("summary nested 1", s["nested"] == 1, str(s["nested"]))
    check("summary cover_only 2", s["cover_only"] == 2, str(s["cover_only"]))
    check("summary empty 1", s["empty"] == 1, str(s["empty"]))
    check("summary magazine_like 1", s["magazine_like"] == 1, str(s["magazine_like"]))

    # idempotence
    again = import_scan.scan(root, known_aids=set())
    check(
        "rescan is idempotent",
        [e["dir"] for e in again["entries"]] == [e["dir"] for e in manifest["entries"]]
        and again["summary"] == manifest["summary"],
    )

    # already-in-library classification
    known = import_scan.scan(root, known_aids={"1454103"})
    ks = known["summary"]
    ke = {e["dir"]: e for e in known["entries"]}
    check(
        "in_library flags known Aid",
        ke["双子鬼"]["in_library"] is True and ke["同级生にそして教师に汚されて"]["in_library"] is True,
    )
    check("in_library false for unknown Aid", ke["[汉化组] 测试作品 1443022"]["in_library"] is False)
    check("summary already_in_library 2", ks["already_in_library"] == 2, str(ks["already_in_library"]))
    check("summary new_aids 1", ks["new_aids"] == 1, str(ks["new_aids"]))
    check("needs_id true for aid-less", ke["海妹妹和暑假 无车号"]["needs_id"] is True)
    # Hints are the review vocabulary now: an aid-less directory is a question
    # (no_aid), and a magazine-named directory is a question too even when a number
    # is present — that number may be an issue number.
    check(
        "aid-less dir carries no_aid hint",
        ke["海妹妹和暑假 无车号"]["hints"] == ["no_aid"],
        str(ke["海妹妹和暑假 无车号"]["hints"]),
    )
    check("aid-less dir needs review", ke["海妹妹和暑假 无车号"]["needs_review"] is True)
    check(
        "magazine dir flagged magazine_like hint",
        "magazine_like" in ke["COMIC 失乐天 2022年6月号"]["hints"],
        str(ke["COMIC 失乐天 2022年6月号"]["hints"]),
    )
    check(
        "magazine dir needs review",
        ke["COMIC 失乐天 2022年6月号"]["needs_review"] is True,
        str(ke["COMIC 失乐天 2022年6月号"]["hints"]),
    )
    check("needs_review true for name_digits Aid", ke["[汉化组] 测试作品 1443022"]["needs_review"] is True)
    check(
        "name_digits Aid carries weak_aid_source hint",
        "weak_aid_source" in ke["[汉化组] 测试作品 1443022"]["hints"],
        str(ke["[汉化组] 测试作品 1443022"]["hints"]),
    )
    check(
        "structural subdir Aid carries no weak hint",
        "weak_aid_source" not in ke["双子鬼"]["hints"],
        str(ke["双子鬼"]["hints"]),
    )
    check(
        "cover-only split half is hinted but not blocked",
        "cover_only" in ke["同级生にそして教师に汚されて"]["hints"] and "cover_only" not in import_scan.BLOCKING_HINTS,
        str(ke["同级生にそして教师に汚されて"]["hints"]),
    )
    check(
        "duplicate Aid group members carry duplicate_aid",
        "duplicate_aid" in ke["双子鬼"]["hints"],
        str(ke["双子鬼"]["hints"]),
    )
    check(
        "already-in-library hint follows in_library",
        "already_in_library" in ke["双子鬼"]["hints"]
        and "already_in_library" not in ke["[汉化组] 测试作品 1443022"]["hints"],
        f"{ke['双子鬼']['hints']} / {ke['[汉化组] 测试作品 1443022']['hints']}",
    )
    check(
        "empty dir carries empty_dir hint", ke["空目录"]["hints"] == ["no_aid", "empty_dir"], str(ke["空目录"]["hints"])
    )
    check(
        "BLOCKING_HINTS is a strict subset of REVIEW_HINTS",
        set(import_scan.BLOCKING_HINTS) < set(import_scan.REVIEW_HINTS),
        f"{import_scan.BLOCKING_HINTS} vs {import_scan.REVIEW_HINTS}",
    )
    # sweep_blockers: which hints actually stop an unpicked batch.  Getting this
    # wrong in either direction is costly — too strict and nothing can be batched,
    # too loose and a guessed Aid gets imported silently.
    check(
        "weak Aid source does not block a sweep",
        import_scan.sweep_blockers(ke["[汉化组] 测试作品 1443022"]) == [],
        str(import_scan.sweep_blockers(ke["[汉化组] 测试作品 1443022"])),
    )
    check(
        "magazine name on a structural Aid does not block",
        import_scan.sweep_blockers({"hints": ["magazine_like"], "aid_source": "subdir"}) == [],
        "structural Aid under a magazine-style name is normal",
    )
    check(
        "magazine name on a name_digits Aid does block",
        import_scan.sweep_blockers({"hints": ["magazine_like"], "aid_source": "name_digits"}) == ["magazine_like"],
        "issue numbers are not album ids",
    )
    check(
        "suspected_same blocks a sweep",
        import_scan.sweep_blockers({"hints": ["suspected_same"], "aid_source": "subdir"}) == ["suspected_same"],
    )
    check(
        "already-in-library does not block (skip-existing handles it)",
        import_scan.sweep_blockers({"hints": ["already_in_library"], "aid_source": "subdir"}) == [],
    )
    check("archive_aids is read-only and returns a set", isinstance(import_scan.archive_aids(), set))

    # write/load round trip
    out = import_scan.write_manifest(manifest, base / "import_manifest.json")
    check("manifest written", out.is_file())
    loaded = import_scan.load_manifest(base / "import_manifest.json")
    check("manifest round-trips", loaded is not None and loaded["summary"] == manifest["summary"])
    check("load_manifest missing -> None", import_scan.load_manifest(base / "nope.json") is None)

    # source tree untouched
    before = sorted(str(p) for p in root.rglob("*"))
    import_scan.scan(root, known_aids=set())
    after = sorted(str(p) for p in root.rglob("*"))
    check("source tree unchanged by scanning", before == after)

    # container unwrap: scanning the parent must find the books, not the container
    parent_scan = import_scan.scan(base, known_aids=set())
    check(
        "container unwrap descends to the book level",
        parent_scan["summary"]["dirs"] == manifest["summary"]["dirs"]
        and parent_scan["summary"]["images"] == manifest["summary"]["images"],
        str(parent_scan["summary"]),
    )
    check(
        "container unwrap records both roots",
        parent_scan["requested_root"] != parent_scan["root"] and parent_scan["root"].endswith("JM"),
        f"{parent_scan['requested_root']} -> {parent_scan['root']}",
    )
    check("container itself never becomes an entry", all(e["dir"] != "JM" for e in parent_scan["entries"]))

    # short (3-digit) Aid with cover file: pages must count, id is a real Aid
    root2 = base / "JM2"
    md = root2 / "[砂漠] 测试 (COMIC LO 2017年7月号)"
    img(md / "187_cover.jpg")
    for i in range(1, 22):
        img(md / "187" / "1" / f"{i:03d}.jpg")
    m2 = import_scan.scan(root2, known_aids=set())
    e2 = {e["dir"]: e for e in m2["entries"]}["[砂漠] 测试 (COMIC LO 2017年7月号)"]
    check("short-Aid tree classified nested", e2["kind"] == "nested", e2["kind"])
    check("short-Aid tree counts its 21 pages", e2["image_count"] == 21, str(e2["image_count"]))
    check(
        "short-Aid cover file is not a page",
        e2["image_count"] + 1 == len(e2["chapters"]["1"]) + 1,
        str(e2["cover_files"]),
    )
    check(
        "short-Aid reported and valid", e2["aid"] == "187" and e2["aid_valid"] is True, f"{e2['aid']}/{e2['aid_valid']}"
    )
    check("short-Aid entry still needs review (magazine name)", e2["needs_review"] is True)
    check("short-Aid summary counts no invalid aids", m2["summary"]["invalid_aids"] == 0, str(m2["summary"]))
    check("short-Aid summary images reconcile", m2["summary"]["images"] == 21, str(m2["summary"]["images"]))

    # near-duplicate names (A-5 merge queue) are review evidence, never a merge
    root3 = base / "JM3"
    for nm, aid in (
        ("小海和暑假 有一点长名字 1440001", "1440001"),
        ("海妹妹和暑假 有一点长名字 1440002", "1440002"),
        ("完全不同的作品 1500000", "1500000"),
    ):
        d3 = root3 / nm
        for i in range(1, 3):
            img(d3 / str(aid) / "1" / f"{i:03d}.jpg")
    m3 = import_scan.scan(root3, known_aids=set())
    pairs = m3["near_duplicates"]
    check("near-duplicate pair found for similar titles", len(pairs) >= 1, str(pairs))
    check(
        "near-duplicate pair names both dirs",
        any("小海" in p["a"] and "海妹妹" in p["b"] or "海妹妹" in p["a"] and "小海" in p["b"] for p in pairs),
        str(pairs),
    )
    check("unrelated title not paired", not any("完全不同的作品" in (p["a"], p["b"]) for p in pairs), str(pairs))
    check(
        "near-duplicate is evidence only (no merge side effect)",
        m3["summary"]["dirs"] == 3 and len(m3["entries"]) == 3,
        str(m3["summary"]["dirs"]),
    )
    check(
        "near_duplicate_pairs summary matches",
        m3["summary"]["near_duplicate_pairs"] == len(pairs),
        str(m3["summary"]["near_duplicate_pairs"]),
    )

    # Volumes of one series must NOT land in the merge queue (前篇/后篇, 第1话/第2话).
    root4 = base / "JM4"
    for nm, aid in (
        ("某系列作品 前篇 1440003", "1440003"),
        ("某系列作品 后篇 1440004", "1440004"),
        ("另一系列 第1话 1440005", "1440005"),
        ("另一系列 第2话 1440006", "1440006"),
    ):
        d4 = root4 / nm
        for i in range(1, 3):
            img(d4 / str(aid) / "1" / f"{i:03d}.jpg")
    m4 = import_scan.scan(root4, known_aids=set())
    near4, series4 = m4["near_duplicates"], m4["series"]
    check("volume pair goes to series, not merge queue", not near4, str(near4))
    check(
        "series pair detected for 前篇/后篇", any("前篇" in p["a"] or "前篇" in p["b"] for p in series4), str(series4)
    )
    check(
        "series pair detected for 第N话",
        any(all(any(tok in name for name in (p["a"], p["b"])) for tok in ("第1话", "第2话")) for p in series4),
        str(series4),
    )
    check("series pairs carry volume markers", all(p.get("volumes") for p in series4), str(series4))
    check(
        "series never merges books",
        sorted(e["aid"] for e in m4["entries"]) == ["1440003", "1440004", "1440005", "1440006"],
        str(m4["summary"]["unique_aids"]),
    )

    shutil.rmtree(base, ignore_errors=True)

    print(f"\nA1 checks: {PASSED} passed, {len(FAILED)} failed")
    if FAILED:
        print("FAILED:", FAILED)
        return 1
    print("A1 contract PASSED (offline, no real-library access)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
