"""The skill placer, `moxi.place`: plan, apply, status, remove and adopt.

Every test places into folders under tmp: HOME points there, CLAUDE_CONFIG_DIR and
CODEX_HOME are unset or point there, and the core's own roots already do (conftest), so no
real harness folder is ever read or written.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import moxi.config
import moxi.files
import moxi.paths
from conftest import FOLDER_LINK, WINDOWS, is_link, link_folder, link_text
from moxi import place
from moxi.place import Item
from moxi_dev.testing import can_symlink, need_symlinks


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A home of its own, so `~/.claude`, `~/.agents` and `~/.codex` are under tmp."""
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # what `~` reads on Windows
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    return home


@pytest.fixture(autouse=True)
def folder_links():
    """On Windows `auto` copies a skill folder (`place._AUTO_JUNCTION`), so the tests here
    that place one as a link set `link`, which makes a junction there; elsewhere `auto`
    links already, and nothing is set."""
    if WINDOWS:
        set_core("skills.form", "link")


@pytest.fixture
def git(tmp_path, monkeypatch):
    """A function that runs git with no global or system config of the user's, here and
    in the placer; skips without git."""
    if shutil.which("git") is None:
        pytest.skip("no git")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    def run(*args):
        subprocess.run(["git", *map(str, args)], check=True, capture_output=True)
    return run


@pytest.fixture
def skill(tmp_path):
    """A skill folder in a package-like place, and a function that makes more."""
    def make(name: str = "comms-send", text: str = "# send\n", base: Path | None = None):
        folder = (base or tmp_path / "pkg" / "skills") / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(text)
        (folder / "scripts").mkdir()
        (folder / "scripts" / "run.sh").write_text("echo hi\n")
        return folder
    return make


def lines():
    said: list[str] = []
    return said, said.append


def ledger() -> dict:
    path = moxi.paths.core().state_dir() / place.LEDGER
    return json.loads(path.read_text()) if path.exists() else {}


def one(steps, action=None):
    assert len(steps) == 1, steps
    if action is not None:
        assert steps[0].action == action, steps[0]
    return steps[0]


def set_core(key, value):
    moxi.config.set("moxi", key, value, by="test")


# -- targets and the harness table --------------------------------------------------------

def test_a_skill_links_into_each_default_harness_folder(home, skill):
    src = skill()
    items = [Item(src, "claude", "comms-send"), Item(src, "codex", "comms-send")]
    steps = place.plan("comms", items)
    assert [s.action for s in steps] == ["add", "add"]
    assert [s.form for s in steps] == [FOLDER_LINK, FOLDER_LINK]
    said, say = lines()
    done = place.apply(steps, say=say)
    claude = home / ".claude" / "skills" / "comms-send"
    codex = home / ".agents" / "skills" / "comms-send"
    assert link_text(claude) == str(src) and link_text(codex) == str(src)
    assert [p.state for p in done] == ["ok", "ok"]
    assert said == []
    assert [s.action for s in place.plan("comms", items)] == ["keep", "keep"]


def test_an_agent_goes_to_the_agents_folder_as_one_file(home, tmp_path):
    need_symlinks()
    agent = tmp_path / "pkg" / "reviewer.md"
    agent.parent.mkdir()
    agent.write_text("an agent\n")
    items = [Item(agent, "claude", "reviewer.md", kind="agent"),
             Item(agent, "codex", "reviewer.md", kind="agent")]
    place.apply(place.plan("loadout", items))
    assert os.readlink(home / ".claude" / "agents" / "reviewer.md") == str(agent)
    assert os.readlink(home / ".codex" / "agents" / "reviewer.md") == str(agent)
    entry = ledger()["loadout"]["placed"][str(home / ".claude" / "agents" / "reviewer.md")]
    assert entry["kind"] == "agent" and entry["harness"] == "claude"


def test_claude_config_dir_moves_claudes_folders(tmp_path, monkeypatch, skill):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    step = one(place.plan("comms", [Item(skill(), "claude", "comms-send")]))
    assert step.target == tmp_path / "cc" / "skills" / "comms-send"


def test_codex_home_moves_codexs_agents_folder_and_not_its_skills(home, tmp_path,
                                                                   monkeypatch, skill):
    need_symlinks()
    agent = tmp_path / "pkg" / "reviewer.md"
    agent.parent.mkdir()
    agent.write_text("an agent\n")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "cx"))
    rows, _ = place.harnesses()
    assert rows["codex"].agents == tmp_path / "cx" / "agents"
    assert rows["codex"].skills == home / ".agents" / "skills"
    items = [Item(agent, "codex", "reviewer.md", kind="agent"),
             Item(skill(), "codex", "comms-send")]
    place.apply(place.plan("loadout", items))
    assert os.readlink(tmp_path / "cx" / "agents" / "reviewer.md") == str(agent)
    assert is_link(home / ".agents" / "skills" / "comms-send")
    assert not os.path.lexists(home / ".codex")


def test_a_dest_places_exactly_there_and_a_relative_one_raises(tmp_path, skill):
    src = skill()
    dest = tmp_path / "proj" / ".claude" / "skills" / "x"
    place.apply(place.plan("loadout", [Item(src, "claude", "x", dest=dest)]))
    assert link_text(dest) == str(src)
    with pytest.raises(ValueError, match="absolute"):
        place.plan("loadout", [Item(src, "claude", "x", dest=Path("rel/x"))])


def test_wrong_calls_raise_and_nothing_else_does(skill):
    src = skill()
    with pytest.raises(TypeError):
        place.plan("comms", [("not", "an", "item")])
    with pytest.raises(ValueError, match="kind"):
        place.plan("comms", [Item(src, "claude", "x", kind="hook")])
    with pytest.raises(ValueError, match="form"):
        place.plan("comms", [Item(src, "claude", "x")], form="hardlink")


def test_bad_rows_are_skipped_with_a_line_and_their_items_refused(skill):
    set_core("harnesses", [
        {"name": "claude", "skills": "", "agents": ""},
        {"name": "claude", "skills": "/x", "agents": ""},
        {"name": "Bad Name", "skills": "/y", "agents": ""},
        {"name": "odd", "skills": 3, "agents": ""},
        {"name": "rel", "skills": "relative/skills", "agents": ""},
        {"name": "opencode", "skills": "~/.opencode/skills", "agents": ""},
    ])
    rows, skipped = place.harnesses()
    assert sorted(rows) == ["claude", "opencode"]
    assert len(skipped) == 4
    assert any("repeats" in line for line in skipped)
    assert rows["opencode"].skills == Path(os.path.expanduser("~/.opencode/skills"))
    assert rows["opencode"].agents is None
    src = skill()
    steps = place.plan("comms", [Item(src, "odd", "x"),
                                 Item(src, "opencode", "x", kind="agent"),
                                 Item(src, "opencode", "x")])
    assert [(s.action, s.cause) for s in steps] == [
        ("refuse", "harness"), ("refuse", "harness"), ("add", None)]
    assert "no agents folder" in steps[1].reason


