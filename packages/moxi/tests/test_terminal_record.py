"""The core's record of the terminal's environment: the set it holds, its file, its reader
and the keyboard test.

The `moxi` command's record step is tested in test_terminal_step.py.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time

import pytest

from moxi import files, paths
from moxi.terminal import (FORMAT, NewerFormat, NoRecord, Record, Unreadable, in_set,
                           keyboard, of, read, version, write)
from moxi.terminal import _record

WINDOWS = sys.platform == "win32"
posix_only = pytest.mark.skipif(WINDOWS, reason="POSIX modes and ttys")


# -- the core set -----------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "MOXI_HOME", "MOXI_DEVICE", "MOXI_PROJECTS_DATA_HOME", "MOXI_X_RUNTIME_DIR",
    "MOXI_CACHE_DEVICE", "MOXI_LOADOUT_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
    "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "HOME", "USERPROFILE",
    "HOMEDRIVE", "HOMEPATH", "WIN_PD_OVERRIDE_APPDATA", "WIN_PD_OVERRIDE_LOCAL_APPDATA",
    "CLAUDE_CONFIG_DIR", "CODEX_HOME", "PATH", "PATHEXT"])
def test_the_set_holds_each_name_the_resolvers_read(name):
    assert in_set(name, fold=False)


@pytest.mark.parametrize("name", [
    "MOXI_ANCHOR_HOME", "MOXI_STARTED_BY", "MOXI_CORE_LOCK_FD", "MOXI_CALLER",
    "MOXI__HOME", "MOXI_PROJECTS_TOKEN", "UV_TOOL_DIR", "APPDATA", "LOCALAPPDATA",
    "GITHUB_TOKEN", "ANTHROPIC_API_KEY", "SHELL", "TERM", "XDG_SESSION_ID", "home",
    "moxi_home"])
def test_the_set_holds_nothing_else(name):
    assert not in_set(name, fold=False)


def test_folding_takes_any_spelling_of_a_name():
    assert in_set("Path", fold=True) and in_set("moxi_cache_home", fold=True)
    assert not in_set("Path", fold=False)
    assert not in_set("moxi_anchor_home", fold=True)


def test_the_record_of_an_environment_keeps_only_set_names_with_exact_values():
    env = {"HOME": "/home/ada", "MOXI_HOME": "", "PATH": "/bin:/usr/bin",
           "MOXI_PROJECTS_DATA_HOME": " /srv/p ", "GITHUB_TOKEN": "ghp_secret",
           "MOXI_ANCHOR_HOME": "/t", "MOXI_STARTED_BY": "moxi", "TERM": "xterm"}
    assert of(env, fold=False) == {"HOME": "/home/ada", "MOXI_HOME": "",
                                   "MOXI_PROJECTS_DATA_HOME": " /srv/p ",
                                   "PATH": "/bin:/usr/bin"}


def test_the_record_of_a_windows_environment_upper_cases_names():
    assert of({"Path": r"C:\bin", "UserProfile": r"C:\Users\ada", "temp": "x"},
              fold=True) == {"PATH": r"C:\bin", "USERPROFILE": r"C:\Users\ada"}


def test_the_record_of_no_mapping_is_the_process_environment(monkeypatch):
    monkeypatch.setenv("MOXI_HOME", "/srv/moxi")
    monkeypatch.setenv("SOME_TOKEN", "t")
    held = of()
    assert held["MOXI_HOME"] == "/srv/moxi" and "SOME_TOKEN" not in held


# -- the file -----------------------------------------------------------------------------------

def test_the_record_is_written_at_the_anchor_and_read_back(fence):
    env = {"HOME": "/home/ada", "MOXI_HOME": "", "PATH": "/bin", "API_KEY": "k"}
    path = write(env, moxi="9.9.9")
    assert path == paths.terminal_record() == fence.anchor_home.joinpath(
        *(("moxi", "data", "moxi") if WINDOWS else (".local", "share", "moxi", "moxi")),
        "terminal.json")
    body = json.loads(path.read_text("utf-8"))
    assert body == {"format": 1, "moxi": "9.9.9",
                    "env": {"HOME": "/home/ada", "MOXI_HOME": "", "PATH": "/bin"}}
    got = read()
    assert got == Record(path, {"HOME": "/home/ada", "MOXI_HOME": "", "PATH": "/bin"},
                         "9.9.9", FORMAT)
    assert "k" not in path.read_text("utf-8")


@posix_only
def test_the_record_is_only_the_accounts_to_read(fence):
    path = write({"HOME": "/h"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_write_names_this_core_by_default(fence):
    write({})
    assert read().moxi == version() != ""


def test_a_write_replaces_a_newer_format(fence):
    path = paths.terminal_record()
    files.atomic_write(path, json.dumps({"format": 2, "moxi": "3.0", "env": {}}))
    write({"HOME": "/h"}, moxi="1.0")
    assert read() == Record(path, {"HOME": "/h"}, "1.0")


def test_a_home_or_store_variable_does_not_move_the_file(fence, monkeypatch, tmp_path):
    before = paths.terminal_record()
    for name in ("HOME", "MOXI_HOME", "XDG_DATA_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    assert paths.terminal_record() == before
    assert write() == before


def test_no_place_for_the_record_raises_no_anchor(monkeypatch):
    monkeypatch.setenv(paths.ANCHOR_HOME, "relative")
    with pytest.raises(paths.NoAnchor):
        read()
    with pytest.raises(paths.NoAnchor):
        write({})


@pytest.mark.parametrize("env", [
    {}, {"HOME": "/home/ada"}, {"A": "", "B": "x", "PATH": "/bin:/usr/bin"},
    {"HOME": 'q"uote\\back\nnew\ttab\rcr\bbs\ffeed\x01\x7f'},
    {"HOME": "/home/zo\u00eb/\u65e5\u672c/\U0001f600"},
    {"HOME": "/home/\udc80\udcff"},              # undecodable bytes, as POSIX keeps them
    {"PATH": "C:\\Windows;C:\\Program Files\\Git\\cmd"},
])
def test_the_text_is_the_json_of_the_record(env):
    got = _record.text(env, "0.2.0")
    body = {"format": 1, "moxi": "0.2.0", "env": dict(sorted(env.items()))}
    assert got == json.dumps(body, indent=2) + "\n"
    assert got.isascii() and json.loads(got) == body


def test_the_same_environment_gives_the_same_bytes(fence):
    env = {"PATH": "/b", "HOME": "/h"}
    first = write(env).read_bytes()
    assert write(dict(reversed(env.items()))).read_bytes() == first


# -- reading ------------------------------------------------------------------------------------

def test_a_missing_file_is_no_record(fence):
    assert read() == NoRecord(paths.terminal_record())


def test_a_missing_folder_above_it_is_no_record(fence, tmp_path):
    (tmp_path / "file").write_text("")
    assert read(tmp_path / "file" / "terminal.json") == NoRecord(
        tmp_path / "file" / "terminal.json")


@pytest.mark.parametrize("data", [
    b"", b"{", b"\xff\xfe", b"[]", b"null", b'"x"', b"{}",
    b'{"format": 1}',
    b'{"format": 1, "moxi": "1", "env": []}',
    b'{"format": 1, "moxi": 1, "env": {}}',
    b'{"format": 1, "moxi": "1", "env": {"HOME": 1}}',
    b'{"format": "1", "moxi": "1", "env": {}}',
    b'{"format": true, "moxi": "1", "env": {}}',
    b'{"format": 1.0, "moxi": "1", "env": {}}',
    b'{"format": 0, "moxi": "1", "env": {}}',
    b'{"format": -3, "moxi": "1", "env": {}}',
])
def test_a_file_not_of_this_shape_is_no_record(fence, data):
    path = paths.terminal_record()
    files.atomic_write(path, data)
    assert read() == NoRecord(path)


def test_a_newer_format_names_both_versions(fence):
    path = paths.terminal_record()
    files.atomic_write(path, b'{"format": 2, "moxi": "0.9.0", "whatever": []}')
    assert read() == NewerFormat(path, 2, "0.9.0", version(), 1)


def test_a_newer_format_without_a_version_names_none(fence):
    path = paths.terminal_record()
    files.atomic_write(path, b'{"format": 7}')
    assert read() == NewerFormat(path, 7, None, version())


def test_extra_keys_at_format_1_are_kept_out(fence):
    path = paths.terminal_record()
    files.atomic_write(path, b'{"format": 1, "moxi": "1", "env": {"HOME": "/h"}, "x": 2}')
    assert read() == Record(path, {"HOME": "/h"}, "1")


def test_a_read_error_that_persists_is_unreadable(fence, monkeypatch):
    path = write({"HOME": "/h"})
    calls = []

    def refuse(self):
        calls.append(time.monotonic())
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(type(path), "read_bytes", refuse)
    started = time.monotonic()
    got = read(retry=0.2)
    assert isinstance(got, Unreadable) and got.path == path
    assert isinstance(got.error, PermissionError)
    assert time.monotonic() - started >= 0.2 and len(calls) > 1


def test_the_retry_is_the_writers_replace_window():
    assert read.__kwdefaults__["retry"] == files.REPLACE_RETRY


def test_a_read_error_that_clears_within_the_retry_reads_the_record(fence, monkeypatch):
    path = write({"HOME": "/h"}, moxi="1")
    real = type(path).read_bytes
    left = [3]

    def refuse_a_while(self):
        if left[0]:
            left[0] -= 1
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(type(path), "read_bytes", refuse_a_while)
    assert read() == Record(path, {"HOME": "/h"}, "1")


def test_no_retry_reads_once(fence, monkeypatch):
    path = write({"HOME": "/h"})
    calls = []

    def refuse(self):
        calls.append(1)
        raise OSError(5, "I/O error")

    monkeypatch.setattr(type(path), "read_bytes", refuse)
    assert isinstance(read(retry=0), Unreadable) and calls == [1]


@pytest.mark.skipif(not WINDOWS, reason="Windows refuses an open during a replace")
def test_a_read_during_the_writers_replaces_reads_a_record(fence):
    path = write({"HOME": "a"}, moxi="1")
    stop = threading.Event()

    def writer():
        n = 0
        while not stop.is_set():
            n += 1
            write({"HOME": str(n)}, moxi="1")

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        for _ in range(200):
            assert isinstance(read(), Record)
    finally:
        stop.set()
        thread.join()
    assert isinstance(read(path), Record)


def test_windows_names_read_upper_cased(fence, monkeypatch):
    path = paths.terminal_record()
    files.atomic_write(path, b'{"format": 1, "moxi": "1", "env": {"Path": "x"}}')
    monkeypatch.setattr(_record, "_WINDOWS", True)
    assert read().env == {"PATH": "x"}


# -- the core's version -------------------------------------------------------------------------

def test_the_version_is_the_installed_distributions():
    from importlib import metadata

    _record._version = None
    assert version() == metadata.version("moxi")


def test_the_version_is_found_on_the_path_without_importlib_metadata(tmp_path, monkeypatch):
    (tmp_path / "moxi_dev-5.0.dist-info").mkdir()
    (tmp_path / "moxi-4.2.1.dist-info").mkdir()
    monkeypatch.setattr(sys, "path", ["", str(tmp_path / "missing"), str(tmp_path)])
    assert _record._find_version() == "4.2.1"


# -- the keyboard -------------------------------------------------------------------------------

@posix_only
def test_a_terminal_on_the_input_is_a_keyboard():
    leader, follower = os.openpty()
    try:
        with os.fdopen(follower, "rb", closefd=False) as stdin:
            assert keyboard(stdin)
    finally:
        os.close(leader)
        os.close(follower)


def test_a_pipe_on_the_input_is_no_keyboard():
    r, w = os.pipe()
    try:
        with os.fdopen(r, "rb", closefd=False) as stdin:
            assert not keyboard(stdin)
    finally:
        os.close(r)
        os.close(w)


def test_a_file_a_closed_input_or_none_is_no_keyboard(tmp_path):
    with open(tmp_path / "in", "wb+") as stdin:
        assert not keyboard(stdin)
    assert not keyboard(stdin)

    class NoFileno:
        pass

    assert not keyboard(NoFileno())


@pytest.mark.parametrize("name", [
    r"\msys-dd50a72ab4668b33-pty0-from-master",
    r"\cygwin-e022582115c10879-pty12-from-master",
    r"\\.\pipe\msys-1888ae32e00d56aa-pty3-from-master",
    r"\\.\pipe\cygwin-0a-pty0-from-master",
    # Current Git for Windows adds `-nat` (seen with MSYS=disable_pcon on Git 2.56).
    r"\msys-1888ae32e00d56aa-pty0-from-master-nat",
    r"\cygwin-e022582115c10879-pty12-from-master-nat",
    r"\\.\pipe\msys-1888ae32e00d56aa-pty3-from-master-NAT",
])
def test_a_cygwin_or_msys_pty_input_pipe_is_named_so(name):
    assert _record.is_pty_pipe(name)


@pytest.mark.parametrize("name", [
    r"\msys-dd50a72ab4668b33-pty0-to-master",
    r"\msys-dd50a72ab4668b33-pty0-from-master-cyg",
    r"\Win32Pipes.0000162c.00000002",
    r"\msys-xyz-pty0-from-master",
    r"\other-dd50a72ab4668b33-pty0-from-master",
    r"\msys-dd50-ptyX-from-master",
    r"\msys-dd50a72ab4668b33-pty0-to-master-nat",
    r"\msys-dd50a72ab4668b33-pty0-from-master-nat-nat",
    r"\msys-dd50a72ab4668b33-pty0-from-master-natx",
    # A script's pipe, as Git Bash names it for `echo hi | program`.
    r"\1888ae32e00d56aa-9740-pipe-nt-0x15",
    "",
])
def test_other_pipes_are_not(name):
    assert not _record.is_pty_pipe(name)


@posix_only
def test_on_windows_a_terminal_is_a_keyboard_only_as_a_console_or_the_pty_pipe(monkeypatch):
    """`isatty()` alone counts on POSIX; on Windows it is true for `NUL` too, so there the
    handle must be a console's or the pty pipe."""
    asked = []
    monkeypatch.setattr(_record, "console", lambda fd: asked.append(fd) or False)
    monkeypatch.setattr(_record, "pty_pipe", lambda fd: False)
    leader, follower = os.openpty()
    try:
        with os.fdopen(follower, "rb", closefd=False) as stdin:
            assert keyboard(stdin) and not asked
            monkeypatch.setattr(_record, "_WINDOWS", True)
            assert not keyboard(stdin) and asked == [follower]
            monkeypatch.setattr(_record, "console", lambda fd: True)
            assert keyboard(stdin)
    finally:
        os.close(leader)
        os.close(follower)


