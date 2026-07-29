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

5. **`cmd_*` tests split at the boundary ADR 0002 moved — unless the subcommand is a shim
   across a boundary the rewrite deletes.**

   Upstream's `run.sh` is bash, so every value it needed from python came back through a
   subcommand invoked as a subprocess. Bessemer's dispatch is python and calls the function
   directly, so a subcommand that exists *only* for run.sh to call has no counterpart here:
   porting it ports the boundary the rewrite exists to remove, and adds a user-facing
   surface for a flow nobody can reach.

   The test to apply: **would a human ever type this?** If yes, it splits. If it exists so
   bash could reach python, it is excluded with that reason.

   First instance, found during issue 01: `CmdFeedbackEditStripTests` (1 test). Upstream's
   `cmd_feedback_edit_strip` is three lines — read stdin, print, return 0 — invoked at
   `run.sh:918`, and the computation it wraps already has its own class
   (`StripFeedbackEditTextTests`, 4 tests, ported). So there was never a split to make: the
   whole of that test is the shim. Excluded, taking exclusions to **141** and pending to
   **196**.

   Each remaining `cmd_*` class is judged the same way by its own issue. Settled so far:

   - **Excluded as shims**: `feedback-edit-strip` (issue 01), and `ledger-append`,
     `ledger-last-base` and `last` (issue 02). The last of those is the clearest case in the
     feature — `run.sh:697` parses its output with `IFS='=' read -r`, so the `key=value`
     format exists solely for bash to read.
   - **Still open**: `status` and `gc`, which are commands a human types, and are therefore
     the ones expected to split.

   **`PORTED_SPLIT` has still never fired on real data.** Issue 04's `CmdStatusTests` or
   issue 05's `CmdGcTests` will be the first, or it never fires at all — in which case the
   path should be deleted rather than left as machinery nothing uses.

   **Excluding a shim must not drop behaviour.** A shim's tests sometimes assert real
   computation that no other class covers — argument-to-record mapping, for instance. Check
   before excluding; where something is uncovered, it lands in the core module with a test
   bessemer writes, *unmarked*, because no upstream test covers it and the manifest must not
   claim one does.

   **There are three dispositions for a shim's assertion, not two.** Landed, covered
   elsewhere, or **asserted upstream and structurally unreachable here** — which is real and
   needs its own name. Issue 02's `assertFalse(central_path.exists())` after a rejected
   argument is one: `run_record` returns a dict and `append_ledger` writes where it is told,
   so "nothing was written" has no host in the new shape. Given only two dispositions, an
   implementer must either overclaim coverage or say nothing, and both hide the same fact.
   Name it in the exclusion reason, and never write a reason claiming a test covers what it
   does not — the manifest is the drift control, and a false reason is the failure it exists
   to prevent.

   Each genuine split typically asserts two things: what was computed and what was printed. The computation half lands in the core
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

   **Comments, in either file.** This decision was written about test comments, and issue 01
   promptly erased a *source* docstring constraint instead — upstream's
   "never at the source step, so an aborted picker leaves no orphan file behind" became a
   generic sentence with every picker reference removed, which is precisely the loss the
   decision exists to prevent, in the file nobody was checking. The rule covers
   `tasklib.py`'s docstrings as much as `test_tasklib.py`'s comments.

   Only 3 of 199 test bodies were read closely when this was found, and the search that
   found them cannot tell code from comment prose. Treat it as "nothing further surfaced".

7. **Three porting rules, learned in issue 01 and binding on the rest.**

   **Rename identifiers, never assertions.** `CONTEXT.md`'s vocabulary applies to names the
   port introduces — `_resolve_task_dir` becomes `source_dir`, `tasks_dir` becomes
   `specs_dir`. It does not apply to *behaviour*: `slugify("")` returns the literal
   `"task"`, a word `CONTEXT.md` retires, and it ships that way because upstream asserts it
   by name. Changing a value to satisfy vocabulary is changing what the code does, which is
   the one thing a port must not do quietly.

   **A helper another F2 issue needs is public.** Upstream's privacy conventions were
   written for one 2325-line file; bessemer splits it across five modules, so
   `issue_summary` and `slugify` are public because issues 03 and 04 call them. Decide by
   who calls it, not by the leading underscore upstream happened to use — and say in your
   report which ones you promoted, so the issue that consumes them is not guessing.

   **Destination module constants live in the manifest, beside `ISSUES` and `LEDGER`.** Each
   port issue adds one. Stated because issue 02 found the convention by imitation and issues
   03, 04 and 05 would each have rediscovered it.

   **Where there is no enumeration, invent none.** F1's rule that every list needs a
   hand-written literal exists because a test derived from the list it checks cannot notice
   the list shrinking. A module that owns no list needs no such pin, and manufacturing one
   to satisfy the rule produces a check with nothing behind it. `bessemer/issues.py` owns
   two single-value constants and that is the whole of it.

