# 13 — orphans: liveness is the dispatcher's, not the container's

Status: Todo
Type: AFK
Blocked by: 11, 12

## What this is

[ADR 0004](../../../../docs/adr/0004-run-liveness.md), implemented. Read it first — every
decision below is settled there, and this issue adds none. The tracer's finding 1
([report](../../../../docs/f3-tracer-report.md)) is what forced it: a `kill -9` dispatcher leaves
an `Up` container, `gc` treats any `Up` container as a live run and hides the container, the
checkout **and** the lock, while `dispatch`'s guard sees the same container and refuses the
branch. Un-dispatchable and un-reclaimable at once.

The F3 README's Tracer section carries the refuted claim with its refutation beneath it. Leave
both standing — the spec file is host-side state and not yours to edit.

## What to build

### 1. The classification, in `gc.py`, shared

One pure function answering *what state is this slug in, and why*, from the two signals — the
container's docker status and the lock's pid. Three outcomes: `IN_FLIGHT`, `ORPHAN`,
`UNVERIFIABLE`, each carrying a reason the callers can render.

**The whole rule is ADR 0004's twelve-cell table.** `Up` is not proof of life; `Exited` is proof
of death; anything unverifiable is kept and said out loud.

`gc.py` stays pure — its no-subprocess AST test must pass unchanged. The function is given the
facts, it does not go and get them.

`_lock_pid_alive` stops being a `bool`. **Absent** and **unreadable** are different facts and
today collapse into one `except OSError: return False`; the second must reach `UNVERIFIABLE`.
`reclaim._container_live`'s `bool | None` is the existing shape for this.

### 2. The three consumers, each owning its disposition

Import the classification; never restate the table (ADR 0004 argues this exception to the
package's restate-rather-than-import rule — do not "fix" it back).

- **`gc` / `reclaim`** — `IN_FLIGHT` hides the slug's artifacts as now; `ORPHAN` lists and
  reclaims; `UNVERIFIABLE` keeps, loudly. `reclaim`'s per-item re-check moves to the same
  classification, so it can no longer refuse a plan `gc` just made. Its first arm today is
  `if live: skip … container is live now`, which is precisely the arm that must become
  conditional on the lock.
- **`dispatch`'s in-flight guard** — `IN_FLIGHT` refuses (`INFLIGHT_LOCK` / `INFLIGHT_CONTAINER`
  unchanged in wording); `UNVERIFIABLE` refuses; `ORPHAN` **does not refuse**. The existing
  stale-cleanup step three lines after the lock (`container.remove` + `checkout.remove`, the
  pin's `:1161–1162`) then reclaims it, and the run log says so.
- **`status`** — Running lists in-flight runs only. An orphaned container renders as a marked
  line naming the remedy. With docker down, in-flight runs are still listed **from the locks**;
  what is unknown is the orphaned-container half, and the message says that rather than giving
  up on the section.

### 3. The rename

`status.stale_locks(locks_dir, running_slugs)` → `orphan_locks(locks_dir)`. The parameter goes:
a lock is orphaned when its pid is dead, and the container has nothing to do with it. Update the
rendered line with it. `stale` survives only where it means *out of date* — F5's
`image_staleness`, untouched.

### 4. The ordering, asserted

Container removal precedes checkout salvage. True today by accident of list order; under
ADR 0004 it is load-bearing — it stops a possible writer before the checkout is rescued and
removed. Assert it.

## Wordings to pin as literals

Each is what an operator reads before trusting a deletion, so each costs a deliberate test edit
to change. Compose them in the house style; the *shape* is fixed, the exact sentence is yours to
write and pin:

- an orphaned container removed while still `Up` — must say it was up **and** that the
  dispatcher was gone, never just "removed container"
- an item kept because the lock could not be read — must name the lock, and say unverified
- a dispatch reclaiming an orphan during its guard sequence — must appear in the run log, naming
  what it reclaimed
- `status`'s orphan line, and its docker-down line

## Acceptance criteria

- [ ] ADR 0004's twelve cells pinned by **one hand-written literal table** in a test, not three
      tables in three modules, and not derived from the code under test
- [ ] Each of the three consumers' dispositions tested against that classification, including
      the three `UNVERIFIABLE` arms
- [ ] The tracer's exact scenario, scripted at tier 2: `Up` container + dead lock pid → `gc`
      lists three classes, `gc --force` reclaims them, `dispatch` proceeds instead of refusing
- [ ] **In that scenario the checkout holds a commit the main repository does not**, and after
      `gc --force` the branch points at it. The tracer could not prove this live — its kill
      landed before the agent committed, so salvage was a no-op fast-forward — and "reclamation
      does not discard work that exists nowhere else" is the property that makes deleting a
      checkout safe at all. It should not wait on a well-timed kill
- [ ] Reboot-shaped case: `Exited` container + a lock whose pid is **alive** (a reused pid) →
      orphan. This is the cell that stops the fix from hiding orphans it used to list
- [ ] Container-before-checkout ordering asserted in the reclaim walk
- [ ] `orphan_locks` renamed, parameter dropped, no caller left passing container facts to it
- [ ] `gc.py` still passes its no-subprocess AST test unchanged
- [ ] The four operator wordings pinned as literals
- [ ] Tier 3 unchanged and still green (`make tracer-tests`) — this issue changes no image, no
      hook and no container argv
- [ ] `make check` green

## Out of scope, named so it is not drifted into

- **Hardening the lock against pid reuse** (a boot-unique token beside the pid). ADR 0004
  defers it with a trigger; adding it here is a lock-format change nobody asked for.
- **Making the container die with its dispatcher.** Rejected in ADR 0004 — it fights the
  exec-driven lifecycle.
- Anything in F4's resume family, and anything about `image_staleness`.
