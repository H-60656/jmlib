"""Read-only storage health checks (P3).

The report is derived from live state, never from cached opinions:

* SQLite catalog (books / chapters / covers / reading memory / findings)
* the physical ``library/archive`` zips (central directory only — no 25 GB
  decompression; MD5 is computed only for the small duplicate-page candidate set)
* the migration manifest (generation provenance, rollback readiness)
* the loose-book queue root recorded in that manifest (never hard-coded)

``collect()`` performs **zero writes** — no DB mutation, no file mutation, no
deletion.  ``report(export_csv=True)`` additionally writes two derived CSV files
under ``exports/`` (gitignored).  File-level repairs are P5 governance actions;
this module only records decisions through the findings state machine.
"""

from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from jmlib import paths, store
from jmlib.aid import AID_RE, cover_aid

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}

# Duplicate-page scanning reads 1455 zip central directories.  The archive is
# static between ingests, so cache the result for this process keyed by a cheap
# catalog signature; a new ingest changes the signature and invalidates it.
_DUP_CACHE: dict[tuple[int, int], dict] = {}

# Findings state machine.  "executed" is deliberately NOT reachable from the
# P3 decision API: only a real P5 executor may set it after an approved change
# manifest has actually been applied.  "rolled_back" is likewise a P5 record.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"approved", "rejected"},
    "approved": {"rejected", "pending"},
    "rejected": {"pending"},
    "executed": {"rolled_back"},
    "rolled_back": {"pending"},
}


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _section(
    key: str,
    title: str,
    level: str,
    count: int,
    unit: str,
    detail: str,
    samples: list[str] | None = None,
    action: str = "",
    destructive: bool = False,
) -> dict:
    return {
        "key": key,
        "title": title,
        "level": level,  # ok | info | warn
        "count": count,
        "unit": unit,
        "detail": detail,
        "samples": samples or [],
        "action": action,
        "destructive": destructive,
    }


