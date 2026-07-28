"""Finding the adapter directory and reading its two TOML layers.

**This module starts no subprocess, directly or indirectly, and calls nothing in
`bessemer.proc`.** Config load is pure: filesystem and environment only. Anything that
needs `git` or `docker` is a resolver, deliberately separated so that `bessemer doctor`
still works when those are broken — which is doctor's entire reason to exist (ADR 0002,
"config load is pure"). That claim is proven by `tests/test_config_purity.py` rather than
asserted here, because a docstring is not a test.

**Discovery walks up from cwd looking for `.bessemer/`, and asks git nothing.** That keeps
load pure, lets bessemer report "config found here" and "not a git work tree" as two facts
rather than one useless error, and matches what users already expect from `.git`,
`node_modules` and `.venv`. There is no override flag and no `BESSEMER_ROOT`: discovery is
not a config value, and an escape hatch nobody has needed yet is how discovery accidentally
becomes configuration. Because the environment layer below is built only from `KNOWN_KEYS`,
that is structural — `BESSEMER_ROOT` cannot be read even by accident.

**Nothing here raises on a user's mistake.** A missing adapter and a malformed TOML file
both come back as `Unresolved`, a reason and a hint — the shared value-or-reason type from
`bessemer.outcome`, the same one issue 05's resolvers return. Doctor renders it as a check
line and keeps going; dispatch hard-errors on the identical value. A `tomllib` traceback
would be the same information delivered as a crash.

That promise is kept by a **total** parse boundary rather than by an enumeration of what
`tomllib` throws — see `_read_layer`, where the specific clauses exist only for the cases
whose *fix* differs and a final `except Exception` absorbs the rest. An enumeration was
tried three times and escaped three times.

The five failure cases here — no adapter found, unparseable TOML, a file that is not UTF-8,
a file that could not be read, and whatever the total clause absorbed — are told apart by
their `reason` and `hint` text and by nothing else. There is no tag and no subtype, so a
caller wanting to branch on *which* failure it got has to match on prose. That is the
deliberate bar at F1: no caller branches on the case yet, doctor and dispatch both render or
refuse on the pair as a whole, and a tag invented before anything reads it would be a schema
nothing pins.

Whether the config root and the git root agree is *not* checked here — it needs git, so it
is a resolver (issue 05). This module's answer to "where is the adapter" is deliberately
allowed to be wrong in a way a later check can name.
"""

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from bessemer.outcome import Resolved, Unresolved

ADAPTER_DIR: Final = ".bessemer"
"""The directory whose presence marks a repository as having an adapter."""

COMMITTED_FILE: Final = "config.toml"
LOCAL_FILE: Final = "config.local.toml"

ENV_PREFIX: Final = "BESSEMER_"

KNOWN_KEYS: Final = frozenset({"source", "base", "specs_dir"})
"""Every key this loader reads. Three, because three is what has been built.

Adopter-facing, so each is here for a named consumer rather than in anticipation of one —
a key the loader accepts but nothing consults is a claim that bessemer is configured by
something it never looks at:

- `source` — the pinned git source `uvx --from` resolves the core from (ADR 0001,
  distribution). Written by issue 07's adapter.
- `base` — the ref a run's pull request targets. Also issue 07's.
- `specs_dir` — where specs live, relative to the config root.

Keys present in a file but absent here are neither read nor rejected; see `unknown_keys`.
"""

DEFAULTS: Final[Mapping[str, object]] = {"specs_dir": f"{ADAPTER_DIR}/specs"}
"""The lowest layer. A key with no entry here has no default and reads as `None`.

`source` and `base` are deliberately absent, for different reasons. A source pin has no
defensible default — bessemer cannot guess which ref a team pinned. `base` is auto-detected
from `origin/HEAD` by issue 05's resolver, and auto-detection sits *below* defaults in the
precedence chain, so a default here would not be a fallback: it would make the resolver
dead code on every machine.
"""

FLAG: Final = "flag"
ENV: Final = "env"
LOCAL: Final = "local"
COMMITTED: Final = "committed"
DEFAULT: Final = "default"

