"""One launch run in a process of its own, as `moxi launcher run` runs it: a launch in
this terminal becomes the harness, so a test runs it here and reads what it left.

    python -m launcher_child SPEC

`SPEC` is a JSON file: `request` (the `LaunchRequest`'s fields), `out` (where this
writes what it saw), and optionally `moxi` (the command comms' wrapper runs under, in
place of this environment's `moxi`), and `free_identity` (when true, comms' identity check
finds every name free: the test's comms has no daemon to ask). The deliverer runs the `deliver` function directly,
from this environment's Python, since a test has no `moxi launcher deliver` to run.
When the test hid the installed turns (`launcher_fake_turns.hide`), it is hidden here too.
The launch is named at one fixed time, so the preview and the launch hold one name.

Before the launch it writes `preview(request).argv` to `out`; after a launch that
returns (one that refused, failed, or did not exec) it adds the result and exits with
its code. A launch that execs never comes back: the harness's own exit code is this
process's.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import launcher_fake_turns as fake_turns
from moxi_launcher import api, compose, first, names

DELIVER = ("import sys; from moxi_launcher import first; "
           "sys.exit(first.deliver(sys.argv[1], pid=int(sys.argv[3])))")


def main(spec_path: str) -> int:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    out = Path(spec["out"])
    if os.environ.get(fake_turns.HIDDEN):
        fake_turns.hide_here()
    names.now = lambda: datetime(2026, 11, 8, 12, 45)
    if spec.get("moxi"):
        compose.moxi_command = lambda: spec["moxi"]
    if spec.get("free_identity"):
        api._identity_taken = lambda name, scope: None
    first.deliverer_argv = lambda rid, pid: [sys.executable, "-c", DELIVER, rid, "--pid",
                                             str(pid)]
    request = api.LaunchRequest(**spec["request"])
    seen = {"preview": list(api.preview(request).argv)}
    out.write_text(json.dumps(seen), encoding="utf-8")
    try:
        result = api.launch(request)
    except Exception as error:  # noqa: BLE001  (a refusal is what the test reads)
        seen["refused"] = {"type": type(error).__name__, "text": str(error),
                           "exit": api.exit_code(error)}
        out.write_text(json.dumps(seen), encoding="utf-8")
        return api.exit_code(error)
    seen["result"] = {"state": result.state, "reason": result.reason,
                      "exit": result.exit_code, "duplicate": result.duplicate}
    out.write_text(json.dumps(seen), encoding="utf-8")
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
