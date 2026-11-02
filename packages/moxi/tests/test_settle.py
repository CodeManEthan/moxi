"""The home move and the settle step in this process, on the fake backend and its simulated
supervisor: each path out of the step starts exactly the units the move stopped, and only
those."""

from __future__ import annotations

import io
import json
import os
import signal
import sys
import textwrap
import types
from pathlib import Path

import pytest

import moxi.paths
from conftest import drop_modules
from moxi import _settle, config, doctor, files, home, units
from moxi.units import _backend, _declare, _plan, _state
from moxi.units._fake import FakeBackend, FakeSupervisor

SETTINGS = """
[relay.enabled]
type = "bool"
default = false
doc = "Run the relay."
read = "use"

[snapshot.enabled]
type = "bool"
default = false
doc = "Take snapshots."
read = "use"

[port]
type = "int"
default = 8470
doc = "The relay's port."
read = "start"
"""

RELAY = "'relay': {'command': 'moxi-demo-relay', 'doc': 'the relay'}"
TWO = "{" + RELAY + ", 'aux': {'command': 'moxi-demo-relay'}}"
ONE = "{" + RELAY + "}"
SOCKET = ("{'relay': {'command': 'moxi-demo-relay', "
          "'socket': {'setting': 'port', 'addresses': ['127.0.0.1']}}}")
TIMER = ("{'snapshot': {'command': 'moxi-demo-relay', 'every': '5m', "
         "'doc': 'take a snapshot', 'switch': 'snapshot.enabled'}}")

SETUP = """
import os

def run():
    how = os.environ.get("MOXITEST_SETUP", "ok")
    if how == "fail":
        raise RuntimeError("no database")
    if how == "exit":
        return 4
    if how == "signal":
        os.kill(os.getpid(), signal_number())
        return None
    if how == "catch":
        from moxi._settle import Interrupted
        try:
            os.kill(os.getpid(), signal_number())
        except Interrupted:
            return None

def signal_number():
    import signal
    return signal.SIGTERM
"""


@pytest.fixture
def w(site, tmp_path, monkeypatch):
    """A module `demo` with a legacy state folder, its units written and running on the fake
    backend, and fresh signal state for the settle step."""
    scripts = tmp_path / "bin"
    scripts.mkdir()
    monkeypatch.setattr(moxi.paths, "_scripts", lambda: scripts)
    for name in ("moxi-demo-relay",):
        (scripts / name).write_text("#!/bin/sh\n")
        (scripts / name).chmod(0o755)
    monkeypatch.setenv("MOXI_DEMO_DEVICE", "oldbox")
    monkeypatch.setattr(_settle, "_state", _settle._Signals())
    saved = {s: signal.getsignal(s) for s in _settle.signals()}
    root = tmp_path / "units"
    root.mkdir()
    supervisor = FakeSupervisor(root)
    backend = FakeBackend(root, runner=supervisor)
    lines: list[str] = []
    home_dir = Path(os.environ["MOXI_HOME"])

    def module(*, units_from=None, version="0.1.0", **names):
        site.write("moxi_demo/settings.toml", SETTINGS)
        site.write("moxi_demo/setup.py", SETUP)
        names.setdefault("SETTINGS", "'settings.toml'")
        names.setdefault("SETUP", "'moxi_demo.setup:run'")
        if units_from is not None:
            site.write("moxi_demo/runtime.py", textwrap.dedent(units_from))
            names["UNITS_FROM"] = "'moxi_demo.runtime:units'"
        site.module("demo", version=version, **names)
        moxi.modules.reset()
        for mod in [m for m in sys.modules if m.startswith("moxi_demo")]:
            del sys.modules[mod]

    def installed(**names):
        module(**names)
        config.set("demo", "relay.enabled", True, by="test")
        for change in units.plan("demo"):
            if change.digest is not None:
                _state.record("demo", change.name, change.digest)
        done = units.apply(units.plan("demo"), say=lines.append)
        assert all(a.ok for a in done), done
        _settle.record_setup("demo", "0.1.0")
        supervisor.calls.clear()

    def legacy(kind="state", data=True):
        old = home_dir / kind / "oldbox" / "demo"
        old.mkdir(parents=True)
        if data:
            (old / "data.txt").write_text("live")
        return old

    def calls(*verbs):
        return [c for c in supervisor.calls if c[0] in verbs]

    def active(unit):
        return supervisor.units.get(unit, {}).get("active", False)

    try:
        with _backend.use(backend):
            yield types.SimpleNamespace(site=site, root=root, sup=supervisor, backend=backend,
                                        module=module, installed=installed,
                                        legacy=legacy, calls=calls, active=active,
                                        lines=lines, home=home_dir, tmp=tmp_path)
    finally:
        for signum, handler in saved.items():
            signal.signal(signum, handler)


def settle(w, capsys, monkeypatch, *, setup="ok", replace=False):
    monkeypatch.setenv("MOXITEST_SETUP", setup)
    code = _settle.settle("demo", replace=replace)
    out, err = capsys.readouterr()
    return code, out + err


SERVICE = "moxi-demo-relay.service"
POSIX = pytest.mark.skipif(sys.platform == "win32",
                           reason="symbolic links need a privilege on Windows")


# -- the move ----------------------------------------------------------------------------------

def test_with_nothing_to_move_the_move_reads_and_stops_nothing(w, monkeypatch):
    w.installed(DAEMONS=ONE)
    monkeypatch.setattr(units, "declarations", lambda m: pytest.fail("read"))
    assert home.move("demo", []) == []
    assert w.sup.calls == []


def test_the_move_stops_records_renames_and_removes_the_host_folder(w):
    w.installed(DAEMONS=SOCKET)
    old, cache = w.legacy(), w.legacy("cache")
    record: list = []
    assert home.move("demo", record, say=w.lines.append) is record
    assert [(s.unit, s.marked) for s in record] == [
        ("moxi-demo-relay.socket", True), (SERVICE, True)]
    assert w.calls("stop") == [("stop", "moxi-demo-relay.socket"), ("stop", SERVICE)]
    assert (w.home / "state" / "demo" / "data.txt").read_text() == "live"
    assert (w.home / "cache" / "demo" / "data.txt").exists()
    assert not old.exists() and not old.parent.exists() and not cache.parent.exists()
    assert w.calls("start") == []          # the start is the settle step's


def test_an_inactive_marked_daemon_is_neither_stopped_nor_recorded(w):
    w.installed(DAEMONS=TWO)
    w.sup.units["moxi-demo-aux.service"]["active"] = False
    w.legacy()
    record = home.move("demo", [])
    assert [s.unit for s in record] == [SERVICE]


@pytest.mark.parametrize("case", ["both", "units_from", "overrun", "unmarked", "device"])
def test_each_refusal_comes_before_any_stop(w, monkeypatch, case):
    units_from = None
    if case == "units_from":
        units_from = "def units():\n    raise OSError('no config')\n"
    if case == "overrun":
        monkeypatch.setattr(_declare, "UNITS_FROM_SECONDS", 0.2)
        units_from = "import time\ndef units():\n    time.sleep(3)\n"
    w.installed(DAEMONS=ONE, **({} if units_from is None else {}))
    if units_from is not None:
        w.module(DAEMONS=ONE, units_from=units_from)
    old = w.legacy()
    if case == "both":
        (w.home / "state" / "demo").mkdir()
        (w.home / "state" / "demo" / "new.txt").write_text("new")
    if case == "unmarked":
        (w.root / SERVICE).write_text("[Service]\nExecStart=/bin/true\n")
    if case == "device":
        for name in ("stat", "lstat"):
            real = getattr(os, name)

            def stat(path, *a, real=real, **k):
                st = real(path, *a, **k)
                if Path(path) == old:
                    return os.stat_result((*st[:2], st.st_dev + 1, *st[3:]))
                return st
            monkeypatch.setattr(os, name, stat)
    record: list = []
    with pytest.raises(home.MoveError) as caught:
        home.move("demo", record)
    assert caught.value.refused and record == []
    assert w.calls("stop") == [] and (old / "data.txt").exists()
    line = caught.value.line
    assert {"both": f"both {old} and {w.home / 'state' / 'demo'} hold its state data",
            "units_from": "demo: can't tell what to stop (UNITS_FROM failed: OSError: "
                          "no config); nothing moved",
            "overrun": "UNITS_FROM failed: did not finish in 0.2 s",
            "unmarked": f"{SERVICE} was not written by moxi; `moxi install demo "
                        "--replace` moves it to DATA/moxi/displaced/",
            "device": "are on different file systems"}[case] in line


def test_with_replace_the_unmarked_running_unit_is_stopped_and_recorded(w):
    w.module(DAEMONS=ONE)
    (w.root / SERVICE).write_text("[Service]\nExecStart=/bin/true\n")
    w.sup.reload()
    w.sup.state(SERVICE)["active"] = True
    w.legacy()
    record = home.move("demo", [], replace=True)
    assert [(s.unit, s.marked) for s in record] == [(SERVICE, False)]
    assert (w.home / "state" / "demo").is_dir()


def test_a_stop_that_fails_on_the_second_unit_raises_before_the_rename(w):
    w.installed(DAEMONS=TWO)
    w.sup.fail[("stop", "moxi-demo-aux.service")] = "Job timed out."
    old = w.legacy()
    record: list = []
    with pytest.raises(home.MoveError) as caught:
        home.move("demo", record)
    assert not caught.value.refused
    assert caught.value.line == "demo: moxi-demo-aux.service did not stop; nothing moved"
    assert [s.unit for s in record] == [SERVICE, "moxi-demo-aux.service"]
    assert (old / "data.txt").exists()


def test_an_empty_new_directory_is_replaced_and_an_empty_old_one_tidied(w):
    w.installed(DAEMONS=ONE)
    w.legacy()
    (w.home / "state" / "demo").mkdir()
    assert [s.unit for s in home.move("demo", [])] == [SERVICE]
    assert (w.home / "state" / "demo" / "data.txt").exists()
    old = w.legacy("cache", data=False)
    (w.home / "cache" / "demo").mkdir()
    w.sup.calls.clear()
    assert home.move("demo", []) == [] and w.sup.calls == []
    assert not old.exists()


# -- an old directory that is a symbolic link ---------------------------------------------

def home_rows(module="demo"):
    from moxi.doctor import _run

    return {r.name: r for r in _run.run([module]) if r.name.startswith("home ")}


def linked_legacy(w, *, relative=False, label="oldbox"):
    """The old state folder as a link to data kept elsewhere."""
    elsewhere = w.tmp / "disk" / "demo-state"
    elsewhere.mkdir(parents=True)
    (elsewhere / "data.txt").write_text("live")
    link = w.home / "state" / label / "demo"
    link.parent.mkdir(parents=True)
    link.symlink_to(os.path.relpath(elsewhere, link.parent) if relative else elsewhere)
    return link, elsewhere


