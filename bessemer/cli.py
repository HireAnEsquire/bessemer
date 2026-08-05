"""Command-line entry point.

The CLI is a renderer over the operations library, never the place logic lives
(ADR 0001, "operations as library, frontends thin"). A subcommand handler's job is to
call an operation and turn the data it returns into terminal output.

Only what exists is exposed. An empty subcommand would be a claim that bessemer can do
something it cannot, and this tool's premise is that it reports only what it can vouch for.
"""

import argparse
import os
import sys
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Final

from bessemer import __version__, config, proc
from bessemer import doctor as doctor_ops
from bessemer import status as status_ops
from bessemer.config import Config
from bessemer.doctor import CheckResult
from bessemer.outcome import Resolved, Unresolved

Handler = Callable[[argparse.Namespace], int]

STATUS_WIDTH: Final = 4
NAME_WIDTH: Final = 10
"""The port source's `printf '%-4s  %-10s %s\\n'`, ported column for column.

Four is `WARN` and `FAIL` exactly; ten fits doctor's names with room for the ones later
features add. Both are minimum widths — a longer name pushes its message right on that line
alone rather than being cut, because a truncated check name is a check nobody can grep for.
"""

HINT_PREFIX: Final = "hint: "
HINT_INDENT: Final = " " * (STATUS_WIDTH + 2 + NAME_WIDTH + 1)
"""A hint goes on its own line, indented under the message it belongs to.

The port source appends the fix to the message and keeps one line per check. Bessemer's
reasons and hints arrive as separate strings from the resolvers, and both are sentences —
joined onto one line they wrap in every terminal, which loses the status column that makes
the report skimmable. Two lines, and only on a check that has something to fix.
"""


def render(result: CheckResult) -> Iterator[str]:
    """One check as the lines it prints: its own, plus a hint line when it has one."""
    yield f"{result.status:<{STATUS_WIDTH}}  {result.name:<{NAME_WIDTH}} {result.message}"
    if result.hint:
        yield f"{HINT_INDENT}{HINT_PREFIX}{result.hint}"


def _start() -> Path | None:
    """The one directory the whole report is about: this process's, read exactly once.

    Resolved here rather than left as `None`. `None` also produces one report about one
    directory — `config.load`, `resolve_base` and `resolve_root_agreement` would each call
    `Path.cwd()` and each get the same answer — but only *because* they agree, and root
    agreement is the check whose whole job is not to rest on two things agreeing by
    coincidence. Issue 05 spent that argument getting `_relate` off spelling and onto
    identity; three independent reads of the working directory would put a coincidence back
    one level up, where nothing compares them.

    `None` on failure, and this is the one case that keeps it. `Path.cwd()` raises `OSError`
    when the working directory has been deleted under a live shell, and `bessemer.resolve`
    already has a reason and a hint written for exactly that (`_cwd_unavailable`). Handing
    the operations `None` gives the reader that reason; raising here would give them a
    traceback out of the one command that exists to be run when things are broken.
    """
    try:
        return Path.cwd()
    except OSError:
        return None


def doctor(args: argparse.Namespace) -> int:
    """Report whether this machine can dispatch a run.

    The context is built here and nowhere else: `os.environ` and the working directory are
    each read at this one site, and both travel to every check as fields (see
    `bessemer.doctor.Context`).
    """
    context = doctor_ops.Context(start=_start(), env=os.environ)
    report = doctor_ops.run_checks(context)
    for result in report:
        for line in render(result):
            print(line)
    return doctor_ops.exit_code(report)


DOCKER_PS_ARGV: Final = ("docker", "ps", "--format", "{{.Names}}\t{{.Status}}")
"""The docker-side gather, ported from the port source's `run.sh` status interception:
`NAME<TAB>STATUS` per running container. Upstream's bash gathered this and piped it to
python over stdin; bessemer's dispatch is python, so the gather moves here — the CLI, the
one caller allowed to ask the daemon — and `bessemer.status` is only ever handed the rows.
"""

