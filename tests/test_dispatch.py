"""Tests for the assembly: the guard sequence, the lock, the lifecycle, and the ledger line.

Three tiers, as F3 README decision 2 draws them. The pure ones — slug derivation, the
notification argv, the two generated prompts, the base chain — are plain unit tests. Everything
else is **tier 2: a whole dispatch driven through a recording double at the proc seam**, which
is not mocking an assumption: `bessemer/proc.py` is the single boundary (ADR 0002), so the
recorded argv stream *is* dispatch's external effect surface.

`tests/guard.py` denies `docker`, `gh` and `osascript` to this suite, so the double is the only
way these paths run at all. What it records is argv, the text on stdin, the deadline, the
working directory and the environment — cwd because "no git argv is ever run inside the
checkout" cannot be asserted without it (tier-2 rider 2).

**The refusal assertions are made on both channels** (F3 decision 6.1). A refused dispatch has
to leave a live run alone, and argv alone cannot see a file write — so every refusal test
asserts the recorded stream stops where it should *and* that the tree is byte-identical
afterwards.

**Security-shaped assertions are hand-written literals** (tier-2 rider 1): the `docker run`
argv, the `--cap-drop ALL` that precedes every cap-add, the FF-only salvage refspec, the
absences. A test that built its expectation from the module would agree with a mutation of it.
"""

import contextlib
import json
import os
import shutil
import signal
import unittest
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final
from unittest import mock

from bessemer import (
    config,
    container,
    dispatch,
    gc,
    ledger,
    proc,
    prompts,
    resume,
    status,
    stream,
)
from bessemer.config import Config
from bessemer.outcome import Resolved
from tests import gitenv

BRANCH: Final = "agent/work"
SLUG: Final = "agent-work"
NAME: Final = "bessemer-agent-work"
IMAGE: Final = "bessemer-agent"
BASE: Final = "origin/main"
SPEC: Final = "10-dispatch.md"
TOKEN_NAME: Final = "CLAUDE_CODE_OAUTH_TOKEN"
TOKEN: Final = "sk-ant-oat-notarealtokenbutitlookslikeone"

BASE_SHA: Final = "1c0ffee1c0ffee1c0ffee1c0ffee1c0ffee1c0ff"
BOUNDARY: Final = "2b0bacafe2b0bacafe2b0bacafe2b0bacafe2b0b"
PREVIOUS_TIP: Final = "3decafbad3decafbad3decafbad3decafbad3dec"
REMOTE_URL: Final = "git@github.com:HireAnEsquire/bessemer.git"
PR_URL: Final = "https://github.com/HireAnEsquire/bessemer/pull/10"
IDENTITY: Final = "Dev Eloper"

CREDENTIAL_URL: Final = f"https://x-access-token:{TOKEN}@github.com/HireAnEsquire/bessemer.git"
"""What git and gh echo while contacting a remote — the stderr that may reach the host log and
nothing else. A literal, for `tests/test_landing.py`'s reason: the suite may not contact a
remote, so credential-bearing text is written rather than provoked."""

VERDICT_TOKEN: Final = "<verdict>approved</verdict>"
NEEDS_WORK_FOOTER: Final = (
    "⚠️ Review: needs-work after 3 round(s) — read the task log before reviewing."
)
"""Restated whole (pin `:1497`, F3 decision 6.6). The review-capped run **still lands**, and
this is the sentence its pull request carries — a reworded footer is a changed contract."""

CONFIG_TOML: Final = f'base = "{BASE}"\nimage = "{IMAGE}"\n'

PASS_ARGV: Final = (
    "docker",
    "exec",
    "-i",
    NAME,
    "timeout",
    "900",
    "claude",
    "--dangerously-skip-permissions",
    "-p",
    "--output-format",
    "stream-json",
    "--verbose",
)
"""What every agent pass runs, hand-written (tier-2 rider 1, and this file's own docstring).

`-i` is what puts the prompt on stdin rather than in an argument, `timeout` is the second word
*inside* the container rather than a wrapper on the host, and the six `claude` words are the
provider invocation the pin spells at `:1099`. Built from `passes.pass_argv` this assertion
would agree with a mutation of it instead of catching one.
"""


