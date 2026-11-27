"""The kit's fence: the environment it leaves, the writes and connects it refuses."""

from __future__ import annotations

import dataclasses
import ntpath
import os
import shutil
import socket
import socketserver
import sqlite3
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from moxi import paths

from moxi_dev import temp, testing
from moxi_dev.testing import FenceError

#: The tests that need a POSIX name: the password database, Unix sockets or /tmp.
POSIX = pytest.mark.skipif(sys.platform == "win32",
                           reason="the password database, Unix sockets and /tmp are POSIX")


def real_home() -> Path:
    """The user's real home, read from the password database: the fence moves only
    $HOME. POSIX only."""
    import pwd

    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def refused(action) -> str:
    """Run `action`, which the fence must refuse; take the refusal off the record, so
    this test can go on to pass, and return its message."""
    start = len(testing._GUARD.breaches)
    with pytest.raises(FenceError) as caught:
        action()
    assert len(testing._GUARD.breaches) == start + 1
    del testing._GUARD.breaches[start:]
    return str(caught.value)


def stray() -> Path:
    """A name in the real home that nothing else uses."""
    return real_home() / f".moxi-dev-fence-probe-{uuid.uuid4().hex}"


# --- the environment -------------------------------------------------------------------

def test_the_env_is_what_the_fence_says(fence, tmp_path):
    current = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    assert current == {k: v for k, v in fence.env.items() if k != "PYTEST_CURRENT_TEST"}
    modules = testing._modules()
    assert modules, "the workspace installs Moxi modules; none found"
    expected = set(testing.HARNESS_HOMES) | {"XDG_RUNTIME_DIR", "TMPDIR",
                                              testing.ANCHOR_HOME_ENV,
                                              testing.FENCED_ENV} | {
        f"MOXI_{m.upper()}_HOME" for m in modules}
    kept = set(testing.KEPT_ENV)
    if testing.WINDOWS:
        expected |= set(testing.WINDOWS_HOMES) | {"HOMEDRIVE", "HOMEPATH", "TEMP", "TMP"}
        kept |= set(testing.WINDOWS_KEPT_ENV)
    for key in os.environ:
        assert (key in expected or key in kept or key.startswith("LC_")), key
    assert expected <= set(os.environ)
    assert "PATH" in os.environ
    assert os.environ["HOME"] == str(tmp_path / "home") == str(fence.home)
    assert fence.home.is_dir()
    for key, rel in testing.HARNESS_HOMES.items():
        assert Path(os.environ[key]) == fence.home / rel
        assert Path(os.environ[key]).is_relative_to(tmp_path)
    assert os.environ["XDG_RUNTIME_DIR"] == str(fence.runtime)
    assert os.environ["TMPDIR"] == str(fence.short) == tempfile.gettempdir()
    # The mark a run started from this test reads as nested in it.
    assert os.environ[testing.FENCED_ENV] == str(fence.short)
    assert fence.runtime.is_dir()
    if not testing.WINDOWS:         # Windows keeps no POSIX mode: the profile's ACL does
        assert (fence.runtime.stat().st_mode & 0o777) == 0o700
    for module in modules:
        home = Path(os.environ[f"MOXI_{module.upper()}_HOME"])
        assert home == fence.module_home(module)
        assert home.parent == (Path(os.environ["LOCALAPPDATA"]) / "moxi" if testing.WINDOWS
                               else fence.short)
    # The account's folder, which places the core's anchor, is the test's own.
    anchor = Path(os.environ[testing.ANCHOR_HOME_ENV])
    assert anchor == fence.anchor_home
    assert anchor == (Path(os.environ["LOCALAPPDATA"]) if testing.WINDOWS else fence.home)
    # Short enough that a socket a few directories under it fits in sun_path, where
    # Python has Unix sockets (not on Windows).
    if hasattr(socket, "AF_UNIX"):
        assert len(str(fence.runtime)) < 40


#: A Windows user's own environment, as a test process there inherits it.
WINDOWS_SOURCE = {
    "PATH": "C:\\Windows\\system32;C:\\Users\\ann\\.local\\bin", "SYSTEMROOT": "C:\\Windows",
    "SYSTEMDRIVE": "C:", "WINDIR": "C:\\Windows",
    "COMSPEC": "C:\\Windows\\system32\\cmd.exe", "PATHEXT": ".COM;.EXE;.BAT;.CMD",
    "OS": "Windows_NT", "PROCESSOR_ARCHITECTURE": "AMD64", "NUMBER_OF_PROCESSORS": "4",
    "USERNAME": "ann", "USERDOMAIN": "PC", "COMPUTERNAME": "PC",
    "USERPROFILE": "C:\\Users\\ann", "HOMEDRIVE": "C:", "HOMEPATH": "\\Users\\ann",
    "APPDATA": "C:\\Users\\ann\\AppData\\Roaming",
    "LOCALAPPDATA": "C:\\Users\\ann\\AppData\\Local",
    "TEMP": "C:\\Users\\ann\\AppData\\Local\\Temp",
    "TMP": "C:\\Users\\ann\\AppData\\Local\\Temp",
    "ONEDRIVE": "C:\\Users\\ann\\OneDrive", "PUBLIC": "C:\\Users\\Public",
    "PROGRAMDATA": "C:\\ProgramData", "PSMODULEPATH": "C:\\Users\\ann\\Documents\\ps",
    "WIN_PD_OVERRIDE_APPDATA": "C:\\Users\\ann\\AppData\\Roaming",
    "ANTHROPIC_API_KEY": "k"}


