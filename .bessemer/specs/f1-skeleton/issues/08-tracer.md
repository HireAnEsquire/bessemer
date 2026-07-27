# 08 — Tracer: `uvx --from . bessemer doctor` green

Status: Todo
Type: AFK
Blocked by: 06, 07

## What to build

Little new code — this issue exists so that "does the whole thing actually work" is
somebody's job. A tracer bullet that is nobody's issue quietly becomes nobody's problem.

Run the real command through the real distribution path and fix whatever the previous
seven issues left mismatched. Expect packaging problems rather than logic problems: an
entry point that resolves under `pip install -e` but not under `uvx --from .`, package
data missing from the wheel, `requires-python` not actually causing uv to fetch 3.14 on a
host whose default python is older.

Also verify the failure paths are as useful as the success path, since a green line on a
healthy machine proves very little:

- Docker daemon stopped → docker check FAILs with a start-it hint, other checks still run
- Run from `/tmp` → config not-found FAILs cleanly, no traceback
- `origin/HEAD` unset → base check FAILs with the `git remote set-head` hint
- A second `.bessemer/` placed above the repo → root agreement FAILs naming both paths

Then update `README.md`: F1 is complete, and `uvx --from . bessemer doctor` is a thing a
reader can now actually run.

## Acceptance criteria

- [ ] `uvx --from . bessemer doctor` exits 0 with every check `ok` or `WARN` on a healthy
      machine
- [ ] uv fetches a 3.14 interpreter when the host default is older, without the user
      installing anything beyond uv
- [ ] All four failure paths above produce actionable output and no traceback
- [ ] `python -m unittest discover` still passes with the Docker daemon stopped
- [ ] CI green
- [ ] README updated to reflect F1 landing
