"""moxi.ipc's Unix server at the file-descriptor limit: an accept failure on its listener
is logged once a minute per errno, with a count of the ones held back, and every other
exception context reaches the loop's own handler once and unchanged.

The failures are made by patching `socket.socket.accept` for the listener's fd only, so
asyncio's own accept loop meets them as it would at the limit."""

from __future__ import annotations

import asyncio
import errno
import gc
import json
import os
import re
import socket
import sys
import time
import weakref

import pytest

from moxi import ipc
from moxi.ipc import _server

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="Unix sockets; Windows pipes accept another way")


async def echo(conn, method, params):
    return {"method": method}


# The accepts asyncio tries on one readiness event: the listener's backlog (100, the
# default of `asyncio.start_unix_server`) and one more.
ONE_EVENT = 101


def fail_accepts(monkeypatch, server, count: int, *,
                 second_event_after_s: float = 0.0) -> list[int]:
    """Make the next `count` accepts on `server`'s listener raise EMFILE. Returns the
    one-item list of failures still to come.

    The clock the log's rate reads is frozen meanwhile, and moves on by
    `second_event_after_s` only at the first failure of asyncio's second accept event,
    so how far apart the two events are is set here, not by the machine's speed."""
    (listener,) = server._listener.sockets
    fd = listener.fileno()
    real = socket.socket.accept
    left = [count]
    clock = [1000.0]
    monkeypatch.setattr(_server, "_clock", lambda: clock[0])

    def accept(self):
        if self.fileno() == fd and left[0] > 0:
            left[0] -= 1
            if count - left[0] == ONE_EVENT + 1:
                clock[0] += second_event_after_s
            raise OSError(errno.EMFILE, os.strerror(errno.EMFILE))
        return real(self)

    monkeypatch.setattr(socket.socket, "accept", accept)
    return left


async def call(address: str) -> float:
    """Send one request to `address` and wait for its answer; the seconds it took."""
    started = time.monotonic()
    reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(address), 10)
    try:
        writer.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "x"}).encode() + b"\n")
        await writer.drain()
        answer = json.loads(await asyncio.wait_for(reader.readline(), 10))
        assert answer["result"] == {"method": "x"}
    finally:
        writer.close()
    return time.monotonic() - started


def accept_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == "moxi.ipc" and r.getMessage().startswith("accept failed")]


def asyncio_records(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "asyncio"]


def test_a_flood_of_accept_failures_logs_one_line(sock_dir, monkeypatch, caplog):
    address = str(sock_dir / "a.sock")

    async def main():
        server = await ipc.serve(address, echo)
        try:
            left = fail_accepts(monkeypatch, server, 150)
            elapsed = await call(address)
        finally:
            server.close()
            await server.wait_closed()
        return left[0], elapsed

    left, elapsed = asyncio.run(main())
    assert left == 0, "asyncio's accept loop met every failure"
    lines = accept_lines(caplog)
    assert len(lines) == 1, lines
    assert lines[0] == (f"accept failed on {address}: {os.strerror(errno.EMFILE)} "
                        f"(errno EMFILE); retrying every 1 s")
    assert [r.levelname for r in caplog.records if r.name == "moxi.ipc"] == ["WARNING"]
    assert all(r.exc_info is None for r in caplog.records if r.name == "moxi.ipc")
    assert asyncio_records(caplog) == []
    assert elapsed >= 0.9, "served after asyncio's one retry"


def test_other_contexts_reach_the_loops_handler_once_and_unchanged(sock_dir, caplog):
    address = str(sock_dir / "a.sock")

    async def main():
        loop = asyncio.get_running_loop()
        seen = []

        def recorder(seen_loop, context):
            seen.append((seen_loop, context, dict(context)))

        loop.set_exception_handler(recorder)
        server = await ipc.serve(address, echo)
        try:
            assert loop.get_exception_handler() is not recorder
            other = {"message": "something else", "exception": ValueError("x")}
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stranger:
                foreign = {"message": _server._ACCEPT_FAILED, "socket": stranger,
                           "exception": OSError(errno.EMFILE, os.strerror(errno.EMFILE))}
                sent = [(other, dict(other)), (foreign, dict(foreign))]
                loop.call_exception_handler(other)
                loop.call_exception_handler(foreign)
        finally:
            server.close()
            await server.wait_closed()
        return loop, seen, sent

    loop, seen, sent = asyncio.run(main())
    assert len(seen) == len(sent) == 2
    for position, (sent_context, sent_copy) in enumerate(sent):
        seen_loop, context, copy = seen[position]
        assert seen_loop is loop
        assert context is sent_context
        assert copy == sent_copy
    assert accept_lines(caplog) == []


