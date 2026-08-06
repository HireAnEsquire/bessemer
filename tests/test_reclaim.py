"""Tests for reclaim: the plan executed, and everything it refuses to touch.

`bessemer/reclaim.py` is the one module that deletes, so these tests are written the way the
deletions are dangerous. Three shapes recur:

- **The scripted `Double` at the proc seam** records argv, timeout, cwd and environment.
  `docker` is not on `tests/guard.py`'s allowlist, so it is not a convenience — it is the
  only way these paths run at all (ADR 0003's tier-2 rider 2). The re-check that precedes
  every item is a `docker ps`, so the recorded stream is where "nothing was touched" is
  provable rather than asserted.
- **Real directories and real files.** A checkout that was removed is a directory that is
  gone; a lock that survived is a file still on disk. Nothing here mocks a filesystem, so a
  deleter that deleted the wrong path fails on the path rather than on a call count.
- **Real temporary git repositories** for the two questions that are git's — a salvage that
  fast-forwards and one that cannot (F3 decision 2's tier-2 rider 5). `git` *is* on the
  allowlist; a scripted answer would prove that `reclaim` believes what it is told about a
  fast-forward, which is the one thing that must not be taken on trust before an `rm -rf`.

**The absence assertions are the point of the file**, and each is over the recorded stream
plus the tree: the logs directory is named by no argv and no surviving-file check ever finds
it changed (ADR 0001 — gc never deletes logs); a docker-down `--force` records no argv at
all; and an item that went live between the scan and the delete leaves its checkout, its lock
and its container exactly where they were.
"""

import inspect
import os
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Final
from unittest import mock

from bessemer import proc, reclaim
from tests.gitenv import fixture_env

TIMEOUT: Final = 60.0
"""For the fixtures' own git commands. Generous: a backstop, not the thing under test."""

IDENTITY: Final = (
    "-c",
    "user.email=tests@bessemer.invalid",
    "-c",
    "user.name=bessemer tests",
    "-c",
    "commit.gpgsign=false",
)
"""Passed to every *fixture* git command, so no fixture depends on the developer's git.
`tests/test_checkout.py`'s, restated: reclaim writes no identity anywhere, so there is no
counterpart here to that file's `IdentityTest` and nothing this may collide with."""

LIVENESS_ARGV: Final = ["docker", "ps", "-q", "-f", "name=^bessemer-{slug}$"]
"""The anchored re-check, hand-written (ADR 0003's tier-2 rider 1). The `^`/`$` are the whole
of it: unanchored, `docker ps -f name=bessemer-fix` matches `bessemer-fix-2` and reclaim would
read a live neighbour's container as the dead one it is about to remove."""

REMOVE_ARGV: Final = ["docker", "rm", "-f", "-v", "bessemer-{slug}"]
"""The pin's `docker rm -fv`, spelled out."""

LIVE: Final = "c0ffeec0ffee\n"
"""What `docker ps -q` prints when the container is there: an id. Empty output means gone."""


def said(stdout: str = "", *, returncode: int = 0, stderr: str = "") -> proc.Result:
    """A scripted answer. Argv is filled in by the double."""
    return proc.Result(argv=("scripted",), returncode=returncode, stdout=stdout, stderr=stderr)


@dataclass(frozen=True)
class Call:
    """One recorded call: what ran, with what deadline, from where, and with what environment.

    `cwd` is recorded because "no git argv ever runs inside a checkout" cannot be checked
    without it (ADR 0003's tier-2 rider 2), and reclaim is a caller of `checkout.salvage`.
    """

    argv: tuple[str, ...]
    timeout: float
    cwd: Path | None
    env: Mapping[str, str] | None


class Double:
    """A `proc.Runner` that records every call and answers from a script. Never spawns.

    **Named limit, in the house style of `tests/guard.py`: running dry answers success with
    empty output.** For reclaim that is the *safe* direction on the probe — an unscripted
    `docker ps` says "no container", which is what makes a plain orphan reclaimable — and so
    every test that needs an item kept scripts the liveness answer that keeps it.
    """

    def __init__(self, *results: proc.Result) -> None:
        self.calls: list[Call] = []
        self.scripted = list(results)

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stdin_text: str | None = None,
    ) -> proc.Result:
        self.calls.append(Call(tuple(argv), timeout, cwd, env))
        if self.scripted:
            scripted = self.scripted.pop(0)
            return proc.Result(
                argv=tuple(argv),
                returncode=scripted.returncode,
                stdout=scripted.stdout,
                stderr=scripted.stderr,
            )
        return proc.Result(argv=tuple(argv), returncode=0, stdout="", stderr="")

    @property
    def argvs(self) -> list[list[str]]:
        return [list(call.argv) for call in self.calls]

    @property
    def words(self) -> list[str]:
        """Every argument of every recorded call, flattened. For the absence assertions."""
        return [word for call in self.calls for word in call.argv]


