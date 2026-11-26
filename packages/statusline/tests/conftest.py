"""Fixtures for statusline's suite.

Every test runs inside the kit's fence (the package's root `conftest.py`): HOME, Claude's
folder and statusline's home are the test's own.
"""

from __future__ import annotations

import sys

import pytest
from statusline_kit import Site

from moxi import modules


@pytest.fixture
def site(tmp_path) -> Site:
    root = tmp_path / "site"
    root.mkdir()
    return Site(root)


@pytest.fixture
def seen(site, monkeypatch) -> Site:
    """`site`, seen by discovery in this process."""
    monkeypatch.syspath_prepend(str(site.root))
    modules.reset()
    yield site
    modules.reset()
    for name in [m for m in sys.modules if m.startswith(("moxi_alpha", "moxi_beta",
                                                          "moxi_gamma"))]:
        del sys.modules[name]


@pytest.fixture(autouse=True)
def _no_effort_variable(monkeypatch):
    """Claude Code may set `CLAUDE_EFFORT` in the shell the tests run in; a test sets it
    itself when it reads it."""
    monkeypatch.delenv("CLAUDE_EFFORT", raising=False)
