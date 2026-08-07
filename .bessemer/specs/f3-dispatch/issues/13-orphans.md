# 13 — orphans: liveness is the dispatcher's, not the container's

Status: Split
Type: AFK
Blocked by: 11, 12

**Superseded 2026-08-07 by [13a](13a-orphan-classification.md),
[13b](13b-dispatch-guard.md) and [13c](13c-status-orphans.md).** The work is unchanged and
[ADR 0004](../../../../docs/adr/0004-run-liveness.md) is still its only specification; what
changed is how much of it one agent is asked to hold at once.

This file is kept rather than deleted, because how it failed is worth more than what it said.

## Why it was split

Dispatched twice. **Both attempts spent their whole budget reading and wrote nothing** — no
commit, no edit, the checkout byte-identical at the end. The first pass hit the 900-second
default; the second, given 3600, was still reading at thirteen minutes and had drifted into
`tests/test_prompts.py`, a module this issue never touches.

Measured afterwards, the reading this issue asked for:

| | ~tokens |
|---|---|
| Orientation the implement prompt mandates — F3 README, ADRs 0001–0004, `CONTEXT.md`, `tests/README.md` | 35k |
| `gc.py`, `reclaim.py`, `status.py`, `dispatch.py`, `cli.py` and their four test modules | 69k |
| **Total, before writing a line** | **~107k** |

That is first-pass reading only; re-reads, greps and tool output are on top. The three slices
come in at 30–38k each, and each names its read set explicitly rather than inheriting "read the
module and its test module" against a 55 KB module.

**It also broke this repository's own convention, visibly.** Every other F3 issue is about one
module — `04` checkout, `06` container, `07` passes, `08` landing, `09` doctor, `11` reclaim.
This one spanned five plus four test files, four to five times the width of anything else in
the feature, in a repository whose files are essays.

## What it cost, recorded so the lesson is not free

Two dispatches, ~45 minutes of wall clock, three implement attempts, and no diff. The retry
ladder is what made it expensive rather than merely slow: it assumes a **transient** failure — a
crash, a container hiccup, a flaky API — and re-runs the identical prompt with no memory of the
previous attempt. For "the task does not fit the budget", that spends three times as much to
reach the same result.

Two findings for the backlog, both from this issue rather than from the tracer:

1. **A budget-exhausted pass should not be retried the way a crashed one is.** Same ladder,
   wrong failure mode.
2. **The implement prompt's ORIENTATION section is a ~35k-token tax on every dispatch in this
   repository**, before the agent reads a single line it will change. It is adapter content
   (`.bessemer/prompts/implement-prompt.md`), so it is this repo's to tune — by naming sections
   rather than documents, the way 13a/13b/13c now do.
