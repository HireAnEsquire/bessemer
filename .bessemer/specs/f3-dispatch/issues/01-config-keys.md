# 01 — config keys: the eight F3 keys and their layer rules

Status: Done
Type: AFK
Blocked by: —

## What to build

`bessemer/config.py` grows F3's keys. This issue owns the complete key list — restated
here as the literal, per F1's rule, so it is never derived from prose elsewhere:

| Key | Type | Layer rule | Default |
|---|---|---|---|
| `image` | str | any layer | none — absent is a dispatch hard error and a doctor FAIL (issue 09) |
| `container_env_keys` | list[str] | **committed only** | `[]` (built-in credential names are separate, issue 06) |
| `container_cap_add` | list[str] | **committed only** | `[]` |
| `container_volumes` | list[str] | **committed only** | `[]` |
| `max_review_rounds` | int | any layer | 3 |
| `pass_timeout` | int | any layer | 900 |
| `pids_limit` | int | any layer | 2048 |
| `memory` | str | any layer | `8g` |

`KNOWN_KEYS` grows from `{"source", "base", "specs_dir"}` to include all eight — and the
hand-written literal test grows with it, the deliberate two-file edit (README decision
8.6). The three committed-only keys form a second owned literal (the set doctor and
dispatch check the local layer against).

## Layer enforcement is a shared fact, rendered twice

Per ADR 0002's resolver discipline: config exposes the violation as data (a committed-only
key found in `config.local.toml`), **doctor renders it as FAIL** (issue 09), **dispatch
hard-errors on it** (issue 10). Neither reimplements the check. The reason is ADR 0001's
`container_env_keys` paragraph — widening a container boundary must be a reviewable diff —
and it applies verbatim to `container_cap_add` (README decision 5.2) and
`container_volumes` (5.3, ruled with the issue breakdown).

## Volume-format validation lands here, not in container.py

Each `container_volumes` entry is either `name:/path` (named volume) or `/path`
(anonymous). A source beginning with `/` or `.` in the `name:` position is refused at
parse — no host binds through config (README decision 5.3). Failing at load makes the
defect doctor-visible instead of surfacing mid-dispatch.

## Acceptance criteria

- [ ] `KNOWN_KEYS` literal test updated by hand; deleting any of the eight new entries
      from `config.py` fails the test (prove one deletion, restore it)
- [ ] Committed-only set pinned by its own hand-written literal
- [ ] A committed-only key in a local-layer fixture produces the violation fact; the same
      key in the committed layer does not
- [ ] Volume-format fixtures: `cache:/yarn-cache` accepted, `/workspace/node_modules`
      accepted, `../x:/y` refused, `/host:/y` refused — each with a message naming the
      rule
- [ ] Config load stays pure — no subprocess, proven by the existing F1 test still
      passing with the new keys
- [ ] Precedence chain unchanged for the any-layer keys (flags > env > local > committed >
      defaults); one test per new key exercises at least two layers
- [ ] `make check` green
