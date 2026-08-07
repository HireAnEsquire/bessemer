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
are live): a checkout is excluded from the plan if `classify_liveness` reads its slug as
`IN_FLIGHT` — a live lock, per ADR 0004, never a live container alone (see below). With
docker down, liveness cannot be verified, so the container class is not reported at all and
every remaining item is marked undeletable. A checkout in use in any way these two signals
cannot see — a human working inside it, a container named off-convention — is reported as an
orphan; the port source's only further protection is on the deletion side (`run.sh`
re-checks both signals immediately before each removal, and salvages the branch by fetch,
skipping loudly if it is not a fast-forward), which F3 inherits as a requirement, not as code
that exists here.

**`classify_liveness` is ADR 0004's twelve-cell table, and the only place it is written
down.** A run's liveness is a property of its dispatcher (the pid in `<locks_dir>/<slug>.pid`),
not of the container that pid may have started — a container can outlive the process that
made it, `sleep infinity` in the adapter image being the tracer's own proof. The two signals
compose asymmetrically: **`Up` is not proof of life; `Exited` is proof of death.** An exited
container settles the question alone, no lock overrides it; anything else defers to the lock,
and a lock this function cannot read is `UNVERIFIABLE` rather than guessed at either way.
`bessemer/reclaim.py` imports this same function for its own per-item re-check rather than
restating the table — ADR 0004's argued exception to this package's restate-rather-than-import
rule, because a twelve-cell decision table copied twice is how a scan and an executor disagree
about the same slug without either being wrong on its own terms.
"""

from dataclasses import dataclass
from enum import Enum
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


class Liveness(Enum):
    """What `classify_liveness` decided a slug's run is, of the three ADR 0004 names."""

    IN_FLIGHT = "in-flight"
    """The dispatcher is alive. Every one of the slug's artifacts is hidden from the plan."""

    ORPHAN = "orphan"
    """No dispatcher owns this slug's artifacts. What gc lists and reclaim reclaims."""

    UNVERIFIABLE = "unverifiable"
    """The lock could not be read, so the dispatcher's liveness cannot be answered either
    way. Kept, and said out loud — the same posture `reclaim.py` already held for a docker
    that could not answer, now covering the lock half too."""


@dataclass(frozen=True)
class Classification:
    """One slug's liveness, and why — ADR 0004's answer, carried rather than re-derived so
    `gc` and `reclaim` cannot read the same facts and disagree."""

    liveness: Liveness
    reason: str


def classify_liveness(
    *, container_status: str | None, lock_pid_alive: bool | None
) -> Classification:
    """ADR 0004's twelve-cell table: what state a slug's run is in, from its container's
    docker status (`None` for no container, otherwise the raw `docker ps` status text) and
    its lock's pid (`True` alive, `False` no lock file or a dead pid, `None` the lock file
    exists but could not be read).

    `Exited` settles the question alone, in **one direction only**: it is checked first and
    nothing after it can override an orphan verdict. Every other container state — `Up` or no
    container at all — defers entirely to the lock, because neither is proof that a
    dispatcher is watching. A lock this function cannot read is `UNVERIFIABLE` rather than
    guessed at either way, and that check runs before the live-pid check so an unreadable
    lock is never misread as a dead one.

    The function is handed the facts; it does not go and get them — `gc.py` stays pure, and
    `reclaim.py`'s own liveness probe (`docker ps -q`, no `-a`) cannot distinguish an exited
    container from no container at all, so it calls this with `container_status=None` for
    "not `Up` right now" rather than claiming `Exited`. That is the safe direction: it defers
    to the lock exactly where `gc`'s richer, `-a`-backed scan would already defer to it, and
    the one cell where this could differ from a true `Exited` reading — a live pid, which
    `Exited` would still call an orphan — is `IN_FLIGHT`'s own reason for existing: the
    re-check window between a scan and a delete is exactly what a dispatch starting mid-gc
    looks like, and keeping a slug whose lock just went live is the correct read regardless
    of whether the container behind it has come up yet.
    """
    if container_status is not None and not is_live_status(container_status):
        return Classification(Liveness.ORPHAN, "the container has exited")
    if lock_pid_alive is None:
        return Classification(Liveness.UNVERIFIABLE, "the lock could not be read")
    if lock_pid_alive:
        return Classification(Liveness.IN_FLIGHT, "the lock names a live pid")
    return Classification(Liveness.ORPHAN, "no live dispatcher owns it")


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


