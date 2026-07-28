"""Tests for adapter discovery, the two TOML layers, and the precedence chain.

Every fixture here is a real directory tree under `tempfile`, not a mock filesystem. The
walk-up is the behaviour under test and it is made of `Path.resolve` and `Path.is_dir`; a
mock would test that this module calls the functions the test told it to call.

No test changes the working directory, and none reads the ambient environment: `load` takes
`start` and `env` as parameters precisely so that it need not. A test that `chdir`s is a
test that cannot run beside another one, and a test that inherits `os.environ` passes or
fails on what the developer happens to have exported.
"""

import ast
import tempfile
import tomllib
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import assert_never, assert_type
from unittest import mock

from bessemer import config
from bessemer.config import Config
from bessemer.outcome import Resolved, Unresolved


def config_source() -> str:
    """The text of `bessemer/config.py`, for the assertions that read the module itself.

    Read from `config.__file__` rather than from a hard-coded path, so a moved module fails
    loudly here instead of leaving these assertions passing against a file that no longer
    exists. `tests/test_config_purity.py` walks the same source the same way.
    """
    origin = config.__file__
    assert origin is not None, "bessemer.config must be importable from a source tree"
    return Path(origin).read_text(encoding="utf-8")


def read_layer_handlers() -> list[str]:
    """The exception types `_read_layer` catches, as written, in source order.

    Read out of the module's own AST rather than restated from the fixtures, because that
    is the only thing that can notice the module growing a case the tests do not know
    about. A literal compared to a hand-written fixture list agrees with itself.

    Exactly one `try` is asserted on the way past: a second one — inside a handler, or
    around a later step — would be a second parse boundary with none of the argument that
    justifies the first, and it would put the handler list below out of order.
    """
    functions = [
        node
        for node in ast.walk(ast.parse(config_source()))
        if isinstance(node, ast.FunctionDef) and node.name == "_read_layer"
    ]
    assert len(functions) == 1, f"expected one _read_layer, found {len(functions)}"
    blocks = [node for node in ast.walk(functions[0]) if isinstance(node, ast.Try)]
    assert len(blocks) == 1, f"expected one try block in _read_layer, found {len(blocks)}"
    return [
        "<bare except>" if handler.type is None else ast.unparse(handler.type)
        for handler in blocks[0].handlers
    ]


def unresolved_sites() -> int:
    """How many times `bessemer/config.py` constructs an `Unresolved`.

    One per failure case, which is what ties the hand-written case names below to the
    module rather than to the fixtures that provoke them.
    """
    return sum(
        1
        for node in ast.walk(ast.parse(config_source()))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        if node.func.id == "Unresolved"
    )


def nested_arrays(depth: int) -> str:
    """A TOML document whose single value is `depth` arrays inside one another.

    Tiny on disk and ruinous to a recursive-descent parser: at 5,000 the document below is
    ten kilobytes. That asymmetry is the reason the total clause exists — an adapter file
    nobody would look at twice can take `tomllib` past the recursion limit.
    """
    return f"v = {'[' * depth}{']' * depth}\n"


class TreeTest(unittest.TestCase):
    """Base for tests that need a throwaway directory tree."""

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        # Resolved, because `find_adapter_dir` resolves: on macOS a temporary directory is
        # handed out as `/var/...`, which is a symlink to `/private/var/...`. Comparing an
        # unresolved fixture path against a resolved answer fails on a correct walk.
        self.tmp = Path(holder.name).resolve()

    def make_adapter(self, root: Path, files: Mapping[str, str] | None = None) -> Path:
        """Create `root/.bessemer/` containing `files`, and return the adapter directory."""
        adapter = root / config.ADAPTER_DIR
        adapter.mkdir(parents=True)
        for name, text in (files or {}).items():
            (adapter / name).write_text(text, encoding="utf-8")
        return adapter

    def load(
        self,
        start: Path,
        env: Mapping[str, str] | None = None,
        flags: Mapping[str, object] | None = None,
    ) -> Config:
        """Load from `start`, failing the test rather than the type checker on `Unresolved`.

        `env` defaults to empty rather than to `load`'s own default of `os.environ`, so a
        test cannot accidentally depend on the developer's exported variables.

        Unwraps the `Resolved`, so the tests below read as assertions about a `Config`
        rather than about the union. `NarrowingTest` is where the union itself is under
        test; here it would be noise repeated forty times.
        """
        loaded = config.load(start=start, env={} if env is None else env, flags=flags)
        assert isinstance(loaded, Resolved), loaded
        return loaded.value


