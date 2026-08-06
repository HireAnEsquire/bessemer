"""The running definition of what bessemer can vouch for.

An **ordered list of small check functions over a shared context**. Each takes the context
and returns a `CheckResult`; dependency-skipping is expressed by asking the context about an
earlier result — `if not ctx.ok(CONFIG): return _skipped(...)`. Deliberately not a
declared-dependency registry (ADR 0002): twelve eventual checks do not buy back the
abstraction, and the port source's hand-written skip messages are better UX than any generic
auto-skip line.

Per ADR 0001's ops-as-library posture this module returns `list[CheckResult]` and never
prints; `bessemer.cli` renders. A future dashboard is then a frontend choice.

**Two contract behaviors, stated here because they are doctor's identity:**

- **A crashing check renders as FAIL with the exception text; the report always completes.**
  One broken check must never take down the report — working when things are broken is the
  whole point, and it is why `bessemer.config` is pure and the resolvers return
  value-or-reason instead of raising. See `run_checks` for what is and is not caught, and
  `_crashed` for what that FAIL line is allowed to say.
- **A skip counts as a failure for exit purposes.** Exit 0 only when every check is `ok` or
  `WARN`; exit 1 on any FAIL. A skip *is* a FAIL — carrying the port source's hand-written
  "skipped — … fix the check above first" as its message — rather than a fourth status, so
  the scriptable-gate semantics are structural instead of a rule someone has to remember.

**`ctx.ok()` raises on a name no earlier check produced**, which is distinct from "ran and
failed". A typo'd `ctx.ok("dokcer")` returning falsy would produce a check that skips forever
while looking principled — precisely the silent drift this design avoids. The list-level
ordering invariant is `tests/test_doctor.py`'s: it walks `CHECKS` and asserts every name a
check queries is emitted by a check earlier in the list, which is the registry's real safety
property expressed as data.

**The check list covers only what has been built.** Each later feature extends it as part of
its own slice (ADR 0002). A check that can only fail teaches nothing, and a doctor that WARNs
about unimplemented subsystems trains its reader to ignore doctor output.

**Nothing here reads `os.environ` or `Path.cwd()`; both live on the context.** One `start` is
threaded to `config.load`, `resolve_base` and `resolve_root_agreement` — let any of them
default independently and doctor reports on two different directories while printing one
report, and root agreement, which exists to catch exactly that disagreement, becomes the
check that cannot see it. `env` is a field for the same class of reason: a check that reads
the ambient environment itself can only be tested by mutating the test runner's own
environment, which makes the test an assertion about the host — green on the machine that has
the variable, red on the one that does not.

**Spawning goes through `ctx.run`, never `proc.run` directly.** `tests/guard.py` allowlists
`git` and the interpreter and deliberately does *not* allowlist `uv` or `docker`: a suite
permitted to run `docker` is one image pull away from network access on a contributor's
laptop, and ADR 0002 requires the suite to pass with no daemon at all. The seam is what makes
the three spawning checks testable without widening that allowlist, so the argv they would
spawn is pinned by a test that reads it off a stand-in runner rather than by one that spawns it.
"""

import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import takewhile
from pathlib import Path
from typing import Final

from bessemer import config, container, proc, prompts, redact, resolve
from bessemer.config import Config
from bessemer.outcome import Resolved, Unresolved

OK: Final = "ok"
WARN: Final = "WARN"
FAIL: Final = "FAIL"

STATUSES: Final = (OK, WARN, FAIL)
"""Every status a check can report. Three, and `tests/test_doctor.py` restates them by hand.

Spelled the way the port source prints them, lowercase `ok` and shouting `WARN`/`FAIL`
included: the whole point of the column is that a failure is visible while scrolling past a
green report, and lowercase `ok` is what makes the other two stand out.
"""

PASSING: Final = (OK, WARN)
"""The statuses that do not fail the run. A WARN is a fact about the machine, not a fault."""

UV: Final = "uv"
CONFIG: Final = "config"
GIT_ENV: Final = "git-env"
ROOT: Final = "root"
BASE: Final = "base"
CREDENTIAL: Final = "credential"

ENV_KEYS: Final = "env-keys"
"""With `CAP_ADD` and `VOLUMES`: three checks about three keys, not one listing offenders.

The report is read a line at a time, and a reader who set `container_cap_add` in their local
layer is looking for a line about capabilities.

The three names are shortened from the keys they are about. `container_cap_add` is eight
characters wider than `bessemer.cli`'s name column, so a line named for the key would push its
own message right and read as misaligned beside every other check. The key itself is in the
message, which is where a reader greps for it.
"""

CAP_ADD: Final = "cap-add"
VOLUMES: Final = "volumes"
PROMPTS: Final = "prompts"
GH: Final = "gh"
DOCKER: Final = "docker"
IMAGE: Final = "image"

NO_ADAPTER: Final = f"no adapter loaded, fix the {CONFIG} check above first"
"""Why the checks that read the adapter's own files skip. One sentence, eight callers.

Hand-written, which is what ADR 0002 asks of a skip message, and written once because it is
one sentence rather than eight: the port source's argument is that a skip should name the
check above that has to be fixed first, and every check that skips for this reason has the
same one above it. The `docker unavailable` skip in `_check_image` stays spelled where it is
used — it is the list's only second dependency, so a constant for it would be a name with one
call site.
"""

