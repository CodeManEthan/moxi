"""`moxi uninstall`, by its steps against a fake environment.

The fake uv of `test_install.py` stands in for uv: an install adds the fake modules a
command names, and here a re-run of `uv tool install` also removes every distribution the
new requirements no longer reach, as uv does, and `uv pip uninstall` removes the one it
names. Units live on the fake backend. The teardown child is replaced by a stand-in that
returns the outcome a test sets; `test_settle_procs.py` runs the real child.
"""

from __future__ import annotations

import json
import os
import shutil
import sys

import pytest

import moxi.cli as cli
import moxi.install as install
import moxi.modules as modules
import moxi.uninstall as uninstall
from conftest import drop_modules
from moxi import _questions, _settle, files, paths, units
from moxi.units import _backend, _state
from moxi.units._fake import FakeBackend, FakeSupervisor
from test_install import CATALOG, Fake, _requirement, moxi_install


class Removing(Fake):
    """The fake uv, removing too."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.teardowns: list[str] = []
        self.teardown_code = 0
        self.teardown_signals: tuple = ()

    def run(self, argv, env=None):
        if argv[1:3] == ["pip", "uninstall"] or argv[2:4] == ["pip", "uninstall"]:
            self.runs.append(argv)
            self._remove(argv[-1])
            return 0
        code = super().run(argv, env)
        if code == 0 and self.kind == install.TOOL:
            kept = self._closure([_requirement(t) for t in self._texts(argv)])
            for name in sorted(self.installed - kept):
                self._remove(name)
        return code

    @staticmethod
    def _texts(argv):
        return [argv[3]] + [argv[i + 1] for i, a in enumerate(argv) if a == "--with"]

    def _closure(self, reqs) -> set[str]:
        seen: set[str] = set()
        todo = list(reqs)
        while todo:
            req = todo.pop()
            spec = CATALOG.get(req.name, {})
            if req.name not in seen:
                seen.add(req.name)
                todo += [_requirement(d) for d in spec.get("requires", ())]
            for extra in req.extras:
                todo += [_requirement(d) for d in spec.get("extras", {}).get(extra, ())]
        return seen

    def _remove(self, dist: str) -> None:
        dist = cli.normalize(dist)
        self.installed.discard(dist)
        norm = dist.replace("-", "_")
        for info in self.site.root.glob(f"{norm}-*.dist-info"):
            shutil.rmtree(info)
        module = CATALOG.get(dist, {}).get("module")
        if module:
            shutil.rmtree(self.site.root / f"moxi_{module}", ignore_errors=True)
            drop_modules({f"moxi_{module}"})
        modules.reset()

    def run_teardown(self, python, module):
        self.teardowns.append(module)
        return _settle.Outcome(self.teardown_code, self.teardown_signals)


@pytest.fixture
def fake(site, tmp_path, monkeypatch):
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    f = Removing(site, tmp_path, monkeypatch)
    monkeypatch.setattr(uninstall, "run_teardown", f.run_teardown)
    root = tmp_path / "units"
    root.mkdir()
    f.sup = FakeSupervisor(root)
    f.root = root
    with _backend.use(FakeBackend(root, runner=f.sup)):
        yield f


def moxi_uninstall(capsys, *argv):
    code = cli.main(["uninstall", *argv])
    out, err = capsys.readouterr()
    return code, out, err


def receipt(fake) -> list[str]:
    import tomllib

    held = tomllib.loads((fake.prefix / "uv-receipt.toml").read_text())["tool"]
    return [install.receipt_entry(e).text() for e in held["requirements"]]


# -- the requirement builder -------------------------------------------------------------------

def test_without_drops_the_distribution_and_every_extra_named_by_the_module():
    reqs = [install.Req("moxi"), install.Req("moxi-comms", ["cache", "codex"]),
            install.Req("Moxi_Cache", url="file:///w/c.whl"),
            install.Req("moxi-turns", url="git+https://h/t")]
    assert [r.text() for r in uninstall.without(reqs, "cache")] == [
        "moxi", "moxi-comms[codex]", "moxi-turns @ git+https://h/t"]


def test_tool_command_without_a_module_keeps_the_rest_of_the_receipt(tmp_path):
    receipt = {"tool": {
        "requirements": [{"name": "moxi"}, {"name": "moxi-comms", "extras": ["cache"]},
                         {"name": "moxi-cache", "path": "/w/c.whl"}],
        "options": {"find-links": ["file:///w"]}}}
    argv, env, reqs = install.tool_command(receipt, tmp_path / "moxi",
                                           [install.Req("moxi-turns")], "uv", tmp_path,
                                           without="cache")
    assert argv == ["uv", "tool", "install", "moxi", "--with", "moxi-comms", "--with",
                    "moxi-turns", "--find-links", "file:///w"]


def test_plain_environment_removes_with_uv_or_pip(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "/u/uv")
    assert uninstall.uninstall_command("moxi-comms") == [
        "/u/uv", "pip", "uninstall", "--python", sys.executable, "moxi-comms"]
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    assert uninstall.uninstall_command("moxi-comms") == [
        sys.executable, "-m", "pip", "uninstall", "-y", "moxi-comms"]


# -- step 0 and step 1: refusals --------------------------------------------------------------

def test_uninstalling_moxi_itself_is_refused(fake, capsys):
    code, _, err = moxi_uninstall(capsys, "moxi")
    assert code == 3
    assert "never removes the moxi command" in err and "uv tool uninstall moxi" in err
    assert fake.runs == []


def test_a_module_that_is_not_installed_is_refused(fake, capsys):
    code, _, err = moxi_uninstall(capsys, "comms")
    assert code == 3 and "comms is not installed here" in err


def test_a_bad_name_or_a_verb_is_refused(fake, capsys):
    assert moxi_uninstall(capsys, "Comms")[0] == 1
    assert moxi_uninstall(capsys, "install")[0] == 1


def test_a_module_another_installed_module_requires_is_refused(fake, capsys):
    moxi_install(capsys, "comms", "--none")
    fake.runs.clear()
    code, _, err = moxi_uninstall(capsys, "turns")
    assert code == 3 and "comms requires turns; uninstall it first" in err
    assert fake.runs == [] and fake.teardowns == []


def test_the_environment_checks_are_install_s(fake, capsys, monkeypatch):
    monkeypatch.setattr(install, "environment", lambda: (_ for _ in ()).throw(
        install.InstallError("moxi runs from the system Python")))
    code, _, err = moxi_uninstall(capsys, "comms")
    assert code == 1 and "system Python" in err


# -- step 6: the package ----------------------------------------------------------------------

def test_uninstall_reruns_uv_without_the_module_and_keeps_what_it_pulled_in(fake, capsys):
    moxi_install(capsys, "comms", "--none")
    assert fake.installed == {"moxi-comms", "moxi-turns"}
    fake.runs.clear()
    code, out, err = moxi_uninstall(capsys, "comms")
    assert code == 0, err
    assert fake.runs == [["/fake/uv", "tool", "install", "moxi", "--with", "moxi-turns",
                          "--find-links", "file:///w"]]
    assert fake.installed == {"moxi-turns"}
    assert receipt(fake) == ["moxi", "moxi-turns"]
    assert "uninstalled comms (moxi-comms 0.1.0)" in out
    assert ("turns was installed for comms, and nothing else requires it now; uninstall "
            "it with: moxi uninstall turns") in out


def test_uninstalling_a_module_taken_through_an_extra_drops_the_extra(fake, capsys):
    moxi_install(capsys, "comms", "--with", "cache")
    assert receipt(fake) == ["moxi", "moxi-comms[cache]"]
    fake.runs.clear()
    code, out, err = moxi_uninstall(capsys, "cache")
    assert code == 0, err
    assert receipt(fake) == ["moxi", "moxi-comms"]
    assert "moxi-cache" not in fake.installed and "moxi-comms" in fake.installed
    assert "uninstalled cache" in out and "was installed for" not in out


def test_uninstall_in_a_plain_environment(site, tmp_path, monkeypatch, capsys):
    f = Removing(site, tmp_path, monkeypatch, kind=install.PLAIN)
    monkeypatch.setattr(uninstall, "run_teardown", f.run_teardown)
    root = tmp_path / "units"
    root.mkdir()
    with _backend.use(FakeBackend(root, runner=FakeSupervisor(root))):
        moxi_install(capsys, "comms", "--none")
        f.runs.clear()
        code, out, err = moxi_uninstall(capsys, "comms")
    assert code == 0, err
    assert f.runs == [["/fake/uv", "pip", "uninstall", "--python", sys.executable,
                       "moxi-comms"]]
    assert f.installed == {"moxi-turns"}
    assert "nothing else requires it now; uninstall it with: moxi uninstall turns" in out


def test_a_failed_package_step_exits_1(fake, capsys):
    moxi_install(capsys, "turns", "--none")
    fake.fail_run = True
    code, out, err = moxi_uninstall(capsys, "turns")
    assert code == 1
    assert "removing moxi-turns failed (exit 1)" in err
    assert "turns was not uninstalled" in err and "the package step failed" in err


# -- step 2: the teardown ---------------------------------------------------------------------

@pytest.fixture
def torn(fake, capsys, monkeypatch):
    """turns installed, declaring a `TEARDOWN`."""
    monkeypatch.setitem(CATALOG, "moxi-turns",
                        {**CATALOG["moxi-turns"], "TEARDOWN": "'moxi_turns.down:run'"})
    moxi_install(capsys, "turns", "--none")
    fake.runs.clear()
    return fake


def test_a_teardown_that_refuses_removes_nothing_and_exits_3(torn, capsys):
    torn.teardown_code = 3
    code, _, err = moxi_uninstall(capsys, "turns")
    assert code == 3
    assert "turns's teardown refused the uninstall; nothing was removed" in err
    assert torn.teardowns == ["turns"] and torn.runs == []
    assert "moxi-turns" in torn.installed


def test_a_teardown_that_fails_names_skip_teardown_and_removes_nothing(torn, capsys):
    torn.teardown_code = 1
    code, _, err = moxi_uninstall(capsys, "turns")
    assert code == 1
    assert ("teardown turns: failed (exit 1); nothing was removed. To uninstall without "
            "it: moxi uninstall turns --skip-teardown") in err
    assert torn.runs == []


@pytest.mark.parametrize("how", ["code", "signal"])
def test_an_interrupted_teardown_removes_nothing_and_exits_1(torn, capsys, how):
    if how == "code":
        torn.teardown_code = _settle.INTERRUPTED
    else:
        torn.teardown_signals = (2,)
    code, _, err = moxi_uninstall(capsys, "turns")
    assert code == 1
    assert "interrupted in turns's teardown; nothing was removed" in err
    assert torn.runs == []


def test_skip_teardown_goes_on_without_it(torn, capsys):
    torn.teardown_code = 1
    code, out, err = moxi_uninstall(capsys, "turns", "--skip-teardown")
    assert code == 0, err
    assert "teardown turns: skipped (--skip-teardown)" in out
    assert torn.teardowns == [] and "moxi-turns" not in torn.installed


def test_a_module_without_teardown_starts_no_child(fake, capsys):
    moxi_install(capsys, "turns", "--none")
    code, _, err = moxi_uninstall(capsys, "turns")
    assert code == 0, err
    assert fake.teardowns == []


def test_a_broken_module_has_no_teardown_to_run_and_is_still_removed(fake, capsys):
    moxi_install(capsys, "turns", "--none")
    fake.site.write("moxi_turns/module.py", "ANNOUNCES = 99\n")
    drop_modules({"moxi_turns"})
    modules.reset()
    code, out, err = moxi_uninstall(capsys, "turns")
    assert code == 0, err
    assert "turns is broken, so it has no teardown to run" in out
    assert fake.teardowns == [] and "moxi-turns" not in fake.installed


# -- the order, and steps 3 and 4 --------------------------------------------------------------

LISTENER = """
import json, os

