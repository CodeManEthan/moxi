"""Listeners: the Unix bind in a private directory, the TCP flags, and the shell's browser
listener with the Host names it may admit."""

from __future__ import annotations

import errno
import os
import socket
import sys

import pytest
from moxi import paths
from moxi.paths import Layout, socket_path

from moxi_loopback import transport
from moxi_loopback.check import RequestView, browser, check
from moxi_loopback.routes import Table
from moxi_loopback.transport import (
    BrowserSockets,
    NotLoopback,
    NotPrivate,
    bind_browser,
    bind_tcp,
    bind_unix,
    browser_names,
    check_private_dir,
)

WINDOWS = sys.platform == "win32"
posix = pytest.mark.skipif(WINDOWS, reason="Unix sockets and POSIX modes")
windows = pytest.mark.skipif(not WINDOWS, reason="Windows socket flags")
BOTH = frozenset({"127.0.0.1", "localhost"})
V4 = frozenset({"127.0.0.1"})


def _has_ipv6_loopback() -> bool:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
    except OSError:
        return False
    return True


ipv6 = pytest.mark.skipif(not _has_ipv6_loopback(), reason="this system has no IPv6 loopback")


@pytest.fixture
def closing():
    """Sockets a test opens, closed after it."""
    opened: list[socket.socket] = []
    yield opened
    for sock in opened:
        sock.close()


def keep(closing, result):
    if isinstance(result, BrowserSockets):
        closing.extend(result.sockets)
    else:
        closing.append(result)
    return result


def fail_v6(monkeypatch, code):
    calls = []

    def refuse(port):
        calls.append(port)
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(transport, "_bind_v6", refuse)
    return calls


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# --- Unix sockets -------------------------------------------------------------------


@posix
def test_bind_unix_in_the_private_runtime_dir(closing):
    Layout("demo").runtime_dir(create=True)
    path = socket_path("demo", "page")
    server = keep(closing, bind_unix(path))
    client = keep(closing, socket.socket(socket.AF_UNIX))
    client.connect(path)
    conn, _ = server.accept()
    conn.close()


@posix
def test_bind_unix_replaces_a_stale_socket(closing):
    Layout("demo").runtime_dir(create=True)
    path = socket_path("demo", "page")
    bind_unix(path).close()
    assert os.path.exists(path)
    keep(closing, bind_unix(path))


@posix
def test_bind_unix_refuses_a_path_that_is_not_a_socket():
    directory = Layout("demo").runtime_dir(create=True)
    path = socket_path("demo", "page")
    (directory / "page.sock").write_text("not a socket")
    with pytest.raises(FileExistsError):
        bind_unix(path)


@posix
@pytest.mark.parametrize("mode", [0o750, 0o705, 0o770])
def test_bind_unix_refuses_a_dir_open_to_others(mode):
    directory = Layout("demo").runtime_dir(create=True)
    os.chmod(directory, mode)
    with pytest.raises(NotPrivate):
        bind_unix(socket_path("demo", "page"))
    assert not os.path.exists(socket_path("demo", "page"))


@posix
def test_the_private_dir_check_refuses_a_link_and_a_file(fence):
    real = fence.short / "real"
    real.mkdir(mode=0o700)
    check_private_dir(real)
    link = fence.short / "link"
    link.symlink_to(real)
    with pytest.raises(NotPrivate):
        check_private_dir(link)
    plain = fence.short / "plain"
    plain.write_text("")
    with pytest.raises(NotPrivate):
        check_private_dir(plain)
    with pytest.raises(NotPrivate):
        check_private_dir(fence.short / "missing")


@posix
def test_a_long_home_binds_through_socket_path(fence, tmp_path, monkeypatch, closing):
    # A runtime path too long for a socket puts the socket in a hashed directory under a
    # short root; the bind checks that directory instead.
    monkeypatch.setattr(paths, "_SHORT_ROOT", str(fence.short))
    home = tmp_path / ("h" * 60) / ("o" * 60)
    monkeypatch.setenv("MOXI_HOME", str(home))
    run_dir = Layout("demo").runtime_dir(create=True)
    path = socket_path("demo", "page")
    assert not path.startswith(str(run_dir))
    server = keep(closing, bind_unix(path))
    client = keep(closing, socket.socket(socket.AF_UNIX))
    client.connect(path)
    server.accept()[0].close()