def test_the_fence_clears_everything_else(monkeypatch):
    monkeypatch.setattr(testing, "WINDOWS", False)
    env = testing._fenced_env(
        {"PATH": "/bin", "LANG": "C.UTF-8", "LC_ALL": "C", "OPENCODE_CONFIG_CONTENT": "{}",
         "OPENCODE_SERVER_PASSWORD": "x", "MOXI_COMMS_STATE_HOME": "/real",
         "ANTHROPIC_API_KEY": "k", "SSH_AUTH_SOCK": "/run/agent", "GIT_DIR": "/repo",
         # Off Windows, Windows's own variables are cleared like any other.
         **{k: v for k, v in WINDOWS_SOURCE.items() if k != "PATH"}},
        PurePosixPath("/t/home"), PurePosixPath("/s/run"), PurePosixPath("/s"), ["comms"])
    assert env == {
        "PATH": "/bin", "LANG": "C.UTF-8", "LC_ALL": "C",
        "HOME": "/t/home", "XDG_CONFIG_HOME": "/t/home/.config",
        "XDG_DATA_HOME": "/t/home/.local/share", "XDG_STATE_HOME": "/t/home/.local/state",
        "XDG_CACHE_HOME": "/t/home/.cache", "CLAUDE_CONFIG_DIR": "/t/home/.claude",
        "CODEX_HOME": "/t/home/.codex", "XDG_RUNTIME_DIR": "/s/run",
        "MOXI_COMMS_HOME": "/s/comms", "MOXI_ANCHOR_HOME": "/t/home"}


@pytest.mark.parametrize("value", ["1", "0", ""])
def test_the_real_supervisor_opt_in_passes_the_fence_unchanged(monkeypatch, value):
    monkeypatch.setattr(testing, "WINDOWS", False)
    source = {"PATH": "/bin", testing.REAL_SUPERVISOR_ENV: value}
    env = testing._fenced_env(source, PurePosixPath("/t/home"), PurePosixPath("/s/run"),
                              PurePosixPath("/s"), [])
    assert env[testing.REAL_SUPERVISOR_ENV] == value


def test_the_real_supervisor_opt_in_stays_unset_when_unset(monkeypatch):
    monkeypatch.setattr(testing, "WINDOWS", False)
    env = testing._fenced_env({"PATH": "/bin"}, PurePosixPath("/t/home"),
                              PurePosixPath("/s/run"), PurePosixPath("/s"), [])
    assert testing.REAL_SUPERVISOR_ENV not in env


#: A fenced test that reports what it sees of the real-supervisor opt-in at call time.
SUPERVISOR_PROBE = """
import os


def test_reads_the_opt_in():
    print("opt-in:", repr(os.environ.get("MOXI_TEST_REAL_SUPERVISOR")))
"""


@pytest.mark.parametrize("value", ["1", None])
def test_a_fenced_test_sees_the_real_supervisor_opt_in_as_pytest_started(
        pytester, monkeypatch, value):
    if value is None:
        monkeypatch.delenv(testing.REAL_SUPERVISOR_ENV, raising=False)
    else:
        monkeypatch.setenv(testing.REAL_SUPERVISOR_ENV, value)
    pytester.makeconftest('pytest_plugins = ["moxi_dev.testing"]\n')
    pytester.makepyfile(test_probe=SUPERVISOR_PROBE)
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines([f"*opt-in: {value!r}"])


def test_on_windows_the_fence_moves_the_user_s_dirs_and_keeps_the_system_s(
        monkeypatch):
    monkeypatch.setattr(testing, "WINDOWS", True)
    home, short = PureWindowsPath("D:/t/home"), PureWindowsPath("D:/s")
    env = testing._fenced_env(WINDOWS_SOURCE, home, short / "run", short, ["comms"])
    assert env == {
        "PATH": WINDOWS_SOURCE["PATH"], "SYSTEMROOT": "C:\\Windows", "SYSTEMDRIVE": "C:",
        "WINDIR": "C:\\Windows", "COMSPEC": "C:\\Windows\\system32\\cmd.exe",
        "PATHEXT": ".COM;.EXE;.BAT;.CMD", "OS": "Windows_NT",
        "PROCESSOR_ARCHITECTURE": "AMD64", "NUMBER_OF_PROCESSORS": "4", "USERNAME": "ann",
        "HOME": "D:\\t\\home", "XDG_CONFIG_HOME": "D:\\t\\home\\.config",
        "XDG_DATA_HOME": "D:\\t\\home\\.local\\share",
        "XDG_STATE_HOME": "D:\\t\\home\\.local\\state",
        "XDG_CACHE_HOME": "D:\\t\\home\\.cache", "CLAUDE_CONFIG_DIR": "D:\\t\\home\\.claude",
        "CODEX_HOME": "D:\\t\\home\\.codex", "XDG_RUNTIME_DIR": "D:\\s\\run",
        "MOXI_COMMS_HOME": "D:\\t\\home\\AppData\\Local\\moxi\\comms",
        "USERPROFILE": "D:\\t\\home", "HOMEDRIVE": "D:", "HOMEPATH": "\\t\\home",
        "LOCALAPPDATA": "D:\\t\\home\\AppData\\Local",
        "APPDATA": "D:\\t\\home\\AppData\\Roaming",
        "WIN_PD_OVERRIDE_LOCAL_APPDATA": "D:\\t\\home\\AppData\\Local",
        "WIN_PD_OVERRIDE_APPDATA": "D:\\t\\home\\AppData\\Roaming",
        "WIN_PD_OVERRIDE_USER_PROGRAM_FILES": "D:\\t\\home\\AppData\\Local\\Programs",
        "WIN_PD_OVERRIDE_PROGRAMS":
            "D:\\t\\home\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs",
        "WIN_PD_OVERRIDE_TEMPLATES":
            "D:\\t\\home\\AppData\\Roaming\\Microsoft\\Windows\\Templates",
        "WIN_PD_OVERRIDE_PERSONAL": "D:\\t\\home\\Documents",
        "WIN_PD_OVERRIDE_DOWNLOADS": "D:\\t\\home\\Downloads",
        "WIN_PD_OVERRIDE_DESKTOPDIRECTORY": "D:\\t\\home\\Desktop",
        "WIN_PD_OVERRIDE_MYPICTURES": "D:\\t\\home\\Pictures",
        "WIN_PD_OVERRIDE_MYVIDEO": "D:\\t\\home\\Videos",
        "WIN_PD_OVERRIDE_MYMUSIC": "D:\\t\\home\\Music",
        "TEMP": "D:\\s", "TMP": "D:\\s",
        "MOXI_ANCHOR_HOME": "D:\\t\\home\\AppData\\Local"}
    # Only PATH, kept as it is everywhere, still names the real profile; and `~` is the
    # test's home, the way Python on Windows finds it.
    assert {k for k, v in env.items() if "\\users\\ann" in v.lower()} == {"PATH"}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert ntpath.expanduser("~\\x") == "D:\\t\\home\\x"
    monkeypatch.delenv("USERPROFILE")
    assert ntpath.expanduser("~\\x") == "D:\\t\\home\\x"      # from HOMEDRIVE + HOMEPATH


