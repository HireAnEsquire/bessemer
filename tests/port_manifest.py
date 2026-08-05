"""The 337 upstream tests, classified — F2's drift control.

The port source is `/Users/sbowles/hae`, branch `agentbox`, commit `e194121f75f4`, file
`.agentbox/test_tasklib.py`: 3703 lines, 337 tests in 56 classes. F2 brings its data core
across, and the failure mode F2 is exposed to is not a weak test — the tests come from
upstream and are the oracle — it is **drift**: a test dropped, renamed away, split in half
and half-landed, or excluded for a reason that sounds better than it is. Nothing in a
review loop notices twelve tests becoming nine, and an agent comparing a 3703-line file by
eye is being asked to do the one thing it is worst at.

So the names below are the census, and `tests/test_port_manifest.py` is the check over it.

**Vendored, not read from the port source.** CI has no `/Users/sbowles/hae`, and a check
that only runs on one laptop is not a check. These names are a copy; the copy is the
artifact. They were transcribed by walking the upstream file's AST at the pinned commit,
so the list is a faithful copy rather than a retyping — what is hand-written, and what
therefore catches a name going missing, is the census in `tests/test_port_manifest.py`:
the total of 337 and the per-class counts, restated there by hand so that shrinking this
file costs a deliberate edit in a second one.

**What is pinned is the counts, not the names.** The correspondence between these strings
and upstream's was established once, by comparison against the pinned commit, and is never
re-checked — nothing here can re-check it, because the port source is not readable from
CI. So mistyping a name, or swapping one class's three names for another's three, stays
green: the counts are unchanged. That is inherent rather than an oversight, but "vendored
census" reads as pinning more than it does, and this is the sentence that stops it.

## The four dispositions

`PENDING`
    Will be ported; no counterpart in bessemer's suite yet. This is the state everything
    portable starts in, because this manifest lands before any of the work it describes —
    issue 00 of F2 blocks issues 01 through 04. A suite that is red on purpose for the
    length of a feature is a suite nobody reads, which is worse than the hole it papers
    over, so pending is green.

    Pending is safe in the direction that matters. Flipping an entry to `PORTED` without
    writing the test fails immediately, because the check then demands a counterpart that
    is not there. The only available cheat is leaving entries pending forever, and that is
    a visible number — the check prints it on every run — rather than an absence. Each
    port issue flips its own slice as part of its acceptance, and F2's tracer requires
    zero pending.

`PORTED`
    Landed, with exactly one counterpart recorded: the test module it lives in and the
    name it goes by there. Upstream's name is the key and bessemer's name is recorded
    beside it, because bessemer's suite favours sentence-style names and upstream does
    not — so the mapping is the artifact rather than a coincidence of spelling.

`PORTED_SPLIT`
    A `cmd_*` test whose two halves land in different files (decision 5 of the F2 README).
    Each such test typically asserts two things: what was computed and what was printed.
    The computation half lands in the core module's tests; the rendering half becomes a
    CLI test against `bessemer/cli.py`. Both destinations are recorded, in that order, so
    a test that loses its rendering half on the way is visible rather than merely gone.

    Not every `cmd_*` test splits. Decision 5 was refined during issue 01: a subcommand that
    exists only because the port source's `run.sh` is bash and had to spawn python to reach
    a function is excluded rather than split, because bessemer's dispatcher calls the
    function directly. `CmdFeedbackEditStripTests` was the first such case, and issues 02
    and 03 excluded five more. The first real use is issue 04's `CmdStatusTests`: `status`
    is a subcommand a human types, so each of its three tests lands in two halves.

`EXCLUDED`
    Not coming, with a reason in prose that says why bessemer is better without it. Never
    a bare flag: an exclusion is reviewable only if the reason is committed beside it.

    Exclusion is per test, not per class. Ten classes are excluded whole, but two tests
    in otherwise in-scope classes reach `_migrate_legacy_ledgers` without naming it, and
    decision 4 drops the behaviour rather than the function. `tests/test_port_manifest.py`
    enumerates those two by name rather than counting them, because a partial exclusion is
    exactly the kind that hides inside a class-level count.

## What this manifest cannot prove

**The counterpart rule is name-based, and that is a deliberate weakness.** This file can
prove that a test with the recorded name exists in bessemer's suite and declares itself
the counterpart of a named upstream test. It cannot prove that test still asserts what
upstream's did. A ported test gutted to `pass` satisfies every check here.

What closes that gap is not machinery. It is the port issues' own requirement to keep
assertions intact, and a reviewer reading the exclusions.

## The binding is two-way, via a marker on the test

A manifest entry names its counterpart, and the counterpart names its manifest entry back,
through the `ported_from` decorator below. Both directions are checked:

- an entry marked `PORTED` or `PORTED_SPLIT` whose counterpart is missing, renamed, or
  moved to another module fails — that is the shrinkage direction;
- a test in bessemer's suite carrying a `ported_from` marker that no entry claims fails —
  that is the growth direction, and it is what keeps a renamed port and an unported test
  from looking identical. Without it the manifest quietly stops describing the suite.

**A counterpart must be somewhere `unittest discover` actually collects.** Recording one
in a helper module, or under a name unittest does not treat as a test, would satisfy a
naive binding while running nothing — worse than the gutted-to-`pass` weakness above,
because a reviewer reading assertions cannot see it either. So the module's last segment
must begin with `test_` and the counterpart's own name with `test`, and the class defining
it must be a `unittest.TestCase`. All three are asserted.

**A marker is how "upstream-derived" is identified, and its limit is that it is opt-in.**
Nothing detects a test that was copied from upstream and left unmarked; that test simply
reads as bessemer's own, and its manifest entry stays pending — a number that is printed,
not an absence. The alternative — treating every test in a port destination module as
upstream-derived — would need an exemption list for the tests F2 writes for code upstream
did not have, and an exemption list is a quieter escape hatch than a pending count.

The marker is read off the class that defines the method, so a counterpart **inherited**
from a shared base class is invisible to the check. No such base exists in this suite; if
one appears, this is the line that stops being true.

## This is a test artifact

Nothing under `bessemer/` imports this module, and `tests/test_port_manifest.py` enforces
that. The census of a suite is not runtime state.
"""

from collections.abc import Callable
from typing import NamedTuple

UPSTREAM_COMMIT = "e194121f75f4"
UPSTREAM_FILE = ".agentbox/test_tasklib.py"

PENDING = "pending"
PORTED = "ported"
PORTED_SPLIT = "ported-split"
EXCLUDED = "excluded"

#: The attribute `ported_from` writes, and `tests/test_port_manifest.py` reads. Named
#: rather than spelled twice, because the two sides drifting is the one thing that would
#: make the reverse check silently stop finding anything.
PORTED_FROM_ATTRIBUTE = "__bessemer_ported_from__"


class Counterpart(NamedTuple):
    """Where a ported test landed: the test module, and the name it goes by there."""

    module: str
    test: str


class Entry(NamedTuple):
    """One upstream test. `MANIFEST` maps upstream class name to a tuple of these."""

    upstream_test: str
    disposition: str
    counterparts: tuple[Counterpart, ...]
    reason: str


def pending(upstream_test: str) -> Entry:
    """Portable, not yet landed."""
    return Entry(upstream_test, PENDING, (), "")


def ported(upstream_test: str, counterpart: Counterpart) -> Entry:
    """Landed whole, in one place."""
    return Entry(upstream_test, PORTED, (counterpart,), "")


def split(upstream_test: str, computation: Counterpart, rendering: Counterpart) -> Entry:
    """Landed in two halves, computation first — decision 5 of the F2 README."""
    return Entry(upstream_test, PORTED_SPLIT, (computation, rendering), "")


def excluded(upstream_test: str, reason: str) -> Entry:
    """Not coming, for the stated reason."""
    return Entry(upstream_test, EXCLUDED, (), reason)


def ported_from[F: Callable[..., object]](
    upstream_class: str, upstream_test: str
) -> Callable[[F], F]:
    """Declare that this test is the counterpart of one upstream test.

    The manifest names the counterpart and the counterpart names the entry back; the check
    requires both. Applied to the two halves of a `PORTED_SPLIT` entry, both carry the same
    upstream key and are told apart by the module they live in.

    `setattr` rather than plain assignment because a function is not typed as carrying
    arbitrary attributes, and the alternative is a cast that says less.
    """

    def mark(test: F) -> F:
        setattr(test, PORTED_FROM_ATTRIBUTE, (upstream_class, upstream_test))
        return test

    return mark


