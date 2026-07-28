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
- **The keys F1 reads are `source`, `base`, and `specs_dir`.** This issue owns that
  schema; issue 07's `config.toml` conforms to it. Only `specs_dir` has a default
  (`.bessemer/specs`). `source` has no defensible one, and `base` must have **none** —
  defaults sit above issue 05's `origin/HEAD` auto-detect in the precedence chain, so
  defaulting `base` would make that auto-detect dead code before it is written. A key with
  a default is also what makes the precedence criterion below provable at all.
- **Two layers**: committed `.bessemer/config.toml` and gitignored
  `.bessemer/config.local.toml`, parsed with `tomllib`. Any key is valid at either layer;
  local wins.
- **Precedence**: CLI flags > `BESSEMER_*` env vars > local > committed > defaults.
  Auto-detected values (issue 05) sit below defaults and are not this module's business.
- **A file `tomllib` cannot read is a structured reason — for any reason, not only a syntax
  error.** TOML mandates UTF-8, so a file saved in another encoding is malformed TOML, but
  `tomllib.load` reports it as `UnicodeDecodeError` rather than `TOMLDecodeError`. "Malformed
  TOML" is too narrow a phrase to scope an `except` clause by; the promise is that nothing
  in this module raises on a user's mistake, and that is what has to hold.
- **An unrecognised key is reported, never rejected.** An older pinned core reading a newer
  config file is routine — `container_env_keys` arrives in F3 and must not hard-fail a
  loader that predates it. Issue 07 depends on this answer, so it is stated here rather
  than left derivable.
- **Not-found is a structured reason, never an exception and never a traceback.** Doctor
  renders it as a check line and keeps going; dispatch hard-errors on the same value.

  Define the `(reason, hint)` type locally. ADR 0002 assigns this role to `Unresolved` in
  `bessemer/outcome.py`, but that module is **issue 04a's** deliverable and 04a is ordered
  after this issue — the ADR's type does not exist yet, and building it here would be
  implementing someone else's issue. 04a deletes the local type and rewrites these tests
  against the shared one. That ordering was a mistake in the issue graph, recorded here
  rather than quietly fixed, because it is the second time F1 has shipped an issue that
  needed something ordered after it.
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
- [ ] A file `tomllib` cannot read produces a structured reason naming the file, not a
      traceback — proven for a syntax error **and** for a file that is not UTF-8, which is
      the case that reaches `load` as a different exception type
- [ ] **The key set and its defaults are pinned by hand-written literals.** This issue owns
      the schema, so a test must restate `{source, base, specs_dir}` and the literal
      `.bessemer/specs` rather than assert against the module's own constants. An
      assertion that reads `KNOWN_KEYS` cannot notice `KNOWN_KEYS` growing, and issue 07's
      criterion rests on it not growing silently. Every key needs coverage in
      `tests/test_config.py` itself, not only wherever it happens to appear