TIMEOUT_SECONDS: Final = 15.0
"""Every probe gets this, and no call site may omit it.

A backstop rather than a deadline, for the reason `bessemer.resolve.TIMEOUT_SECONDS` is one:
a wedged Docker daemon that hangs `docker info` forever must fail doctor rather than hang it.
Doctor is the command a user runs *because* something is broken.
"""

UV_ARGV: Final = ("uv", "--version")
"""What check 1 spawns. `--version` because the question is whether uv is there and runs."""

UV_FLOOR: Final = (0, 9, 0)
"""The oldest uv that can *fetch* a python this package will install under. Restated by hand
in `tests/test_doctor.py`.

**A floor bessemer states because nothing else can.** ADR 0001 originally took
`requires-python = ">=3.14"` to settle the interpreter question — uv reads it and fetches an
interpreter — and issue 06 declined a uv floor on exactly that reasoning. Issue 08's tracer
measured it false: `requires-python` describes what the *package* needs, not what the
*installer* can fetch. uv 0.8.x reads `>=3.14`, downloads its own default (3.13.5), and then
fails the resolve, because the newest 3.14 it can offer is `cpython-3.14.0b4` — a prerelease,
which `>=3.14` excludes. uv 0.9.0 is the first that offers a stable 3.14.

**WARN, not FAIL, and the reason is not that FAIL is merely harsh — it is that FAIL is a claim
this line refutes by existing.** `requires-python` is `>=3.14`, so a doctor that is running at
all was installed under a satisfying interpreter: installation already succeeded. An old uv
below this floor gets there whenever a stable 3.14 is *already on the machine* — measured on the
host that built this check, where uv 0.8.0 installed and ran the very bessemer printing the
line, against Homebrew's python 3.14.6. A FAIL saying "bessemer cannot be installed by this uv"
is therefore false on every host that can read it.

What is true names its condition: this uv cannot download a stable 3.14, so bessemer will not
install on a machine that does not already have one. That is a warning about the *next* machine
— a colleague's, a fresh CI image, this one after its system python moves — which is a fact
about the machine that leaves bessemer working, which is exactly what WARN is for.

*(Issue 08's criterion said "FAIL rather than WARN — nothing works below it". Corrected
host-side after this check shipped the false version and the report quoted its own disproof.)*
"""


def _version_text(parts: Sequence[int]) -> str:
    """A parsed version back as a string, for a message. `(0, 9, 0)` → `"0.9.0"`."""
    return ".".join(str(part) for part in parts)


@dataclass(frozen=True)
class UvVersion:
    """A parsed `uv --version`: what uv called itself, and what that compares as.

    Both halves are kept because each is wrong for the other's job. `parts` is padded and
    truncated so the comparison is numeric (see `_uv_version`), which makes it the wrong thing
    to print — a uv that says `uv 0.8` would be quoted back "uv 0.8.0 is older than…", telling
    the reader its version is a string it has never seen. `text` is uv's own spelling and is
    only ever printed.
    """

    text: str
    parts: tuple[int, ...]


def _uv_version(stdout: str) -> UvVersion | None:
    """The version out of `uv --version`, or `None` if that output held no readable one.

    uv prints `uv 0.9.2 (0aa1e5d 2026-07-11)` — the name, the version, then a build the
    format of which varies by installer (`(Homebrew 2025-07-17)` is another real one). Only
    the second field is read, and only its leading digits per component, so a suffix like
    `0.9.0rc1` compares as `(0, 9, 0)` rather than refusing to parse.

    **Padded to three components so the comparison is numeric throughout.** `(0, 9)` sorts
    below `(0, 9, 0)`, which would fail a `uv 0.9` that is exactly at the floor. The padding is
    also what makes the tuple the right shape to compare: string comparison would put `0.10.0`
    *below* `0.9.0`, which is the one mistake a version floor cannot afford to make, and
    `tests/test_doctor.py` pins that direction specifically.

    Returns `None` rather than raising or guessing, because a uv that changed its output format
    is not a uv that is too old — see `_check_uv` for what that distinction prints.
    """
    fields = stdout.split()
    if len(fields) < 2:
        return None
    parts: list[int] = []
    for component in fields[1].split(".")[:3]:
        digits = "".join(takewhile(str.isdigit, component))
        if not digits:
            return None
        parts.append(int(digits))
    return UvVersion(text=fields[1], parts=tuple(parts + [0] * (3 - len(parts))))


DOCKER_ARGV: Final = ("docker", "info")
"""What check 6 spawns. `info` is the cheapest question only a live daemon can answer.

`docker --version` would answer about the CLI alone and report a healthy docker on a machine
whose daemon is stopped, which is the failure this check exists for — and it is the one the
port source separates too, `command -v docker` then `docker info`. Here one command covers
both: the CLI being absent raises `OSError` out of the spawn and never reaches a returncode.
"""


GH_ARGV: Final = ("gh", "auth", "status")
"""What the `gh` check spawns, and it answers both halves of the question at once.

The pin asks twice — `command -v gh`, then `gh auth status` (run.sh:381–387) — and gets a
different message from each. One command gets both here for the reason `DOCKER_ARGV` does: an
absent binary raises `OSError` out of the spawn and never reaches a returncode, so the two
cases are already distinguishable without a second spawn.

`gh auth status` and not `gh --version`: bessemer's only use of `gh` is opening the pull
request that ends a run (`bessemer.landing`), and an unauthenticated CLI fails that at the
last step of a run that has already spent its money.
"""

