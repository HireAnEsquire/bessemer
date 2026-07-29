"""The check over `tests/port_manifest.py` — F2's drift control, in both directions.

The manifest is a vendored copy of upstream's test census (337 tests, 56 classes, at hae
commit `e194121f75f4`). A copy is only worth having if something notices it disagreeing
with reality, and there are two realities it can disagree with:

**Upstream.** The port source is not readable here — CI has no `/Users/sbowles/hae` — so
the only thing that can catch a name quietly leaving the manifest is a second, independent
statement of how many there should be. `UPSTREAM_TEST_COUNT` and `UPSTREAM_CLASS_COUNTS`
below are that statement: hand-written literals, restated rather than derived. An assertion
that counted the manifest and compared the answer to itself would pass on an empty file.
This is F1's most-taught lesson — seven modules shipped a list pinned only by a constant the
assertion read back — applied at feature scale. What these literals pin is *how many*, not
*which*: the names themselves were checked against the pinned commit once and cannot be
re-checked here, as the manifest's own docstring says.

**Bessemer's suite.** Every entry marked `PORTED` or `PORTED_SPLIT` names a counterpart,
and every counterpart names its entry back through `port_manifest.ported_from`. Both
directions are asserted, because they fail differently:

- *shrinkage* — a counterpart deleted, renamed, or moved to another module. Without this,
  twelve tests becoming nine is invisible.
- *growth* — a marker in the suite that no entry claims, and an entry flipped to `PORTED`
  with nothing behind it. Without this, `PENDING` would be an escape hatch instead of a
  state: an implementer could mark the work done and the check would agree.

`PENDING` is the state everything portable starts in, since the manifest lands before any
of the work it describes. It is green on purpose, and `setUpModule` prints how many remain
so the number is legible without opening the file. Its only cheat is being left forever,
which is a printed count rather than an absence — and F2's tracer requires it to reach zero.

A counterpart also has to be somewhere `unittest discover` collects — a `test_*` module, a
`test*` method, a `TestCase` subclass — or the binding is satisfied by something that never
runs. Found in review: without those three assertions, a counterpart recorded in
`tests.gitenv`, or named `helper_not_a_test`, passed with a body of `raise AssertionError`.

**What none of this proves** is stated in the manifest's own docstring and pinned by
`DocstringTest`: a ported test gutted to `pass` satisfies every assertion in this file. The
counterpart rule is name-based, deliberately, and that weakness is closed by the port
issues' requirement to keep assertions intact and by a reviewer reading the exclusions —
not by machinery here. The never-runs case above was strictly worse, which is why it is
closed rather than named: a reviewer reading assertions cannot see a test nobody runs.

**Known limits, named rather than papered over**, on the same threat model as
`tests/guard.py`: accident and drift, not a hostile author.

- Markers are discovered by importing every module of the `tests` package, subpackages
  included, and reading the attribute off the class that *defines* it. A counterpart
  inherited from a shared base class is invisible. No such base exists today.
- A marker resolved at runtime — `setattr` on a test method from somewhere else — is found
  (this walk is runtime, not static), but a marker applied conditionally would make the
  check's answer depend on import order. Nothing does that.
- `IsolationTest` walks the AST of `bessemer/`, so an import reached through
  `importlib.import_module` or `__import__` is out of its sight, exactly as it is for
  `tests/test_argv_boundary.py`.
"""

import ast
import importlib
import pkgutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import NamedTuple

import tests
from tests import port_manifest
from tests.port_manifest import (
    EXCLUDED,
    MANIFEST,
    PENDING,
    PORTED,
    PORTED_FROM_ATTRIBUTE,
    PORTED_SPLIT,
    Counterpart,
)

# ---------------------------------------------------------------------------------------
# The hand-written census. Restated from the upstream file, not read back from the manifest:
# an entry dropped while the manifest was being assembled has to show up as a disagreement
# between two files, or it does not show up at all.
# ---------------------------------------------------------------------------------------

UPSTREAM_TEST_COUNT = 337
UPSTREAM_CLASS_COUNT = 56

