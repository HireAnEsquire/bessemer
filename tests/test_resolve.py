"""Tests for the two resolvers: base auto-detection and config-root/git-root agreement.

**Real temporary git repositories, not mocks.** The failure modes under test are git's actual
behaviour — `origin/HEAD` unset, a detached HEAD, the inside of a `.git` directory, a
submodule — and a mock would encode this file's assumptions about git rather than git.
`tests/guard.py` allowlists `git` for exactly this. Nothing reaches the network: every remote
here is a local path, and `git submodule add` is given `protocol.file.allow=always` so it
works from one without one.

Three things are stubbed rather than provoked, each because no repository can produce them:

- `proc.run` raising `OSError` and `subprocess.TimeoutExpired`. Uninstalling git and wedging
  it are not things a test may do to the machine it runs on. `AbsorbedExceptionTest` makes up
  for that by provoking both out of the *real* `proc.run` and checking that what it raises is
  what `resolve` catches, so the enumeration is anchored to the wrapper rather than to these
  fixtures.
- git failing with a credential-bearing URL in `stderr`. Git only echoes a remote URL while
  contacting the remote, and the suite may not.
- the roots being in unrelated trees, and the working directory having been deleted.

No test changes the working directory: both resolvers take `start` for the same reason
`bessemer.config.load` does. A test that `chdir`s is a test that cannot run beside another.
"""

import ast
import os
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Final, assert_never, assert_type
from unittest import mock

from bessemer import config, proc, resolve
from bessemer.config import Config
from bessemer.outcome import Resolved, Unresolved
from tests.gitenv import fixture_env

TIMEOUT: Final = 60
"""For git in the fixtures. Generous: it is a backstop, not the thing under test."""

TOKEN: Final = "ghp_thisisnotarealtokenbutitlookslikeone"
CREDENTIAL_URL: Final = f"https://x-access-token:{TOKEN}@github.com/HireAnEsquire/bessemer.git"
CREDENTIAL_STDERR: Final = (
    f"fatal: unable to access '{CREDENTIAL_URL}/': Could not resolve host: github.com"
)
"""How git echoes a token: in a remote URL, in its own failure output.

Written as a literal because the suite cannot produce one — git prints the URL while
*contacting* the remote, and nothing here may reach the network.
"""

Answer = tuple[int, str, str]
"""A returncode, a stdout and a stderr for one stubbed git call."""

IN_WORK_TREE: Final[Answer] = (0, "true\n", "")
HAS_ORIGIN: Final[Answer] = (0, f"{resolve.ORIGIN}\n", "")
HEAD_IS_MAIN: Final[Answer] = (0, f"{resolve.ORIGIN}/main\n", "")


class SpawnRefused(BaseException):
    """Raised by the stand-in that proves a code path spawns nothing.

    A `BaseException`, so that an `except Exception` anywhere in the resolver could not turn
    a spawn into a passing test — the reasoning `tests.guard.GuardViolation` is built on.
    """


def resolve_source() -> str:
    """The text of `bessemer/resolve.py`, for the assertions that read the module itself.

    From `resolve.__file__` rather than a hard-coded path, so a moved module fails loudly
    here instead of leaving these assertions passing against a file that is no longer there.
    `tests/test_config.py` walks `bessemer/config.py` the same way.
    """
    origin = resolve.__file__
    assert origin is not None, "bessemer.resolve must be importable from a source tree"
    return Path(origin).read_text(encoding="utf-8")


def unresolved_sites() -> int:
    """How many times `bessemer/resolve.py` constructs an `Unresolved`.

    One per failure case, by construction: every case is built inside its own helper. That
    is what ties `FailureCaseTest`'s hand-written names to the module rather than to the
    fixtures that provoke them.
    """
    return sum(
        1
        for node in ast.walk(ast.parse(resolve_source()))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        if node.func.id == "Unresolved"
    )


def git_handlers() -> list[str]:
    """The exception types `_git` catches, as written, in source order.

    Read out of the module's AST rather than restated from behaviour, because that is the
    only thing that notices a third clause appearing — or one of these two disappearing,
    which would make a resolver raise on the path doctor needs most.
    """
    functions = [
        node
        for node in ast.walk(ast.parse(resolve_source()))
        if isinstance(node, ast.FunctionDef) and node.name == "_git"
    ]
    assert len(functions) == 1, f"expected one _git, found {len(functions)}"
    blocks = [node for node in ast.walk(functions[0]) if isinstance(node, ast.Try)]
    assert len(blocks) == 1, f"expected one try block in _git, found {len(blocks)}"
    return [
        "<bare except>" if handler.type is None else ast.unparse(handler.type)
        for handler in blocks[0].handlers
    ]


def spawn_calls() -> list[ast.Call]:
    """Every `proc.run(...)` call in `bessemer/resolve.py`."""
    return [
        node
        for node in ast.walk(ast.parse(resolve_source()))
        if isinstance(node, ast.Call)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "run"
        if isinstance(node.func.value, ast.Name) and node.func.value.id == "proc"
    ]


def fake_git(answers: Mapping[str, Answer | BaseException]) -> Callable[..., proc.Result]:
    """A stand-in for `proc.run` that answers each git command from `answers`.

    Keyed on what is being asked rather than on call order, so a test that changes the probe
    order in the resolver does not silently start asserting about a different command. An
    unlisted command gets the healthy answer, which is what keeps each stub to the one
    failure it is about.
    """
    defaults: Mapping[str, Answer] = {
        "is-inside-work-tree": IN_WORK_TREE,
        "remote": HAS_ORIGIN,
        "symbolic-ref": HEAD_IS_MAIN,
        "show-toplevel": (0, "/nowhere\n", ""),
    }

    def key_of(argv: Sequence[str]) -> str:
        for flag in ("--show-toplevel", "--is-inside-work-tree"):
            if flag in argv:
                return flag.removeprefix("--")
        return argv[1]

    def run(argv: Sequence[str], **kwargs: object) -> proc.Result:
        answer = answers.get(key_of(argv), defaults[key_of(argv)])
        if isinstance(answer, BaseException):
            raise answer
        returncode, stdout, stderr = answer
        return proc.Result(
            argv=tuple(argv), returncode=returncode, stdout=stdout, stderr=stderr
        )

    return run


def refuse_to_spawn(*args: object, **kwargs: object) -> proc.Result:
    """A stand-in for `proc.run` that proves the path under test never reaches git."""
    raise SpawnRefused(f"a resolver spawned something: {args!r} {kwargs!r}")


# Every stub below patches `run` on `bessemer.proc` itself, not on `resolve.proc`. Same
# object either way — `resolve` looks the attribute up on the module at call time — and
# `resolve` does not re-export the name, so patching it through there is what mypy refuses.


def config_at(root: Path, **layers: Mapping[str, object]) -> Config:
    """A `Config` rooted at `root`. Built directly, not loaded from disk.

    Discovery and the TOML layers are `tests/test_config.py`'s subject; here they would be
    two more moving parts between a fixture and the resolver under test. The one thing that
    matters about a `Config` here is `root`, and for `base` which layer supplied it.
    """
    empty: Mapping[str, object] = {}
    return Config(
        adapter_dir=root / config.ADAPTER_DIR,
        committed=layers.get("committed", empty),
        local=layers.get("local", empty),
        env=layers.get("env", empty),
        flags=layers.get("flags", empty),
    )


