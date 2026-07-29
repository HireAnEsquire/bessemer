# 06 — Doctor: check runner, F1 checks, rendering

Status: Done
Type: AFK
Blocked by: 05

## What to build

`bessemer/doctor.py` plus its renderer in the CLI. Doctor is the running definition of
what bessemer can vouch for, which is why it is F1's tracer.

### Shape

Per ADR 0001's ops-as-library posture, the core returns `list[CheckResult]` and the CLI
renders. A `CheckResult` carries a short name, a status (`ok`/`WARN`/`FAIL`), a message,
and an optional hint.

An **ordered list of small check functions over a shared context**. Each takes the
context, returns a `CheckResult`, and expresses dependency-skipping by asking the context
about an earlier result — `if not ctx.ok("docker"): return skipped(...)`.

Deliberately not a declared-dependency registry: twelve eventual checks do not buy back
the abstraction, and the port source's hand-written skip messages ("skipped — docker
unavailable, fix the docker check above first") are better UX than any generic auto-skip
line.

**`ctx.ok()` raises on a name no earlier check produced**, distinct from "ran and
failed". A typo'd `ctx.ok("dokcer")` returning falsy would produce a check that skips
forever while looking principled — precisely the silent drift this design avoids. One
list-level test enforces the ordering invariant: walk the check list and assert every
name a check queries is emitted by a check earlier in the list. That is a static
dependency check — the registry's real safety property, as data, without the machinery.

### Contract (state both in the module docstring, they are doctor's identity)

- **A crashing check renders as FAIL with the exception text; the report always
  completes.** One broken check must never take down the report — working when things are
  broken is the whole point.
- **Skipped counts as failed for exit purposes.** Exit 0 only when every check is `ok` or
  `WARN`; exit 1 on any FAIL or skip. This preserves the port source's scriptable-gate
  semantics exactly.

  **A skip *is* a FAIL — there is no fourth status.** Stated outright during implementation,
  because "the statuses are `ok`/`WARN`/`FAIL`" and "exit 1 on any FAIL or skip" only reconcile
  once someone decides that, and a fourth status satisfies the prose equally while breaking the
  pinned-statuses criterion below. A skip is a FAIL carrying the port's hand-written message.
  Structural, so the exit rule needs no separate arithmetic to enforce it.

### F1 checks, in dependency order

Checks cover **only what has been built** — that is the standing scope rule, and each
later feature extends this list as part of its own slice. A check that can only fail
teaches nothing.

1. `uv` / interpreter present and usable
2. config found (the discovered `.bessemer/`, or the not-found reason)
3. git environment clean — WARN if any of issue 05's `REDIRECTING_VARIABLES` is exported
4. root agreement (config root vs git root)
5. base resolution (`origin/HEAD` or the reason, with its hint)
6. docker CLI present and daemon responding

Check 3 exists because issue 05 made the resolvers immune to those variables and that
immunity is exactly what makes the check necessary. Bessemer now answers correctly about the
repository on disk while every git command the developer types by hand answers about
somewhere else — and bessemer is the only thing in the room that knows. WARN rather than
FAIL: a poisoned shell is the user's environment, not a broken adapter, and nothing bessemer
does is wrong because of it. It sits immediately before root agreement so the explanation
lands next to the check whose result it would otherwise make baffling. Read the names from
`resolve.REDIRECTING_VARIABLES` rather than restating them — that list is issue 05's and is
pinned by a literal there; a second hand-written copy here would be two lists to keep in
step, which is a different defect from the one the literal rule prevents.

Rendering follows the port source: one line per check, status column, name column,
message, and the hint on failure. `cli.doctor` already exists as a stub returning 0 —
extend it rather than adding a second path to the same subcommand.

### What the context must carry, and why it is not `os.environ` and not `Path.cwd()`

- **One `start`, threaded to all three callers.** `config.load`, `resolve_base` and
  `resolve_root_agreement` each take `start`. Doctor must pass the *same* one to all of
  them. Let any of them default independently and doctor reports on two different
  directories while printing one report — and root agreement, which exists to catch exactly
  that disagreement, becomes the check that cannot see it.
- **`env` is a context field, not a read of `os.environ` inside a check.** Check 3 asks
  what the developer exported. A check that reads the ambient environment itself can only
  be tested by mutating the test runner's own environment, which makes the test an
  assertion about the host: green on the machine that has the variable, red on the one that
  does not, and vice versa. That is the same shape as issue 03's ambient stdin and issue
  05's ambient `GIT_DIR` — twice now, in two subsystems, both found after the fact.
  `config.load` already takes `env`; follow it.

### The two programs doctor checks for cannot be spawned by the suite

