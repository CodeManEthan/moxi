"""The gateway's commands, its tunnel declaration, its doctor rows and its settings mark.

The verbs run in process with their seams replaced (the commands they run, the terminal,
the shell's start); the files they read and write are real, under the test's own homes.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from moxi import modules
from moxi.paths import Layout
from moxi_ui import cli, settings
from moxi_ui import tunnel as decl
from moxi_ui.gateway import doctor, endpoint, pairing, verbs
from moxi_ui.gateway.address import REFUSED
from moxi_ui.gateway.audit import Audit, read_rows
from moxi_ui.gateway.devices import Devices
from moxi_ui.gateway.doctor import Facts


@dataclass(frozen=True)
class Address:
    """A stand-in for tunnel's `Address`, with the fields the gateway reads."""

    host: str = "box.example.ts.net"
    port: int = 8461
    provider: str = "tailscale"
    exposure: str = "own-devices"
    state: str = "on"
    form: str = "http"
    change: str = "c1"
    retired: bool = False
    target: object = 8461

    @property
    def url(self) -> str:
        return f"https://{self.host}:{self.port}/"


def _not_ready() -> type:
    return modules.load("tunnel", interface=2).api.NotReady


@pytest.fixture
def env(tmp_path):
    """An environment with no Moxi locator, whose runtime directory is its own."""
    environ = {"HOME": str(tmp_path / "h"), "XDG_RUNTIME_DIR": str(tmp_path / "r"),
               "XDG_STATE_HOME": str(tmp_path / "s")}
    os.makedirs(environ["XDG_RUNTIME_DIR"], mode=0o700)
    return environ


def _ns(**kw) -> argparse.Namespace:
    return argparse.Namespace(**kw)


# --- upstream() -----------------------------------------------------------------------------


def test_upstream_on_tcp_waits_for_a_hold_that_names_a_running_shell(env):
    runtime = Layout("ui").runtime_dir(env, create=True)
    with pytest.raises(_not_ready()) as err:
        decl.upstream(environ=env, kind="tcp")
    assert "moxi ui gateway on" in str(err.value)
    endpoint.write_hold(runtime, 8471, pid=os.getpid())
    assert decl.upstream(environ=env, kind="tcp") == "http://127.0.0.1:8471"
    with pytest.raises(_not_ready()):
        decl.upstream(environ=env, kind="tcp", running=lambda pid: False)


def test_upstream_refuses_with_the_locator_text_when_the_runtime_dir_moves(env, tmp_path):
    runtime = Layout("ui").runtime_dir(env, create=True)
    endpoint.write_hold(runtime, 8471, pid=os.getpid())
    moved = {**env, "MOXI_UI_RUNTIME_DIR": str(tmp_path / "elsewhere")}
    for kind in ("tcp", "unix"):
        with pytest.raises(_not_ready()) as err:
            decl.upstream(environ=moved, kind=kind)
        assert "MOXI_UI_RUNTIME_DIR" in str(err.value)
    assert decl.upstream(environ=env, kind="tcp") == "http://127.0.0.1:8471"


def test_upstream_on_a_socket_gives_its_path_only_when_the_path_passes(env, monkeypatch):
    path = endpoint.socket_path(Layout("ui"), env)
    with pytest.raises(_not_ready()) as err:
        decl.upstream(environ=env, kind="unix")  # pytest's temp root fails the walk
    assert "the gateway's socket" in str(err.value)
    monkeypatch.setattr(endpoint, "check_socket_path", lambda p: None)
    assert decl.upstream(environ=env, kind="unix") == f"unix:{path}"


