"""The port of the port source's status tests, plus what bessemer had to write itself.

Every test carrying a `ported_from` marker is a counterpart of one upstream test in
`.agentbox/test_tasklib.py` at `e194121f75f4`, and `tests/port_manifest.py` names it back.
The assertions are upstream's, kept whole; only the names, the grouping into classes and
the entry points changed — and one fixture value, deliberately: upstream's container rows
say `agentbox-*` and bessemer's say `bessemer-*`, the product-name rename
`bessemer/status.py`'s docstring records. Upstream's `CmdStatusTests` land split (decision
5 of the F2 README): the computation half of each is here, the printing half in
`tests/test_cli.py`.

The unmarked tests are bessemer's own, and they exist for four reasons:

- **`bessemer/status.py` spawns no subprocess**, which is this issue's criterion rather
  than an upstream property — `tasklib.py` was one module that shelled out freely.
  `PurityTest` proves it, with one named allowance: `os.kill(pid, 0)` is a signal send,
  not a spawn.
- **No `render_*` function prints.** `RenderContractTest` asserts the lines come back as
  values and stdout stays empty — the ADR 0002 boundary this module's docstring argues.
- **The no-drift invariant is asserted, not just arranged.** `_resume_run_label`'s
  docstring claims it renders through the same `_overall_outcome`/`_age` the recent-runs
  table uses. `NoDriftTest` replaces each shared helper and demands its sentinel in both
  views, so either caller growing a private copy is a red test rather than a stale
  sentence.
- **`mtime_age` has no upstream test of its own** — upstream only reached it through gc's
  `CollectGcItemsTests`, which are issue 05's — so bessemer pins its two behaviours here,
  unmarked, because no upstream test covers them separately and the manifest must not
  claim one does.
"""

import ast
import contextlib
import io
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from bessemer import ledger, proc, status
from bessemer.issues import materialize_ad_hoc
from tests.port_manifest import ported_from
from tests.test_argv_boundary import violations
from tests.test_issues import write_issue

MODULE_PATH = "bessemer/status.py"

WRAPPER_MODULE = proc.__name__
"""`bessemer.proc`, spelled from the module rather than as a literal so a rename fails."""


class ParseDockerRowsTest(unittest.TestCase):
    @ported_from("ParseDockerRowsTests", "test_filters_to_agentbox_prefixed_containers")
    def test_only_containers_with_the_bessemer_prefix_come_back(self) -> None:
        rows = ["bessemer-my-branch\tUp 5 minutes", "some-other-container\tUp 1 hour"]

        containers = status.parse_docker_rows(rows)

        self.assertEqual([c.slug for c in containers], ["my-branch"])
        self.assertEqual(containers[0].uptime, "Up 5 minutes")

    @ported_from("ParseDockerRowsTests", "test_skips_blank_lines")
    def test_blank_lines_are_skipped(self) -> None:
        self.assertEqual(
            status.parse_docker_rows(["", "   ", "bessemer-x\tUp 1 second"]),
            [status.ContainerInfo(slug="x", uptime="Up 1 second")],
        )

    @ported_from("ParseDockerRowsTests", "test_row_with_no_status_field_is_tolerated")
    def test_a_row_with_no_status_field_is_tolerated(self) -> None:
        containers = status.parse_docker_rows(["bessemer-x"])

        self.assertEqual(containers, [status.ContainerInfo(slug="x", uptime="")])

    @ported_from("ParseDockerRowsTests", "test_empty_input_returns_empty")
    def test_empty_input_parses_to_no_containers(self) -> None:
        self.assertEqual(status.parse_docker_rows([]), [])


