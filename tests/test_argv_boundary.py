"""Static enforcement of the argv boundary: one module spawns, and no call sees a shell.

`bessemer/proc.py` exists to make "argv is a list, never a string, never through a shell"
structural rather than aspirational (ADR 0002). A wrapper only owns that invariant while
it is the only way out of the package, so this test walks the AST of every module under
`bessemer/` and refuses:

- **outside the wrapper** — any `subprocess` import, and any use of any spawn entry point;
- **inside the wrapper** — anything on the `subprocess` module other than `run`, `Popen`,
  and the four names that cannot start a process. An allowlist rather than a blocklist,
  because a blocklist loses to the next function someone finds;
- **everywhere** — any call passing a `shell=` keyword.

The banned set is **imported** from `tests.guard`, not restated. One set, two consumers:
the guard bans these paths at runtime for the test suite, this test bans them statically
for the package, and adding a name to the guard makes this test start catching it with no
second edit to remember. The literal restatement that keeps that inventory from shrinking
silently lives in `test_guard.py`, which is where it belongs — a copy here would be the
hand-maintained second list the import exists to avoid.

Import bindings are resolved **here**, because only the static consumer can: the export is
`(module, attr)` pairs precisely so that `import subprocess as sp; sp.run(...)` can be
matched on the module a name is bound to, while `bessemer.proc.run` — the wrapper's own
export, which every other module in the package calls — is not. A matcher keying on the
bare attribute `run` would ban the very function this boundary exists to provide.

AST rather than grep: grep is fooled by a docstring mentioning `shell=True` and misses
`sh = True; spawn(..., shell=sh)`.

**Known limits, named rather than papered over**, and on the same threat model as
`tests/guard.py` — accident and drift, not a hostile author. Nothing resolved at runtime
is visible here: `importlib.import_module(...)`, `__import__`, `eval`, and `getattr` on a
module reached by any route other than its own import binding. Inside the wrapper the
`subprocess` binding is additionally refused in bare form, which closes the `getattr` case
for the one module where it would matter most, but the general case stays open. A `shell`
argument arriving inside a `**kwargs` splat is invisible too. The `tests/` tree is out of
scope by design: tests legitimately spawn the interpreter and the console script to drive
the CLI end to end, which the guard's own allowlist permits.
"""

import ast
import subprocess
import unittest
from pathlib import Path
from typing import Final

import bessemer
from tests.guard import SPAWN_ENTRY_POINTS

WRAPPER: Final = "bessemer/proc.py"
"""Repo-relative path of the one module allowed to spawn."""

SPAWN_MODULE: Final = subprocess.__name__

WRAPPER_SPAWNERS: Final = frozenset({"run", "Popen"})
"""The only two ways out of the package. `Popen` is reserved for F3's log streaming."""

WRAPPER_INERT: Final = frozenset({"PIPE", "STDOUT", "DEVNULL", "TimeoutExpired"})
"""Names on `subprocess` that cannot start a process, so the allowlist admits them.

The line this allowlist draws is **what can spawn**, not what is spelled `subprocess.`.
`PIPE`, `STDOUT` and `DEVNULL` are integers and `TimeoutExpired` is an exception class.
Banning them would forbid the `stdin=DEVNULL` the wrapper requires and the `stdout=PIPE`
F3's streaming needs, leaving the first person to write either with a choice between
widening a security allowlist under deadline and working around it.

`InventoryTest` is what keeps this set honest: nothing here may be a name the guard calls
a spawn entry point.
"""

WRAPPER_ALLOWED: Final = WRAPPER_SPAWNERS | WRAPPER_INERT


def _banned_by_module() -> dict[str, frozenset[str]]:
    """The guard's inventory, regrouped as module name -> attribute names."""
    collected: dict[str, set[str]] = {}
    for module, name in SPAWN_ENTRY_POINTS:
        collected.setdefault(module.__name__, set()).add(name)
    return {module: frozenset(names) for module, names in collected.items()}