IMAGE_INSPECT_ARGV: Final = ("docker", "image", "inspect")
"""The prefix of what the `image` check spawns; the configured image is appended.

The pin's own preflight question, verbatim (`docker image inspect "$IMAGE"`, run.sh:948).
Nothing here asks about staleness — the pin's `image_staleness` is F5's and a stale image
still runs, so a doctor at F3 that reported on it would be reporting from a check bessemer
has not built.
"""


@dataclass(frozen=True)
class CheckResult:
    """One line of the report: what was checked, how it went, and what to do about it."""

    name: str
    status: str
    message: str
    hint: str = ""
    """What to type. Empty on `ok`, where there is nothing to fix."""

    @property
    def passed(self) -> bool:
        """Whether this result lets the run exit 0. A skip is a FAIL, so it does not."""
        return self.status in PASSING


Runner = proc.Runner
"""The one spawn seam. `bessemer.proc.run` satisfies it; tests hand in a stand-in.

An alias, not a declaration. It was declared here first, and F3 issue 04 needed the same
protocol for `bessemer.checkout` — so the declaration moved to `bessemer.proc`, where it is
the shape of that module's own `run`, and this name stays as the one every check and every
doctor test already spells. Two identical protocol declarations would have been
interchangeable by structural typing and drifted anyway; see `proc.Runner` for the argument.
"""


@dataclass
class Context:
    """What the checks share: where to look, what the environment says, and what ran already.

    Mutable, unlike everything else in this package that is a dataclass, and deliberately so:
    it accumulates results as the list runs, which is what `ok` reads. The alternative —
    threading an immutable accumulator through every check — would put the dependency
    machinery back that ADR 0002 rejected.
    """

    start: Path | None = None
    """The directory every check asks about, or `None` for the current one.

    Threaded to `config.load`, `resolve_base` and `resolve_root_agreement` alike. `None` is
    passed through rather than resolved here: each of those already treats `None` as "the
    current directory", and `Path.cwd()` raises when the working directory has been deleted —
    a case `resolve` already has a reason for, and one this module must not turn into a
    traceback by calling `cwd()` itself.

    A caller that *can* resolve it should: `bessemer.cli._start` reads the working directory
    once and passes it, so the three operations share one directory by construction rather
    than by three independent reads agreeing. `None` remains what arrives when even that
    read failed.
    """

    env: Mapping[str, str] = field(default_factory=dict)
    """What the developer exported. A field, never a read of `os.environ` inside a check.

    Defaults to empty rather than to the ambient environment: a default that reached out to
    the process would make every test that forgot to pass one an assertion about the host.
    `bessemer.cli` passes `os.environ`, which is the one place that decision belongs.
    """

    run: Runner = proc.run
    """How a check spawns. See the module docstring for why this is a seam and not a call."""

    results: dict[str, CheckResult] = field(default_factory=dict)
    """Every check that has run, by name. Insertion-ordered, which is check order."""

    config: Config | None = None
    """The loaded adapter, once the config check has loaded it.

    Held here so the two resolver checks below use the same `Config` the config check
    reported on rather than loading their own — three loads of the same files could report
    three different things if something rewrote one mid-report, and doctor would print all
    three without noticing.
    """

    def record(self, result: CheckResult) -> None:
        """Remember a result so later checks can ask about it."""
        self.results[result.name] = result

    def ok(self, name: str) -> bool:
        """Whether the named check passed. Raises if no earlier check produced that name.

        The raise is the point (ADR 0002): a typo'd name returning falsy would produce a
        check that skips forever while looking principled, and a skip is a failure, so the
        report would go red for a reason nobody could find. `ValueError`, the same way
        `bessemer.config._require_known` refuses a key this package's own code got wrong.
        """
        if name not in self.results:
            raise ValueError(
                f"no check named {name!r} has run; doctor's checks are asked about in list "
                f"order and this one has produced {sorted(self.results)}"
            )
        return self.results[name].passed

    def require_config(self) -> Config:
        """The loaded adapter, for a check that has already asked `ok(CONFIG)`.

        Raises rather than returning `None`, so a check that reads it without asking first is
        a crash line naming itself — which the report survives — instead of a `None` quietly
        travelling into a resolver.
        """
        if self.config is None:
            raise ValueError(f"the {CONFIG} check has not loaded an adapter")
        return self.config


def _ok(name: str, message: str) -> CheckResult:
    return CheckResult(name=name, status=OK, message=message)


def _warn(name: str, message: str, hint: str) -> CheckResult:
    return CheckResult(name=name, status=WARN, message=message, hint=hint)


def _fail(name: str, message: str, hint: str) -> CheckResult:
    return CheckResult(name=name, status=FAIL, message=message, hint=hint)


