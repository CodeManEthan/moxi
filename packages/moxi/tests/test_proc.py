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


@pytest.mark.parametrize("stale", ["inherited", "env-given"])
def test_a_run_child_sees_no_lifeline_variable(monkeypatch, stale):
    code = "import os; print(os.environ.get(%r))" % proc.LIFELINE_ENV
    env = None
    if stale == "inherited":
        monkeypatch.setenv(proc.LIFELINE_ENV, "7")
    else:
        monkeypatch.delenv(proc.LIFELINE_ENV, raising=False)
        env = child_env(**{proc.LIFELINE_ENV: "7"})
    assert proc.run([PY, "-c", code], timeout=30, env=env).stdout.strip() == b"None"


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


STUBBORN_GRANDCHILD = """
    import os, signal, sys, time
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    with open(sys.argv[1] + ".tmp", "w") as f:
        f.write(str(os.getpid()))
    os.replace(sys.argv[1] + ".tmp", sys.argv[1])
    time.sleep(60)
"""

STARTS_A_STUBBORN_GRANDCHILD = """
    import os, subprocess, sys, time
    grandchild, marker = sys.argv[1:]
    subprocess.Popen([sys.executable, grandchild, marker], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    while not os.path.exists(marker):
        time.sleep(0.02)
    print("ready", flush=True)
    time.sleep(60)
"""


@POSIX
@pytest.mark.parametrize("capture", [True, False])
def test_a_grandchild_that_ignores_sigterm_and_holds_no_pipe_is_killed(
        tmp_path, monkeypatch, capture):
    """The first child ends on SIGTERM and closes its pipes at once; the grandchild
    ignores SIGTERM and holds none of them, and still gets SIGKILL after the grace."""
    monkeypatch.setattr(proc, "KILL_GRACE", 0.5)
    marker = tmp_path / "grandchild.pid"
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        proc.run([PY, script(tmp_path, "tree.py", STARTS_A_STUBBORN_GRANDCHILD),
                  script(tmp_path, "grandchild.py", STUBBORN_GRANDCHILD), str(marker)],
                 timeout=2, capture=capture)
    took = time.monotonic() - start
    grandchild = int(marker.read_text())
    try:
        assert caught.value.output == (b"ready\n" if capture else None)
        ident = proc.identity(grandchild)
        assert ident is None or gone(ident, 3), "the grandchild outlived the run"
        assert took >= 2.4                  # it had the grace before SIGKILL
    finally:
        try:
            os.kill(grandchild, 9)
        except ProcessLookupError:
            pass


def test_a_tree_that_ends_on_sigterm_does_not_wait_out_the_grace(tmp_path, monkeypatch):
    monkeypatch.setattr(proc, "KILL_GRACE", 5)
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        proc.run([PY, script(tmp_path, "tree.py", TREE)], timeout=1)
    assert time.monotonic() - start < 4


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
        try:
            # Inside the try: the interrupt can come as soon as the parent reads this.
            print("ready", flush=True)
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
        ready = False
        while True:
            try:
                if not ready:
                    ready = True
                    print("ready", flush=True)
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
def test_a_watched_child_stays_in_this_group_and_its_tree_is_killed(tmp_path):
    child = proc.spawn([PY, script(tmp_path, "tree.py", TREE)], lifeline="watch",
                       stdout=subprocess.PIPE)
    word, pid = child.stdout.readline().split()
    grandchild = proc.identity(int(pid))
    assert os.getpgid(child.pid) == os.getpgrp()
    assert os.getsid(child.pid) == os.getsid(0)
    assert os.getsid(child.watchdog.pid) == child.watchdog.pid
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
    assert _watch.main(["123"]) == 2
    assert _watch.main(["1", "100.5"]) == 2
    assert _watch.main(["123", "soon"]) == 2
    assert _watch.main(["123", "nan"]) == 2
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


# -- what a child knows of its lifeline ----------------------------------------------------

KIND = """
    import os
    from moxi import proc
    print(proc.lifeline_kind(), os.environ.get(proc.LIFELINE_ENV), flush=True)
"""


