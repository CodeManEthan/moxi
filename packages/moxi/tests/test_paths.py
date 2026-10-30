import hashlib
import os
import stat
import subprocess
import sys
import sysconfig
import time
from pathlib import Path

import pytest

from moxi import paths
from moxi.paths import Layout

HOME = "/users/someone"

WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="Windows paths and access lists")
POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX roots and sockets")


def env(**extra):
    """An environment with a HOME and nothing else unless given."""
    return {"HOME": HOME, **extra}


@pytest.fixture
def home(tmp_path):
    """An environment whose every root is under a tmp home, through `MOXI_HOME`."""
    return {"HOME": str(tmp_path / "user"), "MOXI_HOME": str(tmp_path / "moxi")}


# -- resolving a root ------------------------------------------------------------------

@POSIX
def test_the_defaults_without_xdg():
    lay = Layout("cache")
    e = env()
    assert lay.data_dir(e) == Path(HOME, ".local/share/moxi/cache")
    assert lay.state_dir(e) == Path(HOME, ".local/state/moxi/cache")
    assert lay.cache_dir(e) == Path(HOME, ".cache/moxi/cache")
    assert lay.config_dir(e) == Path(HOME, ".config/moxi/cache")
    assert lay.config_file(e) == Path(HOME, ".config/moxi/cache/config.toml")
    # No runtime directory: under the state root.
    assert lay.runtime_dir(e) == Path(HOME, ".local/state/moxi/runtime/cache")


@POSIX
def test_the_roots_are_moxis_and_the_directories_the_modules():
    lay = Layout("cache")
    e = env()
    assert lay.data_root(e) == Path(HOME, ".local/share/moxi")
    assert lay.state_root(e) == Path(HOME, ".local/state/moxi")
    assert lay.cache_root(e) == Path(HOME, ".cache/moxi")
    assert lay.config_root(e) == Path(HOME, ".config/moxi")
    assert lay.runtime_root(e) == Path(HOME, ".local/state/moxi/runtime")
    for root, d in [(lay.data_root, lay.data_dir), (lay.state_root, lay.state_dir),
                    (lay.cache_root, lay.cache_dir), (lay.config_root, lay.config_dir),
                    (lay.runtime_root, lay.runtime_dir)]:
        assert d(e) == root(e) / "cache"


@POSIX
def test_xdg_moves_the_defaults():
    e = env(XDG_DATA_HOME="/x/data", XDG_STATE_HOME="/x/state", XDG_CACHE_HOME="/x/cache",
            XDG_CONFIG_HOME="/x/config", XDG_RUNTIME_DIR="/run/user/1000")
    lay = Layout("cache")
    assert lay.data_dir(e) == Path("/x/data/moxi/cache")
    assert lay.state_dir(e) == Path("/x/state/moxi/cache")
    assert lay.cache_dir(e) == Path("/x/cache/moxi/cache")
    assert lay.config_dir(e) == Path("/x/config/moxi/cache")
    assert lay.runtime_dir(e) == Path("/run/user/1000/moxi/cache")


@POSIX
def test_an_empty_variable_is_unset():
    e = env(XDG_DATA_HOME="", MOXI_HOME="", MOXI_CACHE_HOME="", MOXI_CACHE_DATA_HOME="")
    assert Layout("cache").data_dir(e) == Path(HOME, ".local/share/moxi/cache")


def test_a_per_root_override_names_the_root():
    e = env(MOXI_CACHE_DATA_HOME="/d", MOXI_CACHE_STATE_HOME="/s", MOXI_CACHE_CACHE_HOME="/c",
            MOXI_CACHE_CONFIG_HOME="/f", MOXI_CACHE_RUNTIME_DIR="/r",
            XDG_RUNTIME_DIR="/run/user/1000")
    lay = Layout("cache")
    assert lay.data_dir(e) == Path("/d/cache")
    assert lay.state_dir(e) == Path("/s/cache")
    assert lay.cache_dir(e) == Path("/c/cache")
    assert lay.config_file(e) == Path("/f/cache/config.toml")
    assert lay.runtime_dir(e) == Path("/r/cache")


