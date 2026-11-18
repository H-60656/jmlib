"""jmlib command line entry point.

Handlers import their implementation lazily so the CLI stays usable while the
phases are being built out.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path


def _cmd_migrate(args: argparse.Namespace) -> int:
    from jmlib import migrate

    if args.plan:
        return migrate.plan(source=args.source, loose=args.loose)
    if args.apply:
        return migrate.apply()
    if args.rollback:
        return migrate.rollback()
    print("migrate: pass exactly one of --plan / --apply / --rollback")
    return 2


def _cmd_index(args: argparse.Namespace) -> int:
    from jmlib import indexer

    return indexer.rebuild()


def _cmd_serve(args: argparse.Namespace) -> int:
    from jmlib import api

    return api.serve(host=args.host, port=args.port, lan=args.lan)


def _cmd_check(args: argparse.Namespace) -> int:
    from jmlib import health

    return health.report(export_csv=args.csv)


def _cmd_download(args: argparse.Namespace) -> int:
    from jmlib import download

    return download.run(args.ids, option=args.option)


def _cmd_ingest(args: argparse.Namespace) -> int:
    from jmlib import ingest, paths

    if args.rollback:
        return ingest.loose_rollback()
    if args.plan_only:
        m = ingest.loose_plan(root=args.root)
        s = m["summary"]
        print(
            f"loose ingest plan: dirs={s['dirs']} aids={s['aids']} "
            f"books_with_body={s['books_with_body']} chapters={s['chapters']} "
            f"images={s['images']} cover_files={s['cover_files']} "
            f"collisions={s['collisions']}"
        )
        print(f"loose ingest plan: manifest written to {paths.LOOSE_INGEST_MANIFEST}")
        print("loose ingest plan: read-only; no zip, cover, or DB row was written")
        return 0
    if args.apply:
        return ingest.loose_apply()
    print("ingest: pass exactly one of --plan-only / --apply / --rollback")
    return 2


def _cmd_governance(args: argparse.Namespace) -> int:
    """Read-only governance overview / Data Destruction Guard (never executes)."""
    from jmlib import governance

    if args.manifest:
        m = governance.change_manifest(args.manifest)
        print(
            f"governance manifest: kind={m.get('kind')} total={m.get('total')} "
            f"bytes={m.get('total_bytes')} read_only={m.get('read_only')}"
        )
        print("  guard:", m.get("guard", {}).get("Status", m.get("error", "")))
        for item in m.get("items", [])[:20]:
            print("  ·", item.get("target"), item.get("unit"), item.get("size"))
        if m.get("truncated"):
            print(f"  … 仅显示前 {len(m['items'])} 条；完整清单见 data/ 下 manifest")
        if m.get("error"):
            print("  note:", m["error"])
        return 0
    if args.guard:
        g = governance.destruction_guard(args.guard)
        if "error" in g:
            print(g["error"])
            return 2
        print(f"Data Destruction Guard: {g['kind']}")
        for k, v in g.items():
            if k != "kind":
                print(f"  - {k}: {v}")
        return 0
    s = governance.status()
    print(s["note"])
    print(
        f"actions={len(s['actions'])} executed={s['executed']} "
        f"rolled_back={s['rolled_back']} destructive={len(s['destructive'])}"
    )
    for a in s["actions"]:
        print(f"  #{a['id']} [{a['status']}] {a['aid']}/{a['pindex']} {a['from']} -> {a['to']}")
    return 0


def _cmd_resolve(args: argparse.Namespace) -> int:
    """Name→Aid resolution and tag backfill for the unidentified shelf."""
    from jmlib import resolve

    if args.tags and args.summary:
        print("resolve: --tags and --summary do different things; pick one")
        return 2

    if args.tags:
        result = resolve.fill_tags(limit=args.limit, dry_run=not args.apply)
        s = result["summary"]
        print(
            f"tag backfill{' (dry run)' if result['dryRun'] else ''}: "
            f"requested={result['requested']} ok={s['ok']} "
            f"withTags={s['withTags']} written={s['written']} failed={s['failed']}"
        )
        for item in result["items"][:20]:
            mark = "ok" if item["ok"] else "fail"
            print(
                f"  · {item['aid']} [{mark}] {', '.join(item['tags']) or '-'}"
                f"{((' / ' + item['error']) if item['error'] else '')}"
            )
        if not result["dryRun"] and s["written"] == 0:
            print("tag backfill: nothing was written")
        return 0

    if args.summary:
        units = resolve.book_units()
        s = resolve.summary(units)
        print(
            f"unidentified shelf: units={s['units']} zips={s['zips']} "
            f"folders={s['folders']} flat={s['flat']} "
            f"with_aid_hint={s['with_aid_hint']} conflicting={s['conflicting_aids']} "
            f"no_query={s['no_query']}"
        )
        for generation, count in sorted(s["byGeneration"].items()):
            print(f"  · {generation}: {count} units")
        print("resolve summary: read-only; nothing was written and no request was made")
        return 0

    if not args.dry_run and not args.apply:
        print("resolve: pass one of --summary / --dry-run / --apply (or --tags)")
        return 2

    result = resolve.resolve(
        limit=args.limit,
        dry_run=not args.apply,
        min_score=(resolve.DEFAULT_MIN_SCORE if args.min_score is None else args.min_score),
        min_margin=(resolve.DEFAULT_MIN_MARGIN if args.min_margin is None else args.min_margin),
        force=args.force,
    )
    s = result["summary"]
    print(
        f"resolve{' (dry run)' if result['dryRun'] else ''}: "
        f"considered={result['considered']} decided={s['decided']} "
        f"byStatus={s['byStatus']}"
    )
    for item in result["items"][:25]:
        print(
            f"  · [{item['status']}] {item['name']} -> {item['chosen'] or '—'} (score {item['score']}) {item['note']}"
        )
    if len(result["items"]) > 25:
        print(f"  … 另有 {len(result['items']) - 25} 条未显示")
    if result["dryRun"]:
        print("resolve: read-only; no decision row was written and no archive byte moved")
    else:
        print("resolve: decisions recorded in id_resolution; promotion still needs the governance token")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jmlib", description="Local jmcomic resource library")
    parser.add_argument("--version", action="version", version="jmlib 0.1.0")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("migrate", help="plan / apply / rollback the archive migration")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--plan", action="store_true", help="read-only: generate full manifest")
    g.add_argument("--apply", action="store_true", help="execute the recorded migration")
    g.add_argument("--rollback", action="store_true", help="reverse the recorded migration")
    p.add_argument("--source", type=Path, default=None, help="pre-migration archive root")
    p.add_argument("--loose", type=Path, default=None, help="loose-book queue root")
    p.set_defaults(handler=_cmd_migrate)

    p = sub.add_parser("index", help="rebuild SQLite index + covers from library/")
    p.set_defaults(handler=_cmd_index)

    p = sub.add_parser("serve", help="start the local web library")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--lan", action="store_true", help="bind 0.0.0.0 (PIN required)")
    p.set_defaults(handler=_cmd_serve)

    p = sub.add_parser("check", help="read-only storage health report")
    p.add_argument("--csv", action="store_true", help="also export unresolved as CSV")
    p.set_defaults(handler=_cmd_check)

    p = sub.add_parser("download", help="download + auto-ingest via jmcomic")
    p.add_argument("ids", nargs="+", help="album ids (or p-prefixed photo ids)")
    p.add_argument("--option", type=Path, default=None, help="path to option.yml")
    p.set_defaults(handler=_cmd_download)

    p = sub.add_parser("ingest", help="pack the loose-book queue into library/")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--plan-only", action="store_true", help="read-only: write loose_ingest_manifest.json")
    g.add_argument("--apply", action="store_true", help="pack books from the recorded manifest")
    g.add_argument("--rollback", action="store_true", help="reverse a loose ingest")
    p.add_argument("--root", type=Path, default=None, help="loose queue root (default: migration manifest)")
    p.set_defaults(handler=_cmd_ingest)

    p = sub.add_parser("governance", help="read-only P5 governance overview / guards")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--manifest", metavar="KIND", help="show a read-only change manifest")
    g.add_argument("--guard", metavar="KIND", help="show a Data Destruction Guard")
    p.set_defaults(handler=_cmd_governance)

    p = sub.add_parser("resolve", help="identify the unidentified shelf by title (serial, throttled)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--summary", action="store_true", help="read-only: group the shelf into books and count them")
    g.add_argument("--dry-run", action="store_true", help="look up every book but write no decision")
    g.add_argument("--apply", action="store_true", help="record the decisions in id_resolution (still no file moved)")
    p.add_argument(
        "--tags",
        action="store_true",
        help="backfill tags/author from the album detail endpoint "
        "(combine with --apply to write; alone it is a dry run)",
    )
    p.add_argument("--limit", type=int, default=None, help="cap how many books are considered")
    p.add_argument("--min-score", type=float, default=None, help="override the auto-accept score floor")
    p.add_argument("--min-margin", type=float, default=None, help="override the auto-accept lead-over-runner-up floor")
    p.add_argument("--force", action="store_true", help="ignore recorded decisions and re-query upstream")
    p.set_defaults(handler=_cmd_resolve)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))
