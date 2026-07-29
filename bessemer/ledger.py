"""The ledger: the append-only record of every run, as data.

Ported from the port source's `.agentbox/tasklib.py` at commit `e194121f75f4`. **The
upstream tests are the specification** — where this module and a reading of what it "should"
do disagree, upstream wins, and the disagreement is a finding in the port report rather than
a silent improvement. `tests/port_manifest.py` is the census that makes that checkable.

The ledger is the only thing in bessemer that remembers. It is *derived* state
(CONTEXT.md): git stays the source of truth, so a stale or deleted ledger degrades a
default and never a correctness claim. That is the whole reason for the tolerance below.

**Files and JSON, nothing else.** No subprocess, no environment, no git — the same posture
as `bessemer.issues` and `bessemer.config`, and proven by `tests/test_ledger.py`'s purity
tests rather than asserted here.

**Corruption is data, not an exception.** `read_ledger` skips a malformed line instead of
raising. A ledger is append-only and written by concurrent runs, so a half-written final
line is an ordinary event, not a reason to take down the one command that would have shown
it. An unreadable file is the same case one level up: a missing file, a permission error and
a file that is not text all yield no records. Making any of this stricter is a decision, and
it goes back to the human rather than into this module.

**The concurrency story, measured from the port source rather than assumed.** `append_ledger`
opens with mode `"a"` — `O_APPEND` — and writes one buffered line per call, with no lock and
no `fsync`. Measured against the port source on this machine (APFS, 8 concurrent processes,
480 records): no interleaved lines and no lost records, at line sizes of 130 B, 4 KB, 8 KB,
40 KB and 8 MB. `O_APPEND` makes each write's offset claim atomic, and a regular local file
never returned a short write, so a record never straddled another. What that measurement
does *not* cover, and what F3 should treat as the real limit: a short write — which is what
a full disk or an NFS mount can produce — splits one record across two offsets and the
halves can interleave. The design already answers that case rather than preventing it: the
torn line is skipped by `read_ledger` and the run it recorded is forgotten, which costs a
`--base` default and never a landing. Appends are ordered by *completion*, not by
`timestamp`; see `resolve_last` for what that costs.

**`append_ledger` does not print** (decision 2 of the F2 README). Upstream wrote its one
failure to stderr; a library does not talk to a terminal (ADR 0002's ops-as-library split).
The reason is returned instead of destroyed, as an `Unresolved`, so the CLI can still say it.

**The legacy migration is not here** (decision 4 of the F2 README). Upstream called
`_migrate_legacy_ledgers` from six sites, including `collect_recent_ledger_records` below;
bessemer has zero installs, so it could only ever run against files bessemer never created.
The ported functions lose that call and the behaviour goes with it: `read_ledger` on a specs
directory holding only legacy per-directory `runs.jsonl` files returns nothing, and that is
correct here.

**`task_dir` is the record key, and it stays spelled that way.** CONTEXT.md retires *task*,
so the parameters below are `specs_dir` and `source_dir` — but the string inside a ledger
line is data upstream's tests assert by name, and renaming a key is changing what the code
does. The two are bridged in `run_record`, once, rather than at every read site.
"""

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from bessemer.issues import DispatchError
from bessemer.outcome import Unresolved

type Record = dict[str, Any]
"""One ledger line, parsed. `Any` because a ledger line is whatever a past bessemer wrote —
including a version of it that recorded a key this one has never heard of."""

_LEDGER_FILENAME: Final = "runs.jsonl"

_RECENT_BRANCHES_LIMIT: Final = 8
"""How many distinct branches a not-found refusal offers as a hint."""

_LATEST_PER_BRANCH_LIMIT: Final = 10
"""How many branches a resume menu offers. Upstream's default, kept with its caller
unported — see `_latest_per_branch`."""


def central_ledger_path(specs_dir: Path) -> Path:
    """`<bessemer-dir>/runs.jsonl` — one ledger per adapter, beside the specs it dispatches.

    The parent of `specs_dir` rather than a setting of its own: every caller already
    resolves the specs directory, and a second path to configure is a second path to get
    wrong.
    """
    return specs_dir.parent / _LEDGER_FILENAME