def test_the_modules_home_collapses_all_five_roots():
    e = env(MOXI_CACHE_HOME="/h", XDG_DATA_HOME="/x", XDG_RUNTIME_DIR="/run/user/1000")
    lay = Layout("cache")
    assert lay.data_dir(e) == Path("/h/data/cache")
    assert lay.state_dir(e) == Path("/h/state/cache")
    assert lay.cache_dir(e) == Path("/h/cache/cache")
    assert lay.config_dir(e) == Path("/h/config/cache")
    assert lay.runtime_dir(e) == Path("/h/runtime/cache")


def test_moxi_home_holds_every_modules_roots_below_the_modules_own():
    e = env(MOXI_HOME="/m", XDG_DATA_HOME="/x", XDG_RUNTIME_DIR="/run/user/1000")
    for module in ("cache", "comms"):
        lay = Layout(module)
        assert lay.data_dir(e) == Path("/m/data", module)
        assert lay.state_dir(e) == Path("/m/state", module)
        assert lay.cache_dir(e) == Path("/m/cache", module)
        assert lay.config_dir(e) == Path("/m/config", module)
        assert lay.runtime_dir(e) == Path("/m/runtime", module)
    e = env(MOXI_HOME="/m", MOXI_CACHE_HOME="/h", MOXI_CACHE_DATA_HOME="/d")
    assert Layout("cache").data_dir(e) == Path("/d/cache")
    assert Layout("cache").state_dir(e) == Path("/h/state/cache")
    assert Layout("comms").state_dir(e) == Path("/m/state/comms")


def test_a_per_root_override_wins_over_home():
    e = env(MOXI_CACHE_HOME="/h", MOXI_CACHE_DATA_HOME="/d", MOXI_CACHE_RUNTIME_DIR="/r")
    lay = Layout("cache")
    assert lay.data_dir(e) == Path("/d/cache")
    assert lay.runtime_dir(e) == Path("/r/cache")
    assert lay.config_dir(e) == Path("/h/config/cache")


@POSIX
def test_with_no_runtime_variable_runtime_follows_the_state_root():
    e = env(MOXI_CACHE_STATE_HOME="/s")
    assert Layout("cache").runtime_dir(e) == Path("/s/runtime/cache")


def test_each_module_reads_only_its_own_prefix():
    e = env(MOXI_COMMS_HOME="/comms-home", MOXI_COMMS_DATA_HOME="/comms-data")
    assert Layout("cache").data_dir(e) == Layout("cache").data_dir(env())
    assert Layout("comms").data_dir(e) == Path("/comms-data/comms")
    assert Layout("comms").config_dir(e) == Path("/comms-home/config/comms")


def test_the_prefix_is_a_parameter():
    lay = Layout("cache", "OTHER_")
    assert lay.prefix == "OTHER_"
    assert lay.data_dir(env(OTHER_HOME="/o")) == Path("/o/data/cache")
    assert Layout("cache").prefix == "MOXI_CACHE_"


def test_the_locators_are_the_modules_variables_and_moxi_home():
    assert Layout("cache").locators() == (
        "MOXI_CACHE_HOME", "MOXI_CACHE_DATA_HOME", "MOXI_CACHE_STATE_HOME",
        "MOXI_CACHE_CACHE_HOME", "MOXI_CACHE_CONFIG_HOME", "MOXI_CACHE_RUNTIME_DIR",
        "MOXI_HOME")
    # A device label moves no root.
    assert "DEVICE" not in paths.LOCATORS


def test_without_a_mapping_it_reads_the_process_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("MOXI_CACHE_HOME", str(tmp_path))
    for var in ("MOXI_CACHE_DATA_HOME", "MOXI_CACHE_STATE_HOME", "MOXI_CACHE_RUNTIME_DIR"):
        monkeypatch.delenv(var, raising=False)
    lay = Layout("cache")
    assert lay.data_dir() == tmp_path / "data" / "cache"
    assert lay.state_dir() == tmp_path / "state" / "cache"
    assert lay.runtime_dir() == tmp_path / "runtime" / "cache"