# ---------------------------------------------------------------------------------------
# Exclusion reasons. One per upstream class, because the classes are excluded as units and
# 132 copies of one sentence would be a worse artifact than eight specific ones.
# ---------------------------------------------------------------------------------------

_PICKER_SCOPE = (
    "Decision 1 of the F2 README puts the interactive picker out of scope: it is a human "
    "frontend for choosing what to dispatch, so it depends on dispatch existing and on a "
    "ledger with real runs in it. Porting it now means porting a terminal UI against a "
    "dispatcher that does not exist, and it would force a decision about shelling out to "
    "gum — a runtime binary the zero-dependency posture has never had to consider. The "
    "picker is a later feature with its own decision; bessemer is better without half of "
    "it landing early against nothing."
)

PICKER_TASK_SOURCE = (
    f"The picker's first step, choosing between a feature, a one-off spec and an ad-hoc "
    f"prompt. {_PICKER_SCOPE}"
)

PICKER_ISSUES = (
    f"The picker's issue-selection step, walking a feature's issues and their blocked-by "
    f"graph in a terminal menu. {_PICKER_SCOPE}"
)

PICKER_BRANCH = (
    f"The picker's branch step, offering existing working branches and the create-new "
    f"path. {_PICKER_SCOPE}"
)

PICKER_BASE = (
    f"The picker's base step, offering the ref a run's pull request targets. {_PICKER_SCOPE}"
)

PICKER_RESUME = (
    f"The picker's resume step, which reads the ledger for branches worth resuming and so "
    f"needs a ledger with real runs in it — something no bessemer install has yet. "
    f"{_PICKER_SCOPE}"
)

PICKER_CMD = (
    f"The picker's top-level command, wiring every step above together and dispatching the "
    f"result. It is the one class that cannot be ported before any of the others. "
    f"{_PICKER_SCOPE}"
)

PICKER_GUM = (
    f"The gum wrappers themselves. Porting them is what decides that gum is a runtime "
    f"binary bessemer shells out to, which is precisely the decision the picker's deferral "
    f"exists to defer — and F5 may retire gum entirely in favour of a stdlib prompts "
    f"module. {_PICKER_SCOPE}"
)

PICKER_SUMMARY_MENU = (
    f"The picker's confirmation menu: it patches shutil.which for gum detection and "
    f"asserts on the arguments handed to gum_choose, so it is picker scope by what it "
    f"exercises rather than by what it is named. Decision 1 listed seven classes assembled "
    f"by class name and missed this eighth one; the human extended the decision to it "
    f"during issue 00, taking the picker exclusion to 132 tests rather than 127. "
    f"{_PICKER_SCOPE}"
)

_MIGRATION_SCOPE = (
    "Decision 4 of the F2 README drops _migrate_legacy_ledgers deliberately. The migration "
    "is dead on arrival: bessemer has zero installs, so it can only ever run against files "
    "it never created, so any test of it passes forever while proving nothing, and a suite "
    "is better without green tests that cannot fail for a real reason. Note that gc is not "
    "dropped with it — its scan and render are pure functions over rows and paths, testable "
    "now against fixtures, and F3 needs them the day it lands."
)

SHIM_FOR_BASH = (
    "The whole of cmd_feedback_edit_strip is a three-line shim — read stdin, print, return "
    "0 — and the computation it wraps is already covered by StripFeedbackEditTextTests, "
    "which is ported. The subcommand exists only because the port source's run.sh is bash "
    "and had to spawn python to reach that function; bessemer's dispatcher is python and "
    "calls strip_feedback_edit_text directly, so the subcommand crosses a boundary the "
    "rewrite deletes. Porting it would add a user-facing CLI surface for a flow nobody can "
    "reach until F3, which is the stub-is-a-claim rule F1 was built on. This refines "
    "decision 5 of the F2 README: a cmd_* test splits when the subcommand is one a human "
    "would type, and is excluded when the subcommand exists so bash could reach python."
)

_LEDGER_SHIM_SCOPE = (
    "measured rather than argued: run.sh invokes it and nothing else does, and its output "
    "is written for bash to parse. A python dispatcher calls the ledger functions directly, "
    "so the subcommand crosses the boundary the rewrite exists to delete, and porting it "
    "would add a user-facing CLI surface for a flow nobody can reach until F3. Decision 5 "
    "of the F2 README: a cmd_* test splits when a human would type the subcommand, and is "
    "excluded when the subcommand exists so bash could reach python. Excluding a shim must "
    "not drop behaviour with it — what this class asserted and nothing else does landed in "
    "bessemer/ledger.py with tests of bessemer's own, unmarked, because no upstream test "
    "covers those pieces separately and this manifest must not claim one does."
)

CMD_LEDGER_SHIM = (
    f"ledger-append (run.sh:1622) and ledger-last-base (run.sh:881) are bash-to-python "
    f"shims, {_LEDGER_SHIM_SCOPE} This class's own claims are dispositioned below, using the "
    f"three dispositions the F2 README names. Covered elsewhere: the ledger's location "
    f"comes from the specs "
    f"directory, which is CentralLedgerPathTests, ported. Landed: --task-dir is recorded as "
    f"a fact, which is RunRecordTest, and the --issue NUM=OUTCOME parsing and its refusal, "
    f"which is ParseIssueOutcomesTest, both in tests/test_ledger.py. Asserted upstream and "
    f"structurally unreachable here: the *negative* half of the same claim — that nothing "
    f"was written to the source directory, and that a rejected argument writes no central "
    f"file at all. Both were assertFalse(...exists()) against a subcommand that owned the "
    f"whole flow; run_record returns a dict and append_ledger writes where it is told, so "
    f"'nothing was written' has no host in the new shape and no test here covers it."
)

CMD_LAST_SHIM = (
    f"last (run.sh:697) is a bash-to-python shim, {_LEDGER_SHIM_SCOPE} It is the clearest "
    f"case in the feature: run.sh reads its output with IFS='=' read -r, so the key=value "
    f"format exists solely for bash, and the --last flag it backs is a dispatch option in "
    f"bessemer rather than a subcommand. Both of its assertions survive in ResolveLastTests, "
    f"which is ported whole: the newest record wins, and an empty ledger refuses with 'no "
    f"runs recorded yet'."
)

_RESUME_SHIM_SCOPE = (
    "measured rather than argued: run.sh invokes it and nothing else does, and its output is "
    "written for bash to parse with `IFS='=' read -r`. A python dispatcher calls "
    "resolve_resume and resume_dispatch_action directly, so the subcommand crosses the "
    "boundary the rewrite exists to delete, and porting it would add a user-facing CLI "
    "surface for a flow nobody can reach until F3. Decision 5 of the F2 README: a cmd_* test "
    "splits when a human would type the subcommand, and is excluded when the subcommand "
    "exists so bash could reach python. --resume is a dispatch flag in bessemer, never a "
    "subcommand of its own."
)

CMD_RESUME_SHIM = (
    f"resume (run.sh:737) is a bash-to-python shim, {_RESUME_SHIM_SCOPE} Its two claims are "
    f"dispositioned with the three dispositions the F2 README names. Covered elsewhere: the "
    f"six recovered fields its tests asserted — mode, feature, spec, task_dir_exists, "
    f"identity, pr_url — are asserted directly in ResolveResumeTests, ported whole, and so is "
    f"the not-found refusal and its recent-branches hint. The seventh printed field, "
    f"task_dir, upstream printed and never asserted anywhere, so no coverage existed to "
    f"preserve; decision 9 of the F2 README records ResumeInfo.source_dir as F3's debt, and a "
    f"data-layer test asserting it here would claim the wrong owner. Landed: the claim that a "
    f"refused resolution yields no "
    f"partial answer, as test_a_refused_resume_yields_no_partial_answer in "
    f"tests/test_resume.py, unmarked, because no upstream test covers it separately. Asserted "
    f"upstream and structurally unreachable here: the exit code 2, and the empty-string "
    f"rendering of a null feature or spec. Both are properties of a key=value stdout contract "
    f"that has no host once ResumeInfo is returned to a python caller."
)

