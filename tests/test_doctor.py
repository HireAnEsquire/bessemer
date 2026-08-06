"""Tests for the check runner, the F1 checks, and the CLI's rendering of them.

**Nothing here spawns `uv` or `docker`.** `tests/guard.py`'s `ALLOWED_PROGRAMS` is `{"git",
"bessemer"}` plus the interpreter, and neither program is on it — for `docker` that is a
genuine hazard rather than a formality, since a suite permitted to run it is one image pull
away from network access on a contributor's laptop, and ADR 0002 requires this suite to pass
with no daemon at all. So the two checks that spawn are driven through `doctor.Context.run`
with `Spawns`, a stand-in that answers from a table and records what it was asked for. That
leaves the real argv unasserted by the stand-in, which is why `ArgvTest` pins both argvs as
hand-written literals read off the recorder — a test that reads what the check would spawn
without spawning it.

`git` *is* allowlisted, and the resolver checks use it against real temporary repositories
the way `tests/test_resolve.py` does: the failure modes are git's actual behaviour, and a
mock would encode this file's assumptions about git rather than git. Fixture git calls take
`tests.gitenv.fixture_env` for the reason that module exists.

No test changes the working directory. `StartTest` is the one that would be tempted, and the
whole point of it is that doctor reports on the directory it was given rather than on the
one the runner happens to be standing in — which is only provable while those two differ.
"""

import ast
import tempfile
import unittest
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Final
from unittest import mock

from bessemer import cli, config, doctor, proc, resolve
from bessemer.doctor import Check, CheckResult, Context
from tests.gitenv import fixture_env

TIMEOUT: Final = 60
"""For fixture git commands. Generous: it is a backstop, not the thing under test."""

TOKEN: Final = "ghp_thisisnotarealtokenbutitlookslikeone"
CREDENTIAL_URL: Final = f"https://x-access-token:{TOKEN}@github.com/HireAnEsquire/bessemer.git"
CREDENTIAL_STDERR: Final = (
    f"fatal: unable to access '{CREDENTIAL_URL}/': Could not resolve host: github.com"
)
"""How git echoes a token: in a remote URL, in its own failure output.

Written as a literal for the same reason `tests/test_resolve.py` writes it as one — git
prints the URL while *contacting* the remote, and nothing in this suite may reach the
network.
"""

Answer = tuple[int, str, str]
"""A returncode, a stdout and a stderr for one stubbed spawn."""

UV_PRESENT: Final[Answer] = (0, "uv 0.9.2 (0aa1e5d 2026-07-11)\n", "")
DOCKER_UP: Final[Answer] = (0, "Server Version: 28.0.1\n", "")

DAEMON_DOWN: Final[Answer] = (
    1,
    "",
    "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
    "Is the docker daemon running?\n",
)
"""What `docker info` really prints with Docker Desktop stopped."""


class Spawns:
    """A stand-in for `bessemer.proc.run` that answers from a table and spawns nothing.

    Keyed on the program rather than on call order, so a test that reorders the check list
    does not silently start asserting about a different command. An unlisted program gets the
    healthy answer, which keeps each test to the one failure it is about.

    Records every call, which is what `ArgvTest` reads: the check builds its own argv and
    hands it here, so the recorder holds the real thing without anything having been run.
    """

    def __init__(self, **answers: Answer | BaseException) -> None:
        self.answers: Mapping[str, Answer | BaseException] = answers
        self.calls: list[tuple[list[str], float]] = []

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        # Accepted and never read: `stdin_text` is `bessemer.landing`'s alone, and taking
        # it is what keeps this double a `proc.Runner` rather than a near-miss.
        stdin_text: str | None = None,
    ) -> proc.Result:
        command = list(argv)
        self.calls.append((command, timeout))
        defaults: Mapping[str, Answer] = {"uv": UV_PRESENT, "docker": DOCKER_UP}
        answer = self.answers.get(command[0], defaults[command[0]])
        if isinstance(answer, BaseException):
            raise answer
        returncode, stdout, stderr = answer
        return proc.Result(
            argv=tuple(command), returncode=returncode, stdout=stdout, stderr=stderr
        )

    @property
    def programs(self) -> list[str]:
        return [command[0] for command, _ in self.calls]


def rendered(report: Sequence[CheckResult]) -> str:
    """The report as the CLI would print it. Assertions about output go through this.

    Through `cli.render` rather than through a formatting helper of this file's own: what
    reaches a terminal — and from F3 a pull request body — is what the credential assertion
    has to be about, and a second formatter here could redact something the real one does not.
    """
    return "\n".join(line for result in report for line in cli.render(result))


def by_name(report: Sequence[CheckResult]) -> dict[str, CheckResult]:
    return {result.name: result for result in report}