@pytest.fixture
def unread(monkeypatch):
    """This process as a child that has not called `lifeline()`, whose parent set no
    `MOXI_LIFELINE_FD`, with the lifeline's thread never started."""
    monkeypatch.setattr(proc, "_lifeline_value", proc._UNREAD)
    monkeypatch.delenv(proc.LIFELINE_ENV, raising=False)
    held = []
    monkeypatch.setattr(proc, "_hold", lambda fd, on_close: held.append(fd))
    return held


def _said(child: subprocess.Popen) -> str:
    out, _ = child.communicate(timeout=30)
    return out.decode().strip()


@pytest.mark.parametrize("given", [False, True], ids=["inherited", "env-given"])
def test_a_stdin_child_sees_the_variable_at_0_and_reads_stdin(tmp_path, monkeypatch, given):
    monkeypatch.delenv(proc.LIFELINE_ENV, raising=False)
    env = child_env(ONLY="yes") if given else None
    child = proc.spawn([PY, script(tmp_path, "kind.py", KIND)], env=env,
                       stdout=subprocess.PIPE)
    assert _said(child) == "stdin 0"


def test_a_stdin_child_of_a_child_sees_0_not_its_parents_value(tmp_path, monkeypatch):
    monkeypatch.setenv(proc.LIFELINE_ENV, "7")
    child = proc.spawn([PY, script(tmp_path, "kind.py", KIND)],
                       env=child_env(**{proc.LIFELINE_ENV: "9"}), stdout=subprocess.PIPE)
    assert _said(child) == "stdin 0"


def test_an_fd_child_reads_fd(tmp_path, monkeypatch):
    monkeypatch.setenv(proc.LIFELINE_ENV, "7")
    child = proc.spawn([PY, script(tmp_path, "kind.py", KIND)], lifeline="fd",
                       stdout=subprocess.PIPE)
    kind, value = _said(child).split()
    assert kind == "fd"
    assert value not in ("0", "7")
    child.lifeline.close()


@POSIX
@pytest.mark.parametrize("stale", ["none", "inherited", "env-given"])
def test_a_watched_child_reads_none(tmp_path, monkeypatch, stale):
    monkeypatch.delenv(proc.LIFELINE_ENV, raising=False)
    env = None
    if stale == "inherited":
        monkeypatch.setenv(proc.LIFELINE_ENV, "7")
    elif stale == "env-given":
        env = child_env(**{proc.LIFELINE_ENV: "7"})
    child = proc.spawn([PY, script(tmp_path, "kind.py", KIND)], lifeline="watch", env=env,
                       stdout=subprocess.PIPE)
    assert _said(child) == "None None"
    child.lifeline.close()
    assert child.watchdog.wait(timeout=30) == 0


@pytest.mark.parametrize("stale", ["none", "inherited", "env-given"])
def test_a_detached_child_reads_none(tables, tmp_path, monkeypatch, stale):
    monkeypatch.delenv(proc.LIFELINE_ENV, raising=False)
    env = None
    if stale == "inherited":
        monkeypatch.setenv(proc.LIFELINE_ENV, "7")
    elif stale == "env-given":
        env = child_env(**{proc.LIFELINE_ENV: "7"})
    log = tmp_path / "kind.log"
    pid = proc.spawn([PY, script(tmp_path, "kind.py", KIND)], detached=True, output=log,
                     env=env)
    assert _until_ended(pid) == 0
    assert log.read_text().strip() == "None None"


def test_a_plain_child_reads_none(tmp_path, monkeypatch):
    monkeypatch.delenv(proc.LIFELINE_ENV, raising=False)
    child = subprocess.Popen([PY, script(tmp_path, "kind.py", KIND)], stdout=subprocess.PIPE)
    assert _said(child) == "None None"


@pytest.mark.parametrize("value, kind", [
    ("0", "stdin"), ("00", "stdin"), ("3", "fd"), ("1234", "fd"), ("-1", None), ("x", None),
    ("", None), (" 3", None), ("3.0", None), ("٣", None), ("+3", None)])
def test_lifeline_kind_reads_the_variable_and_nothing_else(unread, monkeypatch, value, kind):
    monkeypatch.setenv(proc.LIFELINE_ENV, value)
    assert proc.lifeline_kind() == kind


def test_lifeline_kind_with_the_variable_unset_is_none(unread):
    assert proc.lifeline_kind() is None


