"""Tests for bessemer's own adapter — the `.bessemer/` directory in this repository.

Everything here is asserted against the real committed adapter rather than a fixture. That is
the point of the issue: a fixture proves the loader works, and this repo is bessemer's first
adopter, so what has to be true is that *this* adapter loads. `tests/test_config.py` owns the
loader's behaviour and builds its own temporary trees; nothing here duplicates it.

The adapter is located from `__file__`, not from the working directory, so these tests do not
care where the runner was started. `REPO_ROOT` is spelled out rather than discovered with
`find_adapter_dir`, because the walk-up is one of the things under test and a test that located
its own fixture with the function it is checking would agree with itself.

Two things the suite deliberately does not cover, named rather than left to be assumed:
**the image is never built here** and **the setup hook is never run here**. The unit suite is
docker-free by construction (issue 01a) and the guard's allowlist admits neither `docker` nor a
shell, so both are dev-machine checks; the commands are named in the tests that stand in for
them.
"""

import stat
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from typing import Final

from bessemer import config, container
from bessemer.outcome import Resolved
from tests.gitenv import fixture_env

REPO_ROOT: Final = Path(__file__).resolve().parent.parent
"""The repository this adapter belongs to: the directory holding `tests/`."""

ADAPTER_DIR: Final = REPO_ROOT / ".bessemer"

ADAPTER_KEYS: Final = {"source", "base", "image"}
"""The keys `.bessemer/config.toml` is expected to set, restated by hand.

Not derived from the file, and not derived from `config.KNOWN_KEYS` either. Against the file it
would agree with whatever the file happens to say; against `KNOWN_KEYS` it would accept any key
the loader grows later, and this adapter is meant to carry only the ones it actually needs — a
`specs_dir` line here would restate the default, and a fourth key would be a claim this repo is
configured by something more than it is.

`image` arrived at F3 issue 06, when this repo became a real dispatch target: the key has no
default (an absent one is a doctor FAIL and a dispatch hard error), so an adapter that
dispatches must name the tag its Dockerfile is built under.
"""

LOADER_KEYS: Final = {
    "source",
    "base",
    "specs_dir",
    "image",
    "container_env_keys",
    "container_cap_add",
    "container_volumes",
    "max_review_rounds",
    "pass_timeout",
    "pids_limit",
    "memory",
}
"""What the loader reads, restated by hand. Its schema, this file's obligation.

F1 issue 04 pins this set in `tests/test_config.py` and F3 issue 01 grew it from three keys to
eleven; it is written out a second time here because the criterion this file has to meet is that
the adapter contains nothing the loader ignores, and an assertion reading `config.KNOWN_KEYS`
would keep passing if a key were added to both the loader and the adapter without anyone
deciding it should exist.
"""

IGNORED_PATHS: Final = (
    ".bessemer/config.local.toml",
    ".bessemer/.env",
    ".bessemer/checkouts/",
    ".bessemer/logs/",
    ".bessemer/locks/",
    ".bessemer/runs.jsonl",
)
"""The adapter's runtime state: per-machine output of a run, none of it committable."""

TRACKED_PATHS: Final = (
    ".bessemer/config.toml",
    ".bessemer/Dockerfile",
    ".bessemer/.dockerignore",
    ".bessemer/setup.sh",
    ".bessemer/prompts/implement-prompt.md",
    ".bessemer/prompts/review-prompt.md",
    ".bessemer/specs/f1-skeleton/README.md",
)
"""The adapter itself, plus a spec. Asserted alongside the list above rather than trusted.

A rule broad enough to ignore `.bessemer/` wholesale would satisfy every assertion about the
runtime paths while quietly removing this project's development record from the repository —
which is the failure the two lists together are here to make impossible.

The prompt overrides (F3 issue 03) are adapter content in exactly the sense the other three
files are: committed, reviewable, and read at dispatch time. They are also the adapter file
most likely to be mistaken for runtime state, sitting one directory away from `logs/` and
`locks/` — which is the case for naming them here rather than trusting the rule that happens
to cover them today.
"""


