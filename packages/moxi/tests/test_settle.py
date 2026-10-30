"""The home move and the settle step in this process, on the fake backend and its simulated
supervisor: each path out of the step starts exactly the units the move stopped, and only
those."""

from __future__ import annotations

import json
import os
import signal
import sys
import textwrap
import types
from pathlib import Path

import pytest

import moxi.paths
from moxi import _settle, config, doctor, files, home, units
from moxi.units import _backend, _declare, _state
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
