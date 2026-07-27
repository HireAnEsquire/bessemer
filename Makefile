# The one definition of "the checks", with three consumers (ADR 0002): the developer's
# commit hook, CI, and — from F3 — the in-container agent, whose implement prompt runs this
# same target. CI invokes `make check` verbatim rather than re-listing the steps, because two
# lists of the same checks drift and the party hurt worst by that drift is the agent: it runs
# its command, believes it is done, and finds the gap after the pull request is open.
#
# Every recipe line below is POSIX shell, and none of it depends on which `make` is
# installed. An earlier version of this file set `SHELL := /bin/bash` and
# `.SHELLFLAGS := -eu -o pipefail -c` and then piped the test runner into `tee`. That was
# wrong twice over: `.SHELLFLAGS` arrived in GNU Make 3.82, and /usr/bin/make on macOS is
# 3.81, which ignores it without a word — so `pipefail` never applied, `tee` swallowed the
# runner's exit status, and a red suite exited 0. Measured, not assumed: under 3.81 a recipe
# of `false | tee /dev/null` runs its next line and `make` exits 0.
#
# So: no pipelines whose status matters, and no shell option that is not in POSIX sh.

WORK_DIR := .make
# Gitignored. Kept rather than discarded, so a failed run is still readable afterwards.
FILE_LIST := $(WORK_DIR)/checked-files
TEST_LOG := $(WORK_DIR)/unittest.log

# Every file in the tree that git is not ignoring — tracked, plus untracked.
#
# `pre-commit run --all-files` does not mean this. It means `git ls-files`, so a file nobody
# has staged yet is invisible to every hook, and they all report Passed rather than skipped.
# The hole is worse than a uniform one: mypy sees the file anyway, through `files` in
# pyproject.toml, so the gate reads as complete while ruff and the hygiene hooks never
# touched it. Every issue after this one adds new files, and each would hit it once.
#
# Computed here rather than worked around at the keyboard: `git add` first is exactly the
# second thing a contributor would have to know, and `make check` is supposed to be the only
# one. A recipe that staged files on the contributor's behalf would fix the symptom by
# writing to the index behind their back, which is worse than the hole.
#
# `-z` and `xargs -0` so a filename containing a space cannot split into two paths that do
# not exist. Paths that do not exist are dropped by pre-commit itself, in
# `Classifier.__init__`, so a tracked file deleted from the work tree behaves exactly as it
# did under `--all-files`. The list goes through a file rather than a pipe so that `git`
# failing is `make`'s failure and not something `xargs` reports as an empty run.
CHECKED_FILES := git ls-files -z --cached --others --exclude-standard --deduplicate

# Exit status alone does not prove the suite ran. Issue 01a's mutation run found that an
# unguarded `os.execv` replaces the interpreter's process image mid-suite: the runner
# disappears, prints no summary line, and the shell sees status 0 from whatever took its
# place. `OK`, on a line of its own, is the last thing a completed green run emits.
#
# Two things keep the suite from forging that line. It is matched only against the runner's
# **stderr**, which is where unittest writes its progress and its verdict and where a test's
# own `print()` does not go — `2>$(TEST_LOG)` above captures nothing else. And it is matched
# only against the **last** line, anchored at both ends, so output that merely contains `OK`
# proves nothing. `-u` above is what makes "last" mean what it says: piped stdout is block
# buffered, so without it a test's output can flush after the summary it is supposed to
# precede.
#
# The limit, named rather than papered over, in the house style of `tests/guard.py`: a test
# that writes unittest's exact summary line to *stderr* as the last thing it does before
# vanishing would still pass this gate. That is a deliberate act, not an accident, and this
# is the same threat model the guard states — accident and drift, not a hostile author.
#
# The message names both causes, because the gate cannot tell them apart and the likelier one
# during F2's 337-test port is the innocent one: anything written to stderr at interpreter
# shutdown — an unraisable exception, a `ResourceWarning: unclosed socket` — lands after the
# summary and displaces it. That fails a suite that passed, which is the safe direction, but a
# message asserting "the run did not finish" would send the reader hunting a vanished runner.
SUITE_FINISHED := tail -n 1 $(TEST_LOG) | grep -Eq '^OK( \(.*\))?$$'
DID_NOT_FINISH := $(TEST_LOG) does not end in a unittest summary line. Either the runner vanished mid-suite, or something wrote to stderr after the summary — read the last lines above to see which

.PHONY: check
check:
	@mkdir -p $(WORK_DIR)
	$(CHECKED_FILES) >$(FILE_LIST)
	xargs -0 uv run pre-commit run --files <$(FILE_LIST)
	uv run python -u -m unittest discover 2>$(TEST_LOG); status=$$?; cat $(TEST_LOG) >&2; exit $$status
	@$(SUITE_FINISHED) || { echo "$(DID_NOT_FINISH)"; exit 1; }
