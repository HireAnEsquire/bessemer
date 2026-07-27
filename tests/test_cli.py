"""Tests for the CLI surface: what it exposes, and what it reports as its version."""

import argparse
import contextlib
import importlib.metadata
import io
import unittest

from bessemer import cli


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
    def test_doctor_exits_zero_and_prints_nothing(self) -> None:
        code, out, err = run_cli("doctor")
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertEqual(err, "")


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