UPSTREAM_CLASS_COUNTS = {
    "ParseIssueTests": 6,
    "SelectIssuesTests": 8,
    "LedgerTests": 9,
    "CentralLedgerPathTests": 1,
    "CmdLedgerAppendLastBaseTests": 6,
    "NewestRecordForBranchTests": 2,
    "RecentDistinctBranchesTests": 3,
    "ResolveResumeTests": 12,
    "ResumeDispatchActionTests": 8,
    "CmdResumeTests": 2,
    "CmdResumeGuardTests": 2,
    "ResolveLastTests": 3,
    "CmdLastTests": 2,
    "StripFeedbackEditTextTests": 4,
    "CmdFeedbackEditStripTests": 1,
    "MigrateLegacyLedgersTests": 6,
    "ParseDockerRowsTests": 4,
    "StaleLocksTests": 4,
    "CollectRecentLedgerRecordsTests": 4,
    "IssueSummaryTests": 3,
    "OverallOutcomeTests": 5,
    "AgeTests": 6,
    "FormatTableTests": 2,
    "RenderRunningTests": 5,
    "RenderRecentTests": 4,
    "RenderStatusTests": 4,
    "CmdStatusTests": 3,
    "IsLiveStatusTests": 3,
    "PidAliveTests": 5,
    "HumanSizeTests": 4,
    "DirSizeTests": 2,
    "CollectGcItemsTests": 11,
    "SummarizeLogsTests": 3,
    "RenderGcTests": 4,
    "RenderGcPlanTests": 2,
    "CmdGcTests": 4,
    "SetStatusTests": 2,
    "IsProtectedTests": 2,
    "SlugifyTests": 3,
    "MaterializeAdHocTests": 3,
    "ResolveSpecTests": 8,
    "BranchNameSuggestionTests": 5,
    "FirstFreeBranchNameTests": 4,
    "LedgerBranchHelpersTests": 5,
    "PickIssuesTests": 10,
    "ResumeRunLabelTests": 3,
    "LatestPerBranchTests": 3,
    "ResumeIssueCountTests": 4,
    "PickResumeTests": 10,
    "PickTaskSourceTests": 19,
    "PickBranchTests": 29,
    "PickBaseTests": 12,
    "SummaryLinesTests": 6,
    "SummaryMenuTests": 5,
    "CmdPickTests": 30,
    "GumHelpersTests": 17,
}

# Which classes are excluded whole, restated by hand. Counts alone would not notice a class
# flipping from portable to excluded — the total stays 337 either way — and an exclusion is
# the one disposition that removes coverage rather than deferring it.
#
# Eight picker classes, not the seven decision 1 of the F2 README listed: `SummaryMenuTests`
# was added during issue 00, after it turned out to patch `shutil.which` and assert on
# `gum_choose` arguments. Decision 1 now reads by what a class exercises, not by what it is
# named, and the picker exclusion is 132 tests rather than 127.
#
# `CmdFeedbackEditStripTests` joined during issue 01, which had been told to land it as F2's
# first `PORTED_SPLIT`. There is no computation half to split off: the computation has its
# own class, `StripFeedbackEditTextTests`, and the command is a three-line shim that exists
# only because the port source's `run.sh` is bash and had to spawn python to reach a
# function. So the class is excluded whole and `PORTED_SPLIT` has still never fired on real
# data — issue 02 or 04 settles it.
WHOLLY_EXCLUDED_CLASSES = frozenset(
    {
        "PickTaskSourceTests",
        "PickIssuesTests",
        "PickBranchTests",
        "PickBaseTests",
        "PickResumeTests",
        "CmdPickTests",
        "GumHelpersTests",
        "SummaryMenuTests",
        "MigrateLegacyLedgersTests",
        "CmdFeedbackEditStripTests",
        # Issue 02, applying decision 5's shim rule to the three ledger subcommands:
        # ledger-append (run.sh:1622), ledger-last-base (run.sh:881) and last (run.sh:697)
        # are each invoked by run.sh and by nothing else, and `last`'s key=value output is
        # read back with `IFS='=' read -r`. A python dispatcher calls append_ledger and
        # last_base_for_branch directly, so there is no split to make.
        "CmdLedgerAppendLastBaseTests",
        "CmdLastTests",
        # Issue 03, same rule applied to the two resume subcommands: run.sh reads `resume`
        # (run.sh:737) and `resume-guard` (run.sh:848) with `IFS='=' read -r`, and nothing
        # else invokes either. `--resume` is a dispatch flag in bessemer, never a subcommand,
        # so there is no half a human would type and no split to make.
        "CmdResumeTests",
        "CmdResumeGuardTests",
    }
)

