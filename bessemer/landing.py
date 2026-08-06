"""Landing: the push, the draft pull request, and the body a human reads before merging.

A **Landing** (CONTEXT.md) is the push-plus-pull-request step that ends a run, and a run
lands whatever is done — an approved diff, a needs-work one, or a run that stopped early.
ADR 0003 gives it one entry point, `land`, plus the pure argv builders whose flags are the
contract; the whole of the interface is what is exported here.

Oracle region: `run.sh:1529–1592` at the pin.

## What arrives as a value, and why that is the seam

**The pull-request description is a parameter.** Generating it is one more agent pass, and
passes are `bessemer.passes`' — dispatch runs it and hands the text over. That is why this
module is not blocked on the pass loop, why it composes rather than invokes, and why F4's
feature loop can reuse it unchanged with a description assembled from several issues. A
`land` that ran its own pass would own a container, a prompt and a retry ladder, none of
which have anything to do with pushing a branch.

`footer` is the same shape: `passes.Verdict.footer` is already the sentence, so this module
places it and never reformats it. The two sentences this module *does* own — the attribution
and the description-failure fallback — are pinned literals below.

**One line of the oracle region is nobody's, and it is this paragraph rather than a gap.**
The pin writes the generated description to the run log before composing (`printf '%s\\n'
"$body" >>"$log"`, run.sh:1573). Host-side that text is already in the log: `passes.run_pass`
renders every pass's transcript through `bessemer.stream` as it arrives, the description pass
included, so writing it a second time here would log the same paragraph twice. The pin tees
because its `claude_pass` output was captured rather than rendered.

## Three absences, and each is structural rather than remembered

- **No force-push exists here at F3.** `--force-with-lease` arrives with F4's
  `--hard-reset`, which is the mode where history legitimately diverges; continue-mode
  dispatch forks from the branch tip and appends, so a plain push is the whole of it. There
  is no parameter, no branch and no constant through which a `--force*` could reach an argv,
  and `tests/test_landing.py::AbsenceTest` asserts the absence over every recorded argv of
  every path.
- **Nothing here can merge.** `gh pr create --draft` is the human merge gate (ADR 0001), and
  the word `merge` appears in no argv this module builds.
- **Nothing from a `Result` reaches the body.** git and gh echo remote URLs on stderr and a
  remote URL can carry a token; the body is read off this machine. The only text this module
  composes from a child is the host-log line for a failure, and it goes through `proc.quote`
  at `HOST_LOG` (ADR 0003's one quotability policy). The body is built from the description,
  the footer and the spec's basename, and from nothing else.

## The body rides stdin

`gh pr edit|create --body-file -` reads the body off a pipe, exactly as the pin does
(run.sh:1567's comment: "Body goes to gh via stdin — never a shell arg"). It is agent-written
text, so no amount of quoting in it can become a word of a command line — ADR 0001's rule,
and the same one that puts a pass's prompt on stdin. `proc.run` grew `stdin_text` for this
call and no other.

## Failures end the run

A push or a gh call that failed raises `proc.ProcessError`, after one line naming it in the
host log. Upstream's `set -euo pipefail` ends the run at the same three places, and there is
no useful half a landing: a pull request whose branch was never pushed describes commits
nobody else can see, and a run that could not count its own commits does not know whether
there was anything to land.

The one nonzero exit that is **not** a failure is the probe. `gh pr view` on a branch with
no pull request exits nonzero and says so on stderr; the pin sends that to `/dev/null` and
`|| true` (run.sh:1578), because it is the ordinary first-run answer and the response to it
is to create one.

## Every child runs from the main repository

`bessemer.checkout`'s rule, inherited: the checkout is never a working directory and is not
named here at all. Dispatch salvages the branch out of the checkout — `checkout.salvage`,
the one definition of that refspec (run.sh:1538 is its third spelling) — before calling
`land`, so by the time anything here runs, the branch in the main repository is already at
the tip that is about to be pushed.

The environment is `resolve.git_env`: the ambient one minus the variables that redirect git
at another repository. It is inherited rather than allowlisted because this is the push path
and it genuinely needs `SSH_AUTH_SOCK` and the credential helpers (ADR 0002); the
subtraction is what keeps an exported `GIT_DIR` from pushing out of a repository nobody
named. `gh` gets the same environment for the same reason — it runs git underneath to work
out the remote and the head.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from bessemer import proc
from bessemer.resolve import ORIGIN, git_env

GIT: Final = "git"
GH: Final = "gh"
"""The two programs this module runs. Named once each so the recorded stream is greppable."""

PUSH_REFSPEC: Final = "refs/heads/{branch}:refs/heads/{branch}"
"""The push refspec, as a hand-written literal. **No leading `+`.**

