"""Tests for the subprocess wrapper: what it returns, what it refuses, and what it leaks.

Every child spawned here is `sys.executable`, which `tests/guard.py` allowlists. The
static half of this issue — that no other module may spawn at all — is
`tests/test_argv_boundary.py`.

The quotability classes at the end spawn nothing at all: the policy is a pure function
over a `Result`, and the credential-bearing text is a literal, for the reason
`tests/test_redact.py` writes one — git prints a remote URL while *contacting* the
remote, and this suite may not.
"""

import contextlib
import inspect
import os
import pty
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from typing import Final

from bessemer import proc, redact
from bessemer.proc import Destination, ProcessError, Result, quote, run, run_checked

TIMEOUT: Final = 30
"""Generous, because it is a backstop and not the thing under test. The one test that is
about the timeout sets its own."""

TOKEN: Final = "ghp_thisisnotarealtokenbutitlookslikeone"
CREDENTIAL_URL: Final = f"https://x-access-token:{TOKEN}@github.com/HireAnEsquire/bessemer.git"
REDACTED_URL: Final = "https://<redacted>@github.com/HireAnEsquire/bessemer.git"


def python(*statements: str) -> list[str]:
    """An argv running `statements` in a child interpreter."""
    return [sys.executable, "-c", "\n".join(statements)]


def strings_within(value: object, seen: set[int] | None = None) -> Iterator[str]:
    """Every string reachable from `value` by attribute or container, without cycling."""
    seen = set() if seen is None else seen
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, str):
        yield value
    elif isinstance(value, (bytes, bytearray)):
        yield value.decode("utf-8", "replace")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from strings_within(key, seen)
            yield from strings_within(item, seen)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from strings_within(item, seen)
    else:
        attributes = getattr(value, "__dict__", None)
        if attributes is not None:
            yield from strings_within(attributes, seen)
        yield str(value)


class RunTest(unittest.TestCase):
    def test_a_successful_run_reports_ok_and_its_output(self) -> None:
        result = run(python("print('out')"), timeout=TIMEOUT)
        self.assertTrue(result.ok)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "out")
        self.assertEqual(result.stderr, "")

    def test_a_nonzero_exit_is_data_rather_than_an_exception(self) -> None:
        """The reason `run` is the default: doctor's probes are all "did this fail, and
        how", and an exception per probe turns a check list into control flow."""
        result = run(
            python("import sys", "sys.stderr.write('boom')", "raise SystemExit(3)"),
            timeout=TIMEOUT,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stderr.strip(), "boom")

    def test_the_streams_are_kept_apart(self) -> None:
        result = run(
            python(
                "import sys",
                "sys.stdout.write('to stdout')",
                "sys.stderr.write('to stderr')",
            ),
            timeout=TIMEOUT,
        )
        self.assertEqual(result.stdout, "to stdout")
        self.assertEqual(result.stderr, "to stderr")

    def test_the_result_records_the_argv_it_ran(self) -> None:
        argv = python("pass")
        result = run(argv, timeout=TIMEOUT)
        self.assertEqual(result.argv, tuple(argv))

    def test_cwd_is_honoured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run(
                python("import os", "print(os.getcwd())"), timeout=TIMEOUT, cwd=Path(directory)
            )
            self.assertEqual(
                os.path.realpath(result.stdout.strip()), os.path.realpath(directory)
            )

    def test_env_is_honoured(self) -> None:
        result = run(
            python("import os", "print(os.environ['BESSEMER_TEST_VALUE'])"),
            timeout=TIMEOUT,
            env={**os.environ, "BESSEMER_TEST_VALUE": "from the caller"},
        )
        self.assertEqual(result.stdout.strip(), "from the caller")

    def test_a_string_argv_is_refused(self) -> None:
        """A `str` is a `Sequence[str]`, so the type checker permits what this module
        exists to forbid. The runtime check is the only one that fires."""
        # Erases mypy's `arg-type`: passing a string is the case being made, and the
        # annotation is what makes it a mistake worth catching at runtime as well.
        spawn: Callable[..., object] = run
        for argv in ("echo hello", b"echo hello"):
            with self.subTest(argv=argv):
                with self.assertRaises(TypeError) as caught:
                    spawn(argv, timeout=TIMEOUT)
                self.assertIn("not a string", str(caught.exception))

    def test_a_wedged_child_is_killed_rather_than_waited_on(self) -> None:
        """`timeout` is mandatory because a hung Docker daemon must not hang doctor. This
        pins what actually happens when it fires: `run` does not convert it to a
        `Result`, because no process completed and there is no returncode to report."""
        with self.assertRaises(subprocess.TimeoutExpired):
            run(python("import time", "time.sleep(30)"), timeout=0.2)

    def test_a_program_that_cannot_be_executed_raises(self) -> None:
        """The other case `run` deliberately does not flatten into a `Result`: inventing
        a returncode would make "not installed" indistinguishable from an exit code the
        program itself chose.

        The missing program is named `git` because the suite's guard allowlists spawns by
        basename, and a name it denies would raise `GuardViolation` here instead — the
        test would pass, for a reason that has nothing to do with the wrapper.
        """
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                run([str(Path(directory) / "git")], timeout=TIMEOUT)