class RepoTest(unittest.TestCase):
    """Base for tests that need real temporary git repositories."""

    IDENTITY: Final = (
        "-c",
        "user.email=tests@bessemer.invalid",
        "-c",
        "user.name=bessemer tests",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "protocol.file.allow=always",
    )
    """Passed to every fixture command, so no fixture depends on the developer's git.

    `protocol.file.allow=always` is what lets `git submodule add` take a local path; git
    refuses `file://` submodules by default (CVE-2022-39253). It is set here, on fixture
    commands only, and never on anything the resolvers run.
    """

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        # Resolved, because git resolves and `find_adapter_dir` resolves: on macOS a
        # temporary directory is handed out as `/var/...`, a symlink to `/private/var/...`.
        self.tmp = Path(holder.name).resolve()
        # `tests/gitenv.py` rather than a dict built here: `tests/test_adapter.py` needs the
        # same thing, and two hand-built "safe git environments" in one test tree is the
        # drift that let issue 07's fixtures inherit `GIT_DIR`. `HOME` is overridden to the
        # temporary directory as well, so a fixture cannot read the developer's `~/.gitconfig`
        # even if the config variables above it ever change meaning.
        self.env = fixture_env(HOME=str(self.tmp))

    def git(self, *arguments: str, cwd: Path) -> proc.Result:
        """Run a fixture git command, failing the test if git itself failed."""
        result = proc.run(
            ["git", *self.IDENTITY, *arguments], cwd=cwd, timeout=TIMEOUT, env=self.env
        )
        self.assertTrue(
            result.ok, f"fixture `git {' '.join(arguments)}` failed: {result.stderr}"
        )
        return result

    def make_repo(self, path: Path, branch: str = "main") -> Path:
        """An empty git repository at `path`, with no remotes and no commits."""
        path.mkdir(parents=True, exist_ok=True)
        self.git("init", "--quiet", "--initial-branch", branch, cwd=path)
        return path

    def commit(self, repo: Path) -> None:
        self.git("commit", "--quiet", "--allow-empty", "--message", "fixture", cwd=repo)

    def add_origin(self, repo: Path, url: str | None = None) -> None:
        """Add an `origin` remote pointing at a local path. Nothing is fetched."""
        target = str(self.tmp / "origin.git") if url is None else url
        self.git("remote", "add", resolve.ORIGIN, target, cwd=repo)

    def set_origin_head(self, repo: Path, branch: str = "main") -> None:
        """Point `origin/HEAD` at a branch, the way `git remote set-head` would.

        Written directly rather than through `git remote set-head --auto`, which asks the
        remote and would need one to exist.
        """
        self.git(
            "symbolic-ref",
            resolve.ORIGIN_HEAD,
            f"refs/remotes/{resolve.ORIGIN}/{branch}",
            cwd=repo,
        )

    def unresolved(self, outcome: object) -> Unresolved:
        """Assert a resolver failed, and hand back the reason for further assertions.

        Takes `object` rather than the union: `Resolved` is an invariant generic, so
        `Resolved[str]` and `Resolved[Path]` do not both fit one annotation, and widening
        the parameter is better than writing this twice. The `isinstance` is the assertion
        either way. `NarrowingTest` is where the union's typing is what is under test.
        """
        self.assertIsInstance(outcome, Unresolved)
        assert isinstance(outcome, Unresolved)
        return outcome


class ConfiguredBaseTest(RepoTest):
    """A configured `base` wins, and is the one path that must not touch git."""

    def test_a_configured_base_is_used_and_no_git_runs(self) -> None:
        """The criterion, asserted by making any spawn fail loudly rather than by counting
        calls: `start` points at a directory that is not a repository at all, so a resolver
        that fell through to auto-detection would also have to spawn to find that out."""
        cfg = config_at(self.tmp, committed={"base": "release-1.2"})
        with mock.patch.object(proc, "run", refuse_to_spawn):
            self.assertEqual(resolve.resolve_base(cfg, start=self.tmp), Resolved("release-1.2"))

    def test_the_refusal_fixture_actually_refuses(self) -> None:
        """The control. Without it the test above passes against a patch that did nothing."""
        with mock.patch.object(proc, "run", refuse_to_spawn):
            with self.assertRaises(SpawnRefused):
                proc.run(["git", "--version"], timeout=TIMEOUT)

    def test_a_branch_name_containing_a_slash_is_accepted(self) -> None:
        cfg = config_at(self.tmp, committed={"base": "release/2026-07"})
        with mock.patch.object(proc, "run", refuse_to_spawn):
            self.assertEqual(
                resolve.resolve_base(cfg, start=self.tmp), Resolved("release/2026-07")
            )

    def test_a_base_that_is_not_a_string_is_its_own_reason(self) -> None:
        """`cfg.get("base")` is typed `object | None`: issue 04 does no coercion, so a
        `config.toml` saying `base = 5` reaches this module as an `int`."""
        for value in (5, ["main"], {"name": "main"}, 1.5, True):
            with self.subTest(value=value):
                cfg = config_at(self.tmp, committed={"base": value})
                with mock.patch.object(proc, "run", refuse_to_spawn):
                    failed = self.unresolved(resolve.resolve_base(cfg, start=self.tmp))
                self.assertIn(type(value).__name__, failed.reason)
                self.assertIn(config.COMMITTED, failed.reason)

    def test_a_base_that_is_unusable_text_is_a_different_reason(self) -> None:
        """ "Your base is not a branch name" and "you have no base" are different things to
        be told, and neither may fall through silently to auto-detection.

        `""` is the case a set-but-empty `BESSEMER_BASE` produces — issue 04 treats an empty
        environment variable as set, deliberately — and `-x` is the case where the value
        would be read as an option by whichever git command received it.
        """
        for value in ("", "   ", "main\n", "two words", "-x", "--upload-pack=hack"):
            with self.subTest(value=value):
                cfg = config_at(self.tmp, env={"base": value})
                with mock.patch.object(proc, "run", refuse_to_spawn):
                    failed = self.unresolved(resolve.resolve_base(cfg, start=self.tmp))
                self.assertIn("not usable as a branch name", failed.reason)
                self.assertIn(config.ENV, failed.reason)

    def test_the_two_unusable_cases_do_not_share_a_hint(self) -> None:
        """Quoting a number and removing whitespace are different edits."""
        with mock.patch.object(proc, "run", refuse_to_spawn):
            hints = {
                self.unresolved(
                    resolve.resolve_base(
                        config_at(self.tmp, committed={"base": value}), start=self.tmp
                    )
                ).hint
                for value in (5, "")
            }
        self.assertEqual(len(hints), 2)


