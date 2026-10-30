"""The one error a read raises when it cannot open or read its source: `Unreadable`.

Every harness's reader raises it, for a file and for OpenCode's database alike, so a
caller catches one class. It is an `OSError`, so a caller that already catches `OSError`
around a read keeps working, and it carries the reason in plain words as `reason`. A read
that raises it never stands for an empty read: an empty read is a source that was read and
held nothing.
"""

from __future__ import annotations


class Unreadable(OSError):
    """A read that cannot open or read its source, with the reason in plain words."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


__all__ = ["Unreadable"]