def test_on_windows_every_module_home_is_in_the_tests_profile_spelled_long(monkeypatch):
    # The runner's TEMP, which the short directory inherits, spelled with an 8.3 name;
    # Windows's realpath gives each part's long name.
    monkeypatch.setattr(testing, "WINDOWS", True)
    monkeypatch.setattr(ntpath, "realpath",
                        lambda path: ntpath.normpath(os.fspath(path)).replace("RUNNER~1", "runneradmin"))
    temp = PureWindowsPath("C:/Users/RUNNER~1/AppData/Local/Temp")
    home, short = temp / "pytest-of-r" / "pytest-0" / "t0" / "home", temp / "mx-1"
    modules = ["comms", "turns", "ui"]
    env = testing._fenced_env(WINDOWS_SOURCE, home, short / "run", short, modules)
    local = PureWindowsPath(env["LOCALAPPDATA"])
    assert local == PureWindowsPath(
        "C:/Users/runneradmin/AppData/Local/Temp/pytest-of-r/pytest-0/t0/home/AppData/Local")
    for module in modules:
        module_home = PureWindowsPath(env[f"MOXI_{module.upper()}_HOME"])
        assert module_home == local / "moxi" / module
    assert not [key for key, value in env.items() if "~1" in value]
    assert env["TEMP"] == env["TMP"] == "C:\\Users\\runneradmin\\AppData\\Local\\Temp\\mx-1"
    assert env["XDG_RUNTIME_DIR"] == env["TEMP"] + "\\run"


def test_a_profile_dir_is_new_and_in_the_tests_own_profile(fence):
    store = fence.profile_dir("store")
    assert store.is_dir() and store.parent == fence.home
    if testing.WINDOWS:
        assert store.parent == Path(os.environ["USERPROFILE"])
    with pytest.raises(FileExistsError):
        fence.profile_dir("store")


def test_the_real_accounts_anchor_is_never_the_tests(fence):
    from moxi import paths

    assert paths.anchor_dir().is_relative_to(fence.anchor_home)
    assert paths.terminal_record().is_relative_to(fence.home.parent)


# --- the terminal record ---------------------------------------------------------------

def test_terminal_record_writes_the_recorded_names_under_the_tests_anchor(
        fence, terminal_record, monkeypatch):
    import json
    import stat
    from importlib import metadata

    from moxi import paths

    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-token")
    monkeypatch.setenv("MOXI_PROJECTS_DATA_HOME", "")
    path = terminal_record()
    assert path == paths.terminal_record() and path.is_relative_to(fence.anchor_home)
    body = json.loads(path.read_text())
    assert body["format"] == testing.RECORD_FORMAT == 1
    from moxi import terminal

    assert terminal.read(path).env == body["env"] and terminal.FORMAT == 1
    assert body["moxi"] == metadata.version("moxi")
    recorded = body["env"]
    assert recorded["HOME"] == os.environ["HOME"] and recorded["PATH"] == os.environ["PATH"]
    assert recorded["MOXI_PROJECTS_DATA_HOME"] == ""          # empty is kept as empty
    assert "ANTHROPIC_API_KEY" not in recorded and "TMPDIR" not in recorded
    assert testing.ANCHOR_HOME_ENV not in recorded and "LANG" not in recorded
    if not testing.WINDOWS:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    given = terminal_record({"HOME": "/h", "MOXI_HOME": "/m", "SECRET": "x"}, version="9.9")
    assert json.loads(given.read_text()) == {
        "format": 1, "moxi": "9.9", "env": {"HOME": "/h", "MOXI_HOME": "/m"}}


# --- uv in a child ---------------------------------------------------------------------

def test_uv_finds_its_dirs_as_uv_does():
    dirs, join = testing._uv_dirs, os.path.join     # joined by this system's separator
    assert dirs({}, "/h", is_windows=False) == {
        "UV_CACHE_DIR": join("/h", ".cache", "uv"),
        "UV_PYTHON_INSTALL_DIR": join("/h", ".local", "share", "uv", "python")}
    assert dirs({"XDG_CACHE_HOME": "/c", "XDG_DATA_HOME": "/d"}, "/h", is_windows=False) == {
        "UV_CACHE_DIR": join("/c", "uv"), "UV_PYTHON_INSTALL_DIR": join("/d", "uv", "python")}
    assert dirs({"UV_CACHE_DIR": "/uc", "UV_PYTHON_INSTALL_DIR": "/up", "XDG_CACHE_HOME": "/c",
                 "XDG_DATA_HOME": "/d"}, "/h", is_windows=False) == {
        "UV_CACHE_DIR": "/uc", "UV_PYTHON_INSTALL_DIR": "/up"}
    # An empty value is unset, as uv reads it.
    assert dirs({"UV_CACHE_DIR": "", "XDG_DATA_HOME": ""}, "/h", is_windows=False) == \
        dirs({}, "/h", is_windows=False)


