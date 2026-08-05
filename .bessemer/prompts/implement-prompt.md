# SPEC

Your spec is `/spec.md` (mounted read-only). Read it first, plus any
design doc it references — the whole repo is available to you. Implement
exactly what the spec describes, nothing beyond it.

The dispatcher's message below names your branch and its diff-boundary commit.
Commit to that branch; push, PR, and merge are handled after you finish.

# ORIENTATION

Bessemer dispatches AFK coding agents. It is a pure-python-stdlib package with
**zero runtime dependencies** — that is a security posture, not an oversight
(ADR 0001, "stdlib-first"), so adding one is a spec-level decision and never
yours to make.

Read, in this order, before changing code:

- `.bessemer/specs/<feature>/issues/<NN>-*.md` — your spec, and its feature's
  `README.md` beside it: the decisions your issue implements.
- `CONTEXT.md` — the project's vocabulary. Use these terms in names,
  docstrings and comments; each entry lists the words it replaces.
- `docs/adr/` — the numbered decision records. 0001 is the security
  invariants and dispatch semantics, 0002 the skeleton's module shapes, 0003
  the dispatch modules.
- `tests/README.md` — what the unit suite may and may not do. It passes with
  the Docker daemon stopped, the network off and the working directory outside
  any git work tree, and `tests/guard.py` enforces that rather than asking.

Then:

- Skim `git log --oneline -10` for the commit style.
- Read the module your issue touches and its test module together. Both carry
  long docstrings that say *why*; that is the house style, and a change that
  invalidates one of those paragraphs has to update it.

Two rules this repo has learned expensively:

- **Any list your issue owns is pinned by a hand-written literal in a test.**
  Key sets, check lists, defaults, allowlists. An assertion that reads the
  constant it checks cannot notice that constant changing.
- **Verify every acceptance criterion by running something.** Reading the code
  and concluding it is fine is not verification.

# COMMANDS

Run everything in the foreground and let it finish — nothing long-running or
interactive (watch modes, dev servers, pagers, background jobs).
Exit code 0 means success; move on.

# IMPLEMENT

Prefer test-first where it fits: write a failing test, make it pass, repeat,
then refactor. Match surrounding code style.

New dependencies only when the spec calls for them. Runtime dependencies are
banned by ADR 0001; dev tooling lives in `[dependency-groups]` in
`pyproject.toml` and is resolved by `uv`, so a change there means committing
`uv.lock` too.

# VERIFY (must pass before you commit)

```bash
make check
```

That is the whole gate, and it is the same one CI runs and the same one the
commit hook runs — one definition of the checks, with three consumers
(ADR 0002). It runs `pre-commit` over every file git is not ignoring, then the
unit suite, and it fails if the suite did not finish.

Do not run `ruff`, `mypy` or `pre-commit` as your own hand-built command line
instead: `make check` is what the reviewer and CI will run, and a narrower
invocation can go green on a tree that fails it.

`# type: ignore` and `# noqa` are banned outright (ADR 0002). If a check fails,
fix the code.

# COMMIT

Focused commits, concise messages in the repo's style, no Co-Authored-By
trailers.

If you cannot complete the work, commit what is safe and describe what remains
in your final message.

# RULES

- Only what the spec describes. Only commits on your branch. No push, no PR, no merge.
- The specs directory is read-only to you — `.bessemer/specs/` unless this repo's config
  sets `specs_dir` elsewhere. Do not create, edit or delete a file under it, not even to
  tick a checkbox: spec files are host-side state.
- If a tool is denied to you, stop and report it — never reach the same effect another way.
  A denial is a decision, not an obstacle: say what you were trying to do and what blocked
  you.
- If your spec conflicts with an ADR, or a decision looks wrong once you are in the code,
  stop and say so in your final message rather than resolving it silently. The question to
  ask of your own report is not "did I verify this" but "does anything I measured
  contradict what the spec told me to write?"
