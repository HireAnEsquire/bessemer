"""Tests for override resolution and for the text of the packaged default templates.

Two different kinds of test live here, and keeping them apart matters.

**Resolution** is ordinary behaviour, tested against real directory trees under `tempfile`
rather than a mock filesystem: the thing under test is `read_text` against a path, and a mock
would assert that this module calls the function the test told it to call. No test changes the
working directory and none reads the ambient environment — `load` takes the adapter directory
as a parameter precisely so it need not.

**The template text is a set of controls.** The sentences pinned below are the reason the
defaults diverge from the port source at all (ADR 0001's denied-tool rule, ADR 0002's
read-only specs directory, the verdict semantics that make the review loop terminate, and the
PR body's verbatim-output contract that issue 08 composes against). Every one of them is
restated here **by hand**. An assertion that read the sentence out of the template it is
checking would agree with whatever that template happens to say, and these are exactly the
lines an agent editing a prompt would smooth away without noticing what they were for — which
is the defect this file exists to catch, not a typo.

The pinned sentences are matched **whitespace-flattened** (see `flat`). A control that could
only be matched byte-for-byte would fail on a re-wrap of the same words, and a template is
hard-wrapped prose that people re-wrap. Rewrapping is not rewording: every word, every
backtick and every piece of punctuation still has to match, in order.
"""

import os
import re
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Final

from bessemer import prompts

REPO_ROOT: Final = Path(__file__).resolve().parent.parent
"""The repository this adapter belongs to: the directory holding `tests/`.

From `__file__` rather than the working directory, the same way `tests/test_adapter.py`
locates the adapter it checks, so these tests do not care where the runner was started.
"""

OWN_ADAPTER_DIR: Final = REPO_ROOT / ".bessemer"
OWN_OVERRIDE_DIR: Final = OWN_ADAPTER_DIR / "prompts"

TEMPLATE_NAMES: Final = ("implement-prompt.md", "review-prompt.md", "pr-prompt.md")
"""Every template this package ships, restated by hand, in pass order.

Not derived from `prompts.TEMPLATE_NAMES`: a fourth template appearing there — or one of
these three quietly disappearing with its overrides — is a change to what bessemer tells an
agent, and it should cost an edit in a second file. The names double as the override
filenames adopters write, so they are adopter-facing too.
"""

SECTIONS: Final = {
    "implement-prompt.md": (
        "SPEC",
        "ORIENTATION",
        "COMMANDS",
        "IMPLEMENT",
        "VERIFY (must pass before you commit)",
        "COMMIT",
        "RULES",
    ),
    "review-prompt.md": (
        "ROLE",
        "REVIEW",
        "FIX",
        "VERDICT (required, exactly one)",
        "RULES",
    ),
    "pr-prompt.md": (
        "ROLE",
        "STRUCTURE (in this order)",
        "MANUAL TESTING ASSUMPTIONS",
        "FORMAT",
    ),
}
"""The section order each template must have, hand-written, headings verbatim.

This is the list issue 03 owns, and the port source's section structure is the thing F3 keeps
while the stack-specific *content* is excised — so the order is the shape of the port and the
excision is judged against it. Written with the parentheticals included (`VERIFY (must pass
before you commit)`) because the qualifier is part of what the agent reads: "must pass before
you commit" is the difference between a verification step and a suggestion.

`pr-prompt.md`'s `FORMAT` was missing from the issue's first draft of this list and was
restored after being measured against the pin, where it is section four of four. Its contract
is pinned again below, as a control.

`implement-prompt.md` opens on `SPEC` where the pin opens on `# TASK`. That is the port
renaming a heading, ruled host-side at this issue's review: CONTEXT.md retires "task"
entirely — the dispatch unit is a **Run** and the markdown files are **Specs** — and the
same rule already renamed `tasks_dir` to `specs_dir` at port time. Nothing binds the
rename to the pin's wording: prompt defaults sit outside the parity comparison once hae
runs its overrides.
"""

