"""statusline's doctor rows (the bytecode row's cases are with the install listener's)."""

from statusline_kit import write_config

import json
import os
import shlex
import sys

import pytest

from moxi import paths
from moxi.doctor import Row
from moxi_statusline import doctor, switch


def test_rows_are_rows_in_order():
    rows = doctor.rows()
    assert all(isinstance(r, Row) and r.module == "statusline" for r in rows)
    assert [r.name for r in rows] == ["command", "bytecode", "sample render", "show",
                                      "module names"]


def test_a_config_that_cant_be_read_is_one_warn_row_and_the_rest_read_the_default():
    write_config('show = ["row", "dir"]\n')
    rows = doctor.rows()
    assert rows[1].name == "config"
    assert rows[1].state == "warn"
    assert "'row' first" in rows[1].detail
    assert [r.name for r in rows[2:]] == ["bytecode", "sample render", "show",
                                          "module names"]


def test_the_sample_render_against_the_budget():
    row = doctor.sample_row(floor=10_000.0)
    assert row.state == "ok"
    assert "the budget is 30000.0 ms (3 times the 10000.0 ms start" in row.detail
    row = doctor.sample_row(floor=0.001)
    assert row.state == "warn"
    assert row.fix == "moxi statusline preview"


def test_the_sample_render_fails_when_the_command_is_not_installed(monkeypatch):
    from moxi_statusline import preview

    monkeypatch.setattr(preview, "SCRIPT", "moxi-statusline-nothing")
    row = doctor.sample_row(floor=10.0)
    assert row.state == "fail"
    assert "moxi-statusline-nothing is not installed" in row.detail


def test_show_items_with_nothing_behind_them(seen):
    seen.module("alpha", "def from_input(p, n):\n    return None\n")
    seen.module("beta")
    assert doctor.show_row(["alpha"]).state == "ok"
    row = doctor.show_row(["alpha", "beta", "nosuchmodule"])
    assert row.state == "warn"
    assert "beta (no status line segment)" in row.detail
    assert "nosuchmodule (not installed)" in row.detail
    assert "alpha" not in row.detail


def test_an_installed_module_named_like_a_base_field(seen):
    assert doctor.names_row().state == "ok"
    seen.module("branch", "def from_input(p, n):\n    return None\n")
    row = doctor.names_row()
    assert row.state == "warn"
    assert row.detail.endswith(": branch")


# -- the statusLine command ------------------------------------------------------------

def _line(command):
    path = paths.claude_home() / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {} if command is None else {"statusLine": {"type": "command",
                                                       "command": command}}
    path.write_text(json.dumps(value))


def test_no_status_line_is_ok():
    row = doctor.command_row()
    assert (row.name, row.state, row.detail) == ("command", "ok",
                                                 "Claude Code sets no status line")


def test_moxis_line_here_is_ok():
    command = switch.render_command()
    _line(command)
    row = doctor.command_row()
    assert (row.state, row.detail) == (
        "ok", f"Moxi's line, {switch.moxis_path({'command': command})}")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows: the line's form follows "
                    "the shell Claude Code runs it with, Git Bash or PowerShell")
def test_moxis_line_written_for_the_other_shell_warns(monkeypatch):
    """Git for Windows installed or removed after the line was written: the line is
    Moxi's, but the shell Claude Code now runs it with can't read its form."""
    from moxi_statusline import shell

    for written, now in (("powershell", "bash"), ("bash", "powershell")):
        _line(switch.render_command(kind=written))
        monkeypatch.setattr(shell, "find", lambda env=None, now=now: shell.Shell(
            now, ("bash", "-c") if now == "bash" else shell.POWERSHELL))
        row = doctor.command_row()
        assert (row.state, row.fix) == ("warn", "moxi statusline use"), row.detail
        assert f"another shell than the one it runs a line with here ({now})" in row.detail
        _line(switch.render_command(kind=now))
        assert doctor.command_row().state == "ok"


def test_moxis_line_at_a_path_that_is_gone_fails():
    _line("/gone/env/bin/moxi-statusline-render")
    row = doctor.command_row()
    assert row.state == "fail"
    assert row.detail == ("Claude Code's status line runs "
                          "/gone/env/bin/moxi-statusline-render, which is not an "
                          "executable here")
    assert row.fix == "moxi statusline use"


def test_moxis_line_in_another_env_warns(tmp_path):
    other = tmp_path / "other" / "bin" / "moxi-statusline-render"
    other.parent.mkdir(parents=True)
    other.write_text("#!/bin/sh\n")
    other.chmod(0o755)
    _line(str(other))
    row = doctor.command_row()
    assert row.state == "warn" and "another environment's render" in row.detail


def test_an_own_line_is_checked_by_its_first_word(tmp_path, monkeypatch):
    script = tmp_path / "line.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    # Written as the shell reads it: Git Bash on Windows drops a bare backslash.
    _line(f"{shlex.quote(script.as_posix())} --flag")
    row = doctor.command_row()
    assert row.state == "ok" and script.as_posix() in row.detail
    tools_dir = tmp_path / "tools"
    tool = tools_dir / ("own-tool.exe" if sys.platform == "win32" else "own-tool")
    tools_dir.mkdir()
    tool.write_text("")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(tools_dir) + os.pathsep + os.environ.get("PATH", ""))
    _line("own-tool -c 'echo hi'")
    assert doctor.command_row().state == "ok"
    _line(str(tmp_path / "missing.sh"))
    row = doctor.command_row()
    assert row.state == "warn" and "doesn't resolve" in row.detail
    _line("no-such-command-anywhere")
    assert doctor.command_row().state == "warn"


def test_settings_that_cant_be_read_warn():
    path = paths.claude_home() / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{bad")
    row = doctor.command_row()
    assert row.state == "warn" and "can't be checked" in row.detail
