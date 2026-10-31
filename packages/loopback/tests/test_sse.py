"""The stream route in process: `Topics`, `?topics=`, events cut to their shapes, the
heartbeat, the stream cap, and `Snapshot`."""

from __future__ import annotations

import asyncio
import logging
import threading

import pytest

from asgi_kit import PORT, SERVER_ID, TOKEN, StreamClient, assert_header_set, drive, make_page, scope
from moxi_loopback import server
from moxi_loopback.check import upstream
from moxi_loopback.routes import BadRequest, build_table
from moxi_loopback.server import Snapshot, Topics, app, guard, read_topics


def run(coro):
    return asyncio.run(coro)


def served(**kwargs):
    inner = app(make_page(), module="usage", server_id=SERVER_ID, **kwargs)
    profile = upstream(build_table(inner.page)[0], token=TOKEN, port=PORT)
    return guard(inner, lambda: profile), inner


def events_scope(query=b""):
    return scope(path="/api/events", query=query)


ROUTE = make_page().stream_route


# --- Topics ----------------------------------------------------------------------------


def test_topics_refuses_a_bad_or_repeated_name_and_publish_an_unknown_one():
    for names in [(), ("Bad",), ("a_b",), ("-a",), ("a", "a")]:
        with pytest.raises(ValueError):
            Topics(*names)
    topics = Topics("today")
    with pytest.raises(KeyError):
        topics.publish("other", {})


def test_a_stream_sends_the_last_values_first_then_later_publishes(pages):
    chain, _ = served()
    pages.TOPICS.publish("today", {"total": 1})
    pages.TOPICS.publish("today", {"total": 2})

    async def go():
        client = StreamClient(chain, events_scope())
        status = await client.started()
        first = await client.events(1)
        pages.TOPICS.publish("log", [{"line": "a"}, {"line": "b"}])
        pages.TOPICS.publish("today", {"total": 3})
        later = await client.events(2)
        await client.close()
        return status, client.answer, first, later

    status, answer, first, later = run(go())
    assert status == 200
    assert answer.header("content-type") == "text/event-stream"
    assert_header_set(answer)
    assert first == [("today", {"total": 2})]
    assert later == [("log", [{"line": "a"}, {"line": "b"}]), ("today", {"total": 3})]


def test_a_publish_from_a_thread_reaches_the_stream(pages):
    chain, _ = served()

    async def go():
        client = StreamClient(chain, events_scope())
        await client.started()
        thread = threading.Thread(target=pages.TOPICS.publish, args=("today", {"total": 9}))
        thread.start()
        got = await client.events(1)
        thread.join()
        await client.close()
        return got

    assert run(go()) == [("today", {"total": 9})]


def test_a_stream_keeps_only_the_latest_value_per_topic(pages):
    chain, _ = served()

    async def go():
        client = StreamClient(chain, events_scope())
        await client.started()
        for n in range(5):
            pages.TOPICS.publish("today", {"total": n})
        got = await client.events(1)
        await client.close()
        return got

    assert run(go()) == [("today", {"total": 4})]


def test_topics_picks_the_topics_a_stream_carries(pages):
    chain, _ = served()
    pages.TOPICS.publish("log", [])
    pages.TOPICS.publish("today", {"total": 1})

    async def go():
        client = StreamClient(chain, events_scope(b"topics=today"))
        await client.started()
        first = await client.events(1)
        pages.TOPICS.publish("log", [{"line": "x"}])
        pages.TOPICS.publish("today", {"total": 2})
        later = await client.events(1)
        await client.close()
        return first, later

    first, later = run(go())
    assert first == [("today", {"total": 1})]
    assert later == [("today", {"total": 2})]


def test_no_topics_key_carries_every_declared_topic(pages):
    chain, _ = served()
    pages.TOPICS.publish("log", [])
    pages.TOPICS.publish("today", {"total": 1})

    async def go():
        client = StreamClient(chain, events_scope(b"since=4"))
        await client.started()
        got = await client.events(2)
        await client.close()
        return got

    assert sorted(name for name, _ in run(go())) == ["log", "today"]


