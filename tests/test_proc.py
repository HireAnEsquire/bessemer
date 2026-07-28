"""Tests for the subprocess wrapper: what it returns, what it refuses, and what it leaks.

Every child spawned here is `sys.executable`, which `tests/guard.py` allowlists. The
static half of this issue — that no other module may spawn at all — is
`tests/test_argv_boundary.py`.
"""

import contextlib
import os
import pty
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Final

from bessemer.proc import ProcessError, Result, run, run_checked

TIMEOUT: Final = 30
"""Generous, because it is a backstop and not the thing under test. The one test that is
about the timeout sets its own."""


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


if __name__ == "__main__":
    unittest.main()
