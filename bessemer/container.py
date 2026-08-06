"""The per-run container, and the whole of its privilege surface in one place.

A **Container** (CONTEXT.md) is the Docker container a run's agent executes inside. This
module owns everything that decides what that container may do: the capabilities it holds,
the limits it runs under, the paths mounted into it, and which of the host's secrets cross
the boundary. ADR 0003 gives it a home unmixed with git discipline for exactly that reason —
`checkout.py` is next door and their failure domains do not overlap.

Oracle region: `run.sh:1242–1282` at the pin.

## Two halves: pure builders, and three effectful operations

The argv builders (`run_argv`, `chown_argv`, `setup_hook_argv`, `remove_argv`) are tier-1
pure functions returning `list[str]`. Nothing spawns; nothing reads a file — a claim
`tests/test_container.py::PurityTest` holds by patching `Path`'s readers to raise, because the
first version of `run_argv` did read one and every argv assertion stayed green. That purity is
what makes the security assertions hand-written literals over a value rather than assertions
over a recorded stream: a docker argv is the privilege boundary written down, so it is checked
where it is built.

The one disk fact a flag depends on — whether the adapter's committed env file exists — is read
by `forwarding` and carried in `Forwarding.env_file`.

`start`, `run_setup_hook` and `remove` execute through the proc seam. Each takes its runner
(`proc.Runner`) as a required keyword with no default, so a caller cannot acquire the real
spawner by omission (ADR 0003).

**The child environment is the ambient one**, unlike `checkout.py`'s. `docker` reads
`DOCKER_HOST`, `DOCKER_CONTEXT` and the credential helpers out of it, and stripping them
would point bessemer at a different daemon than the operator's own `docker ps`. The boundary
that matters is not this process's environment but the one crossing into the container, and
that one is built explicitly below, argument by argument (ADR 0001).

## What the builders own, and what they refuse

- **`--cap-drop ALL`, always and unconditionally** (F3 decision 5.2). Capabilities come back
  from three places, and two of them are core's own:

  1. `SUDO_CAPABILITIES` — always, because the hook invocation always goes through sudo.
  2. `CHOWN_CAPABILITIES` — only when `container_volumes` is non-empty, because that is when
     core's chown of the mountpoints runs.
  3. the committed `container_cap_add` list — the adapter's facts, and nothing else.

  This is F3 decision 5.3's coupling rule stated once and applied twice: **core owns the
  capabilities its own argv consumes**, so an adapter never has to discover them. Upstream's
  six carried that cost invisibly — its "only what setup-db.sh needs" comment undercounted by
  five, three consumed by the orchestrator's `docker exec -u root chown` (run.sh:1269–1273)
  and two by the agent's `sudo`. Only `KILL` was ever hae's. Pinned both ways, and with
  `KILL`'s absence from core's own output asserted alongside.
- **The mount table is four sources and nothing else**: the checkout read-write at
  `CHECKOUT_MOUNT`, the adapter's setup hook read-only at `HOOK_MOUNT`, the spec read-only at
  `SPEC_MOUNT`, and each `container_volumes` entry. **The whole `.bessemer/` directory is
  never mounted** (pin comment, run.sh:1216) — it holds the gitignored `.env` and every
  other run's checkout. That absence is structural rather than checked: the only host paths
  this module can name are the three above, and `container_volumes` cannot carry a host path
  at all because `bessemer.config` refuses one at load.
- **No published ports, ever.** ADR 0001, and there is no parameter through which one could
  arrive.

## The environment boundary

ADR 0001 verbatim, and F3 decision 5.4: **bulk transfer only from committed files.** The
committed `.bessemer/container.env` crosses as `--env-file` when it exists, because a
committed file is reviewable in git and a real secret placed there is a visible mistake. The
gitignored `.bessemer/.env` crosses **only** as the names `container_env_keys` declares plus
`CREDENTIAL_NAMES`, each as its own explicit `-e NAME=value`. A key present in `.env` and
not forwarded produces one operator-facing warning naming the key and never its value: the
names alone tell a prompt-injected agent what secrets exist on the host.

Two residuals, recorded rather than discovered later:

1. *A forwarded value appears in the docker argv*, because `-e NAME=value` is the form the
   issue names. The one destination that quotes argv is `proc.Destination.HOST_LOG` — the
   dispatching developer's own machine, where `.bessemer/.env` already sits — and the
   agent-visible destinations quote no arguments at all (issue 02). The alternative form,
   bare `-e NAME` with the value supplied through the child's environment, keeps values out
   of argv entirely; it is not taken here because the flag form is the decision, and it is
   written down as the change to make if a run log ever leaves the host.
2. *Values are read from the file, never from the operator's shell.* The pin's container saw
   exactly `--env-file "$BOX/.env"`, so an exported-but-unwritten `ANTHROPIC_API_KEY` never
   crossed there either — while the pin's own credential check read the environment, so that
   machine passed preflight and started a container without a credential. **Closed at issue
   09 rather than inherited:** `credential_presence` below answers about both channels
   separately, and doctor FAILs on the exported-only one, naming the file as the fix. The
   union of the two is still available as `Credential.present`, which is what the pin was
   actually testing, so issue 10's preflight can refuse on either reading deliberately.

`.env` is parsed by **docker's `--env-file` grammar, not the shell's**, because docker's is
what the pin's container actually received: `#` comment lines, blank lines, `NAME=` and
everything after the first `=` taken verbatim — no quote stripping, no `$VAR` expansion, no
inline comments. See `_read_env_file` for the two lines that grammar drops.

## The setup hook

The invocation is `SETUP_HOOK_COMMAND`, and it is verbatim: sudo matches an argument list
exactly, so an extra flag is denied rather than ignored (ADR 0001). Core runs **exactly one
setup command — the hook** (F3 decision 5, confirmed alongside); upstream's settings-template
`cp` lines are adapter content that moves into hae's own hook at F7.

Before it, and only when there are volumes, one `docker exec -u root … chown agent
<mountpoints>` — **orchestrator privilege, not agent sudo** (pin :1269–1273). The
distinction is the whole reason the agent's sudoers line names one script instead of `chown`.

**The invocation is unconditional, and so is what it costs** (ruled 2026-08-05, from a
measurement; see `SUDO_CAPABILITIES`). Decision 7.4 wrote the image contract's sudo clause as
"iff the setup hook needs root". No adapter can be sudo-less while core invokes the hook
through sudo on every dispatch, so that clause is unconditional here.

Three alternatives were rejected, and each for its own reason:

- *Invoke sudo only when the adapter says the hook needs root.* Bessemer's own dogfood run
  would then take a branch no real adapter takes.
- *Run the hook as root with `docker exec -u root`.* This erases the pin's own distinction
  between orchestrator chown and scoped agent sudo (:1269–1273), and it makes the exact-match
  grant do nothing.
- *Let each adapter commit the two capabilities.* Core consumes them, so an adapter that
  forgets them gets a dispatch that dies at the hook.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from bessemer import config, proc

DOCKER: Final = "docker"
"""The one program this module runs. Named once so the recorded stream is greppable."""

CHECKOUT_MOUNT: Final = "/workspace"
"""Where the checkout is mounted, read-write. Decided here and nowhere else.