8. **Upstream defects are ported, reported, and left.** Each is reproduced against the port,
   not inferred. Numbered rather than counted, because the first draft of this decision said
   "six" and listed four — an unnamed defect is exactly the discovery this list exists to
   prevent.

   1. `_leading_number` scans rather than anchors, so `Blocked by: 2026-07-22` parses as
      blocker `2026`, and `--issues "v2"` selects issue 2.
   2. **Duplicate issue numbers silently drop a file, and falsely open a blocker gate.**
      `by_number = {i.number: i for i in all_issues}` — last file wins. With `01-alpha.md`
      (Todo) and `01-zeta.md` (Done), `02-gamma.md`'s `Blocked by: 01` is judged satisfied by
      the *surviving* 01, so 02 dispatches on top of work that never ran, and `--issues 01`
      returns `01-zeta.md` with no error. `select_issues`' own docstring says an explicit
      list is a human's instruction whose unsatisfied blocker is an error raised before any
      container starts; ambiguity is the one case that design should refuse and does not.
      **The worst of these, and the one F3 will meet first.**
   3. `set_status` rewrites the whole file through `splitlines()`/`join`, so CRLF becomes LF
      and trailing blank lines collapse — a status write silently reformats a file it did
      not otherwise touch.
   4. `set_status`' insert position is "after the first line", not "after the title".
   5. `parse_issue`'s title heuristic breaks when a heading equals its own filename stem.
   6. A branch name containing `/` crashes `materialize_ad_hoc` — `FileNotFoundError` on
      `ad-hoc/T-feat/login.md`. Slashes in branch names are ordinary git, and `CONTEXT.md`
      makes the working branch a run's identity.
   7. `read_text`/`write_text` are called with no `encoding=`, so behaviour follows the
      ambient locale: under `LC_ALL=C PYTHONUTF8=0`, `parse_issue` raises
      `UnicodeDecodeError` on an ordinary UTF-8 issue file. `bessemer/config.py` treats this
      as a first-class case with its own `except` clause; a module claiming "the same
      posture as `bessemer.config`" must not diverge on it silently.
   8. `materialize_ad_hoc` uses `datetime.now()`, so `TZ` changes the *date* in a filename
      that is the run's identity. No test catches it because all three upstream tests pass
      an explicit timestamp, never exercising the default branch.

   9. **`append_ledger` is not non-raising.** It catches `OSError`, so a record holding a
      non-serializable value takes `json.dumps`' `TypeError` straight out — reproduced:
      `TypeError: Object of type PosixPath is not JSON serializable`. `Path` is exactly what
      F3 is likeliest to pass for `source_dir`, and the docstring says this call happens
      *after* the push and the pull request and must never undo them. It fails at the one
      moment its contract exists to protect. **The most dangerous entry on this list for
      F3.**
   10. **The ledger holds two disagreeing definitions of "newest".** `resolve_last`,
       `newest_record_for_branch` and `last_base_for_branch` use *file order*;
       `collect_recent_ledger_records` sorts by *timestamp*. Reproduced: with a 10:00 record
       written before an 09:00 one, `resolve_last` returns the 09:00. So `--last` and the
       status table's top row can name different runs from one ledger. Live triggers: a clock
       step, a record with no timestamp, or two concurrent dispatches where one stamps early
       and writes late. Both orders are documented in their own docstrings and nowhere
       against each other.

   11. **A non-string `timestamp` takes down the whole status read.**
       `read_ledger` accepts `{"timestamp": 1753142400}` as data, then
       `collect_recent_ledger_records` sorts and dies: `TypeError: '<' not supported between
       instances of 'int' and 'str'`. Upstream's docstring claims "a missing or unparseable
       timestamp sorts last rather than raising", which is false for this case. It is issue
       04's status-table read path — the one command that would have shown a damaged ledger
       is the one the damage kills.
   12. **One non-UTF-8 byte erases the entire ledger, silently.** `read_ledger` calls
       `read_text()` with no `encoding=`, so the platform default applies, and the
       `UnicodeDecodeError` handler degrades the *whole file* to no records. Measured under
       `LC_ALL=C PYTHONUTF8=0`: a ledger of two well-formed lines, one carrying a non-ASCII
       branch name, reads back as **zero records** — the ASCII line included. Not "a corrupt
       ledger reading as empty": total history loss from one byte in one line. Same class as
       defect 7.
   13. **The resume menu and the resume itself can disagree about which record is newest.**
       `resolve_resume` recovers through `newest_record_for_branch`, which is
       `reversed(read_ledger(...))` — file order, on the convention that append order is
       recency order. The menu that hands it a branch is built from
       `collect_recent_ledger_records`, which *sorts by timestamp*. Nothing reconciles them,
       so under a clock skew, a hand-edited ledger, or two hosts writing one file, the run the
       human saw described and the run they resume are different records for the same branch.
       This is defect 10's second face — the same file-order-versus-timestamp-order split,
       reaching a decision the human made rather than only a `--base` default. Found in issue
       03; the picker half is unported, so today only the ordering assumption ships.
   14. **A ledger that cannot be decoded is indistinguishable from a branch that never ran.**
       Defect 12 makes `read_ledger` return nothing for the whole file, so
       `newest_record_for_branch` returns `None`, so `resolve_resume` raises
       `no ledger record for branch '<b>' — recent branches in the ledger:\n  (none recorded)`
       for a branch that has run many times. The message names the true cause of neither. Not
       a new defect so much as defect 12's user-visible face, recorded here because it is the
       one a human actually meets.

   **Concurrency, measured against the port source** — 8 processes, 480 records, APFS, line
   sizes from 130 B to 8 MB: no interleaving and no loss. `O_APPEND` makes each write's
   offset claim atomic and a regular local file never returned a short write. No lock, no
   `fsync`. The unmeasured limit is a *short* write — full disk, NFS — which splits a record
   across two offsets so halves can interleave. The design answers that rather than
   preventing it: `read_ledger` skips the torn line, costing a `--base` default and never a
   landing. Appends are ordered by completion, not by timestamp, which is defect 10.

   Two claims deliberately **not** on this list, so nobody re-hunts them: `materialize_ad_hoc`
   with a `../../` branch is not a path traversal (it fails like any other slashed branch),
   and upstream's unparenthesised `except OSError, UnicodeDecodeError:` is not a syntax
   error — PEP 758 made it legal in 3.14, which is bessemer's floor.

   None were fixed, and that is correct. **A port that improves things cannot be verified
   against the suite that came with it** — every fix silently invalidates the oracle, and
   F1's whole record says the confident local improvement is where defects come from. They
   are recorded here so F3 and F4 meet them as known and dated, not as discoveries.

