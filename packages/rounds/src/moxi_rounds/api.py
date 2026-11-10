"""The rounds read API, interface 1: what the rounds on this machine say, read in process.

A module that takes rounds as optional reaches this through the core, `available()`
first and `load()` only on yes:

    found = (moxi.modules.load("rounds", interface=1)
             if moxi.modules.available("rounds") else None)
    if found is None or not found.ok:
        ...  # the feature is off

A read never writes.
"""

from __future__ import annotations

__all__: list[str] = []
