"""The agent's stream-json, turned into log lines and one final text. Pure, host-side.

An agent pass writes `claude --output-format stream-json --verbose` to its stdout: one JSON
event per line. Two things have to come out of it, and ADR 0001 names them as **one**
provider-contract surface — "live-log filtering and final-text capture":

- the `claude |`, `claude ~` and `claude >` lines a reader tails the run log for, and
- the pass's **final text**, which the review loop reads a verdict out of and the landing
  step puts in a pull-request body.

Both live here, together, because splitting them across host and container would put half a
provider contract in each place and make replacing `claude` a two-file change.

## The divergence: this ran inside the container at the pin

Upstream pipes the stream through `python3 /agentbox/stream-filter.py` **in the container**
(run.sh:1099). That assumes a python3 in every adapter image, which fails ADR 0001's
assume-nothing-about-stacks constraint — the PHP and Node adopters bessemer is being built
for have no reason to carry one, and the failure would arrive as an unreadable log on the
first pass rather than as a doctor line. So the core filters `docker exec`'s stream on the
host, and the adapter image's contract stays `bash`, `timeout`, `git`, `claude` and a
non-root user.

**The parity argument, and why it is not a promise.** Moving the renderer changes nothing a
reader sees only if the rendering is identical, and "identical" is a claim a test has to
hold (F3 README decision 5.1). `tests/fixtures/stream/` holds real transcripts and the
bytes upstream's own filter produced over them; `tests/test_stream.py` compares this
module's output to those bytes on both channels. The oracle is
`git show e194121f75f4:.agentbox/stream-filter.py`.

## Malformed input, measured first

Upstream strips each line, skips it if it is empty, and on `json.JSONDecodeError` emits the
stripped line **raw — no prefix, no marker**. That is ported exactly: a stray line from the
CLI reaches the run log looking like what it is.

**One recorded divergence, and it is the only one that changes what a reader sees.**
Upstream then reaches into the parsed value with `.get`, so a line that is valid JSON but
not an *object* — `123`, `"text"`, `[1,2]` — raises `AttributeError` and kills the filter.
Measured 2026-08-05 against the pin. In the container that failed only the pass, and run.sh
retried it; on the host the same exception would come out of the dispatcher and end the
run. So a non-object line takes the malformed arm instead: emitted raw, exactly like a line
that is not JSON at all.

**The rule that follows from it, stated once rather than repeated per field:** where
upstream would raise on a shape it did not expect, this renders what it can and nothing
where it cannot. That is a divergence only in the shapes that make upstream *crash* — an
event whose `message` or `content` is not what the renderer needs, or a failed result whose
text is not a string, where upstream's `[:300]` raises `TypeError` and this quotes `str` of
it. Where upstream renders something rather than crashing, this renders the same thing:
`?` stands only for an **absent** tool name (`NO_NAME`), and a final text that is not a
string is `str`-rendered exactly as `print` would have. On well-formed stream-json none of
this is reachable, which is why the fixtures still hold byte-parity.

## Pure, and what that buys

No subprocess, no filesystem, no terminal: plain functions over `str` and iterables. The
log lines go wherever the caller's `emit` puts them and the final text is **returned**. That
is what makes the parity test a comparison of values rather than of captured streams, and it
keeps the one place bessemer spawns anything `bessemer.proc` (ADR 0002). The subprocess half
of that is enforced statically over the whole package by `tests/test_argv_boundary.py`; what
`tests/test_stream.py` adds is the filesystem half, which is this module's alone.

`emit` is a required keyword with no default, deliberately. A default that dropped the lines
would make a silent run log the cost of forgetting an argument, and the whole reason this
rendering exists is that somebody wants to watch the agent work.

## Two things this module deliberately does not do

**It does not redact.** `bessemer.redact`'s contract is that everything bessemer prints
which it did not write itself passes through it, and an agent's `Bash` command in a
`claude >` line is exactly that. Redacting here would change bytes, which is a divergence
from the pin that no decision sanctions and that the parity gate (ADR 0001) would read as a
mismatch. So the question belongs to whoever *writes* these lines somewhere — `passes.py`
at issue 07 — and to the one quotability policy `proc.py` owns (ADR 0003). This paragraph
exists so that the hole is a decision with an owner rather than an omission.

**It does not name itself `stream_filter`.** F3 README decision 5.1 calls for "a pure
`stream_filter` function"; inside a module called `stream` that stutters, so the function is
`filtered` and the name is recorded here rather than left as a discrepancy a later reader
has to resolve.
"""

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Final

