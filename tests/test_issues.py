"""The port of the port source's issue-file tests, plus this module's purity proof.

Every test carrying a `ported_from` marker is a counterpart of one upstream test in
`.agentbox/test_tasklib.py` at `e194121f75f4`, and `tests/port_manifest.py` names it back.
The assertions are upstream's, kept whole; only the names, the grouping into classes and
the entry points changed — a ported test that asserts less than upstream's did is the one
failure the manifest cannot see.

The unmarked tests at the bottom are bessemer's own: `bessemer/issues.py` claims to be pure
over paths and text, which is a claim upstream never had to make because `tasklib.py` was
one module that shelled out freely.
"""

import ast
import sys
import tempfile
import unittest
from pathlib import Path

from bessemer import issues, proc
from tests.port_manifest import ported_from
from tests.test_argv_boundary import violations

MODULE_PATH = "bessemer/issues.py"

WRAPPER_MODULE = proc.__name__
"""`bessemer.proc`, spelled from the module rather than as a literal so a rename fails."""


def write_issue(
    issues_dir: Path,
    filename: str,
    *,
    status: str = "",
    type_: str = "AFK",
    blocked_by: str = "",
) -> Path:
    """Upstream's fixture writer, ported: one issue file with a full header block."""
    path = issues_dir / filename
    path.write_text(
        f"# {filename}\n\nStatus: {status}\nType: {type_}\nBlocked by: {blocked_by}\n\n"
        f"## What to build\nBody.\n"
    )
    return path


class ParseIssueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.issues_dir = Path(self.tmp.name)

    @ported_from("ParseIssueTests", "test_tolerant_parsing_and_multiple_blockers")
    def test_a_header_parses_and_several_blockers_come_back_as_numbers(self) -> None:
        path = write_issue(
            self.issues_dir, "02-core.md", status="needs-triage", blocked_by="01, 03"
        )
        issue = issues.parse_issue(path)
        self.assertEqual(issue.number, 2)
        self.assertEqual(issue.type, "AFK")
        self.assertEqual(issue.blocked_by, [1, 3])
        self.assertFalse(issue.is_done)

    @ported_from("ParseIssueTests", "test_unknown_status_is_not_done")
    def test_an_unknown_status_is_not_done(self) -> None:
        path = write_issue(self.issues_dir, "01-a.md", status="in-review")
        self.assertFalse(issues.parse_issue(path).is_done)

    @ported_from("ParseIssueTests", "test_missing_type_defaults_to_afk")
    def test_a_missing_type_defaults_to_afk(self) -> None:
        path = self.issues_dir / "01-a.md"
        path.write_text("# a.md\n\nStatus: needs-triage\n\n## What to build\nBody.\n")
        self.assertEqual(issues.parse_issue(path).type, "AFK")

    @ported_from("ParseIssueTests", "test_done_status_case_insensitive")
    def test_done_is_recognised_whatever_its_case(self) -> None:
        path = write_issue(self.issues_dir, "01-a.md", status="done")
        self.assertTrue(issues.parse_issue(path).is_done)

    @ported_from("ParseIssueTests", "test_annotated_done_status_still_counts")
    def test_an_annotated_done_status_still_counts(self) -> None:
        path = write_issue(
            self.issues_dir, "01-a.md", status="Done (verified 2026-07-22, see PR)"
        )
        self.assertTrue(issues.parse_issue(path).is_done)

    @ported_from("ParseIssueTests", "test_done_prefix_word_does_not_count")
    def test_a_longer_word_beginning_with_done_does_not_count(self) -> None:
        path = write_issue(self.issues_dir, "01-a.md", status="done-ish maybe")
        self.assertFalse(issues.parse_issue(path).is_done)


class SelectIssuesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.feature_dir = Path(self.tmp.name)
        self.issues_dir = self.feature_dir / "issues"
        self.issues_dir.mkdir()

    @ported_from("SelectIssuesTests", "test_default_selection_excludes_done_and_hitl")
    def test_the_default_selection_excludes_done_and_hitl_issues(self) -> None:
        write_issue(self.issues_dir, "01-setup.md", status="Done")
        write_issue(self.issues_dir, "02-core.md", status="needs-triage", blocked_by="01")
        write_issue(
            self.issues_dir,
            "03-polish.md",
            status="needs-triage",
            type_="HITL",
            blocked_by="02",
        )
        write_issue(self.issues_dir, "04-extra.md", status="needs-triage", blocked_by="03")

        selected = issues.select_issues(self.feature_dir, None)

        self.assertEqual([i.number for i in selected], [2])

    @ported_from("SelectIssuesTests", "test_dependency_order_respected")
    def test_the_selection_comes_back_in_dependency_order(self) -> None:
        write_issue(self.issues_dir, "01-a.md", status="needs-triage")
        write_issue(self.issues_dir, "02-b.md", status="needs-triage", blocked_by="01")
        write_issue(self.issues_dir, "03-c.md", status="needs-triage", blocked_by="02")

        selected = issues.select_issues(self.feature_dir, "03,01,02")

        self.assertEqual([i.number for i in selected], [1, 2, 3])

    @ported_from(
        "SelectIssuesTests", "test_explicit_blocker_violation_raises_before_any_selection"
    )
    def test_an_explicit_selection_that_violates_a_blocker_raises(self) -> None:
        write_issue(self.issues_dir, "01-a.md", status="needs-triage")
        write_issue(self.issues_dir, "02-b.md", status="needs-triage", blocked_by="01")

        with self.assertRaises(issues.DispatchError):
            issues.select_issues(self.feature_dir, "02")

    @ported_from("SelectIssuesTests", "test_explicit_selection_of_both_satisfies_blocker")
    def test_selecting_a_blocker_alongside_what_it_blocks_satisfies_it(self) -> None:
        write_issue(self.issues_dir, "01-a.md", status="needs-triage")
        write_issue(self.issues_dir, "02-b.md", status="needs-triage", blocked_by="01")

        selected = issues.select_issues(self.feature_dir, "01,02")

        self.assertEqual([i.number for i in selected], [1, 2])

    @ported_from("SelectIssuesTests", "test_unknown_issues_reference_raises")
    def test_an_explicit_reference_to_an_issue_that_does_not_exist_raises(self) -> None:
        write_issue(self.issues_dir, "01-a.md", status="needs-triage")
        with self.assertRaises(issues.DispatchError):
            issues.select_issues(self.feature_dir, "99")

    @ported_from(
        "SelectIssuesTests", "test_unresolvable_default_chain_is_left_out_not_an_error"
    )
    def test_a_default_chain_that_cannot_resolve_is_left_out_rather_than_an_error(self) -> None:
        # 02 is blocked by 01 which is neither Done nor selected (HITL) — left
        # out silently, per spec ("left for a later run"), not a DispatchError.
        write_issue(self.issues_dir, "01-a.md", status="needs-triage", type_="HITL")
        write_issue(self.issues_dir, "02-b.md", status="needs-triage", blocked_by="01")

        selected = issues.select_issues(self.feature_dir, None)

        self.assertEqual(selected, [])

    @ported_from("SelectIssuesTests", "test_circular_blockers_raise")
    def test_blockers_that_form_a_cycle_raise(self) -> None:
        write_issue(self.issues_dir, "01-a.md", status="needs-triage", blocked_by="02")
        write_issue(self.issues_dir, "02-b.md", status="needs-triage", blocked_by="01")

        with self.assertRaises(issues.DispatchError):
            issues.select_issues(self.feature_dir, "01,02")

    @ported_from("SelectIssuesTests", "test_list_all_includes_every_issue_regardless_of_status")
    def test_loading_a_directory_covers_every_issue_regardless_of_status(self) -> None:
        # Unlike select_issues, loading is for the PR checklist and must cover
        # the whole feature — Done and HITL issues included, not just what a
        # given run would select.
        write_issue(self.issues_dir, "01-setup.md", status="Done")
        write_issue(self.issues_dir, "02-core.md", status="needs-triage", blocked_by="01")
        write_issue(
            self.issues_dir,
            "03-polish.md",
            status="needs-triage",
            type_="HITL",
            blocked_by="02",
        )

        loaded = issues.load_issues(self.issues_dir)

        self.assertEqual([i.number for i in loaded], [1, 2, 3])
        self.assertTrue(loaded[0].is_done)


