"""Claude Code's `statusLine` setting: which line is Moxi's, the switch, restore and remove.

Claude's folder is the test's own (the fence points `CLAUDE_CONFIG_DIR` into its tmp).
"""

from __future__ import annotations

import json
import os
import stat

import pytest
from statusline_kit import config_file

from moxi import exit as codes
from moxi import paths
from moxi_statusline import switch

RENDER = "/old/env/bin/moxi-statusline-render"


def settings_file():
    return paths.claude_home() / "settings.json"


def write_settings(data, *, indent=2) -> bytes:
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(data, indent=indent) + "\n").encode() if not isinstance(
        data, (bytes, str)) else (data.encode() if isinstance(data, str) else data)
    path.write_bytes(raw)
    return raw


def settings():
    return json.loads(settings_file().read_bytes())


def here() -> str:
    return switch.render_command()


def backups():
    backups_dir = switch.backups_dir()
    return sorted(backups_dir.iterdir()) if backups_dir.is_dir() else []


# -- Moxi's line ------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    RENDER,
    "/another/one/moxi-statusline-render",
    "'/with a space/bin/moxi-statusline-render'",
    "  /old/env/bin/moxi-statusline-render  ",
])
def test_an_absolute_render_path_is_moxis(command):
    assert switch.is_moxis({"type": "command", "command": command})


@pytest.mark.parametrize("value", [
    {"type": "command", "command": "moxi-statusline-render"},
    {"type": "command", "command": "/home/u/.claude/my-line.sh"},
    {"type": "command", "command": "sh -c /old/env/bin/moxi-statusline-render"},
    {"type": "command", "command": RENDER + " --only cache"},
    {"type": "command", "command": "/old/env/bin/moxi-statusline-render-wrapper"},
    {"type": "command", "command": "relative/bin/moxi-statusline-render"},
    {"type": "command", "command": "'/unclosed/moxi-statusline-render"},
    {"type": "command"},
    None,
    "/old/env/bin/moxi-statusline-render",
])
def test_a_wrapper_a_bare_name_and_other_lines_are_not_moxis(value):
    assert not switch.is_moxis(value)


def test_a_wrapper_script_that_calls_the_render_is_not_moxis(tmp_path):
    wrapper = tmp_path / "line.sh"
    wrapper.write_text(f"#!/bin/sh\nexec {RENDER}\n")
    wrapper.chmod(0o755)
    assert not switch.is_moxis({"type": "command", "command": str(wrapper)})


def test_the_command_written_is_this_environments_render_and_counts_as_moxis():
    path = switch.moxis_path({"command": here()})
    assert path is not None and os.path.samefile(path, switch.render_path())
    assert switch.render_command("/a b/bin/moxi-statusline-render", kind="sh") == \
        "'/a b/bin/moxi-statusline-render'"


# The Windows forms are read with Windows' path rules on every system.
WINDOWS_RENDER = r"C:\Users\you\it's here\Scripts\moxi-statusline-render.exe"


@pytest.mark.parametrize(("kind", "command"), [
    ("bash", "'C:/Users/you/it'\"'\"'s here/Scripts/moxi-statusline-render.exe'"),
    ("powershell", "& 'C:\\Users\\you\\it''s here\\Scripts\\moxi-statusline-render.exe'"),
])
def test_a_windows_command_is_written_for_its_shell_and_is_moxis(monkeypatch, kind, command):
    monkeypatch.setattr(switch, "_WINDOWS", True)
    assert switch.render_command(WINDOWS_RENDER, kind=kind) == command
    assert switch.moxis_path({"command": command}).replace("/", "\\") == WINDOWS_RENDER


@pytest.mark.parametrize("command", [
    r"C:\env\Scripts\moxi-statusline-render.exe",
    r'"C:\with space\Scripts\moxi-statusline-render.exe"',
    "C:/env/Scripts/moxi-statusline-render.exe",
    "'C:/with space/Scripts/MOXI-STATUSLINE-RENDER.EXE'",
    r"& 'C:\env\Scripts\moxi-statusline-render.exe'",
    r"  &'C:\env\Scripts\moxi-statusline-render'  ",
])
def test_every_windows_form_and_the_old_one_are_moxis(monkeypatch, command):
    monkeypatch.setattr(switch, "_WINDOWS", True)
    assert switch.is_moxis({"type": "command", "command": command})


