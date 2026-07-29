"""The values config deliberately refuses to compute, because computing them needs `git`.

Two resolvers, both returning the shared value-or-reason union from `bessemer.outcome`:
`resolve_base` answers "which ref does a run's pull request target", and
`resolve_root_agreement` answers "is the directory holding the adapter the same directory
git calls the top of the work tree". Neither raises; see "Nothing here raises" below.

**These are shared predicates, not doctor-only checks.** Doctor renders the outcome as a
check line; dispatch hard-errors on the identical call (ADR 0002, "resolvers are shared
predicates"). One definition is what stops the two from drifting apart, the same discipline
the port source applies with `have_claude_credential` and `image_staleness`.

**Git runs from the current directory, not from `cfg.root`.** That is deliberate and it is
what makes `resolve_root_agreement` able to say anything at all: the config root can be
wrong — a stray `~/.bessemer` means the walk-up escaped the repository (ADR 0002, "config
root and git root disagreeing is a hard error in both directions") — so asking git from
where the user actually stands keeps "where the adapter is" and "where the repository is"
two independent facts. `start` exists so tests can drive it without `chdir`, exactly as
`bessemer.config.load` takes one.

**Every git invocation goes through `bessemer.proc.run` with an explicit timeout, from one
call site.** `_git` is that site; nothing else here spawns. A second call site is how one
call ends up without a timeout, and a resolver that hangs is worse than one that fails —
doctor exists to be run when things are broken.

**The ambient environment is passed on, minus the variables that redirect git.** An exported
`GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR` or `GIT_CEILING_DIRECTORIES` makes `rev-parse`
answer about a different repository than the one on disk — measured, four of them — which
turns `resolve_root_agreement` from a security predicate into a check a shell variable can
silently point somewhere else. A subtraction rather than an allowlist, and a narrow
deviation from ADR 0002's inheritance decision, argued at `REDIRECTING_VARIABLES`.

**Nothing here raises, and the enumeration is against `proc` rather than against fixtures.**
`bessemer.proc.run` raises in exactly two cases, both documented there and neither a
`Result`: a program that could not be executed at all raises `OSError`, and one killed for
exceeding its timeout raises `subprocess.TimeoutExpired`. So **git not installed is an
exception, not a returncode** — and it is close to the most important thing doctor ever has
to say, so letting it escape would turn a check line into a traceback in the one situation
doctor exists for. Both are absorbed in `_git`, each with its own reason and hint.
`tests/test_resolve.py` provokes both out of the real `proc.run` and asserts that what it
actually raises is what this module catches; a third case would have to be added by whoever
changes `proc`, and this module is where it would land.

The one other route to an exception is reading the current directory, which is not git's
and not `proc`'s: `Path.cwd()` raises `OSError` when the working directory has been
deleted. It has its own reason too — see `_where`.

**Deliberately not a total `except Exception` around the git calls.** `bessemer.config`
needs one because `tomllib`'s failure modes are undocumented and three rounds of review each
found one more escaping an enumerated list. Here the boundary is bessemer's own wrapper, its
two raising cases are documented, and the tests hold that enumeration against the wrapper
itself — so an enumeration is checkable here in the way it was not there.

**git's `stderr` is credential-bearing, and these reasons get printed.** `git` echoes remote
URLs on failure and a remote URL can carry a token
(`https://x-access-token:ghp_…@github.com/…`); doctor renders a reason to a terminal and
from F3 dispatch renders one into a pull request body. The decision, per reason:

- Reasons for a condition bessemer has **diagnosed** — no `origin` remote, `origin/HEAD`
  unset, the two root disagreements, a configured `base` that is not usable — carry none of
  git's text. Bessemer knows more about the situation than git's message does, and the fix is
  bessemer's to state.
- The reasons for a git failure bessemer **could not** diagnose carry git's first `stderr`
  line, redacted and truncated. Dropping it would leave "git exited 128" and nothing else,
  which is not a reason a user can act on. `bessemer.redact` is what makes that safe, and its
  limit is named there. It lived here as a private helper until issue 06 needed the same
  redaction for a crashing check's exception text and promoted it rather than copying it.

`git rev-parse` failing is the sharp case: "not a git repository" and "bad config line 1 in
file .git/config" are both `fatal:` at exit 128, so bessemer cannot tell them apart by
returncode and its own wording alone would be a confident misdiagnosis. Measured, not
assumed — see `tests/test_resolve.py::WorkTreeTest`.

**`cfg.get("base")` is typed `object | None`, and this module owns what that means.** Issue
04 does no coercion, deliberately, so `base = 5` arrives as an `int`, `base = ["main"]` as a
`list`, and `base = ""` as a set-but-empty `str`. All three are unusable, and all three are
told apart from "no `base` at all" rather than crashing or silently falling through to
auto-detection: "your `base` is not a branch name" and "you have no `base`" are different
things to be told. See `_usable_as_branch_name` for how little is checked and why.

**Two paths name one directory by identity, not by spelling.** `_relate` compares roots with
`os.path.samestat` and derives ancestry the same way, because `Path.resolve()` follows symlinks
but does not canonicalise case: on macOS and Windows `…/repo` and `…/Repo` are one directory
that compares unequal under `==` and under `is_relative_to`. Stat-ing can fail where comparing
strings cannot, so that has a reason of its own and it fails closed — `_root_unreadable`.

**The failure vocabulary is fourteen cases**, and `tests/test_resolve.py::FailureCaseTest`
restates every one by hand, anchored to this module's own AST. Each is constructed at exactly
one site — the `_reason`-shaped helpers below — so counting `Unresolved(...)` here is a
count of cases, and growing or shrinking the vocabulary costs a deliberate edit in the test.
The cases are told apart by their `reason` and `hint` text and by nothing else, the same bar
`bessemer.config` sets: no tag, no subtype, because no caller branches on the case yet.
"""