def git_ignore_check(repo: Path, path: str) -> bool:
    """Whether `path` is ignored inside `repo`, according to git itself.

    Asked of `git check-ignore` rather than answered by parsing `.gitignore`, because the
    question is about gitignore semantics — anchoring, trailing slashes, the order rules
    override each other in — and a parser here would be a second implementation of them that
    could agree with the file while disagreeing with git.

    `fixture_env()` rather than the ambient environment, and the whole `GIT_*` family goes
    rather than only the two config variables this used to replace. Measured: with
    `GIT_WORK_TREE` exported, `check-ignore` inside this temporary repository answers about the
    tree that variable names, and `logs/` — ignored here — comes back **not ignored**, exit 1.
    The reversed answer is the dangerous half: "not ignored" is exactly what
    `test_the_adapter_and_the_specs_are_not_ignored` asserts for the paths that must stay
    tracked, so that test would pass while checking nothing. See `tests/gitenv.py`.
    """
    completed = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", path],
        cwd=repo,
        env=fixture_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )
    # 0 is ignored and 1 is not; anything else is git failing rather than answering, and must
    # not be read as "not ignored".
    assert completed.returncode in (0, 1), completed
    return completed.returncode == 0


class DiscoveryTest(unittest.TestCase):
    """The walk-up, exercised against the real adapter rather than a temporary tree."""

    def test_the_loader_finds_this_adapter_from_the_repository_root(self) -> None:
        loaded = config.load(start=REPO_ROOT, env={})
        assert isinstance(loaded, Resolved), loaded
        self.assertEqual(loaded.value.adapter_dir, ADAPTER_DIR)
        self.assertEqual(loaded.value.root, REPO_ROOT)

    def test_the_loader_finds_this_adapter_from_a_nested_subdirectory(self) -> None:
        """`tests/` is where a developer running the suite already is, and two levels further
        down is what a user running bessemer from inside a feature folder would hit."""
        for start in (
            Path(__file__).resolve().parent,
            ADAPTER_DIR / "specs" / "f1-skeleton" / "issues",
        ):
            with self.subTest(start=start):
                loaded = config.load(start=start, env={})
                assert isinstance(loaded, Resolved), loaded
                self.assertEqual(loaded.value.root, REPO_ROOT)


