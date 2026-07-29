# Reviewing F1 issues

**If you are an agent reviewing an issue: this file is your instructions.**

One fresh review session per issue, after the implementer reports done and before the human
commits. You have no memory of the implementation — that is the point. Do not ask the implementer
what it meant; if intent isn't legible from the diff and the spec, that is itself a finding.

This also rehearses F3's reviewer pass, so a weakness in these instructions is worth reporting.

## Read first

1. `.bessemer/specs/f1-skeleton/issues/<NN>-*.md` — the issue, especially its acceptance criteria
2. `.bessemer/specs/f1-skeleton/IMPLEMENTING.md` — the rules the implementer was held to, so you
   are not reporting as findings the things it was told to do, or missing the ones it was told
   not to. Note in particular that files under `.bessemer/specs/` **and under `docs/adr/`** are
   written host-side by a human: an edit to either in the diff is not the implementer's work.
   The implementer is required to stop and raise an ADR conflict rather than resolve it, so an
   ADR hunk sitting beside code that depends on it is the process working, not a bypass of it
3. **The port source, when the issue says the work is a port.** `IMPLEMENTING.md` names the
   file and line range; the tree is at `/Users/sbowles/hae`, branch `agentbox`, commit
   `e194121f75f4`, and your session must be launched with `--add-dir /Users/sbowles/hae` to
   read it. Without it you can check that the code is self-consistent but not that it is a
   port — and "port the frame" issues put the line format, the dependency ordering, the
   hand-written skip messages and the exit semantics beyond your reach. Read it before the
   diff; behaviour the port source got right and this one dropped is a finding, and so is
   behaviour copied that no longer makes sense here. If the issue is not a port, skip this.
4. **The whole change, which is not what `git diff` shows you.** Nothing is committed when you
   review, and an issue's most important files are usually the *new* ones — `git diff` omits
   every untracked file silently, so it renders a green, complete-looking diff of the leftovers.
   Issue 07 is the case to remember: `git diff` showed `.gitignore`, an ADR and the issue file,
   while the Dockerfile carrying the container's entire privilege boundary and the test file
   pinning it were both untracked and therefore invisible. A review of that diff reports clean
   and has read nothing that mattered. So:

       git status --short                        # everything, tracked and not
       git diff HEAD --                          # the tracked half
       git status --porcelain | grep '^??'       # the files the line above cannot show you

   Read every untracked file in full. If the issue's headline deliverable is not in your diff,
   that is the symptom, not an absence of work
5. `docs/adr/0002-skeleton-structure.md` and `CONTEXT.md` — the decisions and vocabulary the
   change must conform to
6. `docs/adr/0001-founding-decisions.md` — if the issue touches a security invariant

## What to check, in priority order

1. **Re-run the acceptance criteria yourself.** Do not trust a transcript. Run the commands, and
   for the awkward criteria run the awkward version — actually stop the Docker daemon, actually
   run from outside a git work tree. A criterion that was asserted rather than executed is a
   finding even if the code is correct.

   **For anything that blocks, refuses, or validates: mutate the control and confirm the named
   test fails.** Copy the tree to your scratchpad, delete one branch or one entry, re-run. A
   test that stays green has not been testing that branch, whatever its name says — and the
   usual cause is that some *other* check caught the same case first, so the test passes for a
   reason it does not mention. This is the single highest-yield technique in these instructions;
   it has found real defects that repeated careful reading did not.

   **Run a benign control mutation too, and report it.** A red result proves a test fired; it
   does not prove the test fired *for the reason it names*. Make a harmless edit of the same
   shape in the same place — reorder two independent flags, reflow a comment, rename a local —
   and confirm the suite stays green. Without that, a test hard-wired to reject any change at
   all reads identically to one guarding a property, and every red row in your report means
   less than it appears to. Report the control alongside the battery even when it finds
   nothing; that it found nothing is the result.

   **Prove the mutation applied before you believe a green.** A scripted edit that matches
   nothing leaves the tree untouched, and the suite you then run is the unmutated one — which
   reads exactly like a mutation the tests failed to catch, and gets reported as a defect that
   does not exist. Assert the file changed (`assert new != old`) or print the mutated lines
   back. Measured during issue 06: a substitution written against `"<redacted>@"` matched
   nothing, because the source used a `REDACTED` constant, and the green run was the original
   code passing its own tests. This is the review layer catching itself in the same failure it
   exists to find.

   **Clear `__pycache__` between mutation runs.** A mutation that preserves file size and
   mtime granularity — a status constant swapped for another of the same length, two lines
   reordered — can leave Python serving stale bytecode, so the restored tree looks broken or
   the mutant looks green. `find . -name __pycache__ -prune -exec rm -rf {} +` before each run.
   Found while mutating issue 06, where two of the mutations were byte-identical in length.

   **A test derived from the thing it checks cannot notice that thing shrinking.** If the
   assertion iterates the same list the code iterates, deleting an entry removes it from both
   sides and the test still passes. Somewhere there has to be a literal — a hand-written
   restatement the code does not generate — or the enumeration is unguarded no matter how
   thorough the loop looks.

   **For anything that promises never to raise, enumerate what the library it wraps actually
   raises.** Mutation perturbs values and branches that exist; it cannot show you an exception
   type nobody wrote a branch for. Read the wrapped library's documented failures and check each
   one against the `except` clauses — `tomllib.load` raises `UnicodeDecodeError` as well as
   `TOMLDecodeError`, and that one escaped a module whose docstring said nothing here raises on
   a user's mistake. The question is "what else can this throw", and no amount of careful
   reading of the code under review will prompt it.

   **Say what you could not falsify, and on what host.** A criterion that only fails on another
   platform, or that a denied tool stopped you from mutating, is a first-class part of your
   output — not a caveat to bury. Name it, say why, and say what would settle it.