def test_the_console_test_is_false_off_windows_and_for_a_bad_descriptor():
    r, w = os.pipe()
    os.close(r)
    os.close(w)
    assert not _record.console(r)


def test_a_pipe_counts_only_on_windows(monkeypatch):
    monkeypatch.setattr(_record, "pty_pipe", lambda fd: True)
    monkeypatch.setattr(_record, "console", lambda fd: False)
    r, w = os.pipe()
    try:
        with os.fdopen(r, "rb", closefd=False) as stdin:
            monkeypatch.setattr(_record, "_WINDOWS", False)
            assert not keyboard(stdin)
            monkeypatch.setattr(_record, "_WINDOWS", True)
            assert keyboard(stdin)
    finally:
        os.close(r)
        os.close(w)


@pytest.mark.skipif(not WINDOWS, reason="named pipes")
@pytest.mark.parametrize(("name", "expected"), [
    ("msys-0123456789abcdef-pty{n}-from-master", True),
    ("cygwin-0123456789abcdef-pty{n}-from-master", True),
    ("msys-0123456789abcdef-pty{n}-from-master-nat", True),
    ("cygwin-0123456789abcdef-pty{n}-from-master-nat", True),
    ("msys-0123456789abcdef-pty{n}-to-master", False),
    ("moxi-test-pipe-{n}", False),
])
def test_a_named_pipe_on_the_input_of_a_child(name, expected):
    """A child whose input is the client end of a pipe of this name sees a keyboard
    exactly when the name is a pty's input side."""
    import subprocess

    import _winapi
    import msvcrt

    full = "\\\\.\\pipe\\" + name.format(n=os.getpid())
    server = _winapi.CreateNamedPipe(
        full, 0x2, 0,  # outbound; a byte pipe that waits
        1, 4096, 4096, 0, _winapi.NULL)
    try:
        client = _winapi.CreateFile(full, _winapi.GENERIC_READ, 0, _winapi.NULL,
                                    _winapi.OPEN_EXISTING, 0, _winapi.NULL)
        fd = msvcrt.open_osfhandle(client, os.O_RDONLY)
        code = ("import sys; from moxi.terminal import keyboard; "
                "print(keyboard())")
        with os.fdopen(fd, "rb") as stdin:
            done = subprocess.run([sys.executable, "-c", code], stdin=stdin,
                                  capture_output=True, text=True, timeout=60)
        assert done.stdout.strip() == str(expected), done.stderr
    finally:
        _winapi.CloseHandle(server)


