"""Tests for the container: the argv literals, the env boundary, and the hook invocation.

**Every security assertion here is a hand-written literal** (F1's rule, at full force per
ADR 0003's tier-2 rider 1). The cap set is written out, not probed for `--cap-drop`; the
mount table is compared whole, not searched; the sudoers invocation is spelled character for
character in this file as well as in `bessemer.container`. A test that built its expectation
from the module would agree with a mutation of the module instead of catching one — and what
would be agreed to here is a container's privilege boundary.

**Absences are asserted alongside presences**, because the flags that are missing are as much
the contract as the flags that are there: no `-p`/`--publish`, no wholesale host environment,
no mount of the adapter directory, `:ro` on the two single-file mounts, and — the half that
needs saying twice — no capability in the argv that config did not put there.

Nothing here spawns and nothing needs docker. The argv builders are pure, so they are tested
as functions over values; the three effectful operations run against `Double`, a thin table
over the real `proc.Result` type at the proc seam (ADR 0003's tier-2 rider 2, which is also
why it records cwd and timeout rather than only argv).
"""

import ast
import dataclasses
import inspect
import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final
from unittest import mock

from bessemer import container, proc, redact

CHECKOUT_MOUNT: Final = "/workspace"
"""Where the checkout is mounted, restated by hand. The path `.bessemer/Dockerfile`'s comment
and every prompt's "the spec is /spec.md" sit next to."""

HOOK_MOUNT: Final = "/bessemer/setup.sh"
"""The read-only mount of the adapter's hook — and the path in the image's sudoers line."""

SPEC_MOUNT: Final = "/spec.md"

SUDOERS_INVOCATION: Final = ("sudo", "/usr/bin/bash", "/bessemer/setup.sh")
"""The hook invocation, restated by hand because sudo matches it verbatim.

The whole point of the literal is that it is written twice by two people —
`container.SETUP_HOOK_COMMAND` and this. An extra flag on either side is *denied* by sudo
rather than ignored, so a dispatch whose setup step grew `-x` fails with a message about the
hook rather than about the flag. `tests/test_adapter.py` closes the third corner: this string
against the grant the Dockerfile writes.
"""

SUDO_CAPABILITIES: Final = ("SETUID", "SETGID")
"""What core's own hook invocation needs, unconditionally. Restated by hand.

Measured on the built image (2026-08-05): with `--cap-drop ALL` and no cap-adds,
`sudo /usr/bin/bash /bessemer/setup.sh` exits 1 with `sudo: unable to change to root gid:
Operation not permitted`; with `SETUID` alone it exits 1 the same way; with both it exits 0.
Core invokes the hook on every dispatch, so it declares the cost on every dispatch — decision
5.3's coupling rule, applied to the second thing core's own argv consumes.
"""

CHOWN_CAPABILITIES: Final = ("CHOWN", "DAC_OVERRIDE", "FOWNER")
"""What core's own chown of a volume mountpoint needs. Restated by hand.

Upstream consumed exactly these three out of its six for the orchestrator's
`docker exec -u root chown` while its comment said the six were "only what setup-db.sh
needs" (run.sh:1246–1252). Conditional where the sudo pair is unconditional, because the chown
argv is only built when there is a mountpoint to chown.
"""

PIN_SIX: Final = ("CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID", "KILL")
"""The pin's six cap-adds, in the pin's order (run.sh:1251–1252).

Five of the six are core's, in two groups with two different reasons, and exactly one — `KILL`
— was ever hae's: its `setup-db.sh` signals a restarted postmaster.
`test_the_pin_six_decompose_into_cores_five_and_haes_one` is what says so, and
`test_core_never_emits_kill` is the absence beside it.
"""

CREDENTIAL_NAMES: Final = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
"""Bessemer's built-in credential names, restated by hand. The names that cross the boundary
without appearing in `container_env_keys` (ADR 0001), and the same literal doctor's
credential check imports (issue 09)."""

CONTAINER: Final = "bessemer-work"
IMAGE: Final = "bessemer-agent"

DECLARED_VALUE: Final = "postgres://user@db.invalid/app"
CREDENTIAL_VALUE: Final = "sk-ant-fixture-not-a-real-key"
WITHHELD_VALUE: Final = "s3cret-that-must-not-cross"
"""Three fixture values, and the third is the one every leak assertion hunts for.

Distinctive on purpose: `WITHHELD_VALUE` is searched for across the whole recorded argv
stream and the warning text, so a test that found it would name the channel it leaked
through rather than reporting a mismatched list.
"""


@dataclass(frozen=True)
class Call:
    """One recorded call at the proc seam: what was run, with what deadline, from where."""

    argv: tuple[str, ...]
    timeout: float
    cwd: Path | None
    env: Mapping[str, str] | None