def test_the_declaration_is_in_the_group_only_tunnel_two_reads():
    from importlib.metadata import entry_points

    targets = {e.name: e.value for e in entry_points(group="moxi.tunnel.targets")}
    old = {e.name for e in entry_points(group="moxi.tunnel")}
    assert targets["ui.gateway"] == "moxi_ui.tunnel:GATEWAY" and "ui.gateway" not in old
    assert decl.GATEWAY["upstream"] == "moxi_ui.tunnel:upstream"
    assert decl.GATEWAY["retired"] == "moxi_ui.tunnel:retired"
    changes = {e.name: e.value for e in entry_points(group="moxi.tunnel.changes")}
    assert changes["ui"] == "moxi_ui.tunnel:on_change"


# --- the change listener and the copy of retired origins ----------------------------------------


def test_the_listener_keeps_every_persistent_origin_and_never_takes_one_out(tmp_path):
    state = tmp_path / "state"
    old, new = Address(port=8461), Address(port=8462)
    decl.on_change("ui/gateway", old, new, state_dir=state)
    decl.on_change("ui/gateway", None, new, state_dir=state)
    decl.on_change("ui/gateway", new, replace(new, form="unix", target="/run/x.sock",
                                              port=8463), state_dir=state)
    decl.on_change("other/thing", Address(port=9000), None, state_dir=state)
    assert decl.read_retired(state) == [{"host": "box.example.ts.net", "port": 8461},
                                        {"host": "box.example.ts.net", "port": 8462}]
    path = decl.retired_path(state)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    decl.on_change("ui/gateway", new, None, state_dir=state)
    assert len(decl.read_retired(state)) == 2


def test_a_copy_that_cant_be_read_is_never_written_over(tmp_path):
    state = tmp_path / "state"
    path = decl.retired_path(state)
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    with pytest.raises(ValueError):
        decl.on_change("ui/gateway", None, Address(), state_dir=state)
    assert path.read_text() == "{not json"


def test_the_retired_callable_gives_what_tunnel_takes(monkeypatch, tmp_path):
    monkeypatch.setattr(decl, "retired_path", lambda state_dir=None: tmp_path / "r.json")
    decl.add_retired([("Box.Example.ts.net.", 8461)])
    assert decl.retired() == [{"host": "box.example.ts.net", "port": 8461}]


def test_a_new_host_or_provider_marks_devices_paired_elsewhere_stale(tmp_path):
    state = tmp_path / "state"
    devices = Devices.of(state)
    here = devices.add(name="phone", credential=None, public_key=None, sign_count=0,
                       host="box.example.ts.net", provider="tailscale", change="c1")
    decl.on_change("ui/gateway", Address(), Address(port=8470), state_dir=state)
    assert not devices.get(here.id).stale  # a new port keeps the devices
    decl.on_change("ui/gateway", Address(), Address(host="new.example.ts.net"),
                   state_dir=state)
    assert devices.get(here.id).stale
    other = devices.add(name="tablet", credential=None, public_key=None, sign_count=0,
                        host="new.example.ts.net", provider="tailscale", change="c2")
    decl.on_change("ui/gateway", None, Address(host="gw.example.test", provider="cloudflare",
                                               port=443), state_dir=state)
    assert devices.get(other.id).stale


# --- pair, devices, revoke, log -----------------------------------------------------------------


def test_pair_with_the_shell_stopped_mints_a_code_a_later_shell_redeems(env, monkeypatch):
    monkeypatch.setattr(verbs, "_address", lambda: Address())
    said = []
    assert verbs.cmd_pair(_ns(), said.append, environ=env) == 0
    url = said[-1]
    assert url.startswith("https://box.example.ts.net:8461/pair?n=")
    code = url.split("#c=", 1)[1]
    later = pairing.tickets(Layout("ui").runtime_dir(env))
    assert later.redeem(code) and not later.redeem(code)


def test_pair_refuses_with_the_locator_text_and_without_an_address(env, tmp_path, monkeypatch):
    monkeypatch.setattr(verbs, "_address", lambda: Address())
    moved = {**env, "MOXI_HOME": str(tmp_path / "moved")}
    with pytest.raises(cli.Refused, match="MOXI_HOME"):
        verbs.cmd_pair(_ns(), print, environ=moved)
    monkeypatch.setattr(verbs, "_address", lambda: None)
    with pytest.raises(cli.Refused, match="moxi ui gateway on"):
        verbs.cmd_pair(_ns(), print, environ=env)


