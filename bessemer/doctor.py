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
checks 1 and 6 testable without widening that allowlist, so the argv they would spawn is
pinned by a test that reads it off a stand-in runner rather than by one that spawns it.
"""

import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import takewhile
from pathlib import Path
from typing import Final, Protocol

from bessemer import config, proc, redact, resolve
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
DOCKER: Final = "docker"

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


class Runner(Protocol):
    """The one spawn seam. `bessemer.proc.run` satisfies it; tests hand in a stand-in.

    A protocol rather than `Callable[..., proc.Result]`, so a stand-in that drops `timeout`
    is a type error here rather than a check that spawns without one.
    """

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> proc.Result: ...


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

    **Not the only site, and no longer the only pinned one.** Three other messages carry
    another program's text — a nonzero `docker info`, a nonzero `uv --version`, and the
    `OSError` in `_probe` — and all four redact.
    `tests/test_doctor.py::RedactionTest` asserts it site by site, from a hand-written list
    of the four: the first review of this issue found the other three correct but unpinned,
    which is a green suite standing behind three redactions nothing would have noticed
    losing.
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
        return _skipped(ROOT, f"no adapter loaded, fix the {CONFIG} check above first")
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
        return _skipped(BASE, f"no adapter loaded, fix the {CONFIG} check above first")
    cfg = ctx.require_config()
    match resolve.resolve_base(cfg, start=ctx.start):
        case Resolved(value=branch):
            source = cfg.layer_of("base") or f"{resolve.ORIGIN}/HEAD"
            return _ok(BASE, f"{branch} (from {source})")
        case Unresolved() as unresolved:
            return _fail(BASE, unresolved.reason, hint=unresolved.hint)


def _check_docker(ctx: Context) -> CheckResult:
    """The docker CLI is present and its daemon is responding.

    Last, because it is the one check whose failure says nothing about the repository: a
    stopped daemon is a two-second fix and everything above it stays true meanwhile. The
    port source ordered it first for the opposite reason — its later checks were all about
    images — and F1 has no image check yet to depend on it.
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
    Check(DOCKER, _check_docker),
)
"""Every check, in dependency order. `tests/test_doctor.py` restates the names by hand.

Six, and covering only what has been built. The hand-written literal in the test is what
notices a check disappearing: an assertion that iterates this tuple cannot, and a doctor that
prints five lines and exits 0 with the whole suite green is the one failure a tool whose job
is reporting must not have.
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