class Double:
    """A `proc.Runner` that records every call and answers from a script. Never spawns.

    A thin table over the real `proc.Result`, not a parallel implementation of proc semantics
    (ADR 0003's tier-2 rider 2): it returns the `Result`s it was handed, in order, and a
    success once it runs out. `docker` is not on `tests/guard.py`'s allowlist, so a double is
    not a convenience here — it is the only way these paths can be exercised at all under the
    suite's no-daemon constraint.

    **Named limit, in the house style of `tests/guard.py`: running dry answers success.** A
    test that scripts one failing `Result` and provokes two calls gets a success for the
    second, silently — the "passes for the wrong reason" shape `tests/README.md` warns about.
    It is kept because the alternative makes every happy-path test script a result it does not
    care about, and it is closed where it could bite: every test that scripts a failure asserts
    the call count as well, which is what says the run stopped where it was supposed to.
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
        # Accepted and never read: `stdin_text` is `bessemer.landing`'s alone, and taking
        # it is what keeps this double a `proc.Runner` rather than a near-miss.
        stdin_text: str | None = None,
    ) -> proc.Result:
        self.calls.append(Call(tuple(argv), timeout, cwd, env))
        if self.scripted:
            return self.scripted.pop(0)
        return proc.Result(argv=tuple(argv), returncode=0, stdout="", stderr="")

    @property
    def argvs(self) -> list[tuple[str, ...]]:
        return [call.argv for call in self.calls]

    @property
    def words(self) -> list[str]:
        """Every argument of every recorded call, flattened. For the leak assertions."""
        return [word for call in self.calls for word in call.argv]


def failure(returncode: int, *, stderr: str = "") -> proc.Result:
    """A `Result` for a child that ran and failed. Argv is the docker one it stands in for."""
    return proc.Result(
        argv=("docker", "exec", CONTAINER), returncode=returncode, stdout="", stderr=stderr
    )


class ContainerTest(unittest.TestCase):
    """A repository with an adapter, a checkout, and a spec — as paths, since nothing runs."""

    def setUp(self) -> None:
        holder = TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name).resolve()

        self.repo = self.tmp / "repo"
        self.adapter = self.repo / ".bessemer"
        self.adapter.mkdir(parents=True)
        (self.adapter / "setup.sh").write_text("#!/usr/bin/env bash\nexit 0\n")

        self.checkout = self.adapter / "checkouts" / "work"
        self.checkout.mkdir(parents=True)

        specs = self.adapter / "specs"
        specs.mkdir()
        self.spec = specs / "one-off.md"
        self.spec.write_text("# a spec\n")

        self.double = Double()

    # --- the module under test ----------------------------------------------------------

    def boundary(
        self,
        *,
        cap_add: Sequence[str] = (),
        volumes: Sequence[str] = (),
        pids_limit: int | str = 2048,
        memory: str = "8g",
        env: container.Forwarding | None = None,
    ) -> container.Boundary:
        """A `Boundary` with the two limits filled in and an empty environment by default.

        The defaults live here rather than on the dataclass deliberately: `Boundary` has none,
        so that no call site in `bessemer/` can acquire a privilege decision by omission, and
        a test fixture is the one place where spelling them every time buys nothing.
        """
        return container.Boundary(
            cap_add=cap_add,
            volumes=volumes,
            pids_limit=pids_limit,
            memory=memory,
            env=container.Forwarding((), (), None) if env is None else env,
        )

    def run_argv(self, **overrides: object) -> list[str]:
        arguments: dict[str, object] = {
            "container": CONTAINER,
            "image": IMAGE,
            "checkout": self.checkout,
            "adapter_dir": self.adapter,
            "spec": self.spec,
            "boundary": self.boundary(),
        }
        arguments.update(overrides)
        return container.run_argv(**arguments)  # type: ignore[arg-type]

    def start(self, **overrides: object) -> proc.Result:
        arguments: dict[str, object] = {
            "container": CONTAINER,
            "image": IMAGE,
            "checkout": self.checkout,
            "adapter_dir": self.adapter,
            "spec": self.spec,
            "boundary": self.boundary(),
            "run": self.double,
        }
        arguments.update(overrides)
        return container.start(**arguments)  # type: ignore[arg-type]

    def capabilities(self, argv: Sequence[str]) -> list[str]:
        """Every `--cap-add` value in `argv`, in order — read positionally, not by pattern."""
        return [argv[index + 1] for index, word in enumerate(argv) if word == "--cap-add"]

    def mounts(self, argv: Sequence[str]) -> list[str]:
        return [argv[index + 1] for index, word in enumerate(argv) if word == "-v"]

    def env_flags(self, argv: Sequence[str]) -> list[str]:
        return [argv[index + 1] for index, word in enumerate(argv) if word == "-e"]

    def write_secrets(self, text: str) -> None:
        (self.adapter / container.SECRETS_FILE).write_text(text, encoding="utf-8")

    def write_container_env(self, text: str = "DEBUG=0\n") -> Path:
        path = self.adapter / container.ENV_FILE
        path.write_text(text, encoding="utf-8")
        return path


class RunArgvTest(ContainerTest):
    """The whole `docker run` argv, compared as one value."""

    def test_the_whole_argv_is_exactly_this(self) -> None:
        """The base case, which is bessemer's own adapter: nothing committed, no volumes, no
        env file, nothing forwarded. The two cap-adds present are core's own sudo pair.

        Compared whole rather than flag by flag, because the argv *is* the privilege boundary
        and the failure mode that matters is an addition — a `--privileged` appended by a
        later edit satisfies every containment check in this file. Only equality sees it.

        Flag order is the pin's (run.sh:1244–1264) and is part of the literal: identity,
        capabilities, limits, mounts, environment, image. A reordering is a review question,
        not a tidy-up.
        """
        self.assertEqual(
            self.run_argv(),
            [
                "docker",
                "run",
                "-d",
                "--name",
                CONTAINER,
                "--cap-drop",
                "ALL",
                "--cap-add",
                "SETUID",
                "--cap-add",
                "SETGID",
                "--pids-limit",
                "2048",
                "--memory",
                "8g",
                "-v",
                f"{self.checkout}:{CHECKOUT_MOUNT}",
                "-w",
                CHECKOUT_MOUNT,
                "-v",
                f"{self.adapter / 'setup.sh'}:{HOOK_MOUNT}:ro",
                "-v",
                f"{self.spec}:{SPEC_MOUNT}:ro",
                "-e",
                "BASH_DEFAULT_TIMEOUT_MS=300000",
                "-e",
                "BASH_MAX_TIMEOUT_MS=300000",
                IMAGE,
            ],
        )

    def test_the_image_is_the_last_argument(self) -> None:
        """Docker's own grammar: everything after the image name is a command for the
        container, so an option appended after it silently becomes the entrypoint's argument.
        """
        argv = self.run_argv(boundary=self.boundary(volumes=["cache:/cache"]))
        self.assertEqual(argv[-1], IMAGE)

    def test_no_port_is_ever_published(self) -> None:
        """ADR 0001: task containers publish no ports. There is no parameter through which one
        could arrive, which is what makes this an assertion about the shape rather than about
        a code path — and why it is asserted over an argv built with everything turned on."""
        argv = self.run_argv(
            boundary=self.boundary(
                cap_add=["SETUID"],
                volumes=["cache:/cache"],
                env=container.Forwarding((("A", "b"),), (), None),
            )
        )
        self.assertNotIn("-p", argv)
        self.assertNotIn("--publish", argv)
        self.assertNotIn("-P", argv)
        self.assertFalse([word for word in argv if word.startswith("--publish")], argv)

    def test_a_relative_path_is_refused_rather_than_read_as_a_volume_name(self) -> None:
        """`-v checkouts/work:/workspace` does not mount `./checkouts/work`: docker reads any
        source without a leading `/` as a volume *name*, so the agent would work in an empty
        volume mounted over the checkout and nothing would say so."""
        for name in ("checkout", "adapter_dir", "spec"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError) as raised:
                    self.run_argv(**{name: Path("relative/path")})
                self.assertIn(name, str(raised.exception))


class CapabilityTest(ContainerTest):
    """`--cap-drop ALL` always, and cap-adds pinned both ways (F3 decision 5.2, 5.3)."""

    def test_cap_drop_all_is_unconditional_and_comes_before_any_add(self) -> None:
        """Nothing about it is derived from config, so no configuration can omit it. Asserted
        as an adjacent pair *and* as a position, because `--cap-drop` with some other value —
        or after an add — would satisfy a membership test."""
        for boundary in (self.boundary(), self.boundary(cap_add=["SETUID"], volumes=["c:/c"])):
            argv = self.run_argv(boundary=boundary)
            with self.subTest(capabilities=self.capabilities(argv)):
                drop = argv.index("--cap-drop")
                self.assertEqual(argv[drop + 1], "ALL")
                adds = [index for index, word in enumerate(argv) if word == "--cap-add"]
                self.assertTrue(all(drop < index for index in adds), argv)

    def test_with_no_volumes_the_capabilities_are_cores_sudo_pair_and_the_committed_list(
        self,
    ) -> None:
        """The "absent" half of the both-ways pin: no volume, so no chown, so none of the three
        capabilities the chown needs. A `CHOWN` in this argv is core widening the boundary for
        a reason that does not apply.

        The sudo pair is here because the hook invocation is unconditional — measured, see
        `SUDO_CAPABILITIES`. This is the named test the leak mutation must fail: one extra
        capability in `Boundary.capabilities` or in `run_argv` turns it red.
        """
        argv = self.run_argv(boundary=self.boundary(cap_add=["KILL"]))
        self.assertEqual(self.capabilities(argv), ["SETUID", "SETGID", "KILL"])
        for capability in CHOWN_CAPABILITIES:
            self.assertNotIn(capability, self.capabilities(argv))

    def test_an_empty_committed_list_and_no_volumes_yields_cores_pair_alone(self) -> None:
        """Bessemer's own adapter, exactly: nothing committed, no volume. What it gets back is
        what core cannot do without."""
        argv = self.run_argv()
        self.assertEqual(self.capabilities(argv), list(SUDO_CAPABILITIES))

    def test_with_volumes_cores_own_chown_capabilities_are_added_too(self) -> None:
        """The "present" half. The chown is bessemer's own (`docker exec -u root … chown`), so
        its cost is bessemer's to declare rather than something an adapter has to discover —
        upstream's comment undercounted exactly here."""
        argv = self.run_argv(
            boundary=self.boundary(cap_add=["KILL"], volumes=["cache:/yarn-cache"])
        )
        self.assertEqual(
            self.capabilities(argv),
            ["SETUID", "SETGID", "CHOWN", "DAC_OVERRIDE", "FOWNER", "KILL"],
        )

    def test_an_empty_committed_list_with_volumes_yields_only_cores_five(self) -> None:
        argv = self.run_argv(boundary=self.boundary(volumes=["/workspace/node_modules"]))
        self.assertEqual(self.capabilities(argv), [*SUDO_CAPABILITIES, *CHOWN_CAPABILITIES])

    def test_a_capability_the_adapter_also_lists_is_not_repeated(self) -> None:
        """`--cap-add SETUID --cap-add SETUID` is harmless to docker and reads as a defect to
        whoever reviews the pinned argv."""
        argv = self.run_argv(
            boundary=self.boundary(cap_add=["SETUID", "CHOWN", "KILL"], volumes=["cache:/c"])
        )
        self.assertEqual(
            self.capabilities(argv),
            ["SETUID", "SETGID", "CHOWN", "DAC_OVERRIDE", "FOWNER", "KILL"],
        )

    def test_core_never_emits_kill(self) -> None:
        """The absence beside the pin-six decomposition. `KILL` is the one cap-add that was
        ever hae's own — its `setup-db.sh` signals a restarted postmaster — so it reaches an
        argv only through `container_cap_add`, and a core that emitted it would be carrying
        one adapter's database into every adopter's container.
        """
        for boundary in (
            self.boundary(),
            self.boundary(volumes=["cache:/cache"]),
            self.boundary(cap_add=["SYS_PTRACE"], volumes=["cache:/cache"]),
        ):
            with self.subTest(volumes=list(boundary.volumes)):
                self.assertNotIn("KILL", self.capabilities(self.run_argv(boundary=boundary)))

    def test_the_pin_six_decompose_into_cores_five_and_haes_one(self) -> None:
        """The parity statement for the coupling rule, as a set rather than a sequence.

        Upstream hard-coded six capabilities and its comment said they were "only what
        setup-db.sh needs". Measured against the pin's own code, five were not: three were
        consumed by the orchestrator's `docker exec -u root chown` (:1269–1273) and two by the
        agent's `sudo` (:1278). `KILL` is the remainder and the only adapter fact — so with
        `KILL` committed and one volume declared, core arrives at the same six. That is what
        makes the split a reorganisation of the pin rather than a change to what a container
        may do.

        A set, because core emits its own before the adapter's while the pin listed the chown
        three first; the order is not the claim.
        """
        argv = self.run_argv(
            boundary=self.boundary(cap_add=["KILL"], volumes=["yarn-cache:/yarn-cache"])
        )
        self.assertEqual(set(self.capabilities(argv)), set(PIN_SIX))
        self.assertEqual(
            set(SUDO_CAPABILITIES) | set(CHOWN_CAPABILITIES) | {"KILL"}, set(PIN_SIX)
        )


