"""The environment a test hands to a git command it runs itself.

**A fixture inherits no `GIT_*` variable at all.** Every git call a test makes builds or
interrogates a repository the test created, and nothing about that repository comes from the
developer's shell — identity is passed with `-c` flags, the config layers are pointed at
`/dev/null`, and the repository is named by `cwd` or by an argument. So the whole family goes,
rather than a list of the ones known to hurt.

That is deliberately **stricter than what the code under test does**.
`bessemer.resolve.REDIRECTING_VARIABLES` subtracts nine names and inherits the rest, because a
resolver runs on an adopter's machine where `HOME`, `PATH` and the config variables are load
bearing and an allowlist breaks in ways that vary per host. A fixture has no such obligation:
it owns everything it touches, so it can afford the blunter rule, and the blunter rule is the
one that survives git growing a tenth variable.

`FamilyTest` ties the two together — every name `resolve` strips begins with `GIT_`, so this
rule provably covers that set — which is what stops these two policies from drifting into
disagreement without either file changing.

**Measured, and this is why the module exists.** With `GIT_DIR` exported, `git init -q -b main
<a temporary directory>` does not create that repository: it *re-initialises the repository
`GIT_DIR` names*, prints `warning: re-init: ignored --initial-branch=main`, and **exits 0**. In
this suite that meant three silent re-inits of the developer's own `.git` per run, on a green
result. With `GIT_WORK_TREE` exported and `GIT_DIR` not, the same command exits 128 —
`fatal: GIT_WORK_TREE ... not allowed without specifying GIT_DIR` — so the loud failure and
the silent write are *different variables*, and setting both hides the silent one behind the
one that works. A suite verified only with every variable exported at once therefore verifies
the benign combination: see `tests/README.md`.
"""

import os
from typing import Final

GIT_PREFIX: Final = "GIT_"
"""The family this module refuses to inherit. One prefix, not a list of names."""

FORCED: Final = {
    # The two config layers a personal `core.excludesFile`, `insteadOf` rewrite or
    # `init.defaultBranch` would arrive through. `/dev/null` parses as an empty config.
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    # Belt and braces on the system layer: honoured even where the path above is not.
    "GIT_CONFIG_NOSYSTEM": "1",
    # No fixture may sit waiting for a credential prompt. `bessemer.proc` closes stdin for
    # the children it spawns; a test spawning git directly does not get that for free.
    "GIT_TERMINAL_PROMPT": "0",
}
"""What every fixture git call gets, after the inherited `GIT_*` names are dropped."""


def fixture_env(**overrides: str) -> dict[str, str]:
    """The ambient environment with every `GIT_*` name replaced by `FORCED` and `overrides`.

    Non-git variables are inherited untouched: `PATH` is how git is found at all, and a test
    that replaced the whole environment would be asserting about a machine nobody has.
    """
    env = {name: value for name, value in os.environ.items() if not name.startswith(GIT_PREFIX)}
    env.update(FORCED)
    env.update(overrides)
    return env