PRECEDENCE: Final = (FLAG, ENV, LOCAL, COMMITTED, DEFAULT)
"""Highest-winning first. ADR 0001: CLI flags > `BESSEMER_*` > local > committed > defaults.

Auto-detected values sit below `DEFAULT` and are issue 05's business, not this module's.
That last part is **issue 04's refinement, not ADR 0001's**: the ADR writes the bottom rung
as one thing, "auto-detect / defaults", which leaves it open whether a default shadows an
auto-detected value. Issue 04 settles it — defaults sit above — which is why `base` carries
no default here.
"""


def _require_known(key: str) -> None:
    """Refuse a key this loader does not read.

    An exception rather than a `None`, and unlike the user-facing failures above this one
    is meant to be loud: a typo'd key name in bessemer's own code would otherwise read as
    "configured to nothing" forever, which is the same defect shape ADR 0002 refuses for
    `ctx.ok()` in doctor.
    """
    if key not in KNOWN_KEYS:
        raise ValueError(f"{key!r} is not a config key; this loader reads {sorted(KNOWN_KEYS)}")


@dataclass(frozen=True)
class Config:
    """A loaded adapter: where it is, and what each layer said.

    The layers are kept apart rather than merged at load time, so `layer_of` can answer
    "why is this value what it is" — the question a user asks after an env var they forgot
    about beat the file they just edited.
    """

    adapter_dir: Path
    """The `.bessemer/` directory itself."""

    committed: Mapping[str, object]
    local: Mapping[str, object]
    env: Mapping[str, object]
    flags: Mapping[str, object]

    @property
    def root(self) -> Path:
        """The directory containing `.bessemer/` — the config root.

        Whether this is also the git root is a separate question with its own answer; see
        the module docstring.
        """
        return self.adapter_dir.parent

    def _layers(self) -> tuple[tuple[str, Mapping[str, object]], ...]:
        """Every layer paired with its name, in `PRECEDENCE` order."""
        by_name: Mapping[str, Mapping[str, object]] = {
            FLAG: self.flags,
            ENV: self.env,
            LOCAL: self.local,
            COMMITTED: self.committed,
            DEFAULT: DEFAULTS,
        }
        return tuple((name, by_name[name]) for name in PRECEDENCE)

    def get(self, key: str) -> object | None:
        """The winning value for `key`, or `None` if no layer sets it."""
        _require_known(key)
        for _, values in self._layers():
            if key in values:
                return values[key]
        return None

    def layer_of(self, key: str) -> str | None:
        """Which layer supplied `key`'s value, or `None` if no layer sets it."""
        _require_known(key)
        for name, values in self._layers():
            if key in values:
                return name
        return None

    def unknown_keys(self) -> tuple[str, ...]:
        """Keys present in either TOML layer that this loader does not read.

        Reported rather than rejected. The core is pinned by a committed ref (ADR 0001), so
        a config file written for a newer pin is read by an older core routinely; erroring
        on an unrecognised key would turn `container_env_keys` landing in F3 into a hard
        failure for anyone who had not yet bumped. Exposed rather than ignored because
        issue 07's adapter must contain nothing but keys this loader reads, and that is an
        assertion someone has to be able to make.
        """
        return tuple(sorted((set(self.committed) | set(self.local)) - KNOWN_KEYS))


def find_adapter_dir(start: Path) -> Path | None:
    """The nearest `.bessemer/` at or above `start`, or `None` at the filesystem root.

    `start` is resolved first, so a path containing `..` or a symlinked working directory
    walks the tree it actually sits in rather than the one it is spelled as.

    Known limit, named rather than papered over: `Path.is_dir` reports `False` for a
    directory this process cannot stat, so an unreadable ancestor is walked past rather than
    reported. A `.bessemer` that exists as a *file* is skipped the same way — it is not an
    adapter, and stopping there would replace a working walk-up with a confusing error.
    """
    origin = start.resolve()
    for directory in (origin, *origin.parents):
        candidate = directory / ADAPTER_DIR
        if candidate.is_dir():
            return candidate
    return None