# --- TCP ----------------------------------------------------------------------------


def test_bind_tcp_port_zero_is_read_back(closing):
    sock = keep(closing, bind_tcp())
    host, port = sock.getsockname()
    assert host == "127.0.0.1" and 0 < port < 65536
    client = keep(closing, socket.create_connection(("127.0.0.1", port), timeout=5))
    sock.accept()[0].close()
    assert client


@posix
def test_posix_tcp_sockets_reuse_the_address_and_never_the_port(closing):
    sock = keep(closing, bind_tcp())
    assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR)
    if hasattr(socket, "SO_REUSEPORT"):
        assert not sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT)


@windows
def test_windows_tcp_is_exclusive_and_a_second_bind_fails(closing):
    sock = keep(closing, bind_tcp())
    assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE)
    port = sock.getsockname()[1]
    other = keep(closing, socket.socket())
    other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    with pytest.raises(OSError):
        other.bind(("127.0.0.1", port))


# --- bind_browser -------------------------------------------------------------------


@ipv6
def test_bind_browser_holds_both_loopbacks_and_admits_localhost(closing):
    result = keep(closing, bind_browser())
    assert result.names == BOTH
    assert sorted(s.getsockname()[0] for s in result.sockets) == ["127.0.0.1", "::1"]
    assert {s.getsockname()[1] for s in result.sockets} == {result.port}
    assert browser_names(result.sockets) == BOTH


@ipv6
def test_bind_browser_refuses_localhost_when_another_socket_holds_ipv6(closing):
    for _ in range(20):
        squatter = keep(closing, socket.socket(socket.AF_INET6))
        squatter.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        squatter.bind(("::1", 0))
        squatter.listen()
        port = squatter.getsockname()[1]
        try:
            result = keep(closing, bind_browser(port))
        except OSError:
            continue  # 127.0.0.1 happened to be taken on that port; pick another
        break
    else:
        pytest.fail("no port was free on 127.0.0.1 beside a [::1] squatter")
    assert result.names == V4
    assert len(result.sockets) == 1
    assert _host_refused(result, "localhost")


def _host_refused(result: BrowserSockets, name: str) -> bool:
    table = Table()
    table.add("GET", "/")
    profile = browser(table, session=lambda view, entry: True, names=result.names)

    def view(host):
        return RequestView("GET", b"/", [(b"host", f"{host}:{result.port}".encode())],
                           "tcp", result.port)

    assert check(view("127.0.0.1"), profile) is None
    refusal = check(view(name), profile)
    return refusal is not None and (refusal.status, refusal.gate) == (403, 1)


@pytest.mark.parametrize("code", [errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT])
def test_no_ipv6_loopback_admits_localhost(monkeypatch, closing, code):
    calls = fail_v6(monkeypatch, code)
    result = keep(closing, bind_browser())
    assert result.names == BOTH
    assert len(result.sockets) == 1 and calls == [result.port]


@pytest.mark.parametrize("code", [errno.EADDRINUSE, errno.EACCES, errno.EPERM, errno.ENOBUFS])
def test_any_other_ipv6_failure_refuses_localhost(monkeypatch, closing, code):
    fail_v6(monkeypatch, code)
    result = keep(closing, bind_browser())
    assert result.names == V4
    assert len(result.sockets) == 1
    assert _host_refused(result, "localhost")


def test_a_fixed_port_already_held_raises_and_binds_nothing(monkeypatch, closing):
    holder = keep(closing, bind_tcp())
    port = holder.getsockname()[1]
    calls = fail_v6(monkeypatch, errno.EADDRNOTAVAIL)
    with pytest.raises(OSError):
        bind_browser(port)
    assert calls == []


def test_bind_browser_on_a_fixed_port(monkeypatch, closing):
    fail_v6(monkeypatch, errno.EADDRNOTAVAIL)
    port = free_port()
    result = keep(closing, bind_browser(port))
    assert result.port == port and result.sockets[0].getsockname() == ("127.0.0.1", port)


def _serve_once_and_stop(result: BrowserSockets) -> None:
    # One connection served and closed by the server first, which leaves it in TIME_WAIT.
    client = socket.create_connection(("127.0.0.1", result.port), timeout=5)
    conn, _ = result.sockets[0].accept()
    client.sendall(b"ping")
    assert conn.recv(4) == b"ping"
    conn.sendall(b"pong")
    conn.close()
    assert client.recv(4) == b"pong"
    client.close()
    for sock in result.sockets:
        sock.close()


