# bessemer roadmap

Direction as of July 2026. All founding decisions — language, distribution, config, UI posture,
security invariants, dispatch semantics — are settled in
[ADR 0001](docs/adr/0001-founding-decisions.md) and are not relitigated here. This file tracks
sequence: what gets built, in what order, and what is deliberately not being built yet.

The work is a **port**, not a redesign. Port source is pinned at hae commit `e194121f75f4`
(branch `agentbox`); that revision's behavior is the specification. Where this roadmap says a
feature is "ported", the question during implementation is "does it behave the same", not "is
this the best design" — design questions were answered during incubation and belong in an ADR if
reopened.

## The port (F1–F7)

Each F is one branch and one PR. **F1–F3 are built interactively** because bessemer cannot yet
dispatch itself; **F4 onward are dispatched through bessemer** — the tool builds itself, which is
also the only honest test of it. F7 lives in the hae repository, not this one.

### F1 — skeleton

Repo layout (the `bessemer/` package: cli, config, doctor, and the module boundaries that fall
out of them), `pyproject.toml` with the CLI entry point and `requires-python`, the config module
(two-layer TOML, the full precedence chain, base-branch auto-detect via `origin/HEAD`), doctor
ported, plus **bessemer's own minimal adapter** — a trivial Dockerfile and a no-op setup hook — so
that dogfooding can begin at F3 rather than waiting for F6's scaffolding.

*Tracer:* `uvx --from . bessemer doctor` runs green.

*Broken down:* [`.bessemer/specs/f1-skeleton/`](.bessemer/specs/f1-skeleton/) — eight issues.
Structure decided in [ADR 0002](docs/adr/0002-skeleton-structure.md).

### F2 — data layer

The python helper's data core ported with its test suite (337 tests): issue parsing and
selection, the central ledger, status, gc scan and render. This is the half that carries over
largely intact; the test suite coming with it is what makes the port verifiable rather than
hopeful.

*Tracer:* `bessemer status` renders real state from a real ledger.

### F3 — dispatch, one-off

The spine: clone, container lifecycle, setup hook invocation, the implement plus review loop with
verdict break, host-side push, draft PR open/update, notification, locks and logs. Every security
invariant in ADR 0001 lands here as explicit code with explicit tests — this is the feature where
weakening one would be easiest and worst.

*Tracer:* bessemer dispatches a one-off spec **on itself**. First dogfood, and it happens before
hae switches over.

### F4 — feature mode and resume

Feature runs (selection loop, host-side `Status:` writes, per-issue PR checklist, the stop-and-land
rule) and the resume family: `--resume <branch>` keyed off the ledger, `--last` as sugar over it,
feedback-only run mode for PR iteration, and `--feedback-edit` opening `git var GIT_EDITOR`
git-commit-style with an empty buffer aborting. Notification verbosity (`off|end|steps`) rides
along here.

Built by F3's dispatcher from this point on.

### F5 — picker, gc, dry-run

The picker, gc force paths, and dry-run parity. Two items fold in here that were pending at
extraction time:

- **Partial-flags picker** — flags supplied but incomplete (a feature named with no branch) drops
  into the picker with supplied values pre-resolved: their steps are skipped, the walk starts at
  the first missing step, and the echoed invocation still includes everything. TTY-only;
  non-TTY partial invocations keep the hard error, because a backgrounded run must never block on
  a prompt.
- **The prompts module evaluation.** The recorded UX requirement is that flows should feel like
  vite/sveltekit-class CLIs (clack aesthetic: cohesive vertical flow, glyphs, color, spinners).
  F5 evaluates a small stdlib-ANSI prompts module that would deliver that natively and **retire
  gum entirely** — zero optional binaries, testable rendering. Fallback if the homegrown
  rendering isn't clearly better: keep gum-as-optional with the numbered-prompt degradation.

### F6 — init and onboarding

`bessemer init` scaffolding and the read-only `init --diff` for adapter drift, the Dockerfile and
setup-hook templates, the two-line repo shim, and adopter-facing documentation — including the
operational reference that ADR 0001 defers to this point (picker step sequence, ledger default
chains, log layout, gc scan rules).

### F7 — hae switchover *(in the hae repository)*

Run the parallel-run acceptance gates from ADR 0001, then install the shim and config, port hae's
adapter (its Dockerfile and DB setup become adapter files), rename `.agentbox/` to `.bessemer/`,
delete the old core, and update hae's docs. The old core stays until all four dispatch gates land
clean; the switchover is reversible until it doesn't need to be.

## After the port

For adopters, in dependency order:

1. **Setup script v1** — Claude-only: token walkthrough, specs directory, defaults. Depends on
   F1's config module and F6's scaffolding.
2. **Multi-agent support (cursor/codex)** — the biggest unknown, and deliberately unscheduled.
   Hard prerequisite: ask the requesting developer what they actually want — which CLI, which
   headless flags, whether they need the review loop at all. Gets its own grill session; the
   provider abstraction is designed there, against a real consumer. **Model choice ships with
   it**, because its natural home is provider config. Escape hatch if it's needed sooner:
   a `BESSEMER_MODEL` env var passed through to the agent CLI.
3. **Setup script v2** — provider picker, on top of 2.

## Parked, with triggers

Parked means "decided not now, for a stated reason". Each has the condition that would reopen it.

- **Wave dispatcher**, including the sub-branch idea (unblocked issues of one feature running in
  parallel containers on sub-branches, with a merge agent reconciling). Parked because
  same-feature tracer-bullet issues *deliberately* overlap files — during incubation every issue
  in a feature touched both core files — so merge resolution would rewrite freshly-reviewed code
  and need its own review gate. Cross-feature parallelism already works today via per-branch
  locks. **Trigger:** a feature whose slices are genuinely disjoint by subsystem.
- **Dashboard.** **Trigger:** parallel-run monitoring pain. When it fires, the frontend contest is
  Textual vs a stdlib-http localhost web UI over the ops library — which is a frontend choice
  precisely because operations are a library. A web UI that can trigger dispatches needs its own
  security grill first: loopback-bound, token-gated, hardened against DNS rebinding and CSRF.
- **Jira (or other tracker) adapter** as an issue-file *source*, never as a direct dispatch path —
  the human approval step is what makes a spec self-authored. **Trigger:** an adopting team whose
  issue-writing genuinely starts in the tracker.
- **Go/Rust binary port.** Rejected in ADR 0001. **Trigger:** adoption scaling an order of
  magnitude, where the fixed costs of release infrastructure finally amortize.
- **Egress restriction.** Open egress re-accepted at adopter scope in ADR 0001. **Triggers**
  (binding on adopters, not just this repo): a real secret outside the LLM-API credential class
  enters the container, specs stop being self-authored, or a shared/central runner appears.
- Rejected outright, recorded so they aren't re-proposed: a review-run skill, and a sizing
  tripwire.

## Grill sessions

Design questions large enough to need their own session, in expected order:

1. ~~resume-feedback issue breakdown~~ — done, shipped in the port source.
2. ~~Extraction design~~ — done 2026-07-24, produced ADR 0001.
3. ~~F1 issue breakdown~~ — done 2026-07-27, produced ADR 0002 and
   `.bessemer/specs/f1-skeleton/`.
4. Setup script and config UX — after the port lands.
5. Multi-agent adapter — after the requesting developer's requirements are gathered.
