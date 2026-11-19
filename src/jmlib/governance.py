"""P5 controlled governance — confirmation-first, reversible, zero deletion.

anti-entropy-governance puts this module in ``confirmation-first`` territory: the
library holds persistent, unique, irreplaceable bytes (25.3 GB of one-of-a-kind
chapter zips).  Two action classes are therefore deliberately separated:

``promote`` (reversible, zero deletion)
    Move ONE unresolved zip from ``library/_unidentified/`` into
    ``library/archive/{Aid}/{Pindex}.zip`` with a same-volume ``os.rename``,
    rebuild that chapter's cover from the zip's first member, and register the
    chapter in SQLite.  Every step is recorded in a durable action manifest and
    is exactly reversible (rename back + drop the DB rows).  **No file is ever
    deleted.**  Execution requires an explicit, *scoped* confirmation token that
    names the exact source and target; generic "go ahead" is not accepted.

Destructive classes (``loose_cleanup`` / ``duplicate_pages`` / ``generations`` /
``migration_rollback``)
    These would delete or irreversibly mutate persistent source bytes.  This
    module exposes ONLY a read-only ``Data Destruction Guard`` and a change
    manifest for them.  There is **no apply/execute function, no deletion call,
    and no runnable destructive command** for these classes.  Their status stays
    ``awaiting scoped confirmation``; a human must decide each item explicitly.
"""

from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from jmlib import cover, ingest, paths, store
from jmlib.aid import AID_RE

SCHEMA = "jmlib.governance.v1"

# Persistent-state / irreversible classes: guards only, never executors.
DESTRUCTIVE_KINDS = {
    "loose_cleanup": "删除 P4 散装源目录（16 个目录 / 1277 张 jpg）以释放空间",
    "duplicate_pages": "从章节 zip 中移除同本内重复页以释放约 38.8 MiB",
    "generations": "合并/改写来源世代目录结构",
    "migration_rollback": "整批反向 rename 把 25.3 GB 搬回原路径",
}


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Durable action ledger (governance_manifest.json) — append-only history.
# ---------------------------------------------------------------------------


def _empty_ledger() -> dict:
    return {"schemaVersion": SCHEMA, "kind": "governance", "updatedAt": _utc(), "actions": []}