def test_the_tunnel_missing_or_too_old_is_refused_with_its_fix(monkeypatch):
    monkeypatch.setattr(doctor, "tunnel_state", lambda: ("absent", "tunnel isn't installed"))
    with pytest.raises(cli.Refused, match="moxi install ui --with tunnel"):
        verbs._address()
    reason = "moxi-tunnel 0.1.0 offers interfaces {1}; the caller speaks 2"
    monkeypatch.setattr(doctor, "tunnel_state", lambda: ("old", reason))
    monkeypatch.setattr(doctor, "raise_tunnel", lambda: "uv tool upgrade moxi")
    with pytest.raises(cli.Refused) as err:
        verbs._address()
    text = str(err.value)
    assert "too old for the gateway" in text and reason in text
    assert "leaves an installed tunnel as it is" in text and "uv tool upgrade moxi" in text


def _device(state: Path, name: str, **kw):
    credential = kw.get("credential")
    return Devices.of(state).add(name=name, credential=credential,
                                 public_key="AAEC" if credential else None,
                                 sign_count=0, host="box.example.ts.net", provider="tailscale",
                                 change="c1")


def test_devices_lists_and_revoke_deletes_and_writes_its_row():
    state = Layout("ui").state_dir()
    one = _device(state, "phone", credential="abc")
    _device(state, "tablet")
    said = []
    verbs.cmd_devices(_ns(), said.append)
    assert len(said) == 2 and one.id in said[0] and "no passkey" in said[1]
    verbs.cmd_revoke(_ns(id=one.id), said.append)
    assert Devices.of(state).get(one.id) is None
    rows = [r for r in read_rows(state) if r.get("kind") == "revoke"]
    assert rows and rows[-1]["device"] == one.id
    with pytest.raises(cli.Refused, match="no paired device"):
        verbs.cmd_revoke(_ns(id="0" * 16), print)


