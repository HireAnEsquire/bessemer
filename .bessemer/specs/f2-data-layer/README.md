# F2 — data layer

The port source's data core, brought over with the test suite that makes the port
verifiable rather than hopeful: issue parsing and selection, the central ledger, resume
resolution, status and gc scanning and rendering.

Scope and sequence come from [ROADMAP.md](../../../ROADMAP.md); the decisions these issues
implement are in [ADR 0001](../../../docs/adr/0001-founding-decisions.md) and
[ADR 0002](../../../docs/adr/0002-skeleton-structure.md).

**Port source: `/Users/sbowles/hae`, branch `agentbox`, commit `e194121f75f4`.**
`.agentbox/tasklib.py` (2325 lines) and `.agentbox/test_tasklib.py` (3703 lines, 337 tests
in 56 classes). Every session working on F2 is launched with
`--add-dir /Users/sbowles/hae`.

## What F2 is not

**F1 found eleven defects, all of the same shape: a test that passed for a reason other
than the one in its name.** Mutation testing found them, because F1 wrote both the code and
its tests, so a weak test was the live risk.

**F2's risk is the opposite one.** The tests come from upstream and are the oracle; they
are not suspect. What can go wrong is *drift* — a test dropped, renamed away, split in half
and half-landed, or excluded for a reason that sounds better than it is. Nothing in F1's
review loop notices twelve tests becoming nine, and an agent comparing a 3703-line upstream
file by eye is being asked to do the one thing it is worst at.

So F2's primary control is not mutation. It is the **port manifest** (issue 00), a
committed hand-written list of all 337 upstream test names, each marked ported or excluded
with a reason. Mutation still applies to code F2 writes that upstream did not have.

## Decisions

Five, settled before any issue was written.

1. **The interactive picker is out of scope.** `CmdPickTests`, `PickBranchTests`,
   `PickTaskSourceTests`, `GumHelpersTests`, `PickBaseTests`, `PickResumeTests`,
   `PickIssuesTests` and `SummaryMenuTests` are 132 of the 337 tests — a human frontend for
   choosing what to dispatch.

   **The rule is what the test exercises, not what its class is called.** `SummaryMenuTests`
   was missed by the first draft of this list, which was assembled from class names: it
   patches `shutil.which` for gum detection and asserts on `gum_choose` arguments, so it is
   picker scope whatever its name suggests. Three in-scope classes — `SelectIssuesTests`,
   `ResolveSpecTests`, `CmdLedgerAppendLastBaseTests` — match a naive search for `gum`,
   `pick` or `which` only in *comment prose* ("unlike the picker's curated branch menu"),
   and stay in scope. A search that cannot tell code from a comment is the wrong instrument
   for this decision; read the class. It depends on dispatch existing and on a ledger with real runs in it, so
   porting it now means porting a terminal UI against a dispatcher that does not exist. It
   would also force a decision about shelling out to `gum`, a runtime binary the
   zero-dependency posture has never had to consider. Later feature, its own decision.

2. **`DispatchError` stays an exception.** ADR 0002's never-raise rule is about
   *resolvers*, and it exists because doctor must work when everything it checks is broken.
   Doctor never calls this code: the seven raise sites are dispatch-time caller errors
   ("issue 12 does not exist", "these dependencies are cyclic"), and ADR 0002 already has
   dispatch hard-erroring on exactly that class. Converting them would rewrite the tests
   that are the port's drift check, at the one moment the port is meant to be mechanical.
   The stray `print` in `append_ledger` goes: a library does not talk to a terminal.

3. **The drift control is a committed manifest, not review by eye.** See issue 00. It is
   F1's most-taught lesson — the literal the code cannot generate — applied at feature
   scale. It cannot read the port source at test time, because CI has no such directory, so
   the names are vendored into this repo. That is also what makes the exclusions
   reviewable: an exclusion is a line in a committed file with a reason beside it.

