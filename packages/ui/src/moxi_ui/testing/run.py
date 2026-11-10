"""A test shell's process: `python -P -m moxi_ui.testing.run '<options as JSON>'`.

`TestShell` starts it; nothing else should. It runs the shell as `moxi-ui-shell` does,
with three differences a test needs: it binds the port its options name, or one the
system picks, it takes the test-only options (`child_args` for every page server it
starts, `leave_out` for the modules its snapshot leaves out, `failed_for` for the seconds
a failed start answers with its failure, `gateway` to run the shell's gateway as
`moxi-ui-shell` does, `checks` to let it run the doctor for its kept run, which it
otherwise never starts) from its one argument, never from a setting or a variable, and it
stops when its lifeline closes (`moxi.proc.spawn(..., lifeline="fd")`).

Once it serves, it prints one line, `ready <JSON>`, holding its runtime file's fields
(the token among them), to stdout, which is a pipe to the test only.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence

from moxi import aio, files, log, proc
from moxi.paths import Layout

from .. import cli, listen, serve
from ..serve import Shell
from ..starter import FAILED_FOR, Starters


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    options = json.loads(args[0]) if args else {}
    child_args = [str(arg) for arg in options.get("child_args", ())]
    leave_out = [str(name) for name in options.get("leave_out", ())]
    port = int(options.get("port", 0))
    failed_for = float(options.get("failed_for", FAILED_FOR))
    gateway = bool(options.get("gateway", False))
    checks = bool(options.get("checks", False))
    log.setup(serve.MODULE, "info")
    layout = Layout(serve.MODULE)
    runtime_dir = layout.runtime_dir(create=True)
    try:
        with files.lock(runtime_dir / serve.LOCK_FILE, timeout_s=serve.LOCK_WAIT):
            starters = Starters(runtime_dir=runtime_dir, state_dir=layout.state_dir(),
                                log=cli.log_path(), child_args=child_args,
                                failed_for=failed_for)
            shell = Shell(listen.bind(port), epoch=None, layout=layout, snapshot=True,
                          starters=starters, leave_out=leave_out, gateway=gateway,
                          run_checks=checks)
            try:
                info = shell.start()
                proc.lifeline(on_close=shell.stop)
                ready = {"server_id": info.server_id, "pid": info.pid, "port": info.port,
                         "token": info.token, "version": info.version,
                         "started_at": info.started_at}
                print("ready " + json.dumps(ready), flush=True)
                aio.run(shell.run, workers=serve.WORKERS, slow=serve.SLOW)
            except KeyboardInterrupt:
                pass
            finally:
                shell.close()
    except files.LockTimeout:
        print("another shell holds page.lock", file=sys.stderr, flush=True)
        return files.LockTimeout.exit_code
    return 0


if __name__ == "__main__":
    sys.exit(main())
