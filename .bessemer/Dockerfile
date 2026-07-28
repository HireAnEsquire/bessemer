# Bessemer's own adapter image — the container a run of bessemer against this repository
# executes inside.
#
# Deliberately trivial, and that is a property of *this* repo rather than a template to copy:
# bessemer's suite is stdlib `unittest` with no services, so there is no database to start, no
# package manager to prime, and no second dependency list to keep in step with. An adapter for
# an application repo carries all of that; see ADR 0001, "what bessemer requires of an adopting
# repo".
#
# Nothing in F1 reads this file. F3's dispatch is what builds it and runs the container; it
# lands here now so that F3 is a dispatch rather than a dispatch plus an adapter.
#
# Build it the way F3 will, supplying the host UID that owns the bind-mounted checkout:
#
#   docker build --build-arg AGENT_UID="$(id -u)" -t bessemer-agent .bessemer
FROM python:3.14-slim-trixie

# `git` because the agent commits on the working branch and pre-commit shells out to it; the
# slim base ships without it. `sudo` for exactly the one grant below. `curl` and
# `ca-certificates` for the CLI install in the next layer.
RUN apt-get update && apt-get install -y --no-install-recommends \
      sudo \
      git \
      curl \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# --- Non-root agent user ----------------------------------------------------------------
# The run's checkout is bind-mounted into this container and is owned on the host by whoever
# dispatched. `AGENT_UID` is what makes the in-container user that same person.
#
# It is not optional decoration: macOS Docker Desktop masks a UID mismatch on a bind mount and
# Linux does not, so a hard-coded 1000 works on the author's machine and breaks for the first
# Linux adopter — the worst possible time to find out. The default below exists so a bare
# `docker build` still produces a usable image; F3 always passes the real one.
#
# The group is *not* a build argument. Write access to the bind mount is decided by the owner
# bits, and matching the owner is what UID does; a GID argument would be a second knob buying
# nothing until something in a checkout is group-writable but not owner-writable.
#
# `-o` below allows a UID that already exists in the base image — 1000 does not here, but will
# on other bases. It is also what makes the refusal necessary rather than tidy: `-o` accepts 0
# as readily as any other duplicate.
ARG AGENT_UID=1000

# The refusal and the user creation are one instruction on purpose. Split into two `RUN`s they
# are two things to keep in step, and the one that can be deleted without breaking a build is
# the check.
#
# **`AGENT_UID=0` must fail the build.** Measured before this guard existed:
# `docker build --build-arg AGENT_UID=0` succeeded, and `id` in the resulting image printed
# `uid=0(root)` — `useradd -o -u 0` creates an `agent` name for the existing root UID, and every
# later `USER agent` then runs as root. No warning anywhere. Dispatch passes the host
# dispatcher's UID straight through, and dispatching from a root shell is ordinary in CI, so
# this is ADR 0001's container boundary dissolving on the machine least likely to be watched.
#
# Non-numeric input is refused by the same guard rather than left to `useradd`. `test -eq` on a
# word is a shell error, and an error inside a `||` branch is exactly the shape that exits 0 —
# so the digits check comes first and everything that is not a run of digits is rejected before
# any arithmetic happens. `08` is rejected as a UID by `useradd` itself; what this has to stop
# is `00`, which is digits, is zero, and would sail past a `case ... 0)` match.
RUN if ! printf '%s' "${AGENT_UID}" | grep -Eq '^[0-9]+$'; then \
        echo "AGENT_UID must be a decimal integer, got '${AGENT_UID}'" >&2; exit 1; \
    elif [ "${AGENT_UID}" -eq 0 ]; then \
        echo "AGENT_UID=0 would make the agent root: refusing to build" >&2; exit 1; \
    fi \
    && useradd -o -u "${AGENT_UID}" -m -d /home/agent -s /bin/bash agent

# The single sudoers line: root for exactly the setup hook, and nothing else (ADR 0001, "setup
# hook: contract not convention"). It is what lets the agent legally revive a service the hook
# starts, without handing it general root.
#
# The path is outside the checkout on purpose. F3 bind-mounts `.bessemer/setup.sh` read-only at
# this absolute path, rather than letting sudo run the copy that arrives inside the checkout,
# because the checkout is writable by the agent — a sudoers entry pointing into it would name a
# script the agent can rewrite and then execute as root. sudo matches the command verbatim, so
# F3's invocation must be exactly `sudo /usr/bin/bash /bessemer/setup.sh`.
#
# Where F3 mounts the checkout is not decided yet and is not decided here: no ADR names a mount
# path, and this comment naming one would read as a contract that nobody agreed to. What is
# fixed is the property — the granted path is not the checkout's own copy of the hook, wherever
# that copy lands.
#
# Bessemer's own hook needs no root at all (it starts nothing), so this grant could be dropped
# from *this* adapter. It is kept because F3 must exercise the path every real adapter uses,
# and an adapter whose setup hook is the one thing never tested through sudo would find that
# out in someone else's repository.
RUN echo "agent ALL=(ALL) NOPASSWD: /usr/bin/bash /bessemer/setup.sh" > /etc/sudoers.d/agent \
    && chmod 0440 /etc/sudoers.d/agent

USER agent

# --- The agent CLI ----------------------------------------------------------------------
# Anthropic's native installer rather than `npm install -g @anthropic-ai/claude-code`, which is
# what the port source used. The npm route means adding Node to a Python image — a second
# runtime and roughly 150 MB — to deliver one binary that no longer needs it. Run as `agent`
# because the installer puts the binary under `$HOME`, so it must be the agent's home.
#
# `stable` rather than a version pin: a pinned version is one yank away from an unbuildable
# image, and the image is rebuilt from scratch rather than upgraded in place, so the pin would
# be doing no reproducibility work between builds either.
RUN curl -fsSL https://claude.ai/install.sh | bash -s stable

ENV PATH="/home/agent/.local/bin:${PATH}"

WORKDIR /home/agent

# The container is started detached and driven with `docker exec`, so its own process only has
# to stay alive. F3 overrides the working directory to the mounted checkout at start.
ENTRYPOINT ["sleep", "infinity"]
