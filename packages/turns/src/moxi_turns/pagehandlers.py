"""The handlers of turns' page, and the feed that carries the live list to its stream.

One `pagereader.Reader` per page server keeps the list. It starts on the page's first read
or subscribe and runs until the page server stops: its scans and watches are not a request,
a stream or a busy hold, so they never delay loopback's idle exit, and that exit is what
stops them when no one has looked for the page's idle time. At process exit every watch is
closed.

- `GET /api/sessions` waits, on its worker thread, at most `FIRST_S` seconds for the
  reader's first scan and the first read of each session it found, then answers the list
  as it stands with the server's `now`. Later reads answer at once.
- `GET /api/events` streams the `sessions` topic. Each change the reader reports asks the
  topic's `Snapshot` for one rebuild, which publishes only when the list differs from the
  last one sent; the first value goes out once the first scan has finished.

Like `page`, this module reaches loopback only through `moxi.modules.load`.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import threading
import time
from typing import Any

from moxi import modules

from .page import TOPIC
from .pagereader import Reader

_loopback = modules.load("loopback", interface=1)
if not _loopback.ok:
    raise ImportError(f"turns' page needs the loopback module: {_loopback.reason}")
lb = _loopback.api

#: The longest the page's first read waits for the first scan and reads, in seconds.
FIRST_S = 5.0

log = logging.getLogger("moxi.turns.page")

_reader_lock = threading.Lock()
_reader_now: Reader | None = None


def _reader() -> Reader:
    """The page server's one reader, started on first use."""
    global _reader_now
    with _reader_lock:
        if _reader_now is None:
            _reader_now = Reader()
            _reader_now.start()
        return _reader_now


@atexit.register
def _stop_reader() -> None:
    with _reader_lock:
        reader = _reader_now
    if reader is not None:
        reader.stop()


def sessions(call: Any) -> dict[str, Any]:
    """`GET /api/sessions`: the list now, with the server's `now`."""
    reader = _reader()
    reader.wait_first(FIRST_S)
    return {"now": time.time(), **reader.value()}


async def events(call: Any) -> Any:
    """`GET /api/events`: the stream over the `sessions` topic."""
    return call.stream(_feed().topics)


class _Feed:
    """The topic, its Snapshot and the task that rebuilds it, on one event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop, reader: Reader):
        self.loop = loop
        self.reader = reader
        self.topics = lb.Topics(TOPIC)
        self.snapshot = lb.Snapshot(self._gather, self.topics, TOPIC)
        self.kick = asyncio.Event()
        self.kick.set()
        self.task = loop.create_task(self._run(), name="turns sessions feed")
        reader.listen(self._changed)

    async def _gather(self) -> dict[str, Any]:
        # The reader's lock is held only while rows are copied, so this runs on the loop.
        return self.reader.value()

    def _changed(self) -> None:
        # From a reader thread.
        try:
            self.loop.call_soon_threadsafe(self.kick.set)
        except RuntimeError:
            # This feed's loop has closed; its streams are gone with it.
            self.reader.unlisten(self._changed)

    async def _run(self) -> None:
        while True:
            await self.kick.wait()
            self.kick.clear()
            if not self.reader.listed:
                continue
            try:
                await self.snapshot.changed()
            except Exception as err:  # noqa: BLE001 - the next change rebuilds again
                log.warning("turns' page could not rebuild its topic: %s", err, exc_info=err)


_feed_now: _Feed | None = None


def _feed() -> _Feed:
    # A page server runs one loop for its life; a loop of its own per in-process test
    # gets a fresh feed, since a task and its Snapshot belong to one loop.
    global _feed_now
    loop = asyncio.get_running_loop()
    if _feed_now is None or _feed_now.loop is not loop:
        if _feed_now is not None:
            _feed_now.reader.unlisten(_feed_now._changed)
        _feed_now = _Feed(loop, _reader())
    return _feed_now


__all__ = ["FIRST_S", "events", "sessions"]