# Excluded tests living in classes that are otherwise in scope. Enumerated by name rather
# than counted, because this is the exclusion a class-level count cannot show: the class
# stays portable, its total is unchanged, and the missing coverage is one line deep.
#
# Both reach `_migrate_legacy_ledgers` without naming it — they write a per-directory
# runs.jsonl and assert the central file is created from it — and decision 4 of the F2
# README drops the behaviour, not just the function. Found in review, after decision 4 was
# scoped by counting `MigrateLegacyLedgersTests` alone. That is the general defect the
# README now records: scoping a deletion by a function's own test class always undercounts
# it, because the scope is every test that reaches the behaviour.
PARTIALLY_EXCLUDED_TESTS = frozenset(
    {
        (
            "CollectRecentLedgerRecordsTests",
            "test_triggers_legacy_migration_when_central_file_missing",
        ),
        ("RenderStatusTests", "test_renders_legacy_per_dir_ledgers_via_migration"),
    }
)

EXCLUDED_TEST_COUNT = 153

# What `unittest discover` collects: modules matching `test*.py`, methods prefixed `test`,
# on `TestCase` subclasses. A counterpart recorded outside that set satisfies the binding
# while running nothing. `test_` rather than discover's looser `test` for the module,
# matching what every module in this suite is already called.
COLLECTED_MODULE_PREFIX = "test_"
COLLECTED_TEST_PREFIX = "test"

# A reason is prose or it is a flag with extra steps. Three filters, because each misses
# what the others catch: a floor on length, a floor on word count so that one long
# hyphenated token does not clear the first, and the names an implementer in a hurry
# actually reaches for.
MINIMUM_REASON_CHARACTERS = 40
MINIMUM_REASON_WORDS = 8
PLACEHOLDER_REASONS = frozenset(
    {
        "",
        "-",
        "todo",
        "tbd",
        "fixme",
        "xxx",
        "n/a",
        "na",
        "none",
        "later",
        "wontfix",
        "excluded",
        "not needed",
        "not applicable",
        "out of scope",
        "see above",
        "see the readme",
    }
)

COUNTERPART_ARITY = {PENDING: 0, PORTED: 1, PORTED_SPLIT: 2, EXCLUDED: 0}

# Pinned by hand so the disclaimer cannot be edited out of the manifest without a second,
# deliberate edit here. The whole sentence, not a keyword: the claim is what matters.
CANNOT_PROVE = "It cannot prove that test still asserts what\nupstream's did."


def entries() -> list[tuple[str, port_manifest.Entry]]:
    """Every entry, paired with the upstream class it belongs to."""
    return [(cls, entry) for cls, group in MANIFEST.items() for entry in group]


class MarkedTest(NamedTuple):
    """One `ported_from` marker found in the suite, and whether unittest would run it."""

    counterpart: Counterpart
    on_a_test_case: bool


def markers_in_suite() -> dict[tuple[str, str], set[MarkedTest]]:
    """Every `ported_from` marker in bessemer's suite, keyed by the upstream test it names.

    `walk_packages`, not `iter_modules`: the latter stops at the top level, so a marker in
    `tests/sub/test_probe.py` would be invisible here while `unittest discover` still ran
    the test — closed against growth everywhere except one directory down. Measured green
    on a clean tree before the fix.

    Read off the defining class — `vars(klass)` rather than `dir(klass)` — so a class
    imported into a second module is counted once, and so the module recorded is the module
    the test is written in rather than one that merely mentions it.
    """
    found: dict[tuple[str, str], set[MarkedTest]] = {}
    for info in pkgutil.walk_packages(tests.__path__, f"{tests.__name__}."):
        module = importlib.import_module(info.name)
        for klass in vars(module).values():
            if not isinstance(klass, type) or klass.__module__ != module.__name__:
                continue
            runnable = issubclass(klass, unittest.TestCase)
            for name, attribute in vars(klass).items():
                key = getattr(attribute, PORTED_FROM_ATTRIBUTE, None)
                if key is not None:
                    mark = MarkedTest(Counterpart(module.__name__, name), runnable)
                    found.setdefault(key, set()).add(mark)
    return found


def setUpModule() -> None:
    """Report how much of the port is still owed, on every run of the suite."""
    outstanding = sum(1 for _, entry in entries() if entry.disposition == PENDING)
    print(
        f"port manifest: {outstanding} of {UPSTREAM_TEST_COUNT} upstream tests still "
        f"pending a counterpart in bessemer's suite"
    )


class CensusTest(unittest.TestCase):
    """The manifest is the whole upstream file, once each."""

    def test_the_manifest_holds_every_upstream_test_exactly_once(self) -> None:
        keys = [(cls, entry.upstream_test) for cls, entry in entries()]
        self.assertEqual(len(keys), len(set(keys)))

    def test_the_manifest_holds_the_hand_written_total(self) -> None:
        self.assertEqual(len(entries()), UPSTREAM_TEST_COUNT)

    def test_the_hand_written_census_covers_every_upstream_class(self) -> None:
        self.assertEqual(len(UPSTREAM_CLASS_COUNTS), UPSTREAM_CLASS_COUNT)
        self.assertEqual(sum(UPSTREAM_CLASS_COUNTS.values()), UPSTREAM_TEST_COUNT)

    def test_every_class_holds_the_hand_written_number_of_tests(self) -> None:
        counts = {cls: len(group) for cls, group in MANIFEST.items()}
        self.assertEqual(counts, UPSTREAM_CLASS_COUNTS)


