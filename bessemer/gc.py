"""The gc scan: finding what old run state is reclaimable, and saying so before anything acts.

Ported from the port source's `.agentbox/tasklib.py` at commit `e194121f75f4`. **The
upstream tests are the specification** — where this module and a reading of what it "should"
do disagree, upstream wins, and the disagreement is a finding in the port report rather than
a silent improvement. `tests/port_manifest.py` is the census that makes that checkable.

**This module deletes nothing — and the name says otherwise, so it is stated here.**
`collect_gc_items` scans and returns a plan; `render_gc` and `render_gc_plan` show it. No
function below removes, moves or truncates a file, and that is the whole shape of the thing:
the plan is a value a human can read before anything acts on it, and F3 owns the acting. A
module called `gc` that returns a list is a module someone will eventually "finish" by
adding the deletion; do not. The reason it stops short is that the deletion side needs a
liveness re-check immediately before each removal and a salvage fetch before any checkout is
discarded (the port source's `run.sh` gc `--force` block, lines 422–523, is the reference
implementation), and none of that belongs in a pure scan whose every test runs against
fixtures. `tests/test_gc.py` asserts the restraint over this module's AST rather than
trusting this paragraph.

**Nothing here spawns a subprocess.** Container state arrives as parsed `docker ps` rows the
CLI gathered through `bessemer.proc` — the same seam as `bessemer/status.py`, and the reason
`tests/guard.py` can deny `docker` to the whole suite. The one operating-system question
asked is `pid_alive`'s signal-0 probe, imported from status, which starts nothing.

**The table, the ages and the liveness tests are status's, imported.** `format_table`,
`mtime_age`, `is_live_status`, `pid_alive` and `parse_docker_rows` come from
`bessemer/status.py` — porting them twice is how two renderers drift into disagreeing about
what a table looks like. `_lock_pid_alive` alone is ported here, because gc is its only
caller.

**Vocabulary.** Two product-name literals are renamed at port time, on the rule
`bessemer/status.py`'s docstring records (a product name in inputs and output prose renames;
a value inside an upstream assertion never does): the `docker rm -fv` commands the scan
proposes use `CONTAINER_PREFIX` (`bessemer-`, upstream `agentbox-`), and `render_gc`'s
header reads "Orphaned bessemer artifacts" where upstream's named its own product. Neither
string is inside any upstream assertion. The header's docker-down clause still says what
`gc --force` would do — force is F3's, unported, and the clause travels as the constraint
its dispatcher must satisfy.

**What a scan does with a checkout that is currently in use** (F3 runs gc while dispatches
are live): a checkout is excluded from the plan if its slug matches a live (`Up`) container,
or if `<locks_dir>/<slug>.pid` names a live process — the clone-before-`docker run` and
cleanup-after-`docker rm` windows. With docker down, liveness cannot be verified, so the
container class is not reported at all and every remaining item is marked undeletable. A
checkout in use in any way those two signals cannot see — a human working inside it, a
container named off-convention — is reported as an orphan; the port source's only further
protection is on the deletion side (`run.sh` re-checks both signals immediately before each
removal, and salvages the branch by fetch, skipping loudly if it is not a fast-forward),
which F3 inherits as a requirement, not as code that exists here.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from bessemer.status import (
    CONTAINER_PREFIX,
    format_table,
    is_live_status,
    mtime_age,
    parse_docker_rows,
    pid_alive,
)

CHECKOUTS_DIR: Final = "checkouts"
"""Where run checkouts live, under the adapter directory: `.bessemer/checkouts/<slug>/`.

