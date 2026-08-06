# F3 tracer report — the first dogfood

Run 2026-08-06 by the repository's author, against
[`docs/f3-tracer-runbook.md`](f3-tracer-runbook.md), discharging
`.bessemer/specs/f3-dispatch/issues/12-tracer.md`.

**Verdict: F3 dispatches. All five runbook items executed; four behaved as specified, and the
fifth — SIGKILL mid-pass — refuted its own spec claim.** That refutation is the most valuable
thing the dogfood produced and is finding 1 below.

## What ran

One-off spec `.bessemer/specs/tracer-oneoff.md` (untracked, host-side, mounted read-only):

> Add a thisisatest.md file with a header and a bullet list. The bullet list must list the
> issues in `f3-dispatch/`.

Branch `tracer-dogfood`, base `main`, image `bessemer-agent`, four dispatches in all.

The agent's output is on the branch as `b901db4` — a 16-line `thisisatest.md` listing all
twelve F3 issues, each named correctly. Real work, correctly scoped, nothing beyond the spec.

## Evidence, and what backs each piece

**Two classes of evidence, kept apart deliberately.** Artifact-backed items were read off the
machine after the fact and are quoted verbatim. Operator-attested items were observed live by
the human running the steps; their console output is not reproduced here, because
single-generation log rotation had already destroyed it by the time the report was written —
which is finding 4.

### Artifact-backed

**The ledger, both landings** (`.bessemer/runs.jsonl`):

```
{"timestamp": "2026-08-06T19:12:52.063258+00:00", "branch": "tracer-dogfood", "base": "main",
 "spec": "tracer-oneoff.md", "feature": null, "issues": {}, "outcome": "approved",
 "pr_url": "https://github.com/HireAnEsquire/bessemer/pull/1", …}
{"timestamp": "2026-08-06T19:35:16.656871+00:00", … same shape …}
```

Written via `append_ledger`, one line per landing, none for the aborted or killed runs — F3
decision 6.4 holds against reality.

**The pull request:** `https://github.com/HireAnEsquire/bessemer/pull/1`, title
`[bessemer] tracer-dogfood`, **draft**, base `main`. Opened by the first run, updated by the
fourth. Closed deliberately at the end.

**The setup hook, in a real container** (surviving log, lines 21 and 26):

```
setup: installing uv into /usr/local/bin
setup: installed uv 0.12.2 (aarch64-unknown-linux-gnu)
```

The blocker issue 12 named — "bessemer's own override prompts say VERIFY = `make check`, but
`.bessemer/Dockerfile` installs no uv" — is cleared, and cleared in the place the issue said it
belonged.

**A whole run's six steps** (surviving log, `2026-08-06T15:33:04` onward):

```
(1/6) checkout: clone of 'tracer-dogfood' @ b901db4020   ... checkout ready (0s)
(2/6) container: bessemer-tracer-dogfood (bessemer-agent) ... container up (0s)
(3/6) setup: the adapter's hook                           ... setup done (2s)
(4/6) implement: claude pass                              ... implement done (45s)
(5/6) review: up to 3 round(s)                            review verdict: APPROVED (round 1)
(6/6) land: push + draft PR                               ... landed (existing PR updated) (24s)
DONE — 0 new commit(s) — draft PR: https://github.com/HireAnEsquire/bessemer/pull/1
```

Two paths not asked for by the runbook, exercised anyway and worth recording: **the existing-PR
update path** (`DONE_UPDATED`), and **continue mode's earlier-commits prompt line**, which
appears in both surviving logs because the branch already stood past the boundary.

**Item 4, duplicate dispatch while a run is live.** Refusal, from the *lock* layer:

```
!! another bessemer run (pid 75779) is already working 'tracer-dogfood' — wait for it or kill it
```

Preceded by one line, expected — `base_ref` consults the ledger before the guard, and printing
the choice is decision 4's named mitigation:

```
bessemer: --base omitted — using 'tracer-dogfood' branch's last recorded base from runs.jsonl: main
```