def transcript(text: str, *, ok: bool = True) -> tuple[str, ...]:
    """A minimal real stream-json transcript, `tests/test_passes.py`'s helper and its reason:
    `run_pass` renders it through `bessemer.stream`, and a fake capture would let broken
    plumbing pass."""
    lines = [
        json.dumps(
            {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
        )
    ]
    if ok:
        lines.append(json.dumps({"type": "result", "result": text}))
    return tuple(lines)


def said(stdout: str = "", *, returncode: int = 0, stderr: str = "") -> proc.Result:
    """A scripted answer. Argv is filled in by the double from what was really asked."""
    return proc.Result(argv=("scripted",), returncode=returncode, stdout=stdout, stderr=stderr)


@dataclass(frozen=True)
class Attempt:
    """One scripted pass: what the exec exited and what it wrote."""

    returncode: int = 0
    lines: tuple[str, ...] = ()
    idles: int = 0


@dataclass(frozen=True)
class Call:
    """One recorded call at either seam: what ran, with what deadline, from where, and fed what.

    `cwd` is here because the security question "was any git argv run inside the checkout"
    cannot be asked without it (F3 decision 2's tier-2 rider 2).
    """

    argv: tuple[str, ...]
    stdin: str | None = None
    timeout: float | None = None
    cwd: Path | None = None
    env: Mapping[str, str] | None = None


DEFAULT_ANSWERS: Final[Mapping[str, tuple[proc.Result, ...]]] = {
    "git show-ref": (said(),),
    "git symbolic-ref": (said("some-other-branch\n"),),
    "git fetch": (said(),),
    "git rev-parse --verify": (said(f"{BASE_SHA}\n"),),
    "git rev-parse": (said(f"{PREVIOUS_TIP}\n"),),
    "git remote": (said(f"{REMOTE_URL}\n"),),
    "git merge-base": (said(f"{BOUNDARY}\n"),),
    "git clone": (said(),),
    "git config": (said(f"{IDENTITY}\n"),),
    "git rev-list": (said("2\n"),),
    "git push": (said(),),
    # Two answers, because one argv asks two questions. The in-flight guard wants "nothing is
    # running under this name"; `passes._alive` wants "the container I just started is still
    # there". Same `docker ps -q -f name=^…$` both times, told apart by order and by nothing
    # else — which is exactly how a dispatch experiences them.
    "docker ps": (said(""), said("c0ffee\n")),
    "docker image": (said(),),
    "docker run": (said("c0ffeeface\n"),),
    "docker exec": (said(),),
    "docker rm": (said(),),
    "gh pr view": (said(""),),
    "gh pr create": (said(f"{PR_URL}\n"),),
    "gh pr edit": (said(),),
    "osascript": (said(),),
}
"""What each child says when a test has not scripted it otherwise.

Keyed by an argv prefix of one, two or three words, longest first, so a test overrides exactly
the question it is about. The last answer for a key repeats once its list runs out — a run asks
`git rev-list --count` twice and `docker rm` twice, and neither difference is ever the subject.
"""


class Double:
    """Both proc seams, one recorder. Never spawns, never sleeps, never reads a clock.

    A thin table over the real `proc.Result` (ADR 0003's tier-2 rider 2), never a parallel
    implementation of proc semantics.

    **Named limit, in the house style of `tests/guard.py`: an unscripted child succeeds
    silently, and an unscripted pass succeeds with a generic transcript.** For a dispatch that
    is benign in one direction only — an unscripted `rev-list` would read as zero commits and
    land nothing — so every test that turns on a count or a verdict scripts it, and every test
    that scripts a failure asserts the call count too, which is what says the run stopped where
    it was supposed to.

    `git clone` **creates its target directory**, because three of the things under test are
    about a checkout existing: `container.start`'s mountpoint pre-creation, the cleanup
    salvage's `is_dir` gate, and the removal that follows a salvage which advanced.
    """

    def __init__(
        self,
        *attempts: Attempt,
        answers: Mapping[str, tuple[proc.Result, ...]] | None = None,
    ) -> None:
        self.calls: list[Call] = []
        self.answers = {**DEFAULT_ANSWERS, **(answers or {})}
        self.consumed: dict[str, int] = {}
        self.attempts = list(attempts)
        self.slept: list[float] = []
        self.polls: list[float] = []
        self.now = 0.0

    def _scripted(self, argv: Sequence[str]) -> proc.Result:
        for width in (3, 2, 1):
            key = " ".join(argv[:width])
            answers = self.answers.get(key)
            if answers is None:
                continue
            index = self.consumed.get(key, 0)
            self.consumed[key] = min(index + 1, len(answers) - 1)
            return answers[index]
        return said()

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stdin_text: str | None = None,
    ) -> proc.Result:
        self.calls.append(Call(tuple(argv), stdin_text, timeout, cwd, env))
        if argv[:2] == ["git", "clone"]:
            Path(argv[-1]).mkdir(parents=True, exist_ok=True)
        scripted = self._scripted(list(argv))
        return proc.Result(
            argv=tuple(argv),
            returncode=scripted.returncode,
            stdout=scripted.stdout,
            stderr=scripted.stderr,
        )

    def stream(
        self,
        argv: Sequence[str],
        *,
        stdin_text: str,
        consume: Callable[[Iterable[str]], None],
        idle: Callable[[], None],
        poll: float,
    ) -> proc.Result:
        self.calls.append(Call(tuple(argv), stdin_text))
        attempt = self.attempts.pop(0) if self.attempts else Attempt(lines=transcript("done"))
        self.polls.append(poll)
        for _ in range(attempt.idles):
            self.now += poll
            idle()
        consume(iter(attempt.lines))
        return proc.Result(
            argv=tuple(argv), returncode=attempt.returncode, stdout="", stderr=""
        )

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    @property
    def argvs(self) -> list[tuple[str, ...]]:
        return [call.argv for call in self.calls]

    @property
    def words(self) -> list[str]:
        """Every argument of every recorded call, flattened. For the absence assertions."""
        return [word for call in self.calls for word in call.argv]

    def matching(self, *prefix: str) -> list[tuple[str, ...]]:
        return [argv for argv in self.argvs if argv[: len(prefix)] == prefix]


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file under `root`, by relative path. The other channel a refusal is asserted on.

    Bytes rather than mtimes: a rotation, a lock write and a truncation all change content, and
    a comparison that also turned on timestamps would fail for reasons that are not the
    subject.
    """
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@dataclass
class Console:
    """Where `console` goes. The operator's terminal, which is also `say`'s first channel."""

    printed: list[str] = field(default_factory=list)

    def __call__(self, line: str) -> None:
        self.printed.append(line)

    @property
    def text(self) -> str:
        return "\n".join(self.printed)


def installed(*programs: str) -> contextlib.AbstractContextManager[object]:
    """`shutil.which` answering for `programs` and for nothing else.

    Preflight asks it three times and the notification a fourth, and the answers must not
    depend on whether the machine running the suite has Docker — which is the same constraint
    `tests/guard.py` exists for, one level up from spawning.
    """
    present = set(programs)

    def which(name: str, *args: object, **kwargs: object) -> str | None:
        return f"/usr/bin/{name}" if name in present else None

    return mock.patch.object(shutil, "which", side_effect=which)


ALL_PRESENT: Final = ("docker", "gh", "osascript")


class DispatchTest(unittest.TestCase):
    """A real adapter tree in a temporary directory, and no daemon anywhere near it."""

    def setUp(self) -> None:
        holder = TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name).resolve()
        self.adapter = self.root / config.ADAPTER_DIR
        self.specs = self.adapter / "specs"
        self.specs.mkdir(parents=True)
        (self.adapter / config.COMMITTED_FILE).write_text(CONFIG_TOML, encoding="utf-8")
        (self.adapter / container.SECRETS_FILE).write_text(
            f"{TOKEN_NAME}={TOKEN}\n", encoding="utf-8"
        )
        (self.adapter / container.HOOK_FILE).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.spec = self.specs / SPEC
        self.spec.write_text("# 10 — dispatch\n", encoding="utf-8")
        self.console = Console()

    @property
    def logs(self) -> Path:
        return self.adapter / dispatch.LOGS_DIR

    @property
    def locks(self) -> Path:
        return self.adapter / dispatch.LOCKS_DIR

    @property
    def checkouts(self) -> Path:
        return self.adapter / dispatch.CHECKOUTS_DIR

    @property
    def log(self) -> Path:
        return self.logs / f"{SLUG}{dispatch.LOG_SUFFIX}"

    @property
    def lock(self) -> Path:
        return self.locks / f"{SLUG}{dispatch.LOCK_SUFFIX}"

    @property
    def work(self) -> Path:
        return self.checkouts / SLUG

    @property
    def ledger_path(self) -> Path:
        return ledger.central_ledger_path(self.specs)

    def config(self) -> Config:
        loaded = config.load(start=self.root)
        assert isinstance(loaded, Resolved)
        return loaded.value

    def dispatch(
        self,
        double: Double,
        *,
        spec: str | Path = SPEC,
        branch: str = BRANCH,
        base: str | None = None,
        env: Mapping[str, str] | None = None,
        pid: int | None = None,
        present: Sequence[str] = ALL_PRESENT,
    ) -> dispatch.RunOutcome:
        """One dispatch with both seams on `double`. Bound to the protocols so a double that
        stopped matching them is a type error here rather than a surprise in the module."""
        runner: proc.Runner = double
        streamer: proc.Streamer = double.stream
        with installed(*present):
            return dispatch.dispatch(
                cfg=self.config(),
                repo_root=self.root,
                start=self.root,
                spec=str(spec),
                branch=branch,
                base=base,
                env=dict(env or {}),
                pid=pid if pid is not None else os.getpid(),
                console=self.console,
                run=runner,
                streamer=streamer,
                sleep=double.sleep,
                clock=double.clock,
                now=lambda: datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC),
            )

    def approved(self) -> Double:
        """A double scripted for the happy path: implement, an approving review, a
        description."""
        return Double(
            Attempt(lines=transcript("implemented")),
            Attempt(lines=transcript(f"looks right {VERDICT_TOKEN}")),
            Attempt(lines=transcript("## Overview\n\nIt does the thing.")),
        )

    def refused(self, double: Double) -> str:
        """Run a dispatch that must be refused, and return the refusal's message."""
        with self.assertRaises(dispatch.Refusal) as raised:
            self.dispatch(double)
        return str(raised.exception)


class SlugTest(unittest.TestCase):
    """`run.sh:1134`'s pipeline, case by case. Pure, and the identity everything else is keyed
    on — a slug that changed would rename a live run's container, log and lock at once."""

    def test_a_plain_branch_name_is_itself(self) -> None:
        self.assertEqual(dispatch.slug_for("work"), "work")

    def test_a_slash_becomes_a_dash(self) -> None:
        self.assertEqual(dispatch.slug_for("agent/work"), "agent-work")

    def test_uppercase_is_folded(self) -> None:
        self.assertEqual(dispatch.slug_for("Agent/Work-2"), "agent-work-2")

    def test_a_run_of_separators_collapses_to_one(self) -> None:
        """`tr -cs` squeezes as it translates, so `a//b` is `a-b` and never `a--b`."""
        self.assertEqual(dispatch.slug_for("a//b__c"), "a-b-c")

    def test_leading_and_trailing_separators_are_stripped(self) -> None:
        self.assertEqual(dispatch.slug_for("/work/"), "work")

    def test_a_name_of_nothing_but_separators_slugs_to_nothing(self) -> None:
        """Upstream's answer too, and unreachable: `git check-ref-format` refuses such a
        branch, and the branch has been proven to exist before this is called."""
        self.assertEqual(dispatch.slug_for("///"), "")

    def test_the_container_name_is_the_prefix_status_scans_for(self) -> None:
        """The rendezvous, asserted against `bessemer.status`'s own constant rather than
        against a literal here — F2 debt 3 is exactly this agreement."""
        self.assertEqual(f"{status.CONTAINER_PREFIX}{dispatch.slug_for(BRANCH)}", NAME)


class NotifyArgvTest(unittest.TestCase):
    """The notification, pinned as a literal (pin `:1063–1073`)."""

    def test_the_argv_is_the_script_then_the_title_then_the_body(self) -> None:
        self.assertEqual(
            dispatch.notify_argv(title="bessemer: work", body="approved"),
            [
                "osascript",
                "-e",
                "on run argv\n"
                "      display notification (item 2 of argv) with title (item 1 of argv)\n"
                "    end run",
                "bessemer: work",
                "approved",
            ],
        )

    def test_the_script_text_never_carries_the_title_or_the_body(self) -> None:
        """The decision, not a formatting choice: AppleScript is a language, and a branch name
        carrying a quote would otherwise end the string it was written into."""
        hostile = 'work" & (do shell script "echo pwned") & "'
        argv = dispatch.notify_argv(title=hostile, body=hostile)
        self.assertNotIn(hostile, argv[2])
        self.assertEqual(argv[3:], [hostile, hostile])


