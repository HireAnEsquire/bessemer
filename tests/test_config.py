"""Tests for adapter discovery, the two TOML layers, and the precedence chain.

Every fixture here is a real directory tree under `tempfile`, not a mock filesystem. The
walk-up is the behaviour under test and it is made of `Path.resolve` and `Path.is_dir`; a
mock would test that this module calls the functions the test told it to call.

No test changes the working directory, and none reads the ambient environment: `load` takes
`start` and `env` as parameters precisely so that it need not. A test that `chdir`s is a
test that cannot run beside another one, and a test that inherits `os.environ` passes or
fails on what the developer happens to have exported.
"""

import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

from bessemer import config
from bessemer.config import Config, NotLoaded


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
        """Load from `start`, failing the test rather than the type checker on `NotLoaded`.

        `env` defaults to empty rather than to `load`'s own default of `os.environ`, so a
        test cannot accidentally depend on the developer's exported variables.
        """
        loaded = config.load(start=start, env={} if env is None else env, flags=flags)
        assert not isinstance(loaded, NotLoaded), loaded
        return loaded


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
        self.assertIsInstance(loaded, NotLoaded)
        assert isinstance(loaded, NotLoaded)
        self.assertIn(config.ADAPTER_DIR, loaded.reason)
        self.assertIn(str(self.tmp), loaded.reason)
        self.assertTrue(loaded.hint)

    def test_the_hint_does_not_name_a_command_that_does_not_exist(self) -> None:
        """`bessemer init` lands in F6. A hint telling a stuck user to run a subcommand
        this build does not have would be worse than no hint at all."""
        loaded = config.load(start=self.tmp, env={})
        assert isinstance(loaded, NotLoaded)
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
        self.assertIsInstance(loaded, NotLoaded)
        assert isinstance(loaded, NotLoaded)
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
        assert isinstance(loaded, NotLoaded)
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
        self.assertIsInstance(loaded, NotLoaded)
        assert isinstance(loaded, NotLoaded)
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
            assert isinstance(loaded, NotLoaded)
            hints.append(loaded.hint)
        self.assertNotEqual(hints[0], hints[1])

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
        self.assertIsInstance(loaded, NotLoaded)
        assert isinstance(loaded, NotLoaded)
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


if __name__ == "__main__":
    unittest.main()