def test_a_skills_harnesses_name_with_no_row_is_skipped_with_one_line():
    set_core("skills.harnesses", ["claude", "opencode", "codex"])
    names, said = place.chosen()
    assert names == ["claude", "codex"]
    assert said == ["skills.harnesses names opencode, which has no row in harnesses"]


def test_a_name_that_is_not_one_path_part_is_refused(skill):
    step = one(place.plan("comms", [Item(skill(), "claude", "../escape")]), "refuse")
    assert step.cause == "name" and step.target is None


# -- the ledger key -----------------------------------------------------------------------

def test_one_owner_places_one_name_into_two_roots_and_drops_one(tmp_path, skill):
    src = skill()
    a = tmp_path / "proj-a" / ".claude" / "skills" / "x"
    b = tmp_path / "proj-b" / ".claude" / "skills" / "x"
    both = [Item(src, "claude", "x", dest=a), Item(src, "claude", "x", dest=b)]
    place.apply(place.plan("loadout", both))
    assert set(ledger()["loadout"]["placed"]) == {str(a), str(b)}
    steps = place.plan("loadout", both[:1])
    assert [(s.action, s.target) for s in steps] == [("keep", a), ("remove", b)]
    place.apply(steps)
    assert is_link(a) and not os.path.lexists(b)
    assert set(ledger()["loadout"]["placed"]) == {str(a)}


def test_two_items_with_one_target_refuse_the_second(skill):
    src, other = skill(), skill("other")
    steps = place.plan("comms", [Item(src, "claude", "x"), Item(other, "claude", "x")])
    assert [s.action for s in steps] == ["add", "refuse"]
    assert steps[1].cause == "duplicate" and "two items place" in steps[1].reason


def test_a_harness_folder_reached_through_a_symlink_gives_one_key(home, tmp_path, skill):
    real = tmp_path / "real-skills"
    real.mkdir()
    (home / ".claude").mkdir()
    link_folder(home / ".claude" / "skills", real)
    src = skill()
    place.apply(place.plan("comms", [Item(src, "claude", "x")]))
    assert list(ledger()["comms"]["placed"]) == [str(real / "x")]
    step = one(place.plan("comms", [Item(src, "claude", "x",
                                         dest=home / ".claude" / "skills" / "x")]), "keep")
    assert step.target == real / "x"


# -- copies, updates, occupants -----------------------------------------------------------

def test_a_copied_folder_carries_its_mark_and_updates_when_the_source_changes(home, skill):
    src = skill()
    item = Item(src, "claude", "comms-send")
    place.apply(place.plan("comms", [item], form="copy"))
    target = home / ".claude" / "skills" / "comms-send"
    mark = json.loads((target / place.MARK).read_text())
    assert mark["owner"] == "comms" and mark["source"] == str(src)
    assert mark["hash"] == mark["source_hash"] and "held" not in mark
    assert (target / "scripts" / "run.sh").read_text() == "echo hi\n"
    assert not list(target.parent.glob(".*.moxi-tmp"))
    one(place.plan("comms", [item], form="copy"), "keep")
    (src / "SKILL.md").write_text("# send, new\n")
    step = one(place.plan("comms", [item], form="copy"), "update")
    place.apply([step])
    assert (target / "SKILL.md").read_text() == "# send, new\n"
    assert [p.state for p in place.status("comms")] == ["ok"]


# -- volatile paths -----------------------------------------------------------------------

VOLATILE = ("endpoint.json", ".claude-plugin/types", "tsconfig.json")


@pytest.fixture
def plugin(tmp_path, skill):
    """A folder placed as a copy at a `dest` with volatile paths, as a module's `SETUP`
    places one; returns (source, dest, item)."""
    src = skill("plugin")
    (src / ".claude-plugin").mkdir()
    (src / ".claude-plugin" / "plugin.json").write_text('{"name": "p"}\n')
    dest = tmp_path / "data" / "turns" / "claude"
    item = Item(src, "claude", "claude", dest=dest, volatile=VOLATILE)
    place.apply(place.plan("turns", [item], form="copy"))
    return src, dest, item


def write_volatile(dest: Path) -> None:
    (dest / "endpoint.json").write_text('{"port": 1}\n')
    (dest / "tsconfig.json").write_text("{}\n")
    (dest / ".claude-plugin" / "types").mkdir()
    (dest / ".claude-plugin" / "types" / "a.d.ts").write_text("x\n")


def test_volatile_paths_are_kept_as_slash_separated_strings(skill):
    src = skill()
    item = Item(src, "claude", "x", volatile=[Path("a") / "b", "c/", "endpoint.json"])
    assert item.volatile == ("a/b", "c", "endpoint.json")
    assert Item(src, "claude", "x", volatile="endpoint.json").volatile == ("endpoint.json",)
    assert Item(src, "claude", "x").volatile == ()
    with pytest.raises(TypeError, match="volatile"):
        Item(src, "claude", "x", volatile=[3])


@pytest.mark.parametrize("bad", ["", ".", "/abs", "../out", "a/../../b", "C:/x"])
def test_a_volatile_path_outside_the_folder_raises(skill, bad):
    with pytest.raises(ValueError, match="volatile"):
        place.plan("turns", [Item(skill(), "claude", "x", volatile=[bad])])


def test_writes_to_volatile_paths_never_read_as_a_change(plugin):
    src, dest, item = plugin
    mark = json.loads((dest / place.MARK).read_text())
    assert mark["volatile"] == list(VOLATILE)
    write_volatile(dest)
    assert [p.state for p in place.status("turns")] == ["ok"]
    one(place.plan("turns", [item], form="copy"), "keep")
    place.apply(place.plan("turns", [item], form="copy"))
    assert (dest / "endpoint.json").read_text() == '{"port": 1}\n'


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_a_read_only_source_copies_writable_and_removes(tmp_path, skill):
    """A source in a read-only install: its owner still writes into the copy, and the
    copy still goes at removal. The source stays as it was."""
    src = skill("plugin")
    (src / "sub").mkdir()
    (src / "sub" / "f.txt").write_text("x\n")
    paths_ = [src, *src.rglob("*")]
    modes = {p: p.stat().st_mode for p in paths_}
    for p in paths_:
        p.chmod(0o555 if p.is_dir() else 0o444)
    try:
        dest = tmp_path / "data" / "turns" / "claude"
        item = Item(src, "claude", "claude", dest=dest, volatile=("sub/endpoint.json",))
        place.apply(place.plan("turns", [item], form="copy"))
        (dest / "sub" / "endpoint.json").write_text("{}\n")
        (dest / "sub" / "f.txt").write_text("x\n")
        assert [p.state for p in place.status("turns")] == ["ok"]
        assert [p.state for p in place.remove("turns", say=lambda line: None)] == ["ok"]
        assert not dest.exists()
        assert all(p.stat().st_mode & 0o222 == 0 for p in paths_)
    finally:
        for p, mode in modes.items():
            p.chmod(mode)


