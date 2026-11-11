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


def start(loadout_dir, text=COMMENTED):
    loadout_dir.mkdir(parents=True, exist_ok=True)
    path = loadout_dir / "loadout.toml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def read(loadout_dir):
    return manifest.load(loadout_dir)


def test_a_first_write_makes_the_loadout_dir_and_leaves_no_lock_file_in_it(loadout_dir):
    state = Layout("loadout").state_dir()
    assert not state.exists() and not loadout_dir.exists()
    assert writes.add("skill", "review", loadout_dir=loadout_dir)
    assert sorted(os.listdir(loadout_dir)) == ["loadout.toml"]
    assert (state / "manifest.lock").exists()
    assert read(loadout_dir).user.names("skill") == ("review",)
    if os.name != "nt":
        # The folder is the user's, made with the ordinary mode, not as a private store.
        assert loadout_dir.stat().st_mode & 0o777 != 0o700


@pytest.mark.parametrize("write", [
    lambda f: writes.add("agent", "lead", loadout_dir=f),
    lambda f: writes.remove("skill", "review", loadout_dir=f),
    lambda f: writes.disable("skill:review@codex", loadout_dir=f),
    lambda f: writes.set_mode("managed", loadout_dir=f),
    lambda f: writes.adopt({"launcher/moxi-handoff": ["claude"]}, loadout_dir=f),
])
def test_every_writer_keeps_comments_and_leaves_no_lock_in_the_loadout_dir(loadout_dir, write):
    path = start(loadout_dir)
    assert write(loadout_dir)
    text = path.read_text(encoding="utf-8")
    for line in ("# My loadout. Keep this comment.", "# agents later",
                 "# the global file stays mine", "# the ones I use everywhere"):
        assert line in text
    assert sorted(os.listdir(loadout_dir)) == ["loadout.toml"]


def test_writes_hold_the_one_manifest_lock(loadout_dir):
    path = start(loadout_dir)
    before = path.read_bytes()
    held, release = threading.Event(), threading.Event()

    def waits(doc, m):
        held.set()
        release.wait(10)
        return False

    def hold():
        writes.edit(waits, loadout_dir=loadout_dir)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert held.wait(10)
        with pytest.raises(files.LockTimeout):
            writes.edit(lambda doc, m: True, loadout_dir=loadout_dir, timeout=0)
    finally:
        release.set()
        holder.join()
    assert path.read_bytes() == before
    assert not (loadout_dir / "loadout.toml.lock").exists()


def test_a_change_that_changes_nothing_writes_nothing(loadout_dir):
    path = start(loadout_dir)
    stamp = path.stat().st_mtime_ns
    assert not writes.add("skill", "review", loadout_dir=loadout_dir)
    assert not writes.remove("agent", "lead", loadout_dir=loadout_dir)
    assert not writes.set_mode("off", loadout_dir=loadout_dir)
    assert path.stat().st_mtime_ns == stamp


def test_a_manifest_that_does_not_read_refuses_every_write(loadout_dir):
    path = start(loadout_dir, COMMENTED + "[skill.review]\nharness = ['claude']\n")
    before = path.read_bytes()
    with pytest.raises(ManifestError) as caught:
        writes.add("skill", "other", loadout_dir=loadout_dir)
    assert "did you mean harnesses" in str(caught.value)
    path.write_text("[user\n", encoding="utf-8")
    with pytest.raises(ManifestError) as caught:
        writes.add("skill", "other", loadout_dir=loadout_dir)
    assert "is not valid TOML" in str(caught.value)
    path.write_bytes(before)
    with pytest.raises(ManifestError):
        writes.edit(lambda doc, m: doc.__setitem__("skil", {}) or True, loadout_dir=loadout_dir)
    assert path.read_bytes() == before


def test_add_to_a_project_finds_its_section_or_makes_one(loadout_dir):
    start(loadout_dir)
    assert writes.add("agent", "lead", project="WEB", loadout_dir=loadout_dir)
    assert writes.add("skill", "deploy", project=os.path.expanduser("~/code/app"),
                      loadout_dir=loadout_dir)
    assert writes.add("skill", "x", project="/srv/other", loadout_dir=loadout_dir)
    m = read(loadout_dir)
    assert m.section("web").names("agent") == ("lead",)
    assert [s.name for s in m.projects] == ["web", "~/code/app", "/srv/other"]
    assert writes.add("skill", "more", project="~/code/app/", loadout_dir=loadout_dir)
    assert read(loadout_dir).section("~/code/app").names("skill") == ("deploy", "more")


def test_add_refuses_what_a_project_section_cannot_hold(loadout_dir):
    for kind in ("hook", "permissions", "mcp"):
        with pytest.raises(ManifestError, match="user scope only"):
            writes.add(kind, "x", project="web", loadout_dir=loadout_dir)
    with pytest.raises(ManifestError, match="user section only"):
        writes.add("skill", "launcher/moxi-handoff", project="web", loadout_dir=loadout_dir)
    with pytest.raises(ManifestError, match="is not a kind"):
        writes.add("skills", "x", loadout_dir=loadout_dir)
    with pytest.raises(ManifestError, match="names skill"):
        writes.add("skill", ".x", loadout_dir=loadout_dir)
    assert not loadout_dir.exists() or not (loadout_dir / "loadout.toml").exists()


