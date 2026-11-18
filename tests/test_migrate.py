"""P0 migration contract checks (no real archive, no network, no writes outside workspace).

Three ways to run the same ``check_*`` functions:

* ``python tests/test_migrate.py``              — standalone, prints PASS/FAIL
* ``python -m pytest -q``                      — collects the ``MigrateContract`` class
* ``python -m unittest discover -s tests -v``  — same class, no pytest needed

Scratch dirs are created with default permissions under ``jmlib/.tmp/contracts``
rather than pytest's ``tmp_path`` / ``tempfile.mkdtemp``: in the DSH Windows
sandbox a ``0o700`` directory cannot be enumerated by the confined process, so
the fixture-based version only works on unrestricted machines.
"""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from jmlib import migrate

ROOT = Path(__file__).resolve().parents[1]


def _zip(root, rel: str, content: bytes = b"x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)


def check_classify_accepts_only_whole_aid(root: Path) -> None:
    src = root / "legacy"
    _zip(src, "JM(1)/123456/1.zip")
    _zip(src, "JM(1)/123456/2.zip")
    _zip(src, "JM(1)/789012.zip")
    _zip(src, "JM(1)(2)/001-[group] COMIC 失乐天 2022年6月号 [DL版].zip")
    (src / "_zip_signatures.json").write_text("{}", encoding="utf-8")

    m = migrate.build_manifest(src, None)
    assert m["summary"] == {
        "total": 4,
        "strict": 3,
        "unresolved": 1,
        "collisions": 0,
        "totalBytes": 4,
        "sidecars": 1,
        "looseBooks": 0,
    }
    by_old = {e["old"]: e for e in m["entries"]}
    assert by_old["JM(1)/123456/1.zip"]["new"] == "archive/123456/1.zip"
    assert by_old["JM(1)/789012.zip"]["new"] == "archive/789012/1.zip"
    u = by_old["JM(1)(2)/001-[group] COMIC 失乐天 2022年6月号 [DL版].zip"]
    assert u["kind"] == "unresolved" and u["reason"] == "no reliable Aid in path"
    assert u["new"].startswith("_unidentified/")
    assert m["sidecars"] == [{"old": "_zip_signatures.json", "new": "_zip_signatures.json"}]
    assert len({e["new"] for e in m["entries"]}) == 4
    assert json.loads(json.dumps(m, ensure_ascii=False))["schemaVersion"] == "jmlib.migration.v1"


def check_collision_is_diverted_not_overwritten(root: Path) -> None:
    src = root / "legacy"
    _zip(src, "JM(1)/123456/1.zip")
    _zip(src, "JM(2)/123456/1.zip")

    m = migrate.build_manifest(src, None)
    assert m["summary"]["collisions"] == 1
    assert m["summary"]["strict"] == 1
    diverted = [e for e in m["entries"] if e["kind"] == "unresolved"]
    assert len(diverted) == 1
    assert diverted[0]["reason"] == "target collision with JM(1)/123456/1.zip"
    assert diverted[0]["new"].startswith("_unidentified/JM(2)/123456/1.zip")
    assert len({e["new"] for e in m["entries"]}) == 2


def check_rollback_is_exact_reverse(root: Path) -> None:
    src = root / "legacy"
    _zip(src, "JM(1)/123456/1.zip")
    _zip(src, "JM(1)(2)/001-title.zip")
    m = migrate.build_manifest(src, None)
    rb = migrate.build_rollback(m)
    assert [(e["from"], e["to"]) for e in rb["entries"]] == [(e["new"], e["old"]) for e in m["entries"]]


def _scratch(name: str) -> Path:
    """Default-mode dir: enumerable by the confined process (0o700 is not)."""
    base = ROOT / ".tmp" / "contracts" / name
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)  # default mode, NOT 0o700
    return base


CHECKS = [
    ("classify_accepts_only_whole_aid", check_classify_accepts_only_whole_aid),
    ("collision_is_diverted_not_overwritten", check_collision_is_diverted_not_overwritten),
    ("rollback_is_exact_reverse", check_rollback_is_exact_reverse),
]


class MigrateContract(unittest.TestCase):
    """Thin unittest wrapper so ``unittest discover`` and pytest run the same checks."""

    def test_classify_accepts_only_whole_aid(self) -> None:
        check_classify_accepts_only_whole_aid(_scratch("classify"))

    def test_collision_is_diverted_not_overwritten(self) -> None:
        check_collision_is_diverted_not_overwritten(_scratch("collision"))

    def test_rollback_is_exact_reverse(self) -> None:
        check_rollback_is_exact_reverse(_scratch("rollback"))


def main() -> int:
    failed = 0
    for name, fn in CHECKS:
        try:
            fn(_scratch(name))
            print(f"PASS {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"contract checks: {len(CHECKS) - failed}/{len(CHECKS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
