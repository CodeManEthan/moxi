"""The scheduled tick's numbers, which the copies of it on each machine share with the
code that reads what they write.

A tick runs from a copy of this file that `moxi-dev timed schedule` puts in the machine's
run directory, so it uses the standard library only. `TICK_VERSION` is raised with any
change to this file; each tick file records it, and `moxi-dev timed show` prints a
machine's newest tick's version beside this one, marked `run schedule again` when older.
`TICK_FORMAT` is the tick files' format and `STAMP_FORMAT` the stamp's, each written in
its file's `format` field.
"""

from __future__ import annotations

TICK_VERSION = 1
TICK_FORMAT = 1
STAMP_FORMAT = 1
#: The stamp, at the source tree's root: the commit a synced tree holds, and every
#: tracked path's mode and blob hash.
STAMP_NAME = ".moxi-dev-stamp.json"
