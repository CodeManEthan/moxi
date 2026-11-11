"""What every test of the launcher shares: the one time its launches are named at."""

from __future__ import annotations

from datetime import datetime

import pytest

from moxi_launcher import names

#: The local time every launch a test makes in its own process is named at.
NAMED_AT = datetime(2026, 11, 8, 12, 45)


@pytest.fixture(autouse=True)
def one_naming_time(monkeypatch):
    """A launch's name holds the minute it was made in, so a test's launches are all
    named at one time: a preview and the launch it previews can't fall in two minutes."""
    monkeypatch.setattr(names, "now", lambda: NAMED_AT)
