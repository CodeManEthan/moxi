"""The one shape for a Moxi program that runs on asyncio: `moxi.aio.run(main)`.

`run` makes the event loop (the proactor loop on Windows), gives it one thread pool as its
default executor, starts the slow-callback guard, runs `main()` to completion and shuts the
pool down. `offload(fn, *args)` runs blocking work on that pool. With the default
`workers=1` every offloaded call runs on the same one thread, in order, so a daemon's
database connection is made and used on one thread.

The guard is a task that sleeps 50 ms and measures how late it woke. When the loop was
held longer than `slow` seconds it logs one warning on the logger `moxi.aio` ("the loop
was blocked for 0.42 s"), at most once every ten seconds. It needs no asyncio debug mode,
which slows every callback.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import sys

TYPE_CHECKING = False
if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine
    from typing import Any, TypeVar

    T = TypeVar("T")

#: How long the guard sleeps between two looks at the loop.
_TICK = 0.05
#: The guard logs at most one warning in this many seconds.
_QUIET = 10.0

_log = logging.getLogger("moxi.aio")


def run(main: Callable[[], Coroutine[Any, Any, T]], *, workers: int = 1,
        slow: float = 0.1) -> T:
    """Run `main()` on a new event loop and return what it returns.

    The loop's default executor is a pool of `workers` threads, which `offload` uses and
    which is shut down, its running calls finished, before `run` returns. A callback that
    holds the loop longer than `slow` seconds is logged on `moxi.aio`. An exception from
    `main()` is raised from `run`; a Ctrl-C cancels `main()` and raises
    `KeyboardInterrupt`, as `asyncio.run` does.
    """
    if not callable(main):
        raise TypeError(f"run takes a function that returns a coroutine, not {main!r}")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError(f"workers must be a whole number of at least 1, not {workers!r}")
    if isinstance(slow, bool) or not isinstance(slow, (int, float)) or not slow > 0:
        raise ValueError(f"slow must be a number of seconds above 0, not {slow!r}")
    from concurrent.futures import ThreadPoolExecutor

    factory = asyncio.ProactorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=factory) as runner:
        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="moxi-aio")
        # The runner's close shuts the default executor down and waits for it.
        runner.get_loop().set_default_executor(executor)
        return runner.run(_guarded(main, float(slow)))


def offload(fn: Callable[..., T], *args: object) -> Awaitable[T]:
    """Run `fn(*args)` on the running loop's executor (the pool `run` made) and return
    an awaitable of its result. The call sees the caller's context variables."""
    loop = asyncio.get_running_loop()
    call = functools.partial(contextvars.copy_context().run, fn, *args)
    return loop.run_in_executor(None, call)


async def _guarded(main: Callable[[], Coroutine[Any, Any, T]], slow: float) -> T:
    guard = asyncio.get_running_loop().create_task(_guard(slow), name="moxi.aio guard")
    try:
        return await main()
    finally:
        guard.cancel()
        try:
            await guard
        except asyncio.CancelledError:
            pass


async def _guard(slow: float) -> None:
    """Sleep `_TICK` at a time and log when a wake comes more than `slow` late."""
    loop = asyncio.get_running_loop()
    last = None
    while True:
        start = loop.time()
        await asyncio.sleep(_TICK)
        now = loop.time()
        lag = now - start - _TICK
        if lag > slow and (last is None or now - last >= _QUIET):
            last = now
            _log.warning("the loop was blocked for %.2f s", lag)


__all__ = ["run", "offload"]
