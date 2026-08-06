"""Tests for landing: the push gate, the pull-request probe, and the body that is composed.

Nothing here spawns. `git` is on `tests/guard.py`'s allowlist and `gh` is not, so the
recording `Double` at the proc seam is not a convenience — it is the only way these paths
run at all under the suite's no-daemon, no-network constraint (ADR 0003's tier-2 rider 2).
It records **everything the seam is handed**: argv, timeout, cwd, environment, and the text
on stdin, because the body riding stdin rather than an argument is the contract.

**The security-shaped assertions are hand-written literals** (ADR 0003's tier-2 rider 1).
The push argv, the three gh argvs, the four sentences of the body and both footers are
spelled out here as well as in `bessemer.landing`; a test that built its expectation from
the module would agree with a mutation of it instead of catching one. The absences are
asserted beside the presences: no `--force*` in any recorded argv ever, no `pr merge`
anywhere, and `--draft` on every create.
"""

import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final

from bessemer import landing, proc

GIT: Final = "git"
"""The program whose calls carry no stdin. Restated rather than imported, so the assertion
about which children are fed a body does not read its answer off the module under test."""

BRANCH: Final = "agent/work"
BASE: Final = "origin/main"
BOUNDARY: Final = "1c0ffee1c0ffee1c0ffee1c0ffee1c0ffee1c0ff"
PREVIOUS_TIP: Final = "2b0bacafe2b0bacafe2b0bacafe2b0bacafe2b0b"

SPEC_NAME: Final = "08-landing.md"

PR_URL: Final = "https://github.com/HireAnEsquire/bessemer/pull/7"

TOKEN: Final = "ghp_thisisnotarealtokenbutitlookslikeone"
CREDENTIAL_URL: Final = f"https://x-access-token:{TOKEN}@github.com/HireAnEsquire/bessemer.git"
"""What git and gh echo while contacting a remote. The stderr that may never reach a body.

A literal for `tests/test_redact.py`'s reason: the suite may not contact a remote, so the
credential-bearing text is written rather than provoked.
"""

DESCRIPTION: Final = "## Overview\n\nIt does the thing.\n"
APPROVED_FOOTER: Final = "Review: approved (round 1/3)."
NEEDS_WORK_FOOTER: Final = (
    "⚠️ Review: needs-work after 3 round(s) — read the task log before reviewing."
)
"""Both footers, restated whole (pin :1497–1498, :1521; F3 decision 6.6).

`bessemer.passes` owns them and this module composes whatever it is handed, so these are
here as the two real values a body is built around — a reworded footer is a changed contract
at both ends, and `tests/test_passes.py` pins the templates themselves.
"""

ATTRIBUTION: Final = (
    "AI-authored via bessemer (spec: `08-landing.md`). "
    "Draft until the dispatching dev reviews it."
)
"""The pin's sentence with the product token renamed (F3 decision 8.1), written whole.

The sentence a human reads under every bessemer pull request, and the one that says the
diff is not a person's. Restated here rather than formatted from the module's template.
"""

DESCRIPTION_FAILED: Final = "_(description generation failed — see the task log)_"

APPROVED_BODY: Final = (
    "## Overview\n"
    "\n"
    "It does the thing.\n"
    "\n"
    "---\n"
    "Review: approved (round 1/3).\n"
    "AI-authored via bessemer (spec: `08-landing.md`). "
    "Draft until the dispatching dev reviews it."
)
"""One whole body, byte for byte, as the pin's `printf` assembles it (run.sh:1574–1575).

Written out rather than concatenated from the parts so that the *separators* are pinned too:
the blank line and the `---` rule between the description and the verdict, and the single
newline between the verdict and the attribution.
"""


def counted(count: int) -> proc.Result:
    """What `git rev-list --count` answers with: a number and a newline."""
    return proc.Result(argv=("git",), returncode=0, stdout=f"{count}\n", stderr="")


def said(stdout: str = "", *, returncode: int = 0, stderr: str = "") -> proc.Result:
    """A scripted answer for any other child. Argv is filled in by the double."""
    return proc.Result(argv=("scripted",), returncode=returncode, stdout=stdout, stderr=stderr)


