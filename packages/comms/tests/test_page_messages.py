"""comms' page's message reads, `/api/messages`, over a rig's record and books.

The reads are served through loopback's strict app, as in `test_page.py`, so a value
that leaves its declared shape fails here. The rig runs on the local backend, whose
store the page server opens read-only; one test reads the mock backend's file. The
page's date is pinned to the rig clock's day, and messages are posted with the rig's
clock set to the time each one should carry.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sqlite3
import sys
import urllib.parse

import pytest
from conftest import wait_until

from moxi.store import transaction
from moxi_comms import attribution, machine_line, page, pagereads, store
from moxi_comms.mock_record import MockRecord
from moxi_comms.paths import comms_db
from moxi_comms.provision import NAMES, provision

from test_page import run, scope, served

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="comms runs on Linux only")

MAIN = "main@proj-a"
PLANNER = "planner@proj-a"
KEYS = ["state", "detail", "today", "day", "messages", "more", "earlier"]


@pytest.fixture
def backend():
    """These tests read the rig's `comms.db`, so the rig is on the local backend."""
    return "local"


@pytest.fixture(autouse=True)
def fresh_log(monkeypatch):
    monkeypatch.setattr(pagereads, "_logged", set())


@pytest.fixture
def today(rig, monkeypatch) -> dt.date:
    """The page's date, pinned to the rig clock's day, so a run across midnight reads
    the day its messages were posted on."""
    day = dt.date.fromtimestamp(rig.clock())
    monkeypatch.setattr(pagereads, "_today", lambda: day)
    return day


def at(day: dt.date, hour: int, minute: int = 0) -> float:
    return dt.datetime.combine(day, dt.time(hour, minute)).timestamp()


def post(rig, when: float, text: str = "hi", *, to=(MAIN,), by: str = PLANNER, **kw):
    rig.clock.t = when
    return rig.record.post_as_identity(
        by, rig.names_channel, machine_line.compose(list(to), text, **kw))


def prose(rig, when: float, text: str = "just words, no line"):
    rig.clock.t = when
    return rig.record.post_as_identity(PLANNER, rig.names_channel, text)


def read(**query) -> dict:
    path = "/api/messages"
    if query:
        path += "?" + urllib.parse.urlencode(query)
    status, body = run(get_with_query(path))
    assert status == 200, body
    return body


async def get_with_query(path: str):
    raw, _, query = path.partition("?")
    sent = {"status": 0, "body": b""}
    asked = [False]

    async def receive():
        if not asked[0]:
            asked[0] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        raise AssertionError("one request")

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
        elif message["type"] == "http.response.body":
            sent["body"] += message.get("body", b"")

    await served()(scope(raw, query.encode("ascii")), receive, send)
    return sent["status"], json.loads(sent["body"]) if sent["body"] else None


def ids(body) -> list[str]:
    return [m["message_id"] for m in body["messages"]]


def rows_of(body, message_id) -> list[dict]:
    (found,) = [m for m in body["messages"] if m["message_id"] == message_id]
    return found["rows"]


def queued(mid, name=MAIN) -> bool:
    conn = store.open_read()
    try:
        return conn.execute("SELECT 1 FROM queue WHERE message_id = ? AND name = ?",
                            (int(mid), name)).fetchone() is not None
    finally:
        conn.close()


def set_row(mid, name=MAIN, **cols) -> None:
    """Move one queue row by hand, as the daemon's hand-over and digests would."""
    conn = store.open_write()
    try:
        sets = ", ".join(f"{key} = ?" for key in cols)
        with transaction(conn):
            conn.execute(f"UPDATE queue SET {sets} WHERE message_id = ? AND name = ?",
                         (*cols.values(), int(mid), name))
    finally:
        conn.close()


def drop_row(mid, name=MAIN) -> None:
    """Take one queue row away, as the name's ack at its turn's end does."""
    conn = store.open_write()
    try:
        with transaction(conn):
            conn.execute("DELETE FROM queue WHERE message_id = ? AND name = ?",
                         (int(mid), name))
    finally:
        conn.close()


def store_fds() -> int:
    """Descriptors this process holds on comms.db and its `-wal` and `-shm` files."""
    db = str(comms_db())
    count = 0
    for fd in os.listdir("/proc/self/fd"):
        try:
            target = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            continue
        if target.startswith(db):
            count += 1
    return count


