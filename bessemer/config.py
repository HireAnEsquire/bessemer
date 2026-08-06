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

The seven failure cases here — no adapter found, unparseable TOML, a file that is not UTF-8, a
file that could not be read, whatever the total clause absorbed, a malformed
`container_volumes` entry, and a `container_env_keys` or `container_cap_add` that is not a
list of names — are told apart by their `reason` and `hint` text and by nothing
else. There is no tag and no subtype, so a caller wanting to branch on *which* failure it got
has to match on prose. That is the deliberate bar at F1: no caller branches on the case yet,
doctor and dispatch both render or refuse on the pair as a whole, and a tag invented before
anything reads it would be a schema nothing pins.

Whether the config root and the git root agree is *not* checked here — it needs git, so it
is a resolver (issue 05). This module's answer to "where is the adapter" is deliberately
allowed to be wrong in a way a later check can name.

**Three keys are committed-layer only, and the loader reports the violation rather than
resolving around it.** `container_env_keys`, `container_cap_add` and `container_volumes`
each widen the container's boundary — which secrets cross it, which capabilities the
process holds, which paths are mounted — and ADR 0001's rule for the first applies verbatim
to the other two (F3 decisions 5.2 and 5.3): the point of naming these in a shared,
committed file is that widening the boundary is a reviewable diff, and a gitignored
`config.local.toml` erases exactly that property, silently, on one machine.

Per ADR 0002's resolver discipline the *fact* lives here and the *response* does not:
`committed_only_violations` exposes the keys a local layer set, **doctor renders them as
FAIL** (F3 issue 09) and **dispatch hard-errors on the same value** (issue 10). Neither
reimplements the check. Precedence is untouched — the local value still wins — because a
loader that silently dropped it would leave a user staring at an override that looks
ignored, and would leave the run to proceed on the committed value with nothing refusing
it. The environment and flag layers are a different matter: neither can carry these keys at
all, structurally, for the reasons written at `_env_layer` and `_flag_layer`.

**`container_volumes` entries are validated at load, and a bad one fails the load.** The two
forms are `name:/path` and `/path`; a source beginning with `/` or `.` is a host bind, which
config may not express (F3 decision 5.3). Refusing here rather than in `container.py` makes
the defect doctor-visible instead of surfacing mid-dispatch with a checkout cloned and a
container half-started. It is the sixth failure case below, and the first that is not about
reading a file: the TOML parsed, and the loader refuses what it says.