def test_lifeline_kind_checks_no_descriptor_is_open(unread, monkeypatch):
    read_end, write_end = os.pipe()
    os.close(read_end)
    os.close(write_end)
    monkeypatch.setenv(proc.LIFELINE_ENV, str(read_end))
    assert proc.lifeline_kind() == "fd"


@pytest.mark.parametrize("value, kind", [("0", "stdin"), ("5", "fd"), (None, None)])
def test_lifeline_kind_after_lifeline_answers_what_it_read(unread, monkeypatch, value, kind):
    if value is not None:
        monkeypatch.setenv(proc.LIFELINE_ENV, value)
    assert proc.lifeline_kind() == kind
    proc.lifeline(on_close=lambda: None)
    assert proc.LIFELINE_ENV not in os.environ
    assert proc.lifeline_kind() == kind
    proc.lifeline(on_close=lambda: None)
    assert proc.lifeline_kind() == kind
    monkeypatch.setenv(proc.LIFELINE_ENV, "9")  # set again after: the first read stands
    assert proc.lifeline_kind() == kind


def test_a_second_lifeline_holds_what_the_first_read(unread, monkeypatch):
    monkeypatch.setenv(proc.LIFELINE_ENV, "5")
    proc.lifeline(on_close=lambda: None)
    proc.lifeline(on_close=lambda: None)
    assert unread == [5, 5]


@pytest.mark.parametrize("value", [None, "", "0"], ids=["unset", "empty", "zero"])
def test_lifeline_holds_stdin_when_the_variable_is_unset_empty_or_0(unread, monkeypatch,
                                                                     value):
    if value is not None:
        monkeypatch.setenv(proc.LIFELINE_ENV, value)
    proc.lifeline(on_close=lambda: None)
    assert unread == [0]


@pytest.mark.parametrize("value", ["-1", "x", " 3"])
def test_lifeline_refuses_a_malformed_value_and_kind_stays_none(unread, monkeypatch, value):
    monkeypatch.setenv(proc.LIFELINE_ENV, value)
    with pytest.raises(ValueError, match=proc.LIFELINE_ENV):
        proc.lifeline(on_close=lambda: None)
    assert unread == []
    assert proc.LIFELINE_ENV not in os.environ
    assert proc.lifeline_kind() is None


class _FakeMsvcrt:
    def __init__(self):
        self.opened = []

    def open_osfhandle(self, handle, flags):
        self.opened.append(handle)
        return 99


@pytest.mark.parametrize("value, opened, held", [("0", [], [0]), ("1234", [1234], [99])],
                         ids=["stdin", "handle"])
def test_on_windows_0_is_stdin_and_never_a_handle(unread, monkeypatch, value, opened, held):
    msvcrt = _FakeMsvcrt()
    monkeypatch.setitem(sys.modules, "msvcrt", msvcrt)
    monkeypatch.setattr(proc, "_WINDOWS", True)
    monkeypatch.setenv(proc.LIFELINE_ENV, value)
    proc.lifeline(on_close=lambda: None)
    assert msvcrt.opened == opened
    assert unread == held


def test_on_windows_every_spelling_of_the_variable_is_taken_out(monkeypatch):
    monkeypatch.setattr(proc, "_WINDOWS", True)
    given = {"PATH": "p", "moxi_lifeline_fd": "7", "Moxi_Lifeline_Fd": "8",
             proc.LIFELINE_ENV: "9"}
    assert proc._lifeline_env(given, None) == {"PATH": "p"}
    assert proc._lifeline_env(given, "0") == {"PATH": "p", proc.LIFELINE_ENV: "0"}
    assert given["moxi_lifeline_fd"] == "7"  # the caller's mapping is left alone


AFTER = """
    import os, subprocess, sys, threading
    from moxi import proc
    value = os.environ.get(proc.LIFELINE_ENV)
    before = proc.lifeline_kind()
    closed = threading.Event()
    proc.lifeline(on_close=closed.set)
    after = proc.lifeline_kind()
    plain = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ.get(%r))" % proc.LIFELINE_ENV],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True).stdout.strip()
    print(value != "0", before, after, plain, flush=True)
    print("closed", closed.wait(30), flush=True)
"""


