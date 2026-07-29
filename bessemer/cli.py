"""Command-line entry point.

The CLI is a renderer over the operations library, never the place logic lives
(ADR 0001, "operations as library, frontends thin"). A subcommand handler's job is to
call an operation and turn the data it returns into terminal output.

Only what exists is exposed. An empty subcommand would be a claim that bessemer can do
something it cannot, and this tool's premise is that it reports only what it can vouch for.
"""

import argparse
import os
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Final

from bessemer import __version__
from bessemer import doctor as doctor_ops
from bessemer.doctor import CheckResult

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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point. Returns the process exit code.

    `argv` defaults to `sys.argv[1:]`; tests pass it explicitly.
    """
    args = build_parser().parse_args(argv)
    handler: Handler = args.handler
    return handler(args)
