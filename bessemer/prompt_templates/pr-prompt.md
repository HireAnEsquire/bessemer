# ROLE

Write the pull-request description for the work on this branch. The dispatcher's
message below this prompt names the fork-point commit; review every commit after
it (`git log <fork>..HEAD`, `git diff <fork>..HEAD`). The spec the work was
implemented from is `/spec.md`.

# STRUCTURE (in this order)

1. **Generated artifacts** — if the change includes migrations, schema changes
   or anything else generated that a reviewer must act on when this lands, a
   callout at the very top: which ones, and anything unusual about them (a data
   migration, a backfill, a manual step). Omit the section entirely if there
   are none.
2. **Overview** — a short paragraph: what the spec asked for and why.
3. **Changes** — brief bullets describing the **functionality**: what a user,
   admin, or API consumer can now do or see differently. Do not inventory code
   changes (no file lists, no "refactored X"); mention internals only when a
   reviewer needs them to understand behavior.
4. **Manual testing** — numbered steps a dev can follow with minimal friction.

# MANUAL TESTING ASSUMPTIONS

The dev has this repo's own development environment running and can use
whatever it exposes — the running app, any admin or API surface it has, and a
shell against its data. Name commands, paths and URLs the way this repo's own
documentation and tooling name them; do not invent an environment.

Write the steps to minimize friction:

- Order steps so environment setup (schema changes, seed data) comes first,
  then each scenario flows without backtracking.
- Give the concrete local URL or command for each step rather than prose like
  "navigate to the profile page".
- Where a specific kind of user or record is needed, give a copy-pasteable way
  to find or create one — identifiers differ between dev environments, so never
  hardcode one without showing where it came from.
- If the flow requires acting as more than one kind of user, structure the
  steps per user type and say how to become each.
- State the expected result of each step, so a dev knows a pass from a fail.

# FORMAT

Every line you output lands verbatim in the PR body. Begin directly with the
first section (the generated-artifacts callout or the overview); no preamble or
process narration, no closing remarks, no enclosing code fence, no title line
(the title is set separately).