@pytest.mark.parametrize("kind", ["stdin", "fd"])
def test_in_a_child_lifeline_keeps_the_kind_and_passes_no_variable_on(tmp_path, kind):
    child = proc.spawn([PY, script(tmp_path, "after.py", AFTER)], lifeline=kind,
                       stdout=subprocess.PIPE)
    named, before, after, plain = child.stdout.readline().decode().split()
    assert named == str(kind == "fd")  # stdin's value is 0, fd's another number
    assert (before, after, plain) == (kind, kind, "None")
    time.sleep(0.2)
    assert child.poll() is None
    child.lifeline.close()
    assert child.stdout.readline() == b"closed True\n"
    assert child.wait(timeout=30) == 0
    child.stdout.close()


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


# -- status --------------------------------------------------------------------------------

@pytest.fixture
def tables(monkeypatch):
    """Fresh tables of kept detached children and unread codes, so what other tests left
    in them neither answers here nor counts toward the bound."""
    from collections import deque

    monkeypatch.setattr(proc, "_detached", [])
    monkeypatch.setattr(proc, "_codes", deque())


def _ended_unreaped(pid: int) -> bool:
    """Whether `pid` has ended and is still waiting to be reaped (Linux: a zombie)."""
    if not sys.platform.startswith("linux"):
        import psutil

        try:
            return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
        except psutil.Error:
            return False
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rpartition(")")[2].split()[0] == "Z"
    except OSError:
        return False


