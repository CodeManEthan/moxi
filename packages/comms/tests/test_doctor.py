"""`moxi comms doctor`: read-only, every fact in one place.

It prints the device and comms' five directories under Moxi's roots, the config file,
the backend and where it came from, the secrets chain, the
adapter binaries on PATH with their versions, and whether the daemon answers. It writes
nothing and exits 0 unless the daemon is configured and down.
"""

from __future__ import annotations

import io
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from moxi_comms import cli, config, paths


def doctor(env=None, socket_path=None):
    """One doctor run with this test's environment (the rig's tmp roots) under `env`."""
    out = io.StringIO()
    ctx = cli.Context(record=object(), env=dict(os.environ, **(env or {})),
                      socket_path=socket_path, out=out, db_path=Path("/nonexistent/db"))
    code = cli.main(["doctor"], ctx=ctx)
    return code, out.getvalue()


def rows(out: str) -> dict:
    found = {}
    for line in out.splitlines():
        if line[:1] != " " and "  " in line:
            label, _, value = line.partition("  ")
            found[label.strip()] = value.strip()
        elif line.startswith("  ") and not line.startswith("            "):
            label, _, value = line.strip().partition("  ")
            found[label.strip()] = value.strip()
    return found


def tree(root: Path) -> list:
    return sorted((str(p.relative_to(root)), p.stat().st_mtime_ns)
                  for p in root.rglob("*")) if root.exists() else []


def test_a_box_with_its_daemon_up(rig):
    code, out = rig.cli(["doctor"])
    assert code == 0, out
    r = rows(out)
    assert r["device"] == "testbox"
    home = Path(os.environ["MOXI_COMMS_HOME"])
    assert r["DATA"] == str(home / "data" / "comms") == str(paths.data_dir())
    assert r["STATE"] == str(home / "state" / "comms")
    assert r["CACHE"] == str(home / "cache" / "comms")
    assert r["CONFIG"] == str(home / "config" / "comms") == str(paths.config_dir())
    assert r["RUNTIME"] == str(paths.runtime_dir())
    assert r["backend"] == "local (the default)"
    assert r["secrets"] == "env, store (the default)"
    assert r["daemon"].startswith("answers at ")
    assert set(r) == {"device", "DATA", "STATE", "CACHE", "CONFIG", "RUNTIME", "config",
                      "backend", "secrets", "cache", "claude", "codex", "opencode",
                      "daemon", "waiting", "turns"}, out


def test_adapter_binaries_and_their_versions(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text("#!/bin/sh\necho '9.9.9 (Claude Code)'\n")
    fake.chmod(0o755)
    _, out = doctor(env={"PATH": str(bin_dir)})
    r = rows(out)
    assert r["claude"] == f"{fake}, 9.9.9 (Claude Code)"
    assert r["codex"] == "not on PATH"


def test_it_writes_nothing_and_a_down_daemon_with_no_config_is_exit_0(tmp_path):
    home = Path(os.environ["MOXI_COMMS_HOME"])
    runtime = paths.runtime_dir()
    before, before_rt = tree(tmp_path), tree(runtime)
    code, out = doctor(env={"PATH": ""})
    assert code == 0, out
    assert rows(out)["daemon"].startswith("not answering at ")
    assert rows(out)["config"].startswith("none (")
    assert tree(tmp_path) == before and tree(runtime) == before_rt
    assert not home.exists()


def test_a_configured_box_whose_daemon_is_down_exits_offline(tmp_path):
    path = config.config_path()
    path.parent.mkdir(parents=True)
    path.write_text('backend = "local"\n')
    code, out = doctor(env={"PATH": ""})
    assert code == cli.EXIT_OFFLINE
    assert rows(out)["config"] == str(path)
    assert rows(out)["backend"] == "local (the config file)"
    code, _ = doctor(env={"PATH": "", "MOXI_COMMS_BACKEND": "local"})
    assert code == cli.EXIT_OFFLINE
    path.unlink()
    code, _ = doctor(env={"PATH": "", "MOXI_COMMS_BACKEND": "mock"})
    assert code == cli.EXIT_OFFLINE                         # the env var configures it too


def test_a_bad_config_is_reported_not_raised(tmp_path):
    path = config.config_path()
    path.parent.mkdir(parents=True)
    path.write_text("[secrets]\nchain = [\"vault\"]\n")
    code, out = doctor(env={"PATH": ""})
    assert rows(out)["config"].startswith("ERROR: ")
    assert code == 0                    # nothing configured it as far as doctor could read


def test_a_real_doctor_process_runs_as_moxi_comms(tmp_path):
    """A real process: the usage line names `moxi comms`, and doctor writes nothing."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "CLAUDE"))}
    env.update(HOME=str(tmp_path / "u"), PATH=os.path.dirname(sys.executable))
    proc = subprocess.run([sys.executable, "-m", "moxi_comms.cli", "doctor"], env=env,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert rows(proc.stdout)["device"] == "testbox"
    usage = subprocess.run([sys.executable, "-m", "moxi_comms.cli", "--help"], env=env,
                           capture_output=True, text=True, timeout=60)
    assert usage.stdout.startswith("usage: moxi comms ")
    assert not (tmp_path / "u").exists()


@pytest.fixture
def no_device(tmp_path, monkeypatch):
    """No device variable, and the core's roots under a tmp `MOXI_HOME`; returns the
    core's state root."""
    monkeypatch.delenv("MOXI_COMMS_DEVICE")
    monkeypatch.delenv("MOXI_DEVICE", raising=False)
    monkeypatch.setenv("MOXI_HOME", str(tmp_path / "moxi"))
    return tmp_path / "moxi" / "state"


def test_with_no_device_id_yet_it_says_so_and_writes_none(no_device):
    code, out = doctor(env={"PATH": ""})
    assert code == 0, out
    assert rows(out)["device"].startswith("none set yet (")
    assert not no_device.exists()


def test_it_shows_a_device_id_that_exists(no_device):
    (no_device / "moxi").mkdir(parents=True)
    (no_device / "moxi" / "device-id").write_text("0123abcd\n")
    _, out = doctor(env={"PATH": ""})
    assert rows(out)["device"] == "0123abcd"


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="POSIX modes, which root ignores")
@pytest.mark.parametrize("mode", [stat.S_IRUSR | stat.S_IXUSR, 0])
def test_a_state_root_it_cannot_write_or_read_is_a_row(no_device, mode):
    (no_device / "moxi").mkdir(parents=True)
    no_device.chmod(mode)
    try:
        code, out = doctor(env={"PATH": ""})
    finally:
        no_device.chmod(stat.S_IRWXU)
    assert code == 0, out
    device = rows(out)["device"]
    assert device.startswith("none set yet (" if mode else "ERROR: "), device
    assert sorted(p.name for p in no_device.iterdir()) == ["moxi"]
    assert list((no_device / "moxi").iterdir()) == []
