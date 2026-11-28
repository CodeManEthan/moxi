"""`moxi-dev cap [--] CMD [ARG...]`: run one command in a memory scope of its own.

The scope is made by the same mechanism as `moxi-dev check`'s (`moxi_dev.scope`): the
same figures (`MOXI_TEST_MEMORY_HIGH_MIB` and `MOXI_TEST_MEMORY_MAX_MIB`, else the default
for the worker count `moxi-dev check` would use), the same skip rules, version rules,
line watch and signal handling, in a unit named `moxi-dev-cap-<12 hex>.scope`. The
command gets `MOXI_DEV_SCOPE` naming that unit, so a `moxi-dev check` inside it runs as
that scope's inner and makes no second scope. The command's words are passed as they are.

A command under the cap is a check: it gets its tests' temp on disk
(`moxi_dev.temp.for_check`), and when the cap changed or kept back a memory choice it
prints why on one line before the command's output. After the command has ended, and
once its scope's unit is gone where there was one, the cap sweeps what test runs left
(`moxi_dev.temp.sweep`) and prints a line when the sweep removed something, before its
last line.

It exits with the command's code. An end past the line or a memory kill prints the same
lines as the check's, with `moxi-dev cap:` in front, and exits 1; a bad figure or
`MOXI_TEST_TEMP`, or a scope that can't be made where one is expected, exits 2. When the command has ended it kills
any process left in the scope and prints `moxi-dev cap: ended 2 processes the command left
running`, and then, last, one line: `moxi-dev cap: memory peak 0.8G, held 0.6G, line
7.4G, high 7.8G, max 9.3G, stalled 0.0s`, or `moxi-dev cap: memory not capped (CI)` where
it ran with no scope.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping

from . import scope, temp
from .check import CheckError, worker_args

USAGE = "usage: moxi-dev cap [--] CMD [ARG...]"


def _say(text: str) -> None:
    print(text, flush=True)


def _sweep() -> temp.Swept:
    """The sweep of what earlier test runs left (`moxi_dev.temp.sweep`)."""
    return temp.sweep()


def _swept() -> None:
    removed = _sweep().line()
    if removed:
        _say(f"moxi-dev cap: {removed}")


def _plain(command: list[str], env: Mapping[str, str]) -> int:
    """Run the command in place, with no scope; its exit code (128 + N for signal N)."""
    try:
        code = subprocess.run(command, env=dict(env)).returncode
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
    try:
        found = scope.figures(os.environ, int(worker_args(os.environ)[1]))
        env, why_temp = temp.for_check(os.environ)
    except (CheckError, scope.ScopeError, temp.TempError) as exc:
        print(f"moxi-dev cap: {exc}", file=sys.stderr, flush=True)
        return 2
    if why_temp:
        did = (f"gave the command {temp.TEMP_ENV}={temp.DISK}"
               if env != dict(os.environ)
               else f"left {temp.TEMP_ENV} as it was")
        _say(f"moxi-dev cap: {did}: {why_temp}")
    system = scope.System(env=env)
    host = scope.find_host(system) if found is not None else None
    why = scope.skip_reason(found, host)
    if why is not None:
        code = _plain(command, env)
        _swept()
        _say(f"moxi-dev cap: memory not capped ({why})")
        return code
    warning = scope.below_default(found)
    if warning:
        _say(f"moxi-dev cap: {warning}")
    outer = scope.Outer(kind="cap", command=command, found=found, host=host, system=system,
                        description=f"moxi-dev cap in {Path.cwd()}", say=_say)
    outcome = outer.run(env)
    if outcome.is_gone:
        _swept()
    if outcome.signum is not None:
        scope.die_by(outcome.signum)
        return outcome.code
    _say(f"moxi-dev cap: memory {outcome.memory}")
    return outcome.code
