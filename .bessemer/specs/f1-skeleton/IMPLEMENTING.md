# Implementing F1 issues

**If you are an agent implementing an issue: this file is your instructions. Read all of it,
then the "Your issue" section for your number.**

F1 is built interactively — one fresh agent session per issue. That is deliberately the same
shape F3's dispatcher will use (fresh agent, one mounted spec), so any confusion an agent hits
here is a spec bug worth fixing, not just a session to steer.

## Read first, in this order

1. `.bessemer/specs/f1-skeleton/issues/<NN>-*.md` — your issue: what to build
2. `.bessemer/specs/f1-skeleton/README.md` — how F1 is built and why specs are tracked
3. `docs/adr/0002-skeleton-structure.md` — why the modules are shaped the way they are
4. `CONTEXT.md` — the project's vocabulary. Use these terms in names, docstrings, comments
5. `docs/adr/0001-founding-decisions.md` — the security invariants and dispatch semantics,
   if your issue touches them

## Rules

- **Implement ONLY your issue.** Later issues belong to other sessions. Do not scaffold stubs
  for work that isn't yours — an empty subcommand or placeholder module is a lie about what
  exists, and this project's whole premise is a tool that reports only what it can vouch for.
- **Match the conventions already in the codebase.** If it is empty, you are setting them: be
  deliberate and consistent.
- **Verify every acceptance criterion by running something**, not by reading the code and
  concluding it is fine. Show the actual output, including for the awkward criteria.
- **From issue 02 onward, `make check` must pass before you report done.**
- **Do not edit any file under `.bessemer/specs/`** — not even to tick an acceptance checkbox.
  Spec files are written host-side by a human; this mirrors the boundary the dispatcher enforces
  from F3.
- **Do not commit.** Leave changes in the working tree and report what you changed and why.
- **If a tool is denied to you, stop and ask — do not find another route to the same effect.**
  Deleting a file with `os.remove` because `rm` was refused reaches the identical outcome the
  refusal was about, and it does it somewhere nobody is looking. The denial is a decision, not
  an obstacle; treat it the way you treat a spec conflict. Say what you were trying to do and
  what was blocked. (ADR 0001 makes this binding on the dispatched prompts too, with the
  reasoning — it is not an F1 house rule.)
- **If your issue conflicts with an ADR, or a decision looks wrong once you are in the code,
  STOP and say so.** Do not resolve it silently. These decisions were grilled at length, so a
  conflict means either the spec is wrong or the reasoning is — and both are worth knowing.

## When done, report

What you built, the verification output, and **anything the spec should have told you but
didn't**. That last one matters most: from F4 these specs are read by agents with nobody to ask.

## Your issue

**01 — package skeleton.** You are setting the conventions seven more sessions will copy. Do not
add ruff, mypy, pre-commit, a Makefile, or CI — that is issue 02. Do not build the test guard —
that is issue 01a. `doctor` is a stub: it exists as a subcommand and exits 0; issue 06 fills it
in. Add no other subcommand.

**01a — test guard.** Security-critical, and split out of 01 precisely because three review
rounds found successive holes in it while it was a sub-bullet of something else. Enumerate
adversarially: for every path you block, ask what the second path to the same effect is — a
keyword argument, an alias, a subclass, an async spelling, a connectionless send. Every comment
that states a reason must be true, and where coverage has a known limit, name the limit rather
than writing a claim that reads as complete.

**02 — tooling.** If `mypy --strict` or `ruff` reports problems in existing code, **fix the
code** — do not loosen configuration, add ignores, or add `# type: ignore` comments to make
checks pass. This is the likeliest place to quietly disarm the thing you were asked to install.
CI must invoke `make check` verbatim rather than listing steps of its own: one definition of the
checks, or they drift.

**03 — subprocess wrapper.** Security-critical. The AST test must be proven in both directions:
write it, then deliberately introduce a `shell=True` call, a stray `subprocess` import outside
the wrapper, and a `subprocess.getoutput` call inside it, and show the test failing on each
before removing them. A security test that has never failed is decoration.

**04 — config.** This module must not start a subprocess, directly or indirectly. Prove it with a
test, not an assertion in a docstring.

**05 — resolvers.** Test against real temporary git repositories, not mocks. The failure modes
here are git's actual behavior — `origin/HEAD` unset, detached HEAD, submodules — and mocks would
encode your assumptions about git rather than git.

**06 — doctor.** Requires the port source (this session is launched with
`--add-dir /Users/sbowles/hae`). The check-runner frame is a port of `.agentbox/run.sh` lines
303–420 at commit `e194121f75f4` — read it first. Port the frame: the ok/WARN/FAIL line format,
dependency ordering, the hand-written skip messages, the exit semantics. Do **not** port its
check list; F1's checks are the five named in the issue.

**07 — own adapter.** Verify the Dockerfile actually builds, and that the `agent` user's UID
inside the built image matches the `AGENT_UID` build argument. A Dockerfile that has never been
built is not done.

**08 — tracer.** The four failure paths are the point of this issue, not an afterthought — a
green line on a healthy machine proves almost nothing. Actually stop the Docker daemon, actually
run from `/tmp`, actually unset `origin/HEAD`, actually plant a second `.bessemer/` above the
repo. Report the real output of each.

---

## For the human running these sessions

**Order.** `01 → 01a → 02` first and in that order: 01 creates the package everything lands in,
01a arms the guard every later suite runs under, 02 installs the checks everything after must
pass. Review all three harder than their size suggests — they set the conventions the rest
imitate. Then `03`, `04`, `07` are independent; `05 → 06 → 08` is a hard chain.

**Launch.** From `/Users/sbowles/bessemer`, run `claude` — except issue 06, which needs
`claude --add-dir /Users/sbowles/hae`. Then:

> Implement issue `<NN>` in this repo. Read `.bessemer/specs/f1-skeleton/IMPLEMENTING.md` and
> follow it, including the "Your issue" entry for `<NN>`.

**After each issue**, before committing, run a fresh review session:

> Review the current diff against issue `<NN>`. Read
> `.bessemer/specs/f1-skeleton/REVIEWING.md` and follow it.

Then you (not the agent) update the issue's `Status:` line and commit. That split is the one the
dispatcher enforces from F3: spec state is host-side, always.