class DiscoveryTest(TreeTest):
    def test_finds_the_adapter_at_the_root_itself(self) -> None:
        adapter = self.make_adapter(self.tmp)
        self.assertEqual(config.find_adapter_dir(self.tmp), adapter)

    def test_finds_the_adapter_from_a_nested_subdirectory(self) -> None:
        """The walk-up is the whole point: a user runs bessemer from wherever they are."""
        adapter = self.make_adapter(self.tmp)
        nested = self.tmp / "src" / "deep" / "deeper"
        nested.mkdir(parents=True)
        self.assertEqual(config.find_adapter_dir(nested), adapter)

    def test_the_nearest_adapter_wins(self) -> None:
        """Two adapters in one chain is the shape issue 05's root-agreement check exists to
        refuse. Discovery still has to be deterministic about which one it found, or that
        check would be reporting on something that varies."""
        self.make_adapter(self.tmp)
        inner_root = self.tmp / "nested"
        inner = self.make_adapter(inner_root)
        self.assertEqual(config.find_adapter_dir(inner_root / "src"), inner)

    def test_a_bessemer_file_is_not_an_adapter(self) -> None:
        """`.bessemer` as a regular file is skipped and the walk continues. Stopping there
        would trade a working walk-up for an error about something that is not config."""
        adapter = self.make_adapter(self.tmp)
        below = self.tmp / "nested"
        below.mkdir()
        (below / config.ADAPTER_DIR).write_text("not a directory\n", encoding="utf-8")
        self.assertEqual(config.find_adapter_dir(below), adapter)

    def test_outside_any_adapter_the_walk_ends_at_the_filesystem_root(self) -> None:
        """`None` here is also the termination proof. Nothing above a temporary directory
        has a `.bessemer/`, so returning at all means the walk reached `/` and stopped; a
        walk that did not terminate would hang this test rather than fail it.

        Asserted this way rather than as `find_adapter_dir(Path("/"))`, which would be an
        assertion about the host's root directory rather than about this function.
        """
        nowhere = self.tmp / "a" / "b" / "c"
        nowhere.mkdir(parents=True)
        self.assertIsNone(config.find_adapter_dir(nowhere))

    def test_a_path_spelled_with_dot_dot_walks_the_tree_it_sits_in(self) -> None:
        adapter = self.make_adapter(self.tmp)
        nested = self.tmp / "src"
        nested.mkdir()
        self.assertEqual(config.find_adapter_dir(nested / ".." / "src"), adapter)

    def test_the_config_root_is_the_directory_holding_the_adapter(self) -> None:
        self.make_adapter(self.tmp)
        loaded = self.load(self.tmp / "src")
        self.assertEqual(loaded.root, self.tmp)
        self.assertEqual(loaded.adapter_dir, self.tmp / config.ADAPTER_DIR)


class NotFoundTest(TreeTest):
    def test_not_found_is_a_reason_and_a_hint_rather_than_an_exception(self) -> None:
        """The criterion is "no exception, no traceback". `load` returning at all is the
        assertion; the isinstance check is what stops it passing on a `Config`."""
        loaded = config.load(start=self.tmp, env={})
        self.assertIsInstance(loaded, Unresolved)
        assert isinstance(loaded, Unresolved)
        self.assertIn(config.ADAPTER_DIR, loaded.reason)
        self.assertIn(str(self.tmp), loaded.reason)
        self.assertTrue(loaded.hint)

    def test_the_hint_does_not_name_a_command_that_does_not_exist(self) -> None:
        """`bessemer init` lands in F6. A hint telling a stuck user to run a subcommand
        this build does not have would be worse than no hint at all."""
        loaded = config.load(start=self.tmp, env={})
        assert isinstance(loaded, Unresolved)
        self.assertNotIn("init", loaded.hint)


class LayerTest(TreeTest):
    def test_neither_layer_present_is_not_an_error(self) -> None:
        """An adapter with a Dockerfile and no config at all is a legitimate adapter."""
        self.make_adapter(self.tmp)
        loaded = self.load(self.tmp)
        self.assertEqual(dict(loaded.committed), {})
        self.assertEqual(dict(loaded.local), {})
        # The literal, not `config.DEFAULTS["specs_dir"]`: an assertion that reads the
        # value out of the module cannot notice the module changing it. See `SchemaTest`.
        self.assertEqual(loaded.get("specs_dir"), ".bessemer/specs")

    def test_the_committed_layer_alone_works(self) -> None:
        self.make_adapter(self.tmp, {config.COMMITTED_FILE: 'base = "main"\n'})
        loaded = self.load(self.tmp)
        self.assertEqual(loaded.get("base"), "main")
        self.assertEqual(loaded.layer_of("base"), config.COMMITTED)

    def test_the_local_layer_alone_works(self) -> None:
        self.make_adapter(self.tmp, {config.LOCAL_FILE: 'base = "develop"\n'})
        loaded = self.load(self.tmp)
        self.assertEqual(loaded.get("base"), "develop")
        self.assertEqual(loaded.layer_of("base"), config.LOCAL)

    def test_local_overrides_committed_for_the_same_key(self) -> None:
        self.make_adapter(
            self.tmp,
            {
                config.COMMITTED_FILE: 'base = "main"\nspecs_dir = "shared"\n',
                config.LOCAL_FILE: 'base = "develop"\n',
            },
        )
        loaded = self.load(self.tmp)
        self.assertEqual(loaded.get("base"), "develop")
        # The key the local layer did not mention still comes from the committed one:
        # local wins per key, and does not replace the layer beneath it wholesale.
        self.assertEqual(loaded.get("specs_dir"), "shared")

    def test_a_key_no_layer_sets_reads_as_none(self) -> None:
        self.make_adapter(self.tmp)
        loaded = self.load(self.tmp)
        self.assertIsNone(loaded.get("base"))
        self.assertIsNone(loaded.layer_of("base"))


