"""The one module in the package permitted to start a child process.

Every child bessemer runs is described by an **argv list — never a command string, and
never through a shell**. That is the invariant this module exists to make structural:
the shell-interpolation and quoting-hazard class the port is being rewritten to escape
cannot occur if no shell is ever involved (ADR 0001, "one subprocess wrapper controls
every child's argv"; ADR 0002, "one subprocess wrapper owns argv").

`tests/test_argv_boundary.py` enforces that statically. No other module under `bessemer/`
may import `subprocess` or name any spawn entry point, and inside this module the allowlist
is drawn at **what can spawn** rather than at what is spelled `subprocess.` (ADR 0002):
`subprocess.run` and `subprocess.Popen` — the latter reserved for F3's live log streaming
and unused here — plus the inert names `PIPE`, `STDOUT`, `DEVNULL` and `TimeoutExpired`.
Those four are integers and an exception class; none can execute anything, and banning them
would forbid the `stdin=DEVNULL` below and the `stdout=PIPE` F3's streaming needs.

**No child ever inherits bessemer's stdin.** A child that can reach the terminal can block
on a credential prompt until the timeout kills it, turning an authentication failure into a
report of a hung process. See `_STDIN`.

**`stderr` is credential-bearing. Do not forward it anywhere an outsider can read.**
`git` and `gh` routinely echo remote URLs in their failure output, and a remote URL can
embed a token. `ProcessError`'s message carries stderr because a developer reading a
traceback needs it; that text must never reach a pull request body, a notification, or
the container log. Later features are the ones that will be tempted.

**`quote` is where that rule is decided, once.** Three F3 modules compose text from a
`Result` — landing's pull request body, dispatch's notification, passes' logging — and
without one owner the paragraph above becomes three local conventions, one of which is
wrong (ADR 0003). Every site composing text *from a `Result`* goes through `quote` and
names its destination; reading `stderr` or `argv` off a `Result` to build a string is the
defect.

Two kinds of site predate the policy and are not violations of it. `doctor.py` and
`resolve.py` build check lines and resolver reasons through `redact.detail` directly:
same redactor, same host-console row, written before there was a function to call — worth
migrating when one of them is next opened, not worth a rewrite here. And `ProcessError`'s
message is deliberately raw; see its docstring for why a traceback is not a destination.

**Nothing here carries the environment.** `Result` has no environment field and
`ProcessError` carries nothing but a `Result`, so there is no environment for a formatted
exception to leak. This matters because host-side children inherit the ambient
environment — the push path genuinely needs `SSH_AUTH_SOCK` and credential helpers — so
that environment holds real credentials. The boundary that does matter is the one
crossing into the container, and that one is docker's `-e` arguments, already governed by
the argv rule above.
"""

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final, Protocol, assert_never

from bessemer import redact

TimeoutExpired = subprocess.TimeoutExpired
"""`subprocess.TimeoutExpired`, re-exported so a caller can absorb it.

`run` lets this escape by contract — no process completed, so there is no returncode to
report — which makes absorbing it the caller's job. But `tests/test_argv_boundary.py`
forbids every other module in the package from importing `subprocess` at all, so without a
name here the two rules together say "handle this exception, but you may not name it". The
alias is what closes that: `except proc.TimeoutExpired` needs no import of the module this
one exists to contain.

Inert, and admitted by the wrapper's allowlist for that reason: it is an exception class and
cannot execute anything. It is not a second entry point.
"""

_STDIN = subprocess.DEVNULL
"""Children never inherit bessemer's stdin, and this is not configurable.

Left inherited, a child gets bessemer's own terminal and can block on a prompt — `git`
asking for an SSH key passphrase, `gh` asking to authenticate — for the entire timeout,
which then surfaces as `TimeoutExpired`. An authentication failure reported as a hung
process sends its reader to the wrong place entirely.

Measured, not assumed: with a pty on the parent's stdin, a child reading one line consumes
the whole timeout; with `DEVNULL` it reads EOF and returns in milliseconds.
`tests/test_proc.py::StdinTest` builds that pty, because an ordinary test runner's stdin is
already not a terminal and the test would otherwise pass without the wrapper doing
anything. It matters most on the F3 push path — the place a prompt is most plausible and
nobody is watching.
"""


