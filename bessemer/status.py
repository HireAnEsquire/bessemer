"""The status view: scanning what is running, rendering what has run.

Ported from the port source's `.agentbox/tasklib.py` at commit `e194121f75f4`. **The
upstream tests are the specification** — where this module and a reading of what it "should"
do disagree, upstream wins, and the disagreement is a finding in the port report rather than
a silent improvement. `tests/port_manifest.py` is the census that makes that checkable.

**`render_*` in a library is not ADR 0002's boundary leaking.** That ADR puts rendering in
the CLI, and the line it draws is *who talks to the terminal*: the functions below return
`list[str]` (and `render_status` one joined `str`) and never print — `bessemer/cli.py` is
what writes them to a terminal, exactly the shape F1's doctor already has, where the core
returns `list[CheckResult]` and `cli.render` prints. What lives here is the *computation* of
the view — parsing, filtering, aligning, ageing — which is data work with a pure test for
every function. `tests/test_status.py` asserts the no-print contract rather than trusting
this paragraph.

**Two things this module refuses to know**, and both are the same seam:

- **Docker rows arrive as text.** `parse_docker_rows` takes `NAME<TAB>STATUS` lines someone
  else fetched; nothing here spawns `docker` — the CLI's gather does, through
  `bessemer.proc`. That keeps every test in `tests/test_status.py` pure and fast, and it is
  why `tests/guard.py` can deny `docker` to the whole suite.
- **`docker_down` is a parameter, not a discovery.** A status view that cannot reach the
  daemon still has to render, and it renders differently — the same "works when things are
  broken" principle as doctor's. Recent must render from the ledger regardless.

The one `os` use below is `os.kill(pid, 0)` in `pid_alive` — a signal-0 liveness probe,
which starts nothing. The purity tests allow exactly that and still refuse every spawn path.

**Vocabulary.** CONTEXT.md retires *task*, so upstream's `tasks_dir` parameter is
`specs_dir` here — but the rendered line `no running tasks` ships as upstream wrote it,
because it is asserted by name and changing an asserted string is changing what the code
does. One value is deliberately *not* ported verbatim: upstream filtered containers by the
prefix `agentbox-`, its own product name. Container names are runtime artifacts bessemer
itself will create — F3's dispatch names the container after the working branch — and
bessemer has zero installs, so no `agentbox-*` container it should recognise can exist.
`CONTAINER_PREFIX` is therefore `bessemer-`, the same adopter-facing product-name rename as
`tasks_dir` becoming `specs_dir`, made at port time for the same reason. The prefix appears
in upstream's tests only as fixture input, never inside an assertion.

**The legacy migration is not here** (decision 4 of the F2 README). Upstream's
`render_status` reached `_migrate_legacy_ledgers` through `collect_recent_ledger_records`;
bessemer's ledger dropped that call, so a specs directory holding only legacy per-directory
`runs.jsonl` files renders as "no runs recorded".

**What F3 owes this module.** `render_running` builds `tail -f <logs_dir>/<slug>.log` lines
and `stale_locks` reads `<locks_dir>/*.pid`; `LOGS_DIR` and `LOCKS_DIR` below say where the
CLI looks for both, under the adapter directory. Nothing writes either yet — F3's dispatch
must write its logs and locks exactly there, or status will render a healthy-looking blank
over live runs.
"""

import os
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from bessemer.issues import (
    AD_HOC_DIR,
    APPROVED,
    issue_summary,
    leading_number,
    load_issues,
    select_issues,
)
from bessemer.ledger import Record, collect_recent_ledger_records
from bessemer.resume import first_line

CONTAINER_PREFIX: Final = "bessemer-"
"""What a run's container is called: this prefix plus the working branch's slug.

Public because gc (issue 05 of F2) embeds it in the `docker rm` commands it proposes, and
because F3's dispatch is what must *name* containers this way for status to see them — see
the module docstring for why this is `bessemer-` where upstream wrote `agentbox-`.
"""

