"""Unit names that say their Moxi home: the tag, the names on every backend, the home a
unit runs with, the refusal while a module's own locator is set, and a tagged home that
leaves the default home's units alone."""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import subprocess
import types
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import moxi.modules
import moxi.paths
from moxi import proc, units
from moxi.doctor import _run
from moxi.units import _backend, _home
from moxi.units._backend import Schedule, Spec
from moxi.units._fence import Fence
from moxi.units._launchd import LaunchdBackend
from moxi.units._schtasks import SchtasksBackend
from moxi.units._systemd import SystemdBackend, dropin_name, environment_line
from moxi_dev.testing import console_script, need_symlinks

from fake_backend import FakeBackend, FakeSupervisor

#: Every test starts in the default home; one that wants a tagged home sets `MOXI_HOME`.
pytestmark = pytest.mark.usefixtures("default_home")

SETTINGS = """
[rows]
type = "table"
items = "bool"
default = {}
doc = "One switch per row."
read = "use"
"""

DAEMON = "{'relay': {'command': 'moxi-demo-relay', 'doc': 'the relay'}}"
TWO = ("{'relay': {'command': 'moxi-demo-relay', 'doc': 'the relay'}, "
       "'aux': {'command': 'moxi-demo-relay', 'doc': 'the other'}}")


def tag_of(path: Path) -> str:
    real = os.path.realpath(path)
    return "h" + hashlib.sha256(real.encode()).hexdigest()[:8]


@pytest.fixture
def w(site, tmp_path, monkeypatch):
    """A module `demo`, a scripts directory holding its command and `moxi`, the fake
    backend over one unit directory, and `home(path)` to move to a tagged home (None: the
    default home)."""
    scripts = tmp_path / "bin"
    scripts.mkdir()
    monkeypatch.setattr(moxi.paths, "_scripts", lambda: scripts)
    for name in ("moxi-demo-relay", "moxi"):
        console_script(scripts, name)
    root = tmp_path / "units"
    root.mkdir()
    supervisor = FakeSupervisor(root)
    backend = FakeBackend(root, runner=supervisor)

    def module(**names):
        site.write("moxi_demo/settings.toml", SETTINGS)
        site.module("demo", SETTINGS="'settings.toml'", **names)
        moxi.modules.reset()

    def home(path):
        if path is None:
            monkeypatch.delenv("MOXI_HOME", raising=False)
        else:
            monkeypatch.setenv("MOXI_HOME", str(path))

    def plan(**kw):
        return {(c.name, c.kind): c for c in units.plan("demo", **kw)}

    def apply():
        done = units.apply(units.plan("demo"), say=lambda line: None)
        assert all(a.ok for a in done), done
        return done

    with _backend.use(backend):
        yield types.SimpleNamespace(root=root, sup=supervisor, backend=backend,
                                    module=module, home=home, plan=plan, apply=apply,
                                    scripts=scripts, tmp=tmp_path)


def spec(kind="daemon", **kw) -> Spec:
    base = dict(module="comms", name="daemon", kind=kind,
                command=("/opt/env/bin/moxi-comms-daemon",), doc="the delivery daemon",
                entry={})
    if kind == "timer":
        base["schedule"] = Schedule(every=5)
    return Spec(**{**base, **kw})


# -- the tag and the names --------------------------------------------------------------------

def test_the_default_home_keeps_todays_names_on_every_backend(tmp_path, monkeypatch):
    assert units.home_tag() is None and units.unit_env("comms") == {}
    assert _home.home_tag({"MOXI_HOME": ""}) is None
    assert SystemdBackend(tmp_path).ids("comms", "daemon", "daemon") == (
        "moxi-comms-daemon.socket", "moxi-comms-daemon.service")
    assert LaunchdBackend.label("comms", "daemon") == "moxi.comms.daemon"
    assert SchtasksBackend(user="PC\\ana").task("comms", "daemon") == "\\Moxi\\comms-daemon"
    assert FakeBackend(tmp_path)._stem("comms", "daemon") == "moxi-comms-daemon"
    assert dropin_name("comms") == "50-moxi-comms.conf"