Explicit on both sides rather than a bare branch name, so no `push.default` a developer has
configured decides which ref this writes — the pin spells it out for that reason
(run.sh:1546).

It is deliberately **not** `checkout.SALVAGE_REFSPEC`, although the two strings match today.
That one is the fetch out of the checkout, consolidated because the pin spells it three
times; this one is the push to the remote. They are two decisions that happen to agree, and
sharing a constant would mean a future edit to either arriving in the other unread. What
they do share is the reason for the missing `+`: a forced write to a user-owned branch
discards work that exists nowhere else.

Restated by hand in `tests/test_landing.py`, deliberately: a test that formatted this
constant would agree with a mutation of it instead of catching one.
"""

COUNT_RANGE: Final = "{start}..refs/heads/{branch}"
"""What `git rev-list --count` is asked about: commits reachable from the branch, not `start`.

`refs/heads/` rather than the bare branch name so the count is about the branch and cannot
be answered by a tag or a remote-tracking ref that shares its name.
"""

TITLE: Final = "[bessemer] {branch}"
"""The pull request's title. The pin's `[agentbox] $branch` with the product token renamed —
adopter-facing prose, and nothing upstream asserts the string (F2 decision 7, F3 decision
8.1). The branch is the run's identity (CONTEXT.md), so the title is the only thing a list of
pull requests needs to tell one bessemer run from another."""

OPEN_URL_FILTER: Final = 'select(.state == "OPEN") | .url'
"""The probe's `--jq`, ported exactly (run.sh:1578).

`gh pr view` answers about closed and merged pull requests too, and editing a merged one
would put this run's description on a diff that already landed. Filtering in the query rather
than in Python is the pin's shape and keeps the decision in one expression: no output means
no open pull request, whether because there is none, because it is closed, or because gh
failed.
"""

BODY_FILE_STDIN: Final = "-"
"""What `--body-file` is given: a dash, meaning read the body from stdin. See the module
docstring for why the body may not be an argument."""

BODY: Final = "{description}\n\n---\n{footer}\n{attribution}"
"""The whole body's layout, as the pin's `printf` assembles it (run.sh:1574–1575).

One template rather than concatenation at the call site, so the separators are pinned too —
the blank line and the `---` rule that divide the agent's description from bessemer's own
two sentences, and the single newline between those two.
"""

ATTRIBUTION: Final = (
    "AI-authored via bessemer (spec: `{spec}`). Draft until the dispatching dev reviews it."
)
"""Who wrote the diff, and what the reader is expected to do about it.

The pin's sentence with the product token renamed (F3 decision 8.1), pinned as the *whole
sentence*: it is the one line telling a reviewer that no person wrote this and that the draft
state is deliberate. `spec` is a basename — the dispatching developer's directory layout is
not something a pull request tells the world about.
"""

DESCRIPTION_FAILED: Final = "_(description generation failed — see the task log)_"
"""What stands in when the description pass produced nothing (run.sh:1572).

A landing still happens: the branch is pushed and the pull request is opened, because the
commits are real whether or not an agent managed to describe them. Italic and parenthesised
because it is bessemer speaking in a place the reader expects the agent's own words.