@pytest.mark.parametrize("query", [
    b"topics=nope", b"topics=today,", b"topics=,today", b"topics=", b"topics=today%2Clog",
    b"topics=%74oday", b"topics=today&topics=log", b"top%69cs=today&topics=log",
    b"topics=today,today", b"topics=today+log", b"since=x", b"other=1",
])
def test_a_bad_topics_value_is_a_400_that_does_not_echo_it(pages, query):
    chain, _ = served()
    answer = run(drive(chain, events_scope(query)))
    assert answer.status == 400
    body = answer.json()["error"]
    for word in (b"nope", b"%74oday", b"%2C", b"x"):
        if word in query and word != b"x":
            assert word.decode() not in body
    if b"topics" in query or b"top%69cs" in query:
        assert body.endswith(": topics")
    assert_header_set(answer)


def test_read_topics_rules():
    assert read_topics(ROUTE, b"") == ("today", "log")
    assert read_topics(ROUTE, b"since=3") == ("today", "log")
    assert read_topics(ROUTE, b"topics=log,today") == ("log", "today")
    assert read_topics(ROUTE, "topics=today") == ("today",)
    for query in [b"topics=%6cog", b"topics=today,,log", b"topics=other", b"topics=a&topics=b",
                  b"topics=log,log", b"\xff"]:
        with pytest.raises(BadRequest):
            read_topics(ROUTE, query)


# --- shapes ----------------------------------------------------------------------------


def test_an_event_is_cut_to_its_topic_shape(pages, caplog):
    chain, _ = served()

    async def go():
        client = StreamClient(chain, events_scope())
        await client.started()
        pages.TOPICS.publish("today", {"total": 1, "secret": "x"})
        pages.TOPICS.publish("today", {"total": 2, "secret": "y"})
        first = await client.events(1)
        pages.TOPICS.publish("log", [{"line": "a", "secret": "z"}])
        second = await client.events(1)
        await client.close()
        return first, second

    with caplog.at_level(logging.WARNING, logger="moxi.usage.page"):
        first, second = run(go())
    assert first == [("today", {"total": 2})]
    assert second == [("log", [{"line": "a"}])]
    assert "x" not in str(first) and "secret" in caplog.text


def test_strict_ends_the_stream_on_an_undeclared_key(pages, caplog):
    chain, inner = served(strict=True)

    async def go():
        client = StreamClient(chain, events_scope())
        await client.started()
        pages.TOPICS.publish("today", {"total": 1})
        ok = await client.events(1)
        pages.TOPICS.publish("today", {"total": 1, "secret": "x"})
        with pytest.raises(EOFError):
            await client.events(1)
        await client.close()
        return ok

    with caplog.at_level(logging.ERROR, logger="moxi.usage.page"):
        assert run(go()) == [("today", {"total": 1})]
    assert "stream ended" in caplog.text
    assert inner.activity.streams == 0


def test_a_mistyped_event_ends_the_stream_even_when_lenient(pages, caplog):
    chain, _ = served()
    pages.TOPICS.publish("today", {"total": "many"})

    async def go():
        client = StreamClient(chain, events_scope())
        await client.started()
        with pytest.raises(EOFError):
            await client.events(1)
        await client.close()

    with caplog.at_level(logging.ERROR, logger="moxi.usage.page"):
        run(go())
    assert "many" not in caplog.text


def test_topics_other_than_the_declared_ones_is_a_logged_500(pages, caplog):
    chain, _ = served()
    pages.STATE["topics"] = Topics("today")
    with caplog.at_level(logging.ERROR, logger="moxi.usage.page"):
        answer = run(drive(chain, events_scope()))
    assert answer.status == 500
    assert answer.body == b""
    assert_header_set(answer)
    assert "declared topics" in caplog.text


# --- the heartbeat, the count and the cap ------------------------------------------------


