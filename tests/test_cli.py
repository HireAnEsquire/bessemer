"""Tests for the CLI surface: what it exposes, and what it reports as its version."""

import argparse
import contextlib
import importlib.metadata
import io
import os
import unittest
from pathlib import Path
from unittest import mock

from bessemer import cli, doctor
from bessemer.doctor import CheckResult


def run_cli(*argv: str) -> tuple[int, str, str]:
    """Run `main` with `argv`, returning (exit code, stdout, stderr).

    Normalises argparse's `SystemExit` — raised by `--version`, `--help`, and usage
    errors — into an exit code, so every case reads the same way.
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exit_:
            code = exit_.code if isinstance(exit_.code, int) else 0
    return code, out.getvalue(), err.getvalue()


def subcommand_names() -> list[str]:
    """The subcommands the parser accepts.

    Reaches into `_actions` because argparse exposes no public accessor for its
    subparsers. The alternative — scraping `--help` — would test the help text's
    formatting rather than the parser's actual surface.
    """
    for action in cli.build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return list(action.choices)
    return []


class VersionTest(unittest.TestCase):
    def test_reports_the_installed_version(self) -> None:
        """Asserted against distribution metadata, which is the only version of record."""
        code, out, _ = run_cli("--version")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), importlib.metadata.version("bessemer"))

    def test_version_output_is_bare_for_scripting(self) -> None:
        _, out, _ = run_cli("--version")
        self.assertNotIn("bessemer", out)


class DoctorTest(unittest.TestCase):
    """The subcommand's wiring: what it prints, and what it exits with.

    `run_checks` is replaced rather than run. Two of doctor's checks spawn `uv` and `docker`,
    which `tests/guard.py` deliberately does not allowlist — so the real checks are driven
    through their own seam in `tests/test_doctor.py`, and what is left to prove here is that
    the subcommand renders every line it is given and returns the exit code it is told.
    """

    def report(self, *results: CheckResult) -> list[CheckResult]:
        return list(results)

    def test_doctor_prints_one_line_per_check_and_exits_zero_when_all_pass(self) -> None:
        report = self.report(
            CheckResult(name="uv", status="ok", message="uv 0.9.2"),
            CheckResult(name="docker", status="ok", message="daemon responding"),
        )
        with mock.patch.object(doctor, "run_checks", return_value=report):
            code, out, err = run_cli("doctor")
        self.assertEqual(code, 0)
        self.assertEqual(
            out.splitlines(),
            ["ok    uv         uv 0.9.2", "ok    docker     daemon responding"],
        )
        self.assertEqual(err, "")

    def test_doctor_exits_one_when_a_check_fails_and_prints_its_hint(self) -> None:
        report = self.report(
            CheckResult(name="docker", status="FAIL", message="daemon down", hint="start it")
        )
        with mock.patch.object(doctor, "run_checks", return_value=report):
            code, out, _ = run_cli("doctor")
        self.assertEqual(code, 1)
        self.assertIn("FAIL  docker     daemon down", out)
        self.assertIn("hint: start it", out)

    def test_the_context_carries_the_process_environment_and_one_directory(self) -> None:
        """`env` and `start` are fields rather than reads inside a check, and this is the one
        site that supplies them — a check reading `os.environ` or `Path.cwd()` itself could
        only be tested by mutating the runner's own environment or working directory.

        `start` is resolved here rather than left as `None`: three operations each calling
        `Path.cwd()` do agree, but root agreement is the check whose job is not to rest on
        two things agreeing by coincidence."""
        with mock.patch.object(doctor, "run_checks", return_value=[]) as run_checks:
            run_cli("doctor")
        context = run_checks.call_args.args[0]
        self.assertIs(context.env, os.environ)
        self.assertEqual(context.start, Path.cwd())

    def test_a_deleted_working_directory_is_reported_rather_than_raised(self) -> None:
        """`Path.cwd()` raises `OSError` when the directory has been removed under a live
        shell. Doctor is the command someone runs *because* things are broken, so that must
        reach the operations as `None` — where `bessemer.resolve` has a reason and a hint for
        it — rather than as a traceback out of the entry point."""
        with mock.patch.object(Path, "cwd", side_effect=OSError("gone")):
            with mock.patch.object(doctor, "run_checks", return_value=[]) as run_checks:
                code, _, _ = run_cli("doctor")
        self.assertEqual(code, 0)
        self.assertIsNone(run_checks.call_args.args[0].start)


class SurfaceTest(unittest.TestCase):
    def test_doctor_is_the_only_subcommand(self) -> None:
        """A subcommand that exists but does nothing is a lie about what bessemer does."""
        self.assertEqual(subcommand_names(), ["doctor"])

    def test_help_lists_doctor(self) -> None:
        code, out, _ = run_cli("--help")
        self.assertEqual(code, 0)
        self.assertIn("doctor", out)

    def test_no_subcommand_prints_usage_and_exits_two(self) -> None:
        """F5's picker will claim this slot; this test is expected to change then."""
        code, _, err = run_cli()
        self.assertEqual(code, 2)
        self.assertIn("usage: bessemer", err)

    def test_unknown_subcommand_is_a_usage_error(self) -> None:
        code, _, err = run_cli("teleport")
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", err)


if __name__ == "__main__":
    unittest.main()