class MountTableTest(ContainerTest):
    """The four sources that may be mounted, and the ones that may not."""

    def test_the_mount_table_is_exactly_this(self) -> None:
        """Checkout read-write at the workspace, hook and spec read-only as single files, then
        each declared volume in the order the adapter listed them."""
        argv = self.run_argv(
            boundary=self.boundary(volumes=["cache:/yarn-cache", "/workspace/node_modules"])
        )
        self.assertEqual(
            self.mounts(argv),
            [
                f"{self.checkout}:{CHECKOUT_MOUNT}",
                f"{self.adapter / 'setup.sh'}:{HOOK_MOUNT}:ro",
                f"{self.spec}:{SPEC_MOUNT}:ro",
                "cache:/yarn-cache",
                "/workspace/node_modules",
            ],
        )

    def test_the_checkout_is_the_working_directory(self) -> None:
        """Without it the agent starts in the image's `WORKDIR` and its first `git` command
        answers about no repository at all."""
        argv = self.run_argv()
        self.assertEqual(argv[argv.index("-w") + 1], CHECKOUT_MOUNT)

    def test_the_hook_and_the_spec_are_read_only_and_the_checkout_is_not(self) -> None:
        """The spec is the agent's instructions and the hook is what sudo runs as root; either
        being writable by the agent is a different tool. The checkout is writable because
        writing to it is the entire job."""
        mounts = self.mounts(self.run_argv())
        self.assertTrue(mounts[0].endswith(f":{CHECKOUT_MOUNT}"))
        self.assertTrue(mounts[1].endswith(":ro"))
        self.assertTrue(mounts[2].endswith(":ro"))

    def test_the_hook_is_mounted_at_the_path_the_sudoers_line_grants(self) -> None:
        """One string, two files: the mount here and the grant in the image. A mount at any
        other path is a sudo denial reported as the hook failing."""
        self.assertEqual(SUDOERS_INVOCATION[-1], HOOK_MOUNT)
        self.assertIn(f":{HOOK_MOUNT}:ro", self.mounts(self.run_argv())[1])

    def test_the_adapter_directory_is_never_mounted(self) -> None:
        """The pin's own comment (run.sh:1216): `.bessemer/` holds the gitignored `.env` and
        every other run's checkout, so mounting it whole hands the container both.

        Asserted over the sources of every mount, with volumes present, rather than trusted to
        the fact that no parameter names the directory.
        """
        argv = self.run_argv(boundary=self.boundary(volumes=["cache:/cache"]))
        for mount in self.mounts(argv):
            source = mount.partition(":")[0]
            with self.subTest(mount=mount):
                self.assertNotEqual(Path(source), self.adapter)
                self.assertNotEqual(source, f"{self.adapter}/")

    def test_a_checkout_or_spec_that_is_the_adapter_directory_is_refused(self) -> None:
        """The one route by which the whole directory could arrive as a mount: a caller
        passing it as the checkout or the spec. Refused where it would be built rather than
        left to review."""
        for name in ("checkout", "spec"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError) as raised:
                    self.run_argv(**{name: self.adapter})
                self.assertIn("adapter directory", str(raised.exception))


class LimitTest(ContainerTest):
    """`--pids-limit` and `--memory`, which ADR 0001's security section requires."""

    def test_the_limits_come_from_config_and_are_stringified(self) -> None:
        argv = self.run_argv(boundary=self.boundary(pids_limit=512, memory="2g"))
        self.assertEqual(argv[argv.index("--pids-limit") + 1], "512")
        self.assertEqual(argv[argv.index("--memory") + 1], "2g")

    def test_a_limit_arriving_from_the_environment_layer_as_a_string_is_accepted(self) -> None:
        """`BESSEMER_PIDS_LIMIT=512` reaches config as a string and the loader deliberately
        does not coerce it (issue 01). Admitted here rather than crashing, because the
        alternative is a dispatch that refuses a value the user can see in their shell."""
        argv = self.run_argv(boundary=self.boundary(pids_limit="512"))
        self.assertEqual(argv[argv.index("--pids-limit") + 1], "512")


