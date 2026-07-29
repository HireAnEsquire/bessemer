# 04 — Status: scanning what is running, rendering what has run

Status: Todo
Type: AFK
Blocked by: 00, 02

## What to build

`bessemer/status.py` — the status view. Ports `parse_docker_rows`, `stale_locks`,
`_overall_outcome`, `_age`, `_age_from_seconds`, `_mtime_age`, `_truncate`, `_format_table`,
`render_running`, `render_recent`, `render_status`, the live-status and pid-alive
predicates, and the summary-line helpers.

This is F2's tracer surface: `bessemer status` is what the feature has to make real.

## The classes this issue owns

Thirteen, 53 tests:

`ParseDockerRowsTests` (4) · `StaleLocksTests` (4) · `OverallOutcomeTests` (5) ·
`AgeTests` (6) · `FormatTableTests` (2) · `RenderRunningTests` (5) ·
`RenderRecentTests` (4) · `RenderStatusTests` (3 of 4 — partial, see below) ·
`CmdStatusTests` (3) · `IsLiveStatusTests` (3) · `PidAliveTests` (5) ·
`SummaryLinesTests` (6) · `ResumeRunLabelTests` (3 — moved here from issue 03)

### Why `_resume_run_label` is in the status module

It renders a picker menu label, and issue 03 listed it until the implementer found that it
calls `_overall_outcome` and `_age` — both of which are yours. Upstream's docstring makes
the sharing an invariant: "reuses `_overall_outcome`/`_age` (the same rendering `status`
uses) so the two views can't drift." That invariant can only be held where the helpers
live. Its only caller, `_pick_resume_run`, is unported under decision 1 — say so in its
docstring, the way issue 02 did for `_ledger_branch_order`.

## Rendering is a library function here, and that is not a contradiction

ADR 0002 puts rendering in the CLI, and this module has `render_*` functions. The line is
real: these return **`list[str]`**, they do not print. `render_status` builds the table;
`cli.py` writes it to a terminal. That is the same shape F1's doctor already has — the core
returns `list[CheckResult]` and `cli.render` prints — and it is why `CmdStatusTests` splits
per decision 5 rather than landing whole.

Say this in the module docstring. A future reader meeting `render_` in a library is entitled
to think the boundary leaked, and the answer should be there rather than in this file.

## Two things this module must not pretend to know

- **Docker rows arrive as text.** `parse_docker_rows` takes rows someone else fetched;
  nothing here spawns `docker`. F1's `tests/guard.py` denies `docker` to the suite outright
  and issue 06 established the pattern: the caller passes what it got. Keep the seam.
- **`docker_down` is a parameter, not a discovery.** `render_running` takes it. A status
  view that cannot reach the daemon still has to render, and it renders differently — that
  is the port source's design and it is the same "works when things are broken" principle
  as doctor's.

**`_migrate_legacy_ledgers` is dropped** (decision 4).
`RenderStatusTests.test_renders_legacy_per_dir_ledgers_via_migration` is excluded for that
reason and you port three of that class's four tests. The manifest records it as a partial
exclusion; do not "restore" it.

## Acceptance criteria

- [ ] All 53 tests land, assertions intact, pending falls by 53
- [ ] **The no-drift invariant is asserted, not just arranged.** `_resume_run_label` and the
      recent-runs table must both go through `_overall_outcome` and `_age` — a test that
      fails if either grows a private copy. A shared helper that two callers *happen* to use
      is one refactor away from two renderings, and the docstring claims otherwise
- [ ] No `render_*` function prints. A test asserts the return type is a list of strings,
      and `tests/test_cli.py` holds the printing half of `CmdStatusTests`
- [ ] `bessemer/status.py` spawns no subprocess — `parse_docker_rows` is handed rows
- [ ] `bessemer status` renders from a ledger written by issue 02's `append_ledger`, not
      from a fixture. That round trip is F2's tracer and this issue is where it first works
- [ ] Age rendering is pinned against fixed timestamps, never `now`. A test whose **expected
      value** is computed from the clock passes at every moment including the broken ones —
      six `AgeTests` exist because upstream found the edges, and a clock-relative port
      throws that away while looking identical
- [ ] **`ResumeRunLabelTests.test_feature_run_with_recorded_outcome` is time-bombed upstream
      and fails today.** Measured at `test_tasklib.py:1866`: the fixture hardcodes
      `"2026-07-22T00:00:00+00:00"` and expects `(2d ago, ✅ approved)`, while `_age` reads
      the wall clock, so the real return on 2026-07-29 is `(7d ago, ✅ approved)`.

      Port it with the **input** derived as `now` − 2 days and the **expected string left
      exactly as upstream wrote it**. That is not a violation of the criterion above, and the
      distinction is the whole point: a clock-derived *expectation* asserts nothing, because
      it recomputes whatever the code did; a clock-derived *input* against a hand-written
      expectation is what makes "2 days old renders as `2d ago`" checkable on every day
      instead of one. Say in the test which of the two you did and why.

      Its sibling at `:1877` uses `assertIn("spec my-task.md", …)` and never reads the age,
      so it is not affected — this is one test, not a class-wide problem. Found by issue 03's
      implementer while the class was still misfiled there; without that it would have landed
      red and read as a port error.
- [ ] Report what `render_status` does when the ledger is empty and when it is missing. Both
      are the ordinary state of a fresh adopter, and they are the first thing anyone sees
