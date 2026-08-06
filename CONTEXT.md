# Bessemer

Bessemer dispatches AFK coding agents: it takes a human-approved markdown instruction file, runs
an agent against an isolated copy of a repository inside a container, and lands the result as a
draft pull request for a human to merge.

## Language

### The unit of work

**Run**:
One execution of bessemer — a working branch plus the specs selected for it — producing one pull
request.
_Avoid_: job, wave

**Dispatch**:
The act of starting a run.
_Avoid_: launch, kick off, submit

**Spec**:
The markdown file mounted into the container as the agent's instructions.
_Avoid_: prompt, ticket, task file

**Issue**:
A spec that lives in a feature and carries workflow metadata — status, blocked-by, and whether it
is AFK or HITL.
_Avoid_: story, card

**Feature**:
A named folder of issues worked as an ordered sequence.
_Avoid_: epic, project

**AFK / HITL**:
Whether an issue may be given to an agent unattended, or requires a human in the loop.

### The isolated environment

**Checkout**:
The per-run working copy of the repository, produced by `git clone --no-hardlinks` and bind-mounted
into the container.
_Avoid_: worktree, clone (as a noun), workspace, sandbox

**Container**:
The per-run Docker container the agent executes inside, holding its own throwaway database.

**Slug**:
The branch-derived key naming a run's container, checkout, lock, and log
(`bessemer-<slug>`, `checkouts/<slug>`, `locks/<slug>.pid`, `logs/<slug>.log`). The
rendezvous identity that status and gc scan by.
_Avoid_: id, name

**Salvage**:
Fast-forwarding the working branch in the main repository from a checkout, before that checkout is
removed.
_Avoid_: rescue, recover

**In-flight**:
A run whose **dispatcher process is alive** — the pid in `locks/<slug>.pid`. Liveness is a
property of the dispatcher, not of the container: the container is one of the run's artifacts,
like the checkout and the lock, and it can outlive the process that made it.
_Avoid_: live, running, active

**Orphan**:
An artifact of a run that is no longer in-flight — a container, a checkout or a lock whose
dispatcher is gone. What `gc` lists and `gc --force` reclaims. **A container that is still `Up`
can be an orphan**, and that is the case the F3 tracer found (2026-08-06): the image's entrypoint
is `sleep infinity`, so a container outlives a killed dispatcher indefinitely.

The two signals compose asymmetrically, and the asymmetry is the whole rule: **`Up` is not proof
of life; `Exited` is proof of death.** An exited container settles it alone — no lock overrides
it, which matters because a lock file survives a reboot while the pid it names does not. A
container that is `Up`, or absent, settles nothing, and the lock is asked.
_Avoid_: stale, leftover, zombie, dangling

### Branches and landing

**Working branch**:
The pre-existing branch a run forks from, commits to, and pushes back to — and the identity of the
run itself.
_Avoid_: feature branch, target branch, agent branch

**Base**:
The ref a run's pull request targets and diffs against. Never pushed to, never forked from.
_Avoid_: parent, upstream, fork point

**Landing**:
The push-plus-pull-request step that ends a run; a run lands whatever is done, even when it
stops early.
_Avoid_: deliver, finalize

### The agent loop

**Implement pass**:
The agent invocation that does the work.

**Review pass**:
The agent invocation that critiques and fixes the implement pass's diff on the same branch.

**Verdict**:
The reviewer's machine-readable judgement; an approval breaks the review loop.
_Avoid_: score, grade

### Deployment shape

**Core**:
The bessemer package itself. Never enters a consuming repository — it is fetched by pin at run
time.
_Avoid_: engine, library, framework

**Adapter**:
The per-repository files under `.bessemer/` that teach the core about one specific repo: image
definition, setup hook, config, prompt overrides.
_Avoid_: plugin, integration, profile

**Setup hook**:
The adapter's idempotent, non-interactive script that prepares a checkout inside its container. A
nonzero exit aborts the run.

**Consuming repo**:
A repository that has an adapter and dispatches runs against itself.
_Avoid_: client, host repo, target

**Ledger**:
The append-only record of runs. Derived state — git remains the source of truth, so a stale or
deleted ledger degrades defaults, never correctness.
_Avoid_: history, database, log

## Relationships

- A **Feature** contains many **Issues**; every **Issue** is a **Spec**, but a one-off **Spec**
  need not be an **Issue**
- A **Run** targets exactly one **Working branch** and produces exactly one pull request
- A **Run** creates one **Checkout** and one **Container**, both keyed by the **Working branch**
- A **Run** processes its **Specs** in order, each through an **Implement pass** then a **Review
  pass**, looping until a **Verdict** approves or the cap is hit
- A **Checkout** is **Salvaged** into the main repository before removal
- A **Consuming repo** has exactly one **Adapter**; the **Core** is shared and never copied in

## Example dialogue

> **Dev:** "If I dispatch three **Issues** from one **Feature**, do I get three pull requests?"
>
> **Maintainer:** "No — that's one **Run**, so one **Working branch** and one pull request. The
> three **Issues** are worked in sequence inside a single **Container**. If you want them split,
> dispatch them as separate **Runs** on separate branches."
>
> **Dev:** "And if the second one fails review?"
>
> **Maintainer:** "The **Run** stops there and lands what's done — the first **Issue**'s commits
> are **Salvaged** out of the **Checkout** and pushed, and the pull request records why it
> stopped. The third **Issue** is untouched, so you resume it in a later **Run** on the same
> **Working branch**."

## Flagged ambiguities

- **"clone" vs "checkout"** — the pinned source uses `CHECKOUTS_DIR` and a gc artifact type of
  `checkout`, but phrases the central security rule as "never write-side git inside the clone".
  Resolved 2026-07-24: **checkout** is the noun, **clone** is only the verb. Founding docs
  corrected to match. "Worktree" is not a synonym — linked worktrees were deliberately abandoned
  because their `.git` file points at the host repo, which would expose host hooks and config to
  the container.
- **"task"** — the pinned source used it at two different levels: the dispatch unit ("branch =
  task identity", "per-task checkout") and, via `TASKS_DIR`, the directory of markdown files.
  Resolved 2026-07-24: **task is retired entirely.** The dispatch unit is a **Run**; the markdown
  files are **Specs**. The config key is therefore **`specs_dir`**, not `tasks_dir` — renamed at
  port time rather than later, because it is adopter-facing and a post-adoption rename would mean
  a config migration in every consuming repo. Note that `specs_dir` points at a directory holding
  both feature folders and one-off specs, which is why it is not `issues_dir` — and `issues/` is
  already the name of a subdirectory *inside* each feature.