import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from bessemer import proc, redact
from bessemer.config import ADAPTER_DIR, COMMITTED_FILE, Config
from bessemer.outcome import Resolved, Unresolved

ORIGIN: Final = "origin"
ORIGIN_HEAD: Final = f"refs/remotes/{ORIGIN}/HEAD"

SET_HEAD_COMMAND: Final = f"git remote set-head {ORIGIN} --auto"
"""The fix for the common auto-detect failure, spelled once because it is the hint."""

CONFIG_FILE: Final = f"{ADAPTER_DIR}/{COMMITTED_FILE}"
"""How a hint spells the file a user would edit to set `base` by hand."""

TIMEOUT_SECONDS: Final = 15.0
"""Every git call gets this, and no call site may omit it.

Generous for a `rev-parse`, and a backstop rather than a deadline: git reads the filesystem
and this is not a fetch. It is here because a wedged git — an unresponsive network
filesystem holding `.git/`, a stale `index.lock` behind a `gc` — must fail doctor rather
than hang it.
"""

REDIRECTING_VARIABLES: Final = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_CEILING_DIRECTORIES",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_NAMESPACE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    }
)
"""Environment variables that tell git which repository to answer about. Removed, always.

**`resolve_root_agreement` is a security predicate, so this is a bypass rather than a
preference.** It is what makes ADR 0001's "the host pushes from the main repository" name
something unambiguous, and it can only do that if git is answering about the repository on
disk. Measured, all four in one fixture:

- `GIT_DIR` is the worst of them. In a directory that is *not a repository at all*,
  `rev-parse --is-inside-work-tree` prints `true`, `--show-toplevel` prints that very
  directory, and `symbolic-ref` reads the *other* repository's refs — so root agreement
  "agrees" about a pair of directories while the base comes from somewhere else entirely.
- `GIT_WORK_TREE` redirects `--show-toplevel` to the directory it names.
- `GIT_COMMON_DIR` substitutes the shared part of the repository, config and refs included:
  a freshly-initialised temporary repository with no remotes reports an `origin`.
- `GIT_CEILING_DIRECTORIES` stops the discovery walk, turning a real work tree into "not a
  git repository" — a false negative in the one command that exists to report the truth.

The remaining five are removed on git's documentation rather than on a measurement: they
name an index, an object store, a ref namespace, or a discovery rule, and this fixture could
not make any of them change a resolver's answer. Stated so the list does not read as nine
demonstrated bypasses when it is four — `tests/test_resolve.py::AmbientEnvironmentTest`
asserts immunity for all nine and asserts the *redirection* only for the four it can show.

**This is a deviation from ADR 0002's "host-side children inherit the ambient environment",
and a deliberately narrow one.** That decision exists for the push path, which genuinely
needs `SSH_AUTH_SOCK` and credential helpers; a strict allowlist was rejected there because
it breaks `git push` and `gh` in ways that vary per adopter machine. Nothing here pushes or
contacts a remote — these are read-only local queries — so nothing here needs a credential,
and removing nine names costs them nothing. The environment is still **inherited**: this is
a subtraction, not an allowlist, so `HOME` (and with it `safe.directory`), `PATH`, the
locale, and every `GIT_CONFIG_*` file variable are all still passed. An allowlist would be
the thing that breaks on the next machine.

**Configuration is deliberately still inherited, and that is the named limit.** A user's
`~/.gitconfig` is kept because git needs it — `safe.directory` lives there — and
`GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM` and the `GIT_CONFIG_COUNT`/`KEY_n`/`VALUE_n`
injection triple are the same class of thing: a config file the user owns. Measured:
injecting `core.worktree` through that triple does *not* redirect `--show-toplevel`, with or
without `GIT_DIR`, because git honours `core.worktree` only for a repository named by
`GIT_DIR` — which this set removes. The line drawn here is **location and discovery, not
configuration**.
"""