@contextlib.contextmanager
def terminal_on_stdin() -> Iterator[None]:
    """Put a real pty on this process's file descriptor 0 for the duration.

    `pty.openpty` rather than `pty.spawn`, which the guard denies — opening a pty forks
    nothing. The master end stays open throughout: close it and the slave reads EOF, which
    is the very condition this fixture exists to remove.
    """
    controlling, terminal = pty.openpty()
    saved = os.dup(0)
    try:
        os.dup2(terminal, 0)
        yield
    finally:
        os.dup2(saved, 0)
        for descriptor in (saved, terminal, controlling):
            os.close(descriptor)


class StdinTest(unittest.TestCase):
    """A child must not be able to read bessemer's stdin.

    **This whole class is meaningless without the pty.** The runner's own stdin is not
    guaranteed to be a terminal, and under CI it is not one: a child then reads EOF
    immediately whether or not the wrapper closed anything, so the assertion would pass on
    an implementation that had stopped doing this entirely. The fixture is what makes fd 0
    the same thing on every host, which is what makes this a test of `stdin=DEVNULL`
    rather than of the environment the suite happened to run in.
    """

    #: Short, because the control has to spend all of it. The child announces itself
    #: before it blocks, so a timeout caused by a slow interpreter start is detectable
    #: rather than indistinguishable from the blocked read this is trying to observe.
    BLOCKED: Final = 1.0

    READER: Final = (
        "import sys",
        "print('ready', flush=True)",
        "line = sys.stdin.readline()",
        "print('eof' if line == '' else 'read ' + line.strip(), flush=True)",
    )

    def test_the_fixture_really_installs_a_terminal(self) -> None:
        """Named separately so a broken fixture reads as a broken fixture, rather than as
        the wrapper having stopped closing stdin.

        The only assertion here with content is the one *inside* the fixture. What fd 0 is
        on the way in is a fact about the host, not about this code: `make check` redirects
        only stderr, so under an interactive shell fd 0 is the developer's terminal and
        under CI it is not. Asserting either would make a correct implementation go red in
        the one place a human is watching and stay green in the one place they are not.
        Restoration is therefore checked against what was actually there.
        """
        before = os.isatty(0)
        with terminal_on_stdin():
            self.assertTrue(os.isatty(0), "the fixture must put a terminal on fd 0")
        self.assertEqual(os.isatty(0), before, "the fixture must put fd 0 back")

    def test_an_inherited_stdin_blocks_for_the_whole_timeout(self) -> None:
        """The control. Without it, the test below cannot tell "the wrapper closed stdin"
        from "there was nothing on stdin to read anyway"."""
        with terminal_on_stdin():
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                subprocess.run(
                    python(*self.READER),
                    timeout=self.BLOCKED,
                    capture_output=True,
                    text=True,
                )
        self.assertIn(
            "ready",
            str(caught.exception.stdout),
            "the child never reached its read; the timeout says nothing about stdin",
        )

    def test_the_wrapper_gives_the_child_no_stdin_to_block_on(self) -> None:
        with terminal_on_stdin():
            result = run(python(*self.READER), timeout=self.BLOCKED)
        self.assertTrue(result.ok)
        self.assertEqual(result.stdout.split(), ["ready", "eof"])


