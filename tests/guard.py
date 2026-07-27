"""Makes the suite's environment constraint structural instead of aspirational.

The suite must pass with no Docker daemon, no network, and outside any git work tree.
Rather than asking the runner to arrange those conditions, this module removes the ability
to depend on them.

Blocking beats arranging. It holds whatever state the machine is in — the daemon can be
running, as it usually is on a dev box — it is repeatable, and CI can enforce it, which
"stop Docker Desktop first" never could.

**This guard defends against accident and drift, not against a hostile test author.** A
test that deliberately reaches for a private module to escape it has defeated it, and that
is acceptable — ADR 0001's container boundary is what stands against intent. This one
stands against a future author who reaches for `docker` in a unit test because it was
convenient, and against the slow loosening that follows. Every claim in these comments must
therefore be true and falsifiable, and where coverage has a known limit the limit is named
rather than papered over: see `_socket` under the network enumeration.

Two halves, deliberately asymmetric:

- **Network: reaching is banned, constructing is not.** Creating a socket contacts nothing.
- **Spawns: allowlisted by program.** `git`, the interpreter, and the installed console
  script are permitted. Everything else is denied by omission — including `docker`, which
  is the constraint that actually matters. The constraint was never "no subprocesses".

An allowlist rather than a docker-shaped blocklist, for the same reason `bessemer/proc.py`
uses one in issue 03: a blocklist loses to the next binary someone reaches for. Widening
`ALLOWED_PROGRAMS` is a reviewable act, not a quiet loosening.

Checking `argv[0]` alone would make the allowlist decorative, so every path by which a
caller can name a different program is checked: `executable=`, which overrides `argv[0]`
outright, and argv arriving by keyword rather than position.

`SPAWN_ENTRY_POINTS` is the public inventory of what counts as spawning. Issue 03's AST
test imports it instead of restating the list, so the two cannot drift into disagreeing
about which paths are spawn paths. They cover the same paths and differ in disposition:
this guard permits `git` and the interpreter; issue 03 forbids all spawning outside
`bessemer/proc.py`.

Installed by `tests/__init__.py`, so it is armed before any test module is imported.
"""

import asyncio
import os
import pty
import socket
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any, ClassVar, Final, Never


# A BaseException, deliberately. A guard that an `except Exception:` inside the code under
# test can swallow is not a guard — the same reason KeyboardInterrupt and SystemExit sit
# outside the Exception hierarchy.
class GuardViolation(BaseException):
    """A test reached for the network, or tried to spawn something not on the allowlist."""


ALLOWED_PROGRAMS: Final = frozenset({"git", "bessemer"})
"""Basenames a test may execute. `sys.executable` is allowed separately, by path.

`git` is for issue 05, which tests resolvers against real temporary repositories rather
than mocks. `bessemer` and the interpreter are for this issue's own pass-through tests and
for any later test that drives the CLI end to end.
"""

DENIED: Final = "denied"
ALLOWLISTED: Final = "allowlisted"

_MISSING: Final = object()

# Module-level network helpers. The ban is on *reaching the network* — connecting and
# resolving — not on holding a socket object. Constructing one, and `socketpair()`, are
# deliberately left alone: `socketpair` returns an unnamed local pair with no address and no
# route off the machine, and asyncio's event loop builds its self-pipe out of exactly that.
# Banning either would ban asyncio outright, while this guard allowlists
# `asyncio.create_subprocess_exec` — a ban that contradicts the allowlist reads as a bug and
# gets removed by whoever hits it.
#
# Name resolution means all of it, `getnameinfo` included: it is the reverse lookup, it is a
# C builtin in its own right, and nothing about banning `getaddrinfo` stops it — so leaving
# it out would leave a live way off the machine inside a list that reads as exhaustive.
_NETWORK_PATHS: Final = (
    "create_connection",
    "create_server",
    "getaddrinfo",
    "getnameinfo",
    "gethostbyname",
    "gethostbyname_ex",
    "gethostbyaddr",
)