def _git_detail(result: proc.Result) -> str:
    """git's own first word on a failure, safe to print: first line, redacted, truncated.

    A one-line spelling of `bessemer.redact.detail` over git's `stderr` specifically, kept
    so the call sites below read as being about git rather than about a text helper. The
    redaction itself is shared with doctor's crashing-check renderer — see `bessemer.redact`
    for why there is exactly one of it.
    """
    return redact.detail(result.stderr)


def _saying(detail: str) -> str:
    """`detail` as a clause to append to a reason, or nothing at all if there is none."""
    return f": {detail}" if detail else ""


def _quote(value: object) -> str:
    """A configured value as it should appear in a reason: short, and unambiguous.

    `repr`, so `""` and `"  "` are visible rather than rendering as nothing, and truncated
    for the same reason `bessemer.redact.DETAIL_LIMIT` exists. This echoes a value out of the
    adapter's own config file — not out of git — so it is the user's own text coming back to
    them, and it is not redacted for that reason: nothing here is another program's output.
    """
    return repr(value)[: redact.DETAIL_LIMIT]


def _base_not_a_string(cfg: Config, value: object) -> Unresolved:
    """A configured `base` that is not text at all: `base = 5`, `base = ["main"]`."""
    return Unresolved(
        reason=(
            f"the {cfg.layer_of('base')} layer sets base to "
            f"{type(value).__name__} {_quote(value)}, and a base must be a branch name"
        ),
        hint=(
            f'quote it as a branch name — base = "main" in {CONFIG_FILE} — or remove the '
            f"key entirely and bessemer reads {ORIGIN}/HEAD instead"
        ),
    )


def _base_not_a_branch_name(cfg: Config, value: str) -> Unresolved:
    """A configured `base` that is text, but not text git can be handed as a ref."""
    return Unresolved(
        reason=(
            f"the {cfg.layer_of('base')} layer sets base to {_quote(value)}, which is not "
            f"usable as a branch name: it is empty, or holds whitespace, or starts with -"
        ),
        hint=(
            f"set base to the branch name a pull request should target, with no spaces and "
            f"no leading dash — or remove the key and let bessemer read {ORIGIN}/HEAD"
        ),
    )


