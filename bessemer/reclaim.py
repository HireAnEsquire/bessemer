"""The deletion side of gc: a plan walked, re-checked, and acted on — or kept and said so.

`bessemer/gc.py` scans and returns a plan a human can read; **this module is the only thing
in the package that acts on it**, and ADR 0003 draws the seam there deliberately. gc stays
pure — its no-subprocess property is what keeps every scan test fast and fixture-driven, and
`tests/test_gc.py` asserts the restraint over that module's AST. Everything that deletes is
here, behind one entry point.

Oracle region: `run.sh:422–523` at the pin.

## The plan is a scan-time snapshot, and that is the whole design

Between the `docker ps` gc scanned with and the moment an item is touched, a dispatch can
start. So **every item is re-checked immediately before anything happens to it**, against the
same two signals the scan itself excluded on (pin :462–474):

1. a live container named `bessemer-<slug>`, and
2. a live pid in `<locks_dir>/<slug>.pid` — the clone-before-`docker run` and
   cleanup-after-`docker rm` windows, where a dispatch owns a slug's artifacts with no
   container anywhere in sight.

Either one keeps the item, loudly. A re-check hoisted out of the loop would be a scan-time
snapshot wearing a re-check's name, and `tests/test_reclaim.py` counts the probes for that
reason.

**One named divergence from the pin, in the safe direction.** The pin reads its re-check
through a command substitution, so a `docker ps` that *failed* yields the empty string and is
read as "no container" — a broken daemon makes the `rm -rf` proceed. Here a docker that could
not answer keeps the item: that is the same unverifiable liveness the docker-down arm refuses
the whole run over, one item down, and answering it two different ways would make the
whole-run refusal a posture the per-item loop does not hold. `bessemer.passes` reads the same
silence the opposite way and is right to — aborting a pass into a container nobody can see is
its safe direction — which is why only `container.liveness_argv` is shared between them and
each owns its own failure arm.

## Docker down refuses the whole run, and the refusal lives here

`gc` *listing* works with a warning when docker is unavailable (F2, already landed); `--force`
refuses entirely (pin :453–456). The guard is in `execute_gc_plan` rather than in the CLI that
also asks the question, because "nothing is deleted when liveness cannot be verified" is a
property of the thing that deletes, not a rule a caller remembers. `REFUSED_DOCKER_DOWN` is
the sentence; the CLI decides it goes to stderr.

## The checkout class never discards unpushed work

A checkout is the one artifact that can hold commits existing nowhere else. So:

- the branch is read as **text**, `checkout.read_branch`, never `git -C <checkout>` — the
  checkout's `.git/config` and hooks are agent-controlled (pin comment :499–504);
- a detached HEAD has no branch to salvage and is **kept, loudly**, for manual inspection;
- otherwise `checkout.salvage` — ADR 0003's single definition of a refspec the pin spells
  three times, fast-forward-only with no `+`. It advanced: remove. It did not: **kept,
  loudly**. A non-fast-forward means the agent rebased or reset inside the checkout, and an
  `rm -rf` there discards work that exists nowhere else.

Importing `salvage` rather than restating the fetch is the point (F3 README decision 3); three
spellings of a security-relevant refspec are three chances for one of them to grow a `+`.

## Logs are never touched, structurally

ADR 0001's invariant. This module **takes no logs directory at all** — there is no parameter
through which one could arrive, no constant naming one, and the class dispatch below acts only
on the three classes it knows. A plan line naming any other class — or naming no slug at all —
is kept and reported, which is the second lock on a door `gc.render_gc_plan`'s class filter
already holds shut (and which `tests/test_gc.py` now pins alone, per F2 decision 9's fourth
debtor entry).

## Failures do not stop the walk

A `docker rm` that failed is reported, the remaining items are still processed, and the report
says the run failed. A deleter that stopped at the first failure would leave a machine half
reclaimed and an operator with no way to tell which half — the state gc exists to end.

## Vocabulary

The pin's skip sentences are ported whole, with two renames on the rule
`bessemer/status.py`'s docstring records: the container prefix is `bessemer-`, and the pin's
"inside the clone" becomes "inside the checkout" — CONTEXT.md resolved **clone** to a verb
only, and the noun is **checkout**. Neither string is inside any upstream assertion.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from bessemer import checkout as checkout_ops
from bessemer import container as container_ops
from bessemer import proc
from bessemer.status import CONTAINER_PREFIX, pid_alive

_PLAN_SEPARATOR: Final = "\t"
"""What `gc.render_gc_plan` puts between a class and a slug.

