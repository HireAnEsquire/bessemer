# 13c — status stops calling a dead run Running

Status: Todo
Type: AFK
Blocked by: 13a

## Read this, and not more

**Budget: ~30k tokens.** The smallest of the three; keep it that way.

- **[ADR 0004](../../../../docs/adr/0004-run-liveness.md)** — whole file, 10 KB. The
  specification.
- `bessemer/gc.py` — the classification issue 13a landed. You are a consumer of it.
- `bessemer/status.py` — whole file, 17 KB. It is the work.
- `tests/test_status.py` — the tests you extend.
- `bessemer/cli.py` — **the `status` handler and `_docker_rows` only.** Nothing else in that
  file is yours.
- `CONTEXT.md` — the **In-flight** and **Orphan** entries only.

Not needed: `dispatch.py`, `reclaim.py`, their tests, `tests/README.md`, the F3 README,
ADRs 0001–0003.

## What to build

The tracer's most quietly wrong output: ten minutes after its dispatcher was killed, `status`
still printed the run under **Running**. Under ADR 0004 that row is not a run at all.

**Three changes.**

1. **The Running section lists in-flight runs only.** An orphaned container renders as a marked
   line beneath, naming the remedy — dropping it would hide the problem, which is worse than
   the lie. Shape, not the exact words:

   ```
   Running
     BRANCH          UPTIME        LOG
     other-branch    Up 2 minutes  tail -f …/other-branch.log
     ⚠ orphan: tracer-dogfood (up 10m, dispatcher gone) — reclaim with: bessemer gc --force
   ```

   This is not duplicating `gc`: `gc` answers "what can be reclaimed", with ages, sizes and a
   plan; `status` answers "is anything running", and the honest answer here is "no, but
   something is still powered on".

2. **`stale_locks` → `orphan_locks`, and the parameter goes.** Two reasons, and the second is
   the real one. CONTEXT.md now puts `stale` on **Orphan**'s avoid list — but the function's
   definition is also simply wrong: *"lock files with no matching live container"* is the same
   conflation ADR 0004 retires. A lock is orphaned when **its pid is dead**, and the container
   has nothing to do with it, so `running_slugs` has no reason to exist:

   ```python
   def stale_locks(locks_dir: Path, running_slugs: set[str]) -> list[str]:   # before
   def orphan_locks(locks_dir: Path) -> list[str]:                          # after
   ```

   Update the rendered line with it. Leave `image_staleness` alone — there `stale` means *out
   of date*, which is correct and unrelated.

3. **`status` answers "is anything running" without docker.** Today the Running section gives
   up entirely when the daemon is down. That was right when running meant "a container is
   `Up`"; under ADR 0004 an in-flight run is a live pid in a lock file and needs no daemon.
   What a dead daemon costs is the *orphaned container* half, and the message should say that
   instead of abandoning the section. Keeping the old arm would leave `status` holding two
   definitions of running — dispatcher-based when docker answers, container-based when it does
   not — which is the conflation this whole ADR exists to retire.

Both new operator lines — the orphan line and the docker-down line — are pinned as literals.

## Acceptance criteria

- [ ] Running lists in-flight runs only; an `Up` container with a dead lock pid renders as an
      orphan line, not as a run
- [ ] With docker down, in-flight runs are still listed **from the locks**, and the message
      names what is unknown rather than dropping the section
- [ ] `orphan_locks` renamed, parameter dropped, and **no caller left passing container facts
      to it** — the point of the rename is that it stops asking the wrong question
- [ ] `status` reads issue 13a's classification and does not restate the twelve-cell table
- [ ] The two operator lines pinned as literals
- [ ] `make check` green

## Out of scope

- `dispatch.py`'s guard — **issue 13b**.
- `gc.py` and `reclaim.py` beyond importing what 13a built.
- The Recent section, the ledger, and `_overall_outcome`. Untouched by ADR 0004.