@pytest.mark.parametrize("command", [
    r"& C:\env\Scripts\moxi-statusline-render.exe",
    r"& 'C:\env\Scripts\moxi-statusline-render.exe' --only cache",
    r"& 'C:\it's\moxi-statusline-render.exe'",
    "C:/env/Scripts/moxi-statusline-render.exe --only cache",
    "'C:/unclosed/moxi-statusline-render.exe",
    r"Scripts\moxi-statusline-render.exe",
    "moxi-statusline-render.exe",
    r"C:\env\Scripts\moxi-statusline-render-wrapper.exe",
])
def test_other_windows_lines_are_not_moxis(monkeypatch, command):
    monkeypatch.setattr(switch, "_WINDOWS", True)
    assert not switch.is_moxis({"type": "command", "command": command})


def test_a_line_in_the_old_windows_form_is_rewritten_by_the_switch(monkeypatch):
    monkeypatch.setattr(switch, "_WINDOWS", True)
    write_settings({"statusLine": {"type": "command",
                                   "command": r"C:\env\Scripts\moxi-statusline-render.exe"}})
    act = switch.switch(None, by="test")
    assert act.ok and act.kind == "refreshed"
    assert settings()["statusLine"]["command"] == here()
    assert not switch.previous_path().exists()


# -- the switch ------------------------------------------------------------------------

def test_switch_with_no_status_line_sets_moxis_with_a_refresh_and_keeps_none():
    write_settings({"model": "made-up", "hooks": {}})
    act = switch.switch(["dir", "model"], by="test")
    assert act.ok and act.kind == "switched"
    assert act.lines == [switch.SWITCHED]
    data = settings()
    assert list(data) == ["model", "hooks", "statusLine"]
    assert data["statusLine"] == {"type": "command", "command": here(),
                                  "refreshInterval": 5}
    kept = json.loads(switch.previous_path().read_text())
    assert kept["had"] is False and "statusLine" not in kept
    assert kept["saved_at"].endswith("Z")
    assert 'show = ["dir", "model"]' in config_file().read_text()


def test_switch_with_no_settings_file_makes_one_private():
    act = switch.switch(None, by="test")
    assert act.ok
    assert settings()["statusLine"]["command"] == here()
    if os.name == "posix":  # Windows has no mode bits
        assert stat.S_IMODE(settings_file().stat().st_mode) == 0o600
    assert not config_file().exists()


def test_switch_over_another_command_keeps_padding_and_refresh_and_saves_it():
    old = {"type": "command", "command": "~/.claude/line.sh", "padding": 2,
           "refreshInterval": 30}
    write_settings({"statusLine": old, "theme": "dark"})
    act = switch.switch(None, by="test")
    assert act.ok and act.kind == "switched"
    assert act.command == "~/.claude/line.sh"
    data = settings()
    assert data["statusLine"] == {"type": "command", "command": here(), "padding": 2,
                                  "refreshInterval": 30}
    assert list(data) == ["statusLine", "theme"]
    saved = switch.saved()
    assert saved.had and saved.value == old


def test_switch_over_moxis_at_an_old_path_rewrites_the_path_alone():
    write_settings({"statusLine": {"type": "command", "command": RENDER, "padding": 0}})
    act = switch.switch(None, by="test")
    assert act.ok and act.kind == "refreshed"
    assert act.lines == [f"Claude Code's status line now runs {here()}"]
    assert settings()["statusLine"] == {"type": "command", "command": here(),
                                        "padding": 0}
    assert not switch.previous_path().exists()


def test_switch_over_moxis_here_writes_nothing():
    raw = write_settings({"statusLine": {"type": "command", "command": here()}})
    act = switch.switch(None, by="test")
    assert act.ok and act.kind == "current"
    assert settings_file().read_bytes() == raw
    assert backups() == []


def test_switch_never_overwrites_the_saved_line_with_moxis():
    old = {"type": "command", "command": "/home/u/line.sh"}
    write_settings({"statusLine": old})
    switch.switch(None, by="test")
    switch.switch(None, by="test")
    assert switch.saved().value == old


