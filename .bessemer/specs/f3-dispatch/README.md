# F3 — dispatch, one-off

The spine: clone, container lifecycle, setup hook invocation, the implement + review loop
with verdict break, host-side push, draft PR open/update, notification, locks and logs —
plus `gc --force` (moved here from F5, decision 1). Every security invariant in
[ADR 0001](../../../docs/adr/0001-founding-decisions.md) lands here as explicit code with
explicit tests.

Scope and sequence come from [ROADMAP.md](../../../ROADMAP.md); the decisions these issues
implement are in ADR 0001 and [ADR 0002](../../../docs/adr/0002-skeleton-structure.md).

**Port source: `/Users/sbowles/hae`, commit `e194121f75f4`.** The spine is
`.agentbox/run.sh`, **1732 lines of bash with no tests** — unlike F2 there is no upstream
oracle. That checkout now sits on a different branch: read the pin via
`git show e194121f75f4:.agentbox/run.sh`, never the working tree.

## Region map of run.sh at the pin

Measured 2026-08-05. Line ranges are the authority issues cite; spot-checked at the flag
blocks and closings (971/995, 1292/1431, 1439/1474, 1647/1724). **The Issue and Pinning
test columns are the spine manifest (decision 2):** the test column is flipped from `—`
host-side as each issue lands, making "a region nobody ported" visible at every review.

