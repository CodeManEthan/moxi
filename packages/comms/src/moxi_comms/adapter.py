"""What an adapter built outside comms uses (`moxi_comms.adapter`), public beside
`moxi_comms.api`.

An adapter outside comms binds its own name with the daemon, reports its turns' ends
(the daemon keeps the cursor) and renders mail as comms' own adapters do:

* `DaemonClient`, `DaemonError`, `DaemonOffline`, `DaemonNoAnswer`: one connection to
  the daemon, a refusal, the socket gone, and a post whose answer was lost.
  `backoff_delays()`: the reconnect backoff comms' adapters use.
* `render_digest(payloads, ...)` and `frame_line(payload, ...)`: delivered mail as a
  digest, and one payload's frame line.
* The attribution tiers and marks: `TIER_EPHEMERAL`, `TIER_CHOSEN`, `TIER_STANDING`,
  `TIER_OPERATOR`, `TIER_SEND_ONLY`, `TIER_UNKNOWN`, `VERIFIED`, `LABELED`,
  `UNVERIFIED`.
* `parse_machine_line(body)`: a message's line zero as a `MachineLine`, or None when it
  is prose.
"""

from __future__ import annotations

from .adapters.claude_shim import frame_line, render_digest
from .attribution import (LABELED, TIER_CHOSEN, TIER_EPHEMERAL, TIER_OPERATOR,
                          TIER_SEND_ONLY, TIER_STANDING, TIER_UNKNOWN, UNVERIFIED,
                          VERIFIED)
from .client import (DaemonClient, DaemonError, DaemonNoAnswer, DaemonOffline,
                     backoff_delays)
from .machine_line import MachineLine
from .machine_line import parse as parse_machine_line

__all__ = ["DaemonClient", "DaemonError", "DaemonOffline", "DaemonNoAnswer",
           "backoff_delays", "render_digest", "frame_line",
           "TIER_EPHEMERAL", "TIER_CHOSEN", "TIER_STANDING", "TIER_OPERATOR",
           "TIER_SEND_ONLY", "TIER_UNKNOWN", "VERIFIED", "LABELED", "UNVERIFIED",
           "parse_machine_line", "MachineLine"]