class StaleLocksTest(unittest.TestCase):
    """`stale_locks` is `orphan_locks` now (ADR 0004, issue 13c): the container has nothing
    to do with a lock's orphan status, only its pid does, so every fixture below drives that
    signal through a mocked `pid_alive` rather than a `running_slugs` set that no longer
    exists."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.locks_dir = Path(self.tmp.name)

    @ported_from("StaleLocksTests", "test_lock_with_no_matching_container_is_stale")
    def test_a_lock_with_no_matching_container_is_stale(self) -> None:
        (self.locks_dir / "orphaned.pid").write_text("123")

        with mock.patch.object(status, "pid_alive", return_value=False):
            self.assertEqual(status.orphan_locks(self.locks_dir), ["orphaned"])

    @ported_from("StaleLocksTests", "test_lock_with_matching_container_is_not_stale")
    def test_a_lock_with_a_matching_container_is_not_stale(self) -> None:
        (self.locks_dir / "live.pid").write_text("123")

        with mock.patch.object(status, "pid_alive", return_value=True):
            self.assertEqual(status.orphan_locks(self.locks_dir), [])

    @ported_from("StaleLocksTests", "test_missing_locks_dir_returns_empty")
    def test_a_missing_locks_directory_has_no_stale_locks(self) -> None:
        self.assertEqual(status.orphan_locks(self.locks_dir / "does-not-exist"), [])

    @ported_from("StaleLocksTests", "test_results_are_sorted")
    def test_the_results_come_back_sorted(self) -> None:
        (self.locks_dir / "zeta.pid").write_text("1")
        (self.locks_dir / "alpha.pid").write_text("2")

        with mock.patch.object(status, "pid_alive", return_value=False):
            self.assertEqual(status.orphan_locks(self.locks_dir), ["alpha", "zeta"])


class OverallOutcomeTest(unittest.TestCase):
    @ported_from("OverallOutcomeTests", "test_one_off_approved_outcome")
    def test_a_one_off_approved_outcome_renders_with_a_tick(self) -> None:
        self.assertEqual(status._overall_outcome({"outcome": "approved"}), "✅ approved")

    @ported_from("OverallOutcomeTests", "test_one_off_needs_work_outcome")
    def test_a_one_off_needs_work_outcome_renders_with_a_warning(self) -> None:
        self.assertEqual(status._overall_outcome({"outcome": "needs-work"}), "⚠️ needs-work")

    @ported_from("OverallOutcomeTests", "test_feature_all_issues_approved")
    def test_a_feature_with_every_issue_approved_is_approved(self) -> None:
        record = {"issues": {"01": "approved", "02": "approved"}}
        self.assertEqual(status._overall_outcome(record), "✅ approved")

    @ported_from("OverallOutcomeTests", "test_feature_partial_approval")
    def test_a_partially_approved_feature_shows_the_fraction(self) -> None:
        record = {"issues": {"01": "approved", "02": "needs-work"}}
        self.assertEqual(status._overall_outcome(record), "⚠️ 1/2 approved")

    @ported_from("OverallOutcomeTests", "test_no_outcome_or_issues_is_dash")
    def test_no_outcome_and_no_issues_renders_as_a_dash(self) -> None:
        self.assertEqual(status._overall_outcome({}), "-")


class AgeTest(unittest.TestCase):
    """Upstream's shape, kept deliberately: every INPUT below is derived from the clock —
    now minus a known delta — and every EXPECTED value is a hand-written literal. The other
    way round would assert nothing: an expectation computed from the clock recomputes
    whatever the code did."""

    @ported_from("AgeTests", "test_missing_timestamp_is_unknown")
    def test_a_missing_timestamp_is_unknown(self) -> None:
        self.assertEqual(status._age(None), "?")

    @ported_from("AgeTests", "test_malformed_timestamp_is_unknown")
    def test_a_malformed_timestamp_is_unknown(self) -> None:
        self.assertEqual(status._age("not-a-timestamp"), "?")

    @ported_from("AgeTests", "test_recent_timestamp_is_seconds_ago")
    def test_a_recent_timestamp_renders_in_seconds(self) -> None:
        ts = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
        self.assertEqual(status._age(ts), "5s ago")

    @ported_from("AgeTests", "test_minutes_ago")
    def test_an_age_in_minutes_renders_in_minutes(self) -> None:
        ts = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        self.assertEqual(status._age(ts), "5m ago")

    @ported_from("AgeTests", "test_hours_ago")
    def test_an_age_in_hours_renders_in_hours(self) -> None:
        ts = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
        self.assertEqual(status._age(ts), "3h ago")

    @ported_from("AgeTests", "test_days_ago")
    def test_an_age_in_days_renders_in_days(self) -> None:
        ts = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        self.assertEqual(status._age(ts), "2d ago")


class MtimeAgeTest(unittest.TestCase):
    """Bessemer's own — see the module docstring for why `mtime_age` arrives untested."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_a_path_that_cannot_be_statted_is_unknown(self) -> None:
        self.assertEqual(status.mtime_age(Path(self.tmp.name) / "gone"), "?")

    def test_a_files_age_comes_from_its_mtime(self) -> None:
        # Same discipline as AgeTest: the INPUT is clock-derived (an mtime two minutes
        # back), the EXPECTED value is a literal.
        path = Path(self.tmp.name) / "aged"
        path.write_text("x")
        two_minutes_back = datetime.now().timestamp() - 120
        os.utime(path, (two_minutes_back, two_minutes_back))

        self.assertEqual(status.mtime_age(path), "2m ago")