class CommittedLayerTest(unittest.TestCase):
    def test_the_committed_layer_parses_as_toml(self) -> None:
        """Read directly, so a syntax error is a failure here rather than an `Unresolved` two
        tests down whose message is about the loader."""
        with (ADAPTER_DIR / "config.toml").open("rb") as handle:
            self.assertIsInstance(tomllib.load(handle), dict)

    def test_the_adapter_sets_exactly_source_base_and_image(self) -> None:
        loaded = config.load(start=REPO_ROOT, env={})
        assert isinstance(loaded, Resolved), loaded
        self.assertEqual(set(loaded.value.committed), ADAPTER_KEYS)

    def test_the_image_names_a_tag_and_comes_from_the_committed_layer(self) -> None:
        """The team's answer, not a machine's: the tag every dispatch of this repo starts its
        container from, and the one `docker build -t` in the Dockerfile's own header."""
        loaded = config.load(start=REPO_ROOT, env={})
        assert isinstance(loaded, Resolved), loaded
        self.assertEqual(loaded.value.get("image"), "bessemer-agent")
        self.assertEqual(loaded.value.layer_of("image"), config.COMMITTED)

    def test_the_five_container_keys_are_left_to_their_defaults(self) -> None:
        """Bessemer's own container needs no secret beyond the built-in credential names, no
        capability back after `--cap-drop ALL`, and no volume — its suite is stdlib `unittest`
        with no services. Writing any of them here would restate a default, and for the three
        committed-only keys an empty list still costs a reviewer a read.
        """
        loaded = config.load(start=REPO_ROOT, env={})
        assert isinstance(loaded, Resolved), loaded
        for key in (
            "container_env_keys",
            "container_cap_add",
            "container_volumes",
            "pids_limit",
            "memory",
        ):
            with self.subTest(key=key):
                self.assertEqual(loaded.value.layer_of(key), config.DEFAULT)

    def test_every_key_in_the_adapter_is_one_the_loader_reads(self) -> None:
        """The criterion, stated as the loader itself answers it. A key here that the loader
        ignores would be a claim that bessemer is configured by something it never looks at —
        and it fails silently, because an unrecognised key is reported and not rejected."""
        loaded = config.load(start=REPO_ROOT, env={})
        assert isinstance(loaded, Resolved), loaded
        self.assertEqual(loaded.value.unknown_keys(), ())
        self.assertLessEqual(ADAPTER_KEYS, LOADER_KEYS)

    def test_the_source_pin_names_a_commit_rather_than_a_moving_ref(self) -> None:
        """A branch would make the pin move under the team without anyone editing a file,
        which is the within-team version skew the pin exists to prevent (ADR 0001)."""
        loaded = config.load(start=REPO_ROOT, env={})
        assert isinstance(loaded, Resolved), loaded
        source = loaded.value.get("source")
        assert isinstance(source, str), source
        url, _, ref = source.rpartition("@")
        self.assertTrue(url.startswith("git+"), source)
        self.assertEqual(len(ref), 40, source)
        self.assertTrue(all(character in "0123456789abcdef" for character in ref), source)

    def test_base_is_set_and_comes_from_the_committed_layer(self) -> None:
        """Set explicitly rather than left to issue 05's `origin/HEAD` auto-detect: the
        committed value is the team's answer, and the auto-detect is for adapters without
        one."""
        loaded = config.load(start=REPO_ROOT, env={})
        assert isinstance(loaded, Resolved), loaded
        self.assertEqual(loaded.value.get("base"), "main")
        self.assertEqual(loaded.value.layer_of("base"), config.COMMITTED)

    def test_specs_dir_is_left_to_its_default_and_that_default_is_where_specs_are(self) -> None:
        """The reason the adapter omits the key: the default already points at the tracked
        specs directory, so setting it would be a second copy of one value."""
        loaded = config.load(start=REPO_ROOT, env={})
        assert isinstance(loaded, Resolved), loaded
        self.assertEqual(loaded.value.layer_of("specs_dir"), config.DEFAULT)
        specs_dir = loaded.value.get("specs_dir")
        assert isinstance(specs_dir, str), specs_dir
        self.assertTrue((REPO_ROOT / specs_dir).is_dir(), specs_dir)


class SetupHookTest(unittest.TestCase):
    """The hook's file properties. **That it exits 0 and is idempotent is not covered here.**

    Running it would mean spawning a shell, which the guard's allowlist denies — deliberately,
    since `bash` is a program no test needs to execute.

    It is not a dev-machine check either, and this paragraph used to say it was. Since F3 issue
    12 the hook installs `uv` into `/usr/local/bin` as root, so running it on a developer's
    machine writes to that machine rather than to a container.
    `tests/integration/test_setup_hook.py` owns both properties now: it runs this file in a real
    container, the way dispatch runs it, and runs it twice.
    """

    HOOK: Final = ADAPTER_DIR / "setup.sh"

    def test_the_hook_is_executable(self) -> None:
        """F3 runs it as a program. A hook committed without the bit set fails the dispatch it
        was supposed to prepare, at the point where the log is least readable."""
        mode = self.HOOK.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR, oct(mode))

    def test_the_hook_declares_an_interpreter(self) -> None:
        first_line = self.HOOK.read_text(encoding="utf-8").splitlines()[0]
        self.assertTrue(first_line.startswith("#!"), first_line)


