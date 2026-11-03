"""The gateway's rules as plain functions: the endpoint's kind, the socket path, the runtime
directory, the hold file, the provider's uid and the peer, the address rule and the mount."""

from __future__ import annotations

import os
import socket
import stat
import sys
from types import SimpleNamespace

from dataclasses import dataclass

import pytest
from moxi_ui.gateway import endpoint, provider
from moxi_ui.gateway.address import OFF, REFUSED, Kept, step
from moxi_ui.gateway.mount import Mount, decide, read_acts, write_acts
from moxi_ui.gateway.shapes import refusals

UID = 1000
OTHER = 1001
SOCK = "/run/user/1000/moxi/ui/gateway.sock"


@dataclass(frozen=True)
class Address:
    """A stand-in for tunnel's `Address`, with the fields the gateway reads (the tests reach
    no optional module directly)."""

    module: str
    name: str
    reach: str
    provider: str
    exposure: str
    url: str
    host: str
    port: int
    target: int | str
    state: str
    change: str
    applied_at: str | None
    form: str = "http"
    retired: bool = False


def address(state="on", change="r1.1", host="gw.example.test", port=443,
            provider_name="cloudflare", target=SOCK, form="unix", retired=False):
    return Address(module="ui", name="gateway", reach="own-devices", provider=provider_name,
                   exposure="internet", url=f"https://{host}/", host=host, port=port,
                   target=target, state=state, change=change, applied_at=None, form=form,
                   retired=retired)


# --- the endpoint's kind --------------------------------------------------------------


def test_every_system_takes_tcp_until_its_unix_check_passes():
    assert endpoint.UNIX_PASSED == frozenset()
    assert {name: endpoint.resolve_kind(name) for name in endpoint.SYSTEMS} == {
        "linux": "tcp", "wsl": "tcp", "darwin": "tcp", "win32": "tcp"}
    passed = {"linux", "darwin", "win32"}
    assert endpoint.resolve_kind("linux", passed) == "unix"
    assert endpoint.resolve_kind("darwin", passed) == "unix"
    assert endpoint.resolve_kind("wsl", passed) == "tcp"
    assert endpoint.resolve_kind("win32", passed) == "tcp", "Windows always takes TCP"


def test_the_system_names():
    assert endpoint.system("win32") == "win32"
    assert endpoint.system("darwin") == "darwin"
    assert endpoint.system("linux", wsl=False) == "linux"
    assert endpoint.system("linux", wsl=True) == "wsl"


# --- the socket path ------------------------------------------------------------------


def _fs(tree):
    """An `lstat` over `{path: (uid, mode)}`, a mode with S_IFLNK for a link."""

    def lstat(path):
        if path not in tree:
            raise FileNotFoundError(2, "No such file or directory", path)
        uid, mode = tree[path]
        kind = mode if stat.S_IFMT(mode) else stat.S_IFDIR | mode
        return SimpleNamespace(st_uid=uid, st_mode=kind)

    return lstat


RUN = {"/": (0, 0o755), "/run": (0, 0o755), "/run/user": (0, 0o755),
       "/run/user/1000": (UID, 0o700), "/run/user/1000/moxi": (UID, 0o700),
       "/run/user/1000/moxi/ui": (UID, 0o700)}
STATE = {"/": (0, 0o755), "/home": (0, 0o755), "/home/someone": (UID, 0o700),
         "/home/someone/.local": (UID, 0o755), "/home/someone/.local/state": (UID, 0o755),
         "/home/someone/.local/state/moxi": (UID, 0o700),
         "/home/someone/.local/state/moxi/runtime": (UID, 0o700),
         "/home/someone/.local/state/moxi/runtime/ui": (UID, 0o700)}
STATE_SOCK = "/home/someone/.local/state/moxi/runtime/ui/gateway.sock"


def _check(path, tree, **kwargs):
    kwargs.setdefault("realpath", lambda p: p)
    endpoint.check_socket_path(path, uid=UID, lstat=_fs(tree), platform="linux", **kwargs)


def test_the_runtime_dir_and_an_owner_only_state_root_pass():
    _check(SOCK, RUN)
    _check(STATE_SOCK, STATE)


