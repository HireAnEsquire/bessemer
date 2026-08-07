"""The assembly: one run, from the guards that refuse it to the ledger line that records it.

ADR 0003's `dispatch(...) -> RunOutcome`, and the only module `bessemer.cli` calls for a run.
The depth lives in the modules composed here — `checkout`, `container`, `passes`, `landing`,
`prompts` — and what this module owns is the **order**, the **lifecycle**, and the
**refusals**. Port source: `run.sh` at pin `e194121f75f4`, regions `:542–596`, `:779–832`,
`:875–888`, `:927–1002`, `:1004–1021`, `:1028–1190`, `:1432–1492`, `:1594–1644`, `:1726–1732`.

**Nothing is touched before the in-flight guard passes** (F3 decision 6.1, pin
`:1144–1148`): no lock write, no log rotation, no container removal. A refused dispatch must
leave a live run's state exactly as it found it, and the tier-2 scenario asserts that on both
channels — the recorded proc stream is empty from the guard onward *and* the tree is
byte-identical, because argv alone cannot see a file write.

**Upstream's traps become `try`/`finally` plus signal conversion** (F3 decision 6.2). The
`finally` is upstream's `cleanup()`: salvage, container removal, lock removal, and a failure
notification composed through `proc.quote`. *Divergence-that-fixes, recorded for the parity
reviewer:* upstream's own comment (`run.sh:1025–1029`) documents a live-observed bash defect —
an `EXIT` trap silently never fires when a backgrounded `run_task &` subshell exits normally,
which leaked a container and a checkout after every completed run. Python's `finally` closes
that class entirely, so a leaked-container difference in bash's favour at the parity gate is
the pin's bug and not the port's. Scope, stated so nobody tests the untestable: this covers
catchable exits. A `SIGKILL` leaves exactly the leftovers `gc` and `reclaim` exist for.

**The lock is an atomic exclusive create, and that is the second recorded divergence** (F3
decision 6.3). Upstream checks the lock at `:1148` and writes it at `:1150` — a window in
which two dispatches of one branch both pass the guard and both proceed. `_Lock.acquire` uses
`O_CREAT | O_EXCL`, and on failure re-reads the file to name the pid that won, which is the
sentence the guard would have printed. The parity gate is outcome-based and a race window is
not an outcome.

**Two divergences fall out of `bessemer.landing` taking the description as a value** (F3
decision 8.2, the seam F4's feature loop reuses). The pin pushes, then generates the pull
request description, then opens the pull request (`:1548`, `:1572`); here the description pass
must produce its text *before* `land` is called, so it runs before the push — and it runs
whether or not there will be anything to push, where the pin skips it inside the
`[ "$commits" -gt 0 ]` arm. The cost is one agent pass on a run whose passes succeeded and
which committed nothing, which is rare; the alternative is a second commit count in this
module, free to disagree with the one `landing` owns as its gate.

**One-off only.** `--feature`, `--resume`, `--last`, `--feedback`, `--hard-reset` and
`--dry-run` are F4's and F5's (F3 README decision 1), so `MODE` is `continue` and there is no
branch here that is not taken on every run.
"""

import os
import re
import shutil
import signal
import types
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, cast

from bessemer import (
    checkout,
    config,
    container,
    doctor,
    gc,
    issues,
    landing,
    ledger,
    passes,
    proc,
    prompts,
    resolve,
    resume,
    status,
)
from bessemer.config import Config
from bessemer.outcome import Resolved, Unresolved

LOGS_DIR: Final = status.LOGS_DIR
LOCKS_DIR: Final = status.LOCKS_DIR
CHECKOUTS_DIR: Final = gc.CHECKOUTS_DIR
CONTAINER_PREFIX: Final = status.CONTAINER_PREFIX
"""The rendezvous, aliased rather than restated — F2 debt 3 is exactly this agreement.

`bessemer status` and `bessemer gc` read what a run writes, and they find it by these four
names. A second spelling here would be two values free to disagree about where a run's log
lives, and the disagreement's shape is a status view that shows nothing while a run is plainly
happening. `tests/test_dispatch.py` writes a real tree through a faked proc seam and renders
`status` over it, then mutates one of these and watches the round-trip fail.
"""

LOG_SUFFIX: Final = ".log"
ROTATED_SUFFIX: Final = ".log.1"
LOCK_SUFFIX: Final = ".pid"
"""What a run's log, its single rotated generation and its lock file are called.

`.log.1` is one generation and no more (pin `:1155`): the branch and its pull request carry
the durable history, and this is "what happened last run".
"""

MODE: Final = "continue"
"""The only dispatch mode F3 has. `hard-reset` is F4's, with `--force-with-lease` behind it.

Written into the log's first line and the entry banner because both are text a reader compares
across runs, and a field that is always the same is still the field that stops being always
the same when F4 lands.
"""

TOTAL_STEPS: Final = 6
"""checkout, container, setup, implement, review, land — the pin's `TOTAL_STEPS=6` (`:237`)
for a one-off run, spelled as the count of `step` calls below rather than derived from them."""

REFUSAL_PREFIX: Final = "!! "
"""Upstream's marker for a line the operator has to act on (`run.sh`).

Refusals, failures **and warnings** wear it, which is the pin's own usage and not a widening:
`:952` fronts its stale-image advisory with the same two characters. The message constants
below hold the sentence *after* it, so the prefix is written once and the pin's whole line is
still reconstructible from either end.
"""

SPEC_NOT_FOUND: Final = "spec not found: {spec}"
BRANCH_MISSING: Final = (
    "branch '{branch}' doesn't exist — create it first: git branch '{branch}' [<start>]"
)
BRANCH_PROTECTED: Final = (
    "refusing '{branch}' as --branch — protected branches are PR targets (--base), "
    "never pushed to"
)
BASE_IS_BRANCH: Final = "--branch and --base name the same ref — the PR would target itself"
BRANCH_CHECKED_OUT: Final = (
    "'{branch}' is checked out in this repo — git refuses fetches/resets into a "
    "checked-out branch; switch away first"
)
"""The four branch guards' refusals, ported (`run.sh:930`, `:933`, `:937`, `:940`).

The protected one renders `resume.is_protected`'s answer and is **never a second
`case master|main`** (ADR 0003, F3 README decision 3): two definitions are how a dispatch
someday pushes to `main` while `resume` refuses it.
"""

