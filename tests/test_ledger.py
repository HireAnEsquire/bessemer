"""The port of the port source's ledger tests, plus what bessemer had to write itself.

Every test carrying a `ported_from` marker is a counterpart of one upstream test in
`.agentbox/test_tasklib.py` at `e194121f75f4`, and `tests/port_manifest.py` names it back.
The assertions are upstream's, kept whole; only the names, the grouping into classes and
the entry points changed.

The unmarked tests are bessemer's own, and they exist for three reasons:

- **`bessemer/ledger.py` is files and JSON and nothing else**, which is this issue's
  criterion rather than an upstream property — `tasklib.py` was one module that shelled out
  freely. `PurityTest` proves it.
- **Two upstream classes were excluded as bash-to-python shims** (decision 5 of the F2
  README): `CmdLedgerAppendLastBaseTests` and `CmdLastTests`. Excluding a shim must not drop
  behaviour with it, so what those tests asserted and nothing else did lands here —
  `RunRecordTest`, `ParseIssueOutcomesTest`, and the source-directory scoping test in
  `BranchLookupTest`. None is marked `ported_from`: no upstream test covers these
  separately, and the manifest must not claim one does.
- **The record's key set is a list this module owns**, so it is restated by hand in
  `RunRecordTest` rather than read back off the code that produced it.
"""

import ast
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from bessemer import ledger, proc
from bessemer.issues import DispatchError
from bessemer.outcome import Unresolved
from tests.port_manifest import ported_from
from tests.test_argv_boundary import violations

MODULE_PATH = "bessemer/ledger.py"

WRAPPER_MODULE = proc.__name__
"""`bessemer.proc`, spelled from the module rather than as a literal so a rename fails."""

RECORD_KEYS = frozenset(
    {
        "timestamp",
        "branch",
        "base",
        "spec",
        "feature",
        "issues",
        "outcome",
        "pr_url",
        "task_dir",
    }
)
"""Every key `run_record` writes, restated by hand.

A ledger line is the one artifact of a run that outlives it, and this set is read by three
later features. An assertion that compared `run_record`'s output to `run_record`'s keys
could not notice a key leaving: it would vanish from both sides at once. `task_dir` is
spelled the way the file spells it — see `bessemer/ledger.py`'s note on why that name
survives CONTEXT.md retiring *task*.
"""


