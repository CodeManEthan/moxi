"""What notes raises when it can't do what was asked.

Every error carries `reason` (what went wrong), `fix` (the command or step that fixes
it, or None) and `exit_code` (what a command exits with on it).
"""

from __future__ import annotations


class NotesError(Exception):
    """Something notes can't do as asked."""

    exit_code = 1

    def __init__(self, reason: str, fix: str | None = None):
        self.reason = reason
        self.fix = fix
        super().__init__(f"{reason}; {fix}" if fix else reason)


class Refused(NotesError):
    """The request breaks a rule: nothing was changed."""

    exit_code = 3


class BadSlug(Refused, ValueError):
    """An inbox slug that doesn't match the slug rule. Also a `ValueError`."""


__all__ = ["NotesError", "Refused", "BadSlug"]