HAE_MARKERS: Final = (
    "django",
    "yarn",
    "hireanesquire",
    "mlaglobal",
    "manage.py",
    "agentbox",
    "hae-api",
    "shell_plus",
    "docker exec",
    "makemigrations",
    "postgis",
    "runserver",
    "node_modules",
    "requirements",
)
"""Strings whose presence in a *default* would mean one adopter's stack survived the port.

The first six are the issue's own list; the rest were collected from the pinned templates
while porting them, and are here because the issue's list is a sample of a class rather than
the class itself — `makemigrations` and `shell_plus` are exactly as stack-specific as
`manage.py`, and a check that looked only for the named six would pass a default that told a
PHP repo to run `makemigrations`.

Matched case-insensitively against the whole file, defaults only. Bessemer's own overrides are
*meant* to name this repo's stack; that is what an override is for.
"""

DENIED_TOOL: Final = (
    "If a tool is denied to you, stop and report it — never reach the same effect another "
    "way. A denial is a decision, not an obstacle: say what you were trying to do and what "
    "blocked you."
)
"""Delta 1 (ADR 0001), required in **both** the implement and the review default.

One literal for both files because the sentence is identical in both: the rule generalizes
past tools to the whole shape — blocked means say so — and two wordings of it would be two
rules. The pin predates the decision, so this is an addition to the ported text, not a port.
"""

SPECS_READ_ONLY: Final = (
    "The specs directory is read-only to you — `.bessemer/specs/` unless this repo's config "
    "sets `specs_dir` elsewhere. Do not create, edit or delete a file under it, not even to "
    "tick a checkbox: spec files are host-side state."
)
"""Delta 2, implement half (ADR 0002's consequence of tracking specs in this repository).

Bessemer's specs are cloned into the agent's checkout, where the port source's gitignored
layout had exposed only the single mounted spec. "Not even to tick a checkbox" is load
bearing: ticking an acceptance box is the specific edit an obliging agent makes.
"""

REVIEW_STOPPING: Final = (
    "an agent-authored edit to a file under the specs directory (`.bessemer/specs/` unless "
    "this repo's config sets `specs_dir` elsewhere) is a review-stopping finding: report it "
    "and end the round `needs-work`, whatever else the diff does. Spec files are host-side "
    "state."
)
"""Delta 2, review half. **Stopping**, not a nit — that is the whole content of the delta.

Matched from mid-sentence because the template's bullet opens with a bolded label; the label
is a formatting choice and the rule is not.
"""

VERDICT_SEMANTICS: Final = (
    "`<verdict>approved</verdict>` — only if you made **no changes this round** and found "
    "nothing wrong. A round that committed fixes ends `needs-work`; the next round "
    "re-reviews fresh."
)
"""Delta 3. Ported verbatim from the pin, and pinned here because it is what makes the review
loop *terminate correctly*: without "no changes this round", a reviewer that fixes something
and approves its own fix ends the loop on a round nobody reviewed."""

FORMAT_CONTRACT: Final = (
    "Every line you output lands verbatim in the PR body.",
    "no preamble or process narration, no closing remarks, no enclosing code fence, no "
    "title line (the title is set separately).",
)
"""The PR template's output contract, in two halves, with the example between them left out.

Issue 08 takes this pass's final text and puts it in a PR body; it cannot strip a code fence
it never asked for, and a title line would land as body text under the title. So the contract
is a control of the same class as the three deltas even though it is a straight port.

The sentence between them names the first `STRUCTURE` section by way of example, and that
example *tracks* the template rather than being pinned — see `test_the_format_example_names
_the_first_structure_section`, which derives it. Pinning it would have frozen hae's
"migrations callout" into a stack-neutral default.
"""

PROMISE: Final = "<promise>"
"""Delta 4: dropped, so its absence is the assertion.

Measured at the pin: `<promise>COMPLETE</promise>` appears in `.agentbox/implement-prompt.md`
and nowhere else in that repository's source — nothing in `run.sh` or `tasklib.py` reads it.
An output contract with no reader is a claim the tool cannot vouch for, so it did not come
across. Asserted on the opening tag alone, so a differently-worded promise fails too.
"""