def _lock_pid_alive(locks_dir: Path, slug: str) -> bool | None:
    """The lock half of `classify_liveness`'s second signal: `True` a live pid owns this
    slug's artifacts (even with no container in sight — the clone-before-`docker run` and
    cleanup-after-`docker rm` windows), `False` no lock file or a dead pid, `None` a lock
    file that exists but could not be read.

    **Absent and unreadable are different facts.** A missing lock is the ordinary case for
    an orphan gc has always listed; a lock `read_text` cannot get through is a fact this
    function cannot answer past, and collapsing the two into one `except OSError: return
    False` — as this used to — would report a definite orphan for a slug this process
    simply could not check, which is exactly what `UNVERIFIABLE` exists to refuse."""
    try:
        pid_text = (locks_dir / f"{slug}.pid").read_text()
    except FileNotFoundError:
        return False
    except OSError:
        return None
    return pid_alive(pid_text)


_LOCK_UNREADABLE: Final = "could not be read, unverified"
"""What an `UNVERIFIABLE` item's `would` field names, paired at the call site with the
lock's own path so the sentence points at the file an operator would go look at rather than
just asserting one exists. Private: nothing outside `_unverified_would` composes this."""


def _unverified_would(locks_dir: Path, slug: str) -> str:
    """The sentence an `UNVERIFIABLE` item is kept under: names the lock, the artifact stays
    put, and says why — the acceptance the ADR itself insists on for a deletion nobody could
    verify was safe."""
    return f"kept — the lock ({locks_dir / f'{slug}.pid'}) {_LOCK_UNREADABLE}"


def collect_gc_items(
    *, checkouts_dir: Path, locks_dir: Path, docker_rows: list[str], docker_down: bool
) -> list[GcItem]:
    """Every artifact in the three actionable gc classes (containers, checkouts, locks),
    classified per slug by `classify_liveness` (ADR 0004) rather than by container status
    alone. Logs aren't items: every past run leaves a .log behind forever, so per-log rows
    would grow monotonically and drown the classes gc can act on — they're one
    `summarize_logs` line instead. `docker_down` disables both the container class (nothing
    to report — `docker ps -a` never ran) and deletability everywhere else (liveness can't be
    verified, so nothing computed here is ever safe to delete).

    `IN_FLIGHT` hides a slug's artifacts, exactly as before. `ORPHAN` lists them, deletable
    when docker is up. `UNVERIFIABLE` — a lock this process could not read — also lists them,
    loudly and never deletable, rather than silently excluding or silently including them:
    ADR 0004's "kept, and said out loud" applies to gc's listing as much as to reclaim's walk.
    """
    containers: dict[str, str] = (
        {} if docker_down else {c.slug: c.uptime for c in parse_docker_rows(docker_rows)}
    )
    deletable = not docker_down

    def classify(slug: str) -> Classification:
        return classify_liveness(
            container_status=containers.get(slug),
            lock_pid_alive=_lock_pid_alive(locks_dir, slug),
        )

    items = []

    for slug in sorted(containers):
        classification = classify(slug)
        if classification.liveness is Liveness.IN_FLIGHT:
            continue
        would = (
            f"docker rm -fv {CONTAINER_PREFIX}{slug}"
            if classification.liveness is Liveness.ORPHAN
            else _unverified_would(locks_dir, slug)
        )
        items.append(
            GcItem(
                "container",
                slug,
                containers[slug] or "?",
                "-",
                would,
                deletable and classification.liveness is Liveness.ORPHAN,
            )
        )

    if checkouts_dir.is_dir():
        for d in sorted(checkouts_dir.iterdir()):
            if not d.is_dir():
                continue
            classification = classify(d.name)
            if classification.liveness is Liveness.IN_FLIGHT:
                continue
            would = (
                "salvage-fetch then rm -rf (skips + flags for manual inspection if non-FF)"
                if classification.liveness is Liveness.ORPHAN
                else _unverified_would(locks_dir, d.name)
            )
            items.append(
                GcItem(
                    "checkout",
                    d.name,
                    mtime_age(d),
                    _human_size(_dir_size(d)),
                    would,
                    deletable and classification.liveness is Liveness.ORPHAN,
                )
            )

    if locks_dir.is_dir():
        for p in sorted(locks_dir.glob("*.pid")):
            slug = p.stem
            classification = classify(slug)
            if classification.liveness is Liveness.IN_FLIGHT:
                continue
            would = (
                "rm -f"
                if classification.liveness is Liveness.ORPHAN
                else _unverified_would(locks_dir, slug)
            )
            items.append(
                GcItem(
                    "lock",
                    slug,
                    mtime_age(p),
                    "-",
                    would,
                    deletable and classification.liveness is Liveness.ORPHAN,
                )
            )

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
