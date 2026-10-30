"""The trampoline, `moxi-slots-exec`, run as the pane would run it but without tmux: its
environment rule, its verdict file, and the launches it refuses to run."""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import time

import pytest

from moxi import paths
from moxi_slots import launchfile, trampoline
from moxi_slots.trampoline import EXEC, NOT_RUN, TAKEN, TERM_NAMES, child_env

pytestmark = pytest.mark.touches("packages/slots/src/moxi_slots/trampoline.py")

PANE = {"TERM": "tmux-256color", "TMUX": "/sock,1,0", "TMUX_PANE": "%4",
        "TERM_PROGRAM": "tmux", "SERVER_SECRET": "from-the-server", "HOME": "/home/x"}


def run(path, *, env=None, timeout=10) -> subprocess.CompletedProcess:
    script = str(paths.script("moxi-slots-exec"))
    return subprocess.run([script, str(path)], env=PANE if env is None else env,
                          capture_output=True, text=True, timeout=timeout)


def content(path) -> str:
    return open(path).read().strip()


def env_lines(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def test_the_child_gets_the_launch_env_and_the_panes_terminal_names_only():
    launch = {"A": "1", "TMUX_PANE": "%99", "TERM": "dumb", "COLORTERM": "truecolor"}
    got = child_env(launch, PANE)
    assert got == {"A": "1", "TERM": "tmux-256color", "TMUX": "/sock,1,0",
                   "TMUX_PANE": "%4", "TERM_PROGRAM": "tmux"}
    assert "COLORTERM" not in got  # a terminal name tmux did not set is dropped
    assert set(TERM_NAMES) == {"TERM", "COLORTERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION",
                               "TMUX", "TMUX_PANE"}


def test_a_launch_runs_with_exactly_its_env_and_writes_exec(tmp_path):
    env_bin = "/usr/bin/env" if os.path.exists("/usr/bin/env") else "/bin/env"
    path = launchfile.write([env_bin], {"A": "1", "TMUX_PANE": "%99"}, str(tmp_path))
    done = run(path)
    assert done.returncode == 0, done.stderr
    assert content(path) == EXEC
    assert env_lines(done.stdout) == {"A": "1", "TERM": "tmux-256color",
                                      "TMUX": "/sock,1,0", "TMUX_PANE": "%4",
                                      "TERM_PROGRAM": "tmux"}


def test_argv0_is_found_on_the_launch_path_and_cwd_is_entered(tmp_path):
    path = launchfile.write(["sh", "-c", 'pwd; echo "$0"', "zero"],
                            {"PATH": os.environ["PATH"]}, str(tmp_path))
    done = run(path)
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == [str(tmp_path), "zero"]


def test_the_launch_leaves_the_file_as_soon_as_it_is_taken(tmp_path):
    path = launchfile.write(["sleep", "5"], {"PATH": os.environ["PATH"],
                                             "SECRET": "s3cret"}, str(tmp_path))
    script = str(paths.script("moxi-slots-exec"))
    proc = subprocess.Popen([script, str(path)], env=PANE)
    try:
        deadline = time.monotonic() + 5
        while content(path).startswith("{") and time.monotonic() < deadline:
            time.sleep(0.01)
        assert "s3cret" not in open(path).read()
        assert content(path) in (TAKEN, EXEC)
    finally:
        proc.kill()
        proc.wait()


def test_a_missing_cwd_fails_and_runs_nothing(tmp_path):
    marker = tmp_path / "ran"
    path = launchfile.write(["touch", str(marker)], {"PATH": os.environ["PATH"]},
                            str(tmp_path / "gone"))
    done = run(path)
    assert done.returncode == NOT_RUN
    assert content(path).startswith(f"failed: cwd: {tmp_path / 'gone'}: ")
    assert "the launch did not run" in done.stderr
    assert not marker.exists()


def test_an_unknown_program_fails(tmp_path):
    path = launchfile.write(["no-such-program-here"], {"PATH": str(tmp_path)},
                            str(tmp_path))
    done = run(path)
    assert done.returncode == NOT_RUN
    assert content(path) == ("failed: argv[0]: no-such-program-here is not found on the "
                             "launch's PATH")


def test_a_program_named_by_path_must_be_executable(tmp_path):
    plain = tmp_path / "plain"
    plain.write_text("echo hi\n")
    path = launchfile.write([str(plain)], {}, str(tmp_path))
    assert run(path).returncode == NOT_RUN
    assert content(path) == f"failed: argv[0]: {plain} is not an executable file"


def test_an_execve_that_raises_is_failed_not_exec(tmp_path):
    junk = tmp_path / "junk"
    junk.write_bytes(b"\x00\x01\x02 not a program")
    junk.chmod(0o755)
    path = launchfile.write([str(junk)], {}, str(tmp_path))
    done = run(path)
    assert done.returncode == NOT_RUN
    assert content(path).startswith("failed: execve: ")


def test_a_launch_file_already_taken_runs_nothing(tmp_path):
    marker = tmp_path / "ran"
    path = launchfile.write(["touch", str(marker)], {"PATH": os.environ["PATH"]},
                            str(tmp_path))
    with open(path, "w") as f:
        f.write("taken\n")
    assert run(path).returncode == NOT_RUN
    assert not marker.exists()


def test_a_file_removed_while_the_trampoline_waits_for_its_lock_runs_nothing(tmp_path):
    """The start's give-up: it holds the lock, finds the launch still there and removes
    the file; the trampoline then gets the lock, finds the file unlinked and stops."""
    marker = tmp_path / "ran"
    path = launchfile.write(["touch", str(marker)], {"PATH": os.environ["PATH"]},
                            str(tmp_path))
    fd = os.open(path, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    script = str(paths.script("moxi-slots-exec"))
    proc = subprocess.Popen([script, str(path)], env=PANE, stderr=subprocess.PIPE,
                            text=True)
    try:
        time.sleep(0.5)  # the trampoline has opened the file and waits for the lock
        assert proc.poll() is None
        os.remove(path)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    _, err = proc.communicate(timeout=10)
    assert proc.returncode == NOT_RUN
    assert "gave up on this launch" in err or "launch file is gone" in err
    assert not marker.exists()


def test_the_child_does_not_inherit_ignored_pipe_signals(tmp_path):
    path = launchfile.write(["sh", "-c", "yes | head -n 1"], {"PATH": os.environ["PATH"]},
                            str(tmp_path))
    done = run(path)
    assert done.returncode == 0
    assert done.stdout == "y\n"
    assert done.stderr == ""


def test_a_bad_call_is_a_usage_error():
    assert trampoline.main([]) == 2
    assert trampoline.main(["a", "b"]) == 2


def test_parse_refuses_what_is_not_a_launch():
    with pytest.raises(ValueError):
        trampoline.parse(b'{"argv": [], "env": {}, "cwd": "/"}')
    with pytest.raises(ValueError):
        trampoline.parse(b'{"argv": ["x"], "env": {"A": 1}, "cwd": "/"}')
    assert trampoline.parse(b'{"argv": ["x"], "env": {}, "cwd": "/"}') == (["x"], {}, "/")


def test_the_trampoline_imports_nothing_of_moxi():
    code = ("import sys, moxi_slots.trampoline\n"
            "print(sorted(m for m in sys.modules if m == 'moxi' or m.startswith('moxi.')))")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=30)
    assert done.stdout.strip() == "[]", done.stderr
