"""The port of the port source's resume tests, plus what bessemer had to write itself.

Every test carrying a `ported_from` marker is a counterpart of one upstream test in
`.agentbox/test_tasklib.py` at `e194121f75f4`, and `tests/port_manifest.py` names it back.
The assertions are upstream's, kept whole; only the names, the grouping into classes and the
entry points changed.

The unmarked tests are bessemer's own, and they exist for three reasons:

- **`bessemer/resume.py` spawns no subprocess**, which is this issue's criterion rather than
  an upstream property — `tasklib.py` was one module that shelled out freely, and
  `_first_free_branch_name` is exactly where it did. `PurityTest` proves it.
- **Two upstream classes were excluded as bash-to-python shims** (decision 5 of the F2
  README): `CmdResumeTests` and `CmdResumeGuardTests`. Excluding a shim must not drop
  behaviour with it, so the one thing they asserted that no ported test does — that a
  resolution failure yields no partial answer — lands here as
  `test_a_refused_resume_yields_no_partial_answer`, unmarked, because no upstream test covers
  it separately and the manifest must not claim one does.
- **Two lists this module owns are restated by hand**: `ACTIONS` and `PROTECTED_BRANCHES`.
  An assertion that read either constant back could not notice an entry leaving it.
"""

import ast
import sys
import tempfile
import unittest
from pathlib import Path

from bessemer import ledger, proc, resume
from bessemer.issues import DispatchError
from tests.port_manifest import ported_from
from tests.test_argv_boundary import violations
from tests.test_issues import write_issue

MODULE_PATH = "bessemer/resume.py"

WRAPPER_MODULE = proc.__name__
"""`bessemer.proc`, spelled from the module rather than as a literal so a rename fails."""

ACTIONS = frozenset({"normal", "feedback-only", "refuse", "hard-error"})
"""Every answer `resume_dispatch_action` can give, restated by hand.

F3's dispatch switches on these strings, so an answer silently leaving the set is an
interface change. Comparing `resume.ACTIONS` to itself would pass on an empty frozenset.
"""

PROTECTED_BRANCHES = frozenset({"master", "main"})
"""The names `is_protected` refuses, restated by hand. Upstream's two tests assert `master`,
`MAIN` and `feature-x`, which pins the members it has — not the absence of a third."""


def never(_name: str) -> bool:
    """A branch-existence predicate that says no. `_first_free_branch_name`'s git questions
    are parameters here; see that function's docstring for why."""
    return False


