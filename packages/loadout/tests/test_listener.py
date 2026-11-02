"""loadout's install listener: a module's install hands its own placements over and
removes the ones the manifest disables; its uninstall removes the ones loadout holds."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import loadout_kit as kit
import pytest

from moxi_loadout import install, listener, stores, writes
from moxi_loadout.manifest import load


def skill_md(name: str) -> str:
    return f"---\nname: {name}\ndescription: A module skill.\n---\nUse it.\n"


@pytest.fixture
def demo(site, folder):
    """A module `demo` shipping skills moxi-demo and moxi-other, and the loadout folder."""
    package = site.module("demo", SKILLS="'skills'", files={
        "skills/moxi-demo/SKILL.md": skill_md("moxi-demo"),
        "skills/moxi-other/SKILL.md": skill_md("moxi-other")})
    kit.configure(folder)
    return site.root / package / "skills"


def module_install(skills, *pairs):
    """What the module's own skills step does after a yes: place each (name, harness) as
    the module, applying only the steps that place."""
    from moxi import place
    from moxi.place import Item

    items = [Item(skills / n, h, n) for n, h in pairs or [("moxi-demo", "claude")]]
    steps = place.plan("demo", items)[:len(items)]
    place.apply([s for s in steps if s.action == "add"], say=lambda line: None)


def found():
    from moxi import modules

    return modules.load("demo")


def lines():
    out: list[str] = []
    return out, out.append


def ledger_bytes() -> bytes | None:
    path = stores.ledger_path()
    return path.read_bytes() if path.exists() else None


def test_a_yes_at_the_modules_question_is_taken_over_in_the_same_run(demo, folder):
    module_install(demo, ("moxi-demo", "claude"), ("moxi-demo", "codex"))
    said, say = lines()
    listener.on_install("demo", found(), say=say)
    for target in (kit.claude() / "moxi-demo", kit.codex_skills() / "moxi-demo"):
        assert kit.owner_of(target) == "loadout"
    assert not kit.handed("demo")[str(kit.claude() / "moxi-demo")].get("seeded")
    manifest = load(folder)
    assert manifest.user.names("skill") == ("demo/moxi-demo",)
    assert sorted(manifest.harnesses("skill", "demo/moxi-demo")) == ["claude", "codex"]
    assert "loadout: took over demo/moxi-demo for claude" in said
    # No ledger write and no install lock: the next install records the hand-over.
    assert ledger_bytes() is None
    assert not (stores.state_dir() / stores.INSTALL_LOCK).exists()
    assert sorted(os.listdir(folder)) == ["loadout.toml"]
    assert install.install().code == 0
    from moxi_loadout import ledger

    records = ledger.read()["placed"]
    assert {r["seeded"] for r in records.values()} == {False}


def test_a_disabled_skill_the_module_places_again_is_removed_in_that_run(demo, folder):
    module_install(demo, ("moxi-demo", "claude"), ("moxi-demo", "codex"))
    assert install.install().code == 0
    writes.disable("skill:demo/moxi-demo@codex")
    assert install.install().code == 0
    codex = kit.codex_skills() / "moxi-demo"
    assert not os.path.lexists(codex)
    # The module's next install places the lapsed skill again; the listener takes it
    # over and removes it, and touches nothing else.
    module_install(demo, ("moxi-demo", "claude"), ("moxi-demo", "codex"))
    assert kit.owner_of(codex) == "demo"
    before = ledger_bytes()
    claude_link = os.readlink(kit.claude() / "moxi-demo")
    said, say = lines()
    listener.on_install("demo", found(), say=say)
    assert not os.path.lexists(codex)
    assert "loadout: removed moxi-demo for codex (disabled)" in said
    assert os.readlink(kit.claude() / "moxi-demo") == claude_link
    assert kit.owner_of(kit.claude() / "moxi-demo") == "loadout"
    assert ledger_bytes() == before
    assert install.install().code == 0
    assert install.status().code == 0


def test_a_module_install_with_nothing_disabled_removes_nothing(demo, folder):
    module_install(demo)
    assert install.install().code == 0
    core_before = kit.core_ledger()
    manifest_before = (folder / "loadout.toml").read_bytes()
    said, say = lines()
    listener.on_install("demo", found(), say=say)
    assert said == []
    assert kit.core_ledger() == core_before
    assert (folder / "loadout.toml").read_bytes() == manifest_before


def test_loadouts_own_install_and_uninstall_change_nothing(demo, folder):
    module_install(demo)
    assert install.install().code == 0
    core_before = kit.core_ledger()
    manifest_before = (folder / "loadout.toml").read_bytes()
    said, say = lines()
    listener.on_install("loadout", None, say=say)
    listener.on_uninstall("loadout", say=say)
    assert said == []
    assert kit.core_ledger() == core_before
    assert (folder / "loadout.toml").read_bytes() == manifest_before


def test_a_listener_that_fails_leaves_the_placement_for_the_next_install(demo, folder,
                                                                          monkeypatch):
    from moxi import place

    kit.manifest(folder, '[user]\nskills = ["demo/moxi-demo"]\n'
                         'disabled = ["skill:demo/moxi-demo@claude"]\n')
    module_install(demo)
    target = kit.claude() / "moxi-demo"
    with monkeypatch.context() as m:
        m.setattr(place, "apply", lambda steps, **kwargs: [])
        with pytest.raises(RuntimeError, match="moxi loadout install removes it"):
            listener.on_install("demo", found(), say=lambda line: None)
    assert os.path.islink(target)
    out = install.install()
    assert out.code == 0, out.text()
    assert not os.path.lexists(target)


def test_an_interrupted_skills_step_is_ended_by_the_next_install(demo, folder):
    # The module placed a disabled skill and the listener never ran.
    kit.manifest(folder, '[user]\nskills = ["demo/moxi-demo"]\n'
                         'disabled = ["skill:demo/moxi-demo@*"]\n')
    module_install(demo)
    out = install.install()
    assert out.code == 0, out.text()
    assert not os.path.lexists(kit.claude() / "moxi-demo")
    from moxi_loadout import ledger

    assert ledger.read()["placed"] == {}


def test_a_module_uninstall_removes_its_skills_seeded_or_not(demo, folder):
    from moxi import place
    from moxi.place import Item

    module_install(demo)
    kit.manifest(folder, '[user]\nskills = ["demo/moxi-demo", "demo/moxi-other"]\n'
                         '[skill."demo/moxi-demo"]\nharnesses = ["claude"]\n'
                         '[skill."demo/moxi-other"]\nharnesses = ["claude"]\n')
    assert install.install().code == 0
    other = kit.claude() / "moxi-other"
    assert kit.handed("demo")[str(other)]["seeded"] is True
    manifest_before = (folder / "loadout.toml").read_bytes()
    said, say = lines()
    listener.on_uninstall("demo", say=say)
    assert not os.path.lexists(kit.claude() / "moxi-demo")
    assert not os.path.lexists(other)
    assert place.status("loadout") == []
    assert "loadout: removed moxi-other for claude (demo is being uninstalled)" in said
    assert (folder / "loadout.toml").read_bytes() == manifest_before
    # The seeded hand-over has lapsed: a reinstall plans the skill as the module's own
    # add, and its question counts it.
    assert kit.handed("demo")[str(other)]["seeded"] is True
    step = place.plan("demo", [Item(demo / "moxi-other", "claude", "moxi-other")])[0]
    assert step.action == "add"


def test_a_failed_removal_at_module_uninstall_is_ended_by_the_next_install(demo, site,
                                                                          folder,
                                                                          monkeypatch):
    import shutil

    import moxi.modules
    from moxi import place

    kit.manifest(folder, '[user]\nskills = ["demo/moxi-demo"]\n'
                         '[skill."demo/moxi-demo"]\nharnesses = ["claude"]\n')
    assert install.install().code == 0
    target = kit.claude() / "moxi-demo"
    real_apply = place.apply
    monkeypatch.setattr(place, "apply", lambda steps, **kwargs: [])
    with pytest.raises(RuntimeError) as raised:
        listener.on_uninstall("demo", say=lambda line: None)
    assert str(target) in str(raised.value)
    assert "moxi loadout install removes it" in str(raised.value)
    monkeypatch.setattr(place, "apply", real_apply)
    # The package goes; the link it left dangles.
    for path in site.root.iterdir():
        if path.name.startswith("moxi_demo"):
            shutil.rmtree(path)
    moxi.modules.reset()
    assert os.path.islink(target) and not os.path.exists(target)
    before = install.status()
    assert before.code == 1
    assert any(r.target == str(target) and r.state == "stale" for r in before.rows)
    assert any(r.target == str(target) and
               r.reason == "demo is not installed; install removes the placement"
               for r in before.rows)
    out = install.install()
    assert out.code == 0, out.text()
    assert not os.path.lexists(target)
    assert place.status("loadout") == []
    assert [r.state for r in out.rows] == ["off"]
    assert install.status().code == 0


def test_a_module_uninstall_with_a_lost_ledger_still_finds_its_skills(demo, folder):
    module_install(demo)
    assert install.install().code == 0
    stores.ledger_path().unlink()
    listener.on_uninstall("demo", say=lambda line: None)
    assert not os.path.lexists(kit.claude() / "moxi-demo")


def test_the_uninstall_listener_leaves_the_users_own_skill_of_that_name(demo, folder):
    kit.skill(folder / "skills", "moxi-demo")
    kit.manifest(folder, '[user]\nskills = ["moxi-demo"]\n[skill.moxi-demo]\n'
                         'harnesses = ["claude"]\n')
    assert install.install().code == 0
    listener.on_uninstall("demo", say=lambda line: None)
    assert os.readlink(kit.claude() / "moxi-demo") == str(folder / "skills" / "moxi-demo")


def test_the_listener_adopts_while_a_cli_install_waits_and_neither_times_out(demo, site,
                                                                             folder):
    from moxi import files, paths

    module_install(demo)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [str(site.root)] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep)
                            if p]))
    with files.lock(paths.core_lock()):
        child = subprocess.Popen([sys.executable, "-m", "moxi_loadout", "install"],
                                 env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True)
        try:
            deadline = time.monotonic() + 30
            waiting = child.stderr.readline()
            assert "waiting for an install or uninstall" in waiting, waiting
            assert time.monotonic() < deadline
            listener.on_install("demo", found(), say=lambda line: None)
            assert kit.owner_of(kit.claude() / "moxi-demo") == "loadout"
        except BaseException:
            child.kill()
            child.communicate()
            raise
    out, err = child.communicate(timeout=60)
    assert child.returncode == 0, out + err
    from moxi_loadout import ledger

    record = ledger.read()["placed"][str(kit.claude() / "moxi-demo")]
    assert record["module"] == "demo" and record["seeded"] is False