class LedgerTest(unittest.TestCase):
    """Read and append against an explicit ledger path — the path a caller would resolve
    through `central_ledger_path`."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger_path = Path(self.tmp.name) / "runs.jsonl"

    def append(self, *, branch: str, base: str, **extra: object) -> None:
        """Upstream's fixture helper: one record with a fixed timestamp, plus overrides."""
        record: ledger.Record = {
            "timestamp": "2026-07-22T00:00:00+00:00",
            "branch": branch,
            "base": base,
        }
        record.update(extra)
        ledger.append_ledger(self.ledger_path, record)

    @ported_from("LedgerTests", "test_append_writes_one_well_formed_line")
    def test_appending_writes_one_well_formed_json_line(self) -> None:
        self.append(
            branch="feature-x",
            base="origin/master",
            issues={"01": "approved"},
            pr_url="https://x/1",
        )

        lines = self.ledger_path.read_text().splitlines()

        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["branch"], "feature-x")
        self.assertEqual(record["base"], "origin/master")
        self.assertEqual(record["issues"], {"01": "approved"})
        self.assertEqual(record["pr_url"], "https://x/1")

    @ported_from("LedgerTests", "test_append_is_one_line_per_call")
    def test_each_append_adds_exactly_one_line(self) -> None:
        self.append(branch="feature-x", base="origin/master")
        self.append(branch="feature-x", base="origin/master")

        lines = self.ledger_path.read_text().splitlines()

        self.assertEqual(len(lines), 2)

    @ported_from("LedgerTests", "test_last_base_for_branch_returns_most_recent")
    def test_the_last_base_for_a_branch_is_its_most_recent_one(self) -> None:
        self.append(branch="feature-x", base="origin/master")
        self.append(branch="feature-x", base="feature-parent")
        self.append(branch="other-branch", base="origin/staging")

        self.assertEqual(
            ledger.last_base_for_branch(self.ledger_path, "feature-x"), "feature-parent"
        )
        self.assertEqual(
            ledger.last_base_for_branch(self.ledger_path, "other-branch"), "origin/staging"
        )

    @ported_from("LedgerTests", "test_last_base_for_unknown_branch_is_none")
    def test_a_branch_that_never_ran_has_no_last_base(self) -> None:
        self.append(branch="feature-x", base="origin/master")

        self.assertIsNone(ledger.last_base_for_branch(self.ledger_path, "never-run"))

    @ported_from("LedgerTests", "test_missing_ledger_degrades_to_none")
    def test_a_missing_ledger_reads_as_no_records(self) -> None:
        self.assertIsNone(ledger.last_base_for_branch(self.ledger_path, "feature-x"))
        self.assertEqual(ledger.read_ledger(self.ledger_path), [])

    @ported_from("LedgerTests", "test_corrupt_ledger_lines_are_skipped_not_fatal")
    def test_a_malformed_line_is_skipped_rather_than_raised(self) -> None:
        """The truncated final line is the ordinary case, not the exotic one: the ledger is
        append-only and written by concurrent runs, so a half-written tail is what a crash
        during a landing leaves behind."""
        self.ledger_path.write_text(
            '{"branch": "feature-x", "base": "origin/master"}\nnot json at all\n{"broken\n'
        )

        self.assertEqual(
            ledger.last_base_for_branch(self.ledger_path, "feature-x"), "origin/master"
        )

    @ported_from("LedgerTests", "test_undecodable_ledger_degrades_to_none_not_fatal")
    def test_a_ledger_that_is_not_text_reads_as_no_records(self) -> None:
        self.ledger_path.write_bytes(b"\xff\xfe\x00bad bytes, not utf-8\n")

        self.assertIsNone(ledger.last_base_for_branch(self.ledger_path, "feature-x"))
        self.assertEqual(ledger.read_ledger(self.ledger_path), [])

    @ported_from("LedgerTests", "test_deleting_ledger_mid_feature_next_run_defaults_to_none")
    def test_deleting_the_ledger_mid_feature_drops_the_next_runs_default(self) -> None:
        self.append(branch="feature-x", base="feature-parent")
        self.ledger_path.unlink()

        self.assertIsNone(ledger.last_base_for_branch(self.ledger_path, "feature-x"))

    @ported_from("LedgerTests", "test_append_ledger_write_failure_does_not_raise")
    def test_a_write_failure_does_not_raise(self) -> None:
        missing_dir_ledger = Path(self.tmp.name) / "does-not-exist" / "runs.jsonl"

        ledger.append_ledger(
            missing_dir_ledger, {"branch": "feature-x", "base": "origin/master"}
        )  # no raise

    def test_a_write_failure_comes_back_as_a_reason_rather_than_a_printed_line(self) -> None:
        """Upstream printed this to stderr from inside the library; decision 2 of the F2
        README removes the print. The information is what must survive the terminal going
        away, so it is returned — a silent `None` would drop it rather than relocate it."""
        missing_dir_ledger = Path(self.tmp.name) / "does-not-exist" / "runs.jsonl"

        failure = ledger.append_ledger(missing_dir_ledger, {"branch": "feature-x"})

        self.assertIsInstance(failure, Unresolved)
        assert isinstance(failure, Unresolved)
        self.assertIn(str(missing_dir_ledger), failure.reason)
        self.assertIn("writable", failure.hint)

    def test_a_successful_append_reports_nothing(self) -> None:
        self.assertIsNone(ledger.append_ledger(self.ledger_path, {"branch": "feature-x"}))

    def test_a_line_that_is_not_an_object_is_skipped(self) -> None:
        """Well-formed JSON, unusable record. Every reader calls `.get`, so a bare scalar
        would take the whole read down rather than one line of it."""
        self.ledger_path.write_text('3\n["a"]\n{"branch": "feature-x"}\n')

        self.assertEqual(ledger.read_ledger(self.ledger_path), [{"branch": "feature-x"}])