def test_a_line_after_the_interval_counts_the_failures_held_back(sock_dir, monkeypatch,
                                                                  caplog):
    monkeypatch.setattr(_server, "ACCEPT_LOG_EVERY", 0.5)
    address = str(sock_dir / "a.sock")

    async def main():
        server = await ipc.serve(address, echo)
        try:
            left = fail_accepts(monkeypatch, server, 150, second_event_after_s=1.0)
            await call(address)
        finally:
            server.close()
            await server.wait_closed()
        return left[0]

    assert asyncio.run(main()) == 0
    lines = accept_lines(caplog)
    assert len(lines) == 2, lines
    assert "more since" not in lines[0]
    assert re.fullmatch(re.escape(lines[0]) + r"; 100 more since \d\d:\d\d:\d\d", lines[1])
    assert asyncio_records(caplog) == []


def test_a_handler_set_after_ours_is_wrapped_by_the_next_server(sock_dir, monkeypatch,
                                                                caplog):
    first_address = str(sock_dir / "a.sock")
    second_address = str(sock_dir / "b.sock")

    async def main():
        loop = asyncio.get_running_loop()
        first = await ipc.serve(first_address, echo)
        dropped = weakref.ref(loop.get_exception_handler())
        seen = []

        def recorder(seen_loop, context):
            seen.append(context)

        loop.set_exception_handler(recorder)
        gc.collect()
        assert dropped() is None, "the replaced filter is gone"
        second = await ipc.serve(second_address, echo)
        try:
            fail_accepts(monkeypatch, first, 150)
            await call(first_address)
            other = {"message": "something else"}
            loop.call_exception_handler(other)
        finally:
            for server in (first, second):
                server.close()
                await server.wait_closed()
        return seen, other

    seen, other = asyncio.run(main())
    lines = accept_lines(caplog)
    assert len(lines) == 1, lines
    assert lines[0].startswith(f"accept failed on {first_address}: ")
    assert asyncio_records(caplog) == []
    assert len(seen) == 1 and seen[0] is other


def accept_retries(loop) -> tuple[int, int]:
    """The accept retries `loop` holds and has not cancelled: those in its timer heap,
    and those its turn has already moved from there to its ready queue."""
    def live(handles):
        return sum(1 for handle in handles
                   if handle._callback == loop._start_serving and not handle.cancelled())
    return live(loop._scheduled), live(loop._ready)


@pytest.mark.parametrize("same_turn", [False, True],
                         ids=["before-due", "in-the-turn-they-come-due"])
def test_close_cancels_the_accept_retries_held_for_its_listener(sock_dir, monkeypatch,
                                                                 caplog, same_turn):
    address = str(sock_dir / "a.sock")

    async def main():
        loop = asyncio.get_running_loop()
        server = await ipc.serve(address, echo)
        # The loop's clock stands still until moved below, so no retry comes due early
        # however slow the machine is.
        clock = [loop.time()]
        loop.time = lambda: clock[0]
        try:
            try:
                # The fourth accept of the same event takes the client, so the call is
                # answered at once and the three retries are still waiting at close.
                left = fail_accepts(monkeypatch, server, 3)
                await call(address)
                if same_turn:
                    # The retries come due; the loop's next turn moves them to its ready
                    # queue behind this task, which then closes before they run.
                    clock[0] += 2 * asyncio.constants.ACCEPT_RETRY_DELAY
                    await asyncio.sleep(0)
                held = accept_retries(loop)
            finally:
                server.close()
                await server.wait_closed()
            after_close = accept_retries(loop)
            clock[0] += 2 * asyncio.constants.ACCEPT_RETRY_DELAY
            for _ in range(3):
                await asyncio.sleep(0)
        finally:
            del loop.time
        return left[0], held, after_close

    left, held, after_close = asyncio.run(main())
    assert left == 0
    assert held == ((0, 3) if same_turn else (3, 0)), "each failed accept left asyncio a retry"
    assert after_close == (0, 0)
    assert asyncio_records(caplog) == []
    assert [r for r in caplog.records if "Invalid file descriptor" in r.getMessage()] == []
    assert all(r.exc_info is None for r in caplog.records)
