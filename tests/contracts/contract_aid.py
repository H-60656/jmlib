"""Contract test for :mod:`jmlib.aid` — one definition of an Aid, two questions.

Checks, with no network and no real library:

  * identity accepts real short ids and rejects leading zeros / 8-digit / junk;
  * ``AID_RE`` is anchored, because some callers use ``match`` not ``fullmatch``;
  * free text only yields *hints*, and a bare 3-digit number is not one;
  * ``{Aid}_cover.jpg`` round-trips for short ids too;
  * every module that reads an Aid out of a known slot shares the *same* rule
    object — a local re-definition silently re-narrows it (this test exists
    because ``indexer.py`` did exactly that once).

Run:  $env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH='src'; python .tmp/test_aid.py
"""

# Moved verbatim from the ``.tmp`` exploration scripts so CI runs it.
# Runs as its own process: every contract redirects ``jmlib.paths`` onto a
# scratch tree, and those redirects must not leak between suites.
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jmlib import aid  # noqa: E402

FAILED: list[str] = []


def check(name: str, got, want) -> None:
    if got != want:
        FAILED.append(f"{name}: got {got!r}, want {want!r}")


# ---------------------------------------------------------------- identity
for value in ("121", "579", "1", "187", "1234", "1234567"):
    check(f"is_aid({value!r})", aid.is_aid(value), True)

for value in ("", "0", "0121", "12345678", "12a", " 121", "121 ", "-121", "1.2"):
    check(f"is_aid({value!r})", aid.is_aid(value), False)

check("is_aid(None)", aid.is_aid(None), False)
check("is_aid(121)", aid.is_aid(121), False)

# anchored: a whole component only, never a prefix of a longer string
check("match('121x')", aid.AID_RE.match("121x") is None, True)
check("match('121')", aid.AID_RE.match("121") is not None, True)
check("fullmatch('121/1')", aid.AID_RE.fullmatch("121/1") is None, True)

# ---------------------------------------------------------------- hints
check("hints('[JYUN] 121 姊乳')", aid.hints("[JYUN] 121 姊乳"), [])
check("hints('574811 姉乳')", aid.hints("574811 姉乳"), ["574811"])
check("hints(two ids)", aid.hints("1234500 1234600两个车号"), ["1234500", "1234600"])
# a hint is deliberately dumb evidence; ``import_scan.MAGAZINE_RE`` is what
# discounts a year/issue number once the number has been *found*.
check("hints(magazine)", aid.hints("(COMIC LO 2017年7月号)"), ["2017"])
check("hints(8 digits)", aid.hints("12345678"), [])
check("hints('')", aid.hints(""), [])

# ---------------------------------------------------------------- covers
check("cover_aid('187_cover.jpg')", aid.cover_aid("187_cover.jpg"), "187")
check("cover_aid('121_cover.JPG')", aid.cover_aid("121_cover.JPG"), "121")
check("cover_aid('12_cover.png')", aid.cover_aid("12_cover.png"), "12")
check("cover_aid('0_cover.jpg')", aid.cover_aid("0_cover.jpg"), None)
check("cover_aid('cover.jpg')", aid.cover_aid("cover.jpg"), None)
check("cover_aid('187_cover')", aid.cover_aid("187_cover"), None)

# ------------------------------------------------- one rule, shared by all
# modules whose import needs a third-party package are only checked when present
SHARED = (
    "jmlib.api",
    "jmlib.api_import",
    "jmlib.api_prefs",
    "jmlib.api_resolve",
    "jmlib.governance",
    "jmlib.health",
    "jmlib.indexer",
    "jmlib.import_scan",
    "jmlib.ingest",
    "jmlib.migrate",
    "jmlib.resolve",
)
FLOOR4 = {"jmlib.migrate": "LEGACY_AID_RE"}  # legacy *source* layouts only
# modules that own no rule of their own: they read ``import_scan.AID_RE``
NO_LOCAL = {"jmlib.api_import", "jmlib.import_apply", "jmlib.resolve"}
CHECKED, SKIPPED = [], []

for name in SHARED:
    try:
        module = importlib.import_module(name)
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        SKIPPED.append(f"{name} ({exc.name})")
        continue
    CHECKED.append(name)
    if name in FLOOR4:
        check(f"{name}.AID_RE gone", hasattr(module, "AID_RE"), False)
        check(f"{name}.{FLOOR4[name]}.pattern", module.LEGACY_AID_RE.pattern, r"^\d{4,7}$")
        continue
    owned = getattr(module, "AID_RE", None)
    if name in NO_LOCAL:
        check(f"{name} owns no AID_RE", owned, None)
        continue
    check(f"{name}.AID_RE is aid.AID_RE", owned is aid.AID_RE, True)

# the two modules that *guess* from free text must share the weaker rule
for name, attr in (("jmlib.import_scan", "NAME_DIGITS_RE"), ("jmlib.resolve", "AID_TOKEN_RE")):
    module = sys.modules.get(name)
    if module is None:
        SKIPPED.append(f"{name}.{attr}")
        continue
    check(f"{name}.{attr} is aid.HINT_RE", getattr(module, attr, None) is aid.HINT_RE, True)

# ---------------------------------------------------------------- report
print(f"checked {len(CHECKED)} modules; skipped {len(SKIPPED)}: {', '.join(SKIPPED) or 'none'}")
if FAILED:
    print(f"FAILED {len(FAILED)}:")
    for line in FAILED:
        print("  -", line)
    sys.exit(1)
print("aid contract OK")
