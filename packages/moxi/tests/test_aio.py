"""moxi.aio: the one loop, its one executor, and the slow-callback guard."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import sys
import threading
import time

import pytest

from moxi import aio

WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="the proactor loop is Windows'")


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == "moxi.aio" and r.levelno == logging.WARNING]


def test_run_returns_what_main_returns():
    async def main():
        await asyncio.sleep(0)
        return 42

    assert aio.run(main) == 42


def test_an_exception_from_main_is_raised_from_run():
    async def main():
        raise LookupError("gone")

    with pytest.raises(LookupError, match="gone"):
        aio.run(main)


def test_run_takes_a_function_not_a_coroutine():
    async def main():
        return 1

    coroutine = main()
    try:
        with pytest.raises(TypeError, match="function that returns a coroutine"):
            aio.run(coroutine)
    finally:
        coroutine.close()


@pytest.mark.parametrize("workers", [0, -1, 1.5, True, "1"])
def test_workers_must_be_a_whole_number_of_at_least_one(workers):
    async def main():
        return None

    with pytest.raises(ValueError, match="workers"):
        aio.run(main, workers=workers)


@pytest.mark.parametrize("slow", [0, -0.1, True, "0.1"])
def test_slow_must_be_above_zero(slow):
    async def main():
        return None

    with pytest.raises(ValueError, match="slow"):
        aio.run(main, slow=slow)


def test_the_loop_runs_without_debug_mode():
    async def main():
        return asyncio.get_running_loop().get_debug()

    assert aio.run(main) is False


def test_offload_runs_on_one_worker_in_order():
    seen: list[tuple[str, int]] = []
    lock = threading.Lock()
    running = 0
    overlapped = False

    def work(n: int) -> int:
        nonlocal running, overlapped
        with lock:
            running += 1
            overlapped = overlapped or running > 1
        time.sleep(0.01)
        seen.append((threading.current_thread().name, n))
        with lock:
            running -= 1
        return n * 2

    async def main():
        return await asyncio.gather(*(aio.offload(work, n) for n in range(5)))

    assert aio.run(main) == [0, 2, 4, 6, 8]
    assert [n for _, n in seen] == [0, 1, 2, 3, 4]
    assert len({name for name, _ in seen}) == 1
    assert seen[0][0].startswith("moxi-aio")
    assert not overlapped


def test_offload_uses_as_many_workers_as_run_was_given():
    names: set[str] = set()
    barrier = threading.Barrier(2, timeout=5)

    def work() -> None:
        barrier.wait()  # two calls only pass this together, on two threads
        names.add(threading.current_thread().name)

    async def main():
        await asyncio.gather(aio.offload(work), aio.offload(work))

    aio.run(main, workers=2)
    assert len(names) == 2


def test_offload_sees_the_callers_context_variables():
    var = contextvars.ContextVar("var", default="unset")

    async def main():
        var.set("set in main")
        return await aio.offload(var.get)

    assert aio.run(main) == "set in main"


def test_run_shuts_its_executor_down_before_it_returns():
    threads: list[threading.Thread] = []

    def work() -> None:
        threads.append(threading.current_thread())

    async def main():
        await aio.offload(work)

    aio.run(main)
    assert threads and not threads[0].is_alive()


def test_a_blocked_loop_logs_one_warning(caplog):
    caplog.set_level(logging.WARNING, logger="moxi.aio")

    async def main():
        await asyncio.sleep(0.01)  # the guard is asleep
        time.sleep(0.3)
        await asyncio.sleep(0.1)  # the guard wakes late and looks

    aio.run(main, slow=0.1)
    found = _warnings(caplog)
    assert len(found) == 1
    assert found[0].startswith("the loop was blocked for 0.")
    assert found[0].endswith(" s")


def test_a_block_under_slow_logs_nothing(caplog):
    caplog.set_level(logging.WARNING, logger="moxi.aio")

    async def main():
        await asyncio.sleep(0.01)
        time.sleep(0.15)
        await asyncio.sleep(0.1)

    aio.run(main, slow=1.0)
    assert _warnings(caplog) == []


def test_the_warning_comes_at_most_once_in_ten_seconds(caplog):
    caplog.set_level(logging.WARNING, logger="moxi.aio")

    async def main():
        for _ in range(3):
            await asyncio.sleep(0.01)
            time.sleep(0.25)
            await asyncio.sleep(0.1)

    aio.run(main, slow=0.1)
    assert len(_warnings(caplog)) == 1


def test_past_the_quiet_time_the_guard_warns_again(caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger="moxi.aio")
    monkeypatch.setattr(aio, "_QUIET", 0.0)

    async def main():
        for _ in range(2):
            await asyncio.sleep(0.01)
            time.sleep(0.25)
            await asyncio.sleep(0.1)

    aio.run(main, slow=0.1)
    assert len(_warnings(caplog)) == 2


def test_the_guard_ends_with_main():
    async def main():
        return [t for t in asyncio.all_tasks() if t.get_name() == "moxi.aio guard"]

    guards = aio.run(main)
    assert len(guards) == 1 and guards[0].cancelled()


@WINDOWS
def test_run_uses_the_proactor_loop_on_windows():
    async def main():
        return type(asyncio.get_running_loop())

    assert issubclass(aio.run(main), asyncio.ProactorEventLoop)