class SetStatusTest(unittest.TestCase):
    """Upstream drove these through `cmd_set_status`; here they call the write directly.

    That command's own half was a return code and nothing else — there is no rendering to
    keep — so this is a whole port rather than a split.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.issues_dir = Path(self.tmp.name)

    @ported_from("SetStatusTests", "test_replaces_existing_status_line_in_place")
    def test_an_existing_status_line_is_replaced_in_place(self) -> None:
        path = write_issue(self.issues_dir, "01-a.md", status="needs-triage", blocked_by="none")
        issues.set_status(path, "Done")
        issue = issues.parse_issue(path)
        self.assertTrue(issue.is_done)
        self.assertEqual(path.read_text().count("Status:"), 1)

    @ported_from("SetStatusTests", "test_inserts_status_line_when_absent")
    def test_a_status_line_is_inserted_when_the_file_has_none(self) -> None:
        path = self.issues_dir / "01-a.md"
        path.write_text("# a.md\n\nType: AFK\nBlocked by: none\n\n## What to build\nBody.\n")
        issues.set_status(path, "Done")
        issue = issues.parse_issue(path)
        self.assertTrue(issue.is_done)


class StripFeedbackEditTextTest(unittest.TestCase):
    @ported_from(
        "StripFeedbackEditTextTests", "test_strips_comment_lines_and_trims_blank_space"
    )
    def test_comment_lines_go_and_surrounding_blank_space_is_trimmed(self) -> None:
        raw = "# branch: b1\n# base: origin/master\n\nDo the thing.\nAnd another.\n"

        self.assertEqual(issues.strip_feedback_edit_text(raw), "Do the thing.\nAnd another.")

    @ported_from("StripFeedbackEditTextTests", "test_only_leading_hash_counts_not_indented")
    def test_only_a_hash_in_column_one_counts(self) -> None:
        raw = "# comment\nreal text\n    # not a comment, indented\n"

        self.assertEqual(
            issues.strip_feedback_edit_text(raw), "real text\n    # not a comment, indented"
        )

    @ported_from("StripFeedbackEditTextTests", "test_all_comments_and_blank_strips_to_empty")
    def test_a_file_of_nothing_but_comments_and_blanks_strips_to_empty(self) -> None:
        raw = "# header\n#\n# lines starting with # are stripped\n\n\n"

        self.assertEqual(issues.strip_feedback_edit_text(raw), "")

    @ported_from("StripFeedbackEditTextTests", "test_preserves_literal_shell_metacharacters")
    def test_shell_metacharacters_survive_as_literal_text(self) -> None:
        raw = (
            '# header\n\nquotes "like this", backticks `like this`, '
            "and $(command) substitutions.\n"
        )

        self.assertEqual(
            issues.strip_feedback_edit_text(raw),
            'quotes "like this", backticks `like this`, and $(command) substitutions.',
        )


class IssueSummaryTest(unittest.TestCase):
    @ported_from("IssueSummaryTests", "test_formats_compact_marks")
    def test_verdicts_render_as_compact_marks(self) -> None:
        self.assertEqual(
            issues.issue_summary({"02": "approved", "03": "needs-work"}), "02✓ 03✗"
        )

    @ported_from("IssueSummaryTests", "test_empty_issues_is_dash")
    def test_no_issues_renders_as_a_dash(self) -> None:
        self.assertEqual(issues.issue_summary({}), "-")

    @ported_from("IssueSummaryTests", "test_sorted_numerically_not_lexically")
    def test_the_marks_sort_numerically_rather_than_lexically(self) -> None:
        self.assertEqual(issues.issue_summary({"10": "approved", "02": "approved"}), "02✓ 10✓")


class SlugifyTest(unittest.TestCase):
    @ported_from("SlugifyTests", "test_takes_first_words_lowercased")
    def test_the_first_words_are_taken_and_lowercased(self) -> None:
        self.assertEqual(
            issues.slugify("Add Foo Bar Baz Qux Quux Extra"), "add-foo-bar-baz-qux-quux"
        )

    @ported_from("SlugifyTests", "test_strips_punctuation")
    def test_punctuation_separates_rather_than_survives(self) -> None:
        self.assertEqual(issues.slugify("Fix bug: can't parse!"), "fix-bug-can-t-parse")

    @ported_from("SlugifyTests", "test_empty_text_falls_back_to_task")
    def test_empty_text_falls_back_to_the_literal_task(self) -> None:
        """`task` is a word CONTEXT.md retires; the port source asserts it by name, so it
        is ported as it stands and reported as a finding rather than quietly renamed."""
        self.assertEqual(issues.slugify(""), "task")


class MaterializeAdHocTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.specs_dir = Path(self.tmp.name)

    @ported_from(
        "MaterializeAdHocTests", "test_creates_file_under_ad_hoc_with_timestamp_and_branch"
    )
    def test_the_file_lands_under_ad_hoc_named_by_timestamp_and_branch(self) -> None:
        path_str = issues.materialize_ad_hoc(
            self.specs_dir,
            "Add a new filter\nmore detail",
            "add-new-filter",
            timestamp="20260722-120000",
        )
        path = Path(path_str)
        self.assertEqual(path.parent, self.specs_dir / "ad-hoc")
        self.assertEqual(path.name, "20260722-120000-add-new-filter.md")
        self.assertEqual(path.read_text(), "Add a new filter\nmore detail\n")

    @ported_from("MaterializeAdHocTests", "test_filename_follows_branch_not_prompt_text")
    def test_the_filename_follows_the_branch_not_the_prompt_text(self) -> None:
        # The filename is named after the branch regardless of what the
        # prompt's own first line would have slugified to.
        path_str = issues.materialize_ad_hoc(
            self.specs_dir,
            "Totally unrelated first line",
            "brand-new",
            timestamp="20260722-120000",
        )
        self.assertEqual(Path(path_str).name, "20260722-120000-brand-new.md")

    @ported_from("MaterializeAdHocTests", "test_returned_path_contains_a_slash")
    def test_the_returned_path_contains_a_slash(self) -> None:
        path_str = issues.materialize_ad_hoc(
            self.specs_dir, "quick task", "quick-task", timestamp="20260722-120000"
        )
        self.assertIn("/", path_str)


class ResolveSpecTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.specs_dir = Path(self.tmp.name)

    @ported_from("ResolveSpecTests", "test_bare_name_resolves_under_tasks_dir_with_md_appended")
    def test_a_bare_name_resolves_under_the_specs_dir_with_md_appended(self) -> None:
        self.assertEqual(
            issues.spec_check_path(self.specs_dir, "my-task"), self.specs_dir / "my-task.md"
        )

    @ported_from("ResolveSpecTests", "test_bare_name_with_md_suffix_is_not_doubled")
    def test_a_bare_name_that_already_ends_in_md_is_not_doubled(self) -> None:
        self.assertEqual(
            issues.spec_check_path(self.specs_dir, "my-task.md"), self.specs_dir / "my-task.md"
        )

    @ported_from("ResolveSpecTests", "test_path_with_slash_used_as_is")
    def test_a_path_with_a_slash_is_used_as_it_stands(self) -> None:
        self.assertEqual(
            issues.spec_check_path(self.specs_dir, "docs/FEATURE.md"), Path("docs/FEATURE.md")
        )

    @ported_from(
        "ResolveSpecTests", "test_path_with_slash_and_no_md_suffix_still_gets_md_appended"
    )
    def test_a_path_with_a_slash_and_no_md_suffix_still_gets_md_appended(self) -> None:
        # Dispatch appends ".md" unconditionally when missing, even for slash
        # paths — the picker's existence check must mirror that exactly, or
        # a valid extension-less spec path gets rejected as "not found". The
        # picker is unported (decision 1 of the F2 README), so that constraint
        # is recorded here for whoever lands it.
        self.assertEqual(
            issues.spec_check_path(self.specs_dir, "docs/FEATURE"), Path("docs/FEATURE.md")
        )

    @ported_from("ResolveSpecTests", "test_task_dir_for_feature_is_tasks_dir_slash_feature")
    def test_the_source_dir_for_a_feature_is_the_specs_dir_plus_its_name(self) -> None:
        self.assertEqual(
            issues.source_dir(self.specs_dir, "my-feature", None), self.specs_dir / "my-feature"
        )

    @ported_from("ResolveSpecTests", "test_task_dir_for_bare_spec_is_tasks_dir")
    def test_the_source_dir_for_a_bare_spec_is_the_specs_dir_itself(self) -> None:
        self.assertEqual(issues.source_dir(self.specs_dir, None, "my-task"), self.specs_dir)

    @ported_from("ResolveSpecTests", "test_task_dir_for_path_spec_is_its_parent")
    def test_the_source_dir_for_a_path_spec_is_that_paths_parent(self) -> None:
        self.assertEqual(
            issues.source_dir(self.specs_dir, None, "docs/FEATURE.md"), Path("docs")
        )

    @ported_from("ResolveSpecTests", "test_task_dir_for_pending_ad_hoc_prompt_is_ad_hoc_dir")
    def test_the_source_dir_for_a_pending_ad_hoc_prompt_is_the_ad_hoc_dir(self) -> None:
        self.assertEqual(
            issues.source_dir(self.specs_dir, None, None, "quick task"),
            self.specs_dir / "ad-hoc",
        )


class PurityTest(unittest.TestCase):
    """`bessemer/issues.py` is pure over paths and text: no subprocess, no environment.

    Not an upstream property — `tasklib.py` was one module that shelled out freely — but the
    criterion this issue was given, and the same shape `tests/test_config_purity.py` proves
    for `config`. Two angles: a static walk that sees code no test happens to run, and a
    fresh interpreter that catches an import arriving through a helper.
    """

    def module_source(self) -> str:
        origin = issues.__file__
        assert origin is not None, "bessemer.issues must be importable from a source tree"
        return Path(origin).read_text(encoding="utf-8")

    def imported_modules(self) -> set[str]:
        found: set[str] = set()
        for node in ast.walk(ast.parse(self.module_source())):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    base = f"{issues.__package__}.{base}" if base else str(issues.__package__)
                found.add(base)
                found.update(f"{base}.{alias.name}" for alias in node.names)
        return found

    def test_the_source_really_was_read(self) -> None:
        """Every assertion below passes on an empty string. This is what stops that."""
        self.assertIn("def select_issues(", self.module_source())

    def test_the_module_crosses_no_spawn_boundary(self) -> None:
        self.assertEqual(violations(MODULE_PATH, self.module_source()), [])

    def test_the_module_does_not_import_the_subprocess_wrapper(self) -> None:
        """Invisible to the walk above: `bessemer.proc` is the one module that may spawn."""
        imported = self.imported_modules()
        self.assertNotIn(WRAPPER_MODULE, imported)
        self.assertFalse({name for name in imported if name.startswith(f"{WRAPPER_MODULE}.")})

    def test_the_module_reads_no_environment(self) -> None:
        """`os` is not imported at all, which is stronger than banning `os.environ` and is
        the only version a static walk can state without chasing aliases. If something here
        ever needs the environment, that is a finding for the port report — issue selection
        is a question about files a user wrote.

        The second half is a belt on top of that: any name spelled `environ`, `getenv` or
        `putenv`, however it arrived. Read off the AST rather than the text, because the
        word *environment* appears in this module's own prose and a substring search reports
        that as a violation — measured, on the first run of this test.
        """
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
            "import sys, bessemer.issues; "
            "print('subprocess' in sys.modules, 'bessemer.proc' in sys.modules)"
        )
        result = proc.run([sys.executable, "-I", "-c", probe], timeout=60)
        self.assertTrue(result.ok, result.stderr)
        self.assertEqual(result.stdout.strip(), "False False")


if __name__ == "__main__":
    unittest.main()