class AutoDetectTest(RepoTest):
    """No configured base: the answer comes from `origin/HEAD`, or the reason says why not."""

    def test_origin_head_gives_the_base(self) -> None:
        repo = self.make_repo(self.tmp / "repo")
        self.add_origin(repo)
        self.set_origin_head(repo)
        outcome = resolve.resolve_base(config_at(repo), start=repo)
        self.assertEqual(outcome, Resolved("main"))

    def test_a_default_branch_containing_a_slash_survives_the_prefix_strip(self) -> None:
        """Only the `origin/` prefix comes off, never the last segment."""
        repo = self.make_repo(self.tmp / "repo")
        self.add_origin(repo)
        self.set_origin_head(repo, branch="feature/nested")
        self.assertEqual(
            resolve.resolve_base(config_at(repo), start=repo), Resolved("feature/nested")
        )

    def test_a_detached_head_does_not_affect_the_answer(self) -> None:
        """`origin/HEAD` is a remote-tracking ref and has nothing to do with local HEAD.
        Asserted because a resolver written against `symbolic-ref HEAD` would pass every
        other test here and fail during a bisect or a rebase."""
        repo = self.make_repo(self.tmp / "repo")
        self.commit(repo)
        self.git("checkout", "--quiet", "--detach", cwd=repo)
        self.add_origin(repo)
        self.set_origin_head(repo)
        self.assertEqual(resolve.resolve_base(config_at(repo), start=repo), Resolved("main"))

    def test_no_origin_remote_is_its_own_reason(self) -> None:
        repo = self.make_repo(self.tmp / "repo")
        failed = self.unresolved(resolve.resolve_base(config_at(repo), start=repo))
        self.assertIn(f"no remote named {resolve.ORIGIN}", failed.reason)
        self.assertIn(f"git remote add {resolve.ORIGIN}", failed.hint)

    def test_an_unset_origin_head_is_its_own_reason_with_the_command_that_fixes_it(
        self,
    ) -> None:
        """The common one. The hint is the whole value of this case."""
        repo = self.make_repo(self.tmp / "repo")
        self.add_origin(repo)
        failed = self.unresolved(resolve.resolve_base(config_at(repo), start=repo))
        self.assertIn(f"{resolve.ORIGIN}/HEAD is not set", failed.reason)
        self.assertEqual(failed.hint.count("git remote set-head origin --auto"), 1)

    def test_the_three_auto_detect_failures_are_told_apart(self) -> None:
        """Criterion 1, as one assertion: a resolver that reported "cannot read
        origin/HEAD" for all three would pass each test above that only checks a substring
        of its own case."""
        outside = self.tmp / "no-repo"
        outside.mkdir()
        no_origin = self.make_repo(self.tmp / "no-origin")
        unset = self.make_repo(self.tmp / "unset")
        self.add_origin(unset)

        failures = [
            self.unresolved(resolve.resolve_base(config_at(where), start=where))
            for where in (outside, no_origin, unset)
        ]
        self.assertEqual(len({failure.reason for failure in failures}), 3)
        self.assertEqual(len({failure.hint for failure in failures}), 3)


class WorkTreeTest(RepoTest):
    """What "not a work tree" covers, measured against git rather than assumed."""

    def test_a_directory_outside_any_repository_is_not_a_work_tree(self) -> None:
        outside = self.tmp / "no-repo"
        outside.mkdir()
        failed = self.unresolved(resolve.resolve_base(config_at(outside), start=outside))
        self.assertIn("not inside a git work tree", failed.reason)
        self.assertIn("not a git repository", failed.reason)

    def test_the_inside_of_a_git_directory_is_not_a_work_tree_either(self) -> None:
        """`git rev-parse --is-inside-work-tree` exits **0** here and prints `false`.
        Measured, not assumed. A resolver checking only the returncode would carry on into
        `git remote` from inside `.git/` and report whatever it found there."""
        repo = self.make_repo(self.tmp / "repo")
        inside = repo / ".git"
        probe = proc.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=inside,
            timeout=TIMEOUT,
            env=self.env,
        )
        self.assertTrue(probe.ok, probe.stderr)
        self.assertEqual(probe.stdout.strip(), "false")

        failed = self.unresolved(resolve.resolve_base(config_at(inside), start=inside))
        self.assertIn("not inside a git work tree", failed.reason)
        self.assertIn("not a work tree", failed.reason)

    def test_a_broken_git_config_is_reported_with_gits_own_words(self) -> None:
        """Why this reason carries git's text at all.

        "not a git repository" and "bad config line 1 in file .git/config" are both `fatal:`
        at exit 128, so bessemer cannot tell them apart — and a reason written from the
        returncode alone would send someone with a broken config file hunting for a missing
        repository. Measured: with the config broken, `rev-parse --is-inside-work-tree`
        itself fails, which is why this is the reason it lands in.
        """
        repo = self.make_repo(self.tmp / "repo")
        (repo / ".git" / "config").write_text("this is not\n[valid\n", encoding="utf-8")
        failed = self.unresolved(resolve.resolve_base(config_at(repo), start=repo))
        self.assertIn("bad config", failed.reason)