# --- the shape ---------------------------------------------------------------------------

def test_the_route_declares_its_query_and_marks_only_the_text():
    route = next(r for r in page.PAGE.routes if r.path == "/api/messages")
    assert route.kind == "read" and route.gateway is False
    assert set(route.query) == {"day", "before", "limit"}
    assert not any(field.required for field in route.query.values())
    assert route.query["limit"].max == 500
    assert list(page.MESSAGE.items) == ["message_id", "at", "author", "author_tier", "to",
                                        "posture", "reply_to", "text", "withdrawn", "rows"]
    marked = {key for key, out in page.MESSAGE.items.items() if out.mark}
    assert marked == {"text"} and page.MESSAGE.items["text"].mark == "redact"


def test_todays_messages_newest_first_in_the_declared_shape(rig, today):
    first = post(rig, at(today, 0, 10), "one", posture="none")
    second = post(rig, at(today, 0, 20), "two", reply_to=first.id, posture="once")
    body = read()
    assert list(body) == KEYS
    assert (body["state"], body["detail"]) == ("up", None)
    assert body["today"] == body["day"] == today.isoformat()
    assert ids(body) == [second.id, first.id]
    newest = body["messages"][0]
    assert list(newest) == list(page.MESSAGE.items)
    assert newest == {**newest, "at": at(today, 0, 20), "author": PLANNER,
                      "author_tier": attribution.TIER_CHOSEN, "to": [MAIN],
                      "posture": "once", "reply_to": first.id, "text": "two",
                      "withdrawn": False}
    assert (body["messages"][1]["posture"], body["messages"][1]["reply_to"]) == ("none", None)
    assert (body["more"], body["earlier"]) == (0, None)


def test_every_id_in_the_answer_is_a_string_of_digits(rig, today):
    first = post(rig, at(today, 1))
    post(rig, at(today - dt.timedelta(days=1), 9))
    post(rig, at(today, 2), reply_to=first.id)
    body = read()
    for m in body["messages"]:
        assert isinstance(m["message_id"], str) and m["message_id"].isdigit()
        assert m["reply_to"] is None or (isinstance(m["reply_to"], str)
                                         and m["reply_to"].isdigit())
    assert isinstance(body["earlier"]["before"], str) and body["earlier"]["before"].isdigit()


def test_limit_more_and_before_page_through_today(rig, today):
    posted = [post(rig, at(today, 0, minute), f"m{minute}").id for minute in range(1, 6)]
    body = read(limit=2)
    assert ids(body) == posted[:2:-1] and body["more"] == 3
    body = read(limit=2, before=ids(body)[-1])
    assert ids(body) == [posted[2], posted[1]] and body["more"] == 1
    body = read(limit=2, before=ids(body)[-1])
    assert ids(body) == [posted[0]] and body["more"] == 0
    assert len(ids(read())) == 5, "twelve by default"


def test_a_prose_message_is_neither_listed_nor_counted(rig, today):
    yesterday = today - dt.timedelta(days=1)
    kept_before = post(rig, at(yesterday, 9))
    prose(rig, at(yesterday, 10))
    prose(rig, at(today, 0, 5))
    kept = post(rig, at(today, 0, 10))
    body = read()
    assert ids(body) == [kept.id] and body["more"] == 0
    assert body["earlier"]["count"] == 1
    assert ids(read(day=yesterday.isoformat(), before=body["earlier"]["before"])) == [
        kept_before.id]


def test_earlier_counts_the_days_before_and_its_cursor_reads_the_newest_one(rig, today):
    one_back, three_back = today - dt.timedelta(days=1), today - dt.timedelta(days=3)
    old = [post(rig, at(three_back, 8)).id, post(rig, at(three_back, 9)).id]
    recent = [post(rig, at(one_back, h)).id for h in (7, 12, 18)]
    now = post(rig, at(today, 0, 30)).id
    body = read()
    assert ids(body) == [now]
    assert body["earlier"] == {"day": one_back.isoformat(), "count": 5,
                               "first_at": at(three_back, 8),
                               "before": str(int(recent[-1]) + 1)}
    # The cursor reads that day, which carries the next `earlier` past a day with none.
    day = read(day=one_back.isoformat(), before=body["earlier"]["before"])
    assert (day["state"], day["day"], day["today"]) == ("up", one_back.isoformat(),
                                                        today.isoformat())
    assert ids(day) == recent[::-1] and day["more"] == 0
    assert day["earlier"] == {"day": three_back.isoformat(), "count": 2,
                              "first_at": at(three_back, 8), "before": str(int(old[-1]) + 1)}
    last = read(day=three_back.isoformat(), before=day["earlier"]["before"])
    assert ids(last) == old[::-1] and last["earlier"] is None