class CentralLedgerPathTest(unittest.TestCase):
    @ported_from("CentralLedgerPathTests", "test_is_tasks_dir_parent_slash_runs_jsonl")
    def test_the_ledger_sits_beside_the_specs_directory(self) -> None:
        specs_dir = Path("/repo/.bessemer/specs")

        self.assertEqual(
            ledger.central_ledger_path(specs_dir), Path("/repo/.bessemer/runs.jsonl")
        )


class RunRecordTest(unittest.TestCase):
    """Bessemer's own, and one of the two places `CmdLedgerAppendLastBaseTests` went.

    That class was excluded as a bash-to-python shim, but the record it built is not part of
    the shim — a python dispatcher needs the same shape. Its docstring claimed two things,
    and this is the second: `--task-dir` becomes a recorded fact rather than a write
    location. (The first — that the ledger's location comes from the specs directory — is
    `CentralLedgerPathTest` above, which is ported and covers it.)
    """

    def test_a_record_carries_exactly_the_hand_written_key_set(self) -> None:
        record = ledger.run_record(branch="feature-x", base="origin/master")

        self.assertEqual(set(record), set(RECORD_KEYS))

    def test_the_run_is_recorded_with_its_source_under_the_key_task_dir(self) -> None:
        """`task_dir` is upstream's spelling and it is data on disk, so it survives
        CONTEXT.md retiring *task* — the parameter is what got renamed."""
        record = ledger.run_record(
            branch="feature-x", base="origin/master", source_dir="/repo/.bessemer/specs/f2"
        )

        self.assertEqual(record["task_dir"], "/repo/.bessemer/specs/f2")

    def test_a_source_that_no_longer_exists_records_as_null_not_a_stale_path(self) -> None:
        """The resumed feedback-only run, whose source directory is gone by the time it
        lands. Upstream recorded `args.task_dir or None` for exactly this."""
        record = ledger.run_record(branch="feature-x", base="origin/master", source_dir="")

        self.assertIsNone(record["task_dir"])

    def test_the_verdicts_and_the_pull_request_are_recorded_as_given(self) -> None:
        record = ledger.run_record(
            branch="feature-x",
            base="origin/master",
            feature="my-feature",
            issues={"01": "approved", "02": "not-attempted"},
            pr_url="https://x/1",
        )

        self.assertEqual(record["feature"], "my-feature")
        self.assertEqual(record["issues"], {"01": "approved", "02": "not-attempted"})
        self.assertEqual(record["pr_url"], "https://x/1")

    def test_a_run_with_no_pull_request_records_null_rather_than_an_empty_string(
        self,
    ) -> None:
        record = ledger.run_record(branch="feature-x", base="origin/master", pr_url="")

        self.assertIsNone(record["pr_url"])

    def test_the_timestamp_is_utc_so_the_hosts_zone_cannot_reorder_the_ledger(self) -> None:
        """`collect_recent_ledger_records` sorts these as plain strings, so a record stamped
        in a local zone would sort against records stamped in another."""
        record = ledger.run_record(branch="feature-x", base="origin/master")

        stamped = datetime.fromisoformat(record["timestamp"])
        self.assertEqual(stamped.utcoffset(), datetime.now(UTC).utcoffset())

    def test_a_record_round_trips_through_the_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "runs.jsonl"
            record = ledger.run_record(
                branch="feature-x", base="origin/master", feature="my-feature"
            )

            ledger.append_ledger(ledger_path, record)

            self.assertEqual(ledger.read_ledger(ledger_path), [record])


