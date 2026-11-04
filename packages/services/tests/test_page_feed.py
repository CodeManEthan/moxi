"""services' stream and the poller that feeds it, in process: the first event, a change,
the supervisor and health cadences, a tick that finds the last gather still running, a
gather that fails, and the idle exit the poller never holds.

The tick tests set the period to an hour, so the poller's own loop ticks once at its start
and the test calls `tick()` itself. The supervisor is the `fake` fixture's.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time

import pytest

from moxi_services import acts, pagehandlers

from page_kit import Stream, send, served

PY = sys.executable
HOUR = 3600.0


@pytest.fixture(autouse=True)
def fresh_feed(monkeypatch, site, fake):
    """A feed of its own per test, no health results or logged failures from another,
    no module rows but a test's own, and a fake supervisor."""
    monkeypatch.setattr(pagehandlers, "_current", None)
    monkeypatch.setattr(pagehandlers, "_known", {})
    monkeypatch.setattr(pagehandlers, "_logged", set())
    yield fake
    feed = pagehandlers._current
    if feed is not None:
        feed.task.cancel()


def a_daemon(name: str = "web", **extra) -> None:
    out = acts.add_written(name, [PY, "-m", "http.server"], **extra)
    assert out.ok, out


class Gathers:
    """A gather in place of the page's: each call recorded with its `health`, blocking
    while `gate` is clear, raising while `raises` is set."""

    def __init__(self):
        self.calls: list[bool] = []
        self.gate = threading.Event()
        self.gate.set()
        self.inside = threading.Event()
        self.raises: Exception | None = None
        self.value = {"state": "up", "detail": None, "rows": [], "problems": []}

    def __call__(self, *, health: bool = False):
        self.calls.append(health)
        self.inside.set()
        assert self.gate.wait(30)
        if self.raises is not None:
            raise self.raises
        return dict(self.value)


@pytest.fixture
def gathers(monkeypatch):
    found = Gathers()
    monkeypatch.setattr(pagehandlers, "gather", found)
    return found


def until(check, within: float = 5.0) -> None:
    deadline = time.monotonic() + within
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.005)


def settled(feed) -> bool:
    """True once the feed has started a gather and that gather has ended."""
    return feed.thread is not None and not feed.thread.is_alive()


async def until_async(check, within: float = 5.0) -> None:
    deadline = time.monotonic() + within
    while not check():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.005)


# -- the stream ---------------------------------------------------------------------------------

def test_the_first_event_is_every_row_and_a_change_comes_at_the_next_tick(monkeypatch):
    monkeypatch.setattr(pagehandlers, "PERIOD", 0.05)
    a_daemon(enable=True)

    async def go():
        stream = Stream(served())
        try:
            name, data = await stream.next(5)
            assert stream.status == 200 and name == "rows"
            assert data["state"] == "up"
            assert [(r["id"], r["state"]) for r in data["rows"]] == [("services/web",
                                                                      "active")]
            await asyncio.to_thread(acts.stop, "web")
            _, data = await stream.next(5)
            assert [(r["id"], r["state"]) for r in data["rows"]] == [("services/web",
                                                                      "inactive")]
        finally:
            await stream.close()

    asyncio.run(go())


def test_a_tick_that_changes_nothing_sends_no_event(monkeypatch):
    monkeypatch.setattr(pagehandlers, "PERIOD", 0.05)
    a_daemon(enable=True)

    async def go():
        stream = Stream(served())
        try:
            await stream.next(5)
            assert await stream.none_within(0.4)
        finally:
            await stream.close()

    asyncio.run(go())


def test_the_poller_starts_with_the_first_stream_and_keeps_running_after_it(monkeypatch,
                                                                            gathers):
    monkeypatch.setattr(pagehandlers, "PERIOD", 0.05)

    async def go():
        assert pagehandlers._current is None and gathers.calls == []
        stream = Stream(served())
        await stream.next(5)
        feed = pagehandlers._current
        await stream.close()
        seen = len(gathers.calls)
        await until_async(lambda: len(gathers.calls) > seen + 2)
        assert feed is pagehandlers._current and not feed.task.done()

    asyncio.run(go())


# -- the cadence ----------------------------------------------------------------------------------

def test_the_first_gather_runs_the_health_checks_and_then_every_health_period(
        monkeypatch, gathers):
    monkeypatch.setattr(pagehandlers, "PERIOD", HOUR)
    monkeypatch.setattr(pagehandlers, "HEALTH_PERIOD", 0.3)

    async def go():
        feed = pagehandlers._feed()
        await until_async(lambda: gathers.calls == [True])
        await until_async(lambda: settled(feed))
        assert feed.tick()
        await until_async(lambda: settled(feed))
        assert gathers.calls == [True, False]
        await asyncio.sleep(0.35)
        assert feed.tick()
        await until_async(lambda: settled(feed))
        assert gathers.calls == [True, False, True]

    asyncio.run(go())