DOCKER_MISSING: Final = "docker not found"
GH_MISSING: Final = "gh not found"
CREDENTIAL_MISSING: Final = (
    "no Claude credential — run `claude setup-token` and put it in {secrets}"
)
IMAGE_UNCONFIGURED: Final = (
    "no image configured, so a run has nothing to start a container from — "
    'set image = "<tag>" in {file} and build that tag from {adapter}/Dockerfile'
)
IMAGE_NOT_A_NAME: Final = "image is not an image name: {image!r}"
IMAGE_MISSING: Final = "image '{image}' missing — build it from {adapter}/Dockerfile and tag it"
"""Preflight's refusals (`run.sh:943–948`).

`docker` and `gh` are asked for with `shutil.which`, which is `command -v` and is the pin's
own question: presence. Whether the daemon answers and whether `gh` is authenticated are
`bessemer doctor`'s to report — a dispatch that said "docker not found" about a stopped daemon
would send its reader to install something they already have.
"""

CREDENTIAL_EXPORTED_ONLY: Final = (
    "WARNING: {names} is exported in this shell but not set in {secrets}, and only that "
    "file's values cross into the container — the agent will start without a credential"
)
"""Said, not refused. The one place F3 issue 09's two-channel finding reaches a dispatch.

The pin sources its `.env` into its own environment before asking, so "in the file" and
"exported" are one question there and `Credential.present` is the union it was testing — which
is what this preflight refuses on, deliberately and port-faithfully. Bessemer's container gets
the file's names and nothing else, so the exported-only machine is one the pin would run and
whose agent cannot authenticate. Refusing it here would be a second divergence for a case
`doctor` already FAILs by name; saying it loudly on the console and in the run log is the one
that costs nothing and hides nothing.
"""

INFLIGHT_LOCK: Final = (
    "another bessemer run (pid {pid}) is already working '{branch}' — wait for it or kill it"
)
INFLIGHT_CONTAINER: Final = (
    "{container} is already running — wait for it or 'docker rm -f {container}' first"
)
"""The in-flight guard's two layers, ported (`run.sh:1014`, `:1018`) — wording unchanged by
ADR 0004, only what decides between them did.

A pid lock catches a live dispatch between docker calls — mid-pass, where no container
question would find it — and the second layer is the same lock read fresh a moment later, for
the narrow window between that read and this one. A container merely answering `Up` no longer
reaches this message on its own: `gc.classify_liveness` (issue 13a) says whose question this
is — the dispatcher's, never the container's — and `Up` with no live lock is `ORPHAN`,
reclaimed below rather than refused.
"""

UNVERIFIABLE_LOCK: Final = (
    "cannot tell whether '{branch}' has a live dispatcher — the lock ({lock}) could not be "
    "read; refusing rather than guessing"
)
"""ADR 0004's third answer. Nothing can be said about who owns the slug, and for a dispatch
the do-nothing direction is refusal — the opposite of `gc`'s, which keeps the artifact instead
of listing it. Both are "do nothing", pointed at what each command is doing."""

LEDGER_BASE: Final = (
    "bessemer: --base omitted — using '{branch}' branch's last recorded base "
    "from runs.jsonl: {base}"
)
NO_LEDGER_BASE: Final = (
    "bessemer: --base omitted — no ledger record for '{branch}', defaulting to {base}"
)
BASE_UNRESOLVED: Final = "could not determine the base ref: {reason}"
"""The base chain's two log lines (`run.sh:883`, `:886`), with the product token renamed, and
the refusal for a chain that ran out.

The first two are the mitigation the chain's named consequence rests on (F3 decision 4): a
`BESSEMER_BASE` in the environment silently loses to the branch's recorded history, so the
choice is printed on every run rather than inferred from a file nobody opened.
"""

ENTRY: Final = "bessemer: '{spec}' on branch '{branch}' ({mode})  |  base {base} @ {sha}"
FINISHED: Final = "bessemer: run finished"
"""The two console lines that bracket a run (`run.sh:1730`, `:1732`). Not log lines: one is
printed before the log exists and the other after the last thing written to it.

One divergence, in `{sha}`: the pin abbreviates it with a second `git rev-parse --short`
(`:1730`), and this truncates the sha it already has to `SHORT_SHA`. A spawn for a display
string is a spawn that can fail, and the number it prints is longer rather than different.
"""

RUN_START: Final = "{stamp} == bessemer [{slug}] run start: mode={mode} boundary={boundary}"
SAID: Final = "{stamp} [{slug}] {message}"
LOGGED: Final = "{stamp} == bessemer [{slug}] {message}"
BANNER_RULE: Final = "=" * 80
BANNER: Final = "== bessemer [{slug}] {title}  ({stamp})"
STEP: Final = "({number}/{total}) {label}"
STEP_DONE: Final = "    ... {label} ({seconds}s)"
"""The run log's layout, ported line for line (`run.sh:1038–1052`, `:1156–1157`).

The contract with `status` and `gc` is pinned by the round-trip test rather than by where this
lives (ADR 0003's deliberate non-seams): what they read is the file's existence and its mtime,
and what a human reads is this.
"""

SPEC_LINE: Final = (
    "spec {spec}  |  branch {branch} ({mode})  |  base {base}  |  boundary {short}"
)
LOG_LINE: Final = "log: {log}  (tail -f it for raw output)"
RECLAIM_LINE: Final = (
    "'{branch}' is an orphan: its dispatcher is gone — reclaiming container {container} and "
    "checkout {checkout} before this run starts"
)
"""ADR 0004's one new operator sentence: an unattended dispatch that reclaims an orphan during
its guard sequence says so here, naming what it reclaimed. It is exactly the event an operator
should find afterwards, and nothing else in the log would explain why a container vanished."""
SHORT_SHA: Final = 10
"""How much of a commit the human-facing lines show (`${MERGE_BASE:0:10}`, `run.sh:1192`)."""

STEP_CHECKOUT: Final = "checkout: clone of '{branch}' @ {short}"
STEP_CONTAINER: Final = "container: {container} ({image})"
STEP_SETUP: Final = "setup: the adapter's hook"
STEP_IMPLEMENT: Final = "implement: claude pass"
STEP_REVIEW: Final = "review: up to {rounds} round(s)"
STEP_LAND: Final = "land: push + draft PR"
"""The six step labels (`run.sh:1203`, `:1248`, `:1272`, `:1438`, `:1496`, `:1537`).

Five are the pin's words. The setup one is not: upstream's "settings templates + postgres +
client deps" names one adapter's three jobs, and **core runs exactly one setup command, the
hook** (F3 decision 5, confirmed alongside) — a step label that promised postgres would be
wrong on every adopter, this repo included.
"""

DONE_CHECKOUT: Final = "checkout ready"
DONE_CONTAINER: Final = "container up"
DONE_SETUP: Final = "setup done"
DONE_IMPLEMENT: Final = "implement done"
DONE_REVIEW: Final = "review done"
DONE_LANDED: Final = "landed"
DONE_UPDATED: Final = "landed (existing PR updated)"
DONE_NOTHING: Final = "nothing to land"

