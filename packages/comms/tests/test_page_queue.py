"""comms' page's `/api/queue` over a rig's record and books.

One address's messages in the books' `queue`, for the opened agent's held, waiting and
unread mail. The kit (the local-backend rig, the pinned date, `post`, the strict app) is
`test_page_messages.py`'s.
"""

from __future__ import annotations

import datetime as dt
import sys
import urllib.parse

import pytest
from conftest import wait_until

from moxi_comms import page, pagereads
from moxi_comms.paths import comms_db

from test_page import run
from test_page_messages import (  # noqa: F401 -- fixtures, found by pytest by name
    MAIN, PLANNER, at, backend, drop_row, fresh_log, get_with_query, post, queued, set_row,
    today)

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="comms runs on Linux only")

OTHER = "main@proj-b"
KEYS = ["state", "detail", "messages", "more"]


def read(**query) -> dict:
    status, body = run(get_with_query("/api/queue?" + urllib.parse.urlencode(query)))
    assert status == 200, body
    return body


def ids(body) -> list[str]:
    return [m["message_id"] for m in body["messages"]]


def posted(rig, when, text="hi", *, to=(MAIN,), **kw):
    """A post whose rows the daemon has routed into the books."""
    sent = post(rig, when, text, to=to, **kw)
    for name in to:
        assert wait_until(lambda name=name: queued(sent.id, name))
    return sent


# --- the shape ---------------------------------------------------------------------------

def test_the_route_takes_a_name_and_answers_messages_in_the_shared_shape():
    route = next(r for r in page.PAGE.routes if r.path == "/api/queue")
    assert route.kind == "read" and route.gateway is False
    assert list(route.query) == ["name"] and route.query["name"].required
    assert list(route.returns) == KEYS
    assert route.returns["messages"].items is page.MESSAGE
    assert pagereads.QUEUE_CAP == 50


def test_an_address_with_no_rows_answers_up_and_empty(rig, today):
    posted(rig, at(today, 0, 1), to=(OTHER,))
    assert read(name=MAIN) == {"state": "up", "detail": None, "messages": [], "more": 0}


# --- the rows ----------------------------------------------------------------------------

def test_every_queue_row_of_the_address_oldest_first_and_nothing_else(rig, today):
    waiting = posted(rig, at(today, 0, 1), "waits")
    handed = posted(rig, at(today, 0, 2), "in its session")
    digested = posted(rig, at(today, 0, 3), "in a digest")
    held = posted(rig, at(today, 0, 4), "held")
    posted(rig, at(today, 0, 5), "to someone else", to=(OTHER,))
    read_already = posted(rig, at(today, 0, 6), "read already")
    taken_back = posted(rig, at(today, 0, 7), "taken back")
    set_row(handed.id, state="handed")
    set_row(digested.id, state="in_digest", digested_once=1)
    set_row(held.id, held=1)
    drop_row(read_already.id)
    rig.daemon.books.withdraw(taken_back.id)
    body = read(name=MAIN)
    assert list(body) == KEYS and (body["state"], body["detail"]) == ("up", None)
    assert ids(body) == [waiting.id, handed.id, digested.id, held.id]
    assert body["more"] == 0
    states = [(m["rows"][0]["state"], m["rows"][0]["held"], m["rows"][0]["takeable"])
              for m in body["messages"]]
    assert states == [("waiting", False, True), ("handed", False, True),
                      ("in_digest", False, False), ("waiting", True, True)]
    first = body["messages"][0]
    assert list(first) == list(page.MESSAGE.items)
    assert first == {**first, "at": at(today, 0, 1), "author": PLANNER, "to": [MAIN],
                     "text": "waits", "withdrawn": False}


def test_the_addresss_row_comes_first_and_the_other_rows_follow(rig, today):
    both = posted(rig, at(today, 0, 1), to=(MAIN, OTHER))
    (sent,) = read(name=OTHER)["messages"]
    assert sent["message_id"] == both.id
    assert [row["qualified"] for row in sent["rows"]] == [OTHER, MAIN]
    (sent,) = read(name=MAIN)["messages"]
    assert [row["qualified"] for row in sent["rows"]] == [MAIN, OTHER]


def test_the_cap_sends_the_oldest_and_more_counts_the_rest(rig, today, monkeypatch):
    monkeypatch.setattr(pagereads, "QUEUE_CAP", 2)
    sent = [posted(rig, at(today, 0, minute), f"m{minute}").id for minute in range(1, 6)]
    body = read(name=MAIN)
    assert ids(body) == sent[:2] and body["more"] == 3


def test_a_name_with_mail_from_an_earlier_day_reads_it(rig, today):
    old = posted(rig, at(today - dt.timedelta(days=3), 9), "from days ago")
    new = posted(rig, at(today, 0, 1))
    assert ids(read(name=MAIN)) == [old.id, new.id]


# --- the query and the store -------------------------------------------------------------

def test_a_read_with_no_name_is_refused_by_the_route(rig, today):
    status, _ = run(get_with_query("/api/queue"))
    assert status == 400


def test_an_empty_name_is_state_error_and_opens_nothing(rig, today, monkeypatch):
    def refused(*a, **kw):
        raise AssertionError("opened the store")

    monkeypatch.setattr(pagereads, "open_read", refused)
    assert read(name="") == {"state": "error", "detail": pagereads.BAD_QUERY,
                             "messages": [], "more": 0}


def test_the_read_answers_with_the_daemon_stopped(rig, today):
    sent = posted(rig, at(today, 0, 1))
    rig.daemon.stop()
    body = read(name=MAIN)
    assert body["state"] == "up" and ids(body) == [sent.id]


def test_no_store_is_up_and_empty_and_makes_no_file():
    assert read(name=MAIN) == {"state": "up", "detail": None, "messages": [], "more": 0}
    assert not comms_db().exists()


def test_a_store_that_cannot_be_read_is_state_error():
    comms_db().parent.mkdir(parents=True, exist_ok=True)
    comms_db().write_bytes(b"not a database " * 100)
    assert read(name=MAIN) == {"state": "error",
                               "detail": pagereads.READ_ERROR.format("DatabaseError"),
                               "messages": [], "more": 0}
