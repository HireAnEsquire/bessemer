# Skeleton structure: pure config, value-or-reason resolvers, one argv boundary

Date: 2026-07-27. Resolves the module boundaries that [ADR 0001](0001-founding-decisions.md)
deliberately left open for the F1 skeleton. Nothing here reopens an 0001 decision; these are the
shapes that fall out of implementing them, and they compose — each exists partly to protect the
next.

The through-line: **doctor must work when everything it checks is broken.** The port source says
so outright ("doctor's whole point is to work even when the things it's checking are broken"),
and that single requirement forces most of what follows.

## Decisions

- **Config load is pure — filesystem and environment only, no subprocess.** Anything needing
  `git` or `docker` is a separate resolver. Had resolution happened eagerly at load, a user with
  no `origin` remote would get a traceback from the one command that exists to tell them their
  `origin` remote is missing.
- **Resolvers return value-or-reason and never raise.** A tagged union of two frozen dataclasses
  (`Resolved` / `Unresolved(reason, hint)`) matched with `match`, in ~30 lines of `outcome.py`.
  The `hint` carries the fix, not just the diagnosis.
  - *Rejected: eager resolution with a doctor-only bypass* — two code paths through resolution,
    and the untested one is the one that runs when things are broken.
  - *Rejected: eager with a `None` sentinel* — no second path, but the reason is lost, so doctor
    could only report "unset" instead of "no origin remote, run `git remote set-head`".
  - *Rejected: a `Result`/`Maybe` library or pydantic* — the shape appears at a handful of sites;
    railway-oriented `bind`/`map` idioms read as foreign in a codebase whose founding premise is a
    script the team can read and fix, and pydantic is a validation library answering a question
    nobody asked here, at the cost of a compiled dependency on a credential-adjacent tool.
    Revisit if resolvers pass ~10, or config passes ~20 keys.
- **Resolvers are shared predicates, called identically by doctor and dispatch.** Doctor renders
  the outcome; dispatch hard-errors on it. Neither reimplements the other's logic — the same
  discipline the port source applies with `have_claude_credential` and `image_staleness`, whose
  comments say explicitly that one definition is what stops the two from drifting.
- **One subprocess wrapper owns argv; the container env boundary is enforced separately.** Argv
  is always a list, never a string, never through a shell — this is the invariant that eliminates
  the quoting-hazard class the rewrite exists to escape. Host-side children inherit the ambient
  environment because the push path genuinely needs it (`SSH_AUTH_SOCK`, credential helpers), and
  the environment that actually matters is the one crossing into the container, which is docker's
  `-e` arguments — already governed by the argv rule. Enforced by an AST test: no subprocess
  machinery outside the wrapper, and inside it an allowlist drawn at **what can spawn**:
  `subprocess.run` and `subprocess.Popen`, plus the inert names `PIPE`, `STDOUT`, `DEVNULL` and
  `TimeoutExpired`. Allowlist rather than blocklist, because a blocklist loses to the next
  function someone finds (`getoutput` and `getstatusoutput` both shell out and trip neither an
  import check nor a `shell=True` check). The inert names are integers and an exception class:
  none can execute anything, and banning them would forbid the `stdin=DEVNULL` the wrapper
  requires and the `stdout=PIPE` F3's streaming needs — leaving the first person to write either
  with a choice between widening a security allowlist under deadline and working around it.
  - *Rejected: a strict env allowlist everywhere* — the stronger-sounding posture, but it breaks
    `git push` and `gh` in ways that vary per adopter machine, to defend a boundary that was
    never the one under threat.
  - *Amended in issue 05: read-only git queries strip the variables that name a repository.*
    Inheritance is for the push path, which needs `SSH_AUTH_SOCK` and credential helpers; a
    local read-only query needs neither, so removing `GIT_DIR`, `GIT_WORK_TREE`,
    `GIT_COMMON_DIR`, `GIT_CEILING_DIRECTORIES` and their kin costs nothing. It is not the
    rejected allowlist above — it is subtraction, so `HOME`, `PATH`, the locale and the user's
    own `~/.gitconfig` all still pass, and it cannot break on an adopter's machine the way an
    allowlist does. The reason it is not optional: root agreement is the predicate that makes
    "the host pushes from the main repository" name something unambiguous, and `GIT_DIR` alone
    makes it compare a decoy against itself and report agreement. Measured, not argued. The
    line is *location and discovery*, not configuration — `GIT_CONFIG_*` stays, because a
    user's git config is something git needs and not something that moves the repository.
- **`run()` is non-raising by default, `run_checked()` raises.** Doctor's probes are all "did this
  fail, and how"; an exception per probe turns a check list into control flow. The raising
  variant's context must never include the environment, and its stderr is credential-bearing —
  `git` and `gh` echo remote URLs that can embed tokens — so it must never reach a PR body,
  notification, or container log. `Result` gets no `__bool__`: `if result:` reads as "did I get a
  result" and would mean the opposite.
- **The adapter directory is found by walking up from cwd, not by asking git.** Keeps load pure,
  degrades independently (bessemer can report "config found here" *and* "not a git work tree" as
  two facts rather than one useless error), and matches what users expect from `.git` and
  `node_modules`. Discovery gets no override flag at F1 — an escape hatch nobody has needed is how
  discovery accidentally becomes configuration.