CMD_RESUME_GUARD_SHIM = (
    f"resume-guard (run.sh:848) is a bash-to-python shim, {_RESUME_SHIM_SCOPE} Covered "
    f"elsewhere: both of its tests assert an action, and a message only when one is set — "
    f"which is exactly what ResumeDispatchActionTests asserts against the function itself, "
    f"all eight ported. Asserted upstream and structurally unreachable here: that the message "
    f"line is omitted entirely for a normal action. resume_dispatch_action returns (action, "
    f"None) and a python caller reads the None, so there is no line to omit, and no test here "
    f"covers the omission."
)

MIGRATION_DEAD_ON_ARRIVAL = f"A direct test of the dropped function. {_MIGRATION_SCOPE}"

MIGRATION_REACHED_INDIRECTLY = (
    f"Not a test of the dropped function by name, but it writes a per-directory runs.jsonl "
    f"and asserts the central file is created from it — which is _migrate_legacy_ledgers, "
    f"one of the six sites tasklib.py calls it from. Left pending it would be unportable: "
    f"issue 04 could only flip it by resurrecting the function decision 4 drops, against a "
    f"tracer that requires zero pending. The general lesson, now in the F2 README: a "
    f"deletion decision scoped by counting a function's own test class always undercounts "
    f"it, because the scope is every test that reaches the behaviour. {_MIGRATION_SCOPE}"
)


#: Destination modules, spelled once. A counterpart's module is repeated across dozens of
#: entries, and a typo in one of those repetitions is a failure whose message is about a
#: missing marker rather than about a misspelling.
ISSUES = "tests.test_issues"
LEDGER = "tests.test_ledger"
RESUME = "tests.test_resume"
STATUS = "tests.test_status"
CLI = "tests.test_cli"


# ---------------------------------------------------------------------------------------
# The census. Order and grouping follow the upstream file; the counts that pin it are
# hand-written in tests/test_port_manifest.py.
# ---------------------------------------------------------------------------------------

