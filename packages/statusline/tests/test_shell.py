"""The shell a status line command runs in, found as Claude Code finds it.

The Windows search reads only paths and file names, so it is tested on every system with
the module's platform flag set; a run through the shell found is tested on this system.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from moxi_statusline import pieces, shell

WINDOWS = sys.platform == "win32"


def _file(path: Path, *, executable: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    if executable:
        path.chmod(0o755)
    return path


def _git(root: Path, where: str) -> str:
    """A Git for Windows folder at `root` with git in `where` and bash in `bin`; the
    `PATH` that finds that git."""
    name = "git.exe" if WINDOWS else "git"
    git = _file(root.joinpath(*where.split("/"), name), executable=True)
    _file(root / "bin" / "bash.exe")
    return str(git.parent)


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(shell, "_WINDOWS", True)


def test_posix_runs_sh_c():
    if WINDOWS:
        pytest.skip("POSIX's shell")
    found = shell.find({})
    assert found.kind == "sh" and found.argv("echo hi") == ["sh", "-c", "echo hi"]


@pytest.mark.parametrize("where", ["cmd", "bin", "mingw64/bin"])
def test_windows_takes_gits_own_bash_from_the_git_on_path(windows, tmp_path, where):
    root = tmp_path / "Git"
    found = shell.find({"PATH": _git(root, where)})
    assert found.kind == "bash"
    assert found.argv("echo hi") == [str(root / "bin" / "bash.exe"), "-c", "echo hi"]


def test_windows_takes_the_named_bash_first(windows, tmp_path):
    named = _file(tmp_path / "elsewhere" / "sh.exe")
    found = shell.find({"PATH": _git(tmp_path / "Git", "cmd"),
                        "Claude_Code_Git_Bash_Path": str(named)})
    assert found.program == (str(named), "-c")


@pytest.mark.parametrize("name", ["zsh.exe", "missing/bash.exe"])
def test_windows_ignores_a_named_bash_that_is_not_one_or_not_there(windows, tmp_path, name):
    named = tmp_path / name
    if "missing" not in name:
        _file(named)
    path_dir = _git(tmp_path / "Git", "cmd")
    found = shell.find({"PATH": path_dir, shell.GIT_BASH_PATH_ENV: str(named)})
    assert found.program == (str(tmp_path / "Git" / "bin" / "bash.exe"), "-c")


def test_windows_never_takes_a_bash_only_on_path_and_falls_back_to_powershell(
        windows, tmp_path):
    lone = tmp_path / "System32"
    _file(lone / ("bash.exe" if WINDOWS else "bash"), executable=True)
    found = shell.find({"PATH": str(lone)})
    assert found.kind == "powershell"
    assert found.argv("x") == ["powershell.exe", "-NoProfile", "-NonInteractive",
                               "-Command", "x"]


def test_windows_a_git_with_no_bash_beside_it_falls_back_to_powershell(windows, tmp_path):
    name = "git.exe" if WINDOWS else "git"
    git = _file(tmp_path / "Git" / "cmd" / name, executable=True)
    assert shell.find({"PATH": str(git.parent)}).kind == "powershell"


def test_pieces_under_powershell_measure_nothing_and_say_they_need_bash(windows, tmp_path):
    env = {"PATH": str(tmp_path)}
    piece = pieces.Piece("cache", "cache timer", "exit 0")
    assert pieces.measure(piece, runs=1, env=env) is None
    out = pieces.lines([piece], {"cache": None}, env=env)
    assert out[-2:] == [pieces.NEEDS_BASH, pieces.NEEDS]
    assert shell.GIT_BASH_PATH_ENV in pieces.NEEDS_BASH
    assert not any("not measured" in line for line in out)


def test_a_command_runs_in_the_shell_found_here():
    found = shell.find(os.environ)
    if found.kind == "powershell":
        pytest.skip("no Git Bash on this Windows")
    done = subprocess.run(found.argv('printf "%s" "$input"'), env={**os.environ,
                          "input": "a b"}, capture_output=True, check=False, timeout=30)
    assert done.returncode == 0 and done.stdout == b"a b"
