"""A test shell's process: `python -P -m moxi_ui.testing.run '<options as JSON>'`.

`TestShell` starts it; nothing else should. It runs the shell as `moxi-ui-serve` does,
with three differences a test needs: it binds the port its options name, or one the
system picks, it takes the test-only options (`child_args` for every page server it starts, `leave_out` for the
modules its snapshot leaves out) from its one argument, never from a setting or a
variable, and it stops when its lifeline closes (`moxi.proc.spawn(..., lifeline="fd")`).

Once it serves, it prints one line, `ready <JSON>`, holding its runtime file's fields
(the token among them), to stdout, which is a pipe to the test only.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence

from moxi import aio, files, log, proc
from moxi.paths import Layout

from .. import listen, serve
from ..serve import Shell


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    options = json.loads(args[0]) if args else {}
    child_args = [str(arg) for arg in options.get("child_args", ())]
    leave_out = [str(name) for name in options.get("leave_out", ())]
    port = int(options.get("port", 0))
    log.setup(serve.MODULE, "info")
    layout = Layout(serve.MODULE)
    runtime_dir = layout.runtime_dir(create=True)
    try:
        with files.lock(runtime_dir / serve.LOCK_FILE, timeout=serve.LOCK_WAIT):
            shell = Shell(listen.bind(port), epoch=None, layout=layout, snapshot=True,
                          child_args=child_args, leave_out=leave_out)
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
