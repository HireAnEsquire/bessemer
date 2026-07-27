# Implementing F1 issues

F1 is built interactively — one fresh agent session per issue. This is deliberately the same
shape F3's dispatcher will use (fresh agent, one mounted spec), so treat any confusion an agent
hits as a spec bug to fix, not just a session to steer.

## Order

`01 → 02` first and in that order: 01 creates the package the rest lands in, and 02 installs the
checks everything after it must pass. Review both harder than their size suggests — they set the
conventions the other six imitate.

Then `03`, `04`, `07` are independent. `05 → 06 → 08` is a hard chain.

## Prompt template

Substitute the issue number and paste. Launch with `--add-dir /Users/sbowles/hae` only for the
issues marked below as needing the port source.

```
Implement issue NN in the bessemer repo (/Users/sbowles/bessemer).

Read first, in this order:
  1. .bessemer/specs/f1-skeleton/issues/NN-*.md   — what to build
  2. .bessemer/specs/f1-skeleton/README.md        — how F1 is built
  3. docs/adr/0002-skeleton-structure.md          — why the modules are shaped this way
  4. CONTEXT.md                                   — the project's vocabulary; use these terms
  5. docs/adr/0001-founding-decisions.md          — the security invariants and dispatch
                                                    semantics, if your issue touches them

Rules:

- Implement ONLY this issue. Later issues are assigned to other sessions. Do not scaffold
  stubs for work that isn't yours — an empty subcommand or a placeholder module is a lie
  about what exists.
- Match the conventions already in the codebase. If it's empty, you're setting them: be
  deliberate and consistent.
- Every acceptance criterion in the issue must be verified by running something, not by
  reading the code and concluding it's fine. Show me the output.
- Do NOT edit any file under .bessemer/specs/ — not even to tick an acceptance checkbox.
  Spec files are written host-side by a human; this mirrors the boundary the dispatcher
  enforces from F3.
- Do NOT commit. Leave the changes in the working tree and tell me what you changed and why.
- If the issue conflicts with an ADR, or a decision looks wrong once you're in the code,
  STOP and say so. Do not resolve it silently — these decisions were grilled, and a
  conflict means either the spec is wrong or the reasoning is, and both are worth knowing.

When done: report what you built, the verification output, and anything the spec should have
said but didn't.
```

## Per-issue additions

**01 — package skeleton.** Add: *"You are setting the conventions for this codebase. Docstring
style, test layout, and naming here will be copied by seven more sessions."* Watch for: extra
subcommands, a hand-maintained `__version__` literal, a runtime dependency sneaking into
`pyproject.toml`.

**02 — tooling.** Add: *"If `mypy --strict` or `ruff` reports problems in existing code, fix the
code. Do not loosen the configuration, add ignores, or add `# type: ignore` comments to make
checks pass. CI must invoke `make check` verbatim rather than listing steps of its own — one
definition of the checks, or they drift."* This is the single most likely place an agent quietly
weakens the thing it was asked to install.

After 02 lands, every later prompt gains: *"`make check` must pass before you report done."*

**03 — subprocess wrapper.** Security-critical. Add: *"The AST test must be proven in both
directions — write it, then deliberately introduce a `shell=True` call, a stray `subprocess`
import outside the wrapper, and a `subprocess.getoutput` call inside it, and show me the test
failing on each before you remove them."*

**04 — config.** Add: *"This module must not start a subprocess, directly or indirectly. Prove
it with a test, not an assertion in a docstring."*

**05 — resolvers.** Add: *"Test against real temporary git repositories, not mocks — the failure
modes here are git's actual behavior (`origin/HEAD` unset, detached HEAD, submodules), and mocks
would encode your assumptions about git rather than git."*

**06 — doctor.** **Needs the port source** — launch with `--add-dir /Users/sbowles/hae`. Add:
*"The check-runner frame is a port of `.agentbox/run.sh` lines 303–420 at commit e194121f75f4.
Read it first. Port the frame — the ok/WARN/FAIL line format, the dependency ordering, the
hand-written skip messages, the exit semantics. Do not port its check list; F1's checks are the
five in the issue."*

**07 — own adapter.** Add: *"Verify the Dockerfile actually builds, and verify the `agent` user's
UID inside the built image matches the `AGENT_UID` build argument. A Dockerfile that has never
been built is not done."*

**08 — tracer.** Add: *"The four failure paths are the point of this issue, not an afterthought —
a green line on a healthy machine proves almost nothing. Actually stop the Docker daemon, actually
run it from /tmp, actually unset `origin/HEAD`, actually plant a second `.bessemer/` above the
repo. Report the real output of each."*

## After each issue

You (not the agent) update the issue's `Status:` line and commit. That split is the same one the
dispatcher enforces: spec state is host-side, always.
