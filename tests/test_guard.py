"""The guard must be armed for every run, not just the run someone remembered to check.

The deliberate-violation demonstration required by issue 01a is a throwaway; these are the
permanent regression tests that the guard is armed, denies what it should, and — just as
importantly — still lets through every entry on the allowlist. A guard tested only on its
denials goes green while denying everything, which is exactly the state where the suite has
silently stopped testing anything.
"""

import _socket
import asyncio
import os
import pty
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from tests.guard import (
    ALLOWED_PROGRAMS,
    ALLOWLISTED,
    ALLOWLISTED_ENTRY_POINTS,
    DENIED,
    DENIED_ENTRY_POINTS,
    NETWORK_ENTRY_POINTS,
    SPAWN_ENTRY_POINTS,
    GuardViolation,
)

CONSOLE_SCRIPT = Path(sys.executable).parent / "bessemer"


def git_path() -> str:
    """The absolute path to git. `shutil.which` scans PATH; it spawns nothing."""
    found = shutil.which("git")
    assert found is not None, "git must be installed to run this suite"
    return found


class NetworkTest(unittest.TestCase):
    def test_connecting_is_blocked(self) -> None:
        with self.assertRaises(GuardViolation):
            socket.create_connection(("example.com", 80))

    def test_resolving_a_name_is_blocked(self) -> None:
        with self.assertRaises(GuardViolation):
            socket.getaddrinfo("example.com", 80)

    def test_resolving_a_name_backwards_is_blocked(self) -> None:
        """`getnameinfo` is the reverse lookup and a C builtin — banning `getaddrinfo` does
        nothing to it, so it needs its own denial."""
        with self.assertRaises(GuardViolation):
            socket.getnameinfo(("1.2.3.4", 53), 0)

    def test_connecting_a_hand_built_socket_is_blocked(self) -> None:
        """Holding a socket is allowed; reaching out with it is not."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            with self.assertRaises(GuardViolation):
                sock.connect(("127.0.0.1", 9))

    def test_a_connectionless_send_is_blocked(self) -> None:
        """UDP needs no `connect`, and a literal address needs no DNS."""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            with self.assertRaises(GuardViolation):
                sock.sendto(b"x", ("1.2.3.4", 53))

    def test_sendmsg_is_blocked(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            with self.assertRaises(GuardViolation):
                sock.sendmsg([b"x"], [], 0, ("1.2.3.4", 53))

    def test_the_listen_side_is_blocked(self) -> None:
        """`create_server` is denied, so its constituent parts must be too."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            for reach_out in (
                lambda: sock.bind(("127.0.0.1", 0)),
                lambda: sock.listen(1),
                lambda: sock.accept(),
            ):
                with self.assertRaises(GuardViolation):
                    reach_out()

    def test_a_local_socketpair_still_works(self) -> None:
        """asyncio's event loop builds its self-pipe from one; banning it bans asyncio."""
        left, right = socket.socketpair()
        with left, right:
            left.send(b"ping")
            self.assertEqual(right.recv(4), b"ping")
            left.sendall(b"pong")
            self.assertEqual(right.recv(4), b"pong")

    def test_violation_survives_a_broad_except(self) -> None:
        """A guard an `except Exception:` can swallow would not be a guard."""
        with self.assertRaises(GuardViolation):
            try:
                socket.getaddrinfo("example.com", 80)
            except Exception:
                self.fail("GuardViolation was caught by `except Exception`")


class KnownExclusionTest(unittest.TestCase):
    """The C base type cannot be patched. Naming the limit is the point."""

    def test_the_python_socket_class_sits_on_an_immutable_c_type(self) -> None:
        self.assertIs(socket.socket.__base__, _socket.socket)

    def test_the_c_type_cannot_be_patched(self) -> None:
        with self.assertRaises(TypeError) as caught:
            _socket.socket.connect = None
        self.assertIn("immutable type", str(caught.exception))


