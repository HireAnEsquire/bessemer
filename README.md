# bessemer

Dispatcher for AFK ("away from keyboard") coding agents.

Per run, bessemer clones your repo from origin, runs a Docker container built from the repo's own
image, runs an agent CLI headless — an implement pass, then a reviewer pass that loops until it
returns a clean verdict — pushes the branch from the host, and opens or updates a **draft** pull
request. You review and merge. Nothing merges itself.

Each container gets its own throwaway database, so concurrent runs cannot collide on migrations
or test databases. That per-run isolation is the reason this tool exists rather than a hosted
alternative.

## Status: F1 (skeleton) complete

Still under construction — the tool has been running daily inside another repository for roughly
two months and is being ported here as a standalone, pure-python rewrite. Adopter installation and
onboarding arrive at F6; see [ROADMAP.md](ROADMAP.md) for the full sequence and what lands when.

What F1 landed is one command that runs end to end, from a checkout of this repository:

```
uvx --refresh --from . bessemer doctor
```

Six checks — `uv`, `config`, `git-env`, `root`, `base`, `docker` — each reporting `ok`, `WARN` or
`FAIL`, with a hint on anything that needs fixing. Exit 0 only when every check is `ok` or `WARN`;
a check that could not run because an earlier one failed counts as a failure. There is no `pip
install` and no virtualenv to make: uv supplies the interpreter.

Two things the F1 tracer measured, both of which will bite a reader who skips them:

**`--refresh` is not optional while developing.** `uvx` caches the environment it builds for a
local `--from` path under the package's declared name and version, and bessemer's version has been
`0.1.0` for every commit so far. Without `--refresh`, edits are invisible in *both* directions —
the cache will keep serving code you deleted and keep hiding code you added. During F1's tracer
this printed nothing at all and exited 0, because the cached wheel was the one built back when
`doctor` was still a stub. A green exit status from a stale artifact is the one failure a tool
whose job is reporting must not have. Adopters never see it: their `--from` is `git+…@<sha>`, and
a new SHA is a new cache key.

**uv 0.9.0 or newer.** ADR 0001 takes `requires-python = ">=3.14"` to mean the host interpreter is
not the adopter's problem, because uv fetches one. Measured, that holds only if the uv doing the
fetching knows about a *stable* 3.14. uv 0.8.0 does not: the newest 3.14 it can offer is the
`3.14.0b4` prerelease, which `>=3.14` excludes, so it downloads its own default (3.13.5) and then
fails the resolve. Bisected: uv 0.8.0 offers `3.14.0b4`, uv 0.8.17 offers `3.14.0rc2`, uv 0.9.0 is
the first offering a stable `3.14.0`.

**Doctor's `uv` line WARNs below this floor**, naming the version you have and the version wanted.
A warning rather than a failure, and the distinction is the whole point: an old uv installs
bessemer perfectly well on a machine that *already* has a python 3.14, which is why you can be
reading the warning at all. What it tells you is that the next machine — a colleague's, a fresh CI
image, this one after its system python moves — will not be able to install it, and that the error
when that happens will name *python* and read as bessemer demanding something exotic. The fix is
`uv self update`. To check by hand:

```
uv python list --only-downloads | grep 3.14
```

Filter for `<download available>`: `uv python list` mixes downloadable builds with interpreters
already on disk, and reading a system python as uv's offering is a mistake two readers made here
independently.

With a new enough uv this genuinely needs nothing else installed. Measured with `uv 0.12.0`, system
interpreters excluded via `--python-preference only-managed` and the download directed into a
scratch directory: uv fetched CPython 3.14.6 and the report came back green.

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
- **[docs/adr/0003-dispatch-structure.md](docs/adr/0003-dispatch-structure.md)** — the six
  modules dispatch is built from: pure plans, effectful executors, one quotability policy.
- **[docs/adr/0004-run-liveness.md](docs/adr/0004-run-liveness.md)** — what makes a run live,
  and why a container that is still `Up` can be an orphan. Forced by the F3 tracer.
- **[ROADMAP.md](ROADMAP.md)** — build sequence, what's deliberately deferred, and the triggers
  that would reopen a parked decision.
- **[CONTEXT.md](CONTEXT.md)** — the project's vocabulary, and the ambiguities it deliberately
  resolves.

## Development

```
make check
```

That is the only command you need: it runs ruff, mypy `--strict` and the standard hygiene hooks
over every file, then the unit suite. CI runs exactly this, and from F3 so does the agent working
inside a container — one definition of "the checks", so none of the three can drift.

Optionally, `uv run pre-commit install` puts the static half on your commits. The tests stay out
of the commit hook so commits stay fast; `make check` is what has to be green.

The unit suite is documented in [tests/README.md](tests/README.md), including the guard that keeps
it free of Docker, the network, and the ambient git repository.

The tests that genuinely need a Docker daemon live outside that suite, behind their own target:

```
make tracer-tests
```

They build the adapter image, run the sudoers grant and the setup hook inside a real container,
and drive one whole dispatch to a real failure. Not part of `make check` and not part of CI — see
[tests/integration/README.md](tests/integration/README.md) for what they need from a machine, and
[docs/f3-tracer-runbook.md](docs/f3-tracer-runbook.md) for the human-run dogfood beside them.

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
