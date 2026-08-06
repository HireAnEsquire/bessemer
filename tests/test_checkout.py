"""Tests for the per-run checkout: creation, identity, branch read, salvage, removal.

**Real temporary git repositories, not mocks.** Every question this module answers is git's
own: whether a fetch fast-forwards, what a detached HEAD looks like on disk, what
`--branch` actually checks out. A double scripted with the answers a reviewer *expects*
would pin this file's beliefs about git rather than git, and the one that matters —
"the fetch refuses when the agent rewrote history" — is exactly the belief that would be
wrong. `tests/guard.py` allowlists `git` for this (F1 issue 05's precedent, ADR 0003's
tier-2 rider 5).

Nothing here reaches the network and nothing needs docker: every remote is a local path,
and the only remote ever contacted is the checkout itself, over the local filesystem.

**The environment is replaced wholesale for the duration of each test**, rather than passed
in at the seam. `bessemer.checkout` builds its own child environment from `os.environ`
through `bessemer.resolve.git_env`, so a fixture that handed one in at the recorder would
be testing the fixture's policy instead of the module's. Patching `os.environ` to
`tests.gitenv.fixture_env` keeps both true at once: the module's real environment policy
runs, and it runs over an environment that cannot see the developer's `~/.gitconfig` —
which the identity tests depend on, since "the dispatching dev has no `user.name`" is one
of the two branches under test.

`Recorder` is the tier-2 double's ancestor and deliberately not a stand-in for git: it
records `(argv, cwd)` and forwards to the real `proc.run`. Recording cwd is what makes the
"no git inside the checkout" assertion expressible at all (ADR 0003, tier-2 rider 2).
"""

import ast
import getpass
import inspect
import os
import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final
from unittest import mock

from bessemer import checkout, proc, resolve
from tests.gitenv import fixture_env

TIMEOUT: Final = 60.0
"""For the fixtures' own git commands. Generous: a backstop, not the thing under test."""

BRANCH: Final = "work"
"""The working branch in every fixture.

A plain name; `ReadBranchTest` covers the one containing a slash, which is the case the
`refs/heads/` prefix strip could get wrong.
"""

IDENTITY: Final = (
    "-c",
    "user.email=tests@bessemer.invalid",
    "-c",
    "user.name=bessemer tests",
    "-c",
    "commit.gpgsign=false",
)
"""Passed to every *fixture* git command, so no fixture depends on the developer's git.

Never passed to anything the module under test runs — the identity the module writes into a
checkout is precisely what `IdentityTest` is about.
"""

SALVAGE_REFSPEC: Final = f"refs/heads/{BRANCH}:refs/heads/{BRANCH}"
"""The fast-forward-only refspec, restated by hand.

The whole point of the literal is that it is written twice, in two files, by two people —
`bessemer.checkout.SALVAGE_REFSPEC` and this. A `+` grown on either side is a forced fetch
that can rewrite a user-owned branch backwards, and a test that built this string from the
module's own template would agree with the mutation instead of catching it.
"""


@dataclass(frozen=True)
class Call:
    """One recorded spawn: what was run, and from where."""

    argv: tuple[str, ...]
    cwd: Path | None


class Recorder:
    """A `proc.Runner` that records every call and then really runs it.

    Not a stand-in for git. The behaviour under test is git's, so the recorder forwards
    everything — argv, timeout, cwd and the environment the module built — untouched, and
    the recording is a side effect. `env` is *not* substituted here; see the module
    docstring for where the hermetic environment actually comes from.
    """

    def __init__(self) -> None:
        self.calls: list[Call] = []

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stdin_text: str | None = None,
    ) -> proc.Result:
        self.calls.append(Call(tuple(argv), cwd))
        return proc.run(argv, timeout=timeout, cwd=cwd, env=env, stdin_text=stdin_text)

    @property
    def argvs(self) -> list[tuple[str, ...]]:
        return [call.argv for call in self.calls]