`.bessemer/Dockerfile` used to say this was undecided; it is decided now, and the prompts
say "the spec is `/spec.md`" about the other end of the same table. A second spelling of
either path in another module is the defect.

Named `CHECKOUT_MOUNT` rather than `WORKSPACE`, next to `HOOK_MOUNT` and `SPEC_MOUNT`:
"workspace" is on CONTEXT.md's avoid list for the **Checkout**, so it may be the path docker
is given and not the word bessemer's own code uses for the thing at the other end.
"""

HOOK_MOUNT: Final = "/bessemer/setup.sh"
"""Where the adapter's setup hook is mounted, read-only — and the path in the sudoers line.

Outside the checkout on purpose (ADR 0001): the checkout is writable by the agent, so a
sudoers entry naming the copy of the hook that arrives inside it would name a script the
agent can rewrite and then run as root.
"""

SPEC_MOUNT: Final = "/spec.md"
"""Where the run's spec is mounted, read-only. The path every prompt template names."""

HOOK_FILE: Final = "setup.sh"
ENV_FILE: Final = "container.env"
"""The committed container environment file, inside the adapter directory.

Upstream's `.env.sandbox` renamed: "sandbox" is on CONTEXT.md's avoid list (F3 decision 5.4).
"""

SECRETS_FILE: Final = ".env"
"""The gitignored file whose declared names — and only those — cross the boundary."""

CREDENTIAL_NAMES: Final = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
"""Bessemer's built-in credential names, forwarded without being declared.

An owned literal, and the only names that cross `SECRETS_FILE` without appearing in
`container_env_keys` — which is what lets an adopter who needs nothing extra never encounter
that key at all (ADR 0001). `credential_presence` reads these names and doctor calls that
rather than restating them (issue 09), so the presence check and the forwarding cannot come
to disagree about which names are the credential.

Both, not one: `CLAUDE_CODE_OAUTH_TOKEN` is a subscription login and `ANTHROPIC_API_KEY` is
an API key, and the pin's own `have_claude_credential` accepts either (run.sh:254).
"""

SETUP_HOOK_COMMAND: Final = ("sudo", "/usr/bin/bash", "/bessemer/setup.sh")
"""The hook invocation, spelled out whole because sudo matches it whole.

The image's grant is `agent ALL=(ALL) NOPASSWD: /usr/bin/bash /bessemer/setup.sh`, and sudo
compares argument lists — an extra flag is *denied*, not ignored. Written as one literal
rather than composed from `HOOK_MOUNT` so the string a reader compares against
`/etc/sudoers.d/agent` is right here; `tests/test_container.py` asserts the two agree, which
is the check a composition would have made vacuous.
"""

AGENT_USER: Final = "agent"
"""The in-container user the mountpoints are chowned to. The image contract's non-UID-0 user."""

SUDO_CAPABILITIES: Final = ("SETUID", "SETGID")
"""What core's own hook invocation needs, added **unconditionally**.

F3 decision 5.3's coupling rule, applied a second time: core owns the capabilities its own
argv consumes. `SETUP_HOOK_COMMAND` goes through `sudo`, and sudo changes both uid and gid, so
under `--cap-drop ALL` it cannot start at all.

**Measured on bessemer's own built image, 2026-08-05** — which is why this is a constant and
not an argument:

- no cap-adds → `sudo: unable to change to root gid: Operation not permitted`, rc 1
- `SETUID` alone → the same refusal, plus
  `sudo: error initializing audit plugin sudoers_audit`, rc 1
- `SETUID` and `SETGID` → the hook runs, rc 0

Unconditional, unlike `CHOWN_CAPABILITIES`, because the thing consuming them is
unconditional: the chown runs only when `container_volumes` is non-empty, and the hook
invocation runs on every dispatch. Leaving them to the adapter's committed
`container_cap_add` was rejected for the reason decision 5.3 rejected it for the chown — a
privilege the *core's* argv consumes, acquired by omission, and forgetting the pair means
every dispatch dies at the hook.

One residual, kept rather than papered over: with these two and no `AUDIT_WRITE`, sudo prints
`sudo: unable to send audit message: Operation not permitted` on stderr and proceeds. A
warning on the hook's stderr is cheaper than a third capability, and the hook's exit status is
what this module reads.
"""