def _read_layer(path: Path) -> Resolved[Mapping[str, object]] | Unresolved:
    """Parse one TOML layer. A file that is not there is an empty layer, not a failure.

    **The parse boundary is total.** The specific clauses below exist only where the fix
    genuinely differs — "re-save as UTF-8" is not "fix the syntax error" — and everything
    else is absorbed by a final `except Exception` that names the exception type in its
    reason. That is not defensiveness, it is the only shape that makes this module's promise
    true: three separate rounds of review each found one more type escaping an enumerated
    list — `TOMLDecodeError`, then `UnicodeDecodeError`, then `RecursionError` from ~600
    nested brackets in a 1 KB file — which is evidence the enumeration does not converge.
    `MemoryError` is the next candidate and nobody has looked for the one after it.

    Deliberately narrower than a blanket `except Exception` around the module: it is scoped
    to two calls into a library whose failure modes bessemer does not own and cannot
    enumerate.

    **Nothing of bessemer's may run inside the block.** That is an obligation on whoever
    edits this function next, not a description of how it happens to read today. The block
    holds `path.open` and `tomllib.load` and nothing else: the parsed layer is *bound*
    inside and the `Resolved` is *constructed* outside, because a total clause wrapped
    around our own code reports our bug as the user's broken file — a defect in
    `bessemer.outcome` came back as "config.toml could not be parsed", sending the reader to
    inspect a file that was fine. That is worse than the traceback it replaced, and it is
    the exact inversion the totality argument was supposed to buy safety from.

    `tests/test_config.py` pins it: one test makes the constructor raise and asserts the
    exception *propagates*. It fails the moment anything of ours moves back inside. Key
    normalisation, value coercion and F3's `container_env_keys` check all read as natural
    additions right after the parse, which is why the property needs a test rather than a
    paragraph.
    """
    try:
        with path.open("rb") as handle:
            # Annotated for the reader, not for the type checker: it states the layer type
            # where the layer is built, rather than leaving it to be derived from the
            # return annotation. Nothing depends on it — `tomllib.load` is typed as
            # returning `dict[str, Any]`, but mypy solves `T` from this function's declared
            # return type, so the unannotated spelling is `Resolved[Mapping[str, object]]`
            # too and leaks no `Any`.
            parsed: Mapping[str, object] = tomllib.load(handle)
    except FileNotFoundError:
        # Both layers are optional, independently: a repo with only a committed file is the
        # common case, and a dev with only a local one is a legitimate second.
        #
        # Known limit, named rather than papered over: a **dangling symlink** lands here
        # too, and is therefore treated as an absent layer rather than reported. That is the
        # intended reading — a symlink to nothing and no file at all both mean "no config
        # here" — but it is a decision, not an accident, and it does mean a broken link in
        # an adapter is silently ignored.
        return Resolved({})
    except UnicodeDecodeError as error:
        # TOML mandates UTF-8, so this *is* malformed TOML — but `tomllib.load` decodes
        # before it parses, so it surfaces as a `UnicodeDecodeError` and never reaches the
        # clause below. It is a `ValueError`, not an `OSError`, so it escapes that one too:
        # without this clause the module's promise that nothing here raises on a user's
        # mistake is false, for a file that a non-UTF-8 editor produces by default.
        #
        # Its own hint, because its own fix: re-saving the file in a different encoding, not
        # correcting a line.
        return Unresolved(
            reason=f"{path} is not valid TOML: it is not UTF-8 text: {error}",
            hint=f"re-save {path.name} as UTF-8; TOML files must be UTF-8",
        )
    except tomllib.TOMLDecodeError as error:
        return Unresolved(
            reason=f"{path} is not valid TOML: {error}",
            hint=f"fix the syntax error at the line named above in {path.name}",
        )
    except OSError as error:
        # A directory named config.toml, a permission denial, a symlink loop — the cases
        # where the bytes could not be obtained at all. Distinct from both parse failures
        # above because the fix is distinct.
        return Unresolved(
            reason=f"{path} could not be read: {error}",
            hint=f"check that {path.name} is a readable file",
        )
    except Exception as error:
        # The total clause. `RecursionError` from deeply nested arrays is the case that
        # forced it — a `RuntimeError`, so it escapes every clause above — but the point is
        # not to have finally caught that one. It is that this module can now promise
        # "nothing here raises on a user's mistake" without that promise depending on
        # bessemer having correctly enumerated `tomllib`'s failure modes.
        #
        # The exception type is named in the reason because it is the only thing that
        # distinguishes one absorbed failure from another: this clause has no idea what it
        # caught, and a reason that did not say would leave a user with less than the
        # traceback it replaced.
        #
        # `BaseException` is deliberately *not* caught. `KeyboardInterrupt` and `SystemExit`
        # are not a user's mistake in a config file, and swallowing them here would make
        # Ctrl-C during a load report a malformed adapter.
        return Unresolved(
            reason=f"{path} could not be parsed: {type(error).__name__}: {error}",
            hint=(
                f"inspect {path.name} for something a TOML parser cannot handle, such as "
                f"deeply nested tables or arrays, and report this if the file looks ordinary"
            ),
        )

    # Outside the block, deliberately: see the docstring. `parsed` is bound only on the path
    # that fell through every handler, and mypy accepts the read because each handler above
    # returns, so there is no route here that skipped the assignment.
    return Resolved(parsed)