def test_on_windows_uv_finds_its_dirs_as_uv_does(monkeypatch, tmp_path):
    local, roaming, home = tmp_path / "L", tmp_path / "R", tmp_path / "H"
    older = tmp_path / "R2"
    (older / "uv").mkdir(parents=True)
    (older / "uv" / "data").write_text("")
    # Made before the platform is patched: the fence judges this test's own writes by it.
    monkeypatch.setattr(testing, "WINDOWS", True)
    env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
    assert testing._uv_dirs(env, str(home)) == {
        "UV_CACHE_DIR": str(local / "uv" / "cache"),
        "UV_PYTHON_INSTALL_DIR": str(roaming / "uv" / "python")}
    # Without the two folders' variables, the folders under the home.
    assert testing._uv_dirs({}, str(home)) == {
        "UV_CACHE_DIR": str(home / "AppData" / "Local" / "uv" / "cache"),
        "UV_PYTHON_INSTALL_DIR": str(home / "AppData" / "Roaming" / "uv" / "python")}
    # uv goes on using its older data folder where it is there, even as a file.
    assert testing._uv_dirs({"APPDATA": str(older)}, str(home))[
        "UV_PYTHON_INSTALL_DIR"] == str(older / "uv" / "data" / "python")
    assert testing._uv_dirs({**env, "UV_PYTHON_INSTALL_DIR": "P"}, str(home))[
        "UV_PYTHON_INSTALL_DIR"] == "P"
    # Off Windows the same environment gives the XDG places, whatever is in APPDATA.
    monkeypatch.setattr(testing, "WINDOWS", False)
    assert testing._uv_dirs(env, str(home)) == {
        "UV_CACHE_DIR": str(home / ".cache" / "uv"),
        "UV_PYTHON_INSTALL_DIR": str(home / ".local" / "share" / "uv" / "python")}


def test_uv_env_carries_the_real_uv_and_leaves_this_process_fenced(fence, uv_env):
    real = testing._SESSION["uv"]
    assert {k: uv_env[k] for k in real} == real
    assert not Path(real["UV_CACHE_DIR"]).is_relative_to(fence.home)
    assert not Path(real["UV_PYTHON_INSTALL_DIR"]).is_relative_to(fence.home)
    assert uv_env["UV_PYTHON_DOWNLOADS"] == "never"
    assert uv_env["HOME"] == str(fence.home)
    added = {*real, "UV_PYTHON_DOWNLOADS", "PYTEST_CURRENT_TEST"}
    assert {k: v for k, v in uv_env.items() if k not in added} == \
        {k: v for k, v in os.environ.items() if k not in added}
    for key in (*real, "UV_PYTHON_DOWNLOADS"):
        assert key not in os.environ


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")
def test_uv_reads_the_real_cache_only_through_uv_env(fence, uv_env):
    def cache_dir(env):
        return subprocess.run(["uv", "cache", "dir"], env=env, capture_output=True,
                              text=True, check=True, timeout=60).stdout.strip()

    assert cache_dir(uv_env) == uv_env["UV_CACHE_DIR"]
    own = ("AppData", "Local", "uv", "cache") if testing.WINDOWS else (".cache", "uv")
    assert Path(cache_dir(dict(os.environ))) == fence.home.joinpath(*own)


UV_PROBE = '''
import os

CACHE, DATA, DATA_VAR = {cache!r}, {data!r}, {data_var!r}


def test_uv_env_is_read_at_the_start(uv_env):
    assert uv_env["UV_CACHE_DIR"] == CACHE
    assert uv_env["UV_PYTHON_INSTALL_DIR"] == os.path.join(DATA, "uv", "python")
    assert "UV_CACHE_DIR" not in os.environ
    assert os.environ[DATA_VAR] != DATA
'''


def test_uv_env_reads_uv_before_the_fence_empties_the_env(pytester, monkeypatch,
                                                                    tmp_path):
    cache, data = str(tmp_path / "cache"), str(tmp_path / "data")
    # The variable uv takes its Pythons' folder from: APPDATA on Windows.
    data_var = "APPDATA" if testing.WINDOWS else "XDG_DATA_HOME"
    monkeypatch.setenv("UV_CACHE_DIR", cache)
    monkeypatch.setenv(data_var, data)
    monkeypatch.delenv("UV_PYTHON_INSTALL_DIR", raising=False)
    pytester.makeconftest('pytest_plugins = ["moxi_dev.testing"]\n')
    pytester.makepyfile(test_uv_probe=UV_PROBE.format(cache=cache, data=data,
                                                      data_var=data_var))
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)


# --- writes ----------------------------------------------------------------------------

@POSIX
def test_a_write_to_the_real_home_fails():
    target = stray()
    assert "outside the test's tmp" in refused(lambda: target.write_text("x"))
    assert not target.exists()
    refused(lambda: os.open(target, os.O_WRONLY | os.O_CREAT))
    refused(lambda: os.mkdir(target))
    refused(lambda: os.remove(target))
    refused(lambda: sqlite3.connect(target))
    refused(lambda: tempfile.mkstemp(dir=real_home()))
    assert not target.exists()


def test_a_write_in_the_tmp_goes_through(tmp_path):
    (tmp_path / "a").write_text("x")
    os.rename(tmp_path / "a", tmp_path / "b")
    (tmp_path / "tree" / "deep").mkdir(parents=True)
    (tmp_path / "tree" / "deep" / "f").write_text("x")
    if testing.can_symlink():        # no link on Windows without the right to make one
        os.symlink(tmp_path / "b", tmp_path / "tree" / "link")
    shutil.rmtree(tmp_path / "tree")             # removes by dir_fd, name by name
    fd, name = tempfile.mkstemp()
    os.close(fd)
    os.remove(name)
    sqlite3.connect(tmp_path / "db.sqlite").close()
    sqlite3.connect(":memory:").close()
    with open(os.devnull, "w") as fh:
        fh.write("x")
    assert (tmp_path / "b").read_text() == "x"


#: Each spelling a write to Windows's `nul` resolves to, which Windows opens as the
#: null device.
WINDOWS_NULLS = ("\\\\.\\nul", "\\\\.\\NUL", "C:\\work\\nul", "C:\\work\\NUL",
                 "C:/work/nul", "\\\\server\\share\\nul")
