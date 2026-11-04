"""Whose process is at the other end of a named pipe, on Windows.

Windows has no user ids to compare, so both ends of a pipe compare user names, read
through psutil as `DOMAIN\\name`: the server checks the client that connected, and the
client checks the server it opened. A user that can't be read never counts as this one.
psutil is imported on first use.
"""

from __future__ import annotations

import os


def user_of(pid: int) -> str | None:
    """The user process `pid` runs as, `DOMAIN\\name`, or None when it can't be read."""
    import psutil

    try:
        return psutil.Process(pid).username()
    except psutil.Error:
        return None


_me: list[str | None] = []


def own_user() -> str | None:
    """The user this process runs as, read as `user_of` reads another's."""
    if not _me:
        _me.append(user_of(os.getpid()))
    return _me[0]


def is_own(user: str | None) -> bool:
    """Whether `user`, as `user_of` read it, is the user this process runs as. None (a
    user that couldn't be read) never is."""
    return user is not None and user == own_user()
