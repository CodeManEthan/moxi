"""The launcher's own doctor checks, `rows()`, which the announcement's `DOCTOR` names.

The core already checks the launcher's settings and its declared stores. The launcher's
own rows come with the parts they check: the flag catalog's `--help` check is one.
"""

from __future__ import annotations

from collections.abc import Iterator

from moxi.doctor import Row

MODULE = "launcher"


def rows() -> Iterator[Row]:
    """Every row the launcher adds to `moxi doctor`; none yet."""
    yield from ()
