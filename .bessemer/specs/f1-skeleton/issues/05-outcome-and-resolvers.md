# 05 — Outcome type and resolvers: base auto-detect, root agreement

Status: Todo
Type: AFK
Blocked by: 03, 04

## What to build

The values config deliberately refuses to compute, plus the shared type they come back in.

### `bessemer/outcome.py`

A tagged union — two frozen dataclasses, `Resolved(value)` and `Unresolved(reason, hint)`
— consumed with `match`. Roughly thirty lines, no dependencies.

Deliberately not a `Result`/`Maybe` library: the shape appears at a handful of sites, and
railway-oriented `bind`/`map` idioms read as foreign in a codebase whose founding premise
is a script the team can read and fix. Python 3.14's pattern matching plus mypy narrowing
already does this natively. Revisit only if resolvers pass roughly ten.

`hint` carries the fix, not just the diagnosis — it is what doctor prints after the
failure text and what makes a check line actionable.

### `bessemer/resolve.py`

- `resolve_base(cfg)` — the base branch, from config if set, else auto-detected from
  `origin/HEAD`. Unresolved reasons that must be distinguishable: not a git work tree, no
  `origin` remote, `origin/HEAD` unset (the common one — hint is
  `git remote set-head origin --auto`).
- `resolve_root_agreement(cfg)` — cross-checks the discovered config root against
  `git rev-parse --show-toplevel`. **Both directions are errors**: `.bessemer/` found
  *above* the git root means the walk escaped the repository entirely (a stray
  `~/.bessemer` from an `init` run in a home directory); found *below* means someone ran
  `init` in a subdirectory. Both hard-fail, with both paths in the message.

**These resolvers are shared predicates, not doctor-only checks.** Doctor renders the
outcome; dispatch hard-errors on the identical call. That is what stops the two from
drifting apart — the same discipline the port source applies with its
`have_claude_credential` and `image_staleness` helpers.

Root agreement is load-bearing beyond hygiene: the security invariant "the host pushes
from the main repository" only names something unambiguous once config root and git root
are known to be the same directory. Dispatch is also the caller that already pays for git,
so the check is free exactly where it matters most — it is the one caller holding
credentials and a push path.

A useful side effect: submodules trip this automatically, since
`git rev-parse --show-toplevel` inside a submodule returns the submodule's root, so
dispatching submodule code against a superproject adapter fails instead of silently using
the wrong image.

## Acceptance criteria

- [ ] `Resolved`/`Unresolved` narrow correctly under `mypy --strict` in a `match` block
- [ ] `resolve_base` returns distinct reasons for: not a work tree, no origin remote,
      `origin/HEAD` unset — each with an actionable hint
- [ ] Config-supplied base short-circuits auto-detection without invoking git
- [ ] `resolve_root_agreement` fails for `.bessemer/` above the git root, fails for below
      it, and passes when they match — all three tested with real temporary repos
- [ ] Every git invocation goes through `bessemer.proc` with an explicit timeout
- [ ] Tests spawn real `git` (permitted by issue 01's allowlist) against local temporary
      repositories with no remotes — nothing reaches the network, which stays banned
- [ ] No resolver raises on any tested failure path
