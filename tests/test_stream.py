"""Tests for the host-side stream filter: byte-parity with the pin, and final-text capture.

**The fixtures are the oracle, and they are the whole point of this file.** F3 README
decision 5.1 moves the filter out of the container, and says in the same breath that "log
lines identical" is a claim held by a test rather than asserted. So the assertions here are
not a reviewer's opinion of what a rendered line should look like: `tests/fixtures/stream/`
holds real stream-json transcripts and the bytes upstream's own `stream-filter.py` produced
over them, and `ParityTest` compares against those bytes and nothing else. See that
directory's `README.md` for provenance, and for the three lines in it that were added by
hand.

Both of upstream's channels are compared, because the filter has two: stderr became the
run log's `claude |/>` lines, stdout became the final text a pass returns. Comparing only
the first would leave the half ADR 0001 calls the other side of the same provider-contract
surface unpinned.

The remaining classes cover what a fixture cannot reach — the truncation boundary exactly,
and the shapes upstream raises on — and they are the only assertions here that state a
belief rather than restate a measurement.
"""

import ast
import json
import unittest
from pathlib import Path
from typing import Final

from bessemer import stream

FIXTURES: Final = Path(__file__).resolve().parent / "fixtures" / "stream"
"""Where the transcripts and upstream's output over them live."""

STEMS: Final = (
    ("read", True),
    ("bash", True),
    ("max-turns", False),
    ("no-result", False),
)
"""Every fixture stem, paired with whether upstream exited 0 over it.

Hand-written rather than derived: upstream's exit status is not recorded in any of the
committed files, and the three that are nonzero are nonzero for three different reasons —
an error result, a stream with no result at all, and (for `read`/`bash`) neither. A list a
test computed from the fixtures would agree with a filter that got the status backwards.
"""


def collected(stem: str) -> tuple[list[str], stream.Capture]:
    """Render one fixture, collecting the log lines the way a run log would receive them."""
    lines: list[str] = []
    transcript = FIXTURES.joinpath(f"{stem}.jsonl").read_text(encoding="utf-8").splitlines()
    capture = stream.filtered(transcript, emit=lines.append)
    return lines, capture


class ParityTest(unittest.TestCase):
    """Byte-identical to `.agentbox/stream-filter.py` at the pin, on both channels."""

    def test_the_log_lines_are_byte_identical_to_upstreams_stderr(self) -> None:
        for stem, _ in STEMS:
            with self.subTest(fixture=stem):
                lines, _ = collected(stem)
                expected = FIXTURES.joinpath(f"{stem}.stderr").read_bytes()
                rendered = "".join(f"{line}\n" for line in lines).encode("utf-8")
                self.assertEqual(rendered, expected)

    def test_the_final_text_reconstructs_upstreams_stdout(self) -> None:
        """`Capture.text` plus upstream's own trailing newline, or nothing when it failed.

        Upstream prints the final text with `print`, which appends the newline, and prints
        nothing at all when it exits nonzero. Reconstructing the stream rather than
        stripping the file keeps the comparison exact for a final text that itself ends in
        a newline — the case a `removesuffix` would silently mis-handle.
        """
        for stem, _ in STEMS:
            with self.subTest(fixture=stem):
                _, capture = collected(stem)
                expected = FIXTURES.joinpath(f"{stem}.stdout").read_bytes()
                stdout = f"{capture.text}\n" if capture.ok else ""
                self.assertEqual(stdout.encode("utf-8"), expected)

    def test_success_matches_upstreams_exit_status(self) -> None:
        for stem, ok in STEMS:
            with self.subTest(fixture=stem):
                _, capture = collected(stem)
                self.assertEqual(capture.ok, ok)

    def test_the_expected_output_still_has_its_trailing_spaces(self) -> None:
        """A canary over the fixtures themselves, not over the module.

        `bash.stderr` carries lines that are a prefix and nothing else — a blank line in an
        agent's message renders as `claude | `, trailing space included. Measured: the
        `trailing-whitespace` hook trims exactly those, which would rewrite the oracle and
        leave a parity test comparing this repository's formatting preferences to itself.
        `.pre-commit-config.yaml` excludes the captured fixture extensions for this; if that
        exclusion is ever narrowed, this fails before the parity assertions turn into a
        tautology.
        """
        lines = FIXTURES.joinpath("bash.stderr").read_text(encoding="utf-8").splitlines()
        self.assertIn(stream.TEXT_PREFIX, lines)

    def test_the_captured_text_is_the_last_assistant_text_of_the_transcript(self) -> None:
        """The other half of the contract, read off the transcript rather than off stdout.

        `read.stdout` could agree with a filter that captured the *first* assistant text, or
        the tool result, and stay green — this fixture's stream is short. Walking the
        transcript for the last `text` block states what the capture is supposed to be.
        """
        for stem in ("read", "bash"):
            with self.subTest(fixture=stem):
                texts = [
                    block["text"]
                    for line in FIXTURES.joinpath(f"{stem}.jsonl").read_text().splitlines()
                    if line.startswith("{")
                    for block in json.loads(line).get("message", {}).get("content", [])
                    if block.get("type") == "text"
                ]
                _, capture = collected(stem)
                self.assertEqual(capture.text, texts[-1])


