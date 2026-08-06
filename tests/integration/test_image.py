"""The build refusals and the user the image ends as — asked of docker, not of the text.

`tests/test_adapter.py` reads `.bessemer/Dockerfile` and pins the three lines the container
boundary consists of. Its own docstring names the limit: it "cannot catch a base image that
ships a surprise", and it never builds anything, because the unit suite must pass with the
daemon stopped. Every assertion here is the same claim put to the builder.

The `AGENT_UID=0` refusal is the one F1 issue 07 could only write down. Its docstring lists the
build as a dev-machine check with the output it printed once, on one machine, on the day it was
written — which is a measurement, not a gate. This module is where it becomes a gate, and F3
README decision 2 lists it in tier 3's contents for that reason: F1-07 owns the property, tier 3
owns running it.
"""

import os
import unittest
from typing import Final

import support

CONTRACT_PROGRAMS: Final = ("/usr/bin/bash", "timeout", "git", "claude")
"""F3 README decision 7.4's adapter-image contract, restated by hand. **Core's list.**

Every adapter image must carry these, whatever its stack, so they are asserted here as the
contract rather than as this image's inventory.
"""

ADAPTER_PROGRAMS: Final = ("make",)
"""What *this* repository's VERIFY step needs from its own image — a different list, differently
owned.

`make check` is what both of bessemer's prompt overrides tell the agent to run, and `make` is no
part of core's contract: an adopter whose checks are `npm test` needs none of it.

Kept apart from the contract above rather than appended to it, because the two fail differently.
A missing contract program is a bug in somebody's adapter image; a missing one of these is a bug
in *this* adapter, and is measured history — the image shipped without `make`, and the gap would
have surfaced as a dogfood run failing after a whole implement pass had been paid for.

The other half of the verify step, `uv`, is not here: it is not in the image, it is what the
setup hook installs, and `tests/integration/test_setup_hook.py` is where that is asserted.
"""


def setUpModule() -> None:
    support.require_image()


class BuildRefusalTest(unittest.TestCase):
    """`--build-arg AGENT_UID=<x>` for the three values of x that must not build an image.

    No tag on any of them (`support.build_argv` leaves it off): a refusal that named a tag would
    be a refusal one `docker build` away from having produced something.

    These are cheap despite being builds — the guard is the first `RUN` after `apt-get`, and
    every layer before it is the one `require_image` has just built and cached.
    """

    def refuse(self, uid: str) -> str:
        """Build with that UID, assert it failed, and return what docker said."""
        built = support.docker(
            support.build_argv(uid=uid), timeout=support.BUILD_TIMEOUT_SECONDS
        )
        self.assertNotEqual(built.returncode, 0, built.stdout + built.stderr)
        return built.stdout + built.stderr

    def test_uid_zero_is_refused(self) -> None:
        """The refusal ADR 0001 requires: `useradd -o -u 0` names the existing root UID `agent`,
        and every later `USER agent` then runs as root with nothing printed anywhere.

        Dispatching from a root shell is ordinary in CI, so this is the container boundary
        dissolving on the machine least likely to be watched.
        """
        self.assertIn("refusing to build", self.refuse("0"))

    def test_zero_spelled_with_two_digits_is_refused(self) -> None:
        """`00` is digits, is zero, and would sail past a `case … 0)` match — the case the
        Dockerfile's comment names as the one the digits-then-arithmetic order exists for."""
        self.assertIn("refusing to build", self.refuse("00"))

    def test_a_non_numeric_uid_is_refused_before_any_arithmetic(self) -> None:
        """`test -eq` on a word is a shell error, and an error inside a `||` branch is the shape
        that exits 0. The digits check has to come first, and this is what says it did."""
        self.assertIn("must be a decimal integer", self.refuse("agent"))


class BuiltImageTest(unittest.TestCase):
    """What the image built by `require_image` actually runs as."""

    def test_the_container_runs_as_the_host_uid_and_not_as_root(self) -> None:
        """`USER agent` in the file, and the build argument reaching `useradd`, are two text
        assertions in `tests/test_adapter.py`. This is the property they are for: the process
        that gets the bind-mounted checkout is the person who dispatched, and is not root.
        """
        identity = support.docker(["run", "--rm", "--entrypoint", "id", support.IMAGE, "-u"])
        self.assertEqual(identity.returncode, 0, identity.stderr)
        self.assertEqual(identity.stdout.strip(), str(os.getuid()))
        self.assertNotEqual(identity.stdout.strip(), "0")

    def test_the_image_carries_the_programs_the_contract_names(self) -> None:
        """F3 README decision 7.4's adapter-image contract, as far as an image can answer it.

        `bash` at exactly `/usr/bin/bash` because the sudoers grant is that string; `timeout`
        because a pass is `timeout <n> claude …` in-container and a missing one is rc 127 on
        the first pass; `git` because the agent commits inside the container; `claude` because
        it is the agent. A missing one of these surfaces today as a pass failing twenty minutes
        into a run, which is the least readable place for it.
        """
        for program in CONTRACT_PROGRAMS:
            with self.subTest(program=program):
                self.assertTrue(self.carries(program), program)

    def test_the_image_carries_what_this_repository_verifies_with(self) -> None:
        """`make check` is bessemer's VERIFY step, so `make` is this adapter's own requirement.

        See `ADAPTER_PROGRAMS` for why it is asserted apart from the contract above, and for the
        run that would have caught the image shipping without it.
        """
        for program in ADAPTER_PROGRAMS:
            with self.subTest(program=program):
                self.assertTrue(self.carries(program), program)

    def carries(self, program: str) -> bool:
        """Whether the image has `program`, asked as a pass would find it: through `PATH`."""
        found = support.docker(
            [
                "run",
                "--rm",
                "--entrypoint",
                "/usr/bin/bash",
                support.IMAGE,
                "-lc",
                f"command -v {program}",
            ]
        )
        return found.ok
