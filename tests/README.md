# The unit suite

```
uv run python -m unittest discover
```

That is the canonical invocation, and the one issue 02's `make check` uses. It installs
bessemer into the project environment, so `importlib.metadata` always resolves and the
version test asserts against a real distribution rather than skipping. There is no
supported way to run the suite against an uninstalled source tree — `bessemer/__init__.py`
has no fallback version, so it raises `PackageNotFoundError` on import instead of
inventing a number.

## The constraint: no daemon, no network, no git work tree

**Every test in this suite must pass with the Docker daemon stopped, the network off, and
the working directory outside any git work tree.**

This is not tidiness. Bessemer's tracer — `bessemer doctor` running green end to end —
needs a live Docker daemon, which makes it a dev-machine gate and not a CI one. The unit
suite is what gives CI a real gate anyway. A single test that quietly reaches for the
daemon, the network, or the ambient repository turns CI green-when-lucky, and the moment
that matters most is F2's mechanical port of 337 tests, where CI is the only thing
watching.

## The constraint is enforced, not requested

`tests/guard.py` arms itself from `tests/__init__.py`, before any test module is imported.
Blocking beats arranging: it holds whatever state the machine is in — your Docker daemon
can be running, as it usually is — it is repeatable, and CI can enforce it, which "stop
Docker Desktop first" never could.

The guard has two halves, deliberately asymmetric.

**Network: reaching out is banned; holding a socket is not.** The line is drawn at
*reaching*, not at *constructing* — creating a socket contacts nothing.

Denied at module level: `create_connection`, `create_server`, `getaddrinfo`,
`getnameinfo`, `gethostbyname`, `gethostbyname_ex`, `gethostbyaddr`. Name resolution means
all of it, `getnameinfo` included — it is the reverse lookup, it is a C builtin in its own
right, and banning `getaddrinfo` does nothing to it. Denied as methods on the Python
`socket.socket` class, which covers it and its Python subclasses:

| Denied method | Why it is its own route out |
|---|---|
| `connect`, `connect_ex` | The obvious one |
| `sendto`, `sendmsg` | Need no prior `connect` at all. A UDP socket sends to a literal address, and a literal address needs no DNS — so banning resolution and connection alone still leaves this open |
| `bind`, `listen`, `accept` | The listen side of the same enumeration. `create_server` is denied, so leaving its constituent parts open would ban the helper rather than the act |

**Left working on purpose:** the `socket.socket` constructor, `socket.socketpair`, and
`send`/`sendall`/`recv`. `socketpair` returns an unnamed local pair with no address and no
route off the machine, and asyncio's event loop builds its self-pipe out of one and talks
to it with `send`/`recv`. Banning any of these bans asyncio outright, while this guard
*allowlists* `asyncio.create_subprocess_exec` — a ban that contradicts the allowlist reads
as a bug and gets deleted by whoever hits it. On a socket that was never allowed to
connect, `send` and `recv` reach nothing. Denying `sendto` costs asyncio nothing because
asyncio never calls it: `BaseSelectorEventLoop._write_to_self` writes to the self-pipe with
`csock.send(b'\0')`. Not because `sendto` fails on a socketpair — measured with the guard
disarmed, `sock.sendto(b'x', b'')` on an AF_UNIX stream pair returns 1 and the peer
receives it. Only the address-bearing form errors, and on argument type (`TypeError: a
bytes-like object is required, not 'tuple'`), not because of the pair.

**`_socket` is a known exclusion, and the list is not exhaustive without saying so.**
`socket.socket.__base__` is the C type `_socket.socket`, which is immutable — patching it
raises `TypeError: cannot set 'connect' attribute of immutable type '_socket.socket'` — so
`socket.socket.__base__(...)` reaches the network by design and cannot be stopped here.
That is acceptable, because **this guard defends against accident and drift, not against a
hostile test author**: ADR 0001's container boundary is what stands against intent. It is
acceptable only because it is stated. Left unstated, the enumeration above reads as
complete and the next person widening it believes something false. `KnownExclusionTest`
pins the immutability so the claim stays checkable.

`ssl.SSLSocket` is covered, but *incidentally* — it subclasses `socket.socket`, and four
of the five methods it overrides (`connect`, `connect_ex`, `accept`, `sendto`) delegate
through `super()` to the patched ones. Incidental coverage that nothing pins is one
upstream refactor away from being a hole, so `SSLSocketTest` pins all four.

