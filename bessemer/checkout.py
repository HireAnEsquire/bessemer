"""The per-run checkout, and the discipline that the host never runs git inside one.

A **Checkout** (CONTEXT.md) is the working copy a run hands to its container, produced by
`git clone --no-hardlinks` at the working branch's tip, bind-mounted read-write, and owned
by the agent from the moment the container starts. Four operations, and ADR 0003 says the
interface listed is the whole of it: `create`, `read_branch`, `salvage`, `remove`.

**Nothing here prints.** A non-fast-forward salvage is a value the caller renders — loudly,
because it means an unpushed branch is still only inside the checkout and the checkout must
be kept. Two callers exist (dispatch's `finally`, and reclaim's per-item loop) and they say
it differently; a `print` here would be a third voice neither of them could suppress.

## The one rule: no host-side git inside the checkout, after it exists

The agent owns the checkout's `.git`, which means it owns `.git/config` and `.git/hooks`. A
host-side `git -C <checkout> push` would run the agent's `pre-push` hook, and honour its
`core.sshCommand`, **on the host, with the dispatching developer's credentials**. So:

- **The branch is read as text**, out of `<checkout>/.git/HEAD` — never
  `git -C <checkout> symbolic-ref`. `read_branch` takes no runner, so there is no seam
  through which it could spawn even by accident (pin comment, run.sh:499–504).
- **Salvage fetches *from* the checkout**, run from the main repository. `upload-pack` is
  git's hardened-against-untrusted-repositories side; the writing end is the main
  repository, whose config is bessemer's.
- **Every argv this module runs has the main repository as its `cwd`.** The checkout is
  named by `-C` or as a fetch URL — an argument, never a working directory. That is what
  makes tier 2's "no git inside the checkout" check expressible over a recorded stream
  (ADR 0003's tier-2 rider 2), and this module is where the check starts.

**`create`'s config writes are the one legal moment**, and they are legal because of *when*
they happen: the checkout is seconds old, no container exists yet, and no agent has touched
it. Nothing after `create` returns may write into the checkout with git again. A later
caller tempted to "just set one more thing" is the defect this paragraph exists to name.

## Salvage is one refspec, spelled once

At the pin the identical fast-forward-only fetch appears **three times** — run.sh:508 (the
gc block), :1177 (the cleanup trap) and :1538 (landing) — and F3 README decision 3 and
ADR 0003 both make consolidating it this module's job. Three spellings of a
security-relevant refspec are three chances for one of them to grow a `+`.

`SALVAGE_REFSPEC` carries **no `+`**, and `tests/test_checkout.py` restates the string by
hand rather than building it from this module. The refspec is fast-forward-only because the
branch in the main repository is user-owned: if the agent rebased or reset inside the
checkout, a forced fetch would rewrite the user's branch backwards and silently discard
work that only exists on the host. When the fetch cannot fast-forward, the answer is to keep
the checkout and tell somebody — never to force, and never to remove.

## The proc seam, and the environment

`create` and `salvage` take their runner (`proc.Runner`); neither reaches for `proc.run`
itself (ADR 0003). It is a required keyword with no default, so a caller cannot acquire the
real spawner by omission and every tier-2 test composes the double at the same seam the
dispatcher composes the real one at.

The child environment is `bessemer.resolve.git_env` — the ambient environment minus the
variables that redirect git at another repository. Shared with the resolvers rather than
restated, because here it is stronger than a preference: an exported `GIT_DIR` would make
the salvage fetch **write into** whatever repository it names, which is the one thing this
module exists to keep from happening by accident.
"""

import getpass
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from bessemer import proc
from bessemer.resolve import ORIGIN, git_env

HEAD_PATH: Final = (".git", "HEAD")
"""Where a checkout records what it has checked out, relative to its root.

A tuple of path parts rather than a string, so the read is built with `Path` joins and the
`.git` in it is visible to a reader grepping for who touches it.
"""