LOGS_DIR: Final = "logs"
"""Where run logs live, under the adapter directory: `.bessemer/logs/<slug>.log`.

The port source kept logs in `$BOX/logs` beside its tasks directory; the adapter directory
is that anchor here. F3's dispatch must write where this reads.
"""

LOCKS_DIR: Final = "locks"
"""Where per-branch lockfiles live, under the adapter directory: `.bessemer/locks/<slug>.pid`.

Same anchor and same F3 debt as `LOGS_DIR`.
"""

_STATUS_MAX_COL_WIDTH: Final = 32
"""Where `format_table`'s ellipsis treatment cuts a free-text column."""


@dataclass
class ContainerInfo:
    """One running container, as status cares about it: which run, and for how long."""

    slug: str
    uptime: str


def parse_docker_rows(rows: list[str]) -> list[ContainerInfo]:
    """`NAME<TAB>STATUS` lines from `docker ps` (the CLI's docker-side gather), filtered to
    `bessemer-*` containers and reduced to (slug, uptime). Branch is the slug itself (per
    the read-only contract: derived from the slug, never a git lookup) — lossy for a branch
    with a slash, but that's the deal."""
    containers = []
    for row in rows:
        if not row.strip():
            continue
        name, _, uptime = row.partition("\t")
        if not name.startswith(CONTAINER_PREFIX):
            continue
        containers.append(
            ContainerInfo(slug=name[len(CONTAINER_PREFIX) :], uptime=uptime.strip())
        )
    return containers


def stale_locks(locks_dir: Path, running_slugs: set[str]) -> list[str]:
    """Lock files with no matching live container, sorted for stable output. Read-only
    cross-check only — no cleanup (that's gc, issue 05 of F2)."""
    if not locks_dir.is_dir():
        return []
    return sorted(p.stem for p in locks_dir.glob("*.pid") if p.stem not in running_slugs)


def _overall_outcome(record: Record) -> str:
    """One ledger record's verdict as a cell: the recorded outcome if the run has one, else
    the per-issue tally, else `-`. The single rendering of "how did that run go" — both the
    recent-runs table and `_resume_run_label` come through here, by documented contract."""
    outcome = record.get("outcome")
    if outcome:
        return {"approved": "✅ approved", "needs-work": "⚠️ needs-work"}.get(
            outcome, str(outcome)
        )
    issues = record.get("issues") or {}
    if issues:
        done = sum(1 for v in issues.values() if v == APPROVED)
        return "✅ approved" if done == len(issues) else f"⚠️ {done}/{len(issues)} approved"
    return "-"


def _age_from_seconds(seconds: int) -> str:
    seconds = max(0, seconds)
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _age(timestamp: object) -> str:
    """A ledger timestamp as a relative age, or `?` for anything unreadable — a malformed
    record renders as unknown rather than taking the table down. A naive timestamp is taken
    as UTC, which is what `run_record` writes."""
    if not isinstance(timestamp, str) or not timestamp:
        return "?"
    try:
        ts = datetime.fromisoformat(timestamp)
    except ValueError:
        return "?"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return _age_from_seconds(int((datetime.now(UTC) - ts).total_seconds()))


