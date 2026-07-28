# 05 — Resolvers: base auto-detect, root agreement

Status: Done
Type: AFK
Blocked by: 03, 04, 04a

## What to build

The values config deliberately refuses to compute. They come back in the `Resolved` /
`Unresolved` union from `bessemer/outcome.py`, which is **issue 04a's deliverable, not
yours** — use it as it stands and do not extend it.

### `bessemer/resolve.py`

- `resolve_base(cfg)` — the base branch, from config if set, else auto-detected from
  `origin/HEAD`. Unresolved reasons that must be distinguishable: not a git work tree, no
  `origin` remote, `origin/HEAD` unset (the common one — hint is
  `git remote set-head origin --auto`).
- `resolve_root_agreement(cfg, *, start=None)` — cross-checks the discovered config root against
  `git rev-parse --show-toplevel`. **Both directions are errors**: `.bessemer/` found
  *above* the git root means the walk escaped the repository entirely (a stray
  `~/.bessemer` from an `init` run in a home directory); found *below* means someone ran
  `init` in a subdirectory. Both hard-fail, with both paths in the message.

  Both resolvers take `start`, defaulting to `Path.cwd()`, mirroring `config.load`. The
  earlier signature took `cfg` alone and could not detect the "above" direction at all — with
  git asked from `cfg.root`, a stray `~/.bessemer` makes git answer about `~` or refuse
  entirely, so the case this issue calls the primary one was unreachable. `Config` does not
  record the directory discovery started from, and the disagreement is only visible from
  there.

- **Two paths name the same directory by identity, not by spelling.** `git rev-parse
  --show-toplevel` returns the on-disk spelling; a `Path` returns whatever its caller wrote.
  `Path.resolve()` follows symlinks but does **not** canonicalise case, so on a
  case-insensitive filesystem — macOS's default, and Windows — `…/repo` and `…/Repo` are the
  same directory and compare unequal under `==`, under `is_relative_to`, and under every other
  string relation. Root agreement then reports two roots in different trees and tells the user
  to `cd` to a directory they are already standing in. Compare by `os.path.samestat` /
  `Path.samefile`, and derive ancestry the same way rather than from string prefixes. Note the
  cost so it is chosen rather than discovered: identity comparison stats, and a stat can raise
  `OSError`, which this issue's resolvers may not let escape. (Added after issue 05's review
  measured it. `Path.cwd()` *does* canonicalise, so the default path was immune and the
  spelling only diverges when a caller passes an explicit `start` — which doctor and dispatch
  both may.)

- **The environment can defeat both resolvers, and `resolve_root_agreement` is a security
  predicate.** git's children inherit the ambient environment (ADR 0002, host-side), so an
  exported `GIT_DIR`, `GIT_WORK_TREE` or `GIT_COMMON_DIR` makes every `rev-parse` answer
  about a *different repository than the one on disk*. Root agreement is what makes "the host
  pushes from the main repository" name something unambiguous; a check that a developer's
  shell variable silently redirects is not that check. Decide what these resolvers pass as
  `env` and say why. Measured: with `GIT_DIR` exported, 15 of 45 tests in an otherwise
  correct `tests/test_resolve.py` fail — the same shape as issue 03's stdin defect, where the
  suite went red on correct code because of the ambient environment, green in CI, and red
  only under a human's own shell.

- **`bessemer.proc.run` raises in exactly two cases, and both are yours to absorb.** A
  program that could not be executed at all raises `OSError`; one killed for exceeding
  `timeout` raises `subprocess.TimeoutExpired`. That is deliberate in issue 03 — neither ran
  to completion, so there is no returncode to report — and it means **git not installed is an
  exception, not a `Result`**. That case is not exotic: it is close to the most important
  thing doctor ever has to say, and a resolver that lets it escape turns a check line into a
  traceback in the one situation doctor exists for. Both get their own reason and their own
  hint. Issue 04 learned this the expensive way, three rounds running, against a library
  whose failure modes were *not* documented; here they are documented and there are two, so
  enumerate them — but pin the enumeration against `proc`, not against your fixtures.

