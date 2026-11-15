"""comms' page's `/api/thread` over a rig's record and books.

The messages between two addresses, or by or to one (`All mail with`), matched by the
one rule with no directory query; attribution runs only on the messages sent. A refresh
with `after` reads only the newer messages and sends the books' rows of every recipient
the view shows. The kit (the local-backend rig, the pinned date, `post`, the strict app) is
`test_page_messages.py`'s.
"""

from __future__ import annotations

import datetime as dt
import sys
import urllib.parse

import pytest
from conftest import wait_until

from moxi_comms import attribution, client as client_mod, page, pagereads
from moxi_comms.paths import comms_db

from test_page import run
from test_page_mail_state import rename_operator
from test_page_messages import (  # noqa: F401 -- fixtures, found by pytest by name
    MAIN, PLANNER, at, backend, drop_row, fresh_log, get_with_query, post, prose, queued,
    today)

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="comms runs on Linux only")

OTHER = "main@proj-b"
KEYS = ["state", "detail", "today", "messages", "count_today", "earlier", "changed"]


def read(**query) -> dict:
    status, body = run(get_with_query("/api/thread?" + urllib.parse.urlencode(query)))
    assert status == 200, body
    return body


def ids(body) -> list[str]:
    return [m["message_id"] for m in body["messages"]]


def his(rig, when, to, text="from him", **kw):
    rig.clock.t = when
    return rig.post_as_operator(list(to), text, **kw)


def between(rig, day, count, start_minute=1):
    """`count` messages between MAIN and PLANNER, each way in turn, a minute apart."""
    out = []
    for i in range(count):
        by, to = (MAIN, PLANNER) if i % 2 else (PLANNER, MAIN)
        out.append(post(rig, at(day, 1, start_minute + i), f"m{i}", to=(to,), by=by).id)
    return out


def noise(rig, day):
    """Messages that are not between MAIN and PLANNER."""
    post(rig, at(day, 2, 1), to=(OTHER,), by=MAIN)
    post(rig, at(day, 2, 2), to=(OTHER,), by=PLANNER)
    post(rig, at(day, 2, 3), to=(MAIN, PLANNER), by=OTHER)


# --- the shape ---------------------------------------------------------------------------

def test_the_route_declares_its_query_and_answers_in_the_shared_shape():
    route = next(r for r in page.PAGE.routes if r.path == "/api/thread")
    assert route.kind == "read" and route.gateway is False
    assert list(route.query) == ["a", "b", "before", "after", "limit"]
    assert [k for k, f in route.query.items() if f.required] == ["a"]
    assert route.query["limit"].max == 100
    assert list(route.returns) == KEYS
    assert route.returns["messages"].items is page.MESSAGE
    assert pagereads.THREAD_LIMIT == 6


def test_a_pair_with_no_messages_answers_up_and_empty(rig, today):
    noise(rig, today)
    assert read(a=MAIN, b="herald") == {
        "state": "up", "detail": None, "today": today.isoformat(), "messages": [],
        "count_today": 0, "earlier": None, "changed": None}


# --- the first read ----------------------------------------------------------------------

def test_the_last_six_oldest_first_and_what_came_before(rig, today):
    sent = between(rig, today, 8)
    noise(rig, today)
    body = read(a=MAIN, b=PLANNER)
    assert list(body) == KEYS and (body["state"], body["detail"]) == ("up", None)
    assert ids(body) == sent[2:]
    assert body["earlier"] == {"count": 2, "first_at": at(today, 1, 1)}
    assert body["count_today"] == 8 and body["changed"] is None
    first = body["messages"][0]
    assert list(first) == list(page.MESSAGE.items)
    assert first == {**first, "author": PLANNER, "to": [MAIN], "text": "m2"}
    assert ids(read(a=PLANNER, b=MAIN)) == sent[2:], "either order"


