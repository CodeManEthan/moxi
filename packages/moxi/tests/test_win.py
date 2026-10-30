"""moxi._win: private directories by access list, and Job Objects (Windows only).

Off Windows only the import is checked: the module binds nothing until a call.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from moxi import _win, files, proc

WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="Windows access lists and jobs")


def test_importing_the_module_loads_no_ctypes():
    code = "import sys, moxi._win; print('ctypes' in sys.modules)"
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                          check=True)
    assert done.stdout.strip() == "False"


def user():
    return _win._token_sid(_win._TOKEN_USER)


def icacls(path) -> str:
    return subprocess.run(["icacls", str(path)], capture_output=True, text=True,
                          check=True).stdout


@WINDOWS
def test_private_dir_makes_a_directory_only_this_user_can_use(tmp_path):
    target = tmp_path / "a" / "b"
    assert _win.private_dir(target) is None
    assert target.is_dir()
    assert _win._only_user(target, user())
    # One entry in the list, and nothing inherited from tmp_path's.
    entries = [line for line in icacls(target).splitlines()[:-1] if line.strip()]
    assert len(entries) == 1, entries
    assert "(I)" not in entries[0]


@WINDOWS
def test_each_missing_parent_of_a_private_dir_is_private_too(tmp_path):
    before = icacls(tmp_path)
    target = tmp_path / "a" / "b" / "c"
    _win.private_dir(target)
    for each in (tmp_path / "a", tmp_path / "a" / "b", target):
        assert _win._only_user(each, user()), each
    assert icacls(tmp_path) == before


@WINDOWS
def test_the_directories_made_on_the_way_to_a_file_are_private(tmp_path):
    before = icacls(tmp_path)
    files.atomic_write(tmp_path / "a" / "b" / "f", "x")
    for each in (tmp_path / "a", tmp_path / "a" / "b"):
        assert _win._only_user(each, user()), each
    assert icacls(tmp_path) == before


@WINDOWS
def test_what_is_made_inside_a_private_dir_is_private_too(tmp_path):
    target = tmp_path / "p"
    _win.private_dir(target)
    (target / "inner").mkdir()
    (target / "f.txt").write_text("x")
    for inside in (target / "inner", target / "f.txt"):
        entries = [line for line in icacls(inside).splitlines()[:-1] if line.strip()]
        assert len(entries) == 1, entries


@WINDOWS
def test_an_existing_open_directory_of_ours_is_made_private(tmp_path):
    target = tmp_path / "open"
    target.mkdir()
    assert not _win._only_user(target, user())
    _win.private_dir(target)
    assert _win._only_user(target, user())


@WINDOWS
def test_a_second_call_on_a_private_dir_changes_nothing(tmp_path):
    target = tmp_path / "p"
    _win.private_dir(target)
    before = icacls(target)
    _win.private_dir(target)
    assert icacls(target) == before


@WINDOWS
def test_private_dir_refuses_a_file(tmp_path):
    target = tmp_path / "f"
    target.write_text("x")
    with pytest.raises(NotADirectoryError):
        _win.private_dir(target)


@WINDOWS
def test_files_private_dir_goes_through_the_access_list(tmp_path):
    made = files.private_dir(tmp_path / "d")
    assert _win._only_user(made, user())


SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


@WINDOWS
def test_terminating_a_job_ends_its_processes():
    child = subprocess.Popen(SLEEPER)
    with _win.Job() as job:
        job.assign(child.pid)
        job.terminate(9)
        assert child.wait(timeout=30) == 9


@WINDOWS
def test_a_kill_on_close_job_ends_its_processes_when_closed():
    child = subprocess.Popen(SLEEPER)
    job = _win.Job(kill_on_close=True)
    job.assign(child.pid)
    time.sleep(0.2)
    assert child.poll() is None
    job.close()
    child.wait(timeout=30)


@WINDOWS
def test_a_plain_job_lets_its_processes_run_when_closed():
    child = subprocess.Popen(SLEEPER)
    try:
        job = _win.Job()
        job.assign(child.pid)
        job.close()
        time.sleep(0.5)
        assert child.poll() is None
    finally:
        child.kill()
        child.wait()


@WINDOWS
def test_a_watched_child_on_windows_ends_when_its_lifeline_closes():
    child = proc.spawn(SLEEPER, lifeline="watch")
    assert isinstance(child.lifeline, _win.Job)
    time.sleep(0.2)
    child.lifeline.close()
    child.wait(timeout=30)