MAKE_CHECK: Final = "make check"
"""What bessemer's own overrides must carry: ADR 0002's one definition of the checks, whose
third consumer is the in-container agent. An override that named `ruff`, `mypy` and the test
runner separately would be a second list of the checks, drifting from the first.

Asserted against the **fenced block**, not against the file. Measured during this issue's
review: with the assertion matching anywhere in the text, changing the fenced command to
`ruff check .` left the suite green, because the surrounding prose says `make check` twice
more. The fenced block is the line the agent copies; the prose is the line explaining it, and
only one of the two is the gate.
"""

RETIRED: Final = "task"
"""A word the defaults and the overrides may not use (CONTEXT.md, "task").

Retired entirely at port time — the dispatch unit is a **Run**, the markdown files are
**Specs** — and these templates are where it kept coming back, because the pin's prose is
saturated with it and the port started from that prose. Six sites survived the first draft of
this issue, including the implement template's `# TASK` heading. Pinned as an absence so the
seventh cannot.

Word-boundary matched, so `tasklib.py` — a filename at the pin, and a proper noun — would
still be legal if a template ever had cause to name it.
"""


def flat(text: str) -> str:
    """`text` with every run of whitespace collapsed to one space.

    What makes a pinned control survive re-wrapping without letting it survive rewording. It
    is applied to both sides, so the comparison is over the same normalisation.
    """
    return " ".join(text.split())


def headings(template: str) -> list[str]:
    """The `# ` headings of a template, in order, without the marker.

    Fenced blocks are skipped. Two of the templates here already contain a ```bash fence, and
    a shell comment inside one would otherwise read as a section — which would make this
    function report a structure the agent never sees as one.
    """
    found: list[str] = []
    fenced = False
    for line in template.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("# "):
            found.append(line.removeprefix("# ").strip())
    return found


def fenced(template: str) -> list[str]:
    """The contents of each fenced code block, stripped, in order.

    What separates the command an agent copies from the prose that talks about it. An
    unterminated fence yields nothing rather than the rest of the file: a template whose fence
    is broken has no runnable block, and reporting the remainder as one would let the
    assertion pass on exactly the file that would confuse the agent.
    """
    blocks: list[str] = []
    current: list[str] | None = None
    for line in template.splitlines():
        if line.startswith("```"):
            if current is None:
                current = []
            else:
                blocks.append("\n".join(current).strip())
                current = None
        elif current is not None:
            current.append(line)
    return blocks


def defaults() -> Iterator[tuple[str, str]]:
    """Every packaged default, as (name, text)."""
    for name in TEMPLATE_NAMES:
        yield name, prompts.load(name)