BANNED: Final = _banned_by_module()

_Located = ast.stmt | ast.expr | ast.alias


def violations(path: str, source: str) -> list[str]:
    """Every argv-boundary violation in one module, as `path:line: reason` strings.

    `path` is the module's repo-relative posix path, compared exactly against `WRAPPER`
    to decide which half of the rule applies.
    """
    found: list[str] = []
    is_wrapper = path == WRAPPER

    def report(node: _Located, reason: str) -> None:
        found.append(f"{path}:{node.lineno}: {reason}")

    def judge(node: _Located, module: str, attr: str) -> None:
        """Decide one resolved `(module, attr)` reference against the applicable rule."""
        if module == SPAWN_MODULE and is_wrapper:
            if attr not in WRAPPER_ALLOWED:
                report(
                    node,
                    f"names {module}.{attr}; {WRAPPER} allows only "
                    f"{sorted(WRAPPER_ALLOWED)} on {module}",
                )
            return
        if attr in BANNED[module]:
            report(node, f"uses the spawn entry point {module}.{attr}")

    tree = ast.parse(source, filename=path)

    # Pass 1: what local names are bound to a module we track, or to an attribute of one.
    # Collected over the whole tree before any use is judged, so a function-level import
    # binds for the whole module rather than only for the code that follows it.
    module_bindings: dict[str, str] = {}
    attr_bindings: dict[str, tuple[str, str]] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # `import os.path` binds `os`; `import os.path as p` binds the submodule.
                bound = alias.asname or alias.name.split(".")[0]
                target = alias.name if alias.asname else bound
                if target == SPAWN_MODULE and not is_wrapper:
                    report(node, f"imports {SPAWN_MODULE}; only {WRAPPER} may")
                if target in BANNED:
                    module_bindings[bound] = target
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module is None:
                continue  # a relative import cannot reach a stdlib module
            if node.module == SPAWN_MODULE and not is_wrapper:
                report(node, f"imports from {SPAWN_MODULE}; only {WRAPPER} may")
            if node.module not in BANNED:
                continue
            for alias in node.names:
                if alias.name == "*":
                    # Refused rather than resolved: a star import binds every name the
                    # module happens to export, so nothing here could tell which arrived.
                    report(node, f"star-imports {node.module}, which hides what it binds")
                    continue
                # Judged at the import as well as at each use: `from subprocess import
                # getoutput` has already crossed the boundary whether or not the name is
                # ever called, and an unused import is the shape a half-finished edit
                # leaves behind.
                judge(alias, node.module, alias.name)
                attr_bindings[alias.asname or alias.name] = (node.module, alias.name)

    # Pass 2: uses. `consumed` records the module names that appeared as the target of an
    # attribute access, so pass 3 can tell `subprocess.run` from a bare `subprocess`.
    consumed: set[int] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            module = module_bindings.get(node.value.id)
            if module is not None:
                consumed.add(id(node.value))
                judge(node, module, node.attr)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            binding = attr_bindings.get(node.id)
            if binding is not None:
                # A reference, not only a call: `spawn = getoutput` then `spawn(...)`
                # would otherwise walk straight past a call-only matcher.
                judge(node, *binding)
        elif isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == "shell":
                    report(
                        node,
                        "passes a shell= keyword; argv never goes through a shell, and "
                        "the default is already False, so naming it is either a no-op "
                        "or the hazard",
                    )

    # Pass 3: the wrapper's allowlist is about the module, not only about its attributes.
    # `sp = subprocess` and `getattr(subprocess, "getoutput")` both hand the module to
    # something this walk cannot follow, so the bare name is refused outright.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and module_bindings.get(node.id) == SPAWN_MODULE
            and id(node) not in consumed
        ):
            report(
                node,
                f"names the {SPAWN_MODULE} module itself; it may appear only as "
                f"{sorted(WRAPPER_ALLOWED)} attribute access",
            )

    return found