class DockerignoreTest(unittest.TestCase):
    """What docker may see when it builds this image, and what it must not.

    The build context is the adapter directory, and at dispatch time that directory holds the
    gitignored `.env` — an LLM credential — plus every live run's checkout. The Dockerfile has
    no `COPY` and no `ADD`, so the honest context is empty, and the file says exactly that.

    The file's own comment records what that does and does not buy today; what is asserted here
    is only that the rules stay the two they are, because the failure mode is an addition.

    Text assertions, like `DockerfileTest`'s and for the same reason: the suite builds nothing.
    """

    RULES: Final = ["*", "!Dockerfile"]
    """The whole file, comments dropped, restated by hand.

    Equality rather than "denies `.env`", because this file is widened by *addition*: a `!.env`
    or a `!config.local.toml` appended below would leave every containment assertion true while
    putting a credential back in the context.
    """

    def rules(self) -> list[str]:
        text = (ADAPTER_DIR / ".dockerignore").read_text(encoding="utf-8")
        return [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

    def test_the_rules_are_exactly_these(self) -> None:
        self.assertEqual(self.rules(), self.RULES)

    def test_nothing_re_admits_the_secrets_file(self) -> None:
        """The property the equality above happens to satisfy, named so it cannot be lost by
        someone editing the list for an unrelated reason.

        `container.SECRETS_FILE` rather than the string, so renaming the credential file cannot
        leave this test guarding a filename nothing uses.
        """
        readmitted = [rule.removeprefix("!") for rule in self.rules() if rule.startswith("!")]
        self.assertNotIn(container.SECRETS_FILE, readmitted)


def dockerfile_instructions() -> list[tuple[str, str]]:
    """The Dockerfile as `(keyword, body)` pairs, comments dropped and continuations joined.

    Parsed rather than grepped, because every assertion below is about *which instruction* a
    piece of text belongs to. `USER` appearing somewhere in the file proves nothing — it appears
    in this repo's prose and could as easily be a comment saying the directive was removed — and
    the refusal of `AGENT_UID=0` has to be in the same instruction as the `useradd` it guards,
    which a line-oriented search cannot see once the instruction spans six lines.

    A continuation contributes exactly one space to the joined body, whatever indentation it
    was written with. That is what lets an assertion below compare a whole instruction against a
    hand-written copy without that copy encoding the file's layout — while any doubled space
    *inside* the text itself survives, which matters because sudo matches its command verbatim.

    Deliberately not a general Dockerfile parser: no `ARG`/`ENV` expansion, no heredocs, no
    parser directives, no `\\` inside a quoted string. It handles this file, and a construct it
    cannot read shows up as an assertion failing rather than as a silently wrong answer.
    """
    instructions: list[tuple[str, str]] = []
    pending = ""
    for raw in (ADAPTER_DIR / "Dockerfile").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not pending and (not line or line.startswith("#")):
            continue
        if line.endswith("\\"):
            pending += line[:-1].rstrip() + " "
            continue
        keyword, _, body = (pending + line).partition(" ")
        instructions.append((keyword.upper(), body.strip()))
        pending = ""
    assert not pending, "Dockerfile ends inside a line continuation"
    return instructions


class DockerfileTest(unittest.TestCase):
    """The three lines the container boundary consists of: the UID, `USER`, and the sudo grant.

    **The image is never built by this suite** — it cannot be, since the suite must pass with no
    Docker daemon (issue 01a), which is what gives CI a real gate. Everything here reads text.
    That is a real limit and it is worth being exact about what it does and does not buy: these
    tests catch the line being deleted, reworded, or moved out from under the thing it guards,
    which is how all three would actually be lost. They cannot catch a base image that ships a
    surprise, or a `docker run --user 0` at dispatch time.

    The dev-machine checks that do build, and what they printed when this issue was verified:

        docker build --build-arg AGENT_UID="$(id -u)" -t bessemer-agent .bessemer
        docker run --rm --entrypoint id bessemer-agent      # uid=502(agent) gid=1000(agent)
        docker build --build-arg AGENT_UID=0 .bessemer      # exit 1, refused before useradd
    """

    GRANT_INSTRUCTION: Final = (
        'echo "agent ALL=(ALL) NOPASSWD: /usr/bin/bash /bessemer/setup.sh"'
        " > /etc/sudoers.d/agent && chmod 0440 /etc/sudoers.d/agent"
    )
    """The **entire** grant instruction, restated by hand, character for character.

    Whole instruction rather than the grant line inside it, and equality rather than
    `assertIn`, because **a sudoers file is widened by addition, not by rewording.** Measured
    against the previous version of this test: appending
    `&& echo "agent ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers.d/agent` to this same `RUN` left
    the required text present and the instruction count at one, and the full suite reported
    209 tests, `OK`, exit 0 — with the agent holding unrestricted root in the built image. A
    containment check cannot see that, by construction; only equality can.

    Every character is load-bearing for a second reason too: sudo matches the command it was
    given against this text verbatim, so `-x` inserted before the path is denied rather than
    ignored. ADR 0001 fixes both halves — the path is *outside* the checkout, because a grant
    naming a script the agent can rewrite is general root wearing one line's clothing, and
    dispatch must invoke exactly `sudo /usr/bin/bash /bessemer/setup.sh`. A reworded grant is a
    broken dispatch, not a style change, so it costs an edit here too.

    The literal is written flat, with no interpolation of a shorter constant: a copy assembled
    from pieces is a copy whose pieces can be rearranged while every piece still matches.
    """

    def setUp(self) -> None:
        self.instructions = dockerfile_instructions()

    def keyword(self, name: str) -> list[str]:
        return [body for keyword, body in self.instructions if keyword == name]

    APT_PACKAGES: Final = ["sudo", "git", "make", "curl", "ca-certificates"]
    """Everything the image installs on top of the base, restated by hand and in file order.

    A list this adapter owns, so it is pinned rather than read: each entry is here for a named
    reason — `sudo` for the one grant, `git` because the agent commits in-container, `make`
    because this repository's VERIFY step is `make check`, `curl` and `ca-certificates` for the
    agent CLI's installer — and a deletion is silent until a run fails inside a container.

    `make` is the measured case, and the reason this test exists at all: the image shipped
    without it, `make check` is what both prompt overrides tell the agent to run, and the gap
    would have surfaced only after a whole implement pass had been paid for.
    """

    def test_the_image_installs_exactly_these_packages(self) -> None:
        installs = [body for body in self.keyword("RUN") if "apt-get install" in body]
        self.assertEqual(len(installs), 1, installs)
        _, _, rest = installs[0].partition("--no-install-recommends")
        # Up to the `&&`, and equality against the whole list: a sixth package appended before
        # the cleanup would leave every containment check true, which is how a list grows
        # without anyone deciding it should.
        self.assertEqual(rest.partition("&&")[0].split(), self.APT_PACKAGES)

    def test_the_uid_is_a_build_argument(self) -> None:
        self.assertEqual(self.keyword("ARG"), ["AGENT_UID=1000"])

    def test_the_user_is_created_with_that_argument(self) -> None:
        """`useradd` reading a literal instead of the argument is the whole failure mode."""
        creations = [body for body in self.keyword("RUN") if "useradd" in body]
        self.assertEqual(len(creations), 1, creations)
        self.assertIn("${AGENT_UID}", creations[0])

    def test_the_same_instruction_refuses_uid_zero(self) -> None:
        """`useradd -o` accepts 0 as readily as any other duplicate UID, and the resulting
        image's "non-root agent" *is* root, with nothing printed anywhere. Measured before the
        guard existed: `--build-arg AGENT_UID=0` built clean and `id` printed `uid=0(root)`.

        Asserted as one instruction rather than as "a check exists somewhere above": two `RUN`s
        are two things to keep in step, and the deletable one is the check.
        """
        creation = next(body for body in self.keyword("RUN") if "useradd" in body)
        self.assertIn("-eq 0", creation)
        self.assertIn("exit 1", creation)

    def test_the_image_ends_as_the_agent_user(self) -> None:
        """Delete `USER agent` and every test in this file still passes while the container runs
        everything as root — which is why it is pinned rather than trusted to review.

        The *last* `USER` is what the container starts as, so a later `USER root` would undo
        this as completely as deleting the line. Asserting the whole list is what sees that.
        """
        self.assertEqual(self.keyword("USER"), ["agent"])

    def grant_instruction(self) -> str:
        """The one instruction that writes the sudoers file, as the Dockerfile spells it.

        Every assertion about the grant reads this rather than `GRANT_INSTRUCTION`. A test whose
        subject is a constant declared beside it cannot fail for any reason involving the image:
        measured on the previous version of the path assertion below, which passed unchanged
        with the Dockerfile's grant moved into a checkout.
        """
        grants = [body for body in self.keyword("RUN") if "sudoers" in body]
        self.assertEqual(len(grants), 1, grants)
        return grants[0]

    def granted_script(self) -> str:
        """The script path sudo will accept, read out of the grant the Dockerfile writes."""
        _, _, command = self.grant_instruction().partition("NOPASSWD:")
        return command.partition('"')[0].split()[-1]

    def test_the_sudo_grant_instruction_is_exactly_this(self) -> None:
        """Equality against the whole instruction, which is the only shape that sees an
        *addition*. See `GRANT_INSTRUCTION` for the measurement that forced it."""
        self.assertEqual(self.grant_instruction(), self.GRANT_INSTRUCTION)

    def test_the_granted_script_is_not_the_checkouts_own_copy_of_the_hook(self) -> None:
        """The half of the grant that is a security property rather than a spelling.

        The agent can write anywhere in the checkout, so a grant naming the hook *as it appears
        inside the checkout* is a script the agent rewrites and sudo then runs as root. Dispatch
        avoids that by bind-mounting the hook read-only at a path of its own.

        **Asserted as a property, not against a mount path.** When this was written the mount
        path was undecided, and an earlier version asserting `assertNotIn("/workspace", …)`
        was decorative twice over: it invented a contract nobody had agreed to, and it could
        not fire. Measured — deleting that clause changed no outcome, in either direction,
        because the assertion doing the work was the one naming the real path.

        F3 issue 06 decided the mount path, and the *next* test names it. This one stays as
        the shape assertion it was, because the shape is what holds if the mount ever moves:
        the hook is spelled `<mount>/.bessemer/setup.sh` inside any checkout, so a granted
        path containing `/.bessemer/` is the checkout's copy no matter where the mount root is.
        """
        script = self.granted_script()
        self.assertTrue(script.startswith("/"), script)
        self.assertNotIn("/.bessemer/", script)

    def test_the_granted_path_is_outside_the_checkouts_mount(self) -> None:
        """The same property, now that the mount path is a decision rather than a guess.

        `bessemer.container.CHECKOUT_MOUNT` is where the checkout is mounted read-write, so a
        grant naming anything under it names a file the agent can rewrite before sudo runs it.
        Two assertions rather than one prefix test: the mount point itself is not "under"
        itself.

        The constant is read rather than the path spelled out, so nothing here restates
        `/workspace` — which CONTEXT.md bans as a word for the **checkout** even where docker
        takes it as a path.
        """
        script = self.granted_script()
        self.assertNotEqual(script, container.CHECKOUT_MOUNT)
        self.assertFalse(script.startswith(container.CHECKOUT_MOUNT + "/"), script)

    def test_the_grant_and_the_invocation_dispatch_builds_are_one_string(self) -> None:
        """The cross-file agreement, which is the half neither file can check alone.

        sudo compares the command it was given against the sudoers text verbatim, so the grant
        in this Dockerfile and `container.SETUP_HOOK_COMMAND` are one contract written in two
        repositories' worth of places. Either drifting is a dispatch whose setup step is denied
        — reported as the hook failing, which sends the reader to the hook.

        Read out of the parsed instruction on this side and out of the module's constant on the
        other, so neither is derived from the other.
        """
        _, _, command = self.grant_instruction().partition("NOPASSWD:")
        granted = command.partition('"')[0].split()
        self.assertEqual(tuple(granted), container.SETUP_HOOK_COMMAND[1:])
        self.assertEqual(container.SETUP_HOOK_COMMAND[0], "sudo")
        self.assertEqual(self.granted_script(), container.HOOK_MOUNT)

    def test_nothing_else_in_the_image_touches_sudo(self) -> None:
        """The other route to the same end: `usermod -aG sudo agent` grants unrestricted root
        without the string `sudoers` appearing anywhere, so pinning the grant instruction alone
        would not see it. Two instructions may mention sudo at all — the one that installs the
        package, and the one that writes the grant.

        Named limit, in the house style, and this one is measured rather than argued. Folding
        `&& chmod u+s /bin/bash` into the existing `apt-get` instruction left all 210 tests
        green, and the image built from it gave the agent `bash -p` with effective uid 0 and
        real uid 502. So: this is an enumeration of what a *second* privilege grant would have
        to look like, not a proof that none exists — a setuid bit, a `COPY` of a setuid binary,
        or the base image shipping its own sudoers drop-in all reach root without tripping it.
        ADR 0001's container boundary is what stands behind it.
        """
        mentions = [body for _, body in self.instructions if "sudo" in body]
        self.assertEqual(len(mentions), 2, mentions)
        self.assertIn("apt-get install", mentions[0])
        self.assertEqual(mentions[1], self.GRANT_INSTRUCTION)


class GitignoreTest(unittest.TestCase):
    """Runtime state is ignored and the adapter is not, answered by `git check-ignore`.

    Asked inside a throwaway repository holding a copy of this repo's `.gitignore`, rather than
    of the repository the tests are running in: the suite has to pass outside any git work tree
    (see `tests/README.md`), and a check against the ambient repo would fail there for a reason
    that has nothing to do with the rules being tested.

    **Both git calls pass `fixture_env()`, and this is not hygiene.** Inheriting the
    environment, `git init` under an exported `GIT_DIR` does not create the repository below —
    it re-initialises the one `GIT_DIR` names, warns, and exits 0, so this class was silently
    re-initialising the developer's own `.git` on every run while looking green. Under
    `GIT_WORK_TREE` alone it exits 128 instead, which is how the silent case stayed hidden: the
    loud variable masks it. `tests/test_gitenv.py::EscapeTest` pins both directions.
    """

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.repo = Path(holder.name).resolve()
        subprocess.run(
            ["git", "init", "-q", "-b", "main", str(self.repo)],
            env=fixture_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
        )
        # `check=True` does not prove the repository is here. `git init` under an inherited
        # `GIT_DIR` re-initialises the repository *that* names and exits 0, leaving this
        # directory without a `.git` — after which `.gitignore` below is written into a plain
        # directory and every `check-ignore` falls through to whatever `GIT_DIR` pointed at.
        #
        # Measured, and the reason this assertion is not redundant with `fixture_env()`: when
        # the environment scrubbing was deleted from both calls in a *clone of this repository*,
        # all 274 tests stayed green under `GIT_DIR`. The decoy held the same `.gitignore`, so
        # `check-ignore` returned the answers the fixture wanted from the wrong repository —
        # the exact trap `test_the_adapter_and_the_specs_are_not_ignored` warns about, sprung on
        # this class. The environment fix stops the escape; this line is what notices it.
        self.assertTrue(
            (self.repo / ".git").is_dir(),
            f"git init exited 0 without creating a repository at {self.repo}; "
            f"something in the environment redirected it",
        )
        (self.repo / ".gitignore").write_bytes((REPO_ROOT / ".gitignore").read_bytes())

    def test_every_runtime_state_path_is_ignored(self) -> None:
        for path in IGNORED_PATHS:
            with self.subTest(path=path):
                self.assertTrue(git_ignore_check(self.repo, path), path)

    def test_the_adapter_and_the_specs_are_not_ignored(self) -> None:
        """The other direction, and the one that matters more. `.bessemer/` ignored wholesale
        would pass every assertion above while dropping this project's development record."""
        for path in TRACKED_PATHS:
            with self.subTest(path=path):
                self.assertFalse(git_ignore_check(self.repo, path), path)

    def test_the_rules_are_anchored_to_this_adapter(self) -> None:
        """`logs/` unanchored would ignore a `logs/` directory anywhere in *this* tree.

        Not a claim about consuming repos, which an earlier version of this docstring made: this
        file is the repository root's own `.gitignore` and is never shipped anywhere. ADR 0001
        has `init` writing `.bessemer/` and nothing else, so no adopter ever receives these
        rules. The risk is here — a future `logs/`, `locks/` or `checkouts/` directory somewhere
        else in this tree, silently untracked because a rule meant for the adapter reached it.
        """
        for path in ("logs/", "locks/", "checkouts/", "runs.jsonl"):
            with self.subTest(path=path):
                self.assertFalse(git_ignore_check(self.repo, f"elsewhere/{path}"), path)


if __name__ == "__main__":
    unittest.main()