def doctor_module() -> ast.Module:
    """`bessemer/doctor.py` parsed, for the assertions that read the module itself.

    From `doctor.__file__` rather than a hard-coded path, so a moved module fails loudly here
    instead of leaving these assertions passing against a file that is no longer there —
    `tests/test_resolve.py` and `tests/test_config.py` both do this.
    """
    origin = doctor.__file__
    assert origin is not None, "bessemer.doctor must be importable from a source tree"
    return ast.parse(Path(origin).read_text(encoding="utf-8"))


def _name_of(node: ast.expr) -> str:
    """The check name an argument to `ctx.ok(...)` denotes, literal or module constant.

    The checks are written `ctx.ok(CONFIG)`, so the AST holds a `Name`; it is resolved
    against the module rather than being pattern-matched textually, and anything that is
    neither a string literal nor a module-level string constant is an error rather than a
    silently skipped dependency — a queried name this reader cannot see is exactly the case
    the ordering invariant exists to catch.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    assert isinstance(node, ast.Name), f"ctx.ok() called with {ast.dump(node)}"
    value = getattr(doctor, node.id, None)
    assert isinstance(value, str), f"ctx.ok({node.id}) does not name a string constant"
    return value


def queried_names(function: ast.FunctionDef) -> list[str]:
    """Every check name this function asks `ctx.ok` about."""
    return [
        _name_of(call.args[0])
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        if isinstance(call.func, ast.Attribute) and call.func.attr == "ok"
        if isinstance(call.func.value, ast.Name) and call.func.value.id == "ctx"
        if call.args
    ]


def calls_on(module: ast.Module, name: str) -> list[str]:
    """Every call of the form `name.something(...)` in `module`, unparsed.

    One matcher, used by the spawn-seam assertion and by the redaction-site count, so the
    two cannot come to disagree about what counts as a call into a module.
    """
    return [
        ast.unparse(node)
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        if isinstance(node.func, ast.Attribute)
        if isinstance(node.func.value, ast.Name) and node.func.value.id == name
    ]


def functions() -> dict[str, ast.FunctionDef]:
    return {
        node.name: node
        for node in ast.walk(doctor_module())
        if isinstance(node, ast.FunctionDef)
    }


def ordering_violations(checks: Sequence[Check]) -> list[str]:
    """Every name a check queries that no *earlier* check in `checks` emits.

    The list-level invariant ADR 0002 describes: the registry's real safety property,
    expressed as data rather than as machinery. `OrderingTest` runs it in both directions,
    because a checker that always returns an empty list would pass the positive half.
    """
    source = functions()
    emitted: set[str] = set()
    violations: list[str] = []
    for check in checks:
        body = source[check.probe.__name__]
        violations += [
            f"{check.name} queries {queried}, which no earlier check emits"
            for queried in queried_names(body)
            if queried not in emitted
        ]
        emitted.add(check.name)
    return violations


class TempDirTest(unittest.TestCase):
    """Base for tests needing a directory outside any repository the runner is standing in."""

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        # Resolved, because git resolves and `find_adapter_dir` resolves: on macOS a
        # temporary directory is handed out as `/var/...`, a symlink to `/private/var/...`.
        self.tmp = Path(holder.name).resolve()
        self.env = fixture_env(HOME=str(self.tmp))

    def git(self, *arguments: str, cwd: Path) -> None:
        """Run a fixture git command, failing the test if git itself failed."""
        identity = (
            "-c",
            "user.email=tests@bessemer.invalid",
            "-c",
            "user.name=bessemer tests",
            "-c",
            "commit.gpgsign=false",
        )
        result = proc.run(
            ["git", *identity, *arguments], cwd=cwd, timeout=TIMEOUT, env=self.env
        )
        self.assertTrue(
            result.ok, f"fixture `git {' '.join(arguments)}` failed: {result.stderr}"
        )

    def make_repo(self, path: Path, *, branch: str = "main") -> Path:
        """A git repository at `path` with a commit, an `origin`, and `origin/HEAD` set."""
        path.mkdir(parents=True, exist_ok=True)
        self.git("init", "--quiet", "--initial-branch", branch, cwd=path)
        self.git("commit", "--quiet", "--allow-empty", "--message", "fixture", cwd=path)
        self.git("remote", "add", "origin", str(self.tmp / "origin.git"), cwd=path)
        self.git(
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            f"refs/remotes/origin/{branch}",
            cwd=path,
        )
        return path

    def make_adapter(self, root: Path, body: str = "") -> Path:
        """A `.bessemer/` with a committed config file, the way `init` will write one."""
        adapter = root / config.ADAPTER_DIR
        adapter.mkdir(parents=True, exist_ok=True)
        (adapter / config.COMMITTED_FILE).write_text(body, encoding="utf-8")
        return adapter

    def context(
        self,
        *,
        start: Path | None = None,
        env: Mapping[str, str] | None = None,
        run: doctor.Runner | None = None,
    ) -> Context:
        """A context rooted in the temporary directory, spawning nothing by default.

        Written out rather than forwarded through `**overrides`, so each argument keeps the
        type `Context` declares for it: a helper taking `**object` would type-check a test
        that handed `start` a string.
        """
        return Context(
            start=self.tmp if start is None else start,
            env={} if env is None else env,
            run=Spawns() if run is None else run,
        )


class StatusTest(unittest.TestCase):
    """The status vocabulary, restated by hand. Deleting one must cost a second edit."""

    def test_the_statuses_are_ok_warn_and_fail(self) -> None:
        self.assertEqual(doctor.STATUSES, ("ok", "WARN", "FAIL"))

    def test_only_ok_and_warn_let_the_run_pass(self) -> None:
        passing = {
            status: CheckResult(name="x", status=status, message="m").passed
            for status in ("ok", "WARN", "FAIL")
        }
        self.assertEqual(passing, {"ok": True, "WARN": True, "FAIL": False})


class CheckListTest(TempDirTest):
    """The check list, restated by hand — the assertion an iteration cannot make.

    Without this, deleting a check makes doctor print five lines and exit 0 with the whole
    suite green: the report shrinks and nothing notices, which is the one failure a tool
    whose job is reporting must not have.
    """

    def test_the_checks_are_these_six_in_this_order(self) -> None:
        self.assertEqual(
            [check.name for check in doctor.CHECKS],
            ["uv", "config", "git-env", "root", "base", "docker"],
        )

    def test_every_check_reports_under_the_name_declared_beside_it(self) -> None:
        """The crash line takes its name from the list; every other line takes it from the
        check itself. Nothing else would notice those two disagreeing."""
        report = doctor.run_checks(self.context())
        self.assertEqual(
            [result.name for result in report], [check.name for check in doctor.CHECKS]
        )


class OrderingTest(unittest.TestCase):
    def test_no_check_queries_a_name_that_runs_after_it(self) -> None:
        self.assertEqual(ordering_violations(doctor.CHECKS), [])

    def test_the_reader_actually_finds_the_queries(self) -> None:
        """A scanner that found nothing would make the assertion above pass while checking
        nothing at all. Two checks depend on config today; both must be visible here."""
        source = functions()
        queried = {
            check.name: queried_names(source[check.probe.__name__]) for check in doctor.CHECKS
        }
        self.assertEqual(queried["root"], ["config"])
        self.assertEqual(queried["base"], ["config"])

    def test_moving_a_check_before_the_one_it_queries_is_a_violation(self) -> None:
        """The negative direction. Without it the invariant is a function that could return
        an empty list for any input."""
        checks = {check.name: check for check in doctor.CHECKS}
        reordered = (checks["root"], checks["config"])
        self.assertEqual(
            ordering_violations(reordered),
            ["root queries config, which no earlier check emits"],
        )


class ContextTest(unittest.TestCase):
    def test_asking_about_a_check_that_never_ran_raises(self) -> None:
        """A typo'd name returning falsy would produce a check that skips forever while
        looking principled — the silent drift ADR 0002 refuses."""
        context = Context()
        with self.assertRaises(ValueError) as raised:
            context.ok("dokcer")
        self.assertIn("dokcer", str(raised.exception))

    def test_asking_about_a_check_that_ran_and_failed_returns_false(self) -> None:
        context = Context()
        context.record(CheckResult(name="docker", status="FAIL", message="down"))
        self.assertFalse(context.ok("docker"))

    def test_a_warning_check_counts_as_ok_for_dependants(self) -> None:
        context = Context()
        context.record(CheckResult(name="git-env", status="WARN", message="exported"))
        self.assertTrue(context.ok("git-env"))

    def test_requiring_the_config_before_it_loaded_raises(self) -> None:
        with self.assertRaises(ValueError):
            Context().require_config()


class CrashTest(unittest.TestCase):
    """The contract: a crashing check renders as FAIL and the report still completes."""

    def crashing(self, error: BaseException) -> Check:
        def probe(ctx: Context) -> CheckResult:
            raise error

        return Check("crash", probe)

    def surviving(self) -> Check:
        return Check("after", lambda ctx: CheckResult(name="after", status="ok", message="ran"))

    def test_a_crashing_check_is_a_fail_and_the_rest_still_run(self) -> None:
        context = Context()
        report = doctor.run_checks(
            context, (self.crashing(RuntimeError("the daemon ate it")), self.surviving())
        )
        crash, after = report
        self.assertEqual(crash.status, "FAIL")
        self.assertIn("RuntimeError", crash.message)
        self.assertIn("the daemon ate it", crash.message)
        self.assertEqual((after.name, after.status), ("after", "ok"))
        self.assertEqual(doctor.exit_code(report), 1)

    def test_a_crashing_check_is_recorded_so_dependants_can_skip(self) -> None:
        context = Context()
        doctor.run_checks(context, (self.crashing(RuntimeError("boom")),))
        self.assertFalse(context.ok("crash"))

    def test_a_keyboard_interrupt_is_not_a_crashing_check(self) -> None:
        """`Exception`, not `BaseException`: Ctrl-C must still end the process, and
        `tests.guard.GuardViolation` must not be absorbable into a green FAIL line."""
        with self.assertRaises(KeyboardInterrupt):
            doctor.run_checks(Context(), (self.crashing(KeyboardInterrupt()),))


class RedactionTest(TempDirTest):
    """One assertion per site that prints another program's text. Four sites, four tests.

    **The first review of this issue found three of the four correct but unpinned**, because
    the criterion named the crashing-check path alone: removing the redaction from the
    docker, uv or `OSError` message left the whole suite green. A redaction nothing asserts
    is a redaction the next mechanical edit deletes — and this module's output goes to a
    terminal today and into a pull request body from F3.

    Every case uses the same credential-bearing text, which is git's shape rather than
    docker's or uv's. Deliberate: the redactor is a URL-userinfo redactor (see
    `bessemer.redact`), the question here is whether each site *runs* it, and using one
    input across four sites is what makes the four comparable.
    """

    def crashing(self, error: BaseException) -> Check:
        def probe(ctx: Context) -> CheckResult:
            raise error

        return Check("crash", probe)

    def assertRedacted(self, output: str) -> None:
        """No credential in the text, and evidence the redactor is what removed it.

        Both halves matter: asserting only the absence of the token would pass on a site that
        dropped the whole message, which is a different behaviour that nothing else here
        would notice.
        """
        self.assertNotIn(TOKEN, output)
        self.assertNotIn("x-access-token", output)
        self.assertIn("<redacted>@github.com", output)

    def line_for(self, name: str, **answers: Answer | BaseException) -> str:
        report = doctor.run_checks(self.context(run=Spawns(**answers)))
        return rendered([by_name(report)[name]])

    def test_a_crashing_check_does_not_print_a_credential(self) -> None:
        """`bessemer.proc.ProcessError` carries the child's stderr in its message, and git's
        stderr can carry a token out of a remote URL."""
        failure = proc.ProcessError(
            proc.Result(
                argv=("git", "fetch", "origin"),
                returncode=128,
                stdout="",
                stderr=CREDENTIAL_STDERR,
            )
        )
        report = doctor.run_checks(Context(), (self.crashing(failure),))
        self.assertRedacted(rendered(report))

    def test_a_nonzero_docker_info_does_not_print_a_credential(self) -> None:
        self.assertRedacted(self.line_for("docker", docker=(1, "", CREDENTIAL_STDERR)))

    def test_a_nonzero_uv_version_does_not_print_a_credential(self) -> None:
        self.assertRedacted(self.line_for("uv", uv=(2, "", CREDENTIAL_STDERR)))

    def test_an_unspawnable_docker_does_not_print_a_credential(self) -> None:
        """The `OSError` branch of `_probe`. One site, reached by both spawning checks, so
        both are asserted — a future check that stops sharing `_probe` should fail here
        rather than quietly become a fifth site."""
        self.assertRedacted(self.line_for("docker", docker=OSError(2, CREDENTIAL_STDERR)))

    def test_an_unspawnable_uv_does_not_print_a_credential(self) -> None:
        self.assertRedacted(self.line_for("uv", uv=OSError(2, CREDENTIAL_STDERR)))

    def test_there_are_exactly_four_places_doctor_redacts(self) -> None:
        """The guard on the list above. Four, hand-written: a fifth call into
        `bessemer.redact` means a fifth message carrying another program's text, and it must
        arrive with an assertion of its own rather than inheriting these four's green."""
        self.assertEqual(
            len(calls_on(doctor_module(), "redact")),
            4,
            "doctor gained or lost a redacting site; add or remove a test in RedactionTest",
        )