class PrecedenceTest(TreeTest):
    """CLI flags > `BESSEMER_*` env vars > local > committed > defaults (ADR 0001).

    Proven by peeling: every layer is supplied at once, then removed one at a time from the
    top, so each step asserts that the layer under test beats *everything* below it rather
    than only its immediate neighbour. `layer_of` is asserted alongside the value, because a
    value alone cannot tell a correct precedence from two layers that happen to agree.
    """

    KEY = "specs_dir"

    def setUp(self) -> None:
        super().setUp()
        self.make_adapter(
            self.tmp,
            {
                config.COMMITTED_FILE: f'{self.KEY} = "from-committed"\n',
                config.LOCAL_FILE: f'{self.KEY} = "from-local"\n',
            },
        )
        self.env = {config.ENV_PREFIX + self.KEY.upper(): "from-env"}
        self.flags: dict[str, object] = {self.KEY: "from-flag"}

    def assert_wins(
        self,
        expected: object,
        layer: str,
        env: Mapping[str, str] | None = None,
        flags: Mapping[str, object] | None = None,
    ) -> None:
        loaded = self.load(self.tmp, env=env, flags=flags)
        self.assertEqual(loaded.get(self.KEY), expected)
        self.assertEqual(loaded.layer_of(self.KEY), layer)

    def test_a_flag_beats_everything(self) -> None:
        self.assert_wins("from-flag", config.FLAG, env=self.env, flags=self.flags)

    def test_an_env_var_beats_local_and_below(self) -> None:
        self.assert_wins("from-env", config.ENV, env=self.env)

    def test_local_beats_committed_and_below(self) -> None:
        self.assert_wins("from-local", config.LOCAL)

    def test_committed_beats_the_default(self) -> None:
        (self.tmp / config.ADAPTER_DIR / config.LOCAL_FILE).unlink()
        self.assert_wins("from-committed", config.COMMITTED)

    def test_the_default_is_the_floor(self) -> None:
        adapter = self.tmp / config.ADAPTER_DIR
        (adapter / config.LOCAL_FILE).unlink()
        (adapter / config.COMMITTED_FILE).unlink()
        self.assert_wins(".bessemer/specs", config.DEFAULT)

    def test_the_five_layers_are_distinct_and_ordered(self) -> None:
        """A duplicated layer name would make the peel above pass while collapsing two
        rungs of the chain into one."""
        self.assertEqual(len(set(config.PRECEDENCE)), len(config.PRECEDENCE))
        self.assertEqual(
            config.PRECEDENCE,
            (config.FLAG, config.ENV, config.LOCAL, config.COMMITTED, config.DEFAULT),
        )


class EnvironmentTest(TreeTest):
    def test_an_env_var_for_a_key_this_loader_does_not_read_is_ignored(self) -> None:
        """The environment layer is built from `KNOWN_KEYS`, not by scanning for the
        prefix, so a `BESSEMER_*` name that is not a key cannot enter it."""
        self.make_adapter(self.tmp)
        loaded = self.load(self.tmp, env={"BESSEMER_TELEPORT": "yes"})
        self.assertEqual(dict(loaded.env), {})

    def test_there_is_no_bessemer_root_escape_hatch(self) -> None:
        """Discovery is not a config value (ADR 0002). Setting `BESSEMER_ROOT` to a
        directory that has an adapter must not redirect the walk to it — asserted against a
        real second adapter, so the test fails if the variable ever starts being read."""
        elsewhere = self.tmp / "elsewhere"
        self.make_adapter(elsewhere, {config.COMMITTED_FILE: 'base = "hijacked"\n'})
        here = self.tmp / "here"
        self.make_adapter(here, {config.COMMITTED_FILE: 'base = "real"\n'})
        loaded = self.load(here, env={"BESSEMER_ROOT": str(elsewhere)})
        self.assertEqual(loaded.root, here)
        self.assertEqual(loaded.get("base"), "real")
        self.assertNotIn("root", config.KNOWN_KEYS)

    def test_a_set_but_empty_env_var_counts_as_set(self) -> None:
        """Treating `""` as absent would invent a rule the shell does not have, and would
        ignore a value the user can see in their own environment."""
        self.make_adapter(self.tmp, {config.COMMITTED_FILE: 'base = "main"\n'})
        loaded = self.load(self.tmp, env={"BESSEMER_BASE": ""})
        self.assertEqual(loaded.get("base"), "")
        self.assertEqual(loaded.layer_of("base"), config.ENV)