class ParseIssueOutcomesTest(unittest.TestCase):
    """Bessemer's own: upstream's `--issue NUM=OUTCOME` parsing, kept when the subcommand
    that used to print its refusal was excluded as a shim."""

    def test_pairs_become_a_mapping_of_issue_number_to_verdict(self) -> None:
        self.assertEqual(
            ledger.parse_issue_outcomes(["01=approved", "02=not-attempted"]),
            {"01": "approved", "02": "not-attempted"},
        )

    def test_a_pair_with_no_separator_is_refused(self) -> None:
        """Named for what it asserts. It said "before anything is written" until review:
        this function writes nothing under any input, so the clause described an ordering the
        test never checked and could not — which is the shape of all eleven of F1's
        defects."""
        with self.assertRaises(DispatchError) as caught:
            ledger.parse_issue_outcomes(["not-a-pair"])

        self.assertIn("--issue expects NUM=OUTCOME", str(caught.exception))

    def test_a_verdict_containing_a_separator_keeps_it(self) -> None:
        """`split("=", 1)`, so only the first separator divides the pair."""
        self.assertEqual(
            ledger.parse_issue_outcomes(["01=needs-work=twice"]), {"01": "needs-work=twice"}
        )

    def test_no_pairs_is_no_outcomes_rather_than_a_refusal(self) -> None:
        self.assertEqual(ledger.parse_issue_outcomes([]), {})


