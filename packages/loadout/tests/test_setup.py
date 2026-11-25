"""loadout's SETUP: the example manifest, the opencode row, adoption and the global mode
question."""

from __future__ import annotations

import importlib.resources
import os
import re
import sys

import loadout_kit as kit

from moxi_loadout import harness_row, install, ledger, setup, stores
from moxi_loadout.manifest import load, parse


def skill_md(name: str) -> str:
    return f"---\nname: {name}\ndescription: A module skill.\n---\nUse it.\n"


def example_text() -> str:
    return importlib.resources.files("moxi_loadout").joinpath(setup.EXAMPLE).read_text(
        encoding="utf-8")


class Terminal:
    """A stdin that is a terminal; the answers come through `ask`."""

    def isatty(self) -> bool:
        return True


def answers(*given):
    left = list(given)
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        if not left:
            raise EOFError
        return left.pop(0)

    return ask, asked


def quiet(line: str) -> None:
    pass


def test_setup_on_a_clean_machine(loadout_dir):
    kit.configure(loadout_dir)
    loadout_dir.rmdir()
    said: list[str] = []
    assert setup.setup(say=said.append) == 0
    assert (loadout_dir / "loadout.toml").read_text(encoding="utf-8") == example_text()
    assert sorted(os.listdir(loadout_dir)) == ["agents", "loadout.toml", "skills"]
    found = load(loadout_dir)
    assert found.exists and found.mode is None and found.items == {}
    # The opencode row is in the core's table, and recorded as loadout's.
    from moxi import place

    rows, _ = place.harnesses()
    assert sorted(rows) == ["claude", "codex", "opencode"]
    opencode = os.path.join(os.environ["XDG_CONFIG_HOME"], "opencode")
    assert str(rows["opencode"].skills) == os.path.join(opencode, "skills")
    assert str(rows["opencode"].agents) == os.path.join(opencode, "agents")
    record = harness_row.record_of(ledger.read())
    assert record["prior"] is False and record["written"]["name"] == "opencode"
    # Nobody at the keyboard: the mode is not written, and the line says how to set it.
    assert setup.NO_ANSWER in said


def test_setup_again_changes_nothing(loadout_dir):
    kit.configure(loadout_dir)
    loadout_dir.rmdir()
    assert setup.setup(say=quiet) == 0
    manifest = (loadout_dir / "loadout.toml").read_bytes()
    entries = ledger.read()["entries"]
    said: list[str] = []
    assert setup.setup(say=said.append) == 0
    assert (loadout_dir / "loadout.toml").read_bytes() == manifest
    assert ledger.read()["entries"] == entries
    assert said == [setup.NO_ANSWER]


def test_the_example_reads_with_every_item_uncommented():
    text = "".join(line[1:] if re.match(r"#[^ \n]", line) else line
                   for line in example_text().splitlines(keepends=True))
    found = parse(text, path="loadout.toml")
    assert found.mode == "off"
    assert "launcher/moxi-handoff" in found.user.names("skill")
    for kind in ("skill", "agent", "hook", "permissions", "mcp"):
        assert found.user.names(kind)
    assert [s.name for s in found.projects] == ["~/code/app"]


def test_a_loadout_dir_with_anything_in_it_gets_no_example(loadout_dir):
    kit.configure(loadout_dir)
    (loadout_dir / "notes.txt").write_text("mine\n", encoding="utf-8")
    assert setup.setup(say=quiet) == 0
    assert sorted(os.listdir(loadout_dir)) == ["notes.txt"]


def test_an_opencode_row_already_there_is_the_users(loadout_dir):
    from moxi import config, place

    kit.configure(loadout_dir)
    mine = {"name": "opencode", "skills": str(loadout_dir / "oc-skills"), "agents": ""}
    config.add_item("moxi", "harnesses", mine, by="test")
    assert setup.setup(say=quiet) == 0
    rows, lines = place.harnesses()
    assert str(rows["opencode"].skills) == str(loadout_dir / "oc-skills") and lines == []
    assert harness_row.record_of(ledger.read())["prior"] is True


def test_a_recorded_row_the_user_removed_is_added_again(loadout_dir):
    from moxi import config, place

    kit.configure(loadout_dir)
    assert setup.setup(say=quiet) == 0
    config.remove_item("moxi", "harnesses", harness_row.wanted(), by="test")
    assert "opencode" not in place.harnesses()[0]
    assert setup.setup(say=quiet) == 0
    assert "opencode" in place.harnesses()[0]
    assert len([r for r in ledger.read()["entries"]
                if r["kind"] == harness_row.KIND]) == 1


def test_the_question_at_a_terminal_writes_the_answer(loadout_dir, monkeypatch):
    kit.configure(loadout_dir)
    loadout_dir.rmdir()
    monkeypatch.setattr(sys, "stdin", Terminal())
    ask, asked = answers("sometimes", "managed")
    said: list[str] = []
    assert setup.setup(say=said.append, ask=ask) == 0
    assert asked[0] == setup.MODE_QUESTION and "'sometimes' is not a mode" in asked[1]
    found = load(loadout_dir)
    assert found.mode == "managed"
    text = (loadout_dir / "loadout.toml").read_text(encoding="utf-8")
    assert text.startswith(example_text().splitlines()[0])
    assert f"loadout: write {loadout_dir / 'global.md'}, then run moxi loadout install" in said
    # Asked once: the manifest now carries a mode.
    ask, asked = answers("off")
    assert setup.setup(say=quiet, ask=ask) == 0
    assert asked == []