class FormatTableTest(unittest.TestCase):
    @ported_from("FormatTableTests", "test_aligns_columns_and_leaves_last_unpadded")
    def test_columns_align_and_the_last_is_left_unpadded(self) -> None:
        lines = status.format_table(["A", "BB"], [["1", "22"], ["333", "4"]])

        self.assertEqual(lines, ["A    BB", "1    22", "333  4"])

    @ported_from("FormatTableTests", "test_truncates_only_marked_columns")
    def test_only_marked_columns_are_truncated(self) -> None:
        long_name = "x" * 50
        lines = status.format_table(
            ["BRANCH", "LOG"], [[long_name, "tail -f " + long_name]], truncate_cols={0}
        )

        branch_cell = lines[1].split("  ")[0]
        self.assertTrue(branch_cell.endswith("…"))
        self.assertIn("tail -f " + long_name, lines[1])


class RenderRunningTest(unittest.TestCase):
    """ADR 0004, issue 13c: liveness comes from `gc.classify_liveness`, so every fixture
    below that involves a lock drives its pid signal through a mocked `status.pid_alive`
    rather than a real one — reading `/proc` for a fixture's made-up pid text would be
    flaky, exactly the reason `tests/test_gc.py` mocks the same name on `gc`."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.logs_dir = Path(self.tmp.name) / "logs"
        self.locks_dir = Path(self.tmp.name) / "locks"
        self.locks_dir.mkdir(parents=True)

    @ported_from("RenderRunningTests", "test_docker_down_skips_containers_and_lock_check")
    def test_docker_down_skips_containers_and_the_lock_check(self) -> None:
        """Recorded divergence from the pin (ADR 0004): upstream's docker-down arm gave up
        on the section entirely. Bessemer's in-flight signal is the lock's pid, which needs
        no daemon, so a live lock still renders as running — from the locks alone — beside
        the one line naming what the down daemon actually costs."""
        (self.locks_dir / "my-branch.pid").write_text("1")

        with mock.patch.object(status, "pid_alive", return_value=True):
            lines = status.render_running([], True, self.logs_dir, self.locks_dir)

        joined = "\n".join(lines)
        self.assertIn("docker unavailable", joined)
        self.assertIn("in-flight runs below are read from the locks", joined)
        self.assertIn("my-branch", joined)
        self.assertIn(f"tail -f {self.logs_dir}/my-branch.log", joined)

    @ported_from("RenderRunningTests", "test_no_containers_says_so")
    def test_no_running_containers_says_so(self) -> None:
        lines = status.render_running([], False, self.logs_dir, self.locks_dir)

        self.assertIn("  no running tasks", lines)

    @ported_from(
        "RenderRunningTests", "test_live_container_row_includes_branch_uptime_and_tail_command"
    )
    def test_a_container_row_carries_branch_uptime_and_tail_command(self) -> None:
        (self.locks_dir / "my-branch.pid").write_text("1")

        with mock.patch.object(status, "pid_alive", return_value=True):
            lines = status.render_running(
                ["bessemer-my-branch\tUp 5 minutes"], False, self.logs_dir, self.locks_dir
            )

        joined = "\n".join(lines)
        self.assertIn("my-branch", joined)
        self.assertIn("Up 5 minutes", joined)
        self.assertIn(f"tail -f {self.logs_dir}/my-branch.log", joined)

    @ported_from("RenderRunningTests", "test_stale_lock_flagged_when_no_matching_container")
    def test_a_stale_lock_is_flagged_when_no_container_matches(self) -> None:
        (self.locks_dir / "orphan.pid").write_text("1")

        with mock.patch.object(status, "pid_alive", return_value=False):
            lines = status.render_running([], False, self.logs_dir, self.locks_dir)

        self.assertTrue(any("orphan: orphan" in line for line in lines))
        self.assertTrue(any("reclaim with: bessemer gc --force" in line for line in lines))

    @ported_from("RenderRunningTests", "test_lock_with_live_container_not_flagged")
    def test_a_lock_with_a_live_container_is_not_flagged(self) -> None:
        (self.locks_dir / "my-branch.pid").write_text("1")

        with mock.patch.object(status, "pid_alive", return_value=True):
            lines = status.render_running(
                ["bessemer-my-branch\tUp 5 minutes"], False, self.logs_dir, self.locks_dir
            )

        self.assertFalse(any("orphan" in line for line in lines))

    def test_an_up_container_with_a_dead_lock_pid_is_an_orphan_not_a_run(self) -> None:
        """The tracer's own scenario (ADR 0004, finding 1): a `kill -9`'d dispatcher leaves
        the container `Up` indefinitely (`sleep infinity`). `Up` is not proof of life, so
        this must not render as a running row — only as the marked orphan line naming the
        remedy."""
        (self.locks_dir / "tracer-dogfood.pid").write_text("999999")

        with mock.patch.object(status, "pid_alive", return_value=False):
            lines = status.render_running(
                ["bessemer-tracer-dogfood\tUp 10 minutes"], False, self.logs_dir, self.locks_dir
            )

        joined = "\n".join(lines)
        self.assertIn("  no running tasks", lines)
        self.assertNotIn("tail -f", joined)
        self.assertIn(
            "  ⚠ orphan: tracer-dogfood (Up 10 minutes, no live dispatcher owns it)"
            " — reclaim with: bessemer gc --force",
            lines,
        )

    def test_docker_down_still_lists_an_in_flight_run_from_its_lock_alone(self) -> None:
        lines = status.render_running([], True, self.logs_dir, self.locks_dir)

        self.assertEqual(len(lines), 2)
        self.assertIn("docker unavailable", lines[1])

        (self.locks_dir / "still-going.pid").write_text("1")
        with mock.patch.object(status, "pid_alive", return_value=True):
            lines = status.render_running([], True, self.logs_dir, self.locks_dir)

        joined = "\n".join(lines)
        self.assertIn("still-going", joined)
        self.assertIn(f"tail -f {self.logs_dir}/still-going.log", joined)

    def test_an_unreadable_lock_is_kept_and_said_out_loud_not_offered_for_reclaim(self) -> None:
        # A directory where a lock file is expected raises IsADirectoryError (an OSError) on
        # read_text — the same trick tests/test_gc.py uses for the same fact.
        (self.locks_dir / "mystery.pid").mkdir()

        lines = status.render_running([], False, self.logs_dir, self.locks_dir)

        joined = "\n".join(lines)
        self.assertIn("mystery", joined)
        self.assertIn("could not be read", joined)
        self.assertNotIn("reclaim with: bessemer gc --force", joined)


class RunningOperatorSentencesTest(unittest.TestCase):
    """Bessemer's own: the two operator lines issue 13c adds, hand-written whole —
    `test_reclaim.py`'s `test_the_..._sentence_is_the_pin` pattern. An assertion built from
    the constant it is checking cannot notice that constant changing, which is why every
    other test in this module that looks for these sentences in rendered output spells the
    words again rather than importing the name."""

    def test_the_orphan_remedy_sentence_is_the_pin(self) -> None:
        self.assertEqual(status._ORPHAN_REMEDY, "reclaim with: bessemer gc --force")

    def test_the_docker_down_running_sentence_is_the_pin(self) -> None:
        self.assertEqual(
            status._DOCKER_DOWN_RUNNING,
            "docker unavailable (daemon down?) — in-flight runs below are read from the"
            " locks; container uptime can't be shown",
        )


class RenderRecentTest(unittest.TestCase):
    @ported_from("RenderRecentTests", "test_no_records_says_so")
    def test_no_records_says_so(self) -> None:
        self.assertEqual(
            status.render_recent([], 10), ["Recent (last 10)", "  no runs recorded"]
        )

    @ported_from("RenderRecentTests", "test_respects_limit")
    def test_the_limit_is_respected(self) -> None:
        records = [
            {"timestamp": f"2026-07-2{n}T00:00:00+00:00", "branch": f"b{n}"} for n in range(5)
        ]

        lines = status.render_recent(records, 2)

        joined = "\n".join(lines)
        self.assertIn("b0", joined)
        self.assertIn("b1", joined)
        self.assertNotIn("b2", joined)

    @ported_from("RenderRecentTests", "test_row_includes_pr_url_unmodified")
    def test_a_row_carries_the_pull_request_url_unmodified(self) -> None:
        record = {
            "timestamp": "2026-07-22T00:00:00+00:00",
            "branch": "b",
            "pr_url": "https://github.com/org/repo/pull/123",
        }

        lines = status.render_recent([record], 10)

        self.assertIn("https://github.com/org/repo/pull/123", "\n".join(lines))

    @ported_from("RenderRecentTests", "test_missing_pr_url_shows_dash")
    def test_a_missing_pull_request_url_shows_a_dash(self) -> None:
        record = {"timestamp": "2026-07-22T00:00:00+00:00", "branch": "b"}

        lines = status.render_recent([record], 10)

        self.assertIn("-", "\n".join(lines))


class RenderStatusTest(unittest.TestCase):
    """The whole view over a ledger `append_ledger` wrote — the same round trip F2's tracer
    demands, inside the suite. Three of upstream's four tests: the fourth asserted the
    legacy migration decision 4 of the F2 README drops, and stays excluded."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.specs_dir = Path(self.tmp.name) / "specs"
        self.logs_dir = Path(self.tmp.name) / "logs"
        self.locks_dir = Path(self.tmp.name) / "locks"
        self.specs_dir.mkdir()
        self.logs_dir.mkdir()
        self.locks_dir.mkdir()

    def render(self, docker_rows: list[str], docker_down: bool) -> str:
        return status.render_status(
            specs_dir=self.specs_dir,
            logs_dir=self.logs_dir,
            locks_dir=self.locks_dir,
            docker_rows=docker_rows,
            docker_down=docker_down,
            limit=10,
        )

    @ported_from("RenderStatusTests", "test_empty_state_exits_cleanly_with_both_sections")
    def test_an_empty_state_renders_both_sections_cleanly(self) -> None:
        out = self.render([], False)

        self.assertIn("no running tasks", out)
        self.assertIn("no runs recorded", out)

    @ported_from("RenderStatusTests", "test_full_fixture_shows_running_and_recent_sections")
    def test_a_full_fixture_shows_running_and_recent_sections(self) -> None:
        ledger.append_ledger(
            ledger.central_ledger_path(self.specs_dir),
            {
                "timestamp": "2026-07-22T00:00:00+00:00",
                "branch": "my-feature",
                "base": "origin/master",
                "feature": "my-feature",
                "issues": {"01": "approved", "02": "needs-work"},
                "outcome": None,
                "pr_url": "https://github.com/org/repo/pull/9",
                "task_dir": str(self.specs_dir / "my-feature"),
            },
        )
        (self.locks_dir / "my-feature.pid").write_text("123")
        (self.locks_dir / "gone.pid").write_text("999")

        with mock.patch.object(status, "pid_alive", side_effect=lambda text: text == "123"):
            out = self.render(["bessemer-my-feature\tUp 10 minutes"], False)

        self.assertIn("my-feature", out)
        self.assertIn("Up 10 minutes", out)
        self.assertIn(f"tail -f {self.logs_dir}/my-feature.log", out)
        self.assertIn("orphan: gone", out)
        self.assertIn("01✓ 02✗", out)
        self.assertIn("https://github.com/org/repo/pull/9", out)

    @ported_from("RenderStatusTests", "test_docker_down_still_renders_recent")
    def test_docker_down_still_renders_recent(self) -> None:
        ledger.append_ledger(
            ledger.central_ledger_path(self.specs_dir),
            {"timestamp": "2026-07-22T00:00:00+00:00", "branch": "my-feature"},
        )

        out = self.render([], True)

        self.assertIn("docker unavailable", out)
        self.assertIn("my-feature", out)

    # --- the computation halves of upstream's CmdStatusTests (decision 5 of the F2 README:
    # `status` is a subcommand a human types, so each of its tests splits — what was
    # computed lands here, what was printed lands in tests/test_cli.py). Upstream's stdin
    # is the bash-to-python boundary the rewrite deletes; "the rows run.sh piped in" become
    # "the rows the caller handed over".

    @ported_from("CmdStatusTests", "test_reads_docker_rows_from_stdin")
    def test_docker_rows_handed_in_reach_the_running_table(self) -> None:
        (self.locks_dir / "my-branch.pid").write_text("123")

        with mock.patch.object(status, "pid_alive", return_value=True):
            out = self.render(["bessemer-my-branch\tUp 1 minute"], False)

        self.assertIn(f"tail -f {self.logs_dir}/my-branch.log", out)

    @ported_from("CmdStatusTests", "test_docker_down_does_not_read_stdin")
    def test_docker_down_ignores_rows_handed_in(self) -> None:
        out = self.render(["bessemer-my-branch\tUp 1 minute"], True)

        self.assertIn("docker unavailable", out)
        self.assertNotIn("my-branch", out)

    @ported_from("CmdStatusTests", "test_exit_code_zero_on_empty_state")
    def test_an_empty_state_is_an_answer_not_an_error(self) -> None:
        out = self.render([], False)

        self.assertIn("no running tasks", out)
        self.assertIn("no runs recorded", out)


