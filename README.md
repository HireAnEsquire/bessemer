# bessemer

Dispatcher for AFK ("away from keyboard") coding agents.

Per run, bessemer clones your repo from origin, runs a Docker container built from the repo's own
image, runs an agent CLI headless — an implement pass, then a reviewer pass that loops until it
returns a clean verdict — pushes the branch from the host, and opens or updates a **draft** pull
request. You review and merge. Nothing merges itself.

Each container gets its own throwaway database, so concurrent runs cannot collide on migrations
or test databases. That per-run isolation is the reason this tool exists rather than a hosted
alternative.

## Status: under construction

Not yet installable. The tool has been running daily inside another repository for roughly two
months and is being ported here as a standalone, pure-python rewrite.

Current milestone: **F1 (skeleton)** — see [ROADMAP.md](ROADMAP.md) for the full sequence and what
lands when. Adopter installation and onboarding arrive at F6.

## Where the code is coming from

Bessemer is a port of the `.agentbox/` orchestrator built inside
[HireAnEsquire/hae](https://github.com/HireAnEsquire/hae). The port is pinned to one exact
revision:

> **hae commit `e194121f75f4`, on the `agentbox` branch.**

That revision's behavior is the specification. "Port it" means reproduce what that commit does —
not improve on it. Design questions were settled during incubation; reopening one means writing an
ADR, not editing code.

## Documentation

- **[docs/adr/0001-founding-decisions.md](docs/adr/0001-founding-decisions.md)** — what bessemer
  is, every decision that shapes it, every alternative rejected and why, the security invariants,
  and the dispatch semantics. Read this first.
- **[docs/adr/0002-skeleton-structure.md](docs/adr/0002-skeleton-structure.md)** — the module
  boundaries of the skeleton: why config load is pure, why resolvers return values-or-reasons,
  and why one module owns every subprocess.
- **[ROADMAP.md](ROADMAP.md)** — build sequence, what's deliberately deferred, and the triggers
  that would reopen a parked decision.
- **[CONTEXT.md](CONTEXT.md)** — the project's vocabulary, and the ambiguities it deliberately
  resolves.

## Security posture, in brief

The full set of invariants is in ADR 0001. The short version:

- The container holds **no git credentials and no real secrets**. The only credential permitted
  inside is an LLM-API key — one that can spend money but cannot touch git, repo hosting, or
  infrastructure.
- The host **never runs write-side git inside the agent's checkout**. It fetches from the
  checkout and pushes from the main repository.
- **Draft PRs only.** The human is the merge gate.
- Agent instructions come only from files a human wrote or approved. PR review comments are
  never fed back to an agent.

## License

MIT. See [LICENSE](LICENSE).