def _skipped(name: str, because: str) -> CheckResult:
    """A check that could not run because an earlier one failed. FAIL, and hand-written.

    Ported verbatim in shape from the port source's `doctor_fail image "skipped — docker
    unavailable, fix the docker check above first"`: a FAIL, so the exit status is right
    without a fourth status existing, and a message written for the specific pair rather than
    generated. The generic line a registry would produce is the thing being given up here,
    and it is the thing worth giving up — this one tells the reader which line above to read.

    No `hint`: the message is the hint, and a second line repeating it in other words is one
    more line between the reader and the failure that actually needs fixing.
    """
    return CheckResult(name=name, status=FAIL, message=f"skipped — {because}")


def _crashed(name: str, error: BaseException) -> CheckResult:
    """A check raised. The line that keeps the report complete — and the one that redacts.

    **This is the first place in bessemer where an unredacted exception could reach output**,
    which is why the decision is written down rather than left to the renderer. Issue 03
    raises `ProcessError` with the child's `stderr` in its message, issue 05 established that
    git's `stderr` can carry a token out of a remote URL, and doctor prints to a terminal
    today and into a pull request body from F3. So the exception text goes through
    `bessemer.redact` — the same redactor the resolvers' reasons use, promoted to a shared
    home by this issue rather than copied, because a second regex is two redactors that can
    disagree and the one that disagrees silently is the one printing into a PR.

    The type name is kept and the text redacted rather than dropped: a crash line reading
    only "the docker check crashed" sends its reader nowhere, and this is a bug report the
    user is being asked to relay.

    **Not the only site, and no longer the only pinned one.** Every other message that carries
    text bessemer did not write redacts too — the `OSError` in `_probe`, a nonzero
    `uv --version`, `gh auth status`, `docker info` or `docker image inspect`, and the
    `OSError` from an unreadable `.env` in `_check_credential`.
    `tests/test_doctor.py::RedactionTest` asserts it site by site, from a hand-written list:
    the first review of F1's issue found three of them correct but unpinned, which is a green
    suite standing behind three redactions nothing would have noticed losing.
    """
    return CheckResult(
        name=name,
        status=FAIL,
        message=(
            f"the {name} check crashed and could not report: "
            f"{type(error).__name__}: {redact.detail(str(error))}"
        ),
        hint=(
            "this is a bug in bessemer rather than a problem with your machine — the rest of "
            "the report below is unaffected; please report the line above"
        ),
    )


def _saying(detail: str) -> str:
    """`detail` as a clause to append to a message, or nothing at all if there is none.

    The same shape as `bessemer.resolve._saying`, and deliberately not shared with it: it is
    two lines of punctuation, and a `bessemer.text` module holding it would be an abstraction
    invented for the second use of a conditional colon.
    """
    return f": {detail}" if detail else ""


def _probe(
    ctx: Context, name: str, argv: Sequence[str], *, missing_hint: str
) -> proc.Result | CheckResult:
    """Run one external program, converting the two cases where nothing ran into lines.

    `bessemer.proc.run` raises rather than inventing a returncode when the program could not
    be executed (`OSError`) or was killed for exceeding its timeout (`TimeoutExpired`). Both
    are absorbed here with their own message, exactly as `bessemer.resolve._git` absorbs
    them: "docker is not installed" is close to the most important thing doctor ever says, so
    letting it reach `_crashed` would report bessemer's own bug for the user's missing binary.

    The `OSError` text is the operating system's — "No such file or directory" and "Permission
    denied" have different fixes — and it is redacted for the same reason everything else
    another program wrote is: this is the module that must not be the exception to that rule.
    That claim is checked rather than asserted; see `_crashed` and
    `tests/test_doctor.py::RedactionTest`. The `TimeoutExpired` branch below carries no such
    text on purpose — `subprocess.TimeoutExpired` holds the child's captured `stdout` and
    `stderr`, so its message is built from bessemer's own argv and timeout instead, the same
    decision `bessemer.resolve._git_timed_out` records.
    """
    command = " ".join(argv)
    try:
        return ctx.run(list(argv), timeout=TIMEOUT_SECONDS)
    except OSError as error:
        return _fail(
            name,
            f"`{command}` could not be run{_saying(redact.detail(str(error)))}",
            hint=missing_hint,
        )
    except proc.TimeoutExpired:
        return _fail(
            name,
            f"`{command}` did not finish within {TIMEOUT_SECONDS:g} seconds and was killed",
            hint=f"run `{command}` yourself to see what it is waiting on, then try again",
        )


