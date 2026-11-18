"""What every test of the launcher shares: the one time its launches are named at, and a
short startup watch."""

from __future__ import annotations

from datetime import datetime

import pytest
from moxi import config

from moxi_launcher import names, settings, startup

#: The local time every launch a test makes in its own process is named at.
NAMED_AT = datetime(2026, 11, 8, 12, 45)


@pytest.fixture(autouse=True)
def one_naming_time(monkeypatch):
    """A launch's name holds the minute it was made in, so a test's launches are all
    named at one time: a preview and the launch it previews can't fall in two minutes."""
    monkeypatch.setattr(names, "now", lambda: NAMED_AT)


#: The startup watch's bound, in seconds, in a test whose launcher config doesn't set
#: `startup.wait_s`.
WATCH_S = 0.5


@pytest.fixture(autouse=True)
def short_startup_watch(monkeypatch):
    """Every Claude slot launch watches its startup screens, and without turns the watch
    runs to `startup.wait_s` (20 s by default). A test that sets the key in its config
    file gets that bound; any other gets `WATCH_S`, in this process."""
    real = startup.watch

    def watch(*args, wait_s=None, **kwargs):
        if wait_s is None:
            current = settings.current()
            wait_s = (current["startup.wait_s"]
                      if current.source("startup.wait_s") == config.FILE else WATCH_S)
        return real(*args, wait_s=wait_s, **kwargs)

    monkeypatch.setattr(startup, "watch", watch)
