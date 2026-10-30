"""The server wrapper's parts: its environment, its config, the launch-file sweep, its
refusals, and `ensure` where a supervisor runs."""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

from moxi_slots import places, server
from moxi_slots.errors import TryAgain

from slots_kit import record

pytestmark = pytest.mark.touches("packages/slots/src/moxi_slots/server.py")


def test_the_server_environment_keeps_only_the_named_variables():
    env = server.clean_env({"HOME": "/h", "USER": "u", "LOGNAME": "u", "SHELL": "/bin/sh",
                            "PATH": "/bin", "LANG": "C.UTF-8", "LC_TIME": "C",
                            "XDG_RUNTIME_DIR": "/run/user/1", "TMUX": "x",
                            "TMUX_PANE": "%1", "SECRET_TOKEN": "s", "MOXI_HOME": "/m",
                            "TERM": "xterm"})
    assert env == {"HOME": "/h", "USER": "u", "LOGNAME": "u", "SHELL": "/bin/sh",
                   "PATH": "/bin", "LANG": "C.UTF-8", "LC_TIME": "C",
                   "XDG_RUNTIME_DIR": "/run/user/1"}


def test_the_user_config_is_the_first_tmux_would_read(fence):
    home = fence.short / "h"
    xdg = fence.short / "x"
    (xdg / "tmux").mkdir(parents=True)
    (home / ".config" / "tmux").mkdir(parents=True)
    env = {"HOME": str(home), "XDG_CONFIG_HOME": str(xdg)}
    assert server.user_conf(env) is None
    (home / ".config" / "tmux" / "tmux.conf").write_text("")
    assert server.user_conf(env) == str(home / ".config" / "tmux" / "tmux.conf")
    (xdg / "tmux" / "tmux.conf").write_text("")
    assert server.user_conf(env) == str(xdg / "tmux" / "tmux.conf")
    (home / ".tmux.conf").write_text("")
    assert server.user_conf(env) == str(home / ".tmux.conf")


def test_the_config_sources_the_users_then_sets_the_options_after(fence):
    home = fence.short / "h"
    home.mkdir()
    (home / ".tmux.conf").write_text("")
    text = server.config_text(None, {"HOME": str(home)})
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    assert lines == ["source-file -q '/etc/tmux.conf'",
                     f"source-file -q '{home}/.tmux.conf'",
                     "set -s exit-empty off", "set -s exit-unattached off",
                     "set -g destroy-unattached off"]


def test_a_named_config_replaces_the_users():
    text = server.config_text("/conf/it's mine.conf", {"HOME": "/nowhere"})
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    assert lines[0] == 'source-file -q "/conf/it\'s mine.conf"'
    assert len(lines) == 4


def test_the_sweep_removes_launch_files_older_than_the_bound(fence):
    directory = fence.short / "launch"
    directory.mkdir()
    old, new = directory / "old", directory / "new"
    old.write_text("env")
    new.write_text("env")
    past = time.time() - 120
    os.utime(old, (past, past))
    assert server.sweep_launch_files(directory, older_than=60) == [old]
    assert new.exists()
    assert server.sweep_launch_files(directory) == [new]
    assert server.sweep_launch_files(fence.short / "none") == []


def test_the_wrapper_takes_no_arguments():
    assert server.main(["--now"]) == 2


def test_the_wrapper_refuses_without_a_recorded_tmux():
    assert server.main([]) == 1


def test_the_wrapper_refuses_tmux_below_the_floor(fake_tmux):
    record("tmux", str(fake_tmux("tmux 3.0")))
    assert server.main([]) == 3


def test_ensure_never_starts_a_server_a_supervisor_should_run(fake_tmux, monkeypatch):
    record("tmux", str(fake_tmux()))
    monkeypatch.setattr(server, "supervised", lambda: True)
    monkeypatch.setattr(server, "unit_state", lambda: None)
    started = []
    monkeypatch.setattr(server, "_start", lambda wait: started.append(wait))
    with pytest.raises(TryAgain, match="moxi doctor slots"):
        server.ensure()
    assert started == []


def test_a_missing_unit_names_the_install(fake_tmux, monkeypatch):
    from moxi.units import UnitState

    record("tmux", str(fake_tmux()))
    monkeypatch.setattr(server, "supervised", lambda: True)
    monkeypatch.setattr(server, "unit_state", lambda: UnitState(
        places.MODULE, "server", "daemon", "systemd", "missing"))
    monkeypatch.setattr(server, "editable", lambda: False)
    with pytest.raises(TryAgain, match="moxi install slots"):
        server.ensure()


def test_a_missing_unit_on_an_editable_install_says_to_start_the_server_by_hand(
        fake_tmux, monkeypatch):
    from moxi.units import UnitState

    record("tmux", str(fake_tmux()))
    monkeypatch.setattr(server, "supervised", lambda: True)
    monkeypatch.setattr(server, "unit_state", lambda: UnitState(
        places.MODULE, "server", "daemon", "systemd", "missing"))
    monkeypatch.setattr(server, "editable", lambda: True)
    with pytest.raises(TryAgain, match="start `moxi-slots-server` by hand") as caught:
        server.ensure()
    assert "moxi install" not in str(caught.value)


def test_editable_reads_the_cores_editable_refusal(monkeypatch):
    from moxi import units

    def plan(cause):
        return lambda module, only=None: [SimpleNamespace(cause=cause)]

    monkeypatch.setattr(units, "plan", plan("editable"))
    assert server.editable()
    monkeypatch.setattr(units, "plan", plan(None))
    assert not server.editable()

    def broken(module, only=None):
        raise ValueError("slots is absent")

    monkeypatch.setattr(units, "plan", broken)
    assert not server.editable()