def _imports_subprocess(source: str) -> bool:
    """Whether this source imports `subprocess` at all, by either statement.

    Asked of the AST rather than of the text, for the reason the whole module is: a
    docstring saying "only this module may import subprocess" is not an import.
    """
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] == SPAWN_MODULE for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module == SPAWN_MODULE:
            return True
    return False


def package_modules() -> list[tuple[str, str]]:
    """Every module under `bessemer/`, as (repo-relative posix path, source)."""
    origin = bessemer.__file__
    assert origin is not None, "bessemer must be importable from a source tree"
    package = Path(origin).parent
    repo = package.parent
    return [
        (module.relative_to(repo).as_posix(), module.read_text(encoding="utf-8"))
        for module in sorted(package.rglob("*.py"))
    ]


class InventoryTest(unittest.TestCase):
    def test_the_ban_comes_from_the_guard_and_is_not_empty(self) -> None:
        """An empty import would make every test below pass while banning nothing."""
        self.assertTrue(SPAWN_ENTRY_POINTS)
        self.assertEqual(set(BANNED), {module.__name__ for module, _ in SPAWN_ENTRY_POINTS})

    def test_the_permitted_spawners_are_ones_the_guard_calls_spawn_paths(self) -> None:
        """If the guard ever stopped calling these spawn paths, the allowlist would be
        permitting names nothing else considers dangerous — which means the two have
        drifted, and this half would be enforcing a rule about nothing."""
        self.assertLessEqual(WRAPPER_SPAWNERS, BANNED[SPAWN_MODULE])

    def test_nothing_admitted_as_inert_can_start_a_process(self) -> None:
        """The assertion that keeps the widened allowlist from becoming a hole. `PIPE`
        and friends are admitted on the claim that they cannot spawn; the guard's
        inventory is the independent record of which names can. Adding `call` to the
        inert set fails here rather than quietly permitting it in the wrapper."""
        self.assertFalse(WRAPPER_INERT & BANNED[SPAWN_MODULE])

    def test_the_two_halves_of_the_allowlist_are_disjoint(self) -> None:
        """A name in both would make the test above pass while permitting a spawner."""
        self.assertFalse(WRAPPER_SPAWNERS & WRAPPER_INERT)
        self.assertEqual(WRAPPER_ALLOWED, WRAPPER_SPAWNERS | WRAPPER_INERT)


class OutsideTheWrapperTest(unittest.TestCase):
    """No module but the wrapper may reach a spawn path, by any spelling."""

    OTHER = "bessemer/elsewhere.py"

    def entry_points(self) -> list[tuple[str, str]]:
        return sorted((module.__name__, attr) for module, attr in SPAWN_ENTRY_POINTS)

    def test_every_spawn_entry_point_is_rejected(self) -> None:
        for module, attr in self.entry_points():
            with self.subTest(entry_point=f"{module}.{attr}"):
                self.assertTrue(violations(self.OTHER, f"import {module}\n{module}.{attr}()\n"))

    def test_every_spawn_entry_point_is_rejected_behind_a_module_alias(self) -> None:
        """`import subprocess as sp` is why the guard exports pairs and this test resolves
        bindings: the attribute alone does not say which module it came off."""
        for module, attr in self.entry_points():
            with self.subTest(entry_point=f"{module}.{attr}"):
                self.assertTrue(violations(self.OTHER, f"import {module} as _m\n_m.{attr}()\n"))

    def test_every_spawn_entry_point_is_rejected_when_imported_by_name(self) -> None:
        for module, attr in self.entry_points():
            with self.subTest(entry_point=f"{module}.{attr}"):
                self.assertTrue(
                    violations(self.OTHER, f"from {module} import {attr}\n{attr}()\n")
                )

    def test_an_unused_subprocess_import_is_still_rejected(self) -> None:
        """The import is the finding. A module that imports it has already lost the
        property that one file is the only way out of the package."""
        self.assertTrue(violations(self.OTHER, "import subprocess\n"))

    def test_a_star_import_of_a_spawning_module_is_rejected(self) -> None:
        self.assertTrue(violations(self.OTHER, "from os import *\n"))

    def test_ordinary_use_of_a_spawning_module_is_left_alone(self) -> None:
        """`os` is on the list for a handful of attributes, not as a module. Refusing it
        wholesale would make this gate something every later issue works around."""
        source = "import os\nimport os.path\nprint(os.environ, os.path.join('a', 'b'))\n"
        self.assertEqual(violations(self.OTHER, source), [])