def refuse_to_spawn(*args: object, **kwargs: object) -> proc.Result:
    """A `proc.run` replacement for the paths that must not spawn anything at all."""
    raise AssertionError(f"the code under test spawned something: {args!r} {kwargs!r}")


class CheckoutTest(unittest.TestCase):
    """Base for the real-repository fixtures. One main repo, one checkout path, one recorder."""

    def setUp(self) -> None:
        holder = TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        # Resolved, because git resolves: on macOS a temporary directory is handed out as
        # `/var/...`, a symlink to `/private/var/...`, and the cwd assertions compare paths.
        self.tmp = Path(holder.name).resolve()

        # The whole environment, not an override at the seam — see the module docstring.
        # `HOME` inside the temporary tree so no fixture and no child can reach the
        # developer's `~/.gitconfig` even if the config variables stop meaning what they do.
        self.env = fixture_env(HOME=str(self.tmp))
        patcher = mock.patch.dict(os.environ, self.env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.repo = self.tmp / "repo"
        self.checkout = self.tmp / "checkouts" / "slug"
        self.recorder = Recorder()

    # --- fixture git ------------------------------------------------------------------

    def git(self, *arguments: str, cwd: Path) -> proc.Result:
        """Run a fixture git command, failing the test if git itself failed."""
        result = proc.run(
            ["git", *IDENTITY, *arguments], cwd=cwd, timeout=TIMEOUT, env=self.env
        )
        self.assertTrue(
            result.ok, f"fixture `git {' '.join(arguments)}` failed: {result.stderr}"
        )
        return result

    def make_repo(self, branch: str = BRANCH) -> None:
        """A main repository with one commit on `main` and `branch` forked from it.

        HEAD stays on `main`: the working branch is never the checked-out one, which is the
        state a real dispatch guards for (`run.sh:940`) and the state `git fetch` into
        `refs/heads/<branch>` requires.
        """
        self.repo.mkdir(parents=True)
        self.git("init", "--quiet", "--initial-branch", "main", cwd=self.repo)
        self.commit("first", repo=self.repo)
        self.git("branch", branch, cwd=self.repo)
        self.git("remote", "add", resolve.ORIGIN, str(self.tmp / "origin.git"), cwd=self.repo)

    def commit(self, message: str, *, repo: Path) -> str:
        """An empty commit on whatever is checked out; returns the new tip."""
        self.git("commit", "--quiet", "--allow-empty", "--message", message, cwd=repo)
        return self.head(repo)

    def head(self, repo: Path) -> str:
        return self.git("rev-parse", "HEAD", cwd=repo).stdout.strip()

    def tip(self, repo: Path, branch: str = BRANCH) -> str:
        return self.git("rev-parse", f"refs/heads/{branch}", cwd=repo).stdout.strip()

    # --- the module under test --------------------------------------------------------

    def create(
        self, branch: str = BRANCH, *, remote_url: str = "git@example.invalid:x/y.git"
    ) -> None:
        checkout.create(
            repo_root=self.repo,
            checkout=self.checkout,
            branch=branch,
            remote_url=remote_url,
            run=self.recorder,
        )

    def salvage(self, branch: str = BRANCH) -> checkout.Salvage:
        return checkout.salvage(
            repo_root=self.repo,
            checkout=self.checkout,
            branch=branch,
            run=self.recorder,
        )


class CreateTest(CheckoutTest):
    """The `git clone` and the three config writes that follow it."""

    def test_the_clone_argv_is_pinned(self) -> None:
        """`--no-hardlinks` and `--branch <b>`, restated by hand.

        `--no-hardlinks` is a security flag, not a performance one: without it the checkout
        shares object files with the host repository by inode, and the agent owns this tree
        (run.sh:1196–1202, the 2026-07-20 audit residual). `--branch` is what makes the
        checkout start on the working branch rather than on the default one.
        """
        self.make_repo()
        self.create()
        clone_argv = self.recorder.argvs[0]
        self.assertEqual(
            clone_argv,
            (
                "git",
                "clone",
                "--quiet",
                "--no-hardlinks",
                "--branch",
                BRANCH,
                str(self.repo),
                str(self.checkout),
            ),
        )

    def test_the_clone_really_lands_on_the_working_branch(self) -> None:
        self.make_repo()
        self.create()
        self.assertEqual(checkout.read_branch(self.checkout), BRANCH)
        self.assertEqual(self.tip(self.checkout), self.tip(self.repo))

    def test_origin_is_repointed_at_the_real_remote(self) -> None:
        """`git clone` leaves `origin` on the host repository's path; the agent needs the
        real remote.

        Left alone, an in-container `git push` would push into the host repository over a
        path that does not exist inside the container. The URL is a parameter — a git
        question dispatch answers, never a default this module invents (F2 decision 9).
        """
        self.make_repo()
        self.create(remote_url="git@example.invalid:acme/widget.git")
        url = self.git("remote", "get-url", resolve.ORIGIN, cwd=self.checkout).stdout.strip()
        self.assertEqual(url, "git@example.invalid:acme/widget.git")

    def test_a_failing_clone_raises_rather_than_returning(self) -> None:
        """There is no useful half a checkout; the caller must not reach the container."""
        self.make_repo()
        with self.assertRaises(proc.ProcessError):
            self.create(branch="no-such-branch")

    def test_nothing_after_a_failing_clone_runs(self) -> None:
        self.make_repo()
        with self.assertRaises(proc.ProcessError):
            self.create(branch="no-such-branch")
        self.assertEqual(len(self.recorder.calls), 1)


class IdentityTest(CheckoutTest):
    """Whose name the agent's commits carry, and what happens when the dev has no identity."""

    def identity(self) -> tuple[str, str]:
        """What the checkout's own config file says, read without the fixture's `-c` flags.

        `IDENTITY` is passed as `-c` overrides, and command-line config outranks every file
        layer — so reading this through `self.git` would report the fixture's identity no
        matter what `create` wrote.
        """

        def local(setting: str) -> str:
            return proc.run(
                ["git", "-C", str(self.checkout), "config", "--local", setting],
                cwd=self.repo,
                timeout=TIMEOUT,
                env=self.env,
            ).stdout.strip()

        return local("user.name"), local("user.email")

    def test_the_dispatching_devs_identity_is_copied_into_the_checkout(self) -> None:
        """So the PR's commits attribute to the person who dispatched (run.sh:1206–1209)."""
        self.make_repo()
        self.git("config", "user.name", "Dispatching Dev", cwd=self.repo)
        self.git("config", "user.email", "dev@example.invalid", cwd=self.repo)
        self.create()
        self.assertEqual(self.identity(), ("Dispatching Dev", "dev@example.invalid"))

    def test_an_absent_email_falls_back_to_the_noreply_address(self) -> None:
        """A dev with no `user.email` anywhere still gets a committable checkout.

        The environment is `fixture_env`, whose two config layers are `/dev/null`, so the
        repository has no identity from any layer — which is what makes this the fallback
        branch rather than an accident of the machine.
        """
        self.make_repo()
        self.create()
        _, email = self.identity()
        self.assertEqual(email, checkout.FALLBACK_EMAIL)

    def test_an_absent_name_falls_back_to_the_operating_systems(self) -> None:
        """The pin's `id -un` link in the chain, answered without spawning `id`.

        Pinned by equality against a patched `getpass.getuser` rather than by "the name is
        not empty": a non-emptiness assertion passes for any constant, so it would hold just
        as well over a module that had stopped asking the operating system anything.
        """
        self.make_repo()
        with mock.patch.object(getpass, "getuser", return_value="octocat"):
            self.create()
        name, _ = self.identity()
        self.assertEqual(name, "octocat")

    def test_a_machine_that_cannot_name_its_user_still_yields_a_committable_checkout(
        self,
    ) -> None:
        """The last link, which the pin does not have: `id -un` failing writes an empty
        `user.name` upstream, and an empty name is a checkout the agent cannot commit in.

        `OSError` is what `getpass.getuser` raises with no password-database entry for the
        uid — a container running as an unmapped uid, which is not hypothetical here.
        """
        self.make_repo()
        with mock.patch.object(getpass, "getuser", side_effect=OSError("no login name")):
            self.create()
        name, _ = self.identity()
        self.assertEqual(name, checkout.FALLBACK_NAME)

    def test_an_empty_configured_identity_is_treated_as_absent(self) -> None:
        """`git config user.name ''` exits 0 with empty output; the pin's `||` never fires.

        Ported behaviour would write an empty name and leave the agent unable to commit at
        all. A superset of the pin, and the one place this module is deliberately stricter.
        """
        self.make_repo()
        self.git("config", "user.email", "", cwd=self.repo)
        self.create()
        _, email = self.identity()
        self.assertEqual(email, checkout.FALLBACK_EMAIL)

    def test_the_whole_argv_stream_is_pinned_in_the_pins_order(self) -> None:
        """Six calls, in run.sh:1204–1209's order — `git clone`, repoint, then
        read-then-write per setting. The reads are interleaved with the writes rather than
        batched, because that is what the pin's `$(git config user.name || id -un)`
        substitution does; a reordering here is a divergence, not a tidy-up.

        Pinned as a whole stream because the *last* call is the load-bearing part: git inside
        the checkout stops here, and every later caller in a run is forbidden it.
        """
        self.make_repo()
        self.git("config", "user.name", "Dispatching Dev", cwd=self.repo)
        self.git("config", "user.email", "dev@example.invalid", cwd=self.repo)
        self.create(remote_url="git@example.invalid:acme/widget.git")
        at = ("git", "-C", str(self.checkout))
        self.assertEqual(
            self.recorder.argvs,
            [
                (
                    "git",
                    "clone",
                    "--quiet",
                    "--no-hardlinks",
                    "--branch",
                    BRANCH,
                    str(self.repo),
                    str(self.checkout),
                ),
                (*at, "remote", "set-url", "origin", "git@example.invalid:acme/widget.git"),
                ("git", "config", "user.name"),
                (*at, "config", "user.name", "Dispatching Dev"),
                ("git", "config", "user.email"),
                (*at, "config", "user.email", "dev@example.invalid"),
            ],
        )


class ReadBranchTest(CheckoutTest):
    """`.git/HEAD` read as text. Never `git -C <checkout> symbolic-ref` (pin :499–504)."""

    def test_a_normal_checkout_reports_its_branch(self) -> None:
        self.make_repo()
        self.create()
        self.assertEqual(checkout.read_branch(self.checkout), BRANCH)

    def test_a_detached_head_reports_no_branch(self) -> None:
        """A raw SHA in `.git/HEAD`: no branch to salvage, and the caller must say so."""
        self.make_repo()
        self.create()
        self.git("checkout", "--quiet", "--detach", cwd=self.checkout)
        self.assertIsNone(checkout.read_branch(self.checkout))

    def test_reading_the_branch_spawns_nothing(self) -> None:
        """The whole point of the textual read: the checkout's `.git/config` is
        agent-controlled, so the host runs no git inside it — read-side included."""
        self.make_repo()
        self.create()
        self.recorder.calls.clear()
        with mock.patch.object(proc, "run", refuse_to_spawn):
            self.assertEqual(checkout.read_branch(self.checkout), BRANCH)
        self.assertEqual(self.recorder.calls, [])

    def test_read_branch_takes_no_runner(self) -> None:
        """Structural, not disciplinary: there is no seam through which it could spawn."""
        parameters = inspect.signature(checkout.read_branch).parameters
        self.assertNotIn("run", parameters)

    def test_a_branch_name_containing_a_slash_survives_the_prefix_strip(self) -> None:
        self.make_repo(branch="feature/nested")
        self.create(branch="feature/nested")
        self.assertEqual(checkout.read_branch(self.checkout), "feature/nested")

    def test_a_missing_checkout_reports_no_branch(self) -> None:
        self.assertIsNone(checkout.read_branch(self.tmp / "nothing-here"))

    def test_a_head_pointing_outside_refs_heads_reports_no_branch(self) -> None:
        """Only `refs/heads/` is a branch this module will salvage; anything else is not one.

        The pin's `sed` prints only lines matching `^ref: refs/heads/`, so a HEAD pointing
        at a remote-tracking ref falls into the same arm as a detached one.
        """
        self.make_repo()
        self.create()
        (self.checkout / ".git" / "HEAD").write_text("ref: refs/remotes/origin/main\n")
        self.assertIsNone(checkout.read_branch(self.checkout))

    def test_an_unreadable_head_reports_no_branch(self) -> None:
        """A directory where the file should be. Absorbed, like the pin's `2>/dev/null`."""
        self.make_repo()
        self.create()
        head = self.checkout / ".git" / "HEAD"
        head.unlink()
        head.mkdir()
        self.assertIsNone(checkout.read_branch(self.checkout))


class SalvageTest(CheckoutTest):
    """The fast-forward-only fetch, run from the main repository. ADR 0003's consolidation."""

    def test_the_refspec_carries_no_plus(self) -> None:
        """Restated by hand. A `+` here is a forced fetch that can rewrite a user-owned
        branch backwards when the agent rebased inside the checkout — the exact loss the
        FF-only rule exists to prevent (run.sh:1164–1168). Mutating
        `checkout.SALVAGE_REFSPEC` to `+refs/...` must fail this test."""
        self.assertEqual(checkout.SALVAGE_REFSPEC.format(branch=BRANCH), SALVAGE_REFSPEC)
        self.assertFalse(checkout.SALVAGE_REFSPEC.startswith("+"))

    def test_the_salvage_argv_is_pinned(self) -> None:
        self.make_repo()
        self.create()
        self.recorder.calls.clear()
        self.salvage()
        self.assertEqual(
            self.recorder.argvs,
            [("git", "fetch", "--quiet", str(self.checkout), SALVAGE_REFSPEC)],
        )
        self.assertFalse(any(part.startswith("+") for part in self.recorder.argvs[0]))

    def test_a_fast_forward_advances_the_branch_in_the_main_repo(self) -> None:
        self.make_repo()
        self.create()
        before = self.tip(self.repo)
        agent_tip = self.commit("agent work", repo=self.checkout)
        self.assertNotEqual(agent_tip, before)

        outcome = self.salvage()

        self.assertTrue(outcome.advanced)
        self.assertEqual(self.tip(self.repo), agent_tip)

    def test_a_rebase_inside_the_checkout_refuses_and_touches_neither_repo(self) -> None:
        """`git commit --amend` inside the checkout: the branch no longer descends from the
        main repository's tip, so the fetch is not a fast-forward and must be refused."""
        self.make_repo()
        self.create()
        before_main = self.tip(self.repo)
        self.git(
            "commit",
            "--quiet",
            "--amend",
            "--allow-empty",
            "--message",
            "rewritten",
            cwd=self.checkout,
        )
        before_checkout = self.tip(self.checkout)
        self.assertNotEqual(before_checkout, before_main)

        outcome = self.salvage()

        self.assertFalse(outcome.advanced)
        self.assertEqual(self.tip(self.repo), before_main)
        self.assertEqual(self.tip(self.checkout), before_checkout)

    def test_a_diverged_branch_refuses_and_touches_neither_repo(self) -> None:
        """Both sides moved on from the fork point — the other way a fetch stops being a
        fast-forward, and the one that does not involve the agent rewriting anything."""
        self.make_repo()
        self.create()
        agent_tip = self.commit("agent work", repo=self.checkout)

        # A commit on `work` in the main repo without checking it out: the dispatch guards
        # forbid the working branch being checked out there, so the fixture must not either.
        self.git("checkout", "--quiet", "-b", "scratch", BRANCH, cwd=self.repo)
        host_tip = self.commit("host work", repo=self.repo)
        self.git("branch", "--force", BRANCH, host_tip, cwd=self.repo)
        self.git("checkout", "--quiet", "main", cwd=self.repo)

        outcome = self.salvage()

        self.assertFalse(outcome.advanced)
        self.assertEqual(self.tip(self.repo), host_tip)
        self.assertEqual(self.tip(self.checkout), agent_tip)

    def test_a_refusal_carries_gits_own_result_for_the_host_log(self) -> None:
        """The caller says it loudly; this module does not print. The `Result` is what makes
        the caller able to quote git through `proc.quote` rather than inventing a reason."""
        self.make_repo()
        self.create()
        self.git(
            "commit",
            "--quiet",
            "--amend",
            "--allow-empty",
            "--message",
            "rewritten",
            cwd=self.checkout,
        )
        outcome = self.salvage()
        self.assertFalse(outcome.result.ok)
        self.assertEqual(outcome.result.argv[:3], ("git", "fetch", "--quiet"))

    def test_salvage_has_no_boolean(self) -> None:
        """`if salvage(...)` would read as "did it run" and mean "did it advance".

        The same exclusion `proc.Result` carries, and for the same reason.
        """
        self.assertIs(type(checkout.Salvage).__dict__.get("__bool__"), None)
        self.assertNotIn("__bool__", checkout.Salvage.__dict__)
        self.assertNotIn("__len__", checkout.Salvage.__dict__)


class NoGitInsideTest(CheckoutTest):
    """Tier 2's "no git inside the checkout" check, at the module that starts it."""

    def test_every_recorded_cwd_is_the_main_repository(self) -> None:
        """Every git this module runs is run *from* the main repository, whose config is
        bessemer's. A host-side git inside the checkout would execute the agent's
        `.git/hooks` and `core.sshCommand` with the dispatching dev's credentials
        (run.sh:1531–1537)."""
        self.make_repo()
        self.create()
        self.commit("agent work", repo=self.checkout)
        self.salvage()

        self.assertTrue(self.recorder.calls)
        for call in self.recorder.calls:
            with self.subTest(argv=call.argv):
                self.assertEqual(call.cwd, self.repo)

    def test_no_recorded_cwd_is_under_the_checkout(self) -> None:
        """Stated as its own assertion rather than as a corollary: the check that survives
        someone deciding a different cwd is fine is the one that names the forbidden tree."""
        self.make_repo()
        self.create()
        self.salvage()
        for call in self.recorder.calls:
            with self.subTest(argv=call.argv):
                self.assertIsNotNone(call.cwd)
                assert call.cwd is not None
                self.assertNotIn(self.checkout, [call.cwd, *call.cwd.parents])


class GitEnvTest(CheckoutTest):
    """The variables that would point git at a different repository never reach a child."""

    def test_the_redirecting_variables_are_stripped(self) -> None:
        """An exported `GIT_DIR` would make the salvage fetch write into some *other*
        repository — a security bypass, not a preference. One list, shared with
        `bessemer.resolve`, so the two cannot drift into disagreeing about which names
        redirect git."""
        recorded: list[Mapping[str, str] | None] = []

        def record_env(
            argv: Sequence[str],
            *,
            timeout: float,
            cwd: Path | None = None,
            env: Mapping[str, str] | None = None,
            stdin_text: str | None = None,
        ) -> proc.Result:
            recorded.append(env)
            return proc.run(argv, timeout=timeout, cwd=cwd, env=env, stdin_text=stdin_text)

        self.make_repo()
        os.environ["GIT_DIR"] = str(self.tmp / "elsewhere")
        checkout.create(
            repo_root=self.repo,
            checkout=self.checkout,
            branch=BRANCH,
            remote_url="git@example.invalid:x/y.git",
            run=record_env,
        )

        self.assertTrue(recorded)
        for env in recorded:
            self.assertIsNotNone(env)
            assert env is not None
            for name in resolve.REDIRECTING_VARIABLES:
                self.assertNotIn(name, env)


class RemoveTest(CheckoutTest):
    """`rm -rf` equivalent. Only ever called by an owner that salvaged, or decided not to."""

    def test_a_checkout_is_removed_whole(self) -> None:
        self.make_repo()
        self.create()
        checkout.remove(self.checkout)
        self.assertFalse(self.checkout.exists())

    def test_removing_what_is_not_there_is_not_an_error(self) -> None:
        """The pin's `rm -rf` tolerates it, and so must a `finally` that cannot know whether
        the checkout got as far as existing."""
        checkout.remove(self.tmp / "never-existed")

    def test_a_removal_that_fails_for_any_other_reason_is_not_swallowed(self) -> None:
        """A checkout that cannot be removed is a leak, and the caller is the only thing that
        can say so. `ignore_errors=True` would turn it into silence."""
        target = self.tmp / "a-file"
        target.write_text("not a directory")
        with self.assertRaises(OSError):
            checkout.remove(target)
        self.assertTrue(target.exists())

    def test_removal_spawns_nothing(self) -> None:
        """`shutil.rmtree`, never a spawned `rm -rf`.

        Worth its own test because `rm -rf` is what the pin says, and a reader porting the
        line literally would reach for the proc seam — which `tests/guard.py` would then have
        to allowlist `rm` for, weakening the guard for the whole suite.
        """
        self.make_repo()
        self.create()
        with mock.patch.object(proc, "run", refuse_to_spawn):
            checkout.remove(self.checkout)


class ModuleShapeTest(unittest.TestCase):
    """The interface ADR 0003 names, restated by hand."""

    INTERFACE: Final = ("create", "read_branch", "salvage", "remove")
    """The four operations. ADR 0003's table, in its order."""

    TYPES: Final = ("Salvage",)
    """Every public *type* the module defines. Listed separately and just as strictly.

    A fifth operation and a second return type are both "the interface grew"; a test that
    counted only functions would wave the second one through, and `Salvage` is exactly the
    public name such a filter would have to exclude to pass.
    """

    def defined_here(self, *, types: bool) -> tuple[str, ...]:
        """Public names this module defines itself, split into types and everything else.

        `__module__` filters out the imports — `proc`, `ORIGIN`, `git_env` and the standard
        library — so this reads the module's own surface rather than its namespace.
        """
        return tuple(
            name
            for name, value in vars(checkout).items()
            if not name.startswith("_")
            and callable(value)
            and getattr(value, "__module__", None) == checkout.__name__
            and isinstance(value, type) is types
        )

    def test_the_public_functions_are_the_ones_the_adr_names(self) -> None:
        """ADR 0003's table says "create / `read_branch` / `salvage` / remove", and adds
        "the interface listed is the whole of it". A fifth public function is a decision to
        take to the ADR, not a diff to wave through."""
        self.assertEqual(self.defined_here(types=False), self.INTERFACE)

    def test_the_public_types_are_the_ones_listed_here(self) -> None:
        self.assertEqual(self.defined_here(types=True), self.TYPES)

    def test_the_module_never_prints(self) -> None:
        """The loud non-fast-forward message is the caller's; this module returns a value.

        Asserted over the source rather than by capturing stdout, because the arm that would
        print is the one a test has the hardest time reaching.
        """
        source = Path(checkout.__file__).read_text()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotEqual(node.func.id, "print")