4. **`_migrate_legacy_ledgers` is deliberately dropped; gc is kept.** The migration is dead
   on arrival — bessemer has zero installs, so it can only ever run against files it never
   created, and its six tests would pass forever while proving nothing.

   **Its blast radius is eight tests, not six.** Upstream calls the function from six sites,
   and two further tests assert the behaviour through those callers rather than directly:
   `CollectRecentLedgerRecordsTests.test_triggers_legacy_migration_when_central_file_missing`
   and `RenderStatusTests.test_renders_legacy_per_dir_ledgers_via_migration`. Both write a
   per-directory `runs.jsonl` and assert the central file is created from it. They are
   excluded for the same reason and with the same reason recorded. Found in review: the
   manifest had them `pending`, which is a state issue 04 could never have flipped without
   resurrecting the dropped function, against a tracer that requires zero pending. A
   deletion decision scoped by counting a function's own test class will always undercount
   it; the scope is every test that reaches the behaviour. gc is different:
   its scan and render are pure functions over rows and paths, testable now against
   fixtures, and F3 needs them the day it lands.

5. **`cmd_*` tests split at the boundary ADR 0002 moved.** Each typically asserts two
   things: what was computed and what was printed. The computation half lands in the core
   module's tests unchanged; the rendering half becomes a CLI test against `bessemer/cli.py`,
   where F1 already has `render()` and `tests/test_cli.py`. The manifest records each as
   ported-split with both destinations, so a test that loses its rendering half on the way
   is visible rather than merely gone.

6. **Picker comments travel with the port, marked.** Two in-scope classes carry them:
   `CmdLedgerAppendLastBaseTests` at upstream `test_tasklib.py:317` ("unlike the picker's
   curated branch menu") and `ResolveSpecTests` at `:1637` ("the picker's existence check
   must mirror that exactly").

   An earlier draft said three, adding `SelectIssuesTests` — which carries no picker comment
   at all. It matched on the English word *which* at `:119`. That is the third time the same
   sloppy search produced a wrong scope in this feature, and it is why the rule below is
   stated in terms of reading the class rather than searching it. Each states a real constraint the picker will have to satisfy and nothing
   else records it, so deleting them loses a requirement; leaving them unmarked points a
   reader at code that is not in the tree. Keep the text, add a clause naming the picker as
   unported. **This is the porting issues' work, not the manifest's** — recorded here as a
   numbered decision precisely because it would otherwise live only in issue 00 and a
   transcript, and the sessions that must act on it read this file.

   Only 3 of 199 test bodies were read closely when this was found, and the search that
   found them cannot tell code from comment prose. Treat it as "nothing further surfaced".

## Sequence

`00` blocks everything. The four port issues are independent of each other and share only
the manifest.

| Issue | Scope | Classes | Tests | Blocked by |
|---|---|---|---|---|
| `00` | Port manifest: all 337 upstream tests, classified | 56 | 337 | — |
| `01` | Issues: parse, select, spec resolution, status writes | 9 | 38 | 00 |
| `02` | Ledger: read, append, branch helpers, resolve-last | 10 | 37 | 00 |
| `03` | Resume: resolve, dispatch action, branch naming | 9 | 42 | 00, 02 |
| `04` | Status: docker rows, locks, age, render | 12 | 50 | 00, 02 |
| `05` | gc: scan, summarize, render a plan | 7 | 30 | 00, 02, 04 |

**These counts are computed from the manifest, not eyeballed.** An earlier draft carried
four — 38, 44, 42, 81 — summing to 205 against a portable remainder of 199, because they
were guessed from class names before the manifest existed. The five sets above are disjoint,
cover all 47 pending classes, and sum to exactly 197. That was verified by partitioning the
manifest programmatically rather than by adding the column up, which is the same instrument
failure this feature has now produced four times.

`04` was one issue of 80 tests and 19 classes before the split — twice the size of anything
F1 shipped. gc separates cleanly: it is a scan that returns a plan, and it needs `04`'s
table and age helpers rather than the other way round.

Each port issue flips its own slice's manifest entries from `pending` to `ported` as part
of its acceptance; an entry cannot be flipped without a counterpart test, so the flip is
evidence rather than bookkeeping. The remaining `pending` count is F2's progress.

## Tracer

Zero `pending` entries remain in the manifest, and `bessemer status` renders real state
from a real ledger — not a fixture. That means a
ledger this repo actually wrote, which F2 cannot produce, since nothing dispatches until
F3. The tracer therefore writes one by hand through the ported `append_ledger` and renders
it, which is the honest version of the claim: the data layer round-trips.