class Passthrough(Double):
    """A `Double` that really runs the `git` calls and answers the `docker` ones itself.

    For the tests whose subject is git's behaviour: `git` is on `tests/guard.py`'s allowlist
    and `docker` is deliberately not, so a real salvage and a scripted liveness probe have to
    share one seam. Everything is still recorded, which is what keeps the "no git argv inside
    the checkout" assertion available on the same runs that exercise the real fetch.
    """

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stdin_text: str | None = None,
    ) -> proc.Result:
        if argv[0] == "docker":
            return super().__call__(argv, timeout=timeout, cwd=cwd, env=env)
        self.calls.append(Call(tuple(argv), timeout, cwd, env))
        return proc.run(argv, timeout=timeout, cwd=cwd, env=env, stdin_text=stdin_text)


class Raising:
    """A `proc.Runner` that raises whatever it was built with, for the two cases `proc.run`
    deliberately does not flatten into a `Result`: a docker that cannot be executed at all,
    and one that hung past its deadline."""

    def __init__(self, error: BaseException) -> None:
        self.error = error

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stdin_text: str | None = None,
    ) -> proc.Result:
        raise self.error


class ReclaimTestCase(unittest.TestCase):
    """A tree shaped like an adapter directory: checkouts, locks and logs beside each other."""

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name).resolve()
        self.repo_root = self.root / "repo"
        self.checkouts_dir = self.root / "checkouts"
        self.locks_dir = self.root / "locks"
        self.logs_dir = self.root / "logs"
        for directory in (self.repo_root, self.checkouts_dir, self.locks_dir, self.logs_dir):
            directory.mkdir()
        self.emitted: list[str] = []

    def checkout(self, slug: str, *, head: str = "ref: refs/heads/agent/work\n") -> Path:
        """A checkout-shaped directory: a tree with a `.git/HEAD` naming a branch.

        `read_branch` reads that file as text and nothing else, so this is a whole checkout as
        far as the arms under test are concerned — the tests whose subject is the *fetch* use
        real repositories in `CheckoutTest` instead.
        """
        path = self.checkouts_dir / slug
        (path / ".git").mkdir(parents=True)
        (path / ".git" / "HEAD").write_text(head)
        return path

    def lock(self, slug: str, pid: str = "999999") -> Path:
        path = self.locks_dir / f"{slug}.pid"
        path.write_text(pid)
        return path

    def log(self, slug: str) -> Path:
        path = self.logs_dir / f"{slug}.log"
        path.write_text("a run happened here")
        return path

    def execute(
        self, plan: str, double: Double, *, docker_down: bool = False
    ) -> reclaim.ReclaimReport:
        """`execute_gc_plan` with the seam on `double`.

        `run` is bound to a `proc.Runner`-annotated name rather than passed inline, so a
        `Double` that stopped matching the protocol is a type error here rather than a
        runtime surprise inside the module.
        """
        runner: proc.Runner = double
        return reclaim.execute_gc_plan(
            plan,
            checkouts_dir=self.checkouts_dir,
            locks_dir=self.locks_dir,
            repo_root=self.repo_root,
            docker_down=docker_down,
            emit=self.emitted.append,
            run=runner,
        )

    @property
    def said(self) -> str:
        return "\n".join(self.emitted)


