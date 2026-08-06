# F3 tracer runbook — the first dogfood

Bessemer dispatches a one-off spec **on itself**. This is the run that turns "F3's tests pass"
into "F3 works", and it is human-run because it drives real credentials, a real push and a real
pull request — and because the evidence is observed rather than asserted.

Issue `.bessemer/specs/f3-dispatch/issues/12-tracer.md` owns it. The tier-3 suite
(`make tracer-tests`) is the scripted half; this file is the other half, and the two do not
overlap: everything below is either a real agent pass, a real pull request, or a signal no test
may send itself.

**Read this whole file before starting step 1.** Steps 2 and 3 are deliberate failures on a real
branch, and step 2 asks you to edit a committed file and put it back.

---

## Before you start

Five preconditions, each with the command that answers it. Paste the output of all five into the
report — a tracer that began on an unknown machine measured nothing.

```
uvx --refresh --from . bessemer doctor          # every line ok or WARN, exit 0
docker build --build-arg AGENT_UID="$(id -u)" -t bessemer-agent .bessemer
gh auth status                                  # authenticated, with a token that can open a PR
git status                                      # clean, and not on the branch you will dispatch
grep -c . .bessemer/.env                        # the credential is in the file, not just exported
```

`--refresh` is not optional (README, "the tracer command is itself the thing that lies"): `uvx`
keys its cache on package name and version, bessemer's version has not moved, and a stale wheel
exits 0 while running code you deleted.

`.bessemer/.env` matters specifically: a credential exported only in your shell makes preflight
warn and the container start without one, so the run would fail at the implement pass with an
authentication error nobody would read as a configuration mistake.

### The spec, and the branch

Write a **trivial but real** one-off spec — real work, small enough to review in a minute, and
outside the specs directory's issue folders so it is plainly a one-off:

```
.bessemer/specs/tracer-oneoff.md
```

Something like "add a `--version` example to README's development section" qualifies. Something
like "fix a typo you already fixed" does not: the run has to produce a diff the reviewer pass can
have an opinion about, or the review loop measures nothing.

Then the branch, created and pushed but **not checked out** — dispatch refuses a branch the main
repository has out, because it fetches into it:

```
git branch tracer-dogfood
git push -u origin tracer-dogfood
git switch main                                 # if you were on it
```

Record `git rev-parse tracer-dogfood` before the first dispatch. Every later "did anything move"
question is asked against it.

---

## 1. Happy path

```
uvx --refresh --from . bessemer run tracer-oneoff.md --branch tracer-dogfood
```

While it runs, from a second terminal:

```
uvx --refresh --from . bessemer status
docker ps --filter name=bessemer- --format '{{.Names}}\t{{.Status}}'
```

After it finishes:

```
uvx --refresh --from . bessemer status
uvx --refresh --from . bessemer gc
tail -n 40 .bessemer/logs/tracer-dogfood.log
tail -n 1 .bessemer/runs.jsonl
```

**Evidence to paste:**

- the draft pull request URL, and confirmation it is a **draft**
- `bessemer status` **during** the run, showing the run live — this is F2 debt 3 proven against
  reality rather than against a fake proc seam
- `bessemer status` **after**, showing it landed
- the ledger line
- `bessemer gc` reporting zero orphans
- the console's step lines, so the six steps and their durations are on the record

**What to look at, not just collect:** does the pull request body describe the actual diff? Did
the review loop end on `approved`, or on `needs-work` after the cap — and if the latter, is the
footer sentence in the pull request exactly the one F3 decision 6.6 pins?

---

## 2. Hook nonzero

Force the adapter's setup hook to fail. It is mounted into the container from **this working
tree**, not from the checkout, so an uncommitted edit is enough:

```
printf '#!/usr/bin/env bash\necho "tracer: forced hook failure"\nexit 1\n' > .bessemer/setup.sh
uvx --refresh --from . bessemer run tracer-oneoff.md --branch tracer-dogfood
git checkout .bessemer/setup.sh                 # PUT IT BACK. Do this before step 3.
```

**Replace the file, do not append to it.** The real hook returns early once `uv` is installed, so
an `exit 1` appended at the bottom is unreachable in a warm container and, in a cold one, fires
only after a network install nobody is waiting for. The failure has to be the first thing the
hook does, which is what makes the abort readable.

**Evidence to paste:**

- the abort message on the console, and the log path it names
- the tail of that log, showing the hook's **own output** — "surfaces the log" is the contract,
  and an abort with no output is a run nobody can debug
