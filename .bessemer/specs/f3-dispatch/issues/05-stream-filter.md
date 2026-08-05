# 05 — stream filter: host-side rendering, fixture-pinned

Status: Done
Type: AFK
Blocked by: —

## What to build

A pure module (suggested `bessemer/stream.py`) rendering claude's stream-json into the
log's "claude |/>" lines, plus **final-text capture** — the two halves ADR 0001 names as
one provider-contract surface, kept together deliberately (README decision 5.1).

This is a **recorded divergence from the pin**: upstream runs
`python3 /agentbox/stream-filter.py` *inside* the container (run.sh:1099), assuming
python3 in every adapter image — which fails ADR 0001's assume-nothing-about-stacks
constraint. The oracle is `git show e194121f75f4:.agentbox/stream-filter.py` — read it
first and port its rendering exactly.

## The fixture is the parity proof

"Log lines identical" is a claim until a test holds it (README decision 5.1 rider):

1. Capture a real stream-json transcript (from a real claude run, or an existing log's
   raw stream if one is preserved) — committed as a test fixture.
2. Run upstream's `stream-filter.py` over it once, host-side, to produce the expected
   output — committed beside it.
3. The test asserts bessemer's rendering of the fixture is **byte-identical** to that
   expected output.

The fixture must include at least: an assistant text block, a tool-use block, a
tool-result, and the final message (for capture). If the captured transcript lacks one,
extend it with a real example and note which lines were added.

## Acceptance criteria

- [ ] Byte-identical rendering of the fixture vs upstream's filter output
- [ ] Final text captured equals the fixture's last assistant text — and is returned,
      not printed (this module talks to no terminal and writes no file)
- [ ] Malformed line handling matches upstream's (measure what upstream does with a
      non-JSON line first; port that, and record it in the docstring)
- [ ] Pure: no subprocess, no filesystem — plain functions over `str`/iterables
- [ ] The divergence (in-container → host-side) recorded in the module docstring with
      the founding-constraint reason and the parity argument
- [ ] `make check` green