class WrapperAllowlistTest(unittest.TestCase):
    """Inside the wrapper: `run` and `Popen`, the four inert names, and nothing else."""

    def test_run_and_popen_are_permitted(self) -> None:
        source = "import subprocess\nsubprocess.run([])\nsubprocess.Popen([])\n"
        self.assertEqual(violations(WRAPPER, source), [])

    def test_the_inert_names_are_permitted(self) -> None:
        """None of these can execute anything, and the wrapper needs `DEVNULL` today and
        `PIPE` the moment F3 streams a log."""
        for attr in sorted(WRAPPER_INERT):
            with self.subTest(attribute=attr):
                source = f"import subprocess\nsubprocess.run([], stdin=subprocess.{attr})\n"
                self.assertEqual(violations(WRAPPER, source), [])

    def test_the_inert_names_are_still_rejected_outside_the_wrapper(self) -> None:
        """Widening the wrapper's allowlist must not widen the import ban: a module that
        reaches for `subprocess.PIPE` has imported `subprocess` to get it."""
        for attr in sorted(WRAPPER_INERT):
            with self.subTest(attribute=attr):
                self.assertTrue(
                    violations(
                        "bessemer/elsewhere.py", f"import subprocess\nsubprocess.{attr}\n"
                    )
                )

    def test_every_other_spawn_entry_point_is_rejected(self) -> None:
        for attr in sorted(BANNED[SPAWN_MODULE] - WRAPPER_ALLOWED):
            with self.subTest(attribute=attr):
                self.assertTrue(
                    violations(WRAPPER, f"import subprocess\nsubprocess.{attr}('x')\n")
                )

    def test_shelling_out_without_an_argv_is_rejected(self) -> None:
        """`getoutput` shells out and trips neither an import check nor a shell= check —
        it is the specific hole an allowlist is here to close."""
        self.assertTrue(
            violations(WRAPPER, "import subprocess\nsubprocess.getoutput('docker info')\n")
        )

    def test_a_harmless_attribute_off_the_allowlist_is_rejected_too(self) -> None:
        """What still makes this an allowlist rather than a blocklist after the widening.
        `CompletedProcess` cannot spawn either, and is refused anyway: the four inert
        names are admitted because something needs them, not because they are harmless.
        `list2cmdline` is the sharper case — it builds exactly the command *string* this
        module exists to never produce."""
        for attr in ("CompletedProcess", "CalledProcessError", "list2cmdline"):
            with self.subTest(attribute=attr):
                self.assertTrue(violations(WRAPPER, f"import subprocess\nsubprocess.{attr}\n"))

    def test_importing_a_denied_name_directly_is_rejected(self) -> None:
        """The wrapper may import `subprocess`, so the attribute rule has to survive the
        name arriving through the import statement instead of a dotted access."""
        self.assertTrue(violations(WRAPPER, "from subprocess import getoutput\n"))

    def test_importing_run_directly_is_permitted(self) -> None:
        self.assertEqual(violations(WRAPPER, "from subprocess import run\nrun([])\n"), [])

    def test_the_module_may_not_be_laundered_through_another_name(self) -> None:
        """`sp = subprocess` rebinds it to a name no import statement declared, so the
        binding table cannot follow it. The bare reference is refused instead."""
        self.assertTrue(
            violations(WRAPPER, "import subprocess\nsp = subprocess\nsp.getoutput('x')\n")
        )

    def test_the_module_may_not_be_reached_through_getattr(self) -> None:
        self.assertTrue(
            violations(WRAPPER, "import subprocess\ngetattr(subprocess, 'getoutput')('x')\n")
        )

    def test_a_spawn_path_on_another_module_is_still_rejected_here(self) -> None:
        """The allowlist widens `subprocess`, not the wrapper. Everything else stays
        banned inside it exactly as it is outside."""
        for module, attr in sorted(
            (module.__name__, attr)
            for module, attr in SPAWN_ENTRY_POINTS
            if module.__name__ != SPAWN_MODULE
        ):
            with self.subTest(entry_point=f"{module}.{attr}"):
                self.assertTrue(violations(WRAPPER, f"import {module}\n{module}.{attr}()\n"))