class PromptTest(unittest.TestCase):
    """The two prompts this module generates. Literals, because a prompt is a control."""

    def test_the_implement_prompt_is_the_template_then_the_two_generated_lines(self) -> None:
        self.assertEqual(
            dispatch.implement_prompt(
                "TEMPLATE\n\n", branch=BRANCH, boundary=BOUNDARY, previous_tip=BOUNDARY
            ),
            "TEMPLATE\n"
            f"Your task spec is the file /spec.md — read it first.\n"
            f"You are on branch {BRANCH}. The task's diff boundary (its merge-base with the "
            f"PR base) is commit {BOUNDARY}.",
        )

    def test_a_branch_already_past_the_boundary_gets_the_build_on_them_line(self) -> None:
        prompt = dispatch.implement_prompt(
            "TEMPLATE", branch=BRANCH, boundary=BOUNDARY, previous_tip=PREVIOUS_TIP
        )
        self.assertTrue(
            prompt.endswith(
                f"The branch already contains commits from earlier runs of this task — read "
                f"them (git log {BOUNDARY}..HEAD) and build on them; do not redo or revert "
                f"finished work."
            )
        )

    def test_the_spec_mount_is_the_containers_and_not_a_second_literal(self) -> None:
        self.assertIn(container.SPEC_MOUNT, dispatch.SPEC_HEADLINE.format(mount="/spec.md"))
        self.assertEqual(container.SPEC_MOUNT, "/spec.md")

    def test_the_spec_sentence_is_kept_even_though_the_template_already_says_it(self) -> None:
        """The ruling issue 10 owed, stated rather than left to a silent shipping decision.

        The packaged implement template's SPEC heading already names `/spec.md`, and the
        dispatcher's generated preamble repeats it (pin `:1476–1477`). **Kept**: hae's F7
        overrides restore its template text byte for byte, and the parity gate compares
        *assembled* prompts — so dropping the repetition would make every F3 prompt differ
        from the pin's for a tidiness nobody asked for. Asserted here so the duplication is a
        decision with a test on it rather than an oversight.
        """
        template = prompts.load(prompts.IMPLEMENT)
        self.assertIn(container.SPEC_MOUNT, template)
        assembled = dispatch.implement_prompt(
            template, branch=BRANCH, boundary=BOUNDARY, previous_tip=BOUNDARY
        )
        self.assertEqual(assembled.count(container.SPEC_MOUNT), 2)

    def test_the_description_prompt_names_the_boundary(self) -> None:
        self.assertEqual(
            dispatch.description_prompt("TEMPLATE\n", boundary=BOUNDARY),
            f"TEMPLATE\nDescribe the work on this branch: every commit after {BOUNDARY}.",
        )


class BaseChainTest(DispatchTest):
    """The six-rung chain at three depths (F3 decision 4), and the line each prints.

    The three the issue names: the flag, the ledger, and `origin/HEAD` auto-detect. The last
    one is the only test in this file that runs a real `git` — F3 decision 2's tier-2 rider 5,
    where the question is git's own behaviour and the answer is a real temporary repository.
    Still no docker and still no network: `origin` is added as a name and `origin/HEAD` is set
    by hand, which is what the resolver actually reads.
    """

    def git(self, *arguments: str) -> proc.Result:
        return proc.run(
            ["git", *arguments], cwd=self.root, timeout=15.0, env=gitenv.fixture_env()
        )

    def chain(self, *, flag: str | None) -> str:
        return dispatch.base_ref(
            flag=flag,
            branch=BRANCH,
            cfg=self.config(),
            start=self.root,
            specs_dir=self.specs,
            console=self.console,
        )

    def test_the_flag_wins_and_says_nothing(self) -> None:
        self.assertEqual(self.chain(flag="origin/release"), "origin/release")
        self.assertEqual(self.console.printed, [])

    def test_the_ledger_beats_config_and_the_choice_is_printed(self) -> None:
        """The named consequence's mitigation: an environment-configured base loses to the
        branch's recorded history, so the loss is on the console of every run."""
        self.ledger_path.write_text(
            json.dumps({"branch": BRANCH, "base": "origin/recorded"}) + "\n", encoding="utf-8"
        )
        self.assertEqual(self.chain(flag=None), "origin/recorded")
        self.assertEqual(
            self.console.printed,
            [
                f"bessemer: --base omitted — using '{BRANCH}' branch's last recorded base "
                f"from runs.jsonl: origin/recorded"
            ],
        )

    def test_with_no_record_the_configured_base_is_used_and_said_so(self) -> None:
        self.assertEqual(self.chain(flag=None), BASE)
        self.assertEqual(
            self.console.printed,
            [
                f"bessemer: --base omitted — no ledger record for '{BRANCH}', "
                f"defaulting to {BASE}"
            ],
        )

    def test_a_ledger_line_for_another_branch_does_not_answer_this_one(self) -> None:
        self.ledger_path.write_text(
            json.dumps({"branch": "someone-else", "base": "origin/theirs"}) + "\n",
            encoding="utf-8",
        )
        self.assertEqual(self.chain(flag=None), BASE)

    def test_the_bottom_rung_is_origin_head_auto_detect(self) -> None:
        """The third depth, against a real repository. With no `base` anywhere — no flag, no
        ledger line, no config key — the chain falls all the way through to the resolver's
        `origin/HEAD` read, and the `origin/` prefix is stripped on the way out."""
        (self.adapter / config.COMMITTED_FILE).write_text(
            f'image = "{IMAGE}"\n', encoding="utf-8"
        )
        self.assertTrue(self.git("init", "-q", "-b", "trunk").ok)
        self.assertTrue(self.git("remote", "add", "origin", REMOTE_URL).ok)
        self.assertTrue(
            self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk").ok
        )

        self.assertEqual(self.chain(flag=None), "trunk")
        self.assertEqual(
            self.console.printed,
            [
                f"bessemer: --base omitted — no ledger record for '{BRANCH}', "
                f"defaulting to trunk"
            ],
        )

    def test_a_chain_that_runs_out_is_a_refusal_carrying_the_resolvers_reason(self) -> None:
        """The bottom rung is `origin/HEAD` auto-detect, and this tree is not a work tree, so
        the chain reaches the end. `resolve` owns the sentence; dispatch refuses on it."""
        (self.adapter / config.COMMITTED_FILE).write_text(
            f'image = "{IMAGE}"\n', encoding="utf-8"
        )
        with self.assertRaises(dispatch.Refusal) as raised:
            self.chain(flag=None)
        self.assertIn("could not determine the base ref", str(raised.exception))


