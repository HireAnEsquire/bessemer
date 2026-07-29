# 01 — Issues: parse, select, spec resolution, status writes

Status: Done
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

Nine classes, **37 ported and 1 newly excluded**:

`ParseIssueTests` (6) · `SelectIssuesTests` (8) · `ResolveSpecTests` (8) ·
`SetStatusTests` (2) · `IssueSummaryTests` (3) · `SlugifyTests` (3) ·
`MaterializeAdHocTests` (3) · `StripFeedbackEditTextTests` (4)

`CmdFeedbackEditStripTests` (1) is **excluded**, decided during this issue and now recorded
in decision 5. Upstream's `cmd_feedback_edit_strip` is three lines — read stdin, print,
return 0 — and exists so `run.sh:918`, being bash, could reach python. Bessemer's dispatch
is python and calls `strip_feedback_edit_text` directly, so the subcommand has no
counterpart and adding one would create a user-facing surface for a flow that does not exist
until F3. The computation it wraps is already ported by `StripFeedbackEditTextTests`, so
nothing is lost — there was never a split to make, because the whole of that test is the
shim.

Exclusions become **141**, pending **196**, and this class joins the wholly-excluded set —
which means `WHOLLY_EXCLUDED_CLASSES` moves from 9 to 10, not just a count bump.

The five F2-wide decisions are in the [feature README](../README.md). Three bear on this
issue directly:

- **`DispatchError` stays an exception** (decision 2). `_topo_order` and `select_issues`
  raise it five times between them; those raises port as they stand. ADR 0002's never-raise
  rule governs resolvers, and doctor does not call this code.
- **`cmd_*` tests split — unless the subcommand is a shim** (decision 5). This issue's only
  `cmd_*` class is the shim case and is excluded, so **no `ported-split` entry lands here**.
  `PORTED_SPLIT` therefore still has never fired on real data; issue 02's
  `CmdLedgerAppendLastBaseTests` or issue 04's `CmdStatusTests` is where that gets settled.
- **Picker comments travel, marked** (decision 6). `ResolveSpecTests` carries one at
  upstream `test_tasklib.py:1637` — "the picker's existence check must mirror that exactly".
  Keep the text, add a clause naming the picker as unported. `SelectIssuesTests` carries
  none; an earlier draft of decision 6 said it did, and that was a false match on the
  English word *which*.

## Acceptance criteria

- [ ] All 37 tests land, assertions intact. A ported test that asserts less than upstream's
      did is the failure this whole feature is built to prevent, and the manifest cannot see
      it — only you and the reviewer can
- [ ] Eight classes' entries flip from `pending` to `ported`; `CmdFeedbackEditStripTests`
      flips to `excluded` with its reason, joins `WHOLLY_EXCLUDED_CLASSES` (9 → 10), and
      `EXCLUDED_TEST_COUNT` moves 140 → 141. Pending falls 197 → 159. The flip is not
      bookkeeping: an entry cannot be flipped to `ported` without a counterpart that runs
- [ ] `bessemer/issues.py` spawns no subprocess and reads no environment — it is pure over
      paths and text, like `config.load`. If something here needs git, that is a finding to
      report rather than an import to add
- [ ] **No `ported-split` entry lands here, and `tests/test_cli.py` is untouched.** An
      earlier draft of this criterion required one; decision 5's shim rule removed the only
      candidate, and this line was left behind when the rest were updated. A checklist item
      that cannot be ticked reads as unfinished work
- [ ] Where the port source's behaviour looks wrong, report it and port it anyway. F1
      learned this in the other direction three times: the spec was wrong, not the code, and
      the implementer that silently improved things was the one that shipped a defect
