"""comms' own doctor checks (the announcement's `DOCTOR`)."""

from __future__ import annotations


def doctor_rows(env=None) -> list:
    """The move of comms 0.2's files and what it left, then the reserved names."""
    from . import reserved
    from .store import upgrade_0_2

    return upgrade_0_2.doctor_rows(env) + reserved.doctor_rows(env)


__all__ = ["doctor_rows"]