def test_a_change_outside_the_volatile_paths_still_reads_modified(plugin):
    src, dest, item = plugin
    write_volatile(dest)
    (dest / ".claude-plugin" / "plugin.json").write_text("edited\n")
    assert [p.state for p in place.status("turns")] == ["modified"]
    assert one(place.plan("turns", [item], form="copy"), "refuse").cause == "occupant"


def test_a_source_change_updates_the_whole_copy_volatile_paths_too(plugin):
    src, dest, item = plugin
    write_volatile(dest)
    (src / "SKILL.md").write_text("# new\n")
    assert [p.state for p in place.status("turns")] == ["stale"]
    place.apply([one(place.plan("turns", [item], form="copy"), "update")])
    assert (dest / "SKILL.md").read_text() == "# new\n"
    assert not (dest / "endpoint.json").exists(), "the caller writes it again"
    assert [p.state for p in place.status("turns")] == ["ok"]


def test_a_volatile_path_in_the_source_is_left_out_of_its_hash_too(plugin):
    src, dest, item = plugin
    (src / "endpoint.json").write_text("old\n")
    assert [p.state for p in place.status("turns")] == ["ok"]
    one(place.plan("turns", [item], form="copy"), "keep")


def test_other_volatile_paths_plan_an_update(plugin):
    src, dest, item = plugin
    other = Item(src, "claude", "claude", dest=dest, volatile=["endpoint.json"])
    assert one(place.plan("turns", [other], form="copy"), "update").reason == \
        "claude: volatile changed"
    place.apply(place.plan("turns", [other], form="copy"))
    assert json.loads((dest / place.MARK).read_text())["volatile"] == ["endpoint.json"]
    write_volatile(dest)
    assert [p.state for p in place.status("turns")] == ["modified"]


def test_remove_deletes_a_copy_with_volatile_writes_and_moves_nothing_aside(plugin):
    src, dest, item = plugin
    write_volatile(dest)
    said, say = lines()
    gone = place.remove("turns", say=say)
    assert [p.state for p in gone] == ["ok"] and said == []
    assert not dest.exists()
    assert not (moxi.paths.core().data_dir() / "displaced").exists()


def test_a_copy_without_volatile_reads_any_write_as_modified(home, skill):
    src = skill()
    item = Item(src, "claude", "x")
    place.apply(place.plan("comms", [item], form="copy"))
    target = home / ".claude" / "skills" / "x"
    assert "volatile" not in json.loads((target / place.MARK).read_text())
    (target / "endpoint.json").write_text("{}\n")
    assert [p.state for p in place.status("comms")] == ["modified"]


def test_a_copied_file_is_marked_by_its_ledger_entry(home, tmp_path):
    agent = tmp_path / "reviewer.md"
    agent.write_text("v1\n")
    item = Item(agent, "claude", "reviewer.md", kind="agent")
    place.apply(place.plan("loadout", [item], form="copy"))
    target = home / ".claude" / "agents" / "reviewer.md"
    assert not target.is_symlink() and target.read_text() == "v1\n"
    entry = ledger()["loadout"]["placed"][str(target)]
    assert entry["form"] == "copy" and entry["hash"] and entry["source_hash"]
    one(place.plan("loadout", [item], form="copy"), "keep")
    target.write_text("edited\n")
    step = one(place.plan("loadout", [item], form="copy"), "refuse")
    assert step.cause == "occupant" and "modified" in step.reason
    assert place.status("loadout")[0].state == "modified"


def test_the_form_changing_plans_an_update(home, skill):
    src = skill()
    item = Item(src, "claude", "comms-send")
    place.apply(place.plan("comms", [item]))
    set_core("skills.form", "copy")
    step = one(place.plan("comms", [item]), "update")
    assert step.form == "copy"
    place.apply([step])
    target = home / ".claude" / "skills" / "comms-send"
    assert target.is_dir() and not target.is_symlink()
    one(place.plan("comms", [item], form="link"), "update")


def test_an_unmarked_path_is_refused_and_replace_moves_it_aside(home, skill):
    target = home / ".claude" / "skills" / "comms-send"
    target.mkdir(parents=True)
    (target / "mine.txt").write_text("the user's\n")
    src = skill()
    step = one(place.plan("comms", [Item(src, "claude", "comms-send")]), "refuse")
    assert step.cause == "occupant" and step.occupant == target
    said, say = lines()
    assert place.apply([step], say=say) == []
    assert (target / "mine.txt").exists() and "not placed" in said[0]
    said, say = lines()
    done = place.apply([step], replace=True, say=say)
    assert [p.state for p in done] == ["ok"] and link_text(target) == str(src)
    displaced = moxi.paths.core().data_dir() / "displaced"
    moved = list(displaced.glob("*/claude/comms-send/mine.txt"))
    assert len(moved) == 1 and moved[0].read_text() == "the user's\n"


def test_an_unmarked_link_is_refused(home, tmp_path, skill):
    target = home / ".claude" / "skills" / "comms-send"
    target.parent.mkdir(parents=True)
    link_folder(target, tmp_path)
    step = one(place.plan("comms", [Item(skill(), "claude", "comms-send")]), "refuse")
    assert step.cause == "occupant" and "did not make" in step.reason


def test_another_owners_placement_is_refused_and_replace_leaves_it(home, skill):
    src = skill()
    place.apply(place.plan("loadout", [Item(src, "claude", "x")]))
    step = one(place.plan("comms", [Item(src, "claude", "x")]), "refuse")
    assert step.cause == "owner"
    said, say = lines()
    place.apply([step], replace=True, say=say)
    assert list(ledger()) == ["loadout"]


def test_a_path_git_tracks_is_named_and_git_is_asked_once_per_folder(tmp_path, monkeypatch,
                                                                      skill):
    if shutil.which("git") is None:
        pytest.skip("no git")
    repo = tmp_path / "repo"
    skills = repo / ".claude" / "skills"
    for name in ("a", "b"):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text("tracked\n")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
    calls = []
    real = place._git_files
    monkeypatch.setattr(place, "_git_files",
                        lambda folder, *rest: calls.append(folder) or real(folder, *rest))
    src = skill()
    steps = place.plan("loadout", [Item(src, "claude", n, dest=skills / n) for n in "ab"])
    assert [(s.action, s.cause) for s in steps] == [("refuse", "tracked")] * 2
    assert all("git tracks it" in s.reason for s in steps)
    assert calls == [str(skills)]