# Patched on the Python `socket.socket` class, which covers it and its Python subclasses.
#
# **`_socket` is a known exclusion.** `socket.socket.__base__` is the C type
# `_socket.socket`, which is immutable — patching it raises `TypeError: cannot set 'connect'
# attribute of immutable type '_socket.socket'` — so `socket.socket.__base__(...)` reaches
# the network by design and cannot be stopped here. That is on the acceptable side of
# "accident and drift, not a hostile author", but only because it is stated: left unstated,
# this list reads as exhaustive and the next person widening it believes something false.
#
# `ssl.SSLSocket` is covered, and covered incidentally: it subclasses `socket.socket`, and
# four of the five methods it overrides — `connect`, `connect_ex`, `accept`, `sendto` —
# delegate through `super()` to the patched ones. Incidental coverage that nothing pins is
# one upstream refactor from being a hole, so `SSLSocketTest` pins all four.
#
# The fifth, `sendmsg`, never reaches this guard: `ssl` refuses it itself with
# `NotImplementedError: sendmsg not allowed on instances of <class 'ssl.SSLSocket'>`. It is
# pinned to that behavior instead — asserting a GuardViolation there would pass for the
# wrong reason and would break if CPython ever dropped the early check.
#
# `sendto` is covered only on a socket that has not yet attempted a connection.
# `SSLSocket._real_connect` assigns `self._sslobj` *before* calling `super().connect`, so
# after a blocked connect the same object's `sendto` takes an early `ValueError` branch and
# never delegates. Still safe, different mechanism — which is why each test below wraps a
# fresh socket rather than reusing one.
#
# `sendto` and `sendmsg` are here because they need no prior `connect`: a UDP socket sends
# to a literal address, and with DNS already banned that is the one remaining way out.
# `bind`/`listen`/`accept` are the listen side of the same enumeration — `create_server` is
# denied above, so leaving its constituent parts open would ban the helper and not the act.
#
# `send`, `sendall`, and `recv` are deliberately absent: the Unix event loop's self-pipe is
# a socketpair and uses them, and on a socket that was never allowed to connect they reach
# nothing. Denying `sendto` costs asyncio nothing because asyncio never calls it —
# `BaseSelectorEventLoop._write_to_self` writes to the self-pipe with `csock.send(b'\0')`.
# Not because `sendto` fails on a socketpair: measured with the guard disarmed,
# `sock.sendto(b'x', b'')` on an AF_UNIX stream pair returns 1 and the peer receives it.
# Only the address-bearing form errors, and on argument type — `TypeError: a bytes-like
# object is required, not 'tuple'` — not because of the pair.
_NETWORK_SOCKET_METHODS: Final = (
    "connect",
    "connect_ex",
    "sendto",
    "sendmsg",
    "bind",
    "listen",
    "accept",
)

NETWORK_ENTRY_POINTS: Final = frozenset(
    [(socket, name) for name in _NETWORK_PATHS]
    + [(socket.socket, name) for name in _NETWORK_SOCKET_METHODS]
)
"""Every network path this guard refuses, as `(target, attribute)` pairs.

The target is the `socket` module for the module-level helpers and the `socket.socket`
class for the methods, so a test can read the installed object off it and check its
disposition — the same treatment `SPAWN_ENTRY_POINTS` gets. It exists because the network
half has no per-entry coverage otherwise: several names are masked by a neighbour catching
the same test for a different reason (`create_connection` by the patched `getaddrinfo`,
`create_server` by the patched `bind`), and `gethostbyname`, `gethostbyname_ex`,
`gethostbyaddr`, and `getnameinfo` have no backstop at all, so dropping one silently
reopens name resolution while every test stays green.
"""


@dataclass(frozen=True)
class _Spawner:
    """An entry point that names a program, and where in its call signature it does so."""

    module: ModuleType
    name: str
    program_index: int | None = None
    program_keyword: str | None = None
    # `executable=` overrides argv[0] entirely: `run(["git"], executable=".../docker")`
    # runs docker. Both are checked, so neither position can smuggle a program past.
    executable_index: int | None = None
    executable_keyword: str | None = None
    shell_index: int | None = None

    @property
    def label(self) -> str:
        return f"{self.module.__name__}.{self.name}"


# `subprocess.Popen(args, bufsize, executable, ..., shell, ...)` — `run`, `call`,
# `check_call`, and `check_output` all forward positionally to it, so one shape covers all
# five.
def _subprocess_spawner(name: str) -> _Spawner:
    return _Spawner(
        module=subprocess,
        name=name,
        program_index=0,
        program_keyword="args",
        executable_index=2,
        executable_keyword="executable",
        shell_index=8,
    )


_POPEN: Final = _subprocess_spawner("Popen")

