# 08 — landing: push, draft PR, the body

Status: Done
Type: AFK
Blocked by: 02

## What to build

`bessemer/landing.py` (ADR 0003): `land(...) -> Landing`. Oracle region:
run.sh:1529–1592.

**The PR-description text arrives as a value.** The description-generation claude pass is
dispatch's, run via issue 07 — landing composes and lands, it does not run passes. That
is why 07 is not on this issue's blocker list, and the seam F4 reuses.

- **Push**: only when commits past the merge-base boundary > 0.
  `git push --quiet -u origin refs/heads/<b>:refs/heads/<b>` — explicit refspec, plain.
  **No force-push code exists in this module at F3** — `--force-with-lease` arrives with
  F4's `--hard-reset`. Absence assertion: no `--force*` in any recorded push argv, ever.
- **PR probe/update/create**, ported exactly:
  `gh pr view <branch> --json url,state --jq 'select(.state == "OPEN") | .url'`; OPEN →
  `gh pr edit <branch> --body-file -`; else
  `gh pr create --draft --base <base-sans-origin/> --head <branch> --title "[bessemer] <branch>" --body-file -`.
  Body always on stdin. Draft always — nothing here can ever emit a non-draft create or
  a merge; assert the absences (`--draft` present; no `pr merge` anywhere).
- **Body composition** (owned literals): description + `\n\n---\n` + verdict footer +
  attribution. Pinned sentences, product token renamed per README decision 8.1:
  - needs-work footer: `⚠️ Review: needs-work after N round(s) — read the task log
    before reviewing.`
  - approved footer: `Review: approved (round N/M).`
  - attribution: `AI-authored via bessemer (spec: \`<basename>\`). Draft until the
    dispatching dev reviews it.`
  - description-failure fallback: `_(description generation failed — see the task log)_`
- **stderr**: gh/git stderr goes to the host log only (pin :1581/:1585), through issue
  02's policy. Nothing from any `Result` reaches the body.

## Acceptance criteria

- [ ] Zero commits past boundary → no push argv, no gh argv, outcome says nothing landed
      (the ledger's unconditional append is issue 10's, not here)
- [ ] Probe scripted OPEN → edit path; scripted no-PR → create path with `--draft`,
      `--base` stripped of `origin/`; body byte-identical on stdin in both
- [ ] All four sentence literals pinned by hand; reword the needs-work footer and the
      named test fails (prove, restore)
- [ ] Absence assertions: no `--force*`, no `pr merge`, `--draft` on every create
- [ ] A `Result` with credential-bearing stderr in the composition path: body contains
      no fragment of it — asserted on the composed string
- [ ] `make check` green