BRANCH_REF_PREFIX: Final = "ref: refs/heads/"
"""What `.git/HEAD` holds when a branch is checked out. Anything else is not a branch.

A detached HEAD holds a raw SHA and matches nothing here; so does a HEAD pointed at a
remote-tracking ref. Both land in the same arm, which is the pin's behaviour: its
`sed -n 's#^ref: refs/heads/##p'` prints nothing for either (run.sh:505).
"""

SALVAGE_REFSPEC: Final = "refs/heads/{branch}:refs/heads/{branch}"
"""The fast-forward-only refspec, as a hand-written literal. **No leading `+`.**

The `+` is the whole decision. With it, `git fetch` overwrites the destination ref whatever
its history; without it, git refuses anything that is not a fast-forward and exits nonzero,
which is exactly the signal `salvage` reports. See the module docstring for why the branch
in the main repository must never be rewritten backwards.

Restated by hand in `tests/test_checkout.py`, deliberately: a test that formatted this
constant would agree with a mutation of it instead of catching one.
"""

FALLBACK_EMAIL: Final = "bessemer@users.noreply.github.com"
"""The commit email when the dispatching developer has none configured.

The pin's `agentbox@users.noreply.github.com` with the product token renamed — adopter-facing
prose, and nothing upstream asserts the string (F2 decision 7).
"""

FALLBACK_NAME: Final = "bessemer"
"""The commit name when neither git config nor the operating system supplies one.

The pin ends its fallback chain at `id -un` (run.sh:1208) and has nowhere to go if that
fails, which would write an empty `user.name` and leave the agent unable to commit at all —
a checkout that looks fine and fails at the end of the first implement pass. One more link
closes it.
"""

TIMEOUT_SECONDS: Final = 15.0
"""For the commands that only read or write config. A backstop, not a deadline.

The same reason `bessemer.resolve.TIMEOUT_SECONDS` is one: these touch the filesystem and
nothing else, so a slow one means something is wedged, and a dispatcher that hangs here
holds a lock nobody can see the owner of.
"""

TRANSFER_TIMEOUT_SECONDS: Final = 600.0
"""For the two commands that move objects: `git clone`, and the salvage fetch.

Forty times the other one because `--no-hardlinks` means every object in the repository is
*copied* rather than shared by inode, and the repositories bessemer is for are large enough
that this is the flag's whole cost. Still a backstop: it is the difference between a slow
`git clone` and a wedged one, and only the second should end a run.
"""


def _git(
    arguments: list[str], *, run: proc.Runner, repo_root: Path, timeout: float
) -> proc.Result:
    """Run one git command from the main repository. The only spawn shape in this module.

    `cwd` is `repo_root` and is not a parameter: a caller that could choose it could choose
    the checkout, and the rule this module enforces would become a convention. The checkout
    is reached by `-C` or by URL — an argument, which the recorded stream shows.
    """
    return run(["git", *arguments], timeout=timeout, cwd=repo_root, env=git_env())


def _checked(
    arguments: list[str], *, run: proc.Runner, repo_root: Path, timeout: float
) -> proc.Result:
    """`_git`, raising `proc.ProcessError` unless it exited 0.

    Not `proc.run_checked`, because that would spawn through `proc` directly and bypass the
    seam the caller handed in. The raise is the same one, built from the same `Result`.
    """
    result = _git(arguments, run=run, repo_root=repo_root, timeout=timeout)
    if not result.ok:
        raise proc.ProcessError(result)
    return result


def _configured(name: str, *, run: proc.Runner, repo_root: Path, fallback: str) -> str:
    """What `git config <name>` says in the main repository, or `fallback`.

    Read from the main repository so the answer is the dispatching developer's own identity,
    through every layer their git would use — that is the point of copying it in (run.sh:1206).

    Falls back on an **empty** answer as well as a failing one. The pin's `||` fires only on
    a nonzero exit, and `git config user.name ''` exits 0 with nothing to say; ported
    literally that writes an empty identity into the checkout and the agent's first commit
    fails. Deliberately a superset of the pin.
    """
    result = _git(["config", name], run=run, repo_root=repo_root, timeout=TIMEOUT_SECONDS)
    return (result.stdout.strip() if result.ok else "") or fallback