class GuardTest(DispatchTest):
    """Each refusal, its ported message, and where in the recorded stream it stops.

    The order is F3 decision 6.1's, and it is asserted as a *sequence* rather than one refusal
    at a time: a guard that moved would still refuse, and would refuse after touching something
    the guard before it exists to protect.
    """

    def test_a_spec_that_is_not_there_refuses_before_any_child_runs(self) -> None:
        double = Double()
        with self.assertRaises(dispatch.Refusal) as raised:
            self.dispatch(double, spec="nope.md")
        self.assertEqual(str(raised.exception), f"spec not found: {self.specs / 'nope.md'}")
        self.assertEqual(double.argvs, [])

    def test_a_branch_that_does_not_exist_refuses_after_one_git_question(self) -> None:
        double = Double(answers={"git show-ref": (said(returncode=1),)})
        self.assertEqual(
            self.refused(double),
            f"branch '{BRANCH}' doesn't exist — create it first: "
            f"git branch '{BRANCH}' [<start>]",
        )
        self.assertEqual(
            double.argvs, [("git", "show-ref", "--verify", "--quiet", f"refs/heads/{BRANCH}")]
        )

    def test_a_protected_branch_is_refused_through_resume_is_protected(self) -> None:
        """Never a second `case master|main` (ADR 0003): the predicate is `bessemer.resume`'s,
        and this asserts through it — `MAIN` and ` main ` are the same branch to it and to
        this refusal."""
        for name in ("main", "master", "MAIN", " main "):
            with self.subTest(branch=name):
                self.assertTrue(resume.is_protected(name))
                double = Double()
                with self.assertRaises(dispatch.Refusal) as raised:
                    self.dispatch(double, branch=name, base="origin/somewhere-else")
                self.assertEqual(
                    str(raised.exception),
                    f"refusing '{name}' as --branch — protected branches are PR targets "
                    f"(--base), never pushed to",
                )

    def test_a_base_naming_the_branch_refuses_with_the_origin_prefix_stripped(self) -> None:
        double = Double()
        with self.assertRaises(dispatch.Refusal) as raised:
            self.dispatch(double, base=f"origin/{BRANCH}")
        self.assertEqual(
            str(raised.exception),
            "--branch and --base name the same ref — the PR would target itself",
        )

    def test_a_branch_checked_out_here_refuses_after_the_symbolic_ref(self) -> None:
        double = Double(answers={"git symbolic-ref": (said(f"{BRANCH}\n"),)})
        self.assertEqual(
            self.refused(double),
            f"'{BRANCH}' is checked out in this repo — git refuses fetches/resets into a "
            f"checked-out branch; switch away first",
        )
        self.assertEqual(
            double.argvs[-1], ("git", "symbolic-ref", "--quiet", "--short", "HEAD")
        )

    def test_a_missing_docker_refuses_with_the_pins_sentence(self) -> None:
        double = Double()
        with self.assertRaises(dispatch.Refusal) as raised:
            self.dispatch(double, present=("gh", "osascript"))
        self.assertEqual(str(raised.exception), "docker not found")

    def test_a_missing_gh_refuses_with_the_pins_sentence(self) -> None:
        double = Double()
        with self.assertRaises(dispatch.Refusal) as raised:
            self.dispatch(double, present=("docker", "osascript"))
        self.assertEqual(str(raised.exception), "gh not found")

    def test_no_credential_anywhere_refuses_and_names_the_file(self) -> None:
        (self.adapter / container.SECRETS_FILE).write_text("", encoding="utf-8")
        self.assertEqual(
            self.refused(Double()),
            f"no Claude credential — run `claude setup-token` and put it in "
            f"{self.adapter / container.SECRETS_FILE}",
        )

    def test_a_credential_only_exported_is_a_loud_warning_and_not_a_refusal(self) -> None:
        """F3 issue 09's two-channel finding, at the one place it reaches a dispatch. The pin's
        union is what the refusal turns on — deliberately, F3 README's note — so this machine
        runs, and the operator is told on the console that its agent will not authenticate."""
        (self.adapter / container.SECRETS_FILE).write_text("", encoding="utf-8")
        self.dispatch(self.approved(), env={TOKEN_NAME: TOKEN})
        self.assertIn("is exported in this shell but not set in", self.console.text)
        self.assertNotIn(TOKEN, self.console.text)

    def test_an_unconfigured_image_refuses_before_asking_docker_about_one(self) -> None:
        (self.adapter / config.COMMITTED_FILE).write_text(
            f'base = "{BASE}"\n', encoding="utf-8"
        )
        double = Double()
        message = self.refused(double)
        self.assertIn("no image configured", message)
        self.assertEqual(double.matching("docker", "image"), [])

    def test_an_image_docker_cannot_find_refuses_with_the_build_hint(self) -> None:
        double = Double(answers={"docker image": (said(returncode=1),)})
        self.assertEqual(
            self.refused(double),
            f"image '{IMAGE}' missing — build it from .bessemer/Dockerfile and tag it",
        )
        self.assertEqual(double.argvs[-1], ("docker", "image", "inspect", IMAGE))

    def test_the_guard_sequence_runs_in_the_order_decision_six_one_states(self) -> None:
        """spec → base chain → branch exists → protected → base≠branch → not-checked-out →
        preflight → mkdir → fetch → BASE_SHA → merge-base → in-flight guard.

        Asserted over the recorded stream as one list: each guard that spawns appears exactly
        once and in this order, and the in-flight `docker ps` is last — nothing may be written
        before it answers. What still refuses there is a lock `gc.classify_liveness` (ADR 0004)
        cannot read — a container answering `Up` no longer does, on its own.
        """
        self.locks.mkdir(parents=True)
        self.lock.mkdir()
        double = Double()
        self.assertEqual(
            self.refused(double),
            f"cannot tell whether '{BRANCH}' has a live dispatcher — the lock ({self.lock}) "
            f"could not be read; refusing rather than guessing",
        )
        self.assertEqual(
            double.argvs,
            [
                ("git", "show-ref", "--verify", "--quiet", f"refs/heads/{BRANCH}"),
                ("git", "symbolic-ref", "--quiet", "--short", "HEAD"),
                ("docker", "image", "inspect", IMAGE),
                ("git", "fetch", "origin", "--quiet"),
                ("git", "rev-parse", "--verify", f"{BASE}^{{commit}}"),
                ("git", "remote", "get-url", "origin"),
                ("git", "merge-base", BASE_SHA, f"refs/heads/{BRANCH}"),
                ("docker", "ps", "-q", "-f", f"name=^{NAME}$"),
            ],
        )

    def test_a_live_lock_refuses_and_names_the_pid_holding_it(self) -> None:
        self.locks.mkdir(parents=True)
        self.lock.write_text(f"{os.getpid()}\n", encoding="utf-8")
        double = Double()
        self.assertEqual(
            self.refused(double),
            f"another bessemer run (pid {os.getpid()}) is already working '{BRANCH}' — "
            f"wait for it or kill it",
        )
        self.assertEqual(double.matching("docker", "ps"), [])

    def test_a_stale_lock_is_cleared_rather_than_refused(self) -> None:
        """Only a reader can tell a crashed run from a live one, which is why the guard above
        the atomic create still exists."""
        self.locks.mkdir(parents=True)
        self.lock.write_text("2147483646\n", encoding="utf-8")
        outcome = self.dispatch(self.approved())
        self.assertEqual(outcome.branch, BRANCH)
        self.assertFalse(self.lock.exists())

    def test_an_orphaned_container_is_reclaimed_rather_than_refused(self) -> None:
        """The tracer's own case (ADR 0004): the container stayed `Up` after its dispatcher
        died. `Up` alone no longer refuses — `gc.classify_liveness` (issue 13a) reads the dead
        pid in the lock and calls the pair `ORPHAN`, so this proceeds: the stale cleanup a few
        lines below removes both, and the run log names it."""
        self.locks.mkdir(parents=True)
        self.lock.write_text("2147483646\n", encoding="utf-8")
        self.checkouts.mkdir(parents=True)
        (self.work / "src").mkdir(parents=True)
        (self.work / "src" / "file.py").write_text("orphaned work\n", encoding="utf-8")
        double = Double(
            Attempt(lines=transcript("implemented")),
            Attempt(lines=transcript(f"looks right {VERDICT_TOKEN}")),
            Attempt(lines=transcript("## Overview\n\nIt does the thing.")),
            answers={"docker ps": (said("c0ffee\n"),)},
        )

        outcome = self.dispatch(double)

        self.assertEqual(outcome.branch, BRANCH)
        self.assertIn(("docker", "rm", "-f", "-v", NAME), double.argvs)
        self.assertFalse((self.work / "src" / "file.py").exists())
        self.assertIn(
            f"'{BRANCH}' is an orphan: its dispatcher is gone — reclaiming container {NAME} "
            f"and checkout {self.work} before this run starts",
            self.log.read_text(encoding="utf-8"),
        )

    def test_an_unreadable_lock_refuses_rather_than_guessing(self) -> None:
        """ADR 0004's third answer: nothing can be said about who owns the branch, and a
        dispatch's do-nothing direction is refusal — the opposite of `gc`'s, which keeps the
        artifact instead of listing it."""
        self.locks.mkdir(parents=True)
        self.lock.mkdir()
        double = Double(answers={"docker ps": (said("c0ffee\n"),)})
        self.assertEqual(
            self.refused(double),
            f"cannot tell whether '{BRANCH}' has a live dispatcher — the lock ({self.lock}) "
            f"could not be read; refusing rather than guessing",
        )