@pytest.mark.parametrize("where, owner, mode, fix", [
    ("/home/someone/.local/state", UID, 0o775, "chmod g-w,o-w /home/someone/.local/state"),
    ("/home/someone/.local", UID, 0o757, "chmod g-w,o-w /home/someone/.local"),
    ("/home/someone", OTHER, 0o700, "who owns it"),
    ("/home", 0, 0o1777, "who owns it"),
])
def test_a_directory_another_user_owns_or_can_write_is_refused_and_named(where, owner, mode,
                                                                        fix):
    tree = {**STATE, where: (owner, mode)}
    with pytest.raises(endpoint.PathRefused) as err:
        _check(STATE_SOCK, tree)
    assert err.value.directory == where
    assert where in str(err.value) and fix in str(err.value)


def test_a_group_writable_state_dir_is_refused_even_for_a_private_group():
    # The user's private group can't be shown to have no other member, so 0775 is refused.
    with pytest.raises(endpoint.PathRefused, match="group can write"):
        _check(STATE_SOCK, {**STATE, "/home/someone/.local/state": (UID, 0o775)})


def test_the_tmp_fallback_is_refused():
    tree = {"/": (0, 0o755), "/tmp": (0, 0o1777), f"/tmp/moxi-{UID}": (UID, 0o700)}
    with pytest.raises(endpoint.PathRefused) as err:
        _check(f"/tmp/moxi-{UID}/0123456789abcdef.sock", tree)
    assert err.value.directory == "/tmp"


def test_a_link_another_user_owns_is_refused_and_a_link_is_followed():
    linked = {**STATE, "/home": (OTHER, stat.S_IFLNK | 0o777), "/var": (0, 0o755),
              "/var/home": (0, 0o755)}
    with pytest.raises(endpoint.PathRefused) as err:
        _check(STATE_SOCK, linked)
    assert err.value.directory == "/home"
    # A link root owns passes, and the directories it leads to are walked too.
    resolved = {**{p.replace("/home", "/var/home", 1): v for p, v in STATE.items()
                   if p.startswith("/home/")},
                **STATE, "/home": (0, stat.S_IFLNK | 0o777), "/var": (0, 0o777),
                "/var/home": (0, 0o755)}
    real = lambda p: p.replace("/home", "/var/home", 1)  # noqa: E731
    with pytest.raises(endpoint.PathRefused) as err:
        _check(STATE_SOCK, resolved, realpath=real)
    assert err.value.directory == "/var"
    resolved["/var"] = (0, 0o755)
    _check(STATE_SOCK, resolved, realpath=real)


def test_a_path_past_the_socket_limit_is_refused():
    deep = "/run/user/1000/" + "d" * 100 + "/gateway.sock"
    with pytest.raises(endpoint.PathRefused, match="past this system's limit of 107"):
        _check(deep, RUN)
    assert endpoint.socket_limit("darwin") == 103


@pytest.mark.skipif(sys.platform != "linux" or not os.path.isdir(f"/run/user/{os.getuid()}"),
                    reason="needs a login runtime directory")
def test_the_real_login_runtime_directory_passes():
    endpoint.check_socket_path(f"/run/user/{os.getuid()}/{endpoint.SOCKET_FILE}")


# --- the runtime directory ------------------------------------------------------------


@pytest.fixture
def plain(tmp_path):
    return {"HOME": str(tmp_path / "home"), "XDG_RUNTIME_DIR": str(tmp_path / "run")}


@pytest.mark.parametrize("name", ["MOXI_HOME", "MOXI_UI_HOME", "MOXI_UI_RUNTIME_DIR"])
def test_a_locator_that_moves_the_runtime_dir_is_named(plain, tmp_path, name):
    moved = {**plain, name: str(tmp_path / "elsewhere")}
    text = endpoint.runtime_problem(moved, platform="linux")
    assert text and name in text and f"unset {name}" in text


def test_a_locator_that_leaves_the_runtime_dir_where_it_was_passes(plain):
    assert endpoint.runtime_problem(plain, platform="linux") is None
    same = {**plain, "MOXI_UI_RUNTIME_DIR": plain["XDG_RUNTIME_DIR"] + "/moxi"}
    assert endpoint.runtime_problem(same, platform="linux") is None


def test_on_windows_the_logon_tasks_stored_environment_counts(plain, tmp_path):
    moved = {**plain, "MOXI_HOME": str(tmp_path / "m")}
    assert endpoint.runtime_problem(moved, platform="win32", stored={"moxi_home": moved["MOXI_HOME"]}) is None
    assert endpoint.runtime_problem(plain, platform="win32", stored={"MOXI_HOME": moved["MOXI_HOME"]})


