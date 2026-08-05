# 07 — passes: run_pass, the review loop, the verdict

Status: Todo
Type: AFK
Blocked by: 05, 06

## What to build

`bessemer/passes.py` (ADR 0003): `run_pass(...) -> PassResult` and
`review_loop(...) -> Verdict`. Oracle regions: run.sh:1090–1130 (claude_pass),
:1494–1527 (review loop).

- **run_pass**: prompt on stdin — never argv, never a shell string. The exec runs
  in-container `timeout "$PASS_TIMEOUT"` around the claude invocation — **in-container
  because a host-side kill of the `docker exec` client leaves the in-container process
  running and wedges the container** (upstream's measured operational knowledge; keep as
  a comment). The claude argv is a pinned literal:
  `claude --dangerously-skip-permissions -p --output-format stream-json --verbose`.
  Raw stream-json returns to the host and renders through issue 05's filter into the log;
  final text is 05's capture.
- **Cadence** (owned constants, pinned): 30s poll, heartbeat console line every 120s,
  3 attempts, 30s between retries. Sleep/clock injectable — the suite must not sleep.
- **Dead-container check before any retry** (pin :1122–1126): container gone → abort the
  run, never retry into it. Timeout (rc 124) and failure (rc≠0) each logged with the
  pin's message shapes; anything quoted from the failure goes through issue 02's policy.
- **review_loop**: up to `max_review_rounds`; per round, the review prompt + the
  boundary line; `<verdict>approved</verdict>` in the pass output breaks the loop;
  otherwise needs-work. Cap reached → needs-work verdict returned (landing still
  proceeds — that is issue 10's call, this module just reports). The verdict token
  parse is a pinned literal — it is the same token F1's REVIEWING.md already exercises.

## Acceptance criteria

- [ ] Claude argv literal test; the exec argv wraps it in `timeout <pass_timeout>` —
      both pinned
- [ ] Prompt reaches the recorded exec via stdin; no prompt content in any argv
- [ ] Scripted double scenarios: success first attempt; fail-fail-succeed (two retries,
      injectable sleep called twice); three failures → PassResult failure; rc 124 →
      timeout message; container-gone mid-retry → abort, and the double records **no
      further exec after the liveness check**
- [ ] Review loop: approved round 1 breaks; needs-work × cap returns needs-work; verdict
      token literal test (a `<verdict>approved</verdict>` embedded mid-output still
      matches — grep semantics, per the pin)
- [ ] Heartbeat: with a fake clock advanced 4 minutes, exactly two heartbeat lines
- [ ] `make check` green