#: Paths that only look like it: a `\\?\` or `\\.\` path creates a file named nul, and
#: the rest are other names.
WINDOWS_NOT_NULLS = ("\\\\?\\C:\\work\\nul", "\\\\.\\C:\\work\\nul", "\\\\?\\nul",
                     "C:\\work\\nul.txt", "C:\\work\\nul:", "C:\\work\\annul",
                     "C:\\work\\null", "C:\\work\\nul\\x", "\\\\.\\con", "/dev/null")


def test_on_windows_the_null_device_goes_through_in_each_spelling(monkeypatch):
    monkeypatch.setattr(testing, "WINDOWS", True)
    for path in WINDOWS_NULLS:
        assert testing._allowed(path), path
    for path in WINDOWS_NOT_NULLS:
        assert not testing._allowed(path), path


def test_off_windows_the_null_device_is_os_devnull_alone(monkeypatch):
    monkeypatch.setattr(testing, "WINDOWS", False)
    assert testing._allowed(os.devnull)
    for path in (*WINDOWS_NULLS, "/work/nul", "/dev/null2", "/dev/nul"):
        assert not testing._allowed(path), path


#: Allowed roots on Windows, and paths inside them in long form.
WINDOWS_ROOTS = ("C:\\t\\base", "\\\\srv\\share\\short")
WINDOWS_LONG_INSIDE = ("\\\\?\\C:\\t\\base", "\\\\?\\C:\\t\\base\\gw0\\basetemp",
                       "\\\\?\\UNC\\srv\\share\\short\\x", "\\\\?\\unc\\srv\\share\\short")
#: Long forms that name a place outside, or no plain path at all.
WINDOWS_LONG_OUTSIDE = ("\\\\?\\C:\\t\\elsewhere", "\\\\?\\C:\\t\\base2\\x",
                        "\\\\?\\C:\\t\\base\\..\\out", "\\\\?\\C:\\t\\base\\.\\x",
                        "\\\\?\\C:\\t\\base/x", "\\\\?\\C:\\t\\base\\",
                        "\\\\?\\UNC\\srv\\share\\other",
                        "\\\\?\\GLOBALROOT\\Device\\C:\\t\\base\\x",
                        "\\\\?\\Volume{0}\\t\\base\\x", "\\\\.\\C:\\t\\base\\x")


def test_on_windows_a_long_form_path_is_judged_by_the_path_it_names(monkeypatch):
    monkeypatch.setattr(testing, "WINDOWS", True)
    for path in WINDOWS_LONG_INSIDE:
        assert testing._under(path, WINDOWS_ROOTS), path
    for path in WINDOWS_LONG_OUTSIDE:
        assert not testing._under(path, WINDOWS_ROOTS), path
    # A root that is itself in long form matches its plain paths.
    assert testing._under("C:\\t\\base\\x", ["\\\\?\\C:\\t\\base"])
    # The long form of nul in the tmp is a file there, and outside it is still refused.
    assert not testing._allowed("\\\\?\\C:\\work\\nul")


def test_off_windows_a_long_form_path_is_a_name_like_any_other(monkeypatch):
    monkeypatch.setattr(testing, "WINDOWS", False)
    for path in WINDOWS_LONG_INSIDE:
        assert not testing._under(path, WINDOWS_ROOTS), path
    assert testing._under(os.path.join("/t/base", "x"), ["/t/base"])   # this system's sep
    assert not testing._under("/t/base2", ["/t/base"])


def test_reading_outside_the_tmp_is_not_a_write():
    with open(testing.__file__, "rb") as fh:
        assert fh.read(1)
    os.close(os.open(testing.__file__, os.O_RDONLY))


@POSIX
def test_a_link_out_of_the_tmp_is_outside(tmp_path):
    os.symlink(real_home(), tmp_path / "out")
    refused(lambda: (tmp_path / "out" / stray().name).write_text("x"))
    os.remove(tmp_path / "out")                  # the link itself is inside


@POSIX
def test_a_rename_out_of_the_tmp_fails(tmp_path):
    (tmp_path / "a").write_text("x")
    refused(lambda: os.rename(tmp_path / "a", stray()))
    refused(lambda: shutil.move(str(tmp_path / "a"), str(stray())))
    assert (tmp_path / "a").exists()


@POSIX
def test_another_thread_is_fenced_too():
    errors = []

    def write():
        try:
            stray().write_text("x")
        except FenceError as exc:
            errors.append(exc)

    start = len(testing._GUARD.breaches)
    thread = threading.Thread(target=write)
    thread.start()
    thread.join()
    del testing._GUARD.breaches[start:]
    assert len(errors) == 1


# --- byte code and the narrow roots ---------------------------------------------------

@pytest.fixture(scope="module")
def outside():
    """A directory outside every test's tmp, made unfenced (a wider-scoped fixture is),
    with a module in it never imported and no `__pycache__`, one `__pycache__` that is
    there already, and a listening socket like another process's in the short root."""
    where = Path(tempfile.mkdtemp(prefix="mxd-out-", dir=testing.short_root()))
    name = f"fresh_{uuid.uuid4().hex}"
    (where / "src").mkdir()
    (where / "src" / f"{name}.py").write_text("VALUE = 7\n")
    (where / "cached" / "__pycache__").mkdir(parents=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(where / "sock"))
    listener.listen()
    try:
        yield where, name
    finally:
        listener.close()
        shutil.rmtree(where, ignore_errors=True)


@POSIX
def test_a_module_first_imported_in_a_test_writes_no_byte_code(outside, monkeypatch):
    where, name = outside
    import importlib
    import sys

    assert sys.dont_write_bytecode
    monkeypatch.syspath_prepend(str(where / "src"))
    monkeypatch.delitem(sys.modules, name, raising=False)
    assert importlib.import_module(name).VALUE == 7
    assert not (where / "src" / "__pycache__").exists()


@POSIX
def test_a_write_into_a_pycache_outside_the_tmp_fails(outside):
    where, _ = outside
    refused(lambda: (where / "cached" / "__pycache__" / "x.cpython-312.pyc").write_bytes(b""))
    refused(lambda: os.mkdir(where / "src" / "__pycache__"))


