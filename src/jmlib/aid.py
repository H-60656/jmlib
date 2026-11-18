"""The one definition of what an Aid is — and where a number is only a hint.

JM album ids are plain positive integers without a leading zero, and the earliest
ones are short: ``import_scan`` has always known that (its ``COVER_LIKE_RE``
comment cites ``187_cover.jpg``).  Requiring four digits for the *identity*
contract therefore rejected real albums, and that is exactly how two correctly
resolved packages were refused at promote time — Aid 121 and Aid 579 are real
albums whose upstream titles match their directories byte for byte.

One regex was answering two different questions, so this module keeps them apart:

* **is this string an Aid?** — a whole path component (``archive/{Aid}/``), a
  route parameter, an ``{Aid}_cover`` prefix.  The value already sits in an Aid
  slot, so a short id must be accepted: 1–7 digits, no leading zero.
* **does this free text contain an Aid?** — a folder name typed by a person.  A
  bare number there is weak evidence: a volume, an issue or a year looks the
  same, so the floor stays at four digits.  :data:`HINT_RE` is that weaker rule.

Anything that reads an Aid out of a *known* slot must use :data:`AID_RE`; anything
that *guesses* from free text must use :data:`HINT_RE`.  Keeping the two apart is
what lets a three-digit album be read after it is promoted, while a folder called
``121 姊乳榨精的性爱`` still does not silently claim to be album 121.
"""

from __future__ import annotations

import re

#: Shortest and longest Aid we accept as an identity.  Seven digits covers every
#: album upstream has ever issued; the floor is one because ids started small.
MIN_DIGITS = 1
MAX_DIGITS = 7

#: Identity: a whole Aid component.  Anchored because callers use ``match`` too.
AID_RE = re.compile(rf"^[1-9][0-9]{{0,{MAX_DIGITS - 1}}}$")

#: Weak evidence out of free text.  Deliberately stricter than :data:`AID_RE`.
HINT_MIN_DIGITS = 4
HINT_RE = re.compile(rf"(?<![0-9])[0-9]{{{HINT_MIN_DIGITS},{MAX_DIGITS}}}(?![0-9])")

#: ``{Aid}_cover.jpg`` — the P4 loose convention, also used by the user's own scripts.
COVER_FILE_RE = re.compile(rf"^([1-9][0-9]{{0,{MAX_DIGITS - 1}}})_cover\.\w+$", re.I)


def is_aid(value: object) -> bool:
    """True when ``value`` is a syntactically valid Aid."""
    return isinstance(value, str) and AID_RE.fullmatch(value) is not None


def cover_aid(filename: str) -> str | None:
    """The Aid a legacy ``{Aid}_cover.jpg`` file claims, else ``None``."""
    match = COVER_FILE_RE.match(str(filename or ""))
    return match.group(1) if match else None


def hints(text: str) -> list[str]:
    """Every Aid-shaped number inside free text, in order (weak evidence)."""
    return HINT_RE.findall(str(text or ""))
