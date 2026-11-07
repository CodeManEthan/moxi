"""Moxi's core.

Importing it notes whether this process started with the start mark, `MOXI_STARTED_BY`,
and sets the mark when it didn't, so every process a Moxi program starts carries it
(`moxi._mark`).
"""

from . import _mark

_mark.note()