def _not_a_work_tree(where: Path, detail: str) -> Unresolved:
    """git will not treat `where` as a work tree, and git's own text says why.

    Carrying git's message is the decision described in the module docstring: "not a git
    repository" and "bad config line 1 in file .git/config" are both `fatal:` at exit 128,
    so a reason written from the returncode alone would confidently name the wrong problem
    for the second one.
    """
    return Unresolved(
        reason=f"{where} is not inside a git work tree{_saying(detail)}",
        hint=(
            f"run bessemer from inside the repository's work tree; `git -C {where} "
            f"rev-parse --is-inside-work-tree` must print true there"
        ),
    )


def _no_origin(where: Path) -> Unresolved:
    """No remote named `origin`, so there is no `origin/HEAD` to auto-detect from."""
    return Unresolved(
        reason=(
            f"the repository at {where} has no remote named {ORIGIN}, so there is no "
            f"{ORIGIN}/HEAD to read a base from"
        ),
        hint=(
            f"add the remote (`git remote add {ORIGIN} <url>`) and then run "
            f"`{SET_HEAD_COMMAND}`, or set base in {CONFIG_FILE}"
        ),
    )


def _origin_head_unset(where: Path) -> Unresolved:
    """`origin/HEAD` names no branch. The common one, and it has a one-line fix.

    Both provoking paths land here on purpose: `git symbolic-ref` exiting nonzero, and its
    exiting zero with an answer that is not a branch name. The fix is the same command
    either way, and a second case whose hint repeated this one would be a distinction with
    nothing on the other side of it.
    """
    return Unresolved(
        reason=(
            f"{ORIGIN}/HEAD is not set in the repository at {where}, so bessemer cannot "
            f"tell which branch {ORIGIN} considers its default"
        ),
        hint=f"run `{SET_HEAD_COMMAND}`, or set base in {CONFIG_FILE} to skip auto-detection",
    )


def _git_failed(where: Path, result: proc.Result) -> Unresolved:
    """A git command bessemer expected to succeed did not, and bessemer has no diagnosis.

    The second reason carrying git's own text, for the reason the module docstring gives:
    with the text removed this says "git exited 128" and sends its reader nowhere.
    """
    command = " ".join(result.argv)
    return Unresolved(
        reason=(
            f"`{command}` in {where} exited {result.returncode} and bessemer could not use "
            f"its answer{_saying(_git_detail(result))}"
        ),
        hint=f"run `{command}` in {where} yourself to see what git is unhappy about",
    )


def _git_missing(argv: Sequence[str], error: OSError) -> Unresolved:
    """git could not be executed at all — the case doctor exists to report.

    `str(error)` rather than the exception type alone, because "No such file or directory"
    and "Permission denied" have different fixes and both arrive here. The message names a
    program and a path, never an environment.
    """
    return Unresolved(
        reason=f"git could not be run (`{' '.join(argv)}`): {error}",
        hint="install git and make sure it is on PATH; `git --version` must work",
    )


def _git_timed_out(argv: Sequence[str]) -> Unresolved:
    """git started and was killed for exceeding `TIMEOUT_SECONDS`.

    Built from bessemer's own argv and timeout rather than from the exception. Deliberate:
    `subprocess.TimeoutExpired` carries the child's captured `stdout` and `stderr`, which is
    exactly the credential-bearing text the module docstring is about, and formatting the
    exception would pull it into a reason that gets printed.
    """
    return Unresolved(
        reason=(
            f"`{' '.join(argv)}` did not finish within {TIMEOUT_SECONDS:g} seconds and was "
            f"killed, so bessemer has no answer from it"
        ),
        hint=(
            "check for a stuck git process, a stale .git/index.lock, or a .git directory on "
            "an unresponsive filesystem, then run the command again"
        ),
    )