import moxi.paths
from moxi import files

def on_install(module, found):
    pass

def on_uninstall(module):
    # A core call under the uninstall's lock: it yields, so trying once never fails.
    with files.lock(moxi.paths.core_lock(), nested=True, timeout=0):
        pass
    with open(os.environ["MOXITEST_HEARD"], "a") as fh:
        fh.write(json.dumps({"who": WHO, "module": module}) + "\\n")
    if os.environ.get("MOXITEST_FAIL_" + WHO):
        raise RuntimeError("no route")
"""


def listener(fake, module: str) -> None:
    """Give installed module `module` a `moxi.installs` listener."""
    fake.site.write(f"moxi_{module}/installs.py", f"WHO = {module!r}\n" + LISTENER)
    info = next(fake.site.root.glob(f"moxi_{module}-*.dist-info"))
    (info / "entry_points.txt").write_text(
        f"[moxi.modules]\n{module} = moxi_{module}.module\n"
        f"[moxi.installs]\n{module} = moxi_{module}.installs\n")
    modules.reset()


def test_every_step_in_order_with_each_listener_and_the_records_dropped(
        torn, capsys, monkeypatch, tmp_path):
    heard = tmp_path / "heard.jsonl"
    monkeypatch.setenv("MOXITEST_HEARD", str(heard))
    moxi_install(capsys, "cache", "--none")
    listener(torn, "turns")
    listener(torn, "cache")
    _state.record("turns", "snapshot", "abc")
    _state.record("cache", "sweep", "def")
    _questions.record("turns", "guard.push")
    _questions.record("cache", "keep")
    order: list[str] = []
    real_teardown, real_remove, real_run = (torn.run_teardown, units.remove, torn.run)
    monkeypatch.setattr(uninstall, "run_teardown",
                        lambda p, m: order.append("teardown") or real_teardown(p, m))

    def remove(module, **k):
        order.append("units")
        return real_remove(module, **k)
    monkeypatch.setattr(units, "remove", remove)
    monkeypatch.setattr(install, "run", lambda argv, env=None: order.append("package")
                        or real_run(argv, env))
    torn.runs.clear()
    code, out, err = moxi_uninstall(capsys, "turns")
    assert code == 0, err
    assert order == ["teardown", "units", "package"]
    events = [json.loads(line) for line in heard.read_text().splitlines()]
    assert events == [{"who": "cache", "module": "turns"},
                      {"who": "turns", "module": "turns"}]
    assert _state.read() == {"cache/sweep": _state.read()["cache/sweep"]}
    assert _questions.read() == {"cache": _questions.read()["cache"]}


def test_a_failing_listener_is_printed_and_the_later_steps_still_run(
        fake, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("MOXITEST_HEARD", str(tmp_path / "heard.jsonl"))
    monkeypatch.setenv("MOXITEST_FAIL_turns", "1")
    moxi_install(capsys, "turns", "--none")
    listener(fake, "turns")
    code, out, err = moxi_uninstall(capsys, "turns")
    assert code == 1
    assert "turns: the moxi.installs listener turns failed: RuntimeError: no route" in err
    assert "moxi-turns" not in fake.installed
    assert "uninstalled turns" in out and "the listeners step failed" in err


def test_a_unit_that_fails_to_go_is_printed_and_the_package_still_goes(
        torn, capsys, monkeypatch):
    def remove(module, **k):
        change = units.UnitChange(module, "relay", "daemon", "remove", None, (), None,
                                  "uninstalled")
        return [units.Applied(change, False, ("moxi-turns-relay.service: busy",))]
    monkeypatch.setattr(units, "remove", remove)
    code, out, err = moxi_uninstall(capsys, "turns")
    assert code == 1
    assert "turns: moxi-turns-relay.service: busy" in err
    assert "the units step failed" in err and "moxi-turns" not in torn.installed


def test_units_remove_under_the_uninstall_s_own_lock_yields(torn, capsys, monkeypatch):
    taken = []
    real = files.lock
    monkeypatch.setattr(files, "lock", lambda p, **k: taken.append(k) or real(p, **k))
    code, _, err = moxi_uninstall(capsys, "turns")
    assert code == 0, err
    # The uninstall's own hold, then units.remove's nested call, which yields.
    assert taken[0] == {"timeout": 0} and {"nested": True} in taken[1:]


def test_a_second_uninstall_waits_for_a_running_install(fake, capsys):
    import subprocess
    import textwrap

    moxi_install(capsys, "turns", "--none")
    lock = paths.core_lock()
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import time
        from moxi import files
        with files.lock({str(lock)!r}):
            print("held", flush=True)
            time.sleep(1.5)
        """)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        code, _, err = moxi_uninstall(capsys, "turns")
    finally:
        holder.wait(10)
    assert code == 0, err
    assert f"waiting for another install or uninstall (pid {holder.pid}) to finish" in err


