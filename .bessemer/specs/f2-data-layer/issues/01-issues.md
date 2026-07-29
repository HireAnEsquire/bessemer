# 01 — Issues: parse, select, spec resolution, status writes

Status: Todo
Type: AFK
Blocked by: 00

## What to build

`bessemer/issues.py` — issue files as data. Ports from `.agentbox/tasklib.py` at
`e194121f75f4`: `parse_issue`, `load_issues`, `_leading_number`, `_topo_order`,
`select_issues`, `cmd_set_status`'s status-writing half, `strip_feedback_edit_text`, and the
slug and ad-hoc helpers.

**This is a port. The upstream tests are the specification** — where the port source and
your judgement disagree about behaviour, the port source wins and the disagreement is a
finding to report, not a thing to fix silently.

## The classes this issue owns

Nine, 38 tests, from `tests/port_manifest.py`:

`ParseIssueTests` (6) · `SelectIssuesTests` (8) · `ResolveSpecTests` (8) ·
`SetStatusTests` (2) · `IssueSummaryTests` (3) · `SlugifyTests` (3) ·
`MaterializeAdHocTests` (3) · `StripFeedbackEditTextTests` (4) ·
`CmdFeedbackEditStripTests` (1)

The five F2-wide decisions are in the [feature README](../README.md). Three bear on this
issue directly:

- **`DispatchError` stays an exception** (decision 2). `_topo_order` and `select_issues`
  raise it five times between them; those raises port as they stand. ADR 0002's never-raise
  rule governs resolvers, and doctor does not call this code.
- **`cmd_*` tests split** (decision 5). `CmdFeedbackEditStripTests` is one test asserting
  both a computation and its output: the computation half lands here, the rendering half in
  `tests/test_cli.py`, and the manifest entry records both destinations as `ported-split`.
  This is F2's first split entry, so it is also the first real exercise of the manifest's
  `PORTED_SPLIT` path, which has never fired on real data.
- **Picker comments travel, marked** (decision 6). `ResolveSpecTests` carries one at
  upstream `test_tasklib.py:1637` — "the picker's existence check must mirror that exactly".
  Keep the text, add a clause naming the picker as unported. `SelectIssuesTests` carries
  none; an earlier draft of decision 6 said it did, and that was a false match on the
  English word *which*.

## Acceptance criteria

- [ ] All 38 tests land, assertions intact. A ported test that asserts less than upstream's
      did is the failure this whole feature is built to prevent, and the manifest cannot see
      it — only you and the reviewer can
- [ ] Every one of the nine classes' entries flips from `pending` to `ported` (or
      `ported-split`), and the pending count falls from 197 to 159. The flip is not
      bookkeeping: an entry cannot be flipped without a counterpart that runs
- [ ] `bessemer/issues.py` spawns no subprocess and reads no environment — it is pure over
      paths and text, like `config.load`. If something here needs git, that is a finding to
      report rather than an import to add
- [ ] The `ported-split` entry records both destinations, and `tests/test_cli.py` holds the
      rendering half. Say what the split cost you, since this is the first one
- [ ] Where the port source's behaviour looks wrong, report it and port it anyway. F1
      learned this in the other direction three times: the spec was wrong, not the code, and
      the implementer that silently improved things was the one that shipped a defect