class SpawnSeamTest(unittest.TestCase):
    def test_no_check_calls_into_proc_directly(self) -> None:
        """Every spawn goes through `ctx.run`. A direct `proc.run` would be untestable
        without widening `tests/guard.py`'s allowlist to `docker`, which this issue must not
        do — so the seam is asserted rather than left to review.

        **Every `proc.` call, not `proc.run` alone.** A matcher spelled for one name is
        narrower than the claim it is checking: `proc.run_checked` spawns too, and it is the
        next thing someone reaches for. Widened rather than the docstring narrowed, because
        the property doctor actually wants is that nothing here spawns except through the
        seam, and `proc` is the only module that can spawn at all.

        Named limit: this reads attribute calls on the name `proc`, so
        `from bessemer.proc import run` would pass it. That route is covered by
        `tests/test_argv_boundary.py` and `tests/guard.py`, which are the real backstop —
        this assertion is about doctor's testability, not about the argv boundary.
        """
        self.assertEqual(calls_on(doctor_module(), "proc"), [])

    def test_the_matcher_sees_a_call_it_is_meant_to_refuse(self) -> None:
        """The negative direction: a matcher that matched nothing would pass the assertion
        above against any module at all."""
        spawn = "proc.run_checked(['docker', 'info'], timeout=1.0)"
        self.assertEqual(calls_on(ast.parse(spawn), "proc"), [spawn])


