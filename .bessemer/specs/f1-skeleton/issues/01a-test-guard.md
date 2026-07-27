# 01a — Test guard: network ban and spawn allowlist

Status: Done
Type: AFK
Blocked by: 01

## Why this is its own issue

It was originally a section of issue 01 and was split out after three rounds of review
found successive holes in it — an `executable=` bypass, a platform-dependent
`posix_spawn` decision, and a network ban drawn at the wrong boundary. Each was real, and
none had anything to do with packaging. A security control that needs its own threat
enumeration needs its own acceptance criteria and its own review pass; carrying it inside
"package skeleton" meant it was reviewed as a detail of something small.

## What this guard is and is not

**It defends against accident and drift, not against a hostile test author.** A test that
deliberately reaches for a private module to escape the guard has defeated it, and that is
acceptable: the container boundary in ADR 0001 is what stands against intent. This one
stands against a future author who reaches for `docker` in a unit test because it was
convenient, and against the slow loosening that follows. Keeping that scope explicit is
what stops the enumeration below from looking like a sandbox it is not.

Consequently: **every claim the guard's own comments make must be true and falsifiable.**
A security note that is subtly wrong about *why* teaches the wrong rule to whoever widens
it later, and it is the one part no test covers. Where coverage has a known limit, name
the limit rather than writing a claim that reads as complete.

## What to build

The suite must pass with no Docker daemon, no network, and outside any git work tree —
proven by **blocking, not by environment**. Blocking holds whatever state the machine is
in, it is repeatable, and CI can enforce it, which "stop Docker Desktop first" never
could. The guard lives in `tests/guard.py`, armed from `tests/__init__.py` before any test
module imports.

`GuardViolation` subclasses `BaseException`, so an `except Exception:` in code under test
cannot swallow it.

### Network

No test in this project, at any issue, should reach the network. Draw the line at
*reaching*, not at *constructing*: creating a socket contacts nothing, and banning the
constructor takes `asyncio` down with it, since the Unix event loop builds its self-pipe
from `socket.socketpair()`. So `socket.socket` and `socketpair` stay open, and every route
by which a socket touches something else is denied — outbound connection, name resolution,
the module-level convenience helpers, **the connectionless sends (`sendto`, `sendmsg`),
which need no prior `connect` and work against a literal address with DNS already
banned**, and the listen side (`bind`, `listen`, `accept`). `send`/`sendall`/`recv` stay
open: the self-pipe uses them, and on a socket never allowed to connect they reach
nothing.

**The ban operates on the Python `socket.socket` class, and `_socket` is a known
exclusion.** The C base type is immutable — `cannot set 'connect' attribute of immutable
type '_socket.socket'` — so `socket.socket.__base__(...)` reaches the network by design
and cannot be patched. Say so where the enumeration is written. It falls on the acceptable
side of the scope above, but only if it is stated; left unstated, the list reads as
exhaustive and the next person widening it believes something false.

`ssl.SSLSocket` is covered, but incidentally: it overrides five denied methods and each
delegates to `super()`, hitting the patched one. Incidental coverage that nothing pins is
one refactor upstream from being a hole — pin it with a test.

Name resolution means **all** of it, `getnameinfo` included — reverse lookup is a C
builtin with no transitive backstop through the patched `getaddrinfo`, so it is a live
network reach that an enumeration reading as exhaustive would quietly leave open.

**The spawn half has a known limit too, and it must be named the same way `_socket` is.**
The wrappers hold the originals they replaced — `_GuardedPopen`'s base class, and the
captured callable inside each checked wrapper — so `subprocess.Popen.__base__` reaches an
unguarded spawn. That is on the acceptable side of "accident and drift, not a hostile
author", but `__base__` is ordinary public attribute access rather than a private module,
so the carve-out does not obviously cover it. State it beside the wrapper.

### Spawns

An allowlist, not a ban. Permitted programs: `git`, `sys.executable`, and the installed
console script. Everything else is denied by omission — including `docker`, the constraint
that actually matters. The constraint was never "no subprocesses".

An allowlist rather than a docker-shaped blocklist, for the same reason `bessemer/proc.py`
uses one in issue 03: a blocklist loses to the next binary someone reaches for. When F3
needs docker in tests it must widen this list explicitly — a reviewable act, not a quiet
loosening.

**Inspecting `argv[0]` is not sufficient.** Every path by which a caller can name a
different program must be inspected too, or the allowlist is decorative: `executable=`
overrides `argv[0]` (positionally as well as by keyword — `Popen` takes it third), and the
argv itself can arrive by keyword. Reading past the end of the positional args and
concluding `None` denies *permitted* programs with a message that reads as a guard bug;
issue 05 spawns git and will hit exactly that. A call shape the guard cannot read a
program out of must fail closed.

