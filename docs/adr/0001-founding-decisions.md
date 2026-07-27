# Founding decisions: what bessemer is, and the shape it takes

Date: 2026-07-24. This is bessemer's founding record. The tool was incubated inside the
[hae](https://github.com/HireAnEsquire/hae) repository as `.agentbox/` — a bash spine plus a
python helper — and proven end-to-end there over roughly two months of daily use. Extraction into
this repository is also a rewrite; the reasoning for that, and for every alternative rejected
along the way, is recorded below so it does not have to be rediscovered.

Port source is pinned: **hae commit `e194121f75f4`** (branch `agentbox`). Where this document
says "port", it means port that revision's behavior, not redesign it.

## Context

Bessemer dispatches AFK ("away from keyboard") coding agents. Per run it clones the target repo
from origin, runs a Docker container built from the repo's own image, runs the agent CLI headless
through an implement pass and then a reviewer pass with a verdict-break loop, pushes the branch
from the host, and opens or updates a draft PR. The human is the merge gate, always.

The founding invariant is **per-run test isolation**: each container gets its own throwaway
database, so migrations and test databases cannot collide across concurrent runs. That is the
capability no hosted alternative offered when this was surveyed (2026-07-20, 23 sources) —
not Claude Code GitHub Actions, not the Copilot cloud agent, not Docker Sandboxes, not gh-aw,
not Anthropic Managed Agents.

Extraction is driven by confirmed adoption from multiple outside teams. Their constraints,
gathered 2026-07-24, define the core's assumptions:

- **Heterogeneous stacks** — Django/React, Node (Next/SvelteKit), PHP. The core may assume
  nothing about language or test tooling.
- **Not everyone has a compose-built image** to base the agent image on.
- **Issue locations vary** — a configurable specs directory is necessary, not optional.
- **`main`/`master` is mixed** — base-branch auto-detection is necessary.
- All adopter machines have node; all have some python3.

The tool has no users other than its author. If a rewrite is ever warranted, this is the cheapest
moment it will ever have. That question was pressure-tested rather than assumed away.

## Decisions

### Language and implementation

- **Extraction IS the rewrite: this repo starts life as a pure-python-stdlib port of the bash
  spine.** One migration instead of two, and the port is validated against a living reference —
  hae keeps running the incubated hybrid untouched until the port passes a parallel-run
  comparison on real dispatches, then installs bessemer as first adopter and deletes the old
  core. Rationale for python over keeping bash: python is already a hard host dependency (the
  existing helper is python), so bash adds a second language while removing no requirement; the
  bash half is the untested half (all 337 tests cover the python helper — and every recent bug
  class was bash-specific: `set -e` AND-list semantics, quoting hazards); and the growth
  direction (config, provider abstraction, resume recovery, setup wizard) is data-shaped, not
  pipe-shaped. Security surface shrinks: one subprocess wrapper controls every child's **argv** —
  a list, never a string, never a shell — so the shell-interpolation surface disappears entirely.
  The environment crossing into the container is a separate boundary, enforced by constructing
  docker's `-e` arguments explicitly rather than forwarding host environment; host-side children
  (`git`, `gh`) inherit the ambient environment because the push path genuinely needs it. The
  python helper carries over largely intact with its test suite.
- **Rejected: TypeScript/node.** Viable — all adopters have node, and node 22 type-stripping
  even removes the build step. Greenfield from zero it would be a real contest. But half the
  system is already tested python: port-to-python rewrites only the untested half,
  port-to-TypeScript rewrites everything including the proven half. For a working system with
  security invariants, that asymmetry decides it.
- **Rejected: Go/Rust binary.** Its benefits (no interpreter dependency, exact signed artifacts,
  Windows) amortize over large user counts; its costs (CI cross-compilation, release infra, no
  vendoring — binaries don't belong in git) are fixed and immediate. Wrong trade for single-digit
  adopting teams. Revisit only if adoption scales an order of magnitude.
- **Rejected: building on the Claude Agent SDK.** Wrong layer. Bessemer deliberately orchestrates
  the agent CLI as a credential-isolated subprocess, and multi-provider support (codex/cursor) is
  on the roadmap — coupling the spine to one vendor's SDK fights both. (If review ambition ever
  grows past a single bounded reviewer — multi-lens, adversarial, judge/escalation — the SDK
  becomes the right tool for that layer specifically.)
- **Rejected: building on a sandbox framework. This was tried, not assumed.** The dispatcher was
  originally built on `@ai-hero/sandcastle` (v0.12.0) and the pipeline was proven end-to-end with
  it — dispatch, container, tests green, branch, PR. It was then torn out and replaced with a
  transparent script: only a thin slice of the framework was used (`createSandbox`/`run` plus the
  docker provider), its abstractions were fought repeatedly (nested paths in the copy helper,
  package-manager breakage, base64 env workarounds, an auto-merge template that had to be
  gutted), and its security model added nothing beyond what container isolation plus a human PR
  review already gave. Every substantive asset — image, DB setup, env, prompts — carried over
  unchanged. The general lesson, which bessemer is built on: for shared infrastructure with
  security invariants, a script the team can read and fix beats a framework whose abstractions
  must be fought. It is also why bessemer's own abstractions stay thin.
- **Python 3.14 via `requires-python`; no compatibility floor.** Under uvx the host interpreter
  is not the adopter's problem — uv reads `requires-python` and fetches the interpreter itself,
  so the version is a language-feature choice, not an adoption constraint. A lower floor would
  buy nothing and cost syntax churn in the ported helper.
- **Stdlib-first is posture, not necessity.** uv makes dependencies mechanically free for
  adopters (one pyproject line, resolved and locked invisibly), so the remaining argument is
  supply-chain surface on a credential-adjacent tool that pushes code. Default is stdlib;
  exceptions are judged on need and audit cost, with uv lock/hash pinning mitigating.
- **Operations as library, frontends thin.** Every operation (dispatch, status, gc, ledger,
  selection) is a function returning data; the CLI is a renderer over them. A future dashboard is
  then a frontend choice, not a rewrite.

### Distribution and configuration

- **Distribution: uvx pinned from git; the core never enters the consuming repo.** The committed
  `.bessemer/config.toml` pins a source ref (tag or commit SHA); a two-line repo shim runs
  `uvx --from git+<source>@<pinned-ref> bessemer ...`. uv resolves, caches, and supplies the
  interpreter; no PyPI publishing required. Every teammate runs the exact team-pinned version —
  the pin lives in a committed file, so the within-team version skew that killed plain
  tool-on-PATH cannot occur — and upgrading is a one-line reviewable ref bump. `.bessemer/` in a
  consuming repo holds ONLY adapter files: Dockerfile, setup hook, config, prompt overrides, spec
  content, runtime state. It is written by `init` once and never touched by any update path, so
  there is no clobber surface at all.
  - *Rejected: plain tool-on-PATH* — within-team version skew, and install infrastructure needed
    on day one.
  - *Rejected: vendored copy with hash-guarded sync* (shadcn/cruft/copier style) — workable, but
    it means building and maintaining installer, sync, and drift machinery that uvx makes
    unnecessary.
  - *Acknowledged trade:* a ref bump doesn't show the core diff in the consuming repo's PR the
    way a vendored update would. Mitigated by pinning SHAs and reading the upstream diff before
    bumping. Scaffold improvements don't auto-propagate either; a read-only `init --diff` shows
    adapter drift against current templates, for applying by hand.
- **Config: two-layer TOML plus secrets env.** Committed `.bessemer/config.toml` carries
  team-level facts (image, base, the source pin, `specs_dir` when a team keeps shared specs
  in-repo). Gitignored `.bessemer/config.local.toml` carries dev-owned values (notify, model,
  provider, `specs_dir` under the local-specs model, machine limits). Any key is valid at either
  layer; local wins. Secrets stay in a gitignored `.bessemer/.env` only. Precedence:
  **CLI flags > `BESSEMER_*` env vars > local > committed > auto-detect / defaults**, where
  auto-detect covers the base branch (from `origin/HEAD`).
  - **One documented exception to "any key at either layer": `container_env_keys`, the list of
    environment variable names permitted to cross into the container, is committed-layer only.**
    Doctor FAILs if it appears in `config.local.toml`. The point of naming keys in a shared,
    committed file is that widening the container's secret boundary is a reviewable diff; a
    gitignored override would erase exactly that property, silently, on one machine. Names are
    public, values are private — the values themselves never leave the gitignored `.env`.
    Bessemer's own credential names are built-in defaults, so an adopter who needs nothing extra
    never encounters this key at all.
- **Prompts: package defaults, per-repo overrides.** Prompt templates ship inside the package; a
  same-named file under `.bessemer/prompts/` wins at read time. Users edit freely without
  forking, un-overridden defaults keep improving with the pin, and doctor reports the override
  count so drift stays visible.
- **Setup hook: `.bessemer/setup.sh`, scaffolded once, contract not convention.** Idempotent,
  non-interactive; a nonzero exit aborts the dispatch and surfaces the log. The image template
  keeps a single sudoers line granting root for exactly this hook — one auditable,
  human-committed file, and also what lets the agent legally revive a dead service mid-run. The
  scaffold's header explains dropping the grant when the stack needs no root.

### User interface

- **UI stays gum-only and optional; fzf is retired.** gum is a single optional binary with a
  tested numbered-prompt fallback; doctor WARNs about its absence and never requires it. fzf's
  remaining use (branch selection) moves to `gum filter`.
  - *Rejected: Textual* — a full application framework to serve a picker that is menus, an input,
    and a confirm. Weight and supply-chain surface without need. (Under uvx a dependency's
    mechanical cost is nil, so need is the whole test. If the parked dashboard's trigger fires, a
    framework becomes needed and Textual's case is strong.)
  - *Rejected: building a localhost web UI now* — no parity target for the port's validation, and
    its trigger, parallel-run monitoring pain, hasn't fired. A localhost server that can trigger
    dispatches will need its own security grill: loopback-bound, token-gated, and hardened
    against DNS rebinding and CSRF.
  - **Standing UX requirement:** prompt flows should aim for the cohesive clack-style polish of
    vite/sveltekit-class CLIs. That is achievable in stdlib ANSI without a framework; the picker
    port evaluates a small internal prompt-rendering module that may retire gum entirely.

### Security core — invariants, not preferences

These were established during incubation, validated by an external audit and by the June 2026
Microsoft disclosure (prompt injection via issue comments hijacking the Claude Code GitHub
Action, exfiltrating CI secrets). They are ported as explicit code with explicit tests, and are
never weakened for convenience:

- **The container holds no git credentials and no real secrets.** The only permitted secret class
  is an LLM-API credential — one that can spend money but cannot touch git, repo hosting, or
  infrastructure. Any credential outside that class entering the container is an egress-posture
  revisit trigger.
- **Bulk environment transfer is permitted only from committed files; gitignored files forward
  declared keys only.** This is the enforcement half of the rule above, and it is a deliberate
  divergence from the port source, which passes the developer's gitignored `.env` wholesale via
  `--env-file` — making the secret-class rule a convention the adopter upholds by restraint
  rather than something the code guarantees. Committed adapter env files (dummy, type-correct
  values needed to boot the app under test) may still transfer in bulk: they are reviewable in
  git, so a real secret placed there is a visible mistake. The gitignored `.env` forwards only
  the names listed in `container_env_keys` plus bessemer's built-in credential names. A key
  present in `.env` but not forwarded produces one operator-facing warning naming the key — never
  its value, and never into the container log, PR body, or a notification, since the names alone
  tell a prompt-injected agent what secrets exist on the host.
- **The host never runs write-side git inside the agent's checkout.** In fact it runs no git
  inside the checkout at all: the current branch is read textually from `.git/HEAD`. The host
  fetches FROM the checkout via a hardened `upload-pack` invocation, and pushes from the main
  repository.
- **Credential checks report presence only** — never values, never fragments.
- **`master`/`main` is refused as the working branch.** The working branch is a push target, so
  protected branches are hard-refused.
- **Draft PRs only; the human is the merge gate.** Nothing auto-merges, ever.
- **`gc` never deletes logs.** Salvage before removing a checkout is fast-forward-only, and
  liveness is re-checked immediately before every deletion.
- **Prompt-injection boundary.** PR title and body may enter prompts on a best-effort basis; PR
  review comments never do. Feedback reaches the agent only through self-authored channels — the
  issue/spec file, or an explicit `--feedback` argument.
- **Task containers publish no ports**, drop all capabilities, and run under pids/memory limits.
- **Specs are self-authored.** A spec is a markdown file a human wrote or approved. Dispatching
  unreviewed third-party text (a Jira ticket body, a GitHub issue comment) as agent instructions
  is the demonstrated injection vector and stays out of the pipeline.
- **Specs reach the agent by mounted path, never inlined into the prompt.** Interpolating a spec's
  contents into a command (`$(cat …)`) is a shell-injection hazard and is never done. The same
  rule covers ad-hoc typed prompts: they are materialized host-side into a spec file and mounted
  like any other spec — which also makes them rerunnable and ledgerable.
- **A duplicate in-flight run fails fast.** Per-branch lockfiles plus a container-name check
  refuse a dispatch whose container is still running, rather than silently killing the running
  one.
- **A crashed run leaks nothing.** Container and checkout cleanup is trap-based, so an aborted or
  failed dispatch leaves no orphaned container or checkout behind.

### Dispatch semantics — ported as-is

Carried over intact from incubation; recorded here because bessemer's implementation must match
them and there is no earlier ADR in this repo to point at:

- **An issue file IS the dispatch spec.** Feature work is broken
  down into markdown issue files (tracer-bullet vertical slices, typed AFK or HITL); the
  dispatcher takes any path. There is no render step, and the self-authored-spec guarantee holds
  because the human approval step during issue-writing is the authoring.
  - *Rejected: direct Jira or GitHub-issue dispatch* — unreviewed third-party text as agent
    instructions, the demonstrated injection vector.
  - *Rejected: a Jira adapter* — agent-sized micro-issues would spam a team's board. A tracker
    adapter remains possible later as an issue-file *source*, with the human approval step intact.
- **Sizing rubric: one vertical slice that fits one implement pass.** An issue is one coherent,
  PR-able, end-to-end slice with named acceptance tests, no overlap in files or migrations with
  other issues in flight, and small enough to implement within the per-pass timeout (default
  900s). Oversized work is split at issue-writing time, not at dispatch time. This rubric is what
  the rest of the pipeline assumes; violating it is the most common cause of a stalled run.
- **One run per invocation.** Parallel waves are multiple backgrounded invocations, guarded by
  the per-branch lockfiles above. *Rejected: a `spec:branch` pair syntax* — it preserved
  one-invocation waves at the cost of a more complicated CLI for a workflow that doesn't need it.
- **Branches are user-owned and mandatory.** `--branch <name>` names THE working branch: the
  agent's checkout forks from its tip, the host pushes results back to it, and the draft PR goes
  from it into `--base`. The branch must already exist — creating branches is the developer's
  act. The one carve-out is the interactive picker, which may offer to create a missing branch
  behind an explicit y/N confirm; the flag path stays a hard error so scripts never create
  branches silently.
- **`--base` is not a fork point.** It has three jobs: the PR target, the diff boundary for the
  review and PR-description passes (computed as `merge-base(branch, base)`), and the reset target
  for `--hard-reset`. It is never pushed to and never forked from.
- **Iteration continues on the branch by default.** Re-dispatch forks from the current branch tip
  and appends commits; no force-push. `--hard-reset` restores reset-and-redo semantics with a
  force-with-lease push. The PR body is regenerated from the full diff on every landing; an
  existing open PR is updated, never duplicated.
- **Feature-run mode: one run = (selected issues, branch) = one PR.** Selected issues run in
  dependency order in ONE container, implement + review per issue, commits accumulating, PR body
  updated after each issue with a per-issue checklist. Guard: every selected issue's `Blocked by:`
  must be Done or selected earlier in the same run. **Stop rule: stop and land what's done** — a
  needs-work verdict, a failing issue, or a HITL-typed next issue stops the sequence, completed
  work is pushed, and the PR records the stop reason. Per-issue outcomes are recorded to the
  issue files' `Status:` lines **host-side**, because issue files live outside the agent's
  checkout by design.
  - *Rejected: skip-and-continue over independent issues* — later slices silently assume the
    skipped slice's behavior.
  - *Rejected: all-or-nothing landing* — it throws away completed, reviewed work.
- **PR granularity is composed across runs.** Re-running the remaining issues on the same branch
  continues that PR; giving a different branch opens a second one. The developer decides how a
  ticket's slices split into PRs, run by run. Stacking uses existing levers only: branch from the
  prior branch and pass it as `--base`.
- **Review is two layers**: the in-container reviewer with a verdict-break loop (cap 3), then the
  human on the draft PR. No second-model or host-side pre-PR review layer. *Rejected: fixed-N
  blind review passes* — wasteful on clean diffs and insufficient on dirty ones, which is the
  whole reason the verdict breaks the loop.
- **Interactive steps appear only when stdin is a TTY.** Backgrounded and scripted runs take
  defaults silently and log the choice — a backgrounded run must never block on a prompt.
- **Egress is open, with the zero-cost hardenings applied** (`--no-hardlinks` clone, `--cap-drop
  ALL`, pids/memory limits). **The original revisit trigger for this decision has now fired and
  it is being consciously re-accepted, not inherited by default.** Open egress was first accepted
  at solo scope — one developer, one machine — with "revisit if this is shared beyond one user's
  machine" written down as the condition. Outside teams adopting bessemer meets that condition
  exactly. Re-evaluated at adopter scope on 2026-07-24, the answer does not change: an allowlist
  proxy would break dependency installs (package registries fetch binaries from third-party
  hosts), pre-commit hook-environment rebuilds, and documentation lookup during a run — and
  proxy misses surface as AFK timeouts, the worst debugging position in the pipeline, on
  machines whose stacks the core deliberately knows nothing about. Meanwhile the channel that
  actually matters for supply-chain attacks, install-time script execution, stays open either
  way. What holds the risk down is not the network boundary but the credential boundary: the
  container has nothing worth exfiltrating.
  That reasoning is contingent, so the refined triggers are binding on **adopters**, not just on
  this repo. Restrict egress, or don't adopt as-is, if: a real secret (anything outside the
  LLM-API credential class) enters the container; specs stop being self-authored; or a shared or
  central runner appears, since a multi-tenant runner breaks the "nothing worth stealing"
  premise entirely. Network posture is deliberately **not** a config knob today — no adopter has
  asked for one, and shipping an untested `proxy` mode would be worse than shipping none.
- **The ledger is derived state.** Git is the source of truth; a stale or deleted ledger degrades
  defaults, never correctness.

### What bessemer requires of an adopting repo

The core assumes nothing about language or test tooling, but the adapter it scaffolds has to
satisfy a small contract. Recording it here because each clause was learned the expensive way:

- **The test suite must run with no external services**, or the setup hook must start them. A
  headless run has no broker, no queue worker, and no live app; a suite that quietly depends on
  one hangs until the pass times out.
- **The suite should be architecturally blocked from real outbound HTTP** (mocked at the
  transport layer, not per-call), and the container's environment file should hold dummy but
  type-correct values. This is what makes "no real secrets in the container" a structural
  property rather than a discipline.
- **Dependency installation belongs in the setup hook, not baked into the image.** A bind-mounted
  checkout shadows a baked `node_modules`, and a baked layer goes stale against the checkout's
  own lockfile. This is the main reason the setup hook exists. The install runs unconditionally — a
  nominally backend change may still touch a frontend file and trigger its pre-commit hooks — with
  a shared named docker volume as the package cache, so only the first run is cold.
- **The image must build with a UID argument** so the in-container agent user matches the host
  owner of the bind-mounted checkout. **macOS Docker Desktop masks a mismatch; Linux does not** —
  which means this breaks for the first Linux adopter and nobody else.
- **The throwaway database's version may differ from production.** Fine for suites that build the
  schema from model state, but flag it if version-specific SQL enters the codebase.

### Deliberately left open

- **The provider-adapter contract** is designed with its first real non-Claude consumer, not
  guessed at now — the same trap that deferring this extraction avoided. Scoping revealed the
  likely consumers reach codex through Cursor, whose headless story is unknown here, so building
  a contract now would mean guessing for users whose usage is unobserved. The shape agreed
  **if/when built**, recorded so it isn't re-derived: per-agent adapter files with a fixed
  contract of preflight, invoke-with-prompt-on-stdin, live-log filtering, and final-text capture,
  plus a per-agent image layer; API-key-only auth injected at container start; and the credential
  staying inside the permitted class — able to spend money, unable to touch git, repo hosting, or
  infrastructure. Any credential outside that class is an egress revisit trigger.
- **Internal module boundaries** beyond the F1 skeleton. (F1's own are settled in
  [ADR 0002](0002-skeleton-structure.md).)
- **The dashboard frontend** (Textual vs stdlib-http localhost web over the ops library), decided
  if and when the parallel-run-pain trigger fires.

### Where mechanism detail lives

This ADR records decisions and rejected alternatives. As-built mechanism — the picker's step
sequence and degradation behavior, ledger default chains, log layout, gc scan rules — is
specified by the pinned port source and belongs in an operational reference, which lands with the
onboarding docs at F6. It is deferred, not dropped.

## Consequences

- hae keeps running its incubated `.agentbox/` unchanged through the port. The switchover is a
  deliberate, reversible install of bessemer once the parallel-run gates pass, and it renames
  hae's adapter directory to `.bessemer/`.
- **Parallel-run acceptance gate** (single current user, so deliberately lightweight): read-only
  commands (`status`, `doctor`, `gc` list, `--dry-run`) are validated side-by-side any time; the
  dispatch gate is one feature-mode multi-issue run, one one-off, one resume-with-feedback, and
  one failure path handled correctly. The bar is **outcome parity** — branch, PR, ledger, and
  Status writes — not byte-identical logs. The old core stays in hae until all four land clean.
- Adopter onboarding cost: install uv (one curl), run `bessemer init`, edit the scaffolded
  Dockerfile and setup hook. No pip, no venv, no interpreter management.
- Building bessemer with bessemer starts at F3: F1–F3 are built interactively because the tool
  cannot dispatch itself yet, and every feature from F4 on is dispatched through it. This repo
  therefore ships its own minimal adapter (a trivial Dockerfile and a no-op setup hook) from F1.
- What this ADR deliberately leaves open, to be decided as the work reaches them: the internal
  module boundaries beyond the F1 skeleton, the provider-adapter contract (designed with its
  first real non-Claude consumer), and the frontend choice for a dashboard if its trigger fires.
