"""A round that has ended: what it still takes.

A round ends by being closed or by being dropped, and stays as it ended. From then on
it takes no new work: no question, no change to one and no ruling of its own. What follows from
an ended round is work for a new round.

Two things are still recorded on it, and neither is asked `takes_new_work`. A
correction of a ruling it holds (a later ruling that replaces the earlier one, or takes
it back) changes what is read from then on and reopens nothing, so it is no new work.
And the mark that a ruling it holds was shown to the operator: that says something of
what is already recorded and adds nothing to the round.

`takes_new_work(name, what)` is the one gate every verb that would add work to a round
runs. `how(state)` says how a round ended, from the event that ended it.
"""

from __future__ import annotations

from collections.abc import Callable

from . import state as states
from .errors import Unmet

#: The event that ends a round each way, and what the round is called from then on.
_ENDINGS = {"round.closed": "closed", "round.dropped": "dropped"}


def how(state: states.State) -> str | None:
    """How the round `state` is of ended, `closed` or `dropped`, or None while it has
    not, and for a log that opened no round."""
    for event in reversed(state.history):
        if event["type"] in _ENDINGS:
            return _ENDINGS[event["type"]]
    return None


def unmet(state: states.State, name: str, what: str) -> Unmet | None:
    """Why the round `name`, whose log folds to `state`, does not take `what` (a new
    question, say), or None when it does: it has not ended."""
    ended = how(state)
    if ended is None:
        return None
    project = name.partition("/")[0]
    return Unmet(f"{name} was {ended} and stays as it ended: it takes {what} no "
                 "longer. What follows from it is new work, in a new round; SLUG "
                 "below is that round's",
                 f"moxi rounds open {project}/SLUG --description TEXT")


def takes_new_work(name: str, what: str) -> Callable:
    """The gate that the round `name` takes new work, for a verb that would add `what`
    to it. It reads the round's own log as the write path read it, so on a project
    alone, which has no round, it always holds."""
    def gate(view):
        return unmet(view.state, name, what)
    return gate


__all__ = ["how", "unmet", "takes_new_work"]
