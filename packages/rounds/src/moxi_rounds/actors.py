"""Who an act is recorded as done by, when nobody is named.

Every event a log holds names its `actor`. A verb run from the command line records the
name given with `--as`, else the OS user the command runs as; a page server that calls
the verbs' functions in process records the OS user it runs as. `default()` is that
one answer for both, so the two never record the same person under two names.
"""

from __future__ import annotations

import getpass

from .errors import BadArgument


def default() -> str:
    """The name of the OS user this process runs as. Raises `BadArgument` when that
    user has no name to give."""
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        raise BadArgument("the user this runs as has no name",
                          "run it as a user that has one") from None


__all__ = ["default"]
