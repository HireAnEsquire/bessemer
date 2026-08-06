"""One whole dispatch against a real container, on the path that fails: the hook exits nonzero.

The scripted end-to-end failure path F3 README decision 2 puts in tier 3. Tier 2 already drives
this scenario through the recording double and asserts the argv stream; what it cannot assert is
that a *real* `docker run` of a *real* adapter image reaches the hook at all, that the hook's
nonzero exit arrives as a nonzero exit, and that the container and the checkout are gone
afterwards. Those are the four things below.

**The failure path rather than the happy path, and that is the whole design.** A happy-path
end-to-end test would need an LLM credential that can spend money, a remote to push to, and a
pull request nobody asked for; the tracer runbook (`docs/f3-tracer-runbook.md`) is where a human
does that once, deliberately, and reads the result. This one runs on a fake credential that is
never used, because the run aborts three steps before anything would authenticate.

What this test needs from the machine, beyond the daemon: `gh` on `PATH`. Dispatch's preflight
refuses without it, before the container exists. It is never invoked here — presence is the
whole question preflight asks.

The fixture is a repository of its own under a temporary directory: its own `.bessemer/`, its
own origin (a bare repository beside it, so `git fetch origin` reaches nothing over a network),
and its own gitignored `.env` holding a credential that is not one. Nothing in this file touches
the repository it lives in.
"""

import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from typing import Final

import support

from bessemer import config, container, dispatch, ledger, proc
from bessemer.outcome import Resolved

BRANCH: Final = "tracer-e2e"
"""The working branch the fixture dispatches. Not a protected name, and not the base."""

HOOK_MARKER: Final = "tracer: this hook is supposed to fail"
FAKE_CREDENTIAL: Final = "ANTHROPIC_API_KEY=not-a-real-key-and-never-reached"

FAILING_HOOK: Final = f"""#!/usr/bin/env bash
set -euo pipefail
echo "{HOOK_MARKER}"
exit 1
"""

CONFIG: Final = """source = "git+https://example.invalid/bessemer@0000000000000000000000000000000000000000"
base = "main"
image = "{image}"
"""
"""The fixture adapter, three keys.

`source` is never resolved by a dispatch — it is the pin an adopter installs the core from — so
an unreachable URL here is honest rather than convenient: nothing in this test may reach the
network for a git object, and a real URL would hide it if something did.
"""

SPEC: Final = """# A spec no agent ever reads

The run this spec belongs to aborts at the setup hook, two steps before the implement pass.
"""


def setUpModule() -> None:
    support.require_image()