def test_the_config_file_is_kept(fake, capsys):
    moxi_install(capsys, "turns", "--none")
    path = paths._layout("turns").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# mine\n")
    code, _, err = moxi_uninstall(capsys, "turns")
    assert code == 0, err
    assert path.read_text() == "# mine\n"


def test_install_questions_record_is_dropped_only_for_the_module(fake, capsys):
    _questions.record("turns", "guard.push")
    _questions.record("comms", "loud")
    _questions.forget("turns")
    assert set(_questions.read()) == {"comms"}
    _questions.forget("nobody")
    assert set(_questions.read()) == {"comms"}


def test_uninstall_is_in_the_usage_and_help(capsys):
    assert "uninstall <module> [--purge [--yes]] [--skip-teardown]" in cli.USAGE
    assert cli.main(["help", "uninstall"]) == 0
    out = capsys.readouterr().out
    assert "--skip-teardown" in out and "--purge" in out and "--yes" in out


# -- step 5: the placements --------------------------------------------------------------------

@pytest.fixture
def skilled(fake, capsys, monkeypatch, tmp_path):
    """turns installed, declaring a `TEARDOWN` and shipping skill `turns-send`, placed for
    claude and codex under a home of its own."""
    from moxi import place

    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setitem(CATALOG, "moxi-turns",
                        {**CATALOG["moxi-turns"], "TEARDOWN": "'moxi_turns.down:run'",
                         "SKILLS": "'skills'"})
    moxi_install(capsys, "turns", "--none")
    for name in ("turns-send", "turns-read"):
        fake.site.write(f"moxi_turns/skills/{name}/SKILL.md", f"# {name}\n")
    source = fake.site.root / "moxi_turns" / "skills"
    items = [place.Item(source / name, harness, name)
             for name in ("turns-read", "turns-send") for harness in ("claude", "codex")]
    place.apply(place.plan("turns", items), say=lambda line: None)
    fake.runs.clear()
    fake.place = place
    fake.claude = lambda name: user / ".claude" / "skills" / name
    fake.codex = lambda name: user / ".agents" / "skills" / name
    return fake