def read_ledger(ledger_path: Path) -> list[Record]:
    """Every well-formed record in `ledger_path`, in file order.

    Derived state: a missing file yields no records, and a corrupt line is skipped rather
    than raising — a run must never fail because `runs.jsonl` was deleted or truncated
    mid-write. A line that parses to something other than an object is skipped for the same
    reason: every reader below calls `.get`, and a bare `3` in the file would take the whole
    command down rather than one line of it.

    **No `encoding=` on the read, which is upstream's, and it costs the whole file.** The
    platform default applies, so under a non-UTF-8 locale one non-ASCII byte anywhere raises
    `UnicodeDecodeError` — and the handler below degrades the *entire ledger* to no records,
    not the offending line. Measured under `LC_ALL=C PYTHONUTF8=0`: two well-formed lines,
    one carrying a non-ASCII branch name, read back as zero records, the ASCII line
    included. That is total history loss from one byte, which is a different thing from the
    per-line tolerance above. Ported as it stands and recorded as defect 12 of the F2
    README, not fixed here.
    """
    try:
        text = ledger_path.read_text()
    except OSError, UnicodeDecodeError:
        return []
    records: list[Record] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def append_ledger(ledger_path: Path, record: Record) -> Unresolved | None:
    """Append one JSON line to `ledger_path`. `None` on success, a reason on failure.

    Best-effort by design: a write failure — a missing directory, a read-only mount — is
    returned rather than raised, because by the time this is called the run has already
    pushed and opened its pull request, and none of that may be undone by a ledger hiccup.

    Upstream printed that failure to stderr from inside the library. Decision 2 of the F2
    README removes the print; returning the reason is what keeps the print's *information*
    once its terminal is gone, and leaves the saying of it to the CLI.

    **Only `OSError` is caught, which is upstream's and is not enough** — a record holding a
    value `json.dumps` cannot serialize raises `TypeError` straight through this contract.
    Ported as it stands and reported as a finding; see the port report.
    """
    try:
        with ledger_path.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as e:
        return Unresolved(
            reason=f"could not append to ledger {ledger_path}: {e}",
            hint=f"check that {ledger_path.parent} exists and is writable",
        )
    return None


def run_record(
    *,
    branch: str,
    base: str,
    spec: str | None = None,
    feature: str | None = None,
    issues: Mapping[str, str] | None = None,
    outcome: str | None = None,
    pr_url: str | None = None,
    source_dir: str | None = None,
) -> Record:
    """One run, as the line the ledger will hold.

    Upstream built this dict inline in `cmd_ledger_append`, the subcommand that existed so
    `run.sh` could reach python. That subcommand is not ported — a python dispatcher calls
    `append_ledger` directly — but the record's shape is not part of the shim, so it lands
    here rather than dying with it. Bessemer's own test pins the key set by hand.

    `source_dir` is the run's source — the feature folder, or the spec's own directory — and
    it is recorded as the key `task_dir`, which is what upstream wrote and what every reader
    below still looks for. **It is a recorded fact and never a write location**: the ledger
    is central, and the only thing this directory decides is which runs a resume menu groups
    together. An empty string records as `None` rather than as a stale path, for the resumed
    run whose source directory no longer exists on disk.

    The timestamp is UTC and taken here, so it is the moment the record was *built* rather
    than the moment it landed; under concurrency those differ, which is the divergence
    `resolve_last` names.
    """
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "branch": branch,
        "base": base,
        "spec": spec,
        "feature": feature,
        "issues": dict(issues or {}),
        "outcome": outcome,
        "pr_url": pr_url or None,
        "task_dir": source_dir or None,
    }


def parse_issue_outcomes(items: Sequence[str]) -> dict[str, str]:
    """`["01=approved", "02=needs-work"]` as `{"01": "approved", "02": "needs-work"}`.

    Upstream's `--issue NUM=OUTCOME` parsing, which lived in the same unported subcommand as
    `run_record`'s body. A caller that already has a mapping should pass it to `run_record`
    and never come here; this exists for the one that has strings, and mostly so that the
    refusal below is not lost with the subcommand that used to print it.

    A pair with no `=` is a `DispatchError`, per decision 2 of the F2 README: this is a
    dispatch-time caller error, which is the class ADR 0002 already has dispatch hard-erroring
    on. Upstream returned exit code 2 and wrote the same sentence to stderr.
    """
    outcomes: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise DispatchError(f"--issue expects NUM=OUTCOME, got '{item}'")
        num, outcome = item.split("=", 1)
        outcomes[num] = outcome
    return outcomes


def last_base_for_branch(ledger_path: Path, branch: str) -> str | None:
    """The most recently recorded base for `branch`, or `None` if it has never run.

    **Every line naming the branch counts, whatever `source_dir` it was stamped with.** A
    branch is the run's identity (CONTEXT.md), so the base it last targeted is a fact about
    the branch and not about the folder someone dispatched it from — dispatching the same
    branch from a second feature does not reset its base default. That is the opposite of
    `_ledger_branch_order` below, which scopes by source directory precisely because it is
    building a menu about one folder.
    """
    for record in reversed(read_ledger(ledger_path)):
        if record.get("branch") != branch:
            continue
        base = record.get("base")
        if isinstance(base, str) and base:
            return base
    return None


def newest_record_for_branch(ledger_path: Path, branch: str) -> Record | None:
    """The most recent record naming `branch`, regardless of `task_dir` — same
    file-order-is-recency-order convention as `last_base_for_branch`."""
    for record in reversed(read_ledger(ledger_path)):
        if record.get("branch") == branch:
            return record
    return None