class RefusedDispatchTest(DispatchTest):
    """A refusal touches nothing — asserted on **both** channels (F3 decision 6.1).

    The tree is snapshotted with a live run's own state in it: a log with a previous run's
    lines, a lock naming a live pid, and a checkout directory. A refusal that rotated the log,
    truncated it, wrote a lock or removed a container would show up as a byte difference here
    even though the argv stream cannot see three of the four.
    """

    def live_run(self) -> dict[str, bytes]:
        self.logs.mkdir(parents=True)
        self.locks.mkdir(parents=True)
        self.checkouts.mkdir(parents=True)
        self.log.write_text("a previous run said things\n", encoding="utf-8")
        self.lock.write_text(f"{os.getpid()}\n", encoding="utf-8")
        (self.work / "src").mkdir(parents=True)
        (self.work / "src" / "file.py").write_text("work in progress\n", encoding="utf-8")
        return snapshot(self.root)

    def test_the_in_flight_refusal_writes_nothing_and_spawns_nothing_after_the_guard(
        self,
    ) -> None:
        before = self.live_run()
        double = Double()

        self.assertIn("already working", self.refused(double))

        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(self.log.with_suffix(dispatch.ROTATED_SUFFIX).exists())
        # The lock guard answers from a file read, so the stream stops before `docker ps` —
        # and a refusal must never reach `docker rm`, which would tear down the live run.
        self.assertEqual(double.matching("docker", "ps"), [])
        self.assertEqual(double.matching("docker", "rm"), [])

    def test_an_unverifiable_lock_writes_nothing_either(self) -> None:
        self.live_run()
        # Unreadable rather than absent: the second layer of the guard is the subject, so the
        # first must not answer, and a container answering `Up` no longer refuses by itself
        # (ADR 0004) — only `gc.classify_liveness`'s UNVERIFIABLE does, and it must not tear
        # down a run it could not identify either.
        self.lock.unlink()
        self.lock.mkdir()
        before = snapshot(self.root)
        double = Double(answers={"docker ps": (said("c0ffee\n"),)})

        self.assertIn("cannot tell whether", self.refused(double))

        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(double.argvs[-1], ("docker", "ps", "-q", "-f", f"name=^{NAME}$"))
        self.assertEqual(double.matching("docker", "rm"), [])

    def test_an_early_refusal_does_not_even_create_the_directories(self) -> None:
        before = snapshot(self.root)
        double = Double(answers={"git show-ref": (said(returncode=1),)})

        self.assertIn("doesn't exist", self.refused(double))

        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(self.logs.exists())
        self.assertFalse(self.locks.exists())
        self.assertFalse(self.checkouts.exists())


class LockTest(DispatchTest):
    """The atomic create, and the race it closes (F3 decision 6.3, recorded divergence)."""

    def test_two_dispatches_racing_one_branch_leave_the_loser_naming_the_winner(self) -> None:
        """The divergence, driven rather than described (F3 decision 6.3).

        Upstream checks the lock at `:1148` and writes it at `:1150`, so two dispatches that
        both looked before either wrote would both proceed. Here the second `O_EXCL` create
        fails, the loser re-reads the file, and it reports the pid that won — which is what
        the guard would have said had it been a microsecond later.

        Driven at `_Lock` rather than through two whole dispatches because that is where the
        window is: by the time both are inside `acquire`, the guard has already let both past,
        which is precisely the state upstream leaves them in.
        """
        self.locks.mkdir(parents=True)
        winner = dispatch._Lock(path=self.lock, pid=os.getpid())
        loser = dispatch._Lock(path=self.lock, pid=os.getpid() + 1)

        winner.acquire(BRANCH)
        with self.assertRaises(dispatch.Refusal) as raised:
            loser.acquire(BRANCH)

        self.assertEqual(
            str(raised.exception),
            f"another bessemer run (pid {os.getpid()}) is already working '{BRANCH}' — "
            f"wait for it or kill it",
        )
        self.assertEqual(self.lock.read_text(encoding="utf-8"), f"{os.getpid()}\n")

    def test_a_lock_whose_owner_is_gone_is_taken_over_rather_than_refused(self) -> None:
        """Upstream's `echo $$ > "$lock"` overwrites unconditionally, and a machine that lost
        power mid-run must not need a file deleted by hand. Liveness is `status.pid_alive`."""
        self.locks.mkdir(parents=True)
        self.lock.write_text("2147483646\n", encoding="utf-8")

        dispatch._Lock(path=self.lock, pid=4242).acquire(BRANCH)

        self.assertEqual(self.lock.read_text(encoding="utf-8"), "4242\n")

    def test_a_lock_holding_no_pid_at_all_is_taken_over_too(self) -> None:
        """The artifact a run killed between the `open` and the `write` leaves behind. Refusing
        on it would make the lock demand by hand exactly the cleanup it exists to spare — and
        it would refuse forever, since nothing else ever removes it."""
        self.locks.mkdir(parents=True)
        for content in ("", "\n", "not-a-pid\n"):
            with self.subTest(content=content):
                self.lock.write_text(content, encoding="utf-8")

                dispatch._Lock(path=self.lock, pid=4242).acquire(BRANCH)

                self.assertEqual(self.lock.read_text(encoding="utf-8"), "4242\n")

    def test_a_dispatch_gets_past_an_empty_lock_left_by_a_killed_run(self) -> None:
        """The same thing end to end, because the two layers of the rule have to agree: the
        guard lets an ownerless lock through and `acquire` takes it, rather than one passing it
        to the other to refuse."""
        self.locks.mkdir(parents=True)
        self.lock.write_text("", encoding="utf-8")

        outcome = self.dispatch(self.approved(), pid=4242)

        self.assertEqual(outcome.verdict.token, "approved")
        self.assertFalse(self.lock.exists())

    def test_a_lock_that_cannot_be_read_still_names_the_run_it_could_not_name(self) -> None:
        self.locks.mkdir(parents=True)
        self.lock.mkdir()
        lock = dispatch._Lock(path=self.lock, pid=7)
        with self.assertRaises(dispatch.Refusal) as raised:
            lock.acquire(BRANCH)
        self.assertIn("(pid ?)", str(raised.exception))

    def test_the_lock_holds_this_dispatchs_pid_and_is_removed_at_the_end(self) -> None:
        self.dispatch(self.approved(), pid=98765)
        self.assertFalse(self.lock.exists())

    def test_the_log_rotates_once_and_only_when_it_had_something_in_it(self) -> None:
        self.logs.mkdir(parents=True)
        self.log.write_text("last run\n", encoding="utf-8")

        self.dispatch(self.approved())

        rotated = self.log.with_suffix(dispatch.ROTATED_SUFFIX)
        self.assertEqual(rotated.read_text(encoding="utf-8"), "last run\n")
        self.assertIn("run start: mode=continue", self.log.read_text(encoding="utf-8"))

    def test_an_empty_log_is_not_rotated_into_a_generation_of_nothing(self) -> None:
        self.logs.mkdir(parents=True)
        self.log.write_text("", encoding="utf-8")
        self.dispatch(self.approved())
        self.assertFalse(self.log.with_suffix(dispatch.ROTATED_SUFFIX).exists())


def expected_run_argv(*, work: Path, adapter: Path, spec: Path) -> tuple[str, ...]:
    """The `docker run` argv, hand-written (tier-2 rider 1).

    Restated here rather than built from `container.run_argv` so that a mutation of the
    builder is caught by the assembly's own test as well as by the container module's:
    `--cap-drop ALL` first and unconditional, the two capabilities core adds for the sudo
    transition and no others, both machine limits, `:ro` on the hook and the spec, the
    credential as an explicit `-e`, and the image last.
    """
    return (
        "docker",
        "run",
        "-d",
        "--name",
        NAME,
        "--cap-drop",
        "ALL",
        "--cap-add",
        "SETUID",
        "--cap-add",
        "SETGID",
        "--pids-limit",
        "2048",
        "--memory",
        "8g",
        "-v",
        f"{work}:/workspace",
        "-w",
        "/workspace",
        "-v",
        f"{adapter}/setup.sh:/bessemer/setup.sh:ro",
        "-v",
        f"{spec}:/spec.md:ro",
        "-e",
        "BASH_DEFAULT_TIMEOUT_MS=300000",
        "-e",
        "BASH_MAX_TIMEOUT_MS=300000",
        "-e",
        f"{TOKEN_NAME}={TOKEN}",
        IMAGE,
    )


