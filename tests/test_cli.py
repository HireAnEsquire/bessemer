"""Tests for the CLI surface: what it exposes, what it prints, and what it exits with.

`CmdStatusTest` holds the printing halves of upstream's `CmdStatusTests`, split per
decision 5 of the F2 README; the computation halves are in `tests/test_status.py`.
"""

import argparse
import contextlib
import importlib.metadata
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bessemer import cli, doctor, proc
from bessemer.doctor import CheckResult
from tests.port_manifest import ported_from


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


class CmdStatusTest(unittest.TestCase):
    """The printing half of upstream's `CmdStatusTests` — what `bessemer status` writes and
    exits with. The computation half of each test is in `tests/test_status.py`, per
    decision 5 of the F2 README.

    Two seams are replaced, and each stands in for a boundary the rewrite moved.
    `cli._docker_rows` is the counterpart of upstream's stdin: `run.sh` gathered `docker ps`
    rows in bash and piped them in, bessemer's CLI gathers them itself, and `tests/guard.py`
    denies `docker` to the suite — so the gather's *result* is supplied, exactly as upstream
    supplied stdin's. `cli._start` is the working directory, patched because the adapter
    these tests build lives in a temporary directory rather than wherever the runner runs.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".bessemer" / "specs").mkdir(parents=True)

    def run_status(self, *, rows: list[str], docker_down: bool) -> tuple[int, str, str]:
        with (
            mock.patch.object(cli, "_docker_rows", return_value=(rows, docker_down)),
            mock.patch.object(cli, "_start", return_value=self.root),
        ):
            return run_cli("status")

    @ported_from("CmdStatusTests", "test_reads_docker_rows_from_stdin")
    def test_the_rows_the_gather_returned_are_printed(self) -> None:
        code, out, _ = self.run_status(
            rows=["bessemer-my-branch\tUp 1 minute"], docker_down=False
        )

        self.assertEqual(code, 0)
        self.assertIn("my-branch", out)

    @ported_from("CmdStatusTests", "test_docker_down_does_not_read_stdin")
    def test_a_down_daemon_prints_the_unavailable_line_and_no_rows(self) -> None:
        code, out, _ = self.run_status(
            rows=["bessemer-my-branch\tUp 1 minute"], docker_down=True
        )

        self.assertEqual(code, 0)
        self.assertIn("docker unavailable", out)
        self.assertNotIn("my-branch", out)

    @ported_from("CmdStatusTests", "test_exit_code_zero_on_empty_state")
    def test_an_empty_state_prints_both_sections_and_exits_zero(self) -> None:
        code, out, _ = self.run_status(rows=[], docker_down=False)

        self.assertEqual(code, 0)
        self.assertIn("no running tasks", out)
        self.assertIn("no runs recorded", out)

    def test_no_adapter_refuses_on_stderr_before_asking_the_daemon(self) -> None:
        """Bessemer's own: with no `.bessemer/` there is no ledger to report on, so status
        refuses with the loader's reason and exits 2. `_docker_rows` is deliberately left
        unpatched — the refusal must come before any gather, and if it did not,
        `tests/guard.py` would fail this test by refusing the `docker` spawn."""
        bare_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(bare_tmp.cleanup)
        with mock.patch.object(cli, "_start", return_value=Path(bare_tmp.name)):
            code, out, err = run_cli("status")

        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("no .bessemer/ directory found", err)
        self.assertIn("hint:", err)


class DockerGatherTest(unittest.TestCase):
    """Bessemer's own: `_docker_rows`'s failure collapse, driven through the subcommand.

    No upstream oracle exists for this — the port source's gather was bash
    (`docker ps ... || DOCKER_DOWN=1` in `run.sh`), so its collapse was never a python
    test's to assert. `CmdStatusTest` above replaces `_docker_rows` wholesale, which
    leaves the body unexercised; here `bessemer.proc.run` itself is replaced — the guard
    denies `docker` to the suite — one test per way of getting no answer, each asserting
    the unavailable line actually prints and the healthy blank does not.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".bessemer" / "specs").mkdir(parents=True)

    def run_status_with_proc(self, run: mock.Mock) -> tuple[int, str, str]:
        with (
            mock.patch.object(proc, "run", run),
            mock.patch.object(cli, "_start", return_value=self.root),
        ):
            return run_cli("status")

    def assert_down_rendering(self, code: int, out: str) -> None:
        """The one right answer for every arm: exit 0, the honest line, and neither the
        healthy blank nor a row — `no running tasks` on a down daemon is a status view
        claiming knowledge it could not have."""
        self.assertEqual(code, 0)
        self.assertIn("docker unavailable", out)
        self.assertNotIn("no running tasks", out)
        self.assertNotIn("my-branch", out)

    def test_a_nonzero_docker_ps_is_docker_down(self) -> None:
        run = mock.Mock(
            return_value=proc.Result(
                argv=cli.DOCKER_PS_ARGV,
                returncode=1,
                stdout="bessemer-my-branch\tUp 1 minute\n",
                stderr="Cannot connect to the Docker daemon",
            )
        )

        code, out, _ = self.run_status_with_proc(run)

        self.assert_down_rendering(code, out)
        run.assert_called_once_with(cli.DOCKER_PS_ARGV, timeout=cli.DOCKER_PS_TIMEOUT_SECONDS)

    def test_a_docker_that_cannot_be_run_is_docker_down(self) -> None:
        run = mock.Mock(side_effect=OSError("No such file or directory"))

        code, out, _ = self.run_status_with_proc(run)

        self.assert_down_rendering(code, out)

    def test_a_docker_ps_that_hangs_is_docker_down(self) -> None:
        run = mock.Mock(side_effect=proc.TimeoutExpired(list(cli.DOCKER_PS_ARGV), 15.0))

        code, out, _ = self.run_status_with_proc(run)

        self.assert_down_rendering(code, out)


class SurfaceTest(unittest.TestCase):
    def test_the_surface_is_exactly_doctor_and_status(self) -> None:
        """A subcommand that exists but does nothing is a lie about what bessemer does.

        Hand-written, so growing the surface costs a deliberate edit here: `{doctor}` since
        issue 06 of F1, `{doctor, status}` since issue 04 of F2 — the two commands a human
        types today, and nothing else."""
        self.assertEqual(subcommand_names(), ["doctor", "status"])

    def test_help_lists_every_subcommand(self) -> None:
        code, out, _ = run_cli("--help")
        self.assertEqual(code, 0)
        self.assertIn("doctor", out)
        self.assertIn("status", out)

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