@dataclass(frozen=True)
class Result:
    """What a child process did. Always exists — a failed process is data, not an error.

    Deliberately distinct from the value-or-reason type that lands with the resolvers:
    "a process ran and failed" and "a value could not be determined" are different
    things, and one type for both turns error handling to mush.

    **No `__bool__`, and none may be added.** `if result:` reads as "did I get a result"
    to every reader and would mean the opposite of `.ok`. `__len__` is excluded for the
    same reason — it is the other way a truth value gets invented for an object that
    should not have one.
    """

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """Whether the child exited 0."""
        return self.returncode == 0


_NO_STDERR: Final = "<no stderr>"
"""What a silent failure reads as. `git rev-parse` failing without a word is ordinary, and
a message ending in a bare colon is worse than one that stops. Shared by `ProcessError` and
`quote`'s host-log arm, which differ in what they quote but not in how they say "nothing"."""


class ProcessError(Exception):
    """A checked call exited nonzero.

    Carries the `Result`, so a handler gets argv, returncode and stderr without parsing
    the message — and carries nothing else, which is what keeps the environment out of
    it. Read the module docstring before putting `stderr` anywhere.

    **This message is not a destination, and deliberately not built by `quote`.** It is a
    traceback a developer is reading on their own machine at the moment of failure, so it
    carries argv and stderr raw and whole — redacted-and-first-line-only is right for a log
    file that outlives the console and wrong for the thing you are debugging with. It is
    also what makes forwarding `str(exc)` a leak: quote the `Result`, never the exception.
    """

    def __init__(self, result: Result) -> None:
        self.result = result
        super().__init__(
            f"{list(result.argv)} exited {result.returncode}: "
            f"{result.stderr.strip() or _NO_STDERR}"
        )


class Destination(Enum):
    """Who will read quoted text, which is the only thing that decides what it may say.

    Two members, because there are two readers with different rights — not because two is a
    tidy number. A third would be a decision about who may read another program's stderr,
    and `tests/test_proc.py::DestinationTest` restates this list by hand so that decision
    cannot be made by adding a member.
    """

    HOST_LOG = "host log"
    """The operator's console and the host-side run log — the developer's own machine.

    The one reader who needs the failure text, and gets it: argv, returncode, and stderr
    after `redact.redacted` and `redact.DETAIL_LIMIT`. Redacted rather than raw because a
    log file outlives the console that printed it.
    """

    AGENT_VISIBLE = "agent-visible"
    """A pull request body, a notification, an assembled prompt.

    One member rather than three, because the reader is the same in all three: someone off
    this machine, the dispatched agent among them. Program name and returncode; never
    stderr, and never argv arguments — a remote URL rides in an argument as readily as in
    git's echo of it.
    """


_NO_PROGRAM: Final = "<no program>"
"""Stand-in for an argv with no usable program name. `run` cannot produce one — a `Result`
always records the argv it ran — but the tier-2 double builds `Result`s by hand, and a
composition site that raises while composing a failure notification loses the failure it
was reporting. It covers an empty argv and an argv whose first entry has no basename
(`""`, `"/"`); a line beginning " exited 1" is the same defect wearing a space."""