- **Config root and git root disagreeing is a hard error in both directions, enforced at dispatch
  and not merely reported by doctor.** Above the git root means the walk escaped the repository (a
  stray `~/.bessemer`); below means `init` ran in a subdirectory. This is load-bearing rather than
  hygienic: the invariant "the host pushes from the main repository" only names something
  unambiguous once the two are known to be the same directory. It catches submodule
  cross-dispatch for free.
- **Doctor is an ordered list of check functions over a shared context, not a dependency
  registry.** Skipping is expressed by asking the context about an earlier result. `ctx.ok()`
  raises on an unknown name — a typo returning falsy would produce a check that skips forever
  while looking principled — and one list-level test asserts every queried name is emitted
  earlier in the list, which is the registry's real safety property expressed as data.
  Two contract behaviors, stated in the module docstring because they are doctor's identity: a
  crashing check renders as FAIL and the report still completes; a skip counts as a failure for
  exit purposes, preserving the port source's scriptable-gate semantics.
  - *Rejected: a declared-dependency registry* — twelve eventual checks don't buy back the
    abstraction, and the port source's hand-written skip messages are better UX than any generic
    auto-skip line.
- **Doctor's check list covers only what has been built.** Each feature extends it as part of its
  own slice. A check that can only fail teaches nothing, and a doctor that WARNs about
  unimplemented subsystems trains its reader to ignore doctor output — fatal for the one tool
  whose output must stay trustworthy.
- **Dev tooling (ruff, mypy strict, pre-commit, CI) is adopted despite the stdlib-first posture.**
  That posture governs the *runtime* supply chain of a credential-adjacent tool; dev tooling ships
  in a dependency group, never reaches an adopter, never enters a container. It earns its keep
  specifically because bessemer is written by agents from F4 on: static checks are the reviewer
  that never gets tired, and a plausible-but-wrong edit is exactly what a type error catches
  before a human reads the diff. The port source is already fully annotated, so `--strict` is
  realistic from the first commit rather than a later migration.
- **One definition of "the checks", with three consumers.** `make check` runs pre-commit over all
  files and then the test suite; CI invokes it verbatim, and from F3 the in-container implement
  prompt runs the same command. A CI job enumerating its own steps would drift from the
  pre-commit config, and the party hurt worst by that drift is the agent — it runs its command,
  believes it is done, and finds the gap after the PR is open. The port source already works this
  way, treating `pre-commit` as the gate an agent must pass before claiming completion.
- **No suppressions; a widened local alias is permitted only where the ill-typedness is the
  assertion.** `# type: ignore` and `# noqa` are banned outright — they are unverifiable at the
  point a reader meets them, and they rot silently. But a test that proves what happens on a call
  a type checker would reject cannot be written without erasing a check somewhere. The permitted
  shape is a one-call local alias — `spawn: Callable[..., object] = subprocess.run` — with a
  comment saying which check is being erased and why. It is narrower than an ignore comment: the
  binding is still checked, so a rename still fails, and only the argument list goes unread. It is
  **not** a way to make ordinary code type-check. If the erasure is not itself the thing under
  test, the code is wrong, not the annotation.
- **The fixer hooks must never rewrite a spec file.** `pre-commit`'s whitespace and end-of-file
  hooks mutate what they check, and from F3 `.bessemer/specs/` is read-only to the agent, with an
  agent-authored edit there treated as a review-stopping finding. Tooling that quietly produced
  one would make the boundary unenforceable — so the config excludes the directory. The checks
  cannot be the thing that violates the rule the checks exist to protect.

  *Amended at issue 05 (2026-08-05): the same sentence covers a **fixture**, and for the same
  reason.* `tests/fixtures/stream/` holds bytes captured from the port source and compared
  byte-for-byte; its `.stderr` files carry lines ending in a space, which the
  trailing-whitespace hook trims. A check that rewrote the oracle would leave a parity test
  comparing this repository's formatting preferences to themselves. The exclusion names the
  captured extensions rather than the directory, so a fixture directory's own prose is still
  tidied.

## Consequences

- The unit suite must pass with no Docker daemon, no network, and outside any git repository. CI
  therefore has a real gate even though the F1 tracer needs a live daemon — and that gate is what
  catches a bad mechanical edit during F2's 337-test port, the riskiest mechanical moment in the
  plan.
- **Specs are tracked in this repo, diverging from the port source, which gitignores them.**
  Bessemer's specs are its development record. Two consequences follow, both accepted
  deliberately: dispatched runs leave `Status:` churn in tracked files, to be committed with the
  merge; and because specs are tracked they are cloned into the agent's checkout, where the port
  source's gitignored layout had exposed only the single mounted spec. From F3 the implement
  prompt declares the specs directory read-only to the agent, and review treats an agent-authored
  edit to a spec file as a review-stopping finding rather than a nit.
- `container_env_keys` is not implemented at F1. The F1 loader parses TOML generically; that key's
  committed-layer restriction and its doctor FAIL land in F3 with the container boundary they
  exist to enforce.