_ALLOWLISTED_SPAWNERS: Final = (
    _subprocess_spawner("run"),
    _subprocess_spawner("call"),
    _subprocess_spawner("check_call"),
    _subprocess_spawner("check_output"),
    _POPEN,
    # `create_subprocess_exec(program, *args, ...)` — everything after the program is an
    # argument to it, so there is no positional `executable` slot to check.
    _Spawner(
        module=asyncio,
        name="create_subprocess_exec",
        program_index=0,
        program_keyword="program",
        executable_keyword="executable",
    ),
    # Allowlisted rather than denied, and this is load-bearing: CPython's `subprocess`
    # routes through `posix_spawn` on its fast path when the program is an absolute path
    # and `posix_spawn_file_actions_addclosefrom_np` is available — true on glibc >= 2.34,
    # false on macOS. Denying these would pass on a mac and turn Linux CI red, with a
    # GuardViolation for a `git` call that every reviewer would read as a guard bug. A
    # denial-only test cannot tell an allowlisted `posix_spawn` from a denied one, so
    # `AllowedSpawnTest` runs a real program through both.
    _Spawner(module=os, name="posix_spawn", program_index=0),
    _Spawner(module=os, name="posix_spawnp", program_index=0),
)

# Denied wholesale. Three distinct reasons, kept distinct on purpose — a security note that
# is subtly wrong about *why* teaches the wrong rule to whoever widens it later.
_DENIED_SHELL: Final = (
    # Take a shell command line rather than an argv: there is nothing to inspect, and the
    # shell is the interpolation hazard the project exists to escape.
    (subprocess, "getoutput"),
    (subprocess, "getstatusoutput"),
    (asyncio, "create_subprocess_shell"),
    (os, "system"),
    (os, "popen"),
)
_DENIED_NO_CHILD_PROGRAM: Final = (
    # Duplicate the current process rather than executing a named one, so there is no
    # program for an allowlist to be about.
    (os, "fork"),
    (os, "forkpty"),
)
_DENIED_NO_LEGITIMATE_USE: Final = (
    # These all present a program and could be allowlisted. Denied anyway, by policy
    # rather than by calling convention — splitting the family into permitted `v` forms
    # and denied `l` forms would state the same rule two different ways.
    #
    # `exec*` replaces the interpreter's process image, ending the test run. `spawn*` is
    # fork-and-exec and does return, but it is a second route around `subprocess` with no
    # timeout discipline. `pty.spawn` forks and proxies a pseudo-terminal. No test needs
    # any of them; `subprocess` is the supported path, and it is allowlisted above.
    (os, "execv"),
    (os, "execve"),
    (os, "execvp"),
    (os, "execvpe"),
    (os, "execl"),
    (os, "execle"),
    (os, "execlp"),
    (os, "execlpe"),
    (os, "spawnv"),
    (os, "spawnve"),
    (os, "spawnvp"),
    (os, "spawnvpe"),
    (os, "spawnl"),
    (os, "spawnle"),
    (os, "spawnlp"),
    (os, "spawnlpe"),
    (pty, "spawn"),
)

ALLOWLISTED_ENTRY_POINTS: Final = frozenset(
    (spawner.module, spawner.name) for spawner in _ALLOWLISTED_SPAWNERS
)
DENIED_ENTRY_POINTS: Final = frozenset(
    _DENIED_SHELL + _DENIED_NO_CHILD_PROGRAM + _DENIED_NO_LEGITIMATE_USE
)

SPAWN_ENTRY_POINTS: Final = ALLOWLISTED_ENTRY_POINTS | DENIED_ENTRY_POINTS
"""Every spawn path this guard covers, as `(module, attribute)` pairs.

Pairs rather than dotted strings: a static consumer resolving `"subprocess.run"` has only
the bare name `run` to match on, which is the name issue 03's own wrapper exports and every
other module in `bessemer/` calls. The pair keeps the two apart. Alias tracking
(`import subprocess as sp`) remains the static consumer's job; this export cannot help with
it and does not pretend to.

Declared in full and never filtered by platform. `install()` skips names this interpreter
lacks — you cannot patch what does not exist — but a static ban whose contents vary by host
is precisely the drift this export exists to prevent.
"""

_installed = False


def _violation(what: str) -> Never:
    raise GuardViolation(
        f"{what}. The unit suite must pass with no Docker daemon, no network, and outside "
        f"any git work tree. Spawns are allowlisted to {sorted(ALLOWED_PROGRAMS)} and the "
        f"interpreter; see tests/README.md."
    )


class _Refuser:
    """Replaces an entry point outright. Carries its disposition so tests can read it."""

    guard_disposition: ClassVar[str] = DENIED

    def __init__(self, what: str) -> None:
        self._what = what

    def __call__(self, *args: object, **kwargs: object) -> Never:
        _violation(self._what)


