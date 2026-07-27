# 03 — Subprocess wrapper: the single argv boundary

Status: Todo
Type: AFK
Blocked by: 01

## What to build

`bessemer/proc.py` — the one module in the package permitted to start a child process.
This exists to make a security invariant structural: **argv is always a list, never a
string, and never goes through a shell**, which eliminates the shell-interpolation and
quoting-hazard class that the port is being rewritten to escape.

- `run(argv, *, timeout, cwd=None, env=None) -> Result` — non-raising. `Result` carries
  `argv`, `returncode`, `stdout`, `stderr`, and an `ok` property. Non-raising is the
  default because doctor's probes are all "did this fail, and how"; an exception per probe
  turns a check list into control flow.
- `run_checked(...) -> Result` — raises on nonzero, with an exception carrying argv,
  returncode, and stderr. For call sites where failure must abort.
- **`timeout` is mandatory on every call** — a required keyword, not a default. A wedged
  Docker daemon hanging doctor forever is worse than doctor failing.
- **The exception must never carry the environment**, and its stderr is
  credential-bearing: `git` and `gh` failures routinely echo remote URLs that can embed
  tokens. That text must never reach a PR body, a notification, or the container log.
  Note this in the module docstring — later features are the ones that will be tempted.
- No `__bool__` on `Result`. `if result:` reads as "did I get a result" to every reader
  and would mean the opposite. Explicit `.ok` only.

`Result` is a plain record that always exists, deliberately distinct from the
value-or-reason type in issue 05. "A process ran and failed" and "a value could not be
determined" are different things, and conflating them turns error handling to mush.

### Enforcement test

A test walking the AST of every module under `bessemer/`:

- **Outside `bessemer/proc.py`**: no `subprocess` import, and no use of `os.system`,
  `os.popen`, `os.exec*`, `os.spawn*`, `os.posix_spawn`,
  `asyncio.create_subprocess_shell`, or `pty.spawn`.
- **Inside `bessemer/proc.py`**: an allowlist of exactly `subprocess.run` and
  `subprocess.Popen` (Popen reserved for F3's live log streaming). Everything else in the
  module is rejected — including `subprocess.getoutput`, `getstatusoutput`, `call`,
  `check_call`, and `check_output`, all of which either shell out or bypass the wrapper's
  timeout discipline.
- **Everywhere**: no call with a `shell=True` keyword.

Allowlist rather than blocklist inside the wrapper is deliberate: a blocklist loses to
the next function someone finds.

AST, not grep — grep is fooled by a docstring mentioning `shell=True` and misses
`sh = True; run(..., shell=sh)`. The `tests/` tree is out of scope for the AST check;
tests legitimately need `subprocess` to drive the CLI end to end, which issue 01's spawn
allowlist permits (interpreter and console script are allowed; docker is not).

## Acceptance criteria

- [ ] `run()` returns a `Result` with `.ok` and never raises on nonzero exit
- [ ] `run_checked()` raises with argv, returncode, and stderr in the message, and with
      no environment data
- [ ] Omitting `timeout` is a `TypeError` at every call site
- [ ] AST test passes and *fails* when a `shell=True` call, a stray `subprocess` import,
      or a `subprocess.getoutput` call is deliberately introduced — prove all three
- [ ] `Result` has no `__bool__`