class DockerDownTest(ReclaimTestCase):
    """`--force` refuses entirely when liveness cannot be verified (pin :453–456).

    The refusal lives in the executor rather than in the CLI that also asks the question:
    "nothing is deleted when liveness is unverifiable" is a property of the thing that
    deletes, not a rule a caller remembers.
    """

    def test_a_down_daemon_refuses_before_a_single_argv_is_built(self) -> None:
        self.checkout("leaked")
        self.lock("leaked")
        double = Double()

        report = self.execute("checkout\tleaked\nlock\tleaked", double, docker_down=True)

        self.assertTrue(report.refused)
        self.assertEqual(double.argvs, [])
        self.assertEqual(report.outcomes, ())

    def test_a_refused_run_leaves_every_artifact_where_it_was(self) -> None:
        checkout = self.checkout("leaked")
        lock = self.lock("leaked")

        self.execute("checkout\tleaked\nlock\tleaked", Double(), docker_down=True)

        self.assertTrue(checkout.is_dir())
        self.assertTrue(lock.is_file())

    def test_the_refusal_sentence_is_the_pins(self) -> None:
        """Hand-written whole: it is the one line an operator sees instead of a reclaim, and
        it has to say *why* nothing happened rather than that nothing happened."""
        self.assertEqual(
            reclaim.REFUSED_DOCKER_DOWN,
            "gc --force refused: docker is down, container liveness can't be verified"
            " — nothing deleted",
        )


class LivenessRecheckTest(ReclaimTestCase):
    """The plan is a scan-time snapshot, so every item is re-checked immediately before it is
    touched (pin :462–474). Two signals, both of them the scan's own."""

    def test_a_container_that_went_live_since_the_scan_is_skipped_untouched(self) -> None:
        checkout = self.checkout("racy")
        double = Double(said(LIVE))

        report = self.execute("checkout\tracy", double)

        self.assertEqual(double.argvs, [[word.format(slug="racy") for word in LIVENESS_ARGV]])
        self.assertTrue(checkout.is_dir())
        self.assertEqual([outcome.action for outcome in report.outcomes], [reclaim.Action.KEPT])
        self.assertIn("container is live now, not touching", self.said)

    def test_a_live_lock_pid_skips_the_item_untouched(self) -> None:
        """The clone-before-`docker run` and cleanup-after-`docker rm` windows: a dispatch
        owns this slug's artifacts even with no container anywhere (pin comment :471–474).

        This process's own pid is the live one — a real answer from `status.pid_alive`'s
        signal-0 probe rather than a patched predicate, because the probe is the check.
        """
        checkout = self.checkout("owned")
        self.lock("owned", pid=str(os.getpid()))
        double = Double(said(""))

        report = self.execute("checkout\towned", double)

        self.assertTrue(checkout.is_dir())
        self.assertEqual([outcome.action for outcome in report.outcomes], [reclaim.Action.KEPT])
        self.assertIn(f"a live run (pid {os.getpid()}) owns it now", self.said)

    def test_the_recheck_happens_before_every_item_not_once(self) -> None:
        """Three items, three probes — a re-check hoisted out of the loop would be a
        scan-time snapshot wearing a re-check's name."""
        self.checkout("a")
        self.checkout("b")
        self.lock("c")
        double = Double()

        self.execute("checkout\ta\ncheckout\tb\nlock\tc", double)

        self.assertEqual(
            [argv for argv in double.argvs if argv[:2] == ["docker", "ps"]],
            [[word.format(slug=slug) for word in LIVENESS_ARGV] for slug in ("a", "b", "c")],
        )

    def test_a_docker_that_cannot_answer_keeps_the_item(self) -> None:
        """**A named divergence from the pin, in the safe direction.** The pin reads a failed
        `docker ps` through command substitution, which yields the empty string — so a broken
        daemon reads as "no container" and the `rm -rf` proceeds. That is the same
        unverifiable liveness the docker-down arm refuses over, one item down; answering it
        differently in the two places would mean the whole-run refusal is a posture the
        per-item loop does not hold.
        """
        checkout = self.checkout("unanswerable")
        double = Double(said("", returncode=1, stderr="Cannot connect to the Docker daemon"))

        report = self.execute("checkout\tunanswerable", double)

        self.assertTrue(checkout.is_dir())
        self.assertEqual([outcome.action for outcome in report.outcomes], [reclaim.Action.KEPT])
        self.assertIn("liveness unverified", self.said)

    def test_a_docker_that_cannot_run_or_hangs_keeps_the_item(self) -> None:
        """The two cases `proc.run` does not flatten into a `Result`, absorbed here for the
        reason above and nowhere else: neither said a container is absent."""
        for index, error in enumerate(
            (OSError("no docker"), proc.TimeoutExpired(cmd=["docker"], timeout=1.0))
        ):
            with self.subTest(error=type(error).__name__):
                slug = f"wedged-{index}"
                checkout = self.checkout(slug)
                runner: proc.Runner = Raising(error)

                report = reclaim.execute_gc_plan(
                    f"checkout\t{slug}",
                    checkouts_dir=self.checkouts_dir,
                    locks_dir=self.locks_dir,
                    repo_root=self.repo_root,
                    docker_down=False,
                    emit=self.emitted.append,
                    run=runner,
                )

                self.assertTrue(checkout.is_dir())
                self.assertEqual(
                    [outcome.action for outcome in report.outcomes], [reclaim.Action.KEPT]
                )