DONE_LINE: Final = "DONE — {new} new commit(s) — draft PR: {url}"
NOTHING_LINE: Final = "DONE — no commits past the boundary (see {log})"

IMPLEMENT_FAILED: Final = "implement pass failed after {attempts} attempt(s) — aborting the run"
REVIEW_FAILED: Final = (
    "review pass failed in round {round} after {attempts} attempt(s) — aborting the run"
)
DESCRIPTION_FAILED: Final = "PR description pass failed — landing with the static body"
SALVAGE_REFUSED: Final = (
    "could not fast-forward '{branch}' from the checkout (agent rewrote history?) — "
    "see {checkout}"
)
CLEANUP_SALVAGE_REFUSED: Final = (
    "could not fast-forward '{branch}' from the checkout — leaving {checkout} for inspection"
)
CLEANED_UP: Final = "cleaned up (container removed; branch '{branch}' kept locally)"
FAILED_STEP: Final = "FAILED during step {number}/{total}: {label} — see {log}"
PRE_FLIGHT: Final = "pre-flight"
"""The failure and cleanup lines (`run.sh:1173`, `:1180`, `:1185`, `:1539`).

`FAILED_STEP` drops the pin's `(exit $rc)`: bash's trap reads `$?`, and the Python equivalent
is the exception already being unwound — whose own text is the line above this one in the same
log. A constant `1` in its place would be decoration.
"""

REMOVE_FAILED: Final = "could not remove container {container}: {detail}"
LEDGER_FAILED: Final = "ledger not written: {reason}"
CLEANUP_BANNER: Final = "cleanup"

INTERRUPTED: Final = "interrupted by {name}"
"""What a `SIGINT` or `SIGTERM` becomes. Upstream routes them through `exit 130` into the same
`EXIT` trap (`run.sh:1190`); here they raise, so one `finally` serves every exit path."""

SPEC_HEADLINE: Final = "Your task spec is the file {mount} — read it first."
BOUNDARY_LINE: Final = (
    "You are on branch {branch}. The task's diff boundary (its merge-base with the PR base) "
    "is commit {boundary}."
)
EARLIER_COMMITS: Final = (
    "The branch already contains commits from earlier runs of this task — read them "
    "(git log {boundary}..HEAD) and build on them; do not redo or revert finished work."
)
DESCRIBE_LINE: Final = "Describe the work on this branch: every commit after {boundary}."
"""The prompt lines this module generates, ported (`run.sh:1477–1481`, `:1555`).

Literals, and pinned as literals: a prompt is a control, and a reworded control is a changed
control (F3 decision 7.2's rule, applied to the generated half).
"""

OSASCRIPT: Final = "osascript"
NOTIFY_SCRIPT: Final = (
    "on run argv\n"
    "      display notification (item 2 of argv) with title (item 1 of argv)\n"
    "    end run"
)
NOTIFY_TITLE: Final = "bessemer: {branch}"
NOTIFY_FAILED: Final = "failed: {label} — see log"
NOTIFY_PR: Final = "{token} — PR: {url}"
NOTIFY_NOTHING: Final = "{token} — no commits to land"
NOTIFY_TIMEOUT_SECONDS: Final = 15.0
"""One end-of-run notification, unconditional, plus one on a hard failure (F3 README decision
1: F3 fires one end-of-run notification and has no verbosity key — `off|end|steps` is F4's).

**Title and body are argv arguments, never interpolated into the script text** (pin
`:1063–1073`): AppleScript is a language, and a branch name carrying a quote is a branch name
that would otherwise end the string it was written into. `on run argv` is what makes them
arguments.

Nothing about it can change a run's outcome: an absent `osascript` skips silently, the
returncode is not read, and both exceptions `proc.run` lets through are absorbed — the pin's
`|| true`, kept as the behaviour rather than as the spelling. `(exit $rc)` is dropped from
`NOTIFY_FAILED` for `FAILED_STEP`'s reason.

**Its reader is `proc.Destination.AGENT_VISIBLE`'s, and the stderr invariant is discharged
structurally rather than by a call.** F3 decision 6.2 names the failure notification as the
quotability policy's highest-risk call site; what landed is a notification composed from a step
label and a verdict token, neither of which came from a `proc.Result` — so there is nothing for
`proc.quote` to be asked about, which is `landing.body`'s answer to the same rule one module
along. A future body that wants to name *why* a run failed quotes the `Result` for this
destination; it may not read `stderr` off one. `QuotabilityTest` holds the absence either way.
"""

GIT: Final = "git"
DOCKER: Final = container.DOCKER
GH: Final = "gh"
"""The three programs this module names. `DOCKER` is `container`'s, so the module that owns the
privilege surface owns the spelling of the binary too."""

TIMEOUT_SECONDS: Final = 15.0
"""For the git questions: `show-ref`, `symbolic-ref`, `rev-parse`, `merge-base`, `remote`."""

FETCH_TIMEOUT_SECONDS: Final = 600.0
"""For `git fetch origin`, which crosses the network. `checkout.TRANSFER_TIMEOUT_SECONDS`'s
value for its reason: a slow fetch is not a hung one."""

INSPECT_TIMEOUT_SECONDS: Final = 15.0
PS_TIMEOUT_SECONDS: Final = 15.0
"""For the two docker questions this module asks: does the image exist, and is a container of
this name already up.

The same number as `TIMEOUT_SECONDS` and deliberately not the same constant. `docker` and
`git` fail in different ways and are tuned for different reasons — `bessemer.doctor` and
`bessemer.cli` already carry a docker deadline apiece for that reason — and a wedged daemon
answering neither is exactly what these two are backstops against.
"""

_SLUG_SEPARATOR: Final = re.compile(r"[^a-z0-9]+")
"""Every run of characters a slug may not contain, collapsed to one `-` — pin `:1134`'s
`tr -cs 'a-z0-9\\n' '-'`, which squeezes as it translates."""


class Refusal(Exception):
    """A guard said no. **Nothing has been touched**, and nothing needs undoing.

    Every one of these is raised before the lock is written and before the `try` that arms
    cleanup, which is what makes "a refused dispatch leaves a live run alone" a property of the
    control flow rather than a rule each guard remembers. The message is the pin's sentence;
    `bessemer.cli` prints it under `REFUSAL_PREFIX`.
    """


class RunFailed(Exception):
    """The run started and could not finish. Cleanup has run, or is running.

    Distinct from `Refusal` because the two say different things to their reader and leave the
    machine in different states: this one has a log with the reason in it, a salvaged branch,
    and a container that has been removed.
    """


class Interrupted(RunFailed):
    """A `SIGINT` or `SIGTERM` arrived. Raised by the handler so the `finally` runs."""