def _local_user() -> str:
    """The operating system's name for whoever is running, standing in for the pin's `id -un`.

    Not a spawn: `tests/guard.py` allowlists programs, and adding `id` to that list to learn
    a name the standard library already knows would widen a security allowlist for a
    convenience. `getpass.getuser` consults `LOGNAME`/`USER`/`LNAME`/`USERNAME` and then the
    password database — a superset of what `id -un` answers, and it raises rather than
    returning nothing when there is no answer at all.
    """
    try:
        return getpass.getuser() or FALLBACK_NAME
    except OSError, KeyError:
        # `getpass.getuser` raises `OSError` with no password-database entry for the uid, and
        # `KeyError` on the platforms where it still surfaces the `pwd` lookup directly.
        return FALLBACK_NAME


def create(
    *,
    repo_root: Path,
    checkout: Path,
    branch: str,
    remote_url: str,
    run: proc.Runner,
) -> None:
    """Clone `branch` into `checkout`, point `origin` at the real remote, set the identity.

    Four effects in the pin's order (run.sh:1204–1209), and the ordering is the contract:
    the `git clone`, the remote repoint, then the two identity writes. **The config writes
    are the one moment the host may run git inside a checkout** — the tree is seconds old, no
    container exists, and no agent has touched it. Nothing after this function returns may
    write into it again; see the module docstring for what the agent owns by then.

    `--no-hardlinks` is a security flag and not a performance one. Without it the checkout
    shares object files with the host repository by inode, and the agent — which owns this
    tree — could corrupt the host repository's objects through them (run.sh:1196–1202, the
    2026-07-20 audit residual). `--branch` is what makes the checkout start on the working
    branch rather than on the repository's default one.

    `remote_url` is a parameter because it is a git question, and a git question is one
    dispatch answers rather than one this module defaults (F2 decision 9). The `origin` that
    `git clone` leaves behind is the *host repository's path*, which does not exist inside
    the container, so an unrepointed checkout is one whose first push writes into the host
    repository instead of the remote.

    Raises `proc.ProcessError` on any of the four. There is no useful half a checkout: a
    caller that proceeded to `docker run` after a failed `git clone` would start a container
    over a tree that is not the branch it thinks it is.
    """
    at_checkout = ["-C", str(checkout)]
    _checked(
        [
            "clone",
            "--quiet",
            "--no-hardlinks",
            "--branch",
            branch,
            str(repo_root),
            str(checkout),
        ],
        run=run,
        repo_root=repo_root,
        timeout=TRANSFER_TIMEOUT_SECONDS,
    )
    _checked(
        [*at_checkout, "remote", "set-url", ORIGIN, remote_url],
        run=run,
        repo_root=repo_root,
        timeout=TIMEOUT_SECONDS,
    )
    for setting, fallback in (
        ("user.name", _local_user()),
        ("user.email", FALLBACK_EMAIL),
    ):
        value = _configured(setting, run=run, repo_root=repo_root, fallback=fallback)
        _checked(
            [*at_checkout, "config", setting, value],
            run=run,
            repo_root=repo_root,
            timeout=TIMEOUT_SECONDS,
        )


def read_branch(checkout: Path) -> str | None:
    """Which branch `checkout` has checked out, read as text. `None` when there is no branch.

    **Takes no runner, and that is the design.** The checkout's `.git/config` is
    agent-controlled, so the host runs no git inside it — read side included — and a function
    with no seam cannot acquire one by a later edit that looked harmless
    (pin comment, run.sh:499–504).

    `None` means "there is no branch here to salvage", and it covers every way that can be
    true: a detached HEAD (a raw SHA, no `ref:` line), a HEAD pointing somewhere outside
    `refs/heads/`, no checkout at all, and a `.git/HEAD` that cannot be read. The pin makes
    the same collapse — its `sed` prints nothing in each case and its `|| true` swallows the
    read failure (run.sh:505) — and the caller's response is the same for all of them: keep
    the checkout, say so, salvage nothing.
    """
    try:
        head = (checkout.joinpath(*HEAD_PATH)).read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        # `OSError` covers the absent checkout, the absent file, a directory in its place and
        # a permission denial; `UnicodeDecodeError` covers a `.git/HEAD` that is not text at
        # all. Nothing here is worth telling apart: every one of them means the same thing to
        # the caller, and a checkout the host cannot read is not one it may run git in.
        return None
    first_line = head.splitlines()[0] if head.splitlines() else ""
    if not first_line.startswith(BRANCH_REF_PREFIX):
        return None
    return first_line.removeprefix(BRANCH_REF_PREFIX).strip() or None


