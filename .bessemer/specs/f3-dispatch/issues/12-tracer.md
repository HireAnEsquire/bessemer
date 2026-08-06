# 12 — tracer: first dogfood

Status: Done
Type: HITL
Blocked by: 10, 11

**Run 2026-08-06. Report: [`docs/f3-tracer-report.md`](../../../../docs/f3-tracer-report.md).**
All five runbook items executed. Four behaved as specified; item 3 refuted its own spec claim —
`gc` cannot see a SIGKILL leak while the container is `Up`, and the container is `Up` forever.
That finding is open and is the tracer's most valuable output. Eight findings in all; three have
already been folded back into the runbook.

The blocker this issue names was real and is cleared — and there was a second one it did not
name: the image carried no `make`, which is what both prompt overrides tell the agent to run.
Both were caught by the tier-3 suite before the human dispatched anything.

## What this is

Bessemer dispatches a one-off spec **on itself** — the F3 tracer (README, Tracer
section), run by the human on a scratch branch of this repo, plus the tier-3 suite that
lives outside `make check`. HITL because it drives real credentials, a real push, and a
real PR — and because the evidence is observed, not scripted.

**Known blocker to clear first** (found by issue 03's implementer, 2026-08-05): bessemer's
own override prompts say VERIFY = `make check`, but `.bessemer/Dockerfile` installs no uv
and `.bessemer/setup.sh` is a no-op — its own comment already records that installing uv
belongs in the hook. Until the hook installs uv, the first dogfood dispatch fails at
VERIFY. This issue owns making bessemer's own setup hook real enough for the tracer; the
hook edit is adapter content, verified here by the tracer run itself.

## What to build (the AFK-able part)

- The tier-3 test directory and make target (suggested `tests/integration/` +
  `make tracer-tests`), **outside the guarded suite** — `tests/guard.py` stays armed
  everywhere `make check` reaches; tier 3 is a separate target, never an exemption
  inside the guard (README decision 2).
- In it: the sudoers exact-match test on the built image, pinning **both** measured
  facts — a different script is refused, and `BASH_ENV` is stripped by `env_reset`
  (ADR 0001); a check that F1-07's `AGENT_UID=0` build-refusal test still runs; one
  scripted end-to-end failure path against a real container.
- The tracer runbook: the steps below, written so the human collects evidence, not
  vibes.

## The runbook (human-run)

1. **Happy path**: a real, trivial-but-real one-off spec; real branch; `bessemer run`.
   Collect: the draft PR URL; `bessemer status` output **during** (run live) and
   **after** (landed) — debt 3 against reality; the ledger line; `gc` showing zero
   orphans.
2. **Hook nonzero**: force the hook to fail; collect the abort message, the surfaced
   log path, and `gc` still showing zero orphans.
3. **SIGKILL mid-pass** — the designed leak meeting its designed remedy (decision 6.2's
   scope): `kill -9` the dispatch; collect `gc`'s orphan listing, then `gc --force`'s
   salvage-and-reclaim output. **This is reclaim's live proof — no tier-2 test can give
   it.**
4. **Duplicate dispatch** while a run is live — the live proof of decision 6.1's
   refusal ordering: collect the refusal message AND evidence the first run's log,
   lock, and container are exactly as before the refusal.
5. Notification observed at landing.

## Acceptance criteria

- [ ] Tier-3 target exists, runs green with docker up, is absent from `make check`, and
      the guard still denies docker to the unit suite (prove: a docker call in a unit
      test still fails)
- [ ] Sudoers test pins both facts on the built image
- [ ] All five runbook items executed with evidence pasted into the report
- [ ] Anything the runbook should have said but didn't — this is the first dogfood, and
      its gaps are F4's spec bugs
