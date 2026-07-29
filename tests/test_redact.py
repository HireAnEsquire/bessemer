"""Tests for the shared redactor: what it removes, and how much it lets through.

`bessemer/redact.py` sits on the path of everything bessemer prints that it did not write
itself — the resolvers' reasons, doctor's four failure messages, and from F3 a pull request
body. It had no tests of its own when it was promoted out of `bessemer.resolve`: its
behaviour was covered incidentally, through consumers, which is how `DETAIL_LIMIT` came to
be read by two modules by two different routes with nothing asserting its value.

Nothing here spawns or reaches the network; the credential-bearing text is a literal, for
the reason `tests/test_resolve.py` writes one — git prints a remote URL while *contacting*
the remote, and this suite may not.
"""

import unittest
from pathlib import Path
from typing import Final

from bessemer import doctor, redact, resolve
from bessemer.config import ADAPTER_DIR, Config
from bessemer.outcome import Unresolved

TOKEN: Final = "ghp_thisisnotarealtokenbutitlookslikeone"
CREDENTIAL_URL: Final = f"https://x-access-token:{TOKEN}@github.com/HireAnEsquire/bessemer.git"


class LimitTest(unittest.TestCase):
    """The cap, restated by hand — the assertion its consumers cannot make.

    `bessemer.resolve._quote` truncates with it directly and `redact.detail` truncates with
    it internally, so both read the same constant and neither would notice it changing. A
    cap raised to 20000 would put a screen of another program's advice into a check line and
    every test that reads a short message would stay green.
    """

    def test_the_cap_is_two_hundred_characters(self) -> None:
        self.assertEqual(redact.DETAIL_LIMIT, 200)

    def test_detail_truncates_at_the_cap(self) -> None:
        self.assertEqual(len(redact.detail("x" * 500)), 200)

    def test_a_reason_built_by_a_resolver_is_capped_too(self) -> None:
        """The second consumer, reached by its own route: `resolve` truncates a configured
        value with the same constant. Asserted through the resolver rather than through the
        helper, so the two routes are both covered from outside."""
        # A `base` with a space in it: unusable as a branch name, so the resolver echoes the
        # value back in its reason without running git — which is the truncating path.
        outcome = resolve.resolve_base(_config_with_base("y " * 400))
        assert isinstance(outcome, Unresolved), outcome
        self.assertIn("y y", outcome.reason)
        self.assertNotIn("y " * 200, outcome.reason)


class RedactionTest(unittest.TestCase):
    def test_userinfo_in_a_url_is_replaced(self) -> None:
        self.assertEqual(
            redact.redacted(f"fatal: unable to access '{CREDENTIAL_URL}'"),
            "fatal: unable to access 'https://<redacted>@github.com/HireAnEsquire/bessemer.git'",
        )

    def test_every_shape_of_credential_bearing_url_is_redacted(self) -> None:
        """Whole-userinfo rather than token-prefix matching: `ghp_`, `github_pat_`, `glpat-`
        and whatever the next forge invents are a list that goes stale, while "the part of a
        URL before the `@`" is the grammar itself."""
        for url in (
            f"https://{TOKEN}@github.com/o/r.git",
            f"https://x-access-token:{TOKEN}@github.com/o/r.git",
            f"https://oauth2:glpat-{TOKEN}@gitlab.com/o/r.git",
            f"http://user:{TOKEN}@internal.example/o/r.git",
            f"ssh://git:{TOKEN}@github.com/o/r.git",
        ):
            with self.subTest(url=url):
                self.assertNotIn(TOKEN, redact.redacted(url))
                self.assertIn("<redacted>@", redact.redacted(url))

    def test_an_scp_style_remote_carries_no_secret_and_is_left_alone(self) -> None:
        """`git@github.com:o/r.git` has no `://` and no userinfo — redacting it would delete
        the username out of a message that never had a credential in it."""
        self.assertEqual(redact.redacted("git@github.com:o/r.git"), "git@github.com:o/r.git")

    def test_a_secret_that_is_not_in_a_url_is_the_named_limit(self) -> None:
        """Stated as a test rather than only in a docstring: this is a redactor for how git
        leaks credentials, not a general secret scanner. Left unstated, the next reader
        believes something false about what reaches a pull request body."""
        self.assertIn(TOKEN, redact.redacted(f"error: token {TOKEN} is expired"))


class DetailTest(unittest.TestCase):
    def test_only_the_first_non_empty_line_survives(self) -> None:
        """The later lines of a git or docker failure are advice aimed at an interactive
        user; the first is the `fatal:`/`error:`."""
        self.assertEqual(
            redact.detail("\n  \nfatal: the point\nhint: a paragraph of advice\n"),
            "fatal: the point",
        )

    def test_silence_is_empty_rather_than_a_bare_colon(self) -> None:
        self.assertEqual(redact.detail("   \n\n"), "")

    def test_redaction_happens_before_truncation(self) -> None:
        """Applied the other way round, the redactor is handed a line it never saw: a URL cut
        mid-userinfo has no `@` left to match, so the pattern does not fire and the start of
        the token is printed.

        **The padding is measured, not decorative.** A first version of this test padded with
        300 characters, which pushed the whole URL past the cap — truncate-first then leaked
        nothing, and the test passed against the defect it was named for. The URL has to
        *straddle* the cap: `ghp_` lands at character 170 and the `@` that ends the userinfo
        at 210, so a cut at 200 falls between them. That is the only arrangement in which the
        two orders differ at all, and the two assertions below are what keep this test honest
        if either the cap or the fixture text is ever edited.
        """
        padded = f"fatal: {'x' * 140}{CREDENTIAL_URL}"
        self.assertLess(padded.index("ghp_"), redact.DETAIL_LIMIT)
        self.assertGreater(padded.index("@github.com"), redact.DETAIL_LIMIT)

        detail = redact.detail(padded)
        self.assertNotIn("ghp_", detail)
        self.assertIn("<redacted>@", detail)


class ConsumerTest(unittest.TestCase):
    """One redactor, not two. A second regex is two redactors that can disagree."""

    def test_doctor_and_resolve_use_the_same_module(self) -> None:
        # Through `vars()` rather than attribute access: mypy refuses a read of a name a
        # module imported but does not re-export, which is exactly what is being asserted.
        self.assertIs(vars(doctor)["redact"], redact)
        self.assertIs(vars(resolve)["redact"], redact)

    def test_neither_consumer_defines_a_redactor_of_its_own(self) -> None:
        for module in (doctor, resolve):
            with self.subTest(module=module.__name__):
                self.assertFalse(hasattr(module, "_CREDENTIAL_IN_URL"))
                self.assertFalse(hasattr(module, "_redact"))


def _config_with_base(value: object) -> Config:
    """A `Config` whose committed layer sets `base`, built directly rather than loaded."""
    empty: dict[str, object] = {}
    return Config(
        adapter_dir=Path("/nowhere") / ADAPTER_DIR,
        committed={"base": value},
        local=empty,
        env=empty,
        flags=empty,
    )


if __name__ == "__main__":
    unittest.main()