**The seventh is the same argument, applied to the other two list-valued keys** (added at F3
issue 06, 2026-08-05, from issue 01's review). `container_env_keys = "DATABASE_URL"` — a bare
string where a list belongs — is a plausible mistake, and a string is iterable: a consumer
mapping entries to flags walks it character by character and builds
`--cap-add S --cap-add E …` or forwards an environment variable called `D`. The reason
volumes are checked here does not stop at volumes, so all three are checked for *shape*: a
list of non-empty strings.

**Shape here, grammar in `container.py`.** Whether an entry is a capability name or an
environment variable name is checked where the flags are built, because that module owns the
patterns and a second copy of them here would be two definitions of the privilege boundary's
alphabet. This module answers "is this the right kind of value", which is the part that can
be answered before a dispatch exists.
"""

import os
import re
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

KNOWN_KEYS: Final = frozenset(
    {
        "source",
        "base",
        "specs_dir",
        "image",
        "container_env_keys",
        "container_cap_add",
        "container_volumes",
        "max_review_rounds",
        "pass_timeout",
        "pids_limit",
        "memory",
    }
)
"""Every key this loader reads. Eleven, because eleven is what has been built.

Adopter-facing, so each is here for a named consumer rather than in anticipation of one —
a key the loader accepts but nothing consults is a claim that bessemer is configured by
something it never looks at:

- `source` — the pinned git source `uvx --from` resolves the core from (ADR 0001,
  distribution). Written by F1 issue 07's adapter.
- `base` — the ref a run's pull request targets. Also that issue's.
- `specs_dir` — where specs live, relative to the config root.
- `image` — the adapter image a run's container is started from (F3 issue 06). No default:
  its absence is a doctor FAIL and a dispatch hard error, which is a named refusal rather
  than `docker run` failing later on an image the adopter never built.
- `container_env_keys` — the environment variable names the gitignored `.bessemer/.env` may
  forward into the container (ADR 0001). Bessemer's own credential names are built in and
  live with the container issue, not here.
- `container_cap_add` — capabilities added back after `--cap-drop ALL`, which is
  unconditional and core-owned (F3 decision 5.2).
- `container_volumes` — named or anonymous volumes mounted into the container
  (F3 decision 5.3), each entry `name:/path` or `/path`; see `_volume_refusal`.
- `max_review_rounds`, `pass_timeout` — the agent loop's cost knobs (F3 decision 7.3).
- `pids_limit`, `memory` — the container's machine limits, required by ADR 0001's security
  section and named there as a local-layer example.

The last eight arrived together at F3, in the deliberate two-file edit F3 decision 8.6
describes: this literal, and the hand-written one in `tests/test_config.py` that would
otherwise agree with whatever this file happens to say.

Keys present in a file but absent here are neither read nor rejected; see `unknown_keys`.
"""

COMMITTED_ONLY_KEYS: Final = frozenset(
    {"container_env_keys", "container_cap_add", "container_volumes"}
)
"""The keys only `config.toml` may set. Every other key is legal at any layer.

The three that widen the container boundary, and the reason is one reason stated once
(ADR 0001, for `container_env_keys`; F3 decisions 5.2 and 5.3 cite it for the other two):
widening it must be a reviewable diff, and a gitignored local layer erases that property on
one machine with nothing to show for it.

A local layer that sets one is reported by `committed_only_violations` and refused by its
callers, not by this module. The environment and flag layers cannot reach these keys at
all — see `_env_layer` and `_flag_layer`.
"""

DEFAULTS: Final[Mapping[str, object]] = {
    "specs_dir": f"{ADAPTER_DIR}/specs",
    "container_env_keys": [],
    "container_cap_add": [],
    "container_volumes": [],
    "max_review_rounds": 3,
    "pass_timeout": 900,
    "pids_limit": 2048,
    "memory": "8g",
}
"""The lowest layer. A key with no entry here has no default and reads as `None`.

The three container keys default to an empty list rather than to `None` so that issue 06's
argv builders iterate rather than branch on `None` before every mount and every capability
— and, more to the point, so that "adds nothing" is the shape of the default for every key
that widens a boundary. The limits carry the port's own values (`run.sh:530–531`), and
`max_review_rounds` / `pass_timeout` the loop's (F3 decision 7.3).

`source`, `base` and `image` are deliberately absent, for three different reasons. A source
pin has no defensible default — bessemer cannot guess which ref a team pinned. `base` is
auto-detected from `origin/HEAD` by issue 05's resolver, and auto-detection sits *below*
defaults in the precedence chain, so a default here would not be a fallback: it would make
the resolver dead code on every machine. `image` names a thing the adopter builds, and any
guess would replace doctor's FAIL with a `docker run` failure further from the cause.

The empty lists here are module state, so `Config.get` hands back a copy of any list it
returns. Without that, one caller appending to the list it got would widen the default for
every later load in the process.
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
        """The winning value for `key`, or `None` if no layer sets it.

        A `list` value is copied on the way out. Every layer here is shared — `DEFAULTS` for
        the lifetime of the process, a parsed TOML layer for the lifetime of this `Config` —
        so without the copy a caller that appended to the list it got would be editing the
        configuration every later reader sees. Three of the list-valued keys are the ones
        that widen the container boundary, and a privilege boundary a stray `append` can
        move is not a boundary.
        """
        _require_known(key)
        for _, values in self._layers():
            if key in values:
                value = values[key]
                return list(value) if isinstance(value, list) else value
        return None

    def layer_of(self, key: str) -> str | None:
        """Which layer supplied `key`'s value, or `None` if no layer sets it."""
        _require_known(key)
        for name, values in self._layers():
            if key in values:
                return name
        return None

    def committed_only_violations(self) -> tuple[str, ...]:
        """Committed-only keys that `config.local.toml` sets, sorted.

        The fact, not the response: doctor renders one FAIL line per key (F3 issue 09) and
        dispatch hard-errors on the same tuple (issue 10), and neither reimplements the
        check — ADR 0002's resolver discipline, arriving at config because this one needs no
        subprocess to answer.

        Sorted, because doctor renders a line per key and the order of a TOML file is not
        something a user should be able to move rendered output around with.

        Nothing here inspects the *value*. A local layer that sets `container_volumes` to
        something malformed fails the load outright (`_volume_refusal`) — being set in the
        wrong file is not a licence to be ill-formed in it.
        """
        return tuple(sorted(COMMITTED_ONLY_KEYS & set(self.local)))

    def unknown_keys(self) -> tuple[str, ...]:
        """Keys present in either TOML layer that this loader does not read.

        Reported rather than rejected. The core is pinned by a committed ref (ADR 0001), so
        a config file written for a newer pin is read by an older core routinely; erroring
        on an unrecognised key would have turned `container_env_keys` landing in F3 into a
        hard failure for anyone who had not yet bumped — and `notify`, F4's notification
        verbosity, is the next key with exactly that shape. Exposed rather than ignored
        because F1 issue 07's adapter must contain nothing but keys this loader reads, and
        that is an assertion someone has to be able to make.
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
    normalisation and value coercion both read as natural additions right after the parse,
    which is why the property needs a test rather than a paragraph — and F3's
    `container_volumes` check, the first one actually written, sits in `load` for exactly
    this reason rather than one line below `tomllib.load` where it would have been shorter.
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


VOLUME_NAME: Final = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*\Z")
"""What may appear before the `:` in a `container_volumes` entry: a docker volume name.

Docker's own pattern for a named volume, and the boundary is drawn here rather than at
"does not start with `/` or `.`" because docker reads any source containing a `/` as a
path. `\\Z` rather than `$`, which would match before a trailing newline and let
`"cache\\n:/x"` through.
"""

VOLUME_HINT: Final = (
    'set container_volumes to a list of "name:/path" or "/path" entries, where a name is '
    "letters, digits, dots, dashes and underscores; a source that names a host path is "
    "refused, because no host path is mounted through config"
)
"""The fix for every `container_volumes` refusal, which is why they share one hint.

The rule is one rule — two forms, no host sources — so a per-shape hint would be five
spellings of the same sentence. The *reason* varies and is carried by the reason text,
which quotes the entry that caused it.
"""


NAME_LIST_KEYS: Final = ("container_env_keys", "container_cap_add")
"""The two list-valued keys whose entries are bare names, checked for shape at load.

`container_volumes` is the third list-valued key and is absent deliberately: its entries have
a grammar of their own (`_volume_refusal`), and it is checked one step earlier with its own
hint because the fix for a malformed volume is not the fix for a malformed name list.

Written out rather than derived from `COMMITTED_ONLY_KEYS`, which happens to hold the same
three keys today for a different reason — being committed-layer-only is a rule about *where*
a key may be set, and being a list of names is a rule about *what* it may say. Deriving one
from the other would make the next any-layer list key inherit a check nobody chose.
"""

NAME_LIST_HINT: Final = (
    "set container_env_keys and container_cap_add to lists of strings — "
    '["DATABASE_URL"], not "DATABASE_URL"; a bare string is read one character at a time'
)
"""The fix for either name list being the wrong shape, which is why they share one hint.

One sentence for both keys, so the two produce one failure case rather than two that read
alike. Which key it was is in the reason, where the value that caused it is quoted too.
"""


def _name_list_refusal(value: object) -> str | None:
    """Why `value` is not a list of names, or `None` if it is. Shape only; see the module
    docstring for why the grammar of a name is `container.py`'s question and not this one's.
    """
    if not isinstance(value, list):
        return f"is not a list of strings: {value!r}"
    for entry in value:
        if not isinstance(entry, str):
            return f"entry {entry!r} is not a string"
        if not entry:
            return f"entry {entry!r} is empty"
    return None


def _climbs_out(mount_point: str) -> bool:
    """Whether a mount point walks upwards out of the directory it names.

    `/workspace/../../etc` is an absolute path with one `:` and a legal volume name in front
    of it, so every other rule below accepts it — and issue 06's container module derives a
    host directory to pre-create from each mount point under the checkout, which is where an
    entry like that would have the loader's blessing to create `/etc`. Refused as text rather
    than resolved: resolving asks the filesystem a question about a path that means something
    inside a container that does not exist yet.
    """
    return ".." in mount_point.split("/")


def _volume_refusal(volumes: object) -> str | None:
    """Why `volumes` is not a legal `container_volumes` value, or `None` if it is.

    The two accepted forms are `name:/path` (a named volume) and `/path` (anonymous). A
    source beginning with `/` or `.` is a host bind, refused because config may not express
    one (F3 decision 5.3): the point of naming volumes in a reviewable committed file is
    lost if `../..:/` is among the things that can be named.

    Everything else is refused too, rather than passed to docker to sort out — `cache:/a:/b`
    would smuggle a mount option (`:ro`, `:z`) past a check that only looked for host
    sources, and `cache:relative` is a `docker run` failure mid-dispatch instead of a doctor
    line before one. The accepted set is exactly the two forms the decision names.

    That is why the source is matched against `VOLUME_NAME` rather than merely checked for a
    leading `/` or `.`. A leading-character test reads as sufficient and is not: docker
    treats *any* source containing a `/` as a path, so `sub/dir:/y` and `~/secrets:/y` would
    both pass a check written to keep host paths out of config and arrive at issue 06's
    mount table as bind-shaped entries this module had declared legal.

    Returns prose rather than raising, and prose rather than a tag, for the reason the whole
    module does: the caller pairs it with `VOLUME_HINT` into the same `Unresolved` every
    other failure here produces. Nothing branches on which refusal it was.
    """
    if not isinstance(volumes, list):
        # A string would otherwise be walked character by character — `container_volumes =
        # "cache:/cache"` is a plausible mistake, and refusing the letter `c` for not being
        # an absolute path is not a message anyone can act on.
        return f"is not a list of strings: {volumes!r}"

    for entry in volumes:
        if not isinstance(entry, str):
            return f"entry {entry!r} is not a string"
        if not entry:
            return f"entry {entry!r} is empty"
        if ":" not in entry:
            if not entry.startswith("/"):
                return (
                    f"entry {entry!r} is neither a named volume (name:/path) nor an "
                    f"absolute path (/path)"
                )
            if _climbs_out(entry):
                return f"entry {entry!r} has a mount point containing '..'"
            continue
        source, _, mount_point = entry.partition(":")
        if not source:
            return f"entry {entry!r} names no volume before its ':'"
        if source.startswith("/") or source.startswith("."):
            # Its own message, ahead of the general one below, because it is the mistake the
            # rule exists for: a user who wrote `../x:/y` meant to mount a host directory
            # and needs to be told config cannot do that, not that they mistyped a name.
            return (
                f"entry {entry!r} names the host path {source!r} as its source; a source "
                f"beginning with / or . is refused"
            )
        if not VOLUME_NAME.match(source):
            return f"entry {entry!r} has a source that is not a volume name: {source!r}"
        if not mount_point.startswith("/"):
            return f"entry {entry!r} has a mount point that is not an absolute path"
        if ":" in mount_point:
            return f"entry {entry!r} has more than one ':'"
        if _climbs_out(mount_point):
            return f"entry {entry!r} has a mount point containing '..'"
    return None


def _env_layer(env: Mapping[str, str]) -> Mapping[str, object]:
    """The `BESSEMER_*` variables that name a key this loader reads and a layer may set.

    Built from `KNOWN_KEYS` rather than by scanning the environment for the prefix. That is
    what makes "no `BESSEMER_ROOT`" structural instead of a rule someone has to remember:
    `root` is not a key, so no environment variable can reach discovery.

    `COMMITTED_ONLY_KEYS` is subtracted for the same kind of reason, one step further on.
    Those keys widen the container boundary and the rule is that widening it is a reviewable
    diff; an environment variable is *less* reviewable than the `config.local.toml` the rule
    already forbids, so `BESSEMER_CONTAINER_ENV_KEYS=AWS_SECRET_ACCESS_KEY` cannot be left
    as something doctor reports after the fact. Enforced by construction rather than by a
    check, so that whoever adds the next committed-only key inherits it without reading this.

    The consequence, named rather than hidden: such a variable is dropped here silently,
    exactly as `BESSEMER_ROOT` is — this module has no violation fact to expose for a value no
    layer ever carried, and inventing one would be a second channel through the loader for
    something the loader deliberately cannot see.

    **Silent here does not mean silent to the user** (issue 09, 2026-08-06). Dropped and
    reported are different things to the person who wrote the variable: their intent was to
    widen the container's boundary from the least reviewable place there is, and they are
    entitled to know it did nothing. Doctor's three committed-only checks therefore read the
    environment directly for these names and FAIL on one that is set. That is doctor's own
    observation rather than a fact from here, and `bessemer.doctor._committed_only` says so.

    A variable that is set but empty counts as set. The alternative — treating `""` as
    absent — invents a rule the shell does not have, and would silently ignore a value the
    user can see in their own environment.
    """
    layer: dict[str, object] = {}
    for key in sorted(KNOWN_KEYS - COMMITTED_ONLY_KEYS):
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
    two must not be reported the same way. A committed-only key raises for the same reason
    — no such flag exists (F3's flag set is `<spec>`, `--branch`, `--base`), so one arriving
    is this package handing a boundary-widening value to itself from the least reviewable
    place there is.
    """
    for key in flags:
        _require_known(key)
        if key in COMMITTED_ONLY_KEYS:
            raise ValueError(
                f"{key!r} is committed-layer only and has no flag; "
                f"this loader's committed-only keys are {sorted(COMMITTED_ONLY_KEYS)}"
            )
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
        path = adapter_dir / filename
        # The first unreadable layer ends the load. Reading the second and reporting both
        # would mean returning two reasons in a type that carries one, and the fix for the
        # first is a prerequisite for trusting anything said about the second.
        match _read_layer(path):
            case Resolved(value=values):
                # Both layers, including the one that may not set this key at all: being
                # written in the wrong file is not a licence to be ill-formed in it, and
                # `config.local.toml` is the layer nobody reviews.
                if "container_volumes" in values:
                    refusal = _volume_refusal(values["container_volumes"])
                    if refusal is not None:
                        return Unresolved(
                            reason=f"{path}: container_volumes {refusal}",
                            hint=VOLUME_HINT,
                        )
                for key in NAME_LIST_KEYS:
                    if key in values:
                        refusal = _name_list_refusal(values[key])
                        if refusal is not None:
                            return Unresolved(
                                reason=f"{path}: {key} {refusal}",
                                hint=NAME_LIST_HINT,
                            )
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