MANIFEST: dict[str, tuple[Entry, ...]] = {
    "ParseIssueTests": (
        ported(
            "test_tolerant_parsing_and_multiple_blockers",
            Counterpart(
                ISSUES, "test_a_header_parses_and_several_blockers_come_back_as_numbers"
            ),
        ),
        ported(
            "test_unknown_status_is_not_done",
            Counterpart(ISSUES, "test_an_unknown_status_is_not_done"),
        ),
        ported(
            "test_missing_type_defaults_to_afk",
            Counterpart(ISSUES, "test_a_missing_type_defaults_to_afk"),
        ),
        ported(
            "test_done_status_case_insensitive",
            Counterpart(ISSUES, "test_done_is_recognised_whatever_its_case"),
        ),
        ported(
            "test_annotated_done_status_still_counts",
            Counterpart(ISSUES, "test_an_annotated_done_status_still_counts"),
        ),
        ported(
            "test_done_prefix_word_does_not_count",
            Counterpart(ISSUES, "test_a_longer_word_beginning_with_done_does_not_count"),
        ),
    ),
    "SelectIssuesTests": (
        ported(
            "test_default_selection_excludes_done_and_hitl",
            Counterpart(ISSUES, "test_the_default_selection_excludes_done_and_hitl_issues"),
        ),
        ported(
            "test_dependency_order_respected",
            Counterpart(ISSUES, "test_the_selection_comes_back_in_dependency_order"),
        ),
        ported(
            "test_explicit_blocker_violation_raises_before_any_selection",
            Counterpart(ISSUES, "test_an_explicit_selection_that_violates_a_blocker_raises"),
        ),
        ported(
            "test_explicit_selection_of_both_satisfies_blocker",
            Counterpart(
                ISSUES, "test_selecting_a_blocker_alongside_what_it_blocks_satisfies_it"
            ),
        ),
        ported(
            "test_unknown_issues_reference_raises",
            Counterpart(
                ISSUES, "test_an_explicit_reference_to_an_issue_that_does_not_exist_raises"
            ),
        ),
        ported(
            "test_unresolvable_default_chain_is_left_out_not_an_error",
            Counterpart(
                ISSUES,
                "test_a_default_chain_that_cannot_resolve_is_left_out_rather_than_an_error",
            ),
        ),
        ported(
            "test_circular_blockers_raise",
            Counterpart(ISSUES, "test_blockers_that_form_a_cycle_raise"),
        ),
        ported(
            "test_list_all_includes_every_issue_regardless_of_status",
            Counterpart(
                ISSUES, "test_loading_a_directory_covers_every_issue_regardless_of_status"
            ),
        ),
    ),
    "LedgerTests": (
        ported(
            "test_append_writes_one_well_formed_line",
            Counterpart(LEDGER, "test_appending_writes_one_well_formed_json_line"),
        ),
        ported(
            "test_append_is_one_line_per_call",
            Counterpart(LEDGER, "test_each_append_adds_exactly_one_line"),
        ),
        ported(
            "test_last_base_for_branch_returns_most_recent",
            Counterpart(LEDGER, "test_the_last_base_for_a_branch_is_its_most_recent_one"),
        ),
        ported(
            "test_last_base_for_unknown_branch_is_none",
            Counterpart(LEDGER, "test_a_branch_that_never_ran_has_no_last_base"),
        ),
        ported(
            "test_missing_ledger_degrades_to_none",
            Counterpart(LEDGER, "test_a_missing_ledger_reads_as_no_records"),
        ),
        ported(
            "test_corrupt_ledger_lines_are_skipped_not_fatal",
            Counterpart(LEDGER, "test_a_malformed_line_is_skipped_rather_than_raised"),
        ),
        ported(
            "test_undecodable_ledger_degrades_to_none_not_fatal",
            Counterpart(LEDGER, "test_a_ledger_that_is_not_text_reads_as_no_records"),
        ),
        ported(
            "test_deleting_ledger_mid_feature_next_run_defaults_to_none",
            Counterpart(
                LEDGER, "test_deleting_the_ledger_mid_feature_drops_the_next_runs_default"
            ),
        ),
        ported(
            "test_append_ledger_write_failure_does_not_raise",
            Counterpart(LEDGER, "test_a_write_failure_does_not_raise"),
        ),
    ),
    "CentralLedgerPathTests": (
        ported(
            "test_is_tasks_dir_parent_slash_runs_jsonl",
            Counterpart(LEDGER, "test_the_ledger_sits_beside_the_specs_directory"),
        ),
    ),
    "CmdLedgerAppendLastBaseTests": (
        excluded("test_cmd_ledger_append_parses_issue_flags", CMD_LEDGER_SHIM),
        excluded("test_cmd_ledger_append_rejects_malformed_issue_flag", CMD_LEDGER_SHIM),
        excluded("test_cmd_ledger_append_writes_to_central_file_not_task_dir", CMD_LEDGER_SHIM),
        excluded("test_cmd_ledger_last_base_reads_central_file", CMD_LEDGER_SHIM),
        excluded("test_cmd_ledger_last_base_ignores_task_dir_scoping", CMD_LEDGER_SHIM),
        excluded("test_cmd_ledger_last_base_no_record_prints_nothing", CMD_LEDGER_SHIM),
    ),
    "NewestRecordForBranchTests": (
        ported(
            "test_returns_most_recent_record_regardless_of_task_dir",
            Counterpart(
                LEDGER, "test_the_newest_record_for_a_branch_ignores_which_source_it_ran_from"
            ),
        ),
        ported(
            "test_no_record_returns_none",
            Counterpart(LEDGER, "test_a_branch_with_no_record_has_no_newest_one"),
        ),
    ),
    "RecentDistinctBranchesTests": (
        ported(
            "test_newest_first_deduped",
            Counterpart(LEDGER, "test_recent_branches_come_back_newest_first_and_deduped"),
        ),
        ported(
            "test_respects_limit",
            Counterpart(LEDGER, "test_recent_branches_stop_at_the_limit"),
        ),
        ported(
            "test_empty_ledger_returns_empty",
            Counterpart(LEDGER, "test_an_empty_ledger_has_no_recent_branches"),
        ),
    ),
    "ResolveResumeTests": (
        ported(
            "test_feature_mode_task_dir_is_the_feature_folder",
            Counterpart(RESUME, "test_a_feature_runs_source_directory_is_the_feature_folder"),
        ),
        ported(
            "test_spec_mode_reconstructs_full_spec_path",
            Counterpart(RESUME, "test_a_spec_runs_full_path_is_rebuilt_from_the_record"),
        ),
        ported(
            "test_feature_dir_deleted_from_disk_is_not_exists",
            Counterpart(RESUME, "test_a_feature_folder_deleted_from_disk_no_longer_exists"),
        ),
        ported(
            "test_spec_file_individually_deleted_is_not_exists",
            Counterpart(RESUME, "test_a_spec_file_deleted_on_its_own_no_longer_exists"),
        ),
        ported(
            "test_null_task_dir_from_a_prior_feedback_only_run_is_not_exists",
            Counterpart(
                RESUME, "test_a_null_source_from_an_earlier_feedback_only_run_does_not_exist"
            ),
        ),
        ported(
            "test_mode_recovers_from_a_feedback_only_run_that_carried_the_identity_forward",
            Counterpart(
                RESUME,
                "test_mode_survives_a_feedback_only_run_that_carried_the_identity_forward",
            ),
        ),
        ported(
            "test_branch_not_in_ledger_raises_with_recent_branches_hint",
            Counterpart(
                RESUME, "test_a_branch_the_ledger_never_saw_refuses_and_lists_the_ones_it_did"
            ),
        ),
        ported(
            "test_empty_ledger_raises_with_no_hint",
            Counterpart(RESUME, "test_an_empty_ledger_refuses_with_nothing_to_suggest"),
        ),
        ported(
            "test_identity_is_the_feature_basename_even_with_null_task_dir",
            Counterpart(
                RESUME, "test_identity_is_the_feature_basename_even_with_no_source_recorded"
            ),
        ),
        ported(
            "test_identity_is_the_spec_basename",
            Counterpart(RESUME, "test_identity_is_the_spec_basename"),
        ),
        ported(
            "test_pr_url_is_carried_from_the_record",
            Counterpart(RESUME, "test_the_pull_request_is_carried_out_of_the_record"),
        ),
        ported(
            "test_pr_url_is_none_when_absent",
            Counterpart(RESUME, "test_a_record_with_no_pull_request_carries_none"),
        ),
    ),
    "ResumeDispatchActionTests": (
        ported(
            "test_normal_when_feature_has_runnable_issues",
            Counterpart(RESUME, "test_a_feature_with_issues_left_to_run_continues_normally"),
        ),
        ported(
            "test_normal_for_spec_mode_regardless_of_issue_count",
            Counterpart(
                RESUME, "test_a_spec_run_continues_normally_whatever_the_issue_count_says"
            ),
        ),
        ported(
            "test_refuse_when_feature_selection_empty_and_no_feedback",
            Counterpart(
                RESUME, "test_a_feature_with_nothing_to_run_and_no_feedback_is_refused"
            ),
        ),
        ported(
            "test_feedback_only_when_feature_selection_empty_and_feedback_given",
            Counterpart(
                RESUME,
                "test_a_feature_with_nothing_to_run_but_feedback_given_runs_the_feedback",
            ),
        ),
        ported(
            "test_refuse_when_task_dir_missing_and_no_feedback",
            Counterpart(RESUME, "test_a_gone_source_directory_with_no_feedback_is_refused"),
        ),
        ported(
            "test_feedback_only_when_task_dir_missing_and_feedback_given",
            Counterpart(RESUME, "test_a_gone_source_directory_with_feedback_runs_the_feedback"),
        ),
        ported(
            "test_hard_error_when_task_dir_missing_and_issues_arg_given",
            Counterpart(
                RESUME, "test_naming_issues_against_a_gone_source_directory_is_a_hard_error"
            ),
        ),
        ported(
            "test_hard_error_beats_feedback_only_when_task_dir_missing",
            Counterpart(
                RESUME, "test_a_hard_error_beats_feedback_when_the_source_directory_is_gone"
            ),
        ),
    ),
    "CmdResumeTests": (
        excluded("test_prints_recovered_fields_on_success", CMD_RESUME_SHIM),
        excluded("test_exit_2_and_hint_on_stderr_when_branch_not_found", CMD_RESUME_SHIM),
    ),
    "CmdResumeGuardTests": (
        excluded("test_prints_action_only_when_normal", CMD_RESUME_GUARD_SHIM),
        excluded("test_prints_message_when_refusing", CMD_RESUME_GUARD_SHIM),
    ),
    "ResolveLastTests": (
        ported(
            "test_returns_newest_record_overall_regardless_of_branch",
            Counterpart(
                LEDGER, "test_the_last_run_is_the_newest_record_whatever_branch_it_was_on"
            ),
        ),
        ported(
            "test_no_outcome_filtering_a_failed_run_can_be_newest",
            Counterpart(LEDGER, "test_a_failed_run_can_be_the_last_one"),
        ),
        ported(
            "test_empty_ledger_raises",
            Counterpart(LEDGER, "test_an_empty_ledger_refuses_rather_than_returning_nothing"),
        ),
    ),
    "CmdLastTests": (
        excluded("test_prints_newest_record_fields", CMD_LAST_SHIM),
        excluded("test_empty_ledger_exits_2_with_refusal_on_stderr", CMD_LAST_SHIM),
    ),
    "StripFeedbackEditTextTests": (
        ported(
            "test_strips_comment_lines_and_trims_blank_space",
            Counterpart(ISSUES, "test_comment_lines_go_and_surrounding_blank_space_is_trimmed"),
        ),
        ported(
            "test_only_leading_hash_counts_not_indented",
            Counterpart(ISSUES, "test_only_a_hash_in_column_one_counts"),
        ),
        ported(
            "test_all_comments_and_blank_strips_to_empty",
            Counterpart(
                ISSUES, "test_a_file_of_nothing_but_comments_and_blanks_strips_to_empty"
            ),
        ),
        ported(
            "test_preserves_literal_shell_metacharacters",
            Counterpart(ISSUES, "test_shell_metacharacters_survive_as_literal_text"),
        ),
    ),
    "CmdFeedbackEditStripTests": (
        excluded("test_reads_stdin_strips_and_prints", SHIM_FOR_BASH),
    ),
    "MigrateLegacyLedgersTests": (
        excluded("test_merges_and_stamps_task_dir_timestamp_sorted", MIGRATION_DEAD_ON_ARRIVAL),
        excluded("test_original_files_left_untouched", MIGRATION_DEAD_ON_ARRIVAL),
        excluded("test_no_legacy_files_no_op", MIGRATION_DEAD_ON_ARRIVAL),
        excluded("test_missing_tasks_dir_no_op", MIGRATION_DEAD_ON_ARRIVAL),
        excluded("test_corrupt_legacy_file_skipped_silently", MIGRATION_DEAD_ON_ARRIVAL),
        excluded("test_already_migrated_is_a_no_op", MIGRATION_DEAD_ON_ARRIVAL),
    ),
    "ParseDockerRowsTests": (
        ported(
            "test_filters_to_agentbox_prefixed_containers",
            Counterpart(STATUS, "test_only_containers_with_the_bessemer_prefix_come_back"),
        ),
        ported(
            "test_skips_blank_lines",
            Counterpart(STATUS, "test_blank_lines_are_skipped"),
        ),
        ported(
            "test_row_with_no_status_field_is_tolerated",
            Counterpart(STATUS, "test_a_row_with_no_status_field_is_tolerated"),
        ),
        ported(
            "test_empty_input_returns_empty",
            Counterpart(STATUS, "test_empty_input_parses_to_no_containers"),
        ),
    ),
    "StaleLocksTests": (
        ported(
            "test_lock_with_no_matching_container_is_stale",
            Counterpart(STATUS, "test_a_lock_with_no_matching_container_is_stale"),
        ),
        ported(
            "test_lock_with_matching_container_is_not_stale",
            Counterpart(STATUS, "test_a_lock_with_a_matching_container_is_not_stale"),
        ),
        ported(
            "test_missing_locks_dir_returns_empty",
            Counterpart(STATUS, "test_a_missing_locks_directory_has_no_stale_locks"),
        ),
        ported(
            "test_results_are_sorted",
            Counterpart(STATUS, "test_the_results_come_back_sorted"),
        ),
    ),
    "CollectRecentLedgerRecordsTests": (
        ported(
            "test_gathers_from_central_file_newest_first",
            Counterpart(LEDGER, "test_records_come_back_from_the_central_file_newest_first"),
        ),
        ported(
            "test_no_ledger_returns_empty",
            Counterpart(LEDGER, "test_no_ledger_collects_no_records"),
        ),
        ported(
            "test_corrupt_ledger_is_skipped_not_fatal",
            Counterpart(
                LEDGER, "test_a_corrupt_ledger_collects_no_records_rather_than_raising"
            ),
        ),
        excluded(
            "test_triggers_legacy_migration_when_central_file_missing",
            MIGRATION_REACHED_INDIRECTLY,
        ),
    ),
    "IssueSummaryTests": (
        ported(
            "test_formats_compact_marks",
            Counterpart(ISSUES, "test_verdicts_render_as_compact_marks"),
        ),
        ported(
            "test_empty_issues_is_dash",
            Counterpart(ISSUES, "test_no_issues_renders_as_a_dash"),
        ),
        ported(
            "test_sorted_numerically_not_lexically",
            Counterpart(ISSUES, "test_the_marks_sort_numerically_rather_than_lexically"),
        ),
    ),
    "OverallOutcomeTests": (
        ported(
            "test_one_off_approved_outcome",
            Counterpart(STATUS, "test_a_one_off_approved_outcome_renders_with_a_tick"),
        ),
        ported(
            "test_one_off_needs_work_outcome",
            Counterpart(STATUS, "test_a_one_off_needs_work_outcome_renders_with_a_warning"),
        ),
        ported(
            "test_feature_all_issues_approved",
            Counterpart(STATUS, "test_a_feature_with_every_issue_approved_is_approved"),
        ),
        ported(
            "test_feature_partial_approval",
            Counterpart(STATUS, "test_a_partially_approved_feature_shows_the_fraction"),
        ),
        ported(
            "test_no_outcome_or_issues_is_dash",
            Counterpart(STATUS, "test_no_outcome_and_no_issues_renders_as_a_dash"),
        ),
    ),
    "AgeTests": (
        ported(
            "test_missing_timestamp_is_unknown",
            Counterpart(STATUS, "test_a_missing_timestamp_is_unknown"),
        ),
        ported(
            "test_malformed_timestamp_is_unknown",
            Counterpart(STATUS, "test_a_malformed_timestamp_is_unknown"),
        ),
        ported(
            "test_recent_timestamp_is_seconds_ago",
            Counterpart(STATUS, "test_a_recent_timestamp_renders_in_seconds"),
        ),
        ported(
            "test_minutes_ago",
            Counterpart(STATUS, "test_an_age_in_minutes_renders_in_minutes"),
        ),
        ported(
            "test_hours_ago",
            Counterpart(STATUS, "test_an_age_in_hours_renders_in_hours"),
        ),
        ported(
            "test_days_ago",
            Counterpart(STATUS, "test_an_age_in_days_renders_in_days"),
        ),
    ),
    "FormatTableTests": (
        ported(
            "test_aligns_columns_and_leaves_last_unpadded",
            Counterpart(STATUS, "test_columns_align_and_the_last_is_left_unpadded"),
        ),
        ported(
            "test_truncates_only_marked_columns",
            Counterpart(STATUS, "test_only_marked_columns_are_truncated"),
        ),
    ),
    "RenderRunningTests": (
        ported(
            "test_docker_down_skips_containers_and_lock_check",
            Counterpart(STATUS, "test_docker_down_skips_containers_and_the_lock_check"),
        ),
        ported(
            "test_no_containers_says_so",
            Counterpart(STATUS, "test_no_running_containers_says_so"),
        ),
        ported(
            "test_live_container_row_includes_branch_uptime_and_tail_command",
            Counterpart(STATUS, "test_a_container_row_carries_branch_uptime_and_tail_command"),
        ),
        ported(
            "test_stale_lock_flagged_when_no_matching_container",
            Counterpart(STATUS, "test_a_stale_lock_is_flagged_when_no_container_matches"),
        ),
        ported(
            "test_lock_with_live_container_not_flagged",
            Counterpart(STATUS, "test_a_lock_with_a_live_container_is_not_flagged"),
        ),
    ),
    "RenderRecentTests": (
        ported(
            "test_no_records_says_so",
            Counterpart(STATUS, "test_no_records_says_so"),
        ),
        ported(
            "test_respects_limit",
            Counterpart(STATUS, "test_the_limit_is_respected"),
        ),
        ported(
            "test_row_includes_pr_url_unmodified",
            Counterpart(STATUS, "test_a_row_carries_the_pull_request_url_unmodified"),
        ),
        ported(
            "test_missing_pr_url_shows_dash",
            Counterpart(STATUS, "test_a_missing_pull_request_url_shows_a_dash"),
        ),
    ),
    "RenderStatusTests": (
        ported(
            "test_empty_state_exits_cleanly_with_both_sections",
            Counterpart(STATUS, "test_an_empty_state_renders_both_sections_cleanly"),
        ),
        ported(
            "test_full_fixture_shows_running_and_recent_sections",
            Counterpart(STATUS, "test_a_full_fixture_shows_running_and_recent_sections"),
        ),
        ported(
            "test_docker_down_still_renders_recent",
            Counterpart(STATUS, "test_docker_down_still_renders_recent"),
        ),
        excluded(
            "test_renders_legacy_per_dir_ledgers_via_migration",
            MIGRATION_REACHED_INDIRECTLY,
        ),
    ),
    "CmdStatusTests": (
        # Decision 5's first genuine split: `status` is a subcommand a human types.
        # Computation half in tests/test_status.py, printing half in tests/test_cli.py.
        split(
            "test_reads_docker_rows_from_stdin",
            Counterpart(STATUS, "test_docker_rows_handed_in_reach_the_running_table"),
            Counterpart(CLI, "test_the_rows_the_gather_returned_are_printed"),
        ),
        split(
            "test_docker_down_does_not_read_stdin",
            Counterpart(STATUS, "test_docker_down_ignores_rows_handed_in"),
            Counterpart(CLI, "test_a_down_daemon_prints_the_unavailable_line_and_no_rows"),
        ),
        split(
            "test_exit_code_zero_on_empty_state",
            Counterpart(STATUS, "test_an_empty_state_is_an_answer_not_an_error"),
            Counterpart(CLI, "test_an_empty_state_prints_both_sections_and_exits_zero"),
        ),
    ),
    "IsLiveStatusTests": (
        ported(
            "test_up_prefix_is_live",
            Counterpart(STATUS, "test_an_up_prefix_is_live"),
        ),
        ported(
            "test_exited_is_not_live",
            Counterpart(STATUS, "test_exited_is_not_live"),
        ),
        ported(
            "test_created_is_not_live",
            Counterpart(STATUS, "test_created_is_not_live"),
        ),
    ),
    "PidAliveTests": (
        ported(
            "test_current_process_is_alive",
            Counterpart(STATUS, "test_the_current_process_is_alive"),
        ),
        ported(
            "test_garbage_text_is_not_alive",
            Counterpart(STATUS, "test_garbage_text_is_not_alive"),
        ),
        ported(
            "test_empty_text_is_not_alive",
            Counterpart(STATUS, "test_empty_text_is_not_alive"),
        ),
        ported(
            "test_dead_pid_is_not_alive",
            Counterpart(STATUS, "test_a_dead_pid_is_not_alive"),
        ),
        ported(
            "test_permission_error_counts_as_alive",
            Counterpart(STATUS, "test_a_permission_error_counts_as_alive"),
        ),
    ),
    "HumanSizeTests": (
        pending("test_bytes"),
        pending("test_kilobytes"),
        pending("test_megabytes"),
        pending("test_gigabytes"),
    ),
    "DirSizeTests": (
        pending("test_sums_file_sizes_recursively"),
        pending("test_missing_dir_is_zero"),
    ),
    "CollectGcItemsTests": (
        pending("test_stopped_container_is_orphan_and_deletable"),
        pending("test_running_container_is_excluded"),
        pending("test_docker_down_excludes_containers_and_marks_undeletable"),
        pending("test_checkout_with_no_live_container_is_orphan"),
        pending("test_checkout_with_live_container_is_excluded"),
        pending("test_checkout_with_live_lock_pid_is_excluded"),
        pending("test_checkout_with_dead_lock_pid_is_still_orphan"),
        pending("test_lock_with_dead_pid_and_no_container_is_orphan"),
        pending("test_lock_with_live_pid_is_not_stale"),
        pending("test_lock_with_live_container_excluded_even_if_pid_dead"),
        pending("test_logs_are_never_items"),
    ),
    "SummarizeLogsTests": (
        pending("test_counts_current_and_rotated_with_total_size"),
        pending("test_no_rotated_omits_rotated_count"),
        pending("test_empty_or_missing_dir_is_empty_string"),
    ),
    "RenderGcTests": (
        pending("test_empty_items_nothing_to_reclaim"),
        pending("test_log_summary_appended_with_and_without_items"),
        pending("test_docker_down_adds_warning_header"),
        pending("test_lists_item_fields"),
    ),
    "RenderGcPlanTests": (
        pending("test_only_deletable_items_included"),
        pending("test_empty_items_gives_empty_plan"),
    ),
    "CmdGcTests": (
        pending("test_reads_docker_rows_from_stdin"),
        pending("test_docker_down_does_not_read_stdin"),
        pending("test_plan_flag_prints_tsv"),
        pending("test_plan_flag_prints_nothing_when_empty"),
    ),
    "SetStatusTests": (
        ported(
            "test_replaces_existing_status_line_in_place",
            Counterpart(ISSUES, "test_an_existing_status_line_is_replaced_in_place"),
        ),
        ported(
            "test_inserts_status_line_when_absent",
            Counterpart(ISSUES, "test_a_status_line_is_inserted_when_the_file_has_none"),
        ),
    ),
    "IsProtectedTests": (
        ported(
            "test_master_and_main_are_protected",
            Counterpart(RESUME, "test_master_and_main_are_protected_whatever_the_case"),
        ),
        ported(
            "test_other_branches_are_not_protected",
            Counterpart(RESUME, "test_an_ordinary_branch_is_not_protected"),
        ),
    ),
    "SlugifyTests": (
        ported(
            "test_takes_first_words_lowercased",
            Counterpart(ISSUES, "test_the_first_words_are_taken_and_lowercased"),
        ),
        ported(
            "test_strips_punctuation",
            Counterpart(ISSUES, "test_punctuation_separates_rather_than_survives"),
        ),
        ported(
            "test_empty_text_falls_back_to_task",
            Counterpart(ISSUES, "test_empty_text_falls_back_to_the_literal_task"),
        ),
    ),
    "MaterializeAdHocTests": (
        ported(
            "test_creates_file_under_ad_hoc_with_timestamp_and_branch",
            Counterpart(
                ISSUES, "test_the_file_lands_under_ad_hoc_named_by_timestamp_and_branch"
            ),
        ),
        ported(
            "test_filename_follows_branch_not_prompt_text",
            Counterpart(ISSUES, "test_the_filename_follows_the_branch_not_the_prompt_text"),
        ),
        ported(
            "test_returned_path_contains_a_slash",
            Counterpart(ISSUES, "test_the_returned_path_contains_a_slash"),
        ),
    ),
    "ResolveSpecTests": (
        ported(
            "test_bare_name_resolves_under_tasks_dir_with_md_appended",
            Counterpart(
                ISSUES, "test_a_bare_name_resolves_under_the_specs_dir_with_md_appended"
            ),
        ),
        ported(
            "test_bare_name_with_md_suffix_is_not_doubled",
            Counterpart(ISSUES, "test_a_bare_name_that_already_ends_in_md_is_not_doubled"),
        ),
        ported(
            "test_path_with_slash_used_as_is",
            Counterpart(ISSUES, "test_a_path_with_a_slash_is_used_as_it_stands"),
        ),
        ported(
            "test_path_with_slash_and_no_md_suffix_still_gets_md_appended",
            Counterpart(
                ISSUES, "test_a_path_with_a_slash_and_no_md_suffix_still_gets_md_appended"
            ),
        ),
        ported(
            "test_task_dir_for_feature_is_tasks_dir_slash_feature",
            Counterpart(
                ISSUES, "test_the_source_dir_for_a_feature_is_the_specs_dir_plus_its_name"
            ),
        ),
        ported(
            "test_task_dir_for_bare_spec_is_tasks_dir",
            Counterpart(ISSUES, "test_the_source_dir_for_a_bare_spec_is_the_specs_dir_itself"),
        ),
        ported(
            "test_task_dir_for_path_spec_is_its_parent",
            Counterpart(ISSUES, "test_the_source_dir_for_a_path_spec_is_that_paths_parent"),
        ),
        ported(
            "test_task_dir_for_pending_ad_hoc_prompt_is_ad_hoc_dir",
            Counterpart(
                ISSUES, "test_the_source_dir_for_a_pending_ad_hoc_prompt_is_the_ad_hoc_dir"
            ),
        ),
    ),
    "BranchNameSuggestionTests": (
        ported(
            "test_feature_mode_uses_feature_folder_name",
            Counterpart(RESUME, "test_a_feature_run_is_named_after_its_folder"),
        ),
        ported(
            "test_one_off_spec_uses_basename_sans_md",
            Counterpart(
                RESUME, "test_a_one_off_spec_is_named_after_its_basename_without_the_suffix"
            ),
        ),
        ported(
            "test_one_off_spec_bare_name_without_md_suffix_unchanged",
            Counterpart(RESUME, "test_a_spec_named_without_a_suffix_is_left_alone"),
        ),
        ported(
            "test_one_off_prompt_uses_slug_of_first_line",
            Counterpart(
                RESUME, "test_a_typed_prompt_is_named_after_the_slug_of_its_first_line"
            ),
        ),
        ported(
            "test_feature_takes_priority_over_spec_or_prompt_text",
            Counterpart(RESUME, "test_a_feature_wins_over_a_spec_and_over_typed_text"),
        ),
    ),
    "FirstFreeBranchNameTests": (
        ported(
            "test_free_suggestion_returned_as_is",
            Counterpart(RESUME, "test_a_free_suggestion_is_used_as_it_stands"),
        ),
        ported(
            "test_local_collision_suffixes",
            Counterpart(RESUME, "test_a_local_branch_of_that_name_forces_a_suffix"),
        ),
        ported(
            "test_remote_collision_suffixes",
            Counterpart(
                RESUME, "test_a_branch_already_pushed_from_elsewhere_forces_a_suffix_too"
            ),
        ),
        ported(
            "test_skips_past_multiple_taken_suffixes",
            Counterpart(RESUME, "test_taken_suffixes_are_skipped_until_one_is_free"),
        ),
    ),
    "LedgerBranchHelpersTests": (
        ported(
            "test_branch_order_is_most_recent_first_deduped",
            Counterpart(LEDGER, "test_branch_order_is_most_recent_first_and_deduped"),
        ),
        ported(
            "test_branch_order_scoped_to_task_dir_ignores_other_dirs",
            Counterpart(LEDGER, "test_branch_order_ignores_branches_run_from_another_source"),
        ),
        ported(
            "test_annotate_branch_includes_base_issues_and_pr",
            Counterpart(
                LEDGER, "test_an_annotated_branch_carries_its_base_issues_and_pull_request"
            ),
        ),
        ported(
            "test_annotate_branch_with_no_ledger_record_returns_bare_name",
            Counterpart(
                LEDGER, "test_a_branch_the_ledger_does_not_know_annotates_to_its_bare_name"
            ),
        ),
        ported(
            "test_annotate_branch_ignores_same_branch_in_other_task_dir",
            Counterpart(
                LEDGER, "test_annotation_ignores_the_same_branch_run_from_another_source"
            ),
        ),
    ),
    "PickIssuesTests": (
        excluded("test_auto_skips_prompt_when_one_issue", PICKER_ISSUES),
        excluded("test_prompts_and_returns_raw_selection_when_multiple_issues", PICKER_ISSUES),
        excluded("test_gum_individual_toggle_wins_over_preselected_all", PICKER_ISSUES),
        excluded("test_gum_all_alone_defers_to_default", PICKER_ISSUES),
        excluded("test_gum_all_entry_is_preselected", PICKER_ISSUES),
        excluded("test_all_entry_label_reflects_done_state_and_ready_count", PICKER_ISSUES),
        excluded("test_no_all_entry_when_fewer_than_two_ready", PICKER_ISSUES),
        excluded("test_blank_response_defers_to_default_selection", PICKER_ISSUES),
        excluded("test_gum_multi_select_matches_fallback_result", PICKER_ISSUES),
        excluded("test_gum_empty_selection_defers_to_default", PICKER_ISSUES),
    ),
    "ResumeRunLabelTests": (
        # In tests/test_status.py rather than tests/test_resume.py: `_resume_run_label`
        # renders through `_overall_outcome`/`_age`, so it lives and is tested where they do.
        ported(
            "test_feature_run_with_recorded_outcome",
            Counterpart(STATUS, "test_a_feature_run_with_a_recorded_outcome"),
        ),
        ported(
            "test_spec_run_falls_back_to_pr_open_when_no_outcome_recorded",
            Counterpart(STATUS, "test_a_spec_run_with_no_outcome_falls_back_to_pr_open"),
        ),
        ported(
            "test_falls_back_to_no_pr_yet_when_neither_outcome_nor_pr_recorded",
            Counterpart(STATUS, "test_no_outcome_and_no_pr_falls_back_to_no_pr_yet"),
        ),
    ),
    "LatestPerBranchTests": (
        ported(
            "test_keeps_only_the_first_record_seen_per_branch",
            Counterpart(LEDGER, "test_only_the_first_record_seen_per_branch_survives"),
        ),
        ported(
            "test_caps_at_limit",
            Counterpart(LEDGER, "test_the_result_is_capped_at_the_limit"),
        ),
        ported(
            "test_skips_records_with_missing_or_non_string_branch",
            Counterpart(LEDGER, "test_a_record_with_no_usable_branch_is_skipped"),
        ),
    ),
    "ResumeIssueCountTests": (
        ported(
            "test_spec_mode_is_always_zero",
            Counterpart(RESUME, "test_a_spec_run_always_counts_zero"),
        ),
        ported(
            "test_missing_task_dir_is_zero",
            Counterpart(RESUME, "test_a_gone_source_directory_counts_zero"),
        ),
        ported(
            "test_feature_mode_counts_ready_issues",
            Counterpart(RESUME, "test_a_feature_counts_the_issues_that_are_ready_to_run"),
        ),
        ported(
            "test_select_issues_error_counts_as_one_not_zero",
            Counterpart(RESUME, "test_a_selection_that_errors_counts_as_one_rather_than_zero"),
        ),
    ),
    "PickResumeTests": (
        excluded("test_empty_ledger_explains_and_raises_pickback", PICKER_RESUME),
        excluded("test_back_entry_raises_pickback", PICKER_RESUME),
        excluded("test_abort_entry_raises_pickabort", PICKER_RESUME),
        excluded("test_normal_branch_blank_feedback_returns_no_feedback", PICKER_RESUME),
        excluded("test_normal_branch_typed_feedback_returned", PICKER_RESUME),
        excluded("test_zero_runnable_issues_makes_feedback_mandatory", PICKER_RESUME),
        excluded("test_gum_lists_records_and_returns_blank_feedback", PICKER_RESUME),
        excluded("test_gum_back_entry_raises_pickback", PICKER_RESUME),
        excluded("test_gum_abort_entry_raises_pickabort", PICKER_RESUME),
        excluded("test_gum_zero_runnable_issues_loops_until_feedback_typed", PICKER_RESUME),
    ),
    "PickTaskSourceTests": (
        excluded("test_selects_a_feature_folder_by_number", PICKER_TASK_SOURCE),
        excluded(
            "test_features_ordered_newest_first_with_alphabetical_tiebreak",
            PICKER_TASK_SOURCE,
        ),
        excluded("test_features_listing_survives_broken_symlink", PICKER_TASK_SOURCE),
        excluded("test_one_off_spec_path_must_exist", PICKER_TASK_SOURCE),
        excluded("test_one_off_spec_path_reprompts_until_found", PICKER_TASK_SOURCE),
        excluded(
            "test_typed_prompt_returns_raw_text_without_materializing",
            PICKER_TASK_SOURCE,
        ),
        excluded("test_selects_resume_and_delegates_to_pick_resume", PICKER_TASK_SOURCE),
        excluded("test_resume_pickback_returns_to_top_menu", PICKER_TASK_SOURCE),
        excluded("test_gum_selects_feature_by_label", PICKER_TASK_SOURCE),
        excluded(
            "test_gum_spec_path_uses_gum_input_and_matches_fallback_result",
            PICKER_TASK_SOURCE,
        ),
        excluded(
            "test_gum_typed_prompt_uses_gum_write_and_does_not_materialize",
            PICKER_TASK_SOURCE,
        ),
        excluded("test_gum_selects_resume_and_delegates_to_pick_resume", PICKER_TASK_SOURCE),
        excluded("test_gum_back_at_top_menu_propagates", PICKER_TASK_SOURCE),
        excluded("test_gum_abort_at_top_menu_propagates", PICKER_TASK_SOURCE),
        excluded("test_gum_explicit_back_entry_raises_pickback", PICKER_TASK_SOURCE),
        excluded("test_gum_explicit_abort_entry_raises_pickabort", PICKER_TASK_SOURCE),
        excluded("test_fallback_back_entry_raises_pickback", PICKER_TASK_SOURCE),
        excluded("test_fallback_abort_entry_raises_pickabort", PICKER_TASK_SOURCE),
        excluded(
            "test_gum_ctrl_c_during_spec_subprompt_propagates_as_abort",
            PICKER_TASK_SOURCE,
        ),
    ),
    "PickBranchTests": (
        excluded("test_selects_ledger_branch_by_number", PICKER_BRANCH),
        excluded("test_typing_an_existing_branch_name_selects_it", PICKER_BRANCH),
        excluded(
            "test_new_branch_name_asks_confirm_and_creates_from_default_base",
            PICKER_BRANCH,
        ),
        excluded("test_new_branch_declined_reprompts", PICKER_BRANCH),
        excluded("test_protected_branch_is_refused_and_reprompts", PICKER_BRANCH),
        excluded("test_checked_out_branch_is_refused_and_reprompts", PICKER_BRANCH),
        excluded("test_top_menu_returncode_130_raises_pickabort", PICKER_BRANCH),
        excluded("test_ledger_branches_pinned_to_top", PICKER_BRANCH),
        excluded("test_other_fzf_path_uses_selected_line", PICKER_BRANCH),
        excluded("test_other_fzf_unmatched_query_becomes_new_branch_candidate", PICKER_BRANCH),
        excluded("test_gum_selects_existing_branch_and_matches_fallback_result", PICKER_BRANCH),
        excluded("test_gum_new_branch_uses_gum_input_and_gum_confirm", PICKER_BRANCH),
        excluded("test_gum_declined_confirm_reprompts", PICKER_BRANCH),
        excluded("test_gum_back_at_top_menu_propagates_not_reprompts", PICKER_BRANCH),
        excluded("test_gum_abort_at_top_menu_propagates", PICKER_BRANCH),
        excluded("test_gum_explicit_back_entry_raises_pickback", PICKER_BRANCH),
        excluded("test_gum_explicit_abort_entry_raises_pickabort", PICKER_BRANCH),
        excluded("test_fallback_explicit_back_entry_raises_pickback", PICKER_BRANCH),
        excluded("test_fallback_explicit_abort_entry_raises_pickabort", PICKER_BRANCH),
        excluded("test_new_branch_name_subprompt_back_returns_to_branch_menu", PICKER_BRANCH),
        excluded("test_new_branch_name_subprompt_abort_propagates", PICKER_BRANCH),
        excluded("test_create_base_subprompt_back_returns_to_branch_menu", PICKER_BRANCH),
        excluded("test_confirm_ctrl_c_propagates_as_abort_not_decline", PICKER_BRANCH),
        excluded("test_gum_new_branch_prefills_free_suggestion", PICKER_BRANCH),
        excluded("test_gum_no_suggestion_prefills_nothing", PICKER_BRANCH),
        excluded("test_gum_local_collision_prefills_first_free_suffix", PICKER_BRANCH),
        excluded("test_gum_remote_collision_also_prefills_a_suffix", PICKER_BRANCH),
        excluded(
            "test_fallback_new_branch_shows_suggestion_and_blank_accepts_it",
            PICKER_BRANCH,
        ),
        excluded("test_fallback_new_branch_can_still_edit_freely", PICKER_BRANCH),
    ),
    "PickBaseTests": (
        excluded("test_fzf_selection_wins", PICKER_BASE),
        excluded("test_fzf_enter_on_empty_query_takes_default", PICKER_BASE),
        excluded("test_fzf_typed_resolvable_query_accepted_without_list_match", PICKER_BASE),
        excluded("test_fzf_130_raises_pickback", PICKER_BASE),
        excluded("test_fzf_unresolvable_query_reprompts", PICKER_BASE),
        excluded("test_blank_response_accepts_the_default", PICKER_BASE),
        excluded("test_response_overrides_default", PICKER_BASE),
        excluded("test_default_prefers_ledger_chain_over_passed_in_default", PICKER_BASE),
        excluded("test_gum_blank_response_accepts_the_default", PICKER_BASE),
        excluded("test_gum_response_overrides_default", PICKER_BASE),
        excluded("test_gum_esc_raises_pickback", PICKER_BASE),
        excluded("test_gum_ctrl_c_raises_pickabort", PICKER_BASE),
    ),
    "SummaryLinesTests": (
        ported(
            "test_feature_run_shows_source_issues_branch_base",
            Counterpart(STATUS, "test_a_feature_run_shows_source_issues_branch_and_base"),
        ),
        ported(
            "test_feature_run_default_issue_selection_shows_all_label",
            Counterpart(STATUS, "test_a_default_issue_selection_shows_the_all_label"),
        ),
        ported(
            "test_one_off_spec_shows_spec_path_and_no_issues_line",
            Counterpart(STATUS, "test_a_one_off_spec_shows_its_path_and_no_issues_line"),
        ),
        ported(
            "test_ad_hoc_prompt_shows_materialized_filename",
            Counterpart(STATUS, "test_an_ad_hoc_prompt_shows_its_materialized_filename"),
        ),
        ported(
            "test_pending_ad_hoc_prompt_shows_first_line_not_yet_materialized",
            Counterpart(STATUS, "test_a_pending_ad_hoc_prompt_shows_its_first_line_not_a_path"),
        ),
        ported(
            "test_created_branch_flow_shows_will_be_created_suffix",
            Counterpart(STATUS, "test_a_branch_to_be_created_carries_the_suffix"),
        ),
    ),
    "SummaryMenuTests": (
        excluded("test_offers_edit_issues_and_edit_base_when_applicable", PICKER_SUMMARY_MENU),
        excluded(
            "test_omits_edit_issues_and_edit_base_when_not_applicable",
            PICKER_SUMMARY_MENU,
        ),
        excluded(
            "test_dispatch_edit_source_edit_branch_and_abort_always_offered",
            PICKER_SUMMARY_MENU,
        ),
        excluded("test_omits_edit_branch_when_not_applicable", PICKER_SUMMARY_MENU),
        excluded("test_gum_choose_receives_options_and_joined_header", PICKER_SUMMARY_MENU),
    ),
    "CmdPickTests": (
        excluded("test_one_off_spec_dispatch_prints_resolved_lines", PICKER_CMD),
        excluded("test_new_branch_base_is_pinned_to_the_confirmed_creation_base", PICKER_CMD),
        excluded("test_feature_dispatch_with_multiple_issues_prints_selection", PICKER_CMD),
        excluded("test_pickback_at_source_step_aborts", PICKER_CMD),
        excluded("test_pickabort_at_source_step_aborts", PICKER_CMD),
        excluded("test_keyboardinterrupt_at_any_step_aborts", PICKER_CMD),
        excluded("test_back_from_issues_returns_to_source_and_reenters", PICKER_CMD),
        excluded("test_back_from_branch_returns_to_issues_when_applicable", PICKER_CMD),
        excluded(
            "test_back_from_branch_returns_to_source_when_issues_step_skipped",
            PICKER_CMD,
        ),
        excluded("test_back_from_base_returns_to_branch", PICKER_CMD),
        excluded("test_pickabort_mid_wizard_aborts_immediately_not_a_step_back", PICKER_CMD),
        excluded("test_create_branch_skips_base_step", PICKER_CMD),
        excluded("test_edit_source_reruns_issues_branch_and_base", PICKER_CMD),
        excluded(
            "test_edit_source_to_a_source_without_issues_step_clears_stale_selection",
            PICKER_CMD,
        ),
        excluded("test_edit_issues_returns_straight_to_summary", PICKER_CMD),
        excluded("test_edit_branch_reruns_base_only", PICKER_CMD),
        excluded("test_edit_branch_creating_new_branch_skips_base_rerun", PICKER_CMD),
        excluded("test_edit_base_returns_straight_to_summary", PICKER_CMD),
        excluded("test_abort_from_summary_exits_1_with_no_output", PICKER_CMD),
        excluded("test_escape_on_summary_goes_back_into_last_real_step", PICKER_CMD),
        excluded("test_escape_on_summary_after_created_branch_goes_back_to_branch", PICKER_CMD),
        excluded("test_escape_walks_backward_continuously_from_summary", PICKER_CMD),
        excluded("test_escape_from_summary_all_the_way_back_aborts", PICKER_CMD),
        excluded(
            "test_back_from_edited_step_cancels_just_that_edit_not_whole_picker",
            PICKER_CMD,
        ),
        excluded(
            "test_summary_menu_offers_only_applicable_edit_entries_for_created_branch",
            PICKER_CMD,
        ),
        excluded("test_summary_menu_offers_issues_and_base_when_applicable", PICKER_CMD),
        excluded(
            "test_one_off_prompt_dispatch_materializes_file_named_after_branch",
            PICKER_CMD,
        ),
        excluded("test_abort_after_typing_prompt_leaves_no_ad_hoc_file", PICKER_CMD),
        excluded(
            "test_resume_skips_issues_and_branch_steps_and_prints_feedback_file",
            PICKER_CMD,
        ),
        excluded("test_resume_with_no_feedback_omits_feedback_file_line", PICKER_CMD),
    ),
    "GumHelpersTests": (
        excluded("test_choose_constructs_args_with_header_and_options", PICKER_GUM),
        excluded("test_choose_no_limit_adds_flag_and_returns_multiple_lines", PICKER_GUM),
        excluded("test_choose_empty_multi_select_is_not_a_cancellation", PICKER_GUM),
        excluded("test_choose_esc_exit_1_raises_pickback", PICKER_GUM),
        excluded("test_choose_ctrl_c_exit_130_raises_pickabort", PICKER_GUM),
        excluded("test_confirm_true_on_zero_exit", PICKER_GUM),
        excluded("test_confirm_false_on_esc_exit_1", PICKER_GUM),
        excluded("test_confirm_ctrl_c_exit_130_raises_pickabort", PICKER_GUM),
        excluded("test_input_returns_stripped_stdout", PICKER_GUM),
        excluded("test_input_value_adds_prefill_flag", PICKER_GUM),
        excluded("test_input_no_value_omits_prefill_flag", PICKER_GUM),
        excluded("test_input_esc_exit_1_raises_pickback", PICKER_GUM),
        excluded("test_input_ctrl_c_exit_130_raises_pickabort", PICKER_GUM),
        excluded("test_write_returns_stripped_stdout", PICKER_GUM),
        excluded("test_write_esc_exit_1_raises_pickback", PICKER_GUM),
        excluded("test_write_ctrl_c_exit_130_raises_pickabort", PICKER_GUM),
        excluded("test_raise_gum_cancel_maps_130_to_abort_else_back", PICKER_GUM),
    ),
}