def test_a_set_home_gives_the_tagged_name_on_every_backend(tmp_path, monkeypatch):
    home = tmp_path / "scratch-home"
    monkeypatch.setenv("MOXI_HOME", str(home))
    tag = units.home_tag()
    assert tag == tag_of(home) and len(tag) == 9 and tag.startswith("h")
    assert SystemdBackend(tmp_path).ids("comms", "daemon", "daemon") == (
        f"moxi-{tag}-comms-daemon.socket", f"moxi-{tag}-comms-daemon.service")
    assert SystemdBackend(tmp_path).ids("comms", "nightly", "timer") == (
        f"moxi-{tag}-comms-nightly.timer", f"moxi-{tag}-comms-nightly.service")
    assert LaunchdBackend.label("comms", "daemon") == f"moxi.{tag}.comms.daemon"
    assert LaunchdBackend(agents=tmp_path).files("comms", "daemon", "daemon") == (
        tmp_path / f"moxi.{tag}.comms.daemon.plist",)
    assert SchtasksBackend(user="PC\\ana").task("comms", "daemon") == (
        f"\\Moxi\\{tag}\\comms-daemon")
    assert FakeBackend(tmp_path)._stem("comms", "daemon") == f"moxi-{tag}-comms-daemon"
    assert dropin_name("comms") == f"50-moxi-{tag}-comms.conf"
    # The namespace prefixes a reader may test stay on every home's names.
    assert SystemdBackend(tmp_path).own(f"moxi-{tag}-comms-daemon.service")


def test_two_spellings_of_one_folder_give_one_tag_and_one_value(tmp_path, monkeypatch):
    need_symlinks()
    real = tmp_path / "homes" / "real"
    real.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.chdir(tmp_path / "homes")
    spellings = [str(real), "real", "./real/../real", "~/homes/real", str(link)]
    want = os.path.realpath(real)
    seen = {(_home.home_tag({"MOXI_HOME": s, "HOME": str(tmp_path)}),
             _home.unit_env("comms", {"MOXI_HOME": s, "HOME": str(tmp_path)})["MOXI_HOME"])
            for s in spellings}
    assert seen == {(tag_of(real), want)}


def test_units_ids_is_the_public_name_of_a_unit_in_this_home(w, monkeypatch):
    assert units.ids("demo", "relay") == ("moxi-demo-relay.socket", "moxi-demo-relay.service")
    assert units.ids("demo", "snap", "timer") == ("moxi-demo-snap.timer",
                                                 "moxi-demo-snap.service")
    w.home(w.tmp / "h")
    tag = units.home_tag()
    assert units.ids("demo", "relay")[-1] == f"moxi-{tag}-demo-relay.service"
    with pytest.raises(ValueError):
        units.ids("demo", "relay", "socket")
    monkeypatch.setattr(_backend, "implementation", lambda: None)
    assert units.ids("demo", "relay") == ()


def test_units_ids_is_empty_for_a_supervisor_with_no_writer(monkeypatch):
    def no_writer():
        raise NotImplementedError("this moxi has no launchd writer yet")

    monkeypatch.setattr(_backend, "implementation", no_writer)
    assert units.ids("demo", "relay") == ()
    assert units.ids("demo", "snap", "timer") == ()


# -- the home a unit runs with ------------------------------------------------------------------

def test_environment_is_quoted_by_its_own_rules():
    """`$` is no variable to systemd here, so it stays one `$`; `%` is a specifier and is
    doubled; `\\` and `"` are escaped inside the quotes."""
    path = '/home/a b/100%/$HOME/x"y\\z'
    assert environment_line("MOXI_HOME", path) == (
        'Environment="MOXI_HOME=/home/a b/100%%/$HOME/x\\"y\\\\z"')


