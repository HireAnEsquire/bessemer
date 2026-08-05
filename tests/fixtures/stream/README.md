# Stream-json fixtures — the parity proof for `bessemer.stream`

F3 README decision 5.1 moves the stream filter host-side, and says the parity claim ("log
lines identical") must be **held by a test, not asserted**. These files are that test's
evidence.

Each stem is a triple:

| File | What it is |
|---|---|
| `<stem>.jsonl` | A stream-json transcript, captured from a real `claude` run |
| `<stem>.stderr` | What upstream's filter wrote to **stderr** over it — the run-log lines |
| `<stem>.stdout` | What upstream's filter wrote to **stdout** over it — the final text |

The expected files were produced by running the oracle itself, host-side, once:

```
git -C /Users/sbowles/hae show e194121f75f4:.agentbox/stream-filter.py > /tmp/oracle.py
python3 /tmp/oracle.py < read.jsonl > read.stdout 2> read.stderr
```

`tests/test_stream.py` asserts bessemer's rendering of each `.jsonl` is byte-identical to
its `.stderr`, and that its captured final text reconstructs its `.stdout` byte-for-byte.

**The extensions name the channel, and one of them had to.** `.stderr` began as `.log`,
which `.gitignore`'s `*.log` swallows — the suite stayed green on the machine that wrote
the files and would have failed on the first clone that did not have them. Naming the two
files after the streams they came out of fixes that and reads better than the exception
would have.

## Provenance

Captured 2026-08-05 against `claude` 2.1.220 (`claude-opus-5[1m]`), each run invoked the
way `run.sh:1099` invokes it — prompt on stdin, `-p --output-format stream-json --verbose`
— against a scratch directory holding one two-line `notes.txt`.

| Stem | The real run behind it |
|---|---|
| `read` | "Read notes.txt … tell me the colour and the number, on two lines." |
| `bash` | Same session shape with `MAX_THINKING_TOKENS=4000`, asking for one long single-line `printf` and a read-back |
| `max-turns` | The `read` prompt doubled, run with `--max-turns 1`, so the run ends `is_error` |
| `no-result` | `read.jsonl` with its final `result` line deleted — a stream that ends without one |

## What was removed, and why

Every capture began with two large `system` events — `hook_response` (7.8 KB) and `init`
(4.8 KB) — carrying this machine's local plugin output, skill and slash-command inventory,
memory paths and cwd. **Both were deleted from every fixture.** They render nothing (the
filter ignores every `type` but `assistant` and `result`), and a fixture is a committed
artifact: one developer's local agent configuration does not belong in it. Deleting whole
lines rather than editing their contents keeps every line that remains a real one.

The `system` events that *do* remain — `hook_started`, `thinking_tokens` — are small,
carry no local inventory, and are what proves the ignore-everything-else arm.

## What was added, and where

Three lines, all of them because a real capture could not produce the case:

- **`bash.jsonl:10`** — an `assistant` event whose block is a `thinking` block *with text*.
  Measured: the CLI emits `"thinking": ""` with the reasoning carried only in the opaque
  `signature` field (see the real, empty one at `bash.jsonl:5`), so no real capture renders
  a `claude ~` line at all. The added event is modelled on the real one, with the
  `signature` replaced by a placeholder — it is hand-authored and says so.
- **`bash.jsonl:11`** — a non-JSON line. Its text is real: it is what the CLI printed when
  invoked with no prompt on either channel, observed during this capture session. It is
  placed here to pin the malformed-line arm, which upstream renders raw.
- **`max-turns.jsonl:7`** — a second `result` event, `is_error` with a result text past 300
  characters. The real `error_max_turns` result above it (`:6`) carries **no** text, so it
  exercises the error arm but not upstream's `[:300]` truncation. Both are kept: the real
  one is the evidence of the shape, the added one exercises the cut. Upstream keeps only
  the last `result` it sees, so the added line is the one rendered.

## Coverage

Between them the four stems reach every arm of the oracle:

- assistant `text`, including a multi-line block and a blank interior line (`bash.stderr:2–6`)
- assistant `thinking` (`bash.stderr:7–8`), and the real empty one that renders nothing
- assistant `tool_use`, both under and over the 160-character brief (`read.stderr:1`,
  `bash.stderr:1`)
- a `tool_result`, and the `user`, `rate_limit_event`, `system` events the filter ignores
- a successful `result`, and its final text (`read.stdout`, `bash.stdout`)
- an error `result`, truncated at 300 (`max-turns.stderr:3`)
- a stream that never carries one (`no-result.stderr:4`)
- a malformed line (`bash.stderr:9`)
