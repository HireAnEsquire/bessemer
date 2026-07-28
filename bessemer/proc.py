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
from pathlib import Path

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


class ProcessError(Exception):
    """A checked call exited nonzero.

    Carries the `Result`, so a handler gets argv, returncode and stderr without parsing
    the message — and carries nothing else, which is what keeps the environment out of
    it. Read the module docstring before putting `stderr` anywhere.
    """

    def __init__(self, result: Result) -> None:
        self.result = result
        super().__init__(
            f"{list(result.argv)} exited {result.returncode}: "
            f"{result.stderr.strip() or '<no stderr>'}"
        )


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
