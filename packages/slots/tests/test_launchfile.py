"""The launch file and the exec verdict, on files and a fake pane: every outcome, the read
order, the give-up at the bound, and the sweep."""

from __future__ import annotations

import fcntl
import os
import stat
import threading
import time

import pytest

from moxi_slots import launchfile, places
from moxi_slots.errors import UsageError
from moxi_slots.launchfile import PaneState, Verdict

pytestmark = pytest.mark.touches("packages/slots/src/moxi_slots/launchfile.py")


def pane(state: PaneState):
    seen = []

    def read(pane_id: str) -> PaneState:
        seen.append(pane_id)
        return state
    read.seen = seen
    return read


ALIVE = PaneState(True, False, None, "slot-1", "@1")
DEAD3 = PaneState(True, True, 3, "slot-1", "@1")
DEAD127 = PaneState(True, True, 127, "slot-1", "@1")
GONE = PaneState(False)


def launch_file(text: str | None = None):
    path = launchfile.write(["true"], {"SECRET": "x"}, "/")
    if text is not None:
        with open(path, "w") as f:
            f.write(text + "\n")
    return path


def test_a_launch_file_is_private_and_holds_the_launch():
    path = launch_file()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(places.launch_dir()).st_mode) == 0o700
    assert path.parent == places.launch_dir() and path.name.endswith(".launch")
    assert open(path).read() == '{"argv": ["true"], "env": {"SECRET": "x"}, "cwd": "/"}'


@pytest.mark.parametrize("argv, env, cwd", [
    ([], {}, "/"), ("true", {}, "/"), ([""], {}, "/"), (["a\0b"], {}, "/"),
    (["true"], {"A=B": "1"}, "/"), (["true"], {"A": 1}, "/"), (["true"], [], "/"),
    (["true"], {}, "relative"), (["true"], {}, None)])
def test_check_refuses_a_malformed_launch(argv, env, cwd):
    with pytest.raises(UsageError):
        launchfile.check(argv, env, cwd)


def test_exec_with_the_lock_free_is_started():
    path = launch_file("exec")
    assert launchfile.wait(path, "%1", pane=pane(ALIVE)) == Verdict("started", "exec")
    assert not path.exists()


def test_exec_and_a_dead_pane_is_started_with_its_status():
    path = launch_file("exec")
    assert launchfile.wait(path, "%1", pane=pane(DEAD3)) == Verdict("started", "exec", 3)


def test_exec_and_a_vanished_pane_is_started():
    path = launch_file("exec")
    assert launchfile.wait(path, "%1", pane=pane(GONE)) == Verdict("started", "exec")


def test_a_failed_line_is_failed_with_the_panes_status():
    path = launch_file("failed: cwd: /nope: No such file or directory")
    assert launchfile.wait(path, "%1", pane=pane(DEAD127)) == Verdict(
        "failed", "failed: cwd: /nope: No such file or directory", 127)
    assert not path.exists()


def test_taken_with_the_lock_free_is_a_trampoline_that_died():
    path = launch_file("taken")
    assert launchfile.wait(path, "%1", pane=pane(DEAD3)) == Verdict(
        "failed", "trampoline died", 3)


def test_an_untaken_launch_and_a_dead_pane_is_a_trampoline_that_died():
    path = launch_file()
    assert launchfile.wait(path, "%1", pane=pane(DEAD3)) == Verdict(
        "failed", "trampoline died", 3)
    assert not path.exists()


def test_a_launch_never_taken_is_removed_at_the_bound():
    path = launch_file()
    started = time.monotonic()
    assert launchfile.wait(path, "%1", bound=0.3, pane=pane(ALIVE)) == Verdict(
        "failed", "not taken")
    assert 0.3 <= time.monotonic() - started < 2
    assert not path.exists()


def test_a_lock_held_past_the_bound_is_unconfirmed():
    path = launch_file("taken")
    fd = os.open(path, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert launchfile.wait(path, "%1", bound=0.3, pane=pane(ALIVE)) == Verdict(
            "unconfirmed", "taken")
    finally:
        os.close(fd)
    assert not path.exists()


def test_the_verdict_is_read_only_after_the_lock_is_seen_free():
    """`exec` is in the file while the trampoline still holds the lock; its execve then
    fails and it writes `failed:` before letting go. The verdict is `failed`."""
    path = launch_file("exec")
    fd = os.open(path, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)

    def execve_fails():
        time.sleep(0.2)
        os.ftruncate(fd, 0)
        os.pwrite(fd, b"failed: execve: [Errno 8] Exec format error\n", 0)
        os.close(fd)

    worker = threading.Thread(target=execve_fails)
    worker.start()
    try:
        verdict = launchfile.wait(path, "%1", pane=pane(ALIVE))
    finally:
        worker.join()
    assert verdict.exec == "failed"
    assert verdict.line == "failed: execve: [Errno 8] Exec format error"


def test_a_file_already_gone_is_failed():
    path = launch_file()
    os.remove(path)
    assert launchfile.wait(path, "%1", pane=pane(ALIVE)).exec == "failed"


def test_the_sweep_removes_old_files_nothing_holds():
    old, held, new = launch_file(), launch_file(), launch_file()
    past = time.time() - 120
    for path in (old, held):
        os.utime(path, (past, past))
    fd = os.open(held, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert launchfile.sweep() == [old]
    finally:
        os.close(fd)
    assert not old.exists() and held.exists() and new.exists()


def test_the_trampoline_is_installed():
    assert os.access(launchfile.trampoline(), os.X_OK)