class ContainerTest(ReclaimTestCase):
    def test_a_dead_container_is_removed_with_rm_fv(self) -> None:
        double = Double()

        report = self.execute("container\tstopped", double)

        self.assertEqual(
            double.argvs,
            [
                [word.format(slug="stopped") for word in LIVENESS_ARGV],
                [word.format(slug="stopped") for word in REMOVE_ARGV],
            ],
        )
        self.assertEqual(
            [outcome.action for outcome in report.outcomes], [reclaim.Action.RECLAIMED]
        )
        self.assertIn("removed container bessemer-stopped", self.said)

    def test_a_failed_removal_is_reported_and_the_run_continues(self) -> None:
        """Partial failure: the remaining items are still processed and the report is failed.

        A deleter that stopped at the first failure would leave a machine half-reclaimed and
        an operator with no way to tell which half, which is the state gc exists to end.
        """
        lock = self.lock("later")
        double = Double(said(""), said("", returncode=1, stderr="No such container"))

        report = self.execute("container\tbroken\nlock\tlater", double)

        self.assertIn("!! failed to remove container bessemer-broken", self.said)
        self.assertEqual(
            [outcome.action for outcome in report.outcomes],
            [reclaim.Action.FAILED, reclaim.Action.RECLAIMED],
        )
        self.assertTrue(report.failed)
        self.assertFalse(lock.exists())


class LockTest(ReclaimTestCase):
    def test_a_stale_lock_file_is_removed(self) -> None:
        lock = self.lock("crashed")

        report = self.execute("lock\tcrashed", Double())

        self.assertFalse(lock.exists())
        self.assertEqual(
            [outcome.action for outcome in report.outcomes], [reclaim.Action.RECLAIMED]
        )
        self.assertIn("removed stale lock crashed", self.said)

    def test_a_lock_that_is_already_gone_is_not_a_failure(self) -> None:
        """The pin's `rm -f`: gc raced another gc, or the dispatch that owned it finished
        cleanly between the scan and now. Nothing to do is not something to report failed."""
        report = self.execute("lock\tvanished", Double())

        self.assertFalse(report.failed)