def _manifest() -> dict:
    if not paths.MIGRATION_MANIFEST.is_file():
        return {}
    loaded = json.loads(paths.MIGRATION_MANIFEST.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _catalog_rows() -> tuple[list[dict], list[dict]]:
    conn = store.connect()
    try:
        books = [dict(r) for r in conn.execute("SELECT aid,title,cover_path,generation FROM books ORDER BY aid")]
        chapters = [
            dict(r)
            for r in conn.execute("SELECT aid,pindex,zip_path,page_count,cover_path FROM chapters ORDER BY aid,pindex")
        ]
    finally:
        conn.close()
    return books, chapters


def _catalog_section(books: list[dict], chapters: list[dict]) -> dict:
    no_title = [b["aid"] for b in books if not b["title"]]
    no_cover = [(c["aid"], c["pindex"]) for c in chapters if not c["cover_path"]]
    broken_cover = [
        (c["aid"], c["pindex"]) for c in chapters if c["cover_path"] and not (paths.ROOT / c["cover_path"]).is_file()
    ]
    missing_zip = [(c["aid"], c["pindex"]) for c in chapters if not (paths.ROOT / c["zip_path"]).is_file()]
    empty = [(c["aid"], c["pindex"]) for c in chapters if not c["page_count"]]
    problems = len(no_title) + len(no_cover) + len(broken_cover) + len(missing_zip) + len(empty)
    samples: list[str] = []
    if no_title:
        samples.append("标题待补（Android 世代无标题文件夹）: " + ", ".join(no_title[:12]))
    for label, rows in (
        ("缺封面", no_cover),
        ("封面文件丢失", broken_cover),
        ("zip 文件丢失", missing_zip),
        ("空章节", empty),
    ):
        if rows:
            samples.append(f"{label}: " + ", ".join(f"{a}/{p}" for a, p in rows[:8]))
    detail = (
        f"{len(books)} 本 · {len(chapters)} 章节 · "
        f"{sum(c['page_count'] for c in chapters)} 页；"
        f"标题待补 {len(no_title)}、缺封面 {len(no_cover)}、封面丢失 {len(broken_cover)}、"
        f"zip 丢失 {len(missing_zip)}、空章节 {len(empty)}。"
    )
    return _section(
        "catalog",
        "目录完整性",
        "warn" if problems else "ok",
        problems,
        "项",
        detail,
        samples,
        action="标题待补可待 P4 由 JmAlbumDetail 补齐；封面缓存可用 jmlib index 重建（不动归档）。",
    )


def _generation_section(manifest: dict, books: list[dict], chapters: list[dict]) -> dict:
    entries = manifest.get("entries", [])
    strict_gen = Counter(e.get("generation", "") for e in entries if e.get("kind") == "strict")
    book_gen = Counter(b.get("generation", "") for b in books)
    generations = sorted(set(strict_gen) | set(book_gen))
    samples = [f"{g or '(空)'}：归档包 {strict_gen[g]} · 建书 {book_gen[g]}" for g in generations]
    detail = (
        f"P0 已把 {len(generations)} 个来源世代拉平为 library/archive/{{Aid}}/{{Pindex}}.zip；"
        f"世代名只保留在迁移清单与 books.generation 字段，不再作路径或身份键。"
        f"当前 {len(books)} 本 / {len(chapters)} 章节已归位。"
    )
    return _section(
        "generations",
        "世代目录已拉平",
        "ok",
        len(generations),
        "个世代",
        detail,
        samples,
        action="无需修复；来源追溯见 data/migration_manifest.json。",
    )


def _rollback_section(manifest: dict) -> dict:
    checks = {
        "migration_manifest.json": paths.MIGRATION_MANIFEST.is_file(),
        "rollback_manifest.json": paths.ROLLBACK_MANIFEST.is_file(),
        "_zip_signatures.json": paths.SIGNATURES.is_file(),
        "_zip_signatures.rebound.json": (paths.LIBRARY / "_zip_signatures.rebound.json").is_file(),
        "_incoming 空骨架（供反向 rename）": paths.INCOMING.is_dir(),
    }
    ok = all(checks.values())
    samples = [f"{k}: {'OK' if v else '缺失'}" for k, v in checks.items()]
    detail = f"{sum(checks.values())}/{len(checks)} 项回滚资源就位。" + (
        " 可执行 jmlib migrate --rollback 反向 rename 回源路径（零删除）。" if ok else " 有缺失项，回滚前需先核对。"
    )
    return _section(
        "rollback",
        "迁移回滚就绪",
        "ok" if ok else "warn",
        sum(checks.values()),
        "项",
        detail,
        samples,
        action="回滚为整批动作，执行前仍需逐项确认。",
    )


def _unresolved_section(unresolved: list[dict]) -> dict:
    by_gen = Counter(u["generation"] for u in unresolved)
    by_reason = Counter(u["reason"] for u in unresolved)
    total_bytes = sum(u["size"] for u in unresolved)
    samples = [f"{g or '(空)'}: {n} 包" for g, n in by_gen.most_common()]
    samples += [f"原因 {r}: {n}" for r, n in by_reason.most_common(4)]
    detail = (
        f"{len(unresolved)} 个 zip 路径中无可靠车号，未建书、未抽封面、不进封面墙；"
        f"占 {total_bytes / 1024 / 1024 / 1024:.2f} GiB，全部保留在 library/_unidentified/。"
        " 其中部分为刊物期号（如「COMIC 失乐天 2022年6月号」），严禁当车号。"
    )
    return _section(
        "unresolved",
        "未识别包",
        "info",
        len(unresolved),
        "包",
        detail,
        samples,
        action="可在 P5 逐项手工指定 Aid 后转正；当前只导出 CSV，不联网回填。",
    )


def _loose_ingest_state() -> dict:
    """Read the P4 loose-ingest manifest (read-only) to see what was ingested.

    The manifest is written by ``jmlib ingest --plan-only/--apply``; it is the
    authoritative record of how many loose books were packed into the library.
    ``in_library`` cross-checks that the target ``{Aid}/`` trees actually exist,
    so a stale manifest alone can never mark the queue as done.
    """
    empty = {"present": False, "books": 0, "chapters": 0, "images": 0, "in_library": 0, "source_dirs": set()}
    if not paths.LOOSE_INGEST_MANIFEST.is_file():
        return empty
    try:
        m = json.loads(paths.LOOSE_INGEST_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {**empty, "present": True}
    summary = m.get("summary") or {}
    aids = [str(b["aid"]) for b in m.get("books", []) if b.get("aid")]
    in_library = sum(1 for a in aids if (paths.ARCHIVE / a).is_dir())
    # Directory names P4 actually packed.  The queue root keeps living, so new
    # directories can appear (or old ones be renamed) after the ingest — those
    # must never be reported as "already ingested".
    source_dirs: set[str] = set()
    for b in m.get("books", []):
        for d in b.get("source_dirs") or []:
            source_dirs.add(Path(d).name)
    return {
        "present": True,
        "books": int(summary.get("books_with_body", 0)),
        "chapters": int(summary.get("chapters", 0)),
        "images": int(summary.get("images", 0)),
        "in_library": in_library,
        "source_dirs": source_dirs,
    }


def _loose_section(manifest: dict) -> dict:
    loose = manifest.get("loose") or {}
    root_raw = loose.get("root")
    root = Path(root_raw) if root_raw else None
    if root is None or not root.is_dir():
        return _section(
            "loose_queue",
            "散装队列",
            "info",
            0,
            "本",
            "迁移清单未记录可用的散装队列根目录，或目录已不存在；无法只读复核。",
            [],
            action="P4 打包入库前需重新指定 --loose。",
        )

    books: list[dict] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        # ``cover_aid`` returns the Aid or ``None`` in one step; the set
        # comprehension keeps only real hits.
        cover_aids = {aid_of for f in d.iterdir() if f.is_file() and (aid_of := cover_aid(f.name))}
        aid_dirs = {p.name for p in d.iterdir() if p.is_dir() and AID_RE.fullmatch(p.name)}
        jpgs = sum(1 for _ in d.rglob("*.jpg"))
        books.append({"name": d.name, "covers": cover_aids, "aid_dirs": aid_dirs, "jpgs": jpgs})

    by_aid: dict[str, list[dict]] = defaultdict(list)
    for b in books:
        for aid in b["covers"] | b["aid_dirs"]:
            by_aid[aid].append(b)
    splits = {a: bs for a, bs in by_aid.items() if len(bs) > 1}
    cover_only = [b["name"] for b in books if b["covers"] and not b["aid_dirs"]]
    missing_cover = [b["name"] for b in books if b["aid_dirs"] and not b["covers"]]
    total_jpgs = sum(b["jpgs"] for b in books)

    samples: list[str] = []
    for aid, bs in splits.items():
        samples.append(
            f"Aid {aid} 分裂："
            + " | ".join(
                f"{b['name'][:24]}（封面{len(b['covers'])} 章目录{len(b['aid_dirs'])} 图{b['jpgs']}）" for b in bs
            )
        )
    if cover_only:
        samples.append("仅封面无正文: " + ", ".join(n[:28] for n in cover_only))
    if missing_cover:
        samples.append("有正文缺封面: " + ", ".join(n[:28] for n in missing_cover))

    problems = len(splits) + len(cover_only) + len(missing_cover)
    ingested = _loose_ingest_state()
    done = ingested["present"] and ingested["in_library"] > 0

    # Directories P4 already packed vs directories that appeared later.  The queue
    # root keeps living, so a stale "done" claim would hide genuinely pending work.
    packed_names = ingested.get("source_dirs") or set()
    packed = [b for b in books if b["name"] in packed_names]
    pending = [b for b in books if b["name"] not in packed_names]
    pending_aids = {aid for b in pending for aid in (b["covers"] | b["aid_dirs"])}

    if done and pending:
        title = "散装队列（P4 已入库 + 新目录待入库）"
        level = "warn"
        unit = "项异常"
        detail = (
            f"P4 已把 {ingested['in_library']}/{ingested['books']} 本散装书"
            f"（{ingested['chapters']} 章节 / {ingested['images']} 张图）打包入库，"
            f"其 {len(packed)} 个源目录按零删除约定原样保留；"
            f"但队列里另有 {len(pending)} 个目录 / {len(pending_aids)} 个车号 / "
            f"{sum(b['jpgs'] for b in pending)} 张 jpg 尚未入库（P4 之后新增或改名）。"
            f"源目录残留异常：跨目录分裂 {len(splits)}、仅封面 {len(cover_only)}、"
            f"有正文缺封面 {len(missing_cover)}。"
        )
        action = "新目录需走导入流程（A 段）逐项确认 Aid 后再入库；源目录清理属 P5 破坏性动作，本页只读。"
    elif done:
        # P4 already packed the queue; the source dirs are preserved by the
        # zero-deletion convention, so the residual split/cover anomalies are
        # informational facts about the source, not pending work on the library.
        title = "散装队列（P4 已入库）"
        level = "warn" if problems else "ok"
        unit = "项源目录残留"
        detail = (
            f"P4 已把 {ingested['in_library']}/{ingested['books']} 本散装书"
            f"（{ingested['chapters']} 章节 / {ingested['images']} 张图）打包入库；"
            f"源 {len(books)} 个目录 / {total_jpgs} 张 jpg 按零删除约定原样保留，仍占用空间且待 P5 确认清理。"
            f"入库时已按 Aid 物理合并、由首图重建封面，书库不含这些结构异常；"
            f"以下仅为源目录残留：跨目录分裂 {len(splits)}、仅封面 {len(cover_only)}、"
            f"有正文缺封面 {len(missing_cover)}。"
        )
        action = "源目录清理属 P5 破坏性动作：逐项确认、生成完整清单并保留回滚后才执行；本页只读。"
    else:
        title = "散装待入库队列"
        level = "warn" if problems else "ok"
        unit = "项异常"
        detail = (
            f"{len(books)} 个散装目录 / {total_jpgs} 张 jpg 仍在原路径（P0 未移动，P4 待入库）；"
            f"按封面文件名与车号目录识别到 {len(by_aid)} 个车号，"
            f"跨目录分裂 {len(splits)} 个、仅封面 {len(cover_only)} 个、有正文缺封面 {len(missing_cover)} 个。"
        )
        action = "P4 打包时按 Aid 物理合并成单一 {Aid}/ 树、缺封面由首图补；原目录保留到确认才清理。"

    section = _section(
        "loose_queue",
        title,
        level,
        problems,
        unit,
        detail,
        samples,
        action=action,
        destructive=True,
    )
    section["books"] = len(books)
    section["jpgs"] = total_jpgs
    section["aids"] = len(by_aid)
    return section


def _duplicate_pages(chapters: list[dict]) -> dict:
    signature = (len(chapters), sum(c["page_count"] for c in chapters))
    if signature in _DUP_CACHE:
        return _DUP_CACHE[signature]

    zip_by_chapter = {(c["aid"], c["pindex"]): c["zip_path"] for c in chapters}
    buckets: dict[tuple[int, int], list[tuple[str, int, str, str]]] = defaultdict(list)
    unreadable: list[str] = []
    members_total = 0
    for ch in chapters:
        zp = paths.ROOT / ch["zip_path"]
        try:
            with zipfile.ZipFile(zp) as zf:
                for info in zf.infolist():
                    if Path(info.filename).suffix.lower() not in IMAGE_SUFFIXES:
                        continue
                    members_total += 1
                    buckets[(info.CRC, info.file_size)].append((ch["aid"], ch["pindex"], info.filename, ch["zip_path"]))
        except (zipfile.BadZipFile, OSError):
            unreadable.append(f"{ch['aid']}/{ch['pindex']}")

    candidates = {k: v for k, v in buckets.items() if len(v) > 1}
    intra = 0
    cross_only = 0
    confirmed: list[dict] = []
    reclaim = 0
    for (crc, size), group in candidates.items():
        by_aid: dict[str, list[tuple[str, int, str, str]]] = defaultdict(list)
        for t in group:
            by_aid[t[0]].append(t)
        same = [t for ts in by_aid.values() if len(ts) > 1 for t in ts]
        if not same:
            cross_only += 1
            continue
        intra += 1
        # MD5-confirm only the small candidate set; group within one book.
        by_hash: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)
        for aid, pindex, name, _zp in same:
            with zipfile.ZipFile(paths.ROOT / zip_by_chapter[(aid, pindex)]) as zf:
                h = hashlib.md5(zf.read(name)).hexdigest()
            by_hash[(aid, h)].append((pindex, name))
        for (aid, _h), ts in by_hash.items():
            if len(ts) < 2:
                continue
            reclaim += size * (len(ts) - 1)
            confirmed.append(
                {
                    "aid": aid,
                    "size": size,
                    "pages": [f"{p}/{n}" for p, n in sorted(ts)],
                }
            )

    confirmed.sort(key=lambda d: (-len(d["pages"]), d["aid"]))
    out = {
        "zips": len(chapters),
        "members": members_total,
        "unreadable": unreadable,
        "candidate_groups": len(candidates),
        "candidate_files": sum(len(v) for v in candidates.values()),
        "intra_book_groups": intra,
        "cross_book_only_groups": cross_only,
        "confirmed_groups": len(confirmed),
        "reclaimable_bytes": reclaim,
        "groups": confirmed,
    }
    _DUP_CACHE[signature] = out
    return out


def _duplicate_section(chapters: list[dict]) -> dict:
    d = _duplicate_pages(chapters)
    samples = [
        f"#{g['aid']}：" + ", ".join(g["pages"][:6]) + ("…" if len(g["pages"]) > 6 else "") for g in d["groups"][:8]
    ]
    detail = (
        f"只读中央目录比对 {d['zips']} 个 zip / {d['members']} 张图："
        f"CRC+长度候选 {d['candidate_groups']} 组（{d['candidate_files']} 张），"
        f"同书内 {d['intra_book_groups']} 组、仅跨书 {d['cross_book_only_groups']} 组；"
        f"MD5 复核确认同书重复 {d['confirmed_groups']} 组，"
        f"理论冗余约 {d['reclaimable_bytes'] / 1024 / 1024:.1f} MiB"
        + (f"，另有 {len(d['unreadable'])} 个 zip 无法读取。" if d["unreadable"] else "。")
        + " 重复页常呈每 25 页一张的规律，可能是合集占位/重复收录，"
        "**仅作信息提示，绝不自动删除**。"
    )
    return _section(
        "duplicate_pages",
        "同本内重复页",
        "info",
        d["confirmed_groups"],
        "组",
        detail,
        samples,
        action="P5 可逐项确认后生成变更清单并回滚；本页不改动任何 zip。",
        destructive=True,
    )


def collect() -> dict:
    """Pure read-only health snapshot.  No DB write, no file mutation."""
    books, chapters = _catalog_rows()
    manifest = _manifest()
    unresolved = store.list_unresolved()
    findings = store.list_findings()

    sections = [
        _catalog_section(books, chapters),
        _generation_section(manifest, books, chapters),
        _rollback_section(manifest),
        _unresolved_section(unresolved),
        _loose_section(manifest),
        _duplicate_section(chapters),
    ]
    status_counts = dict(Counter(f["status"] for f in findings))
    return {
        "generated_at": _utc(),
        "read_only": True,
        "note": "体检只读：不改动任何归档文件。修复动作在 P5 逐项确认后执行；此处只记录裁决与变更清单。",
        "stats": store.stats(),
        "summary": {
            "sections": len(sections),
            "warn": sum(1 for s in sections if s["level"] == "warn"),
            "findings": len(findings),
            "findings_by_status": status_counts,
            "loose_books": next((s.get("books", 0) for s in sections if s["key"] == "loose_queue"), 0),
        },
        "sections": sections,
        "findings": findings,
        "transitions": {k: sorted(v) for k, v in ALLOWED_TRANSITIONS.items()},
    }


# ---------------------------------------------------------------------------
# Findings state machine (decision records only — no file-level execution here)
# ---------------------------------------------------------------------------


def decide(fid: int, status: str) -> tuple[bool, str]:
    """Validate and apply a decision transition.  Returns (ok, message)."""
    finding = store.get_finding(fid)
    if finding is None:
        return False, "finding not found"
    current = finding["status"]
    if status == "executed":
        return False, "file-level execution is a P5 governance action; P3 records decisions only"
    if status not in ALLOWED_TRANSITIONS.get(current, set()):
        allowed = ", ".join(sorted(ALLOWED_TRANSITIONS.get(current, set()))) or "(none)"
        return False, f"cannot move {current} -> {status}; allowed: {allowed}"
    store.set_finding_status(fid, status)
    return True, f"{current} -> {status}"


# ---------------------------------------------------------------------------
# CLI report + CSV export
# ---------------------------------------------------------------------------


def _csv_rows(report: dict) -> list[list[str]]:
    rows: list[list[str]] = [["section", "level", "count", "unit", "detail", "sample"]]
    for s in report["sections"]:
        rows.append([s["key"], s["level"], str(s["count"]), s["unit"], s["detail"], " | ".join(s["samples"][:3])])
    return rows


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    paths.EXPORTS.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)