The first run was untouched. Pid `75779` and container `88091ef2c74c` were identical either
side of the refusal, and **no rotation occurred** — the decisive check, since the log's *bytes*
change continuously while a run is live (finding 2):

```
tracer-dogfood.log     19712 bytes   first line: 2026-08-06T15:33:04 == … run start
tracer-dogfood.log.1    6797 bytes   first line: 2026-08-06T15:17:12 == … run start
```

`.log.1` still holds the earlier run. Had the refused dispatch rotated, `.log.1` would hold the
15:33 run and `.log` would begin at the refusal. Structurally guaranteed too: `_rotate` runs
after `lock.acquire`, and both run after the guard.

**Item 3's aftermath**, measured ten minutes after the dispatcher was killed:

```
container   bessemer-tracer-dogfood   Up 10 minutes   (only process: sleep infinity)
lock        tracer-dogfood.pid = 70854              → dead
checkout    .bessemer/checkouts/tracer-dogfood      → present
bessemer gc → nothing to reclaim
bessemer status → Running: tracer-dogfood, Up 10 minutes
```

See finding 1.

**Final state:** no lock, no checkout, no container, working tree clean, `main` and
`tracer-dogfood` pushed.

### Operator-attested

- **Item 1, happy path** — ran green end to end; PR opened as a draft; `bessemer status` showed
  the run live during and landed after (F2 debt 3 against reality); `gc` reported zero orphans.
  The ledger line and the pull request above are its durable artifacts.
- **Item 2, hook forced nonzero** — dispatch aborted, the log was surfaced, `gc` still reported
  zero orphans. Its log has since been rotated away (finding 4).
- **Item 5, notification** — observed firing at landing.

## Findings

Ordered by what they cost. Each is a spec bug, a runbook bug, or a wording bug — the last
acceptance criterion of issue 12 is that these exist and are written down.

### 1. `gc` cannot see a SIGKILL leak, and never will — spec claim refuted

**The measurement.** Kill the dispatcher with `kill -9`. The container is not killed with it:
the image's `ENTRYPOINT ["sleep", "infinity"]` means it stays `Up` indefinitely. Then, in
`gc.collect_gc_items`:

```python
live_slugs = {c.slug for c in containers if is_live_status(c.uptime)}
```

- container rows are emitted only for containers that are **not** live;
- `if not d.is_dir() or d.name in live_slugs: continue` — the checkout is skipped;
- `if slug in live_slugs: continue` — the lock is skipped.

All three artifacts are invisible, permanently. `gc` prints `nothing to reclaim`, and
`gc --force` walks an empty plan. Meanwhile `dispatch`'s in-flight guard asks a *different*
question — `docker ps` on the container name — sees it, and refuses the branch. Correctly, and
that is what makes the state a wedge: **the branch cannot be dispatched, and `gc --force`
cannot clear it.** The only remedy is `docker rm -f` by hand — precisely the hand-cleanup of
credential-adjacent state that decision 1 pulled `gc --force` forward from F5 to prevent.

`bessemer status` reports the same state as `Running`, and suppresses its stale-lock warning
because a matching container exists. So neither command can tell a live run from a zombie one.

**What it refutes**, three places, all assuming the container dies with its dispatcher:

> F3 README, Tracer: "A run killed mid-pass (SIGKILL — the designed leak, decision 6.2's scope)
> → `gc` lists the orphans, `gc --force` reclaims them with salvage. **This is the designed leak
> meeting its designed remedy.**"

> Issue 12, runbook item 3: "collect `gc`'s orphan listing, then `gc --force`'s
> salvage-and-reclaim output. **This is reclaim's live proof.**"

> Decision 6.2: "SIGKILL leftovers are exactly what gc/reclaim exist for."

Not a port defect: the pin excludes live containers the same way (`run.sh:462–474`), so this is
an inherited hole rather than a translation error. Whether to inherit it is a decision, and it
is ADR-level rather than implementer-level — see the plan below.