class DispositionTest(unittest.TestCase):
    """Every entry is classified, and the classification is internally consistent."""

    def test_every_entry_carries_a_known_disposition(self) -> None:
        for cls, entry in entries():
            with self.subTest(upstream=f"{cls}.{entry.upstream_test}"):
                self.assertIn(entry.disposition, COUNTERPART_ARITY)

    def test_counterpart_count_matches_the_disposition(self) -> None:
        for cls, entry in entries():
            with self.subTest(upstream=f"{cls}.{entry.upstream_test}"):
                self.assertEqual(len(entry.counterparts), COUNTERPART_ARITY[entry.disposition])

    def test_a_split_entry_records_two_distinct_destinations(self) -> None:
        for cls, entry in entries():
            if entry.disposition != PORTED_SPLIT:
                continue
            computation, rendering = entry.counterparts
            with self.subTest(upstream=f"{cls}.{entry.upstream_test}"):
                self.assertNotEqual(computation.module, rendering.module)

    def test_only_excluded_entries_carry_a_reason(self) -> None:
        for cls, entry in entries():
            with self.subTest(upstream=f"{cls}.{entry.upstream_test}"):
                if entry.disposition == EXCLUDED:
                    self.assertNotEqual(entry.reason, "")
                else:
                    self.assertEqual(entry.reason, "")

    def test_every_exclusion_reason_is_prose_and_not_a_placeholder(self) -> None:
        for cls, entry in entries():
            if entry.disposition != EXCLUDED:
                continue
            reason = entry.reason.strip()
            with self.subTest(upstream=f"{cls}.{entry.upstream_test}"):
                self.assertNotIn(reason.rstrip(".").lower(), PLACEHOLDER_REASONS)
                self.assertGreaterEqual(len(reason), MINIMUM_REASON_CHARACTERS)
                self.assertGreaterEqual(len(reason.split()), MINIMUM_REASON_WORDS)

    def test_a_wholly_excluded_class_is_excluded_whole(self) -> None:
        for cls, group in MANIFEST.items():
            if cls not in WHOLLY_EXCLUDED_CLASSES:
                continue
            with self.subTest(upstream=cls):
                self.assertTrue(all(entry.disposition == EXCLUDED for entry in group))

    def test_the_only_other_exclusions_are_the_hand_written_partial_ones(self) -> None:
        partial = {
            (cls, entry.upstream_test)
            for cls, entry in entries()
            if entry.disposition == EXCLUDED and cls not in WHOLLY_EXCLUDED_CLASSES
        }
        self.assertEqual(partial, set(PARTIALLY_EXCLUDED_TESTS))

    def test_the_exclusions_cover_the_hand_written_number_of_tests(self) -> None:
        excluded = [entry for _, entry in entries() if entry.disposition == EXCLUDED]
        self.assertEqual(len(excluded), EXCLUDED_TEST_COUNT)

    def test_a_recorded_counterpart_is_somewhere_unittest_collects(self) -> None:
        """A counterpart unittest never runs binds the manifest to nothing.

        Worse than the gutted-to-`pass` weakness the manifest's docstring names: that one a
        reviewer reading assertions can catch, and this one has no assertions to read.
        """
        for cls, entry in entries():
            for counterpart in entry.counterparts:
                with self.subTest(upstream=f"{cls}.{entry.upstream_test}"):
                    module = counterpart.module.rsplit(".", 1)[-1]
                    self.assertTrue(module.startswith(COLLECTED_MODULE_PREFIX), module)
                    self.assertTrue(
                        counterpart.test.startswith(COLLECTED_TEST_PREFIX), counterpart.test
                    )


