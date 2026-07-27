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

Prove it by **blocking, not by environment**: patch the spawn and network paths
(`socket`, `subprocess`, `os.system`, `os.popen`, `os.exec*`, `os.spawn*`,
`os.posix_spawn`) to raise, so a test that reaches for either fails loudly. Stronger than
stopping a daemon — it holds whatever state the machine is in, it is repeatable, and CI
can enforce it. The guard must be proven in both directions: show a deliberate
`socket.create_connection` and a deliberate `subprocess.run` each failing the suite before
removing them.

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
- [ ] Spawn and network paths are blocked during the suite, and the guard is demonstrated
      failing on a deliberate `socket.create_connection` and a deliberate
      `subprocess.run` before those are removed
- [ ] Suite passes when run from a directory outside any git work tree
- [ ] No `skipTest` anywhere, and `__version__` has no fallback sentinel
- [ ] `pyproject.toml` declares no runtime dependencies; `uv.lock` is committed