def test_log_prints_the_rows_plain_and_as_json(capsys):
    state = Layout("ui").state_dir()
    audit = Audit(state)
    audit("held", {"reason": "port taken"})
    audit.close()
    assert cli.main(["gateway", "log", "--json", "--days", "2"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert json.loads(lines[-1])["kind"] == "held"
    assert cli.main(["gateway", "log"]) == 0
    assert "held reason=port taken" in capsys.readouterr().out
    assert cli.main(["gateway", "log", "--days", "0"]) == 1


# --- on, off ------------------------------------------------------------------------------------


class World:
    """The seams `on` and `off` reach, recorded."""

    def __init__(self, monkeypatch, env, *, address=None, kind="tcp", extra=True,
                 state=("current", None), tty=True, answer=True, shell="up"):
        self.ran: list[list[str]] = []
        self.started = 0
        self.address = address
        runtime = Layout("ui").runtime_dir(env, create=True)
        self.runtime = runtime
        monkeypatch.setattr(verbs, "_run", lambda argv: self.ran.append(list(argv)) or 0)
        monkeypatch.setattr(verbs, "_tty", lambda: tty)
        monkeypatch.setattr(verbs, "_ask", lambda q: self.asked.append(q) or answer)
        monkeypatch.setattr(verbs, "_layout", lambda: SimpleNamespace(
            runtime_dir=lambda e=None, create=False: runtime))
        monkeypatch.setattr(endpoint, "resolve_kind", lambda name, passed=(): kind)
        monkeypatch.setattr(doctor, "tunnel_state", lambda: state)
        monkeypatch.setattr("moxi_ui.gateway.listener.extra_installed", lambda: extra)
        monkeypatch.setattr("moxi_ui.gateway.listener.read_tunnel",
                            lambda: SimpleNamespace(address=self.address, reason=None))
        monkeypatch.setattr(cli, "ensure_shell", lambda port, say=print: (
            SimpleNamespace(port=port) if shell == "up" else None))
        monkeypatch.setattr(cli, "_units_start", self._start)
        monkeypatch.setattr(cli, "_wait_up", lambda s: SimpleNamespace())
        monkeypatch.setattr(verbs, "_doctor_text", lambda: "the doctor's text")
        monkeypatch.setattr(verbs, "HOLD_WAIT", 0.3)
        self.asked: list[str] = []

    def _start(self):
        self.started += 1
        return [SimpleNamespace(ok=True, lines=["started moxi-ui-shell"])]

    def words(self) -> list[list[str]]:
        return [argv[4:] for argv in self.ran]


def test_on_checks_in_order_and_names_each_fix(env, tmp_path, monkeypatch):
    World(monkeypatch, env, extra=False)
    with pytest.raises(cli.Refused, match="MOXI_HOME"):
        verbs.cmd_on(_ns(), print, environ={**env, "MOXI_HOME": str(tmp_path / "m")})
    with pytest.raises(cli.Refused, match="moxi install ui --with gateway"):
        verbs.cmd_on(_ns(), print, environ=env)
    World(monkeypatch, env, state=("absent", "tunnel isn't installed"))
    with pytest.raises(cli.Refused, match="moxi install ui --with tunnel"):
        verbs.cmd_on(_ns(), print, environ=env)
    world = World(monkeypatch, env, kind="unix")
    with pytest.raises(cli.Refused, match="the gateway's socket"):
        verbs.cmd_on(_ns(), print, environ=env)
    assert world.ran == []  # nothing set, nothing started


def test_on_sets_the_switch_starts_the_shell_waits_for_the_hold_then_adds_the_entry(
        env, monkeypatch):
    world = World(monkeypatch, env, shell="socket")
    endpoint.write_hold(world.runtime, 8461, pid=os.getpid())
    said = []
    assert verbs.cmd_on(_ns(), said.append, environ=env) == 0
    assert world.words() == [["config", "set", "--", "ui", "gateway.enabled", "true"],
                             ["tunnel", "add", "ui/gateway"]]
    assert world.started == 1  # the socket held the port, so the unit was started
    world.address = Address()
    world.ran.clear()
    assert verbs.cmd_on(_ns(), said.append, environ=env) == 0
    assert world.words() == [["config", "set", "--", "ui", "gateway.enabled", "true"]]
    assert said[-1] == "the gateway is on at https://box.example.ts.net:8461/"


def test_on_stops_with_the_doctors_text_when_no_hold_comes(env, monkeypatch):
    world = World(monkeypatch, env)
    with pytest.raises(cli.Refused, match="the doctor's text"):
        verbs.cmd_on(_ns(), print, environ=env)
    assert ["tunnel", "add", "ui/gateway"] not in world.words()


def test_off_clears_the_switch_and_asks_about_the_entry(env, monkeypatch):
    world = World(monkeypatch, env)
    said = []
    verbs.cmd_off(_ns(), said.append)
    assert world.words() == [["config", "unset", "--", "ui", "gateway.enabled"]]
    assert world.asked == []  # no entry, no question
    world = World(monkeypatch, env, address=Address())
    verbs.cmd_off(_ns(), said.append)
    assert world.asked == [f"{verbs.OFF_QUESTION} {verbs.OFF_PERSIST}"]
    assert world.words()[-1] == ["tunnel", "remove", "ui/gateway"]
    world = World(monkeypatch, env, address=Address(form="unix"), kind="unix", answer=False)
    verbs.cmd_off(_ns(), said.append)
    assert world.asked == [verbs.OFF_QUESTION]
    assert ["tunnel", "remove", "ui/gateway"] not in world.words()
    world = World(monkeypatch, env, address=Address(), tty=False)
    verbs.cmd_off(_ns(), said.append)
    assert world.asked == [] and said[-1].endswith("To remove it: moxi tunnel remove ui/gateway")
    assert verbs.OFF_PERSIST in said[-1]
    world = World(monkeypatch, env, address=REFUSED)
    monkeypatch.setattr("moxi_ui.gateway.listener.read_tunnel",
                        lambda: SimpleNamespace(address=REFUSED, reason="applied.json is newer"))
    assert verbs.cmd_off(_ns(), said.append) == 0
    assert world.asked == [] and "applied.json is newer" in said[-1]
    assert "moxi doctor tunnel" in said[-1]
    assert said[-1].endswith("To remove it: moxi tunnel remove ui/gateway")


# --- the doctor's rows --------------------------------------------------------------------------


def _facts(tmp_path, **kw) -> Facts:
    base = dict(system="linux", kind="tcp", runtime_dir=str(tmp_path / "r"),
                state_dir=str(tmp_path / "s"), switch=True, port=8460, gateway_port=8461,
                extra=True, tunnel="current", address=Address(), shell_running=True,
                acts={"acts": False, "reason": "tcp"}, hold=8461)
    base.update(kw)
    return Facts(**base)


def test_the_gateway_row_is_off_then_ok_with_what_it_serves(tmp_path):
    row = doctor.gateway_row(_facts(tmp_path, switch=False, address=None))
    assert row.state == "off"
    row = doctor.gateway_row(_facts(tmp_path))
    assert row.state == "ok"
    assert "https://box.example.ts.net:8461/" in row.detail and "127.0.0.1:8461" in row.detail
    assert "0 devices" in row.detail and doctor.ACTS_LINES["tcp"] in row.detail
    row = doctor.gateway_row(_facts(tmp_path, kind="unix", acts={"acts": True, "reason": None}))
    assert row.detail.endswith("the phone's acts on")
    row = doctor.gateway_row(_facts(tmp_path, shell_running=False, acts=None))
    assert row.state == "ok" and row.detail.endswith("off: Moxi isn't running")


@pytest.mark.parametrize(("change", "detail", "fix"), [
    ({"moved": "MOXI_HOME moves the shell's runtime directory"}, "MOXI_HOME", None),
    ({"extra": False}, "extra isn't installed", "moxi install ui --with gateway"),
    ({"tunnel": "absent", "address": None}, "tunnel isn't installed",
     "moxi install ui --with tunnel"),
    ({"tunnel": "old", "tunnel_reason": "offers interfaces {1}"}, "too old for the gateway",
     "uv"),
    ({"gateway_port": 8460, "hold": None}, "the shell's own port", "gateway.port"),
    ({"kind": "unix", "path_problem": "the gateway's socket can't live under /x"}, "/x", None),
    ({"held": {"reason": "taken"}, "hold": None},
     "port 8461 was held by another program; the tunnel entry was removed",
     "free the port and run `moxi ui gateway on`"),
    ({"held": {"reason": "taken", "removed": None}, "hold": None},
     "port 8461 was held by another program; there was no tunnel entry to remove",
     "free the port and run `moxi ui gateway on`"),
    ({"address": None}, "no ui/gateway tunnel entry", "moxi ui gateway on"),
    ({"address": None, "retired_problem": "/s/retired.json can't be read (not a list)"},
     "retired origins, /s/retired.json can't be read (not a list), so tunnel refuses",
     "the copy refills from tunnel's record, unless tunnel was purged"),
    ({"address": Address(state="applying")}, "entry is applying", "moxi tunnel repair"),
    ({"kind": "unix", "acts": {"acts": False, "reason": "target"}},
     "doesn't forward to this Moxi", "moxi tunnel repair"),
    ({"uid_found": False}, "can't tell which user tailscaled runs as", None),
    ({"uid_found": False, "system": "wsl"}, "outside this VM", None),
    ({"acts": {"acts": False, "reason": "tcp", "uid_found": False}},
     "the running Moxi hasn't yet found which user tailscaled runs as", None),
])
def test_each_failure_is_named_with_its_fix(tmp_path, change, detail, fix):
    row = doctor.gateway_row(_facts(tmp_path, **change))
    assert row.state == "fail", row
    assert detail in row.detail
    assert (row.fix is None) if fix is None else (fix in row.fix)


@pytest.mark.parametrize(("change", "detail", "fix"), [
    ({"address": REFUSED, "tunnel_reason": "applied.json is newer"}, "applied.json is newer",
     "moxi doctor tunnel"),
    ({"switch": False}, "gateway.enabled is off while", "moxi tunnel remove ui/gateway"),
    ({"address": Address(exposure="internet")}, "from the internet", None),
    ({"kind": "unix", "acts": {"acts": False, "reason": "origin"}}, "outlived Moxi",
     "moxi tunnel remove ui/gateway, then moxi ui gateway on"),
    ({"kind": "unix", "acts": {"acts": False, "reason": "origin"},
      "address": Address(provider="cloudflare", port=443, exposure="own-devices")},
     "outlived Moxi", "--hostname <new>"),
])
def test_each_warning_is_named_with_its_fix(tmp_path, change, detail, fix):
    row = doctor.gateway_row(_facts(tmp_path, **change))
    assert row.state == "warn", row
    assert detail in row.detail
    assert (row.fix is None) if fix is None else (fix in row.fix)


def test_a_stale_device_is_a_warning(tmp_path):
    stale = SimpleNamespace(stale=True, name="old phone")
    row = doctor.gateway_row(_facts(tmp_path, devices=[stale]))
    assert row.state == "warn" and "old phone" in row.detail and "1 device," in row.detail


def test_the_login_row_follows_the_resolved_start_and_the_unit(tmp_path):
    unit = SimpleNamespace(state="active", enabled=True)
    assert doctor.login_row(_facts(tmp_path, switch=False)).state == "off"
    row = doctor.login_row(_facts(tmp_path, starts="login", unit=unit))
    assert (row.state, row.detail) == ("ok", "the shell starts at login")
    row = doctor.login_row(_facts(tmp_path, system="darwin", starts="login", unit=unit))
    assert row.detail.endswith("from the next login")
    row = doctor.login_row(_facts(tmp_path, starts="connection", unit=unit))
    assert (row.state, row.fix) == ("warn", "moxi install ui")
    row = doctor.login_row(_facts(tmp_path, starts="login",
                                  unit=SimpleNamespace(state="missing", enabled=None)))
    assert row.state == "warn"
    row = doctor.login_row(_facts(tmp_path, system="win32", starts="connection", unit=unit))
    assert row.state == "ok" and "every logon" in row.detail


def test_the_audit_row_checks_modes(tmp_path):
    facts = _facts(tmp_path, switch=False)
    assert doctor.audit_row(facts).state == "off"
    audit = Audit(facts.state_dir)
    audit("held", {"reason": "x"})
    audit.close()
    assert doctor.audit_row(facts).state == "ok"
    day = next((Path(facts.state_dir) / "gateway" / "audit").glob("*.jsonl"))
    day.chmod(0o644)
    row = doctor.audit_row(facts)
    assert row.state == "fail" and "644" in row.detail
    day.chmod(0o600)
    day.parent.chmod(0o755)
    assert doctor.audit_row(facts).state == "fail"


def test_the_doctor_reads_a_current_hold_failure_from_the_audit_log(monkeypatch):
    monkeypatch.setattr(doctor, "_shell", lambda: (True, 0.0))
    monkeypatch.setattr(doctor, "tunnel_state", lambda: ("absent", "tunnel isn't installed"))
    monkeypatch.setattr(endpoint, "resolve_kind", lambda name, passed=(): "tcp")
    state = Layout("ui").state_dir()
    audit = Audit(state)
    audit("held", {"reason": "port 8461 is held by another program"})
    audit.close()
    facts = doctor.read_facts()
    assert facts.held is not None and facts.held["kind"] == "held"
    monkeypatch.setattr(doctor, "_shell", lambda: (False, None))
    assert doctor.read_facts().held is None
    names = [row.name for row in doctor.rows()]
    assert names == ["gateway", "gateway login", "gateway audit"]


@pytest.mark.parametrize("text", [b"{}", b"not json", b"\xff"])
def test_the_doctor_names_a_copy_of_the_retired_origins_it_cant_read(monkeypatch, text):
    monkeypatch.setattr(doctor, "_shell", lambda: (False, None))
    monkeypatch.setattr(doctor, "tunnel_state", lambda: ("absent", "tunnel isn't installed"))
    state = Layout("ui").state_dir()
    assert doctor.read_facts().retired_problem is None
    path = decl.retired_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text)
    problem = doctor.read_facts().retired_problem
    assert problem is not None and problem.startswith(f"{path} can't be read (")


def test_the_command_that_raises_an_installed_tunnel(tmp_path):
    (tmp_path / "uv-receipt.toml").write_text("[tool]\n")
    assert doctor.raise_tunnel(str(tmp_path)) == "uv tool upgrade moxi"
    plain = tmp_path / "plain"
    plain.mkdir()
    text = doctor.raise_tunnel(str(plain), which=lambda name: "/bin/uv", python="/env/python")
    assert text == "uv pip install --python /env/python 'moxi-tunnel>=0.2,<0.3'"
    text = doctor.raise_tunnel(str(plain), which=lambda name: None, python="/env/python")
    assert text == "/env/python -m pip install 'moxi-tunnel>=0.2,<0.3'"


def test_the_installer_counts_any_installed_tunnel_as_the_extra():
    """Why `on` names its own command: the core counts an extra installed by its
    distributions alone, whatever their versions."""
    from moxi import cli as core_cli

    assert core_cli.extra_installed("moxi-ui", "tunnel")
    assert "Version ranges are not checked" in (core_cli.extra_installed.__doc__ or "")


# --- the announcement and the settings mark -----------------------------------------------------


def test_the_shell_starts_at_login_while_the_switch_is_on():
    from moxi import units

    found = modules.load("ui")
    assert found.state == modules.PRESENT, found.reason
    assert found.daemons["shell"]["socket"]["start"] == {"login": "gateway.enabled"}
    assert units.starts(found) == {"shell": "connection"}


def _listing(now):
    return {"daemons": {"shell": {"socket": {"setting": "port",
                                             "start": {"login": "gateway.enabled"}}}},
            "starts": {"shell": now}}


@pytest.mark.parametrize(("platform", "now", "mark"), [
    ("linux", "login", "starts the shell at login; the shell starts at login now"),
    ("linux", "connection", "starts the shell at login; the shell starts at the first "
                            "connection now"),
    ("darwin", "login", "starts the shell at login; the shell starts at login now, from the "
                        "next login"),
    ("linux", None, "starts the shell at login"),
    ("win32", "connection", "the shell starts at every logon on Windows; this switch doesn't "
                            "change that"),
])
def test_the_settings_mark_of_the_login_switch(platform, now, mark):
    item = {"name": "gateway.enabled", "type": "bool", "read": "use", "value": True}
    out = settings.descriptor("ui", item, switches=settings.switches_of(_listing(now)),
                              logins=settings.logins_of(_listing(now)), platform=platform)
    assert out["mark"] == mark
    assert "restart" not in out["mark"]
    other = settings.descriptor("ui", {"name": "theme.mode", "type": "enum", "read": "use"},
                                logins=settings.logins_of(_listing(now)), platform=platform)
    assert other["mark"] == settings.MARK_NOW
