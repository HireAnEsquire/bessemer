# 03 — Resume: what a re-dispatch does, and what a branch is called

Status: Todo
Type: AFK
Blocked by: 00, 02

## What to build

`bessemer/resume.py` — deciding what happens when a run is dispatched against a branch that
already has history, and naming branches. Ports `resolve_resume`, `resume_dispatch_action`,
`ResumeInfo`, the resume-label and issue-count helpers, branch-name suggestion, first-free
branch naming, and the protected-branch check.

**Blocked by 02, not just by 00**: `resolve_resume` reads the ledger, and porting it against
a ledger that does not exist yet means inventing a stand-in and then discovering it disagreed
with the real one.

## The classes this issue owns

Nine, 42 tests:

`ResolveResumeTests` (12) · `ResumeDispatchActionTests` (8) · `CmdResumeTests` (2) ·
`CmdResumeGuardTests` (2) · `ResumeRunLabelTests` (3) · `ResumeIssueCountTests` (4) ·
`BranchNameSuggestionTests` (5) · `FirstFreeBranchNameTests` (4) · `IsProtectedTests` (2)

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

- [ ] All 42 tests land, assertions intact, and pending falls by 42
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
- [ ] `cmd_*` tests split per decision 5, both destinations recorded
- [ ] Report anything the ledger's shape made awkward. Issue 02 is committed by the time you
      run, so a bad fit is a real finding about a real interface rather than a guess
