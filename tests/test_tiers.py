"""The boundary between the unit suite and tier 3, asserted from inside the unit suite.

F3 README decision 2 splits bessemer's tests into three tiers and rules where the third one
lives: **outside the guarded suite, never as an exemption inside the guard.** `tests/guard.py`
denies `docker` to everything `make check` reaches, and the tests that need a real daemon are
kept out of its reach rather than let through it — an exemption would weaken the guard for the
unit suite too, which is the suite CI actually runs.

That ruling is a property of the *file layout* and of `make check`'s invocation, so nothing in
`tests/guard.py` or in `tests/integration/` can hold it. This module holds it, and it is in the
unit suite deliberately: the failure it exists to catch — a tier-3 file becoming collectable by
`make check` — makes CI green-when-lucky, so the test that sees it must be one CI runs.

**`tests/test_guard.py::…::test_docker_is_denied` is the other half** and stays where it is. It
proves the guard still denies docker to a unit test; this module proves the tier-3 files are not
unit tests.
"""

import itertools
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parent.parent

TIER_THREE_DIR: Final = REPO_ROOT / "tests" / "integration"

TIER_THREE_MODULES: Final = frozenset(
    {
        "test_image",
        "test_sudoers",
        "test_setup_hook",
        "test_dispatch_e2e",
    }
)
"""Tier 3's contents, restated by hand — the list F3 README decision 2 owns.

Decision 2 names what tier 3 is for, and each name below is one clause of it: `test_image` the
`AGENT_UID=0` build refusal F1 issue 07 could only document, `test_sudoers` the exact-match test
pinning both facts ADR 0001 measured, `test_setup_hook` the adapter hook issue 12 had to make
real, `test_dispatch_e2e` the one scripted end-to-end failure path.

Written out rather than read off the directory, for the reason every list in this repository is:
an assertion that globbed the directory would agree with whatever the directory happens to hold,
including with a tier-3 test somebody deleted. Adding one is a second file's edit, on purpose.
"""

MAKEFILE: Final = REPO_ROOT / "Makefile"

TRACER_TARGET: Final = "tracer-tests:"
"""The target name, as `make tracer-tests` spells it."""


def _ids(suite: unittest.TestSuite) -> Iterator[str]:
    """Every test id in a discovered suite, however deeply the loader nested it.

    Recursive rather than one level deep: `discover` returns a suite of suites of suites, and a
    single level would see the packages and none of the tests, which is an empty answer wearing
    a passing assertion's clothes.
    """
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _ids(item)
        else:
            yield item.id()


class TierThreeLayoutTest(unittest.TestCase):
    """Where tier 3 lives, and why `unittest discover` cannot reach it from the root."""

    def test_the_tier_three_directory_exists(self) -> None:
        self.assertTrue(TIER_THREE_DIR.is_dir(), TIER_THREE_DIR)

    def test_it_holds_exactly_the_modules_the_decision_names(self) -> None:
        found = {path.stem for path in TIER_THREE_DIR.glob("test_*.py")}
        self.assertEqual(found, set(TIER_THREE_MODULES))

    def test_it_is_not_a_package(self) -> None:
        """The whole mechanism, in one file's absence.

        `unittest discover` recurses into a directory only when it is an importable package, so
        a missing `__init__.py` is what keeps `make check` from collecting these modules — and
        it is also what keeps them from importing `tests/__init__.py`, which arms the guard that
        would then deny them docker.

        Measured before this test was written, on a scratch tree of the same shape: with
        `tests/integration/__init__.py` present, a bare `python -m unittest discover` collects
        the file inside it; without it, the same command collects only `tests.*`.
        """
        self.assertFalse((TIER_THREE_DIR / "__init__.py").exists())

    def test_root_discovery_collects_nothing_from_it(self) -> None:
        """The property itself, asked of the loader rather than inferred from the layout.

        `make check` runs `unittest discover` with no arguments from the repository root. This
        runs the same discovery and asserts every test it found belongs to the `tests` package —
        which is exactly "no tier-3 module was collected", stated in a way that also catches a
        tier-3 test moved somewhere new.
        """
        found = unittest.defaultTestLoader.discover(str(REPO_ROOT))
        modules = {identifier.split(".")[0] for identifier in _ids(found)}
        self.assertEqual(modules, {"tests"})


class TracerTargetTest(unittest.TestCase):
    """`make tracer-tests` exists, and `make check` does not run it."""

    def setUp(self) -> None:
        self.makefile = MAKEFILE.read_text(encoding="utf-8")

    def test_the_target_exists(self) -> None:
        self.assertIn(TRACER_TARGET, self.makefile)

    def test_the_check_recipe_does_not_reach_the_tier_three_directory(self) -> None:
        """`check`'s recipe lines, and none of them naming tier 3.

        The recipe rather than the file: the Makefile mentions `tests/integration` several
        times — the variable, and the comments explaining the split — and a whole-file search
        would go green on a `check` that ran it.
        """
        lines = self.makefile.splitlines()
        after = lines[lines.index("check:") + 1 :]
        recipe = "\n".join(itertools.takewhile(lambda line: line.startswith("\t"), after))
        self.assertTrue(recipe.strip(), "the check target's recipe could not be located")
        self.assertNotIn("integration", recipe)
        self.assertNotIn("TRACER", recipe)