def test_uninstall_removes_the_placements_after_the_units_and_before_the_package(
        skilled, capsys, monkeypatch):
    place = skilled.place
    order: list[str] = []
    real_remove, real_units, real_run = place.remove, units.remove, skilled.run
    monkeypatch.setattr(units, "remove",
                        lambda m, **k: order.append("units") or real_units(m, **k))

    def remove(owner, **k):
        order.append("placements")
        return real_remove(owner, **k)
    monkeypatch.setattr(place, "remove", remove)
    monkeypatch.setattr(install, "run", lambda argv, env=None: order.append("package")
                        or real_run(argv, env))
    code, out, err = moxi_uninstall(capsys, "turns")
    assert code == 0, err
    assert order == ["units", "placements", "package"]
    for name in ("turns-read", "turns-send"):
        assert not os.path.lexists(skilled.claude(name))
        assert not os.path.lexists(skilled.codex(name))
    assert (f"turns: removed skill turns-send for claude ({skilled.claude('turns-send')})"
            in out)
    assert place.status("turns") == []


def test_a_skill_handed_over_stays_and_a_hand_back_in_teardown_is_removed(
        skilled, capsys, monkeypatch):
    """Two skills handed to loadout: its teardown hands one back, and the uninstall
    removes that one with the rest; the one loadout keeps stays where it is."""
    place = skilled.place
    turns = {p.name + "/" + p.harness: p for p in place.status("turns")}
    place.adopt("turns", "loadout", [turns["turns-send/claude"], turns["turns-read/claude"]],
                say=lambda line: None)

    def teardown(python, module):
        back = [p for p in place.status("loadout") if p.name == "turns-send"]
        place.adopt("loadout", module, back, say=lambda line: None)
        return _settle.Outcome(0)
    monkeypatch.setattr(uninstall, "run_teardown", teardown)
    code, out, err = moxi_uninstall(capsys, "turns")
    assert code == 0, err
    assert not os.path.lexists(skilled.claude("turns-send"))
    assert os.path.islink(skilled.claude("turns-read"))
    assert [p.name for p in place.status("loadout")] == ["turns-read"]
    ledger = place._read_ledger()
    send = str(skilled.claude("turns-send"))
    read = str(skilled.claude("turns-read"))
    # The hand-back drops the target from turns' handed map and adds it to loadout's,
    # naming turns; turns keeps the entry for what loadout still holds.
    assert ledger["turns"]["placed"] == {}
    assert set(ledger["turns"]["handed"]) == {read}
    assert ledger["loadout"]["handed"][send]["to"] == "turns"
    assert send not in ledger["loadout"]["placed"]
    # turns no longer places what it was handed back, so loadout's entry has lapsed and
    # loadout may place that target; turns' entry for what loadout holds still skips.
    for name in ("turns-send", "turns-read"):      # the package went: put them back
        skilled.site.write(f"moxi_turns/skills/{name}/SKILL.md", f"# {name}\n")
    source = skilled.site.root / "moxi_turns" / "skills"
    assert [s.action for s in place.plan("loadout", [
        place.Item(source / name, "claude", name)
        for name in ("turns-send", "turns-read")])] == ["add", "keep"]
    assert [s.action for s in place.plan("turns", [
        place.Item(source / "turns-read", "claude", "turns-read")])] == ["skip"]