def _load_ledger() -> dict:
    """The append-only history, or an empty ledger when there is nothing readable.

    Narrowed to ``dict`` here rather than at every call site: the whole module
    indexes ``actions``, and each ``ledger["actions"]`` guarded separately would
    be a guard nobody could keep consistent.
    """
    if not paths.GOVERNANCE_MANIFEST.is_file():
        return _empty_ledger()
    try:
        loaded = json.loads(paths.GOVERNANCE_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty_ledger()
    return loaded if isinstance(loaded, dict) else _empty_ledger()


def _save_ledger(ledger: dict) -> None:
    paths.DATA.mkdir(parents=True, exist_ok=True)
    ledger["updatedAt"] = _utc()
    part = paths.GOVERNANCE_MANIFEST.with_suffix(".json.part")
    part.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(part, paths.GOVERNANCE_MANIFEST)


def actions() -> list[dict]:
    """Read-only view of the governance action history."""
    loaded = _load_ledger().get("actions")
    return loaded if isinstance(loaded, list) else []


# ---------------------------------------------------------------------------
# Data Destruction Guard (read-only) — for destructive classes only.
# ---------------------------------------------------------------------------


def destruction_guard(kind: str, target: str = "", detail: str = "") -> dict:
    """Return a Data Destruction Guard.  Never executes anything.

    This is the ONLY thing offered for destructive classes.  ``Status`` stays
    ``awaiting scoped confirmation`` by construction — there is no code path in
    this module that can flip it to executed for a destructive kind.
    """
    desc = DESTRUCTIVE_KINDS.get(kind)
    if desc is None:
        return {"error": f"unknown destructive kind: {kind}"}
    exact = target or "<由用户逐项指定，未在此预先枚举>"
    return {
        "kind": kind,
        "Target Class": "persistent-state / irreplaceable source-of-truth bytes",
        "Exact Target(s)": exact,
        "Environment": "本机 jmlib 资源库（工作区 E:\\github\\jmcomic\\jmlib\\library）",
        "Why Irreversible": (
            "归档是唯一副本（25.3 GB 无备份）；删除或改写 zip 成员后无法从库内"
            "重建原始字节，只能依赖外部备份。原 P0 迁移与 P4 散装零删除约定也"
            "要求这些源目录保持可回溯。"
        ),
        "Backup / Rollback Note": (
            "无自动备份。若确需执行，必须先由用户对每一具体目标出具 scoped "
            "confirmation，并在项目外另做完整副本；单条回滚记录不足以恢复已删字节。"
        ),
        "Allowed Read-Only Next Steps": [
            "导出完整变更清单（CSV / manifest）供人工审阅",
            "在体检面板记录逐项裁决（findings 状态机）",
            "对 reversible 的 promote 动作走 scoped token 执行并可回滚",
        ],
        "Blocked Destructive Steps": [
            desc,
            "任何 os.remove / unlink / rmtree / zip 成员删除",
            "任何把 25.3 GB 搬回原路径的整批 rollback 自动执行",
        ],
        "Confirmation Required": "yes — explicit scoped confirmation per exact target",
        "Status": "awaiting scoped confirmation",
        "detail": detail,
    }


def refuse_destructive(kind: str, target: str = "", detail: str = "") -> dict:
    """Hard stop: a request to EXECUTE a destructive class is always refused."""
    return {
        "ok": False,
        "status": "refused",
        "message": (
            "persistent-state deletion is confirmation-first: this tool has no "
            "execution path.  Read the guard, get an explicit scoped human "
            "confirmation for each exact target, and make an external backup first."
        ),
        "guard": destruction_guard(kind, target, detail),
    }


def change_manifest(kind: str, limit: int = 40) -> dict:
    """Read-only full change list for human review.  Writes nothing, deletes nothing.

    This is the "逐项确认" preparation step for destructive classes: enumerate
    the exact targets so a human can scope a decision, without exposing any
    execution path.  ``items`` is capped for the UI; ``total`` is always exact.
    """
    if kind not in DESTRUCTIVE_KINDS:
        return {"error": f"unknown destructive kind: {kind}"}
    out: dict = {
        "kind": kind,
        "description": DESTRUCTIVE_KINDS[kind],
        "read_only": True,
        "guard": destruction_guard(kind),
        "items": [],
        "total": 0,
        "total_bytes": 0,
    }

    if kind == "loose_cleanup":
        root = None
        if paths.MIGRATION_MANIFEST.is_file():
            try:
                root = json.loads(paths.MIGRATION_MANIFEST.read_text(encoding="utf-8")).get("loose", {}).get("root")
            except (OSError, ValueError):
                root = None
        if root and Path(root).is_dir():
            dirs = sorted(p for p in Path(root).iterdir() if p.is_dir())
            out["items"] = [
                {"target": str(d), "unit": "目录", "size": sum(f.stat().st_size for f in d.rglob("*") if f.is_file())}
                for d in dirs
            ]
            out["total"] = len(dirs)
            out["total_bytes"] = sum(i["size"] for i in out["items"])
        else:
            out["error"] = "散装源根目录不可读（可能已被外部清理）"

    elif kind == "duplicate_pages":
        from jmlib import health  # lazy: health never imports governance

        conn = store.connect()
        try:
            chapters = [
                dict(r)
                for r in conn.execute(
                    "SELECT aid,pindex,zip_path,page_count,cover_path FROM chapters ORDER BY aid,pindex"
                )
            ]
        finally:
            conn.close()
        d = health._duplicate_pages(chapters)
        out["items"] = [
            {"target": f"{g['aid']}：" + ", ".join(g["pages"]), "unit": "组", "size": g["size"] * (len(g["pages"]) - 1)}
            for g in d["groups"]
        ]
        out["total"] = d["confirmed_groups"]
        out["total_bytes"] = d["reclaimable_bytes"]

    elif kind == "generations":
        from collections import Counter

        entries: list[dict] = []
        if paths.MIGRATION_MANIFEST.is_file():
            try:
                entries = json.loads(paths.MIGRATION_MANIFEST.read_text(encoding="utf-8")).get("entries", [])
            except (OSError, ValueError):
                entries = []
        counts = Counter(str(e.get("generation", "")) for e in entries)
        out["items"] = [{"target": g or "(空)", "unit": "包", "size": n} for g, n in sorted(counts.items())]
        out["total"] = len(out["items"])
        out["total_bytes"] = 0

    elif kind == "migration_rollback":
        if paths.ROLLBACK_MANIFEST.is_file():
            try:
                rows = json.loads(paths.ROLLBACK_MANIFEST.read_text(encoding="utf-8")).get("entries", [])
            except (OSError, ValueError):
                rows = []
        else:
            rows = []
        out["items"] = [
            {"target": f"{r.get('to', '')}  ←  {r.get('from', '')}", "unit": "rename", "size": 0} for r in rows[:limit]
        ]
        out["total"] = len(rows)
        out["total_bytes"] = 0
        out["note"] = "完整 2316 条反向 rename 见 data/rollback_manifest.json；整批动作，执行前仍需逐项确认。"

    if len(out["items"]) > limit:
        out["items"] = out["items"][:limit]
        out["truncated"] = True
    return out


# ---------------------------------------------------------------------------
# Reversible action: promote one unresolved zip into a real book.
# ---------------------------------------------------------------------------

_UNRESOLVED_PREFIX = "library/_unidentified/"


def _join_under(root: Path, rel_path: str) -> Path | None:
    """Join a repo-relative path, keeping it inside ``root`` without reading outside it.

    The containment test runs on the *normalised* join first (pure string
    maths), and only a candidate that already provably sits under ``root`` is
    resolved — i.e. allowed to touch the filesystem.  Resolving first would mean
    asking the disk about a path the caller chose.
    """
    p = Path(rel_path)
    if p.is_absolute() or ".." in p.parts:
        return None
    base = root.resolve()
    candidate = paths.normalize_untrusted(paths.ROOT / p)
    if not (candidate == base or candidate.is_relative_to(base)):
        return None
    try:
        target = candidate.resolve()
    except (OSError, RuntimeError):
        return None
    if not (target == base or target.is_relative_to(base)):
        return None
    return target


def _unresolved_source(rel_path: str) -> tuple[Path | None, str | None]:
    """Map an ``library/_unidentified/...`` relative path to an absolute file.

    This is the single place the project turns client-supplied text into a
    filesystem path, so the containment rule lives here and nowhere else: the
    path must be relative, free of ``..`` parts, below the unresolved root, and
    the resolved result must still sit inside that root.
    """
    if not Path(rel_path).as_posix().startswith(_UNRESOLVED_PREFIX):
        return None, "invalid unresolved rel_path"
    src = _join_under(paths.UNIDENTIFIED, rel_path)
    if src is None:
        return None, "invalid unresolved rel_path"
    return src, None


def _archive_target(rel_path: str) -> Path | None:
    """Map an ``library/archive/{Aid}/{pindex}.zip`` relative path to an absolute file.

    Only ever called with a string this module built from a validated Aid and
    pindex, but resolved and re-checked anyway so the invariant holds without
    trusting the caller.
    """
    return _join_under(paths.ARCHIVE, rel_path)


def _validate(rel_path: str, aid: str, pindex: int | None, title: str) -> tuple[dict | None, str | None]:
    if not AID_RE.fullmatch(aid):
        return None, "invalid aid (expected 1-7 digits)"
    src, err = _unresolved_source(rel_path)
    if err or src is None:
        return None, err or "invalid unresolved rel_path"
    row = store.get_unresolved(rel_path)
    if row is None:
        return None, "rel_path is not a known unresolved item"
    if not src.is_file():
        return None, "source zip missing on disk"
    try:
        with zipfile.ZipFile(src) as zf:
            members = cover.image_members(zf)
            first = members[0] if members else ""
    except (zipfile.BadZipFile, OSError) as exc:
        # Report the exception *class* only: the message can carry an absolute
        # path, and plan errors travel back over HTTP.
        return None, f"source zip unreadable ({type(exc).__name__})"
    if not members:
        return None, "source zip has no image members"

    existing = store.list_chapters(aid)
    if pindex is None:
        pindex = max((c["pindex"] for c in existing), default=0) + 1
    if pindex < 1:
        return None, "invalid pindex"
    if any(c["pindex"] == pindex for c in existing):
        return None, f"chapter {aid}/{pindex} already exists (never overwrite)"

    dst = paths.ARCHIVE / aid / f"{pindex}.zip"
    if dst.exists():
        return None, f"target already exists on disk: {paths.rel(dst)} (never overwrite)"

    would_create_book = store.get_book(aid) is None
    token = f"PROMOTE {rel_path} -> {aid}/{pindex}"
    plan = {
        "schemaVersion": SCHEMA,
        "type": "promote",
        "status": "awaiting scoped confirmation",
        "from": rel_path,
        "to": paths.rel(dst),
        "aid": aid,
        "pindex": pindex,
        "page_count": len(members),
        "first_member": first,
        "title": title,
        "would_create_book": would_create_book,
        "unresolved_row": row,
        "confirm_token": token,
        "confirmation": (
            f"此动作会把 1 个 zip 从 _unidentified 同卷 rename 到 {aid}/{pindex}.zip，"
            "并写入 1 条章节记录；零删除、可回滚。执行需输入上方精确确认串。"
        ),
        "safety": {
            "deletion": "none — only os.rename + SQLite rows; derived cover cache is left intact",
            "overwrite": "never — existing chapter/zip aborts the action",
            "reversible": "governance rollback renames back and removes exactly the added rows",
        },
        "checks": [
            "aid 合法且唯一",
            "rel_path 属于 unresolved 且无路径穿越",
            "源 zip 存在、可读、含图片成员",
            f"目标 {aid}/{pindex}.zip 不存在（不覆盖）",
            "确认串匹配后才执行（scoped confirmation）",
        ],
        "generated_at": _utc(),
    }
    return plan, None


def promote_plan(rel_path: str, aid: str, pindex: int | None = None, title: str = "") -> dict:
    """Read-only change manifest for a promote.  Writes nothing."""
    plan, err = _validate(rel_path, aid, pindex, title)
    if err or plan is None:
        return {"ok": False, "status": "invalid", "error": err or "invalid request"}
    return {"ok": True, "plan": plan}


def promote_apply(plan: dict, confirm: str) -> dict:
    """Execute a planned promote only with the exact scoped confirmation token.

    Re-validates every precondition at execution time (TOCTOU), then performs a
    zero-deletion rename + catalog registration and records the action.
    """
    if not isinstance(plan, dict) or plan.get("type") != "promote":
        return {"ok": False, "status": "invalid", "error": "not a promote plan"}
    # TOCTOU: derive a FRESH server-side manifest from the identity fields only.
    # The confirmation token is NEVER trusted from the client-supplied plan: it
    # is recomputed here, so a hand-crafted plan cannot mint its own token.
    fresh, err = _validate(plan.get("from", ""), plan.get("aid", ""), plan.get("pindex"), plan.get("title", ""))
    if err or fresh is None:
        return {"ok": False, "status": "invalid", "error": f"precondition changed: {err or 'invalid request'}"}
    token = fresh["confirm_token"]
    if not confirm or not isinstance(confirm, str) or confirm.strip() != token:
        return {
            "ok": False,
            "status": "refused",
            "error": "scoped confirmation mismatch — generic agreement is not accepted",
            "confirm_token": token,
        }
    if (
        plan.get("confirm_token") != token
        or plan.get("to") != fresh["to"]
        or plan.get("schemaVersion") != SCHEMA
        or plan.get("status") != "awaiting scoped confirmation"
    ):
        return {
            "ok": False,
            "status": "refused",
            "error": "plan is not a server-issued manifest for this exact action",
            "confirm_token": token,
        }

    src, err = _unresolved_source(fresh["from"])
    if err or src is None:
        return {"ok": False, "status": "invalid", "error": err or "invalid promote source"}
    dst = _archive_target(fresh["to"])
    if dst is None:
        return {"ok": False, "status": "invalid", "error": "invalid promote target"}
    made_parent = not dst.parent.exists()
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.rename(src, dst)  # same volume, zero deletion
    except OSError as exc:
        # P0 zips renamed in from outside the workspace keep their original ACL
        # and may not carry the confined server's DELETE ACE.  Refuse cleanly
        # instead of a 500: nothing has moved, and the catalog is untouched.
        if made_parent and dst.parent.is_dir() and not any(dst.parent.iterdir()):
            try:
                dst.parent.rmdir()  # empty dir created by THIS attempt only
            except OSError:
                pass
        return {
            "ok": False,
            "status": "refused",
            "error": (
                f"rename blocked by file permissions ({type(exc).__name__}). "
                "这个旧 zip 未继承当前服务进程的文件授权；请以完全权限运行 "
                "jmlib serve（或重新授权该归档文件）后重试。"
                "未移动任何文件、未写库、未记录治理动作。"
            ),
            "confirm_token": token,
        }
    if not dst.is_file() or src.exists():
        return {
            "ok": False,
            "status": "refused",
            "error": "rename state inconsistent; refusing to touch the catalog",
            "confirm_token": token,
        }

    info = cover.inspect_chapter(dst, fresh["aid"], fresh["pindex"])
    chapter = {
        "aid": fresh["aid"],
        "pindex": fresh["pindex"],
        "zip_path": fresh["to"],
        "page_count": info["page_count"],
        "cover_path": info["cover_path"],
    }
    book = {
        "aid": fresh["aid"],
        "title": fresh.get("title", ""),
        "author": "",
        "tags": "[]",
        "generation": "governance",
        "cover_path": info["cover_path"],
        "created_at": _utc(),
    }
    created = store.add_chapter(book, chapter)
    store.delete_unresolved(fresh["from"])

    meta_added = False
    if created and fresh.get("title"):
        ingest.put_meta(
            fresh["aid"],
            {
                "title": fresh["title"],
                "author": "",
                "tags": [],
                "generation": "governance",
                "source": "governance",
                "updated_at": _utc(),
            },
        )
        meta_added = True

    ledger = _load_ledger()
    action_id = "g" + str(len(ledger.get("actions", [])) + 1)
    action = {
        "id": action_id,
        "type": "promote",
        "status": "executed",
        "from": fresh["from"],
        "to": fresh["to"],
        "aid": fresh["aid"],
        "pindex": fresh["pindex"],
        "created_book": bool(created),
        "meta_added": meta_added,
        "unresolved_row": fresh["unresolved_row"],
        "cover_path": info["cover_path"],
        "executed_at": _utc(),
    }
    ledger.setdefault("actions", []).append(action)
    _save_ledger(ledger)
    return {
        "ok": True,
        "status": "executed",
        "action": action,
        "message": f"{action_id}: promoted {fresh['aid']}/{fresh['pindex']} ({info['page_count']} pages); reversible",
    }


def promote_rollback(action_id: str) -> dict:
    """Reverse one executed promote exactly: rename back + drop added rows."""
    ledger = _load_ledger()
    action = next((a for a in ledger.get("actions", []) if a["id"] == action_id), None)
    if action is None:
        return {"ok": False, "error": f"unknown action {action_id}"}
    if action["status"] != "executed":
        return {"ok": False, "error": f"action {action_id} is {action['status']}, not executed"}
    dst = _archive_target(action["to"])
    src, src_err = _unresolved_source(action["from"])
    if dst is None or src is None:
        return {"ok": False, "error": f"recorded paths are not reversible: {src_err or 'invalid target'}"}
    if not dst.is_file():
        return {"ok": False, "error": f"target zip missing, cannot rollback: {action['to']}"}
    if src.exists():
        return {"ok": False, "error": f"source path already occupied, refusing to overwrite: {action['from']}"}

    made_parent = not src.parent.exists()
    try:
        src.parent.mkdir(parents=True, exist_ok=True)
        os.rename(dst, src)  # exact reverse, zero deletion
    except OSError as exc:
        if made_parent and src.parent.is_dir() and not any(src.parent.iterdir()):
            try:
                src.parent.rmdir()  # empty dir created by THIS attempt only
            except OSError:
                pass
        return {
            "ok": False,
            "status": "refused",
            "error": (
                f"rename back blocked by file permissions ({type(exc).__name__}). "
                "请以完全权限运行 jmlib serve（或重新授权该归档文件）后重试。"
                "目标 zip 仍在原处；未改库、未改动作状态。"
            ),
            "action": action,
        }
    if not src.is_file() or dst.exists():
        return {
            "ok": False,
            "status": "refused",
            "error": "rename state inconsistent after rollback; refusing to touch the catalog",
            "action": action,
        }
    if action["created_book"]:
        store.remove_book(action["aid"])
    else:
        store.remove_chapter(action["aid"], action["pindex"])
    store.insert_unresolved(action["unresolved_row"])
    if action["meta_added"]:
        ingest.drop_meta(action["aid"])
    # Derived cover cache is intentionally left in place: it is rebuildable and
    # a rollback must not perform any deletion.

    action["status"] = "rolled_back"
    action["rolled_back_at"] = _utc()
    _save_ledger(ledger)
    return {
        "ok": True,
        "status": "rolled_back",
        "action": action,
        "message": f"{action_id}: rolled back; source zip restored, added rows removed",
    }


def status() -> dict:
    """Read-only governance overview for the API/UI."""
    acts = actions()
    return {
        "schemaVersion": SCHEMA,
        "read_only": True,
        "note": (
            "P5 受控治理：可逆转正需输入精确确认串；破坏性类别仅有只读 "
            "Data Destruction Guard，无任何执行路径，等待用户逐项 scoped confirmation。"
        ),
        "actions": acts,
        "executed": sum(1 for a in acts if a["status"] == "executed"),
        "rolled_back": sum(1 for a in acts if a["status"] == "rolled_back"),
        "destructive": [destruction_guard(k) for k in DESTRUCTIVE_KINDS],
    }