The fifth, `sendmsg`, never reaches the guard — `ssl` refuses it first with
`NotImplementedError`. That is pinned to what actually stops it rather than to a
`GuardViolation` it never raises. And `sendto` delegates only on a socket that has not yet
attempted a connection: `SSLSocket._real_connect` assigns `self._sslobj` before calling
`super().connect`, so after a blocked connect the same object's `sendto` takes an early
`ValueError` branch instead. Still safe, different mechanism — which is why each of those
tests wraps a fresh socket.

**Spawns: allowlisted by program.** These entry points are wrapped rather than replaced —
the program about to be executed is inspected, and the call proceeds only if it is on the
allowlist:

- `subprocess.run`, `call`, `check_call`, `check_output`
- `subprocess.Popen`, in its own right. `run()` delegates to `Popen`, so guarding `Popen`
  alone would cover both — but the reverse is not true, and F3 streams logs off a `Popen`
  directly. Guarded by subclassing, so `subprocess.Popen` stays a class and `isinstance`
  keeps working.
- `asyncio.create_subprocess_exec`
- `os.posix_spawn`, `os.posix_spawnp` — **allowlisted, not denied, and this is
  load-bearing.** CPython's `subprocess` routes through them on its fast path when the
  program is an absolute path and `posix_spawn_file_actions_addclosefrom_np` is available:
  true on glibc ≥ 2.34, false on macOS. Denying them passes on a mac and turns Linux CI
  red, with a `GuardViolation` on an ordinary `git` call that every reviewer would read as
  a guard bug.

| Permitted | Why | Who consumes it |
|---|---|---|
| `git` | The failure modes under test are git's actual behavior, so mocks would encode assumptions about git rather than git | Issue 05, testing resolvers against real temporary repositories |
| `sys.executable`, `bessemer` | Driving the CLI as a real process is the only way to test what a user actually invokes | This issue's own pass-through tests, and any later test that drives the CLI end to end |

Everything else is denied by omission — **including `docker`, which is the constraint that
actually matters.** The constraint was never "no subprocesses".

Checking `argv[0]` alone would leave the allowlist decorative, so every path by which a
caller can name a different program is checked too: `executable=`, given positionally or
by keyword, which overrides `argv[0]` outright; and argv arriving by keyword rather than
position. `shell=True` is refused on every call, including calls to permitted programs —
pinned by a test that passes an argv of `["git", "--version"]`, so `git` alone is permitted
and only the shell branch can refuse it — and a call shaped in a way the guard cannot read
a program out of fails closed.

**The originals are a known exclusion, the way `_socket` is on the network side.** A
wrapper has to hold what it replaced in order to call it, so `subprocess.Popen.__base__`
and the callable captured inside each checked wrapper both reach an unguarded spawn. That
is acceptable for the same reason `_socket` is — accident and drift, not a hostile author —
but `__base__` is ordinary public attribute access rather than a private module, so the
carve-out does not obviously cover it, and it is stated rather than left to be discovered.

Some paths are denied wholesale rather than allowlisted, for three different reasons.
Keeping the reasons distinct matters: whoever widens this later should widen it for the
right one.

| Denied | Reason |
|---|---|
| `subprocess.getoutput`, `getstatusoutput`, `asyncio.create_subprocess_shell`, `os.system`, `os.popen` | Take a shell command line rather than an argv. There is nothing to inspect, and the shell is the interpolation hazard the project exists to escape |
| `os.fork`, `os.forkpty` | Duplicate the current process instead of executing a named one, so there is no program for an allowlist to be about |
| `os.exec*` and `os.spawn*` — **both the `l` and `v` spellings**, `pty.spawn` | These do present a program and could be allowlisted. Denied by policy rather than by calling convention: splitting the family into permitted `v` forms and denied `l` forms would state one rule two ways. `exec*` replaces the interpreter's process image, ending the run; `spawn*` is fork-and-exec and does return, but it is a second route around `subprocess` with no timeout discipline; `pty.spawn` forks and proxies a pseudo-terminal. No test needs any of them |

The guard exports `SPAWN_ENTRY_POINTS`, the inventory of every spawn path it covers, as
`(module, attribute)` pairs. Issue 03's AST test imports that set rather than restating
it, so the two cannot drift into disagreeing about which paths count as spawning. They
cover the same paths and differ in **disposition**, not in scope: this guard permits `git`
and the interpreter; issue 03 forbids all spawning outside `bessemer/proc.py`.