class MandatoryTimeoutTest(unittest.TestCase):
    """Required keyword, not a default — at every call site, both entry points."""

    def test_omitting_the_timeout_is_a_type_error(self) -> None:
        # Erases mypy's `call-arg`: omitting a required keyword argument is the case
        # being tested, so the call has to be one the type checker cannot read.
        for name, spawn in (("run", run), ("run_checked", run_checked)):
            erased: Callable[..., object] = spawn
            with self.subTest(entry_point=name):
                with self.assertRaises(TypeError) as caught:
                    erased(python("pass"))
                self.assertIn("timeout", str(caught.exception))

    def test_the_timeout_is_keyword_only(self) -> None:
        """Positional would make it droppable at a glance and orderable by accident."""
        # Erases mypy's `too-many-positional-arguments`, which is the assertion.
        erased: Callable[..., object] = run
        with self.assertRaises(TypeError):
            erased(python("pass"), TIMEOUT)


class RunCheckedTest(unittest.TestCase):
    SECRET: Final = "s3cret-token-in-the-environment"

    def failing_call(self) -> ProcessError:
        """Run a child that exits nonzero with a credential in its environment."""
        with self.assertRaises(ProcessError) as caught:
            run_checked(
                python("import sys", "sys.stderr.write('no such ref')", "raise SystemExit(2)"),
                timeout=TIMEOUT,
                env={**os.environ, "BESSEMER_TEST_TOKEN": self.SECRET},
            )
        return caught.exception

    def test_a_successful_call_returns_the_result(self) -> None:
        result = run_checked(python("print('fine')"), timeout=TIMEOUT)
        self.assertTrue(result.ok)
        self.assertEqual(result.stdout.strip(), "fine")

    def test_a_failing_call_raises_with_argv_returncode_and_stderr(self) -> None:
        message = str(self.failing_call())
        self.assertIn(sys.executable, message)
        self.assertIn("exited 2", message)
        self.assertIn("no such ref", message)

    def test_the_exception_carries_the_result_for_a_handler(self) -> None:
        """So a caller can branch on the returncode without parsing the message."""
        error = self.failing_call()
        self.assertIsInstance(error.result, Result)
        self.assertEqual(error.result.returncode, 2)

    def test_the_exception_carries_no_environment(self) -> None:
        """The invariant, swept rather than asserted about one field: the environment
        holds real credentials host-side, so nothing reachable from the exception as data
        may contain it.

        `__traceback__` is deliberately out of scope. Its frames hold the caller's locals,
        `env` among them, which is true of every exception in Python and is a property of
        tracebacks rather than of this exception.
        """
        error = self.failing_call()
        reachable = [
            *strings_within(error),
            *strings_within(error.args),
            str(error),
            repr(error),
        ]
        for text in reachable:
            self.assertNotIn(self.SECRET, text)
            self.assertNotIn("BESSEMER_TEST_TOKEN", text)

    def test_the_sweep_would_notice_a_leak(self) -> None:
        """Otherwise the test above passes on any exception at all, including one that
        stopped carrying anything."""
        error = self.failing_call()
        self.assertTrue(any("no such ref" in text for text in strings_within(error)))


