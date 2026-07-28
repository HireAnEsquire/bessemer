"""Tests for the shared value-or-reason type.

Two of the three things this type promises are static, not dynamic: that `Resolved` is
generic in its value, and that a `match` narrows each arm to the right type. Neither can be
observed at runtime — a `Resolved[Any]` behaves identically to a `Resolved[Config]` when you
call it — so the assertions for those are `assert_type` and `assert_never`, which mypy
checks and which fail the build rather than the run. `make check` runs mypy over `tests/`,
so these are gated exactly as the runtime assertions are.

The third promise, frozen-ness, is the ordinary kind and is asserted at runtime.

`tests/test_config.py` narrows a real `load()` result the same way. Both are wanted: this
file proves the type narrows on its own, that one proves the caller's return annotation
actually carries the narrowing through.
"""

import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import assert_never, assert_type

from bessemer.outcome import Resolved, Unresolved


class FieldTest(unittest.TestCase):
    """The fields, restated by hand.

    Literals rather than assertions derived from the classes, for the reason the rest of
    this suite uses literals: an assertion that reads the field list out of the dataclass
    cannot notice a field being renamed or dropped — every consumer of `hint` breaks and
    the test stays green. `hint` in particular is the one a reader would think redundant
    next to `reason`, and it is the one doctor's output is useless without.
    """

    def test_resolved_carries_exactly_one_field(self) -> None:
        self.assertEqual([field.name for field in fields(Resolved)], ["value"])

    def test_unresolved_carries_exactly_a_reason_and_a_hint(self) -> None:
        self.assertEqual([field.name for field in fields(Unresolved)], ["reason", "hint"])


class FrozenTest(unittest.TestCase):
    """Both types are frozen: an outcome cannot be edited after the decision that made it.

    Assigned through `setattr` with the name in a variable rather than as a literal, for
    two reasons at once: a direct `outcome.value = ...` is a static error that mypy would
    refuse before the test could run, and a literal attribute name would trip ruff's B010.
    """

    def test_resolved_refuses_assignment(self) -> None:
        resolved = Resolved("a value")
        for name in ("value",):
            with self.subTest(field=name), self.assertRaises(FrozenInstanceError):
                setattr(resolved, name, "something else")
        self.assertEqual(resolved.value, "a value")

    def test_unresolved_refuses_assignment(self) -> None:
        unresolved = Unresolved(reason="no origin remote", hint="git remote add origin ...")
        for name in ("reason", "hint"):
            with self.subTest(field=name), self.assertRaises(FrozenInstanceError):
                setattr(unresolved, name, "rewritten")
        self.assertEqual(unresolved.reason, "no origin remote")
        self.assertEqual(unresolved.hint, "git remote add origin ...")


class GenericTest(unittest.TestCase):
    """`Resolved` is generic in its value, and the value comes back with its own type.

    Two different parameterisations, because one alone cannot distinguish a genuinely
    generic class from one hard-coded to whatever that single test used. `Path` stands in
    for issue 05's `Resolved[str]` and config's `Resolved[Config]` — a class the type
    checker knows has members, so the member access below is a real check rather than a
    restatement of the annotation.

    The load-bearing assertions here are the `assert_type` calls, and they are mypy's
    rather than unittest's: this whole class stays green at runtime against a non-generic
    `Resolved` carrying `object`. Nothing about a generic parameter is observable at run
    time, so nothing below pretends to observe one.
    """

    def test_a_resolved_str_narrows_to_str(self) -> None:
        outcome: Resolved[str] | Unresolved = Resolved("main")
        match outcome:
            case Resolved(value=base):
                assert_type(base, str)
                self.assertEqual(base.upper(), "MAIN")
            case Unresolved(reason=reason):
                self.fail(reason)

    def test_a_resolved_path_narrows_to_path(self) -> None:
        outcome: Resolved[Path] | Unresolved = Resolved(Path("/tmp/adapter/.bessemer"))
        match outcome:
            case Resolved(value=adapter_dir):
                assert_type(adapter_dir, Path)
                self.assertEqual(adapter_dir.name, ".bessemer")
            case Unresolved(reason=reason):
                self.fail(reason)

    def test_two_resolved_values_compare_by_the_value_they_carry(self) -> None:
        """Ordinary dataclass equality, and nothing more than that.

        It is **not** a check on the generic parameter: `Resolved[str]` and `Resolved[Path]`
        are one class at runtime, and this assertion stays green against a non-generic
        `Resolved` with `value: object`. Only `assert_type` above catches that, which is why
        the criterion rests there. Kept because equality is what `FailureCaseTest` uses to
        detect two failure cases collapsing into one, so it is worth pinning that `Resolved`
        did not acquire an `__eq__` of its own.
        """
        self.assertEqual(Resolved("main"), Resolved("main"))
        self.assertNotEqual(Resolved("main"), Resolved("develop"))
        self.assertNotEqual(Resolved("main"), Resolved(Path("main")))


class MatchTest(unittest.TestCase):
    def test_the_union_has_exactly_two_members(self) -> None:
        """`assert_never` is the assertion, and it is mypy's: adding a third member to the
        union without handling it here fails the build. The runtime half only proves both
        arms are reachable, which is what stops this passing on a match that never matched.
        """
        seen: list[str] = []
        outcomes: list[Resolved[str] | Unresolved] = [
            Resolved("main"),
            Unresolved(
                reason="origin/HEAD is not set", hint="git remote set-head origin --auto"
            ),
        ]
        for outcome in outcomes:
            match outcome:
                case Resolved(value=value):
                    seen.append(f"resolved:{value}")
                case Unresolved(hint=hint):
                    seen.append(f"unresolved:{hint}")
                case unreachable:
                    assert_never(unreachable)
        self.assertEqual(
            seen, ["resolved:main", "unresolved:git remote set-head origin --auto"]
        )

    def test_an_unresolved_is_not_a_resolved_of_nothing(self) -> None:
        """The two are separate classes rather than one class with an optional value, so no
        `isinstance` check can confuse them and there is no `Resolved(None)` to mean
        failure. `Resolved(None)` is a perfectly good success carrying `None`."""
        self.assertNotIsInstance(Unresolved(reason="r", hint="h"), Resolved)
        self.assertNotIsInstance(Resolved(None), Unresolved)


if __name__ == "__main__":
    unittest.main()
