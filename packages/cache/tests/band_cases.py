"""The band table, `band-cases.json`: one list of cases that both halves of the chip's band
rule run, the segment's here and the chip's in the browser, so the two can't drift.

Each case is `{"name", "record", "shows", "dim"}`: the record read at the table's `now`
shows `shows` (a band, a kind's word, or "no reading"), dimmed when `dim`.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

PATH = Path(__file__).with_name("band-cases.json")
_TABLE = json.loads(PATH.read_text(encoding="utf-8"))

#: The time every case is read at.
NOW: float = _TABLE["now"]
#: Every case, in the table's order.
CASES: list[dict] = _TABLE["cases"]
#: The times in a record that move with the clock.
TIMES = ("last_request", "read_at", "seen_at")


def shifted(record: dict, now: float) -> dict:
    """`record` as it would be written for a reading at `now` in place of `NOW`."""
    out = copy.deepcopy(record)
    for key in TIMES:
        if out.get(key) is not None:
            out[key] += now - NOW
    return out