class CounterpartTest(unittest.TestCase):
    """The manifest and the suite describe each other, in both directions."""

    def test_every_recorded_counterpart_exists_and_names_its_entry_back(self) -> None:
        markers = markers_in_suite()
        for cls, entry in entries():
            if entry.disposition not in (PORTED, PORTED_SPLIT):
                continue
            marks = markers.get((cls, entry.upstream_test), set())
            with self.subTest(upstream=f"{cls}.{entry.upstream_test}"):
                self.assertEqual({mark.counterpart for mark in marks}, set(entry.counterparts))

    def test_every_counterpart_is_defined_on_a_test_case(self) -> None:
        """The third route to a counterpart that never runs, and the one a name cannot show."""
        for marks in markers_in_suite().values():
            for mark in marks:
                with self.subTest(counterpart=mark.counterpart):
                    self.assertTrue(mark.on_a_test_case)

    def test_every_marker_in_the_suite_is_claimed_by_the_entry_it_names(self) -> None:
        claimed = {
            (cls, entry.upstream_test): set(entry.counterparts)
            for cls, entry in entries()
            if entry.disposition in (PORTED, PORTED_SPLIT)
        }
        for key, marks in markers_in_suite().items():
            with self.subTest(upstream=".".join(key)):
                self.assertIn(key, claimed)
                self.assertEqual({mark.counterpart for mark in marks}, claimed[key])


class DiscoveryTest(unittest.TestCase):
    """The marker walk descends into subpackages, and a control proves that is what it does.

    `unittest discover` collects `tests/sub/test_probe.py`; `pkgutil.iter_modules` stops at
    the top level and never sees it. Nothing in the committed suite lives one directory
    down, so on a clean tree the two calls agree and swapping `walk_packages` back for
    `iter_modules` stays green — measured, 376 tests, `OK`. The fix would ship with no
    guard and the next person to simplify that call would get no signal.

    So this builds the subpackage the suite lacks, in a temporary directory, and grafts it
    onto `tests.__path__` for the duration. A package's `__path__` is what `walk_packages`
    is given, so a directory appended to it is a subdirectory of `tests` as far as the walk
    is concerned — and nothing has to be written into the repository to find out.
    """

    UPSTREAM = ("ParseIssueTests", "test_tolerant_parsing_and_multiple_blockers")
    SUBPACKAGE = "probe_subpackage"

    def graft_a_subpackage_onto_the_tests_package(self) -> None:
        """Build `<tmp>/probe_subpackage/test_probe.py`, holding one marked test."""
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)

        package = Path(root.name) / self.SUBPACKAGE
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "test_probe.py").write_text(
            "import unittest\n\n"
            "from tests.port_manifest import ported_from\n\n\n"
            "class ProbeTest(unittest.TestCase):\n"
            f'    @ported_from("{self.UPSTREAM[0]}", "{self.UPSTREAM[1]}")\n'
            "    def test_a_marker_one_directory_down(self) -> None:\n"
            "        self.assertTrue(True)\n"
        )

        tests.__path__.append(root.name)
        self.addCleanup(tests.__path__.remove, root.name)

        # Cleanups run last-in-first-out, so these fire before the path graft is undone and
        # before the directory is removed. Both matter: an imported probe module outliving
        # this test is a marker no manifest entry claims, which would turn
        # `CounterpartTest` red for a reason that has nothing to do with the manifest.
        for module in (f"tests.{self.SUBPACKAGE}.test_probe", f"tests.{self.SUBPACKAGE}"):
            self.addCleanup(sys.modules.pop, module, None)

        importlib.invalidate_caches()

    def test_the_marker_walk_finds_a_marker_one_directory_down(self) -> None:
        self.graft_a_subpackage_onto_the_tests_package()
        self.assertIn(self.UPSTREAM, markers_in_suite())

    def test_the_control_a_top_level_walk_would_not_have_found_it(self) -> None:
        """Without this, the test above could be passing on a probe that is not nested."""
        self.graft_a_subpackage_onto_the_tests_package()
        shallow = {info.name for info in pkgutil.iter_modules(tests.__path__)}
        self.assertIn(self.SUBPACKAGE, shallow)
        self.assertNotIn("test_probe", shallow)


class IsolationTest(unittest.TestCase):
    """The manifest is a test artifact. The package it describes does not know it exists."""

    def test_no_module_under_bessemer_imports_the_manifest(self) -> None:
        package = Path(__file__).resolve().parent.parent / "bessemer"
        for module in sorted(package.rglob("*.py")):
            for node in ast.walk(ast.parse(module.read_text(), filename=str(module))):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    with self.subTest(module=module.name, imports=name):
                        self.assertNotEqual(name.split(".")[0], tests.__name__)


class DocstringTest(unittest.TestCase):
    """The manifest says what it cannot prove, and keeps saying it."""

    def test_the_manifest_states_the_limit_of_the_counterpart_rule(self) -> None:
        self.assertIsNotNone(port_manifest.__doc__)
        self.assertIn(CANNOT_PROVE, port_manifest.__doc__ or "")