def _check_uv(ctx: Context) -> CheckResult:
    """uv is installed, new enough to install bessemer, and this is the interpreter it ran.

    First in the list because it is the thing that supplies everything else: ADR 0001's
    distribution decision is `uvx --from <pinned source> bessemer …`, so a machine without uv
    cannot run the pinned core at all, whatever else is healthy.

    **uv's version is checked against `UV_FLOOR`; the interpreter's is not.** The two halves
    look symmetrical and are not. `requires-python` really does enforce the interpreter floor,
    at install time and with a better message than doctor could produce, so a check here could
    only fire when packaging metadata had already been ignored. It does not enforce uv's own,
    because it is a statement about the package rather than about the installer reading it —
    see `UV_FLOOR` for what that costs an adopter who is below it. The interpreter version is
    still printed: it is the first thing a bug report needs.

    **The uv reported on is the one on `PATH`, which need not be the one that installed this
    bessemer.** Measured: under `uvx uv@0.9.0 tool run …` the line reads `ok uv 0.9.0` while
    the `PATH` uv is 0.8.0. That is the right subject anyway — the floor is advice about what
    the developer's own `uvx …` invocations will be able to fetch tomorrow, and it is `PATH`'s
    uv that runs those. It does mean the line is not a claim about this process's provenance,
    and nothing here should be written as though it were.
    """
    probe = _probe(
        ctx,
        UV,
        UV_ARGV,
        missing_hint=(
            "install uv (https://docs.astral.sh/uv/getting-started/installation/) — bessemer "
            "is run through `uvx --from <source> bessemer`"
        ),
    )
    if isinstance(probe, CheckResult):
        return probe
    interpreter = ".".join(str(part) for part in sys.version_info[:3])
    if not probe.ok:
        return _fail(
            UV,
            f"`{' '.join(UV_ARGV)}` exited {probe.returncode}"
            f"{_saying(redact.detail(probe.stderr))}",
            hint="reinstall uv, then check that `uv --version` works in your shell",
        )
    found = _uv_version(probe.stdout)
    if found is None:
        return _warn(
            UV,
            f"`{' '.join(UV_ARGV)}` printed no version bessemer could read, so the uv "
            f"{_version_text(UV_FLOOR)} or newer it needs is unchecked — running under "
            f"python {interpreter}",
            hint=(
                f"run `{' '.join(UV_ARGV)}` yourself and check it reports "
                f"{_version_text(UV_FLOOR)} or newer"
            ),
        )
    if found.parts < UV_FLOOR:
        return _warn(
            UV,
            f"uv {found.text} cannot download a stable python 3.14, so bessemer will not "
            f"install on a machine that does not already have one — uv "
            f"{_version_text(UV_FLOOR)} or newer can (running under python {interpreter})",
            hint=(
                "upgrade uv with `uv self update`, or through whatever installed it "
                "(`brew upgrade uv`); this machine already has a python bessemer runs on, so "
                "nothing here is broken — a machine without one would fail to install it"
            ),
        )
    return _ok(UV, f"{probe.stdout.strip()}, running under python {interpreter}")


def _check_config(ctx: Context) -> CheckResult:
    """The adapter directory was found and both TOML layers parsed.

    The loaded `Config` is kept on the context, because the two resolver checks below need
    the same one — see `Context.config`.
    """
    match config.load(start=ctx.start, env=ctx.env):
        case Resolved(value=cfg):
            ctx.config = cfg
            return _ok(CONFIG, f"adapter at {cfg.adapter_dir}")
        case Unresolved() as unresolved:
            return _fail(CONFIG, unresolved.reason, hint=unresolved.hint)


def _check_git_env(ctx: Context) -> CheckResult:
    """Nothing in the environment redirects git at somewhere other than the repository here.

    **This check exists because issue 05 made the resolvers immune to those variables.** That
    immunity is exactly what makes the check necessary: bessemer now answers correctly about
    the repository on disk while every git command the developer types by hand answers about
    somewhere else — and bessemer is the only thing in the room that knows.

    WARN rather than FAIL: a poisoned shell is the user's environment, not a broken adapter,
    and nothing bessemer does is wrong because of it. It sits immediately before root
    agreement so the explanation lands next to the check whose result it would otherwise make
    baffling.

    The names come from `resolve.REDIRECTING_VARIABLES` rather than being restated here. That
    list is issue 05's and is pinned by a literal in `tests/test_resolve.py`; a second
    hand-written copy would be two lists to keep in step, which is a different defect from the
    one the literal rule prevents.

    **Names only, never values.** ADR 0001's credential checks report presence only, and the
    same restraint applies to a path the user exported: the fix is spelled by the name.
    """
    exported = sorted(name for name in resolve.REDIRECTING_VARIABLES if name in ctx.env)
    if not exported:
        return _ok(GIT_ENV, "no git location variables exported")
    return _warn(
        GIT_ENV,
        f"{', '.join(exported)} exported — bessemer strips these and answers about the "
        f"repository on disk, but git commands you type yourself will not",
        hint=(
            f"unset {' '.join(exported)} in this shell if you did not mean to redirect git; "
            f"bessemer's own answers below are unaffected either way"
        ),
    )


def _check_root(ctx: Context) -> CheckResult:
    """The adapter directory and the git work tree root are the same directory.

    Load-bearing rather than hygienic (ADR 0002): the invariant "the host pushes from the main
    repository" only names something unambiguous once the two are known to be one directory.
    Doctor reports it; dispatch will refuse on the identical call.
    """
    if not ctx.ok(CONFIG):
        return _skipped(ROOT, NO_ADAPTER)
    match resolve.resolve_root_agreement(ctx.require_config(), start=ctx.start):
        case Resolved(value=root):
            return _ok(ROOT, f"adapter and git work tree agree on {root}")
        case Unresolved() as unresolved:
            return _fail(ROOT, unresolved.reason, hint=unresolved.hint)