@POSIX
def test_the_rest_of_the_temp_dirs_is_outside(outside, fence, tmp_path_factory):
    where, _ = outside
    refused(lambda: (where / "f").write_text("x"))             # another process's, in /tmp
    basetemp = tmp_path_factory.getbasetemp()
    refused(lambda: (basetemp.parent / f"stray-{uuid.uuid4().hex}").write_text("x"))
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        refused(lambda: s.connect(str(where / "sock")))
    assert fence.roots == (basetemp.resolve(), fence.short.resolve())


def test_temp_files_land_in_the_test_s_short_dir(fence):
    fd, name = tempfile.mkstemp()
    os.close(fd)
    # Resolved on both sides: on macOS /tmp is a link to /private/tmp.
    assert Path(name).parent.resolve() == fence.short.resolve()
    with testing.short_dir() as where:
        assert where.parent.resolve() == fence.short.resolve()
        if hasattr(socket, "AF_UNIX"):          # a Unix socket's bound; Windows has none
            # /var/tmp/mx-<6>/<1>/mx-<8>/sock: the test's short dir is in its
            # process's short process dir.
            assert len(str(where / "sock")) < 44


def test_a_test_s_short_dir_has_a_one_character_name(fence):
    assert len(fence.short.name) == 1
    assert fence.env["TMPDIR"] == str(fence.short)


def test_no_short_name_repeats_in_a_turn_and_one_that_exists_is_passed_over(fence,
                                                                             monkeypatch):
    names = testing._SHORT_NAME_CHARS
    root = fence.short / "names"
    root.mkdir()
    monkeypatch.setattr(testing, "_SHORT_NAMES", [])
    given = []
    for _ in names:                                  # made and removed, as a test's is
        made = testing._make_test_short_dir(str(root))
        given.append(made.name)
        made.rmdir()
    assert sorted(given) == sorted(names)           # one turn: 36 distinct names
    (root / given[0]).mkdir()                        # one left behind
    # The next turn's 35 gives: the existing name is passed over each time it comes up.
    made = {testing._make_test_short_dir(str(root)).name for _ in names[1:]}
    assert made == {*names} - {given[0]}
    longer = testing._make_test_short_dir(str(root))    # every one-character name is taken
    assert longer.parent == root and len(longer.name) > 1 and longer.is_dir()


#: How macOS's realpath spells `/tmp`, the short root its kit uses: the longer spelling.
MACOS_TMP = "/private/tmp"

#: comms' claim socket name, `claim-` and a key as long as a UUID: the longest name a
#: module gives `moxi.paths.socket_path`.
CLAIM_NAME = f"claim-{uuid.UUID(int=0)}"

#: macOS's limit on a Unix socket's path, in bytes.
MACOS_SUN_PATH = 104


@pytest.mark.skipif(sys.platform == "win32", reason="Unix socket paths")
def test_on_macos_a_fenced_test_s_sockets_are_as_short_as_without_process_dirs(fence):
    darwin = temp.Places(platform="darwin", system_temp="/var/folders/q2/abc/T")
    assert darwin.macos_short_root == "/tmp"
    # The kit's macOS branch, on a stand-in for /tmp the fence lets this test write.
    stand_in = fence.short / "tmp"
    stand_in.mkdir()
    choice = temp.choose({}, where=dataclasses.replace(darwin,
                                                       macos_short_root=str(stand_in)))
    assert choice.short_root == str(stand_in)
    process = temp.make_process_dir(choice.short_root)
    try:
        short = testing._make_test_short_dir(str(process.path))
        on_macos = PurePosixPath(MACOS_TMP, process.path.name, short.name)
    finally:
        process.release()
    straight = PurePosixPath(MACOS_TMP, "mx-" + "x" * 8)    # one made straight in /tmp
    assert len(str(on_macos)) == len(str(straight)) == 24

    # Each module's runtime dir, as moxi.paths builds it from the fenced environment.
    modules = sorted({"comms", *testing._modules()})
    env = testing._fenced_env({}, PurePosixPath("/Users/runner/home"), on_macos / "run",
                              on_macos, modules)
    for module in modules:
        runtime = str(paths.Layout(module).runtime_dir(env))
        assert runtime == f"{on_macos}/{module}/runtime/{module}"
    claim = f"{paths.Layout('comms').runtime_dir(env)}/{CLAIM_NAME}.sock"
    assert len(os.fsencode(claim)) < paths.SOCKET_MAX < MACOS_SUN_PATH, (len(claim), claim)


@pytest.fixture(scope="module")
def module_server():
    """A server a wider-scoped fixture serves, its socket in a short_dir made unfenced."""
    class Server(socketserver.UnixStreamServer):
        pass

    with testing.served(lambda path: Server(str(path), _Echo)) as server:
        yield server


@POSIX
def test_a_test_reaches_a_server_a_wider_fixture_serves(module_server):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
        c.settimeout(5)
        c.connect(module_server.server_address)
        c.sendall(b"ping")
        assert c.recv(64) == b"ping"


# --- the run-level clear ---------------------------------------------------------------

#: An agent's session as a harness and Moxi's modules stamp it, and what a run needs.
SESSION_SOURCE = {
    "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s", "CLAUDE_PID": "1",
    "CLAUDE_CONFIG_DIR": "/real/.claude", "CODEX_SANDBOX": "seatbelt",
    "CODEX_HOME": "/real/.codex", "OPENCODE": "1", "OPENCODE_CONFIG_CONTENT": "{}",
    "AI_AGENT": "claude-code", "GIT_EDITOR": "true", "COREPACK_ENABLE_AUTO_PIN": "0",
    "NoDefaultCurrentDirectoryInExePath": "1",
    "MOXI_COMMS_NAME": "agent", "MOXI_COMMS_SOCKET": "/run/s", "MOXI_NOTES_DIR": "/n",
    "MOXI_DEV_SCOPE": "u", "MOXI_X_OTHER": "o", "MOXI_X_INPUT": "i",
}
#: What the clear keeps of it.
RUN_SOURCE = {
    "PATH": "/bin", "HOME": "/home/you", "LANG": "C.UTF-8", "OPENCLAW": "1",
    "MOXI_TEST_WORKERS": "2", "MOXI_TEST_TEMP": "disk", "MOXI_STARTED_BY": "moxi-dev",
    "MOXI_HOME": "/m", "MOXI_COMMS_HOME": "/m/comms", "MOXI_ANCHOR_HOME": "/home/you",
    "MOXI_COMMS_STATE_HOME": "/m/s", "MOXI_COMMS_RUNTIME_DIR": "/run/m",
}