- `bessemer gc` still reporting zero orphans
- `git status` showing `.bessemer/setup.sh` restored

---

## 3. SIGKILL mid-pass — the designed leak meeting its designed remedy

This is `reclaim.py`'s live proof, and no tier-2 test can give it: `SIGKILL` is exactly the case
`try`/`finally` cannot cover, which is why `gc --force` exists (F3 decision 6.2).

Kill the **dispatcher**, not the container. Start it in one terminal:

```
uvx --refresh --from . bessemer run tracer-oneoff.md --branch tracer-dogfood
```

and from a second one, once the log shows the implement pass has started, kill **the dispatcher**:

```
kill -9 "$(cat .bessemer/locks/tracer-dogfood.pid)"
```

**Not `kill %1`, and not the `uvx` process.** `uvx` runs bessemer as a child rather than becoming
it, so killing the job kills the wrapper and leaves the dispatch running — the run then finishes
normally, its `finally` cleans up, nothing leaks, and a clean `gc` afterwards reads as a pass
while having measured nothing. The lock file holds the pid of the process that owns the run,
which is the one whose death has to leave the mess. It is also the pid `bessemer status` calls
live, so reading it is the same question asked by hand.

Then, in order:

```
docker ps --filter name=bessemer- --format '{{.Names}}\t{{.Status}}'
ls -l .bessemer/locks .bessemer/checkouts
uvx --refresh --from . bessemer gc
uvx --refresh --from . bessemer gc --force
uvx --refresh --from . bessemer gc
git log --oneline -3 tracer-dogfood
```

**Evidence to paste:**

- what leaked: the container, the checkout, the lock file
- `gc`'s orphan listing — the plan, before anything is deleted
- `gc --force`'s output: what it salvaged and what it removed
- `gc` afterwards, showing zero orphans
- **whether the killed run's commits survived.** Salvage is fast-forward-only; if the agent had
  committed before the kill, `tracer-dogfood` must now point at that commit. This is the one
  piece of evidence that says reclamation is safe rather than merely tidy.

If salvage *refused* — the checkout kept, with the loud inspect-manually message — that is also a
result. Paste it, say why the branch diverged, and do not delete the checkout by hand before
recording what was in it.

---

## 4. Duplicate dispatch while a run is live

The live proof of decision 6.1's refusal ordering: **nothing is touched before the in-flight
guard passes.** The evidence is not the refusal message; it is that the first run is untouched.

Start a run, and while it is live, capture the first run's state, dispatch the same branch again,
and capture it a second time:

```
SLUG=tracer-dogfood
shasum .bessemer/logs/$SLUG.log; cat .bessemer/locks/$SLUG.pid; docker ps -q -f name=bessemer-$SLUG
uvx --refresh --from . bessemer run tracer-oneoff.md --branch tracer-dogfood   # expect a refusal
shasum .bessemer/logs/$SLUG.log; cat .bessemer/locks/$SLUG.pid; docker ps -q -f name=bessemer-$SLUG
```

**Evidence to paste:**

- the refusal message, and which of the two guards fired (the lock's pid, or the live container)
- both triples, side by side: the log's checksum, the lock's pid and the container id must be
  **identical** before and after. A rotated log, a rewritten lock or a restarted container would
  each mean the refusal happened after something had already been done
- confirmation that the first run then finished normally

---

## 5. Notification

Observed, at landing, on the run from step 1 or a rerun of it. Paste a description or a
screenshot, and say which run it belonged to. A failed run notifies too — step 2 and step 3 both
fire one — so say which of the notifications you saw came from which run.

---

## Afterwards

- Close or merge the draft pull request deliberately. Nothing merges itself, and a tracer that
  leaves an open draft on `main` is a tracer nobody finished.
- Delete `.bessemer/specs/tracer-oneoff.md` and the branch, or keep both and say why.
- `git status` and `bessemer gc` one last time: the machine is as you found it.

## The last criterion, and the one that matters most

> Anything the runbook should have said but didn't — this is the first dogfood, and its gaps are
> F4's spec bugs.

Write that section. Every step above was written from the code and from the spec, by someone who
had not yet run it; the gap between that and what actually happened is the whole value of a first
dogfood. A step that was ambiguous, a command that needed a flag this file does not mention, an
error message that named the wrong thing, a piece of evidence that turned out to prove nothing —
each is a finding, and each belongs in the report next to what you did instead.
