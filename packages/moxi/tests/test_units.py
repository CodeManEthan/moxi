"""The writer's core: declarations, plan, apply, remove and the apply after a daemon switch,
on the fake backend and its simulated supervisor."""

from __future__ import annotations

import json
import os
import textwrap
import time
import types

import pytest

import moxi.paths
from moxi import config, units
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

[loud]
type = "bool"
default = true
doc = "Not a switch: its default is true."
read = "use"

[port]
type = "int"
default = 8470
doc = "The shell's port."
read = "start"

[name]
type = "string"
default = "x"
doc = "Not an int."
read = "start"

[rows]
type = "table"
items = "bool"
default = {}
doc = "One switch per row."
read = "use"
"""


@pytest.fixture
def w(site, tmp_path, monkeypatch):
    """A fake module `demo` with its settings, a scripts directory, and the fake backend."""
    scripts = tmp_path / "bin"
    scripts.mkdir()
    monkeypatch.setattr(moxi.paths, "_scripts", lambda: scripts)
    root = tmp_path / "units"
    root.mkdir()
    supervisor = FakeSupervisor(root)
    backend = FakeBackend(root, runner=supervisor)
    lines: list[str] = []

    def script(name):
        path = scripts / name
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
        return path

    def module(name="demo", *, units_from=None, settings=SETTINGS, **names):
        pkg = f"moxi_{name}"
        if settings is not None:
            site.write(f"{pkg}/settings.toml", settings)
            names.setdefault("SETTINGS", "'settings.toml'")
        if units_from is not None:
            site.write(f"{pkg}/runtime.py", textwrap.dedent(units_from))
            names["UNITS_FROM"] = f"'{pkg}.runtime:units'"
        site.module(name, **names)
        units_mod = __import__("moxi.modules", fromlist=["reset"])
        units_mod.reset()

    def plan(name="demo", **kw):
        return {(c.name, c.kind): c for c in units.plan(name, **kw)}

    def apply(changes, **kw):
        return units.apply(changes, say=lines.append, **kw)

    with _backend.use(backend):
        yield types.SimpleNamespace(site=site, scripts=scripts, root=root, sup=supervisor,
                                    backend=backend, script=script, module=module,
                                    plan=plan, apply=apply, lines=lines, tmp=tmp_path)


DAEMON = "{'relay': {'command': 'moxi-demo-relay', 'doc': 'the relay'}}"


# -- declarations ------------------------------------------------------------------------------

def test_declarations_merge_units_from_and_reject_bad_entries(w, tmp_path):
    tool = tmp_path / "tool"
    tool.write_text("")
    w.module(DAEMONS=DAEMON, HEALTH="{'relay': 'x.y:z', 'extra': 'x.y:z', 'typo': 'x.y:z'}",
             units_from=f"""
             def units():
                 return {{"daemons": {{"extra": {{"argv": [{str(tool)!r}, "-v"], "enabled": False}},
                                      "relay": {{"command": "moxi-demo-other"}},
                                      "rel": {{"argv": ["relative"]}}}},
                         "timers": {{"nightly": {{"argv": [{str(tool)!r}], "at": ["03:00"]}}}}}}
             """)
    decl = units.declarations("demo")
    assert set(decl.daemons) == {"relay", "extra"}
    assert decl.daemons["extra"]["enabled"] is False
    assert set(decl.timers) == {"nightly"}
    assert set(decl.rejected) == {("daemon", "relay"), ("daemon", "rel"), ("health", "typo")}
    assert "announcement declares" in decl.rejected[("daemon", "relay")]
    assert "absolute path" in decl.rejected[("daemon", "rel")]


def test_a_run_time_unit_sharing_a_socket_unit_name_is_rejected(w):
    """A daemon's socket for its k-th address is a unit named for `<daemon>-<k>`, so a
    run-time unit of that name is kept out: the run-time one of the pair, or the
    `<daemon>-<k>` one when both are run-time."""
    w.module(DAEMONS="{'shell': {'command': 'moxi-demo-shell', 'socket': {'setting': "
                     "'port', 'addresses': ['127.0.0.1', '::1']}}, "
                     "'web-1': {'command': 'moxi-demo-web'}}",
             units_from="""
             two = {"setting": "port", "addresses": ["127.0.0.1", "::1"]}
             def units():
                 return {"daemons": {"shell-1": {"command": "moxi-demo-x"},
                                     "shell-2": {"command": "moxi-demo-x", "socket": two},
                                     "web": {"command": "moxi-demo-x", "socket": two},
                                     "api": {"command": "moxi-demo-x", "socket": two}},
                         "timers": {"api-1": {"command": "moxi-demo-x", "every": "5m"},
                                    "shell-3": {"command": "moxi-demo-x", "every": "5m"}}}
             """)
    decl = units.declarations("demo")
    assert set(decl.daemons) == {"shell", "web-1", "shell-2", "api"}
    assert set(decl.timers) == {"shell-3"}
    assert set(decl.rejected) == {("daemon", "shell-1"), ("daemon", "web"),
                                  ("timer", "api-1")}
    assert "declares daemon 'shell-1' beside daemon 'shell'" in \
        decl.rejected[("daemon", "shell-1")]
    assert "declares daemon 'web-1' beside daemon 'web'" in decl.rejected[("daemon", "web")]
    assert "declares timer 'api-1' beside daemon 'api'" in decl.rejected[("timer", "api-1")]
    assert all(r.startswith("UNITS_FROM (moxi_demo.runtime:units) ")
               for r in decl.rejected.values())
    # plan refuses each one, cause declaration
    w.script("moxi-demo-shell")
    plan = w.plan()
    assert plan[("shell-1", "daemon")].action == "refuse"
    assert plan[("shell-1", "daemon")].cause == "declaration"
    assert plan[("api-1", "timer")].cause == "declaration"


@pytest.mark.parametrize("body, text", [
    ("raise RuntimeError('no rows file')", "RuntimeError: no rows file"),
    ("return [1]", "returned list"),
    ("return {'daemon': {}}", "the keys are daemons and timers"),
    ("return {'daemons': []}", "returned daemons as list"),
])
def test_a_failing_units_from_raises_declaration_error(w, body, text):
    w.module(units_from=f"def units():\n    {body}\n")
    with pytest.raises(units.DeclarationError) as caught:
        units.declarations("demo")
    assert text in caught.value.text and caught.value.module == "demo"


def test_an_overrunning_units_from_is_a_declaration_error(w, monkeypatch):
    monkeypatch.setattr(_declare, "UNITS_FROM_SECONDS", 0.2)
    w.module(units_from="import time\ndef units():\n    time.sleep(3)\n    return {}\n")
    started = time.monotonic()
    with pytest.raises(units.DeclarationError, match="did not finish in 0.2 s"):
        units.declarations("demo")
    assert time.monotonic() - started < 2


def test_a_health_key_of_a_units_from_module_is_checked_against_the_merged_daemons(w):
    w.module(HEALTH="{'late': 'x.y:z'}", units_from="""
             def units():
                 return {"daemons": {"late": {"command": "moxi-demo-late"}}}
             """)
    assert units.declarations("demo").rejected == {}


# -- plan --------------------------------------------------------------------------------------

def test_a_new_daemon_plans_add_and_keep_once_applied(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=DAEMON)
    change = w.plan()[("relay", "daemon")]
    assert (change.action, change.reason) == ("add", "not installed")
    assert change.text.startswith("# moxi unit: demo/relay\n")
    [done] = w.apply([change])
    assert done.ok
    assert ("enable", "--now", "moxi-demo-relay.service") in w.sup.calls
    assert w.sup.units["moxi-demo-relay.service"] == {"active": True, "enabled": True}
    assert w.plan()[("relay", "daemon")].action == "keep"


def test_a_changed_daemon_is_enabled_and_restarted(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=DAEMON)
    w.apply(units.plan("demo"))
    (w.root / "moxi-demo-relay.service").write_text("# moxi unit: demo/relay\nold\n")
    change = w.plan()[("relay", "daemon")]
    assert change.action == "change" and change.enabled_before is True
    w.sup.calls.clear()
    w.apply([change])
    assert w.sup.calls == [("daemon-reload",), ("enable", "moxi-demo-relay.service"),
                           ("restart", "moxi-demo-relay.service")]


def test_upgrade_keep_rewrites_without_a_restart(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', 'upgrade': 'keep'}}")
    w.apply(units.plan("demo"))
    (w.root / "moxi-demo-relay.service").write_text("# moxi unit: demo/relay\nold\n")
    w.sup.calls.clear()
    [done] = w.apply(units.plan("demo"))
    assert ("restart", "moxi-demo-relay.service") not in w.sup.calls
    assert done.lines == ("moxi-demo-relay.service rewritten; the new code runs from its "
                          "next start",)


def test_restart_after_an_upgrade_restarts_running_unchanged_daemons(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=DAEMON)
    w.apply(units.plan("demo"))
    w.sup.calls.clear()
    w.apply(units.plan("demo"), restart=True)
    assert ("restart", "moxi-demo-relay.service") in w.sup.calls


def test_an_unmarked_file_is_refused_and_moved_aside_only_with_replace(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=DAEMON)
    old = w.root / "moxi-demo-relay.service"
    old.write_text("[Service]\nExecStart=/old\n")
    change = w.plan()[("relay", "daemon")]
    assert (change.action, change.cause, change.foreign) == ("refuse", "foreign", old)
    assert "--replace" in change.reason
    [done] = w.apply([change])
    assert not done.ok and old.read_text() == "[Service]\nExecStart=/old\n"
    [done] = w.apply([change], replace=True)
    assert done.ok and done.change.action == "change"
    assert old.read_text().startswith("# moxi unit: demo/relay")
    [aside] = (moxi.paths.core().data_dir() / "displaced").glob("*/moxi-demo-relay.service")
    assert aside.read_text() == "[Service]\nExecStart=/old\n"


def test_the_move_aside_crosses_file_systems(w, monkeypatch):
    import errno

    w.script("moxi-demo-relay")
    w.module(DAEMONS=DAEMON)
    old = w.root / "moxi-demo-relay.service"
    old.write_text("[Service]\nExecStart=/old\n")
    change = w.plan()[("relay", "daemon")]
    renames = {"rename": os.rename, "replace": os.replace}

    def across(name):
        def call(src, dst, *a, **k):
            if os.fspath(src) == os.fspath(old):
                raise OSError(errno.EXDEV, "Invalid cross-device link", src)
            return renames[name](src, dst, *a, **k)
        return call

    monkeypatch.setattr(os, "rename", across("rename"))
    monkeypatch.setattr(os, "replace", across("replace"))
    [done] = w.apply([change], replace=True)
    assert done.ok and old.read_text().startswith("# moxi unit: demo/relay")
    [aside] = (moxi.paths.core().data_dir() / "displaced").glob("*/moxi-demo-relay.service")
    assert aside.read_text() == "[Service]\nExecStart=/old\n"


def test_a_file_marked_for_another_unit_is_foreign(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=DAEMON)
    (w.root / "moxi-demo-relay.service").write_text("# moxi unit: other/relay\n")
    change = w.plan()[("relay", "daemon")]
    assert change.cause == "foreign" and "marked for other/relay" in change.reason


def test_an_undeclared_marked_unit_is_removed(w):
    w.module(DAEMONS="{}")
    gone = w.root / "moxi-demo-old.service"
    gone.write_text("# moxi unit: demo/old\n")
    w.sup.reload()
    change = w.plan()[("old", "daemon")]
    assert (change.action, change.reason) == ("remove", "no longer declared")
    [done] = w.apply([change])
    assert done.ok and not gone.exists()
    assert w.sup.calls[0] == ("disable", "--now", "moxi-demo-old.service")


@pytest.mark.parametrize("entry, cause, words", [
    ("{'command': 'moxi-demo-none'}", "command", "no moxi-demo-none in this environment"),
    ("{'command': 'moxi-demo-relay', 'switch': 'loud'}", "switch", "default false"),
    ("{'command': 'moxi-demo-relay', 'switch': 'nope'}", "switch", "not a setting"),
    ("{'command': 'moxi-demo-relay', 'switch': 'port.x'}", "switch", "not a switch table"),
    ("{'command': 'moxi-demo-relay', 'socket': {'setting': 'name', 'addresses': ['127.0.0.1']}}",
     "socket", "must be an int setting"),
])
def test_a_unit_that_cannot_be_written_is_refused_with_its_cause(w, entry, cause, words):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=f"{{'relay': {entry}}}")
    change = w.plan()[("relay", "daemon")]
    assert (change.action, change.cause) == ("refuse", cause)
    assert words in change.reason


def test_a_run_time_argv_that_does_not_exist_is_refused(w, tmp_path):
    w.module(units_from=f"""
             def units():
                 return {{"daemons": {{"x": {{"argv": [{str(tmp_path / 'gone')!r}]}}}}}}
             """)
    change = w.plan()[("x", "daemon")]
    assert (change.action, change.cause) == ("refuse", "command")


def test_an_editable_module_refuses_every_unit(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=DAEMON)
    w.site.distribution("moxi-demo", entry_points={"demo": "moxi_demo.module"},
                        direct_url={"url": "file:///src", "dir_info": {"editable": True}})
    (w.root / "moxi-demo-old.service").write_text("# moxi unit: demo/old\n")
    plan = w.plan()
    assert {(k, c.action, c.cause) for k, c in plan.items()} == {
        (("relay", "daemon"), "refuse", "editable"), (("old", "daemon"), "refuse", "editable")}
    assert "moxi install demo" in plan[("relay", "daemon")].reason
    assert all(a.ok for a in w.apply(plan.values()))
    assert w.sup.calls == []


def test_no_backend_skips_every_unit(w, monkeypatch):
    w.module(DAEMONS=DAEMON)
    monkeypatch.setattr(_backend, "implementation", lambda: None)
    [change] = units.plan("demo")
    assert (change.action, change.cause) == ("skip", "nobackend")
    assert change.reason == "no supervisor on this system (no systemd user manager)"


def test_an_off_platform_unit_is_skipped(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', 'platforms': ['win32']}}")
    change = w.plan()[("relay", "daemon")]
    assert (change.action, change.cause) == ("skip", "platform")


def test_a_daemon_switch_off_skips_or_removes_and_on_installs(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', 'switch': 'relay.enabled'}}")
    change = w.plan()[("relay", "daemon")]
    assert (change.action, change.cause) == ("skip", "switch")
    assert change.reason == ("demo/relay is off; turn it on with: moxi config set demo "
                             "relay.enabled true")
    applied = config.set("demo", "relay.enabled", True, by="test")
    assert [(a.change.name, a.change.action, a.ok) for a in applied] == [
        ("relay", "add", True)]
    assert w.sup.units["moxi-demo-relay.service"]["active"]
    applied = config.unset("demo", "relay.enabled", by="test")
    assert [(a.change.action, a.change.cause) for a in applied] == [("remove", "switch")]
    assert not (w.root / "moxi-demo-relay.service").exists()


def test_a_switch_table_entry_switches_a_daemon(w):
    w.script("moxi-demo-relay")
    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', 'switch': 'rows.relay'}}")
    assert w.plan()[("relay", "daemon")].action == "skip"
    applied = config.set("demo", "rows.relay", True, by="test")
    assert [a.change.action for a in applied] == ["add"]
    applied = config.set("demo", "rows", {"relay": False}, by="test")
    assert [a.change.action for a in applied] == ["remove"]


def test_config_set_of_a_daemon_switch_plans_only_that_daemon(w, capsys):
    for name in ("moxi-demo-relay", "moxi-demo-other"):
        w.script(name)
    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', 'switch': 'relay.enabled'},"
                     " 'other': {'command': 'moxi-demo-other'}}")
    applied = config.set("demo", "relay.enabled", True, by="test")
    assert [a.change.name for a in applied] == ["relay"]
    assert "demo: applying units for relay.enabled" in capsys.readouterr().out
    assert not (w.root / "moxi-demo-other.service").exists()
    assert config.set("demo", "snapshot.enabled", True, by="test") == []


def test_config_set_plans_and_applies_a_daemon_switch_under_one_hold(w, monkeypatch):
    import contextlib

    from moxi import files

    events: list[str] = []
    real_lock, real_plan, real_apply = files.lock, units.plan, units.apply

    @contextlib.contextmanager
    def lock(path, **kw):
        with real_lock(path, **kw):
            events.append("in")
            try:
                yield
            finally:
                events.append("out")

    def plan(*a, **k):
        events.append("plan")
        return real_plan(*a, **k)

    def apply(*a, **k):
        events.append("apply")
        return real_apply(*a, **k)

    monkeypatch.setattr(files, "lock", lock)
    monkeypatch.setattr(units, "plan", plan)
    monkeypatch.setattr(units, "apply", apply)
    w.script("moxi-demo-relay")
    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', 'switch': 'relay.enabled'}}")
    applied = config.set("demo", "relay.enabled", True, by="test")
    assert [a.change.action for a in applied] == ["add"]
    depth, hold, held_at = 0, 0, {}
    for event in events:
        if event == "in":
            depth += 1
            hold += depth == 1
        elif event == "out":
            depth -= 1
        elif depth:
            held_at[event] = hold
    assert held_at.keys() == {"plan", "apply"}, events
    assert held_at["plan"] == held_at["apply"], events


def test_config_set_with_a_failing_units_from_says_the_key_was_not_checked(w, capsys):
    w.module(units_from="def units():\n    raise OSError('rows missing')\n")
    assert config.set("demo", "relay.enabled", True, by="test") == []
    out = capsys.readouterr().out
    assert ("demo: UNITS_FROM failed (OSError: rows missing), so relay.enabled was not "
            "checked as a daemon switch") in out


def test_the_config_verb_exits_1_when_the_units_fail(w, capsys):
    from moxi import cli

    w.script("moxi-demo-relay")
    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay', 'switch': 'relay.enabled'}}")
    w.sup.fail[("enable", "moxi-demo-relay.service")] = "Access denied."
    assert cli.main(["config", "set", "demo", "relay.enabled", "true"]) == 1
    out = capsys.readouterr().out
    assert "Access denied." in out and "retries the units" in out
    assert config.load("demo", config.schema_for(__import__("moxi.modules").modules.load(
        "demo")))["relay.enabled"] is True


def test_a_socket_takes_its_port_from_the_setting_and_a_new_port_is_a_change(w):
    w.script("moxi-demo-shell")
    w.module(DAEMONS="{'shell': {'command': 'moxi-demo-shell', "
                     "'socket': {'setting': 'port', 'addresses': ['127.0.0.1']}}}")
    plan = w.plan()
    assert "listen = 127.0.0.1:8470" in plan[("shell", "socket")].text
    w.apply(plan.values())
    assert w.sup.calls.index(("enable", "--now", "moxi-demo-shell.socket")) < \
        w.sup.calls.index(("start", "moxi-demo-shell.service"))
    config.set("demo", "port", 8471, by="test")
    plan = w.plan()
    assert plan[("shell", "socket")].action == "change"
    assert plan[("shell", "daemon")].action == "keep"


def test_dropping_a_socket_removes_the_marked_socket(w):
    w.script("moxi-demo-shell")
    w.module(DAEMONS="{'shell': {'command': 'moxi-demo-shell', "
                     "'socket': {'setting': 'port', 'addresses': ['127.0.0.1']}}}")
    w.apply(units.plan("demo"))
    w.site.write("moxi_demo/module.py",
                 "SETTINGS = 'settings.toml'\nDAEMONS = {'shell': {'command': "
                 "'moxi-demo-shell', 'args': ['--plain']}}\n")
    __import__("sys").modules.pop("moxi_demo.module", None)
    __import__("moxi.modules").modules.reset()
    plan = w.plan()
    assert plan[("shell", "socket")].action == "remove"
    assert plan[("shell", "daemon")].action == "change"


def test_a_failing_units_from_leaves_its_units_and_plans_the_rest(w, tmp_path):
    w.script("moxi-demo-relay")
    w.module(DAEMONS=DAEMON, units_from="def units():\n    raise OSError('down')\n")
    (w.root / "moxi-demo-row.service").write_text("# moxi unit: demo/row\n")
    plan = w.plan()
    assert plan[("relay", "daemon")].action == "add"
    row = plan[("row", "daemon")]
    assert (row.action, row.cause) == ("skip", "declaration")
    assert row.reason == "UNITS_FROM failed: OSError: down; left as they are"


def test_a_rejected_run_time_daemon_is_refused_and_its_unit_kept(w):
    w.module(units_from="def units():\n    return {'daemons': {'row': {'argv': ['rel']}}}\n")
    (w.root / "moxi-demo-row.service").write_text("# moxi unit: demo/row\n")
    [change] = units.plan("demo")
    assert (change.action, change.cause) == ("refuse", "declaration")


def test_only_plans_the_named_units_and_removes_nothing_else(w):
    for name in ("moxi-demo-relay", "moxi-demo-other"):
        w.script(name)
    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay'}, "
                     "'other': {'command': 'moxi-demo-other'}}")
    (w.root / "moxi-demo-old.service").write_text("# moxi unit: demo/old\n")
    assert set(w.plan(only=["relay"])) == {("relay", "daemon")}
    assert ("old", "daemon") in w.plan()


def test_a_run_time_unit_declared_disabled_is_written_and_never_started(w, tmp_path):
    tool = tmp_path / "tool"
    tool.write_text("")
    source = """
             ENABLED = {flag}
             def units():
                 return {{"daemons": {{"row": {{"argv": [{tool!r}], "enabled": ENABLED}}}}}}
             """
    w.module(units_from=source.format(flag=False, tool=str(tool)))
    w.apply(units.plan("demo"))
    assert (w.root / "moxi-demo-row.service").exists()
    assert not any(c[0] == "enable" for c in w.sup.calls)
    w.site.write("moxi_demo/runtime.py",
                 textwrap.dedent(source.format(flag=True, tool=str(tool))))
    __import__("sys").modules.pop("moxi_demo.runtime", None)
    [change] = units.plan("demo")
    assert change.action == "change" and change.enabled_before is False
    w.apply([change])
    assert ("enable", "--now", "moxi-demo-row.service") in w.sup.calls
    w.site.write("moxi_demo/runtime.py",
                 textwrap.dedent(source.format(flag=False, tool=str(tool))))
    __import__("sys").modules.pop("moxi_demo.runtime", None)
    w.sup.calls.clear()
    w.apply(units.plan("demo"))
    assert ("disable", "--now", "moxi-demo-row.service") in w.sup.calls
    assert not w.sup.units["moxi-demo-row.service"]["active"]


# -- the hold on a changed switched timer --------------------------------------------------------

TIMER = ("{'snapshot': {'command': 'moxi-demo-snap', 'every': '5m', "
         "'switch': 'snapshot.enabled', 'doc': 'take a snapshot'}}")


def test_a_switched_timer_waits_for_the_settle_step(w):
    w.script("moxi-demo-snap")
    w.module(TIMERS=TIMER)
    change = w.plan()[("snapshot", "timer")]
    assert change.action == "add" and change.digest == units.digest(
        {"command": "moxi-demo-snap", "every": "5m", "switch": "snapshot.enabled"})
    [done] = w.apply([change])
    assert done.ok and done.lines == ("demo/snapshot is new; `moxi install demo` applies it "
                                      "and asks about its switch",)
    assert not change.paths[0].exists() and w.sup.calls == []
    _state.record("demo", "snapshot", change.digest)
    [done] = w.apply([change])
    assert done.ok and all(p.exists() for p in change.paths)
    assert ("enable", "--now", "moxi-demo-snapshot.timer") in w.sup.calls
    assert "--if demo.snapshot.enabled --record demo/snapshot --" in change.texts[0]


def test_a_changed_switched_timer_is_held_and_left_as_it_is(w):
    w.script("moxi-demo-snap")
    w.module(TIMERS=TIMER)
    [change] = units.plan("demo")
    _state.record("demo", "snapshot", change.digest)
    w.apply([change])
    before = change.paths[1].read_text()
    w.site.write("moxi_demo/module.py", "SETTINGS = 'settings.toml'\nTIMERS = "
                 + TIMER.replace("5m", "10m") + "\n")
    __import__("sys").modules.pop("moxi_demo.module", None)
    __import__("moxi.modules").modules.reset()
    [change] = units.plan("demo")
    assert change.action == "change"
    [done] = w.apply([change])
    assert done.lines == ("demo/snapshot changed; `moxi install demo` applies it and asks "
                          "about its switch",)
    assert change.paths[1].read_text() == before


def test_a_switchless_run_time_timer_is_written_by_any_apply(w, tmp_path):
    tool = tmp_path / "tool"
    tool.write_text("")
    w.module(units_from=f"""
             def units():
                 return {{"timers": {{"nightly": {{"argv": [{str(tool)!r}], "at": ["03:00"],
                                                   "days": ["sat", "mon"]}}}}}}
             """)
    [change] = units.plan("demo")
    assert change.digest is None
    [done] = w.apply([change])
    assert done.ok and "at = 03:00 on mon,sat" in change.texts[1]
    assert "--if" not in change.texts[0]


def test_a_run_time_timer_digest_counts_argv_cwd_and_enabled():
    base = {"argv": ["/a"], "every": "5m", "switch": "s"}
    seen = {units.digest(base)}
    for extra in ({"argv": ["/b"]}, {"cwd": "/w"}, {"enabled": False}):
        seen.add(units.digest({**base, **extra}))
    assert len(seen) == 4
    assert units.digest({"command": "c", "args": ()}) == units.digest(
        {"args": [], "command": "c"})


# -- failures and remove ---------------------------------------------------------------------

def test_a_failed_step_prints_the_command_and_a_remedy_and_goes_on(w):
    for name in ("moxi-demo-relay", "moxi-demo-other"):
        w.script(name)
    w.module(DAEMONS="{'relay': {'command': 'moxi-demo-relay'}, "
                     "'other': {'command': 'moxi-demo-other'}}")
    w.sup.fail[("enable", "moxi-demo-other.service")] = "Failed to enable unit: denied"
    done = {a.change.name: a for a in w.apply(units.plan("demo"))}
    assert done["relay"].ok and not done["other"].ok
    assert done["other"].lines == (
        "`systemctl --user enable --now -- moxi-demo-other.service` failed: Failed to enable "
        "unit: denied",
        "the unit file is written; fix the cause and run `moxi install demo` again")


def test_remove_takes_every_marked_unit_and_wrap_and_forgets_the_digests(w):
    w.script("moxi-demo-relay")
    w.script("moxi-demo-snap")
    w.module(DAEMONS=DAEMON, TIMERS=TIMER)
    [_, timer] = units.plan("demo")
    _state.record("demo", "snapshot", timer.digest)
    _state.record("other", "x", "d")
    w.apply(units.plan("demo"))
    w.sup.extra |= {"backup.timer", "backup-run.service"}
    w.sup.triggers["backup.timer"] = "backup-run.service"
    w.sup.reload()
    fence = units._apply.fence(w.backend)
    assert w.backend.wrap("backup.timer", module="demo", key="rows.backup", name="backup",
                          fence=fence).ok
    applied = units.remove("demo", say=w.lines.append)
    assert {(a.change.name, a.change.kind, a.ok) for a in applied} == {
        ("relay", "daemon", True), ("snapshot", "timer", True),
        ("backup.timer", "wrap", True)}
    assert list(w.root.glob("moxi-*")) == []
    assert list(w.root.glob("*.d/*.conf")) == []
    assert set(_state.read()) == {"other/x"}


def test_apply_and_remove_take_the_core_lock(w, monkeypatch):
    from moxi import files

    taken = []
    real = files.lock

    def lock(path, **kw):
        taken.append(path)
        return real(path, **kw)

    monkeypatch.setattr(files, "lock", lock)
    w.module(DAEMONS="{}")
    units.apply([])
    units.remove("demo")
    assert taken == [moxi.paths.core_lock()] * 2


def test_units_json_entries_hold_digest_and_time(w):
    _state.record("demo", "snapshot", "abc")
    data = json.loads(_state.path().read_text())
    assert data["demo/snapshot"]["digest"] == "abc"
    assert data["demo/snapshot"]["at"].endswith("Z")
    assert _state.recorded("demo", "snapshot") == "abc"
    _state.forget("demo")
    assert _state.recorded("demo", "snapshot") is None
    assert os.stat(_state.path()).st_mode & 0o777 == 0o600