class MalformedTest(unittest.TestCase):
    """Lines that are not events. Measured against the pin, not decided here."""

    def test_a_line_that_is_not_json_is_emitted_raw(self) -> None:
        """No prefix, and stripped — upstream's `emit(raw)` after its own `raw.strip()`."""
        lines: list[str] = []
        stream.filtered(["  not json at all  "], emit=lines.append)
        self.assertEqual(lines[0], "not json at all")

    def test_a_blank_line_renders_nothing(self) -> None:
        lines: list[str] = []
        stream.filtered(["", "   ", "\t"], emit=lines.append)
        self.assertEqual(lines, [stream.NO_RESULT])

    def test_json_that_is_not_an_object_takes_the_malformed_arm(self) -> None:
        """The one recorded divergence. Upstream raises `AttributeError` here (measured).

        Restated as a literal rather than compared to upstream, because there is no
        upstream output to compare to: the oracle crashes, and a crash host-side would end
        the run instead of the pass. See the module docstring.
        """
        lines: list[str] = []
        stream.filtered(["123", '"a string"', "[1,2]"], emit=lines.append)
        self.assertEqual(lines[:3], ["123", '"a string"', "[1,2]"])

    def test_a_tool_name_that_is_not_a_string_renders_as_itself(self) -> None:
        """Measured: the oracle renders `None` and `7` here rather than crashing.

        So `NO_NAME` stays what upstream uses it for — an **absent** name — and collapsing
        a mis-typed one into it would make two different streams read identically.
        """
        lines: list[str] = []
        stream.filtered(
            [
                '{"type":"assistant","message":{"content":[{"type":"tool_use","name":null}]}}',
                '{"type":"assistant","message":{"content":[{"type":"tool_use","name":7}]}}',
                '{"type":"assistant","message":{"content":[{"type":"tool_use"}]}}',
            ],
            emit=lines.append,
        )
        self.assertEqual(
            lines[:3],
            ["claude > None: ", "claude > 7: ", f"claude > {stream.NO_NAME}: "],
        )

    def test_a_final_text_that_is_not_a_string_is_rendered_not_dropped(self) -> None:
        """Measured: the oracle's `print` puts `5` on stdout and exits 0."""
        capture = stream.filtered(['{"type":"result","result":5}'], emit=lambda _: None)
        self.assertEqual((capture.ok, capture.text), (True, "5"))

    def test_an_event_whose_content_is_not_a_list_renders_nothing(self) -> None:
        lines: list[str] = []
        stream.filtered(
            ['{"type":"assistant","message":{"content":"oops"}}', '{"type":"assistant"}'],
            emit=lines.append,
        )
        self.assertEqual(lines, [stream.NO_RESULT])