class BranchLookupTest(unittest.TestCase):
    """What the ledger knows about one branch: its newest record, and the branches worth
    offering as a hint."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger_path = Path(self.tmp.name) / "runs.jsonl"

    @ported_from(
        "NewestRecordForBranchTests", "test_returns_most_recent_record_regardless_of_task_dir"
    )
    def test_the_newest_record_for_a_branch_ignores_which_source_it_ran_from(self) -> None:
        ledger.append_ledger(
            self.ledger_path, {"branch": "b1", "base": "origin/master", "task_dir": "dir-a"}
        )
        ledger.append_ledger(
            self.ledger_path, {"branch": "b1", "base": "origin/master", "task_dir": "dir-b"}
        )
        ledger.append_ledger(
            self.ledger_path, {"branch": "other", "base": "origin/master", "task_dir": "dir-c"}
        )

        record = ledger.newest_record_for_branch(self.ledger_path, "b1")

        assert record is not None
        self.assertEqual(record["task_dir"], "dir-b")

    @ported_from("NewestRecordForBranchTests", "test_no_record_returns_none")
    def test_a_branch_with_no_record_has_no_newest_one(self) -> None:
        self.assertIsNone(ledger.newest_record_for_branch(self.ledger_path, "never-run"))

    def test_the_last_base_lookup_ignores_which_source_the_branch_ran_from(self) -> None:
        # The lookup considers ALL lines for the branch regardless of source directory —
        # unlike the picker's curated branch menu, which does scope by it (see
        # `LedgerBranchHelpersTest`). **The picker is not ported**: decision 1 of the F2
        # README puts it out of scope, so the contrast names a menu that does not exist in
        # this tree yet, and the helpers it would call are `_ledger_branch_order` and
        # `_annotate_branch` below.
        #
        # Bessemer's own, and the one claim `CmdLedgerAppendLastBaseTests` made that nothing
        # else in this suite makes: the class was excluded as a shim, but this is a fact
        # about `last_base_for_branch` rather than about the subcommand that called it.
        ledger.append_ledger(
            self.ledger_path,
            {"branch": "feature-x", "base": "other-dirs-base", "task_dir": "unrelated/dir"},
        )

        self.assertEqual(
            ledger.last_base_for_branch(self.ledger_path, "feature-x"), "other-dirs-base"
        )

    @ported_from("RecentDistinctBranchesTests", "test_newest_first_deduped")
    def test_recent_branches_come_back_newest_first_and_deduped(self) -> None:
        ledger.append_ledger(self.ledger_path, {"branch": "a"})
        ledger.append_ledger(self.ledger_path, {"branch": "b"})
        ledger.append_ledger(self.ledger_path, {"branch": "a"})
        ledger.append_ledger(self.ledger_path, {"branch": "c"})

        self.assertEqual(ledger.recent_distinct_branches(self.ledger_path), ["c", "a", "b"])

    @ported_from("RecentDistinctBranchesTests", "test_respects_limit")
    def test_recent_branches_stop_at_the_limit(self) -> None:
        for name in ["a", "b", "c", "d"]:
            ledger.append_ledger(self.ledger_path, {"branch": name})

        self.assertEqual(ledger.recent_distinct_branches(self.ledger_path, limit=2), ["d", "c"])

    @ported_from("RecentDistinctBranchesTests", "test_empty_ledger_returns_empty")
    def test_an_empty_ledger_has_no_recent_branches(self) -> None:
        self.assertEqual(ledger.recent_distinct_branches(self.ledger_path), [])


class ResolveLastTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger_path = Path(self.tmp.name) / "runs.jsonl"

    @ported_from("ResolveLastTests", "test_returns_newest_record_overall_regardless_of_branch")
    def test_the_last_run_is_the_newest_record_whatever_branch_it_was_on(self) -> None:
        ledger.append_ledger(self.ledger_path, {"branch": "b1", "base": "origin/master"})
        ledger.append_ledger(self.ledger_path, {"branch": "b2", "base": "feature-parent"})

        record = ledger.resolve_last(self.ledger_path)

        self.assertEqual(record["branch"], "b2")

    @ported_from("ResolveLastTests", "test_no_outcome_filtering_a_failed_run_can_be_newest")
    def test_a_failed_run_can_be_the_last_one(self) -> None:
        ledger.append_ledger(
            self.ledger_path,
            {"branch": "b1", "base": "origin/master", "outcome": "approved"},
        )
        ledger.append_ledger(
            self.ledger_path,
            {"branch": "b2", "base": "origin/master", "outcome": "needs-work"},
        )

        record = ledger.resolve_last(self.ledger_path)

        self.assertEqual(record["branch"], "b2")

    @ported_from("ResolveLastTests", "test_empty_ledger_raises")
    def test_an_empty_ledger_refuses_rather_than_returning_nothing(self) -> None:
        with self.assertRaises(DispatchError) as caught:
            ledger.resolve_last(self.ledger_path)

        self.assertIn("no runs recorded yet", str(caught.exception))


class CollectRecentLedgerRecordsTest(unittest.TestCase):
    """Three of upstream's four. The fourth,
    `test_triggers_legacy_migration_when_central_file_missing`, is excluded with the
    migration itself (decision 4 of the F2 README), and the manifest records it by name
    rather than by a count that cannot see one test missing from a ported class.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.specs_dir = Path(self.tmp.name) / "specs"
        self.specs_dir.mkdir()
        self.central_path = ledger.central_ledger_path(self.specs_dir)

    @ported_from(
        "CollectRecentLedgerRecordsTests", "test_gathers_from_central_file_newest_first"
    )
    def test_records_come_back_from_the_central_file_newest_first(self) -> None:
        ledger.append_ledger(
            self.central_path, {"timestamp": "2026-07-20T00:00:00+00:00", "branch": "a"}
        )
        ledger.append_ledger(
            self.central_path, {"timestamp": "2026-07-22T00:00:00+00:00", "branch": "b"}
        )
        ledger.append_ledger(
            self.central_path, {"timestamp": "2026-07-21T00:00:00+00:00", "branch": "a"}
        )

        records = ledger.collect_recent_ledger_records(self.specs_dir)

        self.assertEqual([r["branch"] for r in records], ["b", "a", "a"])

    @ported_from("CollectRecentLedgerRecordsTests", "test_no_ledger_returns_empty")
    def test_no_ledger_collects_no_records(self) -> None:
        self.assertEqual(ledger.collect_recent_ledger_records(self.specs_dir), [])

    @ported_from("CollectRecentLedgerRecordsTests", "test_corrupt_ledger_is_skipped_not_fatal")
    def test_a_corrupt_ledger_collects_no_records_rather_than_raising(self) -> None:
        self.central_path.write_text("not json\n")

        self.assertEqual(ledger.collect_recent_ledger_records(self.specs_dir), [])

    def test_a_specs_directory_of_legacy_per_source_ledgers_collects_nothing(self) -> None:
        """The behaviour dropped with `_migrate_legacy_ledgers` (decision 4), stated as a
        test rather than left as an absence: upstream would have merged this file into the
        central one and returned its record. Bessemer has zero installs, so a legacy ledger
        is a file it never wrote, and returning nothing is the correct answer here."""
        source = self.specs_dir / "feature-a"
        source.mkdir()
        (source / "runs.jsonl").write_text(
            json.dumps({"timestamp": "2026-07-22T00:00:00+00:00", "branch": "a"}) + "\n"
        )

        self.assertEqual(ledger.collect_recent_ledger_records(self.specs_dir), [])
        self.assertFalse(self.central_path.exists())


