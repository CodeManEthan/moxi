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


class NotReady(Refused):
    """A declaration's `upstream()` says its target can't be applied yet: the act that
    asked applies nothing. `reason` is the text the callable gave."""


class ApplyError(TunnelError):
    """A provider command failed. `reason` is its stderr, unchanged."""

    def __init__(self, stderr: str, fix: str | None = None):
        super().__init__(stderr, fix)


class ReadError(TunnelError):
    """A provider's state could not be read. `timeout` is True when the read did not
    answer in time."""

    def __init__(self, reason: str, fix: str | None = None, *, timeout: bool = False):
        super().__init__(reason, fix)
        self.timeout = timeout


class UsageError(TunnelError):
    """Options that don't go together."""

    exit_code = codes.USAGE


class Busy(TunnelError):
    """Trying again may work: what the user confirmed changed twice under the lock, or the
    foreign config a replace takes over changed after the user confirmed."""

    exit_code = codes.TEMPFAIL


__all__ = ["TunnelError", "Refused", "NotReady", "ApplyError", "ReadError", "UsageError", "Busy"]