@dataclass(frozen=True)
class Call:
    """One recorded call: what ran, with what deadline, from where, and what it was fed.

    `stdin` is why this type exists rather than a bare argv list — "the body reached gh on
    stdin and appears in no argument" is two assertions over one record.
    """

    argv: tuple[str, ...]
    timeout: float
    cwd: Path | None
    env: Mapping[str, str] | None
    stdin: str | None


class Double:
    """A `proc.Runner` that records every call and answers from a script. Never spawns.

    A thin table over the real `proc.Result` (ADR 0003's tier-2 rider 2), the shape
    `tests/test_container.py` already uses, plus the stdin slot the landing seam needs.

    **Named limit, in the house style of `tests/guard.py`: running dry answers success with
    empty output.** For landing that is benign in one direction and not the other — an
    unscripted `rev-list` would read as zero commits and land nothing — so every test that
    depends on a count scripts it, and the tests that script a failure assert the call count
    as well, which is what says the run stopped where it was supposed to.
    """

    def __init__(self, *results: proc.Result) -> None:
        self.calls: list[Call] = []
        self.scripted = list(results)

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stdin_text: str | None = None,
    ) -> proc.Result:
        self.calls.append(Call(tuple(argv), timeout, cwd, env, stdin_text))
        if self.scripted:
            scripted = self.scripted.pop(0)
            return proc.Result(
                argv=tuple(argv),
                returncode=scripted.returncode,
                stdout=scripted.stdout,
                stderr=scripted.stderr,
            )
        return proc.Result(argv=tuple(argv), returncode=0, stdout="", stderr="")

    @property
    def argvs(self) -> list[tuple[str, ...]]:
        return [call.argv for call in self.calls]

    @property
    def words(self) -> list[str]:
        """Every argument of every recorded call, flattened. For the leak assertions."""
        return [word for call in self.calls for word in call.argv]

    @property
    def stdins(self) -> list[str]:
        """The text every child that was fed one got, in order."""
        return [call.stdin for call in self.calls if call.stdin is not None]


@dataclass
class Log:
    """The host-side run log. The only destination landing writes to."""

    emitted: list[str] = field(default_factory=list)


class LandingTest(unittest.TestCase):
    """A main repository as a path — nothing here reads or writes one."""

    def setUp(self) -> None:
        holder = TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.repo = Path(holder.name).resolve()
        self.log = Log()

    def land(
        self,
        double: Double,
        *,
        base: str = BASE,
        description: str = DESCRIPTION,
        footer: str = APPROVED_FOOTER,
    ) -> landing.Landing:
        """`land` with the seam on `double` and the log on `self.log`.

        `run` is bound to a `proc.Runner`-annotated name rather than passed inline, so a
        `Double` that stopped matching the protocol is a type error here and not a runtime
        surprise in `bessemer.landing`.
        """
        runner: proc.Runner = double
        return landing.land(
            repo_root=self.repo,
            branch=BRANCH,
            base=base,
            boundary=BOUNDARY,
            previous_tip=PREVIOUS_TIP,
            description=description,
            footer=footer,
            spec=self.repo / ".bessemer" / "specs" / SPEC_NAME,
            emit=self.log.emitted.append,
            run=runner,
        )


