"""The agent loop: one pass with its retry ladder, and the review rounds that read a verdict.

An **implement pass** and a **review pass** (CONTEXT.md) are the same act with different
prompts: a `claude` invocation inside the run's container, its prompt on stdin, its
stream-json rendered into the run log by `bessemer.stream` as it arrives, and its final text
returned. `run_pass` is that act plus everything upstream learned about it failing;
`review_loop` is the rounds, the verdict token, and the cap.

Oracle regions: `run.sh:1090–1130` (`claude_pass`) and `:1494–1527` (the review loop).

## What the pinned literals are, and why each is one

- **The claude argv** (`CLAUDE_ARGV`) — a provider contract. A pass that lost `-p` would
  wait for a terminal that is not there; one that lost `--output-format stream-json` would
  render nothing and capture nothing.
- **`timeout` runs *inside* the container** (`pass_argv`), which is upstream's measured
  operational knowledge and the reason `proc.streamed` has no host-side deadline at all: a
  host-side kill of the `docker exec` **client** leaves the process it started running in the
  container, and every later exec into that container queues behind it. The deadline has to
  be on the far side of the boundary to end the thing that is actually running.
- **The verdict token** (`VERDICT_TOKEN`) — the same string F1's `REVIEWING.md` already
  exercises and the review prompt promises. It is matched as a **substring of the pass's
  final text**, which is `grep -q` semantics and not a parse: a reviewer that says
  `<verdict>approved</verdict>` in the middle of a paragraph has approved.
- **The two footers** — `Verdict.footer` is what lands in a pull-request body (F3 decision
  6.6), so a reworded footer is a changed contract rather than a typo.

## The cadence is owned here, and the suite must not sleep

`POLL_SECONDS`, `HEARTBEAT_SECONDS`, `ATTEMPTS` and `RETRY_SECONDS` are the pin's numbers,
owned as constants rather than configured: they are what the loop *is*, and the two knobs an
adopter has (`pass_timeout`, `max_review_rounds`) are the ones F3 decision 7.3 makes
any-layer. `clock` and `sleep` arrive as required keywords with no default, for the reason
the proc seams do — a test that forgot to pass them would really sleep thirty seconds, three
times, and the whole suite is supposed to run without a wall clock.

The heartbeat is driven by `proc.Streamer`'s `idle` callback rather than by a timer: the
streamer calls it about every `POLL_SECONDS` while the child runs, and `_Heartbeat` decides
from the injected clock whether one is due. Elapsed time rather than a tick count (upstream's
`hb % 120`) because a poll that overruns must not lose a beat, and a fake clock is then the
whole of what a test needs.

## Two failures a pass has, and the one that is not a retry

A pass "failed" when the exec exited nonzero **or** when its stream ended without a
successful result event — `stream.Capture.ok` is false there, and upstream reached the same
conclusion by a different route (its filter ran in the pipeline, so the filter's exit status
*was* the pass's). Both retry.

**A dead container is not a retry, and the check comes before every one of them** (pin
:1122–1126). Something external removed the container; retrying into it would produce three
identical failures and a run that reports the last one instead of the reason. `docker ps -q`
answering nothing is the pin's own predicate, and a `docker ps` that *failed* answers nothing
too — so a broken daemon reads as gone and aborts, which is the safe direction for a loop
whose alternative is spawning into the dark.

## What this module reports, and what it does not decide

`review_loop` returns a needs-work `Verdict` when the cap is reached; whether the run lands
anyway is issue 10's call (it does — F3 decision 6.6). A pass that failed inside a review
round stops the loop and comes back as `Verdict.failure`, because upstream's `set -euo
pipefail` ends the run there and the caller needs the `PassResult` to say so.

**Nothing here writes a file or spawns anything.** Log lines go to `emit`, operator lines go
to `say`, and both come from `dispatch.py`'s runlog (ADR 0003's deliberate non-seam). The two
are separate parameters rather than one object so that this module needs no type from a module
that does not exist yet; they are the same two destinations upstream's `say` wrote to.

**The redaction question `bessemer.stream` hands over, answered here.** That module renders
without redacting and says the decision belongs to whoever writes its lines somewhere. The
answer is: **the rendered transcript reaches `emit` verbatim**, which is the pin's own
behaviour and what the byte-parity fixtures assert; the log is host-side, on the machine whose
`.bessemer/.env` already holds the credential, and rewriting an agent's own words in the one
place a person goes to find out what it did would cost more than it buys. What *is* quoted —
the failing `Result` — goes through `proc.quote`, which redacts (`FAILED`). Agent-visible
destinations are a different question with a different answer, and it is not asked here: this
module returns the final text and composes nothing for a pull request.

**One rename against the pin, in prose only:** upstream says "aborting task"; **task is
retired** (CONTEXT.md) and the unit is a **run**, so `CONTAINER_GONE` says "aborting the run".
Legal by F2 decision 7's edge — nothing upstream asserts these strings.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Final

from bessemer import proc, stream
from bessemer.container import DOCKER
from bessemer.container import liveness_argv as _container_liveness_argv

CLAUDE_ARGV: Final = (
    "claude",
    "--dangerously-skip-permissions",
    "-p",
    "--output-format",
    "stream-json",
    "--verbose",
)
"""The provider invocation, verbatim (run.sh:1099). Written as one literal, tested as one.

