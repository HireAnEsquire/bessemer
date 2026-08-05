"""Tests for the pass loop: the pinned argv, the retry ladder, the heartbeat, the verdict.

Nothing here spawns and nothing sleeps. The two proc seams are one `Double` — a `proc.Runner`
for the liveness probe and a `proc.Streamer` for the pass itself — recording both into **one
ordered list of calls**, which is what lets "no further exec after the liveness check" be an
assertion rather than a hope (ADR 0003's tier-2 rider 2).

The clock and the sleep are the double's too. A test that let the real ones through would
take ninety seconds to prove a retry ladder and four minutes to prove a heartbeat, so the
module takes both as required keywords and this file supplies fakes for every call.

**The security-shaped assertions are hand-written literals** (ADR 0003's tier-2 rider 1): the
claude argv, the `timeout` wrapper around it, the anchored liveness filter, the verdict token
and both footers are spelled out here as well as in `bessemer.passes`. A test that built its
expectation from the module would agree with a mutation of it.
"""

import json
import unittest
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from bessemer import passes, proc

CONTAINER: Final = "bessemer-work"
BRANCH: Final = "agent/work"
BOUNDARY: Final = "1c0ffee1c0ffee1c0ffee1c0ffee1c0ffee1c0ff"

PASS_TIMEOUT: Final = 900
MAX_REVIEW_ROUNDS: Final = 3
"""`config.DEFAULTS`' two knobs, restated. What every test runs under unless it says so."""

CLAUDE_ARGV: Final = (
    "claude",
    "--dangerously-skip-permissions",
    "-p",
    "--output-format",
    "stream-json",
    "--verbose",
)
"""The provider invocation, restated by hand (run.sh:1099).

Written twice by two readers on purpose: this is the only thing in the argv that decides the
agent runs unattended and speaks stream-json, and a flag lost from it is a pass that hangs on
a terminal that is not there or renders nothing into the log.
"""

VERDICT_TOKEN: Final = "<verdict>approved</verdict>"
"""The approval token, restated by hand. The same string F1's `REVIEWING.md` exercises."""

APPROVED_FOOTER: Final = "Review: approved (round 1/3)."
NEEDS_WORK_FOOTER: Final = (
    "⚠️ Review: needs-work after 3 round(s) — read the task log before reviewing."
)
"""Both pull-request footers, restated whole (pin :1497–1498, :1521; F3 decision 6.6).

The sentence a human reads before deciding how carefully to review an unattended agent's
diff. Reworded, it is a changed contract — so it is written out here rather than formatted
from the module's own template.
"""

NEEDS_WORK_FOOTER_AT_TWO: Final = (
    "⚠️ Review: needs-work after 2 round(s) — read the task log before reviewing."
)
"""The same sentence under a cap of two, so the count is pinned as part of the sentence
rather than as a number formatted into a template both sides share."""

SECRET_IN_PROMPT: Final = "prompt-body-that-must-never-be-an-argument"
"""A distinctive string in every prompt these tests build, hunted for across the recorded
argv stream. A test that found it would name the channel it leaked through."""