def _until_ended(pid: int, timeout: float = 30.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = proc.status(pid)
        if code is not None:
            return code
        time.sleep(0.02)
    raise AssertionError(f"detached child {pid} did not end in {timeout} s")


def test_status_reads_a_running_child_as_none_then_its_code_once(tables, tmp_path):
    gate = tmp_path / "go"
    pid = proc.spawn([PY, "-c", textwrap.dedent(f"""
        import os, sys, time
        while not os.path.exists({str(gate)!r}):
            time.sleep(0.02)
        sys.exit(3)
        """)], detached=True)
    assert proc.status(pid) is None
    assert proc.status(pid) is None  # a running child is answered as often as asked
    gate.touch()
    assert _until_ended(pid) == 3
    with pytest.raises(LookupError):
        proc.status(pid)


@POSIX
def test_status_of_a_killed_child_is_minus_its_signal(tables):
    import signal

    pid = proc.spawn([PY, "-c", "import time; time.sleep(60)"], detached=True)
    assert proc.status(pid) is None
    os.kill(pid, signal.SIGKILL)
    assert _until_ended(pid) == -signal.SIGKILL


@pytest.mark.parametrize("pid", [0, -1, True, "1", None])
def test_status_of_what_is_not_a_pid_raises_lookup_error(tables, pid):
    with pytest.raises(LookupError):
        proc.status(pid)


def test_status_of_a_process_not_started_detached_raises_lookup_error(tables):
    with pytest.raises(LookupError):
        proc.status(os.getpid())
    child = subprocess.Popen([PY, "-c", "import sys; sys.stdin.read()"],
                             stdin=subprocess.PIPE)
    try:
        with pytest.raises(LookupError):
            proc.status(child.pid)
    finally:
        child.stdin.close()
        child.wait(timeout=10)


@POSIX
def test_an_ended_childs_code_is_kept_across_the_next_spawn_that_reaps_it(tables):
    first = proc.spawn([PY, "-c", "import sys; sys.exit(3)"], detached=True)
    assert wait_for(lambda: _ended_unreaped(first), 30)
    second = proc.spawn([PY, "-c", "import time; time.sleep(60)"], detached=True)
    try:
        # Reaped by the spawn: no zombie is left, whatever has the pid now is not ours.
        assert not _ended_unreaped(first)
        import psutil

        assert not psutil.pid_exists(first) or psutil.Process(first).ppid() != os.getpid()
        assert proc.status(first) == 3
        with pytest.raises(LookupError):
            proc.status(first)
    finally:
        os.kill(second, 9)
        _until_ended(second)


@POSIX
def test_past_the_bound_the_oldest_unread_code_is_dropped(tables, monkeypatch):
    monkeypatch.setattr(proc, "CODES_KEPT", 2)
    pids = []
    for code in (1, 2, 3):
        pids.append(proc.spawn([PY, "-c", f"import sys; sys.exit({code})"], detached=True))
        assert wait_for(lambda: _ended_unreaped(pids[-1]), 30)
    proc.spawn([PY, "-c", "pass"], detached=True)  # reaps the three into a table of two
    with pytest.raises(LookupError):
        proc.status(pids[0])
    assert [proc.status(pids[1]), proc.status(pids[2])] == [2, 3]


@POSIX
def test_two_threads_reading_one_ended_child_get_its_code_once(tables):
    import threading

    pid = proc.spawn([PY, "-c", "import sys; sys.exit(3)"], detached=True)
    assert wait_for(lambda: _ended_unreaped(pid), 30)
    start = threading.Barrier(2)
    got: list = []

    def read() -> None:
        start.wait()
        try:
            got.append(proc.status(pid))
        except LookupError:
            got.append("LookupError")

    threads = [threading.Thread(target=read) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert sorted(got, key=str) == [3, "LookupError"]


class _FakePopen:
    """A kept detached child whose `poll` answers in turn from `answers`."""

    def __init__(self, pid: int, answers: list):
        self.pid = pid
        self.answers = answers

    def poll(self):
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def test_a_reused_pids_children_are_answered_oldest_first(tables):
    # An earlier child with pid 4321 ended with 5 and was reaped, its code unread; a
    # later detached child got the same pid and is running, then ends with 7.
    with proc._detached_guard:
        proc._keep_code(4321, 5)
    proc._detached.append(_FakePopen(4321, [None, 7]))
    assert proc.status(4321) == 5
    assert proc.status(4321) is None
    assert proc.status(4321) == 7
    with pytest.raises(LookupError):
        proc.status(4321)


def test_a_reused_pids_oldest_kept_popen_is_polled_first(tables):
    # With SIGCHLD ignored a pid can come back while its earlier Popen is still kept.
    proc._detached.extend([_FakePopen(4321, [0]), _FakePopen(4321, [None])])
    assert proc.status(4321) == 0
    assert proc.status(4321) is None


def test_a_dropped_code_is_answered_by_the_pids_later_child(tables, monkeypatch):
    monkeypatch.setattr(proc, "CODES_KEPT", 1)
    with proc._detached_guard:
        proc._keep_code(4321, 5)
        proc._keep_code(4322, 6)  # drops 4321's code
    proc._detached.append(_FakePopen(4321, [None]))
    assert proc.status(4321) is None
    assert proc.status(4322) == 6


# -- identity ------------------------------------------------------------------------------

def test_identity_names_this_process_by_its_start_time():
    import psutil

    me = proc.identity(os.getpid())
    assert me == proc.ProcId(os.getpid(), psutil.Process().create_time())
    assert proc.alive(me)


def test_a_different_start_time_is_not_alive():
    me = proc.identity(os.getpid())
    assert not proc.alive(proc.ProcId(me.pid, me.started - 10))


@pytest.mark.parametrize("shift, expected", [(0.5, True), (-0.5, True), (1.5, False),
                                             (-1.5, False)])
def test_a_start_time_moved_by_a_clock_adjustment_is_still_alive(monkeypatch, shift,
                                                                  expected):
    """The start time psutil reads moves when the wall clock is adjusted; under a second
    of difference is still the same process."""
    import psutil

    me = proc.identity(os.getpid())
    monkeypatch.setattr(psutil.Process, "create_time", lambda self: me.started + shift)
    assert proc.alive(me) is expected


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


# -- environ -------------------------------------------------------------------------------

LINUX = pytest.mark.skipif(not sys.platform.startswith("linux"),
                           reason="reads /proc, which only Linux has")
MACOS = pytest.mark.skipif(sys.platform != "darwin",
                           reason="sysctl KERN_PROCARGS2 is macOS's; owed to a Mac")
WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="Windows only; owed to Windows")


def _child_with(env: dict[str, str], argv0: str = PY) -> subprocess.Popen:
    """A child started with `env` as its whole environment, which waits on its stdin. It
    has said it is running, so its environment is its own and not this process's."""
    child = subprocess.Popen([argv0, "-c", "import sys; print('up', flush=True); sys.stdin.read()"],
                             executable=PY, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             env=env)
    assert child.stdout.readline() == b"up\n"
    return child


def _end(child: subprocess.Popen) -> None:
    child.stdin.close()
    child.wait(timeout=10)
    child.stdout.close()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no environment reader")
def test_environ_reads_a_child_as_it_was_started():
    env = {"PATH": os.environ.get("PATH", ""), "MOXI_TEST_A": "1",
           "MOXI_TEST_B": "x=y", "MOXI_TEST_C": "", "MOXI_TEST_D": "caf\u00e9"}
    if sys.platform == "darwin":
        env["HOME"] = os.environ.get("HOME", "/")
    child = _child_with(env)
    try:
        read = proc.environ(child.pid)
    finally:
        _end(child)
    assert read is not None
    for key in ("MOXI_TEST_A", "MOXI_TEST_B", "MOXI_TEST_C", "MOXI_TEST_D"):
        assert read[key] == env[key]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no environment reader")
def test_environ_of_this_process_reads_what_it_started_with():
    read = proc.environ(os.getpid())
    assert read is not None and read.get("PATH") == os.environ.get("PATH")


def test_environ_of_a_gone_process_is_none():
    child = subprocess.Popen([PY, "-c", "pass"])
    child.wait()
    assert proc.environ(child.pid) is None


@LINUX
def test_environ_of_a_zombie_is_none():
    # This kernel may refuse the read or return nothing; either way the answer is None.
    child = subprocess.Popen([PY, "-c", "pass"])
    try:
        deadline = time.monotonic() + 10
        while _state(child.pid) != "Z" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert _state(child.pid) == "Z"
        assert proc.environ(child.pid) is None
    finally:
        child.wait()


def _state(pid: int) -> str:
    with open(f"/proc/{pid}/stat") as f:
        return f.read().rpartition(")")[2].split()[0]


@LINUX
def test_the_linux_reader_reads_an_empty_environment_of_a_running_process_as_empty():
    child = _child_with({})
    try:
        assert proc.environ(child.pid) == {}
    finally:
        _end(child)


@pytest.mark.parametrize("pid", [0, -1, True])
def test_environ_of_no_pid_is_none(pid):
    assert proc.environ(pid) is None


@LINUX
def test_environ_of_another_users_process_is_none():
    if os.geteuid() == 0:
        pytest.skip("root reads every process")
    if not os.path.exists("/proc/1/environ"):
        pytest.skip("no pid 1 under /proc")
    assert proc.environ(1) is None


@LINUX
def test_the_linux_reader_splits_entries_and_skips_what_is_not_one(tmp_path):
    fake = tmp_path / "12"
    fake.mkdir()
    (fake / "environ").write_bytes(b"A=1\0B=x=y\0junk\0C=\0=nokey\0D=\xff\0")
    assert proc._linux_environ(12, str(tmp_path)) == {
        "A": "1", "B": "x=y", "C": "", "D": "\ufffd"}
    assert proc._linux_environ(13, str(tmp_path)) is None


@LINUX
@pytest.mark.parametrize("stat, read", [
    (b"12 (a) b) Z 1 12", None),            # a zombie: some kernels read it as empty
    (b"12 (a) b) X 1 12", None),            # dead
    (b"12 (a) Z) S 1 12", {}),              # asleep, its name holding ") Z"
    (None, None),                           # gone before its state was read
])
def test_the_linux_reader_reads_an_empty_environment_by_the_process_state(tmp_path, stat,
                                                                          read):
    fake = tmp_path / "12"
    fake.mkdir()
    (fake / "environ").write_bytes(b"")
    if stat is not None:
        (fake / "stat").write_bytes(stat)
    assert proc._linux_environ(12, str(tmp_path)) == read


def _procargs(argc: int, path: bytes, argv: list[bytes], env: list[bytes],
              apple: list[bytes], pad: int | None = None) -> bytes:
    """A `KERN_PROCARGS2` answer; the path is padded to 8 bytes unless `pad` says how."""
    if pad is None:
        pad = -(len(path) + 1) % 8
    out = argc.to_bytes(4, sys.byteorder, signed=True) + path + b"\0" * (1 + pad)
    out += b"".join(a + b"\0" for a in argv)
    out += b"".join(e + b"\0" for e in env) + b"\0"
    out += b"".join(a + b"\0" for a in apple)
    return out


def test_a_procargs2_answer_gives_the_environment_and_not_the_system_strings():
    raw = _procargs(2, b"/usr/bin/python3", [b"python3", b"-c"],
                    [b"A=1", b"B=x=y", b"junk", b"C="],
                    [b"executable_path=/usr/bin/python3", b"ptr_munge="])
    assert proc._procargs2(raw) == {"A": "1", "B": "x=y", "C": ""}


def test_a_procargs2_answer_with_no_environment_is_empty():
    assert proc._procargs2(_procargs(1, b"/bin/x", [b"x"], [], [b"apple=1"])) == {}
    raw = 1 .to_bytes(4, sys.byteorder) + b"/bin/x\0\0x\0"
    assert proc._procargs2(raw) == {}


@pytest.mark.parametrize("path", [b"/bin/x", b"/usr/bin/python"], ids=["padded", "unpadded"])
def test_a_procargs2_answer_keeps_an_empty_first_argument(path):
    # "/usr/bin/python" and its NUL fill 16 bytes; "/bin/x" and its NUL need one more.
    raw = _procargs(2, path, [b"", b"-c"], [b"A=1"], [b"apple=1"])
    assert proc._procargs2(raw) == {"A": "1"}
    raw = _procargs(1, b"/usr/bin/python3", [b""], [b"A=1"], [b"apple=1"])
    assert proc._procargs2(raw) == {"A": "1"}


def test_a_procargs2_answer_padded_short_of_a_pointer_skips_every_nul():
    raw = _procargs(1, b"/usr/bin/python3", [b"python3"], [b"A=1"], [], pad=3)
    assert proc._procargs2(raw) == {"A": "1"}


def test_a_procargs2_answer_cut_short_is_none():
    assert proc._procargs2(b"\x01") is None
    assert proc._procargs2(2 .to_bytes(4, sys.byteorder) + b"/bin/x") is None
    assert proc._procargs2(3 .to_bytes(4, sys.byteorder) + b"/bin/x\0\0a\0b") is None
    assert proc._procargs2((-1).to_bytes(4, sys.byteorder, signed=True) + b"/x\0") is None


@MACOS
def test_environ_on_macos_reads_a_same_user_process_through_the_kernel():
    child = _child_with({"PATH": "/usr/bin:/bin", "MOXI_TEST_MAC": "here"})
    try:
        read = proc._mac_environ(child.pid)
    finally:
        _end(child)
    assert read is not None and read["MOXI_TEST_MAC"] == "here"


@MACOS
def test_environ_on_macos_reads_a_process_whose_first_argument_is_empty():
    child = _child_with({"PATH": "/usr/bin:/bin", "MOXI_TEST_MAC": "here"}, argv0="")
    try:
        read = proc._mac_environ(child.pid)
    finally:
        _end(child)
    # Read one argument short, PATH (the first entry) would be taken for an argument.
    assert read is not None
    assert read.get("PATH") == "/usr/bin:/bin" and read.get("MOXI_TEST_MAC") == "here"
    assert "executable_path" not in read  # the system's own strings are not the environment


@MACOS
def test_environ_on_macos_of_another_users_process_is_none():
    if os.geteuid() == 0:
        pytest.skip("root reads every process")
    assert proc.environ(1) is None  # launchd, root's


@WINDOWS
def test_environ_on_windows_is_none():
    assert proc.environ(os.getpid()) is None


# -- parent --------------------------------------------------------------------------------

def test_parent_of_this_process_is_its_parent():
    assert proc.parent(os.getpid()) == os.getppid()


def test_parent_of_a_child_is_this_process():
    child = subprocess.Popen([PY, "-c", "import sys; sys.stdin.read()"],
                             stdin=subprocess.PIPE)
    try:
        assert proc.parent(child.pid) == os.getpid()
    finally:
        child.stdin.close()
        child.wait(timeout=10)


@pytest.mark.parametrize("pid", [0, -1, True, 2.0, "1"])
def test_parent_of_what_is_not_a_pid_is_none(pid):
    assert proc.parent(pid) is None


def test_parent_of_a_gone_process_is_none():
    child = subprocess.Popen([PY, "-c", "pass"])
    child.wait()
    assert proc.parent(child.pid) is None


@POSIX
def test_parent_of_pid_1_is_zero():
    assert proc.parent(1) == 0


def _stat(root: Path, pid: int, text: bytes) -> None:
    (root / str(pid)).mkdir(parents=True)
    (root / str(pid) / "stat").write_bytes(text)


@pytest.mark.parametrize("name", [b"python3", b"a b", b"x) (y", b") S 99 (", b"(", b""])
def test_the_linux_reader_counts_fields_from_the_last_parenthesis(tmp_path, name):
    _stat(tmp_path, 42, b"42 (" + name + b") S 7 42 42 0 -1 4194560 100 0 0 0\n")
    assert proc._linux_parent(42, str(tmp_path)) == 7


def test_the_linux_reader_of_a_short_or_unreadable_stat_is_none(tmp_path):
    _stat(tmp_path, 42, b"42 (x) S")
    _stat(tmp_path, 43, b"43 (x) S q 1")
    assert proc._linux_parent(42, str(tmp_path)) is None
    assert proc._linux_parent(43, str(tmp_path)) is None
    assert proc._linux_parent(44, str(tmp_path)) is None


def _kinfo(pid: int, ppid: int, size: int = 648) -> bytes:
    raw = bytearray(size)
    for at, value in ((40, pid), (560, ppid)):
        if at + 4 <= size:
            raw[at:at + 4] = value.to_bytes(4, sys.byteorder, signed=True)
    return bytes(raw)


def test_a_kinfo_proc_answer_gives_the_parent_at_its_offset():
    assert proc._kinfo_parent(_kinfo(42, 7), 42) == 7
    assert proc._kinfo_parent(_kinfo(1, 0), 1) == 0


@pytest.mark.parametrize("raw", [b"", _kinfo(42, 7, 492), _kinfo(42, 7, 656),
                                 _kinfo(43, 7)], ids=["gone", "short", "long", "other pid"])
def test_a_kinfo_proc_answer_of_another_size_or_pid_is_none(raw):
    assert proc._kinfo_parent(raw, 42) is None


@MACOS
def test_parent_on_macos_matches_ps():
    child = subprocess.Popen([PY, "-c", "import sys; sys.stdin.read()"],
                             stdin=subprocess.PIPE)
    try:
        for pid in (os.getpid(), child.pid, 1):
            ps = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True,
                                text=True, check=True)
            assert proc.parent(pid) == int(ps.stdout)
        assert proc.parent(child.pid) == os.getpid()
    finally:
        child.stdin.close()
        child.wait(timeout=10)


