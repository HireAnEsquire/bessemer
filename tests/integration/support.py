"""Shared machinery for tier 3: the image these tests measure, and how docker is asked.

Tier 3 is the suite that needs a live Docker daemon, so it lives outside `tests/` as a package
— `tests/guard.py` arms itself from `tests/__init__.py` and denies `docker` to everything it
reaches, deliberately and permanently (F3 README decision 2). This directory has no
`__init__.py` for exactly that reason: `unittest discover` walks past a directory that is not a
package, so `make check` cannot collect these files even by accident, and `tests/test_tiers.py`
pins that it does not.

**Nothing here skips.** A tier-3 test that quietly turns into a skip when docker is down is the
green-when-lucky failure the guard exists to prevent, one tier up: `make tracer-tests` is run by
a human who has just been told the daemon is required, and "0 tests, OK" would read as a pass.
A daemon that cannot answer raises `TierThreeUnavailable` out of `setUpModule`, which unittest
reports as an error against the whole module.

**These tests reach the network**, twice and knowingly: building the image installs the agent
CLI, and bessemer's own setup hook installs `uv`. That is the other half of why they are not in
the unit suite.
"""

import importlib.util
import os
from collections.abc import Callable
from pathlib import Path
from typing import Final

from bessemer import proc

REPO_ROOT: Final = Path(__file__).resolve().parent.parent.parent
"""The repository these tests are the tier-3 suite of: the directory holding `tests/`."""

ADAPTER_DIR: Final = REPO_ROOT / ".bessemer"

IMAGE: Final = "bessemer-tracer"
"""The tag tier 3 builds under — **not** `bessemer-agent`, the tag `.bessemer/config.toml`
names.

A separate tag so that running the tier-3 suite cannot replace, or silently satisfy, the image
the human's own dispatches use. The two are built from the same Dockerfile with the same build
argument, so the property under test is the same one; what differs is that a failed or
half-finished tier-3 build leaves the dispatch image exactly as it was.
"""

BUILD_TIMEOUT_SECONDS: Final = 1800.0
"""A cold build fetches a base image and installs the agent CLI over the network."""

DOCKER_TIMEOUT_SECONDS: Final = 120.0
"""Everything else: `docker run` of a one-shot command, `docker ps`, `docker rm`."""


def _load_gitenv() -> Callable[..., dict[str, str]]:
    """`tests/gitenv.py`'s `fixture_env`, loaded **from the file** rather than imported.

    `import tests.gitenv` would run `tests/__init__.py`, which arms `tests/guard.py` — and the
    guard denies `docker` to everything it reaches, on purpose. Tier 3 would then fail on its
    first docker call, in a way that reads as the daemon being down.

    Copying the policy into this file instead would be worse: the whole point of that module is
    that a fixture inherits no `GIT_*` variable, and its own docstring records what a single
    inherited `GIT_DIR` did to the developer's repository. One definition, loaded the long way.
    """
    path = REPO_ROOT / "tests" / "gitenv.py"
    spec = importlib.util.spec_from_file_location("tracer_gitenv", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    loaded: Callable[..., dict[str, str]] = module.fixture_env
    return loaded


fixture_env: Final = _load_gitenv()
"""The environment every git command a tier-3 fixture runs is handed. See `_load_gitenv`."""


class TierThreeUnavailable(Exception):
    """Docker could not answer, or the image could not be built.

    Distinct from an assertion failure, and raised rather than skipped: this says the suite did
    not run, where a skip would say it ran and had nothing to prove.
    """


def docker(argv: list[str], *, timeout: float = DOCKER_TIMEOUT_SECONDS) -> proc.Result:
    """Run one docker command through the package's own runner and return what it did.

    `bessemer.proc.run` rather than `subprocess` directly, for the reason the package uses it:
    it is the one place a child's stdin is not inherited and a timeout is not optional. A
    nonzero exit is data here as it is there — most of these tests are *about* a nonzero exit.
    """
    return proc.run(["docker", *argv], timeout=timeout)


def build_argv(*, uid: str, tag: str | None = None) -> list[str]:
    """The build command, as `.bessemer/Dockerfile`'s own header documents it.

    `tag` is optional because the refusal tests build with no tag on purpose: the build is
    expected to fail, and a tag would name an image that must not come to exist.
    """
    argv = ["build", "--build-arg", f"AGENT_UID={uid}"]
    if tag is not None:
        argv += ["-t", tag]
    return [*argv, str(ADAPTER_DIR)]


_built = False


def require_image() -> None:
    """Build `IMAGE` once per process, or raise. Every tier-3 module calls this first.

    The host's own UID, which is what `.bessemer/Dockerfile` takes the argument for: the
    checkout is bind-mounted and owned by whoever ran this, and an image built with anyone
    else's UID measures a container nobody dispatches.

    Idempotent by module state rather than by asking docker whether the tag exists. "The tag
    exists" would be true of an image built a week ago from a different Dockerfile, and every
    assertion below is about *this* file's contents.
    """
    global _built
    if _built:
        return
    version = docker(["version", "--format", "{{.Server.Version}}"])
    if not version.ok:
        raise TierThreeUnavailable(
            "docker could not be reached — tier 3 needs a running daemon: "
            + proc.quote(version, destination=proc.Destination.HOST_LOG)
        )
    built = docker(build_argv(uid=str(os.getuid()), tag=IMAGE), timeout=BUILD_TIMEOUT_SECONDS)
    if not built.ok:
        raise TierThreeUnavailable(
            f"could not build {IMAGE} from {ADAPTER_DIR}/Dockerfile: "
            + proc.quote(built, destination=proc.Destination.HOST_LOG)
        )
    _built = True


def remove_container(name: str) -> None:
    """Best effort, for a `tearDown`: a container that is already gone is not a failure."""
    docker(["rm", "-f", "-v", name])


def container_exists(name: str) -> bool:
    """Whether docker still knows a container by that name — **stopped ones included**.

    `docker ps -a` rather than `docker ps`, because the leak these tests care about is an
    exited container nobody removed: it holds the run's writable layer and its anonymous
    volumes, and `bessemer gc` lists it for exactly that reason.
    """
    listed = docker(["ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"])
    return name in listed.stdout.split()
