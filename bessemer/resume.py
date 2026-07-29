"""Resume: what a re-dispatch does to a branch that already has history, and what a new
branch is called.

Ported from the port source's `.agentbox/tasklib.py` at commit `e194121f75f4`. **The
upstream tests are the specification** — where this module and a reading of what it "should"
do disagree, upstream wins, and the disagreement is a finding in the port report rather than
a silent improvement. `tests/port_manifest.py` is the census that makes that checkable.

## Continue versus start, in prose

Bessemer never decides between continuing a branch and starting one by looking at the
branch. It decides by looking at the **ledger**, and only when the human already said
`--resume <branch>`:

- **A dispatch that does not say `--resume` always starts.** Nothing here runs. A working
  branch that already has ten runs against it is not detected, not warned about, and not
  continued — the human names the branch, and naming it is the instruction.
- **A dispatch that says `--resume <branch>` always continues**, and `resolve_resume` below
  is how it recovers *what* to continue: the newest ledger record naming that branch supplies
  the mode (feature or spec), the source directory, and the pull request already open. If
  there is no such record, the resume is refused outright — `DispatchError`, nonzero exit,
  no container. A resume never falls back to starting something.

So the failure this module exists to prevent is not "continued when it should have started".
It is a resume that *proceeds having recovered nothing*: the feature folder was deleted, or
every issue in it is Done, and the run would start a container with no instructions and
push an empty commit over somebody's branch. `resume_dispatch_action` is the decision table
for exactly that, and its four answers are:

| answer | what happens |
|---|---|
| `NORMAL` | ride the ordinary feature/spec path — the common case, no mode switch |
| `FEEDBACK_ONLY` | nothing selectable remains, but the human typed feedback, so run that |
| `REFUSE` | nothing selectable and nothing typed — exit 1, no container |
| `HARD_ERROR` | an explicit `--issues` that cannot be resolved — exit 1, no container |

Two of the four are refusals, which is the shape to expect: a resume with nothing to do is
much more common than a resume that is wrong about which branch it is on.

**Pure over paths, text, and the ledger.** No subprocess, no environment, no git — the same
posture as `bessemer.issues` and `bessemer.ledger`, and proven by `tests/test_resume.py`'s
purity tests rather than asserted here. One ported function wanted git and did not get it;
see `_first_free_branch_name`.

**The legacy migration is not here** (decision 4 of the F2 README). Upstream's
`resolve_resume` called `_migrate_legacy_ledgers` before reading, so a first resume on an
install that predated the central ledger would rebuild it from per-directory files. The call
goes and the behaviour goes with it: a specs directory holding only legacy `runs.jsonl` files
resolves no records, and every `--resume` against it refuses with the not-found message.

**Vocabulary.** CONTEXT.md retires *task*: upstream's `tasks_dir` is `specs_dir`, and
`ResumeInfo.task_dir`/`task_dir_exists` are `source_dir`/`source_exists`. The `"task_dir"`
*string* stays spelled that way where it is read out of a ledger line, for the reason
`bessemer/ledger.py` gives: a key inside a record is data, and renaming it changes what the
code does.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from bessemer.issues import DispatchError, select_issues, slugify
from bessemer.ledger import (
    central_ledger_path,
    newest_record_for_branch,
    recent_distinct_branches,
)

FEATURE: Final = "feature"
"""A resume whose ledger record names a feature folder: issues are re-selected from it."""

SPEC: Final = "spec"
"""A resume whose ledger record names a one-off spec file: there is nothing to select."""

NORMAL: Final = "normal"
FEEDBACK_ONLY: Final = "feedback-only"
REFUSE: Final = "refuse"
HARD_ERROR: Final = "hard-error"

ACTIONS: Final = frozenset({NORMAL, FEEDBACK_ONLY, REFUSE, HARD_ERROR})
"""Every answer `resume_dispatch_action` can give — the list this module owns.

F3's dispatch switches on these, so the set is an interface rather than an implementation
detail, and `tests/test_resume.py` restates all four by hand. A test that read this frozenset
back could not notice an answer leaving it: the answer would vanish from both sides at once.
"""

_REFUSE_MESSAGE: Final = (
    "all issues Done — pass --feedback for follow-up work, or --issues NN to redo one"
)

_HARD_ERROR_MESSAGE: Final = (
    "--resume: recorded task dir no longer exists on disk — can't resolve --issues without it"
)

PROTECTED_BRANCHES: Final = frozenset({"master", "main"})
"""The branch names `is_protected` refuses. Restated by hand in `tests/test_resume.py`."""

_MAX_BRANCH_SUFFIX: Final = 9
"""`_first_free_branch_name` tries `-2` through `-9` before giving up. Upstream's bound."""


