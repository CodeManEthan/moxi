"""sessions' setup, the announcement's `SETUP`: what `moxi install sessions` runs.

It prints how long each harness keeps the transcripts sessions reads (`retention.lines()`),
since every reader of sessions loses what a harness deletes, and says how to keep them
longer. It asks nothing and changes no setting, so it can run again at any time.
"""

from __future__ import annotations

from moxi import exit as codes

from .retention import lines


def setup() -> int:
    """Print each harness's retention lines; 0."""
    for line in lines():
        print(f"sessions: {line}")
    return codes.OK


__all__ = ["setup"]
