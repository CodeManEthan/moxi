"""The exit codes every Moxi command uses.

A command exits `OK` when it did what was asked, `ERROR` when it failed, `USAGE` when its
arguments are wrong, `REFUSED` when it declined to act on what it found (a store newer
than this code, say), and `TEMPFAIL` when trying again later may work: a lock held past
the wait, or a daemon that isn't serving. 1 is also what Python exits with on an uncaught
exception, and 2 what `argparse` exits with on bad arguments, so both keep that meaning.
Richer outcomes go in a command's `--json` output, on top of these.
"""

OK = 0
ERROR = 1
USAGE = 2
REFUSED = 3
TEMPFAIL = 75

__all__ = ["OK", "ERROR", "USAGE", "REFUSED", "TEMPFAIL"]