- **git's `stderr` is credential-bearing and your reasons are printed.** ADR 0002 and issue
  03 state this about the *exception*; this issue is where it first matters for ordinary
  output, because doctor renders a `reason` to a terminal and F3 renders one into a pull
  request body. `git` echoes remote URLs on failure, and a remote URL can carry a token
  (`https://x-access-token:ghp_…@github.com/…`). Decide per reason whether git's own text
  belongs in it at all, say what you decided, and test a reason built from a failure whose
  stderr contains a URL with embedded credentials — asserting the credential is absent from
  what a user would see. Do not solve this by dropping stderr everywhere without saying so:
  a reason with nothing of git's in it is often useless.

- **`cfg.get("base")` is typed `object | None`, not `str | None`.** Issue 04 does no
  coercion, deliberately — nothing of bessemer's runs inside its parse boundary — so a
  `config.toml` containing `base = 5` or `base = ["main"]` reaches you as an `int` or a
  `list`, and `base = ""` reaches you as a set-but-empty string. This issue is the first
  caller and therefore owns the answer. Whatever you choose, an unusable configured value
  must be a distinguishable reason rather than a crash or a silent fall-through to
  auto-detection: "your `base` is not a branch name" and "you have no `base`" are different
  things to be told.

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

- [ ] `resolve_base` returns distinct reasons for: not a work tree, no origin remote,
      `origin/HEAD` unset — each with an actionable hint
- [ ] Config-supplied base short-circuits auto-detection without invoking git
- [ ] `resolve_root_agreement` fails for `.bessemer/` above the git root, fails for below
      it, and passes when they match — all three tested with real temporary repos
- [ ] **The match case holds when the two roots reach the same directory by different
      spellings** — a test that walks up from a differently-cased path on a case-insensitive
      filesystem and gets agreement, not "different trees". Measured on macOS: `==` and
      `is_relative_to` both say no while `samefile` says yes. Such a test can only fail on a
      case-insensitive host, so say so where it is written: CI is case-sensitive and will run
      it as a tautology
- [ ] Every git invocation goes through `bessemer.proc` with an explicit timeout
- [ ] Tests spawn real `git` (permitted by issue 01's allowlist) against local temporary
      repositories with no remotes — nothing reaches the network, which stays banned
- [ ] **The reason set is pinned by a hand-written literal, anchored to the module.** This
      issue owns both resolvers' failure vocabularies, so a test must restate every case by
      name and assert no two share a `reason` or a `hint` — a deleted branch whose case is
      absorbed by a neighbour leaves every per-case assertion green. Closed against *growth*
      too, and against the module rather than the fixtures: a literal compared only to a
      hand-written fixture list beside it restates the fixtures. `tests/test_config.py` walks
      `bessemer/config.py` with `ast` for exactly this and is the pattern to follow. This is
      F1's most-shipped defect by a wide margin — nine times, in nine modules, by implementers
      who were each being careful
- [ ] No resolver raises **on any path, tested or not** — including `OSError` from a missing
      `git` and `TimeoutExpired`, neither of which is a `Result`. "Tested failure path" was
      the earlier wording and it makes the untested path invisible by construction, which is
      the only kind that ships
- [ ] A reason built from a git failure whose `stderr` contains a remote URL with an embedded
      token does not carry that token into the text a user sees. **This one test is built from
      a synthetic `Result`, not a real repository** — git echoes a remote URL only while
      contacting a remote, and the suite bans the network, so "real repos" and "a
      credential-bearing stderr" cannot both hold. Everything else in this issue uses real git
- [ ] **The suite passes with `GIT_DIR`, `GIT_WORK_TREE` and `GIT_COMMON_DIR` exported**, and
      a test asserts the resolvers answer about the repository on disk rather than the one an
      ambient variable names. Root agreement is a security predicate; an environment variable
      that redirects it silently is a bypass, not a preference