class ArgvTest(TempDirTest):
    """What the spawning checks would run, pinned without running it.

    Hand-written literals rather than a comparison against `doctor.UV_ARGV` and
    `doctor.DOCKER_ARGV`: an assertion that reads the constant it is checking cannot notice
    that constant changing.
    """

    def test_the_checks_spawn_exactly_these_two_commands(self) -> None:
        spawns = Spawns()
        doctor.run_checks(self.context(run=spawns))
        self.assertEqual(
            [command for command, _ in spawns.calls],
            [["uv", "--version"], ["docker", "info"]],
        )

    def test_every_spawn_carries_a_timeout(self) -> None:
        """A wedged daemon must fail doctor rather than hang it, and `Runner` is what makes
        omitting the timeout a type error — this is the runtime half of the same rule."""
        spawns = Spawns()
        doctor.run_checks(self.context(run=spawns))
        self.assertTrue(all(timeout > 0 for _, timeout in spawns.calls))


class UvCheckTest(TempDirTest):
    def test_present_reports_the_version_and_the_interpreter(self) -> None:
        report = by_name(doctor.run_checks(self.context()))
        self.assertEqual(report["uv"].status, "ok")
        self.assertIn("uv 0.9.2", report["uv"].message)
        self.assertIn("python", report["uv"].message)

    def test_not_installed_is_a_fail_naming_the_install(self) -> None:
        missing = OSError(2, "No such file or directory", "uv")
        report = by_name(doctor.run_checks(self.context(run=Spawns(uv=missing))))
        self.assertEqual(report["uv"].status, "FAIL")
        self.assertIn("could not be run", report["uv"].message)
        self.assertIn("install uv", report["uv"].hint)

    def test_a_nonzero_exit_carries_uvs_own_first_line(self) -> None:
        broken = (2, "", "error: unrecognized subcommand\nsecond line of advice\n")
        report = by_name(doctor.run_checks(self.context(run=Spawns(uv=broken))))
        self.assertEqual(report["uv"].status, "FAIL")
        self.assertIn("error: unrecognized subcommand", report["uv"].message)
        self.assertNotIn("second line", report["uv"].message)