def test_a_tagged_homes_systemd_services_carry_the_home(tmp_path, monkeypatch):
    scripts = tmp_path / "bin"
    scripts.mkdir()
    console_script(scripts, "moxi")
    monkeypatch.setattr(moxi.paths, "_scripts", lambda: scripts)
    home = '/srv/100%/$x'
    line = 'Environment="MOXI_HOME=/srv/100%%/$x"'
    env = (("MOXI_HOME", home),)
    [daemon] = SystemdBackend(tmp_path).render(spec(environment=env))
    [timer] = SystemdBackend(tmp_path).render(spec("timer", name="nightly", environment=env))
    for path, text in (*daemon.files, timer.files[0]):
        lines = text.splitlines()
        assert lines.index(line) > lines.index("[Service]"), path
    assert line not in timer.files[1][1]
    [plain] = SystemdBackend(tmp_path).render(spec())
    assert "Environment=" not in plain.files[0][1]


def test_plan_writes_the_real_path_the_tag_was_made_from(w):
    need_symlinks()
    real = w.tmp / "real-home"
    real.mkdir()
    (w.tmp / "via").symlink_to(real, target_is_directory=True)
    w.module(DAEMONS=DAEMON)
    w.home(w.tmp / "via")
    change = w.plan()[("relay", "daemon")]
    tag = tag_of(real)
    assert change.paths == (w.root / f"moxi-{tag}-demo-relay.service",)
    assert f"env = MOXI_HOME={os.path.realpath(real)}" in change.text.splitlines()
    w.home(None)
    assert not any(line.startswith("env = ")
                   for line in w.plan()[("relay", "daemon")].text.splitlines())


def test_a_tagged_homes_launchd_plist_carries_the_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MOXI_HOME", str(tmp_path / "h"))
    tag = units.home_tag()
    backend = LaunchdBackend(agents=tmp_path / "agents", uid=501, gate="/opt/moxi")
    home = units.unit_env("comms")["MOXI_HOME"]
    for kind in ("daemon", "timer"):
        [group] = backend.render(spec(kind, environment=(("MOXI_HOME", home),)))
        [(path, text)] = group.files
        body = plistlib.loads(text.encode())
        assert path.name == f"moxi.{tag}.comms.daemon.plist"
        assert body["Label"] == f"moxi.{tag}.comms.daemon"
        assert body["EnvironmentVariables"]["MOXI_HOME"] == home
        assert "MOXI_LOG_FILE" in body["EnvironmentVariables"]


@pytest.fixture
def tasks(tmp_path, monkeypatch):
    """A scripts directory with `pythonw.exe` beside `python`, for the Task Scheduler
    render, and the backend as `PC\\ana`."""
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    monkeypatch.setattr(moxi.paths, "_scripts", lambda: scripts)
    for name in ("python", "python.exe", "pythonw.exe"):
        (scripts / name).write_text("")
        (scripts / name).chmod(0o755)
    return SchtasksBackend(user="PC\\ana")


def test_a_tagged_homes_task_gives_its_gate_the_home(tasks, tmp_path, monkeypatch):
    monkeypatch.setenv("MOXI_HOME", str(tmp_path / "h"))
    home = units.unit_env("comms")["MOXI_HOME"]
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    for kind in ("daemon", "timer"):
        [group] = tasks.render(spec(kind, environment=(("MOXI_HOME", home),)))
        [(_path, text)] = group.files
        root = ET.fromstring(text.encode("utf-16"))
        args = root.find("t:Actions/t:Exec/t:Arguments", ns).text
        assert args.startswith(subprocess.list2cmdline(["-m", "moxi", "run", "--home", home]))
    [group] = tasks.render(spec())
    assert "--home" not in group.files[0][1]