def test_the_clear_takes_the_session_and_keeps_what_the_run_needs(monkeypatch):
    monkeypatch.setattr(testing, "WINDOWS", False)
    found = testing.cleared_names({**SESSION_SOURCE, **RUN_SOURCE}, ["MOXI_X_INPUT"])
    assert sorted(found) == sorted(set(SESSION_SOURCE) - {"MOXI_X_INPUT"})


def test_on_windows_the_clear_matches_names_without_case(monkeypatch):
    monkeypatch.setattr(testing, "WINDOWS", True)
    env = {"claudecode": "1", "GIT_editor": "true", "Codex_Home": "c",
           "moxi_comms_name": "a", "moxi_test_workers": "2", "Moxi_Comms_Home": "/m",
           "moxi_x_input": "i", "Path": "C:\\bin"}
    assert sorted(testing.cleared_names(env, ["MOXI_X_INPUT"])) == sorted(
        ["claudecode", "GIT_editor", "Codex_Home", "moxi_comms_name"])
    monkeypatch.setattr(testing, "WINDOWS", False)
    assert testing.cleared_names(env) == []


def test_the_clear_has_a_prefix_for_each_harness_the_agent_test_knows():
    from moxi.terminal import _walk

    assert sorted(prefix.rstrip("_").lower() for prefix in testing.AGENT_ENV_PREFIXES) == (
        sorted(_walk.HARNESSES))


def test_a_plugin_s_inputs_are_its_declared_names():
    class Plugin:
        RUN_INPUT_ENV = ("MOXI_X_INPUT", 3)

    class Broken:
        def __getattr__(self, name):
            raise RuntimeError(name)

    module = type(sys)("plugin")
    module.RUN_INPUT_ENV = ["MOXI_Y_INPUT"]
    assert testing.run_inputs([Plugin(), module, Broken(), object()]) == {
        "MOXI_X_INPUT", "MOXI_Y_INPUT"}


