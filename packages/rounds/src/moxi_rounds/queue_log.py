"""A build's place in its repo's queue, as its round's log holds it.

Each queue event about a member is mirrored into its round's log (`queue.join`,
`queue.batch`, `queue.landed`, `queue.ejected`, `queue.left`), so the gates, `next` and
`show` read a member's standing from the round alone. `standing(state, stage)` gives the
newest of them for the stage's current build, or None when it never joined.

The queue file (`queue_file`) holds the queue as a whole: the order, the running batch
and a pause. `next` reads it too, for a member still in the queue, to give its position.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import builds
from . import state as states

JOINED = "queue.join"
LEFT = "queue.left"
BATCHED = "queue.batch"
LANDED = "queue.landed"
EJECTED = "queue.ejected"
#: Every mirrored event.
TYPES = (JOINED, LEFT, BATCHED, LANDED, EJECTED)


@dataclass(frozen=True)
class Standing:
    """A member's standing from its round's log: the `tip` it joined at, whether it is
    `urgent`, the newest mirrored event's type (`last`) and its fields (`fields`)."""

    tip: str
    urgent: bool
    last: str
    fields: dict

    @property
    def queued(self) -> bool:
        """Whether it is still in the queue: joined or in a batch, not ended there."""
        return self.last in (JOINED, BATCHED)

    def line(self) -> str:
        """The standing in words, as `next` says it."""
        reason = self.fields.get("reason")
        if self.last == JOINED:
            return f"queued at {self.tip[:12]}" + (", urgent" if self.urgent else "")
        if self.last == BATCHED:
            return f"in batch {self.fields.get('batch')}, CI pending"
        if self.last == LANDED:
            return f"landed at {str(self.fields.get('landing'))[:12]}"
        if self.last == EJECTED:
            return f"ejected: {reason}"
        return f"left: {reason}"


def standing(state: states.State, stage: str) -> Standing | None:
    """The stage's current build's standing in its queue, None when it never joined."""
    build = builds.current(state, stage)
    if build is None:
        return None
    found: Standing | None = None
    for event in state.history:
        if (event["n"] <= build.n or event.get("type") not in TYPES
                or event.get("stage") != stage):
            continue
        if event["type"] == JOINED:
            found = Standing(tip=str(event.get("tip")), urgent=bool(event.get("urgent")),
                             last=JOINED, fields=dict(event))
        elif found is not None:
            found = Standing(tip=found.tip, urgent=found.urgent, last=event["type"],
                             fields=dict(event))
    return found


__all__ = ["JOINED", "LEFT", "BATCHED", "LANDED", "EJECTED", "TYPES", "Standing",
           "standing"]