class EnvBoundaryTest(ContainerTest):
    """Which of the host's secrets cross, and what is said about the ones that do not."""

    def declared_env(self) -> None:
        """A `.env` with all three shapes: declared, credential, and neither."""
        self.write_secrets(
            f"DATABASE_URL={DECLARED_VALUE}\n"
            f"AWS_SECRET_ACCESS_KEY={WITHHELD_VALUE}\n"
            f"ANTHROPIC_API_KEY={CREDENTIAL_VALUE}\n"
        )

    def forwarding(self, *declared: str) -> container.Forwarding:
        return container.forwarding(adapter_dir=self.adapter, declared=declared)

    def test_declared_names_and_credentials_cross_and_nothing_else_does(self) -> None:
        """ADR 0001's enforcement half: a gitignored file forwards declared keys only, so the
        secret-class rule is a property of the code rather than of the adopter's restraint."""
        self.declared_env()
        forwarding = self.forwarding("DATABASE_URL")
        self.assertEqual(
            forwarding.forwarded,
            (("DATABASE_URL", DECLARED_VALUE), ("ANTHROPIC_API_KEY", CREDENTIAL_VALUE)),
        )
        self.assertEqual(forwarding.withheld, ("AWS_SECRET_ACCESS_KEY",))

    def test_the_argv_carries_exactly_the_declared_key_and_the_credential(self) -> None:
        """The same fact one layer on, where it is flags: the tuning pair core owns, then one
        `-e` per forwarded name and no others."""
        self.declared_env()
        argv = self.run_argv(boundary=self.boundary(env=self.forwarding("DATABASE_URL")))
        self.assertEqual(
            self.env_flags(argv),
            [
                "BASH_DEFAULT_TIMEOUT_MS=300000",
                "BASH_MAX_TIMEOUT_MS=300000",
                f"DATABASE_URL={DECLARED_VALUE}",
                f"ANTHROPIC_API_KEY={CREDENTIAL_VALUE}",
            ],
        )

    def test_the_withheld_value_appears_in_no_recorded_argument_and_no_warning(self) -> None:
        """The leak assertion, over both channels at once: the whole recorded proc stream from
        a real `start`, and the operator-facing warning text.

        The names alone tell a prompt-injected agent what secrets exist on the host (ADR
        0001), so the value must not travel and the warning may name the key only.
        """
        self.declared_env()
        forwarding = self.forwarding("DATABASE_URL")
        self.start(boundary=self.boundary(env=forwarding))

        warning = forwarding.warning(destination=proc.Destination.HOST_LOG)
        assert warning is not None
        self.assertNotIn(WITHHELD_VALUE, warning)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", str(forwarding.forwarded))
        for word in self.double.words:
            self.assertNotIn(WITHHELD_VALUE, word)

    def test_the_warning_names_the_undeclared_key_and_the_key_to_declare_it_in(self) -> None:
        """One line for all withheld keys, not one per key: the operator's decision is the
        same for every one of them."""
        self.declared_env()
        warning = self.forwarding("DATABASE_URL").warning(destination=proc.Destination.HOST_LOG)
        assert warning is not None
        self.assertIn("AWS_SECRET_ACCESS_KEY", warning)
        self.assertIn("container_env_keys", warning)
        self.assertNotIn("DATABASE_URL", warning)
        self.assertNotIn("ANTHROPIC_API_KEY", warning)

    def test_an_agent_visible_destination_gets_no_warning_at_all(self) -> None:
        """The spec's "route through issue 02's policy", as a value rather than as prose.

        ADR 0001 forbids the warning reaching the container log, a PR body, or a notification —
        the names alone tell a prompt-injected agent what secrets exist on the host. A docstring
        saying so cannot stop issue 10 putting it in a notification; a `None` can. The classes
        of reader are `proc.Destination`'s, declared once, so there is no third opinion about
        who may read what.
        """
        self.declared_env()
        forwarding = self.forwarding("DATABASE_URL")
        self.assertTrue(forwarding.withheld)
        self.assertIsNone(forwarding.warning(destination=proc.Destination.AGENT_VISIBLE))
        self.assertIsNotNone(forwarding.warning(destination=proc.Destination.HOST_LOG))

    def test_the_warning_names_its_reader_and_cannot_default_to_one(self) -> None:
        """Keyword-only, no default, like `proc.quote`'s own parameter: the permissive row must
        not be reachable by omission."""
        parameter = inspect.signature(container.Forwarding.warning).parameters["destination"]
        self.assertIs(parameter.default, inspect.Parameter.empty)
        self.assertIs(parameter.kind, inspect.Parameter.KEYWORD_ONLY)

    def test_nothing_withheld_is_no_warning_at_all(self) -> None:
        """`None` rather than an empty string, so a caller cannot print a blank line by
        forwarding it unchecked."""
        self.declared_env()
        self.assertIsNone(
            self.forwarding("DATABASE_URL", "AWS_SECRET_ACCESS_KEY").warning(
                destination=proc.Destination.HOST_LOG
            )
        )

    def test_both_credential_names_cross_without_being_declared(self) -> None:
        """The whole point of the built-in names: an adopter who needs nothing extra never
        encounters `container_env_keys` (ADR 0001). Both, because the pin's own credential
        check accepts either (run.sh:254)."""
        self.assertEqual(container.CREDENTIAL_NAMES, CREDENTIAL_NAMES)
        self.write_secrets("".join(f"{name}=value-{name}\n" for name in CREDENTIAL_NAMES))
        self.assertEqual(
            self.forwarding(),
            container.Forwarding(
                forwarded=tuple((name, f"value-{name}") for name in CREDENTIAL_NAMES),
                withheld=(),
                env_file=None,
            ),
        )

    def test_a_declared_name_absent_from_the_file_contributes_nothing(self) -> None:
        """An adapter declaring a name it does not set on this machine is ordinary, not an
        error: the declaration is a permission, not a requirement."""
        self.write_secrets("DATABASE_URL=x\n")
        self.assertEqual(
            self.forwarding("DATABASE_URL", "REDIS_URL").forwarded, (("DATABASE_URL", "x"),)
        )

    def test_an_absent_secrets_file_forwards_nothing_and_warns_about_nothing(self) -> None:
        """Bessemer's own adapter, and every adapter whose container needs no secret."""
        self.assertEqual(self.forwarding("DATABASE_URL"), container.Forwarding((), (), None))

    def test_the_file_is_parsed_by_dockers_env_file_grammar(self) -> None:
        """Docker's grammar, not the shell's, because docker's is what the pin's container
        actually received (`--env-file "$BOX/.env"`).

        Each line below is one rule: a comment, a blank, a value with an `=` in it, quotes
        that are *not* stripped, whitespace around the name, and a `$VAR` that is not
        expanded. A `.env` written for `source` and read this way is the pin's own behaviour.
        """
        self.write_secrets('# a comment\n\nA=one=two\nB="quoted"\n  C  =spaced\nD=$HOME\nE=\n')
        self.assertEqual(
            self.forwarding("A", "B", "C", "D", "E").forwarded,
            (
                ("A", "one=two"),
                ("B", '"quoted"'),
                ("C", "spaced"),
                ("D", "$HOME"),
                ("E", ""),
            ),
        )

    def test_the_two_shapes_the_grammar_drops(self) -> None:
        """A bare `NAME` is docker's "take it from my own environment", which is the channel
        this module exists to not have; a name that is not an environment variable name is
        something docker rejects outright. Both are dropped rather than guessed at, and
        neither becomes a withheld warning — there is no value to withhold."""
        self.write_secrets("BARE\nnot a name=x\nGOOD=y\n")
        forwarding = self.forwarding("BARE", "GOOD")
        self.assertEqual(forwarding.forwarded, (("GOOD", "y"),))
        self.assertEqual(forwarding.withheld, ())

    def test_the_last_assignment_of_a_name_wins(self) -> None:
        """Docker's own behaviour for a file someone edited twice."""
        self.write_secrets("A=first\nB=b\nA=second\n")
        self.assertEqual(self.forwarding("A", "B").forwarded, (("A", "second"), ("B", "b")))

    def test_a_secrets_file_that_cannot_be_read_is_not_treated_as_absent(self) -> None:
        """Loud, like `prompts.load`'s unreadable override and unlike `config`'s layers: the
        caller is a dispatch about to start a container, and running the agent without the
        credential its adapter declared surfaces twenty minutes later as an empty pass."""
        (self.adapter / container.SECRETS_FILE).mkdir()
        with self.assertRaises(OSError):
            self.forwarding("DATABASE_URL")

    def test_the_committed_env_file_crosses_in_bulk_when_it_exists(self) -> None:
        """Bulk *because* it is committed: a real secret in it is a visible mistake in a diff
        (ADR 0001). Upstream's `.env.sandbox`, renamed — "sandbox" is banned vocabulary.

        The path reaches the argv through `Forwarding.env_file`, because `run_argv` is pure and
        may not ask the filesystem whether the file is there.
        """
        path = self.write_container_env()
        forwarding = self.forwarding()
        self.assertEqual(forwarding.env_file, path)
        argv = self.run_argv(boundary=self.boundary(env=forwarding))
        self.assertEqual(argv[argv.index("--env-file") + 1], str(path))
        self.assertEqual(container.ENV_FILE, "container.env")

    def test_an_absent_committed_env_file_means_no_flag(self) -> None:
        """The common case — an adapter whose app needs no environment to boot under test — so
        the flag is conditional rather than the file being required to exist."""
        self.assertIsNone(self.forwarding().env_file)
        self.assertNotIn(
            "--env-file", self.run_argv(boundary=self.boundary(env=self.forwarding()))
        )

    def test_a_directory_named_like_the_committed_env_file_is_not_one(self) -> None:
        """`is_file`, not `exists`: a directory at that path would reach docker as an
        `--env-file` it cannot read, and the failure would name the flag rather than the
        adapter."""
        (self.adapter / container.ENV_FILE).mkdir()
        self.assertIsNone(self.forwarding().env_file)

    def test_the_gitignored_file_is_never_passed_as_an_env_file(self) -> None:
        """The divergence from the pin, which passed both files wholesale (run.sh:1263) and so
        made the secret-class rule a convention the adopter upheld by restraint."""
        self.declared_env()
        self.write_container_env()
        argv = self.run_argv(boundary=self.boundary(env=self.forwarding("DATABASE_URL")))
        env_files = [argv[index + 1] for index, w in enumerate(argv) if w == "--env-file"]
        self.assertEqual(env_files, [str(self.adapter / container.ENV_FILE)])
        self.assertNotIn(str(self.adapter / container.SECRETS_FILE), argv)

    def test_no_host_environment_crosses_in_bulk(self) -> None:
        """The absence that has no flag to look for: every `-e` is a name this module put
        there, so the argv itself is the enumeration. Asserted by rebuilding the expected list
        from what was declared rather than by checking a count."""
        self.declared_env()
        argv = self.run_argv(boundary=self.boundary(env=self.forwarding("DATABASE_URL")))
        names = [flag.partition("=")[0] for flag in self.env_flags(argv)]
        self.assertEqual(
            names,
            [
                "BASH_DEFAULT_TIMEOUT_MS",
                "BASH_MAX_TIMEOUT_MS",
                "DATABASE_URL",
                "ANTHROPIC_API_KEY",
            ],
        )

    def test_the_claude_tuning_pair_is_core_owned_and_pinned(self) -> None:
        """F3 decision 5.5 and run.sh:1261: how long the agent's own `Bash` tool waits. Core's
        because it is provider-contract surface, and pinned because a value nobody chose is
        the shape a magic number takes."""
        self.assertEqual(
            container.CLAUDE_TUNING_ENV,
            (("BASH_DEFAULT_TIMEOUT_MS", "300000"), ("BASH_MAX_TIMEOUT_MS", "300000")),
        )

    def test_a_forwarding_carries_no_withheld_value_to_carry(self) -> None:
        """Structural rather than disciplinary: `withheld` is names, so no caller can leak a
        withheld value by quoting the type it was handed."""
        self.declared_env()
        for name in self.forwarding("DATABASE_URL").withheld:
            self.assertIsInstance(name, str)
            self.assertNotIn("=", name)


