"""Containment contract for the import-root whitelist.

``paths.resolve_inside_roots`` is the single gate between "text the caller sent"
and "a directory the server reads".  The B1 batch already covers the HTTP
surface (403 on traversal, 400 on an empty path, ``not a directory`` for a
missing one); this module pins the gate itself, so the rule is enforced by the
repository's own test run and not only by the scratch harness.

Offline and self-contained: the whitelist comes from ``JMLIB_IMPORT_ROOTS``,
which the test sets to a scratch tree, and every path below the scratch root is
created and removed here.
"""

from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path

from jmlib import paths

SCRATCH = paths.ROOT / ".tmp" / "contracts" / "containment"
ALLOWED = SCRATCH / "allowed"
OUTSIDE = SCRATCH / "outside"


class ContainmentContract(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(SCRATCH, ignore_errors=True)
        (ALLOWED / "nested").mkdir(parents=True)
        OUTSIDE.mkdir(parents=True)
        self._previous = os.environ.get("JMLIB_IMPORT_ROOTS")
        os.environ["JMLIB_IMPORT_ROOTS"] = str(ALLOWED)

    def tearDown(self) -> None:
        if self._previous is None:
            os.environ.pop("JMLIB_IMPORT_ROOTS", None)
        else:
            os.environ["JMLIB_IMPORT_ROOTS"] = self._previous
        shutil.rmtree(SCRATCH, ignore_errors=True)

    def test_the_root_itself_is_inside(self) -> None:
        self.assertEqual(paths.resolve_inside_roots(str(ALLOWED)), ALLOWED.resolve())

    def test_a_child_directory_is_inside(self) -> None:
        got = paths.resolve_inside_roots(str(ALLOWED / "nested"))
        self.assertEqual(got, (ALLOWED / "nested").resolve())

    def test_a_sibling_with_a_shared_name_prefix_is_outside(self) -> None:
        # ``/allowed-elsewhere`` must not pass for a child of ``/allowed``: the
        # guard compares ``base + os.sep``, not a bare prefix.
        sibling = SCRATCH / "allowed-elsewhere"
        sibling.mkdir()
        self.assertIsNone(paths.resolve_inside_roots(str(sibling)))

    def test_traversal_out_of_the_root_is_refused(self) -> None:
        self.assertIsNone(paths.resolve_inside_roots(str(ALLOWED / ".." / "outside")))

    def test_an_absolute_path_elsewhere_is_refused(self) -> None:
        self.assertIsNone(paths.resolve_inside_roots(str(OUTSIDE)))

    def test_an_empty_path_is_refused(self) -> None:
        self.assertIsNone(paths.resolve_inside_roots(""))
        self.assertIsNone(paths.resolve_inside_roots("   "))

    def test_normalisation_touches_no_disk(self) -> None:
        # ``normalize_untrusted`` is pure string maths: a path that does not
        # exist still normalises, which is what lets the containment test run
        # before any filesystem access.
        ghost = paths.normalize_untrusted(str(ALLOWED / "nope" / ".." / "ghost"))
        self.assertEqual(ghost, (ALLOWED / "ghost").resolve())
        self.assertFalse(ghost.exists())

    def test_the_drop_folder_is_always_allowed(self) -> None:
        os.environ.pop("JMLIB_IMPORT_ROOTS", None)
        self.assertIn(paths.INCOMING.resolve(), paths.allowed_import_roots())

    def test_contained_in_matches_the_resolver(self) -> None:
        roots = paths.allowed_import_roots()
        inside = paths.normalize_untrusted(str(ALLOWED / "nested"))
        outside = paths.normalize_untrusted(str(OUTSIDE))
        self.assertTrue(paths.contained_in(inside, roots))
        self.assertFalse(paths.contained_in(outside, roots))
        self.assertFalse(paths.contained_in(str(ALLOWED) + "-elsewhere", roots))


class NormaliseContract(unittest.TestCase):
    def test_relative_input_becomes_absolute(self) -> None:
        got = paths.normalize_untrusted("some/relative/dir")
        self.assertTrue(got.is_absolute())
        self.assertEqual(got, Path(os.path.abspath("some/relative/dir")))

    def test_redundant_segments_are_collapsed(self) -> None:
        got = paths.normalize_untrusted(str(Path(os.path.abspath("a")) / "." / "b" / ".." / "c"))
        self.assertEqual(got, Path(os.path.abspath("a")) / "c")


if __name__ == "__main__":
    unittest.main(verbosity=2)