@POSIX
def test_the_home_in_the_mapping_is_the_one_used(monkeypatch):
    monkeypatch.setenv("HOME", "/somewhere/else")
    assert Layout("cache").data_dir({"HOME": "/mine"}) == Path("/mine/.local/share/moxi/cache")


def test_a_layout_needs_a_module_name():
    with pytest.raises(ValueError):
        Layout("Not-A-Name")
    assert Layout("cache") == Layout("cache")
    assert Layout("cache") != Layout("comms")
    assert len({Layout("cache"), Layout("cache")}) == 1


def test_windows_defaults_keep_only_config_roaming(monkeypatch):
    monkeypatch.setattr(paths, "_WINDOWS", True)
    monkeypatch.setattr(paths, "_windows_folders", lambda: ("/Roaming", "/Local"))
    lay = Layout("cache")
    e = env(XDG_DATA_HOME="/x", XDG_RUNTIME_DIR="/run/user/1000")
    assert lay.data_dir(e) == Path("/Local/moxi/data/cache")
    assert lay.state_dir(e) == Path("/Local/moxi/state/cache")
    assert lay.cache_dir(e) == Path("/Local/moxi/cache/cache")
    assert lay.config_dir(e) == Path("/Roaming/moxi/config/cache")
    assert lay.runtime_dir(e) == Path("/Local/moxi/runtime/cache")
    assert lay.data_dir(env(MOXI_HOME="/m")) == Path("/m/data/cache")


@WINDOWS
def test_windows_defaults_are_the_users_application_data_folders():
    lay = Layout("cache")
    e = {}
    local = os.environ["LOCALAPPDATA"]
    assert lay.data_dir(e) == Path(local, "moxi", "data", "cache")
    assert lay.config_dir(e) == Path(os.environ["APPDATA"], "moxi", "config", "cache")
    assert lay.runtime_dir(e) == Path(local, "moxi", "runtime", "cache")


# -- the core's own layout ---------------------------------------------------------------

@POSIX
def test_the_core_reads_only_moxi_home_and_the_defaults():
    core = paths.core()
    assert core.module == "moxi"
    assert core.state_dir(env()) == Path(HOME, ".local/state/moxi/moxi")
    e = env(MOXI_MOXI_HOME="/no", MOXI_MOXI_STATE_HOME="/no", MOXI_MOXI_DEVICE="no")
    assert core.state_dir(e) == Path(HOME, ".local/state/moxi/moxi")
    assert core.state_dir(env(MOXI_HOME="/m")) == Path("/m/state/moxi")
    assert core.locators() == ("MOXI_HOME",)
    assert paths.core_lock(env(MOXI_HOME="/m")) == Path("/m/state/moxi/install.lock")


# -- the device ----------------------------------------------------------------------------

def test_the_device_id_is_written_once(home):
    first = paths.device_id(home)
    assert len(first) == 32 and int(first, 16) >= 0
    where = Path(home["MOXI_HOME"], "state", "moxi", "device-id")
    assert where.read_text().strip() == first
    assert paths.device_id(home) == first


@POSIX
def test_the_device_ids_missing_directories_are_each_made_0700(home):
    root = Path(home["MOXI_HOME"])
    root.mkdir()
    os.chmod(root, 0o775)
    old = os.umask(0)
    try:
        paths.device_id(home)
    finally:
        os.umask(old)
    for each in (root / "state", root / "state" / "moxi"):
        assert stat.S_IMODE(os.stat(each).st_mode) == 0o700, each
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o775


def test_two_first_runs_agree_on_one_device_id(home):
    code = "from moxi.paths import device_id; print(device_id())"
    procs = [subprocess.Popen([sys.executable, "-c", code], env={**os.environ, **home},
                              stdout=subprocess.PIPE, text=True) for _ in range(6)]
    ids = {p.communicate(timeout=60)[0].strip() for p in procs}
    assert len(ids) == 1 and ids == {paths.device_id(home)}


