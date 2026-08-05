# 02 — quotability: one policy for what a Result may say, and where

Status: Done
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

**Sharpened by this issue's implementation and ratified at its review (2026-08-05).** The
code is stricter than the two rows above in three places; the stricter rule is the
decision, and none of it may be "fixed" back to the table's wording:

1. *Host log quotes argv redacted, not raw.* The row permits argv, and a remote URL rides
   in an argument — so quoting argv raw would put the token in the log by the other
   channel. Same redactor, not a second one.
2. *Host log quotes the first line of stderr only.* `redact.detail` is `redacted` +
   `DETAIL_LIMIT` plus redact.py's own first-line rule, and is how doctor and resolve
   already spell this. Later lines are advice aimed at an interactive user.
3. *`DETAIL_LIMIT` caps the stderr and not the argv.* The cap is redact's rule about
   another program's output; argv is bessemer's own, a docker argv is legitimately long,
   and a truncated one hides the flag that mattered.

Agent-visible's "program name" is the basename of `argv[0]`: an absolute host path tells
an off-machine reader about the operator's filesystem and nothing about the failure.

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