@posix
def test_a_restart_binds_the_same_fixed_port_at_once(monkeypatch, closing):
    fail_v6(monkeypatch, errno.EADDRNOTAVAIL)
    first = bind_browser()
    _serve_once_and_stop(first)
    again = keep(closing, bind_browser(first.port))
    assert again.port == first.port


@posix
def test_no_second_socket_can_bind_the_shells_address(monkeypatch, closing):
    fail_v6(monkeypatch, errno.EADDRNOTAVAIL)
    result = keep(closing, bind_browser())
    other = keep(closing, socket.socket())
    other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    with pytest.raises(OSError):
        other.bind(("127.0.0.1", result.port))


@windows
def test_windows_restart_on_the_same_fixed_port_is_recorded(monkeypatch, closing):
    # Windows may refuse the bind while the last run's connections sit in TIME_WAIT. The
    # result is recorded for the shell, which decides what to do about it.
    fail_v6(monkeypatch, errno.EADDRNOTAVAIL)
    first = bind_browser()
    _serve_once_and_stop(first)
    try:
        keep(closing, bind_browser(first.port))
    except OSError as err:
        pytest.xfail(f"Windows refused the restart bind: {err}")


# --- browser_names ------------------------------------------------------------------


def listener(closing, family=socket.AF_INET, host="127.0.0.1", port=0, listen=True,
             kind=socket.SOCK_STREAM):
    sock = keep(closing, socket.socket(family, kind))
    if family == socket.AF_INET6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    sock.bind((host, port))
    if listen:
        sock.listen()
    return sock


@ipv6
def test_both_inherited_sockets_admit_localhost(closing):
    v4 = listener(closing)
    v6 = listener(closing, socket.AF_INET6, "::1", v4.getsockname()[1])
    assert browser_names([v4, v6]) == BOTH
    assert browser_names([v6, v4]) == BOTH


@ipv6
def test_ipv4_alone_where_ipv6_loopback_exists_refuses_localhost(closing):
    assert browser_names([listener(closing)]) == V4


def test_ipv4_alone_with_no_ipv6_loopback_admits_localhost(monkeypatch, closing):
    calls = fail_v6(monkeypatch, errno.EADDRNOTAVAIL)
    assert browser_names([listener(closing)]) == BOTH
    assert calls == [0]


def test_ipv4_alone_when_the_ipv6_probe_fails_otherwise_refuses_localhost(monkeypatch,
                                                                           closing):
    fail_v6(monkeypatch, errno.EADDRINUSE)
    assert browser_names([listener(closing)]) == V4


def test_browser_names_refuses_a_wildcard_socket(closing):
    with pytest.raises(NotLoopback):
        browser_names([listener(closing, host="0.0.0.0")])


def test_browser_names_refuses_two_ports(closing):
    with pytest.raises(NotLoopback):
        browser_names([listener(closing), listener(closing)])


@ipv6
def test_browser_names_refuses_ipv6_on_another_port(closing):
    with pytest.raises(NotLoopback):
        browser_names([listener(closing), listener(closing, socket.AF_INET6, "::1")])


def test_browser_names_refuses_no_ipv4_socket(closing):
    with pytest.raises(NotLoopback):
        browser_names([])


@ipv6
def test_browser_names_refuses_ipv6_alone(closing):
    with pytest.raises(NotLoopback):
        browser_names([listener(closing, socket.AF_INET6, "::1")])


def test_browser_names_refuses_a_socket_that_is_not_listening(closing):
    with pytest.raises(NotLoopback):
        browser_names([listener(closing, listen=False)])


def test_browser_names_refuses_a_udp_socket(closing):
    with pytest.raises(NotLoopback):
        browser_names([listener(closing, listen=False, kind=socket.SOCK_DGRAM)])


def test_browser_names_refuses_a_bare_socket_or_another_object(closing):
    with pytest.raises(NotLoopback):
        browser_names(listener(closing))
    with pytest.raises(NotLoopback):
        browser_names([object()])


@posix
def test_browser_names_refuses_a_unix_socket(fence, closing):
    sock = keep(closing, socket.socket(socket.AF_UNIX))
    sock.bind(str(fence.short / "s"))
    sock.listen()
    with pytest.raises(NotLoopback):
        browser_names([sock])
