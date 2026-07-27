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
repository.** Everything that touches those is mocked. This is what gives CI a real gate
even though the F1 tracer needs a live Docker daemon. Write this constraint into
`tests/README.md` or the top-level test module docstring so it survives the next author.

## Acceptance criteria

- [ ] `uvx --from . bessemer --version` prints the version and exits 0
- [ ] `uvx --from . bessemer doctor` exits 0 (stub) and `bessemer --help` lists only
      `doctor`
- [ ] `python -m unittest discover` passes from a clean checkout
- [ ] Suite passes with the Docker daemon stopped, network off, and run from a directory
      outside any git work tree
- [ ] `pyproject.toml` declares no runtime dependencies
