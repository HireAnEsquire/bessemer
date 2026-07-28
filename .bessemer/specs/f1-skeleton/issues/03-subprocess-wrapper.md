# 03 — Subprocess wrapper: the single argv boundary

Status: Todo
Type: AFK
Blocked by: 01, 01a

## What to build

`bessemer/proc.py` — the one module in the package permitted to start a child process.
This exists to make a security invariant structural: **argv is always a list, never a
string, and never goes through a shell**, which eliminates the shell-interpolation and
quoting-hazard class that the port is being rewritten to escape.

- `run(argv, *, timeout, cwd=None, env=None) -> Result` — non-raising. `Result` carries
  `argv`, `returncode`, `stdout`, `stderr`, and an `ok` property. Non-raising is the
  default because doctor's probes are all "did this fail, and how"; an exception per probe
  turns a check list into control flow.

  **"Non-raising" means "a nonzero exit is data", not "nothing ever propagates".** The two
  cases where no process completed — a program that could not be executed (`OSError`) and
  one killed for exceeding `timeout` (`subprocess.TimeoutExpired`) — propagate, because
  there is no returncode to report and inventing one would make "docker is not installed"
  indistinguishable from "docker exited 127". Doctor tolerates both by contract: a
  crashing check renders FAIL and the report still completes (ADR 0002).

  **Re-export `TimeoutExpired` from this module.** A caller told to absorb it cannot name it:
  the argv-boundary test forbids every module but this one from importing `subprocess` at
  all, so the two rules together say "handle this exception, but you may not spell it". The
  alias is inert — an exception class, already on the wrapper's allowlist for that reason —
  and lets a caller write `except proc.TimeoutExpired`. `OSError` is a builtin and needs
  nothing. (Added during issue 05, which hit the contradiction; recorded here because
  `proc.py` is this issue's module.)
- **Child output is decoded with `errors="replace"`.** `git` output is not guaranteed to
  be UTF-8 — a branch name or an author line can carry anything — and a
  `UnicodeDecodeError` out of a function documented as non-raising is exactly the
  surprise this module exists to remove.
- **`argv` must be rejected at runtime when it is a string.** `str` is a `Sequence[str]`,
  so `run("git status", timeout=5)` type-checks clean and hands a shell-shaped string to a
  child — the module's one invariant, waved through by the checker that was supposed to
  hold it. A `TypeError` is the only thing that actually fires.
- **`stdin` is `subprocess.DEVNULL`, always.** Left inherited, a child gets bessemer's own
  TTY and can block on a credential prompt for the whole timeout, surfacing as
  `TimeoutExpired` — an auth failure misreported as a hung process. Measured, not
  assumed: with a pty on stdin, a child reading a line blocks the full timeout; with
  `DEVNULL` it returns in milliseconds. This matters most on the F3 push path, which is
  exactly where a prompt is plausible and where nobody is watching.
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

- **Outside `bessemer/proc.py`**: no `subprocess` import, and no use of any spawn entry
  point. **Import that set from the test guard** (issue 01a exports it as `(module, attr)`
  pairs) rather than restating it here or in the test — a hand-copied list is how
  `os.forkpty`, `os.posix_spawnp` and `asyncio.create_subprocess_exec` end up banned at
  runtime and permitted statically. One set, two consumers; adding a name to the guard
  makes this test start catching it, with no second edit to remember.

  Resolving import bindings is **this test's** job, not the export's. Match on the module
  the name is bound to, so `import subprocess as sp; sp.run(...)` is caught and
  `bessemer.proc.run` — the wrapper's own export, which every other module calls — is not.
  A matcher keying on the bare attribute would ban the very function this issue exists to
  provide.
- **Inside `bessemer/proc.py`**: an allowlist of `subprocess.run` and `subprocess.Popen`
  (Popen reserved for F3's live log streaming), plus the names that **cannot start a
  process**: `PIPE`, `STDOUT`, `DEVNULL`, `TimeoutExpired`. Everything else is rejected —
  including `subprocess.getoutput`, `getstatusoutput`, `call`, `check_call`, and
  `check_output`, all of which either shell out or bypass the wrapper's timeout
  discipline.

  The line is *what can spawn*, not *what is spelled `subprocess.`*. `PIPE` and `DEVNULL`
  are integers and `TimeoutExpired` is an exception class; none can execute anything, and
  banning them would forbid the `stdin=DEVNULL` this issue requires and the `stdout=PIPE`
  that F3's streaming needs — leaving the first person to write either with a choice
  between widening a security allowlist under deadline and working around it.
- **Everywhere**: no call passing a `shell=` keyword **at any value**. Not "no
  `shell=True`": `sh = True; run(..., shell=sh)` is not statically evaluable, so the
  literal rule is undecidable while the keyword rule is exact. `shell=False` is the
  default anyway, so nothing legitimate is lost.

Allowlist rather than blocklist inside the wrapper is deliberate: a blocklist loses to
the next function someone finds.

AST, not grep — grep is fooled by a docstring mentioning `shell=True` and misses
`sh = True; run(..., shell=sh)`. The `tests/` tree is out of scope for the AST check;
tests legitimately need `subprocess` to drive the CLI end to end, which issue 01's spawn
allowlist permits (interpreter and console script are allowed; docker is not).

**The guard masks the "program not installed" test, and `tests/README.md` must say so.**
Spawning a deliberately-absent binary raises `GuardViolation`, not `FileNotFoundError`,
unless its basename is on the guard's `ALLOWED_PROGRAMS` — so a test meaning to prove
`OSError` propagates passes on the guard instead, and goes on passing after the behaviour
it names is removed. Name a permitted program at a path that does not exist. This is the
recurring shape of every real defect F1 has found: a test that passes for a reason other
than the one in its name.

## Acceptance criteria

- [ ] `run()` returns a `Result` with `.ok` and never raises on nonzero exit; `OSError` and
      `TimeoutExpired` propagate, each pinned by a test
- [ ] `run("git status", timeout=...)` raises `TypeError` rather than spawning
- [ ] **A child cannot read bessemer's stdin.** Prove it against a real pty, not against
      the ambient stdin of a test runner: with a terminal on the parent's stdin, a child
      that reads a line must return immediately rather than consume the timeout.
      `tests/README.md` should say why the pty is necessary — a test that skips it passes
      for the wrong reason on every host that runs it.

      **The suite must pass whether or not the runner's own stdin is a terminal.** It is
      not: `make check` redirects only stderr, so under an interactive shell fd 0 is the
      developer's tty, while under CI it is not. Any assertion about the *ambient* stdin
      is an assertion about the host, and it makes a correct implementation go red in the
      one place a human is watching and stay green in the one place they are not
- [ ] `run_checked()` raises with argv, returncode, and stderr in the message, and with
      no environment data
- [ ] Omitting `timeout` is a `TypeError` at every call site
- [ ] AST test passes and *fails* when a `shell=True` call, a stray `subprocess` import,
      or a `subprocess.getoutput` call is deliberately introduced — prove all three
- [ ] The banned-name list is imported from the test guard, not restated; prove it by
      showing the AST test rejects an `os.forkpty` and an `asyncio.create_subprocess_exec`
      call outside `bessemer/proc.py` without either name appearing in this test's source
- [ ] `Result` has no `__bool__`