class FlagTest(TreeTest):
    def test_an_unsupplied_flag_does_not_shadow_the_layers_below(self) -> None:
        """`argparse` spells an absent option as `None`, and this layer is built from a
        `Namespace`. Without the drop, every option bessemer ever adds would silently
        override the config file for every user who did not pass it."""
        self.make_adapter(self.tmp, {config.COMMITTED_FILE: 'base = "main"\n'})
        loaded = self.load(self.tmp, flags={"base": None})
        self.assertEqual(loaded.get("base"), "main")
        self.assertEqual(loaded.layer_of("base"), config.COMMITTED)

    def test_a_flag_naming_something_that_is_not_a_config_key_raises(self) -> None:
        """Flags are bessemer calling bessemer, so this is a bug in the package rather than
        a user's mistake — the one place here that must be loud rather than structured."""
        self.make_adapter(self.tmp)
        with self.assertRaises(ValueError):
            config.load(start=self.tmp, env={}, flags={"teleport": "yes"})


class MalformedTest(TreeTest):
    def test_malformed_committed_toml_is_a_reason_naming_the_file(self) -> None:
        self.make_adapter(self.tmp, {config.COMMITTED_FILE: "base = \n"})
        loaded = config.load(start=self.tmp, env={})
        self.assertIsInstance(loaded, Unresolved)
        assert isinstance(loaded, Unresolved)
        self.assertIn(config.COMMITTED_FILE, loaded.reason)
        self.assertIn("not valid TOML", loaded.reason)
        self.assertTrue(loaded.hint)

    def test_malformed_local_toml_is_a_reason_naming_that_file_instead(self) -> None:
        """Naming the wrong file is the failure mode that costs a user the most time, so
        the two layers are asserted separately rather than as one 'malformed' case."""
        self.make_adapter(
            self.tmp,
            {
                config.COMMITTED_FILE: 'base = "main"\n',
                config.LOCAL_FILE: "base = [unclosed\n",
            },
        )
        loaded = config.load(start=self.tmp, env={})
        assert isinstance(loaded, Unresolved)
        self.assertIn(config.LOCAL_FILE, loaded.reason)
        self.assertNotIn(f"/{config.COMMITTED_FILE}", loaded.reason)

    def test_a_file_that_is_not_utf8_is_a_reason_with_its_own_hint(self) -> None:
        """TOML mandates UTF-8, so this is malformed TOML — but `tomllib.load` decodes
        before it parses, so it arrives as a `UnicodeDecodeError`. That is a `ValueError`,
        not a `TOMLDecodeError` and not an `OSError`, so it escaped every clause and reached
        the caller as a traceback. Written as bytes rather than as text because an encoder
        is what produces it: a config file saved by an editor defaulting to latin-1.
        """
        adapter = self.make_adapter(self.tmp)
        (adapter / config.COMMITTED_FILE).write_bytes(b'base = "m\xffain"\n')
        loaded = config.load(start=self.tmp, env={})
        self.assertIsInstance(loaded, Unresolved)
        assert isinstance(loaded, Unresolved)
        self.assertIn(config.COMMITTED_FILE, loaded.reason)
        self.assertIn("not UTF-8", loaded.reason)
        self.assertIn("UTF-8", loaded.hint)

    def test_the_encoding_hint_differs_from_the_syntax_hint(self) -> None:
        """Re-saving a file in another encoding is not the same fix as correcting a line, so
        one shared hint would be wrong for whichever case it was not written for. Both are
        produced here and compared, rather than each being asserted non-empty alone."""
        syntax = self.make_adapter(self.tmp / "syntax", {config.COMMITTED_FILE: "base = \n"})
        encoding = self.make_adapter(self.tmp / "encoding")
        (encoding / config.COMMITTED_FILE).write_bytes(b'base = "\xff"\n')

        hints = []
        for adapter in (syntax, encoding):
            loaded = config.load(start=adapter.parent, env={})
            assert isinstance(loaded, Unresolved)
            hints.append(loaded.hint)
        self.assertNotEqual(hints[0], hints[1])

    def test_deeply_nested_arrays_are_a_reason_rather_than_a_recursion_error(self) -> None:
        """The case that forced the parse boundary to be total.

        `tomllib` recurses per bracket, so a 10 KB file exhausts the stack and raises
        `RecursionError` — a `RuntimeError`, which is neither `TOMLDecodeError` nor
        `UnicodeDecodeError` nor `OSError`. It escaped all three and reached the caller as a
        traceback, from a file a user could plausibly write.

        The exception type is asserted in the reason, not merely that *something* was
        returned: that is what distinguishes "the total clause caught it and said what it
        was" from "the total clause caught it and said nothing useful".
        """
        self.make_adapter(self.tmp, {config.COMMITTED_FILE: nested_arrays(NESTING_DEPTH)})
        loaded = config.load(start=self.tmp, env={})
        self.assertIsInstance(loaded, Unresolved)
        assert isinstance(loaded, Unresolved)
        self.assertIn(config.COMMITTED_FILE, loaded.reason)
        self.assertIn("RecursionError", loaded.reason)
        self.assertTrue(loaded.hint)

    def test_the_nesting_fixture_is_what_makes_that_test_mean_anything(self) -> None:
        """The control. `nested_arrays` at a shallow depth must parse *cleanly* — otherwise
        the test above would pass on a fixture that was malformed for some ordinary reason
        and never exercised the total clause at all."""
        self.make_adapter(self.tmp, {config.COMMITTED_FILE: nested_arrays(3)})
        loaded = self.load(self.tmp)
        self.assertEqual(dict(loaded.committed), {"v": [[[]]]})

    def test_the_total_clause_does_not_absorb_a_defect_in_bessemers_own_code(self) -> None:
        """The scope of the clause, where the test above pins its width.

        The total clause is only defensible because nothing of bessemer's runs inside it —
        otherwise our bug is reported as the user's broken file, with a hint sending them to
        inspect something that is fine. Measured before this test existed, with the
        constructor one line higher: a valid `config.toml` came back as
        `Unresolved(reason="...could not be parsed: TypeError: ...")`.

        `Resolved.__init__` stands in for a future defect in `bessemer.outcome` — a module
        now shared with issue 05's resolvers, so it is a type other issues will edit.
        Patched on the class rather than on the name `config.Resolved`, because `load`
        matches on that name and a non-class stand-in fails with `called match pattern must
        be a class` before reaching what is under test.

        This passes only once the constructor sits outside the block, which is what makes it
        a pin rather than a restatement of the docstring.
        """

        def explode(instance: object, *args: object, **kwargs: object) -> None:
            raise TypeError("a defect in bessemer.outcome, not in the user's file")

        self.make_adapter(self.tmp, {config.COMMITTED_FILE: 'base = "main"\n'})
        with mock.patch.object(Resolved, "__init__", explode):
            with self.assertRaises(TypeError):
                config.load(start=self.tmp, env={})

    def test_the_total_clause_does_not_swallow_a_keyboard_interrupt(self) -> None:
        """`except Exception`, not `except BaseException`. Ctrl-C during a load is not a
        malformed adapter, and reporting it as one would make the file look broken.

        Driven by patching `tomllib.load`, because there is no config file that provokes a
        `KeyboardInterrupt` — the point under test is the width of the clause, and that is
        the only thing this fixture stands in for.
        """

        def interrupt(*args: object, **kwargs: object) -> object:
            raise KeyboardInterrupt

        self.make_adapter(self.tmp, {config.COMMITTED_FILE: 'base = "main"\n'})
        with mock.patch.object(tomllib, "load", interrupt):
            with self.assertRaises(KeyboardInterrupt):
                config.load(start=self.tmp, env={})

    def test_a_dangling_symlink_is_an_absent_layer(self) -> None:
        """The named limit in `_read_layer`: a symlink to nothing raises
        `FileNotFoundError`, which is the same answer as no file at all — "no config here".
        Pinned so the comment saying so stays true, and so that anyone who decides a broken
        link should be *reported* instead has to change a test rather than discover this."""
        adapter = self.make_adapter(self.tmp, {config.LOCAL_FILE: 'base = "develop"\n'})
        (adapter / config.COMMITTED_FILE).symlink_to(adapter / "gone.toml")
        loaded = self.load(self.tmp)
        self.assertEqual(dict(loaded.committed), {})
        self.assertEqual(loaded.get("base"), "develop")

    def test_a_layer_that_is_a_directory_is_a_read_failure_not_a_parse_failure(self) -> None:
        """A different reason because it has a different fix. Both are `OSError` territory
        rather than `tomllib`'s, and neither may escape as a traceback."""
        adapter = self.make_adapter(self.tmp)
        (adapter / config.COMMITTED_FILE).mkdir()
        loaded = config.load(start=self.tmp, env={})
        self.assertIsInstance(loaded, Unresolved)
        assert isinstance(loaded, Unresolved)
        self.assertIn("could not be read", loaded.reason)


