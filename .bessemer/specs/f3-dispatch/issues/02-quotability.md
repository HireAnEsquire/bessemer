# 02 — quotability: one policy for what a Result may say, and where

Status: Todo
Type: AFK
Blocked by: —

## What to build

One function in `bessemer/proc.py` answering "what of a `Result` may be quoted in which
destination" (ADR 0003). Three F3 modules compose text from `Result`s — landing's PR
body, dispatch's notification, passes' logging — and without one owner the stderr
invariant is three local conventions.

The destination classes and what each may carry — this table is the owned literal:

| Destination | May quote |
|---|---|
| host log / operator console | argv, returncode, stderr **after `redact.redacted` + `DETAIL_LIMIT`** |
| PR body, notification, prompt (agent-visible) | argv program name and returncode only — **never stderr, never argv arguments** (a remote URL rides in arguments too) |

## Why proc.py and not redact.py

`redact.py`'s contract is to import nothing from the package (its own docstring);
importing `Result` there would break it. `proc.py` already owns `Result` and the
stderr-is-credential-bearing docstring, so the policy wraps `bessemer.redact` from
proc's side. Do not duplicate the regex — one redactor, per redact.py's docstring.

## Acceptance criteria

- [ ] The destination table above pinned by a hand-written literal test
- [ ] A `Result` whose stderr carries `https://x-access-token:ghp_x@github.com/o/r.git`:
      log destination gets the redacted form, agent-visible destinations get no stderr
      fragment at all — asserted on the returned strings
- [ ] Argv arguments never reach agent-visible destinations (fixture argv carrying a URL
      argument)
- [ ] `redact.py` unchanged — a test asserts proc's policy calls through it (one
      definition), not a second regex
- [ ] Mutation: invert the agent-visible arm to include stderr; the named test goes red;
      benign control (reorder two independent lines) stays green — report both
- [ ] `make check` green