def _root_above(config_root: Path, git_root: Path) -> Unresolved:
    """The adapter sits above the work tree: the walk-up escaped the repository.

    A stray `~/.bessemer` from an `init` run in a home directory is the ordinary cause. A
    submodule is the other one, and it is caught here for free: `git rev-parse
    --show-toplevel` inside a submodule returns the submodule's root, so dispatching
    submodule code against the superproject's adapter fails instead of silently using the
    wrong image.
    """
    return Unresolved(
        reason=(
            f"the adapter at {config_root}/{ADAPTER_DIR} is above the git work tree at "
            f"{git_root}, so the search for it escaped the repository — or {git_root} is a "
            f"submodule of {config_root}"
        ),
        hint=(
            f"delete the stray {config_root}/{ADAPTER_DIR} if it was created by mistake, or "
            f"give {git_root} an adapter of its own"
        ),
    )


def _root_below(config_root: Path, git_root: Path) -> Unresolved:
    """The adapter sits below the work tree: `init` ran in a subdirectory."""
    return Unresolved(
        reason=(
            f"the adapter at {config_root}/{ADAPTER_DIR} is below the git work tree at "
            f"{git_root}, so it was created in a subdirectory of the repository"
        ),
        hint=f"move {config_root}/{ADAPTER_DIR} to {git_root}/{ADAPTER_DIR}",
    )


def _root_unrelated(config_root: Path, git_root: Path) -> Unresolved:
    """Neither root contains the other, by identity. Failing closed rather than choosing one.

    Rare rather than unreachable, and the distinction is worth being exact about because an
    earlier version of this docstring claimed unreachable and was wrong. Both roots are found
    by walking up from one directory, so on an ordinary filesystem one is an ancestor of the
    other — but that argument was about *string prefixes*, and it did not survive the
    comparison moving to `os.path.samestat`: `.resolve()` does not canonicalise case, so two
    spellings of one directory used to land here. That is now the `_relate` walk's job to get
    right, and this case is what remains: a bind mount, a `$GIT_WORK_TREE` pointing elsewhere,
    or a caller passing a `start` from outside the adapter's tree. The one thing this must not
    do is pick a root and let something push from it.
    """
    return Unresolved(
        reason=(
            f"the adapter at {config_root}/{ADAPTER_DIR} and the git work tree at "
            f"{git_root} are in different trees; neither contains the other"
        ),
        hint=(
            f"run bessemer from inside {git_root}, and check that {ADAPTER_DIR} sits at that "
            f"repository's root"
        ),
    )


def _root_unreadable(config_root: Path, git_root: Path, error: OSError) -> Unresolved:
    """Neither root could be *stat*ed, so identity could not be established either way.

    Where the cost of comparing by identity lands, chosen rather than discovered. `==` on two
    paths cannot fail; `os.path.samestat` needs two `stat` calls and a `stat` raises `OSError`,
    and this issue's resolvers may not let one escape. The ordinary cause is a race — a
    checkout removed between the `git rev-parse` and this comparison, which is exactly what
    F3's garbage collection will be doing on a neighbouring directory while a run resolves.

    **Fails closed, deliberately.** "Could not tell" must never read as "they agree": this is
    the predicate that makes "the host pushes from the main repository" unambiguous, so the
    absence of an answer has to be a refusal rather than a default.
    """
    return Unresolved(
        reason=(
            f"bessemer could not compare the adapter at {config_root}/{ADAPTER_DIR} with the "
            f"git work tree at {git_root}: {error}"
        ),
        hint=(
            "check that both directories still exist and are readable — a checkout removed "
            "mid-run produces this — then run the command again"
        ),
    )


def _directory_missing(where: Path, error: OSError) -> Unresolved:
    """git could not be started because the directory it was to run in is unusable.

    Distinct from `_git_missing` because "install git" would be a confident misdiagnosis:
    `subprocess` raises `FileNotFoundError` for an absent `cwd` exactly as it does for an
    absent program, and telling someone whose `--start` path is misspelled to install git
    sends them to the wrong place entirely.

    Measured, since the two are told apart by an attribute rather than by a type: for a bad
    `cwd`, `error.filename` is the *directory* — a `PosixPath`, echoed back from what
    `bessemer.proc.run` was handed — while for a missing program it is the program, a `str`.
    A directory that exists but is not one raises `NotADirectoryError`, also an `OSError`,
    also with the directory in `filename`, so both land here.
    """
    return Unresolved(
        reason=f"git could not be run in {where}: {error}",
        hint=f"check that {where} exists, is a directory, and is readable",
    )