class AdapterTest(unittest.TestCase):
    """A base for the tests that need a throwaway adapter directory."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.adapter = Path(self.tmp.name) / ".bessemer"
        self.overrides = self.adapter / prompts.OVERRIDE_DIR
        self.adapter.mkdir()

    def write_override(self, name: str, text: str) -> Path:
        self.overrides.mkdir(exist_ok=True)
        path = self.overrides / name
        path.write_text(text, encoding="utf-8")
        return path


class NameTest(unittest.TestCase):
    """What this package ships, and what it refuses to be asked for."""

    def test_the_template_names_are_the_three_this_issue_owns(self) -> None:
        self.assertEqual(prompts.TEMPLATE_NAMES, TEMPLATE_NAMES)

    def test_each_named_constant_is_one_of_them(self) -> None:
        """The constants are what callers spell; a typo in one would resolve to nothing."""
        self.assertEqual(
            (prompts.IMPLEMENT, prompts.REVIEW, prompts.PR),
            ("implement-prompt.md", "review-prompt.md", "pr-prompt.md"),
        )

    def test_the_packaged_directory_holds_exactly_these_files(self) -> None:
        """A stray file beside the defaults is either a template nothing loads or an editor's
        leftover shipped to every adopter in the wheel."""
        origin = prompts.__file__
        assert origin is not None, "bessemer.prompts must be importable from a source tree"
        directory = Path(origin).parent / prompts.DEFAULTS_DIR
        self.assertEqual(
            sorted(path.name for path in directory.iterdir()), sorted(TEMPLATE_NAMES)
        )

    def test_an_unknown_name_raises(self) -> None:
        with self.assertRaises(ValueError):
            prompts.load("nope.md")

    def test_a_traversing_name_raises_before_any_path_is_built(self) -> None:
        """The refusal is also what keeps `name` from being path input. `load` joins it onto
        the adapter directory, so a name from anywhere less trusted than this package's own
        constants would otherwise read a file of the caller's choosing into an agent's
        prompt."""
        for name in ("../../etc/passwd", "/etc/passwd", "implement-prompt.md/../x"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                prompts.load(name)


class ResolutionTest(AdapterTest):
    """Package default, adapter override, and the failures that must not be silent."""

    def test_without_an_adapter_the_packaged_default_is_returned(self) -> None:
        for name, text in defaults():
            with self.subTest(name=name):
                self.assertTrue(text.strip(), f"{name} is empty")

    def test_an_override_wins_and_removing_it_restores_the_default(self) -> None:
        """The whole of ADR 0001's prompt decision, in one round trip: read time, not load
        time, so the same process resolves differently after the file changes."""
        default = prompts.load(prompts.IMPLEMENT)
        self.write_override(prompts.IMPLEMENT, "# SPEC\n\nmine\n")

        self.assertEqual(
            prompts.load(prompts.IMPLEMENT, adapter_dir=self.adapter), "# SPEC\n\nmine\n"
        )

        (self.overrides / prompts.IMPLEMENT).unlink()
        self.assertEqual(prompts.load(prompts.IMPLEMENT, adapter_dir=self.adapter), default)

    def test_only_the_overridden_template_changes(self) -> None:
        """Overrides are per file, not a mode the adapter switches into."""
        self.write_override(prompts.IMPLEMENT, "mine\n")
        for name in (prompts.REVIEW, prompts.PR):
            with self.subTest(name=name):
                self.assertEqual(
                    prompts.load(name, adapter_dir=self.adapter), prompts.load(name)
                )

    def test_an_adapter_with_no_prompts_directory_gets_the_defaults(self) -> None:
        """The common case: most adapters override nothing at all."""
        for name in TEMPLATE_NAMES:
            with self.subTest(name=name):
                self.assertEqual(
                    prompts.load(name, adapter_dir=self.adapter), prompts.load(name)
                )

    def test_an_empty_override_is_an_empty_prompt(self) -> None:
        """Whole-file wins means the file's content is the template, and zero bytes is
        content someone committed. Falling back here would make an emptied override look
        like a working one."""
        self.write_override(prompts.REVIEW, "")
        self.assertEqual(prompts.load(prompts.REVIEW, adapter_dir=self.adapter), "")

    def test_an_override_that_is_a_directory_raises(self) -> None:
        """Present and unreadable is not absent. A silent fallback here would run the agent
        on instructions the adapter's author did not write, unattended, with the difference
        visible nowhere."""
        self.overrides.mkdir()
        (self.overrides / prompts.PR).mkdir()
        with self.assertRaises(OSError):
            prompts.load(prompts.PR, adapter_dir=self.adapter)

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 file: nothing to catch")
    def test_an_override_that_cannot_be_read_raises(self) -> None:
        """The third of the three failure cases `prompts.load`'s docstring names.

        It behaves correctly today — measured, `chmod 000` then `load` raises
        `PermissionError` — but nothing would have noticed it *stopping*, which is the whole
        argument for the other two being tested. A docstring listing three cases and a suite
        covering two is a claim with a gap in it.

        Skipped as root rather than asserted differently: root ignores the mode bits, so the
        read succeeds and the test would be pinning the platform's privilege model instead of
        this module's behaviour. CI containers routinely run as root.
        """
        self.overrides.mkdir()
        path = self.overrides / prompts.PR
        path.write_text("mine\n", encoding="utf-8")
        path.chmod(0o000)
        # Before the temporary tree is removed, and before the assertion — cleanups run last
        # in, first out, so this restores the mode while the directory still exists.
        self.addCleanup(path.chmod, 0o600)
        with self.assertRaises(PermissionError):
            prompts.load(prompts.PR, adapter_dir=self.adapter)

    def test_an_override_that_is_not_utf8_raises(self) -> None:
        """The other half of the same rule, and the likelier accident of the two."""
        self.overrides.mkdir()
        (self.overrides / prompts.PR).write_bytes(b"\xff\xfe not utf-8")
        with self.assertRaises(UnicodeDecodeError):
            prompts.load(prompts.PR, adapter_dir=self.adapter)


class OverriddenTest(AdapterTest):
    """The fact doctor renders a count from."""

    def test_nothing_is_overridden_by_default(self) -> None:
        self.assertEqual(prompts.overridden(self.adapter), ())

    def test_overrides_are_reported_in_template_order(self) -> None:
        """Reported in pass order rather than in the order the filesystem lists them, so a
        doctor line reads the same on every machine."""
        self.write_override(prompts.PR, "a\n")
        self.write_override(prompts.IMPLEMENT, "b\n")
        self.assertEqual(prompts.overridden(self.adapter), (prompts.IMPLEMENT, prompts.PR))

    def test_a_file_that_is_not_a_template_is_not_counted(self) -> None:
        """A glob would report drift that `load` would never read."""
        self.write_override(prompts.IMPLEMENT, "b\n")
        (self.overrides / "README.md").write_text("notes\n", encoding="utf-8")
        (self.overrides / "pr-prompt.md.bak").write_text("old\n", encoding="utf-8")
        self.assertEqual(prompts.overridden(self.adapter), (prompts.IMPLEMENT,))


class SectionTest(unittest.TestCase):
    """The ported structure: which sections, in which order."""

    def test_each_default_has_its_sections_in_order(self) -> None:
        for name, text in defaults():
            with self.subTest(name=name):
                self.assertEqual(headings(text), list(SECTIONS[name]))

    def test_every_template_has_a_pinned_section_list(self) -> None:
        """Without this, adding a fourth template would add an unpinned one."""
        self.assertEqual(sorted(SECTIONS), sorted(TEMPLATE_NAMES))


class ControlTest(unittest.TestCase):
    """The four deltas against the pin, and the PR body's output contract."""

    def test_the_denied_tool_rule_is_in_both_agent_prompts(self) -> None:
        """ADR 0001 makes this binding on **both** prompts; one of the two is half a rule."""
        for name in (prompts.IMPLEMENT, prompts.REVIEW):
            with self.subTest(name=name):
                self.assertIn(flat(DENIED_TOOL), flat(prompts.load(name)))

    def test_the_implement_default_declares_the_specs_directory_read_only(self) -> None:
        self.assertIn(flat(SPECS_READ_ONLY), flat(prompts.load(prompts.IMPLEMENT)))

    def test_the_review_default_makes_a_spec_edit_review_stopping(self) -> None:
        self.assertIn(flat(REVIEW_STOPPING), flat(prompts.load(prompts.REVIEW)))

    def test_the_review_default_pins_the_verdict_semantics(self) -> None:
        self.assertIn(flat(VERDICT_SEMANTICS), flat(prompts.load(prompts.REVIEW)))

    def test_no_default_asks_for_a_promise_token(self) -> None:
        """Delta 4, asserted as an absence over every default rather than only the one that
        carried it, so it cannot reappear in a neighbour."""
        for name, text in defaults():
            with self.subTest(name=name):
                self.assertNotIn(PROMISE, text)

    def test_the_pr_default_pins_the_verbatim_body_contract(self) -> None:
        """Issue 08 composes a PR body out of this pass's final text."""
        text = flat(prompts.load(prompts.PR))
        for sentence in FORMAT_CONTRACT:
            with self.subTest(sentence=sentence):
                self.assertIn(flat(sentence), text)

    def test_the_format_example_names_the_first_structure_section(self) -> None:
        """The example tracks the template while the contract stays pinned.

        Derived rather than restated, deliberately: the pin's parenthetical names *its* first
        section ("the migrations callout"), which is exactly the stack-specific content this
        port excises. What has to stay true is that the example points at whatever the
        stack-neutral `STRUCTURE` actually names first — a FORMAT section telling the agent to
        begin with a section the template no longer has is worse than no example.
        """
        text = prompts.load(prompts.PR)
        first = text.split("1. **", 1)[1].split("**", 1)[0]
        opening = "Begin directly with the first section ("
        example = flat(text).split(opening, 1)[1].split(")", 1)[0]
        for word in first.lower().split():
            with self.subTest(word=word):
                self.assertIn(word, example)