def transcript(text: str, *, ok: bool = True) -> tuple[str, ...]:
    """A minimal real stream-json transcript: one assistant text block, then the result.

    Real JSON rather than a stub, because `run_pass` renders it through
    `bessemer.stream.filtered` — the integration between the two is part of what is under
    test here, and a fake capture would let a broken plumbing pass.
    """
    lines = [
        json.dumps(
            {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
        )
    ]
    if ok:
        lines.append(json.dumps({"type": "result", "result": text}))
    return tuple(lines)


@dataclass(frozen=True)
class Call:
    """One recorded call at either proc seam: what ran, with what deadline, from where.

    Everything both seams are handed, not only argv (ADR 0003's tier-2 rider 2, and
    `tests/test_container.py`'s `Call`): a probe whose timeout nothing asserts is a backstop
    that can be deleted on a green suite.
    """

    argv: tuple[str, ...]
    stdin: str | None
    """The prompt, for a streamed pass; `None` for the liveness probe, which has no stdin."""

    timeout: float | None = None
    """The probe's deadline. `None` for a pass, which deliberately has no host-side one."""

    cwd: Path | None = None
    env: Mapping[str, str] | None = None


@dataclass(frozen=True)
class Attempt:
    """One scripted pass: what the exec exited, what it wrote, and how long it took.

    `idles` is how many times the streamer looks up from the child before it finishes — the
    fake clock advances by one `poll` per idle, so a test asks for wall time in units of
    polls and never in seconds it has to wait.
    """

    returncode: int = 0
    lines: tuple[str, ...] = ()
    idles: int = 0


class Double:
    """Both proc seams, one recorder. Never spawns, never sleeps, never reads a clock.

    A thin table over the real `proc.Result` (ADR 0003's tier-2 rider 2). Its
    `__call__` is the `proc.Runner` the liveness probe uses and `stream` is the
    `proc.Streamer` a pass runs on; both append to `calls`, in order, because the ordering
    between them is itself a contract — the dead-container check has to come before a retry
    and nothing may follow it.

    **Named limit, in the house style of `tests/guard.py`:** running out of scripted attempts
    answers success with an empty transcript, which `run_pass` reads as a failed pass. Every
    test that scripts failures asserts the call count too, so an unscripted extra attempt
    shows up as a count mismatch rather than as a silent third failure.
    """

    def __init__(
        self,
        *attempts: Attempt,
        alive: Sequence[bool] = (),
        probe_raises: BaseException | None = None,
    ) -> None:
        self.calls: list[Call] = []
        self.attempts = list(attempts)
        self.alive = list(alive)
        self.probe_raises = probe_raises
        self.now = 0.0
        self.slept: list[float] = []
        self.polls: list[float] = []
        """The poll interval each pass was started with — the cadence, as the seam saw it."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> proc.Result:
        """The liveness probe. `docker ps -q` prints a container id, or nothing at all.

        `probe_raises` is the other half of what `proc.run` can do: a docker that cannot be
        executed or one that hung past the deadline never becomes a `Result` at all.
        """
        self.calls.append(Call(tuple(argv), None, timeout, cwd, env))
        if self.probe_raises is not None:
            raise self.probe_raises
        alive = self.alive.pop(0) if self.alive else True
        return proc.Result(
            argv=tuple(argv), returncode=0, stdout="c0ffee\n" if alive else "", stderr=""
        )

    def stream(
        self,
        argv: Sequence[str],
        *,
        stdin_text: str,
        consume: Callable[[Iterable[str]], None],
        idle: Callable[[], None],
        poll: float,
    ) -> proc.Result:
        self.calls.append(Call(tuple(argv), stdin_text))
        attempt = self.attempts.pop(0) if self.attempts else Attempt()
        self.polls.append(poll)
        for _ in range(attempt.idles):
            self.now += poll
            idle()
        consume(iter(attempt.lines))
        return proc.Result(
            argv=tuple(argv), returncode=attempt.returncode, stdout="", stderr=""
        )

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    @property
    def argvs(self) -> list[tuple[str, ...]]:
        return [call.argv for call in self.calls]

    @property
    def words(self) -> list[str]:
        """Every argument of every recorded call, flattened. For the leak assertions."""
        return [word for call in self.calls for word in call.argv]


@dataclass
class Log:
    """Where the two output channels go: `emit` is the run log, `say` is the console."""

    emitted: list[str] = field(default_factory=list)
    said: list[str] = field(default_factory=list)


class PassesTest(unittest.TestCase):
    """A double, a log, and the limits every pass runs under."""

    def setUp(self) -> None:
        self.log = Log()

    def limits(
        self,
        *,
        pass_timeout: int | str = PASS_TIMEOUT,
        max_review_rounds: int | str = MAX_REVIEW_ROUNDS,
    ) -> passes.Limits:
        return passes.Limits(pass_timeout=pass_timeout, max_review_rounds=max_review_rounds)

    def run_pass(self, double: Double, *, prompt: str = SECRET_IN_PROMPT) -> passes.PassResult:
        """`run_pass` with both seams on `double` and both clocks fake.

        `streamer` is annotated at the seam rather than passed inline, so a `Double.stream`
        that stopped matching `proc.Streamer` is a type error here and not a runtime surprise
        in `bessemer.passes`.
        """
        streamer: proc.Streamer = double.stream
        return passes.run_pass(
            prompt=prompt,
            container=CONTAINER,
            limits=self.limits(),
            emit=self.log.emitted.append,
            say=self.log.said.append,
            run=double,
            streamer=streamer,
            sleep=double.sleep,
            clock=double.clock,
        )

    def review_loop(
        self,
        double: Double,
        *,
        template: str = "REVIEW\n",
        rounds: int | str = MAX_REVIEW_ROUNDS,
    ) -> passes.Verdict:
        streamer: proc.Streamer = double.stream
        return passes.review_loop(
            template=template,
            branch=BRANCH,
            boundary=BOUNDARY,
            container=CONTAINER,
            limits=self.limits(max_review_rounds=rounds),
            emit=self.log.emitted.append,
            say=self.log.said.append,
            run=double,
            streamer=streamer,
            sleep=double.sleep,
            clock=double.clock,
        )


class ArgvTest(PassesTest):
    """The two argvs, as literals. Both are contracts with something outside this package."""

    def test_the_claude_invocation_is_the_pinned_literal(self) -> None:
        self.assertEqual(passes.CLAUDE_ARGV, CLAUDE_ARGV)

    def test_the_exec_wraps_the_claude_argv_in_an_in_container_timeout(self) -> None:
        """The whole argv, compared whole: `-i` for the prompt, `timeout` on the container's
        side of the boundary, and the pinned invocation after it."""
        self.assertEqual(
            passes.pass_argv(container=CONTAINER, limits=self.limits()),
            [
                "docker",
                "exec",
                "-i",
                CONTAINER,
                "timeout",
                "900",
                *CLAUDE_ARGV,
            ],
        )

    def test_a_string_pass_timeout_reaches_timeout_as_a_number(self) -> None:
        """The environment layer carries text (`tests/test_config.py`'s pass_timeout test), and
        `timeout` is handed a number either way."""
        argv = passes.pass_argv(container=CONTAINER, limits=self.limits(pass_timeout="60"))
        self.assertEqual(argv[4:6], ["timeout", "60"])

    def test_the_liveness_probe_anchors_the_container_name(self) -> None:
        self.assertEqual(
            passes.liveness_argv(container=CONTAINER),
            ["docker", "ps", "-q", "-f", f"name=^{CONTAINER}$"],
        )

    def test_the_liveness_filter_would_not_match_a_neighbouring_run(self) -> None:
        """`docker ps -f name=x` is a substring match. Without the anchors this run would read
        another run's container as its own and retry into it."""
        argv = passes.liveness_argv(container=CONTAINER)
        self.assertNotIn(f"name={CONTAINER}", argv)
        self.assertIn(f"name=^{CONTAINER}$", argv)


class LimitsTest(PassesTest):
    """The two config knobs, checked before anything is built from them."""

    def test_a_non_numeric_pass_timeout_is_refused_naming_the_key(self) -> None:
        with self.assertRaises(ValueError) as caught:
            passes.Limits(pass_timeout="fifteen minutes", max_review_rounds=3)
        self.assertIn("pass_timeout", str(caught.exception))

    def test_a_non_numeric_max_review_rounds_is_refused_naming_the_key(self) -> None:
        with self.assertRaises(ValueError) as caught:
            passes.Limits(pass_timeout=900, max_review_rounds="three")
        self.assertIn("max_review_rounds", str(caught.exception))

    def test_a_bad_value_is_refused_before_any_container_work(self) -> None:
        """Structural, not incidental: `Limits` is the only route to a pass argv or a loop, so
        a value that cannot be a number fails while nothing has been spawned."""
        double = Double()
        with self.assertRaises(ValueError):
            passes.Limits(pass_timeout="soon", max_review_rounds=3)
        self.assertEqual(double.calls, [])

    def test_the_environment_layers_string_is_accepted(self) -> None:
        limits = passes.Limits(pass_timeout="60", max_review_rounds="1")
        self.assertEqual(limits.seconds, 60)
        self.assertEqual(limits.rounds, 1)

    def test_a_boolean_is_not_a_number(self) -> None:
        """`pass_timeout = true` is `1` to Python. A one-second deadline on every pass is not
        what anybody wrote it for."""
        with self.assertRaises(ValueError):
            passes.Limits(pass_timeout=True, max_review_rounds=3)

    def test_zero_and_negative_are_refused(self) -> None:
        for value in (0, -1, "0"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                passes.Limits(pass_timeout=value, max_review_rounds=3)


class RunPassTest(PassesTest):
    """The retry ladder, the prompt channel, and the messages an operator reads."""

    def test_a_pass_that_succeeds_first_time_runs_once_and_returns_its_final_text(self) -> None:
        double = Double(Attempt(lines=transcript("all done")))
        result = self.run_pass(double)
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "all done")
        self.assertEqual(result.attempts, 1)
        self.assertFalse(result.container_gone)
        # One call, and no liveness probe: nothing failed, so nothing asked.
        self.assertEqual(len(double.calls), 1)
        self.assertEqual(double.calls[0].argv[:4], ("docker", "exec", "-i", CONTAINER))

    def test_the_prompt_reaches_the_exec_on_stdin_and_appears_in_no_argv(self) -> None:
        double = Double(Attempt(lines=transcript("done")))
        self.run_pass(double, prompt=f"IMPLEMENT\n{SECRET_IN_PROMPT}")
        self.assertEqual(double.calls[0].stdin, f"IMPLEMENT\n{SECRET_IN_PROMPT}")
        for word in double.words:
            self.assertNotIn(SECRET_IN_PROMPT, word)

    def test_the_prompt_is_logged_verbatim_between_its_markers(self) -> None:
        """ "What exactly was this agent told" is the first question a bad result raises, and
        the run log is the only place it can be answered (pin :1094)."""
        double = Double(Attempt(lines=transcript("done")))
        self.run_pass(double, prompt="line one\nline two")
        self.assertEqual(
            self.log.emitted[:4], ["-- prompt --", "line one", "line two", "-- end prompt --"]
        )

    def test_the_transcript_is_rendered_into_the_log(self) -> None:
        double = Double(Attempt(lines=transcript("hello")))
        self.run_pass(double)
        self.assertIn("claude | hello", self.log.emitted)

    def test_two_failures_then_a_success_retries_twice_and_sleeps_twice(self) -> None:
        double = Double(
            Attempt(returncode=1),
            Attempt(returncode=1),
            Attempt(lines=transcript("third time")),
        )
        result = self.run_pass(double)
        self.assertTrue(result.ok)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(result.text, "third time")
        self.assertEqual(double.slept, [30.0, 30.0])
        # exec, probe, exec, probe, exec — the probe stands between every failure and its
        # retry, and the successful attempt asks nothing.
        self.assertEqual(
            [argv[1] for argv in double.argvs], ["exec", "ps", "exec", "ps", "exec"]
        )

    def test_three_failures_end_the_pass_and_sleep_only_between_them(self) -> None:
        double = Double(*(Attempt(returncode=1) for _ in range(3)))
        result = self.run_pass(double)
        self.assertFalse(result.ok)
        self.assertEqual(result.text, "")
        self.assertEqual(result.attempts, 3)
        self.assertFalse(result.container_gone)
        # Three attempts, three liveness probes, and no fourth exec.
        self.assertEqual(
            [argv[1] for argv in double.argvs],
            ["exec", "ps", "exec", "ps", "exec", "ps"],
        )
        self.assertEqual(double.slept, [30.0, 30.0])

    def test_a_stream_that_ends_without_a_result_is_a_failed_pass(self) -> None:
        """The exec exited 0 and the agent still produced nothing. Upstream reaches the same
        verdict through its pipeline's exit status; here it is `stream.Capture.ok`."""
        double = Double(
            Attempt(lines=transcript("half a thought", ok=False)),
            Attempt(lines=transcript("second time")),
        )
        result = self.run_pass(double)
        self.assertTrue(result.ok)
        self.assertEqual(result.attempts, 2)

    def test_a_pass_without_a_result_says_so_rather_than_reporting_rc_zero(self) -> None:
        """`rc=0` beside the word "failed" reads as a contradiction, and the exec really did
        exit 0 — the failure is the capture's."""
        double = Double(
            Attempt(lines=transcript("half a thought", ok=False)),
            Attempt(lines=transcript("second time")),
        )
        self.run_pass(double)
        self.assertIn("claude pass ended without a result (attempt 1/3)", self.log.said)
        self.assertEqual([line for line in self.log.said if "rc=" in line], [])

    def test_the_pass_is_streamed_at_the_polling_cadence(self) -> None:
        """The heartbeat rides the streamer's idle callback, so the interval it was started
        with is the cadence. Thirty seconds, the pin's."""
        double = Double(Attempt(lines=transcript("done")))
        self.run_pass(double)
        self.assertEqual(double.polls, [30.0])

    def test_the_liveness_probe_carries_a_deadline(self) -> None:
        """A probe with no deadline turns one failed pass into a run that never returns when
        the daemon is wedged — and a backstop nothing asserts is one that can be deleted on a
        green suite."""
        double = Double(Attempt(returncode=1), Attempt(lines=transcript("done")))
        self.run_pass(double)
        probe = next(call for call in double.calls if call.argv[1] == "ps")
        self.assertEqual(probe.timeout, 15.0)

    def test_a_daemon_that_cannot_answer_reads_as_a_dead_container(self) -> None:
        """`proc.run` converts neither case into a `Result`: docker missing raises `OSError`,
        docker hung raises `TimeoutExpired`. Both mean nothing said the container is there,
        and `run_pass` promises to return rather than raise."""
        for error in (
            OSError("no docker here"),
            proc.TimeoutExpired(cmd=["docker", "ps"], timeout=15.0),
        ):
            with self.subTest(error=type(error).__name__):
                self.log = Log()
                double = Double(Attempt(returncode=1), probe_raises=error)
                result = self.run_pass(double)
                self.assertFalse(result.ok)
                self.assertTrue(result.container_gone)
                self.assertEqual([argv[1] for argv in double.argvs], ["exec", "ps"])
                self.assertEqual(double.slept, [])

    def test_a_timeout_is_logged_with_the_pins_message_and_the_configured_cap(self) -> None:
        double = Double(Attempt(returncode=124), Attempt(lines=transcript("done")))
        self.run_pass(double)
        self.assertIn(
            "claude pass TIMED OUT at 900s (attempt 1/3)",
            self.log.said,
        )

    def test_a_failure_is_logged_with_its_returncode_through_the_quoting_policy(self) -> None:
        double = Double(Attempt(returncode=7), Attempt(lines=transcript("done")))
        self.run_pass(double)
        failed = [line for line in self.log.said if line.startswith("claude pass failed")]
        self.assertEqual(len(failed), 1)
        self.assertTrue(failed[0].startswith("claude pass failed rc=7 (attempt 1/3): "))
        # The detail is what `proc.quote` says for the host log, and nothing else — the
        # policy is where "what of a child may be quoted where" is decided (ADR 0003).
        self.assertIn(
            proc.quote(
                proc.Result(
                    argv=tuple(passes.pass_argv(container=CONTAINER, limits=self.limits())),
                    returncode=7,
                    stdout="",
                    stderr="",
                ),
                destination=proc.Destination.HOST_LOG,
            ),
            failed[0],
        )

    def test_a_dead_container_aborts_and_nothing_runs_after_the_probe(self) -> None:
        double = Double(
            Attempt(returncode=1), Attempt(lines=transcript("never reached")), alive=[False]
        )
        result = self.run_pass(double)
        self.assertFalse(result.ok)
        self.assertTrue(result.container_gone)
        self.assertEqual(result.attempts, 1)
        self.assertEqual([argv[1] for argv in double.argvs], ["exec", "ps"])
        self.assertIn(f"container {CONTAINER} is gone — aborting the run", self.log.said)
        # The abort is the point: no sleep, no second exec, nothing spawned into the dark.
        self.assertEqual(double.slept, [])

    def test_a_dead_container_is_checked_before_the_retry_sleep(self) -> None:
        """Ordering, not merely presence: a probe after the sleep would be thirty seconds of
        an operator's evening spent on a container that is already gone."""
        double = Double(Attempt(returncode=1), alive=[False])
        self.run_pass(double)
        self.assertEqual(len(double.calls), 2)
        self.assertEqual(double.slept, [])


class HeartbeatTest(PassesTest):
    """The console line a long pass prints, against a clock that never waits."""

    def test_four_fake_minutes_produce_exactly_two_heartbeats(self) -> None:
        """Eight polls of thirty seconds. The beats fall at two minutes and at four."""
        double = Double(Attempt(lines=transcript("done"), idles=8))
        self.run_pass(double)
        beats = [line for line in self.log.said if "still running" in line]
        self.assertEqual(
            beats,
            [
                "    ... claude pass still running (2m, cap 15m)",
                "    ... claude pass still running (4m, cap 15m)",
            ],
        )

    def test_a_pass_shorter_than_the_interval_says_nothing(self) -> None:
        double = Double(Attempt(lines=transcript("done"), idles=3))
        self.run_pass(double)
        self.assertEqual([line for line in self.log.said if "still running" in line], [])

    def test_each_attempt_counts_its_own_elapsed_time(self) -> None:
        """The beat count belongs to the attempt, not to the run: a retry that lasts one
        minute must not inherit the previous attempt's due beat."""
        double = Double(
            Attempt(returncode=1, idles=4), Attempt(lines=transcript("done"), idles=2)
        )
        self.run_pass(double)
        beats = [line for line in self.log.said if "still running" in line]
        self.assertEqual(beats, ["    ... claude pass still running (2m, cap 15m)"])


class ReviewPromptTest(PassesTest):
    """The one generated line a review round adds to the template."""

    def test_the_boundary_line_follows_the_template_by_one_newline(self) -> None:
        self.assertEqual(
            passes.review_prompt("REVIEW\n\n", branch=BRANCH, boundary=BOUNDARY),
            f"REVIEW\nReview the work on branch {BRANCH} — every commit after {BOUNDARY}.",
        )

    def test_the_composed_prompt_is_what_reaches_the_exec(self) -> None:
        double = Double(Attempt(lines=transcript(VERDICT_TOKEN)))
        self.review_loop(double, template="REVIEW TEMPLATE\n")
        self.assertEqual(
            double.calls[0].stdin,
            f"REVIEW TEMPLATE\nReview the work on branch {BRANCH} — "
            f"every commit after {BOUNDARY}.",
        )


class ReviewLoopTest(PassesTest):
    """Approval, the cap, and what a failed round does to the run."""

    def test_an_approval_in_round_one_breaks_the_loop(self) -> None:
        double = Double(Attempt(lines=transcript(f"looks good {VERDICT_TOKEN}")))
        verdict = self.review_loop(double)
        self.assertTrue(verdict.approved)
        self.assertEqual(verdict.token, "approved")
        self.assertEqual(verdict.round, 1)
        self.assertEqual(verdict.footer, APPROVED_FOOTER)
        self.assertIsNone(verdict.failure)
        self.assertEqual(len(double.calls), 1)
        self.assertIn("review verdict: APPROVED (round 1)", self.log.said)

    def test_a_verdict_embedded_mid_output_still_matches(self) -> None:
        """`grep -q` semantics, per the pin: a reviewer that wraps its verdict in a paragraph
        has still approved."""
        double = Double(Attempt(lines=transcript(f"I fixed nothing. {VERDICT_TOKEN} Ship it.")))
        self.assertTrue(self.review_loop(double).approved)

    def test_needs_work_runs_to_the_cap_and_returns_the_pinned_footer(self) -> None:
        double = Double(*(Attempt(lines=transcript("more work needed")) for _ in range(3)))
        verdict = self.review_loop(double)
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.token, "needs-work")
        self.assertEqual(verdict.round, 3)
        self.assertEqual(verdict.rounds, 3)
        self.assertEqual(verdict.footer, NEEDS_WORK_FOOTER)
        self.assertIsNone(verdict.failure)
        self.assertEqual(len(double.calls), 3)
        self.assertEqual(
            [line for line in self.log.said if line.startswith("review round")],
            ["review round 1/3 ...", "review round 2/3 ...", "review round 3/3 ..."],
        )

    def test_the_cap_is_the_configured_one(self) -> None:
        double = Double(*(Attempt(lines=transcript("more work")) for _ in range(2)))
        verdict = self.review_loop(double, rounds=2)
        self.assertEqual(verdict.rounds, 2)
        self.assertEqual(len(double.calls), 2)
        self.assertEqual(verdict.footer, NEEDS_WORK_FOOTER_AT_TWO)

    def test_a_failed_round_ends_the_loop_and_reports_the_pass(self) -> None:
        """Three failed attempts inside a round is the end of the run — upstream's
        `set -euo pipefail` stops there — so the loop hands the caller the `PassResult`."""
        double = Double(*(Attempt(returncode=1) for _ in range(3)))
        verdict = self.review_loop(double)
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.round, 1)
        self.assertIsNotNone(verdict.failure)
        assert verdict.failure is not None  # for the type checker; asserted above
        self.assertEqual(verdict.failure.attempts, 3)
        self.assertFalse(verdict.failure.container_gone)

    def test_a_dead_container_ends_the_loop_with_the_reason(self) -> None:
        double = Double(Attempt(returncode=1), alive=[False])
        verdict = self.review_loop(double)
        assert verdict.failure is not None
        self.assertTrue(verdict.failure.container_gone)
        self.assertEqual([argv[1] for argv in double.argvs], ["exec", "ps"])


if __name__ == "__main__":
    unittest.main()
