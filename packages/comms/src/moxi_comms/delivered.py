"""Reading a delivered digest back: which messages a text a session was handed holds.

A digest (`adapters/claude_shim.py`'s `render_digest`, which the Codex adapter renders
through too) frames each message between its frame line, `[comms] from <name> · msg <id>
· ...`, and its closing line, `[comms] end · msg <id>`, both at column zero, with every
body line indented (`envelope.INDENT`). `read(text)` finds each message by its closing
line, and its frame line as the nearest line above it, after the message before, that
opens `[comms] ` at column zero and carries the same `msg <id>` bit. A body's lines are
indented, so no text in a body names a message, and a closing line with no frame line for
it names none. The text may hold more than the digest (the words a harness puts around a
delivered prompt), which is read past.

Standard library only.
"""

from __future__ import annotations

import re
from typing import NamedTuple

#: The line that closes one delivered message.
END_LINE = "[comms] end · msg {message_id}"

_END = re.compile(r"^\[comms\] end · msg (\S+)\r?$", re.MULTILINE)


class DeliveredMessage(NamedTuple):
    """One message a delivered text holds: comms' id for it, and where it runs in the
    text, from the start of its frame line (`start`) to the end of its closing line
    (`end`, exclusive)."""

    message_id: str
    start: int
    end: int


def read(text: str) -> tuple[DeliveredMessage, ...]:
    """Each message `text` holds as comms delivered it, in order. Never raises: a text
    that is not a string, or holds no framed message, gives none."""
    if not isinstance(text, str):
        return ()
    out: list[DeliveredMessage] = []
    after = 0
    for end in _END.finditer(text):
        mid = end.group(1)
        frame = re.compile(rf"^\[comms\] (?!end · ).*? · msg {re.escape(mid)}(?: · .*)?\r?$",
                           re.MULTILINE)
        found = None
        for found in frame.finditer(text, after, end.start()):
            pass
        if found is not None:
            out.append(DeliveredMessage(mid, found.start(), end.end()))
            after = end.end()
    return tuple(out)


__all__ = ["END_LINE", "DeliveredMessage", "read"]
