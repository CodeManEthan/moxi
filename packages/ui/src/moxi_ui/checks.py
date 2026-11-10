"""The kept doctor run: the last whole `moxi doctor --json` the shell ran, and its time.

The shell holds one `Checks`. It keeps the last run's rows, error and time in memory and
in `checks.json` in the shell's state directory (written atomically, read at the start),
so a shell started again shows the last run at once.

A run starts only when it is asked for: `ask()` starts one when no run is kept or the
kept one is older than `STALE_AFTER`, and `run_now()` starts one whatever its age. One
run goes at a time, as one task; a second ask meanwhile gets what is kept with `running`
true. The task ends when the doctor ends or passes its time limit. `aclose()` cancels it,
which kills the doctor and everything it started. A cancelled run keeps nothing: the
earlier kept run stands. A run that ended without rows (past its time, no answer) is
kept, as its `error`.

`value()` is what the route and the topic `ui.checks` carry: `{at, running, rows, error}`.
`changed(value)` is called when a run starts and when it ends.

With `enabled` false nothing is ever started: both asks answer with what is kept. Test
shells run that way unless a test asks for the runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from moxi import files

__all__ = ["CHECKS_FILE", "STALE_AFTER", "Checks", "read_kept"]

#: The kept run's file, in the shell's state directory.
CHECKS_FILE = "checks.json"
#: Seconds after which a kept run is too old to answer an ask alone.
STALE_AFTER = 300.0
_AT = "%Y-%m-%dT%H:%M:%SZ"
_ROW_TEXT = ("module", "name", "state", "detail")
_log = logging.getLogger("moxi.ui.checks")


def _rows(listed: object) -> list[dict[str, Any]] | None:
    """`listed` as kept rows, or None when it is not a list of doctor rows."""
    if not isinstance(listed, list):
        return None
    rows = []
    for item in listed:
        if not isinstance(item, dict) or not all(isinstance(item.get(key), str)
                                                 for key in _ROW_TEXT):
            return None
        fix = item.get("fix")
        rows.append({**{key: item[key] for key in _ROW_TEXT},
                     "fix": fix if isinstance(fix, str) else None})
    return rows


def _seconds(at: object) -> float | None:
    """The time `at` names, as seconds since the epoch, or None when it names none."""
    if not isinstance(at, str):
        return None
    try:
        return datetime.strptime(at, _AT).replace(tzinfo=UTC).timestamp()
    except ValueError:
        return None


def read_kept(path: str | os.PathLike) -> dict[str, Any] | None:
    """The run kept in the file at `path`, `{at, rows, error}`, or None when there is no
    file or it does not hold one."""
    try:
        data = json.loads(Path(path).read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    rows, error = _rows(data.get("rows")), data.get("error")
    if rows is None or _seconds(data.get("at")) is None:
        return None
    if error is not None and not isinstance(error, str):
        return None
    return {"at": data["at"], "rows": rows, "error": error}


class Checks:
    """The shell's kept doctor run. See the module docstring.

    `path` is the kept run's file. `run()` runs the doctor to its end and gives
    `{"rows": [...], "error": <text or None>}`, as the doctor route answers. `changed`
    hears each new value. `clock` gives the time in seconds since the epoch.

    Everything but the file's write runs on the shell's loop.
    """

    def __init__(self, path: str | os.PathLike, *,
                 run: Callable[[], Awaitable[dict[str, Any]]],
                 changed: Callable[[dict[str, Any]], Any] | None = None,
                 enabled: bool = True, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.run = run
        self.changed = changed
        self.enabled = enabled
        self.clock = clock
        self.kept = read_kept(self.path)
        self.runs = 0
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        """Whether a run is going now."""
        return self._task is not None

    def value(self) -> dict[str, Any]:
        """The kept run and whether one is going: `{at, running, rows, error}`. `at` is
        None, with no rows, while no run is kept."""
        kept = self.kept or {"at": None, "rows": [], "error": None}
        return {"at": kept["at"], "running": self.running, "rows": list(kept["rows"]),
                "error": kept["error"]}

    def stale(self) -> bool:
        """Whether no run is kept, or the kept one is older than `STALE_AFTER` (or dated
        after now, as after a clock set back)."""
        made = _seconds(self.kept["at"]) if self.kept else None
        return made is None or not 0 <= self.clock() - made <= STALE_AFTER

    def ask(self) -> dict[str, Any]:
        """The value, after starting a run when the kept one is stale."""
        if self.stale():
            self.run_now()
        return self.value()

    def run_now(self) -> dict[str, Any]:
        """The value, after starting a run unless one is going. Call on the shell's
        loop."""
        if self.enabled and self._task is None:
            self.runs += 1
            self._task = asyncio.ensure_future(self._running())
            self._tell()
        return self.value()

    async def aclose(self) -> None:
        """Cancel the run that is going and wait for its doctor to be gone."""
        self.enabled = False
        task = self._task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _running(self) -> None:
        from moxi import aio

        try:
            try:
                answer = await self.run()
                rows = _rows(answer.get("rows"))
                error = answer.get("error")
                if rows is None or not (error is None or isinstance(error, str)):
                    rows, error = [], "moxi doctor gave rows the shell can't read"
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - a failed run is kept as its error
                _log.exception("the kept doctor run failed")
                rows, error = [], f"moxi doctor could not run: {type(err).__name__}"
            at = datetime.fromtimestamp(self.clock(), UTC).strftime(_AT)
            self.kept = kept = {"at": at, "rows": rows, "error": error}
            try:
                await aio.offload(files.atomic_write, self.path, json.dumps(kept))
            except OSError:
                _log.exception("the kept doctor run can't be written")
        finally:
            self._task = None
            self._tell()

    def _tell(self) -> None:
        if self.changed is None:
            return
        try:
            self.changed(self.value())
        except Exception:  # noqa: BLE001 - a failed publish leaves the run as it is
            _log.exception("publishing the kept doctor run failed")
