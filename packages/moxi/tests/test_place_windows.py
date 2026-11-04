"""The placer on a Windows disk: what `test_place.py` decides with `_WINDOWS` patched,
made for real.

Every row here runs only on Windows and skips elsewhere, with a reason naming what it
proves. Each places under tmp: `~` points there through USERPROFILE, which is what
Windows reads for it (not HOME), and the core's own roots already do (conftest).
"""

from __future__ import annotations

import json
import os
import sys
import types

import pytest

from moxi import place
from moxi.place import Item
from test_place import ledger, one, set_core, skill  # noqa: F401


def windows(proves: str):
    return pytest.mark.skipif(sys.platform != "win32", reason=f"Windows only: {proves}")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A home of its own, so `~/.claude`, `~/.agents` and `~/.codex` are under tmp (not
    the fence's `tmp_path / "home"`, whose `CLAUDE_CONFIG_DIR` this unsets)."""
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert os.path.expanduser("~") == str(home)
    return home


@windows("under `link` a skill folder is placed as a directory junction that reads through "
         "to its source, is kept on a re-plan, and is removed alone, its source left")
def test_link_places_a_folder_as_a_junction_and_removes_only_the_junction(home, skill):
    src = skill()
    set_core("skills.form", "link")
    item = Item(src, "claude", "comms-send")
    step = one(place.plan("comms", [item]), "add")
    assert step.form == "junction"
    placed = place.apply([step])
    target = home / ".claude" / "skills" / "comms-send"
    assert [(p.path, p.form, p.state) for p in placed] == [(target, "junction", "ok")]
    assert os.path.isjunction(target) and not os.path.islink(target)
    assert (target / "SKILL.md").read_text() == "# send\n"
    assert ledger()["comms"]["placed"][str(target)]["form"] == "junction"
    one(place.plan("comms", [item]), "keep")
    assert [p.state for p in place.status("comms")] == ["ok"]
    gone = place.remove("comms", say=lambda line: None)
    assert [p.path for p in gone] == [target]
    assert not os.path.lexists(target)
    assert (src / "SKILL.md").read_text() == "# send\n" and (src / "scripts").is_dir()


@pytest.fixture
def project(tmp_path, skill):
    root = tmp_path / "proj"
    src = skill("tidy", base=root / "skills")
    return root, src


@windows("a held item's folder is placed as a relative directory symlink when this account "
         "can make symlinks, readable through it, and dropping it leaves the "
         "source")
def test_a_held_folder_links_relative_when_symlinks_are_allowed(project):
    root, src = project
    dest = root / ".claude" / "skills" / "tidy"
    if not place._symlink_allowed(str(dest.parent)):
        pytest.skip("this account can't make symlinks (Developer Mode off, not elevated); "
                    "the copy fallback row covers it")
    before = place._hash(str(src))
    item = Item(src, "claude", "tidy", dest=dest, held=True, root=root)
    step = one(place.plan("loadout", [item]), "add")
    assert (step.form, step.note) == ("link", None)
    place.apply([step])
    assert os.path.islink(dest) and not os.path.isjunction(dest)
    assert os.readlink(dest) == os.path.join("..", "..", "skills", "tidy")
    assert (dest / "SKILL.md").read_text() == "# send\n"
    one(place.plan("loadout", [item]), "keep")
    place.apply(place.plan("loadout", []))
    assert not os.path.lexists(dest) and place._hash(str(src)) == before


@windows("where this account can't make symlinks, a held item's folder is copied, marked "
         "held, with the note that says why, and dropping it leaves the source")
def test_a_held_folder_is_copied_with_its_note_when_symlinks_are_not_allowed(
        project, monkeypatch):
    root, src = project
    monkeypatch.setattr(place, "_symlink_allowed", lambda folder: False)
    before = place._hash(str(src))
    dest = root / ".claude" / "skills" / "tidy"
    item = Item(src, "claude", "tidy", dest=dest, held=True, root=root)
    step = one(place.plan("loadout", [item]), "add")
    assert (step.form, step.note) == ("copy", place.FOLDER_NOTE)
    place.apply([step])
    assert dest.is_dir() and not os.path.islink(dest) and not os.path.isjunction(dest)
    mark = json.loads((dest / place.MARK).read_text())
    assert mark["held"] is True and mark["owner"] == "loadout"
    assert (dest / "SKILL.md").read_text() == "# send\n"
    one(place.plan("loadout", [item]), "keep")
    place.apply(place.plan("loadout", []))
    assert not dest.exists() and place._hash(str(src)) == before


@windows("from_package reads a uv tool's Windows layout (`Lib\\site-packages`): the "
         "installed package by either slash, an older install's folder, and not a project "
         "folder named like the package")
def test_from_package_reads_windows_paths(tmp_path, monkeypatch):
    import importlib

    site = tmp_path / "tools" / "moxi-fpwin" / "Lib" / "site-packages"
    pkg = site / "moxi_fpwin"
    (pkg / "skills" / "x").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()
    found = types.SimpleNamespace(package="moxi_fpwin.module")
    source = pkg / "skills" / "x"
    assert "\\" in str(source)
    assert place.from_package(found, source)
    assert place.from_package(found, str(source))
    assert place.from_package(found, str(source).replace("\\", "/"))
    # Where an older install lay, before an upgrade moved the package.
    old = tmp_path / "old" / "moxi-fpwin" / "Lib" / "site-packages" / "moxi_fpwin"
    assert place.from_package(found, old / "skills" / "gone")
    assert place.from_package(found, str(old / "skills" / "gone").replace("\\", "/"))
    assert not place.from_package(found, old)
    assert not place.from_package(found, old.parent)
    # A project folder named like the package, and another package, are not it.
    assert not place.from_package(found, tmp_path / "proj" / "moxi_fpwin" / "skills" / "x")
    assert not place.from_package(found, site / "moxi_other" / "skills" / "x")
