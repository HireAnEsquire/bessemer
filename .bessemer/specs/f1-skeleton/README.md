# F1 — skeleton

The first feature of the port: enough package, config, and doctor for
`uvx --from . bessemer doctor` to run green, plus bessemer's own minimal adapter so
that F3 can dogfood a dispatch against this repo.

Scope and sequence come from [ROADMAP.md](../../../ROADMAP.md); the decisions these
issues implement are in [ADR 0001](../../../docs/adr/0001-founding-decisions.md) and
ADR 0002.

## How F1 is built

**Interactively, not dispatched.** Bessemer cannot dispatch itself until F3 exists.
Issues are still typed `AFK` — the content is fully specified and needs no human
judgement mid-task — but the *mechanism* is a human-driven session. From F4 on, issues
like these are dispatched through bessemer itself.

## These specs are tracked, unlike the port source

The port source gitignores its equivalent directory, because host-side `Status:` rewrites
churn tracked files. Bessemer commits them instead: they are the project's development
record, and a reader of this repo should be able to see what each feature actually was.

Two consequences, both deliberate:

1. **Dispatch-time `Status:` writes are expected working-tree churn.** After a dispatched
   run, modified spec files appear in `git status`. Commit them alongside the merge.
2. **Specs live inside the agent's checkout.** Because they are tracked, a dispatched
   agent can read every issue in a feature — and could write to them. From F3, the
   implement prompt declares this directory read-only to the agent, and review checks it.
   An agent-authored edit to a spec file is a review-stopping finding, not a nit.

## Tracer

`uvx --from . bessemer doctor` exits 0 with every check `ok` or `WARN`. This requires a
running Docker daemon, so it is a dev-machine gate. The unit-test suite is docker-free by
construction (issue 01a), so CI still has a real gate.

## One issue was split after the fact

Issue **01a** was a section of issue 01 until three review rounds found successive holes in
it — an `executable=` bypass, a platform-dependent `posix_spawn` decision, and a network ban
drawn at the wrong boundary. It is left as `01a` rather than renumbered so the split stays
legible; `Blocked by:` is what actually orders the issues, and numbers are labels.

The lesson generalizes past this issue: **a security control buried inside a larger issue is
reviewed as a detail of that issue.** When one turns up mid-feature, give it its own file
rather than growing the host issue's criteria list.
