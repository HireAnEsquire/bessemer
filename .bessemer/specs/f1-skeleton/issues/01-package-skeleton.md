# 01 — Package skeleton, pyproject, CLI entry, test discovery

Status: Todo
Type: AFK
Blocked by:

## What to build

The empty-but-runnable shell everything else lands in.

- `bessemer/` package with `__init__.py` exposing `__version__` read from installed
  package metadata (`importlib.metadata`) — not a hand-maintained literal that drifts.
- `pyproject.toml`: `requires-python = ">=3.14"`, project metadata, and a console-script
  entry point `bessemer = "bessemer.cli:main"`. Build backend is whatever uv defaults to
  (hatchling); no reason to differ.
- `bessemer/cli.py` with `argparse`, exposing exactly two things at F1: `--version` and
  the `doctor` subcommand. `doctor` may be a stub that exits 0 printing nothing —
  issue 06 fills it in. No other subcommands are scaffolded; empty `status`/`gc`/`run`
  stubs would be lies about what exists.
- Zero runtime dependencies. `[dependency-groups]` may hold dev tooling (issue 02).
- `tests/` with `python -m unittest discover` working from the repo root, and one real
  test asserting `--version` reports the installed version.

**The suite must pass with no Docker daemon, no network, and outside any git
repository.** This is what gives CI a real gate even though the F1 tracer needs a live
Docker daemon. Write the constraint into `tests/README.md` or the top-level test module
docstring so it survives the next author.

Prove it by **blocking, not by environment** — stronger than stopping a daemon, since it
holds whatever state the machine is in, it is repeatable, and CI can enforce it. The guard
lives in `tests/guard.py`, armed from `tests/__init__.py` before any test module imports,
and has two halves:

- **Network: banned outright.** No test in this project, at any issue, should open a
  socket. Patch `socket` to raise.
- **Spawns: an allowlist, not a ban.** Permitted: `git`, and `sys.executable` or the
  installed console script (so tests can drive the CLI end to end). Everything else is
  denied by omission — including `docker`, which is the actual constraint. Inspect argv;
  do not replace `subprocess` wholesale.

An allowlist rather than a docker-shaped blocklist, for the same reason the wrapper in
issue 03 uses one: a blocklist loses to the next binary someone reaches for. When F3 needs
docker in tests it must widen this list explicitly — a reviewable act, not a quiet
loosening.

`GuardViolation` subclasses `BaseException` so an `except Exception:` in code under test
cannot swallow it.

The guard must be proven failing: show a deliberate `socket.create_connection` and a
deliberate denied spawn each erroring the suite, then remove them. Keep permanent
regression tests asserting the guard is armed, including one proving a broad `except`
cannot eat it.

**Canonical invocation is `uv run python -m unittest discover`.** The package is always
installed in the project environment, so `importlib.metadata` always resolves and the
version assertion is real on every run. `__version__` therefore gets **no fallback
sentinel**, and no test may `skipTest` when metadata is absent — a skipped test is not a
test, and a fallback branch serving an invocation we do not endorse is dead weight that
looks like rigor. Issue 02's `make check` uses this same invocation.

Two small behaviors, specified so they aren't invented twice: bare `bessemer` with no
subcommand prints usage and exits 2 (argparse `required=True`) — note that F5's picker
will deliberately claim this slot later, so a test asserting it should expect to change.
`--version` prints the bare version string with no program name, for scripting.

## Acceptance criteria

- [ ] `uvx --from . bessemer --version` prints the bare version and exits 0
- [ ] `uvx --from . bessemer doctor` exits 0 (stub); `bessemer --help` lists only
      `doctor`; bare `bessemer` prints usage and exits 2
- [ ] `uv run python -m unittest discover` passes from a clean checkout
- [ ] Network is blocked during the suite; spawns are allowlisted to `git` and the
      interpreter/console script, with everything else — docker included — denied
- [ ] Guard demonstrated failing on a deliberate `socket.create_connection` and a
      deliberate denied spawn, before those are removed
- [ ] Permanent tests assert the guard is armed and that a broad `except Exception:`
      cannot swallow a `GuardViolation`
- [ ] Permanent tests prove the allowlist **passes through**, not merely that it denies —
      a permitted program really spawns and its output is asserted. A guard tested only on
      denials goes green while denying everything, which is the state in which the suite
      has stopped testing anything at all
- [ ] Suite passes when run from a directory outside any git work tree
- [ ] No `skipTest` anywhere, and `__version__` has no fallback sentinel
- [ ] `pyproject.toml` declares no runtime dependencies; `uv.lock` is committed
