"""The kit's fence: the environment it leaves, the writes and connects it refuses."""

from __future__ import annotations

import os
import pwd
import shutil
import socket
import socketserver
import sqlite3
import tempfile
import threading
import uuid
from pathlib import Path

import pytest

from moxi_dev import testing
from moxi_dev.testing import FenceError

#: The user's real home, read from the password database: the fence moves only $HOME.
REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)


def refused(action) -> str:
    """Run `action`, which the fence must refuse; take the refusal off the record, so
    this test can go on to pass, and return its message."""
    start = len(testing._GUARD.breaches)
    with pytest.raises(FenceError) as caught:
        action()
    assert len(testing._GUARD.breaches) == start + 1
    del testing._GUARD.breaches[start:]
    return str(caught.value)


def stray() -> Path:
    """A name in the real home that nothing else uses."""
    return REAL_HOME / f".moxi-dev-fence-probe-{uuid.uuid4().hex}"


# --- the environment -------------------------------------------------------------------

def test_the_environment_is_what_the_fence_says(fence, tmp_path):
    current = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    assert current == {k: v for k, v in fence.env.items() if k != "PYTEST_CURRENT_TEST"}
    modules = testing._modules()
    assert modules, "the workspace installs Moxi modules; none found"
    expected = set(testing.HARNESS_HOMES) | {"XDG_RUNTIME_DIR"} | {
        f"MOXI_{m.upper()}_HOME" for m in modules}
    for key in os.environ:
        assert (key in expected or key in testing.KEPT_ENV or key.startswith("LC_")), key
    assert expected <= set(os.environ)
    assert "PATH" in os.environ
    assert os.environ["HOME"] == str(tmp_path / "home") == str(fence.home)
    assert fence.home.is_dir()
    for key, rel in testing.HARNESS_HOMES.items():
        assert Path(os.environ[key]) == fence.home / rel
        assert Path(os.environ[key]).is_relative_to(tmp_path)
    assert os.environ["XDG_RUNTIME_DIR"] == str(fence.runtime)
    assert fence.runtime.is_dir() and (fence.runtime.stat().st_mode & 0o777) == 0o700
    for module in modules:
        home = Path(os.environ[f"MOXI_{module.upper()}_HOME"])
        assert home == fence.module_home(module) and home.parent == fence.short
    # Short enough that a socket a few directories under it fits in sun_path.
    assert len(str(fence.runtime)) < 40


def test_the_fence_clears_everything_else():
    env = testing._fenced_env(
        {"PATH": "/bin", "LANG": "C.UTF-8", "LC_ALL": "C", "OPENCODE_CONFIG_CONTENT": "{}",
         "OPENCODE_SERVER_PASSWORD": "x", "MOXI_COMMS_STATE_HOME": "/real",
         "ANTHROPIC_API_KEY": "k", "SSH_AUTH_SOCK": "/run/agent", "GIT_DIR": "/repo"},
        Path("/t/home"), Path("/s/run"), Path("/s"), ["comms"])
    assert env == {
        "PATH": "/bin", "LANG": "C.UTF-8", "LC_ALL": "C",
        "HOME": "/t/home", "XDG_CONFIG_HOME": "/t/home/.config",
        "XDG_DATA_HOME": "/t/home/.local/share", "XDG_STATE_HOME": "/t/home/.local/state",
        "XDG_CACHE_HOME": "/t/home/.cache", "CLAUDE_CONFIG_DIR": "/t/home/.claude",
        "CODEX_HOME": "/t/home/.codex", "XDG_RUNTIME_DIR": "/s/run",
        "MOXI_COMMS_HOME": "/s/comms"}


# --- writes ----------------------------------------------------------------------------

def test_a_write_to_the_real_home_fails():
    target = stray()
    assert "outside the test's tmp" in refused(lambda: target.write_text("x"))
    assert not target.exists()
    refused(lambda: os.open(target, os.O_WRONLY | os.O_CREAT))
    refused(lambda: os.mkdir(target))
    refused(lambda: os.remove(target))
    refused(lambda: sqlite3.connect(target))
    refused(lambda: tempfile.mkstemp(dir=REAL_HOME))
    assert not target.exists()


def test_a_write_in_the_tmp_goes_through(tmp_path):
    (tmp_path / "a").write_text("x")
    os.rename(tmp_path / "a", tmp_path / "b")
    (tmp_path / "tree" / "deep").mkdir(parents=True)
    (tmp_path / "tree" / "deep" / "f").write_text("x")
    os.symlink(tmp_path / "b", tmp_path / "tree" / "link")
    shutil.rmtree(tmp_path / "tree")             # removes by dir_fd, name by name
    fd, name = tempfile.mkstemp()
    os.close(fd)
    os.remove(name)
    sqlite3.connect(tmp_path / "db.sqlite").close()
    sqlite3.connect(":memory:").close()
    with open(os.devnull, "w") as sink:
        sink.write("x")
    assert (tmp_path / "b").read_text() == "x"


def test_reading_outside_the_tmp_is_not_a_write():
    with open(testing.__file__, "rb") as f:
        assert f.read(1)
    os.close(os.open(testing.__file__, os.O_RDONLY))


def test_a_link_out_of_the_tmp_is_outside(tmp_path):
    os.symlink(REAL_HOME, tmp_path / "out")
    refused(lambda: (tmp_path / "out" / stray().name).write_text("x"))
    os.remove(tmp_path / "out")                  # the link itself is inside


