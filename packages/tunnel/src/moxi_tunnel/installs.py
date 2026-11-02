"""tunnel's `moxi.installs` listener, which the core calls around every module's install
and uninstall.

`on_install(module, found)` runs after any module's install settles. The core calls it on
every listener, so it must exist; tunnel has nothing to do then and returns.

`on_uninstall(module)` runs before any module's package is removed, after its teardown.
For another module it removes that module's addresses without asking; for tunnel itself
it removes nothing and names every address still in the record (`moxi_tunnel.uninstall`).
"""

from __future__ import annotations


def on_install(module: str, found=None) -> None:
    """Nothing to do after an install."""
    return None


def on_uninstall(module: str) -> None:
    """Take `module`'s addresses out, or for tunnel name what stays live."""
    from . import uninstall

    uninstall.on_uninstall(module)


__all__ = ["on_install", "on_uninstall"]