def test_a_placement_that_cannot_go_fails_the_step_and_the_package_still_goes(
        skilled, capsys, monkeypatch):
    place = skilled.place
    monkeypatch.setattr(place, "remove",
                        lambda owner, **k: (_ for _ in ()).throw(ValueError("bad ledger")))
    code, out, err = moxi_uninstall(capsys, "turns")
    assert code == 1
    assert "turns: placements not removed: ValueError: bad ledger" in err
    assert "the placements step failed" in err and "moxi-turns" not in skilled.installed


def test_a_placement_left_behind_by_the_placer_fails_the_step(skilled, capsys, monkeypatch):
    place = skilled.place
    monkeypatch.setattr(place, "remove", lambda owner, **k: [])
    code, out, err = moxi_uninstall(capsys, "turns")
    assert code == 1
    assert (f"turns: skill turns-send for claude was not removed "
            f"({skilled.claude('turns-send')})") in err
    assert "the placements step failed" in err


# -- step 7: --purge ---------------------------------------------------------------------------

STORES = ("{'mail': {'root': 'data', 'path': 'mail.db', 'kind': 'sqlite'}, "
          "'index': {'root': 'cache', 'path': 'index', 'kind': 'dir', 'rebuildable': True}, "
          "'feed': {'root': 'state', 'path': 'feed.jsonl', 'kind': 'jsonl', "
          "'lazy': True}}")