@dataclass(frozen=True)
class Salvage:
    """What a salvage fetch did, and what git said doing it.

    A frozen dataclass, like `proc.Result` next to it — and carrying the same exclusion:
    **no `__bool__`, and none may be added.** `if salvage(...):` reads as "did it run" to
    every reader and would mean "did it advance". `__len__` is excluded on the same grounds.
    `advanced` is the whole decision and has to be asked for by name.
    """

    advanced: bool
    """Whether the branch in the main repository now points at the checkout's tip.

    `False` means git refused a non-fast-forward — the agent rebased or reset inside the
    checkout, or the branch moved on both sides — and **the checkout must be kept**.
    Removing it then discards work that exists nowhere else.
    """

    result: proc.Result
    """What git said. Carried so the caller can quote it through `proc.quote` for the host
    log rather than inventing a reason; git's stderr is credential-bearing and where it may
    go is `proc`'s decision, not this module's."""


def salvage(
    *,
    repo_root: Path,
    checkout: Path,
    branch: str,
    run: proc.Runner,
) -> Salvage:
    """Fast-forward `branch` in the main repository from `checkout`. Never forces.

    The one definition of a refspec the pin spells three times (run.sh:508, :1177, :1538) —
    ADR 0003 and F3 README decision 3. Run **from the main repository**, fetching *from* the
    checkout: `upload-pack` is git's hardened-against-untrusted-repositories side, and the
    end doing the writing is the repository whose config is bessemer's.

    Non-fast-forward is not an error here, it is the other answer: git exits nonzero, this
    returns `Salvage(advanced=False, ...)`, and the caller keeps the checkout and says so.
    Making it raise would put the decision "may this checkout be removed" inside an exception
    handler in two different callers.

    Nothing is checked about `checkout` first. A caller that has not established there is a
    branch to salvage should ask `read_branch`; a fetch from a path that is not a repository
    fails, which is the same answer as a refusal and is safe for the same reason — the
    branch in the main repository is left exactly as it was.
    """
    result = _git(
        [
            "fetch",
            "--quiet",
            str(checkout),
            SALVAGE_REFSPEC.format(branch=branch),
        ],
        run=run,
        repo_root=repo_root,
        timeout=TRANSFER_TIMEOUT_SECONDS,
    )
    return Salvage(advanced=result.ok, result=result)


def remove(checkout: Path) -> None:
    """Delete `checkout` and everything under it. The pin's `rm -rf` (run.sh:1162, :1178).

    **Only ever called by an owner that has salvaged, or has decided not to.** This function
    has no opinion and cannot have one: it is called from dispatch's `finally` after a
    successful salvage, from the stale-leftover cleanup that precedes `create`, and from reclaim
    after a per-item salvage. The decision lives at all three, and none of them may reach
    here after a `Salvage` whose `advanced` is `False`.

    `shutil.rmtree`, not a spawned `rm`: `tests/guard.py` allowlists spawns by program, and
    adding `rm` to that list so this line could be ported literally would weaken the guard
    for the whole suite in exchange for nothing.

    A checkout that is not there is not an error — a `finally` cannot know whether the checkout
    got as far as existing, and `rm -rf` tolerates it too. **Every other failure propagates.**
    A checkout that cannot be removed is a leak, the caller is the only thing that can say so,
    and `ignore_errors=True` is how it would become silence instead.
    """
    try:
        shutil.rmtree(checkout)
    except FileNotFoundError:
        pass