@POSIX
def test_an_absolute_legacy_link_is_read_named_and_moved_as_the_link(w):
    w.installed(DAEMONS=ONE)
    link, elsewhere = linked_legacy(w)
    new = w.home / "state" / "demo"
    assert moxi.paths.Layout("demo").state_dir() == link
    row = home_rows()["home state"]
    assert (row.state, row.fix) == ("warn", "moxi install demo")
    assert str(link) in row.detail
    record = home.move("demo", [])
    assert [s.unit for s in record] == [SERVICE]
    assert new.is_symlink() and new.resolve() == elsewhere.resolve()
    assert (new / "data.txt").read_text() == "live"
    assert not os.path.lexists(link) and not link.parent.exists()
    assert moxi.paths.Layout("demo").state_dir() == new
    assert "home state" not in home_rows()


@POSIX
def test_a_relative_legacy_link_refuses_the_move_before_any_stop(w):
    w.installed(DAEMONS=ONE)
    link, elsewhere = linked_legacy(w, relative=True)
    new = w.home / "state" / "demo"
    assert moxi.paths.Layout("demo").state_dir() == link
    record: list = []
    with pytest.raises(home.MoveError) as caught:
        home.move("demo", record)
    assert caught.value.refused and caught.value.only_move and record == []
    assert w.calls("stop") == []
    assert caught.value.line == (
        f"demo: {link} is a link to a relative path; make {link} an absolute link, or "
        f"move the folder to {new} by hand, then run `moxi install demo` again")
    assert link.is_symlink() and (link / "data.txt").read_text() == "live"
    row = home_rows()["home state"]
    assert row.fix == home.link_fix("demo", link, new)


@POSIX
def test_with_two_labels_whose_first_is_a_link_all_three_take_the_first(w, monkeypatch):
    monkeypatch.setattr(moxi.paths, "_host_name", lambda: "thishost")
    w.installed(DAEMONS=ONE)
    link, elsewhere = linked_legacy(w)
    second = w.home / "state" / "thishost" / "demo"
    second.mkdir(parents=True)
    (second / "other.txt").write_text("other")
    assert moxi.paths.Layout("demo").state_dir() == link
    assert str(link) in home_rows()["home state"].detail
    assert [old for _, old, _ in home.legacy("demo")] == [link]
    home.move("demo", [])
    new = w.home / "state" / "demo"
    assert new.is_symlink() and (new / "data.txt").read_text() == "live"
    assert (second / "other.txt").exists()


@POSIX
def test_a_relative_legacy_link_lets_the_rest_of_the_install_run_and_exits_1(
        w, capsys, monkeypatch):
    w.installed(DAEMONS=ONE)
    link, _ = linked_legacy(w, relative=True)
    w.module(DAEMONS=ONE, version="0.2.0")
    code, out = settle(w, capsys, monkeypatch)
    assert code == 1
    assert "is a link to a relative path" in out
    assert "setup demo: done" in out
    assert _settle.setup_version("demo") == "0.2.0"
    assert w.calls("stop") == []                     # the move stopped nothing
    assert w.active(SERVICE)
    assert moxi.paths.Layout("demo").state_dir() == link


def test_the_move_takes_the_core_lock_nested(w, monkeypatch):
    w.installed(DAEMONS=ONE)
    taken = []
    real = files.lock
    monkeypatch.setattr(files, "lock", lambda p, **k: taken.append((p, k)) or real(p, **k))
    home.move("demo", [])
    assert taken[0] == (moxi.paths.core_lock(), {"nested": True})


# -- the settle step: every path starts exactly the record ---------------------------------

def test_a_clean_settle_moves_sets_up_applies_and_starts_the_record(w, capsys, monkeypatch):
    w.installed(DAEMONS=SOCKET)
    w.legacy()
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert "setup demo: done" in out
    assert w.calls("stop", "start") == [
        ("stop", "moxi-demo-relay.socket"), ("stop", SERVICE),
        ("start", "moxi-demo-relay.socket"), ("start", SERVICE)]
    assert w.active(SERVICE)
    assert _settle.setup_version("demo") == "0.1.0"


def _other_command(w, *, keep=False):
    """The relay's socket daemon with another command: its service text changes and its
    socket set does not. (The name is longer, so the module file's size changes and its
    cached bytecode, written in the same second, is not taken for it.)"""
    script = moxi.paths._scripts() / "moxi-demo-relay-next"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    w.module(DAEMONS=SOCKET.replace("'moxi-demo-relay'", "'moxi-demo-relay-next'"
                                    + (", 'upgrade': 'keep'" if keep else "")))
    assert {c.kind: c.action for c in units.plan("demo")} == {"daemon": "change",
                                                              "socket": "keep"}


def _show_fails_after_the_move(w, monkeypatch):
    """The supervisor's `show` fails once the move has stopped the service, so the unit
    step's read of which services run fails and the move's own read does not."""
    def runner(cmd, **kw):
        if cmd[2:3] == ["show"] and ("stop", SERVICE) in w.sup.calls:
            w.sup.calls.append(tuple(a for a in cmd[2:] if a != "--"))
            import subprocess
            return subprocess.CompletedProcess(cmd, 1, "", "Failed to connect to bus\n")
        return w.sup(cmd, **kw)

    monkeypatch.setattr(w.backend, "runner", runner)


@pytest.mark.parametrize("read", ["answered", "failed"])
def test_after_a_move_a_changed_socket_daemon_starts_its_units_first_and_exits_0(
        w, capsys, monkeypatch, read):
    """The service's text changed and its set did not (`upgrade = "restart"`): the unit
    step starts the socket unit, then the service (after a `stop` that does nothing when
    the read failed, never a `restart`), and `resume`'s starts of the same ids are no-ops
    the supervisor refuses none of."""
    w.installed(DAEMONS=SOCKET)
    _other_command(w)
    w.legacy()
    if read == "failed":
        _show_fails_after_the_move(w, monkeypatch)
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert "refusing" not in out
    sock = "moxi-demo-relay.socket"
    unit_step = [("start", sock), ("start", SERVICE)]
    if read == "failed":
        unit_step = [("stop", SERVICE)] + unit_step
    assert w.calls("stop", "start", "restart") == [
        ("stop", sock), ("stop", SERVICE), *unit_step, ("start", sock), ("start", SERVICE)]
    assert w.active(sock) and w.active(SERVICE)


@pytest.mark.parametrize("read", ["answered", "failed"])
def test_after_a_move_a_keep_socket_daemon_with_its_set_unchanged_waits_for_resume(
        w, capsys, monkeypatch, read):
    """`upgrade = "keep"` with an unchanged set: the unit step starts nothing, and
    `resume` starts the socket unit while the service is down, then the service."""
    monkeypatch.setattr(_plan, "ipv6_loopback", lambda: True)
    w.installed(DAEMONS=SOCKET.replace("'moxi-demo-relay'",
                                       "'moxi-demo-relay', 'upgrade': 'keep'"))
    _other_command(w, keep=True)
    w.legacy()
    if read == "failed":
        _show_fails_after_the_move(w, monkeypatch)
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    sock = "moxi-demo-relay.socket"
    assert w.calls("stop", "start", "restart") == [
        ("stop", sock), ("stop", SERVICE), ("start", sock), ("start", SERVICE)]
    assert w.active(sock) and w.active(SERVICE)


@pytest.mark.parametrize("path", ["refusal", "rename", "setup", "exit", "command",
                                  "switch", "keep"])
def test_every_path_out_starts_exactly_the_recorded_units(w, capsys, monkeypatch, path):
    w.installed(DAEMONS=TWO)
    old = w.legacy()
    if path == "refusal":
        (w.home / "state" / "demo").mkdir()
        (w.home / "state" / "demo" / "x").write_text("x")
    if path == "rename":
        monkeypatch.setattr(os, "rename", lambda a, b: (_ for _ in ()).throw(
            PermissionError(13, "Permission denied")))
    if path == "command":
        (moxi.paths._scripts() / "moxi-demo-relay").unlink()
    if path == "switch":
        w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', 'switch': 'port'}, "
                         "'aux': {'command': 'moxi-demo-relay'}}")
    setup = {"setup": "fail", "exit": "exit"}.get(path, "ok")
    code, out = settle(w, capsys, monkeypatch, setup=setup)
    stops = [c[1] for c in w.calls("stop")]
    starts = [c[1] for c in w.calls("start")]
    if path == "refusal":
        assert code == 1 and stops == [] and starts == []
        assert (old / "data.txt").exists()
        return
    assert stops == [SERVICE, "moxi-demo-aux.service"]
    assert starts == stops, out
    assert code == (0 if path == "keep" else 1), out
    if path in ("setup", "exit"):
        assert "setup demo: failed" in out
    if path == "rename":
        assert "could not move" in out and (old / "data.txt").exists()


def test_replace_over_an_unmarked_active_unit_with_a_failing_setup_starts_it_again(
        w, capsys, monkeypatch):
    w.module(DAEMONS=ONE)
    (w.root / SERVICE).write_text("[Service]\nExecStart=/bin/true\n")
    w.sup.reload()
    w.sup.state(SERVICE)["active"] = True
    w.legacy()
    code, out = settle(w, capsys, monkeypatch, setup="fail", replace=True)
    assert code == 1
    assert w.calls("stop", "start") == [("stop", SERVICE), ("start", SERVICE)]
    assert "ExecStart=/bin/true" in (w.root / SERVICE).read_text()   # never displaced


def test_replace_whose_unit_step_refuses_the_unit_starts_the_old_one(w, capsys, monkeypatch):
    w.module(DAEMONS=ONE)
    (w.root / SERVICE).write_text("[Service]\nExecStart=/bin/true\n")
    w.sup.reload()
    w.sup.state(SERVICE)["active"] = True
    w.legacy()
    (moxi.paths._scripts() / "moxi-demo-relay").unlink()
    code, out = settle(w, capsys, monkeypatch, replace=True)
    assert code == 1 and "no moxi-demo-relay in this environment" in out
    assert w.calls("start") == [("start", SERVICE)] and w.active(SERVICE)


def test_replace_that_displaces_the_unit_runs_the_new_marked_one(w, capsys, monkeypatch):
    w.module(DAEMONS=ONE)
    config.set("demo", "relay.enabled", True, by="test")
    (w.root / SERVICE).write_text("[Service]\nExecStart=/bin/true\n")
    w.sup.reload()
    w.sup.state(SERVICE)["active"] = True
    w.legacy()
    code, out = settle(w, capsys, monkeypatch, replace=True)
    assert code == 0, out
    assert "# moxi unit: demo/relay" in (w.root / SERVICE).read_text()
    assert w.active(SERVICE)
    assert w.calls("start")[-1] == ("start", SERVICE)   # resume's start: a no-op