@pytest.fixture
def stored(fake, capsys, monkeypatch):
    """turns installed, declaring three stores, each written, with the core's stores in
    its state directory, its setup.json entry and its config file."""
    monkeypatch.setitem(CATALOG, "moxi-turns", {**CATALOG["moxi-turns"], "STORES": STORES})
    moxi_install(capsys, "turns", "--none")
    layout = paths.Layout("turns")
    data, cache, state = layout.data_dir(), layout.cache_dir(), layout.state_dir()
    for folder in (data, cache / "index" / "deep", state / "logs"):
        folder.mkdir(parents=True, exist_ok=True)
    (data / "mail.db").write_bytes(b"x" * 2048)
    (data / "mail.db-wal").write_bytes(b"w")
    (data / "mail.db-shm").write_bytes(b"s")
    (cache / "index" / "deep" / "a").write_text("a")
    for name in ("runs.jsonl", "runs.jsonl.1", "doctor.json", "logs/relay.log"):
        (state / name).write_text("{}\n")
    _settle.record_setup("turns", "0.1.0")
    _settle.record_setup("cache", "0.1.0")
    config = layout.config_file()
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("# mine\n")
    fake.layout, fake.data, fake.cache, fake.state = layout, data, cache, state
    fake.config = config
    return fake


def core_stores_gone(state) -> bool:
    return not any(os.path.lexists(state / n)
                   for n in ("runs.jsonl", "runs.jsonl.1", "doctor.json", "logs"))


def test_without_purge_every_store_and_the_setup_entry_stay(stored, capsys):
    code, out, err = moxi_uninstall(capsys, "turns")
    assert code == 0, err
    assert (stored.data / "mail.db").exists() and (stored.cache / "index").exists()
    assert (stored.state / "runs.jsonl").exists() and (stored.state / "logs").exists()
    assert "turns" in _settle.setup_versions()


