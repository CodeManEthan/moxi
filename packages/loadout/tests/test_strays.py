"""Strays: what stands in a harness folder loadout places into that no placement holds,
listed by status, taken in by `take` and moved aside by `move-aside`."""

from __future__ import annotations

import json
import os
import subprocess

import loadout_kit as kit
import pytest

from moxi_loadout import cli, displaced, install, strays
from moxi_loadout.planner import Scope


@pytest.fixture
def lo(loadout_dir):
    kit.configure(loadout_dir)
    kit.skill(loadout_dir / "skills", "review")
    kit.manifest(loadout_dir, '[user]\nskills = ["review"]\n')
    out = install.install()
    assert out.code == 0, out.text()
    return loadout_dir


def hand_skill(skills_dir, name="mine", body="mine\n"):
    target = skills_dir / name
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Mine.\n---\n{body}")
    (target / "notes.txt").write_text("my notes\n")
    return target


def paths_of(outcome) -> list[str]:
    return [s["path"] for s in outcome.as_json()["strays"]]


def test_hand_placed_entries_are_strays_and_placements_are_not(lo, tmp_path):
    from moxi import place

    skill_dir = hand_skill(kit.claude())
    agent = kit.agent(kit.claude("agents") / "helper.md", "helper")
    (kit.claude() / ".hidden").mkdir()
    src = kit.skill(tmp_path / "module-src", "comms-send")
    place.apply(place.plan("comms", [place.Item(src, "codex", "comms-send")]),
                say=lambda line: None)
    out = install.status()
    assert out.code == 0
    found = out.as_json()["strays"]
    assert {"path": os.fspath(skill_dir), "harness": "claude", "kind": "skill"} in found
    assert {"path": os.fspath(agent), "harness": "claude", "kind": "agent"} in found
    assert len(found) == 2, found
    text = out.text()
    at = text.index(install.STRAYS_HEADING)
    assert text[at + 1:at + 3] == [f"  {skill_dir}", f"  {agent}"]


def test_a_scoped_status_lists_its_sections_strays_only(lo, tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    kit.manifest(lo, f'[user]\nskills = ["review"]\n[project."{root}"]\nskills = []\n')
    hand_skill(kit.claude())
    theirs = hand_skill(root / ".claude" / "skills", "theirs")
    assert paths_of(install.status(Scope(projects=(str(root),)))) == [os.fspath(theirs)]
    assert os.fspath(theirs) not in paths_of(install.status(Scope(user=True)))
    assert paths_of(install.status(Scope(item=("skill", "review")))) == []


@pytest.mark.slow
def test_an_entry_git_tracks_is_no_stray(lo, tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")
           or k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, env=env)
    kit.manifest(lo, f'[user]\nskills = ["review"]\n[project."{root}"]\nskills = []\n')
    hand_skill(root / ".claude" / "skills", "committed")
    loose = hand_skill(root / ".claude" / "skills", "loose")
    subprocess.run(["git", "-C", str(root), "add", ".claude/skills/committed"], check=True,
                   env=env)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "a skill"], check=True,
                   env=env)
    assert paths_of(install.status(Scope(projects=(str(root),)))) == [os.fspath(loose)]


def test_take_lists_and_places_it_keeping_the_original(lo):
    target = hand_skill(kit.claude())
    out = strays.take(target)
    assert out.code == 0, out.text()
    assert out.lines[0] == f"copied {target} to {lo / 'skills' / 'mine'}"
    assert (lo / "skills" / "mine" / "notes.txt").read_text() == "my notes\n"
    assert '"mine"' in (lo / "loadout.toml").read_text()
    assert kit.owner_of(target) == "loadout"
    assert (target / "notes.txt").read_text() == "my notes\n"
    [kept] = [k for k in out.kept if k["target"] == os.fspath(target)]
    assert kept["how"] == "replaced"
    assert open(os.path.join(kept["copy"], "notes.txt")).read() == "my notes\n"
    assert os.fspath(target) not in paths_of(install.status())


def test_take_an_agent_file(lo):
    target = kit.agent(kit.claude("agents") / "helper.md", "helper")
    out = strays.take(target)
    assert out.code == 0, out.text()
    assert (lo / "agents" / "helper.md").read_text() == target.read_text()
    assert kit.owner_of(target) == "loadout"


def test_take_refuses_a_path_that_is_no_stray_and_a_name_loadout_holds(lo, tmp_path):
    with pytest.raises(strays.Refused, match="no stray"):
        strays.take(kit.claude() / "review")
    elsewhere = hand_skill(tmp_path / "elsewhere")
    with pytest.raises(strays.Refused, match="no stray"):
        strays.take(elsewhere)
    target = hand_skill(kit.claude())
    kit.skill(lo / "skills", "mine")
    with pytest.raises(strays.Refused, match="holds skill mine already"):
        strays.take(target)
    assert not os.path.islink(target) and (target / "notes.txt").exists()


def test_move_aside_empties_the_target_and_its_back_command_restores_it(lo):
    target = hand_skill(kit.claude())
    kept = strays.move_aside(target)
    assert not os.path.lexists(target)
    assert kept["how"] == "moved" and kept["target"] == os.fspath(target)
    assert kept["copy"].startswith(os.fspath(displaced.root()))
    assert kept["back"] == displaced.back("moved", kept["copy"], target)
    subprocess.run(kept["back"], shell=True, check=True)
    assert (target / "notes.txt").read_text() == "my notes\n"


def test_the_cli_verbs(lo, capsys):
    target = hand_skill(kit.claude())
    assert cli.main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["strays"] == [
        {"path": os.fspath(target), "harness": "claude", "kind": "skill"}]
    assert cli.main(["move-aside", str(target)]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[0].startswith(f"moved {target} to ")
    assert printed[1].startswith("to put it back: ")
    assert cli.main(["move-aside", str(target)]) == 3
    assert "is not there" in capsys.readouterr().err
    target = hand_skill(kit.claude(), "other")
    assert cli.main(["take", str(target), "--json"]) == 0
    assert kit.owner_of(target) == "loadout"
