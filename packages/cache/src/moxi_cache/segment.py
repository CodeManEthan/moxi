"""The status line segment: a session's cache age, read from its clock record.

**The segment shows status and triggers nothing.** It reads one record and returns
text. It starts no process, writes no file and asks nothing.

`segment(session_id, now=None)` gives a `Segment` or None:

* `text`, `cache:<age>`: `12m3s` under an hour, `1h4m` from the hour;
* `band`: `warm`, `near` from `ttl - warn_before`, `cold` from `ttl`;
* `age_s`, the seconds since the session's last request ended, and `ttl_s`, the lifetime
  the clock counted;
* `dim`: the record's `seen_at` is older than its `stale_after` (three of the watcher's
  ticks), which means the watcher has stopped. An idle session's record keeps its old
  `read_at`, stays bright while the watcher runs, and its age counts on.

`warn_before` and `stale_after` are the record's own (`records`): the watcher
writes them at every tick from cache's config, and the segment reads no config. None when
there is no record, or its kind is neither `warm` nor `cold`, or it holds no last request
or lifetime to count, or it lacks `warn_before` or `stale_after` (an older watcher wrote
it; the next tick rewrites it). The age is counted from the record's `last_request` at
`now`, so a record read warm shows `near` and then `cold` as time passes without a new
read.

This module imports only the standard library and the core (through `records`), so a
renderer can load it alone. It loads nothing a render doesn't run: `Segment` is a plain
class rather than a dataclass, since `dataclasses` alone would add several milliseconds
to every render. `moxi cache segment` loads it inside the core's render audit
hook (`moxi.render`).
"""

from __future__ import annotations

import time

from . import records

WARM = "warm"
NEAR = "near"
COLD = "cold"
BANDS = (WARM, NEAR, COLD)

#: The status line's colours: green, yellow, red, and dim.
COLOURS = {WARM: "\033[32m", NEAR: "\033[33m", COLD: "\033[31m"}
DIM = "\033[2m"
RESET = "\033[0m"


class Segment:
    """One session's segment: what to show and how. Immutable: its fields can't be set
    or deleted after it is made. Two segments are equal when every field is."""

    __slots__ = ("text", "band", "age_s", "ttl_s", "dim")

    def __init__(self, text: str, band: str, age_s: float, ttl_s: float,
                 *, dim: bool = False) -> None:
        for name, value in zip(self.__slots__, (text, band, age_s, ttl_s, dim)):
            object.__setattr__(self, name, value)

    def _fields(self) -> tuple:
        return (self.text, self.band, self.age_s, self.ttl_s, self.dim)

    def __setattr__(self, name, value):
        raise AttributeError(f"cannot assign to field {name!r}: a Segment is immutable")

    def __delattr__(self, name):
        raise AttributeError(f"cannot delete field {name!r}: a Segment is immutable")

    def __eq__(self, other):
        if other.__class__ is not self.__class__:
            return NotImplemented
        return self._fields() == other._fields()

    def __hash__(self) -> int:
        return hash(self._fields())

    def __repr__(self) -> str:
        return (f"Segment(text={self.text!r}, band={self.band!r}, age_s={self.age_s!r}, "
                f"ttl_s={self.ttl_s!r}, dim={self.dim!r})")

    def __reduce__(self):
        return (Segment, self._fields())

    def as_dict(self) -> dict:
        return {"text": self.text, "band": self.band, "age_s": self.age_s,
                "ttl_s": self.ttl_s, "dim": self.dim}

    def coloured(self) -> str:
        """The text in its band's colour, dimmed when the watcher has stopped."""
        return f"{DIM if self.dim else ''}{COLOURS[self.band]}{self.text}{RESET}"


def age_text(seconds: float) -> str:
    """`0m45s`, `12m3s` under an hour; `1h0m`, `26h5m` from the hour."""
    total = int(max(0.0, seconds))
    if total >= 3600:
        return f"{total // 3600}h{(total % 3600) // 60}m"
    return f"{total // 60}m{total % 60}s"


def from_record(record: dict, now: float) -> Segment | None:
    """The segment one record shows at `now`, by the record's own `warn_before` and
    `stale_after`."""
    if not records.valid(record) or record["kind"] not in (WARM, COLD):
        return None
    found = records.thresholds(record)
    if found is None:
        return None
    warn_before, stale_after = found
    last, ttl = record["last_request"], record["ttl"]
    if last is None or ttl is None:
        return None
    age = max(0.0, now - last)
    if age >= ttl:
        band = COLD
    elif age >= max(0.0, ttl - warn_before):
        band = NEAR
    else:
        band = WARM
    dim = now - record["seen_at"] > stale_after
    return Segment(f"cache:{age_text(age)}", band, age, float(ttl), dim=dim)


def segment(session_id, now: float | None = None) -> Segment | None:
    """The segment of one session, from its clock record."""
    path = records.record_path(session_id)
    if path is None:
        return None
    record = records.load(path)
    if record is None or record["session"] != session_id:
        return None
    return from_record(record, time.time() if now is None else now)


__all__ = ["Segment", "segment", "from_record", "age_text", "BANDS", "WARM", "NEAR", "COLD"]