def test_a_daemon_switched_off_and_removed_is_taken_off_the_record(w, capsys, monkeypatch):
    w.installed(DAEMONS="{'relay': {'command': 'moxi-demo-relay', "
                        "'switch': 'relay.enabled'}}")
    config._change("demo", "relay.enabled", "set", False, by="test")   # no apply yet
    w.sup.calls.clear()
    w.legacy()
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert ("stop", SERVICE) in w.calls("stop") and w.calls("start") == []
    assert not (w.root / SERVICE).exists()


def test_a_run_time_daemon_turned_disabled_is_taken_off_the_record(w, capsys, monkeypatch,
                                                                    tmp_path):
    tool = tmp_path / "tool"
    tool.write_text("")
    body = ("def units():\n    return {'daemons': {'extra': {'argv': [%r]%s}}}\n")
    w.installed(units_from=body % (str(tool), ""))
    w.module(units_from=body % (str(tool), ", 'enabled': False"))
    w.legacy()
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert ("stop", "moxi-demo-extra.service") in w.calls("stop")
    assert ("start", "moxi-demo-extra.service") not in w.calls("start")
    assert not w.active("moxi-demo-extra.service")


def test_a_rejected_run_time_daemon_is_stopped_and_started_again(w, capsys, monkeypatch,
                                                                  tmp_path):
    tool = tmp_path / "tool"
    tool.write_text("")
    w.installed(units_from=f"def units():\n    return {{'daemons': {{'extra': "
                           f"{{'argv': [{str(tool)!r}]}}}}}}\n")
    w.module(units_from="def units():\n    return {'daemons': {'extra': "
                        "{'argv': ['relative']}}}\n")
    w.legacy()
    code, out = settle(w, capsys, monkeypatch)
    assert code == 1   # plan refuses it (declaration)
    assert w.calls("stop", "start") == [("stop", "moxi-demo-extra.service"),
                                        ("start", "moxi-demo-extra.service")]


def test_a_change_that_drops_the_socket_takes_its_entry_out(w, capsys, monkeypatch):
    w.installed(DAEMONS=SOCKET)
    w.module(DAEMONS=ONE)
    w.legacy()
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert ("stop", "moxi-demo-relay.socket") in w.calls("stop")
    assert ("start", "moxi-demo-relay.socket") not in w.calls("start")
    assert ("start", SERVICE) in w.calls("start", "restart") or w.active(SERVICE)


SOCKETS = ("{'relay': {'command': 'moxi-demo-relay', "
           "'socket': {'setting': 'port', 'addresses': ['127.0.0.1', '::1']}}}")


def test_a_socket_unit_the_set_dropped_leaves_the_record(w, capsys, monkeypatch):
    """The move stops every socket unit, then the plan drops the second address's: its
    file is gone, so its entry leaves the record and only the rest start again."""
    monkeypatch.setattr(_plan, "ipv6_loopback", lambda: True)
    w.installed(DAEMONS=SOCKETS)
    for unit in ("moxi-demo-relay.socket", "moxi-demo-relay-1.socket", SERVICE):
        w.sup.state(unit)["active"] = True
    w.module(DAEMONS=SOCKET)
    w.legacy()
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    stops = w.calls("stop")
    assert stops.index(("stop", "moxi-demo-relay.socket")) < \
        stops.index(("stop", "moxi-demo-relay-1.socket")) < stops.index(("stop", SERVICE))
    assert ("disable", "--now", "moxi-demo-relay-1.socket") in w.calls("disable")
    assert not (w.root / "moxi-demo-relay-1.socket").exists()
    assert ("start", "moxi-demo-relay-1.socket") not in w.calls("start")
    starts = w.calls("start")
    assert starts.index(("start", "moxi-demo-relay.socket")) < starts.index(("start", SERVICE))
    assert w.active("moxi-demo-relay.socket") and w.active(SERVICE)


def test_a_dropped_socket_unit_takes_its_own_entry_out():
    def entry(unit):
        return units.Stopped("demo", "relay", unit, "fake", True)

    record = [entry("moxi-demo-relay.socket"), entry("moxi-demo-relay-1.socket"),
              entry(SERVICE)]
    change = units.UnitChange("demo", "relay", "socket", "change", None,
                              (Path("/u/moxi-demo-relay.socket"),), None, "",
                              dropped=(Path("/u/moxi-demo-relay-1.socket"),))
    _settle._take_out(record, [units.Applied(change, False)])
    assert len(record) == 3
    _settle._take_out(record, [units.Applied(change, True)])
    assert [e.unit for e in record] == ["moxi-demo-relay.socket", SERVICE]


def test_only_a_daemons_or_its_sockets_change_takes_an_entry_out():
    def applied(kind, action, name="relay", paths=()):
        change = units.UnitChange("demo", name, kind, action, None, paths, None, "")
        return units.Applied(change, True)

    def entry(name, unit):
        return units.Stopped("demo", name, unit, "fake", True)

    record = [entry("relay", SERVICE), entry("relay", "moxi-demo-relay.socket"),
              entry("other", "moxi-demo-other.service")]
    _settle._take_out(record, [applied("timer", "remove"), applied("wrap", "remove")])
    assert [e.unit for e in record] == [SERVICE, "moxi-demo-relay.socket",
                                        "moxi-demo-other.service"]
    _settle._take_out(record, [applied("socket", "remove",
                                       paths=(Path("/u/moxi-demo-relay.socket"),))])
    assert [e.unit for e in record] == [SERVICE, "moxi-demo-other.service"]
    _settle._take_out(record, [applied("daemon", "remove")])
    assert [e.unit for e in record] == ["moxi-demo-other.service"]


def test_a_start_the_supervisor_refuses_is_reported_and_recorded(w, capsys, monkeypatch):
    w.installed(DAEMONS=ONE)
    w.legacy()
    w.sup.fail[("start", SERVICE)] = "Unit is masked."
    code, out = settle(w, capsys, monkeypatch, setup="fail")
    assert code == 1
    assert (f"demo: {SERVICE}: `systemctl --user start -- {SERVICE}` failed" in out
            and f"start it by hand: systemctl --user start -- {SERVICE}" in out), out
    records = json.loads((moxi.paths.Layout("demo").state_dir() / "doctor.json")
                         .read_text())
    assert records[f"resume:{SERVICE}"]["fix"] == f"systemctl --user start -- {SERVICE}"
    assert records[f"resume:{SERVICE}"]["text"] == (
        f"demo: {SERVICE}: `systemctl --user start -- {SERVICE}` failed: Unit is masked.")
    assert w.calls("enable") == []


def test_a_start_that_raises_is_recorded_with_its_by_hand_command(w, capsys, monkeypatch):
    w.installed(DAEMONS=ONE)
    w.legacy()

    def runner(cmd, **kw):
        if cmd[2:] == ["start", "--", SERVICE]:
            raise RuntimeError("the runner broke")
        return w.sup(cmd, **kw)

    monkeypatch.setattr(w.backend, "runner", runner)
    code, out = settle(w, capsys, monkeypatch, setup="fail")
    assert code == 1
    assert (f"demo: {SERVICE}: RuntimeError: the runner broke; start it by hand: "
            f"systemctl --user start -- {SERVICE}") in out, out
    records = json.loads((moxi.paths.Layout("demo").state_dir() / "doctor.json")
                         .read_text())
    assert records[f"resume:{SERVICE}"] | {"at": None} == {
        "text": f"demo: {SERVICE}: RuntimeError: the runner broke",
        "fix": f"systemctl --user start -- {SERVICE}", "at": None}


def test_resume_records_clear_only_when_the_step_ends_clean(w, capsys, monkeypatch):
    w.installed(DAEMONS=ONE)
    doctor.record("demo", "old failure", key=f"resume:{SERVICE}", fix="x")
    doctor.record("demo", "other", key="other")
    path = moxi.paths.Layout("demo").state_dir() / "doctor.json"
    code, _ = settle(w, capsys, monkeypatch, setup="fail")
    assert code == 1 and f"resume:{SERVICE}" in json.loads(path.read_text())
    code, _ = settle(w, capsys, monkeypatch)
    assert code == 0
    assert set(json.loads(path.read_text())) == {"other"}


def resume_records():
    path = moxi.paths.Layout("demo").state_dir() / "doctor.json"
    return {k: v for k, v in json.loads(path.read_text()).items()
            if k.startswith("resume:")}


def test_a_resume_record_survives_a_clean_rerun_while_its_unit_is_down(
        w, capsys, monkeypatch):
    w.installed(DAEMONS=ONE)
    w.legacy()
    w.sup.fail[("start", SERVICE)] = "Unit is masked."
    code, _ = settle(w, capsys, monkeypatch)
    assert code == 1 and not w.active(SERVICE)
    fix = resume_records()[f"resume:{SERVICE}"]["fix"]
    # The re-run the failure line advises: nothing to move, the daemon planned keep.
    del w.sup.fail[("start", SERVICE)]
    code, _ = settle(w, capsys, monkeypatch)
    assert code == 0 and not w.active(SERVICE)
    assert resume_records()[f"resume:{SERVICE}"]["fix"] == fix
    # Started by hand: the next clean step clears it.
    w.sup.state(SERVICE)["active"] = True
    code, _ = settle(w, capsys, monkeypatch)
    assert code == 0 and resume_records() == {}


def test_a_failed_status_read_at_the_end_leaves_the_resume_record(w, capsys, monkeypatch):
    w.installed(DAEMONS=ONE)
    doctor.record("demo", "old failure", key=f"resume:{SERVICE}", fix="x")
    real = units.status

    def broken(*a, **k):
        raise RuntimeError("the supervisor did not answer")

    monkeypatch.setattr(units, "status", broken)
    code, _ = settle(w, capsys, monkeypatch)
    assert code == 0 and f"resume:{SERVICE}" in resume_records()
    monkeypatch.setattr(units, "status", real)
    code, _ = settle(w, capsys, monkeypatch)
    assert code == 0 and resume_records() == {}


def test_a_socket_s_resume_record_reads_the_socket_s_own_state(w, capsys, monkeypatch):
    w.installed(DAEMONS=SOCKET)
    sock = "moxi-demo-relay.socket"
    doctor.record("demo", "old failure", key=f"resume:{sock}", fix="x")
    w.sup.state(sock)["active"] = False
    code, _ = settle(w, capsys, monkeypatch)
    assert code == 0 and w.active(SERVICE)
    assert set(resume_records()) == {f"resume:{sock}"}
    w.sup.state(sock)["active"] = True
    code, _ = settle(w, capsys, monkeypatch)
    assert code == 0 and resume_records() == {}


