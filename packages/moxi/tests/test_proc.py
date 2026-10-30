"""moxi.proc: run with a deadline, children on a lifeline, detached children, identities.

Every child here is this interpreter running a small script, so the tests that need no
POSIX call hold on Windows too.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from moxi import _watch, proc

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX sessions and groups")
PY = sys.executable


def script(tmp_path: Path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(textwrap.dedent(text))
    return str(path)


def wait_for(check, timeout: float = 10.0, step: float = 0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(step)
    return check()


def gone(ident: proc.ProcId, timeout: float = 10.0) -> bool:
    return wait_for(lambda: not proc.alive(ident), timeout)


def child_env(**extra: str) -> dict[str, str]:
    """A small whole environment for a child: what Python needs to start, and `extra`."""
    keep = ("PATH", "SYSTEMROOT", "PYTHONPATH")
    return {**{k: os.environ[k] for k in keep if k in os.environ}, **extra}


# -- run ---------------------------------------------------------------------------------

def test_run_captures_bytes_by_default_and_text_for_text_input():
    done = proc.run([PY, "-c", "import sys; print('out'); print('err', file=sys.stderr)"],
                    timeout=30)
    assert (done.returncode, done.stdout.strip(), done.stderr.strip()) == (0, b"out", b"err")
    fed = proc.run([PY, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
                   timeout=30, input="sécret")
    assert fed.stdout == "SÉCRET"


def test_run_feeds_bytes_input():
    fed = proc.run([PY, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
                   timeout=30, input=b"\x00\x01")
    assert fed.stdout == b"\x00\x01"


def test_run_closes_stdin_without_input():
    done = proc.run([PY, "-c", "import sys; print(repr(sys.stdin.read()))"], timeout=30)
    assert done.stdout.strip() == b"''"


def test_run_does_not_check_the_exit_status():
    assert proc.run([PY, "-c", "raise SystemExit(4)"], timeout=30).returncode == 4


def test_run_uses_env_as_given_and_cwd(tmp_path):
    code = "import os; print(os.environ.get('ONLY'), os.environ.get('HOME'), os.getcwd())"
    done = proc.run([PY, "-c", code], timeout=30, env=child_env(ONLY="yes"), cwd=tmp_path)
    only, home, cwd = done.stdout.decode().split()
    assert (only, home) == ("yes", "None")
    assert Path(cwd).resolve() == tmp_path.resolve()


def test_run_without_capture_leaves_output_alone(tmp_path):
    done = proc.run([PY, "-c", "pass"], timeout=30, capture=False)
    assert (done.stdout, done.stderr) == (None, None)


def test_run_refuses_a_single_string():
    with pytest.raises(TypeError):
        proc.run("echo hi", timeout=5)


@POSIX
def test_run_starts_the_child_in_a_new_session():
    done = proc.run([PY, "-c", "import os; print(os.getpid(), os.getsid(0), os.getpgrp())"],
                    timeout=30)
    pid, sid, pgrp = map(int, done.stdout.split())
    assert pid == sid == pgrp
    assert sid != os.getsid(0)


TREE = """
    import subprocess, sys, time
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    print("ready", grandchild.pid, flush=True)
    time.sleep(60)
"""


def test_a_timed_out_run_kills_the_whole_tree_and_keeps_its_output(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        proc.run([PY, script(tmp_path, "tree.py", TREE)], timeout=3)
    word, pid = caught.value.output.split()
    assert word == b"ready"
    assert caught.value.timeout == 3
    assert wait_for(lambda: proc.identity(int(pid)) is None
                    or not proc.alive(proc.identity(int(pid))), 10)


@POSIX
def test_a_child_that_ignores_sigterm_is_killed_after_the_grace(tmp_path, monkeypatch):
    monkeypatch.setattr(proc, "KILL_GRACE", 0.5)
    stubborn = """
        import signal, time
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        print("ready", flush=True)
        time.sleep(60)
    """
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        proc.run([PY, script(tmp_path, "stubborn.py", stubborn)], timeout=2)
    took = time.monotonic() - start
    assert caught.value.output == b"ready\n"
    assert 2.4 <= took < 10


# -- spawn with a lifeline -----------------------------------------------------------------

def test_a_stdin_child_sees_eof_when_the_lifeline_closes(tmp_path):
    marker = tmp_path / "eof"
    reader = f"""
        import sys
        sys.stdin.buffer.read()
        open({str(marker)!r}, "w").close()
    """
    child = proc.spawn([PY, script(tmp_path, "reader.py", reader)])
    assert child.lifeline is child.stdin
    time.sleep(0.2)
    assert child.poll() is None
    child.lifeline.close()
    assert child.wait(timeout=30) == 0
    assert marker.exists()


FD_CHILD = """
    import os, threading
    from moxi import proc
    closed = threading.Event()
    proc.lifeline(on_close=closed.set)
    print("env", proc.LIFELINE_ENV in os.environ, flush=True)
    closed.wait(30)
    print("closed", closed.is_set(), flush=True)