class ArgvTest(LandingTest):
    """The four argvs, as literals. Every one is a contract with something outside."""

    def test_the_push_is_plain_and_names_its_refspec_explicitly(self) -> None:
        """No `--force*`, no wildcard, no reliance on push.default: the whole argv.

        `-u` is what makes a later `git status` in the main repository say the branch is
        tracked; the explicit `refs/heads/…:refs/heads/…` is what stops a configured
        `push.default` from deciding which ref this writes.
        """
        self.assertEqual(
            landing.push_argv(branch=BRANCH),
            [
                "git",
                "push",
                "--quiet",
                "-u",
                "origin",
                "refs/heads/agent/work:refs/heads/agent/work",
            ],
        )

    def test_the_push_refspec_carries_no_plus(self) -> None:
        """The leading `+` is the difference between this push and a force-push.

        Restated as its own assertion because it is one character, and because the argv
        above would still read as correct with it.
        """
        self.assertEqual(landing.PUSH_REFSPEC, "refs/heads/{branch}:refs/heads/{branch}")
        self.assertNotIn("+", landing.PUSH_REFSPEC)

    def test_the_probe_asks_gh_for_an_open_pull_requests_url(self) -> None:
        """Ported exactly, `--jq` expression included: the filter is what makes a closed or
        merged pull request answer nothing, and answering nothing is what selects create."""
        self.assertEqual(
            landing.probe_argv(branch=BRANCH),
            [
                "gh",
                "pr",
                "view",
                BRANCH,
                "--json",
                "url,state",
                "--jq",
                'select(.state == "OPEN") | .url',
            ],
        )

    def test_the_edit_takes_the_body_on_stdin(self) -> None:
        self.assertEqual(
            landing.edit_argv(branch=BRANCH),
            ["gh", "pr", "edit", BRANCH, "--body-file", "-"],
        )

    def test_the_create_is_a_draft_against_the_base_without_its_remote(self) -> None:
        """`--draft` and `${BASE#origin/}` in one literal (pin :1583–1584).

        The draft flag is the human merge gate — nothing bessemer builds may open a
        mergeable pull request — and the base arrives spelled `origin/main` because that is
        what `origin/HEAD` resolution answers, while `gh` wants the branch name.
        """
        self.assertEqual(
            landing.create_argv(branch=BRANCH, base=BASE),
            [
                "gh",
                "pr",
                "create",
                "--draft",
                "--base",
                "main",
                "--head",
                BRANCH,
                "--title",
                "[bessemer] agent/work",
                "--body-file",
                "-",
            ],
        )

    def test_a_base_that_is_already_a_branch_name_is_left_alone(self) -> None:
        self.assertIn("main", landing.create_argv(branch=BRANCH, base="main"))
        self.assertNotIn("origin/main", landing.create_argv(branch=BRANCH, base="main"))

    def test_only_a_leading_origin_is_stripped_from_the_base(self) -> None:
        """Prefix removal, as the pin's `${BASE#origin/}` does it — not a substring
        anywhere. A branch named `release/origin/main` targets itself."""
        argv = landing.create_argv(branch=BRANCH, base="release/origin/main")
        self.assertIn("release/origin/main", argv)


class BodyTest(LandingTest):
    """The composed body, pinned as whole sentences. A reworded one is a changed contract."""

    def test_the_whole_body_is_the_pins_layout(self) -> None:
        self.assertEqual(
            landing.body(
                description=DESCRIPTION, footer=APPROVED_FOOTER, spec=Path("x") / SPEC_NAME
            ),
            APPROVED_BODY,
        )

    def test_the_needs_work_footer_is_carried_verbatim(self) -> None:
        """The sentence a human reads before deciding how carefully to review an unattended
        agent's diff. Reword it and this test is the one that says so."""
        composed = landing.body(
            description=DESCRIPTION, footer=NEEDS_WORK_FOOTER, spec=Path(SPEC_NAME)
        )
        self.assertIn(
            "\n---\n⚠️ Review: needs-work after 3 round(s) — read the task log before "
            "reviewing.\n",
            composed,
        )

    def test_the_attribution_names_the_product_and_the_spec(self) -> None:
        composed = landing.body(
            description=DESCRIPTION, footer=APPROVED_FOOTER, spec=Path("a/b") / SPEC_NAME
        )
        self.assertTrue(composed.endswith(ATTRIBUTION), composed)

    def test_the_spec_appears_as_its_basename(self) -> None:
        """`basename` at the pin (run.sh:1576): the dispatching developer's directory
        layout is not something a pull request tells the world about."""
        composed = landing.body(
            description=DESCRIPTION,
            footer=APPROVED_FOOTER,
            spec=Path("/Users/somebody/private/repo/.bessemer/specs") / SPEC_NAME,
        )
        self.assertIn("(spec: `08-landing.md`)", composed)
        self.assertNotIn("somebody", composed)

    def test_a_missing_description_becomes_the_pinned_fallback(self) -> None:
        composed = landing.body(description="", footer=APPROVED_FOOTER, spec=Path(SPEC_NAME))
        self.assertTrue(composed.startswith(DESCRIPTION_FAILED), composed)

    def test_a_description_of_nothing_but_whitespace_is_a_missing_one(self) -> None:
        """The pin's `[ -n "$body" ]` reads a command substitution, which has already
        dropped the trailing newlines — so a pass that emitted only whitespace takes the
        fallback there too, and a body opening on a blank line is not a description."""
        composed = landing.body(description="  \n\n", footer=APPROVED_FOOTER, spec=Path("s.md"))
        self.assertTrue(composed.startswith(DESCRIPTION_FAILED), composed)

    def test_an_empty_footer_still_composes(self) -> None:
        """Not a case F3 reaches — `passes.Verdict` always carries one — but the pin's
        `footer` is an unset variable until a review runs, and a body that raised here
        would turn a missing sentence into a failed landing."""
        composed = landing.body(description=DESCRIPTION, footer="", spec=Path("s.md"))
        self.assertIn("---\n\nAI-authored via bessemer", composed)