@pytest.mark.parametrize("how", ["dropped", "switched off", "enabled false"])
def test_a_resume_record_for_a_unit_the_step_removed_or_disabled_clears(
        w, capsys, monkeypatch, tmp_path, how):
    tool = tmp_path / "tool"
    tool.write_text("")
    body = ("def units():\n    return {'daemons': {'aux': {'argv': [%r]%s}}}\n")
    w.installed(DAEMONS=ONE, units_from=body % (str(tool), ""))
    aux = "moxi-demo-aux.service"
    w.sup.state(aux)["active"] = False
    doctor.record("demo", "old failure", key=f"resume:{aux}", fix="x")
    if how == "dropped":
        w.module(DAEMONS=ONE)
    elif how == "switched off":
        w.module(DAEMONS=ONE,
                 units_from=body % (str(tool), ", 'switch': 'snapshot.enabled'"))
    else:
        w.module(DAEMONS=ONE, units_from=body % (str(tool), ", 'enabled': False"))
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert resume_records() == {}


# -- a switch key other units also use ---------------------------------------------------

SHARED = ("{'relay': {'command': 'moxi-demo-relay', 'doc': 'the relay', "
          "'switch': 'snapshot.enabled'}}")
TIMER_TOO = ("{'snapshot': {'command': 'moxi-demo-relay', 'every': '5m', "
             "'doc': 'take a snapshot', 'switch': 'snapshot.enabled'}, "
             "'prune': {'command': 'moxi-demo-relay', 'every': '1h', "
             "'doc': 'prune', 'switch': 'snapshot.enabled'}}")
TIMER_FILE = "moxi-demo-snapshot.timer"


def shared_running(w, **names):
    """`relay` installed and running under switch snapshot.enabled, which reads on."""
    w.installed(DAEMONS=SHARED, **names)
    config.set("demo", "snapshot.enabled", True, by="test")
    assert w.active(SERVICE)
    w.sup.calls.clear()


def test_a_new_timer_sharing_a_running_daemon_s_switch_leaves_both_and_is_held(
        w, capsys, monkeypatch):
    shared_running(w)
    w.module(DAEMONS=SHARED, TIMERS=TIMER)
    before = moxi.paths.Layout("demo").config_file().read_bytes()
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert moxi.paths.Layout("demo").config_file().read_bytes() == before
    assert w.active(SERVICE) and w.calls("stop", "disable") == []
    assert not (w.root / TIMER_FILE).exists()
    assert _state.recorded("demo", "snapshot") is None
    line = ("demo/snapshot: snapshot.enabled is also demo/relay's switch; set it with "
            "moxi config set demo snapshot.enabled; it is installed off at the next moxi "
            "install once snapshot.enabled is false")
    assert out.count(line) == 1, out
    assert "applies it and asks" not in out
    from moxi.doctor import _run

    row = {r.name: r for r in _run.run(["demo"])}["timer snapshot"]
    assert (row.state, row.detail) == ("warn", "not installed")
    assert row.fix == ("moxi config set demo snapshot.enabled false, then: moxi install "
                       "demo, then: moxi config set demo snapshot.enabled true")


def test_with_the_shared_key_off_the_timer_is_installed_off_and_the_key_left(
        w, capsys, monkeypatch):
    w.installed(DAEMONS=SHARED)
    w.module(DAEMONS=SHARED, TIMERS=TIMER)
    config_file = moxi.paths.Layout("demo").config_file()
    before = config_file.read_bytes() if config_file.exists() else None
    monkeypatch.setattr(_settle, "_ask", lambda q: pytest.fail("asked"))
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    after = config_file.read_bytes() if config_file.exists() else None
    assert after == before
    assert (w.root / TIMER_FILE).exists()
    assert _state.recorded("demo", "snapshot") is not None
    assert ("demo/snapshot: snapshot.enabled is also demo/relay's switch; set it with "
            "moxi config set demo snapshot.enabled\n") in out
    assert "installed off at the next" not in out


def test_a_changed_timer_sharing_the_key_is_held_while_it_reads_on(w, capsys, monkeypatch):
    shared_running(w, TIMERS=TIMER)
    assert (w.root / TIMER_FILE).exists()
    text = (w.root / TIMER_FILE).read_text()
    w.module(DAEMONS=SHARED, TIMERS=TIMER.replace("'5m'", "'10m'"))
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert (w.root / TIMER_FILE).read_text() == text
    assert w.active(SERVICE) and w.calls("stop", "disable") == []
    assert "is installed off at the next moxi install" in out


def test_two_timers_on_one_key_never_reset_each_other(w, capsys, monkeypatch):
    one = ("{'snapshot': {'command': 'moxi-demo-relay', 'every': '5m', "
           "'doc': 'take a snapshot', 'switch': 'snapshot.enabled'}}")
    w.installed(TIMERS=one)
    config.set("demo", "snapshot.enabled", True, by="test")
    w.module(TIMERS=TIMER_TOO)
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert config.load("demo", config.schema_for(moxi.modules.load("demo")))[
        "snapshot.enabled"] is True
    assert not (w.root / "moxi-demo-prune.timer").exists()
    assert "demo/prune: snapshot.enabled is also demo/snapshot's switch" in out


def test_setup_json_holds_the_version_and_a_new_version_restarts(w, capsys, monkeypatch):
    w.installed(DAEMONS=ONE)
    code, _ = settle(w, capsys, monkeypatch)
    assert code == 0 and w.calls("restart") == []
    w.module(DAEMONS=ONE, version="0.2.0")
    w.sup.calls.clear()
    code, _ = settle(w, capsys, monkeypatch)
    assert code == 0 and w.calls("restart") == [("restart", SERVICE)]
    assert _settle.setup_version("demo") == "0.2.0"
    code, _ = settle(w, capsys, monkeypatch, setup="fail")
    assert _settle.setup_version("demo") == "0.2.0"


@pytest.mark.parametrize("setup", ["fail", "exit"])
def test_a_failed_setup_leaves_setup_json_alone(w, capsys, monkeypatch, setup):
    w.installed(DAEMONS=ONE)
    before = _settle.setup_path().read_bytes()
    w.module(DAEMONS=ONE, version="0.2.0")
    code, out = settle(w, capsys, monkeypatch, setup=setup)
    assert code == 1 and "setup demo: failed" in out
    assert _settle.setup_path().read_bytes() == before
    assert _settle.setup_version("demo") == "0.1.0"


def test_a_module_without_setup_still_settles_and_is_recorded(w, capsys, monkeypatch):
    w.module(DAEMONS=ONE, SETUP="None")
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert "setup demo" not in out
    assert _settle.setup_version("demo") == "0.1.0"


# -- the timer switches ------------------------------------------------------------------------

def test_a_new_switched_timer_is_off_with_nobody_at_the_keyboard(w, capsys, monkeypatch):
    w.module(TIMERS=TIMER)
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert ("demo/snapshot is off; turn it on with: moxi config set demo "
            "snapshot.enabled true") in out
    assert "false" in moxi.paths.Layout("demo").config_file().read_text()
    assert _state.recorded("demo", "snapshot") is not None
    assert (w.root / "moxi-demo-snapshot.timer").exists()


def test_a_new_switched_timer_is_asked_at_a_terminal(w, capsys, monkeypatch):
    w.module(TIMERS=TIMER)
    asked = []

    class Tty:
        def isatty(self):
            return True
    monkeypatch.setattr(sys, "stdin", Tty())
    monkeypatch.setattr("builtins.input", lambda q: asked.append(q) or "y")
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert asked == ["demo/snapshot: take a snapshot, every 5 minutes. Turn it on? [y/N] "]
    assert "true" in (moxi.paths.Layout("demo").config_file().read_text())


def test_a_changed_timer_is_switched_off_again_and_an_unchanged_one_left(w, capsys,
                                                                       monkeypatch):
    w.module(TIMERS=TIMER)
    settle(w, capsys, monkeypatch)
    config.set("demo", "snapshot.enabled", True, by="test")
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0 and "snapshot" not in out
    w.module(TIMERS=TIMER.replace("5m", "10m"))
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert ("demo/snapshot changed (its schedule or command), so it is off again: moxi "
            "config set demo snapshot.enabled true") in out
    assert "false" in moxi.paths.Layout("demo").config_file().read_text()


# -- signals in this process -----------------------------------------------------------------

def test_a_signal_during_setup_abandons_it_and_the_finally_starts_the_record(
        w, capsys, monkeypatch):
    w.installed(DAEMONS=ONE)
    w.legacy()
    _settle.install_handlers()
    code, out = settle(w, capsys, monkeypatch, setup="signal")
    assert code == _settle.INTERRUPTED
    assert "setup demo: interrupted" in out
    assert "demo: interrupted; started again 1 of the 1 units the move stopped" in out
    assert w.calls("stop", "start") == [("stop", SERVICE), ("start", SERVICE)]
    assert _settle.setup_version("demo") == "0.1.0"   # the old entry; SETUP didn't finish


def test_a_setup_that_catches_interrupted_still_stops_when_it_returns(w, capsys, monkeypatch):
    w.installed(DAEMONS=ONE)
    _settle.install_handlers()
    code, out = settle(w, capsys, monkeypatch, setup="catch")
    assert code == _settle.INTERRUPTED
    assert "setup demo: done" not in out


def test_a_signal_inside_a_held_span_lets_the_move_finish(w, capsys, monkeypatch):
    w.installed(DAEMONS=TWO)
    old = w.legacy()
    _settle.install_handlers()

    def runner(cmd, **kw):
        if cmd[2:5] == ["stop", "--", SERVICE]:
            os.kill(os.getpid(), signal.SIGINT)
        return w.sup(cmd, **kw)
    monkeypatch.setattr(w.backend, "runner", runner)
    code, out = settle(w, capsys, monkeypatch)
    assert code == _settle.INTERRUPTED, out
    assert not old.exists()                                   # the rename finished
    assert [c[1] for c in w.calls("stop")] == [SERVICE, "moxi-demo-aux.service"]
    assert [c[1] for c in w.calls("start")] == [SERVICE, "moxi-demo-aux.service"]
    assert "setup demo" not in out                            # no step after the move


def test_a_signal_after_the_steps_flag_is_cleared_cannot_skip_the_restart(
        w, capsys, monkeypatch):
    w.installed(DAEMONS=ONE)
    w.legacy()
    _settle.install_handlers()
    monkeypatch.setattr(_settle, "_after_steps",
                        lambda: os.kill(os.getpid(), signal.SIGTERM))
    code, out = settle(w, capsys, monkeypatch)
    assert code == _settle.INTERRUPTED, out
    assert w.calls("start") == [("start", SERVICE)]


def test_signals_during_the_restart_only_set_the_request(w, capsys, monkeypatch):
    w.installed(DAEMONS=TWO)
    w.legacy()
    _settle.install_handlers()
    real = units.resume

    def resume(record, **kw):
        os.kill(os.getpid(), signal.SIGINT)
        os.kill(os.getpid(), signal.SIGINT)
        return real(record, **kw)
    monkeypatch.setattr(units, "resume", resume)
    code, out = settle(w, capsys, monkeypatch)
    assert code == _settle.INTERRUPTED
    assert len(w.calls("start")) == 2
    assert "started again 2 of the 2 units" in out


