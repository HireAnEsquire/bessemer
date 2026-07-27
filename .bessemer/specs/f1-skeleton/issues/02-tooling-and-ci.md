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
  whitespace/EOF hooks.
- **GitHub Actions CI** on push and PR: install via uv, run `ruff check`, `ruff format
  --check`, `mypy`, and `python -m unittest discover`.

All of this lives in `[dependency-groups]`, never in runtime dependencies. The
stdlib-first posture in ADR 0001 governs the *runtime* supply chain of a
credential-adjacent tool; dev tooling never reaches an adopter and never enters a
container, so it is judged only on whether it earns its keep.

CI deliberately does not run the F1 tracer — that needs a Docker daemon. CI's job is the
unit suite plus static checks, which is what catches a bad mechanical edit during F2's
337-test port.

## Acceptance criteria

- [ ] `ruff check` and `ruff format --check` pass on the repo
- [ ] `mypy --strict` passes with no ignores and no `# type: ignore` comments
- [ ] `pre-commit run --all-files` passes
- [ ] CI workflow runs on push and PR and fails the build on any of the four checks
- [ ] Runtime dependencies remain empty; all tooling is in a dependency group
