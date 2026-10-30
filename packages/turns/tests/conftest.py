"""Shared fixtures: a short runtime root (AF_UNIX paths stop at 108 bytes), a config home
of the test's own, and an endpoint driven by the fake harness."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from fake_harness import FakeHarness
from moxi_turns import listeners
from moxi_turns.endpoint import Endpoint


@pytest.fixture(autouse=True)
def state_home(monkeypatch, tmp_path_factory):
    """Every test keeps turns' state (the hook times among it) in a folder of its own."""
    root = tmp_path_factory.mktemp("state")
    monkeypatch.setenv("MOXI_TURNS_STATE_HOME", str(root))
    return root


@pytest.fixture
def turns_env(monkeypatch, tmp_path_factory):
    root = tmp_path_factory.mktemp("mt")
    (root / "cfg").mkdir()
    monkeypatch.setenv("MOXI_TURNS_RUNTIME_DIR", str(root / "rt"))
    monkeypatch.setenv("MOXI_TURNS_CONFIG_HOME", str(root / "cfg"))
    monkeypatch.delenv("MOXI_TURNS_COMMANDS", raising=False)
    monkeypatch.delenv("MOXI_TURNS_HOME", raising=False)
    yield root


def bare(value):
    """A delivery's value without the delivery id and the ref it echoes."""
    return dataclasses.replace(value, delivery=None, ref=None)


def opener(event):
    """The id of the delivery that opened a `TurnStarted` or `TurnEnded`'s turn, or None
    for the person's own turn (and for any other event)."""
    for entry in getattr(event, "deliveries", ()):
        if entry.how == "opened":
            return entry.id
    return None


class CoreDelivery:
    """A delivery made on the endpoint core rather than through the client, with the
    client's two waits. The steering `deliver` (a steer with a deliver to fall back on)
    has no wire method, so its tests drive the core."""

    def __init__(self, endpoint, handed):
        self.endpoint = endpoint
        self.id = handed.id
        self.ref = handed.ref

    def wait_started(self, timeout):
        return self._wait("started", timeout)

    def wait_ended(self, timeout):
        return self._wait("ended", timeout)

    def _wait(self, until, timeout):
        value = self.endpoint.wait(self.id, until, timeout)
        if value is None:
            raise TimeoutError(f"delivery {self.id} has not {until} after {timeout} s")
        return value


def steer_first(endpoint, text: str) -> CoreDelivery:
    """The core's steering deliver: into the running turn, or a new turn when none runs."""
    return CoreDelivery(endpoint, endpoint.deliver(text, steer=True))


def write_config(root: Path, text: str) -> None:
    (root / "cfg" / "turns").mkdir(parents=True, exist_ok=True)
    (root / "cfg" / "turns" / "config.toml").write_text(text)


@pytest.fixture
def make_endpoint(turns_env):
    made = []

    def make(harness=None, *, registry=None, session_id="s-1", env=None):
        harness = harness or FakeHarness()
        ep = Endpoint(harness, registry=registry or listeners.Registry(),
                      session_id=session_id, env=env)
        made.append(ep.open())
        return ep, harness

    yield make
    for ep in made:
        ep.stop("the test ended")


@pytest.fixture(autouse=True)
def claude_root(monkeypatch, tmp_path_factory):
    """Every test sees a folder marketplace of its own, never the package's: a test run in
    an environment whose sessions load the plugin must not rewrite that environment's
    `endpoint.json`."""
    from moxi_turns import claude_setup

    root = tmp_path_factory.mktemp("claude")
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
        "name": "moxi-turns",
        "plugins": [{"name": "moxi-turns", "source": "./moxi-turns"},
                    {"name": "moxi-turns-gate", "source": "./moxi-turns-gate"}]}))
    for name in ("moxi-turns", "moxi-turns-gate"):
        (root / name / ".claude-plugin").mkdir(parents=True)
        (root / name / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": name, "version": "0.1.0"}))
    monkeypatch.setattr(claude_setup, "claude_root", lambda: root)
    return root