class RootAgreementTest(RepoTest):
    """The config root and the git root, in all three relations plus the failure cases."""

    def adapter(self, root: Path) -> Path:
        """Create `root/.bessemer/` and return `root`, so `config.load` can find it."""
        (root / config.ADAPTER_DIR).mkdir(parents=True)
        return root

    def loaded(self, start: Path) -> Config:
        """A `Config` from the real discovery walk, so `root` is what a user would get."""
        loaded = config.load(start=start, env={})
        assert isinstance(loaded, Resolved), loaded
        return loaded.value

    def test_the_roots_agreeing_returns_the_agreed_root(self) -> None:
        repo = self.adapter(self.make_repo(self.tmp / "repo"))
        nested = repo / "src" / "deep"
        nested.mkdir(parents=True)
        outcome = resolve.resolve_root_agreement(self.loaded(nested), start=nested)
        self.assertEqual(outcome, Resolved(repo))

    def test_an_adapter_above_the_git_root_fails_and_names_both_paths(self) -> None:
        """The stray `~/.bessemer` shape: `init` ran in a home directory, and the walk-up
        from inside a repository escaped the repository to find it."""
        outer = self.adapter(self.tmp / "outer")
        repo = self.make_repo(outer / "repo")
        start = repo / "src"
        start.mkdir()

        cfg = self.loaded(start)
        self.assertEqual(cfg.root, outer)
        failed = self.unresolved(resolve.resolve_root_agreement(cfg, start=start))
        self.assertIn(str(outer), failed.reason)
        self.assertIn(str(repo), failed.reason)
        self.assertIn("above", failed.reason)

    def test_an_adapter_below_the_git_root_fails_and_names_both_paths(self) -> None:
        """The other direction: `init` ran in a subdirectory of the repository."""
        repo = self.make_repo(self.tmp / "repo")
        inner = self.adapter(repo / "packages" / "web")

        cfg = self.loaded(inner)
        self.assertEqual(cfg.root, inner)
        failed = self.unresolved(resolve.resolve_root_agreement(cfg, start=inner))
        self.assertIn(str(inner), failed.reason)
        self.assertIn(str(repo), failed.reason)
        self.assertIn("below", failed.reason)

    def test_the_three_relations_do_not_share_a_reason_or_a_hint(self) -> None:
        agreeing = self.adapter(self.make_repo(self.tmp / "agree"))
        outer = self.adapter(self.tmp / "outer")
        above_start = self.make_repo(outer / "repo")
        below_root = self.make_repo(self.tmp / "below")
        below_start = self.adapter(below_root / "sub")

        self.assertIsInstance(
            resolve.resolve_root_agreement(self.loaded(agreeing), start=agreeing), Resolved
        )
        failures = [
            self.unresolved(resolve.resolve_root_agreement(self.loaded(start), start=start))
            for start in (above_start, below_start)
        ]
        self.assertNotEqual(failures[0].reason, failures[1].reason)
        self.assertNotEqual(failures[0].hint, failures[1].hint)

    def test_a_submodule_trips_the_check_by_itself(self) -> None:
        """The useful side effect, with a real submodule rather than an argument about one.

        `git rev-parse --show-toplevel` inside a submodule returns the submodule's root, so
        the superproject's adapter is *above* it — and dispatching submodule code against the
        superproject's image fails instead of silently using the wrong one.

        The submodule is added from a local path, which needs `protocol.file.allow=always`;
        nothing is fetched over a network.
        """
        upstream = self.make_repo(self.tmp / "upstream")
        self.commit(upstream)
        super_project = self.adapter(self.make_repo(self.tmp / "super"))
        self.commit(super_project)
        self.git("submodule", "add", "--quiet", str(upstream), "sub", cwd=super_project)

        inside = super_project / "sub"
        cfg = self.loaded(inside)
        self.assertEqual(cfg.root, super_project)
        failed = self.unresolved(resolve.resolve_root_agreement(cfg, start=inside))
        self.assertIn(str(inside), failed.reason)
        self.assertIn("submodule", failed.reason)

    def test_outside_a_repository_it_is_the_work_tree_reason(self) -> None:
        """Not a disagreement: there is no git root to disagree with. Reported as the same
        case `resolve_base` reports, because it is the same fact about the machine."""
        outside = self.adapter(self.tmp / "no-repo")
        failed = self.unresolved(
            resolve.resolve_root_agreement(self.loaded(outside), start=outside)
        )
        self.assertIn("not inside a git work tree", failed.reason)

    def test_unrelated_roots_fail_closed(self) -> None:
        """Rare rather than unreachable, so it is stubbed. Kept because the one thing this must
        not do when git names a tree bessemer was not looking at is pick a root and push
        from it.

        **Both directories are created.** They did not have to be while the comparison was made
        of string prefixes; now that it is made of `stat` calls, a path that does not exist
        lands in `_root_unreadable` instead — which is a different case with a different reason,
        and this test caught exactly that when the comparison changed under it.
        """
        here = self.tmp / "here"
        here.mkdir()
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        with mock.patch.object(
            proc, "run", fake_git({"show-toplevel": (0, f"{elsewhere}\n", "")})
        ):
            failed = self.unresolved(
                resolve.resolve_root_agreement(config_at(here), start=self.tmp)
            )
        self.assertIn("neither contains the other", failed.reason)

    def test_a_root_that_cannot_be_stated_is_a_refusal_not_an_agreement(self) -> None:
        """Where the cost of comparing by identity lands. `==` cannot fail; `samestat` needs a
        `stat`, and the ordinary way that fails is a race — a checkout removed between the
        `git rev-parse` and the comparison, which is what F3's garbage collection will be doing
        to a neighbouring directory while a run resolves.

        Fails closed: "could not tell" must not read as "they agree" in the predicate that makes
        "the host pushes from the main repository" unambiguous.
        """
        vanished = self.tmp / "removed-under-us"
        with mock.patch.object(
            proc, "run", fake_git({"show-toplevel": (0, f"{vanished}\n", "")})
        ):
            failed = self.unresolved(
                resolve.resolve_root_agreement(config_at(self.tmp), start=self.tmp)
            )
        self.assertIn("could not compare", failed.reason)
        self.assertIn("removed mid-run", failed.hint)

    def test_a_start_directory_that_is_not_there_is_not_reported_as_a_missing_git(self) -> None:
        """`subprocess` raises `FileNotFoundError` for an absent `cwd` exactly as it does for an
        absent program, so the two arrive identically and only `error.filename` tells them
        apart. Telling someone whose `start` path is misspelled to install git sends them to the
        wrong place entirely. Real git, no stub: the kernel is what refuses this.
        """
        missing = self.tmp / "no-such-directory"
        failed = self.unresolved(resolve.resolve_base(config_at(missing), start=missing))
        self.assertIn(f"git could not be run in {missing}", failed.reason)
        self.assertNotIn("install git", failed.hint)
        self.assertIn("is a directory", failed.hint)

    def test_a_start_that_is_a_file_lands_in_the_same_case(self) -> None:
        """`NotADirectoryError`, a different `OSError` subclass with the directory in
        `filename` just the same. Pinned because an implementation keying on
        `FileNotFoundError` alone would send this one back to "install git"."""
        not_a_directory = self.tmp / "a-file"
        not_a_directory.write_text("not a directory\n", encoding="utf-8")
        failed = self.unresolved(
            resolve.resolve_base(config_at(not_a_directory), start=not_a_directory)
        )
        self.assertIn(f"git could not be run in {not_a_directory}", failed.reason)
        self.assertNotIn("install git", failed.hint)


class AbsorbedExceptionTest(RepoTest):
    """`proc.run`'s two raising cases, enumerated against `proc` rather than against stubs.

    `bessemer.proc.run` documents exactly two: `OSError` when the program could not be
    executed, `subprocess.TimeoutExpired` when it was killed. Both are provoked out of the
    real wrapper below, and the module's own `except` clauses are read out of its AST — so
    this passes only while the two agree. A stub-only test would prove that `resolve`
    handles the exceptions this file thought to raise.
    """

    HANDLERS: Final = {
        "OSError": OSError,
        "proc.TimeoutExpired": subprocess.TimeoutExpired,
    }
    """Every `except` clause in `_git`, hand-written, in source order, with the class each
    one names. The order is not incidental: both are unrelated types, so neither shadows the
    other, but a third clause or a deleted one is the change this is here to catch."""

    def real_exceptions(self) -> list[BaseException]:
        """The two exceptions `proc.run` actually raises, from real spawns.

        The missing program is named `git` at a path that does not exist, because the suite's
        guard allowlists spawns by basename: any other name would raise `GuardViolation`
        instead and this would pass for a reason that has nothing to do with the wrapper.
        """
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OSError) as missing:
                proc.run([str(Path(directory) / "git")], timeout=TIMEOUT)
        with self.assertRaises(subprocess.TimeoutExpired) as expired:
            proc.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.2)
        return [missing.exception, expired.exception]

    def test_the_module_catches_exactly_the_clauses_named_here(self) -> None:
        self.assertEqual(git_handlers(), list(self.HANDLERS))

    def test_the_re_exported_name_is_the_real_exception_class(self) -> None:
        """`proc.TimeoutExpired` exists so this module can name it without importing
        `subprocess`, which `tests/test_argv_boundary.py` forbids it. An alias that had
        drifted to some other object would make the clause above catch nothing."""
        self.assertIs(proc.TimeoutExpired, subprocess.TimeoutExpired)

    def test_each_exception_proc_really_raises_is_caught_by_exactly_one_clause(self) -> None:
        """The anchor. Two real exceptions, two clauses, one clause each — so neither is
        uncovered and neither is absorbed by a neighbour."""
        for error in self.real_exceptions():
            with self.subTest(exception=type(error).__name__):
                matching = [
                    name for name, caught in self.HANDLERS.items() if isinstance(error, caught)
                ]
                # `len(matching)`, bare. An earlier spelling was
                # `matching and len(matching)`, which for the no-match case compares `[]`
                # against `1` — still a failure, but reported as a list-versus-int mismatch
                # rather than as "no clause catches this", and green for the wrong shape if the
                # expected count were ever `0`.
                self.assertEqual(len(matching), 1, f"{error!r} -> {matching}")

    def test_a_missing_git_is_a_reason_from_both_resolvers(self) -> None:
        """The case doctor exists for: a resolver that let this escape would print a
        traceback in the one situation the tool is there to report on."""
        error = FileNotFoundError(2, "No such file or directory", "git")
        cfg = config_at(self.tmp)
        with mock.patch.object(proc, "run", fake_git({"is-inside-work-tree": error})):
            failed = self.unresolved(resolve.resolve_base(cfg, start=self.tmp))
        self.assertIn("git could not be run", failed.reason)
        self.assertIn("PATH", failed.hint)

        with mock.patch.object(proc, "run", fake_git({"show-toplevel": error})):
            also = self.unresolved(resolve.resolve_root_agreement(cfg, start=self.tmp))
        self.assertIn("git could not be run", also.reason)
        # One case, so one hint — while the reasons differ, because each names the command
        # that could not be run. A user with no git needs the same instruction either way.
        self.assertEqual(also.hint, failed.hint)
        self.assertNotEqual(also.reason, failed.reason)

    def test_a_timeout_is_a_reason_from_both_resolvers(self) -> None:
        expired = subprocess.TimeoutExpired(cmd=["git", "rev-parse"], timeout=15.0)
        cfg = config_at(self.tmp)
        with mock.patch.object(proc, "run", fake_git({"is-inside-work-tree": expired})):
            failed = self.unresolved(resolve.resolve_base(cfg, start=self.tmp))
        self.assertIn("did not finish within", failed.reason)

        with mock.patch.object(proc, "run", fake_git({"show-toplevel": expired})):
            also = self.unresolved(resolve.resolve_root_agreement(cfg, start=self.tmp))
        self.assertIn("did not finish within", also.reason)

    def test_a_timeouts_captured_output_never_reaches_the_reason(self) -> None:
        """`TimeoutExpired` carries the child's `stdout` and `stderr`, which is exactly the
        credential-bearing text. The reason is built from bessemer's own argv instead, and
        this is what stops someone formatting the exception into it later."""
        expired = subprocess.TimeoutExpired(
            cmd=["git", "rev-parse"], timeout=15.0, output="", stderr=CREDENTIAL_STDERR
        )
        with mock.patch.object(proc, "run", fake_git({"is-inside-work-tree": expired})):
            failed = self.unresolved(resolve.resolve_base(config_at(self.tmp), start=self.tmp))
        self.assertNotIn(TOKEN, failed.reason + failed.hint)

    def test_a_deleted_working_directory_is_a_reason_and_not_a_traceback(self) -> None:
        """The one raising call that is neither git's nor `proc`'s: `Path.cwd()` raises when
        the directory a shell is sitting in has been removed under it. Stubbed because a
        test may not delete its own runner's directory."""

        def gone() -> Path:
            raise FileNotFoundError(2, "No such file or directory")

        cfg = config_at(self.tmp)
        with mock.patch.object(Path, "cwd", gone):
            for outcome in (
                resolve.resolve_base(cfg),
                resolve.resolve_root_agreement(cfg),
            ):
                failed = self.unresolved(outcome)
                self.assertIn("current working directory", failed.reason)


