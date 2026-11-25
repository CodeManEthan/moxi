"""A recall's holder: the process that holds a recall until its fork's id is written, named
on its record from the record's first write, and the clocks the guards measure a waiting
record by.

**The holder (`holder`, `alive`).** A recall's record names the process that holds the
recall (`holder`): the detached starter the listener or the on-demand command started,
then the child the starter started, then the child itself once it writes its log line.
Each names the next on the record, under the session's lock, before it lets the recall
go. A holder is its pid and start time (`moxi.proc.identity`), and on a system that
keeps a process's start as a count of clock ticks since boot (Linux) also that count and
the boot's id (`ticks`, `boot`).

`alive` is `moxi.proc.alive`, which compares the start time read now with the one
recorded, within a second. On Linux that start time is the boot time plus the ticks, and
the boot time read moves when the wall clock steps, so after a step of more than a
second a live holder reads as gone. There a holder that names its ticks is also alive
when, in the same boot, a running process with its pid started at the same tick, which
no clock step moves. On a system with no such count `alive` stands alone.

**The age a waiting record is measured by (`age`).** From the record's start: on a
system with a clock that counts from boot and no wall-clock step moves (Linux's
`CLOCK_BOOTTIME`), by that clock, read at the record's first write (`at_boot`) and now,
in the same boot; else by the wall clock, from the record's `at`, an `at` later than now
read as now (the writer that holds the session's lock clamps it, `moxi_memory.records`).

Standard library at import; the core is imported at use.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Mapping

#: The fields a holder holds.
HOLDER_FIELDS = ("pid", "started", "ticks", "boot")

_LINUX = sys.platform.startswith("linux")
_BOOT_ID = "/proc/sys/kernel/random/boot_id"


def _boot_id() -> str | None:
    if not _LINUX:
        return None
    try:
        with open(_BOOT_ID, encoding="ascii") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def _ticks(pid: int) -> tuple[int, str] | None:
    """(process `pid`'s start in clock ticks since boot, its state letter), or None
    where the system keeps no such count or the process can't be read."""
    if not _LINUX or pid <= 0:
        return None
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        fields = text[text.rindex(")") + 2:].split()
        return int(fields[19]), fields[0]
    except (OSError, ValueError, IndexError):
        return None


def holder(pid: int | None) -> dict | None:
    """Process `pid` as a record names its holder, or None when it can't be read."""
    import moxi.proc

    if not isinstance(pid, int) or isinstance(pid, bool):
        return None
    found = moxi.proc.identity(pid)
    if found is None:
        return None
    out: dict = {"pid": found.pid, "started": found.started}
    ticks, boot = _ticks(found.pid), _boot_id()
    if ticks is not None and boot is not None:
        out["ticks"], out["boot"] = ticks[0], boot
    return out


def me() -> dict | None:
    """This process, as a record names its holder."""
    return holder(os.getpid())


def named(value: object) -> bool:
    """Whether `value` names a holder: a pid and a start time."""
    if not isinstance(value, Mapping):
        return False
    try:
        int(value["pid"])
        float(value["started"])
    except (KeyError, TypeError, ValueError):
        return False
    return True


def alive(value: Mapping[str, object]) -> bool:
    """Whether the holder `value` names still runs. See the module's docstring."""
    import moxi.proc

    if not named(value):
        return False
    pid = int(value["pid"])  # type: ignore[arg-type]
    if moxi.proc.alive(moxi.proc.ProcId(pid, float(value["started"]))):  # type: ignore[arg-type]
        return True
    ticks, boot = value.get("ticks"), value.get("boot")
    if not isinstance(ticks, int) or not isinstance(boot, str) or boot != _boot_id():
        return False
    found = _ticks(pid)
    return found is not None and found[0] == ticks and found[1] not in ("Z", "X", "x")


def boot_now() -> dict | None:
    """The clock that counts from boot, read now, with the boot's id, or None where the
    system has none."""
    clock = getattr(time, "CLOCK_BOOTTIME", None)
    boot = _boot_id()
    if clock is None or boot is None:
        return None
    try:
        return {"boot": boot, "s": time.clock_gettime(clock)}
    except OSError:
        return None


def age(record: Mapping[str, object], *, now: float | None = None,
        boot: dict | None = None) -> float:
    """How long ago `record`'s recall started, in seconds. See the module's docstring.
    `boot` is `boot_now()` when the caller has read it; with `now` given and no `boot`,
    only the wall clock is read."""
    at_boot = record.get("at_boot")
    if boot is None and now is None:
        boot = boot_now()
    if (isinstance(at_boot, Mapping) and boot is not None
            and at_boot.get("boot") == boot.get("boot")
            and isinstance(at_boot.get("s"), (int, float))):
        return max(float(boot["s"]) - float(at_boot["s"]), 0.0)
    now = time.time() if now is None else now
    try:
        at = float(record.get("at"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        at = now
    return max(now - at, 0.0)


__all__ = ["HOLDER_FIELDS", "holder", "me", "named", "alive", "boot_now", "age"]