CHOWN_CAPABILITIES: Final = ("CHOWN", "DAC_OVERRIDE", "FOWNER")
"""What core's own chown of the volume mountpoints needs, added iff there are volumes.

Not adapter facts and not negotiable: the chown is bessemer's, so its cost is bessemer's to
declare (F3 decision 5.3's coupling rule). `DAC_OVERRIDE` and `FOWNER` ride with `CHOWN`
because a volume's mountpoint can arrive with permissions that refuse the traversal and the
ownership change to a process that is root only inside a capability-dropped container.

Conditional where `SUDO_CAPABILITIES` is unconditional, and the difference is not a judgement
about risk: it is that the chown argv is only built when there is a mountpoint to chown.

Together the two constants account for five of the pin's six cap-adds (run.sh:1251–1252). The
sixth is `KILL`, which is hae's own adapter fact — its `setup-db.sh` signals a restarted
postmaster — and core never emits it. `tests/test_container.py` asserts that absence.
"""

CLAUDE_TUNING_ENV: Final = (
    ("BASH_DEFAULT_TIMEOUT_MS", "300000"),
    ("BASH_MAX_TIMEOUT_MS", "300000"),
)
"""Provider tuning that is core's, not the adapter's (F3 decision 5.5, run.sh:1261).

How long the agent's own `Bash` tool waits before giving up on a command. Core-owned because
it is provider-contract surface, like the stream filter — revisited when the provider
abstraction lands, and recorded beside that divergence rather than left as a magic pair.
"""

IMAGE_CONTRACT: Final = (
    "bash at /usr/bin/bash",
    "coreutils timeout",
    "git",
    "the claude CLI on PATH",
    "a non-UID-0 agent user",
    "sudo plus the one sudoers line",
)
"""What an adapter image must provide, complete (F3 decision 7.4). The owned list.

**The sudo clause is unconditional**, ruled 2026-08-05 at this issue. Decision 7.4 wrote it as
"iff the setup hook needs root", which cannot hold while core invokes the hook through sudo on
every dispatch: an image without sudo fails its own setup step, and one with sudo but no
`SUDO_CAPABILITIES` fails it too. The committed `.bessemer/Dockerfile` already shipped sudo and
the sudoers line, and its own comment already said why — the grant is kept even though this
repo's hook needs no root, because F3 must exercise the path every real adapter uses.

Doctor cannot police any of it — none are host facts — so the failure paths are where a bad
image surfaces, and their messages name the clause. `git` is on the list because the agent
commits in-container; a missing `timeout` is rc 127 on the first pass.
"""

MISSING_PROGRAM_RETURNCODE: Final = 127
"""What a shell exits when the program was not found. The image contract's own symptom."""

START_TIMEOUT_SECONDS: Final = 120.0
"""For `docker run --detach`. The image is already local — doctor and preflight check its
presence — so this creates a container and returns; a slow one means the daemon is wedged."""

EXEC_TIMEOUT_SECONDS: Final = 60.0
"""For the chown exec. It changes ownership of a mountpoint and nothing more."""

HOOK_TIMEOUT_SECONDS: Final = 1800.0
"""For the setup hook, which is where an adapter installs its dependencies (ADR 0001).

Generous because a cold package cache is the normal first run, and a backstop rather than a
deadline: the pin puts no limit on this step at all, and a hook that has hung is
indistinguishable from a hook that is still going except by waiting longer than any of them
take. Thirty minutes is longer than the per-pass timeout's default by design — a hook that
outlives it has failed at something a person needs to look at.
"""

REMOVE_TIMEOUT_SECONDS: Final = 60.0
"""For `docker rm`. Called from a `finally`, so a hang here is a run that never exits."""

CAPABILITY: Final = re.compile(r"(?i)(CAP_)?[A-Z][A-Z_]*\Z")
"""What a `container_cap_add` entry may look like: a Linux capability name.

Case-insensitive and with the `CAP_` prefix optional, because docker accepts all of those
spellings. The point of the pattern is not to enumerate the capabilities — that list grows
with the kernel — but to keep anything *flag-shaped* out of the position next to
`--cap-add`. It is the same rule `bessemer.config` applies to a volume source, arrived at
for the same reason: an entry beginning with `-` is a smuggled argument, not a typo.
"""

ENV_NAME: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
"""What an environment variable name may look like, on both sides of the boundary.

Used for the names read out of `SECRETS_FILE` and for the names a `Boundary` carries. `\\Z`
rather than `$`, which would match before a trailing newline.
"""

MEMORY_LIMIT: Final = re.compile(r"[0-9]+[bkmgBKMG]?\Z")
"""What `--memory` accepts: a byte count with an optional unit suffix. `8g`, `512m`, `1024`.

Checked because the limits are ADR 0001 security requirements rather than preferences, and
because `memory = "banana"` is otherwise a `docker run` failure with a checkout already
cloned — the same load-time-visibility argument `container_volumes` validation rests on.
"""


