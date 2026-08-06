"""Bessemer's own setup hook, run the way dispatch runs it, in a real container.

This is the issue-12 blocker made into a gate. Bessemer's override prompts tell the agent that
VERIFY is `make check`, `make check` runs through `uv`, and `.bessemer/Dockerfile` installs no
uv — so until the hook installed it, the first dogfood dispatch failed at the verify step, after
paying for an implement pass. The hook now installs uv, and the claim that has to hold is not
"the script exits 0" but **"the agent user can run uv afterwards"**: the hook runs as root
through the sudoers grant, and an installer's default of `~/.local/bin` would put the binary in
root's home, where the agent never looks.

Driven through `bessemer.container` rather than through hand-written `docker` argv, because the
question is about the hook as *dispatch* runs it: `container.start` decides the capabilities and
the mounts, and `container.run_setup_hook` decides the command. A test that spelled those out
itself could pass against an image no dispatch can use.

**The hook is copied into a temporary adapter directory rather than mounted from `.bessemer/`.**
Two reasons, both about what else lives in that directory: `container.forwarding` reads
`.bessemer/.env`, so pointing at the real adapter would forward the developer's own LLM
credential into a test container, and `run_argv` refuses an adapter directory that is also the
checkout. The file under test is still the committed one — it is copied, not written.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar, Final

import support

from bessemer import container, proc

CONTAINER: Final = "tracer-setup-hook"
"""Deliberately **not** prefixed `bessemer-`.

`bessemer status` and `bessemer gc` find a run's container by the `bessemer-<slug>` name, so a
test container carrying that prefix would appear in the human's own status table, and would be
listed as reclaimable the moment this test crashed before its cleanup. Tier 3 must not put a
row in front of the person reading the tracer's evidence.
"""

EXEC_TIMEOUT_SECONDS: Final = 120.0


class SetupHookTest(unittest.TestCase):
    """One container, started once for the class: the hook is idempotent, so both runs count."""

    _tmp: ClassVar[tempfile.TemporaryDirectory[str]]
    adapter: ClassVar[Path]
    checkout: ClassVar[Path]
    boundary: ClassVar[container.Boundary]
    first: ClassVar[proc.Result]

    @classmethod
    def setUpClass(cls) -> None:
        support.require_image()
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.adapter = root / "adapter"
        cls.adapter.mkdir()
        shutil.copy2(support.ADAPTER_DIR / container.HOOK_FILE, cls.adapter)
        cls.checkout = root / "checkout"
        cls.checkout.mkdir()
        spec = root / "spec.md"
        spec.write_text("# a spec the hook never reads\n", encoding="utf-8")

        boundary = container.Boundary(
            cap_add=(),
            volumes=(),
            pids_limit=2048,
            memory="8g",
            env=container.forwarding(adapter_dir=cls.adapter, declared=()),
        )
        support.remove_container(CONTAINER)
        container.start(
            container=CONTAINER,
            image=support.IMAGE,
            checkout=cls.checkout,
            adapter_dir=cls.adapter,
            spec=spec,
            boundary=boundary,
            run=proc.run,
        )
        cls.boundary = boundary
        cls.first = container.run_setup_hook(
            container=CONTAINER, boundary=boundary, run=proc.run
        )

    @classmethod
    def tearDownClass(cls) -> None:
        support.remove_container(CONTAINER)
        cls._tmp.cleanup()

    def agent_shell(self, script: str) -> proc.Result:
        """Run `script` in the live container as the image's own user — the agent.

        No `-u`, deliberately: `USER agent` is what decides who a `docker exec` runs as, and
        naming the user here would test a path dispatch does not take.
        """
        return proc.run(
            ["docker", "exec", CONTAINER, "/usr/bin/bash", "-lc", script],
            timeout=EXEC_TIMEOUT_SECONDS,
        )

    def test_the_hook_exits_zero(self) -> None:
        """The contract's own line: a nonzero exit aborts the dispatch. `run_setup_hook` raises
        `SetupHookError` on nonzero, so `setUpClass` would have failed the class — this asserts
        the returncode anyway, because a hook that exits 0 having done nothing is the other
        failure and the tests below are what tell them apart."""
        self.assertEqual(self.first.returncode, 0, self.first.stdout)

    def test_the_agent_user_can_run_uv_afterwards(self) -> None:
        """The claim the whole hook exists for, asked of the user who will run `make check`."""
        found = self.agent_shell("uv --version")
        self.assertEqual(found.returncode, 0, found.stdout + found.stderr)
        self.assertIn("uv ", found.stdout)

    def test_uv_landed_where_both_users_can_reach_it(self) -> None:
        """`/usr/local/bin` is on sudo's `secure_path` and on the agent's `PATH`, which is why
        the hook names it. A uv under `/root` or under `/home/agent/.local` would satisfy the
        test above only by accident of who ran it."""
        where = self.agent_shell("command -v uv")
        self.assertEqual(where.stdout.strip(), "/usr/local/bin/uv", where.stdout)

    def test_running_it_again_installs_nothing_and_still_exits_zero(self) -> None:
        """Idempotence is a contract clause, not a nicety: the agent may legally re-run the hook
        mid-run to revive something that died, and that is what the sudoers grant is for.

        The second run is asserted to have taken the already-installed branch rather than merely
        to have exited 0 — a hook that reinstalled uv every time would pass on exit status while
        spending a network round trip on each call.
        """
        again = container.run_setup_hook(
            container=CONTAINER, boundary=self.boundary, run=proc.run
        )
        self.assertEqual(again.returncode, 0, again.stdout)
        self.assertIn("already installed", again.stdout)