def test_remove_takes_the_item_out_of_one_section_and_keeps_its_table(loadout_dir):
    start(loadout_dir, COMMENTED + "[skill.review]\nharnesses = ['claude']\n")
    assert writes.remove("skill", "review", project="web", loadout_dir=loadout_dir)
    m = read(loadout_dir)
    assert m.user.names("skill") == ("review",) and m.section("web").names("skill") == ()
    assert m.harnesses("skill", "review") == ("claude",)


def test_disable_writes_every_section_listing_the_item_or_the_one_named(loadout_dir):
    start(loadout_dir)
    assert writes.disable("skill:review@codex", loadout_dir=loadout_dir) == ["user", "project web"]
    assert writes.disable("skill:review@codex", loadout_dir=loadout_dir) == []
    assert writes.disable("skill:review@claude", project="web", loadout_dir=loadout_dir) == [
        "project web"]
    m = read(loadout_dir)
    assert m.user.is_disabled("skill", "review", "codex")
    assert not m.user.is_disabled("skill", "review", "claude")
    assert m.section("web").is_disabled("skill", "review", "claude")
    with pytest.raises(ManifestError, match="no section lists"):
        writes.disable("agent:lead@codex", loadout_dir=loadout_dir)
    with pytest.raises(ManifestError, match="doesn't list"):
        writes.disable("agent:lead@codex", user=True, loadout_dir=loadout_dir)
    with pytest.raises(ManifestError, match="not <kind>"):
        writes.disable("review", loadout_dir=loadout_dir)


def test_enable_takes_ids_out_and_refuses_one_harness_under_a_star(loadout_dir):
    start(loadout_dir)
    writes.disable("skill:review@codex", loadout_dir=loadout_dir)
    writes.disable("skill:review@claude", user=True, loadout_dir=loadout_dir)
    assert writes.enable("skill:review@codex", user=True, loadout_dir=loadout_dir) == ["user"]
    assert read(loadout_dir).user.disabled == (manifest.Id("skill", "review", "claude"),)
    assert writes.enable("skill:review@*", loadout_dir=loadout_dir) == ["user", "project web"]
    assert all(not s.disabled for s in read(loadout_dir).sections())
    writes.disable("skill:review@*", user=True, loadout_dir=loadout_dir)
    with pytest.raises(ManifestError, match="for every harness"):
        writes.enable("skill:review@codex", loadout_dir=loadout_dir)


def test_set_mode_writes_the_global_mode(loadout_dir):
    assert writes.set_mode("if-absent", loadout_dir=loadout_dir)
    assert read(loadout_dir).mode == "if-absent"
    start(loadout_dir)
    assert writes.set_mode("managed", loadout_dir=loadout_dir)
    assert read(loadout_dir).mode == "managed"
    with pytest.raises(ManifestError, match="use one of"):
        writes.set_mode("on", loadout_dir=loadout_dir)


def test_adopt_lists_module_skills_with_the_harnesses_taken_in_one_write(loadout_dir):
    start(loadout_dir, COMMENTED + '[skill."comms/moxi-send"]\nharnesses = ["claude"]\n')
    assert writes.adopt({"launcher/moxi-handoff": ["claude", "codex"],
                         "comms/moxi-send": ["codex", "claude"]}, loadout_dir=loadout_dir)
    m = read(loadout_dir)
    assert m.user.names("skill") == ("review", "launcher/moxi-handoff", "comms/moxi-send")
    assert m.harnesses("skill", "launcher/moxi-handoff") == ("claude", "codex")
    assert m.harnesses("skill", "comms/moxi-send") == ("claude", "codex")
    assert not writes.adopt({"comms/moxi-send": ["codex"]}, loadout_dir=loadout_dir)


def test_adopt_leaves_a_listed_skill_that_wants_every_harness(loadout_dir):
    start(loadout_dir, '[user]\nskills = ["launcher/moxi-handoff"]\n')
    assert not writes.adopt({"launcher/moxi-handoff": ["claude"]}, loadout_dir=loadout_dir)
    assert read(loadout_dir).harnesses("skill", "launcher/moxi-handoff") is None
    with pytest.raises(ManifestError, match="module's skill"):
        writes.adopt({"review": ["claude"]}, loadout_dir=loadout_dir)


def test_writes_go_to_the_configured_loadout_dir(loadout_dir):
    from moxi import config

    config.set_value("loadout", "source", str(loadout_dir), by="test")
    assert writes.add("skill", "review")
    assert manifest.load().user.names("skill") == ("review",)
    assert (loadout_dir / "loadout.toml").exists()