9. **A git question inside a data-layer function becomes a parameter, and F3 owes the
   answer.** F2's modules spawn no subprocess, which is not a style rule: it is what lets
   their tests be pure and fast, and it is the seam ADR 0002 draws. Where a ported function
   asked git something, the question is lifted into the signature rather than pulled down
   into the module.

   The case, from issue 03: upstream's `_first_free_branch_name(suggestion)` calls
   `_branch_exists` and `_remote_branch_exists` internally. Bessemer's takes
   `(suggestion, *, local_exists, remote_exists)`. **Two predicates, not one combined
   `taken`** — collapsing them makes the local-collision and remote-collision tests the same
   test, and upstream wrote both because a branch already pushed from elsewhere is as much a
   collision as a local one.

   So **F3's dispatch is the debtor here.** Every such parameter is a git call F3 must make
   and pass in, and a default would be worse than the debt: it would let dispatch forget to
   ask and get a plausible answer. When F3's issues are written, this list is what they have
   to discharge.

   A second entry on the debtor list, found by issue 03's review: **`ResumeInfo.source_dir`
   has never been asserted by any test in either repo.** Upstream's `cmd_resume` prints
   seven fields and its tests asserted six; the mutation `source_dir=""` at both
   construction sites leaves bessemer's 44 resume tests green. F3's dispatch consumes it —
   the F3 issue that does must land the assertion upstream never wrote.

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