def mtime_age(path: Path) -> str:
    """`path`'s last-modified time as a relative age, `?` if it cannot be statted. Public
    because gc (issue 05 of F2) ages checkouts and locks with it."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return "?"
    return _age_from_seconds(int(datetime.now().timestamp() - mtime))


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(width - 1, 1)] + "…"


def format_table(
    headers: list[str], rows: list[list[str]], truncate_cols: AbstractSet[int] = frozenset()
) -> list[str]:
    """Plain aligned-column text (stdlib string formatting only, no gum/fzf/curses) —
    status gets piped and run in narrow terminals. Only `truncate_cols` (free-text columns
    like BRANCH/SOURCE) get the ellipsis treatment; a ready-to-paste column (LOG's
    `tail -f ...`, a PR URL) is never truncated — a clipped command/URL is worse than a wide
    line. The last column is left unpadded so piped/redirected output carries no trailing
    whitespace. Public because gc (issue 05 of F2) renders its plan through it."""
    all_rows = [headers] + [
        [
            _truncate(cell, _STATUS_MAX_COL_WIDTH) if i in truncate_cols else cell
            for i, cell in enumerate(row)
        ]
        for row in rows
    ]
    widths = [max(len(r[i]) for r in all_rows) for i in range(len(headers))]
    lines = []
    for row in all_rows:
        cells = [row[i].ljust(widths[i]) for i in range(len(row) - 1)] + [row[-1]]
        lines.append("  ".join(cells))
    return lines


def render_running(
    docker_rows: list[str], docker_down: bool, logs_dir: Path, locks_dir: Path
) -> list[str]:
    """The Running section, as lines. With the daemon down it is one honest line and no
    lock check — a lock cannot be called stale when nothing can say what is running."""
    lines = ["Running"]
    if docker_down:
        lines.append("  docker unavailable (daemon down?) — Recent below is unaffected")
        return lines
    containers = parse_docker_rows(docker_rows)
    if containers:
        table = format_table(
            ["BRANCH", "UPTIME", "LOG"],
            [[c.slug, c.uptime, f"tail -f {logs_dir}/{c.slug}.log"] for c in containers],
            truncate_cols={0},
        )
        lines.extend(f"  {row}" for row in table)
    else:
        lines.append("  no running tasks")
    for slug in stale_locks(locks_dir, {c.slug for c in containers}):
        lines.append(f"  ⚠ stale lock: {slug} (no matching container)")
    return lines


def render_recent(records: list[Record], limit: int) -> list[str]:
    """The Recent section, as lines: the newest `limit` records as a table."""
    records = records[:limit]
    lines = [f"Recent (last {limit})"]
    if not records:
        lines.append("  no runs recorded")
        return lines
    table = format_table(
        ["BRANCH", "SOURCE", "ISSUES", "OUTCOME", "PR", "AGE"],
        [
            [
                r.get("branch") or "?",
                r.get("feature") or r.get("spec") or "?",
                issue_summary(r.get("issues") or {}),
                _overall_outcome(r),
                r.get("pr_url") or "-",
                _age(r.get("timestamp")),
            ]
            for r in records
        ],
        truncate_cols={0, 1},
    )
    lines.extend(f"  {row}" for row in table)
    return lines


def render_status(
    *,
    specs_dir: Path,
    logs_dir: Path,
    locks_dir: Path,
    docker_rows: list[str],
    docker_down: bool,
    limit: int,
) -> str:
    """The whole view as one string: Running, a blank line, Recent. The CLI prints it."""
    lines = render_running(docker_rows, docker_down, logs_dir, locks_dir)
    lines.append("")
    lines.extend(render_recent(collect_recent_ledger_records(specs_dir), limit))
    return "\n".join(lines)


def is_live_status(uptime: str) -> bool:
    """Docker always starts a running container's status with "Up" (e.g. "Up 5 minutes" vs.
    "Exited (0) 3 hours ago", "Created") — no second `docker ps` call needed to test
    liveness. Public because gc (issue 05 of F2) partitions containers with it."""
    return uptime.startswith("Up")


def pid_alive(pid_text: str) -> bool:
    """Whether `pid_text` names a process that exists, by signal 0 — the one thing in this
    module that asks the operating system a question, and it starts nothing. A pid this
    user may not signal still exists, which is what the `PermissionError` arm says. Public
    because gc (issue 05 of F2) decides lock staleness with it."""
    try:
        pid = int(pid_text.strip())
    except ValueError:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError, OSError:
        return True
    return True


def _resume_run_label(record: Record) -> str:
    """`<branch> — <feature or spec> (<relative time>, <outcome or PR state>)` — the
    "Resume a run" submenu's label. Reuses `_overall_outcome`/`_age` (the same rendering
    `status` uses) so the two views can't drift; when a run has no outcome yet (mid-review,
    or an ad-hoc/spec run that never records one) falls back to whether a PR was ever
    recorded, still without a live `gh` call — the picker stays fast and offline-safe.

    **Its only upstream caller, `_pick_resume`, is the interactive picker, which is not
    ported** — decision 1 of the F2 README puts it out of scope — so nothing in this tree
    calls this today. It lives here, not with resume, because the no-drift contract above
    can only be held where `_overall_outcome` and `_age` live; `tests/test_status.py`
    asserts the shared routing rather than trusting this sentence.
    """
    branch = record.get("branch") or "?"
    identity = (
        f"feature {record['feature']}"
        if record.get("feature")
        else f"spec {record.get('spec') or '?'}"
    )
    outcome = _overall_outcome(record)
    if outcome == "-":
        outcome = "PR open" if record.get("pr_url") else "no PR yet"
    return f"{branch} — {identity} ({_age(record.get('timestamp'))}, {outcome})"


def _source_summary_label(
    specs_dir: Path,
    spec: str | None,
    feature: str | None,
    prompt_text: str | None = None,
    resume_branch: str | None = None,
) -> str:
    """One line naming what a dispatch is about to run: a resume's branch, a feature, a
    pending or materialized ad-hoc prompt, or a spec path."""
    if resume_branch:
        return f"Resume run on {resume_branch}"
    if feature:
        return feature
    if prompt_text is not None:
        # Not yet materialized (that happens at Dispatch) — nothing to name it after here,
        # so the summary shows the text itself rather than a path.
        return f"ad-hoc prompt: {first_line(prompt_text)}"
    assert spec is not None
    path = Path(spec)
    if path.parent == specs_dir / AD_HOC_DIR:
        return f"ad-hoc prompt ({path.name})"
    return spec


def _issues_summary_label(feature_dir: Path, issues_arg: str) -> str:
    """Mirrors pick_issues' own "All"/"All unfinished" framing (same ready-set computation)
    so the summary reads as a restatement of that choice, not a different rule.
    `pick_issues` is the interactive picker's, unported under decision 1 of the F2 README —
    the constraint stands for whoever lands it."""
    if issues_arg:
        nums = [
            n
            for tok in issues_arg.split(",")
            if tok.strip() and (n := leading_number(tok)) is not None
        ]
        return ", ".join(f"{n:02d}" for n in nums)
    issues = load_issues(feature_dir / "issues")
    ready = select_issues(feature_dir, None)
    any_done = any(iss.is_done for iss in issues)
    nums_label = ", ".join(iss.display_number for iss in ready)
    return f"{'all unfinished' if any_done else 'all'} ({nums_label})"


def _summary_lines(
    specs_dir: Path,
    spec: str | None,
    feature: str | None,
    prompt_text: str | None,
    issues_arg: str,
    branch: str,
    create_branch: bool,
    base: str,
    resume_branch: str | None = None,
    feedback: str = "",
) -> list[str]:
    """The "review your choices" block: source, issues (feature runs only), feedback
    (resumes only), branch, base — one `Key: value` line each.

    **Its only upstream caller is `_summary_menu`, the interactive picker's confirmation
    step, which is not ported** (decision 1 of the F2 README) — so nothing in this tree
    calls this today. It is the picker's *data*, not its terminal UI, which is why it lands
    with the port rather than waiting.
    """
    lines = [
        f"Source: {_source_summary_label(specs_dir, spec, feature, prompt_text, resume_branch)}"
    ]
    if feature:
        lines.append(f"Issues: {_issues_summary_label(specs_dir / feature, issues_arg)}")
    if resume_branch:
        lines.append(f"Feedback: {first_line(feedback) if feedback else '(none)'}")
    branch_label = f"{branch} (will be created)" if create_branch else branch
    lines.append(f"Branch: {branch_label}")
    lines.append(f"Base: {base}")
    return lines