class CredentialPresenceTest(ContainerTest):
    """The shared resolver: where a Claude credential is configured, if anywhere.

    One definition, two callers — doctor renders it (F3 issue 09) and dispatch's preflight
    refuses on it (issue 10) — which is the pin's own discipline for this check
    (`have_claude_credential`, "not a second copy of the logic", run.sh:344–346).
    """

    TOKEN: Final = "sk-ant-oat01-thisisnotarealtokenbutitlookslikeone"

    def presence(self, **env: str) -> container.Credential:
        return container.credential_presence(adapter_dir=self.adapter, env=env)

    def test_a_name_in_the_secrets_file_is_the_channel_that_crosses(self) -> None:
        """`forwarding` above reads this file and nothing else, so this is the only channel
        that reaches the agent."""
        self.write_secrets(f"CLAUDE_CODE_OAUTH_TOKEN={self.TOKEN}\n")
        found = self.presence()
        self.assertEqual(found.in_secrets_file, ("CLAUDE_CODE_OAUTH_TOKEN",))
        self.assertTrue(found.crosses)
        self.assertTrue(found.present)

    def test_either_built_in_name_counts(self) -> None:
        self.write_secrets(f"ANTHROPIC_API_KEY={self.TOKEN}\n")
        self.assertEqual(self.presence().in_secrets_file, ("ANTHROPIC_API_KEY",))

    def test_both_names_are_reported_in_the_modules_own_order(self) -> None:
        """`CREDENTIAL_NAMES` order rather than file order: this is a set of names being
        reported, and the reader should not see it reshuffle when a file is edited."""
        self.write_secrets(f"ANTHROPIC_API_KEY={self.TOKEN}\nCLAUDE_CODE_OAUTH_TOKEN=x\n")
        self.assertEqual(self.presence().in_secrets_file, container.CREDENTIAL_NAMES)

    def test_an_exported_name_is_present_but_does_not_cross(self) -> None:
        """The hole the pin left open: it sourced `.env` into its own environment before
        asking, so a credential that only ever existed in the operator's shell passed its
        preflight and started a container without one."""
        found = self.presence(CLAUDE_CODE_OAUTH_TOKEN=self.TOKEN)
        self.assertEqual(found.exported, ("CLAUDE_CODE_OAUTH_TOKEN",))
        self.assertFalse(found.crosses)
        self.assertTrue(found.present)

    def test_an_empty_value_is_not_a_credential_in_either_channel(self) -> None:
        """`[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}${ANTHROPIC_API_KEY:-}" ]` at the pin."""
        self.write_secrets("CLAUDE_CODE_OAUTH_TOKEN=\n")
        found = self.presence(ANTHROPIC_API_KEY="")
        self.assertEqual((found.in_secrets_file, found.exported), ((), ()))
        self.assertFalse(found.present)

    def test_nothing_anywhere_is_present_false(self) -> None:
        self.assertFalse(self.presence(PATH="/usr/bin").present)

    def test_an_absent_secrets_file_is_an_empty_channel_rather_than_an_error(self) -> None:
        """bessemer's own adapter has no `.env`, and doctor must still report on it."""
        self.assertEqual(self.presence().in_secrets_file, ())

    def test_a_secrets_file_that_cannot_be_read_propagates(self) -> None:
        """The same choice `forwarding` makes, and for the same reason: a dispatch must not
        start a container on a credential file it could not read. Doctor catches this and
        renders a line, because doctor's contract is the other one."""
        (self.adapter / container.SECRETS_FILE).mkdir()
        with self.assertRaises(OSError):
            self.presence()

    def test_the_type_carries_no_value_to_leak(self) -> None:
        """ADR 0001's "presence only" as a type rather than as a rule: there is no field here
        a caller could quote a secret out of."""
        self.write_secrets(f"CLAUDE_CODE_OAUTH_TOKEN={self.TOKEN}\n")
        found = self.presence(ANTHROPIC_API_KEY=self.TOKEN)
        self.assertNotIn(self.TOKEN, repr(found))
        self.assertEqual(
            [field.name for field in dataclasses.fields(found)],
            ["in_secrets_file", "exported"],
        )


