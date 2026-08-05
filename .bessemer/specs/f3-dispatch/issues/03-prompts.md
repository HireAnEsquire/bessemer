# 03 — prompts: override resolution and the stack-agnostic defaults

Status: Todo
Type: AFK
Blocked by: —

## What to build

`bessemer/prompts.py` — `load(name) -> str`: package default (via `importlib.resources`),
a same-named file under `.bessemer/prompts/` wins at read time (ADR 0001). Plus the three
default templates, rewritten stack-agnostic, and bessemer's own repo overrides.

**The templates this issue owns — all three, named:** `implement-prompt.md`,
`review-prompt.md`, `pr-prompt.md`. Oracle:
`git show e194121f75f4:.agentbox/<name>` in `/Users/sbowles/hae`. Section order per
template is an owned list (implement: TASK / ORIENTATION / COMMANDS / IMPLEMENT / VERIFY /
COMMIT / RULES; review: ROLE / REVIEW / FIX / VERDICT / RULES; pr: ROLE / STRUCTURE /
MANUAL TESTING ASSUMPTIONS).

## Defaults: hae excised, three deltas added (README decision 7)

Excise every hae-specific passage (implement's ORIENTATION/VERIFY, pr-prompt's MANUAL
TESTING URLs and `docker exec hae-api-1` — the pr-prompt delta is **excision only**, no
content additions). The parity story that makes excision safe: hae's overrides restore
today's text byte-for-byte at F7.

Content deltas, **each pinned as a test literal — they are controls**:

1. The denied-tool rule, in BOTH implement and review defaults (ADR 0001: a denied tool
   is a decision — stop and report, never reach the same effect another way).
2. The specs-dir read-only declaration in the implement default; the review default
   treats an agent-authored edit under the specs dir as a **review-stopping finding**
   (ADR 0002 consequence).
3. The verdict semantics sentence in the review default: approved only on a round with
   **no changes**; a round that committed fixes ends needs-work.
4. `<promise>COMPLETE</promise>` dropped — measured unconsumed at the pin (nothing
   outside the prompt file references it). One-line divergence note in the template or
   module docstring.

## Bessemer's own overrides

`.bessemer/prompts/implement-prompt.md` (and review, if its FIX verification needs it):
ORIENTATION for this repo and VERIFY = `make check` — discharging ADR 0002's third
consumer of "one definition of the checks". Overrides are whole-file (read-time wins),
so they are complete templates, not patches.

## Acceptance criteria

- [ ] `load()` returns the package default; drop an override file in a tmp adapter dir
      and the override wins; remove it and the default returns
- [ ] The four delta literals pinned by hand-written tests against the *default*
      templates (mutating a control sentence fails the named test — prove one)
- [ ] No hae-specific string survives in any default (`grep -i` for `django`, `yarn`,
      `hireanesquire`, `mlaglobal`, `manage.py`, `agentbox` over the defaults — empty)
- [ ] Section-order lists pinned per template
- [ ] Bessemer's own overrides exist and carry `make check`
- [ ] Report anything in the pinned templates that neither ported nor excised cleanly —
      a sentence that is half hae, half generic, is a finding, not a judgement call
- [ ] `make check` green
