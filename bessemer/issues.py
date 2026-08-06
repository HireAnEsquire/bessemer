"""Issue files as data: parsing, selection, spec resolution, and status writes.

Ported from the port source's `.agentbox/tasklib.py` at commit `e194121f75f4`. **The
upstream tests are the specification** — where this module and a reading of what it "should"
do disagree, upstream wins, and the disagreement is a finding in the port report rather than
a silent improvement. `tests/port_manifest.py` is the census that makes that checkable.

**Pure over paths and text.** No subprocess, no environment, no git — on those three,
the same posture as `bessemer.config`, and for the same reason (ADR 0002): everything here
answers a question about files a user wrote, and a question about files should not fail
because `git` is missing. Proven by `tests/test_issues.py`'s purity tests rather than
asserted here.

**The resemblance stops at encoding.** `config` passes an explicit encoding and handles
`UnicodeDecodeError` as a named failure; the four `read_text`/`write_text` calls below pass
no `encoding=` at all, so they use the platform default and a non-UTF-8 issue file raises
where `config` would return a reason. That is upstream's behaviour, ported as it stands and
reported as a finding rather than fixed here. No test in this suite can see it: the only
explicit `encoding="utf-8"` in `tests/test_issues.py` is the purity test reading this
module's own source, and every fixture is written through the same platform default it is
then read back with, on a machine where that default is UTF-8. Which is why it is stated
here rather than left to a test to say.

**`DispatchError` is an exception, and stays one** (decision 2 of the F2 README). ADR 0002's
never-raise rule governs *resolvers*, and it exists because doctor must work when everything
it checks is broken. Doctor never calls this module. The raises below are dispatch-time
caller errors — "issue 12 does not exist", "these are cyclic" — which ADR 0002 already has
dispatch hard-erroring on.

**Vocabulary.** CONTEXT.md retires *task*: the directory of markdown files is `specs_dir`,
and upstream's `_resolve_task_dir` is `source_dir` here. One upstream name survives as
*behaviour* rather than as an identifier — `slugify` falls back to the literal string
`"task"`, which the port source's test asserts by name — so it is ported as it stands and
recorded as a finding.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

_HEADER_RE: Final = re.compile(r"^\s*(status|type|blocked by)\s*:\s*(.*?)\s*$", re.IGNORECASE)
_NUMBER_RE: Final = re.compile(r"\d+")

AD_HOC_DIR: Final = "ad-hoc"
"""Where a typed one-off prompt is materialized, under `specs_dir`."""

DEFAULT_TYPE: Final = "AFK"
"""What a missing or empty `Type:` header reads as.