def test_an_empty_answer_is_off_and_written(loadout_dir, monkeypatch):
    kit.configure(loadout_dir)
    monkeypatch.setattr(sys, "stdin", Terminal())
    ask, _ = answers("")
    assert setup.setup(say=quiet, ask=ask) == 0
    assert load(loadout_dir).mode == "off"


def test_no_answer_at_a_terminal_writes_nothing(loadout_dir, monkeypatch):
    kit.configure(loadout_dir)
    loadout_dir.rmdir()
    monkeypatch.setattr(sys, "stdin", Terminal())
    ask, _ = answers()
    said: list[str] = []
    assert setup.setup(say=said.append, ask=ask) == 0
    assert load(loadout_dir).mode is None
    assert setup.NO_ANSWER in said


def test_the_question_is_skipped_when_the_manifest_has_a_mode(loadout_dir, monkeypatch):
    kit.configure(loadout_dir)
    kit.manifest(loadout_dir, '[global]\nmode = "if-absent"\n')
    before = (loadout_dir / "loadout.toml").read_bytes()
    monkeypatch.setattr(sys, "stdin", Terminal())
    ask, asked = answers("managed")
    said: list[str] = []
    assert setup.setup(say=said.append, ask=ask) == 0
    assert asked == [] and setup.NO_ANSWER not in said
    assert (loadout_dir / "loadout.toml").read_bytes() == before


def test_setup_takes_over_each_modules_own_placements(site, loadout_dir):
    from moxi import place
    from moxi.place import Item

    package = site.module("demo", SKILLS="'skills'", files={
        "skills/moxi-demo/SKILL.md": skill_md("moxi-demo")})
    skills = site.root / package / "skills"
    kit.configure(loadout_dir)
    loadout_dir.rmdir()
    place.apply(place.plan("demo", [Item(skills / "moxi-demo", "claude", "moxi-demo")]),
                say=quiet)
    said: list[str] = []
    assert setup.setup(say=said.append) == 0
    target = kit.claude() / "moxi-demo"
    assert kit.owner_of(target) == "loadout"
    assert not kit.handed("demo")[str(target)].get("seeded")
    found = load(loadout_dir)
    assert found.user.names("skill") == ("demo/moxi-demo",)
    assert found.harnesses("skill", "demo/moxi-demo") == ("claude",)
    assert "loadout: took over demo/moxi-demo for claude" in said
    # The example's comments are kept, no lock file lands in the folder, and setup took
    # no install lock.
    text = (loadout_dir / "loadout.toml").read_text(encoding="utf-8")
    assert text.startswith(example_text().splitlines()[0])
    assert sorted(os.listdir(loadout_dir)) == ["agents", "loadout.toml", "skills"]
    assert not (stores.state_dir() / stores.INSTALL_LOCK).exists()
    out = install.install()
    assert out.code == 0, out.text()
    record = ledger.read()["placed"][str(target)]
    assert record["module"] == "demo" and record["seeded"] is False


def test_a_manifest_that_doesnt_read_fails_setup_and_adopts_nothing(site, loadout_dir):
    from moxi import place
    from moxi.place import Item

    package = site.module("demo", SKILLS="'skills'", files={
        "skills/moxi-demo/SKILL.md": skill_md("moxi-demo")})
    kit.configure(loadout_dir)
    kit.manifest(loadout_dir, "[user]\nskils = []\n")
    place.apply(place.plan("demo", [Item(site.root / package / "skills" / "moxi-demo",
                                         "claude", "moxi-demo")]), say=quiet)
    result = setup.setup(say=quiet)
    assert isinstance(result, str) and "no module skill was taken over" in result
    assert "skils" in result
    assert kit.owner_of(kit.claude() / "moxi-demo") == "demo"


def test_after_setup_install_places_for_opencode(loadout_dir):
    kit.configure(loadout_dir)
    kit.skill(loadout_dir / "skills", "review")
    kit.manifest(loadout_dir, '[global]\nmode = "off"\n[user]\nskills = ["review"]\n')
    assert setup.setup(say=quiet) == 0
    out = install.install()
    assert out.code == 0, out.text()
    placed = os.path.join(os.environ["XDG_CONFIG_HOME"], "opencode", "skills", "review")
    assert kit.placed(placed, loadout_dir / "skills" / "review")


def test_the_announcement_names_setup_and_the_listener():
    from moxi import modules

    found = modules.load("loadout")
    assert found.setup == "moxi_loadout.setup:setup"
    assert any(e.name == "loadout" and getattr(e.load(), "on_install", None)
               for e in modules.extensions("moxi.installs"))


def test_a_manifest_write_that_fails_hands_nothing_over(site, loadout_dir, monkeypatch):
    from moxi import place
    from moxi.files import LockTimeout
    from moxi.place import Item

    from moxi_loadout import writes

    package = site.module("demo", SKILLS="'skills'", files={
        "skills/moxi-demo/SKILL.md": skill_md("moxi-demo")})
    kit.configure(loadout_dir)
    kit.manifest(loadout_dir, "[user]\n")
    place.apply(place.plan("demo", [Item(site.root / package / "skills" / "moxi-demo",
                                         "claude", "moxi-demo")]), say=quiet)

    def held(*args, **kwargs):
        raise LockTimeout(stores.manifest_lock(), 4242)

    monkeypatch.setattr(writes, "adopt", held)
    result = setup.setup(say=quiet)
    assert isinstance(result, str) and "no module skill was taken over" in result
    # The line is true: the module still holds its placement, and loadout holds nothing
    # its manifest doesn't list.
    assert kit.owner_of(kit.claude() / "moxi-demo") == "demo"
    assert kit.handed("demo") == {}
    assert place.status("loadout") == []
