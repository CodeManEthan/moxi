"""tunnel's `moxi.installs` listener, which the core calls around every module's install
and uninstall.

`on_install(module, found)` runs after any module's install settles. The core calls it on
every listener, so it must exist; tunnel has nothing to do then and returns.

`on_uninstall(module)` runs before any module's package is removed. Removing that module's
entries is not built yet, so for now it does nothing and every entry stays in the record.
"""

from __future__ import annotations


def on_install(module: str, found=None) -> None:
    """Nothing to do after an install."""
    return None


def on_uninstall(module: str) -> None:
    """Nothing done before an uninstall yet; the module's entries stay in the record."""
    return None


__all__ = ["on_install", "on_uninstall"]