def _cwd_unavailable(error: OSError) -> Unresolved:
    """The current directory could not be read — deleted, or unreadable, under a live shell.

    Not git's failure and not `proc`'s: `Path.cwd()` is the one other call here that raises,
    and a resolver documented as never raising cannot leave it out. A shell whose directory
    was removed under it keeps running, and every command a user types there fails like this.
    """
    return Unresolved(
        reason=f"the current working directory could not be determined: {error}",
        hint="cd to a directory that exists, then run bessemer again",
    )


def _usable_as_branch_name(value: str) -> bool:
    """Whether `value` can be handed to git as a ref, checked as narrowly as possible.

    Emptiness, whitespace, control characters, and a leading `-`. Nothing else, and the
    narrowness is the point: git's ref grammar also forbids `..`, `~`, `^`, `:`, a trailing
    `.lock` and more, and `git check-ref-format` is the only authority on it — but asking it
    is a subprocess, and a configured base must short-circuit auto-detection *without*
    invoking git. A hand-rolled approximation of that grammar would eventually refuse a name
    git accepts, which is worse than passing an odd name through to the command that will
    reject it by name.

    What is checked is the part that is bessemer's own problem rather than git's grammar: a
    value that is empty or blank is a config mistake with no ref in it at all, and a value
    starting with `-` would be read as an option by whichever git command receives it —
    argv is a list so there is no shell hazard, but `--upload-pack=…` in a ref position is
    still an argument doing something nobody asked for.
    """
    if not value or value.startswith("-"):
        return False
    return not any(
        character.isspace() or ord(character) < 0x20 or character == "\x7f"
        for character in value
    )


def _branch_of(reference: str) -> str:
    """The branch name inside whatever `git symbolic-ref` printed for `origin/HEAD`.

    `--short` answers `origin/main`, and the long form answers
    `refs/remotes/origin/main`; both are stripped, because a resolver that depends on which
    spelling a git version chose is a resolver that breaks on an upgrade. A branch name may
    itself contain `/` — `feature/x` — so only the prefix is removed, never the last segment.
    """
    name = reference.strip()
    for prefix in (f"refs/remotes/{ORIGIN}/", f"{ORIGIN}/"):
        name = name.removeprefix(prefix)
    return name


def _where(start: Path | None) -> Path | Unresolved:
    """The directory git is asked from: `start`, or the current one.

    Returns the bare `Path` rather than a `Resolved[Path]`: this is an internal step, and
    wrapping it would make every caller unwrap a success it is not returning.
    """
    if start is not None:
        return start
    try:
        return Path.cwd()
    except OSError as error:
        return _cwd_unavailable(error)


SAME: Final = "same"
CONFIG_ABOVE: Final = "config-above"
CONFIG_BELOW: Final = "config-below"
UNRELATED: Final = "unrelated"

RELATIONS: Final = (SAME, CONFIG_ABOVE, CONFIG_BELOW, UNRELATED)
"""Every way the config root and the git root can sit relative to one another.

Four, and exhaustive by construction: identical, one containing the other either way, or
neither. `tests/test_resolve.py` restates them by hand.
"""