Private: the plan is a text interface between two modules and this is one end's reading of it,
not something a caller composes with. Restated rather than imported from `gc` for the reason
every literal in this package is — a producer and a consumer agreeing by construction cannot
notice one of them changing.
"""

REFUSED_DOCKER_DOWN: Final = (
    "gc --force refused: docker is down, container liveness can't be verified — nothing deleted"
)
"""Why a `--force` did nothing at all (pin :453–456).

The sentence says *why*, not that: an operator who reads "nothing deleted" without the reason
runs it again. Bessemer's rendering drops the pin's `!! ` prefix, which the CLI's stderr
refusals do not use.
"""

BANNER: Final = (
    "bessemer: gc --force — deleting orphans (re-checking liveness immediately before each) ..."
)
"""The line printed before the walk starts (pin :459), product token renamed.

It promises the re-check, which is what tells a reader why a `skip` line can contradict the
listing printed seconds earlier.
"""


class Action(Enum):
    """What became of one plan item. Three, because there are three ends a walk can reach."""

    RECLAIMED = "reclaimed"
    """The artifact is gone."""

    KEPT = "kept"
    """Deliberately not touched — live, unverifiable, detached, diverged, already gone, or of
    a class this module does not act on. Not a failure: keeping is the safe answer, and it is
    the answer most of these arms are *for*."""

    FAILED = "failed"
    """Something that should have worked did not. The run continues; the exit code does not."""


@dataclass(frozen=True)
class Outcome:
    """One plan item, what happened to it, and the sentence the operator was told.

    `line` is carried rather than recomposed by the caller: the emitted text and the report
    are otherwise two accounts of one run, free to disagree about what happened.
    """

    cls: str
    slug: str
    action: Action
    line: str


@dataclass(frozen=True)
class ReclaimReport:
    """What a `gc --force` came to.

    **No `__bool__`, and none may be added**, for `proc.Result`'s reason: `if report:` reads
    as "is there a report" to one reader and "did it work" to the next. `failed` is the whole
    decision and has to be asked for by name. `__len__` is excluded on the same grounds.
    """

    outcomes: tuple[Outcome, ...]
    refused: bool
    """Whether the run refused before touching anything — docker down, liveness unverifiable.
    `outcomes` is empty when it is true, because nothing was walked at all."""

    @property
    def failed(self) -> bool:
        """Whether anything that should have worked did not. The CLI's nonzero exit.

        Derived rather than recorded, so a stored flag and the outcomes cannot disagree about
        one decision. A refusal is deliberately **not** counted here — it is its own field,
        and the CLI exits nonzero on either.
        """
        return any(outcome.action is Action.FAILED for outcome in self.outcomes)


def _plan_items(plan: str) -> Iterator[tuple[str, str]]:
    """The plan's `<class>\\tslug` lines as pairs, blank lines dropped (the pin's `[ -n ]`).

    A line with no separator yields an empty slug, which the walk refuses rather than acts
    on — the pin's `read -r gc_class gc_slug` reaches the same two fields, and its `case`
    would fall through, but a `docker rm -fv bessemer-` is not a fallthrough worth inheriting.
    """
    for line in plan.splitlines():
        if not line.strip():
            continue
        cls, _, slug = line.partition(_PLAN_SEPARATOR)
        yield cls, slug


@dataclass(frozen=True)
class _Item:
    """One plan item and everywhere its artifacts could be — what a per-class action is given.

    A parameter object rather than five keyword arguments across three functions, for
    `container.Boundary`'s reason one module along: the three actions have different needs,
    and passing all five to each meant every one of them opening with a `del` of the ones it
    does not use. **There is no `logs_dir` field, and that is the structural half of ADR
    0001's gc-never-deletes-logs invariant** — an action cannot name a directory that reached
    it through nothing.
    """

    slug: str
    checkouts_dir: Path
    locks_dir: Path
    repo_root: Path
    run: proc.Runner

    @property
    def container(self) -> str:
        """The container this slug's run owns. One composition site for the prefix."""
        return f"{CONTAINER_PREFIX}{self.slug}"

    @property
    def checkout(self) -> Path:
        return self.checkouts_dir / self.slug

    @property
    def lock(self) -> Path:
        return self.locks_dir / f"{self.slug}.pid"