def test_a_signal_ignored_at_start_stays_ignored(monkeypatch):
    saved = {s: signal.getsignal(s) for s in _settle.signals()}
    try:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        _settle.install_handlers()
        assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
        assert signal.getsignal(signal.SIGTERM) == _settle._on_signal
    finally:
        for signum, handler in saved.items():
            signal.signal(signum, handler)


def test_say_drops_every_line_after_the_terminal_is_gone(monkeypatch):
    class Gone:
        writes = 0

        def write(self, text):
            Gone.writes += 1
            raise OSError(5, "Input/output error")

        def flush(self):
            pass
    monkeypatch.setattr(_settle, "_lines", _settle._Lines())
    monkeypatch.setattr(sys, "stdout", Gone())
    _settle.say("one")
    _settle.say("two")
    assert Gone.writes == 1
    guarded = _settle._Guarded(Gone())
    assert guarded.write("three") == 5


# -- the teardown --------------------------------------------------------------------------

def test_teardown_codes(w, capsys, monkeypatch):
    w.site.write("moxi_demo/down.py", textwrap.dedent("""
        import os
        from moxi.modules import Refused
        def run():
            how = os.environ.get("MOXITEST_DOWN")
            if how == "refuse":
                raise Refused("its store holds unsent mail")
            if how == "fail":
                raise RuntimeError("broken")
        """))
    w.module(TEARDOWN="'moxi_demo.down:run'")
    for how, code in (("ok", 0), ("refuse", 3), ("fail", 1)):
        monkeypatch.setenv("MOXITEST_DOWN", how)
        assert _settle.teardown("demo") == code
    assert "demo: its store holds unsent mail" in capsys.readouterr().err


# -- steps 4 to 6: skills, install questions, listeners ---------------------------------------

QSETTINGS = SETTINGS + """
[guard.push]
type = "bool"
default = false
doc = "Guard pushes."
read = "use"

[guard.force]
type = "bool"
default = false
doc = "Guard force pushes."
read = "use"

[guards]
type = "table"
items = "bool"
default = {}
doc = "Guards by name."
read = "use"

[count]
type = "int"
default = 0
doc = "A number."
read = "use"

[loud]
type = "bool"
default = true
doc = "On by default."
read = "use"

[envy]
type = "bool"
default = false
doc = "Read from the environment too."
read = "use"
env = "MOXI_DEMO_ENVY"
"""

LISTENER = """
import json, os

def _note(event, module, extra=None):
    with open(os.environ["MOXITEST_HEARD"], "a") as fh:
        fh.write(json.dumps({"who": WHO, "event": event, "module": module,
                             **(extra or {})}) + "\\n")

def on_install(module, found):
    from moxi import _questions
    how = os.environ.get("MOXITEST_LISTEN_" + WHO, "ok")
    seen = {"found": found.name, "questions": sorted(_questions.recorded(module))}
    _note("install", module, seen)
    if how == "fail":
        raise RuntimeError("no route")
    if how == "exit":
        raise SystemExit(2)
    if how == "signal":
        import signal
        os.kill(os.getpid(), signal.SIGTERM)
    if how == "catch":
        import signal
        from moxi._settle import Interrupted
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except Interrupted:
            _note("caught", module)

def on_uninstall(module):
    _note("uninstall", module)
"""


def qmodule(w, questions: dict, **names):
    """Module `demo` declaring `questions`, with switch-shaped keys and some that aren't."""
    w.module(QUESTIONS=repr(questions), **names)
    w.site.write("moxi_demo/settings.toml", QSETTINGS)


def listen(w, *, own=True, other=False):
    """`moxi.installs` listeners: module demo's own, and one of another module, ears."""
    if own:
        w.site.write("moxi_demo/installs.py", "WHO = 'demo'\n" + LISTENER)
        info = next(w.site.root.glob("moxi_demo-*.dist-info"))
        (info / "entry_points.txt").write_text(
            "[moxi.modules]\ndemo = moxi_demo.module\n"
            "[moxi.installs]\ndemo = moxi_demo.installs\n")
    if other:
        w.site.write("moxi_ears/__init__.py", "")
        w.site.write("moxi_ears/installs.py", "WHO = 'ears'\n" + LISTENER)
        w.site.write("moxi_ears-0.1.0.dist-info/METADATA",
                     "Metadata-Version: 2.1\nName: moxi-ears\nVersion: 0.1.0\n")
        w.site.write("moxi_ears-0.1.0.dist-info/entry_points.txt",
                     "[moxi.installs]\nears = moxi_ears.installs\n")
    moxi.modules.reset()


def heard(w) -> list[dict]:
    path = w.tmp / "heard.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture
def ear(w, monkeypatch):
    monkeypatch.setenv("MOXITEST_HEARD", str(w.tmp / "heard.jsonl"))
    return w


class Tty:
    def isatty(self):
        return True


def at_terminal(monkeypatch, *answers):
    """Standard input a terminal, answering each question in turn; returns the questions
    asked."""
    asked: list[str] = []
    left = list(answers)
    monkeypatch.setattr(sys, "stdin", Tty())
    monkeypatch.setattr("builtins.input", lambda q: asked.append(q) or left.pop(0))
    return asked


def config_text() -> str:
    path = moxi.paths.Layout("demo").config_file()
    return path.read_text() if path.exists() else ""


def value(key: str):
    """Key `key` of module demo as `moxi.config` reads it, and whether the file sets it."""
    settings = config.load("demo", config.schema_for(moxi.modules.load("demo")))
    return settings[key], settings.source(key)


def recorded_questions() -> dict:
    from moxi import _questions

    return _questions.read()


def test_a_question_is_asked_once_with_default_no(w, capsys, monkeypatch):
    qmodule(w, {"guard.push": "Guard pushes to main"})
    asked = at_terminal(monkeypatch, "")
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert asked == ["demo: Guard pushes to main? [y/N] "]
    assert value("guard.push") == (False, "file")
    assert ("demo: Guard pushes to main is off; turn it on with: moxi config set demo "
            "guard.push true") in out
    assert set(recorded_questions()["demo"]) == {"guard.push"}
    assert config.history("demo", "guard.push")[-1]["by"] == "moxi install"
    asked.clear()
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0 and asked == [] and "guard.push" not in out


def test_a_yes_sets_the_key_true(w, capsys, monkeypatch):
    qmodule(w, {"guard.push": "Guard pushes to main"})
    at_terminal(monkeypatch, "y")
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert value("guard.push") == (True, "file")
    assert "is off" not in out
    assert set(recorded_questions()["demo"]) == {"guard.push"}


def test_nobody_at_the_keyboard_sets_the_key_off_with_its_line(w, capsys, monkeypatch):
    qmodule(w, {"guard.push": "Guard pushes to main"})
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert value("guard.push") == (False, "file")
    assert ("demo: Guard pushes to main is off; turn it on with: moxi config set demo "
            "guard.push true") in out
    assert set(recorded_questions()["demo"]) == {"guard.push"}


def test_an_upgrade_asks_only_the_key_it_adds_and_never_resets_one(w, capsys, monkeypatch):
    qmodule(w, {"guard.push": "Guard pushes to main"})
    at_terminal(monkeypatch, "n")
    settle(w, capsys, monkeypatch)
    config.set("demo", "guard.push", True, by="test")        # the user turns it on later
    qmodule(w, {"guard.push": "Guard pushes to main",
                "guard.force": "Guard force pushes"}, version="0.2.0")
    asked = at_terminal(monkeypatch, "y")
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert asked == ["demo: Guard force pushes? [y/N] "]
    assert value("guard.push") == (True, "file")
    assert value("guard.force") == (True, "file")
    assert set(recorded_questions()["demo"]) == {"guard.push", "guard.force"}


def test_a_recorded_key_the_user_unsets_by_hand_is_not_asked_again(w, capsys, monkeypatch):
    """The record alone stops the question: the file no longer sets the key."""
    qmodule(w, {"guard.push": "Guard pushes to main"})
    at_terminal(monkeypatch, "y")
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert set(recorded_questions()["demo"]) == {"guard.push"}
    config.unset("demo", "guard.push", by="test")             # the user unsets it later
    assert value("guard.push")[1] != "file"
    before = config_text()
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked again"))
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert "guard.push" not in out and config_text() == before
    assert set(recorded_questions()["demo"]) == {"guard.push"}


def test_a_key_the_config_file_already_sets_is_recorded_without_a_question_or_write(
        w, capsys, monkeypatch):
    qmodule(w, {"guard.push": "Guard pushes to main"})
    config.set("demo", "guard.push", True, by="test")
    before = config_text()
    lines = len(config.history("demo"))
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert config_text() == before and len(config.history("demo")) == lines
    assert "guard.push" not in out
    assert set(recorded_questions()["demo"]) == {"guard.push"}


def test_a_crash_between_the_setting_and_its_record_keeps_the_answer_written(
        w, capsys, monkeypatch):
    """The setting is written first: an install cut short before the record leaves the
    answer in the file, and the next install records it without asking again."""
    from moxi import _questions

    qmodule(w, {"guard.push": "Guard pushes to main"})
    at_terminal(monkeypatch, "y")
    real = _questions.record
    monkeypatch.setattr(_questions, "record",
                        lambda m, k: (_ for _ in ()).throw(OSError("disk full")))
    code, out = settle(w, capsys, monkeypatch)
    assert code == 1 and "disk full" in out
    assert value("guard.push") == (True, "file") and recorded_questions() == {}
    monkeypatch.setattr(_questions, "record", real)
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert value("guard.push") == (True, "file")
    assert set(recorded_questions()["demo"]) == {"guard.push"}


@pytest.mark.parametrize("key, why", [
    ("count", "switch count must be a bool setting read at use, with no env and default "
              "false"),
    ("loud", "switch loud must be a bool setting read at use"),
    ("envy", "switch envy must be a bool setting read at use"),
    ("nothere", "switch nothere is not a setting the module declares"),
])
def test_a_key_that_is_not_switch_shaped_is_not_asked_and_exits_1(
        w, capsys, monkeypatch, key, why):
    qmodule(w, {key: "A question", "guard.push": "Guard pushes to main"})
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    code, out = settle(w, capsys, monkeypatch)
    assert code == 1
    assert f"demo: install question {key} not asked: {why}" in out
    assert key not in recorded_questions().get("demo", {})
    # The good key is still asked (off, with nobody at the keyboard) and recorded.
    assert set(recorded_questions()["demo"]) == {"guard.push"}