@dataclass(frozen=True)
class RunOutcome:
    """What a completed run came to. Only ever returned by a run that reached its ledger line.

    A failure is an exception rather than a field here: a caller holding a `RunOutcome` has one
    whose `pull_request` and `verdict` are real, and there is no flag to forget to check —
    `proc.Result`'s opposite decision, for the opposite reason. `Result` describes a child whose
    failure is ordinary data; a dispatch that failed has already unwound a container.
    """

    branch: str
    slug: str
    """The branch-derived key naming this run's container, checkout, lock and log."""

    log: Path
    base: str
    boundary: str
    """The merge-base with `base`: the diff boundary every pass and the landing gate use."""

    verdict: passes.Verdict
    pull_request: landing.Landing
    ledger_error: Unresolved | None
    """Why the ledger line was not written, when it was not.

    Not a failure: the append is the run's last act and happens *after* the pull request
    exists, so a read-only mount may not undo a landing (`ledger.append_ledger`'s contract).

    **Already said, and carried anyway.** `dispatch` puts it on the console and in the run log
    before returning, so no caller has to say it a second time; what the field is for is the
    caller that has to *decide* about it — F4's feature loop, which appends per issue and needs
    to know a ledger stopped answering — and the test that asserts a run landed cleanly.
    """


def slug_for(branch: str) -> str:
    """The branch, as the key that names its container, checkout, lock and log (pin `:1134`).

    Pure, and the whole of upstream's pipeline: lowercase, every run of characters outside
    `[a-z0-9]` collapsed to a single `-`, then one leading and one trailing `-` removed. The
    collapse is `tr -cs`'s squeeze — `feature//x` is `feature-x` and not `feature--x` — and the
    strip is `sed 's/^-//;s/-$//'`, which after a squeeze can only ever have one to remove.

    A branch of nothing but separators slugs to the empty string, upstream's answer too. It is
    not defended against here because git has no such branch name to give: `refs/heads/-` is
    refused by `git check-ref-format`, and by the time this is called the branch has been
    proven to exist.
    """
    return _SLUG_SEPARATOR.sub("-", branch.lower()).strip("-")


def notify_argv(*, title: str, body: str) -> list[str]:
    """`osascript -e '<script>' <title> <body>`. Pure; the whole argv.

    The two variable parts are **arguments to the script**, which is the point: `NOTIFY_SCRIPT`
    reads them out of `argv`, and no amount of quoting in a branch name or a verdict token can
    become part of the program (pin `:1063`, ADR 0001's argv rule one language further in).
    """
    return [OSASCRIPT, "-e", NOTIFY_SCRIPT, title, body]


def implement_prompt(template: str, *, branch: str, boundary: str, previous_tip: str) -> str:
    """The implement template plus the lines this run generates (`run.sh:1476–1482`).

    Composed the way the pin composes it — `"$(cat …)\\n<line>"`, whose command substitution has
    already dropped the file's trailing newlines — so the assembled bytes are the pin's.
    `passes.review_prompt` is the same composition for the review template, and the two are
    apart because that one has a second consumer inside the review loop.

    The third line appears only when the branch already stands past the boundary, which is the
    continue-mode workflow's second and later runs (`:1479`).

    **The `{mount}` sentence is deliberately kept, duplicating the template's own SPEC
    heading.** Flagged as inherited duplication by issue 03's implementer and ruled here:
    keeping it is what makes an assembled F3 prompt byte-identical to the pin's once hae's
    overrides restore its template text at F7, which is the whole parity story for prompts (F3
    decision 7.1). Dropping the repetition would be a prompt change dressed as tidying.
    """
    prompt = template.rstrip("\n") + "\n" + SPEC_HEADLINE.format(mount=container.SPEC_MOUNT)
    prompt += "\n" + BOUNDARY_LINE.format(branch=branch, boundary=boundary)
    if previous_tip != boundary:
        prompt += "\n" + EARLIER_COMMITS.format(boundary=boundary)
    return prompt


def description_prompt(template: str, *, boundary: str) -> str:
    """The pull-request template plus the one line naming this run's diff boundary (`:1554`)."""
    return template.rstrip("\n") + "\n" + DESCRIBE_LINE.format(boundary=boundary)


def _notify(*, title: str, body: str, run: proc.Runner) -> None:
    """Fire one desktop notification, best effort. Cannot fail a run, and cannot be seen to.

    See `NOTIFY_SCRIPT` for the three guards and why each is the pin's.
    """
    if shutil.which(OSASCRIPT) is None:
        return
    try:
        run(notify_argv(title=title, body=body), timeout=NOTIFY_TIMEOUT_SECONDS)
    except OSError, proc.TimeoutExpired:
        pass


@dataclass
class _RunLog:
    """say / banner / step — the run's two output channels, and the step counter between them.

    An internal class passed to collaborators rather than a module of its own, and that is a
    recorded decision (ADR 0003's deliberate non-seams): one consumer, no variation until F4's
    verbosity config, and its tests ride dispatch's. What is *not* internal is the file it
    writes and where — see `LOGS_DIR`.

    `say` goes to both channels; `emit` goes to the log alone and is what `passes.run_pass`
    renders a transcript through. Opened per append rather than held open: a handle living
    across a `try/finally` that is already unwinding a container is one more thing to close
    correctly, and the pin's `>>"$log"` is exactly an open-append-close.
    """

    path: Path
    slug: str
    console: Callable[[str], None]
    now: Callable[[], datetime]
    clock: Callable[[], float]
    number: int = 0
    label: str = ""
    started: float = 0.0

    def _append(self, text: str) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(text)

    def emit(self, line: str) -> None:
        """One raw line into the run log. The channel a pass's transcript arrives on."""
        self._append(f"{line}\n")

    def say(self, message: str) -> None:
        """One status line, on the console and in the log, timestamped and slug-prefixed."""
        stamp = self.now().strftime("%H:%M:%S")
        self.console(SAID.format(stamp=stamp, slug=self.slug, message=message))
        self._append(LOGGED.format(stamp=stamp, slug=self.slug, message=message) + "\n")

    def banner(self, title: str) -> None:
        """A log-only rule introducing a step's raw output."""
        stamp = self.now().strftime("%H:%M:%S")
        self._append(
            "\n"
            + BANNER_RULE
            + "\n"
            + BANNER.format(slug=self.slug, title=title, stamp=stamp)
            + "\n"
            + BANNER_RULE
            + "\n"
        )

    def step(self, label: str) -> None:
        """Advance the numbered step counter and announce it on both channels."""
        self.number += 1
        self.label = label
        self.started = self.clock()
        numbered = STEP.format(number=self.number, total=TOTAL_STEPS, label=label)
        self.say(numbered)
        self.banner(numbered)

    def step_done(self, label: str) -> None:
        """Close the current step, with how long it took."""
        self.say(STEP_DONE.format(label=label, seconds=int(self.clock() - self.started)))