Tolerant on purpose: the default selection stays useful for feature folders that predate
stricter authoring. Note the asymmetry with `Status`, which is tolerant in the *other*
direction — an unrecognised status never counts as Done.
"""

APPROVED: Final = "approved"
"""The one verdict `issue_summary` renders as a tick; everything else is a cross."""


@dataclass
class Issue:
    """One issue file, parsed. Ported field for field."""

    path: Path
    number: int | None
    title: str
    status: str
    type: str
    blocked_by: list[int] = field(default_factory=list)

    @property
    def display_number(self) -> str:
        return f"{self.number:02d}" if self.number is not None else "??"

    @property
    def is_done(self) -> bool:
        # First-token match: humans annotate status lines ("Done (verified ...)"),
        # and an annotation must not un-Done an issue. The negative lookahead is what
        # keeps "done-ish" from counting.
        return bool(re.match(r"done\b(?!-)", self.status.strip(), re.IGNORECASE))


class DispatchError(Exception):
    """A blocker violation or unresolvable selection — refuse before any container starts."""


def leading_number(text: str) -> int | None:
    """The first run of digits anywhere in `text`, as an int.

    Named for how it is used — the leading number of `02-core.md` — but it searches rather
    than anchors, which is what lets `Blocked by: 01, 03` and a title like `# 02 — core`
    both parse. Ported as it stands. Public because `bessemer.status`'s summary-line
    helpers parse an `--issues` argument with it (decision 7 of the F2 README).
    """
    m = _NUMBER_RE.search(text)
    return int(m.group()) if m else None


def parse_issue(path: Path) -> Issue:
    """Read one issue file's header block into an `Issue`.

    The header block ends at the first `##` — everything below is prose, and a `Status:`
    line inside the body is not metadata. The first `#` line is the title, unless the file
    has none, in which case the filename stem is.
    """
    lines = path.read_text().splitlines()
    title = path.stem
    fields = {"status": "", "type": "", "blocked by": ""}
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("##"):
            break
        if title == path.stem and stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            continue
        m = _HEADER_RE.match(line)
        if m:
            fields[m.group(1).lower()] = m.group(2).strip()
    blocked_by = [
        n for tok in fields["blocked by"].split(",") if (n := leading_number(tok)) is not None
    ]
    number = leading_number(path.stem)
    if number is None:
        number = leading_number(title)
    issue_type = fields["type"].strip().upper() or DEFAULT_TYPE
    return Issue(
        path=path,
        number=number,
        title=title,
        status=fields["status"],
        type=issue_type,
        blocked_by=blocked_by,
    )


def load_issues(issues_dir: Path) -> list[Issue]:
    """Every `*.md` in `issues_dir`, filename-sorted and parsed.

    Unlike `select_issues`, this is the whole feature — Done and HITL included. It is what
    the pull request checklist is built from, which has to cover the feature rather than
    the slice one run happens to take.
    """
    return [parse_issue(p) for p in sorted(issues_dir.glob("*.md"))]


def _topo_order(selected: dict[int, Issue]) -> list[int]:
    """Order `selected` so every issue follows its in-set blockers. Ties broken
    by ascending number for determinism."""
    remaining = dict(selected)
    ordered: list[int] = []
    while remaining:
        ready = sorted(
            n for n, iss in remaining.items() if all(b not in remaining for b in iss.blocked_by)
        )
        if not ready:
            cycle = ", ".join(f"{n:02d}" for n in sorted(remaining))
            raise DispatchError(f"circular 'Blocked by' among selected issues: {cycle}")
        for n in ready:
            ordered.append(n)
            del remaining[n]
    return ordered


def select_issues(feature_dir: Path, issues_arg: str | None) -> list[Issue]:
    """The issues one run will work, in dependency order.

    Two modes, and the difference between them is the whole design. An explicit
    `--issues` list is a human's instruction, so a blocker it does not satisfy is an
    error raised before any container starts. The default selection is a fixed point:
    an issue whose blockers are neither Done nor also selected is dropped from the run
    and left for a later one, silently, because nobody asked for it by name.
    """
    issues_dir = feature_dir / "issues"
    if not issues_dir.is_dir():
        raise DispatchError(f"no issues/ directory under {feature_dir}")
    all_issues = load_issues(issues_dir)
    by_number = {i.number: i for i in all_issues if i.number is not None}

    if issues_arg:
        requested: list[int] = []
        for raw in issues_arg.split(","):
            tok = raw.strip()
            if not tok:
                continue
            n = leading_number(tok)
            if n is None:
                raise DispatchError(f"--issues: can't parse a number from '{tok}'")
            requested.append(n)
        missing = [n for n in requested if n not in by_number]
        if missing:
            nums = ", ".join(f"{n:02d}" for n in missing)
            raise DispatchError(
                f"--issues references unknown issue number(s) {nums} "
                f"(no matching file under {issues_dir})"
            )
        selected = {n: by_number[n] for n in dict.fromkeys(requested)}
    else:
        # default = every AFK, not-yet-Done issue reachable through a chain of
        # already-Done or also-selected blockers (a fixed point, not an error:
        # an issue that never resolves is just left for a later run).
        selected = {
            n: iss
            for n, iss in by_number.items()
            if iss.type == DEFAULT_TYPE and not iss.is_done
        }
        changed = True
        while changed:
            changed = False
            for n, iss in list(selected.items()):
                for b in iss.blocked_by:
                    blocker = by_number.get(b)
                    if not (blocker is not None and (blocker.is_done or b in selected)):
                        del selected[n]
                        changed = True
                        break

    if not selected:
        return []

    order = _topo_order(selected)

    violations = []
    for n in order:
        iss = selected[n]
        for b in iss.blocked_by:
            if b in selected:
                continue  # topo order guarantees it lands earlier in this run
            blocker = by_number.get(b)
            if blocker is None:
                violations.append(
                    f"issue {iss.display_number} ({iss.title}): "
                    f"blocked by unknown issue {b:02d}"
                )
            elif not blocker.is_done:
                violations.append(
                    f"issue {iss.display_number} ({iss.title}): "
                    f"blocked by {blocker.display_number} ({blocker.title}), "
                    f"which is Status: {blocker.status or '(none)'} — not Done and "
                    "not selected earlier in this run"
                )
    if violations:
        listing = "\n".join(f"  - {v}" for v in violations)
        raise DispatchError(
            f"blocker violation(s), refusing before any container starts:\n{listing}"
        )

    return [selected[n] for n in order]


def set_status(path: Path, status: str) -> None:
    """Rewrite `path`'s `Status:` line, inserting one if the file has none.

    The write half of upstream's `cmd_set_status`; the printing half of that command was a
    return code and nothing else. Header-block scoped the same way `parse_issue` reads it:
    the first `##` ends the search, so a `Status:` mentioned in the body is never rewritten
    and an issue file without a header gets one inserted above the body instead.

    Line endings are normalised to `\\n` and the file always ends in one, because the
    rewrite joins the lines it split. That is upstream's behaviour, kept.
    """
    lines = path.read_text().splitlines()
    out: list[str] = []
    written = False
    for line in lines:
        if not written:
            if line.strip().startswith("##"):
                out.append(f"Status: {status}")
                written = True
            else:
                m = _HEADER_RE.match(line)
                if m and m.group(1).lower() == "status":
                    out.append(f"Status: {status}")
                    written = True
                    continue
        out.append(line)
    if not written:
        out.insert(1 if out else 0, f"Status: {status}")
    path.write_text("\n".join(out) + "\n")


def strip_feedback_edit_text(raw: str) -> str:
    """Git-commit-message-style cleanup of the feedback a human typed into an editor:
    drop every line starting with `#` (the header the file was pre-filled with, exactly as
    advertised: "lines starting with # are stripped"), then trim surrounding blank space.
    An empty result signals abort, mirroring an empty commit message.

    Only a `#` in column one counts, so an indented `# ...` inside a fenced snippet of the
    feedback survives. Shell metacharacters are text here and nothing else — quoting is the
    hazard class ADR 0001's argv rule exists to eliminate, and this function is on the far
    side of it.
    """
    kept = [line for line in raw.splitlines() if not line.startswith("#")]
    return "\n".join(kept).strip()


def issue_summary(issues: Mapping[str, str]) -> str:
    """Per-issue verdicts as one compact cell: `02✓ 03✗`, or `-` when there are none.

    Sorted by the number inside the key rather than by the key, so `10` follows `02`
    instead of preceding it. A key with no digits in it sorts as 0 — upstream's choice,
    ported: it keeps a malformed record rendering rather than raising inside a status
    table.
    """
    if not issues:
        return "-"

    def by_number(key: str) -> int:
        # A named function rather than upstream's lambda, which called `_leading_number`
        # twice and read as `int | None` to a type checker. Same ordering, including the
        # 0 for a key with no digits in it.
        number = leading_number(key)
        return 0 if number is None else number

    marks = []
    for num in sorted(issues, key=by_number):
        marks.append(f"{num}{'✓' if issues[num] == APPROVED else '✗'}")
    return " ".join(marks)


def slugify(text: str, max_words: int = 6, max_len: int = 50) -> str:
    """A branch-safe slug from free text: the first few alphanumeric words, lowercased.

    Punctuation is a separator rather than something to strip, so `can't` becomes `can-t`.
    The fallback for text with no alphanumerics in it is the literal `"task"` — a word
    CONTEXT.md retires. Ported as it stands because the port source's test asserts it by
    name; see the port report's findings.
    """
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())[:max_words]
    return "-".join(words)[:max_len] or "task"


def materialize_ad_hoc(
    specs_dir: Path, text: str, branch: str, *, timestamp: str | None = None
) -> str:
    """Write a typed prompt to `<specs_dir>/ad-hoc/<timestamp>-<branch>.md`, returning its path.

    Named after the branch rather than the prompt text: the branch is already the run's
    identity everywhere else (container, log, lock), so naming the spec file after it too
    makes every artifact of one run greppable by a single string. The timestamp prefix is
    kept so the same branch re-dispatched with a new prompt does not collide with its own
    earlier file.

    Returns a `str` rather than a `Path`, and that is load-bearing: the path always contains
    a slash, which is how the resolution below tells "a path" from "a bare name under
    `specs_dir`".

    **Call it only once the branch is confirmed — at the dispatch commit point, never at the
    source step — so an aborted dispatch leaves no orphan file behind.** Upstream states that
    against `cmd_pick`, the interactive picker's top-level command: the picker is where a
    user can still back out, so calling this any earlier writes a file for a run that never
    happens. **The picker is not ported** — decision 1 of the F2 README puts it out of scope
    — so the constraint is recorded here for whoever lands it, and names no step that exists
    in this tree today. It binds F3's dispatcher too: whatever the confirmation point turns
    out to be there, this call goes after it.
    """
    ad_hoc_dir = specs_dir / AD_HOC_DIR
    ad_hoc_dir.mkdir(parents=True, exist_ok=True)
    timestamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    path = ad_hoc_dir / f"{timestamp}-{branch}.md"
    path.write_text(text.strip() + "\n")
    return str(path)


def spec_check_path(specs_dir: Path, spec: str) -> Path:
    """Where a `--spec` argument actually points.

    Mirrors the port source's shell resolution exactly: the `.md` suffix is appended
    unconditionally, *even for slash paths*, and only the `specs_dir` prefix is conditioned
    on the slash. An existence check that skipped the suffix for slash paths would reject
    valid extension-less inputs that dispatch itself accepts — and, worse, could validate a
    different file than the one dispatch eventually resolves to.

    The port source's own comment here named the interactive picker as the caller that must
    mirror this ("the picker's existence check must mirror that exactly"). **The picker is
    not ported** — decision 1 of the F2 README puts it out of scope — so the constraint is
    recorded here for whoever lands it at F5.

    **The live referent is `bessemer.dispatch`** (F3 issue 10, 2026-08-06). It calls this and
    adds the one thing missing: the existence hard error, `!! spec not found: <path>`
    (run.sh:818). Resolution itself is not duplicated there and must not be — the picker
    arriving later has to mirror *this* function, and a second resolver would give it two
    things to mirror.
    """
    name = spec if spec.endswith(".md") else f"{spec}.md"
    if "/" in spec:
        return Path(name)
    return specs_dir / name


def source_dir(
    specs_dir: Path, feature: str | None, spec: str | None, prompt_text: str | None = None
) -> Path:
    """The directory a run's source lives in: a feature folder, a spec's parent, or `ad-hoc/`.

    Upstream's `_resolve_task_dir`, renamed: CONTEXT.md retires *task*. The order is a
    precedence, not a search — a feature wins over a spec, and a pending ad-hoc prompt is
    answered before a spec because at that point in a dispatch there is no spec file yet.
    """
    if feature:
        return specs_dir / feature
    if prompt_text is not None:
        return specs_dir / AD_HOC_DIR
    assert spec is not None
    return spec_check_path(specs_dir, spec).parent