def _check_base(ctx: Context) -> CheckResult:
    """The base branch a run's pull request would target, from config or from `origin/HEAD`.

    Depends on config for the same reason root agreement does — a configured `base`
    short-circuits auto-detection, and which layer set it is part of the reason when it is
    unusable — and it is reported after root agreement because a base read out of the wrong
    repository is worth knowing about only once that repository is the right one.
    """
    if not ctx.ok(CONFIG):
        return _skipped(BASE, NO_ADAPTER)
    cfg = ctx.require_config()
    match resolve.resolve_base(cfg, start=ctx.start):
        case Resolved(value=branch):
            source = cfg.layer_of("base") or f"{resolve.ORIGIN}/HEAD"
            return _ok(BASE, f"{branch} (from {source})")
        case Unresolved() as unresolved:
            return _fail(BASE, unresolved.reason, hint=unresolved.hint)


def _check_credential(ctx: Context) -> CheckResult:
    """A Claude credential is configured, and configured where a run can actually reach it.

    **Presence only, never a value or a fragment of one** (ADR 0001). The names are the
    credential's own, so they are printed; nothing here reads a value except to ask whether it
    is empty, and `container.Credential` has no field a value could be quoted out of.

    The rule is `bessemer.container.credential_presence`, not a second copy of it — the pin's
    own discipline for this check (`have_claude_credential`, "not a second copy of the logic",
    run.sh:344–346), which is also ADR 0002's: dispatch's preflight calls the same function
    and refuses on what doctor renders here.

    **Two channels, and only one of them crosses into the container.** The gitignored
    `.bessemer/.env` is where ADR 0001 says secrets live, and `container.forwarding` reads that
    file and nothing else — so a credential exported in the operator's shell and never written
    to the file is a machine that passes every other check and produces a run whose agent
    cannot authenticate. That case is a FAIL naming the file, rather than the pin's `ok`: the
    pin sourced `.env` into its own environment before asking, which made the two channels one
    question there and left this exact hole open (`bessemer.container`, residual 2).

    Depends on config because the file it reads is inside the adapter directory. A credential
    is not actionable without an adapter anyway — there is nothing to dispatch — so the skip
    costs the reader nothing the config line above it does not already say.
    """
    if not ctx.ok(CONFIG):
        return _skipped(CREDENTIAL, NO_ADAPTER)
    adapter_dir = ctx.require_config().adapter_dir
    secrets_file = adapter_dir / container.SECRETS_FILE
    try:
        found = container.credential_presence(adapter_dir=adapter_dir, env=ctx.env)
    except OSError as error:
        # The resolver lets this through on purpose — a dispatch must not start a container
        # on a credential file it could not read. Doctor is the other contract, so the same
        # failure becomes a line. Redacted like every other message here that carries text
        # bessemer did not write.
        return _fail(
            CREDENTIAL,
            f"{secrets_file} could not be read{_saying(redact.detail(str(error)))}",
            hint=f"check that {container.SECRETS_FILE} is a readable file",
        )
    if found.crosses:
        return _ok(
            CREDENTIAL,
            f"{', '.join(found.in_secrets_file)} set in {secrets_file} (value not shown)",
        )
    if found.exported:
        return _fail(
            CREDENTIAL,
            f"{', '.join(found.exported)} is exported in this shell but not set in "
            f"{secrets_file}, and only that file's values cross into the container",
            hint=(
                f"write the credential into {container.SECRETS_FILE} as well — an exported "
                f"value is not forwarded, so a run would start without one"
            ),
        )
    return _fail(
        CREDENTIAL,
        f"no Claude credential: neither {' nor '.join(container.CREDENTIAL_NAMES)} is set "
        f"in {secrets_file}",
        hint=(
            f"run `claude setup-token` and paste the token into "
            f"{container.CREDENTIAL_NAMES[0]} in {container.SECRETS_FILE}"
        ),
    )


def _committed_only(ctx: Context, name: str, key: str) -> CheckResult:
    """One committed-only key, reported over both channels a user can set it through.

    **The local layer's violation is a loader fact; the environment's is doctor's own.** The
    two arrive differently on purpose. `config.committed_only_violations` exposes a key set in
    `config.local.toml`, and doctor renders it while dispatch refuses on the identical value
    (ADR 0002) — neither reimplements the check. The environment has no such fact to expose:
    `config._env_layer` is built from `KNOWN_KEYS` minus `COMMITTED_ONLY_KEYS`, so
    `BESSEMER_CONTAINER_ENV_KEYS` is dropped *by construction* and no layer ever carried it.
    That is the right thing for the loader to do and it means nobody is told, so the one user
    who tries it gets silence — which is why this check looks at the environment directly
    (added from issue 01's review, 2026-08-05).

    Dropped is not the same as reported, and the difference matters to the person who wrote
    the variable: their intent was to widen the container's boundary from the least reviewable
    place there is, and they are entitled to know it did nothing.

    **The key, never the value.** The same restraint the git-env check keeps for a path the
    user exported: the fix is spelled by the name, and a `container_env_keys` value is a list
    of secret names.
    """
    cfg = ctx.require_config()
    variable = config.ENV_PREFIX + key.upper()
    channels = []
    if key in cfg.committed_only_violations():
        channels.append(f"set in {config.LOCAL_FILE}")
    if variable in ctx.env:
        channels.append(f"exported as {variable}, where it is dropped rather than read")
    if not channels:
        return _ok(name, f"{key} is set nowhere but {config.COMMITTED_FILE}")
    return _fail(
        name,
        f"{key} is {' and '.join(channels)}; it may only be set in {config.COMMITTED_FILE}",
        hint=(
            f"move {key} into {config.COMMITTED_FILE}, where widening the container's "
            f"boundary is a reviewable diff"
        ),
    )


