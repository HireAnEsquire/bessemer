# 07 — Bessemer's own adapter

Status: Done
Type: AFK
Blocked by: 01, 04

## What to build

The `.bessemer/` adapter for this repository — so bessemer is its own first adopter, and
F3 has a real dispatch target on day one rather than waiting for F6's scaffolding.

- **`.bessemer/config.toml`** (committed): `source` and `base`. `source` is the pinned ref
  `uvx --from` resolves *bessemer's own core* from (ADR 0001, distribution) — not the port
  source, which an earlier draft of this line wrongly implied. A commit SHA in this repo:
  there are no tags yet, and `@main` would make the pin move under the team without anyone
  editing a file, which is the skew the pin exists to prevent.
  Issue 04's loader reads exactly `source`, `base`, and `specs_dir`; `specs_dir` defaults
  to `.bessemer/specs`, which is where this repo already keeps them, so setting it would
  be noise. Deliberately small — every key added here is a key F1's loader must actually
  read, and 04 pins that set with a literal, so a key you invent will not silently work.
- **`.bessemer/Dockerfile`**: trivial. Python 3.14 base, a non-root `agent` user created
  with a `AGENT_UID` build argument so the in-container user matches the host owner of the
  bind-mounted checkout, the agent CLI, and the single sudoers line granting root for
  exactly the setup hook. Nothing app-specific — bessemer's own test suite is stdlib
  unittest with no services.
  Three sub-decisions, settled here so they are not re-derived: **only `AGENT_UID` is
  parameterised, not the GID** — write access to the bind mount is decided by the owner bits, so
  a GID knob buys nothing until something in a checkout is group-writable but not
  owner-writable. **The agent CLI comes from Anthropic's native installer at `stable`**, not
  `npm install -g`, which would add Node and ~150 MB to a Python image to deliver one binary
  that no longer needs it; unpinned because the image is rebuilt rather than upgraded, so a
  version pin would be one yank away from an unbuildable image and buy no reproducibility
  between builds. **`uv` is deliberately absent** — bessemer's checks run through `uv run`, so
  F3's first dispatch against this repo needs it, and ADR 0001 puts dependency installation in
  the setup hook rather than the image. That is F3's problem to solve in the hook, named here so
  it is a known gap rather than a surprise.

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
- [ ] **`AGENT_UID=0` fails the build.** `useradd -o` accepts a duplicate UID, and 0 is a
      duplicate: the build succeeds and produces an image whose "non-root agent" is root, with
      no error anywhere. Measured, not hypothesised — `docker build --build-arg AGENT_UID=0`
      yields `uid=0(root)`. Dispatch passes the host dispatcher's UID, and dispatching from a
      root shell is ordinary in CI, so this is the container boundary of ADR 0001 dissolving on
      a machine nobody is watching. Fail in the Dockerfile; a comment saying "don't do this" is
      not a check
- [ ] **`USER agent` and the sudoers grant are pinned by tests.** They are the two lines in
      this file the container boundary actually consists of, and both are currently invisible
      to the suite: deleting `USER agent` leaves every test green while the container runs
      everything as root. Pin the directive, and pin the grant's exact text — the path outside
      the checkout is a contract F3 must match verbatim (ADR 0001, setup hook), so a reworded
      grant is a broken dispatch, not a style change.

      **Assert the property, not a mount path.** Where F3 mounts the checkout is undecided, and
      this issue must not decide it in a Dockerfile comment: what is known is that the grant
      names a path *outside* the checkout wherever it lands, and that is what the assertion has
      to say. An assertion against a specific invented path is decorative the moment F3 chooses
      a different one, and it looks like a settled contract to everyone who reads it in between.
      `workspace` in particular is banned vocabulary for the checkout — see `CONTEXT.md`.

      **Pin the whole instruction, not the presence of the line inside it.** A grant is widened
      by *addition*, not by rewording, and `assertIn(GRANT, body)` is blind to that by
      construction: appending `&& echo "agent ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers.d/agent`
      to the same `RUN` leaves the required text present, the instruction count at one, and the
      suite green — measured, 209 tests, exit 0, with the agent holding unrestricted root. The
      assertion has to be equality against a hand-written copy of the entire instruction. For
      the same reason, an assertion whose subject is the test file's own `GRANT` constant tests
      nothing about the image; the subject must be the text read out of the Dockerfile
- [ ] `setup.sh` is executable, exits 0, and is idempotent across repeated runs
- [ ] Runtime-state paths are gitignored; `.bessemer/specs/` and `config.toml` are not
- [ ] The issue 04 loader finds this adapter when called from the repo root and from a
      nested subdirectory — the walk-up, exercised against a real adapter rather than a
      fixture. `bessemer doctor` reporting it is issue 08's tracer, not yours: doctor's
      check list does not exist until issue 06