def test_before_reads_back_from_the_oldest_shown(rig, today):
    sent = between(rig, today, 9)
    body = read(a=MAIN, b=PLANNER, limit=4)
    assert ids(body) == sent[5:] and body["earlier"]["count"] == 5
    body = read(a=MAIN, b=PLANNER, before=ids(body)[0], limit=20)
    assert ids(body) == sent[:5] and body["earlier"] is None
    assert body["count_today"] == 9


def test_a_thread_is_read_back_through_the_record_a_page_at_a_time(rig, today,
                                                                  monkeypatch):
    monkeypatch.setattr(pagereads, "THREAD_PAGE", 3)
    yesterday = today - dt.timedelta(days=1)
    old = between(rig, yesterday, 5)
    noise(rig, yesterday)
    new = between(rig, today, 4)
    noise(rig, today)
    body = read(a=MAIN, b=PLANNER)
    assert ids(body) == old[3:] + new
    assert body["earlier"] == {"count": 3, "first_at": at(yesterday, 1, 1)}
    assert body["count_today"] == 4


def test_count_today_counts_only_todays_messages_of_the_pair(rig, today):
    between(rig, today - dt.timedelta(days=1), 3)
    between(rig, today, 2)
    noise(rig, today)
    body = read(a=MAIN, b=PLANNER)
    assert body["count_today"] == 2 and len(body["messages"]) == 5


def test_tags_name_the_other_by_the_one_rule(rig, today):
    scoped = post(rig, at(today, 1, 1), to=("planner@proj-a",), by=MAIN)
    bare = post(rig, at(today, 1, 2), to=("planner",), by=MAIN)     # the global planner
    among = post(rig, at(today, 1, 3), to=(OTHER, MAIN), by=PLANNER)
    other_scope = post(rig, at(today, 1, 4), to=(OTHER,), by=MAIN)
    assert ids(read(a=MAIN, b=PLANNER)) == [scoped.id, among.id]
    assert ids(read(a=MAIN, b=OTHER)) == [other_scope.id], "by one, to the other"
    assert ids(read(a=MAIN, b="planner")) == [bare.id]


def test_all_mail_with_one_address_is_every_message_by_or_to_it(rig, today):
    to_main = post(rig, at(today, 1, 1), to=(MAIN,), by=PLANNER)
    by_main = post(rig, at(today, 1, 2), to=(OTHER,), by=MAIN)
    among = post(rig, at(today, 1, 3), to=(PLANNER, MAIN), by=OTHER)
    post(rig, at(today, 1, 4), to=(OTHER,), by=PLANNER)
    post(rig, at(today, 1, 5), to=("main",), by=PLANNER)     # the global main
    body = read(a=MAIN)
    assert ids(body) == [to_main.id, by_main.id, among.id]
    assert body["count_today"] == 3


def test_a_prose_message_is_neither_listed_nor_counted(rig, today):
    first = post(rig, at(today, 1, 1), to=(PLANNER,), by=MAIN)
    rig.clock.t = at(today, 1, 2)
    rig.record.post_as_identity(MAIN, rig.names_channel, "planner@proj-a: just words")
    prose(rig, at(today, 1, 3))
    last = post(rig, at(today, 1, 4), to=(MAIN,), by=PLANNER)
    body = read(a=MAIN, b=PLANNER, limit=1)
    assert ids(body) == [last.id]
    assert body["earlier"]["count"] == 1 and body["count_today"] == 2
    assert ids(read(a=MAIN, b=PLANNER)) == [first.id, last.id]


def test_the_operator_is_a_side_by_his_name_or_operator(rig, today):
    asked = post(rig, at(today, 1, 1), to=("operator",), by=MAIN)
    mine = his(rig, at(today, 1, 2), [MAIN])
    his(rig, at(today, 1, 3), [PLANNER])
    assert ids(read(a=MAIN, b="operator")) == [asked.id, mine.id]
    body = read(a="operator")
    assert len(body["messages"]) == 3
    assert body["messages"][1]["author_tier"] == attribution.TIER_OPERATOR
    rename_operator("the-desk")
    assert ids(read(a="the-desk", b=MAIN)) == [asked.id, mine.id]
    assert len(ids(read(a="the-desk"))) == 3