class UvFloorTest(TempDirTest):
    """The uv version floor, and the comparison that decides it.

    Issue 06 declined this check; issue 08's tracer measured the reasoning false and reversed
    it. The floor is restated by hand below, because an assertion reading `doctor.UV_FLOOR`
    would move with it — lower the constant to `(0, 8, 0)` and a suite that read it would stay
    green while doctor blessed a uv that cannot install bessemer at all.
    """

    FLOOR: Final = (0, 9, 0)

    def uv_line(self, version: str) -> CheckResult:
        answer = (0, f"uv {version} (0aa1e5d 2026-07-11)\n", "")
        return by_name(doctor.run_checks(self.context(run=Spawns(uv=answer))))["uv"]

    def test_the_floor_is_uv_0_9_0(self) -> None:
        self.assertEqual(doctor.UV_FLOOR, self.FLOOR)

    def test_below_the_floor_warns_naming_both_versions(self) -> None:
        """The version found and the version wanted. Either alone leaves the reader to guess
        which direction the mismatch runs."""
        result = self.uv_line("0.8.0")
        self.assertEqual(result.status, "WARN")
        self.assertIn("0.8.0", result.message)
        self.assertIn("0.9.0", result.message)
        self.assertIn("uv self update", result.hint)

    def test_below_the_floor_is_a_warn_not_a_fail(self) -> None:
        """**The line must not claim bessemer cannot be installed by this uv.** That claim is
        refuted by its own existence: `requires-python` is `>=3.14`, so a doctor that is
        running was installed under a satisfying interpreter, and an old uv reaches here
        whenever a stable 3.14 is already on disk. Measured on the host this check was written
        on — uv 0.8.0 built and ran the bessemer that printed the line. It shipped as a FAIL
        once; this is the assertion that stops it shipping as one again."""
        self.assertTrue(self.uv_line("0.8.17").passed)

    def test_below_the_floor_says_which_machine_would_actually_break(self) -> None:
        """The true claim names its condition — a machine without a stable 3.14 already on
        it. A message that omits the condition is the false one again in softer clothing."""
        message = self.uv_line("0.8.0").message
        self.assertIn("does not already have one", message)
        self.assertNotIn("cannot be installed", message)

    def test_exactly_the_floor_passes(self) -> None:
        self.assertEqual(self.uv_line("0.9.0").status, "ok")

    def test_0_10_0_is_above_0_9_0(self) -> None:
        """The whole reason the comparison is on integer tuples: `"0.10.0" < "0.9.0"` is true
        as strings, so a textual floor would reject every uv from 0.10 onward — every uv an
        adopter is actually likely to have."""
        self.assertEqual(self.uv_line("0.10.0").status, "ok")
        self.assertEqual(self.uv_line("0.12.0").status, "ok")

    def parts_of(self, stdout: str) -> tuple[int, ...] | None:
        found = doctor._uv_version(stdout)
        return None if found is None else found.parts

    def test_the_versions_uv_really_prints_are_read_correctly(self) -> None:
        """Real `uv --version` output, hand-copied from three installs. The build suffix
        varies by installer, and it is the field after the version that has to be ignored."""
        self.assertEqual(self.parts_of("uv 0.8.0 (Homebrew 2025-07-17)\n"), (0, 8, 0))
        self.assertEqual(self.parts_of("uv 0.9.2 (0aa1e5d 2026-07-11)\n"), (0, 9, 2))
        self.assertEqual(
            self.parts_of("uv 0.12.0 (b88d7c5c4 2026-07-28 aarch64-apple-darwin)\n"),
            (0, 12, 0),
        )

    def test_a_prerelease_suffix_reads_as_its_release(self) -> None:
        self.assertEqual(self.parts_of("uv 0.9.0rc1 (abc 2026-01-01)\n"), (0, 9, 0))

    def test_a_short_version_is_padded_rather_than_sorted_below_the_floor(self) -> None:
        """`(0, 9)` sorts below `(0, 9, 0)`, which would fail a uv that is exactly at it."""
        self.assertEqual(self.parts_of("uv 0.9 (abc 2026-01-01)\n"), (0, 9, 0))
        self.assertEqual(self.uv_line("0.9").status, "ok")

    def test_the_message_quotes_uvs_own_spelling_not_the_padded_one(self) -> None:
        """A uv that says `0.8` must not be told "uv 0.8.0 is older than…" — that quotes it a
        version string it has never printed. The padding exists for the comparison only."""
        result = self.uv_line("0.8")
        self.assertIn("uv 0.8 ", result.message)
        self.assertNotIn("0.8.0", result.message)

    def test_an_unreadable_version_warns_rather_than_failing(self) -> None:
        """A uv that changed its output format is not a uv that is too old. WARN says the
        floor went unchecked; a FAIL here would block a working machine on bessemer's own
        parsing gap."""
        for output in ("uv\n", "\n", "uv version-unknown (dev)\n"):
            with self.subTest(output=output):
                self.assertIsNone(self.parts_of(output))
        result = by_name(doctor.run_checks(self.context(run=Spawns(uv=(0, "uv\n", "")))))["uv"]
        self.assertEqual(result.status, "WARN")
        self.assertIn("unchecked", result.message)
        self.assertTrue(result.passed)

    def test_the_unreadable_warning_does_not_echo_what_uv_printed(self) -> None:
        """The one message in this check built from bessemer's own text only. `_probe`'s
        timeout branch makes the same choice for the same reason: echoing here would be a
        fifth redacting site, and `RedactionTest` counts four.
        """
        result = by_name(
            doctor.run_checks(self.context(run=Spawns(uv=(0, "uv totally-bogus\n", ""))))
        )["uv"]
        self.assertNotIn("totally-bogus", result.message)


