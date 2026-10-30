"""A fake harness on PATH for the launch tests, and a launch run in a process of its own.

Each fake (`claude`, `codex`, and `moxi-comms-fake` standing in for comms' wrapper) is a
Python script run by its `#!` line. It records what it was started with (its argv,
directory, environment, pid and open files) as one JSON file per run in the directory
`LAUNCHER_FAKE_HARNESS` names, prints one line and exits with `LAUNCHER_FAKE_EXIT`
(default 0).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from moxi_dev.testing import wait_for

TESTS = Path(__file__).resolve().parent
NAMES = ("claude", "codex", "moxi-comms-fake")

FAKE_HARNESS = """#!{python}
import json, os, sys
fds = {{}}
if os.path.isdir("/proc/self/fd"):
    for fd in os.listdir("/proc/self/fd"):
        try:
            fds[fd] = os.readlink("/proc/self/fd/" + fd)
        except OSError:
            pass
record = {{"argv": sys.argv, "cwd": os.getcwd(), "env": dict(os.environ),
          "pid": os.getpid(), "fds": fds}}
out = os.environ["LAUNCHER_FAKE_HARNESS"]
tmp = os.path.join(out, ".%d.tmp" % os.getpid())
with open(tmp, "w") as f:
    json.dump(record, f)
os.replace(tmp, os.path.join(out, "%d.json" % os.getpid()))
print("the fake harness ran", flush=True)
sys.exit(int(os.environ.get("LAUNCHER_FAKE_EXIT", "0")))
"""


class Harness:
    """The fake `claude`, `codex` and comms wrapper on PATH, and what each run recorded."""

    def __init__(self, root: Path):
        self.bin = root / "bin"
        self.out = root / "runs-seen"
        self.bin.mkdir()
        self.out.mkdir()
        for name in NAMES:
            path = self.bin / name
            path.write_text(FAKE_HARNESS.format(python=sys.executable))
            path.chmod(0o755)

    @classmethod
    def on_path(cls, root: Path, monkeypatch) -> "Harness":
        """The fakes, first on PATH, recording under `root`."""
        rig = cls(root)
        monkeypatch.setenv("PATH", f"{rig.bin}{os.pathsep}{os.environ.get('PATH', '')}")
        monkeypatch.setenv("LAUNCHER_FAKE_HARNESS", str(rig.out))
        return rig

    def runs(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted(self.out.glob("*.json"))]

    def one(self, timeout: float = 30) -> dict:
        wait_for(lambda: len(self.runs()) >= 1, timeout=timeout)
        [run] = self.runs()
        return run


def run_child(tmp_path: Path, req: dict, *, env: dict | None = None,
              moxi: str | None = None) -> tuple[int, dict]:
    """Run one launch through `launcher_child`, in a process of its own: its exit code
    and what it saw (the preview's argv, and the result or refusal when it returned)."""
    spec = tmp_path / f"spec-{req.get('request_id')}.json"
    out = tmp_path / f"seen-{req.get('request_id')}.json"
    spec.write_text(json.dumps({"request": req, "out": str(out), "moxi": moxi}))
    child_env = {**os.environ, "PYTHONPATH": str(TESTS), **(env or {})}
    done = subprocess.run([sys.executable, "-m", "launcher_child", str(spec)],
                          env=child_env, cwd=str(tmp_path), timeout=120,
                          capture_output=True, text=True)
    seen = json.loads(out.read_text()) if out.exists() else {}
    seen["stderr"] = done.stderr
    return done.returncode, seen