def quote(result: Result, *, destination: Destination) -> str:
    """What of `result` may be said to `destination`. The one answer; see `Destination`.

    `destination` is keyword-only and has no default, so the permissive row cannot be
    reached by omission and no call site can pick a reader by argument order. Every
    composition site names who it is composing for, which is what makes the choice
    reviewable in a diff.

    The `match` is exhaustive by type: mypy narrows `destination` to `Never` at the
    `assert_never`, so adding a member to `Destination` without an arm here fails the gate
    rather than falling through to whichever arm happens to be last.

    Redaction is `bessemer.redact`'s, never a second regex — that module's contract is to
    import nothing from the package, which rules it out as this function's home but not as
    its implementation (ADR 0003). Its `detail` is what the host-log arm calls: first
    non-empty line, redacted, capped. First line only is narrower than "stderr", and
    deliberately so — the later lines of a git or docker failure are advice aimed at an
    interactive user.

    Two further narrowings on the host-log row, stricter than the destination table and
    recorded on the issue: **argv is redacted too**, because a remote URL rides in an
    argument as readily as in git's echo of it and the log is a file; and `DETAIL_LIMIT`
    caps **only the stderr**, because the cap is redact's rule about another program's
    output, while argv is bessemer's own and a truncated one hides the flag that mattered —
    a docker argv is legitimately long.
    """
    match destination:
        case Destination.HOST_LOG:
            argv = redact.redacted(str(list(result.argv)))
            detail = redact.detail(result.stderr) or _NO_STDERR
            return f"{argv} exited {result.returncode}: {detail}"
        case Destination.AGENT_VISIBLE:
            # Basename first, fall back second: `argv[0]` can be present and still yield no
            # name — `""` and `"/"` both do — and a truthiness check on the tuple misses it.
            program = Path(result.argv[0]).name if result.argv else ""
            return f"{program or _NO_PROGRAM} exited {result.returncode}"
    assert_never(destination)


class Runner(Protocol):
    """The shape of `run` — what an effectful module accepts instead of spawning for itself.

    ADR 0003: every effectful module takes its proc callable and none creates it, so
    `dispatch.py` composes the real one and tier-2 tests compose the recording double at the
    same seam. That is one seam with two adapters rather than a mock; the double is a thin
    table over the real `Result` type.

    A protocol rather than `Callable[..., Result]`, so a stand-in that quietly drops
    `timeout` is a type error at the seam rather than a call that spawns without one.

    It lives here because it is the shape of this module's own function. It was declared in
    `doctor.py` first, at F1; F3 issue 04 needed the same protocol for `bessemer.checkout`
    and moved the declaration here rather than writing a second one. `doctor.Runner` is now
    an alias of this name, so the checks and their tests keep the spelling they had and there
    is still exactly one declaration.
    """

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> Result: ...


def run(
    argv: Sequence[str],
    *,
    timeout: float,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Result:
    """Run `argv` to completion and report what happened. A nonzero exit is not an error.

    Non-raising is the default because doctor's probes are all "did this fail, and how";
    an exception per probe turns a check list into control flow (ADR 0002).

    `timeout` is a required keyword with no default. A wedged Docker daemon hanging
    doctor forever is worse than doctor failing.

    Two cases are **not** converted to a `Result`, because in neither did a process run
    to completion and there is no returncode to report: a program that could not be
    executed at all raises `OSError`, and one killed for exceeding `timeout` raises
    `subprocess.TimeoutExpired`. Inventing a returncode for them would make "docker is
    not installed" indistinguishable from "docker exited 127". Doctor tolerates both by
    contract — a crashing check renders as FAIL and the report still completes (ADR
    0002) — which is why this stays a distinction rather than becoming a fabrication.

    `env=None` means the child inherits this process's environment; see the module
    docstring for why that is the right default host-side. `stdin` is never inherited —
    see `_STDIN`.
    """
    if isinstance(argv, (str, bytes)):
        # `str` is a `Sequence[str]`, so a type checker waves this through and the child
        # would be handed a single argument spelled like a command line. The one
        # invariant this module has cannot be left to a check that does not fire.
        raise TypeError(f"argv must be a list of arguments, not a string: {argv!r}")

    command = list(argv)
    completed = subprocess.run(
        command,
        timeout=timeout,
        cwd=cwd,
        env=env,
        stdin=_STDIN,
        capture_output=True,
        text=True,
        # git output is not guaranteed to be UTF-8 — a branch name or an author line can
        # carry anything. A `UnicodeDecodeError` out of a function documented as
        # non-raising would be exactly the surprise this module exists to remove.
        errors="replace",
        check=False,
    )
    return Result(
        argv=tuple(command),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def run_checked(
    argv: Sequence[str],
    *,
    timeout: float,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Result:
    """Run `argv`; raise `ProcessError` unless it exits 0. For call sites that must abort.

    The signature is spelled out rather than forwarded through `**kwargs`, so `timeout`
    is a required keyword here too and not merely at the call `run` eventually makes.
    """
    result = run(argv, timeout=timeout, cwd=cwd, env=env)
    if not result.ok:
        raise ProcessError(result)
    return result