`--dangerously-skip-permissions` is what makes the agent unattended, and it is why every
other thing in this package exists: the container is what the flag is safe inside (ADR 0001).
`-p` is the non-interactive mode, and the two `--output-format`/`--verbose` flags are what
produce the stream `bessemer.stream` renders.

Upstream wrapped this in `bash -c '… | python3 /agentbox/stream-filter.py'`. There is no
shell here and no pipeline: the filter moved host-side (F3 decision 5.1), which leaves the
argv a plain list — the shape ADR 0001 wanted all along.
"""

TIMEOUT_PROGRAM: Final = "timeout"
"""coreutils `timeout`, from the adapter image's contract (`container.IMAGE_CONTRACT`).

In-container, always. See the module docstring for the reason, and note the symptom if an
image lacks it: rc 127 on the first pass, which is the clause `container._hook_failure`
already names for the hook.
"""

TIMEOUT_RETURNCODE: Final = 124
"""What `timeout` exits when it killed the child. The pin's `rc -eq 124`."""

POLL_SECONDS: Final = 30.0
"""How often the streamer looks up from the pass. Upstream's `sleep 30`."""

HEARTBEAT_SECONDS: Final = 120.0
"""How often a still-running pass says so on the console. Upstream's `hb % 120`.

Two minutes because the console line exists for a person deciding whether to keep waiting,
and a pass that says nothing for twenty minutes is indistinguishable from a wedged one.
"""

ATTEMPTS: Final = 3
"""How many times a pass is tried. The pin's `for attempt in 1 2 3`.

Transient provider failures are what it is for; three is upstream's number and the cost of a
fourth is another `pass_timeout` of an operator's evening.
"""

RETRY_SECONDS: Final = 30.0
"""How long to wait before trying a failed pass again. The pin's `sleep 30`."""

LIVENESS_TIMEOUT_SECONDS: Final = 15.0
"""For the `docker ps` that asks whether the container is still there.

The same backstop `bessemer.cli` puts on its own `docker ps`, for the same reason: a wedged
daemon must not turn one failed pass into a run that never returns.
"""

PROMPT_OPEN: Final = "-- prompt --"
PROMPT_CLOSE: Final = "-- end prompt --"
"""What brackets the assembled prompt in the run log (pin :1094).

The whole prompt, verbatim, before the pass runs: "what exactly was this agent told" is the
first question anyone asks of a bad result, and the log is the only place it can be answered.
Self-authored and secret-free — the prompt is bessemer's templates plus generated lines, and
the spec crosses the boundary as a read-only mount rather than as text (ADR 0001).
"""