@MACOS
def test_parent_on_macos_reads_another_users_process():
    ps = subprocess.run(["ps", "-A", "-o", "pid=,uid=,ppid="], capture_output=True,
                        text=True, check=True)
    for line in ps.stdout.splitlines():
        pid, uid, ppid = map(int, line.split())
        if uid != os.getuid() and pid != 1:
            assert proc.parent(pid) == ppid
            return
    pytest.skip("no other user's process to read")


@WINDOWS
def test_parent_on_windows_names_a_dead_parent_whose_pid_identity_may_not_match(tmp_path):
    # A middle process starts a grandchild and exits: Windows does not reparent, so the
    # grandchild still names the middle's pid, and whatever holds that pid now (if any)
    # started after the grandchild.
    out = tmp_path / "pid"
    middle = subprocess.run([PY, "-c", textwrap.dedent(f"""
        import subprocess, sys
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        open({str(out)!r}, "w").write(str(child.pid))
        """)], timeout=30)
    assert middle.returncode == 0
    grandchild = int(out.read_text())
    try:
        named = proc.parent(grandchild)
        assert named is not None and named != os.getpid()
        holder = proc.identity(named)
        mine = proc.identity(grandchild)
        assert holder is None or holder.started > mine.started
    finally:
        subprocess.run(["taskkill", "/F", "/PID", str(grandchild)], capture_output=True)


# -- import weight -------------------------------------------------------------------------

def test_importing_the_module_loads_no_psutil_or_ctypes():
    code = "import sys, moxi.proc; print('psutil' in sys.modules, 'ctypes' in sys.modules)"
    done = subprocess.run([PY, "-I", "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.split() == ["False", "False"]
