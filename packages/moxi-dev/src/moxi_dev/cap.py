"""`moxi-dev cap [--] CMD [ARG...]`: run one command in a memory scope of its own.

The scope is made by the same mechanism as `moxi-dev check`'s (`moxi_dev.scope`): the
same figures (`MOXI_TEST_MEMORY_HIGH_MIB` and `MOXI_TEST_MEMORY_MAX_MIB`, else the default
for the worker count `moxi-dev check` would use), the same skip rules, version rules,
line watch and signal handling, in a unit named `moxi-dev-cap-<12 hex>.scope`. The
command gets `MOXI_DEV_SCOPE` naming that unit, so a `moxi-dev check` inside it runs as
that scope's inner and makes no second scope. The command's words are passed as they are.

It exits with the command's code. An end past the line or a memory kill prints the same
lines as the check's, with `moxi-dev cap:` in front, and exits 1; a bad figure, or a scope
that can't be made where one is expected, exits 2. When the command has ended it kills
any process left in the scope and prints `moxi-dev cap: ended 2 processes the command left
running`, and then, last, one line: `moxi-dev cap: memory peak 0.8G, held 0.6G, line
6.1G, high 6.4G, max 7.9G, stalled 0.0s`, or `moxi-dev cap: memory not capped (CI)` where
it ran with no scope.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import scope
from .check import CheckError, worker_args

USAGE = "usage: moxi-dev cap [--] CMD [ARG...]"


def _say(text: str) -> None:
    print(text, flush=True)


def _plain(command: list[str]) -> int:
    """Run the command in place, with no scope; its exit code (128 + N for signal N)."""
    try:
        code = subprocess.run(command).returncode
    except FileNotFoundError as exc:
        print(f"moxi-dev cap: {command[0]}: {exc.strerror}", file=sys.stderr, flush=True)
        return 127
    except KeyboardInterrupt:
        return 130
    return 128 - code if code < 0 else code


def main(argv: list[str]) -> int:
    if argv[:1] in (["-h"], ["--help"]):
        print(USAGE + "\n\n" + (__doc__ or "").split("\n\n", 1)[1].strip())
        return 0
    command = argv[1:] if argv[:1] == ["--"] else list(argv)
    if not command:
        print(USAGE, file=sys.stderr)
        return 2
    env = os.environ
    try:
        found = scope.figures(env, int(worker_args(env)[1]))
    except (CheckError, scope.ScopeError) as exc:
        print(f"moxi-dev cap: {exc}", file=sys.stderr, flush=True)
        return 2
    system = scope.System(env=env)
    host = scope.find_host(system) if found is not None else None
    why = scope.skip_reason(found, host)
    if why is not None:
        code = _plain(command)
        _say(f"moxi-dev cap: memory not capped ({why})")
        return code
    warning = scope.below_default(found)
    if warning:
        _say(f"moxi-dev cap: {warning}")
    outer = scope.Outer(kind="cap", command=command, found=found, host=host, system=system,
                        description=f"moxi-dev cap in {Path.cwd()}", say=_say)
    outcome = outer.run(env)
    if outcome.signum is not None:
        scope.die_by(outcome.signum)
        return outcome.code
    _say(f"moxi-dev cap: memory {outcome.memory}")
    return outcome.code
