"""Module skills held by loadout: a module's own placement taken over, one the manifest
lists placed as the module and taken over seeded, and the toggles."""

from __future__ import annotations

import os
import signal

import loadout_kit as kit
import pytest

from moxi_loadout import adoption, install, ledger
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


def item(skills, name="moxi-demo", harness="claude"):
    from moxi.place import Item

    return Item(skills / name, harness, name)


def place_as_module(skills, *names):
    """What the module's own skills step does after a yes."""
    from moxi import place

    items = [item(skills, n) for n in names or ("moxi-demo",)]
    place.apply(place.plan("demo", items), say=lambda line: None)


def module_step(skills, name="moxi-demo"):
    from moxi import place

    return place.plan("demo", [item(skills, name)])[0]


def test_a_modules_own_placement_is_taken_over_unseeded(demo, folder):
    place_as_module(demo)
    out = install.install()
    assert out.code == 0, out.text()
    target = kit.claude() / "moxi-demo"
    assert kit.owner_of(target) == "loadout"
    entry = next(iter(kit.handed("demo").values()))
    assert entry["to"] == "loadout" and not entry.get("seeded")
    found = load(folder)
    assert found.user.names("skill") == ("demo/moxi-demo",)
    assert found.harnesses("skill", "demo/moxi-demo") == ("claude",)
    record = next(iter(ledger.read()["placed"].values()))
    assert record["module"] == "demo" and record["seeded"] is False
    assert module_step(demo).action == "skip"
    assert install.status().code == 0


def test_a_skill_the_manifest_lists_is_placed_as_the_module_and_seeded(demo, folder):
    kit.manifest(folder, '[user]\nskills = ["demo/moxi-demo"]\n'
                         '[skill."demo/moxi-demo"]\nharnesses = ["claude"]\n')
    before = (folder / "loadout.toml").read_text()
    out = install.install()
    assert out.code == 0, out.text()
    target = kit.claude() / "moxi-demo"
    assert os.readlink(target) == str(demo / "moxi-demo")
    assert kit.owner_of(target) == "loadout"
    assert next(iter(kit.handed("demo").values()))["seeded"] is True
    record = next(iter(ledger.read()["placed"].values()))
    assert record["seeded"] is True and record["module"] == "demo"
    # The module's next install skips it, and would ask about the rest.
    assert module_step(demo).action == "skip"
    assert module_step(demo, "moxi-other").action == "add"
    assert (folder / "loadout.toml").read_text() == before
    # A second run changes nothing.
    assert install.install().code == 0
    assert install.status().code == 0


def test_the_modules_add_refused_adopts_nothing(demo, folder):
    kit.manifest(folder, '[user]\nskills = ["demo/moxi-demo"]\n'
                         '[skill."demo/moxi-demo"]\nharnesses = ["claude"]\n')
    kit.claude().mkdir(parents=True)
    (kit.claude() / "moxi-demo").mkdir()
    out = install.install()
    assert out.code == 3
    assert [r.state for r in out.rows] == ["blocked"]
    assert kit.handed("demo") == {}
    assert kit.owner_of(kit.claude() / "moxi-demo") is None


@pytest.mark.parametrize("form", ["id", "path"])
def test_replace_on_a_module_skill_places_it_as_the_module_seeded(demo, folder, form):
    from moxi_loadout import teardown

    kit.manifest(folder, '[user]\nskills = ["demo/moxi-demo"]\n'
                         '[skill."demo/moxi-demo"]\nharnesses = ["claude"]\n')
    target = kit.claude() / "moxi-demo"
    kit.claude().mkdir(parents=True)
    target.mkdir()
    (target / "mine.txt").write_text("in the way\n")
    assert install.install().code == 3
    named = "skill:demo/moxi-demo@claude" if form == "id" else str(target)
    out = install.install(replace=[named])
    assert out.code == 0, out.text()
    assert os.readlink(target) == str(demo / "moxi-demo")
    assert kit.owner_of(target) == "loadout"
    assert kit.handed("demo")[str(target)]["seeded"] is True
    assert ledger.read()["placed"][str(target)]["seeded"] is True
    assert module_step(demo).action == "skip"
    assert install.status().code == 0
    # At loadout's uninstall the skill is removed, never handed to the module.
    lines = []
    assert teardown.teardown(say=lines.append) == 0, lines
    assert not any("back to demo" in line for line in lines)
    assert not os.path.lexists(target)
    assert kit.owner_of(target) is None


def test_a_failed_hand_over_undoes_the_placement(demo, folder, monkeypatch):
    from moxi import place

    kit.manifest(folder, '[user]\nskills = ["demo/moxi-demo"]\n'
                         '[skill."demo/moxi-demo"]\nharnesses = ["claude"]\n')

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(place, "adopt", broken)
    out = install.install()
    assert out.code == 1
    assert any("the placement was undone" in r.reason for r in out.rows)
    assert not os.path.lexists(kit.claude() / "moxi-demo")
    assert place.status("demo") == []