class HappyPathTest(DispatchTest):
    """One approved run, asserted as the whole argv sequence and the whole final state."""

    def sequence(self) -> list[tuple[str, ...]]:
        return [
            ("git", "show-ref", "--verify", "--quiet", f"refs/heads/{BRANCH}"),
            ("git", "symbolic-ref", "--quiet", "--short", "HEAD"),
            ("docker", "image", "inspect", IMAGE),
            ("git", "fetch", "origin", "--quiet"),
            ("git", "rev-parse", "--verify", f"{BASE}^{{commit}}"),
            ("git", "remote", "get-url", "origin"),
            ("git", "merge-base", BASE_SHA, f"refs/heads/{BRANCH}"),
            ("docker", "ps", "-q", "-f", f"name=^{NAME}$"),
            ("docker", "rm", "-f", "-v", NAME),
            ("git", "rev-parse", f"refs/heads/{BRANCH}"),
            (
                "git",
                "clone",
                "--quiet",
                "--no-hardlinks",
                "--branch",
                BRANCH,
                str(self.root),
                str(self.work),
            ),
            ("git", "-C", str(self.work), "remote", "set-url", "origin", REMOTE_URL),
            ("git", "config", "user.name"),
            ("git", "-C", str(self.work), "config", "user.name", IDENTITY),
            ("git", "config", "user.email"),
            ("git", "-C", str(self.work), "config", "user.email", IDENTITY),
            expected_run_argv(work=self.work, adapter=self.adapter, spec=self.spec),
            ("docker", "exec", NAME, "sudo", "/usr/bin/bash", "/bessemer/setup.sh"),
            PASS_ARGV,
            PASS_ARGV,
            (
                "git",
                "fetch",
                "--quiet",
                str(self.work),
                f"refs/heads/{BRANCH}:refs/heads/{BRANCH}",
            ),
            PASS_ARGV,
            ("git", "rev-list", "--count", f"{BOUNDARY}..refs/heads/{BRANCH}"),
            ("git", "rev-list", "--count", f"{PREVIOUS_TIP}..refs/heads/{BRANCH}"),
            (
                "git",
                "push",
                "--quiet",
                "-u",
                "origin",
                f"refs/heads/{BRANCH}:refs/heads/{BRANCH}",
            ),
            (
                "gh",
                "pr",
                "view",
                BRANCH,
                "--json",
                "url,state",
                "--jq",
                'select(.state == "OPEN") | .url',
            ),
            (
                "gh",
                "pr",
                "create",
                "--draft",
                "--base",
                "main",
                "--head",
                BRANCH,
                "--title",
                f"[bessemer] {BRANCH}",
                "--body-file",
                "-",
            ),
            (
                "osascript",
                "-e",
                dispatch.NOTIFY_SCRIPT,
                f"bessemer: {BRANCH}",
                f"approved — PR: {PR_URL}",
            ),
            (
                "git",
                "fetch",
                "--quiet",
                str(self.work),
                f"refs/heads/{BRANCH}:refs/heads/{BRANCH}",
            ),
            ("docker", "rm", "-f", "-v", NAME),
        ]

    def test_the_whole_run_is_this_argv_sequence_and_nothing_else(self) -> None:
        double = self.approved()
        outcome = self.dispatch(double)
        self.assertEqual(double.argvs, self.sequence())
        self.assertEqual(outcome.verdict.token, "approved")
        self.assertEqual(outcome.pull_request.pr_url, PR_URL)
        self.assertEqual(outcome.boundary, BOUNDARY)

    def test_the_final_state_is_a_log_no_lock_no_checkout_and_a_ledger_line(self) -> None:
        outcome = self.dispatch(self.approved())

        self.assertTrue(self.log.is_file())
        self.assertFalse(self.lock.exists())
        self.assertFalse(self.work.exists())
        self.assertIsNone(outcome.ledger_error)
        self.assertEqual(len(ledger.read_ledger(self.ledger_path)), 1)

    def test_the_ledger_line_round_trips_as_json_with_every_field_a_string(self) -> None:
        """Defect 9 armour (F3 decision 6.5). The defect stays in `bessemer.ledger` per F2
        decision 8; what lands here is that nothing this module puts in a record can fail
        `json.dumps` — so every field is `str()`-converted, and the line reads back."""
        self.dispatch(self.approved())

        raw = self.ledger_path.read_text(encoding="utf-8").strip()
        record = json.loads(raw)
        self.assertEqual(record["branch"], BRANCH)
        self.assertEqual(record["base"], BASE)
        self.assertEqual(record["spec"], SPEC)
        self.assertEqual(record["outcome"], "approved")
        self.assertEqual(record["pr_url"], PR_URL)
        self.assertEqual(record["task_dir"], str(self.specs))
        for key in ("branch", "base", "spec", "outcome", "pr_url", "task_dir"):
            self.assertIsInstance(record[key], str)

    def test_path_typed_inputs_reach_the_ledger_as_strings_end_to_end(self) -> None:
        """The tier-2 test decision 6.5 names by hand: a dispatch whose spec arrives as a
        `Path` rather than as a string, driven all the way to a ledger line that serialises."""
        outcome = self.dispatch(self.approved(), spec=self.spec)

        self.assertEqual(outcome.slug, SLUG)
        record = json.loads(self.ledger_path.read_text(encoding="utf-8").strip())
        self.assertEqual(record["spec"], SPEC)
        self.assertEqual(json.loads(json.dumps(record)), record)

    def test_the_source_dir_recorded_is_the_specs_own_parent(self) -> None:
        """`run.sh:826–832`'s recorded fact, at the one-off arm: the feature folder is F4's,
        and a one-off run records the directory its spec sits in."""
        self.dispatch(self.approved())
        record = ledger.read_ledger(self.ledger_path)[0]
        self.assertEqual(record["task_dir"], str(self.specs))

    def test_no_git_argv_is_ever_run_from_inside_the_checkout(self) -> None:
        """The security question the double records `cwd` for. The checkout is reached by `-C`
        or by URL — an argument the stream shows — and never by working directory."""
        double = self.approved()
        self.dispatch(double)
        for call in double.calls:
            if call.cwd is not None:
                self.assertFalse(
                    Path(call.cwd).is_relative_to(self.checkouts),
                    f"{call.argv} ran inside the checkout",
                )

    def test_the_absences_hold_over_every_recorded_argv(self) -> None:
        """Asserted alongside the presences, F1's rule at full force: no force push, no merge,
        no published port, and the salvage refspec carries no `+`."""
        double = self.approved()
        self.dispatch(double)

        self.assertFalse([word for word in double.words if word.startswith("--force")])
        self.assertEqual(double.matching("gh", "pr", "merge"), [])
        started = next(argv for argv in double.argvs if argv[:2] == ("docker", "run"))
        # ADR 0001's no-published-port rule, asserted where a port could be published. `-p` is
        # a word of the `claude` argv too — the provider's non-interactive flag — so a check
        # over every recorded word would be about the wrong flag entirely.
        self.assertNotIn("-p", started)
        self.assertNotIn("--publish", started)
        for argv in double.matching("git", "fetch", "--quiet"):
            self.assertEqual(argv[-1], f"refs/heads/{BRANCH}:refs/heads/{BRANCH}")

    def test_the_body_reaches_gh_on_stdin_and_appears_in_no_argument(self) -> None:
        double = self.approved()
        self.dispatch(double)
        bodies = [
            call.stdin
            for call in double.calls
            if call.argv[:2] == ("gh", "pr") and call.stdin is not None
        ]
        self.assertEqual(len(bodies), 1)
        body = bodies[0]
        self.assertIn("It does the thing.", body)
        self.assertIn("Review: approved (round 1/3).", body)
        self.assertIn(f"AI-authored via bessemer (spec: `{SPEC}`)", body)
        self.assertNotIn("It does the thing.", " ".join(double.words))

    def test_the_prompt_rides_stdin_and_the_spec_text_never_enters_an_argv(self) -> None:
        self.spec.write_text("# a spec with 'quotes' and $(danger)\n", encoding="utf-8")
        double = self.approved()
        self.dispatch(double)
        self.assertNotIn("$(danger)", " ".join(double.words))
        prompts_sent = [
            call.stdin for call in double.calls if call.argv[:3] == ("docker", "exec", "-i")
        ]
        self.assertEqual(len(prompts_sent), 3)
        assert prompts_sent[0] is not None
        self.assertIn(f"You are on branch {BRANCH}", prompts_sent[0])

    def test_the_log_carries_the_run_start_line_the_banner_and_the_six_steps(self) -> None:
        self.dispatch(self.approved())
        text = self.log.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("2026-08-06T12:00:00+00:00 == bessemer [agent-work] "))
        self.assertIn(f"run start: mode=continue boundary={BOUNDARY}", text)
        for number, label in enumerate(
            (
                f"checkout: clone of '{BRANCH}'",
                f"container: {NAME} ({IMAGE})",
                "setup: the adapter's hook",
                "implement: claude pass",
                "review: up to 3 round(s)",
                "land: push + draft PR",
            ),
            start=1,
        ):
            self.assertIn(f"({number}/6) {label}", text)
        self.assertIn("cleaned up (container removed;", text)

    def test_an_existing_open_pull_request_is_updated_rather_than_duplicated(self) -> None:
        double = Double(
            Attempt(lines=transcript("implemented")),
            Attempt(lines=transcript(VERDICT_TOKEN)),
            Attempt(lines=transcript("described")),
            answers={"gh pr view": (said(f"{PR_URL}\n"),)},
        )
        outcome = self.dispatch(double)
        self.assertTrue(outcome.pull_request.updated)
        self.assertEqual(double.matching("gh", "pr", "create"), [])
        self.assertEqual(len(double.matching("gh", "pr", "edit")), 1)