class SSLSocketTest(unittest.TestCase):
    """`ssl.SSLSocket`'s coverage is real but incidental — pin it rather than assume it.

    Every test wraps a *fresh* socket. `SSLSocket._real_connect` assigns `self._sslobj`
    before calling `super().connect`, so reusing one across calls silently changes which
    branch `sendto` takes — and would make this class pass for the wrong reason.
    """

    def wrapped(self) -> ssl.SSLSocket:
        context = ssl.create_default_context()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        return context.wrap_socket(
            sock, server_hostname="example.com", do_handshake_on_connect=False
        )

    def test_it_subclasses_the_patched_python_class(self) -> None:
        self.assertIn(socket.socket, ssl.SSLSocket.__mro__)

    def test_connecting_is_blocked(self) -> None:
        with self.wrapped() as tls:
            with self.assertRaises(GuardViolation):
                tls.connect(("example.com", 443))

    def test_connect_ex_is_blocked(self) -> None:
        with self.wrapped() as tls:
            with self.assertRaises(GuardViolation):
                tls.connect_ex(("example.com", 443))

    def test_accepting_is_blocked(self) -> None:
        with self.wrapped() as tls:
            with self.assertRaises(GuardViolation):
                tls.accept()

    def test_a_connectionless_send_is_blocked(self) -> None:
        with self.wrapped() as tls:
            with self.assertRaises(GuardViolation):
                tls.sendto(b"x", ("1.2.3.4", 53))

    def test_sendmsg_is_refused_by_ssl_before_it_reaches_the_guard(self) -> None:
        """The one override that never delegates. Pinned to what actually stops it.

        Asserting a GuardViolation here would pass for the wrong reason, and would break
        if CPython ever dropped this early check.
        """
        with self.wrapped() as tls:
            with self.assertRaises(NotImplementedError) as caught:
                tls.sendmsg([b"x"], [], 0, ("1.2.3.4", 53))
        self.assertIn("not allowed on instances of", str(caught.exception))


class InventoryTest(unittest.TestCase):
    def test_the_export_is_module_attribute_pairs(self) -> None:
        """Dotted strings would leave a static consumer matching the bare name `run`."""
        for module, name in SPAWN_ENTRY_POINTS:
            self.assertIsInstance(module, ModuleType)
            self.assertIsInstance(name, str)

    def test_the_export_is_the_union_of_both_dispositions(self) -> None:
        self.assertEqual(SPAWN_ENTRY_POINTS, ALLOWLISTED_ENTRY_POINTS | DENIED_ENTRY_POINTS)
        self.assertFalse(ALLOWLISTED_ENTRY_POINTS & DENIED_ENTRY_POINTS)

    def test_every_entry_point_carries_its_declared_disposition(self) -> None:
        """Fails when `install()` and the declared sets disagree about an entry point.

        Deliberately *not* what catches a move between the sets: a move edits both tables,
        so the declaration and the installed object agree again and this passes. It catches
        the half-move — a name added to one set, or `install()` growing a branch that stops
        matching what was declared. `test_posix_spawn_is_allowlisted` and the pass-throughs
        in `AllowedSpawnTest` are what catch a full move.
        """
        declared = {pair: ALLOWLISTED for pair in ALLOWLISTED_ENTRY_POINTS}
        declared.update({pair: DENIED for pair in DENIED_ENTRY_POINTS})
        for (module, name), disposition in declared.items():
            if not hasattr(module, name):
                continue  # install() skips these too; the export still declares them
            with self.subTest(entry_point=f"{module.__name__}.{name}"):
                target = getattr(module, name)
                self.assertEqual(getattr(target, "guard_disposition", None), disposition)

    def test_the_denied_enumeration_is_exactly_what_is_declared(self) -> None:
        """Restated as a literal, for the same reason the network one is: an assertion
        derived from the export cannot notice a name leaving it, because removal drops it
        from both sides at once and the two agree again.

        `DeniedSpawnTest` covers `os.system`, `os.fork`, `os.execv` and
        `asyncio.create_subprocess_shell` behaviorally; every other name here has no
        behavior test, and three that appear to — `subprocess.getoutput`,
        `getstatusoutput`, `os.popen` — are *masked* rather than covered. Un-patched they
        route through `check_output` or `Popen` with `shell=True`, so the shell branch
        raises and their tests keep passing with the denial gone. This literal is what
        actually holds the enumeration in place.
        """
        self.assertEqual(
            DENIED_ENTRY_POINTS,
            frozenset(
                [
                    (subprocess, "getoutput"),
                    (subprocess, "getstatusoutput"),
                    (asyncio, "create_subprocess_shell"),
                    (os, "system"),
                    (os, "popen"),
                    (os, "fork"),
                    (os, "forkpty"),
                    (pty, "spawn"),
                ]
                + [
                    (os, f"{family}{spelling}")
                    for family in ("exec", "spawn")
                    for spelling in ("v", "ve", "vp", "vpe", "l", "le", "lp", "lpe")
                ]
            ),
        )

    def test_the_network_enumeration_is_exactly_what_is_declared(self) -> None:
        """Restated as a literal on purpose: an assertion derived from the export alone
        cannot notice a name leaving it, and the network half has no per-entry coverage
        otherwise. `create_connection` is masked by the patched `getaddrinfo` and
        `create_server` by the patched `bind`, so dropping either stays green; the four
        resolution helpers have no backstop at all, so dropping one reopens name resolution
        with the suite still green. Widening the ban means editing this list too.
        """
        self.assertEqual(
            NETWORK_ENTRY_POINTS,
            frozenset(
                [
                    (socket, name)
                    for name in (
                        "create_connection",
                        "create_server",
                        "getaddrinfo",
                        "getnameinfo",
                        "gethostbyname",
                        "gethostbyname_ex",
                        "gethostbyaddr",
                    )
                ]
                + [
                    (socket.socket, name)
                    for name in (
                        "connect",
                        "connect_ex",
                        "sendto",
                        "sendmsg",
                        "bind",
                        "listen",
                        "accept",
                    )
                ]
            ),
        )

    def test_every_network_entry_point_is_installed_as_a_refusal(self) -> None:
        """The network half of `test_every_entry_point_carries_its_declared_disposition`:
        it catches `install()` and the declaration disagreeing about a name."""
        for target, name in NETWORK_ENTRY_POINTS:
            label = (
                f"{target.__name__}.{name}"
                if isinstance(target, ModuleType)
                else f"{target.__module__}.{target.__qualname__}.{name}"
            )
            with self.subTest(entry_point=label):
                installed = getattr(target, name)
                self.assertEqual(getattr(installed, "guard_disposition", None), DENIED)

    def test_posix_spawn_is_allowlisted(self) -> None:
        """Pins the membership itself, which is what a move between the sets changes.

        The entry that matters most: CPython's `subprocess` routes through `posix_spawn` on
        glibc and not on macOS, so denying it passes locally and shows up only as red Linux
        CI — a `GuardViolation` on a `git` call that reads as a guard bug. This test and the
        pass-throughs in `AllowedSpawnTest` fail together if it is ever demoted.
        """
        for name in ("posix_spawn", "posix_spawnp"):
            self.assertIn((os, name), ALLOWLISTED_ENTRY_POINTS)
            self.assertNotIn((os, name), DENIED_ENTRY_POINTS)


