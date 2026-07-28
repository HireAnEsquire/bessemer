#!/usr/bin/env bash
# Bessemer's setup hook — the adapter's chance to make a fresh checkout runnable inside its
# container, before the agent is given any work. F3 runs it once per run, non-interactively,
# and a nonzero exit aborts the dispatch with the log surfaced (ADR 0001, "setup hook: contract
# not convention").
#
# In a real adapter this is where the expensive, repo-specific work lives: starting the
# throwaway database and any service the suite talks to, and installing dependencies *into the
# checkout* — the checkout is bind-mounted, so it shadows anything the image baked in, and a
# baked layer goes stale against the checkout's own lockfile anyway. That is the main reason
# the hook exists at all.
#
# Bessemer's own needs none of it. The suite is stdlib `unittest` with zero runtime
# dependencies, no database, and — by construction (issue 01a) — no daemon and no network. So
# this hook is a no-op, and it is committed as one rather than omitted: F3 runs the hook
# unconditionally, and an adapter is a contract, so the file has to be here and has to exit 0.
#
# It must stay idempotent and non-interactive. The agent may legally re-run it mid-run to
# revive something that died, which is what the image's single sudoers line grants root for.
# Doing nothing is idempotent for free; anything added here has to earn that back.
#
# Note for whoever adds the first real step: bessemer's checks run through `uv`, which this
# adapter's image does not carry. Installing it is dependency installation, so it belongs here
# rather than in the Dockerfile.
set -euo pipefail

echo "setup: nothing to do — bessemer's suite is stdlib unittest with no services."