class DockerCheckTest(TempDirTest):
    def test_daemon_responding_is_ok(self) -> None:
        report = by_name(doctor.run_checks(self.context()))
        self.assertEqual(report["docker"].status, "ok")

    def test_daemon_stopped_is_a_fail_that_says_to_start_it(self) -> None:
        report = by_name(doctor.run_checks(self.context(run=Spawns(docker=DAEMON_DOWN))))
        self.assertEqual(report["docker"].status, "FAIL")
        self.assertIn("daemon is not responding", report["docker"].message)
        self.assertIn("Cannot connect to the Docker daemon", report["docker"].message)
        self.assertIn("start Docker Desktop", report["docker"].hint)

    def test_cli_absent_is_a_fail_that_says_to_install_it(self) -> None:
        missing = OSError(2, "No such file or directory", "docker")
        report = by_name(doctor.run_checks(self.context(run=Spawns(docker=missing))))
        self.assertEqual(report["docker"].status, "FAIL")
        self.assertIn("install Docker Desktop", report["docker"].hint)

    def test_a_wedged_daemon_is_a_fail_rather_than_a_hang(self) -> None:
        wedged = proc.TimeoutExpired(
            cmd=list(doctor.DOCKER_ARGV), timeout=doctor.TIMEOUT_SECONDS
        )
        report = by_name(doctor.run_checks(self.context(run=Spawns(docker=wedged))))
        self.assertEqual(report["docker"].status, "FAIL")
        self.assertIn("did not finish within", report["docker"].message)