class KnownKeyTest(TreeTest):
    def test_asking_for_a_key_this_loader_does_not_read_raises(self) -> None:
        """`None` would read as "configured to nothing" forever — the defect shape ADR 0002
        refuses for `ctx.ok()`, arriving here instead."""
        self.make_adapter(self.tmp)
        loaded = self.load(self.tmp)
        with self.assertRaises(ValueError):
            loaded.get("teleport")
        with self.assertRaises(ValueError):
            loaded.layer_of("teleport")

    def test_every_default_names_a_key_this_loader_reads(self) -> None:
        """A default for an unknown key is unreachable through `get`, so it would be a
        setting that appears configured and can never be read."""
        self.assertLessEqual(set(config.DEFAULTS), set(config.KNOWN_KEYS))

    def test_an_unrecognised_key_in_a_file_is_reported_and_not_rejected(self) -> None:
        """The core is pinned by a committed ref, so an older core reads a newer config
        file routinely. Erroring would turn F3's `container_env_keys` into a hard failure
        for everyone who had not yet bumped the pin."""
        self.make_adapter(
            self.tmp,
            {
                config.COMMITTED_FILE: 'base = "main"\ncontainer_env_keys = ["FOO"]\n',
                config.LOCAL_FILE: "model = 42\n",
            },
        )
        loaded = self.load(self.tmp)
        self.assertEqual(loaded.unknown_keys(), ("container_env_keys", "model"))
        self.assertEqual(loaded.get("base"), "main")

    def test_an_adapter_using_only_known_keys_reports_none(self) -> None:
        self.make_adapter(self.tmp, {config.COMMITTED_FILE: 'base = "main"\n'})
        self.assertEqual(self.load(self.tmp).unknown_keys(), ())