def _container_live(item: _Item) -> bool | None:
    """Whether this item's container is there. `None` when docker could not say.

    Three answers rather than two, and the third is the point: an unanswerable probe is not
    "no container". See the module docstring for why this is the opposite of
    `bessemer.passes`' reading of the same silence.

    The two cases `proc.run` deliberately does not flatten into a `Result` — a docker that
    could not be executed (`OSError`) and one that hung past the deadline
    (`proc.TimeoutExpired`) — are absorbed here, as the same `None` a nonzero exit gives.
    """
    argv = container_ops.liveness_argv(container=item.container)
    try:
        result = item.run(argv, timeout=container_ops.LIVENESS_TIMEOUT_SECONDS)
    except OSError, proc.TimeoutExpired:
        return None
    if not result.ok:
        return None
    return bool(result.stdout.strip())


def _lock_pid(item: _Item) -> str:
    """The pid text in this item's lock file, or empty when there is none to read.

    Read here rather than through `gc._lock_pid_alive` because the pid itself goes into the
    skip line: "a live run (pid 4131) owns it now" is a sentence an operator can act on, and
    a boolean is not.
    """
    try:
        return item.lock.read_text().strip()
    except OSError:
        return ""


def execute_gc_plan(
    plan: str,
    *,
    checkouts_dir: Path,
    locks_dir: Path,
    repo_root: Path,
    docker_down: bool,
    emit: Callable[[str], None],
    run: proc.Runner,
) -> ReclaimReport:
    """Walk `plan`, re-checking liveness before each item, and report what became of it.

    `plan` is `gc.render_gc_plan`'s output — deletable items of known classes, one
    `<class>\\tslug` line each. Taken as text rather than as `GcItem`s so that the filter
    the plan already applied is the filter this module acts behind: the class dispatch below
    is what the executor trusts, and `tests/test_gc.py` pins that filter alone for exactly
    that reason.

    `docker_down` refuses the whole run before a single argv is built; see the module
    docstring for why the guard is here rather than at the caller.

    `emit` takes the operator-facing lines, in order, as they happen — a reclaim can take a
    while and a report printed at the end would be silence while an `rm -rf` runs. Every line
    is also carried on its `Outcome`.

    Never raises for an item: a failure is an `Action.FAILED` outcome, the walk continues, and
    `ReclaimReport.failed` is what the CLI exits on.
    """
    if docker_down:
        return ReclaimReport(outcomes=(), refused=True)

    # Emitted here rather than by the caller, and after the guard: the banner promises the
    # per-item re-check, and a run that refused never made one.
    emit(BANNER)

    outcomes: list[Outcome] = []

    def record(cls: str, slug: str, action: Action, line: str) -> None:
        emit(line)
        outcomes.append(Outcome(cls=cls, slug=slug, action=action, line=line))

    for cls, slug in _plan_items(plan):
        if cls not in _ACTIONS or not slug:
            # Both halves are unreachable through `gc.render_gc_plan` — it filters classes and
            # every item it emits has a slug — and both are arms rather than assertions,
            # because what could arrive here is a `log` line and a line with no separator, and
            # the answer to each is the same: name it and touch nothing. Without the slug half
            # a malformed line would reach `docker rm -fv bessemer-`.
            record(
                cls,
                slug,
                Action.KEPT,
                f"  skip {cls}/{slug} — unusable plan line, not touching",
            )
            continue

        item = _Item(
            slug=slug,
            checkouts_dir=checkouts_dir,
            locks_dir=locks_dir,
            repo_root=repo_root,
            run=run,
        )

        live = _container_live(item)
        if live is None:
            record(
                cls,
                slug,
                Action.KEPT,
                f"  skip {cls}/{slug} — docker could not answer, liveness unverified,"
                " not touching",
            )
            continue
        if live:
            record(
                cls,
                slug,
                Action.KEPT,
                f"  skip {cls}/{slug} — container is live now, not touching",
            )
            continue

        pid = _lock_pid(item)
        if pid and pid_alive(pid):
            record(
                cls,
                slug,
                Action.KEPT,
                f"  skip {cls}/{slug} — a live run (pid {pid}) owns it now, not touching",
            )
            continue

        action, line = _ACTIONS[cls](item)
        record(cls, slug, action, line)

    return ReclaimReport(outcomes=tuple(outcomes), refused=False)


