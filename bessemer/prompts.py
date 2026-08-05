"""The agent's instructions: packaged defaults, and the adapter's overrides.

Three templates ship inside this package — the implement pass's, the review pass's, and the
PR-description pass's. A same-named file under `<adapter>/prompts/` wins, and it wins **at
read time**, which is ADR 0001's decision and not an implementation detail: a user edits a
prompt without forking bessemer, every un-overridden template keeps improving with the pin,
and doctor reports how many are overridden so the drift stays visible.

**Overrides are whole files, never patches.** There is no merge, no section splice, and no
inheritance: the override is the prompt. That is what makes the resolution one `read_text`
with a fallback rather than a template language, and it is why an adapter that wants one
extra sentence copies the default and edits it — accepting that its copy stops tracking the
pin, visibly, in a file someone committed.

**The defaults are stack-agnostic, and that is a property under test.** The port source's
prompts were saturated with one adopter's stack (a Django/React ORIENTATION, a
`manage.py test` VERIFY, that repo's own admin URLs in the PR template), and the core may
assume nothing about language or test tooling (ADR 0001). So the *structure* is ported
section for section and the specifics are excised. The parity story is what makes the
excision provably safe rather than merely tidy: hae's own `.bessemer/prompts/` overrides at
F7 restore today's text byte-for-byte, so the parallel-run gate compares assembled prompts
that match the pin (F3 README decision 7.1).

**Four content deltas against the pin, each pinned by a test literal in
`tests/test_prompts.py`.** They are controls, not prose: a reworded control is a changed
control, so the tests restate the sentences by hand and a rewrite has to be deliberate.

1. *The denied-tool rule*, in both the implement and the review default. ADR 0001 mandates
   it and the pin predates the decision. The sentence is identical in both files, so one
   literal pins both.
2. *The specs directory is read-only to the agent* in the implement default, and an
   agent-authored edit under it is a **review-stopping finding** in the review default —
   ADR 0002's consequence of tracking specs in this repository.
3. *The verdict semantics*: approved only on a round that changed nothing; a round that
   committed fixes ends `needs-work`. Ported verbatim from the pin, pinned here because it
   is what makes the review loop terminate correctly rather than run to its cap.
4. *`<promise>COMPLETE</promise>` is dropped.* **The recorded divergence:** measured at the
   pin, that token appears in `.agentbox/implement-prompt.md:54` and nowhere else in the
   source — no reader in `run.sh`, none in `tasklib.py`. An output contract nothing consumes
   is a claim the tool cannot vouch for, so it goes rather than being ported forward. Prompt
   defaults sit outside the parity comparison once hae runs its overrides, so this costs the
   gate nothing.

A fifth sentence is pinned for the same reason without being a divergence: the PR template's
FORMAT contract — every line lands verbatim in the PR body, no preamble, no code fence, no
title line. Issue 08 composes a PR body out of that pass's final text and has no way to
strip a fence it never asked for.

**The templates hold the static half of a prompt; the dispatcher appends the rest.** At the
pin the assembled prompt is the template file followed by generated lines naming the branch
and the diff boundary (run.sh:1476–1479), delivered on stdin — spec content is never
interpolated into a command (ADR 0001). Nothing here composes; `load` returns a template and
the pass module adds its lines.

**Why the defaults live in `prompt_templates/` rather than `prompts/`:** this module is
`bessemer/prompts.py`, and a package directory of the same name cannot sit beside it. The
override directory keeps the name ADR 0001 published to adopters (`.bessemer/prompts/`),
because that one is a user-facing path and this one is a packaging detail.
"""

from importlib import resources
from pathlib import Path
from typing import Final

IMPLEMENT: Final = "implement-prompt.md"
REVIEW: Final = "review-prompt.md"
PR: Final = "pr-prompt.md"

TEMPLATE_NAMES: Final = (IMPLEMENT, REVIEW, PR)
"""Every template this package ships, in pass order.

The names are the port source's, unchanged, because they are also the override filenames an
adopter writes: renaming them here would rename a documented path for no gain. Ordered as a
run uses them — implement, then review, then the PR description — so a doctor line built
over this reads in the order the reader watched them run.
"""

