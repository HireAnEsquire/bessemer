"""Bessemer's unit suite.

The suite must pass with no Docker daemon, no network, and outside any git work tree.
Importing this package arms the guard that makes that structural — see tests/guard.py for
how, and tests/README.md for why.
"""

from tests import guard

guard.install()
