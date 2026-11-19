"""Run every ``tests/contracts/contract_*.py`` script as its own process.

Why subprocesses and not imports: each contract redirects ``jmlib.paths`` onto its
own scratch tree at module import time and wipes that tree before it starts.  Two
contracts sharing one interpreter would leak one suite's redirect into the next,
and a wipe would then delete the previous suite's fixtures mid-run.  A separate
process per contract also means a hard crash (``SystemExit``, a segfault in a C
extension) cannot take the rest of the run down with it.

Each script prints its own verdict line; this wrapper only checks the exit code
and surfaces the last lines of output when something fails.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

CONTRACTS = Path(__file__).resolve().parent / "contracts"
ROOT = CONTRACTS.parents[1]
TIMEOUT = 300


def _scripts() -> list[Path]:
    return sorted(CONTRACTS.glob("contract_*.py"))


def _run(script: Path) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TIMEOUT,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


class ContractSuites(unittest.TestCase):
    def test_suites_are_discovered(self) -> None:
        found = _scripts()
        self.assertGreaterEqual(
            len(found),
            20,
            f"expected the migrated contract suites under {CONTRACTS}, found {len(found)}",
        )

    def test_every_contract_passes(self) -> None:
        failures: list[str] = []
        for script in _scripts():
            with self.subTest(contract=script.stem):
                code, output = _run(script)
                if code != 0:
                    tail = "\n".join(output.strip().splitlines()[-25:])
                    failures.append(f"{script.name} exited {code}\n{tail}")
        if failures:
            self.fail("contract suites failed:\n\n" + "\n\n".join(failures))


if __name__ == "__main__":
    unittest.main(verbosity=2)
