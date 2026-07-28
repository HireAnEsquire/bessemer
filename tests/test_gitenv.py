"""Tests for the fixture git environment, including the write it exists to prevent.

The behavioural half is the point: `EscapeTest` runs a real `git init` under a poisoned
ambient environment and asserts the repository lands where the test asked for it. Without that,
every assertion here is about the contents of a dictionary, and a dictionary cannot notice that
`git init` writes somewhere else entirely.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Final
from unittest import mock

from bessemer import resolve
from tests.gitenv import FORCED, GIT_PREFIX, fixture_env

TIMEOUT: Final = 60


class FamilyTest(unittest.TestCase):
    """The prefix rule against the module's nine names, so the two policies cannot drift."""

    def test_every_variable_the_resolvers_strip_is_covered_by_the_prefix(self) -> None:
        """`tests/gitenv.py` drops all of `GIT_*`; `bessemer/resolve.py` strips nine names by
        hand. This is what makes the first a superset of the second without copying the list —
        a tenth name added to `resolve` is covered here the moment it is written, and a name
        added there that did *not* begin with `GIT_` would fail here rather than quietly fall
        outside the fixture rule."""
        for name in sorted(resolve.REDIRECTING_VARIABLES):
            with self.subTest(variable=name):
                self.assertTrue(name.startswith(GIT_PREFIX), name)

    def test_the_stripped_set_is_not_empty(self) -> None:
        """The loop above passes over an empty set. This is what stops that."""
        self.assertTrue(resolve.REDIRECTING_VARIABLES)


class FixtureEnvTest(unittest.TestCase):
    def test_no_ambient_git_variable_survives(self) -> None:
        """Including one this project has never heard of: the rule is the prefix, so git
        growing a tenth repository-locating variable needs no edit anywhere."""
        poison = {
            **{name: "/somewhere/else" for name in resolve.REDIRECTING_VARIABLES},
            "GIT_SOME_FUTURE_VARIABLE": "1",
            "GIT_AUTHOR_NAME": "somebody else",
        }
        with mock.patch.dict(os.environ, poison):
            built = fixture_env()
        for name in sorted(poison):
            with self.subTest(variable=name):
                self.assertNotEqual(built.get(name), poison[name])

    def test_the_forced_values_are_what_a_fixture_gets(self) -> None:
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": "/home/dev/.gitconfig"}):
            built = fixture_env()
        self.assertEqual({name: built[name] for name in FORCED}, dict(FORCED))
        self.assertEqual(built["GIT_CONFIG_GLOBAL"], os.devnull)

    def test_non_git_variables_are_inherited(self) -> None:
        """A fixture that replaced the whole environment would be asserting about a machine
        nobody has — and would lose `PATH`, which is how git is found."""
        with mock.patch.dict(os.environ, {"BESSEMER_TEST_MARKER": "kept"}):
            built = fixture_env()
        self.assertEqual(built["BESSEMER_TEST_MARKER"], "kept")
        self.assertEqual(built.get("PATH"), os.environ.get("PATH"))

    def test_an_override_wins_over_both(self) -> None:
        built = fixture_env(HOME="/tmp/elsewhere", GIT_TERMINAL_PROMPT="1")
        self.assertEqual(built["HOME"], "/tmp/elsewhere")
        self.assertEqual(built["GIT_TERMINAL_PROMPT"], "1")