class WrapperExportTest(unittest.TestCase):
    """The wrapper's own `run` is what every other module calls. It must not be caught."""

    OTHER = "bessemer/elsewhere.py"

    def test_calling_the_wrapper_is_not_a_violation(self) -> None:
        for source in (
            "from bessemer.proc import run\nrun(['git', 'status'], timeout=5)\n",
            "from bessemer import proc\nproc.run(['git', 'status'], timeout=5)\n",
            "import bessemer.proc\nbessemer.proc.run(['git', 'status'], timeout=5)\n",
            "from bessemer.proc import run as spawn\nspawn(['git'], timeout=5)\n",
        ):
            with self.subTest(source=source):
                self.assertEqual(violations(self.OTHER, source), [])


class ShellKeywordTest(unittest.TestCase):
    def test_shell_true_is_rejected_outside_the_wrapper(self) -> None:
        self.assertTrue(violations("bessemer/elsewhere.py", "spawn(['sh'], shell=True)\n"))

    def test_shell_true_is_rejected_inside_the_wrapper(self) -> None:
        """The wrapper is allowed to call `subprocess.run`; it is not allowed to hand it
        a shell. Without this the allowlisted half of the module would be unguarded."""
        self.assertTrue(
            violations(WRAPPER, "import subprocess\nsubprocess.run(['sh'], shell=True)\n")
        )

    def test_a_shell_flag_behind_a_variable_is_rejected(self) -> None:
        """What grep misses. The value is never evaluated — the keyword is the finding."""
        self.assertTrue(violations("bessemer/elsewhere.py", "sh = True\nspawn([], shell=sh)\n"))

    def test_shell_false_is_rejected_too(self) -> None:
        """`False` is already the default, so writing it is either a no-op or a rename
        away from the hazard. Refusing the keyword outright is what makes the rule
        decidable without evaluating the expression."""
        self.assertTrue(violations("bessemer/elsewhere.py", "spawn([], shell=False)\n"))

    def test_a_docstring_mentioning_shell_true_is_not_a_violation(self) -> None:
        """What grep gets wrong in the other direction, and why this walks an AST."""
        self.assertEqual(
            violations("bessemer/elsewhere.py", '"""Never pass shell=True to anything."""\n'),
            [],
        )


class PackageTest(unittest.TestCase):
    def test_no_module_crosses_the_argv_boundary(self) -> None:
        found = [
            violation
            for path, source in package_modules()
            for violation in violations(path, source)
        ]
        self.assertEqual(found, [])

    def test_the_scan_actually_reads_the_package(self) -> None:
        """A glob that matched nothing would make the test above pass by finding no code.
        Naming two modules pins both that the walk runs and that it reaches the wrapper."""
        paths = [path for path, _ in package_modules()]
        self.assertIn(WRAPPER, paths)
        self.assertIn("bessemer/cli.py", paths)

    def test_the_wrapper_is_the_only_module_that_imports_subprocess(self) -> None:
        """Stated as its own assertion because it is the property the whole boundary
        rests on, and because a violation list can be empty for the wrong reason."""
        importers = [path for path, source in package_modules() if _imports_subprocess(source)]
        self.assertEqual(importers, [WRAPPER])


if __name__ == "__main__":
    unittest.main()