class ResolveResumeTest(unittest.TestCase):
    """Recovering a run's mode, source and pull request from the newest ledger record."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.specs_dir = Path(self.tmp.name) / "specs"
        self.specs_dir.mkdir()
        self.central_path = ledger.central_ledger_path(self.specs_dir)

    @ported_from("ResolveResumeTests", "test_feature_mode_task_dir_is_the_feature_folder")
    def test_a_feature_runs_source_directory_is_the_feature_folder(self) -> None:
        feature_dir = self.specs_dir / "my-feature"
        feature_dir.mkdir()
        ledger.append_ledger(
            self.central_path,
            {
                "branch": "b1",
                "base": "origin/master",
                "feature": "my-feature",
                "task_dir": str(feature_dir),
            },
        )

        info = resume.resolve_resume(self.specs_dir, "b1")

        self.assertEqual(info.mode, "feature")
        self.assertEqual(info.feature, str(feature_dir))
        self.assertIsNone(info.spec)
        self.assertTrue(info.source_exists)

    @ported_from("ResolveResumeTests", "test_spec_mode_reconstructs_full_spec_path")
    def test_a_spec_runs_full_path_is_rebuilt_from_the_record(self) -> None:
        spec_dir = self.specs_dir / "ad-hoc"
        spec_dir.mkdir()
        (spec_dir / "20260101-b2.md").write_text("prompt text")
        ledger.append_ledger(
            self.central_path,
            {
                "branch": "b2",
                "base": "origin/master",
                "spec": "20260101-b2.md",
                "task_dir": str(spec_dir),
            },
        )

        info = resume.resolve_resume(self.specs_dir, "b2")

        self.assertEqual(info.mode, "spec")
        self.assertEqual(info.spec, str(spec_dir / "20260101-b2.md"))
        self.assertIsNone(info.feature)
        self.assertTrue(info.source_exists)

    @ported_from("ResolveResumeTests", "test_feature_dir_deleted_from_disk_is_not_exists")
    def test_a_feature_folder_deleted_from_disk_no_longer_exists(self) -> None:
        ledger.append_ledger(
            self.central_path,
            {
                "branch": "b1",
                "base": "origin/master",
                "feature": "my-feature",
                "task_dir": str(self.specs_dir / "my-feature"),
            },
        )

        info = resume.resolve_resume(self.specs_dir, "b1")

        self.assertFalse(info.source_exists)

    @ported_from("ResolveResumeTests", "test_spec_file_individually_deleted_is_not_exists")
    def test_a_spec_file_deleted_on_its_own_no_longer_exists(self) -> None:
        spec_dir = self.specs_dir / "ad-hoc"
        spec_dir.mkdir()
        ledger.append_ledger(
            self.central_path,
            {
                "branch": "b2",
                "base": "origin/master",
                "spec": "gone.md",
                "task_dir": str(spec_dir),
            },
        )

        info = resume.resolve_resume(self.specs_dir, "b2")

        self.assertFalse(info.source_exists)

    @ported_from(
        "ResolveResumeTests", "test_null_task_dir_from_a_prior_feedback_only_run_is_not_exists"
    )
    def test_a_null_source_from_an_earlier_feedback_only_run_does_not_exist(self) -> None:
        ledger.append_ledger(
            self.central_path,
            {
                "branch": "b1",
                "base": "origin/master",
                "feature": "my-feature",
                "task_dir": None,
            },
        )

        info = resume.resolve_resume(self.specs_dir, "b1")

        self.assertEqual(info.mode, "feature")
        self.assertFalse(info.source_exists)

    @ported_from(
        "ResolveResumeTests",
        "test_mode_recovers_from_a_feedback_only_run_that_carried_the_identity_forward",
    )
    def test_mode_survives_a_feedback_only_run_that_carried_the_identity_forward(self) -> None:
        # First run: normal feature dispatch. Second run: a feedback-only resume of it, which
        # carries the same `feature=` field forward alongside the still-present source
        # directory — the ledger-append contract the port source's run.sh implements.
        feature_dir = self.specs_dir / "my-feature"
        feature_dir.mkdir()
        for _ in range(2):
            ledger.append_ledger(
                self.central_path,
                {
                    "branch": "b1",
                    "base": "origin/master",
                    "feature": "my-feature",
                    "task_dir": str(feature_dir),
                },
            )

        info = resume.resolve_resume(self.specs_dir, "b1")

        self.assertEqual(info.mode, "feature")
        self.assertTrue(info.source_exists)

    @ported_from(
        "ResolveResumeTests", "test_branch_not_in_ledger_raises_with_recent_branches_hint"
    )
    def test_a_branch_the_ledger_never_saw_refuses_and_lists_the_ones_it_did(self) -> None:
        ledger.append_ledger(
            self.central_path, {"branch": "b1", "base": "origin/master", "feature": "f"}
        )

        with self.assertRaises(DispatchError) as ctx:
            resume.resolve_resume(self.specs_dir, "never-run")

        self.assertIn("no ledger record for branch 'never-run'", str(ctx.exception))
        self.assertIn("b1", str(ctx.exception))

    @ported_from("ResolveResumeTests", "test_empty_ledger_raises_with_no_hint")
    def test_an_empty_ledger_refuses_with_nothing_to_suggest(self) -> None:
        with self.assertRaises(DispatchError) as ctx:
            resume.resolve_resume(self.specs_dir, "never-run")

        self.assertIn("(none recorded)", str(ctx.exception))

    @ported_from(
        "ResolveResumeTests", "test_identity_is_the_feature_basename_even_with_null_task_dir"
    )
    def test_identity_is_the_feature_basename_even_with_no_source_recorded(self) -> None:
        # A feedback-only run's own ledger append records a null source, and must still
        # recover a usable identity for its OWN eventual append, so mode composes across
        # repeated resumes.
        ledger.append_ledger(
            self.central_path,
            {
                "branch": "b1",
                "base": "origin/master",
                "feature": "my-feature",
                "task_dir": None,
            },
        )

        info = resume.resolve_resume(self.specs_dir, "b1")

        self.assertEqual(info.identity, "my-feature")
        self.assertFalse(info.source_exists)

    @ported_from("ResolveResumeTests", "test_identity_is_the_spec_basename")
    def test_identity_is_the_spec_basename(self) -> None:
        spec_dir = self.specs_dir / "ad-hoc"
        spec_dir.mkdir()
        (spec_dir / "20260101-b2.md").write_text("prompt text")
        ledger.append_ledger(
            self.central_path,
            {
                "branch": "b2",
                "base": "origin/master",
                "spec": "20260101-b2.md",
                "task_dir": str(spec_dir),
            },
        )

        info = resume.resolve_resume(self.specs_dir, "b2")

        self.assertEqual(info.identity, "20260101-b2.md")

    @ported_from("ResolveResumeTests", "test_pr_url_is_carried_from_the_record")
    def test_the_pull_request_is_carried_out_of_the_record(self) -> None:
        ledger.append_ledger(
            self.central_path,
            {
                "branch": "b1",
                "base": "origin/master",
                "feature": "my-feature",
                "task_dir": None,
                "pr_url": "https://github.com/example/repo/pull/1",
            },
        )

        info = resume.resolve_resume(self.specs_dir, "b1")

        self.assertEqual(info.pr_url, "https://github.com/example/repo/pull/1")

    @ported_from("ResolveResumeTests", "test_pr_url_is_none_when_absent")
    def test_a_record_with_no_pull_request_carries_none(self) -> None:
        ledger.append_ledger(
            self.central_path,
            {
                "branch": "b1",
                "base": "origin/master",
                "feature": "my-feature",
                "task_dir": None,
            },
        )

        info = resume.resolve_resume(self.specs_dir, "b1")

        self.assertIsNone(info.pr_url)

    def test_a_refused_resume_yields_no_partial_answer(self) -> None:
        """Bessemer's own, and what excluding `CmdResumeTests` must not drop with it.

        Upstream asserted this through the subcommand: on a branch the ledger does not know,
        exit 2 and *nothing on stdout* — no half-filled `mode=`/`task_dir=` lines for run.sh
        to parse. There is no subcommand here, so the same claim is made where it now lives:
        the raise happens before any `ResumeInfo` is built, so a caller cannot be handed a
        partial one. The exit code itself is upstream's shim contract and has no host here.
        """
        ledger.append_ledger(
            self.central_path, {"branch": "b1", "base": "origin/master", "feature": "f"}
        )

        with self.assertRaises(DispatchError):
            resume.resolve_resume(self.specs_dir, "never-run")


class ResumeDispatchActionTest(unittest.TestCase):
    """The empty-selection decision table: continue normally, run feedback only, or refuse."""

    @ported_from("ResumeDispatchActionTests", "test_normal_when_feature_has_runnable_issues")
    def test_a_feature_with_issues_left_to_run_continues_normally(self) -> None:
        action, message = resume.resume_dispatch_action(
            mode="feature", source_exists=True, issue_count=2, issues_arg="", has_feedback=False
        )
        self.assertEqual(action, "normal")
        self.assertIsNone(message)

    @ported_from(
        "ResumeDispatchActionTests", "test_normal_for_spec_mode_regardless_of_issue_count"
    )
    def test_a_spec_run_continues_normally_whatever_the_issue_count_says(self) -> None:
        action, _ = resume.resume_dispatch_action(
            mode="spec", source_exists=True, issue_count=0, issues_arg="", has_feedback=False
        )
        self.assertEqual(action, "normal")

    @ported_from(
        "ResumeDispatchActionTests", "test_refuse_when_feature_selection_empty_and_no_feedback"
    )
    def test_a_feature_with_nothing_to_run_and_no_feedback_is_refused(self) -> None:
        action, message = resume.resume_dispatch_action(
            mode="feature", source_exists=True, issue_count=0, issues_arg="", has_feedback=False
        )
        self.assertEqual(action, "refuse")
        assert message is not None
        self.assertIn("--feedback", message)
        self.assertIn("--issues NN", message)

    @ported_from(
        "ResumeDispatchActionTests",
        "test_feedback_only_when_feature_selection_empty_and_feedback_given",
    )
    def test_a_feature_with_nothing_to_run_but_feedback_given_runs_the_feedback(self) -> None:
        action, message = resume.resume_dispatch_action(
            mode="feature", source_exists=True, issue_count=0, issues_arg="", has_feedback=True
        )
        self.assertEqual(action, "feedback-only")
        self.assertIsNone(message)

    @ported_from(
        "ResumeDispatchActionTests", "test_refuse_when_task_dir_missing_and_no_feedback"
    )
    def test_a_gone_source_directory_with_no_feedback_is_refused(self) -> None:
        action, message = resume.resume_dispatch_action(
            mode="spec", source_exists=False, issue_count=0, issues_arg="", has_feedback=False
        )
        self.assertEqual(action, "refuse")
        self.assertIsNotNone(message)

    @ported_from(
        "ResumeDispatchActionTests",
        "test_feedback_only_when_task_dir_missing_and_feedback_given",
    )
    def test_a_gone_source_directory_with_feedback_runs_the_feedback(self) -> None:
        action, message = resume.resume_dispatch_action(
            mode="spec", source_exists=False, issue_count=0, issues_arg="", has_feedback=True
        )
        self.assertEqual(action, "feedback-only")
        self.assertIsNone(message)

    @ported_from(
        "ResumeDispatchActionTests",
        "test_hard_error_when_task_dir_missing_and_issues_arg_given",
    )
    def test_naming_issues_against_a_gone_source_directory_is_a_hard_error(self) -> None:
        action, message = resume.resume_dispatch_action(
            mode="feature",
            source_exists=False,
            issue_count=0,
            issues_arg="05",
            has_feedback=True,
        )
        self.assertEqual(action, "hard-error")
        assert message is not None
        self.assertIn("--issues", message)

    @ported_from(
        "ResumeDispatchActionTests", "test_hard_error_beats_feedback_only_when_task_dir_missing"
    )
    def test_a_hard_error_beats_feedback_when_the_source_directory_is_gone(self) -> None:
        # Even with feedback given, an explicit --issues can't be resolved without the
        # issues/ dir to check it against — hard error wins.
        action, _ = resume.resume_dispatch_action(
            mode="feature",
            source_exists=False,
            issue_count=0,
            issues_arg="05",
            has_feedback=True,
        )
        self.assertEqual(action, "hard-error")

    def test_every_action_it_can_answer_is_one_of_the_four_named_by_hand(self) -> None:
        """Bessemer's own: `ACTIONS` is a list this module owns, and F3 switches on it."""
        self.assertEqual(resume.ACTIONS, ACTIONS)