class CredentialTest(RepoTest):
    """git's `stderr` is credential-bearing, and these reasons are printed to people.

    Doctor renders a reason to a terminal and from F3 dispatch renders one into a pull
    request body, so a token that reaches a reason reaches both. The decision under test is
    per reason: the two undiagnosed cases carry git's first line, redacted; every diagnosed
    case carries none of git's text at all.
    """

    def test_the_fixture_carries_a_credential_at_all(self) -> None:
        """The control for this class: every assertion below is a `not in`, and each of them
        passes against a fixture that never had a token in it."""
        self.assertIn(TOKEN, CREDENTIAL_STDERR)
        self.assertIn("x-access-token", CREDENTIAL_URL)

    def failing(self, command: str) -> Unresolved:
        """Drive `resolve_base` with one git command failing, credentials and all."""
        with mock.patch.object(proc, "run", fake_git({command: (128, "", CREDENTIAL_STDERR)})):
            return self.unresolved(resolve.resolve_base(config_at(self.tmp), start=self.tmp))

    def test_an_undiagnosed_failure_keeps_gits_words_and_drops_the_token(self) -> None:
        failed = self.failing("remote")
        self.assertNotIn(TOKEN, failed.reason + failed.hint)
        self.assertIn("<redacted>@github.com", failed.reason)
        # The point of keeping stderr at all: without git's own words this reason is
        # "`git remote` exited 128" and sends its reader nowhere.
        self.assertIn("unable to access", failed.reason)

    def test_the_work_tree_failure_keeps_gits_words_and_drops_the_token_too(self) -> None:
        failed = self.failing("is-inside-work-tree")
        self.assertNotIn(TOKEN, failed.reason + failed.hint)
        self.assertIn("<redacted>@github.com", failed.reason)

    def test_a_diagnosed_failure_carries_none_of_gits_text(self) -> None:
        """The other half of the decision. Bessemer knows what an unset `origin/HEAD` is and
        what fixes it, so quoting git there would add a credential-bearing string to a
        message that needed nothing from it."""
        failed = self.failing("symbolic-ref")
        self.assertNotIn(TOKEN, failed.reason + failed.hint)
        self.assertNotIn("unable to access", failed.reason)
        self.assertNotIn("github.com", failed.reason)

    def test_every_shape_of_credential_bearing_url_is_redacted(self) -> None:
        """Userinfo is redacted as a whole rather than by matching token prefixes: `ghp_`,
        `github_pat_`, `glpat-` and whatever the next forge invents are a list that goes
        stale, while "the part of a URL before the `@`" is the grammar itself."""
        shapes = (
            f"https://x-access-token:{TOKEN}@github.com/o/r.git",
            f"https://{TOKEN}@github.com/o/r.git",
            f"https://user:{TOKEN}@gitlab.example.com:8443/o/r.git",
            f"ssh://user:{TOKEN}@git.example.com/o/r.git",
        )
        for url in shapes:
            with self.subTest(url=url):
                with mock.patch.object(
                    proc,
                    "run",
                    fake_git({"remote": (128, "", f"fatal: unable to access '{url}/'")}),
                ):
                    failed = self.unresolved(
                        resolve.resolve_base(config_at(self.tmp), start=self.tmp)
                    )
                self.assertNotIn(TOKEN, failed.reason + failed.hint)
                self.assertIn("<redacted>@", failed.reason)

    def test_an_scp_style_remote_is_left_alone(self) -> None:
        """`git@github.com:o/r.git` has no `://` and carries no secret. Redacting it would
        remove the only identifying part of a reason for no gain."""
        with mock.patch.object(
            proc,
            "run",
            fake_git({"remote": (128, "", "fatal: 'git@github.com:o/r.git' not found")}),
        ):
            failed = self.unresolved(resolve.resolve_base(config_at(self.tmp), start=self.tmp))
        self.assertIn("git@github.com:o/r.git", failed.reason)


class SpawnBoundaryTest(unittest.TestCase):
    """One call site, one timeout. Read out of the module rather than counted at runtime."""

    def test_there_is_exactly_one_spawn_call_and_it_names_a_timeout(self) -> None:
        """Every git invocation goes through `bessemer.proc` with an explicit timeout — and
        the way that stays true is that there is one place it could be written. A second
        call site is how the tenth one ends up without a timeout."""
        calls = spawn_calls()
        self.assertEqual(len(calls), 1)
        keywords = [keyword.arg for keyword in calls[0].keywords]
        self.assertIn("timeout", keywords)
        # `env` too: leaving it out is how the ambient `GIT_DIR` gets back in, and a second
        # spawn site is exactly where it would be left out. Both properties, one call site.
        self.assertIn("env", keywords)

    def test_the_timeout_is_a_number_of_seconds_and_not_optional(self) -> None:
        """A `None` timeout is `subprocess`'s spelling for "wait forever", so the constant
        being a number is the property, not merely that a keyword is present."""
        self.assertIsInstance(resolve.TIMEOUT_SECONDS, float)
        self.assertGreater(resolve.TIMEOUT_SECONDS, 0)

    def test_the_source_really_was_read(self) -> None:
        """Every assertion above passes on an empty string. This is what stops that."""
        self.assertIn("def resolve_base(", resolve_source())


