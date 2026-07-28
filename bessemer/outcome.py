"""The one value-or-reason type: a value was determined, or it was not and here is why.

Two frozen dataclasses consumed with `match` (ADR 0002, "resolvers return value-or-reason
and never raise"). Config load returns one, and issue 05's resolvers will return the same
one — one type, because two types with this shape is the mush the ADR was written to
prevent. This module depends on nothing else in the package, deliberately: everything that
reports a failure imports it, so anything it imported would become a dependency of the
whole package.

**Deliberately not a `Result`/`Maybe` library, and deliberately not growing `bind`/`map`.**
The shape appears at a handful of sites, and railway-oriented idioms read as foreign in a
codebase whose founding premise is a script the team can read and fix. Python's pattern
matching plus mypy's narrowing already does the work those methods would do. Revisit only
if callers pass roughly ten.

**Neither of these is `bessemer.proc.Result`.** "A process ran and failed" is a completed
thing with a returncode; "a value could not be determined" is the absence of an answer.
They are kept apart on purpose and this module does not merge them.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Resolved[T]:
    """A value was determined. Generic, so the value survives the union with its type.

    `Resolved[Config]` and `Resolved[str]` are both needed in F1 — config load returns the
    first, `resolve_base` the second. A union carrying `object` would push a cast to every
    call site and give back exactly the narrowing this type exists to provide.
    """

    value: T


@dataclass(frozen=True)
class Unresolved:
    """A value could not be determined, in a form a caller can render or refuse on.

    Not generic: there is no value, and parameterising the failure half would force every
    call site that only handles the failure to name a type it never touches.
    """

    reason: str
    """What is wrong. Diagnosis only — a reason with no `hint` is a check line the reader
    cannot act on."""

    hint: str
    """What to type. Doctor prints it after the failure text, and it is what makes a check
    line actionable rather than merely true."""