| Lines | Region | Home | Issue | Pinning test |
|---|---|---|---|---|
| 1–228 | header doc, config comment | reference only | — | — |
| 230–280 | roots, `.env` source, `have_claude_credential`, `image_staleness` | F3 (staleness is F5's) | 01, 09 | `tests.test_container.CredentialPresenceTest` (part) |
| 282–301 | `status` intercept (docker-rows gathering) | F2 done; debt 3 wiring F3 | 10 | `tests.test_dispatch.RoundTripTest` |
| 303–420 | `doctor` frame | F1, done | — | — |
| 422–523 | `gc --force`: re-check, salvage-fetch, delete | **F3** (decision 1) | 11 | — |
| 525–540 | config defaults: image, base, rounds, timeout, pids, memory, notify | F3 subset (notify verbosity excluded) | 01 | `test_config.SchemaTest` |
| 542–596 | flag parse; feedback-conflict guards | F3 subset of flags | 10 | `tests.test_cli.SurfaceTest` |
| 598–679 | no-arg picker, branch-creation carve-out | F5 | — | — |
| 681–715 | `--last` | F4 | — | — |
| 717–776 | `--resume` recovery wiring | F4 | — | — |
| 779–820 | mode validation, spec path resolution | F3 (spec path only) | 10 | `tests.test_dispatch.GuardTest` |
| 826–832 | `TASK_DIR` recorded fact | F3 | 10 | `tests.test_dispatch.HappyPathTest` |
| 837–873 | resume guard | F4 | — | — |
| 875–888 | `--base` ledger default chain | F3 (decision 4) | 10 | `tests.test_dispatch.BaseChainTest` |
| 890–925 | `--feedback-edit` editor flow | F4 | — | — |
| 927–940 | branch guards: exists, protected, base≠branch, not checked out | F3 | 10 | `tests.test_dispatch.GuardTest` |
| 942–952 | preflight: docker, gh, credential, image presence | F3 | 09, 10 | `tests.test_doctor`, `tests.test_dispatch.GuardTest` |
| 954–1002 | fetch, `BASE_SHA`, merge-base; `--hard-reset` block | F3 / hard-reset F4 | 10 | `tests.test_dispatch.HappyPathTest` (part) |
| 1004–1021 | `check_no_inflight_run` (lock pid + container name) | F3 | 10 | `tests.test_dispatch.RefusedDispatchTest` |
| 1028–1130 | run_task helpers: say/banner/step, notify, `claude_pass` | F3 | 07, 10 | `tests.test_passes`, `tests.test_dispatch` |
| 1132–1190 | slug/lock/log derivation, stale cleanup, cleanup trap | F3 | 04, 10 | `tests.test_checkout`, `tests.test_dispatch` |
| 1203–1282 | checkout clone, container run, setup hook invocation | F3 | 04, 06 | `tests.test_checkout`, `tests.test_container` |
| 1292–1431 | feature loop, per-issue `Status:` writes, checklist merge | F4 | — | — |
| 1432–1492 | single-pass implement (feedback-only branches: F4) | F3 | 03, 07, 10 | `tests.test_passes`, `tests.test_dispatch.PromptTest` |
| 1494–1527 | review loop, verdict break | F3 | 07 | `tests.test_passes` |
| 1529–1592 | push, PR-description pass, draft PR open/update | F3 | 08 | `tests.test_landing` (part) |
| 1594–1644 | end notification, ledger append | F3 | 10 | `tests.test_dispatch.HappyPathTest` |
| 1647–1724 | `--dry-run` plan | F5 | — | — |
| 1726–1732 | dispatch entry | F3 | 10 | `tests.test_cli`, `tests.test_dispatch` |

(The host-side stream filter — decision 5.1's divergence — has no run.sh row; its oracle
is `.agentbox/stream-filter.py` at the pin, owned by issue 05 — landed 2026-08-05, pinned
by `tests.test_stream`. Its parity evidence is unlike every other pin here: upstream's own
filter was run host-side over four real stream-json transcripts and its bytes committed
beside them (`tests/fixtures/stream/`), so "log lines identical" is a byte comparison
rather than a reading. One divergence recorded in the module docstring beyond the
host-side move itself: a line that is valid JSON but not an object crashes the oracle
(measured), and takes the malformed-line arm here instead. Likewise the three prompt
templates: oracles are `.agentbox/{implement,review,pr}-prompt.md` at the pin, owned by
issue 03 — landed 2026-08-05, pinned by `tests.test_prompts` (sections, deltas, retired-word
absence). The `1432–1492` row's test column stays open until 07 and 10 land their parts.

Issue 04 landed 2026-08-05 and its rows were marked **(part)** — the manifest's job is to
show what nobody ported, so a row half-covered has to say so rather than read as done.
`tests.test_checkout` pins `:1161–1162`'s removal, `:1169–1190`'s salvage fetch, and
`:1203–1209`'s clone plus identity writes. Issue 06 landed the rest of `1203–1282` the same
day — the container run, the chown exec and the setup-hook invocation, pinned by
`tests.test_container` — so that row is no longer part-covered; `1132–1190` still is, with
slug/lock/log derivation and the trap wiring open to issue 10.

Issue 07 landed 2026-08-05: `bessemer/passes.py` pins `:1494–1527` whole — the round cap, the
`<verdict>approved</verdict>` break and both pull-request footers — and the pass mechanism out
of `:1090–1130`, which is why `1028–1130` is **(part)**: say/banner/step and notify are issue
10's half of that region and nothing pins them yet. `1432–1492` is **(part)** for the mirror
reason — the implement *pass* is `run_pass`, and the dispatcher's generated preamble around it
is still issue 10's. Two things arrived with it and are recorded in
[ADR 0003](../../../docs/adr/0003-dispatch-structure.md) rather than here: a second proc seam
(`proc.Streamer`, for the one child that takes a prompt on stdin and is rendered while it
runs, and which deliberately has no host-side timeout), and `passes.Limits`, the parameter
object that makes a non-numeric `pass_timeout` fail before any container work.

Issue 08 landed 2026-08-06: `bessemer/landing.py` pins the push, the commits-past-boundary
gate, the gh probe/edit/create and the body of `:1529–1592`, which is **(part)** for one
reason — the PR-description *pass* in the middle of that region is `passes.run_pass` driven by
issue 10, and landing takes its text as a value (decision 8.2's seam, and the one F4's feature
loop reuses). Two riders discharged: the four sentences are hand-written literals on both
sides, and the absences are asserted over every recorded argv of every path — no `--force*`,
no `pr merge`, `--draft` on every create. One thing arrived with it and is recorded in
[ADR 0003](../../../docs/adr/0003-dispatch-structure.md) rather than here: `proc.run` and
`proc.Runner` grew `stdin_text`, because the body reaches gh on a pipe and never as an
argument, and gh answers a question rather than streaming.

Issue 09 landed 2026-08-06: doctor grew the checks F3 earned — the credential, one check per
committed-only key, the prompt-override count, `gh` and the image — so `:942–952`'s preflight
questions are all answered host-side and pinned, **(part)** because refusing on them is issue
10's half. `:344–346`'s `have_claude_credential` is ported as
`container.credential_presence`, one definition with two callers, and `:230's` `.env` source is
covered by it in the sense the pin used it (the file's names, read rather than exported);
`image_staleness` stays F5's and doctor says nothing about it.

**One divergence from the issue's own wording, measured rather than argued.** The issue said
doctor's credential check reads the environment, which the pin could say safely because it
sourced `.env` into its own environment first. Bessemer's container does not: `forwarding`
reads the gitignored `.env` and nothing else, so a credential that exists only in the
operator's shell never crosses. Read as the issue wrote it, doctor would FAIL the canonical
setup (ADR 0001: "secrets stay in a gitignored `.bessemer/.env` only") and pass a machine whose
runs cannot authenticate — both backwards. So the resolver answers about **both channels
separately**, doctor FAILs the exported-only one naming the file as the fix, and
`Credential.present` keeps the pin's union available for issue 10 to refuse on deliberately.
That closes `bessemer/container.py`'s residual 2 rather than deferring it.

Issue 10 landed 2026-08-06: `bessemer/dispatch.py` and the CLI's `run` subcommand close every
row above that was still open or part-covered — the flag parse, spec resolution, the base
chain, the four branch guards, the preflight's refusals, fetch/`BASE_SHA`/merge-base, the
in-flight guard, say/banner/step and notify, slug/lock/log derivation and the cleanup trap, the
dispatcher's generated preamble, the end notification and the ledger append, and the entry.
`954–1002` stays **(part)** for one reason and it is not an omission: the `--hard-reset` block
inside it is F4's, named in decision 1.

Five things arrived with it, recorded here rather than in the ADR because they are the port's
divergences rather than the package's shape:

1. **The description pass runs before the push, and runs unconditionally.** The pin pushes,
   then generates the description, then opens the pull request (`:1548`, `:1572`); decision
   8.2 made `landing.land` take the description as a *value*, so the pass has to produce its
   text first. It therefore also runs on a run that will have nothing to push, where the pin
   skips it inside the `[ "$commits" -gt 0 ]` arm. The alternative is a second commit count in
   `dispatch.py`, free to disagree with the one `landing` owns as its gate — a worse trade for
   one wasted pass on a rare path.
2. **The setup step's label is not the pin's.** "settings templates + postgres + client deps"
   names one adapter's three jobs, and core runs exactly one setup command: the hook.
3. **`FAILED during step N/6` drops the pin's `(exit $rc)`.** Bash's trap reads `$?`; the
   Python equivalent is the exception being unwound, whose own text is the line above it in the
   same log.
4. **Stale cleanup moved inside the `try`.** The pin arms its trap last because `cleanup` reads
   variables that are not set until then; Python has no such constraint, and a stale removal
   that fails must release the lock it just took rather than leak it. Decision 6.1's *order* is
   otherwise unchanged, and the refused-dispatch test asserts it on both channels.
5. **An exported-only credential is a loud warning, not a second refusal.** Preflight refuses
   on `Credential.present`, which is the pin's union, exactly as this README's issue-09 note
   left available "for issue 10 to refuse on deliberately". The machine whose credential never
   crosses is one `doctor` already FAILs by name, so dispatch says so on the console and in the
   run log instead of inventing a refusal the pin does not have.

6. **Three small ones, recorded so they are not found later as bugs.** The failure
   notification keeps the pin's `— see log` and drops only `(exit $rc)`, for (3)'s reason. The
   `cleanup` banner lands *below* the failure line where the pin prints it above, because
   Python runs `except` before `finally` and there is one of each. And the entry banner
   truncates the base sha it already holds instead of spawning the pin's second
   `git rev-parse --short` (`:1730`) — a spawn for a display string is a spawn that can fail,
   and the number is longer rather than different.

**And the ruling issue 10 owed** (acceptance criterion 7): the implement prompt's SPEC section
names `/spec.md` and the dispatcher's generated preamble (`:1476–1477`) repeats it — **kept**.
hae's F7 overrides restore its template text byte for byte and the parity gate compares
*assembled* prompts, so dropping the repetition would make every F3 prompt differ from the
pin's for a tidiness nobody asked for. `tests.test_dispatch.PromptTest` holds it, so the
duplication is a decision with a test on it rather than an oversight.

`:1210–1212`'s `client/node_modules` pre-create was recorded here at issue 04 as hae adapter
content, excised and owed no test. **Corrected at issue 06, 2026-08-05:** the *path* is hae's
and stays excised, but the mechanism is not — a mountpoint inside the checkout has to exist,
host-owned, before the container is created, or docker's copy-on-first-use hands the
anonymous volume to root. `container.start` derives those directories from
`container_volumes` and pre-creates them, which is decision 5.3's chown generalisation
arriving one step earlier, and `tests.test_container.StartTest` pins the ordering.)

## Decisions

1. **What one-off dispatch is not.** F4's resume family and F5's picker are adjacent in the
   same file; this list is the boundary, in the same spirit as F2's decision 1.

   **Excluded, to F4:**
   - `--feature` / `--issues` and the whole feature loop (`run.sh:1292–1431`), including
     per-issue host-side `Status:` writes and the PR checklist merge.
   - `--resume`, `--last`, the resume guard, and feedback-only run mode
     (`run.sh:681–873`, `1439–1474`).
   - **The entire feedback family, `--feedback` included.** Feedback threads one mechanism
     through the implement, review, and PR-description prompts; splitting it would land
     half a mechanism. F3's prompts carry no feedback section, and F3's tracer (a one-off
     spec on itself) needs none.
   - `--hard-reset` (`run.sh:971–995`) — continue-vs-redo iteration semantics, same family
     as resume. F3 dispatch is continue-mode only: fork from branch tip, plain push, never
     force. (`merge-base` diff-boundary computation stays in F3 — the review and PR passes
     need it on the first run.)
   - Notification verbosity `off|end|steps` — ROADMAP assigns it to F4. F3 fires one
     end-of-run notification, unconditional, no config key.

   **Excluded, to F5:**
   - The no-arg picker (`run.sh:598–679`) and its branch-creation carve-out.
   - `--dry-run` (`run.sh:1647–1724`).
   - `image_staleness` (`run.sh:261–280`) — its inputs (`api/requirements*.txt`,
     `client/yarn.lock`) are hae's; an adapter-generic staleness contract is a design
     question F3 does not need. F3's preflight checks image **presence** only.
   - F2 debtor entry 1 (`_first_free_branch_name`'s two git predicates) — its only
     upstream callers are picker call sites (`tasklib.py:1757`, `:2148`); ruled
     2026-08-05, correction note recorded in F2 README decision 9.

   **Excluded, to F4 (debtor reassignment):** F2 debtor entry 2 (`ResumeInfo.source_dir`
   never asserted) — its consumer is resume dispatch. Same dated correction note.

   **Pulled in, from F5:** `gc --force` (`run.sh:422–523`). Ruled 2026-08-05: decision 9's
   suite-gap pins were written as prerequisites of an F3 issue, `bessemer/gc.py`'s
   docstring already said "force is F3's", and dogfooding starts at F3 — a leaked artifact
   with no reclaim path means hand-cleanup of credential-adjacent state. ROADMAP's F5 line
   corrected to match.

   **So F3 discharges F2 debts 3 and 4 only:** the dispatch-writes-where-status-reads
   round-trip (`.bessemer/logs`, `.bessemer/locks`, `bessemer-<slug>` containers, ledger
   via `append_ledger`), and the two gc pins — no test asserts a stopped container's
   checkout is still an orphan, and `render_gc_plan`'s class filter is never exercised
   alone — before any `rm -rf` trusts the plan. Decision 9's no-defaults rule (a git
   question is a parameter dispatch must answer, never a default) binds every debt
   wherever it lands.

2. **The oracle substitute: three test tiers, and a spine manifest.** run.sh is untested
   bash — F2's drift control (a ported suite) does not exist here; every F3 test is
   bessemer's own. Sanctioned 2026-08-05:

   **Tier 1 — pure functions, plain unit tests.** Slug derivation (`run.sh:1134`), prompt
   assembly (host-side from files + generated lines; agent input rides stdin, `:1098`),
   verdict parsing, guard decision tables, PR-body composition, path derivation, docker
   argv *construction* (pure builders returning `list[str]`). Git questions are parameters
   per F2 decision 9 — no defaults.

   **Tier 2 — orchestration against a scripted double at the proc seam.** `bessemer/proc.py`
   is the single boundary (ADR 0002), so the recorded argv stream IS dispatch's external
   effect surface — asserting it is not mocking an assumption. Whole dispatches driven
   through the double; fidelity and security tests live here. Binding riders:

   1. *Security argv assertions pin hand-written literals* (F1's rule, full force). The
      six cap-adds — `CHOWN, DAC_OVERRIDE, FOWNER, SETUID, SETGID, KILL`
      (`run.sh:1251–1252`) — as a literal list, not "contains `--cap-drop`". Variable
      parts templated; flag set exact. Absence asserted alongside presence: no `-p`/
      `--publish`, no wholesale host env, `:ro` on spec and setup-hook mounts,
      `--no-hardlinks` on clone (`:1204`), FF-only salvage refspec (no `+`).
   2. *The double records cwd* — otherwise the "no git argv inside the checkout" check
      cannot exist. The double is a thin table over the real `Result` type, never a
      parallel implementation of proc semantics.
   3. *The stderr invariant is a tier-2 assertion*: stderr content never appears in PR-body
      composition, notification text, or any assembled prompt. Most easily weakened
      invariant in ADR 0001; here it is a check over the recorded stream plus the
      composed strings.
   4. *Debt 3 (dispatch-writes-where-status-reads) is tier 2, not tier 3*: logs, locks,
      ledger are real file writes needing no docker — fake the proc seam, write real
      files in a tmp tree, render status over them. The tracer re-proves it live.
   5. Where the question is *git's* behavior — salvage FF vs diverged, textual
      `.git/HEAD` read, checked-out-branch refusal — real temporary git repos (F1 issue
      05 precedent). Still no docker, no network.

   **Tier 3 — real docker, separate directory and make target, never under `make check`.**
   `tests/guard.py` stays armed everywhere `make check` reaches; tier 3 lives outside the
   guarded suite rather than as an exemption inside the guard, which would weaken it for
   the unit suite too. Contents, small by design: the tracer; the sudoers exact-match
   test pinning *both* measured facts (different-script refusal, and `BASH_ENV` stripped
   by `env_reset`); `AGENT_UID=0` build refusal (F1-07 owns it — verify it still runs);
   one real end-to-end failure path.

   **The spine manifest — option (b) light.** A committed spec artifact, not a test: the
   region map above grows two columns — owning issue, pinning test — flipped host-side as
   issues land, making "a region nobody ported" visible at each review. It does not
   replace ADR 0001's parallel-run gate, which remains the end-state fidelity check; the
   manifest's job is narrower and honest — no automated oracle is possible against a
   foreign untested bash file, and pretending otherwise would be decoration.

3. **Module map is [ADR 0003](../../../docs/adr/0003-dispatch-structure.md).** Six modules
   (`dispatch`, `checkout`, `container`, `passes`, `landing`, `reclaim`) plus `prompts`;
   checkout/container deliberately separate; `gc.py` stays pure with `reclaim.py` as the
   effectful executor; notify and runlog are deliberate non-seams. Three riders settled
   with it, binding on issues:

   - **Protected-branch guard reuses `resume.is_protected`** — never a second
     `case master|main`. The dispatch issue names the reuse.
   - **The stderr-quotability policy is one function in `proc.py`**, wrapping the existing
     `bessemer.redact` (whose no-package-imports contract rules it out as the home).
     Landing, notification, and pass logging all quote through it; tier 2 asserts through
     it.
   - **`checkout.salvage` consolidates a refspec spelled three times at the pin**
     (run.sh:508, :1177, :1538). The checkout issue records the consolidation and pins
     FF-only (no `+`) with a literal.

   Glossary grew **Landing** and **Slug** (CONTEXT.md, 2026-08-05).

4. **The CLI surface: `bessemer run <spec> --branch <name> [--base <ref>]`.** A subcommand,
   never a bare positional — upstream's top-level positional only worked via intercept
   order, which is the ambiguity dodged, not solved (a spec named `status.md` dispatched
   bare). The subcommand is **`run`**, not `dispatch`: surface naming is CLI ergonomics,
   not vocabulary — `bessemer run spec.md` reads as "run this spec", the same verb-noun
   pun as `git commit`. **Dispatch** stays the glossary term and `dispatch.py` stays the
   module name; `run` is not on Dispatch's avoid list.

   - Flag set, complete at F3: positional `<spec>`, `--branch` (required), `--base`
     (optional). Nothing else. `gc` gains `--force` on its existing parser.
   - Bare `bessemer` with no subcommand: on a TTY this becomes the picker at F5 (where
     upstream's one-word daily flow returns); with stdin not a TTY it prints help and
     exits nonzero, per ADR 0001's interactive-only-on-TTY rule. **F3 wires the non-TTY
     half only.**
   - `SurfaceTest`'s hand-written pin grows `{doctor, status, gc}` → `+ run` — the same
     deliberate-second-file-edit discipline as F1, and the only loosening F3 makes.

   **Spec resolution: reuse + one guard.** Dispatch calls `issues.spec_check_path`
   (`bessemer/issues.py:366`, 8 tests ported from upstream's `ResolveSpecTests`); the only
   new code is the existence hard-error — `!! spec not found: <path>`, oracle run.sh:818.
   The owning issue says "reuse + add guard", never "new resolution code", and names two
   docstring duties: update `spec_check_path`'s sentence "points at nothing in this tree
   today" (dispatch is now the live referent — code edit, implementer-legal), and do NOT
   mistake `_write_ad_hoc_prompt`'s "binds F3's dispatcher too" ordering constraint for an
   F3 duty — that is a picker-era typed-prompt function F3 one-off never calls.

   **Base default chain, port-faithful:** `--base` flag > **ledger-last-base(branch)** >
   `BESSEMER_BASE` env > local config > committed config > `origin/HEAD` auto-detect.
   Verified at the pin: `AGENTBOX_IMAGE`-style env seeds `BASE` at run.sh:526 *without*
   setting `BASE_EXPLICIT`; only the `--base` flag (:563) sets it, so the ledger consult
   at :880–883 overrides an env-configured default. **Named consequence, accepted:**
   `BESSEMER_BASE=... bessemer run ...` silently loses to the branch's recorded history;
   mitigation is the ported log line ("--base omitted — using '<branch>' last recorded
   base: X"), so the choice is visible on every run. The parity argument seals it: the
   parallel-run gate (ADR 0001) would flag a divergent chain as an outcome mismatch, so
   diverging here means building a known parity failure. Defect 10 (file-order vs
   timestamp-order "newest") is inherited, not fixed, per F2 decision 8. *F5 note,
   recorded so the picker round doesn't rediscover it: the picker's base step also sets
   `BASE_EXPLICIT` (run.sh:620).*

5. **The container surface generalizes; hae's specifics become adapter facts.** Five
   rulings, 2026-08-05:

   1. **Stream filter runs host-side — deliberate divergence from the pin.** Upstream
      pipes stream-json through `python3 /agentbox/stream-filter.py` *inside* the
      container (run.sh:1099), assuming python3 in every adapter image — which fails
      ADR 0001's assume-nothing-about-stacks constraint for the PHP/Node adopters it was
      written for. Core filters the `docker exec` stream host-side with a pure `stream_filter`
      function (tier 1). Parity: log lines identical — **a claim held by a test, not
      asserted**: capture a real stream-json transcript as a fixture and pin the rendered
      "claude |/>" lines byte-for-byte against upstream's filter output. The host-side
      function also inherits **final-text capture**: ADR 0001 names "live-log filtering
      and final-text capture" as one provider-contract surface; splitting them across
      host/container would fight that abstraction later.
   2. **`container_cap_add`: committed-layer-only, default empty.** `--cap-drop ALL`
      always, unconditional, core-owned. The pin's six cap-adds are hae's adapter facts,
      committed by hae at F7. Same rule, same doctor FAIL, same reason as
      `container_env_keys` (ADR 0001) — cited, not restated. Tier-2 literal pin:
      `--cap-drop ALL` always in argv; cap-adds exactly the committed list.
   3. **`container_volumes`: named volumes only, committed-layer-only.** Source must not
      begin with `/` or `.` (no host binds through config; anonymous `"/path"` form
      allowed — it names no host resource). *Sharpened by issue 01's implementation and
      ratified at its review (2026-08-05): the source is matched against docker's
      volume-name pattern (`[a-zA-Z0-9][a-zA-Z0-9_.-]*`), not merely checked for a leading
      character — docker reads any source containing a `/` as a path, so `sub/dir:/y`
      would pass the leading-character rule and arrive at issue 06's mount table as a host
      bind. The stricter rule is the decision; do not "fix" the code back to this
      paragraph's first sentence.* Committed-layer-only with the same doctor
      FAIL as the other two container keys (ruled 2026-08-05 with the issue breakdown —
      it is a mount boundary, same rule, same reason). Core chowns each entry's
      mountpoint to the agent user (generalizing run.sh:1273). **Coupling rule, fixed here:** upstream's
      chown consumed `CHOWN`/`DAC_OVERRIDE`/`FOWNER` from the six — the "only what
      setup-db.sh needs" comment undercounted — so with volumes non-empty, core adds
      exactly the caps *its own* chown needs, automatically; a documented core-owned
      implication, pinned by a tier-2 literal both ways (volumes present → those caps in
      argv; absent → absent). The adapter's `container_cap_add` stays purely adapter
      facts.
   4. **The committed container env file is `.bessemer/container.env`** (upstream's
      `.env.sandbox` renamed — "sandbox" is on the glossary avoid list). Bulk transfer
      from it is permitted (committed = reviewable). Gitignored `.bessemer/.env` forwards
      declared keys plus built-in credential names only, each as explicit `-e`;
      unforwarded-key warning names the key, never the value, never into container
      log/PR/notification — ADR 0001's rule verbatim.
   5. **Claude-tuning env (`BASH_DEFAULT_TIMEOUT_MS`, `BASH_MAX_TIMEOUT_MS`,
      run.sh:1261) stays core-owned**, documented, revisited when the provider
      abstraction lands — recorded beside divergence (1), both provider-contract surface.

   Confirmed alongside: the setup-hook mount is core-fixed at the verbatim sudoers path
   (`.bessemer/setup.sh` ro at `/bessemer/setup.sh` — ADR 0001 settled it, F3
   implements); the settings-template `cp` lines (run.sh:1276–1277) are hae adapter
   content that moves into hae's setup hook at F7 — **core runs exactly one setup
   command: the hook.**

6. **Run lifecycle in `dispatch.py`.** Ruled 2026-08-05:

   1. **Step order, port-faithful:** spec guard → branch guards (exists → protected via
      `resume.is_protected` → base≠branch → not-checked-out) → preflight (docker, gh,
      credential presence, image presence) → mkdir logs/checkouts/locks →
      `git fetch origin` → `BASE_SHA` → merge-base → inflight guard → lock → log rotate →
      stale cleanup → arm cleanup → clone → container → hook → passes → landing → notify →
      ledger. **Nothing is touched before the inflight guard passes** (run.sh:1144–1148),
      and the tier-2 refused-dispatch scenario asserts the absence on *both channels*:
      recorded proc stream empty from the guard onward, AND tmp tree byte-identical (no
      lock, no rotation, no log write) — argv alone misses file writes. `git fetch
      origin` stays (a stale `origin/*` base is a wrong diff boundary); stale cleanup
      (`docker rm -fv` + `rm -rf`, :1161–1162) sits after lock, before clone.
   2. **Trap semantics become try/finally + signal conversion.** SIGINT/SIGTERM handlers
      raise, so one `finally` does upstream's `cleanup()`: `checkout.salvage` (FF-only;
      non-FF keeps the checkout with the loud inspect-manually message) → `docker rm
      -fv` → lock removal → failure notification when exiting non-zero, **composed
      through the stderr-quotability policy — its highest-risk call site.** Scope,
      stated so nobody tests the untestable: "a crashed run leaks nothing" covers
      catchable exits; SIGKILL leftovers are exactly what gc/reclaim exist for.
      *Divergence-that-fixes, recorded for the parity reviewer:* upstream's own comment
      (run.sh:1025–1029) documents a live-observed bash defect — the EXIT trap silently
      never fires when a backgrounded `run_task &` subshell exits normally. Python's
      `finally` closes that class entirely; a leaked-container difference in bash's
      favor at the parity gate is the pin's bug, not the port's.
   3. **Lock: pid file, liveness via `status.pid_alive` — and one inherited defect,
      fixed with record.** Upstream is guard-then-write (:1148 check, :1150 write): a
      TOCTOU window where two same-branch dispatches both pass and both proceed. F3 uses
      atomic exclusive create (`O_EXCL`; on failure re-read the pid and report the live
      run) — recorded divergence; the parity gate is outcome-based and a race window is
      not an outcome. Log rotation single-generation, `.log → .log.1`, only when
      non-empty.
   4. **A hard-failed run appends no ledger line** — the append is `run_task`'s last
      act; failure aborts before it. Ledger records landings only. Named consequence,
      upstream's shape too: F4's `--resume` cannot recover a run that never landed.
   5. **Defect 9 armor:** every record field `str()`-converted before `append_ledger`;
      a tier-2 test dispatches with `Path`-typed inputs end-to-end. The defect stays in
      the ledger module per F2 decision 8.
   6. **One-off failure semantics, one scripted tier-2 scenario each:** hook nonzero →
      abort + surface log; implement fails after 3 attempts → run fails, salvage, no PR,
      no ledger; review capped at needs-work → **still lands** — push + draft PR,
      `verdict_token="needs-work"`, footer pinned as a literal: "⚠️ Review: needs-work
      after N round(s) — read the task log before reviewing." (:1497–1498) — a reworded
      footer is a changed contract; container dies mid-pass → abort, never retry into a
      dead container.

7. **Passes and prompts.** Ruled 2026-08-05:

   1. **Package-default prompts go stack-agnostic; hae's text becomes its overrides at
      F7.** The pinned prompts are saturated with hae (ORIENTATION's Django/React,
      VERIFY's `manage.py test`/pre-commit, pr-prompt's hae URLs). F3 ports the section
      *structure*, excising repo-specifics to stack-neutral text. **The parity story is
      what makes the excision provably safe: hae's `.bessemer/prompts/` overrides at F7
      restore today's text byte-for-byte, so the parity gate compares assembled prompts
      that match the pin.** Bessemer's own repo overrides carry its ORIENTATION and
      VERIFY (`make check`) — discharging ADR 0002's third consumer of "one definition
      of the checks"; the prompts issue names both duties.
   2. **Three content deltas in the defaults, recorded divergences-that-comply, each
      pinned as a test literal** (they are controls; a reworded control is a changed
      control):
      - The denied-tool rule added to BOTH implement and review prompts (ADR 0001
        mandates; the pin predates the decision).
      - The specs-dir read-only declaration in the implement prompt, and the review
        prompt treating an agent-authored edit under the specs dir as a
        **review-stopping finding** (ADR 0002 consequence).
      - The review prompt's verdict semantics — "approved only if you made **no changes
        this round**; a round that committed fixes ends needs-work" — same class: it is
        what makes the verdict loop terminate correctly.
      - `<promise>COMPLETE</promise>` **dropped**: measured unconsumed at the pin (no
        reader outside the prompt file itself); an output contract nothing reads is the
        claim-the-tool-cannot-vouch-for F1 bans. Prompt defaults sit outside the parity
        comparison once hae runs its overrides. One-line recorded divergence.
   3. **Pass mechanics, port-faithful.** In-container `timeout "$PASS_TIMEOUT"` — a
      host-side kill of the `docker exec` client leaves the in-container process running
      and wedges the container; upstream's measured operational knowledge, kept as a
      comment. Cadence: 30s poll, heartbeat every 120s, retry after 30s, 3 attempts,
      dead-container check before retry. Claude invocation is a pinned argv literal:
      `claude --dangerously-skip-permissions -p --output-format stream-json --verbose`,
      prompt on stdin. Config keys `max_review_rounds` (3) and `pass_timeout` (900) are
      **any-layer** — they tune cost, not the privilege boundary.
   4. **The adapter image contract, complete list, owned by the container issue:**
      `bash` at `/usr/bin/bash` (the sudoers string), coreutils `timeout`, **`git`**
      (the agent commits in-container; the pinned Dockerfile installs it explicitly for
      exactly that reason), the `claude` CLI on PATH, a non-UID-0 agent user; plus
      `sudo` + the one sudoers line **iff the setup hook needs root** — ADR 0001's
      scaffold header explains dropping the grant, and bessemer's own no-op-hook image
      is the sudo-less existence proof. Doctor cannot police any of these (not host
      facts); the setup-hook and pass failure paths are where a bad image surfaces, and
      their error messages name the contract clause (a missing `timeout` is rc 127 on
      the first pass). Adopter-facing wording deferred to F6.

8. **Landing residuals and doctor's F3 checks.** Ruled 2026-08-05:

   1. **Product-name rename in adopter-facing prose** — legal by F2 decision 7's edge
      (nothing upstream asserts these strings): PR title `[bessemer] <branch>`; footer
      pinned as the *whole sentence* with only the product token renamed: "AI-authored
      via bessemer (spec: `…`). Draft until the dispatching dev reviews it."
   2. Probe/update/create ported exactly (`gh pr view --json url,state`, OPEN → edit,
      else create draft; body always on stdin). Description-pass fallback pinned:
      `_(description generation failed — see the task log)_`. gh's stderr goes to the
      host log (`2>>"$log"`, run.sh:1581/:1585), never the body — ported shape, tier-2
      asserted.
   3. **No force-push code exists in F3's landing** — `--force-with-lease` arrives only
      when F4 adds `--hard-reset`. Tier-2 absence assertion: no `--force*` in any
      recorded push argv. The plain-push invariant is structural, not disciplinary.
   4. **Zero commits past boundary: no push, no PR — but the ledger line still
      appends** (empty `pr_url`; upstream's append is unconditional after the `if`).
      Distinct from decision 6.4's no-line-on-hard-failure; both pinned.
   5. **Doctor grows exactly F3's checks:** credential presence (one shared resolver
      with dispatch preflight — the pin's own `have_claude_credential` discipline, "not
      a second copy of the logic", :346); `gh` present + authenticated; image presence
      (FAIL + build hint; staleness is F5's); `container_env_keys`, `container_cap_add`
      or `container_volumes` in the local layer → FAIL; prompt-override count reported.
      Nothing for unbuilt subsystems. No count stated in prose — the issue names the
      checks, and a numeral restating a list is two values that can disagree.
   6. **`config.KNOWN_KEYS` grows deliberately.** F1's pin is `{"source", "base",
      "specs_dir"}`; F3 adds `image`, `container_env_keys`, `container_cap_add`,
      `container_volumes`, `max_review_rounds`, `pass_timeout`, **`pids_limit`,
      `memory`** — a two-file edit by design (the hand-written literal test moves with
      it). The last two were missing from this list's first draft and caught during the
      issue breakdown — the exact two-lists-disagreeing defect this discipline exists
      for: the pin has `AGENTBOX_PIDS_LIMIT`/`AGENTBOX_MEMORY` (run.sh:530–531), ADR
      0001's security section requires the limits, and ADR 0001's config section names
      "machine limits" as a local-layer example — so both are **any-layer**, defaults
      2048 / 8g. The owning issue names the complete key list AND each key's layer rule
      (three committed-only, rest any-layer).

## Sequence

Twelve issues. `01`–`05` are mutually independent and parallelizable; `10` is the
assembly — big by neighbor-count, thin by logic, since the depth lives in the modules it
composes; `12` is HITL, the human runs it.

| Issue | Scope | Blocked by |
|---|---|---|
| `01` | Config keys: the eight new keys, layer rules, volume-format validation | — |
| `02` | Quotability: the proc.py policy function wrapping `redact` | — |
| `03` | Prompts: `prompts.py` resolution, stack-agnostic defaults, the content deltas, bessemer's own overrides | — |
| `04` | Checkout: clone, identity, `read_branch`, salvage, remove | — |
| `05` | Stream filter: host-side rendering + final-text capture, fixture-pinned | — |
| `06` | Container: argv builders, env boundary, hook invocation, image contract | 01 |
| `07` | Passes: `run_pass`, review loop, verdict | 05, 06 |
| `08` | Landing: push, draft PR open/update, body compose | 02 |
| `09` | Doctor: F3's checks, shared credential resolver | 01, 03 |
| `10` | Dispatch: orchestrator, CLI `run`, lock, cleanup, ledger, debt 3 | 01, 02, 03, 04, 06, 07, 08, 09 |
| `11` | Reclaim: `gc --force`, the debt 4 pins | 04 |
| `12` | Tracer: tier-3 suite + runbook | 10, 11 |

`10`'s eight blockers are deliberate: the alternative — splitting guards+CLI from the
lifecycle — would ship a `run` subcommand that guards and then cannot dispatch, exactly
the stub IMPLEMENTING.md bans. One late assembly issue over a lying intermediate state.

## Tracer

Bessemer dispatches a one-off spec **on itself** — first dogfood, before hae switches
over. Tier 3, run by the human, on a scratch branch of this repo. Criteria, ruled
2026-08-05:

- **Happy path:** a real, trivial-but-real one-off spec; container from bessemer's own
  adapter (no-op hook, sudo-less image); real implement + review passes; real push; real
  draft PR. Evidence collected: the PR URL; `bessemer status` showing the run **live
  during** and **landed after** — debt 3 proven against reality, on top of the tier-2
  round-trip; a ledger line written via `append_ledger`; `gc` reporting zero orphans
  after.
- **Failure rehearsal** (failure paths are the point, F1 issue 08's spirit):
  1. Setup hook forced nonzero → dispatch aborts, log surfaced, no orphan container or
     checkout.
  2. A run killed mid-pass (SIGKILL — the designed leak, decision 6.2's scope) → `gc`
     lists the orphans, `gc --force` reclaims them with salvage. **This is the designed
     leak meeting its designed remedy, and `reclaim.py`'s live proof — no tier-2 test
     can give it.**
  3. Duplicate dispatch on the same branch while one runs → refused, live run untouched.
     **The live proof of decision 6.1's refusal ordering** — the evidence being
     collected is that the first run's log, lock, and container are exactly as before
     the refusal.
- Notification observed firing at landing.