def _sequence_of_strings(key: str, values: Sequence[str]) -> tuple[str, ...]:
    """`values` as a tuple, or a loud refusal naming `key`.

    **A bare string is refused before anything is built from it.** `container_cap_add =
    "SETUID"` is a plausible mistake, a `str` satisfies `Sequence[str]` for a type checker,
    and iterating it yields characters — so a builder mapping entries to flags would emit
    `--cap-add S --cap-add E …`, silently widening nothing and refusing to start with an
    error about the letter `S`. `bessemer.config._name_list_refusal` checks the same shape at
    load, where it is a user's mistake and becomes a doctor line; this is the structural
    backstop one layer in, and it is exactly the guard `proc.run` carries against a `str` argv
    for the same reason.

    The predicate is deliberately in both places, and the two are not one function: config's
    answers "which of the user's files says something ill-formed" and returns prose for an
    `Unresolved`, this one answers "did bessemer hand itself a bad value" and raises. Sharing
    an implementation would mean config importing this module — which is the dependency
    direction that keeps config's load pure — or a third module for four lines of `isinstance`.

    Raises rather than returning a reason: by the time a value reaches here it has passed the
    loader, so a bad one is bessemer's own code handing itself a boundary value and must not
    be reported the way a user's typo is (`config._require_known`'s split).
    """
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{key} must be a list of strings, not a string: {values!r}")
    listed = tuple(values)
    for value in listed:
        if not isinstance(value, str):
            raise TypeError(f"{key} entry is not a string: {value!r}")
        if not value:
            raise ValueError(f"{key} has an empty entry")
    return listed


def _normalised(capability: str) -> str:
    """A capability name as docker compares it: upper case, `CAP_` prefix dropped.

    Docker accepts `chown`, `CHOWN` and `CAP_CHOWN` as one capability, so anything asking
    "is this already in the list" has to ask the same question docker would. Without it,
    `container_cap_add = ["cap_setuid"]` produces a second `--cap-add` for a capability core
    already added, and `["ALL"]` slips past a check written against `"ALL"` alone.
    """
    return capability.upper().removeprefix("CAP_")


def _absolute(name: str, path: Path) -> Path:
    """`path`, or a refusal naming the parameter. Docker reads a relative source as a name.

    `-v checkouts/slug:/workspace` does not mount `./checkouts/slug`: docker takes any source
    without a leading `/` as a volume *name*, so a relative path arrives as a request for an
    empty volume mounted over the checkout — an agent working in an empty tree, reported as
    nothing at all. Named here because the caller that gets this wrong is dispatch, once.
    """
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path, not {str(path)!r}")
    return path


def _mount_point(volume: str) -> str:
    """Where a `container_volumes` entry lands inside the container, as docker will read it.

    The two forms are `name:/path` and `/path` (F3 decision 5.3, enforced at load by
    `bessemer.config`), so the mountpoint is everything after the one `:` or the whole entry.

    **Repeated slashes are collapsed, and a trailing one is dropped**, because docker cleans
    the target path before it mounts and every check in this module has to ask about the path
    docker will use rather than the one the adapter typed. Without it,
    `container_volumes = ["cache://workspace"]` compares unequal to `CHECKOUT_MOUNT` as a
    string, is not `is_relative_to` it either, and then shadows the checkout anyway — the
    silent failure `Boundary` refuses `cache:/workspace` in order to prevent. `PurePosixPath`
    is deliberately not what does the collapsing: it preserves a leading `//`, which is where
    that hole came from.
    """
    _, separator, point = volume.partition(":")
    target = point if separator else volume
    collapsed = re.sub("/+", "/", target)
    return collapsed[:-1] if len(collapsed) > 1 and collapsed.endswith("/") else collapsed


