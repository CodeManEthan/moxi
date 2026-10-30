"""usage's listeners on turns' session events.

Each is a plain function of one event object, the `SessionEvent` turns hands its listener
rows. turns ignores what they return, and each one fails open: a problem is logged and
the event goes on.

* `usage(event)`: Claude's `usage` event, one `$.session.usage()` result (`event.usage`)
  with the time turns received it (`event.received_at`). Its `rateLimits` entries become
  readings with source `session-usage`, observed at that time; a result identical to the
  session's previous one is not stored again.
* `codex_limits(event)`: Codex's `codex_limits` event, one `account/rateLimits/read`
  answer or `account/rateLimits/updated` push (`event.payload`) with its receive time
  (`event.received_at`). sessions maps it to limits records, which are stored as
  app-server readings.

Importing this module imports nothing beyond the standard library's `logging`; the
readings store and sessions load on the first event.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("moxi.usage")


def usage(event: Any) -> None:
    """Store the Claude rate-limit readings of one `usage` event."""
    if getattr(event, "harness", None) not in (None, "claude"):
        return None
    report = getattr(event, "usage", None)
    received = _seconds(getattr(event, "received_at", None))
    if not isinstance(report, dict) or received is None:
        return None
    rate_limits = report.get("rateLimits")
    if not isinstance(rate_limits, list) or not rate_limits:
        return None                       # empty at session start and off a subscription
    try:
        from . import readings

        readings.add_claude(rate_limits, observed_at=received,
                            session_id=getattr(event, "session_id", None), now=received)
    except Exception:  # noqa: BLE001 - a listener fails open
        log.warning("usage: the Claude limits reading was not stored", exc_info=True)
    return None


def codex_limits(event: Any) -> None:
    """Store the Codex app-server readings of one `codex_limits` event."""
    payload = getattr(event, "payload", None)
    received = _seconds(getattr(event, "received_at", None))
    if not isinstance(payload, dict) or received is None:
        return None
    try:
        from moxi_sessions.api import codex

        from . import readings

        records = codex.limits_from_app_server(payload, received)
        readings.add_codex(records, now=received)
    except Exception:  # noqa: BLE001 - a listener fails open
        log.warning("usage: the Codex app-server limits reading was not stored",
                    exc_info=True)
    return None


def _seconds(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


__all__ = ["usage", "codex_limits"]