def _permitted(program: object) -> bool:
    """Whether `program` — the thing about to be executed — is on the allowlist."""
    if isinstance(program, os.PathLike):
        program = os.fspath(program)
    if not isinstance(program, str):
        # bytes argv, or something that is not a path at all. Not worth inspecting: a test
        # that needs to spawn something can spell it as a str like everything else.
        return False
    if os.path.basename(program) in ALLOWED_PROGRAMS:
        return True
    return os.path.realpath(program) == os.path.realpath(sys.executable)


def _program_of(args: Any) -> object:
    """The program an argv-ish argument will execute."""
    if isinstance(args, (str, bytes, os.PathLike)):
        return args
    try:
        return args[0]
    except (TypeError, KeyError, IndexError):
        return None


def _extract(
    args: tuple[object, ...],
    kwargs: dict[str, Any],
    index: int | None,
    keyword: str | None,
) -> object:
    """Find an argument given either positionally or by keyword."""
    if keyword is not None and keyword in kwargs:
        return kwargs[keyword]
    if index is not None and len(args) > index:
        return args[index]
    return _MISSING


def _check(spawner: _Spawner, args: tuple[object, ...], kwargs: dict[str, Any]) -> None:
    """Raise unless every program this call could execute is on the allowlist."""
    label = spawner.label

    shell = _extract(args, kwargs, spawner.shell_index, "shell")
    if shell is not _MISSING and shell:
        _violation(f"{label} was called with shell=True")

    program = _extract(args, kwargs, spawner.program_index, spawner.program_keyword)
    if program is _MISSING:
        # Fail closed. A call shaped in a way this guard does not model is exactly the
        # case where it must not wave anything through.
        _violation(f"{label} was called in a form the guard cannot read a program out of")
    if not _permitted(_program_of(program)):
        _violation(f"the suite tried to spawn {_program_of(program)!r} via {label}")

    executable = _extract(args, kwargs, spawner.executable_index, spawner.executable_keyword)
    if executable is not _MISSING and executable is not None and not _permitted(executable):
        _violation(
            f"the suite tried to spawn {executable!r} via {label} — `executable=` overrides "
            f"argv[0], so allowlisting argv[0] alone would be decorative"
        )


class _Checked:
    """Wraps an entry point so it runs only when every program it could execute is allowed.

    **The originals are a known exclusion, the way `_socket` is on the network side.** A
    wrapper has to hold what it replaced in order to call it, so `subprocess.Popen.__base__`
    and the captured `_real` below both reach an unguarded spawn. That falls on the
    acceptable side of "accident and drift, not a hostile author" — but `__base__` is
    ordinary public attribute access rather than a private module, so the carve-out does not
    obviously cover it, and it is stated here rather than left to be discovered.
    """

    guard_disposition: ClassVar[str] = ALLOWLISTED

    def __init__(self, real: Callable[..., object], spawner: _Spawner) -> None:
        self._real = real
        self._spawner = spawner

    def __call__(self, *args: object, **kwargs: Any) -> object:
        _check(self._spawner, args, kwargs)
        return self._real(*args, **kwargs)


class _GuardedPopen(subprocess.Popen[Any]):
    """Subclassed rather than wrapped, so `subprocess.Popen` stays a class.

    A plain callable would break `isinstance(proc, subprocess.Popen)` and any annotation
    referring to it — F3 streams logs off a `Popen` and will do both. Defined against the
    real class at import time, before `install()` swaps the module attribute.
    """

    guard_disposition: ClassVar[str] = ALLOWLISTED

    def __init__(self, *args: object, **kwargs: Any) -> None:
        _check(_POPEN, args, kwargs)
        super().__init__(*args, **kwargs)


def install() -> None:
    """Arm the guard. Idempotent, so an accidental second call cannot double-wrap."""
    global _installed
    if _installed:
        return
    _installed = True

    for name in _NETWORK_PATHS:
        setattr(socket, name, _Refuser(f"the suite reached for the network via socket.{name}"))

    for name in _NETWORK_SOCKET_METHODS:
        setattr(
            socket.socket,
            name,
            _Refuser(f"the suite reached for the network via socket.socket.{name}"),
        )

    for module, name in DENIED_ENTRY_POINTS:
        # Filtered here and only here: you cannot patch what this interpreter does not have.
        if hasattr(module, name):
            setattr(module, name, _Refuser(f"the suite reached for {module.__name__}.{name}"))

    for spawner in _ALLOWLISTED_SPAWNERS:
        if spawner is _POPEN or not hasattr(spawner.module, spawner.name):
            continue
        setattr(
            spawner.module,
            spawner.name,
            _Checked(getattr(spawner.module, spawner.name), spawner),
        )

    setattr(subprocess, "Popen", _GuardedPopen)
