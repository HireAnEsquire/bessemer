"""The port of the port source's gc tests, plus what bessemer had to write itself.

Every test carrying a `ported_from` marker is a counterpart of one upstream test in
`.agentbox/test_tasklib.py` at `e194121f75f4`, and `tests/port_manifest.py` names it back.
The assertions are upstream's, kept whole; only the names, the grouping into classes and
the entry points changed — and two fixture values, deliberately: upstream's container rows
and `docker rm` strings say `agentbox-*` and bessemer's say `bessemer-*`, the product-name
rename `bessemer/status.py`'s docstring records. Upstream's `CmdGcTests` land split
(decision 5 of the F2 README): the computation half of each is here, the printing half in
`tests/test_cli.py`.

The unmarked tests are bessemer's own, and they exist for three reasons:

- **`bessemer/gc.py` deletes, moves and truncates nothing** — the module's central promise,
  and the one its own name argues against. `RestraintTest` proves it over the AST, because
  a docstring promising restraint is not a check, and this is the module where the
  difference matters most.
- **The table and the age helpers are imported from `bessemer/status.py`, not
  re-implemented** — `SingleDefinitionTest` asserts one definition of each, package-wide,
  and that gc's names are status's objects. Porting them twice is how two renderers drift.
- **`bessemer/gc.py` spawns no subprocess and reads no environment.** `PurityTest` proves
  it in the strong form issues and the ledger use — no `os` at all: the signal-0 probe gc
  needs lives in `bessemer/status.py` and arrives imported.

`_GC_DELETABLE_CLASSES` is the one list this module owns, so `DeletableClassesTest` pins it
with a hand-written literal (F1's rule: an assertion that reads the constant it is checking
cannot notice that constant changing).
"""

import ast
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bessemer import gc, proc, status
from tests.port_manifest import ported_from
from tests.test_argv_boundary import violations

MODULE_PATH = "bessemer/gc.py"

WRAPPER_MODULE = proc.__name__
"""`bessemer.proc`, spelled from the module rather than as a literal so a rename fails."""


class HumanSizeTest(unittest.TestCase):
    @ported_from("HumanSizeTests", "test_bytes")
    def test_bytes_render_without_a_decimal(self) -> None:
        self.assertEqual(gc._human_size(512), "512B")

    @ported_from("HumanSizeTests", "test_kilobytes")
    def test_kilobytes_render_with_one_decimal(self) -> None:
        self.assertEqual(gc._human_size(2048), "2.0K")

    @ported_from("HumanSizeTests", "test_megabytes")
    def test_megabytes_render_with_one_decimal(self) -> None:
        self.assertEqual(gc._human_size(5 * 1024 * 1024), "5.0M")

    @ported_from("HumanSizeTests", "test_gigabytes")
    def test_gigabytes_render_with_one_decimal(self) -> None:
        self.assertEqual(gc._human_size(3 * 1024 * 1024 * 1024), "3.0G")


class DirSizeTest(unittest.TestCase):
    @ported_from("DirSizeTests", "test_sums_file_sizes_recursively")
    def test_file_sizes_are_summed_recursively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_bytes(b"x" * 10)
            (root / "sub").mkdir()
            (root / "sub" / "b.txt").write_bytes(b"y" * 20)

            self.assertEqual(gc._dir_size(root), 30)

    @ported_from("DirSizeTests", "test_missing_dir_is_zero")
    def test_a_missing_directory_sizes_to_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(gc._dir_size(Path(tmp) / "nope"), 0)


class CollectGcItemsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.checkouts_dir = Path(self.tmp.name) / "checkouts"
        self.locks_dir = Path(self.tmp.name) / "locks"
        self.logs_dir = Path(self.tmp.name) / "logs"
        self.checkouts_dir.mkdir()
        self.locks_dir.mkdir()
        self.logs_dir.mkdir()

    def collect(
        self, docker_rows: tuple[str, ...] = (), docker_down: bool = False
    ) -> list[gc.GcItem]:
        return gc.collect_gc_items(
            checkouts_dir=self.checkouts_dir,
            locks_dir=self.locks_dir,
            docker_rows=list(docker_rows),
            docker_down=docker_down,
        )

    @ported_from("CollectGcItemsTests", "test_stopped_container_is_orphan_and_deletable")
    def test_a_stopped_container_is_an_orphan_and_deletable(self) -> None:
        items = self.collect(docker_rows=("bessemer-my-branch\tExited (0) 1 hour ago",))

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].cls, "container")
        self.assertEqual(items[0].slug, "my-branch")
        self.assertTrue(items[0].deletable)

    @ported_from("CollectGcItemsTests", "test_running_container_is_excluded")
    def test_a_running_container_is_excluded(self) -> None:
        items = self.collect(docker_rows=("bessemer-my-branch\tUp 5 minutes",))

        self.assertEqual(items, [])

    @ported_from(
        "CollectGcItemsTests", "test_docker_down_excludes_containers_and_marks_undeletable"
    )
    def test_docker_down_excludes_containers_and_marks_everything_undeletable(self) -> None:
        (self.checkouts_dir / "orphan").mkdir()

        items = self.collect(
            docker_rows=("bessemer-my-branch\tExited (0) 1 hour ago",), docker_down=True
        )

        self.assertEqual([i.cls for i in items], ["checkout"])
        self.assertFalse(items[0].deletable)

    @ported_from("CollectGcItemsTests", "test_checkout_with_no_live_container_is_orphan")
    def test_a_checkout_with_no_live_container_is_an_orphan(self) -> None:
        (self.checkouts_dir / "leaked").mkdir()

        items = self.collect()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].cls, "checkout")
        self.assertEqual(items[0].slug, "leaked")
        self.assertTrue(items[0].deletable)

    def test_a_checkout_whose_container_has_exited_is_still_an_orphan(self) -> None:
        """Bessemer's own, and F2 decision 9's fourth debtor entry (a): no test in either
        repo asserted that `live_slugs` holds **live** containers only.

        Measured there and re-measured here: widening it to every container — the one-token
        mutation `{c.slug for c in containers}` — leaves the whole suite green without this,
        and a checkout whose container merely *exited* then stops being reported as an
        orphan. `bessemer/reclaim.py` walks this plan and removes what is in it, so the gap
        is a run's work sitting in a checkout gc has quietly stopped naming. Both classes
        are asserted, in plan order, because the container arm is what makes the mutation
        reachable: with only the checkout asserted the row would still be here for the wrong
        reason.
        """
        (self.checkouts_dir / "my-branch").mkdir()

        items = self.collect(docker_rows=("bessemer-my-branch\tExited (0) 1 hour ago",))

        self.assertEqual(
            [(i.cls, i.slug) for i in items],
            [("container", "my-branch"), ("checkout", "my-branch")],
        )
        self.assertEqual([i.deletable for i in items], [True, True])

    @ported_from("CollectGcItemsTests", "test_checkout_with_live_container_is_excluded")
    def test_a_checkout_with_a_live_container_is_excluded(self) -> None:
        (self.checkouts_dir / "my-branch").mkdir()

        items = self.collect(docker_rows=("bessemer-my-branch\tUp 5 minutes",))

        self.assertEqual(items, [])

    @ported_from("CollectGcItemsTests", "test_checkout_with_live_lock_pid_is_excluded")
    def test_a_checkout_with_a_live_lock_pid_is_excluded(self) -> None:
        # A dispatch between clone and `docker run` (or after `docker rm`,
        # before cleanup finishes): no container, but the lock pid is live —
        # the checkout is owned, not leaked.
        (self.checkouts_dir / "starting").mkdir()
        (self.locks_dir / "starting.pid").write_text("1")

        with mock.patch.object(gc, "pid_alive", return_value=True):
            items = self.collect()

        self.assertEqual(items, [])

    @ported_from("CollectGcItemsTests", "test_checkout_with_dead_lock_pid_is_still_orphan")
    def test_a_checkout_with_a_dead_lock_pid_is_still_an_orphan(self) -> None:
        (self.checkouts_dir / "crashed").mkdir()
        (self.locks_dir / "crashed.pid").write_text("999999")

        with mock.patch.object(gc, "pid_alive", return_value=False):
            items = self.collect()

        self.assertEqual(
            [(i.cls, i.slug) for i in items], [("checkout", "crashed"), ("lock", "crashed")]
        )

    @ported_from("CollectGcItemsTests", "test_lock_with_dead_pid_and_no_container_is_orphan")
    def test_a_lock_with_a_dead_pid_and_no_container_is_an_orphan(self) -> None:
        (self.locks_dir / "leaked.pid").write_text("999999")

        with mock.patch.object(gc, "pid_alive", return_value=False):
            items = self.collect()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].cls, "lock")
        self.assertEqual(items[0].slug, "leaked")
        self.assertTrue(items[0].deletable)

    @ported_from("CollectGcItemsTests", "test_lock_with_live_pid_is_not_stale")
    def test_a_lock_with_a_live_pid_is_not_stale(self) -> None:
        (self.locks_dir / "starting.pid").write_text("1")

        with mock.patch.object(gc, "pid_alive", return_value=True):
            items = self.collect()

        self.assertEqual(items, [])

    @ported_from(
        "CollectGcItemsTests", "test_lock_with_live_container_excluded_even_if_pid_dead"
    )
    def test_a_lock_with_a_live_container_is_excluded_even_with_a_dead_pid(self) -> None:
        (self.locks_dir / "my-branch.pid").write_text("999999")

        with mock.patch.object(gc, "pid_alive", return_value=False):
            items = self.collect(docker_rows=("bessemer-my-branch\tUp 5 minutes",))

        self.assertEqual(items, [])

    @ported_from("CollectGcItemsTests", "test_logs_are_never_items")
    def test_logs_are_never_items(self) -> None:
        (self.logs_dir / "old.log").write_text("done run")
        (self.logs_dir / "old.log.1").write_text("rotated")

        self.assertEqual(self.collect(), [])


class SummarizeLogsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.logs_dir = Path(self.tmp.name)

    @ported_from("SummarizeLogsTests", "test_counts_current_and_rotated_with_total_size")
    def test_current_and_rotated_are_counted_with_a_total_size(self) -> None:
        (self.logs_dir / "a.log").write_text("x" * 100)
        (self.logs_dir / "b.log").write_text("x" * 100)
        (self.logs_dir / "a.log.1").write_text("x" * 100)

        summary = gc.summarize_logs(self.logs_dir)

        self.assertIn("2 current + 1 rotated", summary)
        self.assertIn("300B", summary)
        self.assertIn("never deletes logs", summary)

    @ported_from("SummarizeLogsTests", "test_no_rotated_omits_rotated_count")
    def test_no_rotated_logs_omits_the_rotated_count(self) -> None:
        (self.logs_dir / "a.log").write_text("x")

        summary = gc.summarize_logs(self.logs_dir)

        self.assertIn("1 current,", summary)
        self.assertNotIn("rotated", summary)

    @ported_from("SummarizeLogsTests", "test_empty_or_missing_dir_is_empty_string")
    def test_an_empty_or_missing_directory_summarizes_to_nothing(self) -> None:
        self.assertEqual(gc.summarize_logs(self.logs_dir), "")
        self.assertEqual(gc.summarize_logs(self.logs_dir / "nope"), "")


class RenderGcTest(unittest.TestCase):
    @ported_from("RenderGcTests", "test_empty_items_nothing_to_reclaim")
    def test_no_items_renders_nothing_to_reclaim(self) -> None:
        self.assertIn("nothing to reclaim", gc.render_gc([], docker_down=False))

    @ported_from("RenderGcTests", "test_log_summary_appended_with_and_without_items")
    def test_the_log_summary_is_appended_with_and_without_items(self) -> None:
        empty = gc.render_gc(
            [], docker_down=False, log_summary="logs: 3 current, 1.0K total — kept"
        )
        item = gc.GcItem("lock", "x", "1h ago", "-", "rm -f", True)
        with_items = gc.render_gc(
            [item], docker_down=False, log_summary="logs: 3 current, 1.0K total — kept"
        )

        self.assertIn("logs: 3 current", empty)
        self.assertIn("logs: 3 current", with_items)

    @ported_from("RenderGcTests", "test_docker_down_adds_warning_header")
    def test_docker_down_adds_the_warning_header(self) -> None:
        out = gc.render_gc([], docker_down=True)

        self.assertIn("docker unavailable", out)

    @ported_from("RenderGcTests", "test_lists_item_fields")
    def test_an_items_fields_are_listed(self) -> None:
        item = gc.GcItem(
            "container", "my-branch", "1h ago", "-", "docker rm -fv bessemer-my-branch", True
        )

        out = gc.render_gc([item], docker_down=False)

        self.assertIn("my-branch", out)
        self.assertIn("1h ago", out)