class GitEnvCheckTest(TempDirTest):
    """Check 3 reads the context's `env`, never `os.environ` — which is what makes these
    tests assertions about doctor rather than about the machine running them."""

    def test_a_clean_environment_is_ok(self) -> None:
        report = by_name(doctor.run_checks(self.context(env={"PATH": "/usr/bin"})))
        self.assertEqual(report["git-env"].status, "ok")

    def test_an_exported_redirecting_variable_is_a_warning_not_a_failure(self) -> None:
        """A poisoned shell is the user's environment, not a broken adapter: nothing
        bessemer does is wrong because of it, and the run still exits 0."""
        env = {"GIT_DIR": "/somewhere/else/.git"}
        report = doctor.run_checks(self.context(env=env, start=self.tmp))
        line = by_name(report)["git-env"]
        self.assertEqual(line.status, "WARN")
        self.assertIn("GIT_DIR", line.message)

    def test_the_warning_names_the_variables_but_never_their_values(self) -> None:
        """ADR 0001's restraint: presence, never the value. The fix is spelled by the name."""
        env = {"GIT_WORK_TREE": "/home/someone/secret-client-repo"}
        line = by_name(doctor.run_checks(self.context(env=env)))["git-env"]
        self.assertIn("GIT_WORK_TREE", line.message)
        self.assertNotIn("secret-client-repo", line.message + line.hint)

    def test_the_names_come_from_the_resolvers_own_list(self) -> None:
        """Read from `resolve.REDIRECTING_VARIABLES` rather than restated in doctor: that
        list is issue 05's and is pinned by a literal there, and a second copy would be two
        lists to keep in step."""
        # Patched on `bessemer.resolve` itself rather than through `doctor.resolve`: the same
        # object either way — doctor looks the attribute up on the module at call time — and
        # patching it through the importer is what mypy refuses, the note
        # `tests/test_resolve.py` carries about `proc.run`.
        with mock.patch.object(resolve, "REDIRECTING_VARIABLES", frozenset({"GIT_NOPE"})):
            line = by_name(doctor.run_checks(self.context(env={"GIT_NOPE": "x"})))["git-env"]
        self.assertEqual(line.status, "WARN")
        self.assertIn("GIT_NOPE", line.message)


class DegradedTest(TempDirTest):
    """Doctor's whole point is to work when the things it checks are broken."""

    def test_no_adapter_and_no_repository_still_produces_a_full_report(self) -> None:
        report = doctor.run_checks(self.context())
        self.assertEqual(len(report), len(doctor.CHECKS))
        self.assertEqual(
            [result.status for result in report], ["ok", "FAIL", "ok", "FAIL", "FAIL", "ok"]
        )
        self.assertEqual(doctor.exit_code(report), 1)

    def test_nothing_in_a_degraded_report_is_a_crash(self) -> None:
        """A traceback rendered as a check line is still a bug — the reasons here are all
        diagnosed ones, from `config.load` and the resolvers."""
        report = doctor.run_checks(self.context(run=Spawns(docker=DAEMON_DOWN)))
        self.assertNotIn("crashed", rendered(report))

    def test_a_missing_adapter_skips_the_checks_that_need_one(self) -> None:
        report = by_name(doctor.run_checks(self.context()))
        for name in ("root", "base"):
            self.assertEqual(report[name].status, "FAIL")
            self.assertTrue(report[name].message.startswith("skipped — "))
            self.assertIn("config", report[name].message)

    def test_a_skip_fails_the_run_even_with_nothing_else_wrong(self) -> None:
        """The port source's scriptable-gate semantics: a report with a skip in it has not
        told the user their machine is able to dispatch."""
        report = [
            CheckResult(name="uv", status="ok", message="fine"),
            CheckResult(name="root", status="FAIL", message="skipped — no adapter loaded"),
        ]
        self.assertEqual(doctor.exit_code(report), 1)

    def test_an_adapter_outside_a_repository_reports_both_resolver_failures(self) -> None:
        """Config found, git absent: two facts rather than one useless error, which is what
        ADR 0002's pure config load buys."""
        self.make_adapter(self.tmp)
        report = by_name(doctor.run_checks(self.context()))
        self.assertEqual(report["config"].status, "ok")
        self.assertEqual(report["root"].status, "FAIL")
        self.assertIn("not inside a git work tree", report["root"].message)
        self.assertEqual(report["base"].status, "FAIL")

    def test_a_failing_check_does_not_stop_the_ones_that_do_not_depend_on_it(self) -> None:
        """The adapter in a subdirectory: root agreement fails, and base — which depends on
        config, not on root — still resolves."""
        repo = self.make_repo(self.tmp / "repo")
        below = repo / "sub"
        below.mkdir()
        self.make_adapter(below)
        report = by_name(doctor.run_checks(self.context(start=below)))
        self.assertEqual(report["root"].status, "FAIL")
        self.assertIn("below the git work tree", report["root"].message)
        self.assertEqual(report["base"].status, "ok")