**Deny `os.exec*` and `os.spawn*` outright, both the `l` and `v` spellings** — nothing in
the suite has reason to replace the process image or to fork-and-exec around `subprocess`,
and splitting the family by calling convention rather than by policy states one rule two
ways. **`os.posix_spawn`/`posix_spawnp` are the sole exception and stay allowlisted**:
CPython's `subprocess` routes through them on its fast path when the program is an
absolute path and `posix_spawn_file_actions_addclosefrom_np` is available — true on
glibc ≥ 2.34, false on macOS. Denying them is green on a Mac and red on Linux CI, so the
reason belongs beside the allowlist entry, and the entry needs a **pass-through** test.
A denial-only test cannot tell an allowlisted `posix_spawn` from a denied one, which makes
the whole suite green under precisely the regression the comment exists to prevent.

### The exported entry-point set

The guard exports the set of spawn entry points it covers, and issue 03's AST test imports
it rather than restating it. Two things the shape must get right, both learned from a
version that got them wrong:

- **`(module, attr)` pairs, not dotted strings.** A static consumer resolving
  `"subprocess.run"` has only the bare name `run` to match on — which is the name issue
  03's own wrapper exports and every other module in `bessemer/` calls. The pair keeps the
  two apart. Alias tracking (`import subprocess as sp`) stays the static consumer's job;
  the export cannot help with that and should not pretend to.
- **Export the full declared set; filter only at arm time.** `install()` skipping names
  absent on this interpreter is right — you cannot patch what does not exist. Exporting
  the filtered set is not: a static ban whose contents vary by host is the drift the
  export exists to prevent.

Prose about the relationship should say the two **cover the same paths**, not that they
forbid the same things — the guard permits `git`, issue 03 permits nothing outside
`bessemer/proc.py`.

### No suppressions yet

Issue 02 installs ruff and mypy. Until then, **write no `# type: ignore` and no `# noqa`**
— they are unverifiable, they age badly (one planted in the first attempt was already an
`unused-ignore` error under `--warn-unused-ignores`), and they hand issue 02's implementer
a suppression to inherit under a spec that forbids adding them. If the code cannot be
written without one, that is a finding to report, not a comment to plant.

## Acceptance criteria

- [ ] Guard demonstrated failing on a deliberate `socket.create_connection`, a deliberate
      connectionless `sendto`, and a deliberate denied spawn, before those are removed.
      **Report the output** — the demonstration is deleted by design, so the human pastes
      it into the commit body; without it the next reviewer must reconstruct it
- [ ] Permanent tests assert the guard is armed and that a broad `except Exception:`
      cannot swallow a `GuardViolation`
- [ ] **A pass-through test for every allowlisted entry** — both every permitted program
      and every wrapped entry point, `posix_spawn`/`posix_spawnp` included. A permitted
      call really runs and its output is asserted. A guard tested only on denials goes
      green while denying everything, which is the state in which the suite has stopped
      testing anything at all
- [ ] Moving any allowlisted entry point into the denied set makes a test fail. Prove it
      for `posix_spawn` specifically: it is the one whose failure mode is invisible on
      macOS and appears only as red Linux CI
- [ ] **Every branch of every check is mutation-proven**: remove the branch, and a named
      test must fail. This applies to the whole guard, not to any one criterion. A test
      that passes because a *different* branch caught the case is not coverage — it is a
      test that will go on passing after the branch it names is deleted. Report which test
      failed for each branch, so the pairing is checkable rather than asserted
- [ ] `executable=` is allowlist-tested wherever it can be passed — positionally, by
      keyword, and on `asyncio.create_subprocess_exec` — and argv passed by keyword is
      found rather than read as absent
- [ ] A UDP `sendto` to a literal address is refused, as are `bind`/`listen`/`accept`,
      while `socketpair` plus `send`/`recv` still work — the case `asyncio` depends on
- [ ] `ssl.SSLSocket`'s coverage is pinned by a test rather than left incidental
- [ ] The exported set is `(module, attr)` pairs, unfiltered by platform, and every name
      in it is demonstrably wrapped or refused once the guard is armed on a host that has
      it
- [ ] **The network enumeration gets the same treatment as the spawn one** — exported, and
      every name in it asserted to be installed as a refusal. Without it, dropping any
      single network name leaves the suite green: some are masked by a neighbouring patch
      catching the same test for a different reason, and the DNS helpers have no backstop
      at all, so removing one silently reopens name resolution
- [ ] Every comment stating a reason is accurate, and the `_socket` exclusion is named
      where the network enumeration is written
- [ ] No `# type: ignore` and no `# noqa` anywhere in the diff
- [ ] Canonical suite green, and green again from a directory outside any git work tree —
      the second run against a tree verified identical to the first, not merely assembled
      to be