def _env_layer(env: Mapping[str, str]) -> Mapping[str, object]:
    """The `BESSEMER_*` variables that name a key this loader reads.

    Built from `KNOWN_KEYS` rather than by scanning the environment for the prefix. That is
    what makes "no `BESSEMER_ROOT`" structural instead of a rule someone has to remember:
    `root` is not a key, so no environment variable can reach discovery.

    A variable that is set but empty counts as set. The alternative — treating `""` as
    absent — invents a rule the shell does not have, and would silently ignore a value the
    user can see in their own environment.
    """
    layer: dict[str, object] = {}
    for key in sorted(KNOWN_KEYS):
        name = ENV_PREFIX + key.upper()
        if name in env:
            layer[key] = env[name]
    return layer


def _flag_layer(flags: Mapping[str, object]) -> Mapping[str, object]:
    """The CLI-flag layer, with unsupplied flags dropped.

    `None` means "the flag was not given", because that is what `argparse` puts in a
    `Namespace` for an absent option and this layer is built from one. No config key has a
    meaningful `None` value, so nothing is lost by the convention.

    An unrecognised key raises: flags are bessemer's own code calling bessemer, so a name
    that is not a config key is a bug in this package rather than a user's mistake, and the
    two must not be reported the same way.
    """
    for key in flags:
        _require_known(key)
    return {key: value for key, value in flags.items() if value is not None}


def load(
    *,
    start: Path | None = None,
    env: Mapping[str, str] | None = None,
    flags: Mapping[str, object] | None = None,
) -> Resolved[Config] | Unresolved:
    """Find the adapter and read its layers. Never raises on a user's mistake.

    `start` defaults to the current working directory, `env` to this process's environment.
    Both are parameters so tests can drive the walk-up and the environment layer without
    touching either — a test that has to `chdir` is a test that cannot run beside another.

    The `Config` arrives wrapped rather than bare so that the success case cannot be told
    from the failure case by a truthiness test or an attribute probe: callers `match`, and
    the type checker narrows.
    """
    start = Path.cwd() if start is None else start
    env = os.environ if env is None else env

    adapter_dir = find_adapter_dir(start)
    if adapter_dir is None:
        return Unresolved(
            reason=f"no {ADAPTER_DIR}/ directory found in {start} or any parent directory",
            hint=(
                f"run bessemer from inside a repository that has a {ADAPTER_DIR}/ directory, "
                f"or create one at that repository's root"
            ),
        )

    layers: dict[str, Mapping[str, object]] = {}
    for name, filename in ((COMMITTED, COMMITTED_FILE), (LOCAL, LOCAL_FILE)):
        # The first unreadable layer ends the load. Reading the second and reporting both
        # would mean returning two reasons in a type that carries one, and the fix for the
        # first is a prerequisite for trusting anything said about the second.
        match _read_layer(adapter_dir / filename):
            case Resolved(value=values):
                layers[name] = values
            case Unresolved() as unresolved:
                return unresolved

    return Resolved(
        Config(
            adapter_dir=adapter_dir,
            committed=layers[COMMITTED],
            local=layers[LOCAL],
            env=_env_layer(env),
            flags=_flag_layer({} if flags is None else flags),
        )
    )
