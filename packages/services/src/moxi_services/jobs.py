"""The page's acts as jobs: one thread that runs them, and the record of each.

An act from services' page never runs inside its request. It may wait 30 seconds for the
core lock and then up to 30 seconds on each supervisor call, while the browser shell gives
a page server's answer 30 seconds to start. So the page's handler checks the request,
`submit`s the act here and answers 202 at once; the page reads the job's record through
its status route until it ends.

- **One thread** runs the jobs, one at a time, in the order they came, as the core lock
  would order them anyway. It starts with the first job and stays for the process's life;
  a slow act never holds the page server's own threads.
- **A record** is kept per job id, in memory, for the page server's life: `running` from
  `submit`, then `done`, `failed` or `refused` with the act's exit code, lines and fix.
  The last `KEEP` jobs that ended are kept, and every running one; after the server stops,
  none is. A job id is submitted once: a second `submit` of an id held here queues
  nothing.
- **The idle exit.** Each job holds the server's idle exit (the hold the page passes in,
  loopback's `busy("act")`) from `submit` until it ends, so a job waiting behind another
  holds it too, and the server never stops between a 202 and the act.

This module imports nothing of loopback's: the page's handlers give it the hold.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

import moxi.exit

#: How many ended jobs are kept, the same count loopback keeps of finished keys.
KEEP = 1024
#: The states a job has; `unknown` is a status read's answer for an id with no record.
STATES = ("running", "done", "failed", "refused")
#: The busy hold's name.
HOLD = "act"
#: What an act that raised, rather than ending in its outcome, is recorded with.
RAISED = ("the act ended in an unexpected error ({}); the page server's log has the "
          "details, and the row's state shows what it did")
#: Exit codes that mean the act changed nothing: asked wrongly, refused, or busy.
_REFUSED = (moxi.exit.USAGE, moxi.exit.REFUSED, moxi.exit.TEMPFAIL)

log = logging.getLogger("moxi.services.page")


@dataclass(frozen=True)
class Job:
    """One job's record: its `job` id, the `act`, the row `id`, its `state`, and once it
    ended the exit `code`, the `lines` and the `fix`."""

    job: str
    act: str
    id: str | None
    state: str = "running"
    code: int | None = None
    lines: tuple[str, ...] = ()
    fix: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"job": self.job, "act": self.act, "id": self.id, "state": self.state,
                "code": self.code, "lines": list(self.lines), "fix": self.fix}


def state_of(code: int) -> str:
    """A job's end state from its act's exit code: 0 `done`; 2, 3 and 75 `refused`, since
    nothing was changed; anything else `failed`."""
    if code == moxi.exit.OK:
        return "done"
    if code in _REFUSED:
        return "refused"
    return "failed"


_STOP = object()


class Jobs:
    """The job thread and the records. `hold(name)` gives the context manager that holds
    the idle exit."""

    def __init__(self, hold: Callable[[str], contextlib.AbstractContextManager[Any]],
                 *, keep: int = KEEP):
        self._hold = hold
        self.keep = keep
        self._lock = threading.Lock()
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._thread: threading.Thread | None = None

    def get(self, job: str) -> Job | None:
        """The record of `job`, or None when there is none here."""
        with self._lock:
            return self._jobs.get(job)

    def submit(self, job: str, act: str, ident: str | None,
               work: Callable[[], Any]) -> tuple[Job, bool]:
        """Record `job` as running and queue `work`, which returns the act's `Outcome`.
        Returns the record and True; or, when `job` is held here already, its record and
        False, queueing nothing."""
        with self._lock:
            held = self._jobs.get(job)
            if held is not None:
                return held, False
            record = Job(job, act, ident)
            hold = contextlib.ExitStack()
            hold.enter_context(self._hold(HOLD))
            self._jobs[job] = record
            self._queue.put((job, work, hold))
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True,
                                                name="services jobs")
                self._thread.start()
            self._trim()
        return record, True

    def close(self) -> None:
        """End the thread once the jobs queued before it have run. For tests: a page
        server's thread lasts its life."""
        self._queue.put(_STOP)

    def _trim(self) -> None:
        ended = [j for j, r in self._jobs.items() if r.state != "running"]
        for job in ended[:max(0, len(ended) - self.keep)]:
            del self._jobs[job]

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            job, work, hold = item
            try:
                ended = self._ended(work)
                with self._lock:
                    self._jobs[job] = replace(self._jobs[job], **ended)
                    self._trim()
            finally:
                hold.close()

    @staticmethod
    def _ended(work: Callable[[], Any]) -> dict[str, Any]:
        try:
            outcome = work()
        except Exception as err:  # noqa: BLE001 - the job's record says so, never the thread
            log.error("a page job raised (%s)", type(err).__name__, exc_info=err)
            return {"state": "failed", "code": moxi.exit.ERROR,
                    "lines": (RAISED.format(type(err).__name__),), "fix": None}
        return {"state": state_of(outcome.code), "code": outcome.code,
                "lines": (*outcome.said, *outcome.lines), "fix": outcome.fix}


__all__ = ["HOLD", "KEEP", "RAISED", "STATES", "Job", "Jobs", "state_of"]
