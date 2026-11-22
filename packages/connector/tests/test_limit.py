"""A provider's `Limit`: at most two calls at once, a third waits for a place then refuses
`busy`, a call past its time refuses `timeout`, and a thread that outlives its call holds
its place until it ends."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from kit import bearer, call, provider, request

from moxi_connector.provider import (BUSY, LIMIT_AT_ONCE, LIMIT_CALL_S, LIMIT_WAIT_S,
                                     TIMEOUT, Limit, Result, Tool)


class Gate:
    """Work that blocks until the test opens it, counting how many run at once."""

    def __init__(self):
        self.opened = threading.Event()
        self.lock = threading.Lock()
        self.running = 0
        self.most = 0
        self.ended = 0

    def work(self, answer: str = "done"):
        def run() -> Result:
            with self.lock:
                self.running += 1
                self.most = max(self.most, self.running)
            try:
                assert self.opened.wait(10), "the test never opened the gate"
                return Result(answer)
            finally:
                with self.lock:
                    self.running -= 1
                    self.ended += 1
        return run


def wait_until(found, within: float = 5.0) -> None:
    end = time.monotonic() + within
    while not found():
        assert time.monotonic() < end, "never happened"
        time.sleep(0.005)


async def settle() -> None:
    """Let the loop run the hand-overs threads scheduled."""
    for _ in range(5):
        await asyncio.sleep(0.01)


def test_the_defaults_are_two_at_once_a_five_second_wait_and_twenty_seconds():
    limit = Limit()
    assert (limit.at_once, limit.wait_s, limit.call_s) == (2, 5.0, 20.0)
    assert (LIMIT_AT_ONCE, LIMIT_WAIT_S, LIMIT_CALL_S) == (2, 5.0, 20.0)
    assert BUSY == "Another read is running; try again in a moment."
    with pytest.raises(ValueError):
        Limit(at_once=0)


def test_two_run_at_once_and_a_third_waits_for_a_place():
    gate = Gate()
    limit = Limit(wait_s=5.0, call_s=10.0)

    async def main():
        calls = [asyncio.ensure_future(limit.run(gate.work(str(n)))) for n in range(3)]
        await asyncio.to_thread(wait_until, lambda: gate.running == 2)
        await asyncio.sleep(0.05)
        assert gate.running == 2 and not any(c.done() for c in calls)
        gate.opened.set()
        return [(await c).text for c in calls]

    assert asyncio.run(main()) == ["0", "1", "2"]
    assert gate.most == 2 and limit.held == 0


def test_a_third_call_with_no_place_in_time_refuses_busy():
    gate = Gate()
    limit = Limit(wait_s=0.1, call_s=10.0)

    async def main():
        first = [asyncio.ensure_future(limit.run(gate.work())) for _ in range(2)]
        await asyncio.to_thread(wait_until, lambda: gate.running == 2)
        third = await limit.run(gate.work())
        gate.opened.set()
        await asyncio.gather(*first)
        return third

    third = asyncio.run(main())
    assert third.is_error and third.refusal == "busy" and third.text == BUSY
    assert gate.ended == 2 and limit.held == 0


def test_a_call_past_its_time_refuses_timeout_and_its_thread_holds_the_place():
    gate = Gate()
    limit = Limit(at_once=1, wait_s=0.1, call_s=0.2)

    async def main():
        late = await limit.run(gate.work())
        assert late.refusal == "timeout" and late.text == TIMEOUT and late.is_error
        assert limit.held == 1 and gate.running == 1
        busy = await limit.run(gate.work())
        assert busy.refusal == "busy"
        gate.opened.set()
        await asyncio.to_thread(wait_until, lambda: gate.ended == 1)
        await settle()
        assert limit.held == 0
        return await limit.run(lambda: Result("served"))

    assert asyncio.run(main()).text == "served"


def test_the_time_counts_from_the_calls_start_its_wait_included():
    gate = Gate()
    limit = Limit(at_once=1, wait_s=5.0, call_s=0.3)

    async def main():
        first = asyncio.ensure_future(limit.run(gate.work()))
        await asyncio.to_thread(wait_until, lambda: gate.running == 1)
        started = time.monotonic()
        second = asyncio.ensure_future(limit.run(lambda: Result("second")))
        await asyncio.sleep(0.2)
        gate.opened.set()             # the place frees with 0.1 s of the second's time left
        got = await second
        await first
        return got, time.monotonic() - started

    got, took = asyncio.run(main())
    assert got.text == "second" and took < 1.0


def test_a_caller_that_hangs_up_leaves_the_thread_its_place_until_it_ends():
    gate = Gate()
    limit = Limit(at_once=1, wait_s=0.05, call_s=10.0)

    async def main():
        running = asyncio.ensure_future(limit.run(gate.work()))
        await asyncio.to_thread(wait_until, lambda: gate.running == 1)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert limit.held == 1
        assert (await limit.run(gate.work())).refusal == "busy"
        gate.opened.set()
        await asyncio.to_thread(wait_until, lambda: gate.ended == 1)
        await settle()
        assert limit.held == 0

    asyncio.run(main())


def test_a_waiter_that_hangs_up_takes_no_place():
    gate = Gate()
    limit = Limit(at_once=1, wait_s=5.0, call_s=10.0)

    async def main():
        first = asyncio.ensure_future(limit.run(gate.work("first")))
        await asyncio.to_thread(wait_until, lambda: gate.running == 1)
        waiter = asyncio.ensure_future(limit.run(lambda: Result("never")))
        await asyncio.sleep(0.05)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        gate.opened.set()
        assert (await first).text == "first"
        await settle()
        assert limit.held == 0
        return await limit.run(lambda: Result("next"))

    assert asyncio.run(main()).text == "next"


def test_an_exception_in_the_work_comes_out_of_run_and_frees_the_place():
    limit = Limit(at_once=1)

    def broken() -> Result:
        raise KeyError("lost")

    async def main():
        with pytest.raises(KeyError):
            await limit.run(broken)
        await settle()
        return await limit.run(lambda: Result("after"))

    assert asyncio.run(main()).text == "after"
    assert limit.held == 0


def test_one_limit_serves_one_event_loop_after_another():
    limit = Limit()
    for n in range(3):
        assert asyncio.run(limit.run(lambda n=n: Result(str(n)))).text == str(n)
    assert limit.held == 0


# -- through the door ----------------------------------------------------------------------

def test_a_limited_tools_refusals_reach_the_caller(front, fake):
    gate = Gate()
    limit = Limit(at_once=1, wait_s=0.05, call_s=0.2)

    async def read(args, ctx):
        result = await limit.run(gate.work())
        ctx.log(refusal=result.refusal)
        return result

    async def main():
        f = await front(providers={"demo": provider(fake, Tool("read", "x", {}, read))})
        got = (await request(f, "POST", "/mcp", call("read"), headers=bearer())).json()
        assert got["result"]["isError"]
        assert got["result"]["structuredContent"] == {"refusal": "timeout"}
        assert got["result"]["content"][0]["text"] == TIMEOUT
        got = (await request(f, "POST", "/mcp", call("read"), headers=bearer())).json()
        assert got["result"]["structuredContent"] == {"refusal": "busy"}
        assert f.host.calllog.rows[-1]["refusal"] == "busy"
        gate.opened.set()
        await f.close()

    asyncio.run(main())
