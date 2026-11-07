"""A wrapper whose adapter never gives its word: the bind the daemon never answers.

`silent_launch(harness, ...)` runs the Claude or Codex wrapper's fork in a child
process (the wrapper forks, so never in the test's own), with an adapter that holds its
end of the pipe open and says nothing for longer than the wrapper waits. It returns the
wrapper's exit code, the execs it asked for (none, or the one call that would have
started the harness) and what it printed on stderr.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace

#: The wrappers comms ships, by the harness each starts.
HARNESSES = ("claude", "codex")

_CHILD = r"""
import json, sys, time
from moxi_comms.adapters import codex_wrapper, session, word

harness, ready_wait, notice_after, cwd = sys.argv[1:5]
ready_wait = float(ready_wait)
if notice_after:
    word.NOTICE_AFTER = float(notice_after)
execs = []

def exec_(binary, argv, env):
    execs.append([str(binary), list(argv)])
    return 0

def silent(*_args):
    time.sleep(ready_wait + 1.0)
    return 0

if harness == "claude":
    session._adapter_process = silent
    s = session.Session("5e55a1d0-0000-4000-8000-0000000000b1", name="silent")
    code = session.launch(s, "/bin/true", ["claude"], {}, ready_wait=ready_wait,
                          exec_=exec_)
else:
    codex_wrapper._adapter_process = silent
    launch = codex_wrapper.plan(
        codex_wrapper.build_parser().parse_args(["--name", "silent", "--no-window"]),
        cwd=cwd, endpoint=lambda: (None, "comms' daemon isn't running"))
    code = codex_wrapper.start(launch, "/bin/true", ready_wait=ready_wait, exec_=exec_,
                               roster={})
print(json.dumps({"code": code, "execs": execs}))
"""


def silent_launch(harness: str, tmp_path, *, ready_wait: float,
                  notice_after: float | None = None) -> SimpleNamespace:
    """The `harness` wrapper's launch with a silent adapter, waiting `ready_wait`."""
    if harness not in HARNESSES:
        raise ValueError(f"comms ships no wrapper for {harness!r}")
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, harness, str(ready_wait),
         "" if notice_after is None else str(notice_after), str(tmp_path)],
        env=dict(os.environ), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    return SimpleNamespace(code=out["code"], execs=out["execs"],
                           err=proc.stderr.splitlines())
