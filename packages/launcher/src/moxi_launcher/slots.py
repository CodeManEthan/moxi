"""slots, as the launcher reaches it: the API behind the slot target and the startup
screens.

slots is an optional link. `api()` is its API module, or None when slots is not installed
or can't be loaded; `available()` says whether it is installed without loading it. The
catalog, the resolver and the composer ask `available()` about slots, and the slot target
asks `api()`, so they agree on whether slots is there.

When `MOXI_LAUNCHER_SLOTS` names a module, that module stands in for slots' API: the seam a
test uses to give the launcher a fake slots, in its own process or in a launch it starts.
"""

from __future__ import annotations

import importlib
import logging
import os
from typing import Any

from moxi import modules

log = logging.getLogger("moxi.launcher.slots")

MODULE = "slots"
#: The interface of slots' API the launcher is written against.
INTERFACE = 1
#: A test seam: the import name of a module that stands in for slots' API.
SEAM = "MOXI_LAUNCHER_SLOTS"


def available() -> bool:
    """Whether slots is installed here (or stood in for on the seam). Imports nothing."""
    return bool(os.environ.get(SEAM)) or modules.available(MODULE)


def api() -> Any | None:
    """slots' API module, or None when slots is not installed or can't be loaded."""
    seam = os.environ.get(SEAM)
    if seam:
        return importlib.import_module(seam)
    if not modules.available(MODULE):
        return None
    found = modules.load(MODULE, interface=INTERFACE)
    if not found.ok or found.api is None:
        log.warning("slots is installed but can't be loaded: %s", found.reason)
        return None
    return found.api


def links(name: str) -> bool:
    """Whether optional module `name` is installed, slots asked through `available()`:
    the default answer the catalog and the resolver give for a row's `requires`."""
    return available() if name == MODULE else modules.available(name)


__all__ = ["INTERFACE", "MODULE", "SEAM", "api", "available", "links"]