"""


def test_an_fd_child_keeps_stdin_free_and_hears_the_close(tmp_path):
    child = proc.spawn([PY, script(tmp_path, "fd.py", FD_CHILD)], lifeline="fd",
                       stdout=subprocess.PIPE)
    assert child.stdin is None
    assert child.stdout.readline() == b"env False\n"
    time.sleep(0.2)
    assert child.poll() is None
    child.lifeline.close()
    assert child.stdout.readline() == b"closed True\n"
    assert child.wait(timeout=30) == 0
    child.stdout.close()


def test_the_default_close_interrupts_the_main_thread(tmp_path):
    interrupted = """
        import signal, sys, time
        from moxi import proc
        signal.signal(signal.SIGINT, signal.default_int_handler)  # even if inherited ignored
        proc.lifeline()
        print("ready", flush=True)
        try:
            time.sleep(60)
        except KeyboardInterrupt:
            sys.exit(7)
    """
    child = proc.spawn([PY, script(tmp_path, "int.py", interrupted)], lifeline="fd",
                       stdout=subprocess.PIPE)
    assert child.stdout.readline() == b"ready\n"
    child.lifeline.close()
    assert child.wait(timeout=30) == 7
    child.stdout.close()


def test_the_default_close_ends_a_process_that_ignores_the_interrupt(tmp_path):
    deaf = """
        import signal, time
        from moxi import proc
        signal.signal(signal.SIGINT, signal.default_int_handler)
        proc.EXIT_GRACE = 0.5
        proc.lifeline()
        print("ready", flush=True)
        while True:
            try:
                time.sleep(60)
            except KeyboardInterrupt:
                pass
    """
    child = proc.spawn([PY, script(tmp_path, "deaf.py", deaf)], lifeline="fd",
                       stdout=subprocess.PIPE)
    assert child.stdout.readline() == b"ready\n"
    child.lifeline.close()
    assert child.wait(timeout=30) == 1
    child.stdout.close()


def test_a_child_on_stdin_calls_lifeline_without_the_variable(tmp_path):
    on_stdin = """
        import threading
        from moxi import proc
        closed = threading.Event()
        proc.lifeline(on_close=closed.set)
        print("ready", flush=True)
        raise SystemExit(0 if closed.wait(30) else 1)
    """
    child = proc.spawn([PY, script(tmp_path, "stdin.py", on_stdin)], stdout=subprocess.PIPE)
    assert child.stdout.readline() == b"ready\n"
    child.lifeline.close()
    assert child.wait(timeout=30) == 0
    child.stdout.close()


MIDDLE = """
    import subprocess, sys, time
    from moxi import proc
    kind, argv = sys.argv[1], sys.argv[2:]
    child = proc.spawn(argv, lifeline=kind, stdout=subprocess.DEVNULL)
    print(child.pid, flush=True)
    time.sleep(60)
"""

COOPERATIVE = """
    import time
    from moxi import proc
    proc.lifeline()
    time.sleep(60)
"""


@pytest.mark.parametrize("kind", ["stdin", "fd", "watch"])
def test_the_child_ends_when_its_parent_is_killed(tmp_path, kind):
    if kind == "watch":
        argv = [PY, script(tmp_path, "tree.py", TREE)]
    else:
        argv = [PY, script(tmp_path, "coop.py", COOPERATIVE)]
    middle = subprocess.Popen([PY, script(tmp_path, "middle.py", MIDDLE), kind, *argv],
                              stdout=subprocess.PIPE)
    try:
        pid = int(middle.stdout.readline())
        child = wait_for(lambda: proc.identity(pid))
        assert child is not None
        time.sleep(0.3)
        assert proc.alive(child)
        middle.kill()
        middle.wait(timeout=30)
        assert gone(child, 15)
    finally:
        middle.stdout.close()
        if middle.poll() is None:
            middle.kill()
            middle.wait()


@POSIX
def test_a_watched_child_is_alone_in_its_session_and_its_tree_is_killed(tmp_path):
    child = proc.spawn([PY, script(tmp_path, "tree.py", TREE)], lifeline="watch",
                       stdout=subprocess.PIPE)
    word, pid = child.stdout.readline().split()
    grandchild = proc.identity(int(pid))
    assert os.getsid(child.pid) == child.pid
    child.lifeline.close()
    assert child.wait(timeout=30) == -15
    assert gone(grandchild)
    assert child.watchdog.wait(timeout=30) == 0
    child.stdout.close()


@POSIX
def test_the_watchdog_kills_a_child_that_ignores_sigterm(tmp_path):
    stubborn = """
        import signal, time
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        print("ready", flush=True)
        time.sleep(60)
    """
    child = proc.spawn([PY, script(tmp_path, "stubborn.py", stubborn)], lifeline="watch",
                       stdout=subprocess.PIPE)
    assert child.stdout.readline() == b"ready\n"
    start = time.monotonic()
    child.lifeline.close()
    assert child.wait(timeout=30) == -9
    assert time.monotonic() - start >= _watch.GRACE - 0.5
    child.stdout.close()


@POSIX
def test_the_watchdog_leaves_when_the_child_ends_by_itself(tmp_path):
    child = proc.spawn([PY, "-c", "pass"], lifeline="watch")
    assert child.wait(timeout=30) == 0
    assert child.watchdog.wait(timeout=30) == 0
    child.lifeline.close()


@POSIX
def test_the_watchdog_refuses_bad_arguments(capsys):
    assert _watch.main([]) == 2
    assert _watch.main(["x"]) == 2
    assert _watch.main(["1"]) == 2
    assert "usage" in capsys.readouterr().err


def test_spawn_refuses_what_does_not_fit():
    with pytest.raises(ValueError, match="one of stdin, fd, watch"):
        proc.spawn([PY, "-c", "pass"], lifeline="pipe")
    with pytest.raises(ValueError, match="no lifeline"):
        proc.spawn([PY, "-c", "pass"], detached=True, lifeline="stdin")
    with pytest.raises(ValueError, match="output="):
        proc.spawn([PY, "-c", "pass"], detached=True, stdout=subprocess.PIPE)
    with pytest.raises(ValueError, match="for a detached child"):
        proc.spawn([PY, "-c", "pass"], output="x.log")


def test_spawn_passes_env_as_given_and_cwd(tmp_path):
    code = "import os; print(os.environ.get('ONLY'), os.environ.get('HOME'), os.getcwd())"
    child = proc.spawn([PY, "-c", code], env=child_env(ONLY="yes"), cwd=tmp_path,
                       stdout=subprocess.PIPE)
    out, _ = child.communicate(timeout=30)
    only, home, cwd = out.decode().split()
    assert (only, home) == ("yes", "None")
    assert Path(cwd).resolve() == tmp_path.resolve()


# -- spawn detached ------------------------------------------------------------------------

DETACHED = """
    import os, sys
    print("out", os.environ.get("ONLY"), repr(sys.stdin.read()), flush=True)
    print("err", file=sys.stderr, flush=True)
    if hasattr(os, "getsid"):
        print("session", os.getsid(0) == os.getpid(), flush=True)
    print("done", flush=True)
