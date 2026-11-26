"""`proc.run`'s options that name a run and end all of it: `tag`, `on_start` and
`end_leftovers`. Real processes, in the test's own folder."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from pathlib import Path

import pytest

from moxi import proc
from moxi_dev.testing import PROC_WAIT_S

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX sessions and groups")
WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="Windows only; owed to Windows")
PY = sys.executable


def script(tmp_path: Path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(textwrap.dedent(text))
    return str(path)


def wait_for(check, timeout: float = PROC_WAIT_S, step: float = 0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(step)
    return check()


def read_pid(marker: Path) -> int:
    assert wait_for(marker.exists), f"{marker} was never written"
    return int(marker.read_text())


def ended(pid: int) -> bool:
    ident = proc.identity(pid)
    return ident is None or not proc.alive(ident)


def kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
    except OSError:
        pass


def a_tag() -> str:
    return uuid.uuid4().hex


# A process that writes its pid to argv[1] and sleeps; with `ignore`, it ignores SIGTERM
# and SIGINT first.
SLEEPER = """
    import os, signal, sys, time
    if len(sys.argv) > 2 and sys.argv[2] == "ignore":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    with open(sys.argv[1] + ".tmp", "w") as fh:
        fh.write(str(os.getpid()))
    os.replace(sys.argv[1] + ".tmp", sys.argv[1])
    time.sleep(120)
"""

# Starts SLEEPER (in the same group, or in a session of its own with "setsid") and exits
# once its pid is written.
LEAVES_ONE = """
    import os, subprocess, sys, time
    sleeper, marker, how = sys.argv[1:4]
    subprocess.Popen([sys.executable, sleeper, marker, *sys.argv[4:]],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=how == "setsid")
    while not os.path.exists(marker):
        time.sleep(0.02)
"""

# Prints its own MOXI_RUN_TAG and its child's.
TAGS = """
    import os, subprocess, sys
    below = subprocess.run([sys.executable, "-c",
                            "import os; print(os.environ.get('MOXI_RUN_TAG'))"],
                           capture_output=True, text=True).stdout.strip()
    print(os.environ.get("MOXI_RUN_TAG"), below)
