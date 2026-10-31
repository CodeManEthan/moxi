"""`python -m moxi`: the `moxi` command, for a caller that has the environment's
interpreter but not its console script.

A Windows task runs the gate this way, `pythonw.exe -m moxi run ...`, so that no console
window opens at logon. `pythonw.exe` gives the process no standard streams (`sys.stdin`,
`sys.stdout` and `sys.stderr` are None), so each missing one is opened on the null device
first, and a line the command prints is dropped instead of raising.
"""

from __future__ import annotations

import os
import sys


def _streams() -> None:
    for name, mode in (("stdin", "r"), ("stdout", "w"), ("stderr", "w")):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, mode, encoding="utf-8"))  # noqa: SIM115


def main() -> int:
    _streams()
    from moxi.cli import main as cli

    return cli()


if __name__ == "__main__":
    sys.exit(main())