def test_a_tracked_occupant_is_cause_tracked_when_git_answers_in_the_time_given(
        tmp_path, git, skill):
    repo = tmp_path / "repo"
    target = repo / ".claude" / "skills" / "x"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("the project's\n")
    git("init", "-q", repo)
    git("-C", repo, "add", ".")
    step = one(place.plan("loadout", [Item(skill(), "claude", "x", dest=target)],
                          git_seconds=5), "refuse")
    assert (step.cause, step.reason) == (
        "tracked", f"{target} is a folder the placer did not mark, and git tracks it")


@pytest.mark.timed
@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in git is a shell script")
@pytest.mark.parametrize("how", ["git_seconds", "each call"])
def test_an_occupant_stays_cause_occupant_when_git_does_not_answer(
        tmp_path, monkeypatch, skill, how):
    """A git that doesn't answer in time never makes an occupant `tracked`: it stays
    `occupant`, its reason says so, and `git_seconds` bounds every folder's calls
    together."""
    bin_dir = tmp_path / "slow-bin"
    bin_dir.mkdir()
    (bin_dir / "git").write_text("#!/bin/sh\nexec sleep 30\n")
    (bin_dir / "git").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    targets = []
    for name in ("one", "two"):
        target = tmp_path / name / ".claude" / "skills" / "x"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("the user's\n")
        targets.append(target)
    items = [Item(skill(), "claude", "x", dest=targets[0]),
             Item(skill("y"), "claude", "x", dest=targets[1])]
    if how == "git_seconds":
        options = {"git_seconds": 0.5}
    else:
        monkeypatch.setattr(place, "_GIT_SECONDS", 0.5)
        options = {}
    start = time.monotonic()
    steps = place.plan("loadout", items, **options)
    took = time.monotonic() - start
    assert [(s.action, s.cause, s.occupant) for s in steps] == [
        ("refuse", "occupant", t) for t in targets]
    for s in steps:
        assert s.reason == (f"{s.target} is a folder the placer did not mark; git did not "
                            f"answer in time, so whether it tracks it is unknown")
    assert took < (2 if how == "git_seconds" else 4), took


def test_git_seconds_must_be_a_number(skill):
    with pytest.raises(ValueError, match="git_seconds"):
        place.plan("loadout", [Item(skill(), "claude", "x")], git_seconds=-1)


def _occupy(case, target, src, other):
    """Put occupant `case` at `target`, `src` being the item's source."""
    if case == "a link not the placer's":
        link_folder(target, other)
    elif case == "an unmarked folder":
        target.mkdir()
        (target / "SKILL.md").write_text("the user's\n")
    elif case == "a folder marked for another owner":
        shutil.copytree(src, target)
        (target / place.MARK).write_text(json.dumps({"owner": "tidy", "hash": "x"}))
    elif case == "a modified folder copy":
        place.apply(place.plan("loadout", [Item(src, "claude", "x", dest=target)],
                               form="copy"))
        (target / "SKILL.md").write_text("edited\n")
    elif case == "a file not the placer's":
        target.write_text("the user's\n")
    elif case == "a modified file copy":
        place.apply(place.plan("loadout", [Item(src, "claude", "x", kind="agent",
                                                dest=target)], form="copy"))
        target.write_text("edited\n")


OCCUPANTS = [("a link not the placer's", "skill", "a link the placer did not make"),
             ("an unmarked folder", "skill", "a folder the placer did not mark"),
             ("a folder marked for another owner", "skill", "a folder the placer did not mark"),
             ("a modified folder copy", "skill", "a placed copy that has been modified"),
             ("a file not the placer's", "agent", "a file the placer did not place"),
             ("a modified file copy", "agent", "a placed copy that has been modified")]


@pytest.mark.parametrize("case, kind, what", OCCUPANTS, ids=[c[0] for c in OCCUPANTS])
def test_an_occupant_git_tracks_is_cause_tracked_with_the_same_reason(
        tmp_path, git, skill, case, kind, what):
    """Each kind of occupant reads cause `occupant` while git does not track it and cause
    `tracked` once it does; the reason only gains ", and git tracks it", and `replace`
    moves it aside either way."""
    if kind == "skill":
        src = skill()
    else:
        src = tmp_path / "pkg" / "reviewer.md"
        src.parent.mkdir()
        src.write_text("an agent\n")
    repo = tmp_path / "repo"
    folder = repo / ".claude" / ("skills" if kind == "skill" else "agents")
    folder.mkdir(parents=True)
    git("init", "-q", repo)
    target = folder / "x"
    _occupy(case, target, src, tmp_path)
    item = Item(src, "claude", "x", kind=kind, dest=target)

    step = one(place.plan("loadout", [item], form="copy"), "refuse")
    assert (step.cause, step.reason, step.occupant) == ("occupant", f"{target} is {what}",
                                                       target)
    git("-C", repo, "add", ".")
    step = one(place.plan("loadout", [item], form="copy"), "refuse")
    assert (step.cause, step.reason, step.occupant) == (
        "tracked", f"{target} is {what}, and git tracks it", target)

    said, say = lines()
    assert place.apply([step], say=say) == []
    assert said == [f"x for claude not placed: {step.reason}"]
    said, say = lines()
    done = place.apply([step], replace=True, say=say)
    assert [(p.path, p.state) for p in done] == [(target, "ok")]
    assert said[0].startswith(f"moved {target} to ")
    moved = list((moxi.paths.core().data_dir() / "displaced").glob("*/claude/x"))
    assert len(moved) == 1
    one(place.plan("loadout", [item], form="copy"), "keep")


def test_a_marked_folder_this_ledger_lacks_is_cause_tracked(tmp_path, git, skill):
    """A copy with this owner's mark that the ledger has no entry for came with a commit:
    an occupant only because git tracks it, so always cause `tracked`."""
    src = skill()
    repo = tmp_path / "repo"
    target = repo / ".claude" / "skills" / "x"
    item = Item(src, "claude", "x", dest=target)
    place.apply(place.plan("loadout", [item], form="copy"))
    led = ledger()
    led["loadout"]["placed"].clear()
    (moxi.paths.core().state_dir() / place.LEDGER).write_text(json.dumps(led))
    git("init", "-q", repo)
    git("-C", repo, "add", ".")
    step = one(place.plan("loadout", [item], form="copy"), "refuse")
    assert (step.cause, step.reason) == (
        "tracked", f"{target} is a marked folder this ledger lacks, and git tracks it")
    said, say = lines()
    done = place.apply([step], replace=True, say=say)
    assert [p.path for p in done] == [target] and said[0].startswith(f"moved {target} to ")


