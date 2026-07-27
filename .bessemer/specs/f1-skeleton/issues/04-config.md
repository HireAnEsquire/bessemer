# 04 — Config: discovery, two-layer load, precedence

Status: Todo
Type: AFK
Blocked by: 01

## What to build

`bessemer/config.py` — finding the adapter directory and reading its two TOML layers.

**This module runs no subprocesses.** Config load is pure: filesystem and environment
only. Anything requiring `git` or `docker` is a resolver (issue 05), deliberately
separated so that `bessemer doctor` still works when those are broken — which is doctor's
entire reason to exist.

- **Discovery: walk up from cwd** looking for a `.bessemer/` directory, stopping at the
  filesystem root. No git involved. This keeps load pure, degrades independently of git,
  and matches what users already expect from `.git`, `node_modules`, and `.venv`.
- **Two layers**: committed `.bessemer/config.toml` and gitignored
  `.bessemer/config.local.toml`, parsed with `tomllib`. Any key is valid at either layer;
  local wins.
- **Precedence**: CLI flags > `BESSEMER_*` env vars > local > committed > defaults.
  Auto-detected values (issue 05) sit below defaults and are not this module's business.
- **Not-found is a structured reason, never an exception and never a traceback.** Doctor
  renders it as a check line and keeps going; dispatch hard-errors on the same value.
- **No override flag.** No `--repo`, no `BESSEMER_ROOT`. Discovery is not a config value,
  and an escape hatch nobody has needed yet is how discovery accidentally becomes
  configuration. A real case (CI running from outside the repo, say) can argue for itself
  later.

`container_env_keys` is deliberately **not** implemented here. F1's loader parses TOML
generically; that key's committed-layer restriction and its doctor FAIL land in F3
alongside the container boundary they exist to enforce. Validating a key nothing reads
would be premature.

## Acceptance criteria

- [ ] Discovery finds `.bessemer/` from the repo root and from any nested subdirectory
- [ ] Discovery from outside any adapter returns a structured not-found reason; no
      exception, no traceback
- [ ] Local layer overrides committed for the same key; either layer alone works; neither
      present is not an error
- [ ] Full precedence chain proven by test: a flag beats an env var beats local beats
      committed beats the default
- [ ] A test asserts this module imports no subprocess machinery and calls nothing from
      `bessemer.proc`
- [ ] Malformed TOML produces a structured reason naming the file, not a `tomllib`
      traceback