DEFAULTS_DIR: Final = "prompt_templates"
"""The packaged templates' directory, inside this package. See the module docstring."""

OVERRIDE_DIR: Final = "prompts"
"""The adapter subdirectory an override lives in: `.bessemer/prompts/<name>` (ADR 0001)."""


def _require_known(name: str) -> None:
    """Refuse a name this package does not ship.

    Loud, like `config._require_known` and for the same reason: the caller is bessemer's own
    code, so a name that is not a template is a bug in this package rather than a user's
    mistake, and returning a default for it would hide that forever.

    It is also what keeps `name` out of path construction as free text. `load` builds
    `<adapter>/prompts/<name>`, so a name arriving from anywhere less trusted than this
    module's own constants — a future config key, a provider adapter's template set — would
    otherwise be a traversal (`../../.ssh/config`) into a read that ends up in an agent's
    prompt. Nothing passes user input here today; this is what makes that stay true.
    """
    if name not in TEMPLATE_NAMES:
        raise ValueError(
            f"{name!r} is not a prompt template; this package ships {list(TEMPLATE_NAMES)}"
        )


def load(name: str, *, adapter_dir: Path | None = None) -> str:
    """The template text for `name`: the adapter's override if it has one, else the default.

    `adapter_dir` is the `.bessemer/` directory itself — `Config.adapter_dir`. It is a
    parameter rather than something discovered here because discovery belongs to
    `bessemer.config` and having two walk-ups would mean two answers to where the adapter is.
    `None` means "no adapter in play" and yields the packaged default, which is what doctor
    wants when config failed to load and what the tests want when the default is the subject.

    **A present-but-unreadable override is an error, not a fallback.** Only
    `FileNotFoundError` falls through to the default: a file that is there and cannot be read
    — a directory in its place, a permission denial, bytes that are not UTF-8 — propagates.
    The alternative reads better and is worse. Silently substituting the default would run
    the agent on instructions the adapter's author did not write, unattended, with the
    difference visible nowhere; a run that stops with the path in the message is the failure
    the author can act on. This module therefore does *not* share `bessemer.config`'s
    never-raise contract, and deliberately: config is what doctor calls to explain a broken
    machine, and this is what a dispatch calls with a container already running.

    An empty override file is an empty prompt, not an absent one. Whole-file wins means the
    file's content is the template, and a zero-byte file is content someone committed.
    """
    _require_known(name)
    if adapter_dir is not None:
        try:
            return (adapter_dir / OVERRIDE_DIR / name).read_text(encoding="utf-8")
        except FileNotFoundError:
            # The ordinary case — most adapters override nothing. Distinguished from every
            # other read failure above, in the docstring, because the distinction is the
            # decision.
            pass
    return resources.files(__package__).joinpath(DEFAULTS_DIR, name).read_text(encoding="utf-8")


def overridden(adapter_dir: Path) -> tuple[str, ...]:
    """Which templates this adapter overrides, in `TEMPLATE_NAMES` order.

    The fact, not the response — the same split `config.committed_only_violations` makes.
    Doctor renders a count from this (F3 issue 09; ADR 0001 asks for the count so that drift
    from the pinned defaults stays visible), and it renders it as an `ok` line, because an
    override is a feature. Nothing here decides that.

    Beyond ADR 0003's original one-function interface for this module, and recorded there
    rather than argued here: the ADR's table names it since 2026-08-05.

    It exists so doctor does not have to restate `<adapter>/prompts/<name>` to count what is
    in it. A glob over the directory would count differently — a `README.md` an adopter left
    beside their override, a stale `pr-prompt.md.bak` — and would report drift that `load`
    would never read.

    A name whose path cannot be stat'd counts as absent: `Path.is_file` reports `False` for
    an unreadable parent rather than raising, so a locked-down adapter directory under-counts
    here instead of failing doctor. `load` is where an unreadable override becomes loud, and
    it is loud at the moment the file is actually needed.
    """
    return tuple(
        name for name in TEMPLATE_NAMES if (adapter_dir / OVERRIDE_DIR / name).is_file()
    )