class SpellingTest(RepoTest):
    """One directory reached by two spellings must be one directory to root agreement.

    **This test can only fail on a case-insensitive filesystem.** macOS's default is one and
    Windows is one; CI runs `ubuntu-latest`, where `…/repo` and `…/Repo` are genuinely different
    directories, so there the fixture below is two repositories and the assertion is a
    tautology. Said here rather than left to be discovered, the way `tests/README.md` records
    the other wrong-reason cases — a green run on Linux is not evidence about this defect, and
    `test_the_filesystem_is_what_makes_this_test_mean_anything` reports which kind of host ran.

    Measured on macOS, and this is the whole reason `_relate` exists: `Path.resolve()` follows
    symlinks but does not canonicalise case, so `==` and `is_relative_to` both answer "different
    trees" while `samefile` answers "the same directory". Root agreement would then tell a user
    to `cd` somewhere they already are.
    """

    def case_insensitive(self) -> bool:
        """Whether this filesystem treats two cases of one name as one directory."""
        probe = self.tmp / "CaseProbe"
        probe.mkdir()
        return (self.tmp / "caseprobe").is_dir()

    def test_the_filesystem_is_what_makes_this_test_mean_anything(self) -> None:
        """Not an assertion about the host — that would be red on Linux and green on macOS for
        reasons having nothing to do with bessemer. It records which one ran, so a reader of a
        green log can tell whether the test below proved anything."""
        kind = "case-insensitive" if self.case_insensitive() else "case-sensitive"
        self.assertIn(kind, ("case-insensitive", "case-sensitive"))

    def test_two_spellings_of_one_root_agree(self) -> None:
        repo = self.make_repo(self.tmp / "Repo")
        (repo / config.ADAPTER_DIR).mkdir()
        if not self.case_insensitive():
            self.skipTest("case-sensitive filesystem: the two spellings are two directories")

        # The differently-cased spelling, never `resolve`d into agreement by the test itself:
        # `find_adapter_dir` resolves, and `resolve()` is what fails to canonicalise case, so
        # this is the exact path a caller passing an explicit `start` would hand over.
        other = self.tmp / "repo"
        cfg = config_at(other)
        self.assertNotEqual(cfg.root.resolve(), repo.resolve())
        self.assertTrue(cfg.root.resolve().samefile(repo.resolve()))

        outcome = resolve.resolve_root_agreement(cfg, start=other)
        self.assertIsInstance(outcome, Resolved)

    def test_a_differently_cased_subdirectory_is_still_below(self) -> None:
        """Ancestry by identity, not by prefix: the adapter is genuinely below the work
        tree, and the answer must stay `below` rather than degrading to `unrelated` because
        the spellings differ."""
        repo = self.make_repo(self.tmp / "Repo")
        inner = repo / "Packages" / "Web"
        inner.mkdir(parents=True)
        (inner / config.ADAPTER_DIR).mkdir()
        if not self.case_insensitive():
            self.skipTest("case-sensitive filesystem: the two spellings are two directories")

        failed = self.unresolved(
            resolve.resolve_root_agreement(
                config_at(self.tmp / "repo" / "packages" / "web"),
                start=self.tmp / "repo" / "packages" / "web",
            )
        )
        self.assertIn("below the git work tree", failed.reason)

    def test_the_relations_are_exactly_these_four(self) -> None:
        """Hand-written, and closed against growth: a fifth relation would mean a case
        `resolve_root_agreement` does not handle, and its `match` would fall through to
        `unrelated` — reporting a disagreement about something else entirely."""
        self.assertEqual(
            resolve.RELATIONS, ("same", "config-above", "config-below", "unrelated")
        )


