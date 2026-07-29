# 02 — Ledger: the append-only record of every run

Status: Done
Type: AFK
Blocked by: 00

## What to build

`bessemer/ledger.py` — the central ledger. Ports `central_ledger_path`, `read_ledger`,
`append_ledger`, `last_base_for_branch`, `newest_record_for_branch`,
`recent_distinct_branches`, `resolve_last`, `collect_recent_ledger_records`, and the
`cmd_ledger_*` and `cmd_last` handlers' computational halves.

The ledger is the only thing in bessemer that remembers. Everything F3 does — resume, "what
did I dispatch last", the status table — reads it, so a defect here is invisible until it
has been silently wrong for a week.

## The classes this issue owns

Ten classes: **29 ported, 8 newly excluded, 1 already excluded.**

`LedgerTests` (9) · `CentralLedgerPathTests` (1) · `NewestRecordForBranchTests` (2) ·
`RecentDistinctBranchesTests` (3) · `LedgerBranchHelpersTests` (5) ·
`LatestPerBranchTests` (3) · `ResolveLastTests` (3) ·
`CollectRecentLedgerRecordsTests` (3 of 4 — partial, see below)

### All three ledger subcommands are shims

`CmdLedgerAppendLastBaseTests` (6) and `CmdLastTests` (2) are **excluded** under decision 5.
An earlier draft of this issue said 37 tests and listed them as splits; that was written
before the shim rule existed. Measured, not assumed:

- `run.sh:1622` invokes `ledger-append`, `run.sh:881` invokes `ledger-last-base`
- `run.sh:697` invokes `last` and parses its output with `IFS='=' read -r`

The `key=value` output format exists **only so bash can read it**. All three cross the
bash-to-python boundary the rewrite deletes; a python dispatcher calls `append_ledger` and
`last_base_for_branch` directly. `last` backs run.sh's `--last` flag, which in bessemer is a
dispatch option rather than a subcommand.

**But check each of the eight before dropping it.** Their class docstring claims real
behaviour — "`--tasks-dir` resolves the central ledger location (its parent), `--task-dir`
becomes the recorded `task_dir` fact rather than a write location". `CentralLedgerPathTests`
covers the first. If the second, or the `--issue NUM=OUTCOME` record-building inside
`cmd_ledger_append`, is asserted nowhere else, **that computation lands in `ledger.py` with a
test bessemer writes** — not marked `ported_from`, because no upstream test covers it
separately and the manifest must not claim one does. Say which of the eight carried
behaviour and where it went.

Exclusions become **149**, pending falls **159 → 122**.

## What is deliberately not here

**`_migrate_legacy_ledgers` is dropped** (decision 4). Bessemer has zero installs, so it can
only ever run against files it never created.

Its blast radius reaches this issue. `CollectRecentLedgerRecordsTests` has four upstream
tests and you port three: `test_triggers_legacy_migration_when_central_file_missing` is
excluded, because it writes a per-directory `runs.jsonl` and asserts the central file is
created from it. Upstream calls the migration from six sites, so **the ported functions lose
that call**, and the behaviour goes with it. `read_ledger` on a tasks directory holding only
legacy per-directory files returns nothing, and that is correct for bessemer.

The manifest records this as a partial exclusion — `PARTIALLY_EXCLUDED_TESTS` — precisely
because a class-level count cannot see one test missing from an otherwise-ported class.

`CmdLedgerAppendLastBaseTests` carries a picker comment at upstream `test_tasklib.py:317`
("unlike the picker's curated branch menu"). Decision 6: keep the text, add a clause naming
the picker as unported.

## Corruption is data, not an exception

Upstream's `read_ledger` tolerates a malformed line rather than raising — a ledger is
append-only and written by concurrent runs, so a half-written final line is an ordinary
event and not a reason to take down the one command that would have shown it. Port that
behaviour and state it in the module docstring. `LedgerTests` covers it; if you find
yourself making it stricter, that is a decision and it goes back to the human.

## Acceptance criteria

- [ ] 29 tests land with assertions intact; 8 flip to `excluded` with the shim reason;
      `CmdLedgerAppendLastBaseTests` and `CmdLastTests` join `WHOLLY_EXCLUDED_CLASSES`
      (10 → 12) and `EXCLUDED_TEST_COUNT` moves 141 → 149. Pending falls 159 → 122
- [ ] **`append_ledger` prints nothing.** Upstream has one `print` in it; F2's decision 2
      removes it. A library does not talk to a terminal — that is ADR 0002's ops-as-library
      split, and the CLI is where output happens
- [ ] A malformed ledger line does not raise, and a test proves it against a file with a
      truncated final line
- [ ] **No new subcommand, and `tests/test_cli.py` is untouched.** `SurfaceTest` still pins
      `doctor` as the only one. Three commands for a dispatcher that does not exist would be
      a claim that bessemer can do something it cannot — F1's standing rule
- [ ] `bessemer/ledger.py` spawns no subprocess. It is file and JSON handling only
- [ ] Say what the ledger's concurrency story actually is, as measured from the port
      source's code — not what it should be. F3 will run concurrent dispatches against it
      and will need the real answer, and this is the last moment anyone reads that code with
      fresh eyes