The port source kept them in `$BOX/checkouts`; the adapter directory is that anchor here.
Same F3 debt as status's `LOGS_DIR` and `LOCKS_DIR`: dispatch must clone where this scans,
or gc will never see what it leaks.
"""

_GC_DELETABLE_CLASSES: Final = frozenset({"container", "checkout", "lock"})
"""The classes `render_gc_plan` will put in a plan — the three gc can act on. Logs are
deliberately not one (see `summarize_logs`), and `tests/test_gc.py` pins this set by hand."""


@dataclass
class GcItem:
    """One reclaimable artifact, as the plan reports it: what class of thing, which run's,
    how old, how big, and what acting on it would do."""

    cls: str
    slug: str
    age: str
    size: str
    would: str
    deletable: bool


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file() and not p.is_symlink():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "K", "M"):
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}G"


def _lock_pid_alive(locks_dir: Path, slug: str) -> bool:
    """A live pid in `<locks_dir>/<slug>.pid` means a dispatch currently owns that slug's
    artifacts even when no container exists yet (or anymore) — the clone-before-`docker run`
    and cleanup-after-`docker rm` windows."""
    try:
        pid_text = (locks_dir / f"{slug}.pid").read_text()
    except OSError:
        return False
    return pid_alive(pid_text)


def collect_gc_items(
    *, checkouts_dir: Path, locks_dir: Path, docker_rows: list[str], docker_down: bool
) -> list[GcItem]:
    """Every artifact in the three actionable gc classes (containers, checkouts, locks),
    orphan-filtered per class (the port source's `docs/AGENT_SANDBOXING.md` gc section has
    the definitions — unported). Logs aren't items: every past run leaves a .log behind
    forever, so per-log rows would grow monotonically and drown the classes gc can act on —
    they're one `summarize_logs` line instead. `docker_down` disables both the container
    class (nothing to report — `docker ps -a` never ran) and deletability everywhere else
    (liveness can't be verified, so nothing computed here is ever safe to delete)."""
    containers = [] if docker_down else parse_docker_rows(docker_rows)
    live_slugs = {c.slug for c in containers if is_live_status(c.uptime)}
    deletable = not docker_down

    items = []

    for c in sorted(
        (c for c in containers if not is_live_status(c.uptime)), key=lambda c: c.slug
    ):
        items.append(
            GcItem(
                "container",
                c.slug,
                c.uptime or "?",
                "-",
                f"docker rm -fv {CONTAINER_PREFIX}{c.slug}",
                deletable,
            )
        )

    if checkouts_dir.is_dir():
        for d in sorted(checkouts_dir.iterdir()):
            if not d.is_dir() or d.name in live_slugs:
                continue
            if _lock_pid_alive(locks_dir, d.name):
                # A dispatch owns this checkout in its pre/post-container window —
                # not an orphan.
                continue
            items.append(
                GcItem(
                    "checkout",
                    d.name,
                    mtime_age(d),
                    _human_size(_dir_size(d)),
                    "salvage-fetch then rm -rf (skips + flags for manual inspection if non-FF)",
                    deletable,
                )
            )

    if locks_dir.is_dir():
        for p in sorted(locks_dir.glob("*.pid")):
            slug = p.stem
            if slug in live_slugs:
                continue
            try:
                pid_text = p.read_text()
            except OSError:
                pid_text = ""
            if pid_alive(pid_text):
                # Process still running (just hasn't reached the container step yet) —
                # not stale.
                continue
            items.append(GcItem("lock", slug, mtime_age(p), "-", "rm -f", deletable))

    return items


def summarize_logs(logs_dir: Path) -> str:
    """Logs in one line, not one row each — count + total size only. They're audit trail:
    never deleted, so there's nothing per-log for the dispatcher to decide."""
    if not logs_dir.is_dir():
        return ""
    current = sorted(logs_dir.glob("*.log"))
    rotated = sorted(logs_dir.glob("*.log.1"))
    if not current and not rotated:
        return ""
    total = sum(p.stat().st_size for p in current + rotated)
    counts = f"{len(current)} current"
    if rotated:
        counts += f" + {len(rotated)} rotated"
    return (
        f"logs: {counts}, {_human_size(total)} total"
        " — kept (audit trail, gc never deletes logs)"
    )


def render_gc(items: list[GcItem], docker_down: bool, log_summary: str = "") -> str:
    """The scan as a human-readable report, one string; the CLI prints it."""
    header = "Orphaned bessemer artifacts"
    if docker_down:
        header += (
            "  (docker unavailable — liveness unverified, listing only;"
            " gc --force would refuse)"
        )
    lines = [header]
    if not items:
        lines.append("  nothing to reclaim")
        if log_summary:
            lines.append(f"  {log_summary}")
        return "\n".join(lines)
    table = format_table(
        ["CLASS", "SLUG/BRANCH", "AGE", "SIZE", "WOULD"],
        [[i.cls, i.slug, i.age, i.size, i.would] for i in items],
        truncate_cols={1},
    )
    lines.extend(f"  {row}" for row in table)
    if log_summary:
        lines.append(f"  {log_summary}")
    return "\n".join(lines)


def render_gc_plan(items: list[GcItem]) -> str:
    """The scan as machine-readable TSV, deletable items of known classes only — what the
    deletion side (F3's) walks, re-checking liveness before each line."""
    return "\n".join(
        f"{i.cls}\t{i.slug}" for i in items if i.deletable and i.cls in _GC_DELETABLE_CLASSES
    )