class BoundaryTest(ContainerTest):
    """The last gate before an argv exists: what a `Boundary` refuses to be built from."""

    def test_a_bare_string_cap_add_never_reaches_an_argv(self) -> None:
        """The motivating case (issue 01's review): `container_cap_add = "SETUID"` is a
        plausible mistake, a `str` satisfies `Sequence[str]` for a type checker, and iterating
        one yields characters — so a builder mapping entries to flags emits
        `--cap-add S --cap-add E …`.

        Refused at construction, which is what makes "before any argv is built" structural:
        there is no route to `run_argv` that does not pass through here. `bessemer.config`
        refuses the same shape at load, where it is a user's mistake and becomes a doctor
        line; this is the backstop, and the same guard `proc.run` carries against a `str`
        argv.
        """
        with self.assertRaises(TypeError) as raised:
            self.boundary(cap_add="SETUID")
        self.assertIn("container_cap_add", str(raised.exception))
        self.assertIn("not a string", str(raised.exception))

    def test_a_bare_string_volumes_never_reaches_an_argv_either(self) -> None:
        with self.assertRaises(TypeError) as raised:
            self.boundary(volumes="cache:/cache")
        self.assertIn("container_volumes", str(raised.exception))

    def test_an_entry_that_is_not_a_string_is_refused(self) -> None:
        """TOML admits `container_cap_add = [42]`, and `42` would reach docker as a flag
        value spelled by `str()` somewhere further down."""
        with self.assertRaises(TypeError):
            self.boundary(cap_add=[42])  # type: ignore[list-item]
        with self.assertRaises(TypeError):
            self.boundary(volumes=[None])  # type: ignore[list-item]

    def test_an_empty_entry_is_refused(self) -> None:
        """An empty capability name reaches docker as a flag with no value."""
        with self.assertRaises(ValueError):
            self.boundary(cap_add=[""])

    def test_a_flag_shaped_capability_is_refused(self) -> None:
        """The same rule config applies to a volume source: an entry beginning with `-` in the
        position next to `--cap-add` is a smuggled argument, not a typo."""
        for capability in ("--privileged", "-x", "CAP SETUID", "chown;reboot"):
            with self.subTest(capability=capability):
                with self.assertRaises(ValueError) as raised:
                    self.boundary(cap_add=[capability])
                self.assertIn("container_cap_add", str(raised.exception))

    def test_all_is_refused_because_it_would_undo_the_drop(self) -> None:
        """`--cap-add ALL` after `--cap-drop ALL` hands every capability back, which makes the
        drop decorative — and the drop is the one thing in the argv no configuration may affect
        (F3 decision 5.2). Refused in every spelling docker would accept, because a check
        written against `"ALL"` alone is defeated by `cap_all`.
        """
        for capability in ("ALL", "all", "CAP_ALL", "cap_all"):
            with self.subTest(capability=capability):
                with self.assertRaises(ValueError) as raised:
                    self.boundary(cap_add=[capability])
                self.assertIn("undo --cap-drop ALL", str(raised.exception))

    def test_a_capability_core_already_adds_is_not_repeated_in_another_spelling(self) -> None:
        """Docker reads `setuid`, `SETUID` and `CAP_SETUID` as one capability, so "already in
        the list" has to be asked the way docker would ask it."""
        for spelling in ("setuid", "CAP_SETUID", "cap_setuid"):
            with self.subTest(spelling=spelling):
                boundary = self.boundary(cap_add=[spelling])
                self.assertEqual(boundary.capabilities(), SUDO_CAPABILITIES)

    def test_the_spellings_docker_accepts_are_accepted(self) -> None:
        """The control for the test above: lowercase and the `CAP_` prefix are both legal
        docker spellings, and refusing them would refuse a working configuration."""
        boundary = self.boundary(cap_add=["chown", "CAP_SETUID", "DAC_OVERRIDE"])
        self.assertEqual(list(boundary.cap_add), ["chown", "CAP_SETUID", "DAC_OVERRIDE"])

    def test_a_mount_point_that_climbs_out_is_refused(self) -> None:
        """`start` derives a host directory to pre-create from each mount point under the
        checkout, so `..` in one is a host path outside the checkout. `bessemer.config`
        refuses it at load too; this is the gate that cannot be bypassed by building a
        `Boundary` by hand."""
        for volume in ("cache:/workspace/../../etc", "/workspace/../../etc"):
            with self.subTest(volume=volume):
                with self.assertRaises(ValueError):
                    self.boundary(volumes=[volume])

    def test_a_volume_that_would_hide_the_checkout_is_refused(self) -> None:
        """A volume mounted at the checkout's own path shadows it: the agent works in an empty
        tree, commits nothing, and no error is printed anywhere. Inside it is the supported
        case — that is what the pin's `node_modules` volume is."""
        with self.assertRaises(ValueError) as raised:
            self.boundary(volumes=[f"cache:{CHECKOUT_MOUNT}"])
        self.assertIn("hide the checkout", str(raised.exception))
        self.boundary(volumes=[f"cache:{CHECKOUT_MOUNT}/node_modules"])

    def test_a_double_slash_does_not_smuggle_a_volume_over_the_checkout(self) -> None:
        """`cache://workspace` is the same mount as `cache:/workspace` once docker cleans the
        target, and it compares unequal to it as a string — so a check written against the
        string alone accepts it and the volume shadows the checkout anyway.

        `PurePosixPath` is not what closes it: it preserves a leading `//`, which is where the
        hole came from. The mountpoint is normalised before anything compares it.
        """
        for volume in ("cache://workspace", f"cache:{CHECKOUT_MOUNT}/", "//workspace"):
            with self.subTest(volume=volume):
                with self.assertRaises(ValueError) as raised:
                    self.boundary(volumes=[volume])
                self.assertIn("hide the checkout", str(raised.exception))

    def test_a_normalised_mount_point_is_what_the_chown_and_the_pre_create_see(self) -> None:
        """The same normalisation, read from the other end: one spelling reaches the argv the
        chown exec is built from, whatever the adapter typed."""
        boundary = self.boundary(volumes=[f"cache:{CHECKOUT_MOUNT}//node_modules/"])
        self.assertEqual(boundary.mount_points(), (f"{CHECKOUT_MOUNT}/node_modules",))

    def test_a_mount_point_that_is_not_absolute_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.boundary(volumes=["cache:relative"])

    def test_a_limit_that_is_not_a_limit_is_refused(self) -> None:
        """The load-time-visibility argument, applied to the two keys ADR 0001's security
        section requires: `memory = "banana"` is otherwise a `docker run` failure with a
        checkout already cloned."""
        for kwargs in (
            {"pids_limit": "many"},
            {"pids_limit": 0},
            {"pids_limit": -1},
            {"pids_limit": "0"},
            {"memory": "banana"},
            {"memory": "8gb"},
            {"memory": ""},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError) as raised:
                    self.boundary(**kwargs)
                self.assertIn(next(iter(kwargs)), str(raised.exception))

    def test_a_limit_switched_off_is_refused_too(self) -> None:
        """Docker reads `--memory 0` and `--pids-limit 0` as *unlimited*, so a limit ADR 0001
        requires can be switched off by a value that looks like a value. Every spelling of zero,
        because `0g` reaches the same place as `0`."""
        for memory in ("0", "0g", "00", "0m"):
            with self.subTest(memory=memory):
                with self.assertRaises(ValueError) as raised:
                    self.boundary(memory=memory)
                self.assertIn("memory", str(raised.exception))

    def test_the_limits_docker_accepts_are_accepted(self) -> None:
        for memory in ("8g", "512m", "1024", "2G"):
            with self.subTest(memory=memory):
                self.assertEqual(self.boundary(memory=memory).memory, memory)

    def test_a_forwarded_name_that_is_not_a_variable_name_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.boundary(env=container.Forwarding(((("--privileged"), "x"),), (), None))

    def test_no_field_has_a_default(self) -> None:
        """ADR 0003's rule for the proc runner, applied to the other thing a call site must
        not acquire by omission. A `Boundary()` standing for "no capabilities, the usual
        limits" is how a privilege decision stops being declared — and the limits' real
        defaults live in `config.DEFAULTS`, where restating them here would be two values
        that can disagree about a security posture.
        """
        with self.assertRaises(TypeError):
            container.Boundary()  # type: ignore[call-arg]
        parameters = inspect.signature(container.Boundary).parameters
        for name, parameter in parameters.items():
            with self.subTest(field=name):
                self.assertIs(parameter.default, inspect.Parameter.empty)
                self.assertIs(parameter.kind, inspect.Parameter.KEYWORD_ONLY)

    def test_the_sequences_are_copied_so_a_later_append_cannot_widen_them(self) -> None:
        """A privilege boundary a caller's stray `append` can move is not a boundary — the
        same reason `config.Config.get` copies a list on the way out."""
        caps = ["SETUID"]
        boundary = self.boundary(cap_add=caps)
        caps.append("SYS_ADMIN")
        self.assertEqual(list(boundary.cap_add), ["SETUID"])
        self.assertIsInstance(boundary.cap_add, tuple)