class BriefTest(unittest.TestCase):
    """`brief` — the one-line summary of a tool call's input (upstream's `brief_input`)."""

    def test_the_first_key_present_wins_in_upstreams_order(self) -> None:
        self.assertEqual(
            stream.brief({"description": "later", "command": "earlier", "path": "last"}),
            "earlier",
        )

    def test_an_empty_value_is_skipped_like_an_absent_one(self) -> None:
        """Upstream's `if val:`, not `if key in inp`. An empty command falls through."""
        self.assertEqual(stream.brief({"command": "", "file_path": "/x"}), "/x")

    def test_no_recognised_key_is_the_empty_string(self) -> None:
        self.assertEqual(stream.brief({"old_string": "a", "new_string": "b"}), "")

    def test_all_whitespace_collapses_to_single_spaces(self) -> None:
        self.assertEqual(stream.brief({"command": " a\n\tb   c \n"}), "a b c")

    def test_exactly_the_limit_is_not_truncated(self) -> None:
        value = "x" * stream.BRIEF_LIMIT
        self.assertEqual(stream.brief({"command": value}), value)

    def test_one_past_the_limit_is_cut_to_the_limit_with_an_ellipsis(self) -> None:
        brief = stream.brief({"command": "x" * (stream.BRIEF_LIMIT + 1)})
        self.assertEqual(len(brief), stream.BRIEF_LIMIT)
        self.assertEqual(brief, "x" * 157 + "...")

    def test_a_non_string_value_is_rendered_through_str(self) -> None:
        self.assertEqual(stream.brief({"command": 41}), "41")


class ResultTest(unittest.TestCase):
    """The result event: what ends a stream, and what the caller gets back."""

    def test_the_last_result_wins(self) -> None:
        lines: list[str] = []
        capture = stream.filtered(
            ['{"type":"result","result":"first"}', '{"type":"result","result":"second"}'],
            emit=lines.append,
        )
        self.assertEqual(capture.text, "second")
        self.assertEqual(lines, [])

    def test_a_result_with_no_text_succeeds_with_the_empty_string(self) -> None:
        capture = stream.filtered(['{"type":"result"}'], emit=lambda _: None)
        self.assertEqual((capture.ok, capture.text), (True, ""))

    def test_an_error_result_reports_no_text_however_much_it_carried(self) -> None:
        """Upstream prints nothing on stdout when it fails; the text went to the log."""
        lines: list[str] = []
        capture = stream.filtered(
            ['{"type":"result","is_error":true,"result":"boom"}'], emit=lines.append
        )
        self.assertEqual((capture.ok, capture.text), (False, ""))
        self.assertEqual(lines, ["claude !! error result: boom"])

    def test_an_error_result_is_quoted_to_three_hundred_characters(self) -> None:
        lines: list[str] = []
        stream.filtered(
            [json.dumps({"type": "result", "is_error": True, "result": "y" * 400})],
            emit=lines.append,
        )
        self.assertEqual(lines[0], stream.ERROR_PREFIX + "y" * stream.ERROR_LIMIT)

    def test_an_empty_stream_ends_without_a_result(self) -> None:
        lines: list[str] = []
        capture = stream.filtered([], emit=lines.append)
        self.assertEqual((capture.ok, capture.text), (False, ""))
        self.assertEqual(lines, [stream.NO_RESULT])


class PurityTest(unittest.TestCase):
    """The module writes no file and reads none. **Only the filesystem half.**

    "No subprocess" is already enforced for every module under `bessemer/`, statically, by
    `tests/test_argv_boundary.py` — from a set imported from `tests.guard` rather than
    restated. Repeating it here would be the hand-maintained second list that file's
    docstring exists to argue against, so this covers what it does not: the acceptance
    criterion "no filesystem — plain functions over `str`/iterables".

    A **banned** set rather than an allowlist of what may be imported. An allowlist reddens
    on a later `import re` that harms nothing, which trains a reader to widen it without
    looking; a banned set only reddens when the module reaches for the thing it must not.
    """

    BANNED: Final = frozenset({"os", "io", "pathlib", "shutil", "tempfile", "sys", "open"})

    def test_it_imports_nothing_that_can_reach_the_filesystem(self) -> None:
        tree = ast.parse(Path(stream.__file__).read_text(encoding="utf-8"))
        imported = {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertEqual(imported & self.BANNED, set())

    def test_it_calls_no_builtin_that_opens_a_file(self) -> None:
        """`open` needs no import, so the import check above cannot see it."""
        tree = ast.parse(Path(stream.__file__).read_text(encoding="utf-8"))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertEqual(called & self.BANNED, set())