def test_another_owners_placement_git_tracks_is_still_cause_owner(tmp_path, git, skill):
    src = skill()
    repo = tmp_path / "repo"
    target = repo / ".claude" / "skills" / "x"
    place.apply(place.plan("tidy", [Item(src, "claude", "x", dest=target)], form="copy"))
    git("init", "-q", repo)
    git("-C", repo, "add", ".")
    step = one(place.plan("loadout", [Item(src, "claude", "x", dest=target)]), "refuse")
    assert step.cause == "owner"
    assert "tracked" in place.CAUSES and "occupant" in place.CAUSES


# -- remove -------------------------------------------------------------------------------

def test_remove_unlinks_deletes_copies_and_moves_a_modified_copy_aside(home, tmp_path,
                                                                        skill):
    a, b, c = skill("a"), skill("b"), skill("c")
    place.apply(place.plan("comms", [Item(a, "claude", "a")]))
    place.apply(place.plan("comms", [Item(a, "claude", "a"), Item(b, "claude", "b"),
                                     Item(c, "claude", "c")], form="copy"))
    folder = home / ".claude" / "skills"
    place.apply(place.plan("comms", [Item(a, "claude", "a"), Item(b, "claude", "b"),
                                     Item(c, "claude", "c")], form="link")[:1])
    (folder / "c" / "SKILL.md").write_text("my edit\n")
    said, say = lines()
    gone = place.remove("comms", say=say)
    assert {p.name: p.state for p in gone} == {"a": "ok", "b": "ok", "c": "modified"}
    assert not any(os.path.lexists(folder / n) for n in "abc")
    assert (a / "SKILL.md").exists(), "a link's source is never removed"
    moved = list((moxi.paths.core().data_dir() / "displaced").glob("*/claude/c/SKILL.md"))
    assert moved and moved[0].read_text() == "my edit\n"
    assert any("modified" in line for line in said)
    assert ledger() == {}


def test_remove_leaves_a_path_that_is_no_longer_the_placement(home, tmp_path, skill):
    place.apply(place.plan("comms", [Item(skill(), "claude", "x")]))
    target = home / ".claude" / "skills" / "x"
    target.unlink()
    target.mkdir()
    said, say = lines()
    place.remove("comms", say=say)
    assert target.is_dir() and "left" in said[0]
    assert ledger() == {}


# -- the missing source and io ------------------------------------------------------------

@pytest.mark.parametrize("form", ["link", "copy"])
def test_a_missing_source_is_refused_and_its_placement_left(home, skill, form):
    gone, kept = skill("gone"), skill("kept")
    items = [Item(gone, "claude", "gone"), Item(kept, "claude", "kept")]
    place.apply(place.plan("comms", items, form=form))
    target = home / ".claude" / "skills" / "gone"
    before = os.readlink(target) if form == "link" else (target / "SKILL.md").read_text()
    shutil.rmtree(gone)
    steps = place.plan("comms", items, form=form)
    assert [(s.action, s.cause) for s in steps] == [("refuse", "source"), ("keep", None)]
    assert steps[0].reason == f"source missing: {gone}"
    said, say = lines()
    place.apply(steps, say=say)
    after = os.readlink(target) if form == "link" else (target / "SKILL.md").read_text()
    assert os.path.lexists(target) and after == before
    assert str(target) in ledger()["comms"]["placed"]
    assert {p.name: p.state for p in place.status("comms")} == {"gone": "stale",
                                                                "kept": "ok"}


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs a non-root POSIX")
def test_an_unreadable_file_in_a_source_is_that_items_io_refusal(skill):
    bad, good = skill("bad"), skill("good")
    secret = bad / "scripts" / "run.sh"
    secret.chmod(0)
    try:
        steps = place.plan("comms", [Item(bad, "claude", "bad"), Item(good, "claude", "good")])
    finally:
        secret.chmod(0o644)
    assert [(s.action, s.cause) for s in steps] == [("refuse", "io"), ("add", None)]
    assert "Permission denied" in steps[0].reason


# -- status -------------------------------------------------------------------------------

def test_status_reads_missing_stale_and_modified(home, skill):
    a, b, c = skill("a"), skill("b"), skill("c")
    place.apply(place.plan("comms", [Item(a, "claude", "a"), Item(b, "claude", "b")]))
    place.apply(place.plan("comms", [Item(a, "claude", "a"), Item(b, "claude", "b"),
                                     Item(c, "claude", "c")], form="copy")[2:])
    folder = home / ".claude" / "skills"
    (folder / "a").unlink()
    shutil.rmtree(b)
    (c / "SKILL.md").write_text("changed source\n")
    states = {p.name: p.state for p in place.status("comms")}
    assert states == {"a": "missing", "b": "stale", "c": "stale"}
    (folder / "c" / "SKILL.md").write_text("changed copy\n")
    assert {p.name: p.state for p in place.status("comms")}["c"] == "modified"


# -- held items ---------------------------------------------------------------------------

@pytest.fixture
def project(tmp_path, skill):
    root = tmp_path / "proj"
    src = skill("tidy", base=root / "skills")
    return root, src


def test_a_held_target_that_is_its_source_is_held_in_place(project):
    root, src = project
    item = Item(src, "claude", "tidy", dest=src, held=True, root=root)
    step = one(place.plan("loadout", [item]), "keep")
    assert step.reason == place.HELD_IN_PLACE
    place.apply([step])
    assert ledger()["loadout"]["placed"][str(src)]["form"] == place.IN_PLACE
    steps = place.plan("loadout", [])
    one(steps, "remove")
    place.apply(steps)
    assert (src / "SKILL.md").exists() and ledger() == {}


def test_a_held_items_links_are_relative_and_survive_the_project_moving(tmp_path, project):
    need_symlinks()
    root, src = project
    dest = root / ".claude" / "skills" / "tidy"
    item = Item(src, "claude", "tidy", dest=dest, held=True, root=root)
    one(place.plan("loadout", [item]), "add")
    place.apply(place.plan("loadout", [item]))
    assert os.readlink(dest) == os.path.join("..", "..", "skills", "tidy")
    one(place.plan("loadout", [item]), "keep")
    moved = tmp_path / "elsewhere" / "proj"
    shutil.copytree(root, moved, symlinks=True)
    link = moved / ".claude" / "skills" / "tidy"
    assert link.is_symlink() and (link / "SKILL.md").read_text() == "# send\n"
    # The moved tree's link resolves to its own source: held in place there.
    msrc = moved / "skills" / "tidy"
    steps = place.plan("loadout", [Item(msrc, "claude", "tidy", dest=link, held=True,
                                        root=moved)])
    assert [(s.action, s.reason) for s in steps][0] == ("keep", place.HELD_IN_PLACE)
    # The old tree's entry is no longer named, so it plans its removal.
    assert [(s.action, s.target) for s in steps[1:]] == [("remove", dest)]