class SetupHookTest(ContainerTest):
    """The chown, then the one setup command core runs."""

    def test_the_invocation_is_the_sudoers_string_and_nothing_more(self) -> None:
        """Equality, not containment: sudo compares argument lists, so an extra flag is denied
        rather than ignored. An added `-x` or `--login` fails this test, which is the point."""
        self.assertEqual(container.SETUP_HOOK_COMMAND, SUDOERS_INVOCATION)
        self.assertEqual(
            container.setup_hook_argv(container=CONTAINER),
            ["docker", "exec", CONTAINER, "sudo", "/usr/bin/bash", HOOK_MOUNT],
        )

    def test_the_chown_argv_is_this(self) -> None:
        """Root through docker rather than through the agent's sudo — orchestrator privilege
        (pin :1269–1273). Granting the agent `chown` instead would be granting it root over
        its whole tree, which is what the one-script sudoers line exists to avoid."""
        self.assertEqual(
            container.chown_argv(container=CONTAINER, points=["/yarn-cache", "/workspace/nm"]),
            [
                "docker",
                "exec",
                "-u",
                "root",
                CONTAINER,
                "chown",
                "agent",
                "/yarn-cache",
                "/workspace/nm",
            ],
        )

    def hook(self, *results: proc.Result, volumes: Sequence[str] = ()) -> proc.Result:
        self.double = Double(*results)
        return container.run_setup_hook(
            container=CONTAINER, boundary=self.boundary(volumes=volumes), run=self.double
        )

    def test_with_volumes_the_chown_runs_first_and_the_hook_second(self) -> None:
        self.hook(volumes=["cache:/yarn-cache", "/workspace/node_modules"])
        self.assertEqual(
            self.double.argvs,
            [
                (
                    "docker",
                    "exec",
                    "-u",
                    "root",
                    CONTAINER,
                    "chown",
                    "agent",
                    "/yarn-cache",
                    "/workspace/node_modules",
                ),
                ("docker", "exec", CONTAINER, *SUDOERS_INVOCATION),
            ],
        )

    def test_with_no_volumes_there_is_nothing_to_chown(self) -> None:
        """Running it anyway would need `CHOWN` in a container that was deliberately not given
        it — the coupling rule read from the other end."""
        self.hook()
        self.assertEqual(
            self.double.argvs, [("docker", "exec", CONTAINER, *SUDOERS_INVOCATION)]
        )

    def test_a_nonzero_hook_aborts_and_names_the_hook(self) -> None:
        """ADR 0001's contract: a nonzero exit aborts the dispatch and the log is surfaced.
        Surfacing is the caller's — this exception carries the `Result` so it can."""
        with self.assertRaises(container.SetupHookError) as raised:
            self.hook(failure(1, stderr="setup: could not start postgres"))
        self.assertIn(HOOK_MOUNT, str(raised.exception))
        self.assertIn("exited 1", str(raised.exception))
        self.assertEqual(raised.exception.result.returncode, 1)

    def test_a_127_names_the_image_contract_instead(self) -> None:
        """Two failures wear the same nonzero exit and need different readers. A hook that ran
        and failed is the adapter author's; 127 is a program the *image* does not have, which
        is the shape a missing `timeout` or a missing `git` takes."""
        with self.assertRaises(container.SetupHookError) as raised:
            self.hook(failure(127, stderr="bash: line 1: git: command not found"))
        message = str(raised.exception)
        for clause in container.IMAGE_CONTRACT:
            self.assertIn(clause, message)

    def test_the_abort_message_quotes_stderr_through_the_one_policy(self) -> None:
        """Issue 02's policy, at a site that would otherwise invent its own convention: git and
        docker echo remote URLs, and a remote URL can embed a token. The redaction is
        `bessemer.redact`'s, never a second regex."""
        leaky = "fatal: could not read https://x-access-token:ghp_secret@github.com/o/r.git"
        with self.assertRaises(container.SetupHookError) as raised:
            self.hook(failure(1, stderr=leaky))
        message = str(raised.exception)
        self.assertIn(redact.REDACTED, message)
        self.assertNotIn("ghp_secret", message)

    def test_a_failing_chown_aborts_before_the_hook_runs(self) -> None:
        """A different failure with a different reader: the hook never ran, and a mountpoint
        the agent cannot write to would otherwise surface inside the first pass."""
        with self.assertRaises(proc.ProcessError):
            self.hook(failure(1), volumes=["cache:/yarn-cache"])
        self.assertEqual(len(self.double.calls), 1)

    def test_the_hook_gets_the_long_deadline_and_the_chown_the_short_one(self) -> None:
        """The hook is where an adapter installs its dependencies (ADR 0001), so a cold cache
        is the normal first run; the chown changes ownership of a mountpoint and nothing more.
        """
        self.hook(volumes=["cache:/yarn-cache"])
        self.assertEqual(self.double.calls[0].timeout, container.EXEC_TIMEOUT_SECONDS)
        self.assertEqual(self.double.calls[1].timeout, container.HOOK_TIMEOUT_SECONDS)
        self.assertGreater(container.HOOK_TIMEOUT_SECONDS, container.EXEC_TIMEOUT_SECONDS)

    def test_core_runs_exactly_one_setup_command(self) -> None:
        """F3 decision 5: upstream's settings-template `cp` lines and its `yarn install` are
        adapter content that moves into hae's own hook at F7. Anything else appearing here is
        core learning about somebody's stack."""
        self.hook()
        execs = [argv for argv in self.double.argvs if argv[:2] == ("docker", "exec")]
        self.assertEqual(len(execs), 1)


class StartTest(ContainerTest):
    """Starting the container, and the one filesystem effect that has to precede it."""

    def test_start_runs_the_built_argv_with_a_deadline(self) -> None:
        self.start()
        self.assertEqual(self.double.argvs, [tuple(self.run_argv())])
        self.assertEqual(self.double.calls[0].timeout, container.START_TIMEOUT_SECONDS)

    def test_a_container_that_would_not_start_raises(self) -> None:
        """There is no useful half a container: a caller that went on to `docker exec` would
        report a missing container instead of the reason the run never began."""
        self.double = Double(failure(125, stderr="docker: no such image"))
        with self.assertRaises(proc.ProcessError):
            self.start()

    def test_a_volume_inside_the_checkout_is_pre_created_before_the_container_exists(
        self,
    ) -> None:
        """Generalising the pin's `mkdir -p "$wt/client/node_modules"` (run.sh:1210–1212).

        Before, not after: docker copies a mountpoint's existing contents into a fresh
        anonymous volume when the container is created, and the directory being there and
        host-owned is what makes the volume arrive owned by the agent rather than by root.
        The double asserts the ordering by reading the filesystem at the moment it is called —
        an assertion made after `start` returned could not tell the two orders apart.
        """
        seen: list[bool] = []

        def record(
            argv: Sequence[str],
            *,
            timeout: float,
            cwd: Path | None = None,
            env: Mapping[str, str] | None = None,
            stdin_text: str | None = None,
        ) -> proc.Result:
            seen.append((self.checkout / "node_modules").is_dir())
            return proc.Result(argv=tuple(argv), returncode=0, stdout="", stderr="")

        self.start(
            boundary=self.boundary(volumes=[f"{CHECKOUT_MOUNT}/node_modules"]), run=record
        )
        self.assertEqual(seen, [True])

    def test_a_volume_outside_the_checkout_creates_nothing_on_the_host(self) -> None:
        """A named volume's mountpoint is docker's to create. Only the ones *inside* the
        checkout are a host directory at all."""
        self.start(boundary=self.boundary(volumes=["cache:/yarn-cache"]))
        self.assertEqual([path.name for path in self.checkout.iterdir()], [])

    def test_a_nested_volume_path_is_created_whole(self) -> None:
        """The pin's own case is two levels deep (`client/node_modules`)."""
        self.start(boundary=self.boundary(volumes=[f"{CHECKOUT_MOUNT}/client/node_modules"]))
        self.assertTrue((self.checkout / "client" / "node_modules").is_dir())

    def test_pre_creation_is_idempotent(self) -> None:
        """A re-dispatch on the same branch clones a fresh checkout, but a hook that already
        ran, or a caller that starts twice, must not fail on an existing directory."""
        boundary = self.boundary(volumes=[f"{CHECKOUT_MOUNT}/node_modules"])
        self.start(boundary=boundary)
        self.start(boundary=boundary)