class AmbientEnvironmentTest(RepoTest):
    """The developer's shell must not redirect either resolver, and must not redden the suite.

    Two separate defects, both real, and both fixed by the same policy:

    1. **The suite went red on correct code because of the ambient environment.** With
       `GIT_DIR` exported, 15 of 45 tests here failed — every temporary directory looked like
       a repository, so cases that must be `Unresolved` came back `Resolved('main')` from
       bessemer's own checkout. Green in CI, green in an agent session, red only under a
       human's own shell: the same shape as issue 03's stdin defect, and it fails in the one
       place someone is watching.
    2. **`resolve_root_agreement` could be pointed at another repository.** It is what makes
       "the host pushes from the main repository" name something unambiguous, so a variable
       that silently redirects it is a bypass, not a preference.

    Every variable is asserted immune. The four that this fixture can *show* redirecting a
    bare git call are asserted in both directions, so the immunity assertions cannot be
    passing because the variable never mattered.
    """

    REDIRECTING = [
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CEILING_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_DIR",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_WORK_TREE",
    ]
    """Every variable the resolvers strip, restated by hand and sorted.

    A literal rather than an assertion against `resolve.REDIRECTING_VARIABLES`: a test that
    reads the set it checks cannot notice the set shrinking, and shrinking it is what
    reopens the bypass. Removing `GIT_DIR` from the module must cost an edit here too.
    """

    MEASURED = ["GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_CEILING_DIRECTORIES"]
    """The four shown below to redirect a bare git call in this fixture.

    The other five are stripped on git's documentation — they name an index, an object store,
    a ref namespace or a discovery rule — and nothing here demonstrates them. Named rather
    than blurred into the list above, so the set does not read as nine proven bypasses.
    """

    def setUp(self) -> None:
        super().setUp()
        # The decoy holds a *different* answer for every question a resolver asks: it is a
        # repository, it has an `origin`, and its `origin/HEAD` names another branch. An
        # empty decoy would let a redirect pass as a coincidence.
        self.decoy = self.make_repo(self.tmp / "decoy")
        self.add_origin(self.decoy)
        self.set_origin_head(self.decoy, branch="decoy-default")

        self.local = self.make_repo(self.tmp / "local")
        self.add_origin(self.local)
        self.set_origin_head(self.local)
        # Deep, so `GIT_CEILING_DIRECTORIES` has a walk to interrupt: at the root of a work
        # tree there is nothing to walk up, and the variable would look harmless.
        self.deep = self.local / "packages" / "web"
        self.deep.mkdir(parents=True)
        (self.deep / config.ADAPTER_DIR).mkdir()

        # A third repository, with **no remotes at all**, for the both-directions probe.
        # `GIT_COMMON_DIR` substitutes config and refs, so it only shows as a difference
        # where the honest answer is *absent*: in `self.local`, which has its own `origin`
        # and its own `origin/HEAD`, the decoy's answers coincide with the truth and a real
        # redirect reads as no change. Measured — this is what the first version of the test
        # got wrong.
        self.spare = self.make_repo(self.tmp / "spare")
        self.spare_deep = self.spare / "a" / "b"
        self.spare_deep.mkdir(parents=True)

    def values(self) -> dict[str, str]:
        """Each variable, set to something that names the decoy rather than the local repo."""
        return {
            "GIT_DIR": str(self.decoy / ".git"),
            "GIT_WORK_TREE": str(self.decoy),
            "GIT_COMMON_DIR": str(self.decoy / ".git"),
            "GIT_CEILING_DIRECTORIES": str(self.local),
            "GIT_DISCOVERY_ACROSS_FILESYSTEM": "1",
            "GIT_NAMESPACE": "decoy",
            "GIT_INDEX_FILE": str(self.decoy / ".git" / "index"),
            "GIT_OBJECT_DIRECTORY": str(self.decoy / ".git" / "objects"),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(self.decoy / ".git" / "objects"),
        }

    def test_the_module_strips_exactly_the_variables_named_here(self) -> None:
        self.assertEqual(sorted(resolve.REDIRECTING_VARIABLES), self.REDIRECTING)

    def test_the_measured_subset_is_part_of_the_stripped_set(self) -> None:
        """Otherwise the both-directions test below could be proving something about a
        variable the module never strips."""
        self.assertLessEqual(set(self.MEASURED), set(self.REDIRECTING))

    def test_every_variable_has_a_value_to_test_with(self) -> None:
        """The control for `values`: a variable missing from it would silently go untested by
        every loop below, which is precisely how one drops out of coverage."""
        self.assertEqual(sorted(self.values()), self.REDIRECTING)

    def test_a_bare_git_call_really_is_redirected_by_each_measured_variable(self) -> None:
        """Both directions, for the four that can be shown. Without this, every immunity
        assertion below could be green because the variable does nothing at all.

        `GIT_DIR` is the sharp one: it makes `symbolic-ref` read the decoy's refs, so a
        resolver inheriting it reports the decoy's default branch as this repository's base.
        `GIT_CEILING_DIRECTORIES` redirects in the other direction — it makes a real work
        tree fail to be found at all.
        """
        commands = (
            ["git", "rev-parse", "--show-toplevel"],
            ["git", "symbolic-ref", "--quiet", "--short", resolve.ORIGIN_HEAD],
            ["git", "remote"],
        )
        # Probed inside `self.spare`, which has no remotes: see `setUp`. The ceiling names
        # that repository's root, because a ceiling above the walk's start is what stops it.
        poison = {
            "GIT_DIR": str(self.decoy / ".git"),
            "GIT_WORK_TREE": str(self.decoy),
            "GIT_COMMON_DIR": str(self.decoy / ".git"),
            "GIT_CEILING_DIRECTORIES": str(self.spare),
        }
        self.assertEqual(sorted(poison), sorted(self.MEASURED))

        for name, value in poison.items():
            with self.subTest(variable=name):
                changed = False
                for command in commands:
                    honest = proc.run(
                        command, cwd=self.spare_deep, timeout=TIMEOUT, env=self.env
                    )
                    after = proc.run(
                        command,
                        cwd=self.spare_deep,
                        timeout=TIMEOUT,
                        env={**self.env, name: value},
                    )
                    changed = changed or (honest.returncode, honest.stdout) != (
                        after.returncode,
                        after.stdout,
                    )
                self.assertTrue(
                    changed, f"{name} changed nothing; it does not belong in MEASURED"
                )

    def test_resolve_base_answers_about_the_repository_on_disk(self) -> None:
        """`main` is the local repository's `origin/HEAD`; `decoy-default` is the one every
        variable points at."""
        for name, value in self.values().items():
            with self.subTest(variable=name):
                with mock.patch.dict(os.environ, {name: value}):
                    outcome = resolve.resolve_base(config_at(self.deep), start=self.deep)
                self.assertEqual(outcome, Resolved("main"))

    def test_root_agreement_answers_about_the_repository_on_disk(self) -> None:
        """The security half. The adapter is at `packages/web/`, so the honest answer is the
        below-the-git-root failure naming the local repository — never agreement, and never a
        word about the decoy."""
        for name, value in self.values().items():
            with self.subTest(variable=name):
                with mock.patch.dict(os.environ, {name: value}):
                    failed = self.unresolved(
                        resolve.resolve_root_agreement(config_at(self.deep), start=self.deep)
                    )
                self.assertIn("below the git work tree", failed.reason)
                self.assertIn(str(self.local), failed.reason)
                self.assertNotIn(str(self.decoy), failed.reason)

    def test_all_of_them_at_once_change_nothing_either(self) -> None:
        """Set individually, one variable can be inert because another is missing —
        `core.worktree` needs `GIT_DIR`, and `GIT_COMMON_DIR` behaves differently alongside
        it. All nine together is the combination a poisoned shell actually has."""
        with mock.patch.dict(os.environ, self.values()):
            self.assertEqual(
                resolve.resolve_base(config_at(self.deep), start=self.deep), Resolved("main")
            )
            failed = self.unresolved(
                resolve.resolve_root_agreement(config_at(self.deep), start=self.deep)
            )
        self.assertIn(str(self.local), failed.reason)
        self.assertNotIn(str(self.decoy), failed.reason)

    def test_the_environment_is_inherited_minus_those_names_and_nothing_else(self) -> None:
        """A subtraction, not an allowlist. `HOME` carries `safe.directory` and `PATH` is how
        git is found at all; an allowlist here is what breaks on the next machine, and it
        would break it in a way that reads as bessemer being unable to run git."""
        ambient = {
            "GIT_DIR": "/somewhere/else/.git",
            "GIT_CONFIG_GLOBAL": "/home/dev/.gitconfig",
            "HOME": "/home/dev",
            "PATH": "/usr/bin",
            "SSH_AUTH_SOCK": "/tmp/agent.sock",
            "LANG": "en_GB.UTF-8",
        }
        with mock.patch.dict(os.environ, ambient, clear=True):
            built = resolve._git_env()
        self.assertEqual(
            dict(built),
            {name: value for name, value in ambient.items() if name != "GIT_DIR"},
        )


