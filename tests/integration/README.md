# Tier 3 — the tests that need a real daemon

```
make tracer-tests
```

Not part of `make check`, not part of CI, and not runnable on a machine with no Docker. That is
the point of the tier, ruled in the [F3 spec](../../.bessemer/specs/f3-dispatch/README.md),
decision 2:

> **Tier 3 — real docker, separate directory and make target, never under `make check`.**
> `tests/guard.py` stays armed everywhere `make check` reaches; tier 3 lives outside the guarded
> suite rather than as an exemption inside the guard, which would weaken it for the unit suite
> too.

## What each module is for

| Module | The claim it settles |
|---|---|
| `test_image.py` | `AGENT_UID=0`, `00` and a non-numeric UID all fail the build; the built image runs as your UID and carries the programs the adapter-image contract names |
| `test_sudoers.py` | The one sudoers line: the granted command runs as root, a different script does not, an extra flag does not, and `BASH_ENV` does not cross `env_reset` |
| `test_setup_hook.py` | Bessemer's own setup hook, run as dispatch runs it, leaves `uv` where the **agent** user can run it — and a second run installs nothing |
| `test_dispatch_e2e.py` | A whole dispatch whose setup hook exits 1: the run aborts, the log carries the hook's output, and no container, checkout, lock or ledger line is left behind |

Each is the half of a property that reading a file cannot reach. `tests/test_adapter.py` pins the
Dockerfile's *text* and says so in its own docstring — "the image is never built by this suite"
— and tier 3 is where that sentence stops being a limit.

## What it needs, and what it touches

- **A running Docker daemon.** Nothing here skips without one: a missing daemon raises out of
  `setUpModule`, because "0 tests, OK" from a suite whose subject is docker is the
  green-when-lucky result the whole tier structure exists to avoid.
- **`gh` on `PATH`.** Dispatch's preflight refuses without it. It is never invoked.
- **The network.** Building the image installs the agent CLI; the setup hook installs `uv`.
- **An image tagged `bessemer-tracer`**, built by the first module that runs and left behind
  afterwards. Deliberately not `bessemer-agent`, the tag `.bessemer/config.toml` names, so a
  tier-3 run cannot replace the image your own dispatches use.
- **One desktop notification**, on macOS: the end-to-end module drives a real dispatch to a real
  failure, and a failing run notifies. It is the same notification a failed run of your own
  fires.

What it does not touch: this repository. The end-to-end module builds its own repository, its own
origin and its own adapter under a temporary directory. No container outlives its test — the
sudoers probes run under `docker run --rm`, the setup-hook container is removed in
`tearDownClass`, and the dispatched one is removed by the run's own `finally` and again in
`tearDown`. Only that last one carries the `bessemer-` prefix `bessemer status` and `bessemer gc`
scan for, and its absence afterwards is the thing that module asserts.

## Why there is no `__init__.py`

`unittest discover` recurses into a directory only when it is an importable package, so its
absence is what keeps `make check` from collecting these files. It is also what keeps them from
importing `tests/__init__.py`, which arms `tests/guard.py` — the guard denies `docker`
deliberately and permanently, so a tier-3 module imported as `tests.integration.*` would fail
every docker call in a way that reads as the daemon being down.

Both halves are pinned by `tests/test_tiers.py`, in the unit suite, where CI can see them.

## The tracer runbook

The tests above are the AFK-able half of issue 12. The other half is
[`docs/f3-tracer-runbook.md`](../../docs/f3-tracer-runbook.md): the first dogfood dispatch,
run by a human against a real branch with real credentials, collecting evidence a test cannot.
