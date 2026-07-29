"""Text belonging to another program, made safe to print. One redactor, shared.

**Everything bessemer prints that it did not write itself passes through here.** `git`,
`docker` and `uv` all echo their own failure text, `git`'s can carry a token out of a remote
URL (`https://x-access-token:ghp_…@github.com/…`), and that text reaches a terminal today
and a pull request body from F3. Issue 05 established the hazard and wrote the redactor;
issue 06 needed the same thing for a crashing check's exception text and promoted it here
rather than copying it. Two regexes are two redactors that can disagree, and the one that
disagrees silently is the one printing into a pull request.

This module depends on nothing else in the package, for the reason `bessemer.outcome` does:
everything that reports someone else's text imports it, so anything it imported would become
a dependency of the whole package.
"""

import re
from typing import Final

DETAIL_LIMIT: Final = 200
"""How much of another program's own output may appear in a message, after redaction.

A cap rather than a judgement about content: git can emit a screen of advice, and a check
line that becomes a paragraph stops being read. Applied after `redacted`, never before —
truncating first would leave the redactor a line it never saw.
"""

_CREDENTIAL_IN_URL: Final = re.compile(r"(?<=://)[^/@\s]*@")
"""Userinfo in a URL: everything between `://` and the `@` that ends the authority.

**This is a redactor for how git leaks credentials, not a general secret scanner.** That is
the named limit. A token reaches git's output by riding in a remote URL —
`https://x-access-token:ghp_…@github.com/o/r.git` — because that is where a credential
helper, a CI checkout, or an `insteadOf` rewrite puts it, and git echoes the URL when a
remote operation fails. A secret pasted into `stderr` some other way is not covered, and
nothing here pretends it is; what is covered is the shape that actually occurs.

Redacting the whole authority userinfo rather than matching token prefixes: `ghp_`,
`github_pat_`, `glpat-` and whatever the next forge invents are a list that goes stale,
while "the part of a URL before the `@`" is the grammar itself. `git@github.com:o/r.git`
carries no secret and has no `://`, so scp-style remotes are left alone.
"""

REDACTED: Final = "<redacted>@"
"""What replaces the userinfo. Present in the output, so a reader can see it happened."""


def redacted(text: str) -> str:
    """Remove credentials from anything of another program's about to be shown to a user."""
    return _CREDENTIAL_IN_URL.sub(REDACTED, text)


def detail(text: str) -> str:
    """Another program's own first word on a failure, safe to print.

    First line only, because the later lines of a `git` or `docker` failure are advice aimed
    at an interactive user and the first is the `fatal:`/`error:`. Empty when the program
    said nothing, which callers must tolerate — `git rev-parse` failing silently is possible
    and a message ending in a bare colon is worse than one that stops.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return redacted(stripped)[:DETAIL_LIMIT]
    return ""