class FailureScenarioTest(DispatchTest):
    """The four end-to-end failure paths F3 decision 6.6 owns, one scripted dispatch each."""

    def test_a_nonzero_setup_hook_aborts_the_run_and_surfaces_its_log(self) -> None:
        double = Double(
            answers={"docker exec": (said("hook output", returncode=3, stderr="boom"),)}
        )

        with self.assertRaises(dispatch.RunFailed) as raised:
            self.dispatch(double)

        self.assertIn("the setup hook /bessemer/setup.sh exited 3", str(raised.exception))
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("hook output", text)
        # Aborted at the hook: no pass was ever streamed, nothing was pushed, no PR touched.
        self.assertEqual(double.matching("docker", "exec", "-i"), [])
        self.assertEqual(double.matching("git", "push"), [])
        self.assertEqual(double.matching("gh"), [])
        self.assertEqual(ledger.read_ledger(self.ledger_path), [])
        self.assertFalse(self.lock.exists())
        self.assertFalse(self.work.exists())

    def test_an_implement_pass_that_fails_three_times_ends_the_run_after_salvage(self) -> None:
        double = Double(Attempt(returncode=1), Attempt(returncode=1), Attempt(returncode=1))

        with self.assertRaises(dispatch.RunFailed) as raised:
            self.dispatch(double)

        self.assertEqual(
            str(raised.exception), "implement pass failed after 3 attempt(s) — aborting the run"
        )
        self.assertEqual(len(double.matching("docker", "exec", "-i")), 3)
        self.assertEqual(double.matching("git", "push"), [])
        self.assertEqual(double.matching("gh"), [])
        self.assertEqual(ledger.read_ledger(self.ledger_path), [])
        # Salvage ran in the cleanup and the checkout is gone with it.
        self.assertEqual(
            double.argvs[-2],
            (
                "git",
                "fetch",
                "--quiet",
                str(self.work),
                f"refs/heads/{BRANCH}:refs/heads/{BRANCH}",
            ),
        )
        self.assertFalse(self.work.exists())
        self.assertFalse(self.lock.exists())

    def test_a_review_capped_at_needs_work_still_lands_with_the_pinned_footer(self) -> None:
        """F3 decision 6.6: reaching the cap is an outcome, not a failure. The run pushes,
        opens the draft pull request, records `needs-work`, and the body carries the pin's
        sentence."""
        double = Double(
            Attempt(lines=transcript("implemented")),
            *(Attempt(lines=transcript("still not right")) for _ in range(3)),
            Attempt(lines=transcript("described")),
        )

        outcome = self.dispatch(double)

        self.assertFalse(outcome.verdict.approved)
        self.assertEqual(outcome.verdict.token, "needs-work")
        self.assertEqual(len(double.matching("docker", "exec", "-i")), 5)
        body = next(
            call.stdin for call in double.calls if call.argv[:3] == ("gh", "pr", "create")
        )
        assert body is not None
        self.assertIn(NEEDS_WORK_FOOTER, body)
        self.assertEqual(ledger.read_ledger(self.ledger_path)[0]["outcome"], "needs-work")

    def test_a_container_that_dies_mid_pass_aborts_without_another_exec(self) -> None:
        """Never retry into a container something else removed (pin `:1122–1126`). The liveness
        probe is the last docker question of the run, and no exec follows it."""
        double = Double(
            Attempt(returncode=1),
            Attempt(lines=transcript("never reached")),
            answers={"docker ps": (said(""), said(""))},
        )

        with self.assertRaises(dispatch.RunFailed):
            self.dispatch(double)

        execs = [
            i for i, argv in enumerate(double.argvs) if argv[:3] == ("docker", "exec", "-i")
        ]
        probes = [i for i, argv in enumerate(double.argvs) if argv[:2] == ("docker", "ps")]
        self.assertEqual(len(execs), 1)
        self.assertGreater(probes[-1], execs[-1])
        self.assertIn(f"container {NAME} is gone", self.log.read_text(encoding="utf-8"))
        self.assertEqual(ledger.read_ledger(self.ledger_path), [])

    def test_a_salvage_that_cannot_fast_forward_keeps_the_checkout_and_says_so(self) -> None:
        """The other half of the FF-only rule: removing the checkout would discard work that
        exists nowhere else, so it stays and the message names it."""
        double = Double(
            Attempt(lines=transcript("implemented")),
            Attempt(lines=transcript(VERDICT_TOKEN)),
            answers={
                "git fetch --quiet": (said(returncode=1, stderr="non-fast-forward"),),
            },
        )

        with self.assertRaises(dispatch.RunFailed) as raised:
            self.dispatch(double)

        self.assertIn("could not fast-forward", str(raised.exception))
        self.assertTrue(self.work.is_dir())
        self.assertIn("leaving", self.log.read_text(encoding="utf-8"))
        self.assertEqual(double.matching("git", "push"), [])

    def test_a_zero_commit_landing_still_appends_a_ledger_line_with_no_url(self) -> None:
        """F3 decision 8.4, and its distinction from 6.4: nothing to push is still a landing,
        so the line is written with an empty `pr_url`; a hard failure writes nothing at all."""
        double = Double(
            Attempt(lines=transcript("implemented")),
            Attempt(lines=transcript(VERDICT_TOKEN)),
            Attempt(lines=transcript("described")),
            answers={"git rev-list": (said("0\n"),)},
        )

        outcome = self.dispatch(double)

        self.assertFalse(outcome.pull_request.landed)
        self.assertEqual(double.matching("git", "push"), [])
        self.assertEqual(double.matching("gh"), [])
        record = ledger.read_ledger(self.ledger_path)[0]
        self.assertIsNone(record["pr_url"])
        self.assertEqual(record["outcome"], "approved")


class QuotabilityTest(DispatchTest):
    """The stderr invariant, over the recorded stream **and** the composed strings.

    ADR 0001's most easily weakened invariant (F3 decision 2's tier-2 rider 3). Here it is a
    check that no child's stderr reaches a notification argument, a pull-request body or an
    assembled prompt — the three agent-visible destinations — while still reaching the host log,
    which is the one reader entitled to it.
    """

    def failing_run(self) -> Double:
        return Double(
            answers={
                "docker exec": (said("", returncode=3, stderr=f"fatal: {CREDENTIAL_URL}"),),
            }
        )

    def test_no_stderr_fragment_reaches_a_notification_argument(self) -> None:
        double = self.failing_run()
        with self.assertRaises(dispatch.RunFailed):
            self.dispatch(double)

        notifications = double.matching("osascript")
        self.assertEqual(len(notifications), 1)
        for word in notifications[0]:
            self.assertNotIn(TOKEN, word)
            self.assertNotIn("fatal:", word)
        self.assertEqual(notifications[0][-1], "failed: setup: the adapter's hook — see log")

    def test_the_same_stderr_does_reach_the_host_log_redacted(self) -> None:
        """The other half, so this is a policy and not a blanket ban: the operator's own log is
        the one destination `proc.quote` lets stderr into, and the credential in a remote URL is
        redacted on the way (`bessemer.redact`)."""
        with self.assertRaises(dispatch.RunFailed):
            self.dispatch(self.failing_run())

        text = self.log.read_text(encoding="utf-8")
        self.assertIn("fatal:", text)
        self.assertIn("<redacted>@", text)
        self.assertNotIn(TOKEN, text)

    def test_no_stderr_reaches_a_pull_request_body_or_a_prompt(self) -> None:
        double = Double(
            Attempt(lines=transcript("implemented")),
            Attempt(lines=transcript(VERDICT_TOKEN)),
            Attempt(lines=transcript("described")),
            answers={
                "gh pr view": (said("", returncode=1, stderr=f"fatal: {CREDENTIAL_URL}"),)
            },
        )
        self.dispatch(double)

        for call in double.calls:
            if call.stdin is not None:
                self.assertNotIn(TOKEN, call.stdin)
                self.assertNotIn("fatal:", call.stdin)