def _workspace_plugins() -> list[object]:
    """Every pytest plugin in the workspace's `src/` folders, imported: a file defining a
    top-level `pytest_*` function."""
    import ast
    import importlib

    root = Path(__file__).resolve().parents[3] / "packages"
    if not root.is_dir():
        pytest.skip("not run from the repository (an sdist has no workspace)")
    found = []
    for path in sorted(root.glob("*/src/**/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not any(isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and stmt.name.startswith("pytest_") for stmt in tree.body):
            continue
        parts = path.relative_to(root).parts[2:]        # under <package>/src/
        dotted = ".".join(parts).removesuffix(".py").removesuffix(".__init__")
        found.append(importlib.import_module(dotted))
    return found


def test_every_module_s_session_variables_are_cleared_and_no_plugin_keeps_one():
    from moxi import modules

    stamps = modules.session_env()
    assert stamps, "the workspace installs Moxi modules that stamp a session; none found"
    plugins = _workspace_plugins()
    assert any(getattr(plugin, "RUN_INPUT_ENV", ()) for plugin in plugins)
    inputs = testing.run_inputs(plugins)
    for key in stamps:
        name = key + "X" if key.endswith("_") else key
        assert testing.cleared_names({name: "1"}, inputs) == [name], key
        assert not any(one == key or (key.endswith("_") and one.startswith(key))
                       for one in inputs), key


RUN_PROBE = """\
import json, os, subprocess, sys
import pytest

CLEARED = {cleared!r}
KEPT = {kept!r}
NAMES = "import json, os; print(json.dumps(sorted(os.environ)))"


def names_of_a_child():
    done = subprocess.run([sys.executable, "-c", NAMES], env=dict(os.environ),
                          capture_output=True, text=True, check=True)
    return set(json.loads(done.stdout))


@pytest.fixture(scope="session")
def run_env():
    return dict(os.environ)


@pytest.fixture(scope="module")
def module_child():
    return names_of_a_child()


def test_the_run_and_its_children_hold_no_session_variable(run_env, module_child):
    for seen in (set(run_env), module_child):
        assert not seen & set(CLEARED), sorted(seen & set(CLEARED))
        assert set(KEPT) <= seen, sorted(set(KEPT) - seen)
"""

INPUT_PLUGIN = """\
import os

RUN_INPUT_ENV = ("MOXI_X_INPUT",)


def pytest_collection_finish(session):
    print("at collection:", os.environ.get("MOXI_X_INPUT"), os.environ.get("MOXI_X_OTHER"))
"""


def test_a_run_clears_the_session_for_every_scope_and_child_and_keeps_its_inputs(
        pytester, monkeypatch, tmp_path):
    kept = {"MOXI_TEST_WORKERS": "2", "MOXI_STARTED_BY": "moxi-dev",
            "MOXI_HOME": str(tmp_path / "m"), "MOXI_COMMS_HOME": str(tmp_path / "c"),
            "MOXI_X_INPUT": "given"}
    for name, value in {**SESSION_SOURCE, **kept}.items():
        if name != "NoDefaultCurrentDirectoryInExePath" or not testing.WINDOWS:
            monkeypatch.setenv(name, value)
    cleared = sorted(set(SESSION_SOURCE) - {"MOXI_X_INPUT"})
    pytester.makeconftest('pytest_plugins = ["moxi_dev.testing"]\n')
    pytester.makepyfile(xinput=INPUT_PLUGIN)
    pytester.makepyfile(test_probe=RUN_PROBE.format(cleared=cleared, kept=sorted(kept)))
    result = pytester.runpytest_subprocess("-p", "xinput", "-p", "no:cacheprovider", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*at collection: given None*"])


# --- a pytest run inside a test --------------------------------------------------------

@POSIX
def test_an_in_process_run_puts_the_outer_fence_back(pytester):
    pytester.makeconftest('pytest_plugins = ["moxi_dev.testing"]\n')
    pytester.makepyfile(test_inner=(
        "import os, tempfile\n"
        "def test_inner(tmp_path, fence):\n"
        "    (tmp_path / 'f').write_text('x')\n"
        "    assert tempfile.gettempdir() == str(fence.short)\n"))
    roots, short, session = testing._GUARD.roots, testing._GUARD.short, dict(testing._SESSION)
    start = len(testing._GUARD.breaches)
    with pytest.raises(FenceError):
        stray().write_text("x")                  # a refusal of the outer test's own
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)
    assert testing._GUARD.active and testing._GUARD.network
    assert (testing._GUARD.roots, testing._GUARD.short) == (roots, short)
    assert testing._SESSION == session
    assert len(testing._GUARD.breaches) == start + 1, "the outer refusal is still recorded"
    del testing._GUARD.breaches[start:]
    refused(lambda: stray().write_text("x"))


# --- the network -----------------------------------------------------------------------

@pytest.mark.parametrize("host", ["192.0.2.1", "8.8.8.8", "10.0.0.1"])
def test_a_connect_off_the_machine_fails(host):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        assert "not a loopback address" in refused(lambda: s.connect((host, 9)))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as u:
        refused(lambda: u.sendto(b"x", (host, 9)))


@POSIX
def test_a_connect_to_a_socket_outside_the_tmp_fails():
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        refused(lambda: s.connect(f"/run/user/{os.getuid()}/moxi-dev-probe.sock"))
        refused(lambda: s.bind(str(stray())))


class _Echo(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.sendall(self.request.recv(64))


def _loopbacks():
    hosts = ["127.0.0.1", "localhost"]
    if socket.has_ipv6:
        try:
            with socket.socket(socket.AF_INET6) as s:
                s.bind(("::1", 0))
            hosts.append("::1")
        except OSError:
            pass
    return hosts


@pytest.mark.parametrize("host", _loopbacks())
def test_a_loopback_connect_works(host):
    family = socket.AF_INET6 if ":" in host else socket.AF_INET

    class Server(socketserver.TCPServer):
        address_family = family
        allow_reuse_address = True

    bind = "::1" if family == socket.AF_INET6 else "127.0.0.1"
    with testing.served(lambda _: Server((bind, 0), _Echo)) as server:
        port = server.server_address[1]
        with socket.create_connection((host, port), timeout=5) as c:
            c.sendall(b"ping")
            assert c.recv(64) == b"ping"


# --- a run of its own: what fails, what passes ----------------------------------------

PROBES = '''
import os, pwd, socket, uuid
from pathlib import Path

import pytest

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)


def stray():
    return REAL_HOME / f".moxi-dev-fence-probe-{uuid.uuid4().hex}"


def test_writes_to_the_real_home():
    stray().write_text("x")


def test_swallows_the_refusal():
    try:
        stray().write_text("x")
    except OSError:
        pass


def test_connects_off_the_machine():
    with socket.create_connection(("192.0.2.1", 9), timeout=0.5):
        pass


@pytest.fixture
def leaky():
    yield
    try:
        stray().write_text("x")
    except OSError:
        pass


def test_a_fixture_writes_out_in_teardown(leaky):
    pass


def test_writes_in_its_tmp(tmp_path):
    (tmp_path / "f").write_text("x")


def test_sees_only_the_fenced_environment(fence):
    assert "OPENCODE_CONFIG_CONTENT" not in os.environ
    assert "MOXI_DEV_PROBE_SECRET" not in os.environ
    assert os.environ["HOME"] == str(fence.home)


@pytest.mark.live
def test_live():
    from moxi_dev import testing
    assert testing._GUARD.network is False
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        with pytest.raises(OSError) as caught:     # lifted: the kernel answers
            s.connect(f"/run/user/{os.getuid()}/moxi-dev-probe-{uuid.uuid4().hex}.sock")
        assert not isinstance(caught.value, testing.FenceError)
'''


@pytest.fixture
def probes(pytester, monkeypatch):
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", "{}")
    monkeypatch.setenv("MOXI_DEV_PROBE_SECRET", "x")
    monkeypatch.delenv("MOXI_TEST_LIVE", raising=False)
    pytester.makeconftest('pytest_plugins = ["moxi_dev.testing"]\n')
    pytester.makepyfile(test_probes=PROBES)
    return pytester


@POSIX
def test_a_probe_run_fails_what_breaks_the_fence(probes):
    result = probes.runpytest_subprocess("-p", "no:cacheprovider", "-rA")
    result.assert_outcomes(failed=3, errors=1, passed=3, skipped=1)
    lines = set(result.outlines)
    for word, name in (("FAILED", "test_writes_to_the_real_home"),
                       ("FAILED", "test_swallows_the_refusal"),
                       ("FAILED", "test_connects_off_the_machine"),
                       ("ERROR", "test_a_fixture_writes_out_in_teardown"),
                       ("PASSED", "test_writes_in_its_tmp"),
                       ("PASSED", "test_sees_only_the_fenced_environment")):
        assert any(line.startswith(f"{word} test_probes.py::{name}") for line in lines), \
            (word, name)
    text = result.stdout.str()
    assert "the test fence refused, and the test went on" in text     # the swallowed one
    assert "outside the test's call" in text                         # the teardown one
    assert "not a loopback address" in text
    assert "a live test runs only with MOXI_TEST_LIVE=1" in text
    assert not list(real_home().glob(".moxi-dev-fence-probe-*"))


@POSIX
def test_a_live_probe_runs_with_the_word(probes, monkeypatch):
    monkeypatch.setenv("MOXI_TEST_LIVE", "1")
    result = probes.runpytest_subprocess("-p", "no:cacheprovider", "-k", "live")
    result.assert_outcomes(passed=1, deselected=6)