TEXT_PREFIX: Final = "claude | "
"""What an assistant text line gets, one line at a time. The pipe is the agent talking."""

THINKING_PREFIX: Final = "claude ~ "
"""What a thinking block gets.

**Measured 2026-08-05: today's CLI never renders one.** It emits `"thinking": ""` with the
reasoning carried only in an opaque `signature` field, so this arm produces nothing against
a real stream. Ported anyway — it is the pin's behaviour, the field is the provider's to
start populating again, and a renderer that dropped it would lose the lines silently.
"""

TOOL_PREFIX: Final = "claude > "
"""What a tool call gets, followed by the tool name, a colon, and `brief` of its input."""

NO_NAME: Final = "?"
"""What stands in for a tool call that names no tool. The pin's `block.get("name", "?")`."""

ERROR_PREFIX: Final = "claude !! error result: "
"""What precedes a failed result's own words. Two bangs: this one ends the pass."""

NO_RESULT: Final = "claude !! stream ended without a result event"
"""The stream stopped without saying how it went — a crash, or a timeout kill.

The whole line, not a prefix: there is nothing to say beyond it, because the reason the
stream stopped is not in the stream.
"""

BRIEF_KEYS: Final = ("command", "file_path", "path", "pattern", "description", "prompt")
"""Which of a tool call's inputs stands for the call, in first-one-present order.

Upstream's list and upstream's order (`brief_input`). The order is the decision: `Bash`
carries both `command` and `description`, and the command is what a reader tailing the log
needs. An input with none of these renders as the tool name alone.
"""

BRIEF_LIMIT: Final = 160
"""How much of a tool call's input a log line may carry, ellipsis included.

A tool call is one line in a log somebody is watching scroll; a 400-character `Bash`
command that wraps five times costs more than it tells. The pin's number.
"""

ELLIPSIS: Final = "..."
"""Marks a `brief` that was cut. Three ASCII dots, not `…` — the pin's, and greppable."""

ERROR_LIMIT: Final = 300
"""How much of a failed result's text is quoted into the log. The pin's number.

Larger than `BRIEF_LIMIT` because this line is the last thing the pass says, and smaller
than unbounded because a provider error can be a page of JSON.
"""


@dataclass(frozen=True)
class Capture:
    """What a pass's stream came to: whether it succeeded, and the text it ended with.

    Frozen, and carrying no log lines — those went to `emit` as they arrived, which is what
    makes `tail -f` on the run log show the agent working rather than replay it at the end.

    **No `__bool__`, and none may be added**, for `bessemer.checkout.Salvage`'s reason: `if
    capture:` would read as "is there a capture" and mean "did the pass succeed". `ok` is
    the decision and has to be asked for by name.
    """

    ok: bool
    """Whether the stream ended in a successful result event.

    False covers both of upstream's nonzero exits — an `is_error` result, and a stream that
    ended without any result at all. The caller's response is the same for both: the pass
    did not produce an answer, and `run.sh`'s retry fired.
    """

    text: str
    """The final text, empty when `ok` is false.

    Exactly what upstream put on **stdout**, which is nothing at all when it failed — a
    failed pass's own words went to the log through `ERROR_PREFIX` and must not reach a
    pull-request body as though they were a result.
    """