def _check_env_keys(ctx: Context) -> CheckResult:
    """`container_env_keys` is set in the committed layer or nowhere (ADR 0001).

    Both channels — the local layer and the environment — are `_committed_only`'s subject, and
    the reason the second one is doctor's own observation rather than a loader fact is written
    there.

    Written out beside its two siblings rather than generated from a table, so that the
    dependency each declares is visible to `tests/test_doctor.py`'s ordering reader — which
    walks the function named in `CHECKS` and looks for `ctx.ok` calls in *it*. A factory would
    move the query one call deeper, where the invariant that keeps this list in dependency
    order cannot see it.
    """
    if not ctx.ok(CONFIG):
        return _skipped(ENV_KEYS, NO_ADAPTER)
    return _committed_only(ctx, ENV_KEYS, "container_env_keys")


def _check_cap_add(ctx: Context) -> CheckResult:
    """`container_cap_add` is set in the committed layer or nowhere (F3 decision 5.2).

    Both channels, and the guard written out, for the reasons `_check_env_keys` gives.
    """
    if not ctx.ok(CONFIG):
        return _skipped(CAP_ADD, NO_ADAPTER)
    return _committed_only(ctx, CAP_ADD, "container_cap_add")


def _check_volumes(ctx: Context) -> CheckResult:
    """`container_volumes` is set in the committed layer or nowhere (F3 decision 5.3).

    Both channels, and the guard written out, for the reasons `_check_env_keys` gives.
    """
    if not ctx.ok(CONFIG):
        return _skipped(VOLUMES, NO_ADAPTER)
    return _committed_only(ctx, VOLUMES, "container_volumes")


def _check_prompts(ctx: Context) -> CheckResult:
    """How many of the packaged prompt templates this adapter overrides.

    **Informational, and `ok` whatever the count is.** An override is the feature ADR 0001
    designed — a user edits a prompt without forking bessemer — so a WARN here would train the
    reader to ignore a line that is reporting a working configuration. What the ADR asks for is
    that the drift stays *visible*, which is a line in the report rather than a status column.

    The names come from `prompts.overridden`, which exists so doctor never restates
    `<adapter>/prompts/<name>`: a glob would count a `README.md` left beside an override, and
    report drift that `prompts.load` would never read.
    """
    if not ctx.ok(CONFIG):
        return _skipped(PROMPTS, NO_ADAPTER)
    overridden = prompts.overridden(ctx.require_config().adapter_dir)
    total = len(prompts.TEMPLATE_NAMES)
    if not overridden:
        return _ok(PROMPTS, f"no override; all {total} templates come from the pin")
    return _ok(
        PROMPTS,
        f"{len(overridden)} of {total} templates overridden: {', '.join(overridden)}",
    )


def _check_gh(ctx: Context) -> CheckResult:
    """The GitHub CLI is present and authenticated.

    Bessemer opens the pull request that ends a run with `gh` (`bessemer.landing`), and it does
    so *last* — after the container, the passes and the push. An unauthenticated CLI therefore
    fails at the one point where a run has already spent everything it was going to spend,
    which is why this is a FAIL here rather than something the landing step discovers.

    Independent of config and of docker: `gh` is a fact about the machine.
    """
    probe = _probe(
        ctx,
        GH,
        GH_ARGV,
        missing_hint=(
            "install the GitHub CLI (https://cli.github.com) — bessemer opens the run's pull "
            "request with it"
        ),
    )
    if isinstance(probe, CheckResult):
        return probe
    if not probe.ok:
        return _fail(
            GH,
            f"`{' '.join(GH_ARGV)}` exited {probe.returncode}"
            f"{_saying(redact.detail(probe.stderr))}",
            hint="run `gh auth login`",
        )
    return _ok(GH, "CLI present, authenticated")


def _check_docker(ctx: Context) -> CheckResult:
    """The docker CLI is present and its daemon is responding.

    Late, because it is the one check whose failure says nothing about the repository: a
    stopped daemon is a two-second fix and everything above it stays true meanwhile. The
    port source ordered it first for the opposite reason — its later checks were all about
    images — and the image check below is the first one bessemer has that depends on it, which
    is why docker is no longer last.
    """
    probe = _probe(
        ctx,
        DOCKER,
        DOCKER_ARGV,
        missing_hint="install Docker Desktop or docker-ce, and check that `docker` is on PATH",
    )
    if isinstance(probe, CheckResult):
        return probe
    if not probe.ok:
        return _fail(
            DOCKER,
            f"the docker daemon is not responding{_saying(redact.detail(probe.stderr))}",
            hint="start Docker Desktop (or `dockerd`), then run bessemer doctor again",
        )
    return _ok(DOCKER, "CLI present, daemon responding")