def test_dropping_a_held_item_removes_its_links_and_leaves_its_source(project):
    root, src = project
    before = place._hash(str(src))
    dest = root / ".claude" / "skills" / "tidy"
    place.apply(place.plan("loadout", [Item(src, "claude", "tidy", dest=dest, held=True,
                                            root=root)]))
    place.apply(place.plan("loadout", []))
    assert not os.path.lexists(dest)
    assert place._hash(str(src)) == before


def test_a_held_item_with_copy_places_copies_marked_held(project):
    root, src = project
    before = place._hash(str(src))
    set_core("skills.form", "copy")
    dest = root / ".codex" / "skills" / "tidy"
    item = Item(src, "codex", "tidy", dest=dest, held=True, root=root)
    step = one(place.plan("loadout", [item]), "add")
    assert step.form == "copy"
    place.apply([step])
    mark = json.loads((dest / place.MARK).read_text())
    assert mark["held"] is True and mark["owner"] == "loadout"
    one(place.plan("loadout", [item]), "keep")
    place.apply(place.plan("loadout", []))
    assert not dest.exists() and place._hash(str(src)) == before
    need_symlinks()
    set_core("skills.form", "link")
    place.apply(place.plan("loadout", [item]))
    assert os.readlink(dest) == os.path.join("..", "..", "skills", "tidy")


def test_held_refusals(tmp_path, project, skill):
    root, src = project
    inside = root / ".claude" / "skills" / "tidy"
    outside = tmp_path / "other" / "tidy"
    elsewhere = skill("loose")
    steps = place.plan("loadout", [
        Item(src, "claude", "tidy", dest=outside, held=True, root=root),
        Item(src, "claude", "tidy", dest=inside, held=True),
        Item(elsewhere, "claude", "loose", dest=root / "x", held=True, root=root),
    ])
    assert [(s.action, s.cause) for s in steps] == [("refuse", "held")] * 3
    assert "outside the project" in steps[0].reason
    assert steps[1].reason == steps[2].reason == "held source is in no project"


# -- adopt --------------------------------------------------------------------------------

def test_a_skill_handed_to_loadout_and_back(home, skill):
    src = skill()
    item = Item(src, "claude", "comms-send")
    place.apply(place.plan("comms", [item], form="copy"))
    target = home / ".claude" / "skills" / "comms-send"
    moved = place.adopt("comms", "loadout", place.status("comms"))
    assert [(p.owner, p.path) for p in moved] == [("loadout", target)]
    assert json.loads((target / place.MARK).read_text())["owner"] == "loadout"
    led = ledger()
    assert led["comms"]["handed"] == {str(target): {"kind": "skill", "harness": "claude",
                                                    "name": "comms-send", "to": "loadout"}}
    assert "placed" not in led["comms"] or not led["comms"]["placed"]
    step = one(place.plan("comms", [item], form="copy"), "skip")
    assert step.reason == "comms-send was handed to loadout; skipped"
    assert place.remove("comms") == [] and target.exists()
    one(place.plan("loadout", [item], form="copy"), "keep")

    back = place.adopt("loadout", "comms", place.status("loadout"))
    assert [p.owner for p in back] == ["comms"]
    led = ledger()
    assert led["comms"]["placed"].keys() == {str(target)}
    assert not led["comms"].get("handed")
    assert json.loads((target / place.MARK).read_text())["owner"] == "comms"
    one(place.plan("comms", [item], form="copy"), "keep")
    gone = place.remove("comms")
    assert [p.path for p in gone] == [target] and not target.exists()


def test_a_hand_over_and_back_of_one_of_two_roots(tmp_path, skill):
    src = skill()
    a, b = tmp_path / "pa" / "x", tmp_path / "pb" / "x"
    place.apply(place.plan("comms", [Item(src, "claude", "x", dest=a),
                                     Item(src, "claude", "x", dest=b)]))
    first = [p for p in place.status("comms") if p.path == a]
    place.adopt("comms", "loadout", first)
    place.adopt("loadout", "comms", place.status("loadout"))
    led = ledger()
    owners = {key: [o for o, r in led.items() if key in r.get("placed", {})]
              for key in (str(a), str(b))}
    assert owners == {str(a): ["comms"], str(b): ["comms"]}
    assert str(a) not in led["comms"].get("handed", {})


def test_adopt_refuses_a_placement_not_the_givers_and_goes_on(tmp_path, skill):
    src = skill()
    a, b = tmp_path / "pa" / "x", tmp_path / "pb" / "x"
    place.apply(place.plan("comms", [Item(src, "claude", "x", dest=a)]))
    stranger = place.Placed("comms", "skill", "claude", "x", b, "link", "ok")
    said, say = lines()
    moved = place.adopt("comms", "loadout", [stranger, *place.status("comms")], say=say)
    assert [p.path for p in moved] == [a]
    assert said == [f"x at {b} is not comms's; not handed to loadout"]


def test_a_hand_over_lapses_once_the_taker_no_longer_places_the_target(home, skill):
    """comms hands its skill to loadout, which is then removed: the hand-over has
    lapsed, so comms's next plan places the skill again and drops the entry."""
    item = Item(skill(), "claude", "comms-send")
    place.apply(place.plan("comms", [item]))
    target = home / ".claude" / "skills" / "comms-send"
    place.adopt("comms", "loadout", place.status("comms"))
    step = one(place.plan("comms", [item]), "skip")       # loadout places it: skipped
    assert step.reason == "comms-send was handed to loadout; skipped"
    place.remove("loadout", say=lambda line: None)
    assert not os.path.lexists(target)
    assert ledger()["comms"]["handed"].keys() == {str(target)}
    one(place.plan("comms", [item]), "add")
    done = place.apply(place.plan("comms", [item]))
    assert [(p.owner, p.path) for p in done] == [("comms", target)]
    assert is_link(target)
    led = ledger()
    assert list(led) == ["comms"] and not led["comms"]["handed"]
    assert list(led["comms"]["placed"]) == [str(target)]
    one(place.plan("comms", [item]), "keep")