class LedgerBranchHelpersTest(unittest.TestCase):
    """The two source-scoped helpers. Their only upstream caller is the interactive picker,
    which decision 1 of the F2 README leaves unported — so nothing in this tree calls them
    yet, and they are here because they are pure ledger reads whose tests are in scope."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger_path = Path(self.tmp.name) / "runs.jsonl"
        self.source_dir = Path(self.tmp.name) / "my-feature"

    def append(self, *, source_dir: Path | None = None, **extra: object) -> None:
        record: ledger.Record = {
            "timestamp": "2026-07-22T00:00:00+00:00",
            "task_dir": str(source_dir or self.source_dir),
        }
        record.update(extra)
        ledger.append_ledger(self.ledger_path, record)

    @ported_from("LedgerBranchHelpersTests", "test_branch_order_is_most_recent_first_deduped")
    def test_branch_order_is_most_recent_first_and_deduped(self) -> None:
        self.append(branch="a", base="origin/master")
        self.append(branch="b", base="origin/master")
        self.append(branch="a", base="feature-x")

        self.assertEqual(
            ledger._ledger_branch_order(self.ledger_path, self.source_dir), ["a", "b"]
        )

    @ported_from(
        "LedgerBranchHelpersTests", "test_branch_order_scoped_to_task_dir_ignores_other_dirs"
    )
    def test_branch_order_ignores_branches_run_from_another_source(self) -> None:
        self.append(branch="a", base="origin/master")
        self.append(
            branch="other-features-branch",
            base="origin/master",
            source_dir=Path(self.tmp.name) / "elsewhere",
        )

        self.assertEqual(ledger._ledger_branch_order(self.ledger_path, self.source_dir), ["a"])

    @ported_from("LedgerBranchHelpersTests", "test_annotate_branch_includes_base_issues_and_pr")
    def test_an_annotated_branch_carries_its_base_issues_and_pull_request(self) -> None:
        self.append(
            branch="feat",
            base="origin/master",
            issues={"01": "approved", "02": "needs-work"},
            pr_url="https://x/1",
        )

        label = ledger._annotate_branch(self.ledger_path, self.source_dir, "feat")

        self.assertIn("base: origin/master", label)
        self.assertIn("issues done: 1/2", label)
        self.assertIn("PR: https://x/1", label)

    def test_a_later_run_without_a_pull_request_does_not_hide_the_branchs_existing_one(
        self,
    ) -> None:
        """Bessemer's own, and it exists because a claim had nothing behind it: replacing
        the `pr_url` search with `last.get("pr_url")` left the whole suite green. Upstream
        writes the search and documents none of it, so the docstring asserting it is
        bessemer's — and a documented claim no test can fail is what F1 shipped eleven times.
        The live case is the feedback-only re-run: it lands on a branch whose pull request is
        already open and records no URL of its own."""
        self.append(branch="feat", base="origin/master", pr_url="https://x/1")
        self.append(branch="feat", base="origin/master")

        label = ledger._annotate_branch(self.ledger_path, self.source_dir, "feat")

        self.assertIn("PR: https://x/1", label)

    @ported_from(
        "LedgerBranchHelpersTests",
        "test_annotate_branch_with_no_ledger_record_returns_bare_name",
    )
    def test_a_branch_the_ledger_does_not_know_annotates_to_its_bare_name(self) -> None:
        self.assertEqual(
            ledger._annotate_branch(self.ledger_path, self.source_dir, "unknown"), "unknown"
        )

    @ported_from(
        "LedgerBranchHelpersTests", "test_annotate_branch_ignores_same_branch_in_other_task_dir"
    )
    def test_annotation_ignores_the_same_branch_run_from_another_source(self) -> None:
        self.append(
            branch="feat", base="other-base", source_dir=Path(self.tmp.name) / "elsewhere"
        )

        self.assertEqual(
            ledger._annotate_branch(self.ledger_path, self.source_dir, "feat"), "feat"
        )


class LatestPerBranchTest(unittest.TestCase):
    @ported_from("LatestPerBranchTests", "test_keeps_only_the_first_record_seen_per_branch")
    def test_only_the_first_record_seen_per_branch_survives(self) -> None:
        records: list[ledger.Record] = [
            {"branch": "b1", "n": 1},
            {"branch": "b2", "n": 2},
            {"branch": "b1", "n": 3},
        ]

        result = ledger._latest_per_branch(records)

        self.assertEqual([r["branch"] for r in result], ["b1", "b2"])
        self.assertEqual(result[0]["n"], 1)  # newest-first input -> first-seen wins

    @ported_from("LatestPerBranchTests", "test_caps_at_limit")
    def test_the_result_is_capped_at_the_limit(self) -> None:
        records: list[ledger.Record] = [{"branch": f"b{i}"} for i in range(15)]

        result = ledger._latest_per_branch(records, limit=10)

        self.assertEqual(len(result), 10)

    @ported_from("LatestPerBranchTests", "test_skips_records_with_missing_or_non_string_branch")
    def test_a_record_with_no_usable_branch_is_skipped(self) -> None:
        records: list[ledger.Record] = [
            {"branch": None},
            {"no_branch_key": True},
            {"branch": ""},
            {"branch": "b1"},
        ]

        result = ledger._latest_per_branch(records)

        self.assertEqual([r["branch"] for r in result], ["b1"])


class PurityTest(unittest.TestCase):
    """`bessemer/ledger.py` is file and JSON handling and nothing else: no subprocess, no
    environment. Not an upstream property — `tasklib.py` was one module that shelled out
    freely — but this issue's criterion, and the same shape `tests/test_issues.py` proves
    for `issues`. Two angles: a static walk that sees code no test happens to run, and a
    fresh interpreter that catches an import arriving through a helper.
    """

    def module_source(self) -> str:
        origin = ledger.__file__
        assert origin is not None, "bessemer.ledger must be importable from a source tree"
        return Path(origin).read_text(encoding="utf-8")

    def imported_modules(self) -> set[str]:
        found: set[str] = set()
        for node in ast.walk(ast.parse(self.module_source())):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    base = f"{ledger.__package__}.{base}" if base else str(ledger.__package__)
                found.add(base)
                found.update(f"{base}.{alias.name}" for alias in node.names)
        return found

    def test_the_source_really_was_read(self) -> None:
        """Every assertion below passes on an empty string. This is what stops that."""
        self.assertIn("def read_ledger(", self.module_source())

    def test_the_module_crosses_no_spawn_boundary(self) -> None:
        self.assertEqual(violations(MODULE_PATH, self.module_source()), [])

    def test_the_module_does_not_import_the_subprocess_wrapper(self) -> None:
        """Invisible to the walk above: `bessemer.proc` is the one module that may spawn."""
        imported = self.imported_modules()
        self.assertNotIn(WRAPPER_MODULE, imported)
        self.assertFalse({name for name in imported if name.startswith(f"{WRAPPER_MODULE}.")})

    def test_the_module_reads_no_environment(self) -> None:
        """`os` is not imported at all, which is stronger than banning `os.environ` and is
        the only version a static walk can state without chasing aliases. The ledger answers
        questions about one file; if something here ever needs the environment, that is a
        finding for the port report."""
        imported = self.imported_modules()
        self.assertNotIn("os", imported)
        self.assertFalse({name for name in imported if name.startswith("os.")})

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
            "import sys, bessemer.ledger; "
            "print('subprocess' in sys.modules, 'bessemer.proc' in sys.modules)"
        )
        result = proc.run([sys.executable, "-I", "-c", probe], timeout=60)
        self.assertTrue(result.ok, result.stderr)
        self.assertEqual(result.stdout.strip(), "False False")


if __name__ == "__main__":
    unittest.main()