def test_attribution_runs_only_on_the_messages_sent(rig, today, monkeypatch):
    between(rig, today, 10)
    noise(rig, today)
    his(rig, at(today, 3), [MAIN])
    rig.daemon.stop()           # its routing attributes the messages too
    called = []
    real = attribution.from_record

    def counted(conn, msg):
        called.append(msg.id)
        return real(conn, msg)

    monkeypatch.setattr(attribution, "from_record", counted)
    body = read(a=MAIN, b=PLANNER)
    assert sorted(called) == sorted(ids(body)) and len(called) == 6
    called.clear()
    body = read(a=MAIN)
    assert sorted(called) == sorted(ids(body))


# --- a refresh ---------------------------------------------------------------------------

def test_a_refresh_reads_only_the_newer_messages(rig, today):
    sent = between(rig, today, 8)
    newest = read(a=MAIN, b=PLANNER)["messages"][-1]["message_id"]
    newer = between(rig, today, 2, start_minute=20)
    noise(rig, today)
    body = read(a=MAIN, b=PLANNER, after=newest)
    assert list(body) == KEYS and body["state"] == "up"
    assert ids(body) == newer and body["earlier"] is None
    assert body["count_today"] == 10
    assert isinstance(body["changed"], list)
    assert ids(read(a=MAIN, b=PLANNER, after=newer[-1])) == []
    assert newest == sent[-1]


def test_a_refresh_carries_the_rows_of_a_shown_message_until_it_is_read(rig, today):
    """A shown message's `not read yet` clears on the next refresh: its row for the
    pair is in `changed` while the books hold it, and gone once the name has read it."""
    elsewhere = post(rig, at(today, 1, 0), "from another", to=(MAIN,), by=OTHER)
    unread = post(rig, at(today, 1, 1), "for main", to=(MAIN,), by=PLANNER)
    taken = post(rig, at(today, 1, 2), "taken back", to=(MAIN,), by=PLANNER)
    late = post(rig, at(today, 1, 3), "too late", to=(PLANNER,), by=MAIN,
                expires_at=int(at(today, 1, 2)))
    for m in (unread, taken, elsewhere):
        assert wait_until(lambda m=m: queued(m.id))
    assert wait_until(lambda: rig.daemon.books.dropped_reason(PLANNER, late.id))
    rig.daemon.books.withdraw(taken.id)
    newest = read(a=MAIN, b=PLANNER)["messages"][-1]["message_id"]
    assert newest == late.id
    changed = {c["message_id"]: c["rows"] for c in
               read(a=MAIN, b=PLANNER, after=newest)["changed"]}
    assert changed[unread.id] == [
        {"qualified": MAIN, "state": "waiting", "held": False, "takeable": True}]
    assert [r["state"] for r in changed[taken.id]] == ["withdrawn"]
    assert [(r["qualified"], r["state"]) for r in changed[late.id]] == [
        (PLANNER, "expired")]
    assert elsewhere.id in changed, "every row of the two addresses at or below after"
    drop_row(unread.id)
    changed = read(a=MAIN, b=PLANNER, after=newest)["changed"]
    assert unread.id not in [c["message_id"] for c in changed]


def test_changed_holds_only_the_two_addresses_rows_at_or_below_after(rig, today):
    both = post(rig, at(today, 1, 1), to=(MAIN, OTHER), by=PLANNER)
    above = post(rig, at(today, 1, 2), to=(MAIN,), by=PLANNER)
    for name in (MAIN, OTHER):
        assert wait_until(lambda name=name: queued(both.id, name))
    assert wait_until(lambda: queued(above.id))
    changed = read(a=MAIN, b=PLANNER, after=both.id)["changed"]
    assert changed == [{"message_id": both.id, "rows": [
        {"qualified": MAIN, "state": "waiting", "held": False, "takeable": True}]}]
    assert [r["qualified"] for r in read(a=OTHER, after=both.id)["changed"][0]["rows"]] == [
        MAIN, OTHER], "all mail with one name carries every row of its messages"