`tests/guard.py`'s `ALLOWED_PROGRAMS` is `{"git", "bessemer"}` plus the interpreter by
path. **`uv` and `docker` are both absent**, so checks 1 and 6 raise `GuardViolation` if a
test lets them reach a real spawn.

**Do not widen the allowlist.** For `docker` it is a genuine hazard, not a formality — a
suite permitted to run `docker` is one image pull away from network access on a
contributor's laptop, and ADR 0002 requires the suite to pass with no daemon at all. The
check must be testable by handing doctor a stubbed runner instead. That leaves the real
argv unasserted by the stub, so **pin the argv separately** — a test that reads what the
check would spawn without spawning it, the way issue 07 pins Dockerfile instructions by
reading them.

This is worth stating because the tempting fix is two characters in a frozenset, and the
guard is written to make that a reviewable act rather than a quiet one.

### Doctor prints exception text, and exception text is credential-bearing

The contract above says a crashing check renders as FAIL with the exception text. Issue 03
raises from `run_checked` with `stderr` in the message, and issue 05 established that git's
stderr can carry a token from a remote URL. Doctor renders to a terminal today and, from
F3, into a pull request body — so **this is the first place an unredacted exception reaches
output**, and the crashing-check path is precisely the one nobody wrote a reason for.

Decide what the crash renderer prints and say so. If redaction is reused, `resolve._redact`
and `DETAIL_LIMIT` are private to `resolve`; promote them deliberately to a shared home
rather than copying — a second regex is two redactors that can disagree, and the one that
disagrees silently is the one printing into a PR.

*Settled during implementation: the shared home is a new `bessemer/redact.py`, importing
nothing else in the package for the same reason `bessemer.outcome` does — everything that
reports another program's text imports it, so anything it imported would become a dependency
of the whole package. `proc` was the alternative and is wrong: the hazard is not spawning, it
is printing, and `doctor` redacts an exception `proc` never produced.*

**No version floor is checked for `uv` or the interpreter, only presence and usability.**
`requires-python` is what enforces the floor, at install time, with a better message than
doctor could produce. Doctor reports both versions and judges neither. Named because "present
and usable" reads as an invitation to invent a minimum, and a floor bessemer enforces in two
places is two floors that can disagree.

## Acceptance criteria

- [ ] `bessemer doctor` prints one line per check and exits 0 when all pass
- [ ] Exit 1 on any FAIL, and on any skip
- [ ] A check raising an exception renders as FAIL and the remaining checks still run —
      proven by a deliberately crashing test check
- [ ] `ctx.ok("nonexistent")` raises; `ctx.ok` on a check that ran and failed returns
      False
- [ ] Ordering test fails when a check is moved before the one it queries
- [ ] Doctor runs and reports usefully outside a git repo, with no `.bessemer/`, and with
      the Docker daemon stopped — no traceback in any of the three
- [ ] Base and root-agreement lines come from the issue 05 resolvers, not reimplemented
- [ ] **One `start` reaches `config.load`, `resolve_base` and `resolve_root_agreement`** —
      proven by a test running doctor against a directory that is not the process's cwd, and
      getting a report about that directory rather than about where the runner happens to be
- [ ] **No rendered line carries a credential, at *every* site that prints another program's
      text** — not only the crashing check. Same construction as issue 05's: a synthetic
      failure whose stderr holds `https://x-access-token:ghp_…@github.com/…`, asserted absent
      from the rendered output. One assertion per site: a crashing check, a nonzero `docker
      info`, a nonzero `uv --version`, and an `OSError` from either.

      The earlier wording named the crash path alone, and that is exactly what shipped: four
      redacting call sites, one of them pinned, three surviving the mutation that deletes the
      redactor. A criterion that names one instance of a rule teaches that the rule has one
      instance. Where a test already goes red on such a mutation, check *which* assertion
      fired — the `uv` site went red for losing `detail()`'s first-line behaviour, which is a
      test passing for a reason other than the one wanted
- [ ] **`DETAIL_LIMIT` is pinned by a test**: `detail()` of a long single line is capped.
      `bessemer/redact.py` now sits on the path of everything bessemer prints that it did not
      write, with two consumers reading the cap by different routes, and a cap enforced by
      nothing is a docstring. Whichever file owns `redact` owns this test
- [ ] **No test spawns `docker` or `uv`**, and the argv each check would spawn is pinned by a
      test that does not spawn it. `ALLOWED_PROGRAMS` is unchanged by this issue; if you
      believe it must change, stop and say why rather than changing it
- [ ] **The check list and the status values are pinned by hand-written literals.** A test
      restates the six check names in order, and another restates `ok`/`WARN`/`FAIL`.
      Without this, deleting a check makes doctor print five lines and exit 0 with the
      whole suite green — the report shrinks and nothing notices, which is the one failure
      a tool whose job is reporting must not have. An assertion that iterates the check
      list cannot catch it; the literal is the point