class StreamTest(unittest.TestCase):
    """The second seam: a prompt in on stdin, a transcript out line by line, no deadline.

    Every child here is `sys.executable`, like the rest of this file. The one thing that
    cannot be a literal is time: two tests wait on a child that sleeps for a fraction of a
    second, because "the caller saw the line before the child exited" and "idle fires while
    it runs" are claims about concurrency and nothing else can hold them.
    """

    POLL: Final = 0.05
    """Short, because it is how often the wait loop looks up, not a deadline."""

    def consumed(
        self,
        argv: list[str],
        *,
        stdin_text: str = "",
        idle: Callable[[], None] = lambda: None,
    ) -> tuple[Result, list[str]]:
        """Run `argv` through `stream`, collecting the lines the consumer was handed."""
        lines: list[str] = []

        def consume(handle: Iterable[str]) -> None:
            lines.extend(line.rstrip("\n") for line in handle)

        result = proc.streamed(
            argv, stdin_text=stdin_text, consume=consume, idle=idle, poll=self.POLL
        )
        return result, lines

    def test_stdin_reaches_the_child_and_its_output_reaches_the_consumer(self) -> None:
        result, lines = self.consumed(
            python("import sys", "print(sys.stdin.read().strip().upper())"),
            stdin_text="a prompt\n",
        )
        self.assertTrue(result.ok)
        self.assertEqual(lines, ["A PROMPT"])

    def test_a_nonzero_exit_is_a_result_and_not_an_exception(self) -> None:
        """`run`'s contract, unchanged: a failed process is data. `bessemer.passes` retries
        on it, and an exception there would be a `try` around every attempt."""
        result, _ = self.consumed(python("raise SystemExit(124)"))
        self.assertFalse(result.ok)
        self.assertEqual(result.returncode, 124)

    def test_the_childs_stderr_is_merged_into_the_stream(self) -> None:
        """One pipe, one reader — see the docstring. A docker or provider error lands in the
        run log in order rather than in a second channel nobody drains."""
        _, lines = self.consumed(
            python("import sys", "print('out')", "sys.stderr.write('boom\\n')")
        )
        self.assertIn("out", lines)
        self.assertIn("boom", lines)

    def test_the_result_carries_no_output(self) -> None:
        """The transcript went to the consumer as it arrived and is not held here."""
        result, lines = self.consumed(python("print('rendered')"))
        self.assertEqual(lines, ["rendered"])
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_idle_fires_while_the_child_is_still_running(self) -> None:
        beats = 0

        def idle() -> None:
            nonlocal beats
            beats += 1

        result, _ = self.consumed(
            python("import time", "time.sleep(0.3)", "print('slow')"), idle=idle
        )
        self.assertTrue(result.ok)
        self.assertGreaterEqual(beats, 1)

    def test_a_line_reaches_the_consumer_before_the_child_exits(self) -> None:
        """The whole reason this is not `run`: a pass takes minutes and its log is read while
        it runs. Timed rather than asserted about buffering, because buffering is what would
        break it."""
        arrivals: list[float] = []

        def consume(handle: Iterable[str]) -> None:
            for _ in handle:
                arrivals.append(time.monotonic())

        proc.streamed(
            python(
                "import sys, time",
                "print('early', flush=True)",
                "time.sleep(0.3)",
                "print('late', flush=True)",
            ),
            stdin_text="",
            consume=consume,
            idle=lambda: None,
            poll=self.POLL,
        )
        self.assertEqual(len(arrivals), 2)
        self.assertGreaterEqual(arrivals[1] - arrivals[0], 0.15)

    def test_a_prompt_larger_than_a_pipe_buffer_does_not_deadlock(self) -> None:
        """Why the write happens off the main thread. A prompt bigger than the kernel's pipe
        buffer blocks its writer until the child reads, and the child here writes first —
        which is a deadlock in any arrangement that writes stdin before reading stdout."""
        prompt = "x" * (512 * 1024)
        result, lines = self.consumed(
            python(
                "import sys",
                "print('y' * 200_000, flush=True)",
                "print(len(sys.stdin.read()))",
            ),
            stdin_text=prompt,
        )
        self.assertTrue(result.ok)
        self.assertEqual(lines[-1], str(len(prompt)))

    def test_a_consumer_that_raises_is_re_raised_and_does_not_wedge_the_child(self) -> None:
        """An exception on a thread otherwise reaches nothing but `sys.unraisablehook`, and a
        consumer that stopped draining would block the child on a full pipe forever."""

        def consume(handle: Iterable[str]) -> None:
            next(iter(handle))
            raise RuntimeError("the log went away")

        with self.assertRaises(RuntimeError) as caught:
            proc.streamed(
                python("for n in range(20_000): print(n)"),
                stdin_text="",
                consume=consume,
                idle=lambda: None,
                poll=self.POLL,
            )
        self.assertIn("the log went away", str(caught.exception))

    def test_a_string_argv_is_refused(self) -> None:
        """`run`'s guard, at the second entry point: a `str` is a `Sequence[str]`."""
        erased: Callable[..., object] = proc.streamed
        with self.assertRaises(TypeError):
            erased(
                "echo hi", stdin_text="", consume=lambda lines: None, idle=lambda: None, poll=1
            )

    def test_there_is_no_host_side_timeout(self) -> None:
        """The absence is the decision, so it is pinned rather than left to be added back.

        A host-side kill would end the `docker exec` client and leave the agent running in the
        container, wedging it for every later exec. The deadline is `bessemer.passes`'
        in-container `timeout`.
        """
        self.assertNotIn("timeout", inspect.signature(proc.streamed).parameters)