def test_the_files_mode_key_order_and_indent_are_kept():
    write_settings({"z": 1, "statusLine": {"type": "command", "command": "/x/l.sh"},
                    "a": [1, 2]}, indent=4)
    settings_file().chmod(0o644)
    assert switch.switch(None, by="test").ok
    text = settings_file().read_text()
    if os.name == "posix":  # Windows has no mode bits
        assert stat.S_IMODE(settings_file().stat().st_mode) == 0o644
    assert list(json.loads(text)) == ["z", "statusLine", "a"]
    assert text.splitlines()[1].startswith('    "z"')


def test_a_settings_file_that_is_a_link_stays_one_and_its_target_changes(tmp_path):
    target = tmp_path / "dotfiles" / "settings.json"
    target.parent.mkdir()
    target.write_text('{"theme": "dark"}\n')
    link = settings_file()
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    assert switch.switch(None, by="test").ok
    assert link.is_symlink()
    assert json.loads(target.read_text())["statusLine"]["command"] == here()


def test_a_backup_holds_the_old_bytes_and_no_lock_file_is_left():
    raw = write_settings({"statusLine": {"type": "command", "command": "/x/l.sh"}})
    assert switch.switch(None, by="test").ok
    found = backups()
    assert len(found) == 1 and found[0].read_bytes() == raw
    if os.name == "posix":  # Windows has no mode bits
        assert stat.S_IMODE(found[0].stat().st_mode) == 0o600
    assert os.listdir(settings_file().parent) == ["settings.json"]


def test_the_newest_five_backups_are_kept():
    write_settings({"statusLine": {"type": "command", "command": "/x/l.sh"}})
    for _ in range(4):
        assert switch.switch(None, by="test").ok
        assert switch.restore().ok
    assert len(backups()) == 5


@pytest.mark.parametrize("raw", [b'{"a": 1,}\n', b"// comment\n{}\n", b"[1, 2]\n"])
def test_unreadable_json_changes_nothing(raw):
    write_settings(raw)
    act = switch.switch(["dir"], by="test")
    assert act.code == codes.REFUSED and act.kind == "refused"
    assert act.lines[-1] == switch.LEFT
    assert str(settings_file()) in act.lines[0]
    assert settings_file().read_bytes() == raw
    assert not switch.previous_path().exists()
    assert backups() == []
    assert not config_file().exists()


def test_a_file_changed_between_read_and_write_is_not_written(monkeypatch):
    write_settings({"statusLine": {"type": "command", "command": "/x/l.sh"}})
    real = switch.read

    def read_then_meddle(env=None):
        doc = real(env)
        settings_file().write_text('{"theme": "light"}\n')
        return doc

    monkeypatch.setattr(switch, "read", read_then_meddle)
    act = switch.switch(["dir"], by="test")
    assert act.code == codes.TEMPFAIL and act.kind == "changed"
    assert "changed since it was read" in act.lines[0]
    assert settings() == {"theme": "light"}
    assert backups() == []
    assert not config_file().exists()


def test_a_show_the_config_refuses_leaves_the_switch_and_says_so():
    write_settings({})
    act = switch.switch(["row", "dir"], by="test")
    assert act.code == codes.ERROR and act.kind == "config"
    assert act.lines[0] == switch.SWITCHED
    assert "its items weren't written" in act.lines[1]
    assert switch.is_moxis(settings()["statusLine"])


# -- restore ----------------------------------------------------------------------------

def test_restore_writes_the_old_line_back():
    old = {"type": "command", "command": "/home/u/line.sh", "padding": 1,
           "refreshInterval": 7, "extra": {"kept": [1, "two", None]}}
    original = {"theme": "dark", "statusLine": old, "hooks": {"Stop": []}}
    write_settings(original)
    assert switch.switch(None, by="test").ok
    act = switch.restore()
    assert act.ok and act.kind == "restored"
    assert act.lines == ["Claude Code's status line is your old one again: "
                         "/home/u/line.sh"]
    assert settings() == original
    assert list(settings()) == list(original)
    assert switch.previous_path().exists()