def brief(inputs: Mapping[str, object]) -> str:
    """One line standing for a tool call's input. Empty when nothing recognisable is in it.

    Upstream's `brief_input`, exactly: first key of `BRIEF_KEYS` with a **truthy** value
    wins — an empty `command` falls through to `file_path` rather than winning and rendering
    blank — all whitespace inside collapses to single spaces so a heredoc stays one line,
    and anything past `BRIEF_LIMIT` is cut so that the result is `BRIEF_LIMIT` characters
    *including* the ellipsis.
    """
    for key in BRIEF_KEYS:
        value = inputs.get(key)
        if not value:
            continue
        text = " ".join(str(value).split())
        if len(text) <= BRIEF_LIMIT:
            return text
        return text[: BRIEF_LIMIT - len(ELLIPSIS)] + ELLIPSIS
    return ""


def _event(raw: str) -> Mapping[str, object] | None:
    """The event a stream line holds, or `None` when the line is not one.

    The two arms the caller has to tell apart are "nothing here" and "not an event": a blank
    line is skipped in silence, and a line that is not a JSON object is echoed raw. This
    returns `None` for the second; the caller checks the first before asking.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _blocks(event: Mapping[str, object]) -> list[object]:
    """The content blocks of an assistant message, or none when the event has no usable ones."""
    message = event.get("message")
    if not isinstance(message, Mapping):
        return []
    content = message.get("content")
    return content if isinstance(content, list) else []


def _rendered(block: Mapping[str, object]) -> list[str]:
    """The log lines one assistant content block renders to. Unknown block types render none.

    Text and thinking are split on their own newlines after a `strip`, so a paragraph
    becomes one prefixed line each and a blank line inside one stays blank — that is the
    pin's shape, trailing space and all, and the fixtures pin it.
    """
    match block.get("type"):
        case "text":
            return _prefixed(TEXT_PREFIX, block.get("text"))
        case "thinking":
            return _prefixed(THINKING_PREFIX, block.get("thinking"))
        case "tool_use":
            # `NO_NAME` is upstream's default for an **absent** name, and it is applied the
            # same way here: a name that is present but not a string renders through the
            # f-string as itself, because collapsing it into `?` would make two different
            # streams — a nameless tool call and a mis-typed one — read identically.
            inputs = block.get("input")
            return [
                TOOL_PREFIX
                + f"{block.get('name', NO_NAME)}: "
                + brief(inputs if isinstance(inputs, Mapping) else {})
            ]
        case _:
            return []


def _prefixed(prefix: str, body: object) -> list[str]:
    """`body`'s lines, each behind `prefix`. Nothing at all when it has none."""
    if not isinstance(body, str):
        return []
    return [prefix + line for line in body.strip().splitlines()]


def filtered(transcript: Iterable[str], *, emit: Callable[[str], None]) -> Capture:
    """Render `transcript`'s log lines through `emit`, and return what the pass came to.

    The whole of upstream's `main`, minus its two writes: the lines it sent to stderr go to
    `emit` in the order they arrive, and the text it sent to stdout comes back in `Capture`.

    `transcript` is consumed lazily and once, so a caller may hand it the pass's stdout as
    it is read and see lines land in the run log while the agent is still working.

    **The last result event wins**, which is upstream's behaviour and not an accident of
    porting: a stream carries at most one, and a caller that concatenated two would mean the
    second. Events other than `assistant` and `result` render nothing — that includes the
    `user` events carrying tool results, which is why a tool's *output* never reaches the
    log through here.
    """
    result: Mapping[str, object] | None = None
    for raw in transcript:
        line = raw.strip()
        if not line:
            continue
        event = _event(line)
        if event is None:
            emit(line)
            continue
        match event.get("type"):
            case "assistant":
                for block in _blocks(event):
                    if isinstance(block, Mapping):
                        for rendered in _rendered(block):
                            emit(rendered)
            case "result":
                result = event
    if result is None:
        emit(NO_RESULT)
        return Capture(ok=False, text="")
    # Upstream's `result.get("result") or ""` then `print(text)`, which renders a non-string
    # final text rather than dropping it. `str` is what `print` would have done; a falsy
    # value — including `None` and `""` — is the empty string, which is what `or ""` means.
    final = result.get("result")
    text = "" if not final else str(final)
    if result.get("is_error"):
        emit(ERROR_PREFIX + text[:ERROR_LIMIT])
        return Capture(ok=False, text="")
    return Capture(ok=True, text=text)