def test_a_switch_table_entry_is_asked_as_a_plain_key_is(w, capsys, monkeypatch):
    qmodule(w, {"guards.push": "Guard pushes to main"})
    asked = at_terminal(monkeypatch, "yes")
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert asked == ["demo: Guard pushes to main? [y/N] "]
    settings = config.load("demo", config.schema_for(moxi.modules.load("demo")))
    assert settings["guards.push"] is True
    assert set(recorded_questions()["demo"]) == {"guards.push"}


def test_a_switch_table_entry_already_in_the_file_is_recorded_without_a_question(
        w, capsys, monkeypatch):
    qmodule(w, {"guards.push": "Guard pushes to main"})
    config.set("demo", "guards.push", False, by="test")
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    code, out = settle(w, capsys, monkeypatch)
    assert code == 0, out
    assert set(recorded_questions()["demo"]) == {"guards.push"}


def test_ctrl_c_at_a_question_writes_neither_setting_nor_record(w, capsys, monkeypatch):
    qmodule(w, {"guard.push": "Guard pushes to main"})
    _settle.install_handlers()

    def ctrl_c(question):
        os.kill(os.getpid(), signal.SIGINT)
        return "y"
    monkeypatch.setattr(sys, "stdin", Tty())
    monkeypatch.setattr("builtins.input", ctrl_c)
    code, out = settle(w, capsys, monkeypatch)
    assert code == _settle.INTERRUPTED
    assert "demo: interrupted; no later step ran" in out
    assert value("guard.push") == (False, "default") and recorded_questions() == {}


def test_a_question_s_setting_and_record_are_one_held_span(w, capsys, monkeypatch):
    """A signal while the answer is written lets the record follow it, then stops."""
    from moxi import _questions

    qmodule(w, {"guard.push": "Guard pushes to main",
                "guard.force": "Guard force pushes"})
    _settle.install_handlers()
    real = config.set

    def set_then_signal(*a, **k):
        done = real(*a, **k)
        os.kill(os.getpid(), signal.SIGTERM)
        return done
    monkeypatch.setattr(config, "set", set_then_signal)
    code, out = settle(w, capsys, monkeypatch)
    assert code == _settle.INTERRUPTED
    assert set(_questions.recorded("demo")) == {"guard.push"}   # the second never asked
    assert value("guard.force") == (False, "default")


# -- the listeners ----------------------------------------------------------------------------

def test_every_listener_hears_the_install_last_its_own_module_s_included(
        ear, capsys, monkeypatch):
    qmodule(ear, {"guard.push": "Guard pushes to main"}, DAEMONS=ONE)
    config.set("demo", "relay.enabled", True, by="test")
    listen(ear, other=True)
    code, out = settle(ear, capsys, monkeypatch)
    assert code == 0, out
    events = [e for e in heard(ear) if e["event"] == "install"]
    assert [(e["who"], e["module"]) for e in events] == [("demo", "demo"), ("ears", "demo")]
    # Each ran after the units and the questions: the unit file is written and the
    # question recorded by the time it was called.
    assert all(e["questions"] == ["guard.push"] and e["found"] == "demo" for e in events)
    assert (ear.root / SERVICE).exists()


def test_a_failing_listener_is_printed_the_rest_run_and_the_step_exits_1(
        ear, capsys, monkeypatch):
    ear.module()
    listen(ear, other=True)
    for how in ("fail", "exit"):
        monkeypatch.setenv("MOXITEST_LISTEN_demo", how)
        code, out = settle(ear, capsys, monkeypatch)
        assert code == 1, out
        why = "RuntimeError: no route" if how == "fail" else "SystemExit: 2"
        assert f"demo: the moxi.installs listener demo failed: {why}" in out
        assert [e["who"] for e in heard(ear) if e["event"] == "install"][-1] == "ears"


def test_a_listener_without_on_install_or_that_cannot_load_fails_the_step(
        ear, capsys, monkeypatch):
    ear.module()
    listen(ear, other=True)
    ear.site.write("moxi_ears/installs.py", "def on_uninstall(module):\n    pass\n")
    code, out = settle(ear, capsys, monkeypatch)
    assert code == 1
    assert "the moxi.installs listener ears failed: AttributeError" in out
    ear.site.write("moxi_ears/installs.py", "import no_such_module_here\n")
    drop_modules({"moxi_ears"})
    code, out = settle(ear, capsys, monkeypatch)
    assert code == 1
    assert "the moxi.installs listener ears failed: loading moxi_ears.installs" in out


@pytest.mark.parametrize("how", ["signal", "catch"])
def test_a_signal_in_a_listener_stops_the_step_before_the_next(ear, capsys, monkeypatch,
                                                               how):
    ear.module()
    listen(ear, other=True)
    monkeypatch.setenv("MOXITEST_LISTEN_demo", how)
    _settle.install_handlers()
    code, out = settle(ear, capsys, monkeypatch)
    assert code == _settle.INTERRUPTED
    assert "demo: interrupted; no later step ran" in out
    assert [e["who"] for e in heard(ear)] == (["demo"] if how == "signal"
                                              else ["demo", "demo"])


@pytest.mark.parametrize("path", ["setup", "refusal", "raised"])
def test_after_a_failed_setup_a_refused_move_or_an_exception_no_later_step_runs(
        ear, capsys, monkeypatch, path):
    qmodule(ear, {"guard.push": "Guard pushes to main"}, DAEMONS=ONE)
    listen(ear)
    if path == "refusal":
        ear.legacy()
        (ear.home / "state" / "demo").mkdir()
        (ear.home / "state" / "demo" / "x").write_text("x")
    if path == "raised":
        monkeypatch.setattr(units, "plan", lambda *a, **k: (_ for _ in ()).throw(
            KeyError("boom")))
    code, out = settle(ear, capsys, monkeypatch, setup="fail" if path == "setup" else "ok")
    assert code == 1, out
    assert heard(ear) == [] and recorded_questions() == {}


@pytest.mark.parametrize("path", ["unit", "link"])
def test_a_refused_unit_or_a_relative_legacy_link_still_runs_the_later_steps(
        ear, capsys, monkeypatch, path):
    if path == "link" and sys.platform == "win32":
        pytest.skip("symbolic links need a privilege on Windows")
    if path == "unit":
        (moxi.paths._scripts() / "moxi-demo-relay").unlink()   # the plan refuses it
        qmodule(ear, {"guard.push": "Guard pushes to main"}, DAEMONS=ONE)
    else:
        ear.installed(DAEMONS=ONE)
        linked_legacy(ear, relative=True)
        qmodule(ear, {"guard.push": "Guard pushes to main"}, DAEMONS=ONE)
    listen(ear)
    code, out = settle(ear, capsys, monkeypatch)
    assert code == 1, out
    assert [e["who"] for e in heard(ear)] == ["demo"]
    assert set(recorded_questions()["demo"]) == {"guard.push"}


# -- the skills question and its flags --------------------------------------------------------

def test_the_skills_answer_is_passed_to_the_child_and_parsed_there():
    assert _settle.child_argv("py", "comms", skills=True)[-1] == "--skills"
    assert _settle.child_argv("py", "comms", replace=True, skills=False)[-2:] == [
        "--replace", "--no-skills"]
    assert _settle.child_argv("py", "comms")[-1] == "comms"
    assert _settle._parse(["comms", "--skills"]).skills is True
    assert _settle._parse(["comms", "--no-skills"]).skills is False
    assert _settle._parse(["comms"]).skills is None
    with pytest.raises(SystemExit):
        _settle._parse(["comms", "--skills", "--no-skills"])


def _step(skills=None):
    return _settle.Settle("comms", None, skills=skills)