class StackTest(unittest.TestCase):
    """The defaults assume nothing about language or test tooling (ADR 0001)."""

    def test_no_default_carries_a_marker_of_the_port_source_stack(self) -> None:
        for name, text in defaults():
            for marker in HAE_MARKERS:
                with self.subTest(name=name, marker=marker):
                    self.assertNotIn(marker, text.lower())


class VocabularyTest(unittest.TestCase):
    """CONTEXT.md's terms, in the prose an agent actually reads.

    Over the overrides as well as the defaults. The glossary is not a house style for source
    comments — a prompt telling an agent to "complete the task" is teaching the retired word
    to the one reader who will echo it back into commit messages and a PR body.
    """

    def test_no_template_uses_the_retired_word(self) -> None:
        pattern = re.compile(rf"\b{RETIRED}\b", re.IGNORECASE)
        templates = list(defaults()) + [
            (f".bessemer/prompts/{name}", prompts.load(name, adapter_dir=OWN_ADAPTER_DIR))
            for name in (prompts.IMPLEMENT, prompts.REVIEW)
        ]
        for name, text in templates:
            with self.subTest(name=name):
                self.assertIsNone(pattern.search(text), f"{name} uses {RETIRED!r}")


class OwnOverrideTest(unittest.TestCase):
    """Bessemer's own adapter: this repo is its own first adopter, so its overrides are real.

    Asserted against the committed files rather than a fixture, the way `tests/test_adapter.py`
    asserts against the committed adapter: what has to be true is that *these* overrides load
    and say what they must.
    """

    def test_the_repo_overrides_exactly_implement_and_review(self) -> None:
        """Hand-written, because it is a claim about what this repo has decided to diverge on.
        The PR template is deliberately not overridden — nothing in it is repo-specific once
        the port's stack content is gone."""
        self.assertEqual(
            prompts.overridden(OWN_ADAPTER_DIR),
            ("implement-prompt.md", "review-prompt.md"),
        )

    def test_the_override_directory_holds_nothing_else(self) -> None:
        """A file here that `load` never reads is a prompt nobody is running and everybody
        will assume is running."""
        self.assertEqual(
            sorted(path.name for path in OWN_OVERRIDE_DIR.iterdir()),
            ["implement-prompt.md", "review-prompt.md"],
        )

    def test_both_overrides_verify_with_make_check(self) -> None:
        """ADR 0002's third consumer of the one definition of the checks.

        Against the fenced blocks, so the prose around it cannot satisfy this on behalf of a
        fence that has been narrowed to something else. See `MAKE_CHECK`.
        """
        for name in (prompts.IMPLEMENT, prompts.REVIEW):
            with self.subTest(name=name):
                blocks = fenced(prompts.load(name, adapter_dir=OWN_ADAPTER_DIR))
                self.assertIn(MAKE_CHECK, blocks)

    def test_the_overrides_are_complete_templates(self) -> None:
        """Overrides are whole files, not patches, so a section the default had and the
        override dropped is a section this repo's agents simply never see."""
        for name in (prompts.IMPLEMENT, prompts.REVIEW):
            with self.subTest(name=name):
                text = prompts.load(name, adapter_dir=OWN_ADAPTER_DIR)
                self.assertEqual(headings(text), list(SECTIONS[name]))

    def test_the_overrides_keep_the_controls(self) -> None:
        """The three deltas are decisions (ADR 0001, ADR 0002), not defaults an adapter may
        drop by copying the file and editing it. This repo's own overrides are the first place
        that could have happened, and the tracer dispatches through them."""
        implement = prompts.load(prompts.IMPLEMENT, adapter_dir=OWN_ADAPTER_DIR)
        review = prompts.load(prompts.REVIEW, adapter_dir=OWN_ADAPTER_DIR)
        self.assertIn(flat(DENIED_TOOL), flat(implement))
        self.assertIn(flat(DENIED_TOOL), flat(review))
        self.assertIn(flat(SPECS_READ_ONLY), flat(implement))
        self.assertIn(flat(REVIEW_STOPPING), flat(review))
        self.assertIn(flat(VERDICT_SEMANTICS), flat(review))
        self.assertNotIn(PROMISE, implement)
        self.assertNotIn(PROMISE, review)


if __name__ == "__main__":
    unittest.main()