HEARTBEAT: Final = "    ... claude pass still running ({minutes}m, cap {cap}m)"
TIMED_OUT: Final = "claude pass TIMED OUT at {seconds}s (attempt {attempt}/{attempts})"
FAILED: Final = "claude pass failed rc={returncode} (attempt {attempt}/{attempts}): {detail}"
NO_RESULT: Final = "claude pass ended without a result (attempt {attempt}/{attempts})"
RETRYING: Final = "retrying in {seconds}s ..."
CONTAINER_GONE: Final = "container {container} is gone — aborting the run"
"""The pin's message shapes, kept because an operator reads them and a test pins them.

`FAILED`'s detail is `proc.quote` at `HOST_LOG`, not the raw tail upstream cut to 140
characters: quoting anything of a child's is the one policy `proc.py` owns (ADR 0003), and
this is one of its three call sites. What that leaves in the slot is the failing argv and the
exit status and nothing else — `proc.streamed` merges the child's stderr into the transcript,
so the `Result` it returns carries none — and the argv is what says *which* container and
which deadline this attempt was. The pass's own words, stderr included, are already in the
log above this line, rendered by `bessemer.stream`.

`NO_RESULT` has no counterpart at the pin and is not a divergence: upstream ran its filter
inside the pipeline, so a stream that ended without a result event *was* a nonzero `rc` and
took the `FAILED` line. Host-side the exec exits 0 and the failure is the capture's, so
saying `rc=0` would be a line that reads as a contradiction.
"""

REVIEW_ROUND: Final = "review round {round}/{rounds} ..."
APPROVED_SAID: Final = "review verdict: APPROVED (round {round})"
NEEDS_WORK_SAID: Final = "review verdict: needs-work (round {round})"

VERDICT_TOKEN: Final = "<verdict>approved</verdict>"
"""The reviewer's approval, matched as a substring of the pass's final text.

One literal, spelled once, and the same one the review prompt asks for and F1's
`REVIEWING.md` already exercises. Substring rather than parse is the pin's `grep -q`, and it
is the forgiving direction: a reviewer that wraps its verdict in a sentence has still
approved, and there is no second token whose meaning it could be confused with.
"""

APPROVED: Final = "approved"
NEEDS_WORK: Final = "needs-work"
"""The two verdict tokens the ledger and the pull-request footer carry (`verdict_token`)."""

APPROVED_FOOTER: Final = "Review: approved (round {round}/{rounds})."
NEEDS_WORK_FOOTER: Final = (
    "⚠️ Review: needs-work after {rounds} round(s) — read the task log before reviewing."
)
"""What the pull-request body says about the review (pin :1497–1498, :1521).

Pinned as whole sentences, F3 decision 6.6: this is the sentence a human reads before
deciding how carefully to review an unattended agent's diff, and a reworded footer is a
changed contract rather than an edit. "task log" is left as the pin wrote it — it names the
log file, and renaming the artifact is issue 10's business, not a prose choice made here.
"""

REVIEW_LINE: Final = "Review the work on branch {branch} — every commit after {boundary}."
"""The one generated line a review round adds to the template (pin :1503–1504).

Prompt *templates* are `bessemer.prompts`' and the dispatcher's preamble is issue 10's; this
line is neither. It belongs to the loop because it is the only part of a review prompt that
changes with the run, and `prompts.load` deliberately composes nothing.
"""


def _positive_number(key: str, value: int | str) -> int:
    """`value` as a positive `int`, or a refusal naming `key`.

    Both knobs arrive from `bessemer.config`, which **does not coerce**: a `pass_timeout` set
    through `BESSEMER_PASS_TIMEOUT` is the string `"60"` (pinned at
    `tests/test_config.py`'s pass_timeout test) while the same key in TOML is an `int`. So
    both types are accepted and neither is guessed at.

    A `bool` is refused although it is an `int`: `pass_timeout = true` in TOML is a mistake
    that would otherwise arrive as a one-second deadline on every pass.

    Raises, and raises with the key in the message, because the caller is `Limits` and the
    reader is whoever wrote the config file.
    """
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{key} is not a number: {value!r}")
    if isinstance(value, str) and not value.strip().isdigit():
        raise ValueError(f"{key} is not a number: {value!r}")
    number = int(value)
    if number <= 0:
        raise ValueError(f"{key} is not positive: {value!r}")
    return number


