"""What a log says of rulings shown and not: the read-backs it holds and the rulings
no mark names, read from a log's state.

A read-back is a ruling of a project's own log, `readback=TIME`: the operator's word
that he read the list of rulings not yet shown through `TIME`. It covers every ruling
of the project recorded at or before that time, in whichever log.

So whether a ruling is shown takes two logs: its own, which holds the marks, and the
project's, which holds the read-backs. The index keeps a row per log, and each of the
two functions it lists items with gives one half: `unmarked` the rulings of a log that
no mark names, each with when it was recorded, and `read_backs` the read-backs of a
log. Whoever lists the rulings not yet shown reads the two together.

This module reads states only, so the index can use it; `shown` has the list, the
mark and the gates.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping

from . import corrections
from . import state as states

#: What starts the item of a read-back; the time he read the list through follows.
READBACK = corrections.READBACK

#: A time as a log writes one: UTC, to the second.
TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


def read_back(ruling: states.Ruling) -> str | None:
    """The time a read-back says the list was read through, or None for a ruling that
    is no read-back."""
    rules = ruling.rules or ""
    if not rules.startswith(READBACK) or not TIME.fullmatch(rules[len(READBACK):]):
        return None
    return rules[len(READBACK):]


def through(project_state: states.State) -> str | None:
    """The time the operator has read the project's list through: the latest any of
    its read-backs names, or None when it has none."""
    return max((time for time in map(read_back, project_state.rulings)
                if time is not None), default=None)


# -- what the index keeps ----------------------------------------------------------------

def unmarked(state: states.State) -> Iterator[tuple[str, Mapping]]:
    """The rulings of `state` that no mark names, a read-back aside, each as `(id,
    body)` for the index: the sentence, the item it rules, the pointer to where he said
    it, the ruling it replaces and the one it takes back, and when it was recorded.
    Whether a read-back of the project covers that time is not in this log."""
    for ruling in state.rulings:
        if ruling.id is None or ruling.shown is not None or read_back(ruling):
            continue
        yield ruling.id, {"text": ruling.text, "rules": ruling.rules,
                          "record": ruling.record, "supersedes": ruling.supersedes,
                          "revokes": ruling.revokes, "at": ruling.at}


def read_backs(state: states.State) -> Iterator[tuple[str, Mapping]]:
    """The read-backs of `state`, each as `(id, body)` for the index; the body holds
    the time it names as `through`."""
    for ruling in state.rulings:
        time = read_back(ruling)
        if ruling.id is not None and time is not None:
            yield ruling.id, {"through": time}


__all__ = ["READBACK", "TIME", "read_back", "through", "unmarked", "read_backs"]
