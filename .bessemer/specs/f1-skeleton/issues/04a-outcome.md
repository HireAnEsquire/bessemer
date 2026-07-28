# 04a — The outcome type

Status: Done
Type: AFK
Blocked by: 04

## What to build

`bessemer/outcome.py` — a tagged union, two frozen dataclasses, `Resolved(value)` and
`Unresolved(reason, hint)`, consumed with `match`. Roughly thirty lines, no dependencies
on anything else in the package.

Split out of issue 05 because of an ordering mistake: ADR 0002 puts this type in
`outcome.py` and has config load return it, but `outcome.py` was a deliverable of issue
05, which is *blocked by* 04. Issue 04 therefore could not use the type the ADR told it
to use, and defined a second one with the same shape. One value-or-reason type is the
whole point — two is the mush the ADR was written to prevent.

Deliberately not a `Result`/`Maybe` library: the shape appears at a handful of sites, and
railway-oriented `bind`/`map` idioms read as foreign in a codebase whose founding premise
is a script the team can read and fix. Python 3.14's pattern matching plus mypy narrowing
already does this natively. Revisit only if callers pass roughly ten.

- **`Resolved` is generic in its value.** `Resolved[Config]` and `Resolved[str]` are both
  needed in F1 — config load returns the first, `resolve_base` the second. A union that
  only carries `object` pushes a cast to every call site and gives back exactly the
  narrowing this type exists to provide.
- **`hint` carries the fix, not just the diagnosis.** It is what doctor prints after the
  failure text, and it is what makes a check line actionable. `reason` says what is wrong;
  `hint` says what to type.
- **Neither of these is `bessemer.proc.Result`.** "A process ran and failed" is a
  completed thing with a returncode; "a value could not be determined" is the absence of
  an answer. Issue 03 keeps them apart deliberately and this issue does not merge them.

## Acceptance criteria

- [ ] `Resolved`/`Unresolved` narrow correctly under `mypy --strict` in a `match` block —
      proven by a test that would fail to type-check if narrowing broke, not by a comment
      claiming it works
- [ ] `Resolved` is generic: a `Resolved[Config]` and a `Resolved[str]` both type-check,
      and `.value` comes back with the right type at each site
- [ ] Both are frozen, and a test pins that — a mutable outcome is a value that can be
      edited after the decision that produced it
- [ ] `bessemer/config.py`'s `load()` returns `Resolved[Config] | Unresolved`, and
      `NotLoaded` is gone from the package. Its cases — not found, unparseable TOML, a
      non-UTF-8 file, an unreadable file, and whatever the total clause catches — survive as
      distinguishable `reason`/`hint` pairs, each still naming the offending file where it
      named one before. Distinguishability currently rests entirely on prose substrings,
      with no tag or subtype; that is the bar, and a reworded reason breaks a caller no test
      represents. If unifying the types makes a tag natural, say so rather than adding one
      silently
- [ ] **The failure cases are pinned by a hand-written literal, and the set is closed.**
      A test names every case and asserts no two share a `reason` or a `hint` — case
      collapse is invisible to the per-case substring assertions, since each of those stays
      green when a clause is deleted and another absorbs it.

      **The set must be closed against growth as well as collapse, and against the module
      rather than against the fixtures.** A literal compared only to a hand-written fixture
      list restates the fixtures — add a sixth `except` clause to `_read_layer` and both
      sides stay in agreement while the module has changed underneath them. Count the
      handlers in the source with `ast`, the way `tests/test_config_purity.py` already
      walks the module, and assert that count against the literal
- [ ] `tests/test_config.py` still passes, with its assertions rewritten against the new
      type rather than deleted