class StartTest(TempDirTest):
    """One `start` reaches `config.load`, `resolve_base` and `resolve_root_agreement`.

    Let any of them default independently and doctor reports on two directories while
    printing one report — and root agreement, which exists to catch exactly that
    disagreement, becomes the check that cannot see it.

    This suite runs from inside bessemer's own repository, which has a `.bessemer/` of its
    own, so a check that quietly fell back to the process's working directory would report a
    healthy adapter and a healthy root here. That is what makes the assertion discriminating
    rather than decorative.
    """

    def test_the_whole_report_is_about_the_directory_it_was_given(self) -> None:
        repo = self.make_repo(self.tmp / "repo")
        adapter = self.make_adapter(repo)
        report = by_name(doctor.run_checks(self.context(start=repo)))

        self.assertNotEqual(repo, Path.cwd())
        self.assertEqual(report["config"].message, f"adapter at {adapter}")
        self.assertEqual(report["root"].status, "ok")
        self.assertIn(str(repo), report["root"].message)
        self.assertEqual(report["base"].status, "ok")
        self.assertEqual(report["base"].message, "main (from origin/HEAD)")
        self.assertNotIn(str(Path.cwd()), rendered(list(report.values())))

    def test_a_configured_base_is_reported_with_the_layer_that_set_it(self) -> None:
        repo = self.make_repo(self.tmp / "repo")
        self.make_adapter(repo, body='base = "release"\n')
        report = by_name(doctor.run_checks(self.context(start=repo)))
        self.assertEqual(report["base"].message, "release (from committed)")

    def test_a_repository_without_origin_head_reports_the_resolvers_hint(self) -> None:
        """The base line comes from issue 05's resolver rather than being reimplemented —
        reason and hint arrive as it wrote them."""
        repo = self.tmp / "bare-ish"
        repo.mkdir()
        self.git("init", "--quiet", "--initial-branch", "main", cwd=repo)
        self.make_adapter(repo)
        report = by_name(doctor.run_checks(self.context(start=repo)))
        self.assertEqual(report["base"].status, "FAIL")
        self.assertIn("no remote named origin", report["base"].message)
        self.assertIn("git remote add origin", report["base"].hint)


class RenderTest(unittest.TestCase):
    """The port source's `printf '%-4s  %-10s %s\\n'`, plus a hint line bessemer adds."""

    def test_a_passing_check_is_one_line_in_columns(self) -> None:
        line = list(cli.render(CheckResult(name="docker", status="ok", message="all good")))
        self.assertEqual(line, ["ok    docker     all good"])

    def test_a_failure_puts_its_hint_on_an_indented_second_line(self) -> None:
        lines = list(
            cli.render(
                CheckResult(
                    name="base", status="FAIL", message="origin/HEAD is unset", hint="run it"
                )
            )
        )
        self.assertEqual(
            lines,
            [
                "FAIL  base       origin/HEAD is unset",
                "                 hint: run it",
            ],
        )

    def test_the_status_column_is_where_a_reader_scans(self) -> None:
        """Every line starts with the status, padded to the same width, so a FAIL is visible
        while scrolling past a green report."""
        statuses = {
            status: next(iter(cli.render(CheckResult(name="n", status=status, message="m"))))[
                :6
            ]
            for status in doctor.STATUSES
        }
        self.assertEqual(statuses, {"ok": "ok    ", "WARN": "WARN  ", "FAIL": "FAIL  "})


def iter_report() -> Iterator[CheckResult]:
    """A minimal healthy report, for tests about exit status alone."""
    yield CheckResult(name="uv", status="ok", message="present")
    yield CheckResult(
        name="git-env", status="WARN", message="GIT_DIR exported", hint="unset it"
    )


class ExitCodeTest(unittest.TestCase):
    def test_ok_and_warn_exit_zero(self) -> None:
        self.assertEqual(doctor.exit_code(list(iter_report())), 0)

    def test_any_fail_exits_one(self) -> None:
        report = [*iter_report(), CheckResult(name="docker", status="FAIL", message="down")]
        self.assertEqual(doctor.exit_code(report), 1)

    def test_an_empty_report_is_not_a_pass_by_accident(self) -> None:
        """`all()` of nothing is true, so this exits 0 — recorded rather than asserted as
        desirable. It is unreachable through `run_checks(ctx)`, which always runs `CHECKS`,
        and `CheckListTest` is what keeps that tuple from shrinking to nothing."""
        self.assertEqual(doctor.exit_code([]), 0)


if __name__ == "__main__":
    unittest.main()