def _rotate(log: Path) -> None:
    """`log` → `log.1`, and only when there is something in it (pin `:1155`).

    Rotate rather than truncate: the continue-mode workflow re-dispatches the same branch, and
    truncating would lose the previous run's evidence every time. Single generation, because
    the branch and its pull request are the durable history and this is last-run debugging.

    An empty or absent log is left alone — `[ -s "$log" ]` — so a first run does not create a
    `.log.1` of nothing.
    """
    try:
        if log.stat().st_size == 0:
            return
    except FileNotFoundError:
        return
    log.replace(log.with_suffix(ROTATED_SUFFIX))


def _lock_pid(path: Path) -> str:
    """What the lock file says, or the empty string. Never raises — a lock that cannot be read
    is a lock whose owner cannot be named, which is what `?` in the refusal is for."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError, UnicodeDecodeError:
        return ""


def _lock_pid_alive(path: Path) -> bool | None:
    """The in-flight guard's own read of `classify_liveness`'s lock signal (ADR 0004):
    `True`/`False` once the file is read (no file at all is `False`, the ordinary shape of an
    orphan), `None` when it exists but could not be read. Read fresh here rather than through
    `gc._lock_pid_alive` — that one is `gc`'s own, its module docstring says so — the shared
    thing is `classify_liveness` itself, never the reading that feeds it."""
    try:
        pid_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except OSError, UnicodeDecodeError:
        return None
    return status.pid_alive(pid_text)


@dataclass
class _Lock:
    """The run's pid file, created atomically. The fix for the pin's guard-then-write window.

    Upstream checks the lock at `:1148` and writes it at `:1150`; two dispatches of one branch
    can both pass that check and both proceed, each believing it is alone. `O_CREAT | O_EXCL` is
    one syscall that both tests and takes, so the loser learns it lost — and re-reads the file
    to name the pid that won (F3 decision 6.3, recorded divergence).

    The guard above this is not made redundant by it, and this does not make the guard's own
    refusal redundant either: the guard is where an operator gets told about a run that is
    plainly still going, before anything has been asked of docker. This is the last word, for
    the case the guard cannot see — the other dispatch passed the same guard a microsecond ago.

    **A lock with no live owner is taken over rather than refused.** That is upstream's
    behaviour too (its `echo $$ > "$lock"` at `:1150` overwrites unconditionally), and it is
    what keeps a machine that lost power mid-run from needing a file deleted by hand. Liveness
    is `status.pid_alive` — one definition, shared with `gc`'s lock class.

    **No live owner includes no owner at all**: a zero-byte lock, or one holding something that
    is not a pid, is what a run killed between the `open` and the `write` leaves behind, and
    refusing on it would make this class demand by hand exactly the cleanup it exists to spare.
    What still refuses is a path that cannot be opened for writing — a directory in the lock's
    place — because then nothing can be said about who holds it and nothing can take it either.
    """

    path: Path
    pid: int

    def acquire(self, branch: str) -> None:
        """Take the lock, or refuse naming the pid that holds it."""
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            holder = _lock_pid(self.path)
            if holder and status.pid_alive(holder):
                raise Refusal(INFLIGHT_LOCK.format(pid=holder, branch=branch)) from None
            try:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_TRUNC)
            except OSError:
                raise Refusal(INFLIGHT_LOCK.format(pid=holder or "?", branch=branch)) from None
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"{self.pid}\n")

    def release(self) -> None:
        """Remove the lock. A lock already gone is not an error — the `finally` cannot know how
        far the run got, and the pin's `rm -f` tolerates it too."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def raising_signals() -> Iterator[None]:
    """`SIGINT` and `SIGTERM` raise `Interrupted` while this is entered, then are restored.

    Upstream's `trap 'exit 130' INT TERM` (`:1190`) exists to route a signal death *through*
    the `EXIT` trap, because a bare one would skip cleanup and leave a container, a checkout
    and a lock behind. Raising is Python's spelling of that routing: the exception unwinds into
    the `finally` that is already there, so there is one cleanup path and not two.

    Restored on the way out, and restored to whatever was installed rather than to the default:
    a caller that had its own handler — a test runner, or F4's feature loop around several
    dispatches — must get it back.

    Public because it is what a test drives to prove the conversion happens at all; a signal
    delivered to a private name is a test asserting about an implementation detail of the
    thing it is trying to prove.
    """
    installed: list[tuple[int, signal._HANDLER]] = []

    def handler(number: int, frame: types.FrameType | None) -> None:
        raise Interrupted(INTERRUPTED.format(name=signal.Signals(number).name))

    for number in (signal.SIGINT, signal.SIGTERM):
        try:
            installed.append((number, signal.signal(number, handler)))
        except ValueError:
            # `signal.signal` is main-thread-only. A dispatch driven from another thread keeps
            # whatever that thread inherits, which is the interpreter's own handling — and the
            # `finally` still runs there, because that is what raises there too.
            pass
    try:
        yield
    finally:
        for restored, previous in installed:
            signal.signal(restored, previous)


def _git(
    arguments: Sequence[str],
    *,
    repo_root: Path,
    run: proc.Runner,
    timeout: float = TIMEOUT_SECONDS,
) -> proc.Result:
    """One git question, asked **from the main repository** and never from inside a checkout.

    `env` is `resolve.git_env()` for that module's reason: an ambient `GIT_DIR` would send every
    one of these — the `fetch` that writes included — at whatever repository it names rather
    than at the one bessemer is dispatching from.

    Returns whatever happened; the caller decides what a nonzero exit means, because the
    callers mean different things by it.
    """
    return run([GIT, *arguments], timeout=timeout, cwd=repo_root, env=resolve.git_env())


def _checked_git(
    arguments: Sequence[str],
    *,
    repo_root: Path,
    run: proc.Runner,
    timeout: float = TIMEOUT_SECONDS,
) -> str:
    """`_git`, and its stdout, or `proc.ProcessError`. For the questions with no other answer.

    A `rev-parse` that cannot resolve the base, a `merge-base` with nothing in common, a
    `remote get-url` with no origin: each is a run that cannot start, and none has a sensible
    substitute value. Raised rather than refused because they are reached after the guards, and
    a guard is where a message is written for a human to act on.
    """
    result = _git(arguments, repo_root=repo_root, run=run, timeout=timeout)
    if not result.ok:
        raise proc.ProcessError(result)
    return result.stdout.strip()