class ResumeIssueCountTest(unittest.TestCase):
    """How many issues a resume still has to run — the mandatory-feedback input."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.feature_dir = Path(self.tmp.name) / "01-feature"
        self.feature_dir.mkdir()

    def info(self, **overrides: object) -> resume.ResumeInfo:
        fields: dict[str, object] = {
            "mode": "feature",
            "source_dir": str(self.feature_dir),
            "feature": str(self.feature_dir),
            "spec": None,
            "source_exists": True,
            "identity": "01-feature",
            "pr_url": None,
        }
        fields.update(overrides)
        return resume.ResumeInfo(**fields)  # type: ignore[arg-type]

    @ported_from("ResumeIssueCountTests", "test_spec_mode_is_always_zero")
    def test_a_spec_run_always_counts_zero(self) -> None:
        info = self.info(mode="spec", source_dir="", feature=None, spec="x.md", identity="x.md")
        self.assertEqual(resume._resume_issue_count(info), 0)

    @ported_from("ResumeIssueCountTests", "test_missing_task_dir_is_zero")
    def test_a_gone_source_directory_counts_zero(self) -> None:
        info = self.info(source_dir="", source_exists=False)
        self.assertEqual(resume._resume_issue_count(info), 0)

    @ported_from("ResumeIssueCountTests", "test_feature_mode_counts_ready_issues")
    def test_a_feature_counts_the_issues_that_are_ready_to_run(self) -> None:
        issues_dir = self.feature_dir / "issues"
        issues_dir.mkdir()
        write_issue(issues_dir, "01-a.md", status="needs-triage")
        write_issue(issues_dir, "02-b.md", status="needs-triage")

        self.assertEqual(resume._resume_issue_count(self.info()), 2)

    @ported_from("ResumeIssueCountTests", "test_select_issues_error_counts_as_one_not_zero")
    def test_a_selection_that_errors_counts_as_one_rather_than_zero(self) -> None:
        # No issues/ dir under an existing feature folder -> select_issues raises; a blocker
        # violation just means "don't force feedback", not "there's definitely nothing to
        # run".
        self.assertEqual(resume._resume_issue_count(self.info()), 1)


class BranchNameTest(unittest.TestCase):
    """What a new working branch is called, and what it is called when that name is taken."""

    @ported_from("BranchNameSuggestionTests", "test_feature_mode_uses_feature_folder_name")
    def test_a_feature_run_is_named_after_its_folder(self) -> None:
        self.assertEqual(
            resume._branch_name_suggestion(None, "rollout-readiness", None), "rollout-readiness"
        )

    @ported_from("BranchNameSuggestionTests", "test_one_off_spec_uses_basename_sans_md")
    def test_a_one_off_spec_is_named_after_its_basename_without_the_suffix(self) -> None:
        self.assertEqual(
            resume._branch_name_suggestion("tasks/foo-bar.md", None, None), "foo-bar"
        )

    @ported_from(
        "BranchNameSuggestionTests", "test_one_off_spec_bare_name_without_md_suffix_unchanged"
    )
    def test_a_spec_named_without_a_suffix_is_left_alone(self) -> None:
        self.assertEqual(resume._branch_name_suggestion("foo-bar", None, None), "foo-bar")

    @ported_from("BranchNameSuggestionTests", "test_one_off_prompt_uses_slug_of_first_line")
    def test_a_typed_prompt_is_named_after_the_slug_of_its_first_line(self) -> None:
        self.assertEqual(
            resume._branch_name_suggestion(None, None, "Add a new filter\nmore detail"),
            "add-a-new-filter",
        )

    @ported_from(
        "BranchNameSuggestionTests", "test_feature_takes_priority_over_spec_or_prompt_text"
    )
    def test_a_feature_wins_over_a_spec_and_over_typed_text(self) -> None:
        self.assertEqual(
            resume._branch_name_suggestion("ignored.md", "my-feature", "ignored too"),
            "my-feature",
        )

    @ported_from("FirstFreeBranchNameTests", "test_free_suggestion_returned_as_is")
    def test_a_free_suggestion_is_used_as_it_stands(self) -> None:
        self.assertEqual(
            resume._first_free_branch_name(
                "rollout-readiness", local_exists=never, remote_exists=never
            ),
            "rollout-readiness",
        )

    @ported_from("FirstFreeBranchNameTests", "test_local_collision_suffixes")
    def test_a_local_branch_of_that_name_forces_a_suffix(self) -> None:
        self.assertEqual(
            resume._first_free_branch_name(
                "rollout-readiness",
                local_exists=lambda n: n == "rollout-readiness",
                remote_exists=never,
            ),
            "rollout-readiness-2",
        )

    @ported_from("FirstFreeBranchNameTests", "test_remote_collision_suffixes")
    def test_a_branch_already_pushed_from_elsewhere_forces_a_suffix_too(self) -> None:
        self.assertEqual(
            resume._first_free_branch_name(
                "rollout-readiness",
                local_exists=never,
                remote_exists=lambda n: n == "rollout-readiness",
            ),
            "rollout-readiness-2",
        )

    @ported_from("FirstFreeBranchNameTests", "test_skips_past_multiple_taken_suffixes")
    def test_taken_suffixes_are_skipped_until_one_is_free(self) -> None:
        taken = {"rollout-readiness", "rollout-readiness-2", "rollout-readiness-3"}
        self.assertEqual(
            resume._first_free_branch_name(
                "rollout-readiness", local_exists=lambda n: n in taken, remote_exists=never
            ),
            "rollout-readiness-4",
        )

    def test_a_name_taken_all_the_way_to_nine_falls_back_to_the_bare_suggestion(self) -> None:
        """Bessemer's own: upstream documents the fallback and never exercises it, and the
        suffix bound is a number this module owns. `always` rather than an enumerated set, so
        the assertion does not restate the range it is checking."""

        def always(_name: str) -> bool:
            return True

        self.assertEqual(
            resume._first_free_branch_name("busy", local_exists=always, remote_exists=never),
            "busy",
        )


class IsProtectedTest(unittest.TestCase):
    """The names a run may not take as its working branch."""

    @ported_from("IsProtectedTests", "test_master_and_main_are_protected")
    def test_master_and_main_are_protected_whatever_the_case(self) -> None:
        self.assertTrue(resume.is_protected("master"))
        self.assertTrue(resume.is_protected("MAIN"))

    @ported_from("IsProtectedTests", "test_other_branches_are_not_protected")
    def test_an_ordinary_branch_is_not_protected(self) -> None:
        self.assertFalse(resume.is_protected("feature-x"))

    def test_surrounding_whitespace_does_not_unprotect_a_name(self) -> None:
        """Bessemer's own: the docstring says whitespace-tolerant, and a claim in a comment
        with no test behind it is the defect every F1 review flagged. Upstream strips and
        never tests it either."""
        self.assertTrue(resume.is_protected(" main "))

    def test_the_protected_set_is_the_two_names_written_out_by_hand(self) -> None:
        """Bessemer's own. Upstream's two tests pin the members the set has; neither would
        notice a third name being added to it, and a third name is a branch a dispatch would
        silently start refusing."""
        self.assertEqual(resume.PROTECTED_BRANCHES, PROTECTED_BRANCHES)


class PurityTest(unittest.TestCase):
    """`bessemer/resume.py` is paths, text and the ledger: no subprocess, no environment.

    Not an upstream property — `tasklib.py` shelled out to `git show-ref` from
    `_first_free_branch_name`'s own helpers — but this issue's criterion, and the same shape
    `tests/test_ledger.py` proves for `ledger`. Two angles: a static walk that sees code no
    test happens to run, and a fresh interpreter that catches an import arriving through a
    helper.
    """

    def module_source(self) -> str:
        origin = resume.__file__
        assert origin is not None, "bessemer.resume must be importable from a source tree"
        return Path(origin).read_text(encoding="utf-8")

    def imported_modules(self) -> set[str]:
        found: set[str] = set()
        for node in ast.walk(ast.parse(self.module_source())):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    base = f"{resume.__package__}.{base}" if base else str(resume.__package__)
                found.add(base)
                found.update(f"{base}.{alias.name}" for alias in node.names)
        return found

    def test_the_source_really_was_read(self) -> None:
        """Every assertion below passes on an empty string. This is what stops that."""
        self.assertIn("def resolve_resume(", self.module_source())

    def test_the_module_crosses_no_spawn_boundary(self) -> None:
        self.assertEqual(violations(MODULE_PATH, self.module_source()), [])

    def test_the_module_does_not_import_the_subprocess_wrapper(self) -> None:
        """Invisible to the walk above: `bessemer.proc` is the one module that may spawn, and
        it is the import a branch-existence check would reach for."""
        imported = self.imported_modules()
        self.assertNotIn(WRAPPER_MODULE, imported)
        self.assertFalse({name for name in imported if name.startswith(f"{WRAPPER_MODULE}.")})

    def test_the_module_reads_no_environment(self) -> None:
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
        was loaded — `tests/guard.py` needs it to arm the guard."""
        probe = (
            "import sys, bessemer.resume; "
            "print('subprocess' in sys.modules, 'bessemer.proc' in sys.modules)"
        )
        result = proc.run([sys.executable, "-I", "-c", probe], timeout=60)
        self.assertTrue(result.ok, result.stderr)
        self.assertEqual(result.stdout.strip(), "False False")


if __name__ == "__main__":
    unittest.main()