class NothingToLandTest(LandingTest):
    """Zero commits past the boundary: no push, no gh, nothing said to the world."""

    def test_no_push_and_no_gh_argv_are_built_at_all(self) -> None:
        double = Double(counted(0), counted(0))
        result = self.land(double)
        self.assertFalse(result.landed)
        self.assertEqual(result.pr_url, "")
        self.assertEqual([argv[0] for argv in double.argvs], ["git", "git"])
        self.assertNotIn("push", double.words)
        self.assertNotIn("gh", double.words)

    def test_the_counts_are_still_reported(self) -> None:
        """The caller's line is "no commits past the boundary", and the ledger's line is
        issue 10's — both read the number rather than recomputing it."""
        result = self.land(Double(counted(0), counted(0)))
        self.assertEqual((result.commits, result.new_commits), (0, 0))

    def test_a_branch_whose_only_commits_predate_this_run_still_lands(self) -> None:
        """Zero *new* commits is not zero commits: a run that failed its first pass after
        an earlier run committed still has something past the boundary, and the pin gates
        on the boundary count alone (run.sh:1541)."""
        double = Double(counted(3), counted(0), said(), said(PR_URL))
        result = self.land(double)
        self.assertTrue(result.landed)
        self.assertEqual(result.new_commits, 0)
        self.assertIn("push", double.words)


class PushTest(LandingTest):
    """The push itself: plain, explicit, and from the main repository."""

    def scripted(self) -> Double:
        """Two commits past the boundary, one of them new, then an open pull request."""
        return Double(counted(2), counted(1), said(), said(PR_URL), said())

    def test_the_recorded_push_is_the_pinned_argv(self) -> None:
        double = self.scripted()
        self.land(double)
        self.assertIn(
            (
                "git",
                "push",
                "--quiet",
                "-u",
                "origin",
                "refs/heads/agent/work:refs/heads/agent/work",
            ),
            double.argvs,
        )

    def test_every_child_runs_from_the_main_repository(self) -> None:
        """The one rule `bessemer.checkout` states and this module inherits: the checkout is
        never a working directory. Landing never names it at all, so the assertion is that
        every call is made from the repository whose config is bessemer's."""
        double = self.scripted()
        self.land(double)
        for call in double.calls:
            self.assertEqual(call.cwd, self.repo, call.argv)

    def test_git_children_get_the_location_stripped_environment(self) -> None:
        """An exported `GIT_DIR` would send this push out of a repository nobody named
        (`resolve.git_env`). gh gets it too — it runs git for its own repository
        questions."""
        double = self.scripted()
        self.land(double)
        for call in double.calls:
            self.assertIsNotNone(call.env, call.argv)
            assert call.env is not None
            self.assertNotIn("GIT_DIR", call.env)
            self.assertNotIn("GIT_WORK_TREE", call.env)

    def test_a_failed_push_aborts_and_never_reaches_gh(self) -> None:
        """Upstream's `set -e` ends the run at the failing push. There is no half a
        landing: a pull request whose branch was never pushed describes commits nobody
        else can see."""
        double = Double(counted(2), counted(1), said(returncode=128, stderr="rejected"))
        with self.assertRaises(proc.ProcessError):
            self.land(double)
        self.assertNotIn("gh", double.words)

    def test_a_failed_count_aborts_before_the_gate(self) -> None:
        """A `rev-list` that could not answer is not zero commits. Reading it as zero would
        turn a broken repository into a silent "nothing to land"."""
        double = Double(said(returncode=128, stderr="bad revision"))
        with self.assertRaises(proc.ProcessError):
            self.land(double)
        self.assertEqual(len(double.calls), 1)