def base_ref(
    *,
    flag: str | None,
    branch: str,
    cfg: Config,
    start: Path | None,
    specs_dir: Path,
    console: Callable[[str], None],
) -> str:
    """The base ref, by the port-faithful chain (F3 decision 4).

    `--base` flag > the branch's **last recorded base in the ledger** > `BESSEMER_BASE` > local
    config > committed config > `origin/HEAD` auto-detect. The last four are
    `resolve.resolve_base`'s own precedence, consulted as one step and **lazily** — a machine
    whose git is broken still dispatches on a flag or on a ledger record, and the auto-detect
    spawn never happens on the two shallower depths.

    **Named consequence, accepted and printed rather than hidden:** `BESSEMER_BASE=… bessemer
    run …` loses to the branch's recorded history, because only the flag sets upstream's
    `BASE_EXPLICIT` (`:563`) while the environment merely seeds the default (`:526`). The
    ledger line is logged on every run without a flag, which is what makes the choice visible.
    Defect 10 — file order standing in for timestamp order in "last" — is inherited, not fixed
    (F2 decision 8).

    Public, and one of three functions here that are: it is the only piece of the chain a
    tier-1 test can reach all three depths of without a git work tree behind it.
    """
    if flag:
        return flag

    recorded = ledger.last_base_for_branch(ledger.central_ledger_path(specs_dir), branch)
    if recorded:
        console(LEDGER_BASE.format(branch=branch, base=recorded))
        return recorded

    match resolve.resolve_base(cfg, start=start):
        case Resolved(value=configured):
            console(NO_LEDGER_BASE.format(branch=branch, base=configured))
            return configured
        case Unresolved(reason=reason):
            raise Refusal(BASE_UNRESOLVED.format(reason=reason))


def _preflight(
    *,
    cfg: Config,
    adapter_dir: Path,
    env: Mapping[str, str],
    run: proc.Runner,
    console: Callable[[str], None],
) -> str:
    """docker, gh, the credential, the image — and the image's name back (`run.sh:942–952`).

    Four questions and one warning, in the pin's order, all before any directory is created.
    The credential one is `container.credential_presence`, the **shared** resolver doctor
    renders and this refuses on (F3 decision 8.5, the pin's own `have_claude_credential`
    discipline at `:346`) — not a second copy of the rule. The image question is
    `doctor.IMAGE_INSPECT_ARGV`, for the same reason one layer down: the pin's own preflight
    command, with one spelling in this package.

    `image_staleness` is deliberately absent: it is F5's, its inputs are one adapter's file
    names, and a stale image still runs.
    """
    if shutil.which(DOCKER) is None:
        raise Refusal(DOCKER_MISSING)
    if shutil.which(GH) is None:
        raise Refusal(GH_MISSING)

    secrets = adapter_dir / container.SECRETS_FILE
    credential = container.credential_presence(adapter_dir=adapter_dir, env=env)
    if not credential.present:
        raise Refusal(CREDENTIAL_MISSING.format(secrets=secrets))
    if not credential.crosses:
        console(
            REFUSAL_PREFIX
            + CREDENTIAL_EXPORTED_ONLY.format(
                names=", ".join(credential.exported), secrets=secrets
            )
        )

    image = cfg.get("image")
    if image is None:
        raise Refusal(
            IMAGE_UNCONFIGURED.format(file=config.COMMITTED_FILE, adapter=config.ADAPTER_DIR)
        )
    if not isinstance(image, str) or not image:
        raise Refusal(IMAGE_NOT_A_NAME.format(image=image))
    if not run((*doctor.IMAGE_INSPECT_ARGV, image), timeout=INSPECT_TIMEOUT_SECONDS).ok:
        raise Refusal(IMAGE_MISSING.format(image=image, adapter=config.ADAPTER_DIR))
    return image