def test_the_poller_reads_every_five_seconds_and_checks_health_every_thirty():
    assert (pagehandlers.PERIOD, pagehandlers.HEALTH_PERIOD) == (5.0, 30.0)


def test_a_tick_that_finds_the_last_gather_running_is_skipped(monkeypatch, gathers):
    monkeypatch.setattr(pagehandlers, "PERIOD", HOUR)
    gathers.gate.clear()

    async def go():
        feed = pagehandlers._feed()
        await until_async(gathers.inside.is_set)
        try:
            for _ in range(3):
                assert feed.tick() is False
            assert feed.skipped == 3 and gathers.calls == [True]
        finally:
            gathers.gate.set()
        await until_async(lambda: settled(feed))
        assert feed.tick() is True
        await until_async(lambda: len(gathers.calls) == 2)

    asyncio.run(go())


def test_a_health_pass_due_while_a_gather_runs_is_run_at_the_next_tick_that_gathers(
        monkeypatch, gathers):
    monkeypatch.setattr(pagehandlers, "PERIOD", HOUR)
    monkeypatch.setattr(pagehandlers, "HEALTH_PERIOD", 0.1)
    gathers.gate.clear()

    async def go():
        feed = pagehandlers._feed()
        await until_async(gathers.inside.is_set)
        await asyncio.sleep(0.15)
        assert feed.tick() is False
        gathers.gate.set()
        await until_async(lambda: settled(feed))
        assert feed.tick() is True
        await until_async(lambda: settled(feed))
        assert gathers.calls == [True, True]

    asyncio.run(go())


def test_the_health_pass_runs_each_check_under_services_own_limit_a_few_at_a_time(
        monkeypatch):
    seen = {}
    real = pagehandlers.verdict.views

    def views(found, **kwargs):
        seen.update(kwargs)
        return real(found, **kwargs)

    monkeypatch.setattr(pagehandlers.verdict, "views", views)
    pagehandlers.gather(health=True)
    assert seen == {"health": True, "status_seconds": pagehandlers.STATUS_SECONDS,
                    "health_seconds": pagehandlers.state.HEALTH_SECONDS,
                    "health_at_once": pagehandlers.HEALTH_AT_ONCE}


# -- failures ---------------------------------------------------------------------------------

def test_the_poller_outlives_a_gather_that_raises(monkeypatch, gathers, caplog):
    monkeypatch.setattr(pagehandlers, "PERIOD", HOUR)
    gathers.raises = RuntimeError("boom")

    async def go():
        feed = pagehandlers._feed()
        with caplog.at_level(logging.ERROR, logger="moxi.services.page"):
            await until_async(lambda: gathers.calls and settled(feed))
            assert feed.tick()
            await until_async(lambda: len(gathers.calls) == 2 and settled(feed))
        gathers.raises = None
        assert feed.tick()
        await until_async(lambda: "rows" in feed.topics._values)
        assert not feed.task.done()

    asyncio.run(go())
    failed = [r for r in caplog.records if "gather failed" in r.getMessage()]
    assert len(failed) == 1


def test_a_store_that_cannot_be_read_is_published_as_state_error(monkeypatch, too_new):
    monkeypatch.setattr(pagehandlers, "PERIOD", 0.05)

    async def go():
        stream = Stream(served())
        try:
            _, data = await stream.next(5)
            assert data["state"] == "error" and "newer than" in data["detail"]
        finally:
            await stream.close()

    asyncio.run(go())


# -- the idle exit ------------------------------------------------------------------------------

def test_the_poller_holds_nothing_the_idle_exit_reads_while_it_gathers(monkeypatch,
                                                                      gathers):
    monkeypatch.setattr(pagehandlers, "PERIOD", HOUR)
    app = served()

    async def go():
        stream = Stream(app)
        await stream.next(5)
        await stream.close()
        feed = pagehandlers._current
        await until_async(lambda: settled(feed))
        gathers.gate.clear()
        assert feed.tick()
        await until_async(gathers.inside.is_set)
        try:
            status, health = await send(app, "GET", "/api/health")
            assert status == 200
            assert health["open"] == {"requests": 0, "streams": 0, "busy": []}
            assert feed.thread.is_alive()
        finally:
            gathers.gate.set()

    asyncio.run(go())


def test_a_gather_runs_on_a_thread_of_its_own_never_the_bases(monkeypatch, gathers):
    monkeypatch.setattr(pagehandlers, "PERIOD", HOUR)
    names = []
    real = gathers.__call__

    def gather(*, health=False):
        names.append(threading.current_thread().name)
        return real(health=health)

    monkeypatch.setattr(pagehandlers, "gather", gather)

    async def go():
        feed = pagehandlers._feed()
        await until_async(lambda: names and settled(feed))

    asyncio.run(go())
    assert names == ["services rows gather"]
    assert all(t.daemon for t in threading.enumerate() if t.name == "services rows gather")