def _check_image(ctx: Context) -> CheckResult:
    """The configured adapter image exists on this machine.

    Two failures, one line each, and they are different failures: no `image` key at all is an
    adapter that has not said which image to run, and a configured image docker cannot find is
    one nobody has built here yet. `bessemer.config` deliberately gives `image` no default, so
    the first case is a named refusal rather than `docker run` failing later on a tag the
    adopter never chose.

    **No staleness line.** The pin's `image_staleness` compares the image's creation timestamp
    against dependency inputs it knows by name (`api/requirements*.txt`, `client/yarn.lock`) —
    adopter facts a stack-agnostic core cannot have. It is F5's, and a stale image still runs.

    Depends on docker as well as on config, and skips with the port source's own line when the
    daemon is down: `docker image inspect` against a stopped daemon reports the image missing,
    which would be a FAIL saying "build it" to someone whose image is already built.
    """
    if not ctx.ok(CONFIG):
        return _skipped(IMAGE, NO_ADAPTER)
    if not ctx.ok(DOCKER):
        return _skipped(IMAGE, f"docker unavailable, fix the {DOCKER} check above first")
    cfg = ctx.require_config()
    image = cfg.get("image")
    if image is None:
        return _fail(
            IMAGE,
            "no image configured, so a run has nothing to start a container from",
            hint=(
                f'set image = "<tag>" in {config.COMMITTED_FILE} and build that tag from '
                f"{config.ADAPTER_DIR}/Dockerfile"
            ),
        )
    if not isinstance(image, str) or not image:
        # A TOML number or list reaching the argv would crash `_probe` while building its
        # message, which would report bessemer's bug for the adopter's typo.
        return _fail(
            IMAGE,
            f"image is not an image name: {image!r} (from the {cfg.layer_of('image')} layer)",
            hint='set image to a tag, such as image = "bessemer-adapter"',
        )
    probe = _probe(
        ctx,
        IMAGE,
        (*IMAGE_INSPECT_ARGV, image),
        # Unreachable while the docker check above passes, and written anyway: this check does
        # not get to assume the machine held still between two spawns.
        missing_hint="install Docker Desktop or docker-ce, and check that `docker` is on PATH",
    )
    if isinstance(probe, CheckResult):
        return probe
    if not probe.ok:
        return _fail(
            IMAGE,
            f"image {image} is not present on this machine"
            f"{_saying(redact.detail(probe.stderr))}",
            hint=f"build it from {config.ADAPTER_DIR}/Dockerfile and tag it {image}",
        )
    return _ok(IMAGE, f"{image} present (from the {cfg.layer_of('image')} layer)")


@dataclass(frozen=True)
class Check:
    """A check and the name it reports under.

    The name is declared beside the function rather than read out of its result, because the
    crash path has no result to read it from: a check that raises before returning still has
    to produce a named FAIL line. `tests/test_doctor.py` asserts every check's own result
    agrees with the name declared here, so the two cannot drift.
    """

    name: str
    probe: Callable[[Context], CheckResult]


CHECKS: Final = (
    Check(UV, _check_uv),
    Check(CONFIG, _check_config),
    Check(GIT_ENV, _check_git_env),
    Check(ROOT, _check_root),
    Check(BASE, _check_base),
    Check(CREDENTIAL, _check_credential),
    Check(ENV_KEYS, _check_env_keys),
    Check(CAP_ADD, _check_cap_add),
    Check(VOLUMES, _check_volumes),
    Check(PROMPTS, _check_prompts),
    Check(GH, _check_gh),
    Check(DOCKER, _check_docker),
    Check(IMAGE, _check_image),
)
"""Every check, in dependency order. `tests/test_doctor.py` restates the names by hand.

Covering only what has been built, which is what grew this list at F3: the credential, the
three committed-only keys, the prompt overrides, `gh` and the image all became checkable
because the subsystems they are about now exist. No count is stated here, in the issue, or in
the README — a numeral restating a list is a second value that can disagree with it.

The order is what the reader is asked to fix things in, and the dependencies fall out of it:
everything the adapter's own files answer runs before the three checks that ask another
program. `docker` sits second-to-last rather than last for the first time, because `image` is
the first check bessemer has that cannot be answered without it.

The hand-written literal in the test is what notices a check disappearing: an assertion that
iterates this tuple cannot, and a doctor that prints one line fewer and exits 0 with the whole
suite green is the one failure a tool whose job is reporting must not have.
"""


def run_checks(ctx: Context, checks: Sequence[Check] = CHECKS) -> list[CheckResult]:
    """Run every check in order and return one result each. Always returns a full report.

    **`Exception`, not `BaseException`.** A check that crashes is rendered and the report
    continues; `KeyboardInterrupt` and `SystemExit` are not a crashing check and must still
    end the process, and `tests.guard.GuardViolation` is a `BaseException` precisely so that
    a check reaching for `docker` in the suite cannot be absorbed into a green FAIL line.

    `checks` is a parameter so a test can run a deliberately-crashing check through this
    exact function rather than through a copy of it — the contract above is only worth as
    much as the thing that proves it.
    """
    report: list[CheckResult] = []
    for check in checks:
        try:
            result = check.probe(ctx)
        except Exception as error:
            result = _crashed(check.name, error)
        ctx.record(result)
        report.append(result)
    return report


def exit_code(report: Sequence[CheckResult]) -> int:
    """0 when every check is `ok` or `WARN`, 1 otherwise — a skip being a FAIL.

    The port source's scriptable-gate semantics, preserved exactly: `bessemer doctor` in a
    shell script means "is this machine able to dispatch".
    """
    return 0 if all(result.passed for result in report) else 1
