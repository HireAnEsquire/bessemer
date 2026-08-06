# 11 — reclaim: gc --force, and the pins that come first

Status: Done
Type: AFK
Blocked by: 04

## What to build

`bessemer/reclaim.py` (ADR 0003): `execute_gc_plan(plan) -> ReclaimReport`, and the
`--force` flag on the existing `gc` parser. Oracle region: run.sh:422–523. `gc.py` stays
pure — its AST no-deletion test must still pass unchanged; the deleter lives here and
consumes the plan as data.

## The debt 4 pins come BEFORE any rm -rf trusts the plan

F2 decision 9, fourth entry — two suite gaps, measured by mutation, that this issue must
close first, as tests over `gc.py`'s existing behavior:

1. `live_slugs` including *stopped* containers stays green today — pin that a checkout
   whose container has exited IS an orphan.
2. `render_gc_plan`'s class filter is never exercised alone — pin it with a fixture item
   that is unknown-class but deletable.

Land these two tests, then build the executor.

## The executor

Per item, in plan order (pin :462–520):

- **Re-check liveness immediately before touching anything** — the plan is a scan-time
  snapshot: live container (`docker ps -q -f name=^bessemer-<slug>$`) → skip, say so;
  live lock pid (`status.pid_alive`) → skip, say so (the clone-before-run and
  cleanup-after windows, pin comment :471–474).
- **container** → `docker rm -fv bessemer-<slug>`; failure reported, run continues,
  exit ends nonzero.
- **checkout** → `checkout.read_branch`: detached → skip LOUDLY (inspect manually);
  else `checkout.salvage`: FF → `remove` + report; non-FF → skip LOUDLY — **gc never
  discards unpushed agent work**. Salvage is issue 04's single definition; importing it
  is the point (README decision 3).
- **lock** → remove the pid file.
- **Docker down**: listing works with the warning (already F2); `--force` **refuses
  entirely** — liveness unverifiable means nothing is deleted (pin :453–456).

Logs are never touched — `gc` never deletes logs is an ADR 0001 invariant; assert the
absence (no argv and no file operation ever names the logs dir).

## Acceptance criteria

- [x] The two debt 4 pins land first and are named in the report; each shown red against
      a mutated `gc.py` copy, green against the real one
- [x] Scripted scenarios: stale plan where an item went live between scan and delete →
      skipped, nothing touched for it; detached checkout → kept + loud; non-FF → kept +
      loud; clean orphan set → all three classes reclaimed in order
- [x] Docker-down `--force` refusal: no deletion argv recorded at all, exit nonzero,
      ported message
- [x] Logs-untouched absence assertion
- [x] Partial failure (container rm fails) → remaining items still processed, exit
      nonzero
- [x] `make check` green
