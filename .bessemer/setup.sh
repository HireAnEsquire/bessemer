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
# Bessemer's own is nearly that small: the suite is stdlib `unittest` with zero runtime
# dependencies, no database, and — by construction (issue 01a) — no daemon and no network. One
# step is left, and it is the one the previous version of this file predicted in a comment and
# did not do: **`uv` is what runs the checks, and the image does not carry it.**
#
# Why here and not in `.bessemer/Dockerfile`: installing uv is dependency installation, and the
# thing it installs dependencies for is the checkout, which the image never sees. The image is
# the stack; the hook is what the stack needs to run *this* tree.
#
# Why `/usr/local/bin`: the hook runs as root through the one sudoers grant, so `$HOME` here is
# root's, and an installer's default of `~/.local/bin` would put uv somewhere the agent user
# cannot reach. `/usr/local/bin` is on both users' `PATH` — on sudo's `secure_path` for this
# script, and on the agent's for every pass afterwards.
#
# The agent's half is the half that is measured: `tests/integration/test_setup_hook.py` runs this
# hook in a real container and then asks the *agent* user where `uv` is. Root's half is asserted
# by this script itself — the read-back at the bottom runs under the same `secure_path` this
# paragraph claims, and fails the hook if it is wrong.
#
# **What this deliberately does not do: `uv sync`.** Root creating `/workspace/.venv` would hand
# the agent a virtualenv it cannot write, and the first `uv run` would fail on permissions
# rather than on anything the agent did. The agent's own `make check` builds the environment as
# itself, once, and that is the correct owner.
#
# It must stay idempotent and non-interactive. The agent may legally re-run it mid-run to revive
# something that died, which is what the image's single sudoers line grants root for. The
# `command -v` below is what buys that back: a second run installs nothing and exits 0.
set -euo pipefail

# Where the installer puts the binary, and its instruction to leave shell profiles alone — a
# hook that edits `.bashrc` is a hook whose effect depends on which shell the next step happens
# to start.
export UV_INSTALL_DIR=/usr/local/bin
export INSTALLER_NO_MODIFY_PATH=1

if command -v uv >/dev/null 2>&1; then
    echo "setup: uv already installed ($(uv --version)) — nothing to do."
    exit 0
fi

echo "setup: installing uv into ${UV_INSTALL_DIR}"
curl -LsSf https://astral.sh/uv/install.sh | sh

# Read back through `PATH` rather than through `${UV_INSTALL_DIR}/uv`: what the next step needs is
# a uv the shell can find, and an installer that wrote somewhere else must fail *here*, where the
# message names the hook, rather than as a missing command inside `make check`.
#
# On its own line, not inside the `echo`. Measured: `echo "… $(uv --version)"` reports the status
# of `echo`, so with no uv on `PATH` the substitution prints `command not found` to stderr and the
# hook prints `setup: installed ` and **exits 0** — `set -e` does not see a failure in a command
# substitution inside a successful command. The check has to be a command of its own.
installed=$(uv --version)
echo "setup: installed ${installed}"
