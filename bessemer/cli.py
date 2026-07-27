"""Command-line entry point.

The CLI is a renderer over the operations library, never the place logic lives
(ADR 0001, "operations as library, frontends thin"). A subcommand handler's job is to
call an operation and turn the data it returns into terminal output.

Only what exists is exposed. An empty subcommand would be a claim that bessemer can do
something it cannot, and this tool's premise is that it reports only what it can vouch for.
"""

import argparse
from collections.abc import Callable, Sequence

from bessemer import __version__

Handler = Callable[[argparse.Namespace], int]


def doctor(args: argparse.Namespace) -> int:
    """Report whether this machine can dispatch a run.

    Stub at F1: the check list lands with the subsystems it checks, so that no check
    can report on something unbuilt.
    """
    return 0


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