class RemoveTest(ContainerTest):
    """`docker rm -f -v`, from the stale-leftover cleanup and from the `finally`."""

    def test_the_remove_argv_is_this(self) -> None:
        """`-f` because the container is running until something kills it, and `-v` for its
        anonymous volumes — which is what keeps a bare `/path` volume from outliving the run
        that made it (the pin's `-fv`, run.sh:1161)."""
        self.assertEqual(
            container.remove_argv(container=CONTAINER),
            ["docker", "rm", "-f", "-v", CONTAINER],
        )

    def test_removing_what_is_not_there_is_not_an_error(self) -> None:
        """The pin's `|| true`, and the *normal* case in the stale cleanup that precedes a run.
        A `Result` rather than nothing, so the caller can still say so loudly."""
        self.double = Double(failure(1, stderr="Error: No such container: bessemer-work"))
        result = container.remove(container=CONTAINER, run=self.double)
        self.assertFalse(result.ok)
        self.assertEqual(self.double.argvs, [("docker", "rm", "-f", "-v", CONTAINER)])


class ImageContractTest(unittest.TestCase):
    """The complete list an adapter image must satisfy (F3 decision 7.4), as a literal."""

    CLAUSES: Final = (
        "bash at /usr/bin/bash",
        "coreutils timeout",
        "git",
        "the claude CLI on PATH",
        "a non-UID-0 agent user",
        "sudo plus the one sudoers line",
    )
    """Restated by hand. Doctor cannot police any of these — none are host facts — so the
    failure paths are the only place a bad image surfaces, and a clause quietly dropping off
    this list is a clause no error message names any more.

    The sudo clause is unconditional (ruled 2026-08-05). Decision 7.4 wrote it as "iff the
    setup hook needs root", which cannot hold while core invokes the hook through sudo on every
    dispatch — measured: an image with no sudo fails its own setup step, and so does one with
    sudo and no `SUDO_CAPABILITIES`.
    """

    def test_the_contract_is_exactly_these_clauses(self) -> None:
        self.assertEqual(container.IMAGE_CONTRACT, self.CLAUSES)

    def test_the_bash_clause_names_the_path_the_hook_is_invoked_with(self) -> None:
        """`/usr/bin/bash` is in the contract *because* it is in the sudoers line: the grant
        names an absolute path, so an image whose bash lives elsewhere fails the invocation
        rather than the lookup."""
        self.assertIn(container.SETUP_HOOK_COMMAND[1], container.IMAGE_CONTRACT[0])


class ModuleShapeTest(unittest.TestCase):
    """The interface ADR 0003 names, restated by hand."""

    INTERFACE: Final = (
        "forwarding",
        "credential_presence",
        "run_argv",
        "chown_argv",
        "setup_hook_argv",
        "remove_argv",
        "liveness_argv",
        "start",
        "run_setup_hook",
        "remove",
    )
    """ADR 0003's row is "start / `run_setup_hook` / pure argv builders / remove"; the builders
    are named here, and the ADR's table carries the same names as of 2026-08-05.

    `credential_presence` joined them at F3 issue 09, which is where doctor gained the check
    that needs it — one more public function is a decision to take to the ADR, not a diff to
    wave through, the treatment `tests/test_checkout.py` gives its four.

    `liveness_argv` joined them at issue 11, and it is a *move* rather than a growth:
    `bessemer.passes` declared it, `bessemer.reclaim` became its second consumer, and the
    anchored `docker ps -q -f name=^…$` is a container question. `passes.liveness_argv` is an
    alias, so the ADR's `passes` row is unchanged and this one gained a name.
    """

    TYPES: Final = ("Boundary", "Forwarding", "Credential", "SetupHookError")
    """Every public *type*, listed separately and just as strictly: a new return type is "the
    interface grew" as much as a new function is, and a filter that counted only functions
    would wave it through."""

    def defined_here(self, *, types: bool) -> tuple[str, ...]:
        """Public names this module defines itself, split into types and everything else.

        `__module__` filters out the imports — `proc`, `config` and the standard library — so
        this reads the module's own surface rather than its namespace.
        """
        return tuple(
            name
            for name, value in vars(container).items()
            if not name.startswith("_")
            and callable(value)
            and getattr(value, "__module__", None) == container.__name__
            and isinstance(value, type) is types
        )

    def test_the_public_functions_are_the_ones_the_adr_names(self) -> None:
        self.assertEqual(self.defined_here(types=False), self.INTERFACE)

    def test_the_public_types_are_the_ones_listed_here(self) -> None:
        self.assertEqual(self.defined_here(types=True), self.TYPES)

    def test_the_module_never_prints(self) -> None:
        """The withheld-key warning is a value the caller renders; this module has no voice.
        Two callers exist already — dispatch's console and the run log — and a `print` here
        would be a third that neither could suppress.

        Asserted over the source rather than by capturing stdout, because the arm that would
        print is the one a test has the hardest time reaching.
        """
        source = Path(container.__file__).read_text()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotEqual(node.func.id, "print")

    def test_every_effectful_operation_takes_its_runner_with_no_default(self) -> None:
        """ADR 0003: a default of `proc.run` is how a module acquires the real spawner by
        omission, and a test that forgot to pass the double would still go green."""
        for name in ("start", "run_setup_hook", "remove"):
            with self.subTest(name=name):
                parameter = inspect.signature(getattr(container, name)).parameters["run"]
                self.assertIs(parameter.default, inspect.Parameter.empty)
                self.assertIs(parameter.kind, inspect.Parameter.KEYWORD_ONLY)

    def test_the_argv_builders_spawn_nothing(self) -> None:
        """Tier 1 means tier 1: a builder that reached the proc seam would make every literal
        in this file an assertion about a recorded stream instead of about a value."""
        for name in ("run_argv", "chown_argv", "setup_hook_argv", "remove_argv"):
            with self.subTest(name=name):
                self.assertNotIn("run", inspect.signature(getattr(container, name)).parameters)


class PurityTest(ContainerTest):
    """The builders read no file either, which is the half a signature cannot show.

    `run_argv` used to call `is_file()` on the committed env file. Every argv assertion in this
    file stayed green, and the module's own claim — "nothing spawns; nothing reads a file" — was
    false for the one flag whose presence depends on the filesystem. The fix moved the read into
    `forwarding`; this is what stops it coming back, since the next person to need a disk fact
    in a builder will reach for exactly that call again.
    """

    def setUp(self) -> None:
        super().setUp()

        def refuse(*args: object, **kwargs: object) -> object:
            raise AssertionError("an argv builder touched the filesystem")

        for name in ("is_file", "exists", "is_dir", "open", "read_text", "stat", "iterdir"):
            patcher = mock.patch.object(Path, name, refuse)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_run_argv_touches_no_file(self) -> None:
        self.assertTrue(self.run_argv())

    def test_run_argv_touches_no_file_with_everything_turned_on(self) -> None:
        """The arm that had the defect: a committed env file present, volumes declared, names
        forwarded."""
        boundary = self.boundary(
            cap_add=["KILL"],
            volumes=["cache:/cache"],
            env=container.Forwarding(
                forwarded=(("DATABASE_URL", "x"),),
                withheld=("Y",),
                env_file=self.adapter / container.ENV_FILE,
            ),
        )
        argv = self.run_argv(boundary=boundary)
        self.assertIn("--env-file", argv)

    def test_the_other_three_builders_touch_no_file_either(self) -> None:
        self.assertTrue(container.chown_argv(container=CONTAINER, points=["/cache"]))
        self.assertTrue(container.setup_hook_argv(container=CONTAINER))
        self.assertTrue(container.remove_argv(container=CONTAINER))