# --- the hold file --------------------------------------------------------------------


def test_the_hold_file_names_a_running_shell_and_its_port(tmp_path):
    assert endpoint.hold_port(tmp_path) is None
    endpoint.write_hold(tmp_path, 8461)
    assert stat.S_IMODE(os.stat(tmp_path / endpoint.HOLD_FILE).st_mode) == 0o600
    assert endpoint.hold_port(tmp_path) == 8461
    assert endpoint.hold_port(tmp_path, running=lambda pid: False) is None
    endpoint.remove_hold(tmp_path, pid=os.getpid() + 1)
    assert endpoint.hold_port(tmp_path) == 8461, "another process's file is left alone"
    endpoint.remove_hold(tmp_path)
    assert not (tmp_path / endpoint.HOLD_FILE).exists()
    (tmp_path / endpoint.HOLD_FILE).write_text('{"pid": true, "port": 1}')
    assert endpoint.hold_port(tmp_path) is None


# --- the provider ---------------------------------------------------------------------


def test_tailscaleds_uid_comes_only_from_its_local_socket(monkeypatch):
    asked = []
    found = provider.provider_uid("tailscale", on_socket=lambda p: asked.append(p) or 0)
    assert found == 0 and asked == [provider.TAILSCALED_SOCKET]
    # With its socket absent, a process that is merely named tailscaled counts for nothing.
    import psutil

    named = SimpleNamespace(info={"name": "tailscaled", "uids": SimpleNamespace(real=OTHER)})
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([named]))
    assert provider.provider_uid("tailscale", on_socket=lambda p: None) is None
    assert provider.provider_uid("cloudflare", getuid=lambda: UID) == UID
    assert provider.provider_uid("another") is None


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="needs SO_PEERCRED")
def test_the_uid_on_a_socket_is_its_listeners(tmp_path):
    path = str(tmp_path / "d.sock")
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(path)
        server.listen()
        assert provider.uid_on_socket(path) == os.getuid()
    assert provider.uid_on_socket(str(tmp_path / "missing.sock")) is None


def test_the_check_runs_on_linux_and_wsl_only():
    assert provider.checks("linux") and provider.checks("wsl")
    assert not provider.checks("darwin") and not provider.checks("win32")


def test_the_peer_on_a_socket_is_the_scopes():
    assert provider.peer_of({"moxi.peer": {"uid": 7}}, "unix") == 7
    assert provider.peer_of({"moxi.peer": {"uid": None}}, "unix") is None
    assert provider.peer_of({}, "unix") is None
    assert provider.peer_of({"moxi.peer": {"uid": True}}, "unix") is None


def _row(local, remote, uid):
    def enc(host, port):
        raw = socket.inet_aton(host)
        return f"{int.from_bytes(raw, sys.byteorder):08X}:{port:04X}"

    return f"   0: {enc(*local)} {enc(*remote)} 01 00000000:00000000 00:00000000 00000000 {uid:5d}"