@dataclass(frozen=True, kw_only=True)
class Limits:
    """The two knobs config gives the loop, checked before a container exists.

    `container.Boundary`'s shape and `container.Boundary`'s reason: constructing this is the
    only route to a pass argv or a review loop, so "a non-numeric `pass_timeout` fails before
    the first pass" is structural rather than a rule a call site remembers. The load-time
    visibility argument behind `config`'s volume validation applies one layer in — a
    `max_review_rounds` of `"three"` must not be discovered after a checkout is cloned and a
    container is running, and `timeout three` would be rc 125 twenty minutes later.

    Keyword-only, no defaults: the real ones live in `config.DEFAULTS`, where restating them
    would be two values free to disagree.
    """

    pass_timeout: int | str
    """Seconds `timeout` gives one pass, in-container. `str` because the env layer is text."""

    max_review_rounds: int | str
    """How many review rounds at most, before the loop reports needs-work."""

    seconds: int = field(init=False)
    """`pass_timeout` as a number, converted once. What the argv and the messages use."""

    rounds: int = field(init=False)
    """`max_review_rounds` as a number, converted once."""

    def __post_init__(self) -> None:
        # Normalisation on a frozen dataclass, which is what `object.__setattr__` is for —
        # `container.Boundary.__post_init__`'s move, for its reason: the declared types are
        # the wider ones a caller may pass, and the stored ones are what every reader gets.
        # Converting here rather than in a property is what makes the check happen exactly
        # once, at the moment that decides whether there will be a pass at all.
        object.__setattr__(self, "seconds", _positive_number("pass_timeout", self.pass_timeout))
        object.__setattr__(
            self, "rounds", _positive_number("max_review_rounds", self.max_review_rounds)
        )


@dataclass(frozen=True)
class PassResult:
    """What one pass came to, after however many attempts it took.

    **No `__bool__`, and none may be added**, for `proc.Result`'s reason and
    `stream.Capture`'s: `if result:` would read as "is there a result" and mean "did the pass
    succeed".
    """

    ok: bool
    """Whether an attempt produced a successful stream. False after `ATTEMPTS` failures, and
    false when the container went away."""

    text: str
    """The pass's final text — what the reviewer's verdict is read out of and what a
    description pass hands to a pull-request body. Empty unless `ok`."""

    attempts: int
    """How many attempts were made, so a caller can say "after 3 attempts" without counting."""

    container_gone: bool
    """Whether the loop stopped because the container was no longer there.

    Distinct from an ordinary failure because the response is: a dispatch aborts and does not
    try the next pass into a container that is not there, and the operator is being told that
    something outside bessemer removed it.
    """


@dataclass(frozen=True)
class Verdict:
    """How the review ended: the token, the round, and the sentence the pull request carries."""

    approved: bool
    token: str
    """`APPROVED` or `NEEDS_WORK` — what the ledger records as `verdict_token`."""

    round: int
    """The round it ended on: the approving round, or the cap when none approved."""

    rounds: int
    """The cap it was run under, so the footer and the ledger agree about `N`."""

    footer: str
    """`APPROVED_FOOTER` or `NEEDS_WORK_FOOTER`, filled in. The pull-request body's line."""

    failure: PassResult | None = None
    """The failed pass that ended the loop early, if one did.

    `None` on every ordinary outcome, approved or not. When it is set the run is over — a
    review pass that failed three times, or a container that went away mid-round — and the
    caller aborts rather than lands. Upstream reaches the same place through `set -euo
    pipefail`, which ends the run at the failing `claude_pass` inside the loop.
    """


def pass_argv(*, container: str, limits: Limits) -> list[str]:
    """`docker exec -i <container> timeout <pass_timeout> claude …`. Pure; the whole argv.

    **`-i` is what makes the prompt possible**: the prompt goes on the child's stdin and
    never into an argument, so no amount of quoting in a spec, a branch name or a template
    can become a word of a command line (ADR 0001).

    **`timeout` is the second word inside the container, not a wrapper on the host.** See the
    module docstring: a host-side kill ends the client and leaves the agent running in the
    container, which then wedges. This is the shape that ends the thing that is actually
    running.
    """
    return [
        DOCKER,
        "exec",
        "-i",
        container,
        TIMEOUT_PROGRAM,
        str(limits.seconds),
        *CLAUDE_ARGV,
    ]