2. **Gaps.** Every acceptance criterion, satisfied or not. Say which, with evidence.
3. **Unasked-for work.** Stubs, extra subcommands, extra config keys, modules belonging to a
   later issue. Scope creep here is not harmless: this project's premise is a tool that reports
   only what it can vouch for, and a stub is a claim that something exists.
4. **Weakened checks.** Added `# type: ignore`, loosened lint or mypy configuration, skipped or
   deleted tests, assertions softened to make something pass. Compare against the config as it
   was before the diff.
5. **Decision conformance.** Does the change contradict ADR 0001 or 0002? Does it use the
   vocabulary in `CONTEXT.md` — `checkout` not `clone`/`worktree`, `run` not `task`, `spec` not
   `task file`? Vocabulary drift in names and docstrings is worth reporting; it is cheap now and
   expensive after seven more issues copy it.
6. **Security invariants**, where touched — argv-only subprocess, no environment in exception
   context, no credential-bearing text heading anywhere agent-visible. Two questions earn their
   keep every time: *is there a second path to the thing being blocked* — a keyword argument, an
   alias, an async spelling — and *does the control have a test that would fail if it started
   refusing everything*, not only tests that it refuses the bad case.
7. **Comments that assert.** A security note stating a reason, a docstring claiming two things
   are kept in step — check the claim, and check that it is even falsifiable as worded. A
   comment that is subtly wrong about *why* teaches the wrong rule to whoever widens it later,
   and it is the part no test covers.

## What NOT to do

- **Do not fix anything.** Report only. A reviewer that edits leaves its own work unreviewed.
  (F3's in-container reviewer *does* fix inline, because a human gates the PR afterwards. Here
  the human is the immediate next step, so findings are more useful than patches.)
- **Do not restyle**, do not propose refactors, do not suggest architecture changes. The codebase
  is eight issues old; there is nothing to refactor yet.
- **Do not resolve a conflict with an ADR by preferring your own judgement.** The ADR wins. If
  the ADR itself looks wrong, say so as a finding and leave it.

## Output

A short list of findings, each with file:line, what is wrong, and what would make it right.
Order by severity. Then one line:

- `<verdict>approved</verdict>` — every acceptance criterion verified by you, no findings above
  nit level
- `<verdict>needs-work</verdict>` — anything else, with the blocking findings named

The verdict token is deliberately the same one F3's review loop will parse. Using it now means
the format is exercised before anything depends on it.

Finally: **anything the spec should have said but didn't.** The implementer is asked the same
question; you see different gaps than it does.

## How findings reach the implementer

**The implementer never sees your review.** It sees only what the human relays, in a session
that already holds its own work but nothing of yours. Whoever writes that relay — a human
today, F3's dispatcher later — must make it self-contained:

- **No finding numbers.** They index your document, which the implementer has not read.
- **Anchor every item at `file:line` with the defect stated in full.** "Finding 3, fix it" is
  an instruction to read a document that was never sent.
- **Carry the measurement, not the conclusion.** The command and its output are what let the
  implementer reproduce and know when it is fixed; "this is wrong" is not.
- **Say which items block.** Your verdict names them; the relay must keep that mapping.

Written down after it went wrong twice in F1 and F2. The failure is invisible from both
ends — the reviewer sees a complete report, the implementer sees a coherent-looking
instruction referring to something that does not exist — so nobody in the loop is positioned
to notice it except the relay itself.