def test_the_peer_on_tcp_is_the_proc_net_tcp_row_of_the_scopes_addresses(tmp_path):
    (tmp_path / "net").mkdir()
    (tmp_path / "net" / "tcp").write_text("\n".join([
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid",
        _row(("127.0.0.1", 50000), ("127.0.0.1", 8461), 0),
        _row(("127.0.0.1", 8461), ("127.0.0.1", 50000), UID),
        _row(("127.0.0.1", 50001), ("127.0.0.1", 8461), OTHER)]) + "\n")
    (tmp_path / "net" / "tcp6").write_text("header\n")
    scope = {"client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 8461)}
    assert provider.peer_of(scope, "tcp", proc=str(tmp_path)) == 0
    scope["client"] = ("127.0.0.1", 50001)
    assert provider.peer_of(scope, "tcp", proc=str(tmp_path)) == OTHER
    scope["client"] = ("127.0.0.1", 50002)
    assert provider.peer_of(scope, "tcp", proc=str(tmp_path)) is None


# --- the address rule -----------------------------------------------------------------


KEPT = Kept("r1.1", "gw.example.test", 443, "cloudflare")


def test_each_row_of_the_address_table():
    on = address()
    assert step(KEPT, OFF) == step(KEPT, OFF).__class__("off", None, ends=True)
    assert not step(None, OFF).ends
    assert step(KEPT, REFUSED).kept == KEPT and not step(KEPT, REFUSED).ends
    assert step(KEPT, None).kept == KEPT and not step(KEPT, None).ends
    for state in ("applying", "removing"):
        done = step(KEPT, address(state=state, change="r1.9", port=8000))
        assert done.kept == KEPT and not (done.build or done.ends)
    built = step(None, on)
    assert built.build and built.find_uid and built.kept == KEPT and not built.ends
    assert step(KEPT, on).row == "same" and not step(KEPT, on).find_uid
    token = step(KEPT, address(change="r1.2"))
    assert token.row == "token" and token.find_uid and not (token.build or token.ends)
    assert token.kept.change == "r1.2"
    port = step(KEPT, address(change="r1.3", port=8443))
    assert port.build and port.ends and port.stale is None
    host = step(KEPT, address(change="r1.4", host="gw2.example.test"))
    assert host.build and host.ends and host.stale == KEPT
    moved = step(KEPT, address(change="r1.5", provider_name="tailscale"))
    assert moved.build and moved.ends and moved.stale == KEPT


def test_a_none_between_a_remove_and_an_add_changes_nothing_by_itself():
    seen_none = step(step(KEPT, None).kept, address(change="r2.1"))
    no_none = step(KEPT, address(change="r2.1"))
    assert seen_none == no_none and not seen_none.ends


# --- the mount ------------------------------------------------------------------------


def test_the_mount_and_its_reasons_in_order():
    sock = address()
    assert decide(on=True, kind="unix", endpoint=SOCK, address=sock, refused=False) == Mount(True, None)
    assert decide(on=False, kind="unix", endpoint=SOCK, address=sock, refused=False).reason == "off"
    assert decide(on=True, kind="unix", endpoint=SOCK, address=sock, refused=True).reason == "record"
    for other in (address(target="/elsewhere/gateway.sock"), address(form="http", target=8461)):
        assert decide(on=True, kind="unix", endpoint=SOCK, address=other,
                      refused=False).reason == "target"
    tcp = address(form="http", target=8461)
    assert decide(on=True, kind="tcp", endpoint=8461, address=tcp, refused=False).reason == "tcp"
    assert decide(on=True, kind="tcp", endpoint=8461, address=address(form="http", target=8462),
                  refused=False).reason == "target"
    retired = address(retired=True)
    assert decide(on=True, kind="unix", endpoint=SOCK, address=retired,
                  refused=False).reason == "origin"
    assert decide(on=True, kind="tcp", endpoint=8461,
                  address=address(form="http", target=8461, retired=True),
                  refused=False).reason == "tcp", "tcp comes before origin"


def test_gateway_acts_holds_the_pid_the_value_and_the_reason(tmp_path):
    write_acts(tmp_path, Mount(False, "tcp"))
    assert read_acts(tmp_path) == {"pid": os.getpid(), "acts": False, "reason": "tcp"}
    assert stat.S_IMODE(os.stat(tmp_path / "gateway.acts").st_mode) == 0o600
    write_acts(tmp_path, Mount(False, "tcp"), refusals={"fixture": "why"})
    assert read_acts(tmp_path)["refusals"] == {"fixture": "why"}
    write_acts(tmp_path, Mount(False, "tcp"), refusals={})
    assert "refusals" not in read_acts(tmp_path)


def _act(**kw):
    return {"path": "/api/go", "kind": "act", "gateway": True, "confirmed": True,
            "status": "/api/job", **kw}


STATUS_READ = {"path": "/api/job", "kind": "read", "gateway": True}


def test_refusals_names_each_module_the_export_checks_refuse_with_its_route():
    mounts = {"good": {"routes": [_act(), STATUS_READ]},
              "plain": {"routes": [{"path": "/api/r", "kind": "read"}]},
              "unconfirmed": {"routes": [_act(confirmed=False), STATUS_READ]},
              "unreached": {"routes": [_act(), {**STATUS_READ, "gateway": False}]},
              "no/name": {"routes": [_act(confirmed=False)]}}
    assert refusals(mounts) == {
        "unconfirmed": "the act /api/go reaches the gateway but isn't a confirmed act",
        "unreached": "the act /api/go reaches the gateway but its status route /api/job "
                     "doesn't"}
