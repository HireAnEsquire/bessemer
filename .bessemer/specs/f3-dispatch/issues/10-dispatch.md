# 10 — dispatch: the assembly

Status: Done
Type: AFK
Blocked by: 01, 02, 03, 04, 06, 07, 08, 09

## What to build

`bessemer/dispatch.py` (ADR 0003) and the CLI `run` subcommand. The depth lives in the
modules this composes; what this issue owns is the *order*, the lifecycle, and the
refusals. Oracle regions: run.sh:542–596, :779–832, :875–888, :927–1002, :1004–1021,
:1028–1190, :1432–1492 (single-pass shape), :1594–1644, :1726–1732.

- **CLI**: `bessemer run <spec> --branch <name> [--base <ref>]` — positional spec,
  `--branch` required, `--base` optional, nothing else (README decision 4).
  `SurfaceTest`'s hand-written pin grows `{doctor, status, gc} → + run` — the only
  loosening F3 makes. Bare `bessemer`, stdin not a TTY: help + exit nonzero. Spec
  resolution: **reuse `issues.spec_check_path`** + the one new existence guard
  (`!! spec not found: <path>`, oracle :818); update its docstring sentence "points at
  nothing in this tree today" — dispatch is now the live referent.
- **Guard sequence, ordered** (README decision 6.1): spec → branch exists → protected
  via `resume.is_protected` (**never a second `case master|main`**) → base≠branch →
  not-checked-out → preflight (docker, gh, credential via issue 09's shared resolver,
  image) → mkdir → `git fetch origin` → `BASE_SHA` → merge-base → inflight guard → lock.
- **Base chain** (README decision 4): flag > `last_base_for_branch` > env > local >
  committed > `origin/HEAD` auto-detect; the ledger consult logged with the ported line
  ("--base omitted — using '<branch>' last recorded base: X"). Defect 10 inherited, not
  fixed.
- **Lock**: `O_EXCL` atomic create — the **recorded divergence** fixing the pin's
  guard-then-write TOCTOU (README decision 6.3); on failure re-read the pid and report
  the live run. Liveness via `status.pid_alive`. Log rotation `.log → .log.1`, only
  when non-empty. Slug derivation ported from :1134 (owned pure function).
- **Lifecycle**: try/finally + SIGINT/SIGTERM handlers that raise (README decision 6.2).
  The finally: `checkout.salvage` (non-FF → keep + loud message) → container remove →
  lock remove → failure notification through issue 02's policy. The
  divergence-that-fixes note (:1025–1029's bash trap bug) goes in a comment.
- **Notification**: one end-of-run notification, unconditional, osascript via argv
  arguments (never interpolated into the script text, pin :1063), silently skipped
  without osascript, `|| true`-equivalent — it can never change the exit code.
- **Ledger**: append after landing, unconditional on landing content — a zero-commit
  landing appends with empty `pr_url`; a hard failure appends **nothing** (README
  decisions 6.4, 8.4). **Defect 9 armor**: every record field `str()`-converted; the
  tier-2 test dispatches with `Path`-typed inputs end-to-end. `source_dir` recorded per
  :826–832 (feature dir later; spec's parent now).
- **Debt 3 round-trip** (F2 decision 9, third entry): a faked-proc dispatch writes real
  log/lock/ledger files in a tmp tree; `bessemer status` over that tree renders the run.
  Container-name rendezvous asserted against `status.CONTAINER_PREFIX`.

## The four end-to-end failure scenarios (README decision 6.6) — owned here

Scripted tier-2 dispatches, one each: hook nonzero → abort + log surfaced; implement
fails 3 attempts → run fails, salvage ran, no PR, no ledger line; review capped →
**still lands** with the pinned needs-work footer; container dies mid-pass → abort,
no exec after the liveness check.

## Acceptance criteria

- [x] Guard-order sequence assertion over the recorded stream; each guard's refusal
      message ported
- [x] **Refused-dispatch absence assertion on both channels**: proc stream empty from
      the guard onward AND tmp tree byte-identical — no lock, no rotation, no log write
      (README decision 6.1)
- [x] Lock: two dispatches racing the same branch — `O_EXCL` loser reports the winner's
      pid; divergence note in the code
- [x] The four failure scenarios, plus the happy path, each asserting the full argv
      sequence and final state
- [x] Debt 3 round-trip green; mutate `LOGS_DIR` rendezvous and it fails (prove,
      restore)
- [x] Path-typed inputs end-to-end; the ledger line's JSON round-trips
- [x] **Resolved: keep** (README, issue 10's note; `PromptTest`) — the implement prompt's
      SPEC section names
      `/spec.md` and the dispatcher's generated preamble (the pin assembles at
      run.sh:1476) repeats it — inherited duplication flagged by issue 03's implementer.
      Keeping it is legal (it is the pin's shape); silently shipping both without a ruling
      is not
- [x] SurfaceTest pin updated by hand; base chain exercised at three depths (flag,
      ledger, auto-detect)
- [x] No stderr fragment in notification text or any agent-visible string — through
      issue 02's policy, asserted
- [x] `make check` green