class SchemaTest(unittest.TestCase):
    """The key set and its defaults, restated by hand.

    Deliberately literals rather than assertions against `config.KNOWN_KEYS` and
    `config.DEFAULTS`. A test that reads the thing it checks cannot notice that thing
    changing: `KNOWN_KEYS |= {"image"}` and `DEFAULTS = {"specs_dir": "specs"}` both survive
    a derived assertion with every other test still green.

    That matters beyond tidiness. This issue owns the schema and issue 07's adapter conforms
    to it, so a key appearing here without a consumer is a claim that bessemer is configured
    by something it never looks at — and it appears silently, which is the half a derived
    assertion cannot see. Adding a key is meant to cost a deliberate edit in this file.
    """

    def test_the_loader_reads_exactly_these_three_keys(self) -> None:
        self.assertEqual(set(config.KNOWN_KEYS), {"source", "base", "specs_dir"})

    def test_specs_dir_is_the_only_key_with_a_default_and_this_is_its_value(self) -> None:
        self.assertEqual(dict(config.DEFAULTS), {"specs_dir": ".bessemer/specs"})

    def test_base_has_no_default(self) -> None:
        """Stated separately because it is load-bearing rather than incidental: defaults sit
        *above* issue 05's `origin/HEAD` auto-detect, so a default for `base` would make
        that resolver dead code on every machine before it is written."""
        self.assertNotIn("base", config.DEFAULTS)

    def test_source_has_no_default(self) -> None:
        """Bessemer cannot guess which ref a team pinned, and inventing one would send a
        run at a version nobody chose."""
        self.assertNotIn("source", config.DEFAULTS)


class SourceKeyTest(TreeTest):
    """`source` gets its own coverage here rather than only wherever it turns up.

    It is one of the two keys issue 07's committed `config.toml` carries, and it is the one
    that decides which core version a run executes — a key readable only by accident of some
    other test's fixture is a key nothing actually pins.
    """

    PIN = "git+https://github.com/HireAnEsquire/bessemer@v0.1.0"

    def test_the_source_pin_is_read_from_the_committed_layer(self) -> None:
        self.make_adapter(self.tmp, {config.COMMITTED_FILE: f'source = "{self.PIN}"\n'})
        loaded = self.load(self.tmp)
        self.assertEqual(loaded.get("source"), self.PIN)
        self.assertEqual(loaded.layer_of("source"), config.COMMITTED)

    def test_the_local_layer_can_override_the_source_pin(self) -> None:
        """Not forbidden here: `container_env_keys` is the one committed-only key, and it
        lands in F3. A dev pointing at a local core build is the case this serves."""
        self.make_adapter(
            self.tmp,
            {
                config.COMMITTED_FILE: f'source = "{self.PIN}"\n',
                config.LOCAL_FILE: 'source = "git+file:///tmp/bessemer@wip"\n',
            },
        )
        self.assertEqual(self.load(self.tmp).get("source"), "git+file:///tmp/bessemer@wip")

    def test_the_source_pin_is_read_from_the_environment(self) -> None:
        self.make_adapter(self.tmp, {config.COMMITTED_FILE: f'source = "{self.PIN}"\n'})
        loaded = self.load(self.tmp, env={"BESSEMER_SOURCE": "git+ssh://elsewhere@main"})
        self.assertEqual(loaded.get("source"), "git+ssh://elsewhere@main")
        self.assertEqual(loaded.layer_of("source"), config.ENV)

    def test_an_adapter_that_sets_no_source_reads_as_none(self) -> None:
        self.make_adapter(self.tmp)
        loaded = self.load(self.tmp)
        self.assertIsNone(loaded.get("source"))
        self.assertIsNone(loaded.layer_of("source"))