class ResultTest(unittest.TestCase):
    def result(self, returncode: int) -> Result:
        return Result(argv=("git", "status"), returncode=returncode, stdout="", stderr="")

    def test_ok_is_exit_zero(self) -> None:
        self.assertTrue(self.result(0).ok)
        self.assertFalse(self.result(1).ok)

    def test_there_is_no_bool(self) -> None:
        """`if result:` reads as "did I get a result" and would mean the opposite of
        `.ok`. Asserted against the class dictionary, because inheriting object's default
        truthiness is exactly the state being required."""
        self.assertNotIn("__bool__", vars(Result))

    def test_there_is_no_length_either(self) -> None:
        """The other way a truth value gets invented: `bool(x)` falls back to `__len__`,
        so adding one would reopen `if result:` without anyone writing `__bool__`."""
        self.assertNotIn("__len__", vars(Result))

    def test_a_failed_result_is_still_truthy(self) -> None:
        """The consequence, pinned: `if result:` is useless here, which is the point."""
        self.assertTrue(bool(self.result(1)))


def failed_push() -> Result:
    """A push that failed with a credential-bearing remote URL in argv *and* in stderr.

    Both channels carry it because both are ways it actually happens: git echoes the URL it
    could not reach, and the URL was on the command line to begin with. A fixture that
    carried it in only one of them would let a policy that guards one and forgets the other
    pass.
    """
    return Result(
        argv=("git", "push", CREDENTIAL_URL, "HEAD:bessemer/f3-dispatch"),
        returncode=128,
        stdout="",
        stderr=(
            f"fatal: unable to access '{CREDENTIAL_URL}': the remote hung up\n"
            "hint: a paragraph of advice for an interactive user\n"
        ),
    )