def dispatch(
    *,
    cfg: Config,
    repo_root: Path,
    start: Path | None,
    spec: str,
    branch: str,
    base: str | None,
    env: Mapping[str, str],
    pid: int,
    console: Callable[[str], None],
    run: proc.Runner,
    streamer: proc.Streamer,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    now: Callable[[], datetime],
) -> RunOutcome:
    """Run one spec on one branch, and record it. The whole of a one-off dispatch.

    Every parameter is a seam or a fact, and none has a default (ADR 0003, F2 decision 9): the
    two proc seams, the two clocks and `sleep` so that a tier-2 test drives a whole dispatch
    with no daemon and no wall clock; `repo_root`, `env` and `pid` because they are questions
    about the machine that a library is told rather than goes and asks.

    `repo_root` is the **agreed** root — `resolve.resolve_root_agreement`'s answer, refused on
    by `bessemer.cli` before this is called, for the reason `cfg` is: that resolver spawns git
    outside the seam below, so making it this function's own call would put a real git
    repository behind every orchestration test in exchange for a refusal the caller can already
    print. Everything past it runs on the seam.

    Raises `Refusal` for a guard and `RunFailed` for a run that started and could not finish;
    returns a `RunOutcome` only for one that landed and recorded.
    """
    adapter_dir = cfg.adapter_dir
    specs_setting = cfg.get("specs_dir")
    if not isinstance(specs_setting, str):
        raise Refusal(f"specs_dir is configured to {specs_setting!r}, which is not a string")
    specs_dir = cfg.root / specs_setting

    # --- Guards. Nothing on disk is touched until every one of them has passed. ------------
    spec_path = issues.spec_check_path(specs_dir, spec)
    if not spec_path.is_file():
        raise Refusal(SPEC_NOT_FOUND.format(spec=spec_path))

    chosen_base = base_ref(
        flag=base, branch=branch, cfg=cfg, start=start, specs_dir=specs_dir, console=console
    )

    exists = _git(
        ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        repo_root=repo_root,
        run=run,
    )
    if not exists.ok:
        raise Refusal(BRANCH_MISSING.format(branch=branch))
    if resume.is_protected(branch):
        raise Refusal(BRANCH_PROTECTED.format(branch=branch))
    if branch == chosen_base.removeprefix(f"{resolve.ORIGIN}/"):
        raise Refusal(BASE_IS_BRANCH)
    current = _git(["symbolic-ref", "--quiet", "--short", "HEAD"], repo_root=repo_root, run=run)
    if current.ok and current.stdout.strip() == branch:
        raise Refusal(BRANCH_CHECKED_OUT.format(branch=branch))

    image = _preflight(cfg=cfg, adapter_dir=adapter_dir, env=env, run=run, console=console)

    # Both parameter objects are constructed here, before any directory exists: a
    # `max_review_rounds` of `"three"` or a malformed `container_cap_add` must fail now and not
    # twenty minutes into a container (`passes.Limits`, `container.Boundary`).
    limits = passes.Limits(
        pass_timeout=cast(int | str, cfg.get("pass_timeout")),
        max_review_rounds=cast(int | str, cfg.get("max_review_rounds")),
    )
    privilege = container.Boundary(
        cap_add=cast(Sequence[str], cfg.get("container_cap_add")),
        volumes=cast(Sequence[str], cfg.get("container_volumes")),
        pids_limit=cast(int | str, cfg.get("pids_limit")),
        memory=cast(str, cfg.get("memory")),
        env=container.forwarding(
            adapter_dir=adapter_dir,
            declared=cast(Sequence[str], cfg.get("container_env_keys")),
        ),
    )
    withheld = privilege.env.warning(destination=proc.Destination.HOST_LOG)
    if withheld is not None:
        # Names only, never a value, and never into the container, the pull request or the
        # notification — ADR 0001's rule, discharged by `Forwarding.warning` refusing to
        # compose one for any other destination.
        console(REFUSAL_PREFIX + withheld)

    logs_dir = adapter_dir / LOGS_DIR
    locks_dir = adapter_dir / LOCKS_DIR
    checkouts_dir = adapter_dir / CHECKOUTS_DIR
    for directory in (logs_dir, checkouts_dir, locks_dir):
        directory.mkdir(parents=True, exist_ok=True)

    # A stale `origin/*` is a wrong diff boundary, which is a review scoped to the wrong
    # commits and a pull request that says so (`run.sh:957`).
    _git(
        ["fetch", "origin", "--quiet"],
        repo_root=repo_root,
        run=run,
        timeout=FETCH_TIMEOUT_SECONDS,
    )
    base_sha = _checked_git(
        ["rev-parse", "--verify", f"{chosen_base}^{{commit}}"], repo_root=repo_root, run=run
    )
    remote_url = _checked_git(["remote", "get-url", "origin"], repo_root=repo_root, run=run)
    boundary = _checked_git(
        ["merge-base", base_sha, f"refs/heads/{branch}"], repo_root=repo_root, run=run
    )

    slug = slug_for(branch)
    name = f"{CONTAINER_PREFIX}{slug}"
    log_path = logs_dir / f"{slug}{LOG_SUFFIX}"
    lock = _Lock(path=locks_dir / f"{slug}{LOCK_SUFFIX}", pid=pid)
    work = checkouts_dir / slug

    # The in-flight guard, both layers (`run.sh:1009–1021`), reading issue 13a's classification
    # rather than asking docker its own question (ADR 0004). A lock whose pid is gone is not
    # refused here — `_Lock.acquire` takes it over — because only a reader can tell a live run
    # from a crashed one, and a crashed one must not need a file deleted by hand.
    recorded_pid = _lock_pid(lock.path)
    if recorded_pid and status.pid_alive(recorded_pid):
        raise Refusal(INFLIGHT_LOCK.format(pid=recorded_pid, branch=branch))
    probed = run(passes.liveness_argv(container=name), timeout=PS_TIMEOUT_SECONDS)
    container_status = "Up" if probed.stdout.strip() else None
    classification = gc.classify_liveness(
        container_status=container_status, lock_pid_alive=_lock_pid_alive(lock.path)
    )
    if classification.liveness is gc.Liveness.IN_FLIGHT:
        raise Refusal(INFLIGHT_CONTAINER.format(container=name))
    if classification.liveness is gc.Liveness.UNVERIFIABLE:
        raise Refusal(UNVERIFIABLE_LOCK.format(branch=branch, lock=lock.path))
    # ORPHAN: proceed rather than refuse — this is the wedge ADR 0004 closes. The stale
    # cleanup a few lines below (inside the `try`) reclaims it; `reclaiming` is whether there
    # is anything there to say so about, which `classification.liveness` alone cannot answer —
    # an ordinary first dispatch of a branch classifies the same way.
    reclaiming = lock.path.exists() or container_status == "Up" or work.exists()

    # --- Past here the tree is written to, so every exit path runs the cleanup below. ------
    console(
        ENTRY.format(
            spec=spec_path, branch=branch, mode=MODE, base=chosen_base, sha=base_sha[:SHORT_SHA]
        )
    )
    lock.acquire(branch)
    _rotate(log_path)
    log_path.write_text(
        RUN_START.format(
            stamp=now().isoformat(timespec="seconds"), slug=slug, mode=MODE, boundary=boundary
        )
        + "\n",
        encoding="utf-8",
    )
    runlog = _RunLog(path=log_path, slug=slug, console=console, now=now, clock=clock)

    try:
        with raising_signals():
            # An orphan from a previous run of this branch that ended badly (`:1161–1162`,
            # ADR 0004). Inside the `try` rather than before it, where the pin has it: bash
            # arms its trap last because `cleanup` reads variables that are not set until then,
            # and Python has no such constraint — so a removal that fails releases the lock it
            # just took instead of leaking it.
            if reclaiming:
                runlog.say(RECLAIM_LINE.format(branch=branch, container=name, checkout=work))
            container.remove(container=name, run=run)
            checkout.remove(work)

            runlog.say(
                SPEC_LINE.format(
                    spec=spec_path,
                    branch=branch,
                    mode=MODE,
                    base=chosen_base,
                    short=boundary[:SHORT_SHA],
                )
            )
            runlog.say(LOG_LINE.format(log=log_path))
            previous_tip = _checked_git(
                ["rev-parse", f"refs/heads/{branch}"], repo_root=repo_root, run=run
            )

            runlog.step(STEP_CHECKOUT.format(branch=branch, short=previous_tip[:SHORT_SHA]))
            checkout.create(
                repo_root=repo_root,
                checkout=work,
                branch=branch,
                remote_url=remote_url,
                run=run,
            )
            runlog.step_done(DONE_CHECKOUT)

            runlog.step(STEP_CONTAINER.format(container=name, image=image))
            container.start(
                container=name,
                image=image,
                checkout=work.resolve(),
                adapter_dir=adapter_dir.resolve(),
                spec=spec_path.resolve(),
                boundary=privilege,
                run=run,
            )
            runlog.step_done(DONE_CONTAINER)

            runlog.step(STEP_SETUP)
            try:
                hook = container.run_setup_hook(container=name, boundary=privilege, run=run)
            except container.SetupHookError as error:
                # ADR 0001's contract: a nonzero hook aborts the run and the log is surfaced.
                # The hook's own output is what its author needs, and the exception's message
                # is already composed for `HOST_LOG`, so both go into the log and neither goes
                # anywhere else.
                runlog.emit(error.result.stdout)
                runlog.say(REFUSAL_PREFIX + str(error))
                raise RunFailed(str(error)) from error
            runlog.emit(hook.stdout)
            runlog.step_done(DONE_SETUP)

            def one_pass(prompt: str) -> passes.PassResult:
                """One agent pass, on this run's container and this run's log.

                A closure rather than eight keywords written twice: `container`, `limits`, both
                log channels, both seams, `sleep` and `clock` are the same for every pass a run
                makes, and the only thing that differs between the two call sites is the prompt.
                Defined here rather than at module scope because every one of those is a local
                of this dispatch — a module-level helper would have to take them all back as
                parameters and would be the repetition again with a name on it.
                """
                return passes.run_pass(
                    prompt=prompt,
                    container=name,
                    limits=limits,
                    emit=runlog.emit,
                    say=runlog.say,
                    run=run,
                    streamer=streamer,
                    sleep=sleep,
                    clock=clock,
                )

            runlog.step(STEP_IMPLEMENT)
            implemented = one_pass(
                implement_prompt(
                    prompts.load(prompts.IMPLEMENT, adapter_dir=adapter_dir),
                    branch=branch,
                    boundary=boundary,
                    previous_tip=previous_tip,
                )
            )
            if not implemented.ok:
                # Covers the dead-container case too, which `run_pass` has already said in its
                # own words: either way there is no next pass to run (F3 decision 6.6).
                raise RunFailed(IMPLEMENT_FAILED.format(attempts=implemented.attempts))
            runlog.step_done(DONE_IMPLEMENT)

            runlog.step(STEP_REVIEW.format(rounds=limits.rounds))
            verdict = passes.review_loop(
                template=prompts.load(prompts.REVIEW, adapter_dir=adapter_dir),
                branch=branch,
                boundary=boundary,
                container=name,
                limits=limits,
                emit=runlog.emit,
                say=runlog.say,
                run=run,
                streamer=streamer,
                sleep=sleep,
                clock=clock,
            )
            if verdict.failure is not None:
                raise RunFailed(
                    REVIEW_FAILED.format(round=verdict.round, attempts=verdict.failure.attempts)
                )
            # Reaching the cap is **not** a failure: the run lands with the needs-work footer
            # (F3 decision 6.6), which is why nothing is raised on `verdict.approved`.
            runlog.step_done(DONE_REVIEW)

            runlog.step(STEP_LAND)
            # Salvage first: `landing` counts and pushes from the **main** repository, and the
            # agent's commits are still only in the checkout. The `finally` salvages again and
            # finds nothing to do — the idempotence the pin relies on too (`:1538`, `:1177`).
            salvaged = checkout.salvage(
                repo_root=repo_root, checkout=work, branch=branch, run=run
            )
            if not salvaged.advanced:
                runlog.emit(proc.quote(salvaged.result, destination=proc.Destination.HOST_LOG))
                raise RunFailed(SALVAGE_REFUSED.format(branch=branch, checkout=work))

            described = one_pass(
                description_prompt(
                    prompts.load(prompts.PR, adapter_dir=adapter_dir), boundary=boundary
                )
            )
            if not described.ok:
                # `landing.body` substitutes its own static sentence for empty text, so a failed
                # description pass costs the body's first paragraph and nothing else (F3
                # decision 8.2). The run still lands.
                runlog.say(DESCRIPTION_FAILED)

            pull_request = landing.land(
                repo_root=repo_root,
                branch=branch,
                base=chosen_base,
                boundary=boundary,
                previous_tip=previous_tip,
                description=described.text,
                footer=verdict.footer,
                spec=spec_path,
                emit=runlog.emit,
                run=run,
            )
            if pull_request.landed:
                runlog.step_done(DONE_UPDATED if pull_request.updated else DONE_LANDED)
                runlog.say(
                    DONE_LINE.format(new=pull_request.new_commits, url=pull_request.pr_url)
                )
            else:
                runlog.step_done(DONE_NOTHING)
                runlog.say(NOTHING_LINE.format(log=log_path))

            _notify(
                title=NOTIFY_TITLE.format(branch=branch),
                body=(
                    NOTIFY_PR.format(token=verdict.token, url=pull_request.pr_url)
                    if pull_request.landed
                    else NOTIFY_NOTHING.format(token=verdict.token)
                ),
                run=run,
            )

            # The ledger is the run's last act, and it appends **whatever landed** — a
            # zero-commit landing records an empty `pr_url` (F3 decision 8.4), and a hard
            # failure never reaches this line at all (decision 6.4). Every field is
            # `str()`-converted on the way in: defect 9 stays in `bessemer.ledger` per F2
            # decision 8, and this is the armour against it (decision 6.5).
            ledger_error = ledger.append_ledger(
                ledger.central_ledger_path(specs_dir),
                ledger.run_record(
                    branch=str(branch),
                    base=str(chosen_base),
                    spec=str(spec_path.name),
                    outcome=str(verdict.token),
                    pr_url=str(pull_request.pr_url),
                    source_dir=str(spec_path.parent),
                ),
            )
            if ledger_error is not None:
                runlog.say(LEDGER_FAILED.format(reason=ledger_error.reason))

            return RunOutcome(
                branch=branch,
                slug=slug,
                log=log_path,
                base=chosen_base,
                boundary=boundary,
                verdict=verdict,
                pull_request=pull_request,
                ledger_error=ledger_error,
            )
    except BaseException:
        # The failure line and its notification, the pin's pair at `:1172–1175`. One ordering
        # difference, and it is Python's rather than a choice: `except` runs before `finally`,
        # so the `cleanup` banner lands *below* this line where the pin prints it above.
        # `BaseException` so an `Interrupted`, and `tests/guard.py`'s own violation, are
        # reported like any other end rather than passing through unannounced.
        label = runlog.label or PRE_FLIGHT
        runlog.say(
            REFUSAL_PREFIX
            + FAILED_STEP.format(
                number=runlog.number, total=TOTAL_STEPS, label=label, log=log_path
            )
        )
        _notify(
            title=NOTIFY_TITLE.format(branch=branch),
            body=NOTIFY_FAILED.format(label=label),
            run=run,
        )
        raise
    finally:
        # Upstream's `cleanup()`, in its order (`:1169–1186`): salvage, remove the container,
        # drop the lock. Salvage is **fast-forward only**, and a refusal keeps the checkout —
        # removing it would discard work that exists nowhere else.
        runlog.banner(CLEANUP_BANNER)
        if work.is_dir():
            final = checkout.salvage(repo_root=repo_root, checkout=work, branch=branch, run=run)
            if final.advanced:
                checkout.remove(work)
            else:
                runlog.say(
                    REFUSAL_PREFIX
                    + CLEANUP_SALVAGE_REFUSED.format(branch=branch, checkout=work)
                )
        removed = container.remove(container=name, run=run)
        if not removed.ok:
            # Never fatal — the pin's `|| true` — but never silent either: a container that
            # could not be removed is exactly the leak `gc` exists to reclaim.
            runlog.say(
                REMOVE_FAILED.format(
                    container=name,
                    detail=proc.quote(removed, destination=proc.Destination.HOST_LOG),
                )
            )
        lock.release()
        runlog.say(CLEANED_UP.format(branch=branch))