class RenderGcPlanTest(unittest.TestCase):
    @ported_from("RenderGcPlanTests", "test_only_deletable_items_included")
    def test_only_deletable_items_of_known_classes_are_included(self) -> None:
        items = [
            gc.GcItem("container", "a", "1h ago", "-", "rm", True),
            gc.GcItem("checkout", "b", "1h ago", "1K", "rm", False),
            gc.GcItem("log (rotated)", "c", "1h ago", "1K", "kept", False),
        ]

        self.assertEqual(gc.render_gc_plan(items), "container\ta")

    def test_an_unknown_class_is_filtered_out_even_when_it_is_deletable(self) -> None:
        """Bessemer's own, and F2 decision 9's fourth debtor entry (b): the class filter was
        never exercised alone.

        Upstream's fixture — inherited byte for byte above — has one unknown-class item and
        it is also `deletable=False`, so dropping `and i.cls in _GC_DELETABLE_CLASSES`
        leaves the suite green: the deletable half of the condition already excluded the row.
        Here the unknown class is the *only* thing keeping it out, which is the half
        `bessemer/reclaim.py` depends on — the executor dispatches on the class it reads off
        this plan, so a `log (rotated)` line reaching it is a line naming the one artifact
        gc must never delete (ADR 0001).
        """
        items = [
            gc.GcItem("checkout", "b", "1h ago", "1K", "rm", True),
            gc.GcItem("log (rotated)", "c", "1h ago", "1K", "kept", True),
        ]

        self.assertEqual(gc.render_gc_plan(items), "checkout\tb")

    @ported_from("RenderGcPlanTests", "test_empty_items_gives_empty_plan")
    def test_no_items_gives_an_empty_plan(self) -> None:
        self.assertEqual(gc.render_gc_plan([]), "")


