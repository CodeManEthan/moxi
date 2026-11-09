"""Helpers statusline's tests share: Claude's made-up input, a folder of made-up modules,
statusline's config file, and one render in a child process.

A render that installs the audit hook runs in a child process, since the hook can't be
removed from the test's own.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from moxi import paths

#: Claude's status line input for the tests: every field the line reads, made up.
INPUT = {
    "session_id": "abcdef123456",
    "transcript_path": "/made/up/projects/abcdef123456.jsonl",
    "cwd": "/made/up/cwd",
    "model": {"id": "claude-made-up", "display_name": "Claude Madeup 9"},
    "workspace": {"current_dir": "/made/up/work/tree"},
    "effort": {"level": "high"},
    "context_window": {"used_percentage": 42},
    "cost": {"total_cost_usd": 1.239, "total_lines_added": 12, "total_lines_removed": 3},
}


class Site:
    """A folder of made-up module distributions."""

    def __init__(self, root: Path):
        self.root = root

    def write(self, relpath: str, text: str) -> Path:
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
        return path

    def module(self, name: str, segment: str | None = None, *,
               segment_name: str = "segment", **files: str) -> str:
        """Module `name`: package `moxi_<name>`, its announcement, and when `segment` is
        given, `moxi_<name>/<segment_name>.py` holding it, announced as its `SEGMENT`.
        Each keyword in `files` is another file of the package, by its stem."""
        package = f"moxi_{name}"
        dotted = f"{package}.{segment_name}" if segment is not None else None
        self.write(f"{package}/__init__.py", "")
        self.write(f"{package}/module.py",
                   f"SUMMARY = 'A made-up module.'\nSEGMENT = {dotted!r}\n")
        if segment is not None:
            self.write(f"{package}/{segment_name}.py", segment)
        for stem, text in files.items():
            self.write(f"{package}/{stem}.py", text)
        info = f"moxi_{name}-0.1.0.dist-info"
        self.write(f"{info}/METADATA",
                   f"Metadata-Version: 2.1\nName: moxi-{name}\nVersion: 0.1.0\n")
        self.write(f"{info}/entry_points.txt",
                   f"[moxi.modules]\n{name} = {package}.module\n")
        return package


#: A segment module that answers through `from_input`, coloured red.
FROM_INPUT = """
    class Seg:
        def __init__(self, text):
            self.text = text
        def coloured(self):
            return "\\033[31m" + self.text + "\\033[0m"

    def from_input(payload, now):
        return Seg("in:" + payload["model"]["display_name"].split()[-1])

    def segment(session_id, now=None):
        return Seg("never")
"""

#: A segment module that answers through `segment(session_id, now)`.
BY_SESSION = """
    class Seg:
        def __init__(self, text):
            self.text = text
        def coloured(self):
            return "\\033[32m" + self.text + "\\033[0m"

    def segment(session_id, now=None):
        return Seg("sid:" + session_id[:3])
"""


def text_segment(text: str) -> str:
    """A segment module whose `from_input` shows `text`."""
    return FROM_INPUT.replace('"in:" + payload["model"]["display_name"].split()[-1]',
                              repr(text))


def config_file() -> Path:
    """statusline's config file, in the test's own home."""
    return paths.Layout("statusline").config_file()


def write_config(text: str) -> Path:
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))
    return path


def render(*args: str, stdin: object = INPUT, site: Site | None = None,
           env: dict | None = None) -> subprocess.CompletedProcess:
    """One render in a child process: `stdin` as JSON (bytes and str as they are)."""
    data = (stdin if isinstance(stdin, bytes) else stdin.encode() if isinstance(stdin, str)
            else json.dumps(stdin).encode())
    environ = dict(os.environ if env is None else env)
    if site is not None:
        environ["PYTHONPATH"] = os.pathsep.join(
            [str(site.root), *filter(None, [environ.get("PYTHONPATH")])])
    return subprocess.run([sys.executable, "-m", "moxi_statusline.render", *args],
                          input=data, capture_output=True, env=environ, timeout=60,
                          check=False)


def out(done: subprocess.CompletedProcess) -> list[str]:
    """The rows a render printed."""
    assert done.returncode == 0, done.stderr
    return done.stdout.decode().rstrip("\n").split("\n")
