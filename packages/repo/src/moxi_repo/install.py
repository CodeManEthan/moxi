"""What `moxi install` and `moxi uninstall` run of repo's: its `SETUP`, its `TEARDOWN`, and
its `moxi.installs` listener.

The core runs `SETUP` on every `moxi install repo`, a reinstall and an upgrade included,
and its doctor flags a module whose `SETUP` last ran at another version, with `moxi install
repo` as the fix. Repo's `SETUP` runs `refresh` over every indexed repo, so an install that
moved the environment rewrites every config entry, shim and launcher to name this
environment's Python and runner, and every declaration is read again.

The core calls the listener for every module, repo included: `on_install(module, found)`
last in each module's install or upgrade, and `on_uninstall(module)` after that module's
`TEARDOWN`, before its package is removed. So a module whose upgrade moves a piece's
function, or drops a piece, and a module that is uninstalled, never leave a closed piece
refusing commits. `TEARDOWN` runs first in `moxi uninstall repo` and takes out of every
indexed repo what repo wrote there.
"""

from __future__ import annotations

import sys


def _failed(exc) -> int:
    print(f"repo: {exc.text}", file=sys.stderr)
    for line in exc.lines:
        print(f"  {line}", file=sys.stderr)
    return 1


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
        return _failed(exc)
    print_report(report)
    return 0


def teardown() -> int:
    """Take out of every indexed repo what repo wrote there, and clear repo's doctor
    records; prints what it did. 0 when every repo was cleaned; 1, with the reason, when
    the index can't be read or a repo couldn't be cleaned, which stops the uninstall."""
    from moxi_repo import api
    from moxi_repo.cli import print_report

    try:
        report = api.teardown()
    except api.Failure as exc:
        return _failed(exc)
    if print_report(report):
        print("repo: fix the problem above and run moxi uninstall repo again", file=sys.stderr)
        return 1
    return 0


def on_install(module: str, found) -> None:
    """Rewrite module `module`'s registered pieces in every indexed repo from its
    declarations as they now are, and remove each piece it no longer declares, with a line.
    One repo's problem is printed and the rest go on; an index that can't be read raises."""
    from moxi_repo import api
    from moxi_repo.cli import print_report

    print_report(api.install_module(module))


def on_uninstall(module: str) -> None:
    """Remove module `module`'s pieces and specs from every indexed repo. One repo's
    problem is printed and the rest go on; an index that can't be read raises."""
    from moxi_repo import api
    from moxi_repo.cli import print_report

    print_report(api.uninstall_module(module))


__all__ = ["setup", "teardown", "on_install", "on_uninstall"]
