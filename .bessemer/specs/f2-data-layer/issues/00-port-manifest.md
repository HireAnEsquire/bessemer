# 00 — Port manifest: the 337 upstream tests, classified

Status: Todo
Type: AFK
Blocked by: nothing — this blocks every other F2 issue

## What to build

`tests/port_manifest.py` (or a data file beside it) listing **every one of the 337 test
names** in `.agentbox/test_tasklib.py` at commit `e194121f75f4`, each classified:

- `ported` — lands in bessemer with a counterpart test
- `ported-split` — a `cmd_*` test whose computation half and rendering half land in
  different files (decision 5); both destinations recorded
- `excluded` — with a **reason**, in prose, that says why bessemer is better without it

Plus a test that reads the manifest and asserts every `ported` and `ported-split` name has
a counterpart in bessemer's suite, and every `excluded` name carries a non-empty reason.

## Why this is its own issue, and why it comes first

**It must be written by an agent that never ports anything.** A manifest produced by the
same session that does the porting is derived from the port: tests and manifest shrink
together and the check reports green while the suite is smaller than it should be. This is
the fixed-point defect F1 hit nine times in nine modules, at feature scale. Separate
session, committed before any port issue starts.

**It is vendored, not read from the port source.** CI has no `/Users/sbowles/hae`, and a
check that only runs on one laptop is not a check. Copy the names in; the copy is the
artifact.

**The counterpart rule is name-based, and that is a deliberate weakness.** A manifest can
prove a test with the right name exists. It cannot prove that test still asserts what
upstream's did — a ported test gutted to `pass` satisfies it. Say so in the module
docstring rather than letting a reader over-trust it. What closes that gap is the port
issues' own requirement to keep assertions intact, and a reviewer reading the exclusions.

## Naming

Upstream names are the key. Where a ported test is renamed — bessemer's suite favours
sentence-style names and upstream does not — the manifest records both, so the mapping is
the artifact rather than a coincidence of spelling.

## The exclusions this feature already knows about

Record these with the reasons from the [feature README](../README.md), not with a bare
flag:

- The ~127 picker tests (decision 1)
- `MigrateLegacyLedgersTests`, 6 tests (decision 4)

Anything else an implementer wants to exclude is a decision, and decisions go back to the
human — that is IMPLEMENTING.md's stop-and-raise rule, and F1 ended with an implementer
shipping a claim its own measurements disproved because it treated a decision as a detail.

## Acceptance criteria

- [ ] All 337 upstream test names appear exactly once, and a test asserts the count is 337
      — a hand-written literal, so a name dropped while transcribing is caught rather than
      silently reducing the denominator
- [ ] Every `excluded` entry has a prose reason; a test asserts none is empty or a
      placeholder
- [ ] The counterpart check fails when a `ported` test is deleted from bessemer's suite —
      prove it by deleting one and showing the red, then restoring
- [ ] The counterpart check fails when an entry is *added* to the manifest with no
      counterpart, not only when one goes missing. Closed against growth as well as
      shrinkage, for the reason issue 05 of F1 records
- [ ] The manifest's own docstring states what it cannot prove: that a ported test still
      asserts what upstream's asserted
- [ ] Counts per upstream class are recorded and pinned, so twelve tests becoming nine is
      caught even if a name is reused
- [ ] Nothing in bessemer's runtime package imports the manifest — it is a test artifact
