"""The shell's listeners: sockets systemd passes through `LISTEN_FDS`, launchd's by key,
and the shell's own bind when nothing was passed."""

from __future__ import annotations

import errno
import os
import socket
import sys

import pytest
from moxi_ui.loopback.api import bind_browser

from moxi_ui import listen

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="no socket activation on Windows")

#: Where the tests put the passed descriptors, clear of any the test process holds.
FIRST = 200


UNITS = ("moxi-ui-shell.socket", "moxi-ui-shell-1.socket")


class Passed:
    """The shell's loopback listeners at descriptors from `FIRST`, as systemd lays them
    out: `127.0.0.1`, then `[::1]` where the system has an IPv6 loopback."""

    def __init__(self):
        bound = bind_browser(0)
        self.port, self.count = bound.port, len(bound.sockets)
        for place, sock in enumerate(bound.sockets):
            os.dup2(sock.fileno(), FIRST + place)
            sock.close()

    def systemd(self, *, count=None, names=None, pid=None):
        count = self.count if count is None else count
        env = {"LISTEN_PID": str(os.getpid() if pid is None else pid),
               "LISTEN_FDS": str(count), "OTHER": "kept"}
        env["LISTEN_FDNAMES"] = ":".join(UNITS[:count]) if names is None else names
        return env

    def close(self):
        for fd in (FIRST, FIRST + 1):
            try:
                os.close(fd)
            except OSError:
                pass


@pytest.fixture
def passed(monkeypatch):
    held = Passed()
    monkeypatch.setattr(listen, "_FIRST_FD", FIRST)
    yield held
    held.close()


def test_systemds_sockets_are_taken_with_their_names_and_the_variables_go(passed):
    env = passed.systemd()
    got = listen.take(1, environ=env, platform="linux")
    try:
        assert got.inherited and got.port == passed.port
        assert got.fd_names == UNITS[:passed.count]
        # localhost needs [::1] held, or no IPv6 loopback at all: either way here.
        assert got.names == frozenset({"127.0.0.1", "localhost"})
        assert env == {"OTHER": "kept"}
        assert all(not sock.get_inheritable() for sock in got.sockets)
        # The sockets listen: a connect reaches the first.
        with socket.create_connection(("127.0.0.1", passed.port), timeout=2):
            conn, _ = got.sockets[0].accept()
            conn.close()
    finally:
        got.close()


def test_only_the_ipv4_socket_passed_refuses_localhost_where_ipv6_loopback_exists(
        passed, monkeypatch):
    monkeypatch.setattr("moxi_ui.loopback.transport._no_ipv6_loopback", lambda: False)
    if passed.count == 2:
        os.close(FIRST + 1)
    got = listen.take(1, environ=passed.systemd(count=1), platform="linux")
    try:
        assert got.inherited and got.names == frozenset({"127.0.0.1"})
        assert got.fd_names == ("moxi-ui-shell.socket",)
    finally:
        got.close()


def test_passed_sockets_never_read_the_port(passed):
    def unread():
        raise AssertionError("the port was read")

    got = listen.take(unread, environ=passed.systemd(), platform="linux")
    try:
        assert got.inherited and got.port == passed.port
    finally:
        got.close()
    got = listen.take(lambda: 0, environ={}, platform="linux")
    try:
        assert not got.inherited
    finally:
        got.close()


def _has_ipv6_loopback():
    try:
        with socket.socket(socket.AF_INET6) as sock:
            sock.bind(("::1", 0))
    except OSError:
        return False
    return True


@pytest.mark.skipif(not _has_ipv6_loopback(), reason="no IPv6 loopback here")
def test_another_socket_on_ipv6_loopback_leaves_localhost_refused(tmp_path):
    from shell_kit import FETCH, Harness

    other = socket.socket(socket.AF_INET6)
    other.bind(("::1", 0))
    other.listen()
    port = other.getsockname()[1]
    try:
        try:
            got = listen.bind(port)
        except listen.PortInUse:
            pytest.skip("127.0.0.1 holds that port too")
        try:
            assert got.port == port and got.names == frozenset({"127.0.0.1"})
        finally:
            got.close()
    finally:
        other.close()
    shell = Harness(tmp_path / "state", names=got.names)
    cookie, key = shell.sign_in()
    answer = shell.send("GET", "/m/fixture/api/summary", headers=[cookie, key, *FETCH],
                        host=b"localhost:%d" % port)
    assert answer.status == 403
    # Holding both addresses itself, the shell admits localhost.
    both = listen.bind(0)
    try:
        assert both.names == frozenset({"127.0.0.1", "localhost"})
    finally:
        both.close()


def test_a_name_count_that_does_not_match_names_none(passed):
    got = listen.take(1, environ=passed.systemd(names="a:b:c"), platform="linux")
    try:
        assert got.inherited and got.fd_names == ()
    finally:
        got.close()


def test_variables_for_another_process_are_dropped_and_the_shell_binds(passed):
    env = passed.systemd(pid=1)
    got = listen.take(0, environ=env, platform="linux")
    try:
        assert not got.inherited and got.fd_names == () and got.port != passed.port
        assert env == {"OTHER": "kept"}
    finally:
        got.close()


def test_with_nothing_passed_the_shell_binds_its_own():
    got = listen.take(0, environ={}, platform="linux")
    try:
        assert not got.inherited and len(got.sockets) >= 1
    finally:
        got.close()


def test_a_passed_socket_that_is_not_a_loopback_listener_is_refused(monkeypatch):
    plain = socket.socket()
    plain.bind(("127.0.0.1", 0))  # bound, never listening
    os.dup2(plain.fileno(), FIRST)
    plain.close()
    monkeypatch.setattr(listen, "_FIRST_FD", FIRST)
    with pytest.raises(listen.NotLoopback, match="not listening"):
        listen.take(1, environ={"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "1"},
                    platform="linux")
    with pytest.raises(OSError):  # take closed it
        os.fstat(FIRST)


# --- launchd ------------------------------------------------------------------------------


def _launchd(table):
    """A fake `launch_activate_socket`: `table` maps a key to its descriptors, or to an
    errno; every other key is ENOENT."""
    asked = []

    def activate(key):
        asked.append(key)
        found = table.get(key, errno.ENOENT)
        return (found, []) if isinstance(found, int) else (0, list(found))

    activate.asked = asked
    return activate


def test_launchds_sockets_are_taken_by_key(passed):
    keys = {"Listeners": [FIRST]}
    if passed.count == 2:
        keys["Listeners-1"] = [FIRST + 1]
    activate = _launchd(keys)
    got = listen.take(1, environ={}, activate=activate, platform="darwin")
    try:
        assert got.inherited and got.port == passed.port and got.fd_names == ()
        assert len(got.sockets) == passed.count and "localhost" in got.names
        assert activate.asked[:2] == ["Listeners", "Listeners-1"]
    finally:
        got.close()


def test_a_process_launchd_did_not_start_binds_its_own():
    activate = _launchd({"Listeners": errno.ESRCH})
    got = listen.take(0, environ={}, activate=activate, platform="darwin")
    try:
        assert not got.inherited and activate.asked == ["Listeners"]
    finally:
        got.close()


def test_a_launchd_key_that_cannot_be_taken_is_refused(passed):
    activate = _launchd({"Listeners": [FIRST], "Listeners-1": errno.EALREADY})
    with pytest.raises(listen.NotLoopback, match="Listeners-1"):
        listen.take(1, environ={}, activate=activate, platform="darwin")