liveness_argv = _container_liveness_argv
"""`bessemer.container.liveness_argv`, under the name this module and its tests already use.

The declaration moved next door at issue 11, when `bessemer.reclaim` became its second
consumer — the anchoring is a container fact, and two spellings of an anchored `docker ps`
are two chances for one of them to lose a `^`. An assignment rather than a bare import so the
name is an explicit export: ADR 0003 lists it under this module's interface, and it still is.
"""


def review_prompt(template: str, *, branch: str, boundary: str) -> str:
    """The review template plus the one line naming this run's branch and diff boundary.

    Composed the way the pin composes it (`"$(cat …)\\n<line>"`): command substitution strips
    the file's trailing newlines, so the generated line follows the template's last line of
    text by exactly one newline. Spelled out here rather than left to `str.join` so that the
    assembled bytes are the pin's.
    """
    return template.rstrip("\n") + "\n" + REVIEW_LINE.format(branch=branch, boundary=boundary)


@dataclass
class _Heartbeat:
    """Says "still running" on the console when one is due, and not otherwise.

    A class rather than a closure over the loop's variables, which is what keeps the elapsed
    time and the beat count from being rebound by the next attempt — and what makes the
    cadence testable against a fake clock without a pass anywhere in sight.
    """

    say: Callable[[str], None]
    clock: Callable[[], float]
    cap_minutes: int
    started: float
    beats: int = 0

    def __call__(self) -> None:
        elapsed = self.clock() - self.started
        if elapsed < (self.beats + 1) * HEARTBEAT_SECONDS:
            return
        self.beats += 1
        self.say(HEARTBEAT.format(minutes=int(elapsed // 60), cap=self.cap_minutes))


@dataclass
class _Rendering:
    """Renders one attempt's transcript into the log, and keeps what it captured.

    `proc.streamed` hands the child's output to a callable on another thread; this is that
    callable, and `capture` is where the final text ends up. `bessemer.stream.filtered` does
    the rendering — the provider contract is one surface and lives in one module (ADR 0001).
    """

    emit: Callable[[str], None]
    capture: stream.Capture | None = field(default=None)

    def __call__(self, lines: Iterable[str]) -> None:
        self.capture = stream.filtered(lines, emit=self.emit)


def _alive(*, container: str, run: proc.Runner) -> bool:
    """Whether the container is still there. A `docker ps` that could not answer says "no".

    The pin's `[ -z "$(docker ps -q -f name=…)" ]`, including its treatment of a broken
    daemon: command substitution over a failed command is the empty string, so no output is
    no container. Aborting on a docker that cannot answer is the safe direction — the
    alternative retries a pass into something nobody can see.

    The two cases `proc.run` deliberately does not flatten into a `Result` — a docker that
    cannot be executed (`OSError`) and one that hung past the deadline
    (`proc.TimeoutExpired`) — are absorbed **here**, and only here, because they are the
    same answer: nothing said a container is there. Letting them escape would break
    `run_pass`'s own contract of returning rather than raising, in the middle of a run whose
    container may still need removing.
    """
    try:
        result = run(liveness_argv(container=container), timeout=LIVENESS_TIMEOUT_SECONDS)
    except OSError, proc.TimeoutExpired:
        return False
    return bool(result.stdout.strip())


def run_pass(
    *,
    prompt: str,
    container: str,
    limits: Limits,
    emit: Callable[[str], None],
    say: Callable[[str], None],
    run: proc.Runner,
    streamer: proc.Streamer,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> PassResult:
    """Run one agent pass in `container`, retrying up to `ATTEMPTS` times. The pin's
    `claude_pass`.

    `prompt` reaches the agent on stdin and appears in no argv; it is written to the log
    first, between `PROMPT_OPEN` and `PROMPT_CLOSE`, so the run log answers what the agent was
    told. `emit` takes the log lines — the prompt, and every `claude |/>` line
    `bessemer.stream` renders while the pass runs; `say` takes the handful of lines an
    operator watching the console needs: the heartbeat, a failure, a retry, a dead container.

    Returns rather than raises on failure, `proc.run`'s reason: three failed attempts is an
    outcome the caller decides about (issue 10 aborts the run), not an exception in the middle
    of a `try/finally` that is already unwinding a container.
    """
    emit(PROMPT_OPEN)
    for line in prompt.splitlines():
        emit(line)
    emit(PROMPT_CLOSE)

    cap_minutes = limits.seconds // 60
    for attempt in range(1, ATTEMPTS + 1):
        rendering = _Rendering(emit)
        result = streamer(
            pass_argv(container=container, limits=limits),
            stdin_text=prompt,
            consume=rendering,
            idle=_Heartbeat(say=say, clock=clock, cap_minutes=cap_minutes, started=clock()),
            poll=POLL_SECONDS,
        )
        capture = rendering.capture
        if result.ok and capture is not None and capture.ok:
            return PassResult(
                ok=True, text=capture.text, attempts=attempt, container_gone=False
            )

        if result.returncode == TIMEOUT_RETURNCODE:
            say(TIMED_OUT.format(seconds=limits.seconds, attempt=attempt, attempts=ATTEMPTS))
        elif result.ok:
            # The exec succeeded and the agent still produced nothing — see `NO_RESULT`.
            say(NO_RESULT.format(attempt=attempt, attempts=ATTEMPTS))
        else:
            # The pass's own words are already in the log, rendered above; what this adds is
            # the exit status and the argv, through the one policy that decides what of a
            # child may be said where (ADR 0003).
            say(
                FAILED.format(
                    returncode=result.returncode,
                    attempt=attempt,
                    attempts=ATTEMPTS,
                    detail=proc.quote(result, destination=proc.Destination.HOST_LOG),
                )
            )

        if not _alive(container=container, run=run):
            # Before any retry, every time — never after the sleep, and never skipped on the
            # last attempt: the operator is being told why the run is ending, and a caller
            # must not spawn into a container something else removed (pin :1122–1126).
            say(CONTAINER_GONE.format(container=container))
            return PassResult(ok=False, text="", attempts=attempt, container_gone=True)

        if attempt < ATTEMPTS:
            say(RETRYING.format(seconds=int(RETRY_SECONDS)))
            sleep(RETRY_SECONDS)

    return PassResult(ok=False, text="", attempts=ATTEMPTS, container_gone=False)


def review_loop(
    *,
    template: str,
    branch: str,
    boundary: str,
    container: str,
    limits: Limits,
    emit: Callable[[str], None],
    say: Callable[[str], None],
    run: proc.Runner,
    streamer: proc.Streamer,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> Verdict:
    """Review until a round approves or the cap is reached. The pin's `:1494–1527`.

    Every round runs the same prompt — the reviewer reads the branch, not this loop's memory
    of it — and the loop ends the moment a round's final text carries `VERDICT_TOKEN`. The
    prompt is composed once for the same reason: it does not change between rounds, and
    building it per round would invite a difference nobody asked for.

    Reaching the cap is **not** a failure here: it returns a needs-work `Verdict` and the run
    still lands (F3 decision 6.6). What ends the loop early is a pass that failed, which comes
    back as `Verdict.failure` with the round it died in.
    """
    rounds = limits.rounds
    prompt = review_prompt(template, branch=branch, boundary=boundary)
    for number in range(1, rounds + 1):
        say(REVIEW_ROUND.format(round=number, rounds=rounds))
        result = run_pass(
            prompt=prompt,
            container=container,
            limits=limits,
            emit=emit,
            say=say,
            run=run,
            streamer=streamer,
            sleep=sleep,
            clock=clock,
        )
        if not result.ok:
            return Verdict(
                approved=False,
                token=NEEDS_WORK,
                round=number,
                rounds=rounds,
                footer=NEEDS_WORK_FOOTER.format(rounds=rounds),
                failure=result,
            )
        if VERDICT_TOKEN in result.text:
            say(APPROVED_SAID.format(round=number))
            return Verdict(
                approved=True,
                token=APPROVED,
                round=number,
                rounds=rounds,
                footer=APPROVED_FOOTER.format(round=number, rounds=rounds),
            )
        say(NEEDS_WORK_SAID.format(round=number))

    return Verdict(
        approved=False,
        token=NEEDS_WORK,
        round=rounds,
        rounds=rounds,
        footer=NEEDS_WORK_FOOTER.format(rounds=rounds),
    )
