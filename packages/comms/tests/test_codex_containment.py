"""The Codex roster settings handed to turns.

turns owns cwd, developer instructions and approval policy. comms owns the roster and
hands every other Codex thread setting to turns' Codex host as its flags; the host checks
each load answer against the sandbox they name.
"""

from __future__ import annotations

import os

import pytest

from moxi_comms.adapters import codex_wrapper
from moxi_comms.adapters.codex_roster import CodexAgent, record_write_root


def agent(tmp_path, **changes):
    values = dict(qualified="pilot@proj-a", name="pilot", scope="proj-a",
                  cwd=str(tmp_path), thread_file=tmp_path / "pilot.json",
                  writable_roots=[tmp_path / "row"], model="gpt-sample")
    values.update(changes)
    return CodexAgent(**values)


def test_workspace_options_carry_every_roster_owned_setting(tmp_path):
    added = tmp_path / "record"
    options = agent(tmp_path, network_access=False).launch_options(added)
    assert options == {
        "sandbox": "workspace-write",
        "model": "gpt-sample",
        "config": {
            "agents": {"enabled": False},
            "sandbox_workspace_write": {
                "writable_roots": [str(tmp_path / "row"), str(added)],
                "network_access": False,
            },
        },
    }
    assert not {"cwd", "developerInstructions", "approvalPolicy"} & options.keys()


def test_the_added_root_is_not_listed_twice(tmp_path):
    root = tmp_path / "row"
    options = agent(tmp_path).launch_options(root)
    assert options["config"]["sandbox_workspace_write"]["writable_roots"] == [str(root)]


def test_a_permission_profile_replaces_the_sandbox_and_gets_no_added_root(tmp_path):
    options = agent(tmp_path, sandbox=None, permissions="locked",
                    profile={"network": {"enabled": True}}).launch_options(
                        tmp_path / "record")
    assert options == {
        "permissions": "locked",
        "model": "gpt-sample",
        "config": {
            "agents": {"enabled": False},
            "permissions": {"locked": {"network": {"enabled": True}}},
        },
    }


def host_options(row, added, cwd):
    """The thread parameters turns' host reads from the wrapper's flags for `row`."""
    base = codex_wrapper.row_flags(row)
    asked = codex_wrapper.launch_args(["--cwd", str(cwd), *base])["options"]
    flags = base + codex_wrapper.comms_flags(asked, added)
    return codex_wrapper.launch_args(["--cwd", str(cwd), *flags])["options"]


@pytest.mark.parametrize("changes", [
    {},
    {"network_access": False},
    {"writable_roots": []},
    {"writable_roots": ["row"]},
    {"model": None},
    {"sandbox": "read-only"},
    {"sandbox": "danger-full-access", "writable_roots": []},
    {"writable_roots": ["has space", 'quote"d', "dot.ted", "ünï"]},
    {"sandbox": None, "permissions": "locked", "profile": {"network": {"enabled": True}}},
    {"sandbox": None, "permissions": "a.b c", "profile": {
        "filesystem": {"read": ["/x", "/y y"], "write": []}, "network": {"enabled": False},
        "note": "line\nbreak", "count": 3, "ratio": 0.5}},
    {"sandbox": None, "permissions": "bare"},
], ids=["workspace", "network-off", "no-roots", "added-already", "no-model", "read-only",
        "full-access", "odd-roots", "profile", "odd-profile", "bare-profile"])
def test_the_host_flags_give_the_options_a_roster_row_gives(tmp_path, changes):
    """One launch path: the wrapper's flags for a roster row, read by turns' host, are
    the thread parameters the row's `launch_options()` gives."""
    changes = dict(changes)
    added = tmp_path / "record"
    if changes.get("writable_roots"):
        changes["writable_roots"] = [str(tmp_path / r) for r in changes["writable_roots"]]
    if changes.get("writable_roots") == [str(tmp_path / "row")]:
        added = tmp_path / "row"
    row = agent(tmp_path, **changes)
    assert host_options(row, added, tmp_path) == row.launch_options(added)


def test_a_sandbox_row_carries_its_sandbox_so_the_host_checks_every_load(tmp_path):
    """The host checks a load answer whenever the options name a sandbox; a row always
    names one unless it names a permission profile."""
    assert host_options(agent(tmp_path), None, tmp_path)["sandbox"] == "workspace-write"
    assert "sandbox" not in host_options(agent(tmp_path, sandbox=None, permissions="p"),
                                         None, tmp_path)


def test_the_roster_has_no_load_verifier_of_its_own():
    assert not hasattr(CodexAgent, "verify_load")


def test_the_mock_backend_adds_the_store_directory(monkeypatch, tmp_path):
    store = tmp_path / "mock" / "store.json"
    monkeypatch.setenv("MOXI_COMMS_BACKEND", "mock")
    monkeypatch.setenv("MOXI_COMMS_MOCK_BACKEND", str(store))
    root, reason = record_write_root(env=os.environ, cwd=tmp_path)
    assert root == store.parent
    assert str(store) in reason


def test_a_bad_config_adds_no_root_and_says_why(monkeypatch, tmp_path):
    from moxi_comms import config as config_mod
    from moxi_comms.config import ConfigError

    monkeypatch.setattr(config_mod, "load", lambda **kwargs: (_ for _ in ()).throw(
        ConfigError("bad config")))
    root, reason = record_write_root(env=os.environ, cwd=tmp_path)
    assert root is None
    assert "could not be read" in reason