def report(export_csv: bool = False) -> int:
    """Print a human-readable health report; optionally export derived CSVs."""
    data = collect()
    print(f"jmlib 存储体检  {data['generated_at']}")
    print(data["note"])
    s = data["stats"]
    print(
        f"\n总览  {s['books']} 本 / {s['chapters']} 章节 / {s['page_total']} 页 / "
        f"{s['unresolved']} 未识别 / {s['memory']} 记忆点 / "
        f"{data['summary']['findings']} 治理项"
    )
    for sec in data["sections"]:
        flag = {"ok": "OK  ", "info": "INFO", "warn": "WARN"}[sec["level"]]
        print(f"\n[{flag}] {sec['title']}  {sec['count']} {sec['unit']}")
        print(f"       {sec['detail']}")
        for sample in sec["samples"][:6]:
            print(f"       · {sample}")
        if sec["action"]:
            print(f"       → {sec['action']}")
    if data["findings"]:
        print("\n治理项（findings）")
        for f in data["findings"]:
            print(f"  #{f['id']} [{f['status']}] {f['kind']} · {f['target']}")
    else:
        print("\n治理项：暂无（体检不会自动创建治理项）。")

    if export_csv:
        unresolved_rows = [["rel_path", "reason", "generation", "size"]] + [
            [u["rel_path"], u["reason"], u["generation"], str(u["size"])] for u in store.list_unresolved()
        ]
        _write_csv(paths.EXPORTS / "jmlib-unresolved.csv", unresolved_rows)
        _write_csv(paths.EXPORTS / "jmlib-health.csv", _csv_rows(data))
        print(f"\n已导出 CSV → {paths.EXPORTS}")
    print("\n体检为只读操作；未确认不执行任何修复。")
    return 0