NESTING_DEPTH = 5000
"""Bracket depth for the deeply-nested fixture below.

Far past the recursion limit rather than near it. Measured on this machine: 300 levels
parses cleanly, 600 raises — so the threshold is a property of how much stack is left when
the parser is entered, which varies with the runner, the platform, and how deep in a test
the call happens. A depth chosen just above the observed limit would be a test that passes
here and parses cleanly under a different runner, taking the total clause's only coverage
with it.

**Whether the interpreter raises or dies is a platform question, and it is covered rather
than assumed.** CPython's guard is a recursion counter, not a stack probe, so on a build
whose C stack is smaller than the counter allows this could segfault instead. That would not
be silent: CI runs `make check` on `ubuntu-latest`, so this fixture executes on Linux/glibc
on every pull request, and a crashed interpreter fails the Makefile's summary-line gate —
which exists precisely because a vanished runner exits 0 (see the `SUITE_FINISHED` comment).
A hard crash therefore shows up as a red build naming this file, not as lost coverage.
"""


class FailureCaseTest(TreeTest):
    """The five ways `load` can fail, produced side by side and compared.

    Each already has its own test above, and each of those asserts a substring. None of
    them can see that two cases have collapsed into the same wording — an `except` clause
    deleted, or two clauses given one shared hint, leaves every individual assertion green
    while a user reading the output can no longer tell which mistake they made.

    The five are restated here by hand. On its own that closes the set against *collapse*
    and nothing else: `CASE_NAMES` was previously compared only against `cases()`, which is
    hand-written beside it, so the two agreed with each other while saying nothing about
    the module. A sixth `except PermissionError` clause with its own reason and hint was
    planted in `_read_layer` and the whole suite stayed green.

    So the literal is anchored to the module's own AST — the handler list in `_read_layer`
    and the number of `Unresolved` constructions — the way `tests/test_config_purity.py`
    already walks this source. Growth now costs an edit here as well as in the module,
    which is what the criterion asks for.

    Distinguishability rests entirely on this prose. There is no tag and no subtype (see
    the module docstring of `bessemer.config`), so a caller that wanted to branch would
    have to match substrings — and a rewording would break it silently. Recorded here
    rather than fixed by inventing a tag nothing reads yet.
    """

    CASE_NAMES = [
        "absorbed by the total clause",
        "not UTF-8",
        "not found",
        "unparseable TOML",
        "unreadable",
    ]
    """Every way `load` can fail, sorted. Hand-written; see the class docstring."""

    HANDLERS = [
        "FileNotFoundError",
        "UnicodeDecodeError",
        "tomllib.TOMLDecodeError",
        "OSError",
        "Exception",
    ]
    """Every `except` clause in `_read_layer`, as written and in order. Hand-written.

    Five handlers and five cases is a coincidence of arithmetic, not a correspondence, and
    the two lists must not be tied together: `FileNotFoundError` produces no failure at all
    — an absent layer is an empty layer — and the "not found" case comes from `load`, which
    has no handler. They are asserted separately for that reason.

    The order is part of the assertion. `Exception` last is what leaves the specific clauses
    reachable; `FileNotFoundError` before `OSError` is what keeps a missing file from being
    reported as unreadable.
    """

    def cases(self) -> dict[str, Unresolved]:
        """One `Unresolved` per failure case, keyed by the name used in the criteria."""
        no_adapter = self.tmp / "no-adapter"
        no_adapter.mkdir()

        syntax = self.make_adapter(self.tmp / "syntax", {config.COMMITTED_FILE: "base = \n"})
        encoding = self.make_adapter(self.tmp / "encoding")
        (encoding / config.COMMITTED_FILE).write_bytes(b'base = "\xff"\n')
        unreadable = self.make_adapter(self.tmp / "unreadable")
        (unreadable / config.COMMITTED_FILE).mkdir()
        nested = self.make_adapter(
            self.tmp / "nested",
            {config.COMMITTED_FILE: nested_arrays(NESTING_DEPTH)},
        )

        starts = {
            "not found": no_adapter,
            "unparseable TOML": syntax.parent,
            "not UTF-8": encoding.parent,
            "unreadable": unreadable.parent,
            "absorbed by the total clause": nested.parent,
        }
        found: dict[str, Unresolved] = {}
        for name, start in starts.items():
            loaded = config.load(start=start, env={})
            assert isinstance(loaded, Unresolved), (name, loaded)
            found[name] = loaded
        return found

    def test_read_layer_catches_exactly_these_types_in_this_order(self) -> None:
        """Read out of the module's AST, so a sixth clause fails here.

        This is the half `CASE_NAMES` alone could not provide: a planted
        `except PermissionError` with its own reason and hint left the suite green and
        exit 0, because every other assertion in this file compares one hand-written list
        against another.
        """
        self.assertEqual(read_layer_handlers(), self.HANDLERS)

    def test_the_module_produces_one_failure_per_named_case(self) -> None:
        """The second anchor, and the one that also covers `load`.

        `read_layer_handlers` cannot see a case added outside `_read_layer` — a second
        early return in `load`, say — and counting `Unresolved` constructions cannot see a
        handler added that returns something else. Neither subsumes the other.
        """
        self.assertEqual(unresolved_sites(), len(self.CASE_NAMES))

    def test_there_are_five_failure_cases_and_no_two_read_alike(self) -> None:
        cases = self.cases()
        self.assertEqual(sorted(cases), self.CASE_NAMES)
        pairs = [(outcome.reason, outcome.hint) for outcome in cases.values()]
        self.assertEqual(len(set(pairs)), len(pairs))
        # Reason and hint separately, not only as a pair: two cases sharing a hint while
        # differing in an exception's own text still produce five distinct pairs above, and
        # a shared hint is the collapse a user actually feels.
        self.assertEqual(len({reason for reason, _ in pairs}), len(pairs))
        self.assertEqual(len({hint for _, hint in pairs}), len(pairs))

    def test_every_failure_case_carries_both_a_reason_and_a_hint(self) -> None:
        for name, outcome in self.cases().items():
            with self.subTest(case=name):
                self.assertTrue(outcome.reason, name)
                self.assertTrue(outcome.hint, name)

    def test_every_failure_about_a_file_names_that_file(self) -> None:
        """The four file-shaped cases name `config.toml`; "not found" has no file to name
        and names the directory the walk started from instead."""
        cases = self.cases()
        for name in (
            "unparseable TOML",
            "not UTF-8",
            "unreadable",
            "absorbed by the total clause",
        ):
            with self.subTest(case=name):
                self.assertIn(config.COMMITTED_FILE, cases[name].reason)
        self.assertIn(str(self.tmp / "no-adapter"), cases["not found"].reason)


