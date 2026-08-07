# 13b — the dispatch guard stops wedging the branch

Status: Todo
Type: AFK
Blocked by: 13a

## Read this, and not more

**Budget: ~35k tokens.** `dispatch.py` is 55 KB and `tests/test_dispatch.py` is 67 KB — reading
both whole is 30k tokens on its own and is what timed this work out when it was one big issue.
Read by region:

- **[ADR 0004](../../../../docs/adr/0004-run-liveness.md)** — whole file, 10 KB. The
  specification.
- `bessemer/gc.py` — the classification issue 13a landed. It already exists; you are its third
  consumer, not its author.
- `bessemer/dispatch.py` — **the guard sequence and the `try` block that follows it.** Find it
  from the two constants `INFLIGHT_LOCK` and `INFLIGHT_CONTAINER`, read from there to the
  stale-cleanup call inside the `try`. Do not read the pass loop, the landing call, the ledger
  append, or `_RunLog`.
- `tests/test_dispatch.py` — **`GuardTest` and `RefusedDispatchTest` only.** Locate them by
  class name; do not read the file end to end.
- `CONTEXT.md` — the **In-flight** and **Orphan** entries only.
- F3 README — **decision 6.1 only** (the run lifecycle's step order). It is one bullet in a
  45 KB file; find it, read it, stop.

Not needed: `status.py`, `cli.py`, `reclaim.py`, `tests/README.md`, ADRs 0001–0003.

## What to build

The wedge, closed. Dispatch already knows how to clean up after a dead run — `container.remove`
+ `checkout.remove`, three lines after the lock inside the `try` (the pin's `:1161–1162`). It
never runs, because the in-flight guard refuses first on any live container:

```python
if run(passes.liveness_argv(container=name), …).stdout.strip():
    raise Refusal(INFLIGHT_CONTAINER…)
```

So a run whose dispatcher was killed leaves a branch that can be neither dispatched nor
reclaimed. **That guard is the cause, not `gc`.**

Replace the direct docker question with issue 13a's classification, and give each answer its
own disposition (ADR 0004):

- `IN_FLIGHT` → refuse, wording unchanged (`INFLIGHT_LOCK`, `INFLIGHT_CONTAINER`)
- `UNVERIFIABLE` → refuse — nothing can be said about who owns the slug, and for a dispatch the
  do-nothing direction is refusal
- `ORPHAN` → **do not refuse.** Proceed; the stale-cleanup step already there reclaims it

Note the asymmetry is deliberate and is not a second dialect: *cannot verify* means **gc keeps
the artifact** and **dispatch refuses the run**. Both are "do nothing", pointed at what each
command is doing.

`_Lock.acquire` already takes over a lock whose pid is gone, deliberately, so that "a crashed
run must not need a file deleted by hand". This change makes the container arm agree with a
decision the lock arm made first.

**One new operator sentence, pinned as a literal:** an unattended dispatch reclaiming an orphan
during its guard sequence must say so in the run log, naming what it reclaimed. It is exactly
the event an operator should find afterwards, and nothing else in the log would explain why a
container vanished.

## Acceptance criteria

- [ ] The guard reads issue 13a's classification; it does not ask docker its own question and
      does not restate the twelve-cell table
- [ ] Scripted: `ORPHAN` (container `Up`, lock pid dead) → the dispatch **proceeds**, the stale
      cleanup removes container and checkout, and the run log carries the reclaim line
- [ ] Scripted: `IN_FLIGHT` → refused, wording unchanged, and the refused-dispatch absence
      assertions that already exist still hold on **both channels** — no proc calls from the
      guard onward, and the tmp tree byte-identical
- [ ] Scripted: `UNVERIFIABLE` → refused
- [ ] The reclaim log line pinned as a literal
- [ ] `make check` green

## Out of scope

- `status.py` and the `orphan_locks` rename — **issue 13c**.
- Anything in `gc.py` or `reclaim.py` beyond importing what 13a built. If the classification
  needs a change to serve this issue, that is a finding to report, not an edit to make here.
- The resume family, `--hard-reset`, and the feature loop. All F4.