@dataclass
class ResumeInfo:
    """What the ledger remembers about a branch, as one resume needs it.

    `feature` and `spec` are paths, and exactly one of them is set — which one is what `mode`
    reports. `source_dir` is the record's own `task_dir`, kept even when it no longer exists
    on disk, because a run that cannot re-select issues still records where it came from.
    """

    mode: str
    source_dir: str
    feature: str | None
    spec: str | None
    source_exists: bool
    identity: str
    """The ledger's own feature/spec basename — survives a null `source_dir`, so a
    feedback-only run's ledger append can carry mode forward even once the directory is
    gone."""
    pr_url: str | None


def resolve_resume(specs_dir: Path, branch: str) -> ResumeInfo:
    """Recover `--resume <branch>`'s dispatch mode from the newest central-ledger record
    naming it, regardless of `task_dir` — a branch stays resumable even after the
    feature/spec that started it was superseded.

    Mode comes from which identity field the record carries (`feature=` or `spec=`), not
    from re-inspecting the filesystem: a feedback-only run's own ledger line always carries
    the SAME field forward from the run it resumed, so mode recovery composes across repeated
    resumes. `source_dir` is that record's own `task_dir` for feature mode (feature folder ==
    source directory), or `task_dir/<spec basename>` reconstructed from the record's `spec`
    field for spec mode.

    Raises `DispatchError` — the caller refuses, exit nonzero — only when the branch has no
    ledger record at all; that is the sole "not found" case. Everything else (a missing
    source directory, a missing spec file) is expressed via `source_exists=False` instead of
    a raise, since a resume can still proceed feedback-only from there.

    **A ledger this cannot read is indistinguishable from a branch that never ran.**
    `read_ledger` degrades to no records on one non-UTF-8 byte anywhere in the file (defect
    12 of the F2 README), and what arrives here is an empty list either way — so a resume of
    a real branch refuses with "(none recorded)". Ported as it stands; the fix belongs in the
    ledger, and is not made there either.
    """
    central_path = central_ledger_path(specs_dir)
    record = newest_record_for_branch(central_path, branch)
    if record is None:
        recent = recent_distinct_branches(central_path)
        hint = "\n".join(f"  {b}" for b in recent) if recent else "  (none recorded)"
        raise DispatchError(
            f"no ledger record for branch '{branch}' — recent branches in the ledger:\n{hint}"
        )
    source = record.get("task_dir") or None
    feature_name = record.get("feature")
    spec_name = record.get("spec")
    pr_url = record.get("pr_url") or None
    if feature_name:
        return ResumeInfo(
            mode=FEATURE,
            source_dir=source or "",
            feature=source,
            spec=None,
            source_exists=source is not None and Path(source).is_dir(),
            identity=feature_name,
            pr_url=pr_url,
        )
    spec_path = f"{source}/{spec_name}" if source and spec_name else None
    return ResumeInfo(
        mode=SPEC,
        source_dir=source or "",
        feature=None,
        spec=spec_path,
        source_exists=spec_path is not None and Path(spec_path).is_file(),
        identity=spec_name or "",
        pr_url=pr_url,
    )


def resume_dispatch_action(
    *,
    mode: str,
    source_exists: bool,
    issue_count: int,
    issues_arg: str,
    has_feedback: bool,
) -> tuple[str, str | None]:
    """The empty-selection decision table for `--resume`: `(action, message)`.

    `action` is one of `ACTIONS`; `message` is set for `REFUSE` and `HARD_ERROR` only, and is
    what the caller prints before exiting 1. See this module's docstring for the table in
    prose.

    The order of the branches below is the design. A gone source directory is answered
    *before* mode, because neither mode can select anything without it, and an explicit
    `--issues` against a gone directory is answered before feedback — a request naming issue
    numbers that cannot be resolved is a caller error, and feedback does not make it
    resolvable.
    """
    if not source_exists:
        if issues_arg:
            return HARD_ERROR, _HARD_ERROR_MESSAGE
        return (FEEDBACK_ONLY, None) if has_feedback else (REFUSE, _REFUSE_MESSAGE)
    if mode == SPEC:
        return NORMAL, None
    if issue_count > 0:
        return NORMAL, None
    return (FEEDBACK_ONLY, None) if has_feedback else (REFUSE, _REFUSE_MESSAGE)


