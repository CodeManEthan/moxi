"""What every test of the launcher shares: the one time its launches are named at, a
short startup watch, and the `slot_target` mark, which skips a test of the slot target on
native Windows unless the test is also marked `on_windows`."""

from __future__ import annotations

import sys
from datetime import datetime

import pytest
from moxi import config

from moxi_launcher import names, settings, startup

#: Why a test marked `slot_target` is skipped on native Windows.
SLOT_TARGET_SKIP = ("the slot target needs slots, which runs on Linux, macOS and WSL2, "
                    "not native Windows")


def pytest_configure(config):
    config.addinivalue_line("markers", f"slot_target: a test of the slot target; {SLOT_TARGET_SKIP}")
    config.addinivalue_line("markers", "on_windows: a slot target test of a path native Windows "
                            "runs, such as its refusal there; it is not skipped there")


def pytest_collection_modifyitems(items):
    if sys.platform != "win32":
        return
    skip = pytest.mark.skip(reason=SLOT_TARGET_SKIP)
    for item in items:
        if item.get_closest_marker("slot_target") and not item.get_closest_marker("on_windows"):
            item.add_marker(skip)


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