@pytest.mark.skipif(not WINDOWS, reason="anonymous pipes")
def test_a_scripts_pipe_into_a_child_is_no_keyboard():
    import subprocess

    code = "from moxi.terminal import keyboard; print(keyboard())"
    done = subprocess.run([sys.executable, "-c", code], input="", capture_output=True,
                          text=True, timeout=60)
    assert done.stdout.strip() == "False", done.stderr


# -- the keyboard on Windows, live: NUL, a new console, moxi.proc.run's child --------------

_SAYS = ("import sys; from moxi.terminal import keyboard; "
         "print(sys.stdin is not None and sys.stdin.isatty(), keyboard())")


@pytest.mark.skipif(not WINDOWS, reason="the Windows console")
def test_nul_on_the_input_is_no_keyboard_though_isatty_says_true():
    import subprocess

    done = subprocess.run([sys.executable, "-c", _SAYS], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=60)
    assert done.stdout.split() == ["True", "False"], done.stderr


@pytest.mark.skipif(not WINDOWS, reason="the Windows console")
def test_a_redirect_from_nul_is_no_keyboard(tmp_path):
    import subprocess

    script = tmp_path / "says.py"
    script.write_text(_SAYS)
    done = subprocess.run(f'"{sys.executable}" "{script}" < NUL', shell=True,
                          capture_output=True, text=True, timeout=60)
    assert done.stdout.split() == ["True", "False"], done.stderr


@pytest.mark.skipif(not WINDOWS, reason="the Windows console")
def test_a_new_console_on_the_input_is_a_keyboard(tmp_path):
    """A child started with a console of its own (as a terminal window starts a shell)
    has that console on its input."""
    import subprocess

    out = tmp_path / "out.txt"
    code = ("import sys; from moxi.terminal import keyboard; "
            f"open({str(out)!r}, 'w').write('%s %s' % "
            "(sys.stdin is not None and sys.stdin.isatty(), keyboard()))")
    proc = subprocess.Popen([sys.executable, "-c", code],
                            creationflags=subprocess.CREATE_NEW_CONSOLE)
    assert proc.wait(timeout=60) == 0
    assert out.read_text().split() == ["True", "True"]


@pytest.mark.skipif(not WINDOWS, reason="the Windows console")
def test_a_child_of_moxi_proc_run_is_no_keyboard():
    from moxi import proc

    done = proc.run([sys.executable, "-c", _SAYS], timeout=60, input=None)
    assert done.stdout.decode().split() == ["True", "False"], done.stderr
