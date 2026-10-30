"""The errors services raises.

Each is a `ServicesError` carrying `reason`, `fix` (the command or step that fixes it, or
None) and `exit_code`, the code a command exits with on it (the core's `moxi.exit`):

- `Refused` (exit 3): declined on what was found: a name taken, an unknown id, a project
  key that names no project, a unit in moxi's own namespace.
- `Usage` (exit 2): asked wrongly.
- `Unreadable` (exit 1): the store exists and can't be read or written.
- `TooNew` (exit 3): the store was written by a newer services; it is also the core's
  `moxi.store.StoreTooNew`.
- `Busy` (exit 75): the store, or something services waits on, stayed locked past the
  wait; trying again may work.
"""

from __future__ import annotations

import moxi.exit
import moxi.store


class ServicesError(Exception):
    """Something services can't do as asked."""

    exit_code = moxi.exit.ERROR

    def __init__(self, reason: str, fix: str | None = None):
        self.reason = reason
        self.fix = fix
        super().__init__(f"{reason}; {fix}" if fix else reason)


class Refused(ServicesError):
    """Declined on what was found."""

    exit_code = moxi.exit.REFUSED


class Usage(ServicesError):
    """Asked wrongly."""

    exit_code = moxi.exit.USAGE


class Unreadable(ServicesError):
    """The store exists and can't be read or written. `path` is its file."""

    exit_code = moxi.exit.ERROR

    def __init__(self, reason: str, fix: str | None = None, path: str | None = None):
        self.path = path
        super().__init__(reason, fix)


class TooNew(Unreadable, moxi.store.StoreTooNew):
    """The store was written by a newer services than this one. A caller that catches
    `Unreadable` catches it, and so does one that catches `moxi.store.StoreTooNew`."""

    exit_code = moxi.exit.REFUSED

    def __init__(self, path: str, version: int, known: int):
        moxi.store.StoreTooNew.__init__(self, path, version, known)
        self.path = path
        self.reason = (f"the services store {path} is at schema version {version}, newer "
                       f"than the {known} this version of moxi-services knows")
        self.fix = "upgrade it with `moxi install services`"
        self.args = (f"{self.reason}; {self.fix}",)


class Busy(Unreadable):
    """Locked by another process past the wait. Trying again may work."""

    exit_code = moxi.exit.TEMPFAIL

    def __init__(self, reason: str | None = None, path: str | None = None):
        super().__init__(reason or "the services store stayed locked by another process "
                         "past the wait", "try again in a moment", path)


__all__ = ["ServicesError", "Refused", "Usage", "Unreadable", "TooNew", "Busy"]
