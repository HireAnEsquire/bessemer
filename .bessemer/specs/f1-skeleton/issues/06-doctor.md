# 06 — Doctor: check runner, F1 checks, rendering

Status: Todo
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
message, and the hint on failure.

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
- [ ] **The check list and the status values are pinned by hand-written literals.** A test
      restates the six check names in order, and another restates `ok`/`WARN`/`FAIL`.
      Without this, deleting a check makes doctor print five lines and exit 0 with the
      whole suite green — the report shrinks and nothing notices, which is the one failure
      a tool whose job is reporting must not have. An assertion that iterates the check
      list cannot catch it; the literal is the point