class CheckoutTest(ReclaimTestCase):
    """The class with unpushed work in it. Every arm here is "keep unless salvage succeeded".

    Real git repositories, because the question is git's (F3 decision 2's tier-2 rider 5): a
    scripted fetch would prove that reclaim believes what it is told about a fast-forward,
    and believing that wrongly is an `rm -rf` over work that exists nowhere else.
    """

    BRANCH: Final = "agent/work"
    """The working branch. A slash in it, because that is what a dispatched branch looks like
    and it is the shape a `refs/heads/` prefix strip can get wrong."""

    DEFAULT_BRANCH: Final = "main"
    """What the main repository has checked out — never the working branch.

    Not decoration: `git fetch <url> refs/heads/X:refs/heads/X` refuses to write a branch that
    is checked out, and a real dispatch guards for the same state (run.sh:940). A fixture that
    left HEAD on the working branch would make every salvage here fail for a reason that has
    nothing to do with fast-forwarding.
    """

    def setUp(self) -> None:
        super().setUp()
        # The whole environment, `tests/gitenv.py`'s way: no inherited `GIT_*`, both config
        # layers at /dev/null, `HOME` inside the temporary tree. Patched into `os.environ`
        # rather than passed at the seam, because `bessemer.checkout` builds its own child
        # environment with `resolve.git_env` and that is the thing under test here.
        self.env = fixture_env(HOME=str(self.root))
        patcher = mock.patch.dict(os.environ, self.env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.git("init", "--quiet", "--initial-branch", self.DEFAULT_BRANCH, cwd=self.repo_root)
        self.commit_in(self.repo_root, "seed")
        self.git("branch", self.BRANCH, cwd=self.repo_root)

    def git(self, *arguments: str, cwd: Path) -> proc.Result:
        """Run a fixture git command, failing the test if git itself failed.

        `IDENTITY` goes on every fixture command and on nothing the module under test runs:
        reclaim writes no identity anywhere, and a fixture that relied on the developer's
        would pass or fail by whose machine it ran on.
        """
        result = proc.run(
            ["git", *IDENTITY, *arguments], cwd=cwd, timeout=TIMEOUT, env=self.env
        )
        self.assertTrue(
            result.ok, f"fixture `git {' '.join(arguments)}` failed: {result.stderr}"
        )
        return result

    def cloned(self, slug: str) -> Path:
        """A checkout of the working branch, as `checkout.create` would leave one."""
        path = self.checkouts_dir / slug
        self.git(
            "clone",
            "--quiet",
            "--no-hardlinks",
            "--branch",
            self.BRANCH,
            str(self.repo_root),
            str(path),
            cwd=self.repo_root,
        )
        return path

    def commit_in(self, path: Path, message: str) -> None:
        (path / f"{message}.txt").write_text(f"{message}\n")
        self.git("add", "-A", cwd=path)
        self.git("commit", "--quiet", "-m", message, cwd=path)

    def head_of(self, branch: str) -> str:
        return self.git("rev-parse", branch, cwd=self.repo_root).stdout.strip()

    def test_a_fast_forwarding_checkout_is_salvaged_then_removed(self) -> None:
        checkout = self.cloned("done")
        self.commit_in(checkout, "agent-work")
        before = self.head_of(self.BRANCH)

        report = self.execute("checkout\tdone", Passthrough())

        self.assertNotEqual(self.head_of(self.BRANCH), before)
        self.assertFalse(checkout.exists())
        self.assertEqual(
            [outcome.action for outcome in report.outcomes], [reclaim.Action.RECLAIMED]
        )
        self.assertIn(
            f"salvaged + removed checkout done (branch '{self.BRANCH}' fast-forwarded)",
            self.said,
        )

    def test_a_diverged_checkout_is_kept_loudly_and_the_branch_is_untouched(self) -> None:
        """The one that matters most: a non-fast-forward means the agent rebased or reset
        inside the checkout, so an `rm -rf` here discards work that exists nowhere else.
        **gc never discards unpushed agent work.**"""
        checkout = self.cloned("diverged")
        self.commit_in(checkout, "agent-work")
        # The branch moves in the main repository too, onto a commit the checkout has never
        # seen: the agent rebased, or a human committed to the same branch. Moved with
        # `branch -f` rather than by checking it out, so HEAD stays where `DEFAULT_BRANCH`
        # says it must.
        self.commit_in(self.repo_root, "human-work")
        self.git("branch", "--force", self.BRANCH, "HEAD", cwd=self.repo_root)
        before = self.head_of(self.BRANCH)

        report = self.execute("checkout\tdiverged", Passthrough())

        self.assertEqual(self.head_of(self.BRANCH), before)
        self.assertTrue(checkout.is_dir())
        self.assertEqual([outcome.action for outcome in report.outcomes], [reclaim.Action.KEPT])
        self.assertIn("!! SKIPPING checkout diverged", self.said)
        self.assertIn("was not a fast-forward", self.said)
        self.assertIn(str(checkout), self.said)

    def test_a_detached_checkout_is_kept_loudly_and_no_fetch_is_attempted(self) -> None:
        """Detached HEAD holds a raw SHA, so there is no branch to salvage — and nothing to
        fetch. Asserted over the recorded stream as well as the tree, because "no salvage
        was attempted" is what says the branch arm was not reached with an empty name."""
        checkout = self.cloned("detached")
        self.git("checkout", "--quiet", "--detach", cwd=checkout)
        double = Passthrough()

        report = self.execute("checkout\tdetached", double)

        self.assertTrue(checkout.is_dir())
        self.assertNotIn("fetch", double.words)
        self.assertEqual([outcome.action for outcome in report.outcomes], [reclaim.Action.KEPT])
        self.assertIn("!! SKIPPING checkout detached", self.said)
        self.assertIn("detached HEAD, no branch to salvage", self.said)

    def test_a_checkout_that_is_already_gone_is_skipped_before_any_git(self) -> None:
        double = Double()

        report = self.execute("checkout\tvanished", double)

        self.assertNotIn("git", double.words)
        self.assertFalse(report.failed)
        self.assertIn("skip checkout/vanished — already gone", self.said)

    def test_the_salvage_fetch_never_runs_inside_the_checkout(self) -> None:
        """`bessemer.checkout`'s one rule, inherited and re-asserted at this caller: every
        git child's `cwd` is the main repository and the checkout is named as an argument.
        The checkout's `.git/config` and hooks are agent-controlled (ADR 0001)."""
        checkout = self.cloned("owned")
        self.commit_in(checkout, "agent-work")
        double = Passthrough()

        self.execute("checkout\towned", double)

        git_calls = [call for call in double.calls if call.argv[0] == "git"]
        self.assertTrue(git_calls)
        for call in git_calls:
            with self.subTest(argv=call.argv):
                self.assertEqual(call.cwd, self.repo_root)


class LogsUntouchedTest(ReclaimTestCase):
    """ADR 0001's invariant: gc never deletes logs. Asserted as an absence, twice over."""

    def test_no_argv_and_no_deletion_ever_names_the_logs_directory(self) -> None:
        current = self.log("finished")
        rotated = self.logs_dir / "finished.log.1"
        rotated.write_text("rotated")
        self.checkout("leaked")
        self.lock("leaked")
        double = Double()

        self.execute("container\tleaked\ncheckout\tleaked\nlock\tleaked", double)

        self.assertNotIn(str(self.logs_dir), double.words)
        self.assertEqual(
            sorted(p.name for p in self.logs_dir.iterdir()),
            sorted([current.name, rotated.name]),
        )
        self.assertEqual(current.read_text(), "a run happened here")

    def test_no_directory_a_log_could_be_in_can_reach_this_module_at_all(self) -> None:
        """The structural half: `execute_gc_plan` takes three directories and none of them is
        the logs one, and the per-class actions are handed a value with no field for it. An
        arm cannot name a directory that reached it through nothing, so "gc never deletes
        logs" is a shape rather than a rule three functions remember."""
        parameters = set(inspect.signature(reclaim.execute_gc_plan).parameters)
        self.assertEqual(
            parameters,
            {"plan", "checkouts_dir", "locks_dir", "repo_root", "docker_down", "emit", "run"},
        )
        self.assertNotIn("logs_dir", {field.name for field in fields(reclaim._Item)})

    def test_a_log_class_in_the_plan_is_refused_rather_than_acted_on(self) -> None:
        """`gc.render_gc_plan` filters logs out, and `tests/test_gc.py` pins that filter
        alone. This is the second lock on the same door: a class this module does not know is
        never a path this module builds, so a plan that somehow named one deletes nothing."""
        current = self.log("finished")
        double = Double()

        report = self.execute("log (rotated)\tfinished", double)

        self.assertTrue(current.is_file())
        self.assertEqual(double.argvs, [])
        self.assertEqual([outcome.action for outcome in report.outcomes], [reclaim.Action.KEPT])
        self.assertIn("unusable plan line", self.said)


class PlanOrderTest(ReclaimTestCase):
    """A clean orphan set: all three classes reclaimed, in the order the plan names them."""

    def test_every_class_is_reclaimed_in_plan_order(self) -> None:
        checkout = self.checkout("leaked")
        lock = self.lock("leaked")
        double = Double()

        report = self.execute("container\tleaked\ncheckout\tleaked\nlock\tleaked", double)

        self.assertEqual(
            [(outcome.cls, outcome.action) for outcome in report.outcomes],
            [
                ("container", reclaim.Action.RECLAIMED),
                ("checkout", reclaim.Action.RECLAIMED),
                ("lock", reclaim.Action.RECLAIMED),
            ],
        )
        self.assertFalse(checkout.exists())
        self.assertFalse(lock.exists())
        self.assertFalse(report.failed)

    def test_a_blank_plan_does_nothing_at_all(self) -> None:
        double = Double()

        report = self.execute("", double)

        self.assertEqual(double.argvs, [])
        self.assertEqual(report.outcomes, ())
        self.assertFalse(report.failed)


class ModuleShapeTest(unittest.TestCase):
    """The interface ADR 0003 names, restated by hand — `tests/test_container.py`'s treatment
    of the module next door, and for the sharper reason: this is the module that deletes, so
    one more public function is a decision to take to the ADR rather than a diff to wave
    through."""

    INTERFACE: Final = ("execute_gc_plan",)
    TYPES: Final = ("Action", "Outcome", "ReclaimReport")
    CONSTANTS: Final = ("CONTAINER_PREFIX", "REFUSED_DOCKER_DOWN", "BANNER")
    """Every public string in the namespace, listed just as strictly.

    The last two are the module's own — operator-facing sentences the CLI renders, and a third
    would be a line nothing has decided the destination of. `CONTAINER_PREFIX` is
    `bessemer.status`', imported and visible in `vars` the way `tests/test_gc.py`'s plumbing
    names are; it is here so that this list is a true census rather than one with an
    unexplained exemption in it.
    """

    def defined_here(self, *, types: bool) -> tuple[str, ...]:
        return tuple(
            name
            for name, value in vars(reclaim).items()
            if not name.startswith("_")
            and callable(value)
            and getattr(value, "__module__", None) == reclaim.__name__
            and isinstance(value, type) is types
        )

    def test_the_public_functions_are_the_ones_the_adr_names(self) -> None:
        self.assertEqual(self.defined_here(types=False), self.INTERFACE)

    def test_the_public_types_are_the_ones_listed_here(self) -> None:
        self.assertEqual(self.defined_here(types=True), self.TYPES)

    def test_the_public_constants_are_the_two_sentences_the_cli_renders(self) -> None:
        constants = tuple(
            name
            for name, value in vars(reclaim).items()
            if not name.startswith("_") and isinstance(value, str) and name.isupper()
        )
        self.assertEqual(constants, self.CONSTANTS)

    def test_the_module_never_prints(self) -> None:
        """Every line is a value the caller renders, `bessemer.checkout`'s rule inherited: two
        callers exist for the sentences here — the CLI's stdout and an `Outcome` — and a
        `print` would be a third voice neither could suppress."""
        source = Path(reclaim.__file__ or "").read_text(encoding="utf-8")
        self.assertIn("def execute_gc_plan(", source)
        self.assertNotIn("print(", source)


class MalformedPlanTest(ReclaimTestCase):
    """A line `gc.render_gc_plan` cannot produce, refused rather than acted on."""

    def test_a_line_with_no_slug_reaches_no_argv_and_no_path(self) -> None:
        """Without this arm a separator-less line would reach `docker rm -fv bessemer-`, which
        is a real container name on a machine unlucky enough to have one."""
        double = Double()

        report = self.execute("container", double)

        self.assertEqual(double.argvs, [])
        self.assertEqual([outcome.action for outcome in report.outcomes], [reclaim.Action.KEPT])
        self.assertIn("unusable plan line", self.said)


class ReportTest(ReclaimTestCase):
    def test_the_report_has_no_truth_value_of_its_own(self) -> None:
        """`proc.Result`'s exclusion, inherited: `if report:` reads as "is there a report" to
        one reader and "did it work" to the next. `failed` is the whole decision and has to
        be asked for by name."""
        self.assertIsNone(vars(reclaim.ReclaimReport).get("__bool__"))
        self.assertIsNone(vars(reclaim.ReclaimReport).get("__len__"))

    def test_every_outcome_carries_the_sentence_that_was_emitted(self) -> None:
        """The report is what the CLI's exit code is computed from and the emitted lines are
        what a human reads; a report whose outcomes did not carry their own sentence would be
        two accounts of one run, free to disagree."""
        self.checkout("leaked")
        double = Double()

        report = self.execute("checkout\tleaked", double)

        self.assertEqual([reclaim.BANNER, *(o.line for o in report.outcomes)], self.emitted)

    def test_the_banner_promises_the_recheck_and_is_not_printed_when_nothing_is_walked(
        self,
    ) -> None:
        """It is emitted by the executor rather than by the CLI, and after the docker-down
        guard: a line promising "re-checking liveness immediately before each" is a lie on a
        run that refused before checking anything."""
        self.assertIn("re-checking liveness immediately before each", reclaim.BANNER)

        self.execute("checkout\tleaked", Double(), docker_down=True)

        self.assertEqual(self.emitted, [])


if __name__ == "__main__":
    unittest.main()