@dataclass(frozen=True, kw_only=True)
class Boundary:
    """Everything config decides about the container: capabilities, limits, mounts, secrets.

    One parameter object rather than five keyword arguments repeated between `run_argv` and
    `start`, and it earns that by being where the values are *checked*. Constructing a
    `Boundary` is the last moment before an argv exists, so the acceptance criterion "a
    malformed `container_cap_add` fails before any argv is built" is discharged structurally:
    there is no route to `run_argv` that skips this constructor.

    The checks here are a backstop, not the user-facing home. `bessemer.config` refuses a
    malformed value at load, where it becomes a doctor line rather than a mid-dispatch crash;
    these raise, because a value arriving here has already passed that gate and a bad one is
    therefore bessemer's own bug (`config._require_known`'s split, again).

    **No field has a default, and every one is keyword-only.** ADR 0003's rule for the proc
    runner, applied to the other thing a caller must not acquire by omission: a `Boundary()`
    that stood for "no capabilities, the usual limits" would be how a call site stops
    declaring a privilege decision, and the limits' actual defaults live in
    `config.DEFAULTS` — restating them here would be two values that can disagree about a
    security posture.

    Frozen, and the sequences are normalised to tuples on the way in: every field is a
    privilege boundary, and one a caller's stray `append` could move afterwards is not a
    boundary — the same reason `config.Config.get` copies a list on the way out.
    """

    cap_add: Sequence[str]
    """Capabilities added back after `--cap-drop ALL`. The committed `container_cap_add`."""

    volumes: Sequence[str]
    """The committed `container_volumes`, each `name:/path` or `/path`."""

    pids_limit: int | str
    """`--pids-limit`. `str` is admitted because the environment layer carries strings."""

    memory: str
    """`--memory`, with docker's unit suffixes."""

    env: Forwarding
    """What the adapter's two env files contribute, read once by `forwarding`.

    The whole value rather than its `forwarded` pairs alone, so `run_argv` needs to ask the
    filesystem nothing: the committed file's presence is a fact read on disk, and a builder
    that read it would not be pure.
    """

    def __post_init__(self) -> None:
        caps = _sequence_of_strings("container_cap_add", self.cap_add)
        for capability in caps:
            if not CAPABILITY.match(capability):
                raise ValueError(f"container_cap_add entry is not a capability: {capability!r}")
            if _normalised(capability) == "ALL":
                # `--cap-add ALL` after `--cap-drop ALL` gives every capability back, which
                # makes the drop decorative — and the drop is the one thing in this argv that
                # no configuration may affect (F3 decision 5.2). A committed file is
                # reviewable, but this module claims the boundary holds whatever it says.
                raise ValueError(
                    "container_cap_add may not contain ALL: it would undo --cap-drop ALL"
                )

        volumes = _sequence_of_strings("container_volumes", self.volumes)
        for volume in volumes:
            point = PurePosixPath(_mount_point(volume))
            if not point.is_absolute() or ".." in point.parts:
                raise ValueError(
                    f"container_volumes entry has no usable mountpoint: {volume!r}"
                )
            if str(point) == CHECKOUT_MOUNT:
                # A volume mounted at the checkout's own path hides it: the agent would work
                # in an empty tree and commit nothing, with no error anywhere. Inside it is
                # the supported case (that is what the pin's `node_modules` volume is).
                raise ValueError(f"container_volumes entry would hide the checkout: {volume!r}")

        for name, _ in self.env.forwarded:
            if not ENV_NAME.match(name):
                raise ValueError(f"forwarded name is not an environment variable: {name!r}")

        if isinstance(self.pids_limit, str) and not self.pids_limit.isdigit():
            raise ValueError(f"pids_limit is not a number: {self.pids_limit!r}")
        if int(self.pids_limit) <= 0:
            # `--pids-limit 0` and `-1` are both docker's spelling of "unlimited", which is a
            # machine limit ADR 0001 requires, switched off by a value that looks like a value.
            raise ValueError(f"pids_limit is not positive: {self.pids_limit!r}")
        if not MEMORY_LIMIT.match(self.memory):
            raise ValueError(f"memory is not a docker memory limit: {self.memory!r}")
        if not self.memory.rstrip("bkmgBKMG").strip("0"):
            # `--memory 0` is docker's "unlimited" too, and `0g` reaches it by another
            # spelling. The same refusal as the line above, for the same reason: the two
            # limits are ADR 0001 security requirements, so "off" is not one of their values.
            raise ValueError(f"memory is not positive: {self.memory!r}")

        # Normalisation on a frozen dataclass, which is what `object.__setattr__` is for:
        # the declared types are the wider ones a caller may pass, the stored values are the
        # immutable ones this class promises.
        object.__setattr__(self, "cap_add", caps)
        object.__setattr__(self, "volumes", volumes)

    def capabilities(self) -> tuple[str, ...]:
        """Every capability added back: core's own first, then the adapter's committed list.

        The coupling rule in code (F3 decision 5.3, applied twice): core's unconditional
        `SUDO_CAPABILITIES`, then its `CHOWN_CAPABILITIES` when there is a mountpoint to chown,
        then whatever the adapter committed. Core's come first because they are the ones no
        configuration can remove, and an argv read top to bottom should reach the floor before
        it reaches the choices.

        Nothing is repeated, and "the same capability" is judged the way docker judges it:
        `SETUID`, `setuid` and `CAP_SETUID` are one capability, so an adapter that lists any of
        them gets it once. `--cap-add SETUID --cap-add cap_setuid` is harmless to docker and
        reads as a defect to whoever reviews the pinned argv. The adapter's own spelling
        survives when it is the one being kept, because it is the adapter's file.

        `KILL` never appears from here: it is the sixth of the pin's cap-adds and the only one
        that was ever hae's own, so it arrives, if at all, through `container_cap_add`.
        """
        capabilities = list(SUDO_CAPABILITIES)
        if self.volumes:
            capabilities += CHOWN_CAPABILITIES
        already = {_normalised(capability) for capability in capabilities}
        capabilities += [
            capability for capability in self.cap_add if _normalised(capability) not in already
        ]
        return tuple(capabilities)

    def mount_points(self) -> tuple[str, ...]:
        """Where each declared volume lands, in the order the adapter listed them.

        A method beside `capabilities` rather than a module-level function over
        `boundary.volumes`: both answer a question about this object's own data, and a caller
        reaching into the field to derive one of them is the shape that ends up deriving it
        twice, differently. Two consumers — the chown exec names them all, and `start`
        pre-creates the ones inside the checkout.

        Every value here has already been through `__post_init__`, so it is absolute, contains
        no `..`, and is not the checkout's own mount point.
        """
        return tuple(_mount_point(volume) for volume in self.volumes)