class ProbeTest(LandingTest):
    """Which of the two gh paths a run takes, and what decides it."""

    def test_an_open_pull_request_is_edited_rather_than_duplicated(self) -> None:
        double = Double(counted(2), counted(1), said(), said(PR_URL), said())
        result = self.land(double)
        self.assertTrue(result.updated)
        self.assertEqual(result.pr_url, PR_URL)
        self.assertIn(("gh", "pr", "edit", BRANCH, "--body-file", "-"), double.argvs)
        self.assertNotIn("create", double.words)

    def test_no_open_pull_request_creates_a_draft_one(self) -> None:
        double = Double(counted(2), counted(1), said(), said(""), said(f"{PR_URL}\n"))
        result = self.land(double)
        self.assertFalse(result.updated)
        self.assertEqual(result.pr_url, PR_URL)
        self.assertIn("--draft", double.words)
        self.assertNotIn("edit", double.words)

    def test_a_probe_that_failed_is_read_as_no_pull_request(self) -> None:
        """The pin sends the probe's stderr to `/dev/null` and its failure to `|| true`
        (run.sh:1578): a branch with no pull request is exactly what `gh pr view` exits
        nonzero about, and it is the ordinary first-run case."""
        double = Double(
            counted(2), counted(1), said(), said(returncode=1, stderr="no pull requests found")
        )
        result = self.land(double)
        self.assertFalse(result.updated)
        self.assertIn("--draft", double.words)

    def test_a_failed_probe_says_nothing_to_the_log(self) -> None:
        """`2>/dev/null` at the pin. "No pull request yet" is the normal path, and a line
        about it on every first run is noise in the file an operator reads after a failure."""
        double = Double(
            counted(2), counted(1), said(), said(returncode=1, stderr=CREDENTIAL_URL)
        )
        self.land(double)
        self.assertEqual(self.log.emitted, [])


class BodyOnStdinTest(LandingTest):
    """The body reaches gh as bytes on a pipe, and appears in no argument on either path."""

    def test_the_edit_path_puts_the_body_on_stdin_byte_for_byte(self) -> None:
        double = Double(counted(2), counted(1), said(), said(PR_URL), said())
        self.land(double)
        self.assertEqual(double.stdins, [f"{APPROVED_BODY}\n"])

    def test_the_create_path_puts_the_same_bytes_on_stdin(self) -> None:
        """Byte-identical on both paths, because they are the same body: a run that landed
        by creating and a re-run that landed by editing must not describe themselves
        differently."""
        double = Double(counted(2), counted(1), said(), said(""), said(PR_URL))
        self.land(double)
        self.assertEqual(double.stdins, [f"{APPROVED_BODY}\n"])

    def test_the_body_is_never_a_word_of_any_argv(self) -> None:
        """ADR 0001's rule at its highest-risk call site: the body is text an agent wrote,
        and the pin's own comment says it goes to gh via stdin and never as a shell
        argument (run.sh:1567)."""
        double = Double(counted(2), counted(1), said(), said(""), said(PR_URL))
        self.land(double)
        for word in double.words:
            self.assertNotIn("It does the thing", word)
            self.assertNotIn("AI-authored", word)

    def test_git_is_fed_nothing(self) -> None:
        """Only gh reads a body. A `git push` handed one would be a child holding a pipe
        for no reason, and the assertion is what keeps the stdin slot meaning one thing."""
        double = Double(counted(2), counted(1), said(), said(PR_URL), said())
        self.land(double)
        self.assertTrue(any(call.argv[0] == GIT for call in double.calls))
        for call in double.calls:
            if call.argv[0] == GIT:
                self.assertIsNone(call.stdin, call.argv)


