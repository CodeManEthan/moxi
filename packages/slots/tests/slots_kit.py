"""Helpers the slots tests share: a recorded setting, a real tmux, a server started by
hand and its teardown."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from moxi import config, paths
from moxi_slots import places, tmux


def write_tool(path: Path, body: str) -> Path:
    """An executable shell script at `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def record(key: str, value, by: str = "test") -> None:
    config.set(places.MODULE, key, value, by=by)


def real_tmux() -> tuple[str | None, str]:
    """tmux on PATH at or above the floor, or None with why not."""
    found = shutil.which("tmux")
    if found is None:
        return None, "tmux is not on PATH"
    seen = tmux.judge(subprocess.run([found, "-V"], capture_output=True, text=True,
                                     timeout=5).stdout)
    if not seen.ok:
        return None, f"tmux on PATH is {seen.text}, below {tmux.FLOOR_TEXT}"
    return found, seen.text


@dataclass
class Server:
    tmux: str
    socket: str
    proc: subprocess.Popen

    @property
    def pid(self) -> int:
        return self.proc.pid


def start_server(env: dict | None = None) -> subprocess.Popen:
    script = str(paths.script("moxi-slots-server"))
    return subprocess.Popen([script], env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            start_new_session=True)


def wait_answer(timeout: float = 5.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (pid := tmux.ping(timeout=1.0)) is not None:
            return pid
        time.sleep(0.02)
    raise AssertionError("the server did not answer")


def kill(path: str, socket: str, proc: subprocess.Popen | None) -> None:
    subprocess.run([path, "-S", socket, "kill-server"], capture_output=True, timeout=5,
                   env={k: v for k, v in os.environ.items() if k not in tmux.DROPPED})
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
    if os.path.exists(socket):
        os.remove(socket)