class RoundTripTest(DispatchTest):
    """F2 debt 3: dispatch writes where status reads, proven by rendering one over the other.

    Real files in a temporary tree, a faked proc seam, and no docker anywhere — which is what
    F3 decision 2's rider 4 says this test is: tier 2 rather than tier 3. The tracer re-proves
    it live against a real run.
    """

    def render(self, rows: list[str]) -> str:
        return status.render_status(
            specs_dir=self.specs,
            logs_dir=self.adapter / status.LOGS_DIR,
            locks_dir=self.adapter / status.LOCKS_DIR,
            docker_rows=rows,
            docker_down=False,
            limit=10,
        )

    def test_status_renders_the_run_dispatch_wrote(self) -> None:
        self.dispatch(self.approved())

        rendered = self.render([f"{NAME}\tUp 2 minutes"])

        self.assertIn(SLUG, rendered)
        self.assertIn(BRANCH, rendered)
        self.assertIn("approved", rendered)

    def test_the_container_name_status_scans_for_is_the_one_dispatch_starts(self) -> None:
        """The rendezvous asserted against `status.CONTAINER_PREFIX` by name, per the issue."""
        double = self.approved()
        self.dispatch(double)
        started = next(argv for argv in double.argvs if argv[:2] == ("docker", "run"))
        name = started[started.index("--name") + 1]
        self.assertTrue(name.startswith(status.CONTAINER_PREFIX))
        self.assertEqual(name, f"{status.CONTAINER_PREFIX}{SLUG}")

    def test_a_live_run_is_visible_while_it_is_still_running(self) -> None:
        """The lock and the log exist from the moment the run starts, which is what makes
        `bessemer status` answer during a dispatch and not only after one."""
        seen: dict[str, bool] = {}

        double = self.approved()
        original = double.stream

        def watching(*args: object, **kwargs: object) -> proc.Result:
            seen["lock"] = self.lock.is_file()
            seen["log"] = self.log.is_file()
            seen["checkout"] = self.work.is_dir()
            return original(*args, **kwargs)  # type: ignore[arg-type]

        with mock.patch.object(double, "stream", watching):
            self.dispatch(double)

        self.assertEqual(seen, {"lock": True, "log": True, "checkout": True})

    def test_moving_the_logs_directory_breaks_the_round_trip(self) -> None:
        """The mutation the issue asks for, run and restored. `status` finds a run's log under
        `status.LOGS_DIR`; dispatch writes it under the same name because it *is* the same
        name. Point one of them somewhere else and the round trip stops working — which is
        what says this test would notice.

        Both renders are taken mid-dispatch (the same `stream` hook
        `test_a_live_run_is_visible_while_it_is_still_running` uses), while the lock this
        process itself holds is still on disk: under ADR 0004 a run is in-flight only while
        its dispatcher's pid is live, and a lock read after `self.dispatch` returns has
        already been released, which would make the row an orphan — with no log line at all
        — for a reason that has nothing to do with `LOGS_DIR`."""
        rows = [f"{NAME}\tUp 2 minutes"]
        captured: dict[str, str] = {}

        double = self.approved()
        original = double.stream

        def watching(*args: object, **kwargs: object) -> proc.Result:
            captured["agreeing"] = self.render(rows)
            with mock.patch.object(status, "LOGS_DIR", "somewhere-else"):
                captured["elsewhere"] = status.render_status(
                    specs_dir=self.specs,
                    logs_dir=self.adapter / status.LOGS_DIR,
                    locks_dir=self.adapter / status.LOCKS_DIR,
                    docker_rows=rows,
                    docker_down=False,
                    limit=10,
                )
            # Restored, still mid-dispatch: a mutation test that left the constant moved
            # would take every later test in this class with it.
            self.assertEqual(status.LOGS_DIR, "logs")
            captured["restored"] = self.render(rows)
            return original(*args, **kwargs)  # type: ignore[arg-type]

        with mock.patch.object(double, "stream", watching):
            self.dispatch(double)

        # Agreeing: the `tail -f` path status prints is the file dispatch actually wrote.
        self.assertIn(f"tail -f {self.log}", captured["agreeing"])
        self.assertTrue(self.log.is_file())

        # Disagreeing: point status at another directory and it hands the operator a path to
        # a file that is not there. The round trip is the two names being one name.
        self.assertNotIn(f"tail -f {self.log}", captured["elsewhere"])
        self.assertIn(
            f"tail -f {self.adapter / 'somewhere-else'}/{SLUG}.log", captured["elsewhere"]
        )

        self.assertEqual(captured["restored"], captured["agreeing"])

    def test_gc_sees_no_orphan_after_a_run_that_finished(self) -> None:
        items = gc.collect_gc_items(
            checkouts_dir=self.adapter / gc.CHECKOUTS_DIR,
            locks_dir=self.adapter / status.LOCKS_DIR,
            docker_rows=[],
            docker_down=False,
        )
        self.assertEqual(items, [])

        self.dispatch(self.approved())

        after = gc.collect_gc_items(
            checkouts_dir=self.adapter / gc.CHECKOUTS_DIR,
            locks_dir=self.adapter / status.LOCKS_DIR,
            docker_rows=[],
            docker_down=False,
        )
        self.assertEqual(after, [])


class SignalTest(DispatchTest):
    """`SIGINT`/`SIGTERM` become an exception, so one `finally` serves every exit path."""

    def test_a_signal_inside_the_context_raises_and_the_handlers_are_restored(self) -> None:
        before = signal.getsignal(signal.SIGINT)
        with self.assertRaises(dispatch.Interrupted) as raised:
            with dispatch.raising_signals():
                os.kill(os.getpid(), signal.SIGINT)
        self.assertEqual(str(raised.exception), "interrupted by SIGINT")
        self.assertIs(signal.getsignal(signal.SIGINT), before)

    def test_an_interrupt_mid_pass_still_salvages_removes_and_unlocks(self) -> None:
        """The whole point of the conversion: upstream's bare signal death skipped `cleanup`
        and left a container, a checkout and a lock behind."""

        double = self.approved()

        def interrupt(*args: object, **kwargs: object) -> proc.Result:
            raise dispatch.Interrupted("interrupted by SIGTERM")

        with mock.patch.object(double, "stream", interrupt):
            with self.assertRaises(dispatch.Interrupted):
                self.dispatch(double)

        self.assertFalse(self.lock.exists())
        self.assertFalse(self.work.exists())
        self.assertEqual(double.argvs[-1], ("docker", "rm", "-f", "-v", NAME))
        self.assertIn("cleaned up", self.log.read_text(encoding="utf-8"))


class ModuleShapeTest(unittest.TestCase):
    """Claims this module's docstrings make that are cheaper to hold than to re-argue."""

    def test_the_rendezvous_names_are_the_ones_status_and_gc_use(self) -> None:
        self.assertIs(dispatch.LOGS_DIR, status.LOGS_DIR)
        self.assertIs(dispatch.LOCKS_DIR, status.LOCKS_DIR)
        self.assertIs(dispatch.CHECKOUTS_DIR, gc.CHECKOUTS_DIR)
        self.assertIs(dispatch.CONTAINER_PREFIX, status.CONTAINER_PREFIX)

    def test_the_capture_used_is_the_stream_modules(self) -> None:
        """One provider-contract surface, one module (ADR 0001): dispatch renders nothing
        itself and reads a verdict out of nothing itself."""
        self.assertTrue(hasattr(stream, "filtered"))
        self.assertNotIn("filtered", vars(dispatch))


if __name__ == "__main__":
    unittest.main()
