# 04 — checkout: the never-git-inside-it discipline

Status: Todo
Type: AFK
Blocked by: —

## What to build

`bessemer/checkout.py` (ADR 0003): create / `read_branch` / `salvage` / remove. Oracle
regions at the pin: run.sh:1203–1213 (clone + identity), :505–513 and :1169–1190
(salvage + HEAD read), :1161–1162 (stale remove).

- **create**: `git clone --quiet --no-hardlinks --branch <b> <repo_root> <wt>`; then
  `remote set-url origin <remote_url>`, `user.name`/`user.email` with the pin's
  fallbacks (`id -un`; `bessemer@users.noreply.github.com` — product token renamed).
  The config writes happen **before the agent ever touches the checkout** — they are the
  one legal moment; say so in the docstring.
- **read_branch**: textual read of `<wt>/.git/HEAD` — `ref: refs/heads/` prefix
  stripped; detached HEAD (raw SHA) returns None. Never `git -C <wt> symbolic-ref`; the
  checkout's `.git/config` is agent-controlled (pin comment :499–504).
- **salvage**: `git fetch --quiet <repo_root>/<wt> refs/heads/<b>:refs/heads/<b>`, run
  from the main repository. **This consolidates a refspec the pin spells three times —
  run.sh:508, :1177, :1538** (README decision 3); record that in the module docstring.
  FF-only: the refspec carries no `+`, pinned by a hand-written literal. Non-FF returns
  a distinguishable outcome — the caller keeps the checkout and says so loudly; this
  module does not print.
- **remove**: `rm -rf` equivalent, only ever called by an owner that has salvaged or
  decided not to (dispatch's finally, reclaim).

## Test posture

Real temporary git repositories (F1 issue 05 precedent) — the failure modes are git's:
diverged branch (salvage refuses), rebased inside the checkout (refuses), detached HEAD
(read_branch None), clean FF (branch advances in the main repo). No mocks for git
behavior. The cwd of every git argv is asserted **outside** the checkout — tier 2's
"no git inside the checkout" check starts here.

## Acceptance criteria

- [ ] FF salvage advances the branch in a real main repo; non-FF (amended commit inside
      the checkout) refuses and leaves both repos untouched — asserted on refs
- [ ] `read_branch` on detached HEAD returns None; on a normal checkout returns the
      branch; never spawns git — proven by the recorded proc stream being empty for it
- [ ] The no-`+` refspec literal test; mutate the refspec to `+refs/...` and the named
      test fails (prove, restore)
- [ ] Every recorded git argv's cwd is the main repo, never under the checkout path
- [ ] Clone argv pinned: `--no-hardlinks` present, `--branch <b>` present
- [ ] `make check` green