def test_a_rename_out_of_the_tmp_fails(tmp_path):
    (tmp_path / "a").write_text("x")
    refused(lambda: os.rename(tmp_path / "a", stray()))
    refused(lambda: shutil.move(str(tmp_path / "a"), str(stray())))
    assert (tmp_path / "a").exists()


def test_another_thread_is_fenced_too():
    errors = []

    def write():
        try:
            stray().write_text("x")
        except FenceError as exc:
            errors.append(exc)

    start = len(testing._GUARD.breaches)
    thread = threading.Thread(target=write)
    thread.start()
    thread.join()
    del testing._GUARD.breaches[start:]
    assert len(errors) == 1


# --- the network -----------------------------------------------------------------------

@pytest.mark.parametrize("host", ["192.0.2.1", "8.8.8.8", "10.0.0.1"])
def test_a_connect_off_the_machine_fails(host):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        assert "not a loopback address" in refused(lambda: s.connect((host, 9)))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as u:
        refused(lambda: u.sendto(b"x", (host, 9)))


def test_a_connect_to_a_socket_outside_the_tmp_fails():
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        refused(lambda: s.connect(f"/run/user/{os.getuid()}/moxi-dev-probe.sock"))
        refused(lambda: s.bind(str(stray())))


class _Echo(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.sendall(self.request.recv(64))


def _loopbacks():
    hosts = ["127.0.0.1", "localhost"]
    if socket.has_ipv6:
        try:
            with socket.socket(socket.AF_INET6) as s:
                s.bind(("::1", 0))
            hosts.append("::1")
        except OSError:
            pass
    return hosts


@pytest.mark.parametrize("host", _loopbacks())
def test_a_loopback_connect_works(host):
    family = socket.AF_INET6 if ":" in host else socket.AF_INET

    class Server(socketserver.TCPServer):
        address_family = family
        allow_reuse_address = True

    bind = "::1" if family == socket.AF_INET6 else "127.0.0.1"
    with testing.served(lambda _: Server((bind, 0), _Echo)) as server:
        port = server.server_address[1]
        with socket.create_connection((host, port), timeout=5) as c:
            c.sendall(b"ping")
            assert c.recv(64) == b"ping"


# --- a run of its own: what fails, what passes ----------------------------------------

PROBES = '''
import os, pwd, socket, uuid
from pathlib import Path

import pytest

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)


def stray():
    return REAL_HOME / f".moxi-dev-fence-probe-{uuid.uuid4().hex}"


def test_writes_to_the_real_home():
    stray().write_text("x")


def test_swallows_the_refusal():
    try:
        stray().write_text("x")
    except OSError:
        pass


def test_connects_off_the_machine():
    with socket.create_connection(("192.0.2.1", 9), timeout=0.5):
        pass


@pytest.fixture
def leaky():
    yield
    try:
        stray().write_text("x")
    except OSError:
        pass


def test_a_fixture_writes_out_in_teardown(leaky):
    pass


def test_writes_in_its_tmp(tmp_path):
    (tmp_path / "f").write_text("x")


def test_sees_only_the_fenced_environment(fence):
    assert "OPENCODE_CONFIG_CONTENT" not in os.environ
    assert "MOXI_DEV_PROBE_SECRET" not in os.environ
    assert os.environ["HOME"] == str(fence.home)


@pytest.mark.live
def test_live():
    from moxi_dev import testing
    assert testing._GUARD.network is False
'''


@pytest.fixture
def probes(pytester, monkeypatch):
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", "{}")
    monkeypatch.setenv("MOXI_DEV_PROBE_SECRET", "x")
    monkeypatch.delenv("MOXI_TEST_LIVE", raising=False)
    pytester.makeconftest('pytest_plugins = ["moxi_dev.testing"]\n')
    pytester.makepyfile(test_probes=PROBES)
    return pytester


def test_a_probe_run_fails_what_breaks_the_fence(probes):
    result = probes.runpytest_subprocess("-p", "no:cacheprovider", "-rA")
    result.assert_outcomes(failed=3, errors=1, passed=3, skipped=1)
    lines = set(result.outlines)
    for word, name in (("FAILED", "test_writes_to_the_real_home"),
                       ("FAILED", "test_swallows_the_refusal"),
                       ("FAILED", "test_connects_off_the_machine"),
                       ("ERROR", "test_a_fixture_writes_out_in_teardown"),
                       ("PASSED", "test_writes_in_its_tmp"),
                       ("PASSED", "test_sees_only_the_fenced_environment")):
        assert any(line.startswith(f"{word} test_probes.py::{name}") for line in lines), \
            (word, name)
    text = result.stdout.str()
    assert "the test fence refused, and the test went on" in text     # the swallowed one
    assert "outside the test's call" in text                         # the teardown one
    assert "not a loopback address" in text
    assert "a live test runs only with MOXI_TEST_LIVE=1" in text
    assert not list(REAL_HOME.glob(".moxi-dev-fence-probe-*"))


def test_a_live_probe_runs_with_the_word(probes, monkeypatch):
    monkeypatch.setenv("MOXI_TEST_LIVE", "1")
    result = probes.runpytest_subprocess("-p", "no:cacheprovider", "-k", "live")
    result.assert_outcomes(passed=1, deselected=6)
