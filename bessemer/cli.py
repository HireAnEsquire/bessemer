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
import time
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import Final

from bessemer import __version__, config, proc, reclaim, resolve
from bessemer import dispatch as dispatch_ops
from bessemer import doctor as doctor_ops
from bessemer import gc as gc_ops
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


def _refuse(command: str, reason: str, hint: str) -> int:
    """A caller-error refusal: both halves on stderr, exit 2 — the usage-error exit argparse
    already gives this CLI, and the code upstream's subcommands used for the same class.
    Prefixed with the subcommand refusing, since two of them now share this path."""
    print(f"{command}: {reason}", file=sys.stderr)
    print(f"{HINT_PREFIX}{hint}", file=sys.stderr)
    return 2


def _status_report(cfg: Config, limit: int) -> int:
    """Gather the docker-side facts, render the view, print it."""
    specs_setting = cfg.get("specs_dir")
    if not isinstance(specs_setting, str):
        return _refuse(
            "status",
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
            return _refuse("status", reason, hint)


def _spaced() -> Callable[[str], None]:
    """A printer that puts one blank line above the first line it is given, and none above the
    rest. Separates the reclaim's output from the listing above it.

    A closure rather than a `print()` before the call, because a `--force` that refuses emits
    nothing at all and a blank line above nothing is a blank line the operator has to wonder
    about. It stays a rendering decision here rather than a second reading of `docker_down`,
    which is `bessemer.reclaim`'s to make.
    """
    first = True

    def spaced(line: str) -> None:
        nonlocal first
        if first:
            print()
            first = False
        print(line)

    return spaced


def _gc_reclaim(cfg: Config, items: list[gc_ops.GcItem], docker_down: bool) -> int:
    """Execute the plan the scan just printed, and exit on what became of it.

    The listing comes first and unconditionally (the pin's order, run.sh:447–457): `--force`
    adds a deletion to the report a plain `gc` prints, it does not replace it, so the operator
    reads what is about to go before it goes.

    The refusal is `bessemer.reclaim`'s decision and this is only where it is rendered —
    stderr, because it is the reason a command did nothing, and exit 1 rather than the usage
    exit 2 `_refuse` gives: the invocation was correct and the machine was not.
    """
    report = reclaim.execute_gc_plan(
        gc_ops.render_gc_plan(items),
        checkouts_dir=cfg.adapter_dir / gc_ops.CHECKOUTS_DIR,
        locks_dir=cfg.adapter_dir / status_ops.LOCKS_DIR,
        repo_root=cfg.root,
        docker_down=docker_down,
        emit=_spaced(),
        run=proc.run,
    )
    if report.refused:
        print(reclaim.REFUSED_DOCKER_DOWN, file=sys.stderr)
        return 1
    return 1 if report.failed else 0


def _gc_report(cfg: Config, plan: bool, force: bool = False) -> int:
    """Gather the docker-side facts, scan, and print the report — or the TSV plan.

    The gather is `_docker_rows`, the same one `status` uses, rather than a second of its
    own: upstream's `cmd_gc` read stdin because `run.sh` piped `docker ps` in from bash, and
    that boundary is the one the rewrite deletes. Reusing the gather means its three failure
    arms (nonzero exit, `OSError`, a hang) are already proven to collapse to docker-down by
    `DockerGatherTest`, and gc renders that as `collect_gc_items` says it must: no container
    class, nothing deletable.
    """
    docker_rows, docker_down = _docker_rows()
    items = gc_ops.collect_gc_items(
        checkouts_dir=cfg.adapter_dir / gc_ops.CHECKOUTS_DIR,
        locks_dir=cfg.adapter_dir / status_ops.LOCKS_DIR,
        docker_rows=docker_rows,
        docker_down=docker_down,
    )
    if plan:
        rendered = gc_ops.render_gc_plan(items)
        if rendered:
            print(rendered)
        return 0

    log_summary = gc_ops.summarize_logs(cfg.adapter_dir / status_ops.LOGS_DIR)
    print(gc_ops.render_gc(items, docker_down, log_summary))
    if force:
        return _gc_reclaim(cfg, items, docker_down)
    return 0


def gc(args: argparse.Namespace) -> int:
    """Report what old run state could be reclaimed, and with `--force` reclaim it.

    Scanning is `bessemer/gc.py`, which deletes nothing; acting is `bessemer/reclaim.py`, and
    the seam between them is ADR 0003's. Without `--force` this command still touches nothing.

    Same shape as `status`: the adapter is resolved first, and a failure refuses before the
    daemon is ever asked — the checkouts, locks and logs gc scans all live under it.
    """
    match config.load(start=_start()):
        case Resolved(value=cfg):
            return _gc_report(cfg, plan=args.plan, force=args.force)
        case Unresolved(reason=reason, hint=hint):
            return _refuse("gc", reason, hint)


def _dispatch(cfg: Config, start: Path | None, args: argparse.Namespace) -> int:
    """Compose the real seams and run one dispatch. The whole of the CLI's half of a run.

    Root agreement is resolved here rather than inside `bessemer.dispatch`, and it is the one
    guard that lives on this side: `resolve.resolve_root_agreement` spawns git through
    `proc.run` directly rather than through the seam a dispatch is handed, so asking it there
    would put a real git repository behind every tier-2 orchestration test. It refuses exactly
    as `config.load` does, two lines above, and the agreed root travels on as a fact.

    Everything after it is the composition ADR 0003 names: the real `proc.run` and
    `proc.streamed`, the real clocks, this process's pid and environment. A test composes the
    recording double at the same seam.
    """
    match resolve.resolve_root_agreement(cfg, start=start):
        case Unresolved(reason=reason, hint=hint):
            return _refuse("run", reason, hint)
        case Resolved(value=repo_root):
            pass

    try:
        dispatch_ops.dispatch(
            cfg=cfg,
            repo_root=repo_root,
            start=start,
            spec=args.spec,
            branch=args.branch,
            base=args.base,
            env=os.environ,
            pid=os.getpid(),
            console=print,
            run=proc.run,
            streamer=proc.streamed,
            sleep=time.sleep,
            clock=time.monotonic,
            now=datetime.now,
        )
    except (dispatch_ops.Refusal, dispatch_ops.RunFailed) as stopped:
        # Both halves print the same way and exit the same way, because the difference between
        # them is what the machine looks like afterwards rather than what the operator reads:
        # a refusal touched nothing, a failure has a log with the reason in it — which the
        # message names either way.
        print(f"{dispatch_ops.REFUSAL_PREFIX}{stopped}", file=sys.stderr)
        return 1
    print(dispatch_ops.FINISHED)
    return 0


def run(args: argparse.Namespace) -> int:
    """Dispatch one spec on one branch.

    Same shape as `status` and `gc`: the adapter is resolved first, and a failure refuses
    before anything else is asked — with no `.bessemer/` there is no image, no prompt override
    and nowhere to write a log.
    """
    start = _start()
    match config.load(start=start):
        case Resolved(value=cfg):
            return _dispatch(cfg, start, args)
        case Unresolved(reason=reason, hint=hint):
            return _refuse("run", reason, hint)


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

    gc_parser = subcommands.add_parser(
        "gc",
        help="report what old run state could be reclaimed, and with --force reclaim it",
        description=(
            "Report what old run state could be reclaimed. Deletes nothing without --force."
        ),
    )
    # Mutually exclusive because they are two different jobs asked of one command: `--plan` is
    # a machine-readable listing for something else to consume, and `--force` deletes. Their
    # combination has no meaning that is not one of the two, and argparse refusing it is
    # cheaper than a precedence rule a reader has to learn.
    gc_mode = gc_parser.add_mutually_exclusive_group()
    gc_mode.add_argument(
        "--plan",
        action="store_true",
        help="print the reclaimable items as machine-readable TSV instead of a table",
    )
    gc_mode.add_argument(
        "--force",
        action="store_true",
        help=(
            "reclaim the listed items after re-checking each is not live; refuses entirely"
            " if docker is unavailable"
        ),
    )
    gc_parser.set_defaults(handler=gc)

    run_parser = subcommands.add_parser(
        "run",
        help="dispatch a run: one spec, on one branch, landing as a draft pull request",
        description="Dispatch a run: one spec, on one branch, landing as a draft PR.",
    )
    run_parser.add_argument("spec", help="the spec to run, relative to the specs directory")
    run_parser.add_argument(
        "--branch",
        required=True,
        help="the working branch to fork from, commit to and push back to (required)",
    )
    run_parser.add_argument(
        "--base",
        help=(
            "the ref the pull request targets and diffs against; omitted, the branch's last "
            "recorded base wins, then config, then origin/HEAD"
        ),
    )
    run_parser.set_defaults(handler=run)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point. Returns the process exit code.

    `argv` defaults to `sys.argv[1:]`; tests pass it explicitly.
    """
    args = build_parser().parse_args(argv)
    handler: Handler = args.handler
    return handler(args)