def test_an_earlier_day_reads_back_in_pages_and_pages_within_itself(rig, today,
                                                                    monkeypatch):
    monkeypatch.setattr(pagereads, "DAY_PAGE", 2)
    one_back = today - dt.timedelta(days=1)
    post(rig, at(today - dt.timedelta(days=2), 9))
    posted = [post(rig, at(one_back, 1, minute)).id for minute in range(7)]
    post(rig, at(today, 0, 1))
    cursor = read()["earlier"]["before"]
    body = read(day=one_back.isoformat(), before=cursor, limit=3)
    assert ids(body) == posted[:3:-1] and body["more"] == 4
    body = read(day=one_back.isoformat(), before=ids(body)[-1], limit=30)
    assert ids(body) == posted[3::-1] and body["more"] == 0


def test_a_day_with_no_message_is_empty_and_still_says_what_came_before(rig, today):
    two_back = today - dt.timedelta(days=2)
    older = post(rig, at(today - dt.timedelta(days=3), 9))
    post(rig, at(today - dt.timedelta(days=1), 9))
    body = read(day=two_back.isoformat(), before=str(int(older.id) + 5))
    assert (body["state"], body["messages"], body["more"]) == ("up", [], 0)
    assert body["earlier"]["count"] == 1 and body["earlier"]["before"] == str(
        int(older.id) + 1)


def test_today_with_no_message_has_no_messages_and_no_earlier(rig, today):
    body = read()
    assert (body["state"], body["messages"], body["more"], body["earlier"]) == (
        "up", [], 0, None)


def test_a_day_before_today_needs_a_message_id(rig, today):
    body = read(day=(today - dt.timedelta(days=1)).isoformat())
    assert (body["state"], body["detail"]) == ("error", pagereads.NO_CURSOR)
    assert (body["messages"], body["earlier"]) == ([], None)


@pytest.mark.parametrize("query", [
    {"before": "12a"}, {"before": "-5"}, {"before": ""}, {"before": "1e9"},
    {"day": "yesterday"}, {"day": "2026-13-01"}, {"day": "2026-1-1"}, {"limit": "0"},
    {"limit": "-3"},
])
def test_a_query_the_page_never_sends_is_state_error_and_opens_nothing(
        rig, today, monkeypatch, query):
    def refused(*a, **kw):
        raise AssertionError("opened the store")

    monkeypatch.setattr(pagereads, "open_read", refused)
    body = read(**query)
    assert body == {"state": "error", "detail": pagereads.BAD_QUERY,
                    "today": today.isoformat(), "day": today.isoformat(), "messages": [],
                    "more": 0, "earlier": None}


def test_a_limit_over_500_is_refused_by_the_route(rig, today):
    status, _ = run(get_with_query("/api/messages?limit=501"))
    assert status == 400


# --- rows from the books -----------------------------------------------------------------

def test_each_recipients_row_from_the_books(rig, today):
    waiting = post(rig, at(today, 0, 1), "waits", to=(MAIN, "main@proj-b"))
    handed = post(rig, at(today, 0, 2), "handed")
    digested = post(rig, at(today, 0, 3), "in a digest")
    withdrawn = post(rig, at(today, 0, 4), "taken back")
    expired = post(rig, at(today, 0, 5), "too late", expires_at=int(at(today, 0, 4)))
    had = post(rig, at(today, 0, 6), "read already")
    for m in (waiting, handed, digested, withdrawn, had):
        assert wait_until(lambda m=m: queued(m.id))
    assert wait_until(lambda: queued(waiting.id, "main@proj-b"))
    assert wait_until(lambda: rig.daemon.books.dropped_reason(MAIN, expired.id))
    set_row(handed.id, state="handed")
    set_row(digested.id, state="in_digest", held=1, digested_once=1)
    rig.daemon.books.withdraw(withdrawn.id)
    drop_row(had.id)
    body = read()
    row = {"held": False, "takeable": True}
    assert rows_of(body, waiting.id) == [
        {"qualified": MAIN, "state": "waiting", **row},
        {"qualified": "main@proj-b", "state": "waiting", **row}]
    assert rows_of(body, handed.id) == [{"qualified": MAIN, "state": "handed", **row}]
    assert rows_of(body, digested.id) == [
        {"qualified": MAIN, "state": "in_digest", "held": True, "takeable": False}]
    assert rows_of(body, withdrawn.id) == [
        {"qualified": MAIN, "state": "withdrawn", "held": False, "takeable": False}]
    assert rows_of(body, expired.id) == [
        {"qualified": MAIN, "state": "expired", "held": False, "takeable": False}]
    assert rows_of(body, had.id) == []
    by_id = {m["message_id"]: m for m in body["messages"]}
    assert by_id[withdrawn.id]["withdrawn"] is True
    assert not any(by_id[i]["withdrawn"] for i in (waiting.id, handed.id, expired.id))


