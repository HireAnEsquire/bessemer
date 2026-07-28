# 07 — Bessemer's own adapter

Status: Todo
Type: AFK
Blocked by: 01, 04

## What to build

The `.bessemer/` adapter for this repository — so bessemer is its own first adopter, and
F3 has a real dispatch target on day one rather than waiting for F6's scaffolding.

- **`.bessemer/config.toml`** (committed): `source` (the port-source pin) and `base`.
  Issue 04's loader reads exactly `source`, `base`, and `specs_dir`; `specs_dir` defaults
  to `.bessemer/specs`, which is where this repo already keeps them, so setting it would
  be noise. Deliberately small — every key added here is a key F1's loader must actually
  read, and 04 pins that set with a literal, so a key you invent will not silently work.
- **`.bessemer/Dockerfile`**: trivial. Python 3.14 base, a non-root `agent` user created
  with a `AGENT_UID` build argument so the in-container user matches the host owner of the
  bind-mounted checkout, the agent CLI, and the single sudoers line granting root for
  exactly the setup hook. Nothing app-specific — bessemer's own test suite is stdlib
  unittest with no services.
- **`.bessemer/setup.sh`**: effectively a no-op. Idempotent, non-interactive, exits 0.
  Its header explains what the hook is for in a real adapter (starting services,
  installing dependencies into the checkout) and why bessemer's own needs none.
- **`.gitignore` entries** for the adapter's runtime state: `config.local.toml`, `.env`,
  `checkouts/`, `logs/`, `locks/`, `runs.jsonl`.

The `AGENT_UID` argument is not optional decoration. macOS Docker Desktop masks a UID
mismatch on a bind mount; Linux does not — so omitting it works on the author's machine
and breaks for the first Linux adopter, which is the worst possible time to discover it.

The specs directory (`.bessemer/specs/`) is **tracked**, diverging from the port source,
which gitignores its equivalent. Rationale and the two consequences are in this feature's
[README](../README.md).

Nothing in F1 reads the Dockerfile or the setup hook — F3 does. They land here so that F3
is a dispatch, not a dispatch plus an adapter.

## Acceptance criteria

- [ ] `.bessemer/config.toml` exists, parses, and **every key in it is one the issue 04
      loader actually reads**. Issue 04 owns the schema; this file conforms to it rather
      than proposing to it. A key here that the loader ignores is a claim that bessemer is
      configured by something it never looks at
- [ ] The Dockerfile builds, and the resulting image has an `agent` user whose UID matches
      the `AGENT_UID` build argument
- [ ] `setup.sh` is executable, exits 0, and is idempotent across repeated runs
- [ ] Runtime-state paths are gitignored; `.bessemer/specs/` and `config.toml` are not
- [ ] The issue 04 loader finds this adapter when called from the repo root and from a
      nested subdirectory — the walk-up, exercised against a real adapter rather than a
      fixture. `bessemer doctor` reporting it is issue 08's tracer, not yours: doctor's
      check list does not exist until issue 06
