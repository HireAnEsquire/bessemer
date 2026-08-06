# Run liveness is a property of the dispatcher, not of the container

Date: 2026-08-06. Forced by the F3 tracer, which refuted a claim
[the F3 spec](../../.bessemer/specs/f3-dispatch/README.md) made about its own failure
rehearsal. Evidence: [`docs/f3-tracer-report.md`](../f3-tracer-report.md), finding 1.

Nothing here reopens [ADR 0001](0001-founding-decisions.md)'s security invariants or
[ADR 0003](0003-dispatch-structure.md)'s module map. It settles one question those two left
implicit and the port inherited without stating: **what makes a run live.**

## The measurement

`kill -9` on a dispatcher mid-pass. The container is not killed with it — the adapter image's
entrypoint is `sleep infinity`, so it stays `Up` indefinitely. Then:

- `gc.collect_gc_items` computes `live_slugs` from containers whose docker status starts with
  `Up`, and excludes the container, **the checkout and the lock** for any slug in that set. So
  it printed `nothing to reclaim`, and `gc --force` walked an empty plan.
- `dispatch`'s in-flight guard asked a different question — `docker ps` on the container name —
  saw the same container, and refused the branch.
- `status` printed the run under **Running**, ten minutes after its dispatcher died.

The run was therefore **un-dispatchable and un-reclaimable at the same time**, with `docker rm
-f` by hand as the only exit — the hand-cleanup of credential-adjacent state that F3 README
decision 1 pulled `gc --force` forward from F5 specifically to prevent.

Not a port defect: the pin excludes live containers the same way (`run.sh:462–474`). The hole
is inherited, and this ADR is the decision to stop inheriting it.

## Decisions

- **Liveness is a property of the dispatcher.** A **run** is an execution of bessemer
  (CONTEXT.md); the **container** is one of its artifacts, alongside the checkout and the lock,
  and it can outlive the process that made it. The authoritative signal is the pid in
  `locks/<slug>.pid`. *Rejected: container-first liveness with the zombie as a named
  exception* — the exception is the common case for every abnormal end, and a definition that
  needs an exception for its own failure mode is the wrong definition.

- **The two signals compose asymmetrically: `Up` is not proof of life; `Exited` is proof of
  death.** The container signal is demoted in one direction only, never removed. An exited
  container settles the question alone and no lock overrides it — which matters because **a
  lock file survives a reboot while the pid it names does not**: pids restart low, so a
  post-reboot `pid_alive` can be true about an unrelated process. Without this half, making the
  lock authoritative would *hide* orphans that today's code lists. The whole rule is twelve
  cells and every one is answered:

  | container | lock absent | lock, dead pid | lock, live pid | lock unreadable |
  |---|---|---|---|---|
  | **absent** | orphan | orphan | in-flight (pre/post-container window) | unverifiable |
  | **`Up`** | orphan | orphan — *the tracer's case* | in-flight | unverifiable |
  | **`Exited`** | orphan | orphan | orphan | orphan |

- **Anything that cannot be verified is kept, and said out loud.** `reclaim.py` already held
  this for the docker half ("docker could not answer, liveness unverified, not touching"); it
  now covers the lock half too. That forces `_lock_pid_alive` to stop being a `bool`:
  **absent** and **unreadable** are different facts and currently collapse into one `except
  OSError: return False`. Three-valued, the shape `reclaim._container_live`'s `bool | None`
  already set.

- **The classification is shared; the disposition is not.** One pure function in `gc.py` —
  ADR 0003's designated pure scanner — answers *what state is this slug in, and why*.
  `reclaim`, `dispatch` and `status` import it and each decides what to do about the answer:

  | Consumer | On `IN_FLIGHT` | On `ORPHAN` | On `UNVERIFIABLE` |
  |---|---|---|---|
  | `gc` / `reclaim` | hide / keep | list / reclaim | keep, loudly |
  | `dispatch` guard | refuse | proceed, reclaiming it | refuse |
  | `status` | Running | orphan line | say so |

  This is the one place the package's restate-rather-than-import rule is deliberately not
  applied, and the exception is argued rather than assumed. That rule exists so a producer and
  a consumer cannot agree by construction — correct for **literals**. A twelve-cell decision
  table restated three times reproduces *this very bug* one layer down: `gc` plans an item,
  `reclaim` skips it, and the operator reads a silence with no reason in it. The precedent is
  `container.liveness_argv`, whose docstring already draws the line — share the question, never
  the answer.

