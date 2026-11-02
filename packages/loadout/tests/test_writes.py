"""The manifest's writers: one edit each, comments kept, the lock in loadout's state dir."""

from __future__ import annotations

import os
import textwrap
import threading

import pytest
from moxi import files
from moxi.paths import Layout

from moxi_loadout import manifest, stores, writes
from moxi_loadout.manifest import ManifestError

COMMENTED = """\
# My loadout. Keep this comment.
[global]
mode = "off"   # the global file stays mine

[user]
skills = ["review"]   # the ones I use everywhere
# agents later

[project.web]
skills = ["review"]
"""


def start(folder, text=COMMENTED):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "loadout.toml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def read(folder):
    return manifest.load(folder)


def test_a_first_write_makes_the_folder_and_leaves_no_lock_file_in_it(folder):
    state = Layout("loadout").state_dir()
    assert not state.exists() and not folder.exists()
    assert writes.add("skill", "review", folder=folder)
    assert sorted(os.listdir(folder)) == ["loadout.toml"]
    assert (state / "manifest.lock").exists()
    assert read(folder).user.names("skill") == ("review",)
    if os.name != "nt":
        # The folder is the user's, made with the ordinary mode, not as a private store.
        assert folder.stat().st_mode & 0o777 != 0o700


@pytest.mark.parametrize("write", [
    lambda f: writes.add("agent", "lead", folder=f),
    lambda f: writes.remove("skill", "review", folder=f),
    lambda f: writes.disable("skill:review@codex", folder=f),
    lambda f: writes.set_mode("managed", folder=f),
    lambda f: writes.adopt({"launcher/moxi-handoff": ["claude"]}, folder=f),
])
def test_every_writer_keeps_comments_and_leaves_no_lock_in_the_folder(folder, write):
    path = start(folder)
    assert write(folder)
    text = path.read_text(encoding="utf-8")
    for line in ("# My loadout. Keep this comment.", "# agents later",
                 "# the global file stays mine", "# the ones I use everywhere"):
        assert line in text
    assert sorted(os.listdir(folder)) == ["loadout.toml"]


def test_writes_hold_the_one_manifest_lock(folder):
    path = start(folder)
    before = path.read_bytes()
    held, release = threading.Event(), threading.Event()

    def waits(doc, m):
        held.set()
        release.wait(10)
        return False

    def hold():
        writes.edit(waits, folder=folder)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert held.wait(10)
        with pytest.raises(files.LockTimeout):
            writes.edit(lambda doc, m: True, folder=folder, timeout=0)
    finally:
        release.set()
        holder.join()
    assert path.read_bytes() == before
    assert not (folder / "loadout.toml.lock").exists()


def test_a_change_that_changes_nothing_writes_nothing(folder):
    path = start(folder)
    stamp = path.stat().st_mtime_ns
    assert not writes.add("skill", "review", folder=folder)
    assert not writes.remove("agent", "lead", folder=folder)
    assert not writes.set_mode("off", folder=folder)
    assert path.stat().st_mtime_ns == stamp


def test_a_manifest_that_does_not_read_refuses_every_write(folder):
    path = start(folder, COMMENTED + "[skill.review]\nharness = ['claude']\n")
    before = path.read_bytes()
    with pytest.raises(ManifestError) as caught:
        writes.add("skill", "other", folder=folder)
    assert "did you mean harnesses" in str(caught.value)
    path.write_text("[user\n", encoding="utf-8")
    with pytest.raises(ManifestError) as caught:
        writes.add("skill", "other", folder=folder)
    assert "is not valid TOML" in str(caught.value)
    path.write_bytes(before)
    with pytest.raises(ManifestError):
        writes.edit(lambda doc, m: doc.__setitem__("skil", {}) or True, folder=folder)
    assert path.read_bytes() == before


def test_add_to_a_project_finds_its_section_or_makes_one(folder):
    start(folder)
    assert writes.add("agent", "lead", project="WEB", folder=folder)
    assert writes.add("skill", "deploy", project=os.path.expanduser("~/code/app"),
                      folder=folder)
    assert writes.add("skill", "x", project="/srv/other", folder=folder)
    m = read(folder)
    assert m.section("web").names("agent") == ("lead",)
    assert [s.name for s in m.projects] == ["web", "~/code/app", "/srv/other"]
    assert writes.add("skill", "more", project="~/code/app/", folder=folder)
    assert read(folder).section("~/code/app").names("skill") == ("deploy", "more")