class DestinationTest(unittest.TestCase):
    """The destination table (ADR 0003, F3 issue 02), restated by hand.

    The table is the owned literal: two destinations, and what each may carry. A third one
    arriving — "the container log", "the ledger" — must be a deliberate edit here as well as
    in `proc.py`, because that is the moment somebody decides who is allowed to read stderr.
    Derived from `Destination` instead, this test would ratify whatever was added.
    """

    def test_there_are_exactly_two_destinations(self) -> None:
        self.assertEqual(
            [(member.name, member.value) for member in Destination],
            [("HOST_LOG", "host log"), ("AGENT_VISIBLE", "agent-visible")],
        )

    def test_the_destination_has_no_default(self) -> None:
        """So the permissive row cannot be reached by omission. The message is asserted, not
        merely the exception type: `TypeError` is also what a misspelled keyword raises, and
        a test that took either would stay green on a signature that had grown a default and
        started failing for some other reason entirely."""
        # Erases mypy's `call-arg`: omitting a required keyword is the case being pinned.
        erased: Callable[..., object] = quote
        with self.assertRaises(TypeError) as caught:
            erased(failed_push())
        self.assertIn("destination", str(caught.exception))

    def test_the_destination_is_keyword_only(self) -> None:
        """Positional would let a call site pick a reader by argument order — and put the two
        rows one comma apart."""
        # Erases mypy's `too-many-positional-arguments`, which is the assertion.
        erased: Callable[..., object] = quote
        with self.assertRaises(TypeError) as caught:
            erased(failed_push(), Destination.HOST_LOG)
        self.assertIn("positional", str(caught.exception))


class HostLogQuotingTest(unittest.TestCase):
    """Row one: argv, returncode, and stderr after `redacted` + `DETAIL_LIMIT`."""

    def test_the_whole_row_is_one_literal(self) -> None:
        self.assertEqual(
            quote(failed_push(), destination=Destination.HOST_LOG),
            f"['git', 'push', '{REDACTED_URL}', 'HEAD:bessemer/f3-dispatch'] exited 128: "
            f"fatal: unable to access '{REDACTED_URL}': the remote hung up",
        )

    def test_a_credential_in_stderr_arrives_redacted_rather_than_dropped(self) -> None:
        """The operator is the one reader who needs the failure text, so it is quoted — but
        the run log is a file that outlives the console, so it is quoted redacted."""
        line = quote(failed_push(), destination=Destination.HOST_LOG)
        self.assertNotIn(TOKEN, line)
        self.assertIn("<redacted>@", line)
        self.assertIn("the remote hung up", line)

    def test_argv_is_redacted_too_rather_than_only_stderr(self) -> None:
        """Stricter than the table's row, deliberately: the table permits argv, and a remote
        URL rides in an argument as readily as in git's echo of it. Quoting argv raw would
        put the token in the log by the other channel."""
        line = quote(failed_push(), destination=Destination.HOST_LOG)
        self.assertNotIn(TOKEN, line)
        self.assertIn("'git', 'push'", line)

    def test_only_the_first_line_of_stderr_is_quoted(self) -> None:
        """Narrower than "stderr", and pinned so the narrowing is visible: `redact.detail`'s
        rule is that the later lines of a git or docker failure are advice aimed at an
        interactive user. Reached through the shared helper rather than restated here."""
        self.assertNotIn("hint:", quote(failed_push(), destination=Destination.HOST_LOG))

    def test_the_quoted_stderr_is_capped(self) -> None:
        """`DETAIL_LIMIT` reached through `redact`, not a second cap: a screen of git advice
        in a log line is the thing that stops being read."""
        result = Result(argv=("git", "status"), returncode=1, stdout="", stderr="x" * 500)
        line = quote(result, destination=Destination.HOST_LOG)
        self.assertEqual(line, f"['git', 'status'] exited 1: {'x' * redact.DETAIL_LIMIT}")

    def test_a_silent_failure_says_so_rather_than_ending_in_a_colon(self) -> None:
        result = Result(argv=("git", "status"), returncode=1, stdout="", stderr="  \n")
        self.assertEqual(
            quote(result, destination=Destination.HOST_LOG),
            "['git', 'status'] exited 1: <no stderr>",
        )