class DeniedSpawnTest(unittest.TestCase):
    def test_docker_is_denied(self) -> None:
        """Denied by omission from the allowlist, not by being named."""
        with self.assertRaises(GuardViolation):
            subprocess.run(["docker", "info"], check=False)

    def test_an_arbitrary_binary_is_denied(self) -> None:
        """What an allowlist buys: the next binary someone reaches for is already denied."""
        with self.assertRaises(GuardViolation):
            subprocess.run(["kubectl", "get", "pods"], check=False)

    def test_popen_is_denied_in_its_own_right(self) -> None:
        """`run()` delegates to `Popen`, but F3 streams logs off `Popen` directly."""
        with self.assertRaises(GuardViolation):
            subprocess.Popen(["docker", "logs", "-f", "x"])

    def test_shell_true_is_denied_even_for_an_allowed_program(self) -> None:
        """argv is `["git", "--version"]`, so `git` alone is permitted and only the shell
        branch can refuse this. Passing the command as the string `"git --version"` would
        test nothing: that whole string is not an allowed basename, so the *program* check
        denies it and the shell branch never decides anything."""
        with self.assertRaises(GuardViolation) as caught:
            subprocess.run(["git", "--version"], shell=True, check=False)
        self.assertIn("shell=True", str(caught.exception))

    def test_shell_given_positionally_is_still_denied(self) -> None:
        """`Popen(args, bufsize, executable, stdin, stdout, stderr, preexec_fn, close_fds,
        shell, ...)` — `shell` is position 8, not only a keyword."""
        with self.assertRaises(GuardViolation) as caught:
            subprocess.Popen(["git", "--version"], -1, None, None, None, None, None, True, True)
        self.assertIn("shell=True", str(caught.exception))

    def test_shelling_out_without_argv_is_denied(self) -> None:
        with self.assertRaises(GuardViolation):
            subprocess.getoutput("docker info")

    def test_os_system_is_denied(self) -> None:
        with self.assertRaises(GuardViolation):
            os.system("docker info")

    def test_forking_is_denied(self) -> None:
        with self.assertRaises(GuardViolation):
            os.fork()

    def test_exec_family_is_denied_including_the_v_spellings(self) -> None:
        """Denied by policy, not by calling convention — `l` and `v` alike."""
        with self.assertRaises(GuardViolation):
            os.execv("/bin/sh", ["sh", "-c", "true"])
        with self.assertRaises(GuardViolation):
            os.execl("/bin/sh", "sh", "-c", "true")

    def test_spawn_family_is_denied_including_the_v_spellings(self) -> None:
        with self.assertRaises(GuardViolation):
            os.spawnv(os.P_WAIT, "/bin/sh", ["sh", "-c", "true"])
        with self.assertRaises(GuardViolation):
            os.spawnl(os.P_WAIT, "/bin/sh", "sh", "-c", "true")

    def test_posix_spawn_denies_a_program_off_the_allowlist(self) -> None:
        """The allowlist check runs here too — see the pass-through test for the other half."""
        with self.assertRaises(GuardViolation):
            os.posix_spawn("/usr/local/bin/docker", ["docker", "info"], {})

    def test_asyncio_exec_is_denied(self) -> None:
        with self.assertRaises(GuardViolation):
            asyncio.create_subprocess_exec("docker", "info")

    def test_asyncio_shell_is_denied(self) -> None:
        with self.assertRaises(GuardViolation):
            asyncio.create_subprocess_shell("docker info")