def test_after_a_hand_back_and_the_givers_removal_the_taker_may_place_or_adopt(
        home, skill):
    """comms hands its skill to loadout, loadout hands it back, and comms is removed:
    loadout's entry naming comms has lapsed. loadout may place the target itself, and
    once comms places it again, may adopt it, which clears the entry for good."""
    item = Item(skill(), "claude", "comms-send")
    place.apply(place.plan("comms", [item]))
    target = home / ".claude" / "skills" / "comms-send"
    place.adopt("comms", "loadout", place.status("comms"))
    place.adopt("loadout", "comms", place.status("loadout"))
    one(place.plan("loadout", [item]), "skip")            # comms places it: skipped
    place.remove("comms", say=lambda line: None)
    assert ledger()["loadout"]["handed"][str(target)]["to"] == "comms"
    one(place.plan("loadout", [item]), "add")
    place.apply(place.plan("loadout", [item]))
    led = ledger()
    assert list(led) == ["loadout"] and not led["loadout"]["handed"]
    assert list(led["loadout"]["placed"]) == [str(target)]
    place.remove("loadout", say=lambda line: None)

    # The other way: comms places it again and hands it over; one owner per target,
    # and the taker's handed map no longer names it.
    place.apply(place.plan("comms", [item]))
    place.adopt("comms", "loadout", place.status("comms"))
    place.adopt("loadout", "comms", place.status("loadout"))
    place.apply(place.plan("comms", [item]))
    place.adopt("comms", "loadout", place.status("comms"))
    led = ledger()
    assert [o for o, r in led.items() if str(target) in r["placed"]] == ["loadout"]
    assert str(target) not in led["loadout"]["handed"]
    assert led["comms"]["handed"][str(target)]["to"] == "loadout"
    one(place.plan("loadout", [item]), "keep")
    one(place.plan("comms", [item]), "skip")


def test_a_hand_over_handed_on_stands_until_no_owner_places_the_target(home, skill):
    """comms hands its skill to loadout, which hands it on to tidy: comms skips it while
    tidy holds it, and places it again once nobody does."""
    item = Item(skill(), "claude", "comms-send")
    place.apply(place.plan("comms", [item]))
    target = home / ".claude" / "skills" / "comms-send"
    place.adopt("comms", "loadout", place.status("comms"))
    place.adopt("loadout", "tidy", place.status("loadout"))
    step = one(place.plan("comms", [item]), "skip")
    assert step.reason == "comms-send was handed to loadout; skipped"
    said, say = lines()
    assert place.apply(place.plan("comms", [item]), say=say) == []
    assert said == ["comms-send was handed to loadout; skipped"]
    assert list(ledger()["tidy"]["placed"]) == [str(target)]
    place.remove("tidy", say=lambda line: None)
    one(place.plan("comms", [item]), "add")
    place.apply(place.plan("comms", [item]))
    led = ledger()
    assert list(led["comms"]["placed"]) == [str(target)] and not led["comms"]["handed"]
    # loadout's entry stands while comms holds the target.
    assert led["loadout"]["handed"][str(target)]["to"] == "tidy"
    one(place.plan("loadout", [item]), "skip")


def test_apply_skips_a_lapsed_hand_over_the_taker_holds_again(home, skill):
    """A plan made while the hand-over had lapsed, applied after the taker placed the
    target again, is skipped at apply and keeps the entry."""
    item = Item(skill(), "claude", "comms-send")
    place.apply(place.plan("comms", [item]))
    place.adopt("comms", "loadout", place.status("comms"))
    place.remove("loadout", say=lambda line: None)
    stale = place.plan("comms", [item])
    one(stale, "add")
    place.apply(place.plan("loadout", [item]))
    said, say = lines()
    assert place.apply(stale, say=say) == []
    assert said == ["comms-send was handed to loadout; skipped"]
    led = ledger()
    assert list(led["comms"]["handed"]) == [str(home / ".claude" / "skills" / "comms-send")]
    assert list(led["loadout"]["placed"]) == list(led["comms"]["handed"])


def test_a_seeded_hand_over_is_marked_and_skips_and_lapses_as_any(home, skill):
    item = Item(skill(), "claude", "comms-send")
    place.apply(place.plan("comms", [item]))
    target = home / ".claude" / "skills" / "comms-send"
    moved = place.adopt("comms", "loadout", place.status("comms"), seeded=True)
    assert [(p.owner, p.path) for p in moved] == [("loadout", target)]
    assert ledger()["comms"]["handed"] == {str(target): {
        "kind": "skill", "harness": "claude", "name": "comms-send", "to": "loadout",
        "seeded": True}}
    one(place.plan("comms", [item]), "skip")
    place.remove("loadout", say=lambda line: None)
    one(place.plan("comms", [item]), "add")
    place.apply(place.plan("comms", [item]))
    assert not ledger()["comms"]["handed"]


def test_adopt_replaces_an_existing_handed_entry(home, skill):
    """A handed entry the giver already has for the target is replaced, seeded or not as
    the call says."""
    item = Item(skill(), "claude", "comms-send")
    place.apply(place.plan("comms", [item]))
    key = str(home / ".claude" / "skills" / "comms-send")
    path = moxi.paths.core().state_dir() / place.LEDGER
    led = ledger()
    led["comms"]["handed"] = {key: {"kind": "skill", "harness": "claude",
                                    "name": "comms-send", "to": "tidy", "seeded": True}}
    path.write_text(json.dumps(led))
    place.adopt("comms", "loadout", place.status("comms"))
    assert ledger()["comms"]["handed"] == {key: {
        "kind": "skill", "harness": "claude", "name": "comms-send", "to": "loadout"}}
    place.adopt("loadout", "comms", place.status("loadout"))
    place.adopt("comms", "loadout", place.status("comms"), seeded=True)
    assert ledger()["comms"]["handed"][key]["seeded"] is True


def test_a_seeded_target_is_never_handed_back_and_the_rest_go(home, skill):
    """The hand-back of a seeded target is refused with a line naming its taker and
    changes nothing; an unseeded one in the same call goes back whole."""
    src = skill()
    items = [Item(src, "claude", "comms-send"), Item(src, "codex", "comms-send")]
    place.apply(place.plan("comms", items, form="copy"))
    claude = home / ".claude" / "skills" / "comms-send"
    codex = home / ".agents" / "skills" / "comms-send"
    mine = {p.path: p for p in place.status("comms")}
    place.adopt("comms", "loadout", [mine[claude]], seeded=True)
    place.adopt("comms", "loadout", [mine[codex]])
    before = ledger()["comms"]["handed"][str(claude)]
    said, say = lines()
    back = place.adopt("loadout", "comms", place.status("loadout"), say=say)
    assert [p.path for p in back] == [codex]
    assert said == ["comms-send was seeded; loadout removes it"]
    led = ledger()
    assert list(led["loadout"]["placed"]) == [str(claude)]
    assert led["comms"]["handed"] == {str(claude): before}
    assert list(led["comms"]["placed"]) == [str(codex)]
    assert json.loads((claude / place.MARK).read_text())["owner"] == "loadout"
    assert json.loads((codex / place.MARK).read_text())["owner"] == "comms"
    place.remove("loadout", say=lambda line: None)
    assert not os.path.lexists(claude) and os.path.isdir(codex)