"task log" is left as the pin wrote it, for `passes.NEEDS_WORK_FOOTER`'s reason: it names the
log file, and renaming that artifact is issue 10's business rather than a prose choice made
here.
"""

TIMEOUT_SECONDS: Final = 15.0
"""For the two `rev-list --count` calls. A backstop, not a deadline.

`bessemer.checkout.TIMEOUT_SECONDS`' reason: these read the local object store and nothing
else, so a slow one means something is wedged — and a dispatcher that hangs here holds a lock
nobody can see the owner of.
"""

PUSH_TIMEOUT_SECONDS: Final = 600.0
"""For `git push`, which moves objects to a remote over whatever network is there.

The same ten minutes `bessemer.checkout` gives its transfers, for the same reason: it is the
difference between a slow push and a wedged one, and only the second should end a run.
"""

GH_TIMEOUT_SECONDS: Final = 120.0
"""For the three `gh` calls, each of which is one round trip to GitHub's API.

Shorter than the push because none of them moves a repository, and long enough that a slow
API answer is not read as a failed landing.
"""


@dataclass(frozen=True)
class Landing:
    """What a run's landing came to: what was pushed, and where it can be read.

    **No `__bool__`, and none may be added**, for `proc.Result`'s reason and
    `checkout.Salvage`'s: `if landing:` reads as "did it land" to one reader and "is there a
    landing" to the next. `landed` is the whole decision and has to be asked for by name.
    """

    commits: int
    """Commits past the merge-base boundary. The gate: zero means nothing lands."""

    new_commits: int
    """Commits this run added, which is what an operator's "done" line reports.

    Distinct from `commits` because a run that failed its first pass on a branch an earlier
    run committed to still lands: the boundary count decides, and this number is what says
    how much of it is new.
    """

    pr_url: str
    """The draft pull request's URL, or the empty string when nothing landed.

    Empty is also what a `gh pr create` that printed nothing leaves, which is not treated as
    a failure: gh exited 0, so the pull request exists, and a run that aborted over a missing
    URL would abort after the thing it was reporting had already worked.
    """

    updated: bool
    """Whether an existing open pull request was edited rather than a new one created.

    Re-runs land on the same branch (CONTEXT.md: the working branch is the run's identity),
    so the second dispatch updates the first's pull request instead of opening a duplicate.
    """

    @property
    def landed(self) -> bool:
        """Whether anything was pushed and a pull request touched.

        Derived rather than recorded, because `commits` is the gate and a stored flag beside
        it would be two values free to disagree about one decision — ADR 0003's reason for
        not restating a count, one module along. False means the branch had no commits past
        the boundary: nothing was pushed and no gh call was made. The ledger line is appended
        either way; that is issue 10's, and it records an empty `pr_url` here (F3 decision
        8.4).
        """
        return self.commits > 0


def push_argv(*, branch: str) -> list[str]:
    """`git push --quiet -u origin refs/heads/<b>:refs/heads/<b>`. Pure; the whole argv.

    Plain, and every word of it is the decision: no `--force*` (see the module docstring),
    `-u` so the branch in the main repository tracks the remote one a developer will pull,
    and the explicit refspec so nothing configured decides which ref is written.
    """
    return [
        GIT,
        "push",
        "--quiet",
        "-u",
        ORIGIN,
        PUSH_REFSPEC.format(branch=branch),
    ]


def count_argv(*, start: str, branch: str) -> list[str]:
    """`git rev-list --count <start>..refs/heads/<b>` — how far the branch is past `start`.

    Two callers with two `start`s: the merge-base boundary, which is the gate, and the
    branch's tip before this run, which is what makes "3 new commits" a true sentence.
    """
    return [GIT, "rev-list", "--count", COUNT_RANGE.format(start=start, branch=branch)]


def probe_argv(*, branch: str) -> list[str]:
    """`gh pr view <b> --json url,state --jq '…'` — the URL of an open pull request, if any.

    Ported exactly, `--jq` expression included; see `OPEN_URL_FILTER` for what the filter
    decides.
    """
    return [GH, "pr", "view", branch, "--json", "url,state", "--jq", OPEN_URL_FILTER]


def edit_argv(*, branch: str) -> list[str]:
    """`gh pr edit <b> --body-file -`. The re-run path: same branch, same pull request."""
    return [GH, "pr", "edit", branch, "--body-file", BODY_FILE_STDIN]


def create_argv(*, branch: str, base: str) -> list[str]:
    """`gh pr create --draft --base <base> --head <b> --title … --body-file -`.

    **`--draft` is unconditional and there is no parameter that could remove it.** A run
    lands whatever is done, review verdict included, so the thing that keeps an unattended
    agent's diff from being mergeable is this flag (ADR 0001's human merge gate).

    `base` arrives spelled the way `origin/HEAD` resolution answers — `origin/main` — and gh
    wants the branch name, so a leading `origin/` is removed and nothing else is (the pin's
    `${BASE#origin/}`, :1583). Prefix removal rather than a replacement: a branch genuinely
    named `release/origin/main` targets itself.
    """
    return [
        GH,
        "pr",
        "create",
        "--draft",
        "--base",
        base.removeprefix(f"{ORIGIN}/"),
        "--head",
        branch,
        "--title",
        TITLE.format(branch=branch),
        "--body-file",
        BODY_FILE_STDIN,
    ]


def body(*, description: str, footer: str, spec: Path) -> str:
    """The pull request's body: the agent's description, the verdict, and the attribution.

    Pure, and composed from three strings none of which came from a `proc.Result` — that is
    the stderr invariant discharged structurally rather than by a rule this function
    remembers (ADR 0001, F3 decision 2's tier-2 rider 3).

    A `description` that is empty or nothing but whitespace becomes `DESCRIPTION_FAILED`. The
    pin tests its own with `[ -n "$body" ]` on a command substitution, which has already
    dropped the trailing newlines; the whitespace case is the same answer reached one step
    further, because a body that opens on a blank line is not a description either.

    **Deliberately a superset of the pin in one more way**, recorded rather than discovered:
    a description that has text *and* leading blank lines is stripped at both ends here,
    where the pin's command substitution drops trailing newlines only. `checkout._configured`
    widens the pin the same way for the same kind of reason — the wider rule is the one whose
    result a reader wants, and a pull request opening on blank lines under a `## Overview`
    the agent wrote is the pin's own output being read as layout it did not choose.

    `spec` is reduced to its basename here rather than at the call site, so there is no route
    to a body carrying an absolute host path.
    """
    return BODY.format(
        description=description.strip() or DESCRIPTION_FAILED,
        footer=footer,
        attribution=ATTRIBUTION.format(spec=spec.name),
    )


def land(
    *,
    repo_root: Path,
    branch: str,
    base: str,
    boundary: str,
    previous_tip: str,
    description: str,
    footer: str,
    spec: Path,
    emit: Callable[[str], None],
    run: proc.Runner,
) -> Landing:
    """Push `branch` and open or update its draft pull request. The pin's `:1529–1592`.

    Nothing happens at all when the branch has no commits past `boundary`: no push argv, no
    gh argv, and a `Landing` that says so. The gate is the pin's (`:1541`) and it is checked
    *here*, where the push is, rather than by the caller — a `land` handed a count it did not
    take could be handed the wrong one.

    `boundary` is the merge-base with the base ref and `previous_tip` is where the branch
    stood before this run; both are git questions dispatch answers, and neither has a default
    (F2 decision 9).

    `emit` takes the host-side run log's lines, and gets one only when a child failed —
    `proc.quote` at `HOST_LOG`, which is the single destination gh's and git's stderr may
    reach (ADR 0003). A landing that worked writes nothing: the push is `--quiet` and the
    edit's output is what the pin sends to `/dev/null`.

    Raises `proc.ProcessError` when the count, the push, the edit or the create fails. See
    the module docstring for why none of those is a `Landing` with a flag on it.
    """
    commits = _count(start=boundary, branch=branch, repo_root=repo_root, emit=emit, run=run)
    new_commits = _count(
        start=previous_tip, branch=branch, repo_root=repo_root, emit=emit, run=run
    )
    if commits == 0:
        return Landing(commits=commits, new_commits=new_commits, pr_url="", updated=False)

    _checked(
        push_argv(branch=branch),
        timeout=PUSH_TIMEOUT_SECONDS,
        repo_root=repo_root,
        emit=emit,
        run=run,
    )

    composed = body(description=description, footer=footer, spec=spec)
    # The pin's `printf '%s\n'`: gh gets the body and one closing newline, which is what makes
    # the last line of a markdown body a line rather than a fragment.
    stdin_text = f"{composed}\n"

    # A probe that failed is not an error, and its stderr goes nowhere: no output means no
    # open pull request, which is the ordinary first-run answer (the pin's `2>/dev/null ||
    # true`, :1578).
    probe = run(
        probe_argv(branch=branch),
        timeout=GH_TIMEOUT_SECONDS,
        cwd=repo_root,
        env=git_env(),
    )
    existing = probe.stdout.strip() if probe.ok else ""

    if existing:
        _checked(
            edit_argv(branch=branch),
            timeout=GH_TIMEOUT_SECONDS,
            repo_root=repo_root,
            emit=emit,
            run=run,
            stdin_text=stdin_text,
        )
        return Landing(
            commits=commits,
            new_commits=new_commits,
            pr_url=existing,
            updated=True,
        )

    created = _checked(
        create_argv(branch=branch, base=base),
        timeout=GH_TIMEOUT_SECONDS,
        repo_root=repo_root,
        emit=emit,
        run=run,
        stdin_text=stdin_text,
    )
    return Landing(
        commits=commits,
        new_commits=new_commits,
        pr_url=created.stdout.strip(),
        updated=False,
    )


def _checked(
    argv: list[str],
    *,
    timeout: float,
    repo_root: Path,
    emit: Callable[[str], None],
    run: proc.Runner,
    stdin_text: str | None = None,
) -> proc.Result:
    """Run one child from the main repository; say why in the host log, then raise, if it
    failed.

    Not `proc.run_checked`, for `checkout._checked`'s reason: that would spawn through `proc`
    directly and bypass the seam the caller handed in. The raise is the same one, built from
    the same `Result`.

    The log line comes first and the exception second, and they are not the same text. The
    line is composed for `HOST_LOG` through `proc.quote` — redacted, first line of stderr only
    — because the run log outlives the console. `proc.ProcessError`'s own message is
    deliberately raw and is a traceback the dispatching developer reads at the moment of
    failure; forwarding `str(exc)` anywhere else is the leak `proc` warns about.
    """
    result = run(argv, timeout=timeout, cwd=repo_root, env=git_env(), stdin_text=stdin_text)
    if not result.ok:
        emit(proc.quote(result, destination=proc.Destination.HOST_LOG))
        raise proc.ProcessError(result)
    return result


def _count(
    *,
    start: str,
    branch: str,
    repo_root: Path,
    emit: Callable[[str], None],
    run: proc.Runner,
) -> int:
    """How many commits `branch` has past `start`, as a number.

    Checked rather than tolerated: a `rev-list` that could not answer is not zero commits, and
    reading it as zero would turn a broken repository into a quiet "nothing to land" — the run
    would report success and the work would sit in the main repository unpushed.
    """
    result = _checked(
        count_argv(start=start, branch=branch),
        timeout=TIMEOUT_SECONDS,
        repo_root=repo_root,
        emit=emit,
        run=run,
    )
    counted = result.stdout.strip()
    if not counted.isdigit():
        # `rev-list --count` exiting 0 with something that is not a number is bessemer's own
        # bug or a git that changed under it, and either way the gate cannot be decided.
        raise ValueError(f"git rev-list --count did not answer with a number: {counted!r}")
    return int(counted)