class NarrowingTest(TreeTest):
    """`match` over the union, with the value's type checked rather than described.

    `assert_type` is the assertion here, and it is a static one: mypy fails the build if the
    narrowed type is not exactly what is written, and `make check` runs mypy over `tests/`.
    Written this way because a runtime `assertIsInstance` cannot see the difference between
    a correctly generic `Resolved[Config]` and a `Resolved[Any]` that has quietly erased
    every call site's type — which is the failure this criterion is about.
    """

    def test_a_loaded_config_narrows_to_config_in_a_match_block(self) -> None:
        self.make_adapter(self.tmp, {config.COMMITTED_FILE: 'base = "main"\n'})
        match config.load(start=self.tmp, env={}):
            case Resolved(value=loaded):
                assert_type(loaded, Config)
                # Exercised, not merely annotated: a member access is what would fail if
                # `loaded` came back as `object`.
                self.assertEqual(loaded.get("base"), "main")
            case Unresolved(reason=reason):
                self.fail(reason)

    def test_a_failed_load_narrows_to_unresolved_in_a_match_block(self) -> None:
        match config.load(start=self.tmp, env={}):
            case Resolved(value=loaded):
                self.fail(f"expected no adapter, got {loaded.root}")
            case Unresolved(reason=reason, hint=hint):
                assert_type(reason, str)
                assert_type(hint, str)
                self.assertIn(config.ADAPTER_DIR, reason)

    def test_the_two_cases_exhaust_the_union(self) -> None:
        """`assert_never` is a compile-time claim that nothing else can reach it. If a third
        member were ever added to the union, mypy fails here — which is the whole reason the
        return type is a closed union rather than an `Outcome` protocol."""
        for start in (self.tmp, self.make_adapter(self.tmp / "real").parent):
            match config.load(start=start, env={}):
                case Resolved():
                    pass
                case Unresolved():
                    pass
                case unreachable:
                    assert_never(unreachable)


if __name__ == "__main__":
    unittest.main()