class RenderContractTest(unittest.TestCase):
    """Bessemer's own: rendering returns values and never prints — the ADR 0002 line the
    module docstring argues, asserted."""

    def test_render_functions_return_lines_and_never_print(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        record = {"timestamp": "2026-07-22T00:00:00+00:00", "branch": "b"}

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            running = status.render_running([], False, root / "logs", root / "locks")
            recent = status.render_recent([record], 10)
            whole = status.render_status(
                specs_dir=root / "specs",
                logs_dir=root / "logs",
                locks_dir=root / "locks",
                docker_rows=[],
                docker_down=False,
                limit=10,
            )

        self.assertIsInstance(running, list)
        self.assertTrue(all(isinstance(line, str) for line in running))
        self.assertIsInstance(recent, list)
        self.assertTrue(all(isinstance(line, str) for line in recent))
        self.assertIsInstance(whole, str)
        self.assertEqual(out.getvalue(), "")


class IsLiveStatusTest(unittest.TestCase):
    @ported_from("IsLiveStatusTests", "test_up_prefix_is_live")
    def test_an_up_prefix_is_live(self) -> None:
        self.assertTrue(status.is_live_status("Up 5 minutes"))

    @ported_from("IsLiveStatusTests", "test_exited_is_not_live")
    def test_exited_is_not_live(self) -> None:
        self.assertFalse(status.is_live_status("Exited (0) 3 hours ago"))

    @ported_from("IsLiveStatusTests", "test_created_is_not_live")
    def test_created_is_not_live(self) -> None:
        self.assertFalse(status.is_live_status("Created"))


class PidAliveTest(unittest.TestCase):
    @ported_from("PidAliveTests", "test_current_process_is_alive")
    def test_the_current_process_is_alive(self) -> None:
        self.assertTrue(status.pid_alive(str(os.getpid())))

    @ported_from("PidAliveTests", "test_garbage_text_is_not_alive")
    def test_garbage_text_is_not_alive(self) -> None:
        self.assertFalse(status.pid_alive("not-a-pid"))

    @ported_from("PidAliveTests", "test_empty_text_is_not_alive")
    def test_empty_text_is_not_alive(self) -> None:
        self.assertFalse(status.pid_alive(""))

    @ported_from("PidAliveTests", "test_dead_pid_is_not_alive")
    def test_a_dead_pid_is_not_alive(self) -> None:
        with mock.patch("bessemer.status.os.kill", side_effect=ProcessLookupError):
            self.assertFalse(status.pid_alive("123"))

    @ported_from("PidAliveTests", "test_permission_error_counts_as_alive")
    def test_a_permission_error_counts_as_alive(self) -> None:
        # Can't signal a pid we don't own, but that means it exists.
        with mock.patch("bessemer.status.os.kill", side_effect=PermissionError):
            self.assertTrue(status.pid_alive("123"))


class ResumeRunLabelTest(unittest.TestCase):
    """Moved here from issue 03 with the function itself: `_resume_run_label` renders
    through `_overall_outcome` and `_age`, so it lives — and is tested — where they do."""

    @ported_from("ResumeRunLabelTests", "test_feature_run_with_recorded_outcome")
    def test_a_feature_run_with_a_recorded_outcome(self) -> None:
        # Upstream hardcodes the INPUT timestamp ("2026-07-22T00:00:00+00:00") against a
        # wall clock `_age` reads, so upstream's test asserts "(2d ago" only during the one
        # week it was written in — measured red upstream on 2026-07-29, where it renders
        # "(7d ago". Ported with the INPUT derived from the clock — now minus two days —
        # and the EXPECTED string exactly as upstream wrote it. A clock-derived EXPECTATION
        # would recompute whatever the code did and assert nothing; a clock-derived input
        # against upstream's hand-written expectation is what makes "two days old renders
        # as 2d ago" checkable on every day instead of one.
        timestamp = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        label = status._resume_run_label(
            {
                "branch": "rollout-readiness",
                "feature": "rollout-readiness",
                "timestamp": timestamp,
                "outcome": "approved",
            }
        )
        self.assertEqual(
            label, "rollout-readiness — feature rollout-readiness (2d ago, ✅ approved)"
        )

    @ported_from(
        "ResumeRunLabelTests", "test_spec_run_falls_back_to_pr_open_when_no_outcome_recorded"
    )
    def test_a_spec_run_with_no_outcome_falls_back_to_pr_open(self) -> None:
        label = status._resume_run_label(
            {
                "branch": "b1",
                "spec": "my-task.md",
                "timestamp": "2026-07-24T00:00:00+00:00",
                "pr_url": "https://github.com/x/y/pull/1",
            }
        )
        self.assertIn("spec my-task.md", label)
        self.assertIn("PR open", label)

    @ported_from(
        "ResumeRunLabelTests",
        "test_falls_back_to_no_pr_yet_when_neither_outcome_nor_pr_recorded",
    )
    def test_no_outcome_and_no_pr_falls_back_to_no_pr_yet(self) -> None:
        label = status._resume_run_label(
            {"branch": "b1", "feature": "f", "timestamp": "2026-07-24T00:00:00+00:00"}
        )

        self.assertIn("no PR yet", label)


class NoDriftTest(unittest.TestCase):
    """Bessemer's own: the shared rendering is asserted, not just arranged.

    `_resume_run_label`'s docstring claims it reuses `_overall_outcome`/`_age` — "the same
    rendering `status` uses" — so the picker label and the recent-runs table cannot drift.
    A shared helper two callers *happen* to use is one refactor away from two renderings,
    so each helper is replaced at the module and its sentinel demanded in **both** views:
    either caller growing a private copy stops calling the shared name, and the sentinel
    goes missing from its view."""

    RECORD = {
        "branch": "b1",
        "feature": "f",
        "timestamp": "2026-07-22T00:00:00+00:00",
        "outcome": "approved",
    }

    def test_both_views_render_the_outcome_through_the_shared_helper(self) -> None:
        with mock.patch.object(status, "_overall_outcome", return_value="OUTCOME-SENTINEL"):
            label = status._resume_run_label(dict(self.RECORD))
            table = "\n".join(status.render_recent([dict(self.RECORD)], 10))

        self.assertIn("OUTCOME-SENTINEL", label)
        self.assertIn("OUTCOME-SENTINEL", table)

    def test_both_views_render_the_age_through_the_shared_helper(self) -> None:
        with mock.patch.object(status, "_age", return_value="AGE-SENTINEL"):
            label = status._resume_run_label(dict(self.RECORD))
            table = "\n".join(status.render_recent([dict(self.RECORD)], 10))

        self.assertIn("AGE-SENTINEL", label)
        self.assertIn("AGE-SENTINEL", table)


class SummaryLinesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.specs_dir = Path(self.tmp.name)

    @ported_from("SummaryLinesTests", "test_feature_run_shows_source_issues_branch_base")
    def test_a_feature_run_shows_source_issues_branch_and_base(self) -> None:
        issues_dir = self.specs_dir / "01-feature" / "issues"
        issues_dir.mkdir(parents=True)
        write_issue(issues_dir, "01-a.md", status="needs-triage")
        write_issue(issues_dir, "02-b.md", status="needs-triage", blocked_by="01")

        lines = status._summary_lines(
            self.specs_dir,
            None,
            "01-feature",
            None,
            "01,02",
            "feature-a",
            False,
            "origin/master",
        )

        self.assertEqual(
            lines,
            [
                "Source: 01-feature",
                "Issues: 01, 02",
                "Branch: feature-a",
                "Base: origin/master",
            ],
        )

    @ported_from(
        "SummaryLinesTests", "test_feature_run_default_issue_selection_shows_all_label"
    )
    def test_a_default_issue_selection_shows_the_all_label(self) -> None:
        issues_dir = self.specs_dir / "01-feature" / "issues"
        issues_dir.mkdir(parents=True)
        write_issue(issues_dir, "01-a.md", status="Done")
        write_issue(issues_dir, "02-b.md", status="needs-triage")
        write_issue(issues_dir, "03-c.md", status="needs-triage")

        lines = status._summary_lines(
            self.specs_dir, None, "01-feature", None, "", "feature-a", False, "origin/master"
        )

        self.assertEqual(lines[1], "Issues: all unfinished (02, 03)")

    @ported_from("SummaryLinesTests", "test_one_off_spec_shows_spec_path_and_no_issues_line")
    def test_a_one_off_spec_shows_its_path_and_no_issues_line(self) -> None:
        (self.specs_dir / "my-task.md").write_text("# task\n")

        lines = status._summary_lines(
            self.specs_dir, "my-task", None, None, "", "feature-a", False, "origin/master"
        )

        self.assertEqual(lines, ["Source: my-task", "Branch: feature-a", "Base: origin/master"])

    @ported_from("SummaryLinesTests", "test_ad_hoc_prompt_shows_materialized_filename")
    def test_an_ad_hoc_prompt_shows_its_materialized_filename(self) -> None:
        spec = materialize_ad_hoc(
            self.specs_dir, "Add a thing", "feature-a", timestamp="20260722-120000"
        )

        lines = status._summary_lines(
            self.specs_dir, spec, None, None, "", "feature-a", False, "origin/master"
        )

        self.assertEqual(lines[0], "Source: ad-hoc prompt (20260722-120000-feature-a.md)")

    @ported_from(
        "SummaryLinesTests", "test_pending_ad_hoc_prompt_shows_first_line_not_yet_materialized"
    )
    def test_a_pending_ad_hoc_prompt_shows_its_first_line_not_a_path(self) -> None:
        lines = status._summary_lines(
            self.specs_dir,
            None,
            None,
            "Add a thing\nmore detail",
            "",
            "feature-a",
            False,
            "origin/master",
        )

        self.assertEqual(lines[0], "Source: ad-hoc prompt: Add a thing")
        self.assertFalse((self.specs_dir / "ad-hoc").exists())

    @ported_from("SummaryLinesTests", "test_created_branch_flow_shows_will_be_created_suffix")
    def test_a_branch_to_be_created_carries_the_suffix(self) -> None:
        (self.specs_dir / "my-task.md").write_text("# task\n")

        lines = status._summary_lines(
            self.specs_dir, "my-task", None, None, "", "brand-new", True, "origin/staging"
        )

        self.assertIn("Branch: brand-new (will be created)", lines)
        self.assertIn("Base: origin/staging", lines)


class PurityTest(unittest.TestCase):
    """`bessemer/status.py` spawns nothing: docker rows arrive as text and `docker_down` is
    a parameter, so every test above is pure and fast, and `tests/guard.py` can deny
    `docker` to the whole suite. Not an upstream property — `tasklib.py` was one module
    that shelled out freely — but this issue's criterion, and the same shape
    `tests/test_ledger.py` proves for the ledger.

    One deliberate difference from the sibling purity tests: `os` **is** imported here,
    because `pid_alive` sends signal 0. A signal send starts nothing, so the assertions ban
    the spawn paths and the environment names rather than the module — weaker than issues'
    and ledger's "no os at all", and named as such.
    """

    def module_source(self) -> str:
        origin = status.__file__
        assert origin is not None, "bessemer.status must be importable from a source tree"
        return Path(origin).read_text(encoding="utf-8")

    def imported_modules(self) -> set[str]:
        found: set[str] = set()
        for node in ast.walk(ast.parse(self.module_source())):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    base = f"{status.__package__}.{base}" if base else str(status.__package__)
                found.add(base)
                found.update(f"{base}.{alias.name}" for alias in node.names)
        return found

    def test_the_source_really_was_read(self) -> None:
        """Every assertion below passes on an empty string. This is what stops that."""
        self.assertIn("def render_status(", self.module_source())

    def test_the_module_crosses_no_spawn_boundary(self) -> None:
        self.assertEqual(violations(MODULE_PATH, self.module_source()), [])

    def test_the_module_does_not_import_the_subprocess_wrapper(self) -> None:
        """Invisible to the walk above: `bessemer.proc` is the one module that may spawn."""
        imported = self.imported_modules()
        self.assertNotIn(WRAPPER_MODULE, imported)
        self.assertFalse({name for name in imported if name.startswith(f"{WRAPPER_MODULE}.")})

    def test_the_module_reads_no_environment(self) -> None:
        """The belt half of the sibling tests' two-part check, on its own: any name spelled
        `environ`, `getenv` or `putenv`, however it arrived. The "no `os` at all" half is
        unavailable here — `pid_alive` needs `os.kill` — which makes this the one status
        assertion that is weaker than its siblings, deliberately and visibly."""
        forbidden = {"environ", "environb", "getenv", "putenv"}
        reached = set()
        for node in ast.walk(ast.parse(self.module_source())):
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                reached.add(node.attr)
            elif isinstance(node, ast.Name) and node.id in forbidden:
                reached.add(node.id)
        self.assertEqual(reached, set())

    def test_importing_the_module_pulls_in_neither_subprocess_nor_the_wrapper(self) -> None:
        """A fresh interpreter, because this one imported `subprocess` before any test module
        was loaded — `tests/guard.py` needs it to arm the guard. The control for this probe
        lives in `tests/test_config_purity.py`, which proves the same two-flag spelling can
        report `True True`."""
        probe = (
            "import sys, bessemer.status; "
            "print('subprocess' in sys.modules, 'bessemer.proc' in sys.modules)"
        )
        result = proc.run([sys.executable, "-I", "-c", probe], timeout=60)
        self.assertTrue(result.ok, result.stderr)
        self.assertEqual(result.stdout.strip(), "False False")


if __name__ == "__main__":
    unittest.main()