def test_add_refuses_what_a_project_section_cannot_hold(folder):
    for kind in ("hook", "permissions", "mcp"):
        with pytest.raises(ManifestError, match="user scope only"):
            writes.add(kind, "x", project="web", folder=folder)
    with pytest.raises(ManifestError, match="user section only"):
        writes.add("skill", "launcher/moxi-handoff", project="web", folder=folder)
    with pytest.raises(ManifestError, match="is not a kind"):
        writes.add("skills", "x", folder=folder)
    with pytest.raises(ManifestError, match="names skill"):
        writes.add("skill", ".x", folder=folder)
    assert not folder.exists() or not (folder / "loadout.toml").exists()


def test_remove_takes_the_item_out_of_one_section_and_keeps_its_table(folder):
    start(folder, COMMENTED + "[skill.review]\nharnesses = ['claude']\n")
    assert writes.remove("skill", "review", project="web", folder=folder)
    m = read(folder)
    assert m.user.names("skill") == ("review",) and m.section("web").names("skill") == ()
    assert m.harnesses("skill", "review") == ("claude",)


def test_disable_writes_every_section_listing_the_item_or_the_one_named(folder):
    start(folder)
    assert writes.disable("skill:review@codex", folder=folder) == ["user", "project web"]
    assert writes.disable("skill:review@codex", folder=folder) == []
    assert writes.disable("skill:review@claude", project="web", folder=folder) == [
        "project web"]
    m = read(folder)
    assert m.user.is_disabled("skill", "review", "codex")
    assert not m.user.is_disabled("skill", "review", "claude")
    assert m.section("web").is_disabled("skill", "review", "claude")
    with pytest.raises(ManifestError, match="no section lists"):
        writes.disable("agent:lead@codex", folder=folder)
    with pytest.raises(ManifestError, match="doesn't list"):
        writes.disable("agent:lead@codex", user=True, folder=folder)
    with pytest.raises(ManifestError, match="not <kind>"):
        writes.disable("review", folder=folder)


def test_enable_takes_ids_out_and_refuses_one_harness_under_a_star(folder):
    start(folder)
    writes.disable("skill:review@codex", folder=folder)
    writes.disable("skill:review@claude", user=True, folder=folder)
    assert writes.enable("skill:review@codex", user=True, folder=folder) == ["user"]
    assert read(folder).user.disabled == (manifest.Id("skill", "review", "claude"),)
    assert writes.enable("skill:review@*", folder=folder) == ["user", "project web"]
    assert all(not s.disabled for s in read(folder).sections())
    writes.disable("skill:review@*", user=True, folder=folder)
    with pytest.raises(ManifestError, match="for every harness"):
        writes.enable("skill:review@codex", folder=folder)


def test_set_mode_writes_the_global_mode(folder):
    assert writes.set_mode("if-absent", folder=folder)
    assert read(folder).mode == "if-absent"
    start(folder)
    assert writes.set_mode("managed", folder=folder)
    assert read(folder).mode == "managed"
    with pytest.raises(ManifestError, match="use one of"):
        writes.set_mode("on", folder=folder)


def test_adopt_lists_module_skills_with_the_harnesses_taken_in_one_write(folder):
    start(folder, COMMENTED + '[skill."comms/moxi-send"]\nharnesses = ["claude"]\n')
    assert writes.adopt({"launcher/moxi-handoff": ["claude", "codex"],
                         "comms/moxi-send": ["codex", "claude"]}, folder=folder)
    m = read(folder)
    assert m.user.names("skill") == ("review", "launcher/moxi-handoff", "comms/moxi-send")
    assert m.harnesses("skill", "launcher/moxi-handoff") == ("claude", "codex")
    assert m.harnesses("skill", "comms/moxi-send") == ("claude", "codex")
    assert not writes.adopt({"comms/moxi-send": ["codex"]}, folder=folder)


def test_adopt_leaves_a_listed_skill_that_wants_every_harness(folder):
    start(folder, '[user]\nskills = ["launcher/moxi-handoff"]\n')
    assert not writes.adopt({"launcher/moxi-handoff": ["claude"]}, folder=folder)
    assert read(folder).harnesses("skill", "launcher/moxi-handoff") is None
    with pytest.raises(ManifestError, match="module's skill"):
        writes.adopt({"review": ["claude"]}, folder=folder)


def test_writes_go_to_the_configured_folder(folder):
    from moxi import config

    config.set("loadout", "source", str(folder), by="test")
    assert writes.add("skill", "review")
    assert manifest.load().user.names("skill") == ("review",)
    assert (folder / "loadout.toml").exists()