@dataclass(frozen=True)
class Forwarding:
    """Everything read off disk about the environment crossing into the container.

    Both files, in one value, read once: what the gitignored `SECRETS_FILE` may contribute,
    what it may not, and whether the committed `ENV_FILE` exists at all. It is one type rather
    than three returns because **`run_argv` has to stay pure** — a builder that asked the
    filesystem whether the committed file was there would be a tier-1 function that reads a
    file, and the module's own claim about its builders would be false.

    Otherwise the fact, not the response — the split `config.committed_only_violations` and
    `prompts.overridden` already make. `withheld` is what the operator is warned about; where
    that warning is *printed* is the caller's decision, and `warning` is what makes the one
    thing it may not do unavailable rather than merely forbidden.
    """

    forwarded: tuple[tuple[str, str], ...]
    """Name and value, in the order `SECRETS_FILE` lists them, for the names permitted to
    cross. File order rather than sorted so the argv reads against the file a reviewer has
    open."""

    withheld: tuple[str, ...]
    """Names present in `SECRETS_FILE` that do not cross, in file order. **Names only** —
    this type never carries a withheld value, so no caller can leak one by quoting it."""

    env_file: Path | None
    """The committed `ENV_FILE`, when the adapter has one; `None` when it does not.

    A path rather than a boolean, so the one flag built from it needs nothing else, and
    resolved here rather than in `run_argv` for the purity reason in the class docstring.
    """

    def warning(self, *, destination: proc.Destination) -> str | None:
        """The operator-facing line naming the withheld keys — or `None`, for two reasons.

        `None` when nothing was withheld, and `None` for any destination that is not the host.
        **That is issue 02's policy, reached through its own `Destination`** rather than
        restated as a rule about where callers may print this: ADR 0001 forbids the warning
        reaching the container log, a PR body, or a notification, because the names alone tell
        a prompt-injected agent what secrets exist on the host. Prose in a docstring cannot
        stop that; a function that answers `None` can.

        `proc.quote` itself is not what runs here — it answers for a `Result` and there is no
        child process in this story — but the classes of reader are the same classes, declared
        once in `proc.Destination`, so a third opinion about who may read what cannot appear.

        Keyword-only and no default, like `proc.quote`'s own parameter: the permissive row must
        not be reachable by omission, and every call site names the reader it is composing for.

        **Names, never values** (ADR 0001), and one line rather than one per key: the
        operator's decision is the same for all of them — declare it in `container_env_keys`,
        or leave it out — and a warning per key turns a routine `.env` with five unrelated
        entries into five lines nobody reads.
        """
        if not self.withheld or destination is not proc.Destination.HOST_LOG:
            return None
        names = ", ".join(self.withheld)
        return (
            f"not forwarded into the container: {names} — "
            f"add a name to container_env_keys in {config.COMMITTED_FILE} to forward it"
        )


def _read_env_file(path: Path) -> tuple[tuple[str, str], ...]:
    """`path` parsed by docker's `--env-file` grammar. An absent file is an empty one.

    Docker's grammar rather than the shell's, because docker's is what the pin's container
    actually received (`--env-file "$BOX/.env"`, run.sh:1263): a comment is a line whose first
    non-blank character is `#`, a blank line is skipped, and everything after the first `=`
    is the value **verbatim** — no quote stripping, no `$VAR` expansion, no inline comments.
    A `.env` written for `source` and read this way is the pin's own behaviour, divergences
    included.

    Two shapes are dropped rather than guessed at, and both are things docker does something
    else with: a bare `NAME` with no `=`, which docker fills from *its own* environment (a
    channel this module exists to not have), and a name that is not an environment variable
    name, which docker rejects outright. Dropping is safe in one direction only — a name that
    should have crossed and did not is the `withheld` warning's subject.

    A file that is there and cannot be read propagates, like `prompts.load`'s override and
    unlike `config`'s layers: the caller is a dispatch with a container about to start, and
    running the agent without the credential its adapter declared is a failure that surfaces
    twenty minutes later as an unexplained empty pass.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # The ordinary case for an adapter with no secrets at all — bessemer's own.
        return ()

    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, separator, value = stripped.partition("=")
        name = name.strip()
        if not separator or not ENV_NAME.match(name):
            continue
        # Last assignment wins, and keeps the position of the first: docker's own
        # last-wins behaviour, and a stable argv order for a file someone edited twice.
        values[name] = value
    return tuple(values.items())


def forwarding(*, adapter_dir: Path, declared: Sequence[str]) -> Forwarding:
    """Which names from the adapter's gitignored `.env` may cross, and which are withheld.

    `declared` is the committed `container_env_keys`. Permitted is that list plus
    `CREDENTIAL_NAMES` and nothing else — ADR 0001's enforcement half: a gitignored file
    forwards declared keys only, so the secret-class rule is a property of the code rather
    than of the adopter's restraint. A declared name that `.env` does not set contributes
    nothing; there is no error, because the adapter declaring a name it does not use on this
    machine is ordinary.

    It also reports whether the committed `ENV_FILE` is there, because this is the one function
    in the module that is allowed to look: the argv builders are pure, and something has to ask
    the filesystem for the two facts docker's flags are built from. Both reads happen here, in
    the same call, against the same adapter directory.
    """
    permitted = set(_sequence_of_strings("container_env_keys", declared)) | set(
        CREDENTIAL_NAMES
    )
    entries = _read_env_file(adapter_dir / SECRETS_FILE)
    committed = adapter_dir / ENV_FILE
    return Forwarding(
        forwarded=tuple((name, value) for name, value in entries if name in permitted),
        withheld=tuple(name for name, _ in entries if name not in permitted),
        env_file=committed if committed.is_file() else None,
    )


@dataclass(frozen=True)
class Credential:
    """Which of `CREDENTIAL_NAMES` are set, and through which channel. **Names, never values.**

    ADR 0001's "credential checks report presence only" as a type: there is no field here a
    caller could quote a secret out of, which is the same restraint `Forwarding.withheld`
    keeps for the names it reports.

    The two channels are kept apart because they do not mean the same thing. Only
    `in_secrets_file` crosses into the container — `forwarding` above reads that file and
    nothing else — so a credential that exists only in the operator's shell is a machine that
    cannot dispatch, and a check that merged the two would report it as healthy.
    """

    in_secrets_file: tuple[str, ...]
    """Names set in the adapter's gitignored `SECRETS_FILE`, in `CREDENTIAL_NAMES` order."""

    exported: tuple[str, ...]
    """Names set in the environment the caller handed in, in `CREDENTIAL_NAMES` order."""

    @property
    def crosses(self) -> bool:
        """Whether a credential would reach the agent inside the container."""
        return bool(self.in_secrets_file)

    @property
    def present(self) -> bool:
        """Whether either channel has one. The pin's `have_claude_credential`, exactly.

        The pin sources `SECRETS_FILE` into its own environment before asking (run.sh:246–248,
        :254), so "in the file" and "exported" are one question there. They are two here, and
        this property is the union the pin was actually testing.
        """
        return bool(self.in_secrets_file or self.exported)