class BypassTest(unittest.TestCase):
    """Every way a caller can name a program that is not `argv[0]`."""

    def test_executable_kwarg_cannot_smuggle_a_program_past_run(self) -> None:
        with self.assertRaises(GuardViolation):
            subprocess.run(["git", "--version"], executable="/usr/local/bin/docker")

    def test_executable_kwarg_cannot_smuggle_a_program_past_popen(self) -> None:
        with self.assertRaises(GuardViolation):
            subprocess.Popen(["git", "--version"], executable="/usr/local/bin/docker")

    def test_executable_kwarg_cannot_smuggle_a_program_past_asyncio_exec(self) -> None:
        """`create_subprocess_exec(program, *args)` has no positional `executable` slot,
        but it forwards the keyword to `Popen`, so the keyword still names the program."""
        with self.assertRaises(GuardViolation):
            asyncio.create_subprocess_exec(
                "git", "--version", executable="/usr/local/bin/docker"
            )

    def test_executable_given_positionally_is_still_checked(self) -> None:
        """`Popen(args, bufsize, executable, ...)` — position 2, not only a keyword."""
        with self.assertRaises(GuardViolation):
            subprocess.Popen(["git", "--version"], -1, "/usr/local/bin/docker")

    def test_argv_by_keyword_is_found_not_read_as_absent(self) -> None:
        """Reading past the end of `args` and concluding `None` would deny permitted
        programs with a message that reads as a guard bug. Issue 05 spawns git."""
        result = subprocess.run(args=["git", "--version"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("git version", result.stdout)

    def test_a_pathlike_program_is_read_rather_than_refused(self) -> None:
        """`Path(...)` is not a `str`, and everything that is not a `str` is denied — so
        without the `os.fspath` coercion a permitted `git` given as a path object is
        refused, again with a message that reads as a guard bug."""
        result = subprocess.run(
            [Path(git_path()), "--version"], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("git version", result.stdout)

    def test_a_program_that_is_not_a_str_is_denied(self) -> None:
        """Both halves of what `_permitted` refuses without inspecting: a bytes argv, and
        something that is not a path at all.

        The second is what pins the branch. Bytes are denied either way — a bytes basename
        is not in a `str` allowlist and a bytes realpath never equals a `str` one — so
        deleting the check leaves the bytes case unchanged and turns the non-path case into
        a `TypeError` out of `os.path.basename` instead of a `GuardViolation`."""
        with self.assertRaises(GuardViolation):
            subprocess.run([b"git", b"--version"])
        with self.assertRaises(GuardViolation):
            subprocess.run([1])

    def test_a_denied_program_by_keyword_is_still_denied(self) -> None:
        with self.assertRaises(GuardViolation):
            subprocess.run(args=["docker", "info"], check=False)

    def test_an_unreadable_call_shape_fails_closed(self) -> None:
        """The message assertion is what makes this test about the fail-closed branch.
        Without it the branch can be deleted and this still passes: `_program_of` returns
        `None` for the unreadable argv and the allowlist branch refuses that anyway — the
        same way the shell test passed on the program check before it named `shell`."""
        spawn: Callable[..., object] = subprocess.run
        with self.assertRaises(GuardViolation) as caught:
            spawn()
        self.assertIn("cannot read a program out of", str(caught.exception))


class AllowedSpawnTest(unittest.TestCase):
    """A pass-through test per allowlisted program and per wrapped entry point.

    Denial-only coverage cannot tell a working allowlist from a guard that denies
    everything, which is the state in which the suite has stopped testing anything.
    """

    def test_the_allowlist_is_exactly_what_is_tested_here(self) -> None:
        """If someone widens the allowlist, this fails until they add a pass-through."""
        self.assertEqual(ALLOWED_PROGRAMS, frozenset({"git", "bessemer"}))

    def test_git_runs(self) -> None:
        result = subprocess.run(["git", "--version"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("git version", result.stdout)

    def test_the_interpreter_runs(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", "print('drove the CLI')"], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "drove the CLI")

    def test_the_console_script_runs(self) -> None:
        """`bessemer` is on the allowlist; this test is the entry's only consumer so far."""
        result = subprocess.run(
            [str(CONSOLE_SCRIPT), "--version"], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0)
        self.assertRegex(result.stdout.strip(), r"^\d+\.\d+")

    def test_check_output_runs(self) -> None:
        stdout = subprocess.check_output(["git", "--version"], text=True)
        self.assertIn("git version", stdout)

    def test_call_runs(self) -> None:
        self.assertEqual(subprocess.call(["git", "--version"], stdout=subprocess.DEVNULL), 0)

    def test_check_call_runs(self) -> None:
        self.assertEqual(
            subprocess.check_call(["git", "--version"], stdout=subprocess.DEVNULL), 0
        )

    def test_popen_runs_and_is_still_a_class(self) -> None:
        """Guarded by subclassing, so `isinstance` and annotations keep working."""
        with subprocess.Popen(
            ["git", "--version"], stdout=subprocess.PIPE, text=True
        ) as process:
            stdout, _ = process.communicate()
        self.assertIsInstance(process, subprocess.Popen)
        self.assertIn("git version", stdout)

    def test_posix_spawn_runs(self) -> None:
        """The entry whose regression is invisible on macOS: denying it fails only on CI."""
        self.assertIn("git version", self.spawn_git_via(os.posix_spawn, git_path()))

    def test_posix_spawnp_runs(self) -> None:
        """`posix_spawnp` searches PATH, so it takes the bare name."""
        self.assertIn("git version", self.spawn_git_via(os.posix_spawnp, "git"))

    def spawn_git_via(self, spawn: Callable[..., int], program: str) -> str:
        """Run `git --version` through a posix_spawn variant, returning its stdout."""
        with tempfile.TemporaryDirectory() as directory:
            captured = Path(directory) / "stdout.txt"
            pid = spawn(
                program,
                ["git", "--version"],
                os.environ,
                file_actions=[
                    (
                        os.POSIX_SPAWN_OPEN,
                        1,
                        str(captured),
                        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                        0o644,
                    )
                ],
            )
            _, status = os.waitpid(pid, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 0)
            return captured.read_text()

    def test_asyncio_exec_runs_git(self) -> None:
        async def run_git() -> tuple[int | None, bytes]:
            process = await asyncio.create_subprocess_exec(
                "git", "--version", stdout=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            return process.returncode, stdout

        returncode, stdout = asyncio.run(run_git())
        self.assertEqual(returncode, 0)
        self.assertIn(b"git version", stdout)

    def test_asyncio_exec_finds_a_program_given_by_keyword(self) -> None:
        """`create_subprocess_exec(program=...)` leaves the positional args empty, so
        reading position 0 alone finds nothing and fails closed on a permitted program.
        The asyncio spelling of `test_argv_by_keyword_is_found_not_read_as_absent`.

        Bare `git` here, because a keyword `program` leaves no positional slot for its
        arguments: it prints its usage and exits 1.
        """

        async def run_git() -> tuple[int | None, bytes]:
            process = await asyncio.create_subprocess_exec(
                program="git", stdout=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            return process.returncode, stdout

        returncode, stdout = asyncio.run(run_git())
        self.assertEqual(returncode, 1)
        self.assertIn(b"usage: git", stdout)


if __name__ == "__main__":
    unittest.main()
