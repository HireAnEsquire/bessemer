# 06 — container: the privilege surface, unmixed

Status: Done
Type: AFK
Blocked by: 01

## What to build

`bessemer/container.py` (ADR 0003): pure argv builders + start / `run_setup_hook` /
remove. Oracle region: run.sh:1242–1282. The argv builders are tier-1 pure functions
returning `list[str]`; execution goes through the proc seam.

## The argv builders own these literals

- **Caps**: `--cap-drop ALL` always, unconditional. Cap-adds = exactly the committed
  `container_cap_add` list, plus — **only when `container_volumes` is non-empty** — the
  caps core's own chown needs (README decision 5.3's coupling rule; upstream's chown
  consumed CHOWN/DAC_OVERRIDE/FOWNER from its six). Pinned both ways by hand-written
  literals: volumes present → those caps in argv; absent → absent.
- **Limits**: `--pids-limit` / `--memory` from config (issue 01's keys).
- **Mount table**, pinned as a literal: checkout rw at `/workspace`;
  `.bessemer/setup.sh` ro at `/bessemer/setup.sh`; spec ro at `/spec.md`; each
  `container_volumes` entry. Absences asserted alongside: no `-p`/`--publish` ever, and
  never a mount of the whole `.bessemer/` directory (pin comment :1216 — it holds
  `.env`).
- **Env boundary** (ADR 0001 verbatim, README decision 5.4): `--env-file` for the
  committed `.bessemer/container.env` when present (bulk from committed = reviewable);
  gitignored `.bessemer/.env` contributes **only** `container_env_keys` + the built-in
  credential names (`CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY` — an owned literal),
  each as explicit `-e KEY=value`. A key present in `.env` but not forwarded → one
  operator-facing warning naming the key, never the value — and the warning string never
  reaches the container log, PR body, or notification (route through issue 02's policy).
- **Setup hook invocation**: exactly `sudo /usr/bin/bash /bessemer/setup.sh` — the
  sudoers-verbatim string, pinned. Before it, the chown exec
  (`docker exec -u root <cid> chown agent <mountpoints>`) for each volume mountpoint —
  orchestrator privilege, not agent sudo (pin :1269–1273). A volume path under
  `/workspace` gets its directory pre-created in the checkout (generalizing pin :1212).
  Nonzero hook exit aborts the dispatch and surfaces the log (ADR 0001 contract);
  the abort message names the failing hook, and a 127 from a missing binary names the
  image-contract clause.

## The image contract, and bessemer's own image

Owned list (README decision 7.4): `bash` at `/usr/bin/bash`; coreutils `timeout`;
`git`; the `claude` CLI on PATH; a non-UID-0 agent user; plus `sudo` + the one sudoers
line iff the setup hook needs root. This issue verifies **bessemer's own adapter image**
(`.bessemer/Dockerfile`, built with a real `AGENT_UID`) against every entry — measured
on the built image, F1-07's "a Dockerfile that has never been built is not done". Two
small edits ride along: bessemer's committed `config.toml` gains `image =
"bessemer-agent"`, and the Dockerfile comment "Where F3 mounts the checkout is not
decided yet" is updated — it is decided now: `/workspace`.

## Acceptance criteria

- [ ] Cap literals pinned both ways (volumes present/absent); mutate the builder to leak
      one extra cap and the named test fails (prove, restore)
- [ ] Mount-table literal test, including both absence assertions
- [ ] Env boundary: fixture `.env` with a declared key, an undeclared key, and a
      credential — argv carries exactly declared + credential as `-e`; warning names the
      undeclared key only; no value appears in any recorded stream
- [ ] Sudoers invocation string pinned; an extra flag in the built argv fails the test
- [ ] Image contract verified on the built image, each entry a run command with output
      shown (`command -v timeout`, `git --version`, `claude --version`, `id -u agent`,
      sudoers content)
- [ ] `make check` green (image verification is a build-and-inspect step in this issue's
      report, not part of the unit suite — the suite needs no docker)
- [ ] **Malformed `container_cap_add` fails before any argv is built** (added from issue
      01's review, 2026-08-05): issue 01 validates volume entries at load so a defect is
      doctor-visible rather than mid-dispatch, and the same reason does not stop at
      volumes. The case that motivates it: `container_cap_add = "SETUID"` — a bare string
      — iterates as characters, and a builder that maps entries to `--cap-add` flags would
      emit `--cap-add S --cap-add E …`. Wherever the shape check lands (issue 01's loader
      pattern or this module's builder boundary), a non-list or non-string-entry value is
      a refusal with the key named, and a test proves the bare-string case never reaches
      an argv.