def test_purge_with_nobody_at_the_keyboard_keeps_what_cannot_be_rebuilt(stored, capsys):
    code, out, err = moxi_uninstall(capsys, "turns", "--purge")
    assert code == 0, err
    mail = stored.data / "mail.db"
    assert mail.exists() and (stored.data / "mail.db-wal").exists()
    assert f"turns: kept store mail: {mail} (2.0 KB)" in out
    assert not (stored.cache / "index").exists()
    assert f"turns: removed store index: {stored.cache / 'index'} (1 B)" in out
    assert f"turns: store feed is not there ({stored.state / 'feed.jsonl'})" in out
    assert core_stores_gone(stored.state)
    assert f"turns: removed runs.jsonl: {stored.state / 'runs.jsonl'}" in out
    assert "turns" not in _settle.setup_versions() and "cache" in _settle.setup_versions()
    assert stored.config.read_text() == "# mine\n"
    assert f"turns: kept its config file: {stored.config}" in out
    # The order: the package first, then the stores.
    assert out.index("removed store index") > 0 and "moxi-turns" not in stored.installed


def test_purge_yes_removes_every_store_with_its_sqlite_files(stored, capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked"))
    code, out, err = moxi_uninstall(capsys, "turns", "--purge", "--yes")
    assert code == 0, err
    assert list(stored.data.iterdir()) == []
    assert f"turns: removed store mail: {stored.data / 'mail.db'} (2.0 KB)" in out
    assert core_stores_gone(stored.state)


def test_purge_asks_at_a_terminal_default_no(stored, capsys, monkeypatch):
    class Tty:
        def isatty(self):
            return True
    asked: list[str] = []
    monkeypatch.setattr(sys, "stdin", Tty())
    monkeypatch.setattr("builtins.input", lambda q: asked.append(q) or "y")
    code, out, err = moxi_uninstall(capsys, "turns", "--purge")
    assert code == 0, err
    assert asked == [f"turns: remove store mail, {stored.data / 'mail.db'} (2.0 KB)? It "
                     f"can't be rebuilt. [y/N] "]
    assert not (stored.data / "mail.db").exists()


def test_purge_removes_a_store_that_is_a_link_as_a_link(stored, capsys, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "a").write_text("keep me")
    index = stored.cache / "index"
    shutil.rmtree(index)
    index.symlink_to(elsewhere)
    code, out, err = moxi_uninstall(capsys, "turns", "--purge")
    assert code == 0, err
    assert not os.path.lexists(index) and (elsewhere / "a").read_text() == "keep me"
    assert f"turns: removed store index, a link; kept what it points at ({elsewhere})" in out


def test_purge_of_a_broken_module_removes_only_the_core_s_stores(stored, capsys):
    stored.site.write("moxi_turns/module.py", "ANNOUNCES = 99\n")
    drop_modules({"moxi_turns"})
    modules.reset()
    code, out, err = moxi_uninstall(capsys, "turns", "--purge")
    assert code == 0, err
    assert ("turns is broken, so its declared stores can't be read; only the core's "
            "stores in its state directory are removed") in out
    assert (stored.data / "mail.db").exists() and (stored.cache / "index").exists()
    assert core_stores_gone(stored.state)


def test_a_store_that_cannot_be_removed_fails_the_purge(stored, capsys, monkeypatch):
    real = shutil.rmtree

    def rmtree(path, *a, **k):
        if str(path).endswith("index"):
            raise PermissionError(13, "Permission denied")
        return real(path, *a, **k)
    monkeypatch.setattr(shutil, "rmtree", rmtree)
    code, out, err = moxi_uninstall(capsys, "turns", "--purge")
    assert code == 1
    assert (f"turns: store index not removed ({stored.cache / 'index'}): Permission "
            f"denied") in err
    assert "the purge step failed" in err and core_stores_gone(stored.state)


def test_yes_without_purge_is_a_usage_error(fake, capsys):
    with pytest.raises(SystemExit) as raised:
        uninstall.main(["turns", "--yes"])
    assert raised.value.code == 2
    assert "--yes answers --purge's questions; give it with --purge" in \
        capsys.readouterr().err