class StderrTest(LandingTest):
    """Where a child's stderr may go: the host log, redacted — and nowhere near the body."""

    def test_a_failing_gh_is_quoted_to_the_host_log_and_redacted(self) -> None:
        """Issue 02's policy, at the destination the pin's `2>>"$log"` names (:1585).

        The credential in the URL is what makes this worth asserting rather than reading:
        the log outlives the console that printed it.
        """
        double = Double(
            counted(2), counted(1), said(), said(""), said(returncode=1, stderr=CREDENTIAL_URL)
        )
        with self.assertRaises(proc.ProcessError):
            self.land(double)
        self.assertEqual(len(self.log.emitted), 1)
        line = self.log.emitted[0]
        self.assertNotIn(TOKEN, line)
        self.assertIn("<redacted>", line)

    def test_no_fragment_of_a_childs_stderr_reaches_the_body(self) -> None:
        """The most easily weakened invariant in ADR 0001, asserted on the composed string.

        A `Result` carrying a credential is in scope while the body is being composed — the
        probe's — and the body is what an off-machine reader sees. The assertion is over
        the bytes that actually reached gh, not over the composition function.
        """
        double = Double(
            counted(2), counted(1), said(), said(returncode=1, stderr=CREDENTIAL_URL)
        )
        self.land(double)
        composed = double.stdins[0]
        self.assertNotIn(TOKEN, composed)
        self.assertNotIn("x-access-token", composed)
        self.assertNotIn("github.com", composed)
        self.assertNotIn("<redacted>", composed)

    def test_a_landing_that_worked_says_nothing_to_the_log(self) -> None:
        """`--quiet` on the push and `>/dev/null` on the edit: the run log carries what went
        wrong, and a successful landing has nothing to add to it."""
        self.land(Double(counted(2), counted(1), said(), said(PR_URL), said()))
        self.assertEqual(self.log.emitted, [])


class AbsenceTest(LandingTest):
    """What this module may never emit. F3 landing has no force-push and no merge."""

    def scenarios(self) -> list[Double]:
        """Every path through `land`, so "in any recorded argv, ever" means something."""
        doubles = [
            Double(counted(0), counted(0)),
            Double(counted(2), counted(1), said(), said(PR_URL), said()),
            Double(counted(2), counted(1), said(), said(""), said(PR_URL)),
        ]
        for double in doubles:
            self.land(double)
        failing = Double(counted(2), counted(1), said(returncode=1, stderr="rejected"))
        with self.assertRaises(proc.ProcessError):
            self.land(failing)
        return [*doubles, failing]

    def test_no_recorded_argv_ever_carries_a_force_flag(self) -> None:
        """`--force-with-lease` arrives with F4's `--hard-reset` and not before. The branch
        in the main repository is user-owned, and continue-mode dispatch appends to it."""
        for double in self.scenarios():
            for word in double.words:
                self.assertFalse(word.startswith("--force"), word)

    def test_nothing_here_can_merge_a_pull_request(self) -> None:
        """The draft state is the human merge gate (ADR 0001). A `gh pr merge` anywhere in
        this module would make every other assertion about drafts decorative."""
        for double in self.scenarios():
            for argv in double.argvs:
                self.assertNotIn("merge", argv)

    def test_every_create_is_a_draft(self) -> None:
        for double in self.scenarios():
            for argv in double.argvs:
                if "create" in argv:
                    self.assertIn("--draft", argv)


class TimeoutTest(LandingTest):
    """Every child gets a deadline, because a wedged one holds the run's lock."""

    def test_no_call_is_made_without_one(self) -> None:
        double = Double(counted(2), counted(1), said(), said(PR_URL), said())
        self.land(double)
        for call in double.calls:
            self.assertGreater(call.timeout, 0.0, call.argv)

    def test_the_transfers_get_longer_than_the_questions(self) -> None:
        """A push moves objects over a network; a `rev-list --count` reads the local
        object store. One deadline for both would be the wrong length for one of them."""
        self.assertGreater(landing.PUSH_TIMEOUT_SECONDS, landing.TIMEOUT_SECONDS)
