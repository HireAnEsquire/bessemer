# Reviewing F1 issues

**If you are an agent reviewing an issue: this file is your instructions.**

One fresh review session per issue, after the implementer reports done and before the human
commits. You have no memory of the implementation — that is the point. Do not ask the implementer
what it meant; if intent isn't legible from the diff and the spec, that is itself a finding.

This also rehearses F3's reviewer pass, so a weakness in these instructions is worth reporting.

## Read first

1. `.bessemer/specs/f1-skeleton/issues/<NN>-*.md` — the issue, especially its acceptance criteria
2. `git diff` (and `git status` for new files) — the whole change
3. `docs/adr/0002-skeleton-structure.md` and `CONTEXT.md` — the decisions and vocabulary the
   change must conform to
4. `docs/adr/0001-founding-decisions.md` — if the issue touches a security invariant

## What to check, in priority order

1. **Re-run the acceptance criteria yourself.** Do not trust a transcript. Run the commands, and
   for the awkward criteria run the awkward version — actually stop the Docker daemon, actually
   run from outside a git work tree. A criterion that was asserted rather than executed is a
   finding even if the code is correct.
2. **Gaps.** Every acceptance criterion, satisfied or not. Say which, with evidence.
3. **Unasked-for work.** Stubs, extra subcommands, extra config keys, modules belonging to a
   later issue. Scope creep here is not harmless: this project's premise is a tool that reports
   only what it can vouch for, and a stub is a claim that something exists.
4. **Weakened checks.** Added `# type: ignore`, loosened lint or mypy configuration, skipped or
   deleted tests, assertions softened to make something pass. Compare against the config as it
   was before the diff.
5. **Decision conformance.** Does the change contradict ADR 0001 or 0002? Does it use the
   vocabulary in `CONTEXT.md` — `checkout` not `clone`/`worktree`, `run` not `task`, `spec` not
   `task file`? Vocabulary drift in names and docstrings is worth reporting; it is cheap now and
   expensive after seven more issues copy it.
6. **Security invariants**, where touched — argv-only subprocess, no environment in exception
   context, no credential-bearing text heading anywhere agent-visible.

## What NOT to do

- **Do not fix anything.** Report only. A reviewer that edits leaves its own work unreviewed.
  (F3's in-container reviewer *does* fix inline, because a human gates the PR afterwards. Here
  the human is the immediate next step, so findings are more useful than patches.)
- **Do not restyle**, do not propose refactors, do not suggest architecture changes. The codebase
  is eight issues old; there is nothing to refactor yet.
- **Do not resolve a conflict with an ADR by preferring your own judgement.** The ADR wins. If
  the ADR itself looks wrong, say so as a finding and leave it.

## Output

A short list of findings, each with file:line, what is wrong, and what would make it right.
Order by severity. Then one line:

- `<verdict>approved</verdict>` — every acceptance criterion verified by you, no findings above
  nit level
- `<verdict>needs-work</verdict>` — anything else, with the blocking findings named

The verdict token is deliberately the same one F3's review loop will parse. Using it now means
the format is exercised before anything depends on it.

Finally: **anything the spec should have said but didn't.** The implementer is asked the same
question; you see different gaps than it does.
