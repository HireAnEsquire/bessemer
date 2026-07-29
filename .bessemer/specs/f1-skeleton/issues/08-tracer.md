# 08 — Tracer: `uvx --from . bessemer doctor` green

Status: Done — except CI, which cannot be evidenced until the work is pushed
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

### Where these are performed, which is not on the developer's machine

Every one of these four is a change to somebody's state, and three of them are changes to
state that is not yours to change. **All four are staged in a throwaway clone under a
scratchpad directory, never in this checkout, never in `$HOME`.** Specifically:

- The second `.bessemer/` goes *above a clone inside a temporary directory*. Not `~/.bessemer`
  — writing an adapter into a developer's home directory is exactly the stray-adapter accident
  this check exists to catch, and creating one to test the check is committing the fault to
  observe it.
- `origin/HEAD` is unset in a clone. Deleting `refs/remotes/origin/HEAD` here would leave the
  developer's own repository in the broken state afterwards.
- **`base = "main"` in this repo's own `.bessemer/config.toml` short-circuits `resolve_base`
  before git is ever consulted**, so the `origin/HEAD` path is unreachable against this
  adapter no matter what the refs say. The clone's committed config must be edited, or the key
  overridden, for that failure path to be reached at all — and a run that "verified" it
  without doing so verified the short-circuit. Say which you did.
- **Do not stop the Docker daemon yourself.** It is the human's machine and the human's
  running containers, and quitting an application on someone's desktop is not a step an agent
  takes on its own. Report what you need and stop; the human runs that one and pastes the
  output back. This has been marked unverified in three consecutive sessions precisely because
  a stub is easy and the real thing is not the agent's to do.

### The tracer command is itself the thing that lies

`uvx --from .` keys its cache on **package name and version**, not on file contents. The
version sat at `0.1.0` across all seven preceding issues, so the command this issue names as
its own tracer served issue 01's stub wheel — printing nothing, exiting 0 — while `uv run` was
green. Stale in both directions: with the version fixed, a marker *removed* from the source
still printed. Run every tracer invocation with `--refresh`, and treat a silent exit 0 as the
symptom rather than as a pass. Adopters are immune; `git+…@<sha>` is a fresh key per commit.

Named here because an agent that ran the command once, got exit 0, and ticked the first
criterion would have "verified" the stub — the same shape as the `base` short-circuit above,
in the one command this issue exists to run.

### `uvx --from .`, not `uv run`

The tracer's whole subject is the distribution path. `uv run` uses the working tree and would
pass while the packaging is broken — which is the failure class this issue predicts. Every
tracer invocation is `uvx --from . bessemer doctor`, and the report should show the command
that produced each output.

Then update `README.md`: F1 is complete, and `uvx --from . bessemer doctor` is a thing a
reader can now actually run.

## Acceptance criteria

- [ ] `uvx --from . bessemer doctor` exits 0 with every check `ok` or `WARN` on a healthy
      machine
- [ ] uv fetches a 3.14 interpreter when the host default is older, without the user
      installing anything beyond uv. **This one cannot be executed by uninstalling the host's
      python**, and pinning an *older* interpreter does not test it either — that only proves
      `requires-python` rejecting, which is the opposite direction. The construction that
      answers it is `--python-preference only-managed` with `UV_PYTHON_INSTALL_DIR` pointed
      into the scratchpad, which forces a real download without touching the host. Note also
      that `uv python list` mixes downloadable builds with system interpreters already on
      disk; filter for `<download available>` or you will read Homebrew's python as uv's
      offering — measured, twice, by two different readers
- [ ] **Doctor states the uv floor.** ADR 0001 is amended: uv >= 0.9.0 is an adoption
      constraint, because uv 0.8.x can download no stable 3.14 and `requires-python` cannot
      enforce a property of the installer. Issue 06 decided against a uv version floor on
      reasoning this tracer measured false; the check belongs in the `uv` line, with the
      version it found and the floor it wants.

      **WARN, not FAIL, and the message is conditional.** The first version of this criterion
      said "FAIL rather than WARN — nothing works below it", and that is false: below the
      floor bessemer installs fine on any host that already has a 3.14, which is the host it
      was written on. Worse, the FAIL refutes itself — `requires-python` is `>=3.14`, so if
      `bessemer doctor` is running at all then a satisfying interpreter was found and the
      install already succeeded. A line reading "bessemer cannot be installed by this one" is
      disproved by the fact that it printed. The true claim names its condition: *this uv
      cannot download a stable 3.14, so bessemer will not install on a machine that does not
      already have one.* That is a warning about the next machine, or this one after its
      system python moves — real, worth saying, and not a local failure. Say it in the
      message and in the constant's docstring, which carried the same falsehood.

      This also settles the conflict with the criterion above it: a healthy machine exits 0,
      and a below-floor uv does not make a working machine unhealthy
- [ ] All four failure paths above produce actionable output and no traceback
- [ ] `python -m unittest discover` still passes with the Docker daemon stopped — the
      human-run step, since the daemon is not the agent's to stop
- [ ] **Nothing outside a scratchpad directory was modified to produce any of this.** This
      checkout's refs, its `.bessemer/`, and `$HOME` are all as they were; show `git status`
      and a `$HOME` check rather than asserting it
- [ ] CI green
- [ ] README updated to reflect F1 landing