def test_a_seeded_target_handed_on_is_not_handed_back_by_the_next_owner(home, skill):
    item = Item(skill(), "claude", "comms-send")
    place.apply(place.plan("comms", [item]))
    place.adopt("comms", "loadout", place.status("comms"), seeded=True)
    place.adopt("loadout", "tidy", place.status("loadout"))
    said, say = lines()
    assert place.adopt("tidy", "comms", place.status("tidy"), say=say) == []
    assert said == ["comms-send was seeded; loadout removes it"]
    assert [p.owner for p in place.status("tidy")] == ["tidy"]


# -- the core lock ------------------------------------------------------------------------

def _hold_the_core_lock(seconds=1.0):
    """Another process (a running install) holding the core lock for `seconds`."""
    lock = moxi.paths.core_lock()
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""\
            import sys, time
            import moxi.files
            with moxi.files.lock({str(lock)!r}):
                print("held", flush=True)
                time.sleep({seconds})
            """)],
        stdout=subprocess.PIPE, text=True, env=os.environ.copy())
    assert holder.stdout.readline().strip() == "held"
    return holder


def test_a_standalone_apply_waits_for_the_core_lock(tmp_path, skill):
    steps = place.plan("comms", [Item(skill(), "claude", "x")])
    holder = _hold_the_core_lock()
    try:
        start = time.monotonic()
        place.apply(steps)
        assert time.monotonic() - start > 0.5
    finally:
        holder.wait(timeout=10)


def test_a_standalone_remove_waits_for_the_core_lock(skill):
    place.apply(place.plan("comms", [Item(skill(), "claude", "x")]))
    holder = _hold_the_core_lock()
    try:
        start = time.monotonic()
        place.remove("comms", say=lambda line: None)
        assert time.monotonic() - start > 0.5
    finally:
        holder.wait(timeout=10)
    assert ledger() == {}


def test_a_standalone_adopt_waits_for_the_core_lock(skill):
    place.apply(place.plan("comms", [Item(skill(), "claude", "x")]))
    holder = _hold_the_core_lock()
    try:
        start = time.monotonic()
        moved = place.adopt("comms", "loadout", place.status("comms"),
                            say=lambda line: None)
        assert time.monotonic() - start > 0.5
    finally:
        holder.wait(timeout=10)
    assert len(moved) == 1 and "loadout" in ledger()


def test_apply_inside_a_nested_hold_yields(skill):
    steps = place.plan("comms", [Item(skill(), "claude", "x")])
    with moxi.files.lock(moxi.paths.core_lock(), nested=True):
        assert [p.state for p in place.apply(steps)] == ["ok"]
        assert place.remove("comms") != []


# -- Windows decisions (the plan only; the disk side is a row on a Windows host) ----------

@pytest.mark.parametrize("setting, symlinks, held, folder, want", [
    ("auto", True, False, True, ("copy", None)),
    ("link", True, False, True, ("junction", None)),
    ("auto", True, False, False, ("link", None)),
    ("auto", False, False, False, ("copy", place.FILE_NOTE)),
    ("auto", True, True, True, ("link", None)),
    ("auto", False, True, True, ("copy", place.FOLDER_NOTE)),
    ("copy", True, False, True, ("copy", None)),
])
def test_the_windows_form(monkeypatch, tmp_path, setting, symlinks, held, folder, want):
    monkeypatch.setattr(place, "_WINDOWS", True)
    monkeypatch.setattr(place, "_symlink_allowed", lambda folder: symlinks)
    source = tmp_path / "src"
    if folder:
        source.mkdir()
    else:
        source.write_text("x")
    planner = place._Planner("o", setting, {}, [], {})
    item = Item(source, "claude", "x", held=held, root=tmp_path)
    assert planner._form(item, str(source), str(tmp_path / "t" / "x")) == want


def test_the_symlink_probe_leaves_nothing_behind(tmp_path):
    base = tmp_path / "probe"
    base.mkdir()
    assert place._symlink_allowed(str(base / "not" / "yet")) is can_symlink()
    assert list(base.iterdir()) == []


# -- import cost --------------------------------------------------------------------------

def test_importing_the_placer_pulls_none_of_its_heavier_pieces():
    code = ("import sys, moxi.place; print(' '.join(m for m in ('hashlib', 'shutil', "
            "'subprocess', 'secrets', 'moxi._vendor.tomlkit', 'moxi.proc') "
            "if m in sys.modules))")
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                          check=True)
    assert done.stdout.strip() == ""


# -- from_package: the placements moxi install makes --------------------------------------

def test_from_package_reads_the_installed_package_and_an_older_install_of_it(
        tmp_path, monkeypatch):
    import importlib
    import types

    site = tmp_path / "site"
    pkg = site / "moxi_fpdemo"
    (pkg / "skills" / "x").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()
    found = types.SimpleNamespace(package="moxi_fpdemo.module")
    assert place.from_package(found, pkg / "skills" / "x")
    # Reached through a link to the package folder, and the package through a link.
    alias = tmp_path / "alias"
    link_folder(alias, pkg)
    assert place.from_package(found, alias / "skills" / "x")
    # Where an older install lay, before an upgrade moved the package.
    old = tmp_path / "old" / "lib" / "python3.11" / "site-packages" / "moxi_fpdemo"
    assert place.from_package(found, old / "skills" / "gone")
    assert place.from_package(found, str(old).replace("site-", "dist-") + "/skills/y")
    # A project folder named like the package is not the package; nor is the folder
    # an older install's package lay in, or another package.
    assert not place.from_package(found, tmp_path / "proj" / "moxi_fpdemo" / "skills" / "x")
    assert not place.from_package(found, old.parent)
    assert not place.from_package(found, old)
    assert not place.from_package(found, site / "moxi_other" / "skills" / "x")
    assert not place.from_package(types.SimpleNamespace(package=None), pkg / "skills")
    assert not place.from_package(None, pkg / "skills")
    assert not place.from_package(found, "")
    # A package that can't be imported here still counts by an older install's path.
    gone = types.SimpleNamespace(package="moxi_nowhere")
    assert not place.from_package(gone, tmp_path / "moxi_nowhere" / "skills" / "x")
    assert place.from_package(gone, old.parent / "moxi_nowhere" / "skills" / "x")