def test_a_failed_undo_names_the_target(demo, folder, monkeypatch):
    from moxi import place

    kit.manifest(folder, '[user]\nskills = ["demo/moxi-demo"]\n'
                         '[skill."demo/moxi-demo"]\nharnesses = ["claude"]\n')
    real_apply = place.apply

    def broken(*args, **kwargs):
        raise OSError("disk full")

    def apply(steps, **kwargs):
        if any(s.action == "remove" for s in steps):
            raise OSError("read-only")
        return real_apply(steps, **kwargs)

    monkeypatch.setattr(place, "adopt", broken)
    monkeypatch.setattr(place, "apply", apply)
    out = install.install()
    assert out.code == 1
    target = str(kit.claude() / "moxi-demo")
    assert any(target in r.reason and "still placed" in r.reason for r in out.rows)


def test_a_module_skill_whose_module_is_absent_reads_off(site, folder):
    kit.configure(folder)
    kit.manifest(folder, '[user]\nskills = ["gone/moxi-gone"]\n')
    out = install.install()
    assert out.code == 0
    assert {r.state for r in out.rows} == {"off"}
    assert "gone is not installed" in out.rows[0].reason


def test_a_module_skill_whose_module_is_broken_reads_broken(site, folder, monkeypatch):
    from moxi import modules

    kit.configure(folder)
    kit.manifest(folder, '[user]\nskills = ["bent/moxi-bent"]\n')
    found = modules.Found(name="bent", state=modules.BROKEN, reason="its announcement fails")
    monkeypatch.setattr(modules, "load", lambda name: found)
    out = install.install()
    assert out.code == 0
    assert {r.state for r in out.rows} == {"off"}
    assert out.rows[0].reason == "bent is broken"


def test_disable_then_enable_an_adopted_skill(demo, folder):
    from moxi_loadout import writes

    place_as_module(demo)
    install.install()
    target = kit.claude() / "moxi-demo"
    writes.disable("skill:demo/moxi-demo@claude")
    assert install.install().code == 0
    assert not os.path.lexists(target)
    # The module's hand-over has lapsed: its own plan would place the skill again.
    assert module_step(demo).action == "add"
    writes.enable("skill:demo/moxi-demo@claude")
    assert install.install().code == 0
    assert kit.owner_of(target) == "loadout"
    assert next(iter(kit.handed("demo").values()))["seeded"] is True


def test_the_users_own_skill_wins_its_name_over_a_module_skill(demo, folder):
    kit.skill(folder / "skills", "moxi-demo")
    kit.manifest(folder, '[user]\nskills = ["moxi-demo", "demo/moxi-demo"]\n'
                         '[skill."demo/moxi-demo"]\nharnesses = ["claude"]\n')
    out = install.install()
    assert out.code == 3
    rows = {(r.name, r.harness): r for r in out.rows}
    assert rows["demo/moxi-demo", "claude"].state == "blocked"
    assert os.readlink(kit.claude() / "moxi-demo") == str(folder / "skills" / "moxi-demo")


def test_take_lists_every_module_placement_in_one_write(demo, folder):
    place_as_module(demo, "moxi-demo", "moxi-other")
    taken = adoption.take(folder=folder, say=lambda line: None)
    assert sorted(t.placed.name for t in taken) == ["moxi-demo", "moxi-other"]
    assert load(folder).user.names("skill") == ("demo/moxi-demo", "demo/moxi-other")
    assert not os.path.lexists(folder / "loadout.toml.lock")


def test_take_leaves_a_placement_the_module_made_with_dest(demo, folder, tmp_path):
    from moxi import place

    dest = tmp_path / "demo-data" / "moxi-demo"
    mine = item(demo)
    there = place.Item(mine.source, "claude", "moxi-demo", dest=dest)
    place.apply(place.plan("demo", [mine, there], form="copy"), say=lambda line: None)
    by_path = {str(p.path): p for p in place.status("demo")}
    at_folder = by_path[place.target_key(kit.claude() / "moxi-demo")]
    at_dest = by_path[place.target_key(dest)]
    assert adoption.at_its_folder(at_folder) and not adoption.at_its_folder(at_dest)
    taken = adoption.take(["demo"], folder=folder, say=lambda line: None)
    assert [str(t.placed.path) for t in taken] == [str(at_folder.path)]
    assert kit.owner_of(dest) == "demo"
    assert load(folder).harnesses("skill", "demo/moxi-demo") == ("claude",)


def test_a_placement_whose_harness_row_is_gone_is_not_at_its_folder():
    from pathlib import Path

    from moxi.place import Placed

    placed = Placed("demo", "skill", "nowhere", "moxi-demo", Path("/x/moxi-demo"), "link",
                    "ok")
    assert not adoption.at_its_folder(placed, {})


def test_signals_wait_for_the_span_to_end():
    seen = []
    with pytest.raises(KeyboardInterrupt):
        with adoption.deferred_signals():
            signal.raise_signal(signal.SIGINT)
            seen.append("still running")
    assert seen == ["still running"]
