"""Proof that `bessemer.config` starts no subprocess, directly or indirectly.

Config load is pure — filesystem and environment only — so that `bessemer doctor` still
works when `git` and `docker` are broken, which is doctor's entire reason to exist
(ADR 0002). A module docstring saying so is worth nothing: this is the test the issue asks
for in its place.

Three angles, because each misses what the others catch:

1. **Static.** The module names no spawn entry point and imports neither `subprocess` nor
   `bessemer.proc`. Catches a call on a path no test happens to exercise, which is exactly
   where an accidental spawn would hide.
2. **Transitive.** A fresh interpreter that imports `bessemer.config` and nothing else must
   not have `subprocess` in `sys.modules`. Catches the indirect case the static pass cannot
   see — `config` importing a bessemer module that imports the wrapper — and it is the only
   angle here that would survive the import arriving through a helper added later.
3. **Runtime.** Every spawn entry point and both `bessemer.proc` functions are replaced with
   something that raises, and the whole public surface is then driven through its success
   and failure paths. Catches what a static walk cannot follow at all: `getattr`,
   `importlib`, anything resolved by name.

The static pass reuses `violations` from `tests/test_argv_boundary.py`, which is already the
project's definition of "crosses the argv boundary" and is itself fed by the guard's
inventory. Restating that list here would create the third copy the import exists to avoid.
"""

import ast
import contextlib
import sys
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

from bessemer import config, proc
from tests.guard import SPAWN_ENTRY_POINTS
from tests.test_argv_boundary import violations

MODULE_PATH = "bessemer/config.py"

WRAPPER_MODULE = proc.__name__
"""`bessemer.proc`, spelled from the module rather than as a literal so a rename fails."""


def module_source() -> str:
    origin = config.__file__
    assert origin is not None, "bessemer.config must be importable from a source tree"
    return Path(origin).read_text(encoding="utf-8")


