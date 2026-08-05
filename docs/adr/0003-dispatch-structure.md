# Dispatch structure: six modules, pure plans and effectful executors, one quotability policy

Date: 2026-08-05. Resolves the module boundaries [ADR 0001](0001-founding-decisions.md)
deliberately left open beyond the F1 skeleton, for F3's dispatch. Mirror of
[ADR 0002](0002-skeleton-structure.md)'s role for F1: nothing here reopens an earlier
decision; these are the shapes that fall out of implementing them. The port source's spine
is `.agentbox/run.sh` at pin `e194121f75f4` — 1732 lines of bash with no seams and no
tests; where its logic lands is exactly the question this ADR answers.

The through-line: **a module is deep when a caller — or a test — gets a lot of behaviour
per unit of interface.** The seams below are drawn where a second consumer already exists
(F4's feature loop, gc's deleter, doctor) or where a privilege surface deserves an unmixed
home, and nowhere else.

## Decisions

- **The module map. The interface listed is the whole of it.** Written as six plus a small
  seventh; `stream.py` was added when issue 05 landed it (2026-08-05), and the numeral came
  out of this sentence with it — a count restating a table is two values that can disagree,
  and this one already had.

  | Module | Interface | What hides behind it |
  |---|---|---|
  | `dispatch.py` | `dispatch(...) -> RunOutcome` | Orchestration: the guard sequence, lock acquire/release, log rotation, cleanup ordering (run.sh's trap semantics as try/finally), the step counter, the end notification. The only module the CLI calls. |
  | `checkout.py` | create / `read_branch` / `salvage` / remove | The never-git-inside-the-checkout discipline: `--no-hardlinks` clone, identity config, textual `.git/HEAD` read, FF-only upload-pack fetch. |
  | `container.py` | `start` / `run_setup_hook` / `remove`; the argv builders `run_argv` / `chown_argv` / `setup_hook_argv` / `remove_argv`; `Boundary`, `forwarding` / `Forwarding` / `mount_points` / `SetupHookError` | The whole privilege surface: `--cap-drop ALL` plus the six cap-adds, pids/memory limits, the mount table with `:ro`, explicit `-e` env from declared keys, the root-exec-chown vs agent-sudo distinction (run.sh:1273 vs :1278), the verbatim sudoers invocation string. |
  | `passes.py` | `run_pass(...) -> PassResult`; `review_loop(...) -> Verdict`; the argv builders `pass_argv` / `liveness_argv`; `review_prompt`; `Limits` | Retry ladder, per-pass timeout, stream-filter plumbing, heartbeat, dead-container abort, verdict-token parse, the round cap. |
  | `landing.py` | `land(...) -> Landing` | Push (plain, `-u`, explicit refspec), PR probe/create/update via `gh` with the body on stdin, body composition and footer, the commits-past-boundary gate. |
  | `reclaim.py` | `execute_gc_plan(plan) -> ReclaimReport` | gc `--force`: per-item liveness re-check (container and lock pid), per-class actions, salvage-before-remove via `checkout.salvage`, skip-loudly semantics. |
  | `prompts.py` | `load(name) -> str`; `overridden(adapter_dir)`; `TEMPLATE_NAMES` | Package-default vs `.bessemer/prompts/` override resolution (ADR 0001). Two consumers at F3 already: the three passes, and doctor's override count — `overridden` exists so doctor never restates the override path (added at issue 03, 2026-08-05). |
  | `stream.py` | `filtered(transcript, *, emit) -> Capture`; `brief(inputs)` | The provider's stream-json: the `claude \|/>` rendering and final-text capture, which ADR 0001 names as one surface. Pure — no proc, no filesystem — because F3 README decision 5.1 moved it out of the container, where the pin ran it as `python3 /agentbox/stream-filter.py` (run.sh:1099) on an assumption about the adapter image. Its consumer at F3 is `passes.py`, which renders each attempt's transcript through it and reads the verdict out of the capture (added at issue 05, consumer landed at issue 07, both 2026-08-05). |

- **`checkout.py` and `container.py` stay separate.** *Rejected: one `environment.py`*
  (CONTEXT.md groups them as "the isolated environment") — their failure domains and
  consumers differ: gc's deleter needs `salvage` with no container anywhere in sight, and
  the container's privilege surface deserves a home unmixed with git discipline. Merge
  would trade that separation for one fewer file.

- **`checkout.salvage` is the single definition of a refspec the port source spells three
  times.** At the pin the identical FF-only fetch-from-checkout appears at run.sh:508 (gc
  block), :1177 (cleanup trap) and :1538 (landing). Three spellings of a security-relevant
  refspec are three chances for one to grow a `+`; one function, three callers.

- **`gc.py` stays pure; the deleter is `reclaim.py`.** F2's no-subprocess property in the
  data layer is a property, not an accident — it is what keeps gc's scan-and-plan tests
  pure and fast. The plan is data; `reclaim.py` executes it through the proc seam and
  re-checks liveness per item because the plan is a scan-time snapshot. A future "finisher"
  tempted to fold the deleter into gc.py should read this paragraph first.

- **One definition of "protected branch".** `is_protected` / `PROTECTED_BRANCHES` already
  exist in `bessemer/resume.py`, ported and tested. dispatch's guard sequence calls them —
  never a second `case master|main` (run.sh:930's shape). Two definitions are how a
  dispatch someday pushes to `main` while resume refuses it. If resume.py stops being the
  natural home once dispatch is the main consumer, moving the definition is fine; having
  one is the invariant.

- **One quotability policy, owned by `proc.py`, wrapping `bessemer.redact`.** Three
  modules compose text from `Result`s (landing's PR body, dispatch's notification, passes'
  logging); if each decides locally what is quotable, the stderr invariant is three
  conventions. `proc.py` — which already owns `Result` and its
  stderr-is-credential-bearing docstring — gains the one function answering "what of a
  `Result` may be quoted where", and everything else calls it. It *wraps* the existing
  `bessemer.redact` rather than extending that module: redact.py's contract is to depend
  on nothing in the package, and importing `Result` there would break it. Tier-2 tests
  assert through the policy function; a composition site that bypasses it is the defect.

- **Deliberate non-seams, recorded because a non-seam is a decision too:**
  - *notify* is a private function in `dispatch.py` — one consumer, no variation until
    F4's verbosity config; one adapter is a hypothetical seam. F4 promotes it if the
    config surface justifies.
  - *runlog* (say/banner/step, the `== bessemer ==` banner layout) is an internal class
    in `dispatch.py`, passed to collaborators — an internal seam whose tests ride
    dispatch's. The log *layout* contract with status and gc is pinned by the
    dispatch-writes-where-status-reads round-trip test, not by module placement.
  - *no argv-builder grab-bag module* — builders live in the module whose privilege
    surface they encode.

- **Every effectful module accepts its proc callable; none creates it.** `dispatch.py`
  composes the real adapters; tier-2 tests compose the recording double at the same seam.
  One seam, two adapters — the seam is real. The double is a thin table over the real
  `Result` type, never a parallel implementation of proc semantics.

  *The seam's type is `proc.Runner`, one declaration* (added at issue 04, 2026-08-05). F1
  declared the protocol inside `doctor.py`; `checkout.py` was the second module to need it
  and five more follow, so the declaration moved to `bessemer/proc.py` — where it is the
  shape of that module's own `run` — and `doctor.Runner` became an alias, keeping the
  spelling its checks and tests already use. Structural typing would have made two
  declarations interchangeable and therefore free to drift apart unnoticed, which is the
  failure mode worth one file move.

  *A module that takes a runner takes it as a required keyword with no default.* A default
  of `proc.run` is how a module acquires the real spawner by omission, and a test that
  forgot to pass the double would still go green — against the machine. `doctor.Context`
  keeps its default because it is a dataclass of shared state rather than a call site;
  every F3 module spells it out.

- **The container's privilege surface arrives as one value, `container.Boundary`, and it has
  no defaults** (added at issue 06, 2026-08-05). Five things config decides — cap-adds,
  volumes, the two limits, and the forwarded environment pairs — are needed by both
  `run_argv` and `start`, and passing them as ten keyword arguments across two functions is
  where a call site starts omitting one. The parameter object earns its place by being where
  they are *checked*: constructing it is the only route to an argv, so "a malformed
  `container_cap_add` never reaches a flag" is structural rather than a rule the builder
  remembers. No field has a default, for the reason the runner has none — a `Boundary()`
  standing for "no capabilities, the usual limits" is a privilege decision acquired by
  omission, and the limits' real defaults live in `config.DEFAULTS`, where restating them
  would be two values free to disagree about a security posture.

- **An agent pass runs on a second proc seam, `proc.Streamer`** (added at issue 07,
  2026-08-05). `Runner` describes a child that answers a question inside a deadline, which
  is every child bessemer has except one: a pass takes a prompt on **stdin**, writes a
  transcript for minutes, and has to be rendered into the run log while it runs rather than
  after it ends. `proc.streamed` is that call and `Streamer` is its protocol, declared beside
  `Runner` for the reason `Runner` moved into `proc.py` at issue 04 — one declaration, in
  the module whose own function it is the shape of. Widening `Runner` instead was rejected:
  it would put four parameters no `docker rm` will ever use on every call site in the
  package. **`proc.streamed` deliberately takes no `timeout`**, alone in that module: the
  deadline for a pass is `timeout(1)` *inside* the container, because a host-side kill ends
  the `docker exec` client and leaves the agent running in the container, wedging it for
  every later exec. `tests/test_proc.py::StreamTest` pins the absence, so it cannot be added
  back as a tidying.

- **The pass loop's two config knobs arrive as one value, `passes.Limits`, and it has no
  defaults** (added at issue 07, 2026-08-05). `container.Boundary`'s shape for
  `container.Boundary`'s reason, one module along: `pass_timeout` and `max_review_rounds`
  are the two keys `bessemer.config` deliberately does not coerce — the environment layer
  hands over strings — and constructing `Limits` is the only route to a pass argv or a
  review loop, so "a `max_review_rounds` of `"three"` fails before any container work" is
  structural rather than a check each entry point remembers. The defaults stay in
  `config.DEFAULTS`, where restating them would be two values free to disagree.

- **The environment git children get is one function, `resolve.git_env`** (promoted at
  issue 04, 2026-08-05). It was private to the resolvers, which read; `checkout.salvage`
  **writes**, and an exported `GIT_DIR` would send that write into whatever repository it
  names rather than the main one. Sharing only the variable list and copying the
  comprehension would leave the two consumers one edit from disagreeing about what
  "minus those names" means, so the function moved rather than the constant alone. It stays
  in `resolve.py` rather than `proc.py`: `proc` owns argv and knows nothing about git, and a
  git-specific environment policy in the module that also spawns docker is a rule applied by
  proximity.

## Consequences

- F4's feature loop composes `container` + `passes` + `landing` per issue without new
  seams; that reuse is why the pass loop and the container lifecycle are separate modules
  now rather than refactored apart later under a dispatched agent's hands.
- The unit suite still passes with no Docker daemon, no network, outside any git
  repository (ADR 0002): argv builders are pure, orchestration runs against the double,
  git-behaviour questions use real temporary repositories.
- Six modules with tests against their interfaces make this map expensive to redraw —
  which is why it is an ADR and not a paragraph in a spec file.
