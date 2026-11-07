"""comms' and cache's daemons on the writer: an install from a release writes each unit
through `moxi.units`, an editable install is refused, and the unmarked unit each
module's old `service` verb wrote is refused until `--replace`.

The modules are the real ones in this environment, reached the way the core reaches any
module: by their `moxi.modules` entry point, never by an import. A stand-in `.dist-info`
put first on `sys.path` names the real announcement and says, in its `direct_url.json`,
how it was installed. Units go to the fake backend over a directory in tmp. A module this
environment does not hold is skipped.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

import moxi.modules
import moxi.paths
from moxi import units
from moxi.units import _backend
from moxi.units._fake import FakeBackend, FakeSupervisor
from moxi_dev.testing import console_script

#: Unit names here are the default home's, which carry no tag (`moxi.units.home_tag`).
pytestmark = pytest.mark.usefixtures("default_home")

#: How an install records itself in `direct_url.json`; one from an index writes none.
FROM_INDEX = None
FROM_WHEEL = {"url": "file:///wheels/x-1.0.0-py3-none-any.whl", "archive_info": {}}
EDITABLE = {"url": "file:///src/x", "dir_info": {"editable": True}}

#: Each module's one daemon: its name, console script, unit file, and the lines the
#: fake backend renders from its entry.
DAEMONS = {
    "comms": ("daemon", "moxi-comms-daemon", "moxi-comms-daemon.service",
              ("description = Moxi comms: the delivery daemon", "stop = group")),
    "cache": ("watch", "moxi-cache-watch", "moxi-cache-watch.service",
              ("description = Moxi cache: the watcher that keeps clock records of live "
               "Claude sessions",
               # A stop ends the watcher alone: the asks it started outlive it.
               "stop = process")),
}

#: The modules whose daemon declares a socket, which the writer plans as a change of its
#: own beside the daemon's.
SOCKETS = {"comms"}

@pytest.fixture(params=sorted(DAEMONS))
def box(request, tmp_path, monkeypatch):
    """One module's daemon, a scripts directory holding its console script, its roots
    (the default home's, `default_home`) and the user unit folder in tmp, and the fake
    backend over that folder."""
    module = request.param
    if importlib.util.find_spec(f"moxi_{module}") is None:
        pytest.skip(f"moxi-{module} is not installed in this environment")
    name, script, unit, lines = DAEMONS[module]
    scripts = tmp_path / "bin"
    scripts.mkdir()
    command = console_script(scripts, script)
    monkeypatch.setattr(moxi.paths, "_scripts", lambda: scripts)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    # The module's roots are the default home's, in tmp: a locator of its own would have
    # the plan refuse its units.
    root = home / ".config" / "systemd" / "user"
    root.mkdir(parents=True)
    supervisor = FakeSupervisor(root)

    def install(how):
        info = tmp_path / "site" / f"moxi_{module}-1.0.0.dist-info"
        info.mkdir(parents=True)
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: moxi-{module}\n"
                                       "Version: 1.0.0\n")
        (info / "entry_points.txt").write_text(
            f"[moxi.modules]\n{module} = moxi_{module}.module\n")
        if how is not None:
            (info / "direct_url.json").write_text(json.dumps(how))
        monkeypatch.syspath_prepend(str(info.parent))
        moxi.modules.reset()
        found = moxi.modules.load(module)
        assert (found.state, found.distribution, found.version) == (
            moxi.modules.PRESENT, f"moxi-{module}", "1.0.0"), found.reason

    class Box:
        pass

    b = Box()
    b.module, b.name, b.command, b.unit, b.lines = module, name, command, root / unit, lines
    b.sup, b.root, b.tmp, b.install = supervisor, root, tmp_path, install
    b.said = []
    b.apply = lambda changes, **kw: units.apply(changes, say=b.said.append, **kw)
    moxi.modules.reset()
    with _backend.use(FakeBackend(root, runner=supervisor)):
        yield b
    moxi.modules.reset()


def daemon_of(box, changes):
    """The daemon's change among `changes`; the rest is its socket, when it has one."""
    [change] = [c for c in changes if c.kind == "daemon"]
    rest = sorted(c.kind for c in changes if c is not change)
    assert rest == (["socket"] if box.module in SOCKETS else [])
    return change


def test_the_module_declares_one_daemon_the_core_accepts(box):
    decl = units.declarations(box.module)
    assert list(decl.daemons) == [box.name] and decl.timers == {}
    assert moxi.paths.script(decl.daemons[box.name]["command"]) == box.command
    assert decl.rejected == {}


@pytest.mark.parametrize("how", [FROM_INDEX, FROM_WHEEL], ids=["index", "wheel"])
def test_a_release_install_writes_the_daemon_through_the_writer(box, how):
    box.install(how)
    changes = units.plan(box.module)
    change = daemon_of(box, changes)
    assert (change.name, change.kind, change.action) == (box.name, "daemon", "add")
    assert change.paths == (box.unit,)
    text = change.text.splitlines()
    assert text[0] == f"# moxi unit: {box.module}/{box.name}"
    assert f"run = {box.command}" in text
    for line in box.lines:
        assert line in text

    done = box.apply(changes)
    assert all(d.ok for d in done), [d.lines for d in done]
    assert box.unit.read_text() == change.text
    assert oct(box.unit.stat().st_mode & 0o777) == "0o644"
    assert ("enable", "--now", box.unit.name) in box.sup.calls
    assert box.sup.units[box.unit.name] == {"active": True, "enabled": True}
    assert {again.action for again in units.plan(box.module)} == {"keep"}


def test_an_editable_install_is_refused_and_nothing_is_written(box):
    box.install(EDITABLE)
    [change] = units.plan(box.module)
    assert (change.name, change.action, change.cause) == (box.name, "refuse", "editable")
    assert f"moxi-{box.module} is an editable install" in change.reason
    assert f"`moxi install {box.module}`" in change.reason

    [done] = box.apply([change])
    assert done.ok and done.lines == (change.reason,)
    assert list(box.root.iterdir()) == []
    assert box.sup.calls == []


def test_the_old_verbs_unmarked_unit_is_refused_until_replace(box):
    """A unit the module's old `service install` verb wrote before it went: the same file
    name, no mark. The writer refuses that file and names `--replace`, which moves it to
    the displaced folder and writes the marked unit in its place."""
    old = ("[Unit]\nDescription=the old verb's unit\n\n[Service]\nType=simple\n"
           f"ExecStart={box.command}\nRestart=on-failure\n\n"
           "[Install]\nWantedBy=default.target\n")
    box.unit.write_text(old)

    box.install(FROM_INDEX)
    change = daemon_of(box, units.plan(box.module))
    assert (change.action, change.cause, change.foreign) == ("refuse", "foreign", box.unit)
    assert "--replace" in change.reason
    [done] = box.apply([change])
    assert not done.ok
    assert box.unit.read_text() == old

    done = box.apply(units.plan(box.module), replace=True)
    assert all(d.ok for d in done), [d.lines for d in done]
    assert box.unit.read_text().startswith(f"# moxi unit: {box.module}/{box.name}\n")
    [moved] = moxi.paths.core().data_dir().rglob(f"displaced/*/{box.unit.name}")
    assert moved.read_text() == old