class CmdGcComputationTest(unittest.TestCase):
    """The computation halves of upstream's `CmdGcTests` (decision 5 of the F2 README: `gc`
    is a subcommand a human types, so each of its tests splits — what was computed lands
    here, what was printed lands in tests/test_cli.py). Upstream's stdin is the
    bash-to-python boundary the rewrite deletes; "the rows run.sh piped in" become "the rows
    the caller handed over"."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.checkouts_dir = Path(self.tmp.name) / "checkouts"
        self.locks_dir = Path(self.tmp.name) / "locks"
        self.checkouts_dir.mkdir()
        self.locks_dir.mkdir()

    def collect(self, docker_rows: list[str], docker_down: bool) -> list[gc.GcItem]:
        return gc.collect_gc_items(
            checkouts_dir=self.checkouts_dir,
            locks_dir=self.locks_dir,
            docker_rows=docker_rows,
            docker_down=docker_down,
        )

    @ported_from("CmdGcTests", "test_reads_docker_rows_from_stdin")
    def test_docker_rows_handed_in_reach_the_rendered_scan(self) -> None:
        items = self.collect(["bessemer-my-branch\tExited (0) 1 hour ago"], False)

        self.assertIn("my-branch", gc.render_gc(items, docker_down=False))

    @ported_from("CmdGcTests", "test_docker_down_does_not_read_stdin")
    def test_docker_down_ignores_rows_handed_in(self) -> None:
        items = self.collect(["bessemer-my-branch\tExited (0) 1 hour ago"], True)

        out = gc.render_gc(items, docker_down=True)
        self.assertIn("docker unavailable", out)
        self.assertNotIn("my-branch", out)

    @ported_from("CmdGcTests", "test_plan_flag_prints_tsv")
    def test_the_plan_for_a_stopped_container_is_one_tsv_line(self) -> None:
        items = self.collect(["bessemer-my-branch\tExited (0) 1 hour ago"], False)

        self.assertEqual(gc.render_gc_plan(items), "container\tmy-branch")

    @ported_from("CmdGcTests", "test_plan_flag_prints_nothing_when_empty")
    def test_an_empty_scan_plans_nothing(self) -> None:
        self.assertEqual(gc.render_gc_plan(self.collect([], False)), "")


class RestraintTest(unittest.TestCase):
    """Bessemer's own: `bessemer/gc.py` deletes, moves and truncates nothing — asserted over
    the module's AST, because the module's name says otherwise and a docstring promising
    restraint is not a check. The acceptance criterion names five calls; the move/rename
    family is added on the same reasoning, because "moves nothing" is half the promise.

    Two assertions, because a call-name match alone has a second path — the alias question
    F1's test-guard issue made standard for every control like this: `from shutil import
    rmtree as _tidy` renames the call site out of the forbidden set. So the imports are
    walked too: `shutil` is banned entirely (nothing gc could legitimately want from it),
    and a `from` import of any forbidden *name* fails on `alias.name`, which an `as` clause
    cannot disguise. The known limit, named: a deleter reached dynamically —
    `getattr(Path, "unlink")`, `importlib` — beats both walks. Same threat model as
    `tests/guard.py`: accident and drift, not a hostile author."""

    FORBIDDEN_CALLS = frozenset(
        {
            "unlink",
            "rmtree",
            "remove",
            "removedirs",
            "rmdir",
            "truncate",
            "rename",
            "renames",
            "replace",
            "move",
        }
    )
    FORBIDDEN_MODULES = frozenset({"shutil"})

    def module_source(self) -> str:
        origin = gc.__file__
        assert origin is not None, "bessemer.gc must be importable from a source tree"
        return Path(origin).read_text(encoding="utf-8")

    def forbidden_imports(self, source: str) -> set[str]:
        """Every import in `source` that could put a deleter behind a local name: a
        forbidden module however aliased, or a forbidden name pulled in by `from` —
        `alias.name`, never `asname`, so renaming at the import is exactly what cannot
        hide one."""
        reached = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in self.FORBIDDEN_MODULES:
                        reached.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                base = (node.module or "").split(".")[0]
                if base in self.FORBIDDEN_MODULES:
                    reached.add(node.module or "")
                for alias in node.names:
                    if alias.name in self.FORBIDDEN_CALLS:
                        reached.add(f"{node.module}.{alias.name}")
        return reached

    def test_the_source_really_was_read(self) -> None:
        """Every assertion below passes on an empty string. This is what stops that."""
        self.assertIn("def collect_gc_items(", self.module_source())

    def test_the_module_calls_nothing_that_deletes_moves_or_truncates(self) -> None:
        reached = set()
        for node in ast.walk(ast.parse(self.module_source())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else None
            if isinstance(func, ast.Name):
                name = func.id
            if name in self.FORBIDDEN_CALLS:
                reached.add(name)
        self.assertEqual(reached, set())

    def test_the_module_imports_nothing_that_deletes_moves_or_truncates(self) -> None:
        """The alias path: a call-name match cannot see `from shutil import rmtree as
        _tidy`, so the import is where that route is closed."""
        self.assertEqual(self.forbidden_imports(self.module_source()), set())

    def test_the_import_walk_catches_an_alias_and_passes_a_benign_one(self) -> None:
        """Both directions, in-test: the walk is itself a control that has to be shown to
        fail on the mutant and pass on ordinary code, or it is decoration."""
        self.assertEqual(
            self.forbidden_imports("from shutil import rmtree as _tidy\n"),
            {"shutil", "shutil.rmtree"},
        )
        self.assertEqual(
            self.forbidden_imports("import shutil as sh\n"),
            {"shutil"},
        )
        self.assertEqual(
            self.forbidden_imports("from os import unlink as _u\n"),
            {"os.unlink"},
        )
        self.assertEqual(
            self.forbidden_imports("from pathlib import Path as P\nimport ast\n"),
            set(),
        )


class DeletableClassesTest(unittest.TestCase):
    def test_the_deletable_classes_are_pinned_by_hand(self) -> None:
        """The one list this module owns, restated as a literal so shrinking or growing it
        costs a deliberate edit in a second file (F1's most-taught rule). Growing it is
        F3's call to make, not a refactor's side effect — every entry here is a class of
        artifact the deletion side will act on."""
        self.assertEqual(gc._GC_DELETABLE_CLASSES, {"container", "checkout", "lock"})


class SingleDefinitionTest(unittest.TestCase):
    """Bessemer's own: the table and the age helpers are imported from `bessemer/status.py`,
    not re-implemented — porting them twice is how two renderers drift into disagreeing
    about what a table looks like."""

    SHARED_HELPERS = ("format_table", "mtime_age", "_age_from_seconds")

    def test_each_shared_helper_is_defined_exactly_once_in_the_package(self) -> None:
        package = Path(gc.__file__ or "").resolve().parent
        definitions: dict[str, list[str]] = {name: [] for name in self.SHARED_HELPERS}
        for module in sorted(package.rglob("*.py")):
            tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    if node.name in definitions:
                        definitions[node.name].append(module.name)
        self.assertEqual(
            definitions,
            {
                "format_table": ["status.py"],
                "mtime_age": ["status.py"],
                "_age_from_seconds": ["status.py"],
            },
        )

    def test_gc_uses_statuss_objects_not_copies(self) -> None:
        """Through `vars(gc)` rather than attribute access: the imports are gc's plumbing,
        not its API, so mypy's no-implicit-reexport rightly refuses `gc.format_table` — and
        making them explicit exports to satisfy a test would widen the module's surface."""
        for name in (
            "format_table",
            "mtime_age",
            "is_live_status",
            "pid_alive",
            "parse_docker_rows",
            "CONTAINER_PREFIX",
        ):
            with self.subTest(name=name):
                self.assertIs(vars(gc)[name], vars(status)[name])


class PurityTest(unittest.TestCase):
    """`bessemer/gc.py` spawns nothing and reads no environment: container state arrives as
    parsed rows the CLI gathered, so every test above is pure and fast, and `tests/guard.py`
    can deny `docker` to the whole suite. The strong form issues and the ledger use — no
    `os` at all — holds here, because the one operating-system question gc asks
    (`pid_alive`'s signal 0) lives in `bessemer/status.py` and arrives imported."""

    def module_source(self) -> str:
        origin = gc.__file__
        assert origin is not None, "bessemer.gc must be importable from a source tree"
        return Path(origin).read_text(encoding="utf-8")

    def imported_modules(self) -> set[str]:
        found: set[str] = set()
        for node in ast.walk(ast.parse(self.module_source())):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    base = f"{gc.__package__}.{base}" if base else str(gc.__package__)
                found.add(base)
                found.update(f"{base}.{alias.name}" for alias in node.names)
        return found

    def test_the_source_really_was_read(self) -> None:
        """Every assertion below passes on an empty string. This is what stops that."""
        self.assertIn("def render_gc(", self.module_source())

    def test_the_module_crosses_no_spawn_boundary(self) -> None:
        self.assertEqual(violations(MODULE_PATH, self.module_source()), [])

    def test_the_module_does_not_import_the_subprocess_wrapper(self) -> None:
        """Invisible to the walk above: `bessemer.proc` is the one module that may spawn."""
        imported = self.imported_modules()
        self.assertNotIn(WRAPPER_MODULE, imported)
        self.assertFalse({name for name in imported if name.startswith(f"{WRAPPER_MODULE}.")})

    def test_the_module_reads_no_environment(self) -> None:
        """`os` is not imported at all — stronger than banning `os.environ`, and available
        here where it was not in `tests/test_status.py`, because gc's liveness probe is
        status's, imported."""
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
            "import sys, bessemer.gc; "
            "print('subprocess' in sys.modules, 'bessemer.proc' in sys.modules)"
        )
        result = proc.run([sys.executable, "-I", "-c", probe], timeout=60)
        self.assertTrue(result.ok, result.stderr)
        self.assertEqual(result.stdout.strip(), "False False")


if __name__ == "__main__":
    unittest.main()
