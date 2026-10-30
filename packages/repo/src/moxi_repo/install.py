"""What `moxi install repo` runs: repo's `SETUP`.

The core runs `SETUP` on every `moxi install repo`, a reinstall and an upgrade included,
and its doctor flags a module whose `SETUP` last ran at another version, with `moxi install
repo` as the fix. Repo's `SETUP` runs `refresh` over every indexed repo, so an install that
moved the environment rewrites every config entry, shim and launcher to name this
environment's Python and runner, and every declaration is read again.
"""

from __future__ import annotations

import sys


def setup() -> int:
    """`refresh` over every indexed repo; prints what it did. 0 once it ran, whatever one
    repo's problem (each stays a doctor row, with its fix); 1 when it can't run at all: no
    usable git while repos are indexed, repo's config can't be read, or its index can't."""
    from moxi_repo import api
    from moxi_repo.cli import print_report

    try:
        if not api.indexed():
            print("repo: no repo is indexed yet, so there is nothing to rewrite")
            return 0
        report = api.refresh()
    except api.Failure as exc:
        print(f"repo: {exc.text}", file=sys.stderr)
        for line in exc.lines:
            print(f"  {line}", file=sys.stderr)
        return 1
    print_report(report)
    return 0


__all__ = ["setup"]