def _reclaim_container(item: _Item) -> tuple[Action, str]:
    """`docker rm -fv bessemer-<slug>` (pin :481–486).

    `container.remove` rather than a second `docker rm` here: that function owns the flags and
    already never raises on docker's exit status, which is the behaviour this walk needs.
    """
    result = container_ops.remove(container=item.container, run=item.run)
    if result.ok:
        return Action.RECLAIMED, f"  removed container {item.container}"
    return Action.FAILED, f"  !! failed to remove container {item.container}"


def _reclaim_checkout(item: _Item) -> tuple[Action, str]:
    """Salvage, then remove — or keep the checkout and say so loudly (pin :488–511).

    The three keeping arms are the module's reason to exist; see its docstring. The loud ones
    name the path, because "inspect manually" without one is an instruction nobody can follow.
    """
    slug, checkout = item.slug, item.checkout
    if not checkout.is_dir():
        return Action.KEPT, f"  skip checkout/{slug} — already gone"

    branch = checkout_ops.read_branch(checkout)
    if branch is None:
        return (
            Action.KEPT,
            f"  !! SKIPPING checkout {slug} — detached HEAD, no branch to salvage"
            f" — inspect manually: {checkout}",
        )

    salvage = checkout_ops.salvage(
        repo_root=item.repo_root, checkout=checkout, branch=branch, run=item.run
    )
    if not salvage.advanced:
        return (
            Action.KEPT,
            f"  !! SKIPPING checkout {slug} — salvage fetch was not a fast-forward"
            f" (branch diverged/rebased inside the checkout) — inspect manually: {checkout}",
        )

    try:
        checkout_ops.remove(checkout)
    except OSError as error:
        # `checkout.remove` tolerates an absent tree and propagates everything else, because a
        # checkout that cannot be removed is a leak and silence about it is worse than a
        # nonzero exit. The branch is already salvaged, so nothing is lost — but the tree is
        # still there, and the operator is the only one who can act on that.
        return Action.FAILED, f"  !! failed to remove checkout {checkout}: {error.strerror}"
    return (
        Action.RECLAIMED,
        f"  salvaged + removed checkout {slug} (branch '{branch}' fast-forwarded)",
    )


def _reclaim_lock(item: _Item) -> tuple[Action, str]:
    """Remove the pid file (pin :513–516).

    `missing_ok` is the pin's `rm -f`: another gc, or the dispatch that owned it finishing
    cleanly between the scan and now, is not something to report failed.
    """
    try:
        item.lock.unlink(missing_ok=True)
    except OSError as error:
        return Action.FAILED, f"  !! failed to remove stale lock {item.slug}: {error.strerror}"
    return Action.RECLAIMED, f"  removed stale lock {item.slug}"


_ACTIONS: Final = {
    "container": _reclaim_container,
    "checkout": _reclaim_checkout,
    "lock": _reclaim_lock,
}
"""The three classes this module acts on, and what acting means for each.

A table rather than a `match`, so "the classes reclaim knows" is a value the unknown-class arm
above can test against — one list, checked before dispatch instead of a fallthrough nobody
notices. It is deliberately **not** built from `gc._GC_DELETABLE_CLASSES`: that constant says
what may go in a plan and this one says what can be executed, and a shared name would let
growing either one silently grow the other.
"""