def test_all_mail_with_one_name_refreshes_every_recipients_row(rig, today):
    """In `All mail with` a message's every recipient is shown, so a refresh carries
    each one's row: a message by `a` to another name clears when that name reads it."""
    sent = post(rig, at(today, 1, 1), "to the other", to=(OTHER,), by=MAIN)
    among = post(rig, at(today, 1, 2), "to both", to=(MAIN, OTHER), by=PLANNER)
    post(rig, at(today, 1, 3), "elsewhere", to=(OTHER,), by=PLANNER)
    for mid in (sent.id, among.id):
        assert wait_until(lambda mid=mid: queued(mid, OTHER))
    assert wait_until(lambda: queued(among.id, MAIN))
    drop_row(among.id, MAIN)
    newest = read(a=MAIN)["messages"][-1]["message_id"]
    assert newest == among.id
    changed = {c["message_id"]: [(r["qualified"], r["state"]) for r in c["rows"]]
               for c in read(a=MAIN, after=newest)["changed"]}
    assert changed == {sent.id: [(OTHER, "waiting")], among.id: [(OTHER, "waiting")]}
    drop_row(sent.id, OTHER)
    changed = read(a=MAIN, after=newest)["changed"]
    assert [c["message_id"] for c in changed] == [among.id], "read by the other: cleared"
    assert read(a=MAIN, b=PLANNER, after=newest)["changed"] == [], (
        "a thread between two names carries only their rows")


# --- the query and the store -------------------------------------------------------------

@pytest.mark.parametrize("query", [
    {"a": ""}, {"a": MAIN, "b": ""}, {"a": MAIN, "before": "12a"},
    {"a": MAIN, "after": "-1"}, {"a": MAIN, "before": "5", "after": "3"},
    {"a": MAIN, "limit": "0"},
])
def test_a_query_the_page_never_sends_is_state_error_and_opens_nothing(
        rig, today, monkeypatch, query):
    def refused(*a, **kw):
        raise AssertionError("opened the store")

    monkeypatch.setattr(pagereads, "open_read", refused)
    assert read(**query) == {"state": "error", "detail": pagereads.BAD_QUERY,
                             "today": today.isoformat(), "messages": [], "count_today": 0,
                             "earlier": None, "changed": None}


@pytest.mark.parametrize("query", ["", "?b=main", "?a=main&limit=101"])
def test_a_read_with_no_a_or_a_limit_over_100_is_refused_by_the_route(rig, today, query):
    status, _ = run(get_with_query("/api/thread" + query))
    assert status == 400


def test_the_read_asks_the_daemon_nothing_and_answers_with_it_stopped(rig, today,
                                                                     monkeypatch):
    sent = between(rig, today, 2)
    rig.daemon.stop()

    def no_call(*a, **kw):
        raise AssertionError("asked the daemon")

    monkeypatch.setattr(client_mod.DaemonClient, "__init__", no_call)
    body = read(a=MAIN, b=PLANNER)
    assert body["state"] == "up" and ids(body) == sent
    assert read(a=MAIN, b=PLANNER, after=sent[0])["state"] == "up"


def test_no_store_is_up_and_empty_and_makes_no_file(monkeypatch):
    monkeypatch.setattr(pagereads, "_today", lambda: dt.date(2026, 11, 15))
    assert read(a=MAIN) == {"state": "up", "detail": None, "today": "2026-11-15",
                            "messages": [], "count_today": 0, "earlier": None,
                            "changed": None}
    assert not comms_db().exists()


def test_a_store_that_cannot_be_read_is_state_error():
    comms_db().parent.mkdir(parents=True, exist_ok=True)
    comms_db().write_bytes(b"not a database " * 100)
    body = read(a=MAIN, b=PLANNER)
    assert (body["state"], body["detail"]) == (
        "error", pagereads.READ_ERROR.format("DatabaseError"))
    assert (body["messages"], body["earlier"], body["changed"]) == ([], None, None)
