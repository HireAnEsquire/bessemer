# 13a — orphan classification, and the two modules that scan and reclaim

Status: Todo
Type: AFK
Blocked by: 11, 12

## Read this, and not more

**Budget: ~38k tokens.** This issue is sized to be done without reading the repository. Reading
past this list is what made its predecessor (issue 13) time out twice without writing a line.

- **[ADR 0004](../../../../docs/adr/0004-run-liveness.md)** — whole file, 10 KB. It is the
  specification; this issue only implements it.
- `bessemer/gc.py` and `bessemer/reclaim.py` — whole files, they are small and they are the
  work.
- `tests/test_gc.py`, `tests/test_reclaim.py` — the tests you extend.
- `CONTEXT.md` — the **In-flight** and **Orphan** entries only. They are new and they are the
  vocabulary this issue is written in.
- `tests/README.md` — **do not read it**. The one rule that binds you: the unit suite must pass
  with no Docker daemon, no network, and outside any git work tree. `tests/guard.py` enforces
  it, and `test_gc.py` and `test_reclaim.py` already show the shape.

Not needed: `dispatch.py`, `status.py`, `cli.py` and their tests (issues 13b and 13c), the F3
README, ADRs 0001–0003.

## What to build

ADR 0004's classification, and its first two consumers. The tracer found the bug this fixes:
a `kill -9` dispatcher leaves an `Up` container, and `gc` treats any `Up` container as a live
run — hiding the container, the checkout **and** the lock from the scan permanently.

**One pure function in `gc.py`**, answering *what state is this slug in, and why*, from the two
signals: the container's docker status and the lock's pid. Three outcomes — `IN_FLIGHT`,
`ORPHAN`, `UNVERIFIABLE` — each carrying a reason its callers can render.

The whole rule is ADR 0004's twelve-cell table. In one sentence: **`Up` is not proof of life;
`Exited` is proof of death**, and anything unverifiable is kept and said out loud.

`_lock_pid_alive` stops returning `bool`. **Absent** and **unreadable** are different facts and
today collapse into one `except OSError: return False`; the second must reach `UNVERIFIABLE`.
`reclaim._container_live`'s `bool | None` is the existing shape for this.

`gc.py` stays pure — its no-subprocess AST test must pass unchanged. The function is handed the
facts; it does not go and get them.

**Then the two consumers in this issue's scope:**

- `gc`'s scan — `IN_FLIGHT` hides the slug's artifacts as now, `ORPHAN` lists them,
  `UNVERIFIABLE` keeps and says so.
- `reclaim`'s per-item re-check — the same classification. Its first arm today is
  `if live: skip … container is live now`, and **that arm is exactly what must become
  conditional on the lock**. Leave it and the executor will refuse a plan the scan just made,
  which is this same bug one layer down.

Two operator sentences to pin as literals, because each is what someone reads before trusting a
deletion: an orphaned container removed **while still `Up`** (must say it was up *and* that the
dispatcher was gone — never just "removed container"), and an item kept because the lock could
not be read (must name the lock and say unverified).

## Acceptance criteria

- [ ] ADR 0004's twelve cells pinned by **one hand-written literal table** in a test — not
      derived from the code under test, and not restated per module
- [ ] The **reboot cell**: `Exited` container + a lock whose pid is **alive** (a reused pid)
      → orphan. This is the cell that stops the fix from hiding orphans the old code listed,
      and it is the one most likely to be skipped
- [ ] `UNVERIFIABLE` reaches `gc` and `reclaim`, and each keeps rather than acts
- [ ] The tracer's scenario, scripted: `Up` container + dead lock pid → `gc` lists container,
      checkout and lock; `gc --force` reclaims all three
- [ ] **The checkout in that scenario holds a commit the main repository does not, and after
      `gc --force` the branch points at it.** The tracer could not prove this live — its kill
      landed before the agent committed — and it is the property that makes deleting a checkout
      safe at all
- [ ] Container removal precedes checkout salvage, **asserted**. True today by accident of list
      order; under ADR 0004 it is load-bearing, because it stops a possible writer before the
      checkout is rescued
- [ ] `gc.py` still passes its no-subprocess AST test, unchanged
- [ ] The two operator sentences pinned as literals
- [ ] `make check` green

## Out of scope

- `dispatch.py`'s in-flight guard — **issue 13b**. Do not touch it; it will still refuse on a
  live container until then, which is the wedge, and 13b is where that is fixed.
- `status.py` rendering and the `stale_locks` → `orphan_locks` rename — **issue 13c**.
- Hardening the lock against pid reuse. ADR 0004 defers it with a trigger; adding it is a
  lock-format change nobody asked for.
