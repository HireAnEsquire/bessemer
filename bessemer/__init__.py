"""Bessemer core: the dispatcher package itself.

The core never enters a consuming repository — it is fetched by pin at run time
(ADR 0001, "distribution: uvx pinned from git").
"""

from importlib.metadata import version

# Read from distribution metadata, with no fallback. A hand-maintained literal drifts from
# pyproject.toml and starts lying; a fallback sentinel would only serve running from an
# uninstalled source tree, which is not an invocation this project endorses. If this raises
# PackageNotFoundError, bessemer is not installed — run it under `uv run` or `uvx`.
__version__ = version("bessemer")

__all__ = ["__version__"]