**What the remedy actually looked like**, once the container was stopped by hand: `gc` listed
container, checkout and lock, and `gc --force` reclaimed all three with salvage. So
`reclaim.py` works; what is broken is the scan that decides what reaches it.

**A weaker point on the same run.** This kill landed early — the log's last line is
`claude > Bash: cat /spec.md` — and the checkout's tip equalled the branch tip already landed by
run 1. So salvage was a no-op fast-forward. "Salvage rescues work that exists nowhere else" is
therefore **still unproven live**, and a future tracer should kill a run after the log shows a
commit.

### 2. The runbook's duplicate-dispatch control was invalid

Item 4 said the log's checksum must be identical either side of the refusal. It cannot be: the
live run appends to that file continuously, so the checksum changes whether or not the refusal
touched it. A moving target is not a control.

The question that mattered was whether the refused run **rotated** the log. Replacement control:
**inode, first line, and monotonic size** — inode and first line unchanged, size only grows.
Fixed in the runbook.

### 3. "Nothing is touched before the in-flight guard" is wider than the code

Decision 6.1 reads as absolute. In `dispatch.dispatch`, three things precede the guard:
`mkdir` of `logs/`, `checkouts/` and `locks/`, and `git fetch origin`, which moves `origin/*` in
the main repository. Port-faithful — it is the pin's own step order — and none of it is the
run's own state, which is what the tier-2 scenario asserts about ("from the guard onward").
The prose is what overstates. Wording, not behaviour.

### 4. Single-generation log rotation destroys the runbook's own evidence

Five runbook steps on one branch means five dispatches writing one log path. Rotation is
`.log → .log.1`, one generation, so by step 4 the logs for steps 1 and 2 are gone. The runbook
says to paste evidence as you go; it does not say that failing to is unrecoverable, and it
should — or it should put each failure rehearsal on its own branch.

This report's split between artifact-backed and operator-attested evidence is a direct
consequence.

### 5. Two runbook steps were wrong before the run, and were fixed before it

Recorded because they were found by review rather than by running, and both would have made a
step measure nothing:

- Item 3 said `kill -9 %1`. `uvx` runs bessemer as a child rather than becoming it, so that
  kills the wrapper and leaves the dispatch running — its `finally` cleans up, nothing leaks,
  and a clean `gc` reads as a pass. Now: `kill -9 "$(cat .bessemer/locks/<slug>.pid)"`.
- Item 2 appended `exit 1` to the setup hook. The hook returns early once `uv` is installed, so
  the appended line is unreachable in a warm container and, in a cold one, fires only after a
  network install. Now: replace the file.

### 6. `DONE — 0 new commit(s)` alongside `landed (existing PR updated)`

Correct, and it reads as a contradiction of decision 8.4 ("zero commits past boundary: no push,
no PR"). Two different quantities: `new_commits` counts commits since the branch's *previous
tip*, decision 8.4 counts commits past the *boundary*. Worth one clarifying sentence wherever
8.4 is stated, because the operator reading the console has only one of the two numbers.

### 7. The agent process did not survive its dispatcher

After the `kill -9`, `docker top` showed only `sleep infinity` — the `claude` process was gone.
`bessemer/passes.py` carries upstream's measured comment that a host-side kill of the
`docker exec` client leaves the in-container process running and wedges the container. Adjacent
but not identical (that comment is about killing the exec client mid-run, this is the whole
dispatcher dying), so it is recorded as an observation rather than as a contradiction. If the
two are the same case, the comment's reasoning — which is why passes uses an in-container
`timeout` — needs re-measuring.

### 8. What the pre-tracer tier-3 suite caught, so the dogfood did not have to

Issue 12 named one blocker: no `uv`. There was a second — **the image had no `make`**, which is
what both prompt overrides tell the agent to run — and it would have failed the first dispatch
at VERIFY *after* paying for an implement pass. Both were fixed and pinned before the human ran
anything (`tests/integration/test_image.py`, `tests/integration/test_setup_hook.py`).

Recorded as evidence for decision 2's tier structure: the cheap tier found the expensive bug.
