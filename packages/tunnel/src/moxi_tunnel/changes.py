"""The change listeners: modules that want to hear when a target's address changes.

A module declares `on_change(target, old, new)` in the extension group
`moxi.tunnel.changes`, named by the module:

    [project.entry-points."moxi.tunnel.changes"]
    ui = "moxi_ui.tunnel:on_change"

Tunnel calls every listener in the acting process once the act's last record write is
done and its locks are let go, once for each entry whose content (every field but
`generation` and `applied_at`) differs between the record before the act and after it:
`old` is None for an add, `new` None for a remove or forget. An act that rolled back
changes no content, so it calls none. A failing listener is logged and the act still
succeeds. A running service reads `Address.change` instead.
"""

from __future__ import annotations

from moxi import log, modules

from . import api, locks
from .record import Record

#: The extension group listeners are declared in.
GROUP = "moxi.tunnel.changes"


def changed(before: Record, after: Record) -> list[str]:
    """The targets whose entry content differs between `before` and `after`."""
    out = []
    for key in sorted(set(before.entries) | set(after.entries)):
        old, new = before.entries.get(key), after.entries.get(key)
        if (old is None) != (new is None) or (old is not None and new is not None
                                              and old.content() != new.content()):
            out.append(key)
    return out


def notify(before: Record, after: Record) -> None:
    """Call every listener for each target whose entry changed between `before` and
    `after`. Called with no lock held."""
    keys = changed(before, after)
    if not keys:
        return
    locks.none_held()
    logger = log.setup("tunnel")
    listeners = modules.extensions(GROUP)
    for key in keys:
        old, new = api.from_entry(before, key), api.from_entry(after, key)
        for ext in listeners:
            try:
                ext.load()(key, old, new)
            except Exception as exc:  # noqa: BLE001 - a listener never fails the act
                logger.warning("the change listener %s (%s) failed for %s: %s", ext.name,
                               ext.distribution, key, exc)


__all__ = ["GROUP", "changed", "notify"]