def _relate(config_root: Path, git_root: Path) -> str | Unresolved:
    """How `config_root` sits relative to `git_root`, decided by identity not by spelling.

    **`==` and `is_relative_to` are the wrong tools here, and this is measured rather than
    argued.** `Path.resolve()` follows symlinks but does *not* canonicalise case, so on a
    case-insensitive filesystem — macOS's default, and Windows — `…/repo` and `…/Repo` are one
    directory that compares unequal under every string relation. Root agreement would then
    report two roots in different trees and tell a user to `cd` to the directory they are
    already standing in.

    Not a live false failure today, which is why it is worth writing down precisely:
    `Path.cwd()` *does* canonicalise case, so the default `start` arrives already agreeing
    with git's own spelling, and symlinks agree both ways because `resolve()` handles those. The
    defect needs an explicit `start` — which issue 06's doctor and F3's dispatch may both pass,
    and they inherit this comparison rather than writing their own.

    So: `os.path.samestat` for identity, and ancestry derived the same way — walking a path's
    parents and asking each whether it *is* the other root, rather than asking whether one
    string starts with the other. On a case-sensitive filesystem this is exactly what the
    string comparison did; on a case-insensitive one it is what the string comparison should
    have done.

    Returns `Unresolved` when a `stat` fails: see `_root_unreadable` for where that lands and
    why it fails closed.
    """
    try:
        config_stat = config_root.stat()
        git_stat = git_root.stat()
        if os.path.samestat(config_stat, git_stat):
            return SAME
        # `git_root`'s ancestors, asked whether any *is* the config root: that is "the adapter
        # is above the work tree". Checked before the opposite walk only for readability —
        # both cannot hold at once, since a directory cannot be its own strict ancestor.
        for ancestor in git_root.parents:
            if os.path.samestat(ancestor.stat(), config_stat):
                return CONFIG_ABOVE
        for ancestor in config_root.parents:
            if os.path.samestat(ancestor.stat(), git_stat):
                return CONFIG_BELOW
    except OSError as error:
        return _root_unreadable(config_root, git_root, error)
    return UNRELATED


def _git_env() -> Mapping[str, str]:
    """This process's environment, minus the variables that redirect git's answers.

    Read at call time rather than captured at import, so a long-lived process that changed
    its own environment — a test, or F3's dispatcher — gets the environment it has now
    rather than the one it started with. See `REDIRECTING_VARIABLES` for the policy and for
    why this subtracts rather than allowlists.
    """
    return {
        name: value for name, value in os.environ.items() if name not in REDIRECTING_VARIABLES
    }


def _git(arguments: Sequence[str], *, cwd: Path) -> proc.Result | Unresolved:
    """Run one git command. The only spawn site in this module, and the only timeout.

    Returns a `Result` for anything that ran to completion, however it exited, and an
    `Unresolved` for the two cases where nothing ran: `proc.run` raises `OSError` when the
    program could not be executed and `subprocess.TimeoutExpired` when it was killed for
    exceeding the timeout (see `bessemer.proc.run`). Those two clauses are the whole of this
    module's exception handling, and `tests/test_resolve.py::AbsorbedExceptionTest` holds
    them against what `proc.run` really raises rather than against a fixture.

    `env` is passed rather than left to inherit, and this is the only place it is decided:
    an ambient `GIT_DIR` makes every command below answer about a different repository than
    the one at `cwd`, which for `resolve_root_agreement` is a bypass rather than a
    preference. See `REDIRECTING_VARIABLES`.
    """
    argv = ["git", *arguments]
    try:
        return proc.run(argv, cwd=cwd, timeout=TIMEOUT_SECONDS, env=_git_env())
    except OSError as error:
        # Two causes, one exception type: `subprocess` raises `FileNotFoundError` for an
        # absent `cwd` just as it does for an absent program. They are told apart by
        # `error.filename` — see `_directory_missing` — because "install git" is a confident
        # misdiagnosis for a `start` path that is simply not there.
        if error.filename is not None and Path(os.fspath(error.filename)) == cwd:
            return _directory_missing(cwd, error)
        return _git_missing(argv, error)
    except proc.TimeoutExpired:
        return _git_timed_out(argv)


