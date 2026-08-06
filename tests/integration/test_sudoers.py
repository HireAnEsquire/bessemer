"""The one sudoers line, measured on the built image. Both facts ADR 0001 states.

ADR 0001, "setup hook: contract not convention", makes two claims about the grant that no
amount of reading the Dockerfile can settle, because both are about what *sudo* does with it:

> Measured on the built image: a different script is refused, and `BASH_ENV` is stripped by
> `env_reset` rather than sourced as root.

`tests/test_adapter.py` pins the grant's text character for character, and pins that the path it
names is outside the checkout. That is the half a text assertion can hold. The half it cannot is
that a grant reading exactly like that one actually refuses everything else — sudoers syntax is
not obvious, `NOPASSWD:` applies to what follows it, and a rule that parses is not a rule that
denies.

**Each denial is asserted beside a control that passes.** A refusal test with no control is
green when sudo is broken, when the image has no sudoers file at all, and when the script it
tried to run did not exist — three ways to "refuse" that would leave the boundary untested.

The command the control runs is `bessemer.container.SETUP_HOOK_COMMAND`, not a literal copied
into this file. sudo matches its argument list verbatim, so what has to be true is that *the
argv core sends* is the argv this image's grant accepts; two hand-written copies of that string
could agree with each other while disagreeing with the one dispatch uses.
"""

import os
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar, Final

import support

from bessemer import container

HOOK_MARKER: Final = "HOOK RAN"
OTHER_MARKER: Final = "OTHER SCRIPT RAN"
PWN_MARKER: Final = "BASH_ENV SOURCED"
"""Each probe script prints its marker and the UID that ran it.

The UID is what makes the `BASH_ENV` assertion say something: the outer shell sources it as the
agent either way, so "the marker is absent" would be false for the wrong reason. What must not
appear is the marker *as root*.
"""

PROBE_MOUNT: Final = "/probe"


class SudoersTest(unittest.TestCase):
    """One temporary directory of probe scripts, mounted read-only, one container per test."""

    _probe: ClassVar[tempfile.TemporaryDirectory[str]]
    probe: ClassVar[Path]

    @classmethod
    def setUpClass(cls) -> None:
        support.require_image()
        cls._probe = tempfile.TemporaryDirectory()
        probe = Path(cls._probe.name)
        # Stands in for the adapter's real hook at the granted path. The grant names a path,
        # not a file's contents, so a hook that prints and exits is the whole of what the
        # control needs — and `tests/integration/test_setup_hook.py` is where the real one runs.
        (probe / "hook.sh").write_text(f'echo "{HOOK_MARKER} uid=$(id -u)"\n', encoding="utf-8")
        (probe / "other.sh").write_text(
            f'echo "{OTHER_MARKER} uid=$(id -u)"\n', encoding="utf-8"
        )
        (probe / "pwn.sh").write_text(f'echo "{PWN_MARKER} uid=$(id -u)"\n', encoding="utf-8")
        cls.probe = probe

    @classmethod
    def tearDownClass(cls) -> None:
        cls._probe.cleanup()

    def shell(self, script: str, *, env: dict[str, str] | None = None) -> str:
        """Run `script` as the agent user in a throwaway container; return everything it said.

        `--cap-drop ALL` plus exactly `container.SUDO_CAPABILITIES`, which is what dispatch
        gives a container: the Dockerfile's own measurement is that sudo exits 1 with "unable to
        change to root gid" without them, so a control running with more capability than
        dispatch grants would prove something dispatch cannot do.

        stdout and stderr together, because sudo's refusals go to stderr and the markers go to
        stdout, and every assertion below is about the presence or absence of a line.
        """
        argv = ["run", "--rm", "--cap-drop", "ALL"]
        for capability in container.SUDO_CAPABILITIES:
            argv += ["--cap-add", capability]
        argv += ["-v", f"{self.probe}:{PROBE_MOUNT}:ro"]
        argv += ["-v", f"{self.probe / 'hook.sh'}:{container.HOOK_MOUNT}:ro"]
        for name, value in (env or {}).items():
            argv += ["-e", f"{name}={value}"]
        argv += ["--entrypoint", "/usr/bin/bash", support.IMAGE, "-c", script]
        result = support.docker(argv)
        return result.stdout + result.stderr

    def test_the_granted_command_runs_and_runs_as_root(self) -> None:
        """The control for both refusals below, and the contract dispatch depends on.

        `SETUP_HOOK_COMMAND` is the argv `container.setup_hook_argv` puts after `docker exec`.
        If this line ever fails, no dispatch against this image can get past its setup step.
        """
        said = self.shell(" ".join(container.SETUP_HOOK_COMMAND))
        self.assertIn(f"{HOOK_MARKER} uid=0", said)

    def test_a_different_script_is_refused(self) -> None:
        """The first measured fact. The grant names one path; anything else needs a password,
        and there is no password — the agent user has none and nothing is interactive."""
        said = self.shell(f"sudo /usr/bin/bash {PROBE_MOUNT}/other.sh")
        self.assertNotIn(OTHER_MARKER, said)

    def test_an_extra_flag_is_refused(self) -> None:
        """The same fact one level finer, and the reason ADR 0001 says dispatch must invoke the
        grant verbatim: sudo matches the whole argument list, so `-x` is denied, not ignored.

        This is what makes a reworded grant a broken dispatch rather than a style change.
        """
        said = self.shell(f"sudo /usr/bin/bash -x {container.HOOK_MOUNT}")
        self.assertNotIn(HOOK_MARKER, said)

    def test_bash_env_is_sourced_by_this_image_outside_sudo(self) -> None:
        """The control for the fact below: `BASH_ENV` is honoured here, so a later absence is
        `env_reset` doing its job and not bash ignoring a variable it never reads."""
        said = self.shell("true", env={"BASH_ENV": f"{PROBE_MOUNT}/pwn.sh"})
        self.assertIn(f"{PWN_MARKER} uid={os.getuid()}", said)

    def test_bash_env_does_not_cross_the_sudo_boundary(self) -> None:
        """The second measured fact, and the one that decides whether the grant is one script or
        arbitrary root: `sudo /usr/bin/bash <script>` starts a *bash*, and a bash that read
        `BASH_ENV` would source an agent-writable file as root before reaching the script.

        Asserted as "the marker is not there as root", not as "the marker is not there": the
        outer shell sources it as the agent in the same command, which is what the line above
        proves. Only the root spelling is the violation.
        """
        said = self.shell(
            " ".join(container.SETUP_HOOK_COMMAND), env={"BASH_ENV": f"{PROBE_MOUNT}/pwn.sh"}
        )
        self.assertIn(f"{HOOK_MARKER} uid=0", said)
        self.assertIn(f"{PWN_MARKER} uid={os.getuid()}", said)
        self.assertNotIn(f"{PWN_MARKER} uid=0", said)