"""


def test_a_detached_child_appends_to_its_output_and_returns_its_pid(tmp_path):
    log = tmp_path / "logs" / "child.log"
    log.parent.mkdir()
    log.write_text("old\n")
    pid = proc.spawn([PY, script(tmp_path, "d.py", DETACHED)], detached=True, output=log,
                     env=child_env(ONLY="yes"), cwd=tmp_path)
    assert isinstance(pid, int)
    assert wait_for(lambda: "done" in log.read_text(), 30)
    lines = log.read_text().splitlines()
    assert lines[0] == "old"
    assert "out yes ''" in lines
    assert "err" in lines
    if sys.platform != "win32":
        assert "session True" in lines


@POSIX
def test_a_detached_output_file_is_created_0600(tmp_path):
    log = tmp_path / "new" / "child.log"
    proc.spawn([PY, "-c", "print('done', flush=True)"], detached=True, output=log)
    assert wait_for(lambda: log.exists() and "done" in log.read_text(), 30)
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_a_detached_child_without_output_writes_nowhere(tmp_path):
    pid = proc.spawn([PY, "-c", "print('lost')"], detached=True, cwd=tmp_path)
    ident = proc.identity(pid)
    assert ident is None or gone(ident)
    assert list(tmp_path.iterdir()) == []


@POSIX
def test_an_ended_detached_child_is_reaped_by_the_next_spawn(tmp_path):
    import psutil

    first = proc.spawn([PY, "-c", "pass"], detached=True)
    assert wait_for(lambda: psutil.Process(first).status() == psutil.STATUS_ZOMBIE)
    proc.spawn([PY, "-c", "pass"], detached=True)
    assert not psutil.pid_exists(first) or psutil.Process(first).ppid() != os.getpid()


# -- identity ------------------------------------------------------------------------------

def test_identity_names_this_process_by_its_start_time():
    import psutil

    me = proc.identity(os.getpid())
    assert me == proc.ProcId(os.getpid(), psutil.Process().create_time())
    assert proc.alive(me)


def test_a_different_start_time_is_not_alive():
    me = proc.identity(os.getpid())
    assert not proc.alive(proc.ProcId(me.pid, me.started - 10))


def test_an_ended_process_has_no_identity():
    child = subprocess.Popen([PY, "-c", "pass"])
    ident = proc.identity(child.pid)
    child.wait()
    assert proc.identity(child.pid) is None or proc.identity(child.pid) != ident
    assert not proc.alive(ident)


def test_identity_of_no_pid_is_none():
    assert proc.identity(0) is None
    assert proc.identity(-5) is None


def test_procid_is_frozen():
    ident = proc.ProcId(1, 2.0)
    with pytest.raises(AttributeError):
        ident.pid = 3  # type: ignore[misc]


# -- import weight -------------------------------------------------------------------------

def test_importing_the_module_loads_no_psutil_or_ctypes():
    code = "import sys, moxi.proc; print('psutil' in sys.modules, 'ctypes' in sys.modules)"
    done = subprocess.run([PY, "-I", "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.split() == ["False", "False"]