def _resume_issue_count(info: ResumeInfo) -> int:
    """How many issues a feature-mode resume still has to run — the `issue_count`
    `resume_dispatch_action` needs to decide whether feedback is mandatory.

    Mirrors the default selection (`select_issues` with no `--issues`) rather than
    re-deriving the rule, so this answer and the dispatch's own cannot drift. A blocker
    violation counts as **one**, not zero: it means "do not force feedback", not "there is
    definitely nothing to run" — the real dispatch re-runs `select_issues` itself and
    surfaces the actual problem.

    Its only upstream caller is the interactive picker, which decision 1 of the F2 README
    does not port. It is here because it is resume logic that F3's dispatch needs, on the
    same basis as `bessemer.ledger`'s `_ledger_branch_order`.
    """
    if info.mode != FEATURE or not info.source_exists:
        return 0
    assert info.feature is not None
    try:
        return len(select_issues(Path(info.feature), None))
    except DispatchError:
        return 1


def is_protected(branch: str) -> bool:
    """Whether `branch` is a name a run may not take as its own working branch.

    **This is not the thing standing between a dispatch and `main`.** It is a data-layer
    predicate over a string — case-insensitive, whitespace-tolerant, and that is the whole of
    it. The protections that actually hold are ADR 0001's, on the push path, and they land in
    F3: the working branch is never `main` because the push refuses it, not because this
    returned `True` somewhere upstream of the push. A reader who takes this function for the
    safety mechanism has been misled by its name, and would then be free to add a caller that
    skips it.
    """
    return branch.strip().lower() in PROTECTED_BRANCHES


def _first_line(text: str) -> str:
    return next((line for line in text.splitlines() if line.strip()), "")


def _branch_name_suggestion(
    spec: str | None, feature: str | None, prompt_text: str | None
) -> str:
    """What to call a new working branch, before collision suffixing — the feature folder
    name (the established habit), a one-off spec's basename sans `.md`, or the slug of a
    one-off prompt's first line.

    A precedence, not a search: a feature wins over a spec and over typed text. An ad-hoc
    prompt's own materialized filename takes the branch name instead (see
    `bessemer.issues.materialize_ad_hoc`) — this is only ever a starting point for naming the
    branch itself.

    Its only upstream caller is the interactive picker, unported under decision 1 of the F2
    README; F3's dispatch is the caller bessemer expects.
    """
    if feature:
        return feature
    if prompt_text is not None:
        return slugify(_first_line(prompt_text))
    assert spec is not None
    name = Path(spec).name
    return name[: -len(".md")] if name.endswith(".md") else name


def _first_free_branch_name(
    suggestion: str,
    *,
    local_exists: Callable[[str], bool],
    remote_exists: Callable[[str], bool],
) -> str:
    """`suggestion` itself if it is free, else the first free `-2`..`-9` suffix — checked
    against both local branches and `origin`'s remote-tracking refs, because a branch already
    pushed from elsewhere is just as much a collision as a local one. Exhausting the suffixes
    falls back to the bare suggestion; picking it then collides the same way typing an
    already-existing name does today.

    **The two predicates are parameters because branch existence is a git question and this
    module spawns nothing.** Upstream asked git directly — `_branch_exists` shelled out to
    `git show-ref` for local branches, `_remote_branch_exists` for the remote-tracking ones,
    both answerable locally without a network call — and both belong to F3's dispatch here,
    which is where the subprocess wrapper is allowed. They stay two arguments rather than one
    combined `taken` for the same reason upstream kept two functions: the local and remote
    cases have a test each, and collapsing them would leave those two tests identical.
    Recorded in the port report rather than resolved by importing `bessemer.proc`.
    """

    def taken(name: str) -> bool:
        return local_exists(name) or remote_exists(name)

    if not taken(suggestion):
        return suggestion
    for n in range(2, _MAX_BRANCH_SUFFIX + 1):
        candidate = f"{suggestion}-{n}"
        if not taken(candidate):
            return candidate
    return suggestion
