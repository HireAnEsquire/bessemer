# 09 — doctor: the checks F3 has earned

Status: Todo
Type: AFK
Blocked by: 01, 03

## What to build

Doctor grows exactly the checks F3 builds — ADR 0002: a check that can only fail teaches
nothing, so nothing lands for unbuilt subsystems. The checks, named (no count in prose —
this list is the authority, and the doctor list-order test restates it as the literal):

- **credential** — presence only, value never printed. Built as a shared resolver
  (ADR 0002 discipline) that dispatch's preflight (issue 10) calls identically — the
  pin's own `have_claude_credential` rule: "not a second copy of the logic" (run.sh:346).
  Checks `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY` — the same built-in-names
  literal issue 06 owns; import it, don't restate it.
- **gh** — CLI present and authenticated (`gh auth status`), FAIL with install/login
  hints (pin :381–387)
- **image** — the configured `image` resolves and exists locally
  (`docker image inspect`); missing config key or missing image both FAIL with a build
  hint. Staleness is F5's — no staleness line at F3
- **container_env_keys in local layer** — FAIL (ADR 0001 names this check)
- **container_cap_add in local layer** — FAIL (README decision 5.2)
- **container_volumes in local layer** — FAIL (README decision 5.3)
- **prompt overrides** — reports the override count (ADR 0001: drift stays visible).
  Informational `ok` line, never WARN/FAIL — an override is a feature, not a defect

The three layer checks render the violation fact issue 01 exposes — doctor renders,
dispatch hard-errors, neither reimplements (ADR 0002).

## Acceptance criteria

- [ ] Each check exercised both ways with real fixtures (credential set/unset via env;
      committed-only key planted in a local-layer fixture; override file present/absent)
- [ ] **The committed-only check reports the env channel too** (added from issue 01's
      review, 2026-08-05): a committed-only key arriving as an env var
      (`BESSEMER_CONTAINER_ENV_KEYS=…`) is dropped by construction in the loader — the env
      layer is built from `KNOWN_KEYS` minus `COMMITTED_ONLY_KEYS` — and issue 01
      deliberately gives the loader no violation fact for it. So the one user who tries it
      gets silence unless doctor looks at the environment directly. Doctor's check reads
      `os.environ` for the three committed-only names and reports a set one as a FAIL line
      naming the key and the committed-layer rule, never the value. Local-layer violations
      arrive as loader facts; env-layer ones are doctor's own to notice — say that split
      in the check's docstring.
- [ ] The list-order test (F1's "every queried name is emitted earlier") extended;
      dependency skips keep hand-written messages
- [ ] Credential resolver: one definition — an AST or import test asserts doctor and the
      (future) dispatch preflight symbol are the same callable; at minimum, no second
      env read of the credential names outside it
- [ ] Doctor still completes with everything broken (no docker, no config, no git) —
      the F1 through-line, re-proven with the new checks in the list
- [ ] No value of any credential appears in any output — asserted on rendered lines
- [ ] `make check` green