class AgentVisibleQuotingTest(unittest.TestCase):
    """Row two: the program name and the returncode. Nothing else, from anywhere.

    A pull request body, a notification and an assembled prompt are one destination because
    the reader is the same — someone outside this machine, including the agent, whose whole
    input is attacker-adjacent text.
    """

    def test_the_whole_row_is_one_literal(self) -> None:
        self.assertEqual(
            quote(failed_push(), destination=Destination.AGENT_VISIBLE), "git exited 128"
        )

    def test_no_fragment_of_stderr_reaches_an_agent_visible_destination(self) -> None:
        """**The named test.** Not "no token": a redacted URL would satisfy that while still
        forwarding another program's failure text to a reader the module docstring forbids.
        Every fragment of the fixture's stderr is asserted absent, including the redacted
        shape, so widening the agent-visible arm to quote stderr — redacted or not — is red.
        """
        line = quote(failed_push(), destination=Destination.AGENT_VISIBLE)
        for fragment in (
            TOKEN,
            CREDENTIAL_URL,
            REDACTED_URL,
            "<redacted>",
            "github.com",
            "fatal",
            "unable to access",
            "the remote hung up",
            "hint",
        ):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, line)

    def test_argv_arguments_never_reach_an_agent_visible_destination(self) -> None:
        """The second half of the row, and the one a reader is likeliest to think harmless:
        a remote URL rides in an argument too, so "argv" is not quotable here even though
        `git push` looks like public information."""
        line = quote(failed_push(), destination=Destination.AGENT_VISIBLE)
        for fragment in ("push", CREDENTIAL_URL, REDACTED_URL, "bessemer/f3-dispatch"):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, line)

    def test_the_program_is_named_rather_than_pathed(self) -> None:
        """The name, not argv[0] whole: an absolute host path tells an outside reader about
        the operator's filesystem and tells them nothing about the failure."""
        result = Result(
            argv=("/opt/homebrew/bin/git", "status"), returncode=1, stdout="", stderr=""
        )
        self.assertEqual(quote(result, destination=Destination.AGENT_VISIBLE), "git exited 1")

    def test_an_argv_with_no_usable_program_is_named_rather_than_blank(self) -> None:
        """`run` cannot produce any of these, but the tier-2 double constructs `Result`s by
        hand — and a composition site that raises while composing a failure notification
        loses the failure it was reporting.

        The last two cases are why the fallback cannot be a truthiness check on the argv
        tuple: the tuple is non-empty and the *basename* is what comes back empty, which
        renders as a line beginning " exited 1" — the same defect wearing a space.
        """
        for argv in ((), ("",), ("/",)):
            with self.subTest(argv=argv):
                result = Result(argv=argv, returncode=1, stdout="", stderr="")
                self.assertEqual(
                    quote(result, destination=Destination.AGENT_VISIBLE),
                    "<no program> exited 1",
                )


class OneRedactorTest(unittest.TestCase):
    """The policy wraps `bessemer.redact`; it does not grow a second copy of it.

    `redact.py`'s contract is to import nothing from the package, which is why the policy
    lives here and not there — and why the temptation from here is to inline the regex.
    """

    def test_proc_uses_the_shared_redactor_module(self) -> None:
        # Through `vars()` rather than attribute access: mypy refuses a read of a name a
        # module imported but does not re-export, which is exactly what is being asserted.
        self.assertIs(vars(proc)["redact"], redact)

    def test_proc_defines_no_redactor_of_its_own(self) -> None:
        self.assertFalse(hasattr(proc, "_CREDENTIAL_IN_URL"))
        self.assertFalse(hasattr(proc, "_redact"))
        self.assertFalse(hasattr(proc, "DETAIL_LIMIT"))

    def test_the_quoted_stderr_is_exactly_what_the_shared_helper_returns(self) -> None:
        """One definition, asserted by behaviour and not only by import: a second regex that
        agreed today would still be free to disagree tomorrow, and the one that disagrees
        silently is the one printing into a pull request body."""
        pushed = failed_push()
        self.assertIn(
            redact.detail(pushed.stderr), quote(pushed, destination=Destination.HOST_LOG)
        )


if __name__ == "__main__":
    unittest.main()
