"""`moxi-tunnel-cloudflared`: the command of tunnel's one daemon.

It runs `cloudflared --no-autoupdate tunnel --config <state>/cloudflared.yml run`, the
binary being the `cloudflare.binary` setting, else `cloudflared` on `PATH`.

- On POSIX it replaces its own process with `cloudflared`, so the supervisor watches
  `cloudflared` itself.
- On Windows the core runs every daemon under its `moxi run` gate, and a replaced process
  would leave the gate's child, so it runs `cloudflared` as its child, waits for it and
  exits with its code. It passes no breakaway or new-process-group flag, so `cloudflared`
  stays in the gate's job. A child ended by a signal (POSIX only) exits non-zero.
- When `cloudflared.yml` is missing or holds no target rule, it logs one line and exits
  0, so a switch turned on by hand gives an idle unit, not a crash loop: the supervisors
  restart a daemon only when it fails.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence

from moxi import exit as codes
from moxi import log

from . import cloudflare


def command(binary: str) -> list[str]:
    """The `cloudflared` command line, on tunnel's own config file."""
    return [binary, "--no-autoupdate", "tunnel", "--config",
            str(cloudflare.config_path()), "run"]


def run_child(argv: Sequence[str]) -> int:
    """Run `argv` as this process's child, wait for it and return its exit code: 128 plus
    the signal's number when a signal ended it."""
    child = subprocess.Popen(list(argv))
    try:
        code = child.wait()
    except KeyboardInterrupt:
        code = child.wait()
    if code < 0:
        return 128 + -code
    return code


def main(argv: Sequence[str] | None = None) -> int:
    logger = log.setup("tunnel")
    path = cloudflare.config_path()
    on_disk = cloudflare.read_file()
    if on_disk.text is None or not on_disk.rules():
        what = "is missing" if on_disk.text is None else "holds no target rule"
        logger.info("%s %s; no Cloudflare target to serve, so cloudflared is not started",
                    path, what)
        return codes.OK
    binary = cloudflare.find_binary()
    if binary is None:
        logger.error("cloudflared is not installed (not on PATH, and no cloudflare.binary "
                     "setting); install it, or moxi config set tunnel cloudflare.binary "
                     "<path>")
        return codes.ERROR
    args = command(binary)
    if sys.platform == "win32":
        return run_child(args)
    os.execv(binary, args)
    return codes.ERROR  # not reached: execv replaces this process


if __name__ == "__main__":
    sys.exit(main())
