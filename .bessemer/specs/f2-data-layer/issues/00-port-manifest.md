# 00 — Port manifest: the 337 upstream tests, classified

Status: Done
Type: AFK
Blocked by: nothing — this blocks every other F2 issue

## What to build

`tests/port_manifest.py` (or a data file beside it) listing **every one of the 337 test
names** in `.agentbox/test_tasklib.py` at commit `e194121f75f4`, each classified:

- `pending` — will be ported, has not been yet
- `ported` — lands in bessemer with a counterpart test
- `ported-split` — a `cmd_*` test whose computation half and rendering half land in
  different files (decision 5); both destinations recorded
- `excluded` — with a **reason**, in prose, that says why bessemer is better without it

**Everything starts `pending`, and that is the whole answer to "the manifest lands before
the port does".** At this commit nothing has been ported, so a check demanding a counterpart
for all 204 would be red from issue 00 until issue 04 — and a suite that is red on purpose
for the length of a feature is a suite nobody reads. The counterpart rule therefore applies
to `ported` and `ported-split` entries only.

That state cannot be abused in the direction that matters. Flipping an entry to `ported`
without writing the test fails immediately, because the check then demands a counterpart
that is not there — so the only available cheat is leaving entries `pending` forever, and
that is visible as a number rather than as an absence. **Each port issue flips its own
slice as part of its acceptance, and F2's tracer requires zero `pending` remaining.** Until
then the count is the feature's progress bar, which is a fair thing for it to be.

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

- The 132 picker tests (decision 1), which includes `SummaryMenuTests` — added after the
  first draft of that list was assembled from class names and missed it
- `MigrateLegacyLedgersTests`, 6 tests (decision 4)

Some in-scope tests carry comments referring to the picker ("unlike the picker's curated
branch menu", "the picker's existence check must mirror that exactly"). Those comments
describe code F2 is not porting. Leaving them is a reader pointed at something that does not
exist; deleting them silently drops a real constraint that the picker will have to satisfy
when it lands. Flag them in your report rather than deciding — it is one line either way and
it is not yours to pick.

*Settled: the comments travel, with a marker.* Three were found — in `SelectIssuesTests`,
`ResolveSpecTests` and `CmdLedgerAppendLastBaseTests` — and each states a real constraint
the picker must satisfy ("the picker's existence check must mirror that exactly"). Deleting
them loses a requirement that nothing else records; leaving them unmarked points a reader at
code that is not in the tree. Each keeps its text and gains a clause naming the picker as
unported. The search that found them cannot tell code from comment prose, and only 3 of 199
bodies were read closely, so this is "nothing further surfaced", not "exhaustive" — the port
issues will meet the rest one class at a time.

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
- [ ] **Flipping an entry to `ported` without a counterpart test fails.** This is what makes
      `pending` safe rather than an escape hatch — prove it by flipping one and showing the
      red
- [ ] **A counterpart must be a test that actually runs.** Deciding what "upstream-derived"
      means is only half the question; the other half is what counts as a counterpart
      *existing*, and the issue's silence on it is a hole. A marker on a method named
      `helper_not_a_test`, or in a module `unittest discover` never collects, satisfies a
      naive check while executing nothing — strictly worse than the "gutted to `pass`"
      weakness the docstring already names, and invisible to a reviewer reading assertions.
      Constrain both halves of `Counterpart`, and walk subpackages rather than the top level
      only: `pkgutil.iter_modules` does not descend, so a marker in `tests/sub/test_x.py` is
      unseen while its test still runs
- [ ] **The reverse direction holds: a test in bessemer's suite that came from upstream and
      is not in the manifest fails.** Otherwise a renamed port is indistinguishable from a
      test nobody ported, and the manifest silently stops describing the suite. How
      upstream-derived tests are identified is yours to decide — say what you chose and what
      it cannot see
- [ ] `make check` is green at this commit, with every entry `pending` and nothing ported.
      A feature-length red suite is a suite nobody reads
- [ ] The `pending` count is reported by the check's own output, so the number is visible
      without reading the file
- [ ] The manifest's own docstring states what it cannot prove: that a ported test still
      asserts what upstream's asserted
- [ ] Counts per upstream class are recorded and pinned, so twelve tests becoming nine is
      caught even if a name is reused
- [ ] Nothing in bessemer's runtime package imports the manifest — it is a test artifact