"""


def test_the_tag_reaches_the_child_and_its_child(tmp_path):
    tag = a_tag()
    done = proc.run([PY, script(tmp_path, "tags.py", TAGS)], timeout_s=60, tag=tag)
    assert done.stdout.decode().split() == [tag, tag]


def test_the_tag_replaces_one_the_environment_holds(tmp_path):
    tag = a_tag()
    done = proc.run([PY, script(tmp_path, "tags.py", TAGS)], timeout_s=60, tag=tag,
                    env={**os.environ, proc.RUN_TAG_ENV: "an older one"})
    assert done.stdout.decode().split() == [tag, tag]


def test_without_a_tag_the_environment_is_left_as_it_is(tmp_path):
    done = proc.run([PY, script(tmp_path, "tags.py", TAGS)], timeout_s=60,
                    env={**os.environ, proc.RUN_TAG_ENV: "kept"})
    assert done.stdout.decode().split() == ["kept", "kept"]


def test_on_start_gets_the_first_process_before_the_run_ends(tmp_path):
    seen = []
    done = proc.run([PY, "-c", "import os, time; print(os.getpid()); time.sleep(0.5)"],
                    timeout_s=60,
                    on_start=lambda ident: seen.append((ident, proc.alive(ident))))
    [(ident, was_alive)] = seen
    assert isinstance(ident, proc.ProcId)
    assert ident.pid == int(done.stdout)
    assert was_alive  # proc.alive accepts the start time it carries
    assert not proc.alive(ident)


def test_an_on_start_that_raises_ends_the_run_and_is_raised(tmp_path):
    marker = tmp_path / "sleeper.pid"
    began = []

    def on_start(ident):
        began.append(ident)
        wait_for(marker.exists)
        raise RuntimeError("no record could be written")

    with pytest.raises(RuntimeError, match="no record"):
        proc.run([PY, script(tmp_path, "sleeper.py", SLEEPER), str(marker)], timeout_s=60,
                 on_start=on_start, end_leftovers=True)
    assert wait_for(lambda: ended(began[0].pid))


@POSIX
@pytest.mark.parametrize("how", ["group", "setsid"])
def test_a_normal_end_ends_what_the_run_left(tmp_path, how):
    """A group member is ended by the group, a child in a session of its own by its
    tag; both are gone when `run` returns."""
    marker = tmp_path / "left.pid"
    done = proc.run([PY, script(tmp_path, "leaves.py", LEAVES_ONE),
                     script(tmp_path, "sleeper.py", SLEEPER), str(marker), how],
                    timeout_s=60, tag=a_tag(), end_leftovers=True)
    left = read_pid(marker)
    try:
        assert done.returncode == 0
        assert ended(left), "the leftover outlived the run"
    finally:
        kill(left)


@POSIX
def test_a_leftover_that_ignores_the_first_signal_is_killed_after_the_grace(tmp_path):
    marker = tmp_path / "left.pid"
    began = time.monotonic()
    proc.run([PY, script(tmp_path, "leaves.py", LEAVES_ONE),
              script(tmp_path, "sleeper.py", SLEEPER), str(marker), "setsid", "ignore"],
             timeout_s=60, tag=a_tag(), end_leftovers=True, grace_s=1.0)
    took = time.monotonic() - began
    left = read_pid(marker)
    try:
        assert ended(left)
        assert took >= 1.0
    finally:
        kill(left)


@POSIX
def test_without_end_leftovers_a_normal_end_leaves_the_group_as_today(tmp_path):
    marker = tmp_path / "left.pid"
    proc.run([PY, script(tmp_path, "leaves.py", LEAVES_ONE),
              script(tmp_path, "sleeper.py", SLEEPER), str(marker), "group"],
             timeout_s=60, tag=a_tag())
    left = read_pid(marker)
    try:
        assert not ended(left)
    finally:
        kill(left)


@POSIX
def test_a_process_with_another_tag_is_left_alone(tmp_path):
    marker = tmp_path / "other.pid"
    other = subprocess.Popen([PY, script(tmp_path, "sleeper.py", SLEEPER), str(marker)],
                             env={**os.environ, proc.RUN_TAG_ENV: a_tag()},
                             start_new_session=True)
    try:
        read_pid(marker)
        proc.run([PY, "-c", "pass"], timeout_s=60, tag=a_tag(), end_leftovers=True)
        assert other.poll() is None
    finally:
        other.kill()
        other.wait()


@POSIX
def test_a_timeout_ends_the_tagged_child_too(tmp_path):
    marker = tmp_path / "left.pid"
    keeps_on = """
        import os, subprocess, sys, time
        sleeper, marker = sys.argv[1:3]
        subprocess.Popen([sys.executable, sleeper, marker], start_new_session=True)
        time.sleep(120)
    """
    with pytest.raises(subprocess.TimeoutExpired):
        proc.run([PY, script(tmp_path, "keeps.py", keeps_on),
                  script(tmp_path, "sleeper.py", SLEEPER), str(marker)],
                 timeout_s=3, tag=a_tag(), end_leftovers=True, grace_s=1.0)
    left = read_pid(marker)
    try:
        assert ended(left)
    finally:
        kill(left)


@POSIX
def test_an_interrupt_sends_the_first_signal_and_kills_only_after_the_grace(tmp_path):
    marker = tmp_path / "sleeper.pid"
    caught = tmp_path / "caught"
    deaf = f"""
        import os, signal, sys, time
        def heard(signum, frame):
            open({str(caught)!r}, "w").write(signal.Signals(signum).name)
        signal.signal(signal.SIGINT, heard)
        with open({str(marker)!r} + ".tmp", "w") as fh:
            fh.write(str(os.getpid()))
        os.replace({str(marker)!r} + ".tmp", {str(marker)!r})
        while True:
            time.sleep(1)
    """
    began = []

    def on_start(ident):
        wait_for(marker.exists)
        began.append(time.monotonic())
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        proc.run([PY, script(tmp_path, "deaf.py", deaf)], timeout_s=60, tag=a_tag(),
                 on_start=on_start, end_leftovers=True, first_signal="SIGINT", grace_s=1.5)
    took = time.monotonic() - began[0]
    pid = int(marker.read_text())
    try:
        assert caught.read_text() == "SIGINT"
        assert took >= 1.5
        assert ended(pid)
    finally:
        kill(pid)


@POSIX
def test_a_second_interrupt_during_the_grace_kills_at_once(tmp_path):
    marker = tmp_path / "sleeper.pid"
    caught = tmp_path / "caught"
    deaf = f"""
        import os, signal, sys, time
        def heard(signum, frame):
            open({str(caught)!r}, "w").write("yes")
        signal.signal(signal.SIGINT, heard)
        with open({str(marker)!r} + ".tmp", "w") as fh:
            fh.write(str(os.getpid()))
        os.replace({str(marker)!r} + ".tmp", {str(marker)!r})
        while True:
            time.sleep(1)
    """

    def on_start(ident):
        wait_for(marker.exists)

        def again():
            wait_for(caught.exists)
            proc._interrupt_main()

        threading.Thread(target=again, daemon=True).start()
        raise RuntimeError("the first interrupt")

    began = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        proc.run([PY, script(tmp_path, "deaf.py", deaf)], timeout_s=60, tag=a_tag(),
                 on_start=on_start, end_leftovers=True, first_signal="SIGINT", grace_s=60)
    pid = int(marker.read_text())
    try:
        assert time.monotonic() - began < 30
        assert wait_for(lambda: ended(pid))
    finally:
        kill(pid)


def test_proc_gains_one_public_name_and_no_new_function():
    assert proc.__all__ == ["LIFELINE_ENV", "RUN_TAG_ENV", "CODES_KEPT", "ProcId", "run",
                            "spawn", "exec_env", "lifeline", "lifeline_kind", "identity",
                            "alive", "process_env", "cmdline", "status", "parent"]
    assert proc.RUN_TAG_ENV == "MOXI_RUN_TAG"


# -- Windows -----------------------------------------------------------------------------

@WINDOWS
def test_on_windows_a_normal_end_terminates_the_job(tmp_path):
    marker = tmp_path / "left.pid"
    proc.run([PY, script(tmp_path, "leaves.py", LEAVES_ONE),
              script(tmp_path, "sleeper.py", SLEEPER), str(marker), "group"],
             timeout_s=60, end_leftovers=True)
    left = read_pid(marker)
    try:
        assert wait_for(lambda: ended(left))
    finally:
        kill(left)


@WINDOWS
def test_on_windows_the_run_ends_when_its_caller_dies(tmp_path):
    marker = tmp_path / "sleeper.pid"
    caller = f"""
        import sys
        from moxi import proc
        proc.run([sys.executable, {script(tmp_path, "sleeper.py", SLEEPER)!r},
                  {str(marker)!r}], timeout_s=120, end_leftovers=True)
    """
    holder = subprocess.Popen([PY, script(tmp_path, "caller.py", caller)])
    try:
        pid = read_pid(marker)
        holder.kill()
        holder.wait()
        assert wait_for(lambda: ended(pid))
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait()
