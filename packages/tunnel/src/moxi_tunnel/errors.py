"""What tunnel raises when it can't do what was asked.

Every error carries `reason` (what went wrong), `fix` (the command or step that fixes it,
or None) and `exit_code` (what a command exits with on it, from `moxi.exit`). A lock held
past the wait is the core's `moxi.files.LockTimeout`, whose `exit_code` is 75.
"""

from __future__ import annotations

from moxi import exit as codes


class TunnelError(Exception):
    """Something tunnel can't do as asked."""

    exit_code = codes.ERROR

    def __init__(self, reason: str, fix: str | None = None):
        self.reason = reason
        self.fix = fix
        super().__init__(f"{reason}; {fix}" if fix else reason)


class Refused(TunnelError):
    """The request breaks a rule, or something it needs is missing: nothing was changed."""

    exit_code = codes.REFUSED


__all__ = ["TunnelError", "Refused"]