def test_the_operators_message_carries_his_tier(rig, today):
    rig.clock.t = at(today, 0, 1)
    mine = rig.post_as_operator([MAIN], "from him")
    (sent,) = read()["messages"]
    assert sent["message_id"] == mine.id
    assert (sent["author"], sent["author_tier"]) == ("operator", attribution.TIER_OPERATOR)


# --- which store answers what ------------------------------------------------------------

def test_the_read_answers_with_the_daemon_stopped(rig, today):
    sent = post(rig, at(today, 0, 1))
    rig.daemon.stop()
    body = read()
    assert body["state"] == "up" and ids(body) == [sent.id]


def test_no_connection_stays_open_after_the_read_answers(rig, today):
    sent = post(rig, at(today, 0, 1))
    post(rig, at(today - dt.timedelta(days=1), 9))
    assert wait_until(lambda: queued(sent.id))
    rig.daemon.stop()           # its own connections stay as they are from here on
    before = store_fds()
    for _ in range(3):
        read()
        read(limit=1)
    assert store_fds() == before


def test_no_store_is_up_and_empty_and_makes_no_file(monkeypatch):
    monkeypatch.setattr(pagereads, "_today", lambda: dt.date(2026, 11, 15))
    assert not comms_db().exists()
    body = read()
    assert body == {"state": "up", "detail": None, "today": "2026-11-15",
                    "day": "2026-11-15", "messages": [], "more": 0, "earlier": None}
    assert not comms_db().exists()
    assert not comms_db().parent.exists() or not any(comms_db().parent.iterdir())


def test_a_store_that_cannot_be_read_is_state_error_naming_only_the_class(caplog):
    target = comms_db()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"not a database, at a path the page must not show " * 40)
    before = target.read_bytes()
    with caplog.at_level(logging.ERROR, logger="moxi.comms.page"):
        first, second = read(), read()
    assert first == second
    assert first["state"] == "error"
    assert first["detail"] == pagereads.READ_ERROR.format("DatabaseError")
    assert str(target) not in first["detail"]
    assert first["messages"] == [] and first["earlier"] is None
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1
    assert target.read_bytes() == before


def test_a_store_at_another_step_is_state_error_and_not_migrated():
    conn = store.open_write(comms_db())
    try:
        steps = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.execute(f"PRAGMA user_version = {steps - 1}")
    finally:
        conn.close()
    body = read()
    assert (body["state"], body["detail"]) == (
        "error", pagereads.READ_ERROR.format("StepMismatch"))
    conn = sqlite3.connect(comms_db())
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == steps - 1
    finally:
        conn.close()


def test_the_mock_backend_reads_its_file(tmp_path, monkeypatch):
    day = dt.date.today()
    monkeypatch.setattr(pagereads, "_today", lambda: day)
    record_file = tmp_path / "mock-record.json"
    monkeypatch.setenv("MOXI_COMMS_BACKEND", "mock")
    monkeypatch.setenv("MOXI_COMMS_MOCK_BACKEND", str(record_file))
    record = MockRecord(record_file, clock=lambda: at(day, 0, 1))
    try:
        prov = provision(record, comms_db(), operator=("900001", "operator"))
        sent = record.post_as_identity(PLANNER, prov.rooms[NAMES],
                                       machine_line.compose([MAIN], "on the mock"))
    finally:
        record.close()
    body = read()
    assert body["state"] == "up" and ids(body) == [sent.id]
    assert body["messages"][0]["text"] == "on the mock"