def test_a_tagged_homes_running_task_is_read_from_its_tag_folder(tasks, tmp_path,
                                                                 monkeypatch):
    monkeypatch.setenv("MOXI_HOME", str(tmp_path / "h"))
    path = SchtasksBackend.runtime_file("demo", "relay")
    path.parent.mkdir(parents=True, exist_ok=True)
    me = proc.identity(os.getpid())
    path.write_text(json.dumps({"pid": me.pid, "started": me.started}))
    task = tasks.task("demo", "relay")
    other = "\\Moxi\\demo-relay"
    assert tasks.running([task, other], Fence("schtasks", lambda *a, **k: None)) == {task}


# -- two homes in one unit directory -----------------------------------------------------------

def test_marked_keeps_two_homes_apart_in_one_unit_directory(w):
    w.module(DAEMONS=DAEMON)
    w.apply()
    w.home(w.tmp / "h")
    tag = units.home_tag()
    assert {c.action for c in w.plan().values()} == {"add"}
    assert w.backend.marked("demo") == ()
    w.apply()
    [ref] = w.backend.marked("demo")
    assert ref.paths == (w.root / f"moxi-{tag}-demo-relay.service",)
    w.home(None)
    [ref] = w.backend.marked("demo")
    assert ref.paths == (w.root / "moxi-demo-relay.service",)
    assert {c.action for c in w.plan().values()} == {"keep"}


def test_systemd_marked_keeps_two_homes_apart(tmp_path, monkeypatch):
    root = tmp_path / "user"
    root.mkdir()
    backend = SystemdBackend(root)
    for home in (None, tmp_path / "a", tmp_path / "b"):
        if home is None:
            monkeypatch.delenv("MOXI_HOME", raising=False)
        else:
            monkeypatch.setenv("MOXI_HOME", str(home))
        for group in backend.render(spec()):
            for path, text in group.files:
                path.write_text(text)
    for home in (None, tmp_path / "a", tmp_path / "b"):
        if home is None:
            monkeypatch.delenv("MOXI_HOME", raising=False)
        else:
            monkeypatch.setenv("MOXI_HOME", str(home))
        [ref] = backend.marked("comms")
        assert ref.paths == (root / f"{backend._stem('comms', 'daemon')}.service",)


def test_a_tagged_homes_doctor_and_acts_name_no_untagged_unit(w):
    """An untagged unit is the default home's: a tagged home's doctor shows none of its
    rows, and its remove and stop leave it written and running."""
    w.module(DAEMONS=TWO)
    w.apply()
    untagged = sorted(p.name for p in w.root.iterdir())
    assert untagged == ["moxi-demo-aux.service", "moxi-demo-relay.service"]
    assert all(w.sup.units[name]["active"] for name in untagged)
    w.home(w.tmp / "h")
    rows = _run.run(["demo"])
    unit_rows = [r for r in rows if r.name.startswith(("daemon ", "timer ", "socket "))]
    assert unit_rows and all(r.detail.startswith("not installed") for r in unit_rows)
    assert not any("moxi-demo-" in f"{r.detail} {r.fix}" for r in rows)
    w.apply()
    units.stop("demo", ["relay", "aux"])
    units.remove("demo")
    assert sorted(p.name for p in w.root.iterdir()) == untagged
    assert all(w.sup.units[name]["active"] for name in untagged)
    assert not any("moxi-demo-" in " ".join(call) for call in w.sup.calls
                   if call[0] in ("stop", "disable"))


# -- a module's own locator --------------------------------------------------------------------

@pytest.mark.parametrize("home", [None, "h"])
def test_a_modules_locator_refuses_its_units_in_any_home(w, monkeypatch, home):
    w.module(DAEMONS=TWO)
    w.apply()
    w.module(DAEMONS=DAEMON)  # aux is no longer declared, and is refused, not removed
    if home:
        w.home(w.tmp / home)
    monkeypatch.setenv("MOXI_DEMO_HOME", str(w.tmp / "demo-store"))
    changes = units.plan("demo")
    assert {(c.name, c.action, c.cause) for c in changes} == {
        ("relay", "refuse", "locator"), ("aux", "refuse", "locator")} if not home else {
        ("relay", "refuse", "locator")}
    for change in changes:
        assert "MOXI_DEMO_HOME is set" in change.reason and "MOXI_HOME" in change.reason
        assert change.reason.endswith("fix: unset MOXI_DEMO_HOME")
    before = sorted(p.name for p in w.root.iterdir())
    assert not any(a.ok for a in units.apply(changes, say=lambda line: None))
    assert sorted(p.name for p in w.root.iterdir()) == before