class FailureCaseTest(RepoTest):
    """The fourteen ways a resolver can fail, produced side by side and compared.

    Each has its own test above and each of those asserts a substring. None of them can see
    that two cases have collapsed into one wording — a branch deleted and absorbed by a
    neighbour leaves every per-case assertion green while a user can no longer tell which
    mistake they made.

    So the fourteen are restated here by hand **and anchored to the module's own AST**: every
    case is constructed at exactly one site in `bessemer/resolve.py`, so counting
    `Unresolved(...)` there is a count of cases. That closes the set against growth as well
    as collapse, which a literal compared only to the hand-written fixture list beside it
    cannot do — it would restate the fixtures. `tests/test_config.py` does the same to
    `bessemer/config.py` and is the pattern this follows.
    """

    CASE_NAMES = [
        "base is not a branch name",
        "base is not a string",
        "config root above git root",
        "config root below git root",
        "config root unrelated to git root",
        "git could not be executed",
        "git failed undiagnosably",
        "git timed out",
        "no origin remote",
        "not a work tree",
        "origin/HEAD unset",
        "roots could not be compared",
        "start directory unusable",
        "working directory gone",
    ]
    """Every way either resolver can fail, sorted. Hand-written; see the class docstring.

    Twelve until the review of this issue, then fourteen: comparing roots by identity
    introduced a `stat` that can fail (`roots could not be compared`), and `subprocess` raising
    `FileNotFoundError` for an absent `cwd` as well as an absent program needed telling apart
    from "install git" (`start directory unusable`). Both additions turned this list red first,
    which is the closure against growth doing its job — the count moved because someone edited
    it on purpose.
    """

    def cases(self) -> dict[str, Unresolved]:
        """One `Unresolved` per failure case, keyed by the name used in the criteria.

        Real repositories wherever a repository can produce the case; a stubbed `proc.run`
        for the five that none can — a missing git, a wedged git, a git answering about an
        unrelated tree, a git answering about a directory that is no longer there, and a
        deleted working directory.
        """
        outside = self.tmp / "no-repo"
        outside.mkdir()
        never_existed = self.tmp / "no-such-directory"
        no_origin = self.make_repo(self.tmp / "no-origin")
        unset = self.make_repo(self.tmp / "unset")
        self.add_origin(unset)

        outer = self.tmp / "outer"
        (outer / config.ADAPTER_DIR).mkdir(parents=True)
        above = self.make_repo(outer / "repo")
        below_root = self.make_repo(self.tmp / "below")
        below = below_root / "sub"
        (below / config.ADAPTER_DIR).mkdir(parents=True)

        def loaded(start: Path) -> Config:
            outcome = config.load(start=start, env={})
            assert isinstance(outcome, Resolved), outcome
            return outcome.value

        found: dict[str, Unresolved] = {
            "base is not a string": self.unresolved(
                resolve.resolve_base(config_at(self.tmp, committed={"base": 5}), start=self.tmp)
            ),
            "base is not a branch name": self.unresolved(
                resolve.resolve_base(
                    config_at(self.tmp, committed={"base": ""}), start=self.tmp
                )
            ),
            "not a work tree": self.unresolved(
                resolve.resolve_base(config_at(outside), start=outside)
            ),
            "no origin remote": self.unresolved(
                resolve.resolve_base(config_at(no_origin), start=no_origin)
            ),
            "origin/HEAD unset": self.unresolved(
                resolve.resolve_base(config_at(unset), start=unset)
            ),
            "config root above git root": self.unresolved(
                resolve.resolve_root_agreement(loaded(above), start=above)
            ),
            "config root below git root": self.unresolved(
                resolve.resolve_root_agreement(loaded(below), start=below)
            ),
            # Real git, no stub: the kernel refuses the `cwd`, which is the whole point of
            # telling this apart from a missing git.
            "start directory unusable": self.unresolved(
                resolve.resolve_base(config_at(never_existed), start=never_existed)
            ),
        }

        stubs: dict[str, Mapping[str, Answer | BaseException]] = {
            "git failed undiagnosably": {"remote": (128, "", "fatal: something git knows")},
            "git could not be executed": {
                "is-inside-work-tree": FileNotFoundError(2, "No such file or directory", "git")
            },
            "git timed out": {
                "is-inside-work-tree": subprocess.TimeoutExpired(cmd=["git"], timeout=15.0)
            },
        }
        for name, answers in stubs.items():
            with mock.patch.object(proc, "run", fake_git(answers)):
                found[name] = self.unresolved(
                    resolve.resolve_base(config_at(self.tmp), start=self.tmp)
                )

        # Both directories exist: with the comparison made of `stat` calls, a path that is not
        # there is the *next* case rather than this one.
        here = self.tmp / "here"
        here.mkdir()
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        with mock.patch.object(
            proc, "run", fake_git({"show-toplevel": (0, f"{elsewhere}\n", "")})
        ):
            found["config root unrelated to git root"] = self.unresolved(
                resolve.resolve_root_agreement(config_at(here), start=self.tmp)
            )

        with mock.patch.object(
            proc, "run", fake_git({"show-toplevel": (0, f"{self.tmp / 'vanished'}\n", "")})
        ):
            found["roots could not be compared"] = self.unresolved(
                resolve.resolve_root_agreement(config_at(self.tmp), start=self.tmp)
            )

        def gone() -> Path:
            raise FileNotFoundError(2, "No such file or directory")

        with mock.patch.object(Path, "cwd", gone):
            found["working directory gone"] = self.unresolved(
                resolve.resolve_base(config_at(self.tmp))
            )

        return found

    def test_the_module_produces_one_failure_per_named_case(self) -> None:
        """The anchor to the module. A thirteenth case with its own reason and hint leaves
        every assertion below green, because they all compare hand-written lists to fixtures
        this file also wrote."""
        self.assertEqual(unresolved_sites(), len(self.CASE_NAMES))

    def test_there_are_fourteen_failure_cases_and_no_two_read_alike(self) -> None:
        cases = self.cases()
        self.assertEqual(sorted(cases), self.CASE_NAMES)
        pairs = [(failure.reason, failure.hint) for failure in cases.values()]
        self.assertEqual(len(set(pairs)), len(pairs))
        # Reason and hint separately, not only as a pair: two cases sharing a hint while
        # differing in a path or an exception's text still produce distinct pairs, and a
        # shared hint is the collapse a user actually feels.
        self.assertEqual(len({reason for reason, _ in pairs}), len(pairs))
        self.assertEqual(len({hint for _, hint in pairs}), len(pairs))

    def test_every_failure_case_carries_both_a_reason_and_a_hint(self) -> None:
        for name, failure in self.cases().items():
            with self.subTest(case=name):
                self.assertTrue(failure.reason, name)
                self.assertTrue(failure.hint, name)

    def test_no_reason_or_hint_leaks_a_credential(self) -> None:
        """Swept across every case rather than asserted about the two that quote git: a
        thirteenth case added later is covered by this without anyone remembering to."""
        for name, failure in self.cases().items():
            with self.subTest(case=name):
                self.assertNotIn(TOKEN, failure.reason + failure.hint)


class NarrowingTest(RepoTest):
    """`match` over the union, with the value's type checked rather than described.

    `assert_type` is a static assertion: mypy fails the build if the narrowed type is not
    exactly what is written, and `make check` runs mypy over `tests/`. A runtime
    `assertIsInstance` cannot tell a correctly generic `Resolved[str]` from a `Resolved[Any]`
    that has quietly erased every call site's type.
    """

    def test_a_resolved_base_narrows_to_str(self) -> None:
        repo = self.make_repo(self.tmp / "repo")
        self.add_origin(repo)
        self.set_origin_head(repo)
        match resolve.resolve_base(config_at(repo), start=repo):
            case Resolved(value=branch):
                assert_type(branch, str)
                self.assertEqual(branch.upper(), "MAIN")
            case Unresolved(reason=reason):
                self.fail(reason)

    def test_a_resolved_root_narrows_to_path(self) -> None:
        repo = self.make_repo(self.tmp / "repo")
        (repo / config.ADAPTER_DIR).mkdir()
        match resolve.resolve_root_agreement(config_at(repo), start=repo):
            case Resolved(value=root):
                assert_type(root, Path)
                self.assertTrue(root.is_dir())
            case Unresolved(reason=reason):
                self.fail(reason)

    def test_both_unions_are_exhausted_by_the_two_cases(self) -> None:
        """`assert_never` is a compile-time claim that nothing else can reach it: a third
        member added to either union fails here, which is the reason these are closed unions
        rather than an `Outcome` protocol."""
        outside = self.tmp / "no-repo"
        outside.mkdir()
        cfg = config_at(outside)
        for outcome in (
            resolve.resolve_base(cfg, start=outside),
            resolve.resolve_root_agreement(cfg, start=outside),
        ):
            match outcome:
                case Resolved():
                    self.fail(f"expected no answer outside a repository: {outcome}")
                case Unresolved():
                    pass
                case unreachable:
                    assert_never(unreachable)


if __name__ == "__main__":
    unittest.main()