class HookFailureTest(unittest.TestCase):
    """Dispatch a run whose setup hook exits 1, and inspect what is left behind."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.repo = root / "repo"
        self.origin = root / "origin.git"
        self.adapter = self.repo / config.ADAPTER_DIR
        (self.adapter / "specs").mkdir(parents=True)
        (self.adapter / "config.toml").write_text(
            CONFIG.format(image=support.IMAGE), encoding="utf-8"
        )
        (self.adapter / "specs" / "tracer.md").write_text(SPEC, encoding="utf-8")
        hook = self.adapter / container.HOOK_FILE
        hook.write_text(FAILING_HOOK, encoding="utf-8")
        hook.chmod(0o755)
        # In the adapter's gitignored secrets file rather than in the environment handed to
        # `dispatch`, because that is the channel `container.Credential.crosses` is about: a
        # credential only exported in the operator's shell makes preflight warn, and a warning
        # in the middle of this test's console output would be noise the run does not have.
        (self.adapter / container.SECRETS_FILE).write_text(
            FAKE_CREDENTIAL + "\n", encoding="utf-8"
        )

        self.git(["init", "--quiet", "--bare", "--initial-branch=main", str(self.origin)])
        self.git(["init", "--quiet", "--initial-branch=main", str(self.repo)])
        self.git(["config", "user.email", "tracer@example.invalid"])
        self.git(["config", "user.name", "Tracer"])
        self.git(["add", "--all"])
        self.git(["commit", "--quiet", "-m", "the fixture repository"])
        self.git(["remote", "add", "origin", str(self.origin)])
        self.git(["push", "--quiet", "origin", "main"])
        # The branch exists and is **not** checked out: dispatch refuses a branch the main
        # repository has out, because it fetches into it.
        self.git(["branch", BRANCH])

        self.slug = dispatch.slug_for(BRANCH)
        self.container = f"{dispatch.CONTAINER_PREFIX}{self.slug}"
        self.checkout = self.adapter / dispatch.CHECKOUTS_DIR / self.slug
        self.log = self.adapter / dispatch.LOGS_DIR / f"{self.slug}{dispatch.LOG_SUFFIX}"
        self.lock = self.adapter / dispatch.LOCKS_DIR / f"{self.slug}{dispatch.LOCK_SUFFIX}"
        self.console: list[str] = []

    def tearDown(self) -> None:
        # Before the temporary directory goes: a container outliving its checkout is exactly
        # the leak this test asserts against, and leaving one behind would put a row in the
        # human's own `bessemer status`.
        support.remove_container(self.container)
        self._tmp.cleanup()

    def git(self, arguments: list[str]) -> None:
        """A fixture git command, run from the fixture repository with no inherited `GIT_*`."""
        result = proc.run(
            ["git", *arguments],
            timeout=support.DOCKER_TIMEOUT_SECONDS,
            cwd=self.repo if self.repo.is_dir() else None,
            env=support.fixture_env(),
        )
        if not result.ok:
            raise support.TierThreeUnavailable(
                "fixture git failed: "
                + proc.quote(result, destination=proc.Destination.HOST_LOG)
            )

    def run_dispatch(self) -> dispatch.RunFailed:
        """Run the dispatch, assert it failed, and return the failure."""
        loaded = config.load(start=self.repo, env={})
        assert isinstance(loaded, Resolved), loaded
        with self.assertRaises(dispatch.RunFailed) as raised:
            dispatch.dispatch(
                cfg=loaded.value,
                repo_root=self.repo,
                start=self.repo,
                spec="tracer.md",
                branch=BRANCH,
                base=None,
                env={},
                pid=os.getpid(),
                console=self.console.append,
                run=proc.run,
                streamer=proc.streamed,
                sleep=time.sleep,
                clock=time.monotonic,
                now=datetime.now,
            )
        return raised.exception

    def test_the_hook_aborts_the_run_and_the_log_carries_its_output(self) -> None:
        """ADR 0001's contract, whole: a nonzero hook aborts the dispatch **and surfaces the
        log**. Both halves, because an abort with no output is a run nobody can debug.

        The exception's message names the mount path rather than the adapter's path — the hook
        that failed is the one the container saw, at `/bessemer/setup.sh`.
        """
        failure = self.run_dispatch()
        self.assertIn(container.HOOK_MOUNT, str(failure))
        self.assertIn("exited 1", str(failure))
        log = self.log.read_text(encoding="utf-8")
        self.assertIn(HOOK_MARKER, log)
        self.assertIn(dispatch.REFUSAL_PREFIX, log)

    def test_it_leaves_no_container_and_no_checkout(self) -> None:
        """The `finally` block's whole job, against a real daemon: salvage, remove, unlock.

        A container is asserted absent through `docker ps -a`, not `docker ps`: an exited
        container is still an orphan holding a writable layer, and it is what `bessemer gc`
        lists. The lock goes too — a lock file outliving its run is a branch nobody can
        dispatch again without deleting a file by hand.
        """
        self.run_dispatch()
        self.assertFalse(support.container_exists(self.container), self.container)
        self.assertFalse(self.checkout.exists(), self.checkout)
        self.assertFalse(self.lock.exists(), self.lock)

    def test_a_hard_failure_appends_no_ledger_line(self) -> None:
        """F3 decision 6.4: the ledger records landings, and the append is the run's last act.

        Asserted here rather than only in tier 2 because the consequence is F4's — `--resume`
        cannot recover a run that never landed — and a file that quietly came to exist would
        make that a different feature.
        """
        self.run_dispatch()
        self.assertFalse(
            ledger.central_ledger_path(self.adapter / "specs").exists(),
            "a run that aborted at the hook wrote a ledger line",
        )