@pytest.mark.parametrize("variable", ["MOXI_DEMO_DATA_HOME", "MOXI_DEMO_STATE_HOME",
                                      "MOXI_DEMO_CACHE_HOME", "MOXI_DEMO_CONFIG_HOME",
                                      "MOXI_DEMO_RUNTIME_DIR"])
def test_every_locator_of_the_module_refuses(w, monkeypatch, variable):
    w.module(DAEMONS=DAEMON)
    monkeypatch.setenv(variable, str(w.tmp / "elsewhere"))
    [change] = units.plan("demo")
    assert (change.action, change.cause) == ("refuse", "locator")
    assert change.reason.startswith(f"{variable} is set")


def test_other_variables_of_the_namespace_and_moxi_home_alone_do_not_refuse(w, monkeypatch):
    w.module(DAEMONS=DAEMON)
    for name, value in (("MOXI_DEMO_NAME", "lead"), ("MOXI_DEMO_MCP_URL", "http://x"),
                        ("MOXI_DEMO_SCOPE", "s"), ("MOXI_DEMO_HOME", ""),
                        ("MOXI_OTHER_HOME", str(w.tmp / "o"))):
        monkeypatch.setenv(name, value)
    assert [c.action for c in units.plan("demo")] == ["add"]
    w.home(w.tmp / "h")
    assert [c.action for c in units.plan("demo")] == ["add"]


def test_comms_locators_are_its_prefix_variables_and_nothing_else():
    session = {"MOXI_COMMS_NAME": "lead", "MOXI_COMMS_SCOPE": "s",
               "MOXI_COMMS_MCP_TOKEN": "t", "MOXI_COMMS_MCP_URL": "http://x",
               "MOXI_COMMS_PATH_LINE": "p", "MOXI_HOME": "/h"}
    assert _home.locators_set("comms", session) == ()
    assert _home.locators_set("comms", {**session, "MOXI_COMMS_HOME": "/c",
                                        "MOXI_COMMS_RUNTIME_DIR": "/r"}) == (
        "MOXI_COMMS_HOME", "MOXI_COMMS_RUNTIME_DIR")


# -- a wrap per home ---------------------------------------------------------------------------

def test_a_wrap_drop_in_is_per_home_and_tells_its_gate_the_home(w):
    w.module(DAEMONS=DAEMON)
    w.sup.extra |= {"backup.timer", "backup-run.service"}
    w.sup.triggers["backup.timer"] = "backup-run.service"
    w.sup.reload()
    w.home(w.tmp / "h")
    tag = units.home_tag()
    home = units.unit_env("demo")["MOXI_HOME"]
    acted = units.wrap("backup.timer", module="demo", key="rows.backup", name="backup")
    assert acted.ok, acted.lines
    dropin = w.root / "backup-run.service.d" / f"50-moxi-{tag}-demo.conf"
    text = dropin.read_text()
    assert f"run --home {home} --if demo.rows.backup" in text
    assert f"run --home {home} --record demo/backup --result" in text
    assert units.wraps("demo") == {"backup.timer": units.Wrapped("demo", "rows.backup",
                                                                  "backup")}
    w.home(None)
    assert units.wraps("demo") == {}
    refused = units.wrap("backup.timer", module="demo", key="rows.backup", name="backup")
    assert not refused.ok
    assert f"of another Moxi home (50-moxi-{tag}-demo.conf)" in refused.lines[0]
    assert sorted(p.name for p in dropin.parent.iterdir()) == [dropin.name]