def recent_distinct_branches(
    ledger_path: Path, limit: int = _RECENT_BRANCHES_LIMIT
) -> list[str]:
    """The `limit` most-recently-active branches anywhere in the ledger, newest first,
    deduped — the not-found refusal's hint list."""
    seen: list[str] = []
    for record in reversed(read_ledger(ledger_path)):
        branch = record.get("branch")
        if isinstance(branch, str) and branch and branch not in seen:
            seen.append(branch)
            if len(seen) >= limit:
                break
    return seen


def resolve_last(ledger_path: Path) -> Record:
    """The single newest record in the ledger, regardless of branch or outcome.

    No outcome filtering: resuming a failed run is a primary use case for "the last one".
    Raises `DispatchError` on an empty or missing ledger — the "no runs recorded yet"
    refusal, which is a dispatch-time caller error and so an exception (decision 2).

    **Newest means last in the file, not largest `timestamp`,** and
    `collect_recent_ledger_records` sorts by `timestamp` instead. The two disagree whenever
    the orders disagree, which is a finding against the port source rather than a choice made
    here; see the port report.
    """
    records = read_ledger(ledger_path)
    if not records:
        raise DispatchError("no runs recorded yet — dispatch normally first")
    return records[-1]


def collect_recent_ledger_records(specs_dir: Path) -> list[Record]:
    """Every record in the central ledger, newest first.

    ISO 8601 timestamps sort correctly as plain strings, and a missing or empty timestamp
    sorts last rather than raising.

    **A timestamp that is present but not a string raises**, and upstream's docstring
    claimed otherwise. `read_ledger` accepts `{"timestamp": 1753142400}` as data, and the
    sort below then dies with `TypeError: '<' not supported between instances of 'int' and
    'str'`. This is the status table's read path, so the one command that would have shown a
    damaged ledger is the command the damage kills. Ported as it stands and recorded as
    defect 11 of the F2 README; the claim is narrowed here rather than the code widened.

    Upstream triggered the legacy migration here first —
    reads counted, per that migration's contract — and decision 4 of the F2 README drops it,
    so a specs directory holding only legacy per-directory ledgers yields nothing.
    """
    records = read_ledger(central_ledger_path(specs_dir))
    records.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
    return records


def _ledger_branch_order(ledger_path: Path, source_dir: Path) -> list[str]:
    """Branches with a ledger record stamped with this `source_dir`, most-recent-run first,
    deduped.

    Scoped to the source directory, unlike `last_base_for_branch`: this answers "which
    branches has *this feature* been run on", which is a menu about a folder.

    **Its only upstream caller is the interactive picker, which is not ported** — decision 1
    of the F2 README puts it out of scope — so nothing in this tree calls this today. It is
    here because its tests are in scope and it is a pure ledger read; the terminal UI that
    consumed it is what was deferred.
    """
    seen: list[str] = []
    for record in reversed(read_ledger(ledger_path)):
        if record.get("task_dir") != str(source_dir):
            continue
        branch = record.get("branch")
        if isinstance(branch, str) and branch and branch not in seen:
            seen.append(branch)
    return seen


def _annotate_branch(ledger_path: Path, source_dir: Path, branch: str) -> str:
    """`branch` with what the ledger knows about it appended, or bare if it knows nothing.

    Same source-directory scoping and the same unported caller as `_ledger_branch_order`.
    The pull request URL is taken from the newest record that *has* one rather than from the
    newest record, so a later feedback-only run that opened no pull request does not hide the
    one the branch already has. Upstream writes the same search and documents none of it, so
    this sentence is bessemer's claim — and
    `test_a_later_run_without_a_pull_request_does_not_hide_the_branchs_existing_one` is what
    holds it up, added after a mutation to `last.get("pr_url")` left the suite green.
    """
    records = [
        r
        for r in read_ledger(ledger_path)
        if r.get("task_dir") == str(source_dir) and r.get("branch") == branch
    ]
    if not records:
        return branch
    last = records[-1]
    bits = [f"base: {last.get('base', '?')}"]
    issues = last.get("issues") or {}
    if issues:
        done = sum(1 for outcome in issues.values() if outcome == "approved")
        bits.append(f"issues done: {done}/{len(issues)}")
    pr_url = next((r.get("pr_url") for r in reversed(records) if r.get("pr_url")), None)
    if pr_url:
        bits.append(f"PR: {pr_url}")
    return f"{branch}  ({', '.join(bits)})"


def _latest_per_branch(
    records: Sequence[Record], limit: int = _LATEST_PER_BRANCH_LIMIT
) -> list[Record]:
    """The newest record per branch, newest-first, capped at `limit`.

    `records` is expected already newest-first — `collect_recent_ledger_records` is what
    produces it — so the first record seen for a branch is its latest. Same unported caller
    as the two helpers above: the picker's resume menu.
    """
    seen: set[str] = set()
    result: list[Record] = []
    for record in records:
        branch = record.get("branch")
        if isinstance(branch, str) and branch and branch not in seen:
            seen.add(branch)
            result.append(record)
            if len(result) >= limit:
                break
    return result