def imported_modules(source: str) -> set[str]:
    """Every module named by an `import` or `from ... import` statement in `source`.

    Relative imports are resolved against the package, since `from . import proc` inside
    `bessemer/` reaches the wrapper exactly as the absolute spelling does.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = f"{config.__package__}.{base}" if base else str(config.__package__)
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


class StaticTest(unittest.TestCase):
    def test_the_module_crosses_no_spawn_boundary(self) -> None:
        """The same walk that guards every other module in the package (issue 03)."""
        self.assertEqual(violations(MODULE_PATH, module_source()), [])

    def test_the_module_does_not_import_the_subprocess_wrapper(self) -> None:
        """Distinct from the check above, and not implied by it: `bessemer.proc` is the one
        module that *may* spawn, so importing it is invisible to a subprocess-shaped ban."""
        imported = imported_modules(module_source())
        self.assertNotIn(WRAPPER_MODULE, imported)
        self.assertFalse({name for name in imported if name.startswith(f"{WRAPPER_MODULE}.")})

    def test_the_source_really_was_read(self) -> None:
        """Every assertion above passes on an empty string. This is what stops that."""
        self.assertIn("def load(", module_source())


class TransitiveImportTest(unittest.TestCase):
    def test_importing_the_module_does_not_pull_in_subprocess(self) -> None:
        """Run in a fresh interpreter, because this one imported `subprocess` before any
        test module was loaded — `tests/guard.py` needs it to arm the guard. Asking about
        `sys.modules` in-process would answer a question about the test runner.

        `-I` isolates the child from `PYTHONPATH` and the user site directory, so what it
        imports is what the package imports. `sys.executable` is the project interpreter and
        has bessemer installed, which is how the suite is run at all (`tests/README.md`).
        """
        probe = (
            "import sys, bessemer.config; "
            "print('subprocess' in sys.modules, 'bessemer.proc' in sys.modules)"
        )
        result = proc.run([sys.executable, "-I", "-c", probe], timeout=60)
        self.assertTrue(result.ok, result.stderr)
        self.assertEqual(result.stdout.strip(), "False False")

    def test_the_probe_can_detect_an_import(self) -> None:
        """The control. Without it the assertion above would pass just as happily against a
        probe that printed `False False` because it had crashed, been misspelled, or been
        pointed at a module that does not exist."""
        probe = (
            "import sys, subprocess, bessemer.proc; "
            "print('subprocess' in sys.modules, 'bessemer.proc' in sys.modules)"
        )
        result = proc.run([sys.executable, "-I", "-c", probe], timeout=60)
        self.assertTrue(result.ok, result.stderr)
        self.assertEqual(result.stdout.strip(), "True True")


class SpawnRefused(BaseException):
    """Raised by the stand-ins below. A `BaseException` so that a stray `except Exception`
    inside the code under test could not turn a real spawn into a passing run — the same
    reasoning `tests.guard.GuardViolation` is built on."""


@contextlib.contextmanager
def every_spawn_path_refused() -> Iterator[None]:
    """Replace every spawn entry point, and both wrapper functions, with a refusal.

    The guard's inventory rather than a hand-written list, for the reason it is exported:
    a second list drifts, and the half that drifts is the half nobody is testing.
    """

    def refuse(*args: object, **kwargs: object) -> object:
        raise SpawnRefused(f"bessemer.config tried to spawn: {args!r} {kwargs!r}")

    targets = [(module, name) for module, name in SPAWN_ENTRY_POINTS if hasattr(module, name)]
    targets += [(proc, "run"), (proc, "run_checked")]
    with contextlib.ExitStack() as stack:
        for module, name in targets:
            stack.enter_context(mock.patch.object(module, name, refuse))
        yield


class RuntimeTest(unittest.TestCase):
    """Drive the whole surface with every spawn path armed to raise.

    Both halves matter. The success path is where a spawn would plausibly be added — an
    auto-detected default, a `git config` lookup — and the failure paths are where a hastily
    written diagnostic would reach for `git rev-parse` to enrich a message.
    """

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name).resolve()

    def test_the_refusal_fixture_actually_refuses(self) -> None:
        """The control for this class. Without it every test below would pass against a
        fixture that patched nothing."""
        with every_spawn_path_refused():
            with self.assertRaises(SpawnRefused):
                proc.run(["git", "--version"], timeout=5)

    def test_a_full_load_spawns_nothing(self) -> None:
        adapter = self.tmp / config.ADAPTER_DIR
        adapter.mkdir()
        (adapter / config.COMMITTED_FILE).write_text('base = "main"\n', encoding="utf-8")
        (adapter / config.LOCAL_FILE).write_text('specs_dir = "elsewhere"\n', encoding="utf-8")

        with every_spawn_path_refused():
            loaded = config.load(
                start=self.tmp,
                env={"BESSEMER_SOURCE": "git+https://example.invalid@v1"},
                flags={"base": None},
            )
            assert isinstance(loaded, config.Config)
            self.assertEqual(loaded.root, self.tmp)
            self.assertEqual(loaded.get("base"), "main")
            self.assertEqual(loaded.get("specs_dir"), "elsewhere")
            self.assertEqual(loaded.layer_of("source"), config.ENV)
            self.assertEqual(loaded.unknown_keys(), ())

    def test_the_failure_paths_spawn_nothing_either(self) -> None:
        missing = self.tmp / "no-adapter-here"
        missing.mkdir()
        broken = self.tmp / "broken"
        (broken / config.ADAPTER_DIR).mkdir(parents=True)
        (broken / config.ADAPTER_DIR / config.COMMITTED_FILE).write_text(
            "base = \n", encoding="utf-8"
        )

        with every_spawn_path_refused():
            self.assertIsNone(config.find_adapter_dir(missing))
            self.assertIsInstance(config.load(start=missing, env={}), config.NotLoaded)
            self.assertIsInstance(config.load(start=broken, env={}), config.NotLoaded)


if __name__ == "__main__":
    unittest.main()