class EscapeTest(unittest.TestCase):
    """`git init` under a poisoned environment, in both directions.

    The decoy is a temporary repository, never this one. That matters: the inherited-environment
    half of this test *does* re-initialise whatever `GIT_DIR` names, which is the whole finding,
    and a test that demonstrated it against the developer's own repository would be committing
    the defect in order to prove it.
    """

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name).resolve()
        self.decoy = self.tmp / "decoy"
        subprocess.run(
            ["git", "init", "-q", "-b", "main", str(self.decoy)],
            env=fixture_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            timeout=TIMEOUT,
        )

    def init(self, target: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "init", "-q", "-b", "main", str(target)],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=TIMEOUT,
        )

    def poisoned(self, **only: str) -> dict[str, str]:
        """An environment holding exactly the `GIT_*` variables named, and no others.

        Built from `fixture_env()` rather than from `os.environ`, because a poison has to be
        the *only* one present for either test below to mean what its name says. Measured, on
        this very file: with an ambient `GIT_DIR` exported,
        `{**os.environ, "GIT_WORK_TREE": …}` is not `GIT_WORK_TREE` alone, `git init` succeeds,
        and the test asserting it fails loudly went green while asserting nothing. Same rule as
        the ambient-stdin case in `tests/README.md`: never assert about an environment the host
        contributed to.
        """
        return fixture_env(**only)

    def test_git_dir_alone_writes_to_the_wrong_repository_and_exits_zero(self) -> None:
        """The measured defect, pinned so the fix cannot be undone without this going red.

        `git init` does not create the repository it was handed; it re-initialises the one
        `GIT_DIR` names, warns, and **exits 0**. That is why an all-variables-exported run was
        green: it was silently re-initialising the developer's own `.git` three times a suite.
        """
        target = self.tmp / "wanted-here"
        result = self.init(target, self.poisoned(GIT_DIR=str(self.decoy / ".git")))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("re-init", result.stderr)
        # The directory is created; the *repository* is not. Measured, and worth being exact
        # about: `target.exists()` is true, which is how a fixture asserting only that its
        # directory was made would sail past this — every later git call in it then falls
        # through to whatever `GIT_DIR` names.
        self.assertTrue(target.is_dir())
        self.assertFalse(
            (target / ".git").exists(), "the repository was created where it was asked for"
        )

    def test_git_work_tree_alone_fails_loudly_instead(self) -> None:
        """The other half of the masking. A different variable, a different failure — loud
        rather than silent — so setting both hides the silent one behind the one that works.

        `git init` refuses `GIT_WORK_TREE` without a `GIT_DIR` outright, which is the exit 128
        that reddened issue 07's fixtures and the reason the silent case above went unnoticed.
        """
        target = self.tmp / "wanted-here"
        result = self.init(target, self.poisoned(GIT_WORK_TREE=str(self.decoy)))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("GIT_WORK_TREE", result.stderr)

    def test_the_pair_writes_a_worktree_key_into_the_victims_config(self) -> None:
        """The durable half, and the incident this whole module came out of.

        `GIT_DIR` alone re-initialises the victim and writes nothing to its config.
        `GIT_DIR` **plus** a `GIT_WORK_TREE` naming somewhere else makes the re-init record
        that somewhere as `core.worktree` — and the victim keeps working until that path is
        deleted, after which *every* git command in it fails with `fatal: Invalid path` and
        `git config --unset core.worktree` cannot fix it from inside, because git validates
        `core.worktree` before it will act. During issue 05 the path was a test's temporary
        directory, so the breakage arrived when `TemporaryDirectory` cleaned up.

        Measured here rather than argued, against a victim this test created.
        """
        victim = self.tmp / "victim"
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        self.init(victim, fixture_env())
        config = victim / ".git" / "config"
        self.assertNotIn("worktree", config.read_text(encoding="utf-8"))

        alone = self.init(self.tmp / "a", self.poisoned(GIT_DIR=str(config.parent)))
        self.assertEqual(alone.returncode, 0)
        self.assertNotIn("worktree", config.read_text(encoding="utf-8"))

        pair = self.init(
            self.tmp / "b",
            self.poisoned(GIT_DIR=str(config.parent), GIT_WORK_TREE=str(elsewhere)),
        )
        self.assertEqual(pair.returncode, 0, pair.stderr)
        self.assertIn(f"worktree = {elsewhere}", config.read_text(encoding="utf-8"))

    def test_the_two_poisons_fail_in_different_directions(self) -> None:
        """The masking, as one assertion rather than as an observation in two docstrings: a
        silent success and a loud failure from the same fixture command, which is why "the
        variables are exported" is not one condition."""
        silent = self.init(self.tmp / "a", self.poisoned(GIT_DIR=str(self.decoy / ".git")))
        loud = self.init(self.tmp / "b", self.poisoned(GIT_WORK_TREE=str(self.decoy)))
        self.assertEqual(silent.returncode, 0)
        self.assertNotEqual(loud.returncode, 0)
        # And together: the loud one disappears, which is the trap.
        both = self.init(
            self.tmp / "c",
            self.poisoned(GIT_DIR=str(self.decoy / ".git"), GIT_WORK_TREE=str(self.decoy)),
        )
        self.assertEqual(both.returncode, 0)
        self.assertFalse((self.tmp / "c" / ".git").exists())

    def test_the_fixture_environment_creates_the_repository_that_was_asked_for(self) -> None:
        """Both poisons at once, which is the combination that looked benign."""
        target = self.tmp / "wanted-here"
        result = self.init(
            target,
            fixture_env(),
        )
        with mock.patch.dict(
            os.environ,
            {"GIT_DIR": str(self.decoy / ".git"), "GIT_WORK_TREE": str(self.decoy)},
        ):
            also = self.init(self.tmp / "wanted-too", fixture_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(also.returncode, 0, also.stderr)
        self.assertTrue((target / ".git").is_dir())
        self.assertTrue((self.tmp / "wanted-too" / ".git").is_dir())


if __name__ == "__main__":
    unittest.main()