def test_the_skills_question_is_answered_ahead_by_a_flag(capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    monkeypatch.setattr(sys, "stdin", Tty())
    assert _settle.skills_wanted(_step(True), ["moxi-comms-send"], ["claude"]) is True
    assert _settle.skills_wanted(_step(False), ["moxi-comms-send"], ["claude"]) is False
    assert capsys.readouterr().out == ""


def test_the_skills_question_at_a_terminal_and_with_nobody_there(capsys, monkeypatch):
    asked = at_terminal(monkeypatch, "y", "")
    skills = ["moxi-comms-send", "moxi-comms-read"]
    assert _settle.skills_wanted(_step(), skills, ["claude", "codex"]) is True
    assert _settle.skills_wanted(_step(), ["moxi-comms-send"],
                                 ["claude", "codex", "pi"]) is False
    assert asked == [
        "comms ships 2 skills (moxi-comms-send, moxi-comms-read). Place them for claude "
        "and codex? [y/N] ",
        "comms ships 1 skill (moxi-comms-send). Place them for claude, codex and pi? "
        "[y/N] "]
    line = "comms: skills not placed; place them with: moxi install comms --skills"
    assert capsys.readouterr().out.count(line) == 1
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    assert _settle.skills_wanted(_step(), skills, ["claude"]) is False
    assert line in capsys.readouterr().out


# -- step 4: the skills, placed ------------------------------------------------------------------

@pytest.fixture
def sk(w, monkeypatch):
    """`w` with a home of its own, so every harness folder is under tmp, and helpers for
    module demo's `SKILLS`."""
    from moxi import place

    user = w.tmp / "user"
    user.mkdir()
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("USERPROFILE", str(user))  # what `~` reads on Windows
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    w.user = user
    w.place = place
    w.pkg = w.site.root / "moxi_demo"

    def skills(*names, **more):
        """Module demo shipping `names` in `skills/`, and nothing else there."""
        folder = w.pkg / "skills"
        if folder.exists():
            import shutil

            shutil.rmtree(folder)
        for name in names:
            w.site.write(f"moxi_demo/skills/{name}/SKILL.md", f"# {name}\n")
        w.module(SKILLS="'skills'", **more)

    def claude(name):
        return user / ".claude" / "skills" / name

    def codex(name):
        return user / ".agents" / "skills" / name

    w.skills, w.claude, w.codex = skills, claude, codex
    return w


def place_settle(w, capsys, *, skills=None, replace=False):
    code = _settle.settle("demo", replace=replace, skills=skills)
    out, err = capsys.readouterr()
    return code, out + err


def owned(w, owner="demo") -> dict:
    ledger = w.place._read_ledger()
    return ledger.get(owner, {})


def test_skills_are_not_placed_with_nobody_at_the_keyboard(sk, capsys, monkeypatch):
    sk.skills("moxi-demo-send")
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    code, out = place_settle(sk, capsys)
    assert code == 0, out
    assert "demo: skills not placed; place them with: moxi install demo --skills" in out
    assert not os.path.lexists(sk.claude("moxi-demo-send")) and sk.place.status("demo") == []


def test_skills_flag_places_each_skill_for_each_harness_as_links(sk, capsys):
    sk.skills("moxi-demo-send", "moxi-demo-read")
    code, out = place_settle(sk, capsys, skills=True)
    assert code == 0, out
    src = sk.pkg / "skills"
    for name in ("moxi-demo-read", "moxi-demo-send"):
        assert os.readlink(sk.claude(name)) == str(src / name)
        assert os.readlink(sk.codex(name)) == str(src / name)
        assert f"demo: placed skill {name} for claude ({sk.claude(name)})" in out
    assert len(sk.place.status("demo")) == 4


def test_a_yes_at_the_terminal_places_them_and_a_rerun_asks_nothing(sk, capsys, monkeypatch):
    sk.skills("moxi-demo-send")
    asked = at_terminal(monkeypatch, "y")
    code, out = place_settle(sk, capsys)
    assert code == 0, out
    assert asked == ["demo ships 1 skill (moxi-demo-send). Place them for claude and codex? "
                     "[y/N] "]
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    code, out = place_settle(sk, capsys, skills=False)
    assert code == 0, out
    assert "placed" not in out and "not placed" not in out
    assert [p.state for p in sk.place.status("demo")] == ["ok", "ok"]


def test_an_upgrade_adds_and_removes_skills_as_the_package_says_without_asking(
        sk, capsys, monkeypatch):
    sk.skills("moxi-demo-send", "moxi-demo-old")
    place_settle(sk, capsys, skills=True)
    sk.skills("moxi-demo-send", "moxi-demo-new")
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    code, out = place_settle(sk, capsys)
    assert code == 0, out
    assert not os.path.lexists(sk.claude("moxi-demo-old"))
    assert os.path.islink(sk.claude("moxi-demo-new"))
    assert os.path.islink(sk.claude("moxi-demo-send"))
    assert (f"demo: removed skill moxi-demo-old for claude "
            f"({sk.claude('moxi-demo-old')})") in out
    # SKILLS dropped altogether: every placement of the package's goes.
    sk.module()
    code, out = place_settle(sk, capsys)
    assert code == 0, out
    assert sk.place.status("demo") == []
    assert not os.path.lexists(sk.claude("moxi-demo-send"))


def test_with_none_placed_an_upgrade_asks_again(sk, capsys, monkeypatch):
    sk.skills("moxi-demo-send")
    place_settle(sk, capsys)                             # nobody there: not placed
    asked = at_terminal(monkeypatch, "n")
    code, out = place_settle(sk, capsys)
    assert code == 0 and len(asked) == 1
    assert sk.place.status("demo") == []


def test_a_skill_handed_over_is_skipped_and_counts_as_placed(sk, capsys, monkeypatch):
    sk.skills("moxi-demo-send")
    place_settle(sk, capsys, skills=True)
    sk.place.adopt("demo", "loadout", sk.place.status("demo"), say=lambda line: None)
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    code, out = place_settle(sk, capsys)
    assert code == 0, out
    assert "demo: moxi-demo-send was handed to loadout; skipped" in out
    assert sk.place.status("demo") == []
    assert len(sk.place.status("loadout")) == 2
    assert os.path.islink(sk.claude("moxi-demo-send"))


def test_a_skill_handed_to_an_owner_since_removed_is_placed_again(sk, capsys, monkeypatch):
    sk.skills("moxi-demo-send")
    place_settle(sk, capsys, skills=True)
    sk.place.adopt("demo", "loadout", sk.place.status("demo"), say=lambda line: None)
    sk.place.remove("loadout", say=lambda line: None)
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    code, out = place_settle(sk, capsys)
    assert code == 0, out
    assert (f"demo: placed skill moxi-demo-send for claude "
            f"({sk.claude('moxi-demo-send')})") in out
    assert [p.state for p in sk.place.status("demo")] == ["ok", "ok"]
    assert not owned(sk)["handed"]


def test_a_skill_handed_on_is_skipped_until_no_owner_holds_it(sk, capsys, monkeypatch):
    sk.skills("moxi-demo-send")
    place_settle(sk, capsys, skills=True)
    sk.place.adopt("demo", "loadout", sk.place.status("demo"), say=lambda line: None)
    sk.place.adopt("loadout", "tidy", sk.place.status("loadout"), say=lambda line: None)
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    code, out = place_settle(sk, capsys)
    assert code == 0, out
    assert "demo: moxi-demo-send was handed to loadout; skipped" in out
    assert "not placed" not in out and sk.place.status("demo") == []
    sk.place.remove("tidy", say=lambda line: None)
    code, out = place_settle(sk, capsys)
    assert code == 0, out
    assert [p.state for p in sk.place.status("demo")] == ["ok", "ok"]
    assert not owned(sk)["handed"]


def seed(w, name, *harnesses):
    """What another owner does to place a skill of demo's that the user chose there: place
    it as demo, then take it over, seeded."""
    place = w.place
    items = [place.Item(w.pkg / "skills" / name, h, name) for h in harnesses]
    steps = place.plan("demo", items)[:len(items)]
    done = place.apply(steps, say=lambda line: None)
    place.adopt("demo", "loadout", done, seeded=True, say=lambda line: None)


def test_with_only_seeded_hand_overs_the_question_is_asked_for_the_rest(sk, capsys,
                                                                         monkeypatch):
    sk.skills("moxi-demo-send", "moxi-demo-read")
    seed(sk, "moxi-demo-send", "claude", "codex")
    asked = at_terminal(monkeypatch, "y")
    code, out = place_settle(sk, capsys)
    assert code == 0, out
    assert asked == ["demo ships 1 skill (moxi-demo-read). Place them for claude and codex? "
                     "[y/N] "]
    assert "demo: moxi-demo-send was handed to loadout; skipped" in out
    assert sorted((p.name, p.harness) for p in sk.place.status("demo")) == [
        ("moxi-demo-read", "claude"), ("moxi-demo-read", "codex")]
    assert len(sk.place.status("loadout")) == 2
    assert all(e.get("seeded") for e in owned(sk)["handed"].values())


def test_with_only_seeded_hand_overs_nobody_there_places_nothing(sk, capsys, monkeypatch):
    """As on a clean machine: nobody at the keyboard, nothing placed and the line; an
    upgrade asks again, and the flags answer ahead."""
    sk.skills("moxi-demo-send", "moxi-demo-read")
    seed(sk, "moxi-demo-send", "claude", "codex")
    handed = owned(sk)["handed"]
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    for _ in range(2):
        code, out = place_settle(sk, capsys)
        assert code == 0, out
        assert "demo: skills not placed; place them with: moxi install demo --skills" in out
        assert sk.place.status("demo") == [] and owned(sk)["handed"] == handed
    code, out = place_settle(sk, capsys, skills=False)
    assert code == 0 and sk.place.status("demo") == []
    asked = at_terminal(monkeypatch, "n")
    code, out = place_settle(sk, capsys)
    assert code == 0 and len(asked) == 1 and sk.place.status("demo") == []
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    code, out = place_settle(sk, capsys, skills=True)
    assert code == 0, out
    assert sorted(p.name for p in sk.place.status("demo")) == ["moxi-demo-read"] * 2
    assert owned(sk)["handed"] == handed


def test_a_seeded_hand_over_for_one_harness_leaves_the_skill_in_the_question(
        sk, capsys, monkeypatch):
    sk.skills("moxi-demo-send")
    seed(sk, "moxi-demo-send", "claude")
    asked = at_terminal(monkeypatch, "y")
    code, out = place_settle(sk, capsys)
    assert code == 0, out
    assert asked == ["demo ships 1 skill (moxi-demo-send). Place them for claude and codex? "
                     "[y/N] "]
    assert [p.harness for p in sk.place.status("demo")] == ["codex"]


def test_with_only_seeded_hand_overs_of_every_skill_nothing_is_asked(sk, capsys,
                                                                      monkeypatch):
    sk.skills("moxi-demo-send")
    seed(sk, "moxi-demo-send", "claude", "codex")
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    code, out = place_settle(sk, capsys)
    assert code == 0, out
    assert "skills not placed" not in out and sk.place.status("demo") == []


def test_an_unseeded_hand_over_beside_a_seeded_one_counts_as_placed(sk, capsys,
                                                                     monkeypatch):
    sk.skills("moxi-demo-send", "moxi-demo-read")
    seed(sk, "moxi-demo-send", "claude", "codex")
    place = sk.place
    items = [place.Item(sk.pkg / "skills" / "moxi-demo-read", h, "moxi-demo-read")
             for h in ("claude", "codex")]
    done = place.apply(place.plan("demo", items)[:2], say=lambda line: None)
    place.adopt("demo", "loadout", done, say=lambda line: None)
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    code, out = place_settle(sk, capsys)
    assert code == 0, out
    assert out.count("was handed to loadout; skipped") == 4
    assert "skills not placed" not in out and sk.place.status("demo") == []


def test_a_lapsed_seeded_hand_over_is_named_and_counted_in_the_question(sk, capsys,
                                                                       monkeypatch):
    """The taker removed it, so the hand-over has lapsed: the skill plans `add` and is
    asked about, never placed as the module's own without a yes."""
    sk.skills("moxi-demo-send")
    seed(sk, "moxi-demo-send", "claude", "codex")
    sk.place.remove("loadout", say=lambda line: None)
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    code, out = place_settle(sk, capsys)
    assert code == 0, out
    assert "demo: skills not placed; place them with: moxi install demo --skills" in out
    assert not os.path.lexists(sk.claude("moxi-demo-send"))
    asked = at_terminal(monkeypatch, "y")
    code, out = place_settle(sk, capsys)
    assert code == 0, out
    assert asked == ["demo ships 1 skill (moxi-demo-send). Place them for claude and codex? "
                     "[y/N] "]
    assert [p.state for p in sk.place.status("demo")] == ["ok", "ok"]
    assert not owned(sk)["handed"]


def test_a_broken_harness_row_keeps_its_placements_and_says_why(sk, capsys, monkeypatch):
    """A name in `skills.harnesses` whose row is skipped, or that has no row at all,
    still reaches the placer, which refuses it and leaves what it placed there. The
    install says the line and goes on: the doctor warns about the row."""
    from moxi.doctor import _run

    sk.skills("moxi-demo-send")
    place_settle(sk, capsys, skills=True)
    claude = sk.claude("moxi-demo-send")
    before = owned(sk)["placed"]
    assert str(claude) in before
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    config.set("moxi", "harnesses", [{"name": "claude", "skills": "relative/skills"},
                                     {"name": "codex"}], by="test")
    code, out = place_settle(sk, capsys)
    assert code == 0, out
    assert ("demo: moxi-demo-send for claude not placed: harness claude has no row in "
            "harnesses (some rows are skipped: moxi config moxi shows the table)") in out
    assert "removed skill" not in out
    assert os.path.islink(claude) and owned(sk)["placed"] == before
    assert [(r.name, r.state) for r in _run._harness_rows()] == [
        ("harnesses", "warn"), ("skills.harnesses claude", "warn")]

    config.unset("moxi", "harnesses", by="test")
    config.set("moxi", "skills.harnesses", ["claude", "codex", "cluade"], by="test")
    code, out = place_settle(sk, capsys)
    assert code == 0, out
    assert ("demo: moxi-demo-send for cluade not placed: harness cluade has no row in "
            "harnesses") in out
    assert "removed skill" not in out and owned(sk)["placed"] == before
    assert [r.name for r in _run._harness_rows()] == ["skills.harnesses cluade"]


def test_the_skills_question_names_only_harnesses_with_a_working_row(sk, capsys,
                                                                       monkeypatch):
    sk.skills("moxi-demo-send")
    config.set("moxi", "skills.harnesses", ["claude", "cluade", "codex"], by="test")
    asked = at_terminal(monkeypatch, "y")
    code, out = place_settle(sk, capsys)
    assert code == 0, out
    assert asked == ["demo ships 1 skill (moxi-demo-send). Place them for claude and codex? "
                     "[y/N] "]
    assert "moxi-demo-send for cluade not placed: harness cluade has no row" in out


def test_a_name_taken_out_of_skills_harnesses_removes_its_placements(sk, capsys,
                                                                     monkeypatch):
    sk.skills("moxi-demo-send")
    place_settle(sk, capsys, skills=True)
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    config.set("moxi", "skills.harnesses", ["codex"], by="test")
    code, out = place_settle(sk, capsys)
    assert code == 0, out
    assert (f"demo: removed skill moxi-demo-send for claude ({sk.claude('moxi-demo-send')})"
            in out)
    assert not os.path.lexists(sk.claude("moxi-demo-send"))
    assert [p.harness for p in sk.place.status("demo")] == ["codex"]
    # Its only placements on a broken row still count as its own: no question.
    config.set("moxi", "harnesses", [{"name": "codex", "skills": "relative"}], by="test")
    code, out = place_settle(sk, capsys)
    assert code == 0 and "skills not placed; place them with" not in out
    assert [p.harness for p in sk.place.status("demo")] == ["codex"]


def test_a_reinstall_keeps_placements_the_module_did_not_make_from_its_package(
        sk, capsys, monkeypatch, tmp_path):
    """A module that also places for a user (a project manager, say) owns placements
    whose source is not in its package: one another module handed it, and a project's
    own skill. Its re-install manages only its own and leaves those exactly as they are,
    even after its own SKILLS go."""
    place = sk.place
    other = tmp_path / "comms-pkg" / "comms-send"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("# comms\n")
    place.apply(place.plan("comms", [place.Item(other, "claude", "comms-send")]),
                say=lambda line: None)
    place.adopt("comms", "demo", place.status("comms"), say=lambda line: None)
    # A project folder named like the module's package is still not its package.
    project = tmp_path / "proj" / "moxi_demo"
    held = project / ".claude" / "skills" / "proj-skill"
    held.mkdir(parents=True)
    (held / "SKILL.md").write_text("# project\n")
    dest = project / ".agents" / "skills" / "proj-skill"
    item = place.Item(held, "codex", "proj-skill", dest=dest, held=True, root=project)
    place.apply(place.plan("demo", [place.Item(other, "claude", "comms-send"), item]),
                say=lambda line: None)
    keep = {str(sk.claude("comms-send")), str(dest)}
    assert {str(p.path) for p in place.status("demo")} == keep
    before = dict(owned(sk)["placed"])

    sk.skills("moxi-demo-send")
    asked = at_terminal(monkeypatch, "y")
    code, out = place_settle(sk, capsys)
    assert code == 0, out
    assert len(asked) == 1                    # none of its own yet: asked
    assert {str(p.path) for p in place.status("demo")} == keep | {
        str(sk.claude("moxi-demo-send")), str(sk.codex("moxi-demo-send"))}
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    for _ in range(2):                        # a re-run, then SKILLS dropped
        code, out = place_settle(sk, capsys)
        assert code == 0, out
        assert "removed skill comms-send" not in out and "proj-skill" not in out
        assert {k: owned(sk)["placed"][k] for k in keep} == before
        assert os.readlink(sk.claude("comms-send")) == str(other)
        assert os.readlink(dest) == os.path.relpath(held, dest.parent)
        sk.module()
    assert {str(p.path) for p in place.status("demo")} == keep


def test_an_occupant_is_refused_and_exits_1_and_replace_moves_it_aside(sk, capsys):
    sk.skills("moxi-demo-send")
    mine = sk.claude("moxi-demo-send")
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("# mine\n")
    code, out = place_settle(sk, capsys, skills=True)
    assert code == 1
    assert "demo: moxi-demo-send for claude not placed:" in out
    assert (mine / "SKILL.md").read_text() == "# mine\n"
    assert os.path.islink(sk.codex("moxi-demo-send"))
    code, out = place_settle(sk, capsys, replace=True)
    assert code == 0, out
    assert os.path.islink(mine) and f"demo: moved {mine} to " in out
    displaced = moxi.paths.core().data_dir() / "displaced"
    assert [p.read_text() for p in displaced.rglob("SKILL.md")] == ["# mine\n"]


def test_an_occupant_git_tracks_is_refused_and_replace_moves_it_aside(sk, capsys,
                                                                     monkeypatch):
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("no git")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(sk.tmp / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    sk.skills("moxi-demo-send")
    mine = sk.claude("moxi-demo-send")
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("# mine\n")
    repo = sk.user / ".claude"
    for args in (["init", "-q", str(repo)], ["-C", str(repo), "add", "."]):
        subprocess.run(["git", *args], check=True, capture_output=True)
    code, out = place_settle(sk, capsys, skills=True)
    assert code == 1
    assert (f"demo: moxi-demo-send for claude not placed: {mine} is a folder the placer "
            f"did not mark, and git tracks it") in out
    code, out = place_settle(sk, capsys, replace=True)
    assert code == 0, out
    assert os.path.islink(mine) and f"demo: moved {mine} to " in out
    assert f"demo: placed skill moxi-demo-send for claude ({mine})" in out


def test_a_skills_folder_not_on_disk_or_a_broken_ledger_fails_the_step(sk, capsys,
                                                                       monkeypatch):
    import importlib.resources

    sk.skills("moxi-demo-send")
    real = importlib.resources.files

    class Zipped:
        def joinpath(self, *parts):
            return self

    monkeypatch.setattr(importlib.resources, "files",
                        lambda pkg: Zipped() if pkg == "moxi_demo" else real(pkg))
    code, out = place_settle(sk, capsys, skills=True)
    assert code == 1
    assert ("demo: skills not placed: package moxi_demo is not a folder on disk, so its "
            "skills can't be placed") in out
    monkeypatch.setattr(importlib.resources, "files", real)
    ledger = moxi.paths.core().state_dir() / sk.place.LEDGER
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("{not json")
    code, out = place_settle(sk, capsys, skills=True)
    assert code == 1 and "demo: skills not placed:" in out
    assert not os.path.lexists(sk.claude("moxi-demo-send"))


RULE = ("'b' is not a Moxi skill name: a skill Moxi ships is named moxi-<name>, lower-case "
        "ASCII letters and digits in words joined by single hyphens, 64 characters at most")


def test_a_skill_not_named_moxi_name_is_not_placed_and_the_step_exits_1(sk, capsys,
                                                                       monkeypatch):
    sk.skills("moxi-a", "b")
    asked = at_terminal(monkeypatch, "y")
    code, out = place_settle(sk, capsys)
    assert code == 1, out
    assert asked == ["demo ships 1 skill (moxi-a). Place them for claude and codex? [y/N] "]
    assert out.count(f"demo: skill b not placed: {RULE}") == 1
    assert os.path.islink(sk.claude("moxi-a")) and os.path.islink(sk.codex("moxi-a"))
    assert not os.path.lexists(sk.claude("b")) and not os.path.lexists(sk.codex("b"))
    assert sorted(p.name for p in sk.place.status("demo")) == ["moxi-a", "moxi-a"]
    # Answered ahead, or with nothing else to place, the same.
    code, out = place_settle(sk, capsys, skills=True)
    assert code == 1 and f"demo: skill b not placed: {RULE}" in out
    sk.skills("b")
    code, out = place_settle(sk, capsys, skills=True)
    assert code == 1 and f"demo: skill b not placed: {RULE}" in out
    assert sk.place.status("demo") == []


def test_a_misnamed_skill_an_earlier_install_placed_is_removed(sk, capsys, monkeypatch):
    sk.skills("moxi-a", "b")
    src = sk.pkg / "skills" / "b"
    place = sk.place
    place.apply(place.plan("demo", [place.Item(src, h, "b") for h in ("claude", "codex")]),
                say=lambda line: None)
    assert os.path.islink(sk.claude("b"))
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    code, out = place_settle(sk, capsys)
    assert code == 1, out
    assert f"demo: skill b not placed: {RULE}" in out
    assert f"demo: removed skill b for claude ({sk.claude('b')})" in out
    assert not os.path.lexists(sk.claude("b")) and not os.path.lexists(sk.codex("b"))
    assert sorted(p.name for p in place.status("demo")) == ["moxi-a", "moxi-a"]


def test_the_step_counts_folders_only_and_reads_no_skill_md(sk, capsys):
    """Dot-named folders, `__pycache__` and plain files in `SKILLS` are not skills, and a
    skill whose `SKILL.md` names it otherwise, or that has none, is still placed: the
    contract check holds the file before release."""
    sk.skills("moxi-a")
    folder = sk.pkg / "skills"
    for name in (".hidden", "__pycache__"):
        (folder / name).mkdir()
    (folder / "README.md").write_text("not a skill\n")
    (folder / "moxi-b").mkdir()
    (folder / "moxi-a" / "SKILL.md").write_text("---\nname: other\n---\n")
    code, out = place_settle(sk, capsys, skills=True)
    assert code == 0, out
    assert sorted({p.name for p in sk.place.status("demo")}) == ["moxi-a", "moxi-b"]


def test_the_placer_s_apply_is_a_held_span(sk, capsys, monkeypatch):
    """A signal while the placer applies only sets the request: every placement is made
    and recorded, then the step stops before the questions and listeners."""
    sk.skills("moxi-demo-send")
    real = sk.place.apply

    def apply(steps, **k):
        os.kill(os.getpid(), signal.SIGTERM)
        return real(steps, **k)
    monkeypatch.setattr(sk.place, "apply", apply)
    monkeypatch.setattr(_settle, "_ask_questions", lambda step: pytest.fail("asked"))
    _settle.install_handlers()
    code, out = place_settle(sk, capsys, skills=True)
    assert code == _settle.INTERRUPTED, out
    assert "demo: interrupted; no later step ran" in out
    assert [p.state for p in sk.place.status("demo")] == ["ok", "ok"]
