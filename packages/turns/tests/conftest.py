"""Shared fixtures: a short runtime root (AF_UNIX paths stop at 108 bytes), a config home
a test can write to, an endpoint driven by the fake harness, and a stand-in `claude`.

Every test runs inside the kit's fence (the package's root `conftest.py`), which empties
the environment (`MOXI_TURNS_COMMANDS` and the rest) and points every home into tmp. On top
of it, every test finds the stand-in `claude` first on PATH and turns' data and config
homes in its own tmp, so a test that reads Claude Code's state through turns' code (the
page server's reach among them) reaches only the stand-in and finds no placed copy but one
the test made."""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

import pytest

from fake_harness import FakeHarness
from moxi_turns import listeners
from moxi_turns.endpoint import Endpoint


@pytest.fixture(autouse=True)
def no_installed_listeners(monkeypatch):
    """The listener group reads empty, so no installed module's listener runs in a test.

    The workspace installs modules (usage, comms and the rest) whose listeners keep their
    own stores, and an endpoint built without a registry would load them. A listener call
    can outlive the test that started it, and it finds its store from the environment of
    the moment: past the test's end that is the real one. A test that wants rows passes a
    registry, or sets `moxi.modules.extensions` itself."""
    from moxi import modules
    from moxi_turns import listeners

    real = modules.extensions
    monkeypatch.setattr(modules, "extensions",
                        lambda group: [] if group == listeners.GROUP else real(group))


@pytest.fixture(autouse=True)
def only_turns_guards(monkeypatch, no_installed_listeners):
    """The guard group reads turns' own entry alone, so no installed module's guard joins
    a test's table. The workspace installs modules whose guards are on by default where
    another module is installed (the launcher's wake guard, with cache), and a test reads
    the table it builds. A test that wants another module's guard passes `entries`, or
    sets `moxi.modules.extensions` itself."""
    from moxi import modules
    from moxi_turns import guards

    real = modules.extensions
    monkeypatch.setattr(modules, "extensions", lambda group: (
        [e for e in real(group) if e.name == "turns"] if group == guards.GROUP
        else real(group)))


@pytest.fixture(autouse=True)
def state_home(monkeypatch, tmp_path_factory):
    """Every test keeps turns' state (the hook times among it) in a folder of its own."""
    root = tmp_path_factory.mktemp("state")
    monkeypatch.setenv("MOXI_TURNS_STATE_HOME", str(root))
    return root


#: The stand-in `claude`: it logs each call's arguments and answers the calls turns makes
#: from files in `$FAKE_CLAUDE_DIR`.
FAKE_CLAUDE = """#!/bin/sh
echo "$*" >> "$FAKE_CLAUDE_DIR/log"
case "$*" in
  "--version") echo "$FAKE_CLAUDE_VERSION (Claude Code)" ;;
  "plugin marketplace list --json") cat "$FAKE_CLAUDE_DIR/markets.json" ;;
  "plugin list --json") cat "$FAKE_CLAUDE_DIR/plugins.json" ;;
esac
exit 0
"""


@pytest.fixture(autouse=True)
def stub_claude(monkeypatch, tmp_path_factory):
    """The stand-in `claude` first on PATH, its files and log in the test's own folder,
    and turns' data and config homes in tmp. Returns the folder of its files."""
    root = tmp_path_factory.mktemp("claude-stub")
    bin_dir = root / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "claude"
    exe.write_text(FAKE_CLAUDE)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    state = root / "state"
    state.mkdir()
    (state / "markets.json").write_text("[]")
    (state / "plugins.json").write_text("[]")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_DIR", str(state))
    monkeypatch.setenv("FAKE_CLAUDE_VERSION", "2.1.296")
    monkeypatch.setenv("MOXI_TURNS_DATA_HOME", str(root / "data"))
    monkeypatch.setenv("MOXI_TURNS_CONFIG_HOME", str(root / "config"))
    return state


@pytest.fixture
def turns_env(monkeypatch, tmp_path_factory):
    root = tmp_path_factory.mktemp("mt")
    (root / "cfg").mkdir()
    monkeypatch.setenv("MOXI_TURNS_RUNTIME_DIR", str(root / "rt"))
    monkeypatch.setenv("MOXI_TURNS_CONFIG_HOME", str(root / "cfg"))
    yield root


@pytest.fixture
def short_runtime(turns_env, monkeypatch):
    """A runtime root short enough that every internal socket fits in its endpoint's
    directory. pytest's own root can be long enough under many workers to push the
    longest of them past the core's limit, so a test that needs them in place uses this."""
    root = Path(tempfile.mkdtemp(prefix="mt"))
    monkeypatch.setenv("MOXI_TURNS_RUNTIME_DIR", str(root / "rt"))
    yield root / "rt"
    shutil.rmtree(root, ignore_errors=True)


#: How long a test's cleanup waits for an endpoint to finish ending.
END_WAIT = 30.0


def end_endpoint(ep) -> None:
    """Stop `ep` and wait until it has ended. An endpoint that ended on its own thread
    (its server or session went) may still be telling its listeners `gone` when the test
    is done with it, and a second `stop` returns at once, so the cleanup waits for the
    first to finish: a listener left running past the test runs in the next test's
    environment, or in the real one between tests."""
    ep.stop("the test ended")
    assert ep.wait_closed(END_WAIT), "the endpoint did not finish ending"


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
        end_endpoint(ep)


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