def test_a_heartbeat_comment_goes_out_on_a_quiet_stream(pages, monkeypatch):
    monkeypatch.setattr(server, "HEARTBEAT", 0.05)
    chain, _ = served()

    async def go():
        client = StreamClient(chain, events_scope())
        await client.started()
        got = await client.events(2)
        await client.close()
        return got

    assert run(go()) == [("", "heartbeat"), ("", "heartbeat")]


def test_an_open_stream_counts_and_a_disconnect_lowers_the_count(pages):
    chain, inner = served()

    async def go():
        client = StreamClient(chain, events_scope())
        await client.started()
        during = (inner.activity.streams, inner.activity.quiet())
        await client.close()
        return during, inner.activity.streams, inner.activity.quiet()

    during, after, quiet = run(go())
    assert during == (1, None)
    assert after == 0
    assert quiet is not None


def test_the_stream_cap_answers_503_with_retry_after(pages):
    chain, inner = served()

    async def go():
        clients = [StreamClient(chain, events_scope()) for _ in range(server.STREAM_CAP)]
        for client in clients:
            await client.started()
        over = await drive(chain, events_scope())
        read = await drive(chain, scope(path="/api/summary"))
        for client in clients:
            await client.close()
        again = StreamClient(chain, events_scope())
        status = await again.started()
        await again.close()
        return over, read, status

    over, read, status = run(go())
    assert over.status == 503
    assert over.header("retry-after") == "1"
    assert_header_set(over)
    assert read.status == 200  # Streams never take the slots reads and acts need.
    assert status == 200
    assert inner.activity.streams == 0


# --- Snapshot --------------------------------------------------------------------------


def test_a_new_servers_first_gather_sends_an_event(pages):
    chain, _ = served()
    snap = Snapshot(lambda: {"total": 0}, pages.TOPICS, "today")

    async def go():
        client = StreamClient(chain, events_scope())
        await client.started()
        await snap.changed()
        got = await client.events(1)
        await client.close()
        return got

    assert run(go()) == [("today", {"total": 0})]


def test_snapshot_publishes_only_when_the_hash_changes(pages):
    topics = Topics("today")
    published = []
    real = topics.publish
    topics.publish = lambda name, data: (published.append(data), real(name, data))
    value = {"total": 1}
    snap = Snapshot(lambda: dict(value), topics, "today")

    async def go():
        await snap.changed()
        await snap.changed()
        value["total"] = 2
        await snap.changed()
        await snap.changed()

    run(go())
    assert published == [{"total": 1}, {"total": 2}]


def test_snapshot_callers_share_one_rebuild(pages):
    topics = Topics("today")
    gathers = []

    async def gather():
        gathers.append(1)
        await asyncio.sleep(0.05)
        return {"total": len(gathers)}

    snap = Snapshot(gather, topics, "today")

    async def go():
        # Three callers before the rebuild starts share it; two asking while it gathers
        # share the one rebuild after it.
        first = [asyncio.ensure_future(snap.changed()) for _ in range(3)]
        await asyncio.sleep(0.01)
        second = [asyncio.ensure_future(snap.changed()) for _ in range(2)]
        await asyncio.gather(*first, *second)

    run(go())
    assert len(gathers) == 2
    assert topics._values["today"] == {"total": 2}


def test_a_gather_that_raises_reaches_its_callers_and_publishes_nothing(pages):
    topics = Topics("today")
    calls = []

    def gather():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("gather broke")
        return {"total": 1}

    snap = Snapshot(gather, topics, "today")

    async def go():
        with pytest.raises(RuntimeError, match="gather broke"):
            await snap.changed()
        assert "today" not in topics._values
        await snap.changed()

    run(go())
    assert topics._values["today"] == {"total": 1}


def test_snapshot_refuses_a_name_its_topics_lack():
    with pytest.raises(KeyError):
        Snapshot(lambda: {}, Topics("today"), "log")
    with pytest.raises(TypeError):
        Snapshot(None, Topics("today"), "today")