DOCKER_PS_TIMEOUT_SECONDS: Final = 15.0
"""A backstop, for `bessemer.doctor.TIMEOUT_SECONDS`'s reason: a wedged daemon that hangs
`docker ps` forever must degrade status to its docker-down rendering, not hang it."""

DEFAULT_RECENT_LIMIT: Final = 10
"""How many recent runs `status` shows without `-n`. Upstream's default, kept."""


def _docker_rows() -> tuple[list[str], bool]:
    """What the daemon said, as `(rows, docker_down)`.

    Every way of getting no answer — docker not installed (`OSError`), the daemon down or
    unreachable (nonzero exit), a hang (`TimeoutExpired`) — collapses to `([], True)`, the
    same collapse upstream's `run.sh` made with `|| DOCKER_DOWN=1`: `render_running` has one
    honest line for all of them, and the ledger half of the report must render regardless.
    """
    try:
        result = proc.run(DOCKER_PS_ARGV, timeout=DOCKER_PS_TIMEOUT_SECONDS)
    except OSError, proc.TimeoutExpired:
        return [], True
    if not result.ok:
        return [], True
    return result.stdout.splitlines(), False


def _refuse(reason: str, hint: str) -> int:
    """A caller-error refusal: both halves on stderr, exit 2 — the usage-error exit argparse
    already gives this CLI, and the code upstream's subcommands used for the same class."""
    print(f"status: {reason}", file=sys.stderr)
    print(f"{HINT_PREFIX}{hint}", file=sys.stderr)
    return 2


def _status_report(cfg: Config, limit: int) -> int:
    """Gather the docker-side facts, render the view, print it."""
    specs_setting = cfg.get("specs_dir")
    if not isinstance(specs_setting, str):
        return _refuse(
            f"specs_dir is configured to {specs_setting!r}, which is not a string",
            f"fix specs_dir in {cfg.adapter_dir / config.COMMITTED_FILE}",
        )
    docker_rows, docker_down = _docker_rows()
    print(
        status_ops.render_status(
            specs_dir=cfg.root / specs_setting,
            logs_dir=cfg.adapter_dir / status_ops.LOGS_DIR,
            locks_dir=cfg.adapter_dir / status_ops.LOCKS_DIR,
            docker_rows=docker_rows,
            docker_down=docker_down,
            limit=limit,
        )
    )
    return 0


def status(args: argparse.Namespace) -> int:
    """Report what is running and what has run.

    The adapter is resolved first and a failure refuses before the daemon is ever asked —
    with no `.bessemer/` there is no ledger to report on, so there is nothing a docker
    answer could add to the refusal.
    """
    match config.load(start=_start()):
        case Resolved(value=cfg):
            return _status_report(cfg, limit=args.limit)
        case Unresolved(reason=reason, hint=hint):
            return _refuse(reason, hint)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Split out from `main` so tests can inspect the parser without running a command.
    """
    parser = argparse.ArgumentParser(
        prog="bessemer",
        description="Dispatcher for AFK coding agents.",
    )
    parser.add_argument("--version", action="version", version=__version__)

    subcommands = parser.add_subparsers(
        dest="command",
        metavar="<command>",
        required=True,
    )

    doctor_parser = subcommands.add_parser(
        "doctor",
        help="report whether this machine can dispatch a run",
        description="Report whether this machine can dispatch a run.",
    )
    doctor_parser.set_defaults(handler=doctor)

    status_parser = subcommands.add_parser(
        "status",
        help="report what is running and what has run",
        description="Report what is running and what has run.",
    )
    status_parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=DEFAULT_RECENT_LIMIT,
        help="how many recent runs to show (default: %(default)s)",
    )
    status_parser.set_defaults(handler=status)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point. Returns the process exit code.

    `argv` defaults to `sys.argv[1:]`; tests pass it explicitly.
    """
    args = build_parser().parse_args(argv)
    handler: Handler = args.handler
    return handler(args)