def credential_presence(*, adapter_dir: Path, env: Mapping[str, str]) -> Credential:
    """Where a Claude credential is configured, if anywhere. One definition, two callers.

    Doctor renders this as a check line (F3 issue 09) and dispatch's preflight refuses on it
    (issue 10) — the same split `config.committed_only_violations` and `prompts.overridden`
    make, and the pin's own discipline for this exact check: `have_claude_credential` is shared
    by its doctor and its preflight so the two "can't drift apart" (run.sh:344–346). It lives
    here rather than in doctor because this module owns `CREDENTIAL_NAMES` and `SECRETS_FILE`,
    and the presence check and the forwarding must not come to disagree about either.

    **Either name counts, and an empty value does not.** `[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}
    ${ANTHROPIC_API_KEY:-}" ]` is the pin's test: a name exported as the empty string is a name
    with no credential in it, and treating it as set would be a green check on a machine that
    cannot authenticate.

    A `SECRETS_FILE` that is there and cannot be read propagates, exactly as `_read_env_file`
    says: the value of this function to a dispatch is that it fails loudly rather than starting
    a container without the credential. Doctor catches it and renders a line, because doctor's
    contract is the other one.
    """
    entries = dict(_read_env_file(adapter_dir / SECRETS_FILE))
    return Credential(
        in_secrets_file=tuple(name for name in CREDENTIAL_NAMES if entries.get(name)),
        exported=tuple(name for name in CREDENTIAL_NAMES if env.get(name)),
    )


def run_argv(
    *,
    container: str,
    image: str,
    checkout: Path,
    adapter_dir: Path,
    spec: Path,
    boundary: Boundary,
) -> list[str]:
    """The `docker run` argv, whole. Pure: this is the privilege boundary written down.

    **Pure means it asks the filesystem nothing.** Whether the committed `ENV_FILE` exists is a
    disk fact, and it arrives as `boundary.env.env_file` — read once by `forwarding`, where the
    other env-file read already happens. An earlier version called `is_file()` here, which made
    a tier-1 builder a function that reads a file and this docstring wrong;
    `tests/test_container.py::ModuleShapeTest` now pins the claim instead of stating it.

    Flag order is part of the contract `tests/test_container.py` pins as a literal, and it is
    the pin's own (run.sh:1244–1264): identity, capabilities, limits, mounts, then the
    environment, then the image. Reading top to bottom answers "what may this container do"
    before it answers "what is in it".

    **`--cap-drop ALL` is first and unconditional.** Nothing about it is derived from config,
    so no configuration can omit it (F3 decision 5.2).

    The absences are as much the contract as the flags: no `-p`/`--publish` (ADR 0001), no
    wholesale host environment, no mount of the adapter directory itself. See the module
    docstring for why the last one is structural rather than checked.
    """
    checkout = _absolute("checkout", checkout)
    adapter_dir = _absolute("adapter_dir", adapter_dir)
    spec = _absolute("spec", spec)
    for name, path in (("checkout", checkout), ("spec", spec)):
        if path == adapter_dir:
            # The named absence, refused at the one place it could arrive: the adapter
            # directory holds the gitignored `.env` and every other run's checkout, and
            # mounting it whole is the mistake the pin's :1216 comment exists to prevent.
            raise ValueError(f"{name} must not be the adapter directory itself: {str(path)!r}")

    argv = [DOCKER, "run", "-d", "--name", container, "--cap-drop", "ALL"]
    for capability in boundary.capabilities():
        argv += ["--cap-add", capability]
    argv += ["--pids-limit", str(boundary.pids_limit), "--memory", boundary.memory]
    argv += ["-v", f"{checkout}:{CHECKOUT_MOUNT}", "-w", CHECKOUT_MOUNT]
    argv += ["-v", f"{adapter_dir / HOOK_FILE}:{HOOK_MOUNT}:ro"]
    argv += ["-v", f"{spec}:{SPEC_MOUNT}:ro"]
    for volume in boundary.volumes:
        argv += ["-v", volume]
    for name, value in CLAUDE_TUNING_ENV:
        argv += ["-e", f"{name}={value}"]
    if boundary.env.env_file is not None:
        # The one bulk transfer, and it is bulk *because* the file is committed: a real
        # secret in it is a visible mistake in a diff (ADR 0001). Absent is the common case —
        # an adapter whose app needs no environment to boot under test writes no such file —
        # so the flag is conditional rather than the file being required to exist.
        argv += ["--env-file", str(boundary.env.env_file)]
    for name, value in boundary.env.forwarded:
        argv += ["-e", f"{name}={value}"]
    argv.append(image)
    return argv


def chown_argv(*, container: str, points: Sequence[str]) -> list[str]:
    """`docker exec -u root <cid> chown agent <mountpoints>` — orchestrator privilege.

    Root through docker rather than through the agent's sudo, and that distinction is the
    reason the sudoers line can name one script (pin :1269–1273). A volume's mountpoint is
    created root-owned by docker; the agent could not write to it, and granting the agent
    `chown` to fix that would be granting it root over the whole tree.

    One exec listing every mountpoint, as the pin does — the ownership change is one act, and
    a failure names the whole list either way.
    """
    return [
        DOCKER,
        "exec",
        "-u",
        "root",
        container,
        "chown",
        AGENT_USER,
        *_sequence_of_strings("mountpoints", points),
    ]


def setup_hook_argv(*, container: str) -> list[str]:
    """`docker exec <cid> sudo /usr/bin/bash /bessemer/setup.sh`. Verbatim; see
    `SETUP_HOOK_COMMAND`."""
    return [DOCKER, "exec", container, *SETUP_HOOK_COMMAND]