def test_a_label_read_without_create_never_writes_the_id(home):
    where = Path(home["MOXI_HOME"], "state", "moxi", "device-id")
    assert paths.device_label("cache", home, create=False) is None
    assert Layout("cache").device(home, create=False) is None
    assert not Path(home["MOXI_HOME"]).exists()
    assert paths.device_label("cache", {**home, "MOXI_DEVICE": "all"}, create=False) == "all"
    me = paths.device_id(home)
    assert where.is_file()
    assert Layout("cache").device(home, create=False) == me == Layout("cache").device(home)


def test_two_threads_of_one_process_agree_on_one_device_id(home, monkeypatch):
    """The second thread waits for the first one's lock instead of being refused by it."""
    import threading

    from moxi import files

    barrier = threading.Barrier(2)
    real_write = files.atomic_write

    def slow_write(*args, **kwargs):
        time.sleep(0.3)             # the other thread has read no id and reached the lock
        return real_write(*args, **kwargs)

    monkeypatch.setattr(files, "atomic_write", slow_write)
    found, failed = [], []

    def first_run():
        barrier.wait()
        try:
            found.append(paths.device_id(home))
        except Exception as e:  # noqa: BLE001 - the test reports it
            failed.append(e)

    threads = [threading.Thread(target=first_run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert failed == [] and len(found) == 2 and len(set(found)) == 1


def test_the_device_label(home):
    me = paths.device_id(home)
    assert paths.device_label(env=home) == me
    assert paths.device_label("cache", home) == me
    assert paths.device_label("cache", {**home, "MOXI_DEVICE": "all"}) == "all"
    e = {**home, "MOXI_DEVICE": "all", "MOXI_CACHE_DEVICE": "box"}
    assert paths.device_label("cache", e) == "box"
    assert paths.device_label("comms", e) == "all"
    assert paths.device_label(None, e) == "all"
    assert paths.device_label("cache", {**home, "MOXI_CACHE_DEVICE": ""}) == me
    assert Layout("cache").device(e) == "box"
    # A label moves no root.
    assert Layout("cache").state_dir(e) == Path(home["MOXI_HOME"], "state", "cache")


# -- live data under a host folder ---------------------------------------------------------

def test_an_old_host_folder_is_read_in_place_until_moved(home, monkeypatch):
    monkeypatch.setattr(paths, "_host_name", lambda: "thishost")
    root = Path(home["MOXI_HOME"])
    lay = Layout("cache")
    old_state = root / "state" / "thishost" / "cache"
    old_cache = root / "cache" / "thishost" / "cache"
    old_state.mkdir(parents=True)
    old_cache.mkdir(parents=True)
    assert lay.state_dir(home) == old_state
    assert lay.cache_dir(home) == old_cache
    # Only state and cache were ever keyed by host.
    (root / "data" / "thishost" / "cache").mkdir(parents=True)
    assert lay.data_dir(home) == root / "data" / "cache"
    # Once the new directory exists, it is the one.
    (root / "state" / "cache").mkdir(parents=True)
    assert lay.state_dir(home) == root / "state" / "cache"
    assert lay.cache_dir(home) == old_cache


def test_the_modules_device_variable_names_the_old_folder_first(home, monkeypatch):
    monkeypatch.setattr(paths, "_host_name", lambda: "thishost")
    root = Path(home["MOXI_HOME"])
    (root / "state" / "thishost" / "cache").mkdir(parents=True)
    (root / "state" / "box" / "cache").mkdir(parents=True)
    e = {**home, "MOXI_CACHE_DEVICE": "box", "MOXI_DEVICE": "other"}
    assert Layout("cache").state_dir(e) == root / "state" / "box" / "cache"
    assert Layout("cache").state_dir(home) == root / "state" / "thishost" / "cache"


def test_what_is_not_an_old_folder_is_passed_over(home, monkeypatch):
    monkeypatch.setattr(paths, "_host_name", lambda: "thishost")
    root = Path(home["MOXI_HOME"])
    (root / "state" / "thishost").mkdir(parents=True)
    (root / "state" / "thishost" / "cache").write_text("a file, not a directory")
    (root / "outside" / "cache").mkdir(parents=True)
    e = {**home, "MOXI_CACHE_DEVICE": "../outside"}
    assert Layout("cache").state_dir(e) == root / "state" / "cache"


@POSIX
def test_the_host_name_comes_without_the_socket_module():
    assert paths._host_name() == os.uname().nodename


# -- the runtime directory and sockets ----------------------------------------------------

@POSIX
def test_a_created_runtime_directory_is_private(home):
    lay = Layout("cache")
    path = lay.runtime_dir(home)
    assert not path.exists()
    assert lay.runtime_dir(home, create=True) == path
    assert stat.S_IMODE(path.stat().st_mode) == 0o700
    path.chmod(0o755)
    lay.runtime_dir(home, create=True)
    assert stat.S_IMODE(path.stat().st_mode) == 0o700


@WINDOWS
def test_a_created_runtime_directory_grants_only_this_user(home):
    from moxi import _win

    path = Layout("cache").runtime_dir(home, create=True)
    assert path.is_dir()
    assert _win._only_user(path, _win._token_sid(_win._TOKEN_USER))


@POSIX
def test_a_short_socket_path_is_in_the_runtime_directory(monkeypatch, tmp_path):
    # Short roots that need not exist: socket_path makes no runtime directory.
    monkeypatch.setattr(paths, "_SHORT_ROOT", str(tmp_path / "t"))
    monkeypatch.setenv("MOXI_CACHE_RUNTIME_DIR", "/r")
    assert paths.socket_path("cache", "daemon") == "/r/cache/daemon.sock"
    assert paths.socket_path("cache", "page") == "/r/cache/page.sock"
    monkeypatch.setenv("MOXI_HOME", "/h")
    assert paths.socket_path("moxi", "daemon") == "/h/runtime/moxi/daemon.sock"
    assert not (tmp_path / "t").exists()


@POSIX
def test_a_long_socket_path_is_hashed_into_a_private_directory(monkeypatch, tmp_path):
    short = tmp_path / "t"
    monkeypatch.setattr(paths, "_SHORT_ROOT", str(short))
    runtime = tmp_path / ("r" * 100)
    monkeypatch.setenv("MOXI_CACHE_RUNTIME_DIR", str(runtime))
    long = str(runtime / "cache" / "daemon.sock")
    digest = hashlib.sha256(os.fsencode(long)).hexdigest()[:16]
    got = paths.socket_path("cache", "daemon")
    assert got == str(short / f"moxi-{os.getuid()}" / f"{digest}.sock")
    # Under the real /tmp the hashed path is short whatever the long one was.
    real = f"/tmp/moxi-{os.getuid()}/{digest}.sock"
    assert len(os.fsencode(real)) < paths.SOCKET_MAX
    assert stat.S_IMODE((short / f"moxi-{os.getuid()}").stat().st_mode) == 0o700
    assert paths.socket_path("cache", "daemon") == got
    assert paths.socket_path("cache", "page") != got


def test_a_socket_name_is_one_plain_name(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "_SHORT_ROOT", str(tmp_path))
    for bad in ("", "..", "a/b"):
        with pytest.raises(ValueError):
            paths.socket_path("cache", bad)


def test_windows_has_no_socket_paths(monkeypatch):
    monkeypatch.setattr(paths, "_WINDOWS", True)
    with pytest.raises(NotImplementedError, match="named pipes"):
        paths.socket_path("cache", "daemon")


# -- this environment's programs ---------------------------------------------------------

def test_script_is_in_this_environments_scripts_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "_scripts", lambda: tmp_path)
    exe = tmp_path / ("moxi-cache-watch.exe" if sys.platform == "win32" else "moxi-cache-watch")
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    assert paths.script("moxi-cache-watch") == exe
    with pytest.raises(FileNotFoundError, match="moxi-cache-gone"):
        paths.script("moxi-cache-gone")


def test_script_never_looks_on_path(monkeypatch, tmp_path):
    elsewhere = tmp_path / "bin"
    elsewhere.mkdir()
    (elsewhere / "moxi-cache-watch").write_text("#!/bin/sh\n")
    (elsewhere / "moxi-cache-watch").chmod(0o755)
    monkeypatch.setenv("PATH", str(elsewhere))
    monkeypatch.setattr(paths, "_scripts", lambda: tmp_path / "scripts")
    with pytest.raises(FileNotFoundError):
        paths.script("moxi-cache-watch")


def test_python_is_the_environments_own_path():
    scripts = Path(sysconfig.get_path("scripts"))
    own = scripts / ("python.exe" if sys.platform == "win32" else "python")
    assert own.is_file(), "the tests run in an environment with its own python"
    assert paths.python() == own


def test_python_without_one_in_the_scripts_directory_is_this_interpreter(monkeypatch,
                                                                          tmp_path):
    monkeypatch.setattr(paths, "_scripts", lambda: tmp_path)
    assert paths.python() == Path(sys.executable)


def test_script_tries_pathext_in_the_scripts_directory_only(monkeypatch, tmp_path):
    """The Windows search, by hand: each PATHEXT extension in the scripts directory, and
    never the working directory, where a file of the same name may stand."""
    scripts, here = tmp_path / "scripts", tmp_path / "here"
    scripts.mkdir()
    here.mkdir()
    (here / "moxi-cache-watch.exe").write_text("")
    monkeypatch.chdir(here)
    monkeypatch.setattr(paths, "_WINDOWS", True)
    monkeypatch.setenv("PATHEXT", ".COM;.EXE")
    monkeypatch.setattr(os, "pathsep", ";")
    monkeypatch.setattr(paths, "_scripts", lambda: scripts)
    with pytest.raises(FileNotFoundError):
        paths.script("moxi-cache-watch")
    exe = scripts / "moxi-cache-watch.exe"
    exe.write_text("")
    exe.chmod(0o755)
    assert paths.script("moxi-cache-watch") == exe
    assert paths.script("moxi-cache-watch.exe") == exe     # a name with its extension
    for bad in ("", "..", "sub/moxi-cache-watch", "sub\\moxi-cache-watch"):
        with pytest.raises(FileNotFoundError):
            paths.script(bad)


@WINDOWS
def test_script_on_windows_never_takes_a_file_in_the_working_directory(monkeypatch,
                                                                       tmp_path):
    scripts, here = tmp_path / "scripts", tmp_path / "here"
    scripts.mkdir()
    here.mkdir()
    (here / "moxi-cache-watch.exe").write_text("")
    monkeypatch.chdir(here)
    monkeypatch.setattr(paths, "_scripts", lambda: scripts)
    with pytest.raises(FileNotFoundError):
        paths.script("moxi-cache-watch")


@WINDOWS
def test_script_and_python_find_exe_files_on_windows():
    assert paths.python().name == "python.exe"
    assert paths.script("moxi").suffix.lower() == ".exe"


# -- a user's home in a given environment ---------------------------------------------------

@POSIX
def test_a_given_home_expands_only_this_users_tilde():
    import pwd

    me = pwd.getpwuid(os.getuid())
    given = {"HOME": "/home/given"}
    assert paths._expanduser("~", given) == "/home/given"
    assert paths._expanduser("~/x", given) == "/home/given/x"
    assert paths._expanduser(f"~{me.pw_name}/x", given) == os.path.join(me.pw_dir, "x")
    assert paths._expanduser("/abs/~x", given) == "/abs/~x"


# -- import cost --------------------------------------------------------------------------

def test_importing_paths_loads_nothing_it_defers():
    code = ("import sys, moxi.paths; "
            "print(' '.join(m for m in ('socket', 'sysconfig', 'platformdirs', 'shutil', "
            "'uuid', 'hashlib') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                         check=True).stdout
    assert out.strip() == ""
