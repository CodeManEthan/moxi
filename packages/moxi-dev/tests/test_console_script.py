"""The stand-in console script: found the way this system finds one, and runnable."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

import moxi.paths
from moxi_dev import testing
from moxi_dev.testing import console_script


def test_the_scripts_directory_lookup_finds_it(tmp_path, monkeypatch):
    monkeypatch.setattr(moxi.paths, "_scripts", lambda: tmp_path)
    path = console_script(tmp_path, "moxi-demo-relay")
    assert moxi.paths.script("moxi-demo-relay") == path
    assert path.name == ("moxi-demo-relay.cmd" if testing.WINDOWS else "moxi-demo-relay")


def test_a_path_lookup_finds_it(tmp_path):
    path = console_script(tmp_path, "moxi")
    assert os.path.samefile(shutil.which("moxi", path=str(tmp_path)), path)


@pytest.mark.skipif(os.name == "nt", reason="the POSIX shape: a /bin/sh script, 0o755")
def test_on_posix_it_is_a_bare_sh_script_with_the_execute_bits(tmp_path):
    path = console_script(tmp_path, "moxi")
    assert path == tmp_path / "moxi"
    assert path.read_text() == "#!/bin/sh\n"
    assert path.stat().st_mode & 0o777 == 0o755


@pytest.mark.skipif(os.name != "nt", reason="the Windows shape: a .cmd batch file")
def test_on_windows_it_is_a_cmd_file_with_crlf_lines(tmp_path):
    path = console_script(tmp_path, "moxi", "print('a')\nprint('b')\n")
    assert path == tmp_path / "moxi.cmd"
    lines = path.read_bytes().split(b"\r\n")
    assert lines[1:] == [b"print('a')", b"print('b')", b""]
    assert b"\n" not in b"".join(lines)


def test_with_no_source_it_runs_and_exits_0(tmp_path):
    path = console_script(tmp_path, "moxi")
    done = subprocess.run([str(path), "any", "words"], capture_output=True, text=True,
                          timeout=30)
    assert (done.returncode, done.stdout) == (0, "")


def test_its_source_runs_under_this_python_with_the_arguments_and_exit_status(tmp_path):
    out = tmp_path / "out"
    source = ("import sys\n"
              f"open({str(out)!r}, 'w').write('\\n'.join([sys.executable, *sys.argv[1:]]))\n"
              "sys.exit(3)\n")
    path = console_script(tmp_path, "moxi", source)
    done = subprocess.run([str(path), "send", "a b", ""], timeout=30)
    assert done.returncode == 3
    python, *argv = out.read_text().split("\n")
    assert os.path.normcase(python) == os.path.normcase(sys.executable)
    assert argv == ["send", "a b", ""]


def test_a_path_lookup_runs_it_by_its_bare_name(tmp_path):
    out = tmp_path / "out"
    console_script(tmp_path, "moxi",
                   f"import sys\nopen({str(out)!r}, 'w').write(' '.join(sys.argv[1:]))\n")
    found = shutil.which("moxi", path=str(tmp_path))
    subprocess.run([found, "alpha", "x"], check=True, timeout=30)
    assert out.read_text() == "alpha x"