def test_restore_of_none_removes_the_setting():
    write_settings({"theme": "dark"})
    assert switch.switch(None, by="test").ok
    act = switch.restore()
    assert act.ok
    assert settings() == {"theme": "dark"}
    assert act.lines == ["Claude Code's status line is removed, as it was before Moxi's"]


def test_restore_over_another_command_refuses_and_force_writes():
    old = {"type": "command", "command": "/home/u/line.sh"}
    write_settings({"statusLine": old})
    assert switch.switch(None, by="test").ok
    raw = write_settings({"statusLine": {"type": "command", "command": "/home/u/new.sh"}})
    act = switch.restore()
    assert act.code == codes.REFUSED and act.kind == "not-moxis"
    assert act.lines == ["Claude Code's status line is no longer Moxi's (it runs "
                         "/home/u/new.sh), so restore leaves it; moxi statusline restore "
                         "--force puts the saved one back over it"]
    assert settings_file().read_bytes() == raw
    act = switch.restore(force=True)
    assert act.ok
    assert settings()["statusLine"] == old


def test_restore_over_no_line_refuses_without_force():
    write_settings({"statusLine": {"type": "command", "command": "/home/u/line.sh"}})
    assert switch.switch(None, by="test").ok
    write_settings({})
    act = switch.restore()
    assert act.kind == "not-moxis" and "(it has none)" in act.lines[0]


def test_restore_with_nothing_saved_changes_nothing():
    raw = write_settings({"statusLine": {"type": "command", "command": here()}})
    act = switch.restore()
    assert act.code == codes.REFUSED and act.kind == "nothing-saved"
    assert act.lines[0].startswith("No old status line was saved")
    assert settings_file().read_bytes() == raw
    assert backups() == []


def test_restore_with_a_saved_record_that_cant_be_read_changes_nothing():
    raw = write_settings({"statusLine": {"type": "command", "command": here()}})
    path = switch.previous_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json")
    act = switch.restore()
    assert act.code == codes.ERROR and "is not plain JSON" in act.lines[0]
    assert settings_file().read_bytes() == raw


# -- remove and state --------------------------------------------------------------------

def test_remove_takes_out_moxis_line_and_leaves_another():
    write_settings({"a": 1, "statusLine": {"type": "command", "command": RENDER}})
    act = switch.remove()
    assert act.ok and act.kind == "removed"
    assert settings() == {"a": 1}
    raw = write_settings({"statusLine": {"type": "command", "command": "/x/l.sh"}})
    assert switch.remove().kind == "not-moxis"
    assert settings_file().read_bytes() == raw


def test_state_says_what_is_set_and_saved():
    state = switch.state()
    assert state["set"] is False and state["moxis"] is False and state["saved"] is None
    write_settings({"statusLine": {"type": "command", "command": "/x/l.sh"}})
    switch.switch(None, by="test")
    state = switch.state()
    assert state["moxis"] and state["here"] and state["command"] == here()
    assert state["saved"]["had"] is True
    assert state["saved"]["statusLine"]["command"] == "/x/l.sh"
    write_settings("{oops")
    assert "is not plain JSON" in switch.state()["error"]


def test_project_lines_finds_a_projects_own_line(tmp_path):
    project = tmp_path / "proj"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "settings.json").write_text(
        '{"statusLine": {"type": "command", "command": "./line.sh"}}')
    (project / ".claude" / "settings.local.json").write_text("{broken")
    assert switch.project_lines(project) == [
        (str(project / ".claude" / "settings.json"), "./line.sh")]
    assert switch.project_lines(tmp_path) == []


@pytest.mark.skipif(os.name != "posix", reason="Windows takes no write away by a folder's mode")
def test_a_dir_that_cant_be_written_is_said_and_changes_nothing():
    raw = write_settings({"statusLine": {"type": "command", "command": "/x/l.sh"}})
    settings_dir = settings_file().parent
    settings_dir.chmod(0o500)
    try:
        act = switch.switch(None, by="test")
    finally:
        settings_dir.chmod(0o700)
    assert act.code == codes.ERROR and act.kind == "refused"
    assert "can't be written" in act.lines[0]
    assert settings_file().read_bytes() == raw