def remove_argv(*, container: str) -> list[str]:
    """`docker rm -f -v <cid>` (the pin's `-fv`, run.sh:1161).

    `-f` because the container is running — it is `sleep infinity` until something kills it —
    and `-v` for its anonymous volumes, which is what keeps a `container_volumes` entry
    written as a bare `/path` from outliving the run that made it.
    """
    return [DOCKER, "rm", "-f", "-v", container]


class SetupHookError(Exception):
    """The adapter's setup hook exited nonzero, so the dispatch aborts (ADR 0001).

    Carries the `Result`, so the caller can write the hook's own output to the run log —
    "surfaces the log" is the contract, and this exception is not the log.

    **The message is composed for `proc.Destination.HOST_LOG` and belongs nowhere else.** It
    quotes the failing argv and the first line of stderr through issue 02's policy, which
    makes it safe for the operator's console and the host-side run log and unsafe for a PR
    body or a notification. A caller composing either of those quotes the `Result` for the
    agent-visible destination instead; `str(exc)` is not a destination-neutral string.
    """

    def __init__(self, result: proc.Result) -> None:
        self.result = result
        super().__init__(_hook_failure(result))


def _hook_failure(result: proc.Result) -> str:
    """Why the run is aborting, naming the hook — and on 127, the image-contract clause.

    Two failures wear the same nonzero exit and need different readers to act. A hook that
    ran and failed is the adapter author's: their script, their log. Exit 127 is a program the
    *image* does not have — the shape a missing `timeout` or a missing `git` takes — so it
    names the contract rather than sending an author to debug a script that never ran.
    """
    detail = proc.quote(result, destination=proc.Destination.HOST_LOG)
    message = f"the setup hook {HOOK_MOUNT} exited {result.returncode}: {detail}"
    if result.returncode == MISSING_PROGRAM_RETURNCODE:
        clauses = "; ".join(IMAGE_CONTRACT)
        message += (
            f" — exit {MISSING_PROGRAM_RETURNCODE} is a program the image does not have. "
            f"An adapter image must provide: {clauses}"
        )
    return message


def start(
    *,
    container: str,
    image: str,
    checkout: Path,
    adapter_dir: Path,
    spec: Path,
    boundary: Boundary,
    run: proc.Runner,
) -> proc.Result:
    """Start the container detached, and return what docker said.

    Raises `proc.ProcessError` if it did not start. There is no useful half a container: a
    caller that went on to `docker exec` would report a missing container instead of the
    reason the run never began — the same reason `checkout.create` raises.

    **Volume mountpoints inside the checkout are pre-created first**, generalising the pin's
    `mkdir -p "$wt/client/node_modules"` (run.sh:1210–1212). It has to happen before the
    container exists, not after: docker copies a mountpoint's existing contents into a fresh
    anonymous volume when the container is created, and the directory being there and
    host-owned is what makes the volume arrive owned by the agent rather than by root.
    Upstream's line named one adapter's path; this one derives them from the volume list.
    """
    for point in boundary.mount_points():
        inside = PurePosixPath(point)
        if inside.is_relative_to(CHECKOUT_MOUNT):
            # `..` and a mountpoint equal to the mount root are both refused by `Boundary`, so
            # the relative path below cannot climb out of the checkout.
            (checkout / inside.relative_to(CHECKOUT_MOUNT)).mkdir(parents=True, exist_ok=True)

    result = run(
        run_argv(
            container=container,
            image=image,
            checkout=checkout,
            adapter_dir=adapter_dir,
            spec=spec,
            boundary=boundary,
        ),
        timeout=START_TIMEOUT_SECONDS,
    )
    if not result.ok:
        raise proc.ProcessError(result)
    return result


def run_setup_hook(*, container: str, boundary: Boundary, run: proc.Runner) -> proc.Result:
    """Chown the volume mountpoints, then run the adapter's hook once. Core's only setup step.

    Raises `SetupHookError` when the hook exits nonzero — ADR 0001's contract: a nonzero exit
    aborts the dispatch and the log is surfaced. Raises `proc.ProcessError` when the chown
    fails, which is a different failure with a different reader: the hook never ran, and a
    mountpoint the agent cannot write to would otherwise surface as an unexplained failure
    inside the first pass.

    The chown is skipped entirely when there are no volumes — there is nothing to own, and
    running it anyway would need `CHOWN` in a container that was deliberately not given it.
    """
    points = boundary.mount_points()
    if points:
        chown = run(
            chown_argv(container=container, points=points), timeout=EXEC_TIMEOUT_SECONDS
        )
        if not chown.ok:
            raise proc.ProcessError(chown)

    result = run(setup_hook_argv(container=container), timeout=HOOK_TIMEOUT_SECONDS)
    if not result.ok:
        raise SetupHookError(result)
    return result


def remove(*, container: str, run: proc.Runner) -> proc.Result:
    """Remove the container and its anonymous volumes. A container that is gone is not an error.

    Never raises on docker's exit status, and that is the pin's `|| true` (run.sh:1161, and
    the cleanup trap's second call at :1186): this is called from the stale-leftover cleanup
    that precedes a run and from the `finally` that ends one, and neither has anything better
    to do about a failure than what the caller already does — quote the returned `Result` to
    the host log. Removing what is not there is the *normal* case in the first of those.

    A `Result` rather than `None` so the caller can say so loudly when it did fail, because a
    container that could not be removed is exactly the leak `gc` exists to reclaim.
    """
    return run(remove_argv(container=container), timeout=REMOVE_TIMEOUT_SECONDS)
