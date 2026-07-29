# 03 — Resume: what a re-dispatch does, and what a branch is called

Status: Done
Type: AFK
Blocked by: 00, 02

## What to build

`bessemer/resume.py` — deciding what happens when a run is dispatched against a branch that
already has history, and naming branches. Ports `resolve_resume`, `resume_dispatch_action`,
`ResumeInfo`, `_resume_issue_count`, branch-name suggestion, first-free branch naming, and
the protected-branch check.

**Blocked by 02, not just by 00**: `resolve_resume` reads the ledger, and porting it against
a ledger that does not exist yet means inventing a stand-in and then discovering it disagreed
with the real one.

## The classes this issue owns

Eight, 39 tests:

`ResolveResumeTests` (12) · `ResumeDispatchActionTests` (8) · `CmdResumeTests` (2) ·
`CmdResumeGuardTests` (2) · `ResumeIssueCountTests` (4) ·
`BranchNameSuggestionTests` (5) · `FirstFreeBranchNameTests` (4) · `IsProtectedTests` (2)

### `ResumeRunLabelTests` moved to issue 04

An earlier draft of this issue listed it here, making nine classes and 42 tests. It is
issue 04's, and the reason is not the dependency — it is what the function is.
`_resume_run_label` renders a picker menu label; its only upstream caller is
`_pick_resume_run`, which decision 1 does not port. It is not part of the
continue-versus-start decision at all. And its docstring states an invariant across the two
views — "reuses `_overall_outcome`/`_age` (the same rendering `status` uses) so the two
views can't drift" — which can only be *held* in the module that owns those helpers.

Landing it here would have meant one of: issue 04's status module importing its own helpers
back out of `resume`, or a second private copy of them, which is the drift the docstring
exists to prevent.

**`_resume_issue_count` stays**, even though its only caller is the same unported picker. It
is resume logic — it feeds `resume_dispatch_action`'s mandatory-feedback decision, and F3's
dispatch needs the same computation. Issue 02 landed `_ledger_branch_order` and
`_annotate_branch` on exactly this basis: in-scope tests plus pure logic, with the unported
caller named in the docstring. Do the same here.

## Why this is the sharp one

`resume_dispatch_action` is the function that decides whether a dispatch **continues** a
branch or **starts** one. Get it wrong in the continue direction and a run appends to
somebody else's work; get it wrong in the start direction and a resume silently discards
history the human expected to build on. It has twelve upstream tests around resolution and
eight around the action itself, which is upstream telling you where it found the edges.

`IsProtectedTests` is small and load-bearing: it is what stops a dispatch naming `main` as
its own branch. Two tests, and both matter more than their count suggests.

**`_migrate_legacy_ledgers` is dropped** (decision 4), and upstream's `resolve_resume` calls
it. The call goes; the behaviour goes with it. If any of the twelve `ResolveResumeTests`
turns out to depend on it, stop and report — the manifest says all twelve are portable, and
a thirteenth partial exclusion would be a finding about the manifest, not a licence to
exclude.

## Acceptance criteria

- [ ] All 39 tests are dispositioned — ported with assertions intact, or excluded under
      decision 5 with an honest reason — and pending falls by 39. An earlier draft said
      "land", which four of the 39 cannot do under this issue's own shim criterion.
      `ResumeRunLabelTests`'s three entries stay `pending` for issue 04 — the pending count
      is where that shows, and it should show rather than be absorbed
- [ ] **The continue-versus-start decision is stated in prose in the module docstring**, in
      terms a human can check against their own expectation. Every other property here is
      recoverable from the tests; this one is the thing a reader needs before they trust the
      tool with a branch they care about
- [ ] `bessemer/resume.py` spawns no subprocess. Branch *existence* is a git question and
      belongs to F3's dispatch, not here — if a ported function needs it, report that rather
      than importing `proc`
- [ ] `is_protected` is not the only thing standing between a dispatch and `main`, and the
      docstring says so. It is a data-layer predicate; ADR 0001's push-path protections are
      separate and land in F3. A reader who thinks this function is the safety mechanism has
      been misled by its name
- [ ] `CmdResumeTests` and `CmdResumeGuardTests` dispositioned per decision 5, which now has
      three outcomes: split, excluded-as-shim, or asserted-upstream-and-unreachable-here.
      This criterion said "split, both destinations recorded" before issue 02 found that a
      shim's class docstring can claim behaviour no split preserves — so decide it by reading
      `run.sh` for who invokes `resume`, not by this line. Whatever you choose, excluding a
      shim must not drop behaviour, and a manifest reason must not claim coverage that does
      not exist. `ported-split` has still never fired on real data; if it fires here, say so
- [ ] Report anything the ledger's shape made awkward. Issue 02 is committed by the time you
      run, so a bad fit is a real finding about a real interface rather than a guess