Two things about its shape are deliberate. **Pairs, not dotted strings** — a static
consumer resolving `"subprocess.run"` has only the bare name `run` to match on, which is
exactly the name issue 03's own wrapper exports and every other module in `bessemer/`
calls; the pair keeps them apart. Alias tracking (`import subprocess as sp`) stays the
static consumer's problem. **Declared in full, filtered only at arm time** — `install()`
skips names this interpreter lacks, since you cannot patch what does not exist, but a
static ban whose contents vary by host is the drift the export exists to prevent.
`ALLOWLISTED_ENTRY_POINTS` and `DENIED_ENTRY_POINTS` are exported alongside it, so a test
can assert which disposition each entry point actually has.

`NETWORK_ENTRY_POINTS` gives the network half the same treatment — `(target, attribute)`
pairs, the `socket` module for the helpers and the `socket.socket` class for the methods —
because without it the network half has no per-entry coverage. Several names are masked by
a neighbour catching the same test for a different reason (`create_connection` by the
patched `getaddrinfo`, `create_server` by the patched `bind`), and the four resolution
helpers have no backstop at all, so dropping one silently reopens name resolution. The test
that pins the enumeration restates it as a literal, deliberately: an assertion derived from
the export alone cannot notice a name leaving it.

An allowlist rather than a docker-shaped blocklist, for the same reason `bessemer/proc.py`
uses one (issue 03): a blocklist loses to the next binary someone reaches for. When F3
needs docker in tests, it widens `ALLOWED_PROGRAMS` explicitly — a reviewable act in a
diff, not a quiet loosening.

`GuardViolation` subclasses `BaseException` so an `except Exception:` in the code under
test cannot swallow it.

If a test needs something the allowlist denies, it needs a temporary fixture it creates
and destroys, or a mock. Reach for the allowlist only when the binary itself is the thing
under test.

## Tests that pass for the wrong reason

The recurring defect shape in this feature is not a test that fails; it is a test that
passes for a reason other than the one in its name, and goes on passing after the
behaviour it names is deleted. Two cases are load-bearing enough to write down, because
both are invisible on a green run.

**A missing binary raises `GuardViolation`, not `FileNotFoundError`.** The guard checks
the program *before* the spawn happens, so an obviously-absent name like
`no-such-program` never reaches the operating system: the guard refuses it first. A test
meaning to prove that `bessemer.proc.run` lets an `OSError` propagate would therefore pass
on the guard's refusal instead — and keep passing if `run` were changed to swallow
`OSError` entirely.

The fix is to name a program the allowlist **permits**, at a path that does not exist:
`tests/test_proc.py` spawns `<a temporary directory>/git`. The basename is `git`, so the
guard waves it through; the path is not there, so the kernel is what refuses it, which is
the thing under test. Any test about how a spawn *fails* needs this treatment.

**A child cannot block on stdin that is not a terminal — and whether it is one is a
property of the host, not of the code.** `make check` redirects only stderr, so fd 0 is
inherited from whoever ran it: the developer's terminal under an interactive shell, a pipe
or `/dev/null` under CI and under most editors. Both happen, routinely.

That cuts two ways, and each has already produced a defect here.

A test checking that `run` closes its child's stdin passes for free wherever fd 0 is *not*
a terminal: the child reads EOF immediately whether or not the wrapper closed anything, so
the test stays green on an implementation that stopped doing it. `StdinTest` therefore
builds a real pty with `pty.openpty` and `dup2`s it onto fd 0 for the duration, restoring
it afterwards. (`pty.openpty` forks nothing and is not on the denied list; `pty.spawn`
is.) It runs a *control* alongside the assertion — the same child, spawned through
`subprocess` with stdin inherited, which must consume the entire timeout — because without
the control the fixture itself could be broken and the real test would still look green.

The other direction: **no test may assert anything about the ambient fd 0.** An
`assertFalse(os.isatty(0))` outside the fixture is an assertion about the host. It is
green in CI and green in an agent session, and red under `make check` in an interactive
shell — failing on a correct implementation in the one place a human is watching, and
passing in the one place they are not. Capture what fd 0 was, assert only inside the
fixture, and check restoration against what was captured. **The suite must pass whether or
not the runner's stdin is a terminal**, which means verifying it both ways:

```
uv run python -m unittest discover                     # ordinary runner
script -q /dev/null uv run python -m unittest discover  # with a tty on fd 0
```
