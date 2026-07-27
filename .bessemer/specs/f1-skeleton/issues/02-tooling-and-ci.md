# 02 — Tooling: ruff, mypy strict, pre-commit, CI

Status: Todo
Type: AFK
Blocked by: 01

## What to build

The automated reviewers. This lands early, before the substantive modules, so that
everything after it is written already-conforming rather than retrofitted.

- **ruff** for lint and format, configured in `pyproject.toml`. One tool replacing
  black/isort/flake8.
- **mypy `--strict`** over `bessemer/` and `tests/`. The port source is already fully
  annotated with modern syntax (`int | None`, `list[X]`), so strict is realistic from day
  one and will not need loosening when F2 lands.
- **pre-commit** config running ruff (check + format) and mypy, plus standard
  whitespace/EOF hooks. **mypy must be configured `pass_filenames: false`** and run over
  the whole package: pre-commit passes only changed filenames, and mypy given a subset of
  a package produces incomplete results — it can report success on a broken tree.
- **`make check`** — the single entry point: `pre-commit run --all-files`, then
  `python -m unittest discover`. Four legible lines, no cleverness.
- **GitHub Actions CI** on push and PR: install uv, then run **exactly `make check`** —
  not a re-enumeration of the individual steps.

There is deliberately **one definition of "the checks", with three consumers**: the
developer's commit hook, CI, and — from F3 — the in-container agent, whose implement
prompt runs the same `make check`. Two lists of the same checks drift, and the failure
mode is worst for the agent: it runs its command, believes it is done, and discovers the
gap after the PR is open. The port source already works this way, running `pre-commit` as
the gate an agent must pass before claiming completion.

Tests stay out of the commit hook so commits stay fast; they are in `make check`, which
is what CI and the agent run.

All of this lives in `[dependency-groups]`, never in runtime dependencies. The
stdlib-first posture in ADR 0001 governs the *runtime* supply chain of a
credential-adjacent tool; dev tooling never reaches an adopter and never enters a
container, so it is judged only on whether it earns its keep.

CI deliberately does not run the F1 tracer — that needs a Docker daemon. CI's job is the
unit suite plus static checks, which is what catches a bad mechanical edit during F2's
337-test port.

## Acceptance criteria

- [ ] `make check` passes from a clean checkout and is the only command a contributor
      needs to know
- [ ] CI invokes `make check` verbatim — no separately listed steps that could drift
- [ ] `mypy --strict` passes with no ignores and no `# type: ignore` comments, and is
      configured `pass_filenames: false` so it always sees the whole package
- [ ] Deleting a type annotation somewhere in the package makes `make check` fail —
      proving mypy is actually running over everything, not a changed-file subset
- [ ] `pre-commit run --all-files` passes; the commit hook does not run the test suite
- [ ] Runtime dependencies remain empty; all tooling is in a dependency group
