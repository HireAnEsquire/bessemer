# SPEC

Your spec is `/spec.md` (mounted read-only). Read it first, plus any
design doc it references — the whole repo is available to you. Implement
exactly what the spec describes, nothing beyond it.

The dispatcher's message below names your branch and its diff-boundary commit.
Commit to that branch; push, PR, and merge are handled after you finish.

# ORIENTATION

- Read the repo's own instructions for agents before changing code — whatever
  it keeps at the top level or under a docs directory, and any architecture
  note the spec points at.
- Skim `git log --oneline -10` for the repo's commit style.
- Explore the areas the spec touches — especially their existing tests.
- The environment is ready: this repo's setup hook has already run, so its
  services, dependencies and environment variables are in place. If something
  fails on the environment rather than on your change, report it; don't patch
  around it.

# COMMANDS

Run everything in the foreground and let it finish — nothing long-running or
interactive (watch modes, dev servers, pagers, background jobs).
Exit code 0 means success; move on.

# IMPLEMENT

Prefer test-first where it fits: write a failing test, make it pass, repeat,
then refactor. Match surrounding code style.

New dependencies only when the spec calls for them, and installed the way this
repo already installs them — through its own manifest and lockfile, both
committed.

# VERIFY (must pass before you commit)

Run this repo's own checks over what you changed, and fix what they report:
the tests covering the code you touched, plus whatever lint, format and type
gate the repo runs before a commit.

Find the command the repo already uses — do not invent one, and do not install
a tool that is not there. If the gate is missing or broken, say so rather than
working around it.

If your change requires a generated artifact this repo keeps in version
control — a migration, a lockfile, a schema snapshot — generate it with the
repo's own command and commit it.

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