- **`gc --force` deletes an orphaned container that is still `Up`.** *Rejected: report-only.*
  A run with no dispatcher has no future — decision 6.4 writes no ledger line for a hard-failed
  run, and F4's `--resume` is documented as unable to recover a run that never landed — so
  nothing will ever collect that container's output, and if an agent process is still alive
  inside it, it is spending money against a run nobody will land. Refusing would mean the human
  runs the identical `docker rm -f`, which is the outcome this decision exists to remove.
  Uncommitted work is not a counter-argument: the checkout class already salvages
  fast-forward-only and then `rm -rf`s, so only committed work was ever rescuable.

- **Container removal precedes checkout salvage, asserted rather than emergent.** Today it
  holds by accident of list order — `collect_gc_items` appends containers, then checkouts, then
  locks; `render_gc_plan` preserves it; `reclaim` walks it. Under this ADR that ordering is
  load-bearing: it stops a possible writer before the checkout is rescued and removed. A
  property that must hold gets a test.

- **`dispatch`'s in-flight guard uses the same definition, which is what un-wedges the
  branch.** Dispatch already knows how to clean up after a dead run — `container.remove` +
  `checkout.remove`, three lines after the lock, the pin's `:1161–1162`. It never ran because
  the guard refused first. With the guard dispatcher-based, a dispatch reclaims its own orphan
  and proceeds, and `gc --force` becomes the tidy-up path for someone who is *not* dispatching
  rather than the only escape. `_Lock.acquire` already takes over a lock whose pid is gone,
  deliberately, so "a crashed run must not need a file deleted by hand" — the container arm now
  agrees with a decision the lock arm made first. An orphan reclaimed by an unattended dispatch
  is said loudly in the run log: it is exactly the event an operator should find afterwards.

- **`status` answers "is anything running" without docker.** Its Running section currently
  gives up entirely when the daemon is down, which was correct while "running" meant "a
  container is `Up`". Under this definition an in-flight run is a live pid in a lock file and
  needs no daemon; what a dead daemon costs is the *orphaned container* half. Keeping the old
  arm would leave `status` holding two definitions of running — dispatcher-based when docker
  answers, container-based when it does not — which is the conflation this ADR retires.

- **`stale_locks` becomes `orphan_locks` and loses its parameter.** CONTEXT.md now puts
  `stale` on **Orphan**'s avoid list, and the rename is not only vocabulary: the function's
  definition is *"lock files with no matching live container"*, which is the wrong signal. The
  right one needs no container at all — a lock whose pid is dead — so `running_slugs` goes.
  `stale` survives only where it means *out of date* rather than *abandoned*: F5's
  `image_staleness`.

- **Recorded divergence-that-fixes, for the parity gate.** ADR 0001's acceptance gate is
  outcome-based, and `gc` and `status` will now report differently from the pin on identical
  machine state. Same class as F3 decision 6.2's bash-`EXIT`-trap divergence, and the same
  argument: the pin's behaviour is the defect, and inheriting it means building a known wedge
  into hae's adoption. F7's parallel-run reviewer should expect this difference and not file
  it.

## Residual risk, accepted with a trigger

**An `Up` container whose lock names a reused pid reads as in-flight, and its orphan stays
hidden.** It needs an abnormally-ended run *and* a pid collision, and it errs toward hiding
rather than deleting — the safe direction. Hardening exists: write something boot-unique into
the lock beside the pid (acquisition time compared against process start time, or the container
id) and treat a mismatch as a dead owner. That is a lock-format change for a compound unlikely
event, so it is deferred. **Trigger:** any sighting of it, or a shared/CI runner where pid
churn is high enough to make the collision ordinary.

## Consequences

- The classification type in `gc.py` is imported by three modules — more coupling than this
  package has anywhere else. Bought deliberately: the twelve-cell table is then pinned by one
  hand-written literal table instead of three that can disagree, and a disagreement between
  them is invisible on a green run.
- `bessemer status` gains an output shape (the orphan line) and loses one (the docker-down
  give-up). Both are operator-facing text, so both are pinned as literals.
- A branch can no longer be wedged by a killed run. That is the property the tracer went
  looking for and did not find.
- `gc --force` can now kill a running container. The blast radius is bounded by the same
  liveness question asked twice — once at scan, once immediately before the act — and by
  salvage running fast-forward-only before any checkout is removed.