def resolve_base(cfg: Config, *, start: Path | None = None) -> Resolved[str] | Unresolved:
    """The base branch: from config if it is set and usable, else from `origin/HEAD`.

    Auto-detection is skipped entirely when config supplies a base — no git runs at all,
    which is what keeps a configured adapter working on a machine whose git is broken.

    The auto-detect path probes in the order the failures need distinguishing: whether this
    is a work tree at all, then whether there is an `origin`, then what `origin/HEAD` says.
    Each probe answers one question, because a single failing command cannot tell "no
    repository" from "no remote" from "no default branch" — and those are three different
    things to be told, with three different fixes.
    """
    configured = cfg.get("base")
    if configured is not None:
        if not isinstance(configured, str):
            return _base_not_a_string(cfg, configured)
        if not _usable_as_branch_name(configured):
            return _base_not_a_branch_name(cfg, configured)
        return Resolved(configured)

    where = _where(start)
    if isinstance(where, Unresolved):
        return where

    tree = _git(["rev-parse", "--is-inside-work-tree"], cwd=where)
    if isinstance(tree, Unresolved):
        return tree
    if not tree.ok:
        return _not_a_work_tree(where, _git_detail(tree))
    if tree.stdout.strip() != "true":
        # Exit 0 and `false`: a bare repository, or the inside of a `.git` directory. Git
        # answered the question rather than failing it, so there is no stderr to quote.
        return _not_a_work_tree(where, "git reports a git directory here, not a work tree")

    remotes = _git(["remote"], cwd=where)
    if isinstance(remotes, Unresolved):
        return remotes
    if not remotes.ok:
        return _git_failed(where, remotes)
    if ORIGIN not in remotes.stdout.split():
        # `git remote` and not `git remote get-url origin`: the name is all that is being
        # asked about, and `get-url` prints the URL, which is the text that can carry a
        # token. Nothing here needs the URL, so nothing here reads it.
        return _no_origin(where)

    head = _git(["symbolic-ref", "--quiet", "--short", ORIGIN_HEAD], cwd=where)
    if isinstance(head, Unresolved):
        return head
    branch = _branch_of(head.stdout) if head.ok else ""
    if not branch or not _usable_as_branch_name(branch):
        return _origin_head_unset(where)
    return Resolved(branch)


def resolve_root_agreement(
    cfg: Config, *, start: Path | None = None
) -> Resolved[Path] | Unresolved:
    """Whether the config root and the git work tree root are the same directory.

    **Both directions are errors** (ADR 0002). Above the git root means the walk-up escaped
    the repository; below means `init` ran in a subdirectory. Both messages name both paths,
    because the fix is a move and neither path alone says which move.

    Load-bearing rather than hygienic: the security invariant "the host pushes from the main
    repository" only names something unambiguous once the two are known to be the same
    directory. Doctor reports this; dispatch refuses on it, and dispatch is the caller
    holding credentials and a push path.

    **The two roots are compared by identity, not by spelling** — see `_relate`. Doctor and
    dispatch inherit this comparison rather than writing their own, which is why it has to be
    right here even though the default `start` cannot currently reach the case that breaks a
    string comparison.

    Returns the agreed root, so a caller that has checked agreement does not have to ask git
    again to find out what it agreed on. Git's spelling is the one returned: it is the on-disk
    one, and after an identity match the two spellings are interchangeable anyway.
    """
    where = _where(start)
    if isinstance(where, Unresolved):
        return where

    top = _git(["rev-parse", "--show-toplevel"], cwd=where)
    if isinstance(top, Unresolved):
        return top
    if not top.ok:
        return _not_a_work_tree(where, _git_detail(top))
    reported = top.stdout.strip()
    if not reported:
        # Exit 0 and nothing on stdout. Not a diagnosis bessemer can make — hence the
        # undiagnosed case rather than a claim about work trees.
        return _git_failed(where, top)

    # Resolved on both sides before comparing: `..` and symlinks are normalised here, so the
    # paths in a reason read as somewhere a user can go. Identity is still what decides the
    # answer — `resolve()` does not canonicalise case, so it is not enough on its own.
    git_root = Path(reported).resolve()
    config_root = cfg.root.resolve()

    match _relate(config_root, git_root):
        case Unresolved() as unresolved:
            return unresolved
        case str() as relation if relation == SAME:
            return Resolved(git_root)
        case str() as relation if relation == CONFIG_ABOVE:
            return _root_above(config_root, git_root)
        case str() as relation if relation == CONFIG_BELOW:
            return _root_below(config_root, git_root)
        case _:
            return _root_unrelated(config_root, git_root)
