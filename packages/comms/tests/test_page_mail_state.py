"""comms' page's `/api/mail-state` over a rig's record and books.

What waits for the operator's reply (`owed`) and the open asks between agents (`asks`)
are decided by `replies`, the daemon's own rule, applied over the record with no daemon
call. The kit (the local-backend rig, the pinned date, `post`, the strict app) is
`test_page_messages.py`'s.
"""

from __future__ import annotations

import datetime as dt
import sys
import time
import urllib.parse

import pytest
from conftest import wait_until

from moxi.store import transaction
from moxi_comms import client as client_mod, constants, machine_line, page, pagereads, store
from moxi_comms.paths import comms_db

from test_page import run
from test_page_messages import (  # noqa: F401 -- fixtures, found by pytest by name
    MAIN, backend, drop_row, fresh_log, get_with_query, queued, set_row, today)

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="comms runs on Linux only")

OTHER = "main@proj-b"
KEYS = ["state", "detail", "today", "queued", "owed", "asks", "last", "operator_names"]


def read() -> dict:
    status, body = run(get_with_query("/api/mail-state"))
    assert status == 200, body
    return body


def post(rig, text="hi", *, to=(MAIN,), by=OTHER, ago_s=0.0, **kw):
    """An agent's post, `ago_s` before now."""
    rig.clock.t = time.time() - ago_s
    return rig.record.post_as_identity(
        by, rig.names_channel, machine_line.compose(list(to), text, **kw))


def ask(rig, text="which way?", *, to=("operator",), by=OTHER, ago_s=0.0, **kw):
    return post(rig, text, to=to, by=by, ago_s=ago_s, posture="once", **kw)


def his(rig, to, text="noted", **kw):
    """The operator's post, on his own lane."""
    rig.clock.t = time.time()
    return rig.record.post_as_user(rig.prov.operator_user_id, rig.names_channel,
                                   machine_line.compose(list(to), text, **kw),
                                   token=rig.operator_token)


def agent(qualified: str) -> dict:
    """A `queued` author that is not the operator."""
    return {"qualified": qualified, "operator": False}


def owed_ids(body) -> list[str]:
    return [o["message_id"] for o in body["owed"]]


def rename_operator(name: str) -> None:
    conn = store.open_write()
    try:
        with transaction(conn):
            conn.execute("UPDATE operators SET name = ?", (name,))
    finally:
        conn.close()


# --- the shape -----------------------------------------------------------------------------

def test_the_route_is_a_read_with_no_query_and_marks_only_the_text():
    route = next(r for r in page.PAGE.routes if r.path == "/api/mail-state")
    assert route.kind == "read" and route.gateway is False and not route.query
    assert list(route.returns) == KEYS
    owed = page.MAIL_STATE["owed"].items.items
    assert {key for key, out in owed.items() if out.mark} == {"text"}
    for key in ("queued", "asks", "last"):
        assert not any(out.mark for out in page.MAIL_STATE[key].items.items.values())


def test_an_empty_record_answers_up_with_empty_lists(rig, today):
    body = read()
    assert body == {"state": "up", "detail": None, "today": today.isoformat(),
                    "queued": [], "owed": [], "asks": [], "last": [],
                    "operator_names": ["operator"]}


# --- owed ------------------------------------------------------------------------------------

def test_a_verified_once_ask_to_him_is_owed(rig, today):
    asked = ask(rig, "which way, left or right?", ago_s=60)
    body = read()
    assert list(body) == KEYS and body["state"] == "up"
    (owed,) = body["owed"]
    assert {**owed, "at": 0} == {"message_id": asked.id, "author": OTHER, "at": 0,
                                 "text": "which way, left or right?"}
    assert abs(owed["at"] - (time.time() - 60)) < 2


def test_owed_is_oldest_first(rig, today):
    first = ask(rig, ago_s=120)
    second = ask(rig, by="planner@proj-a", ago_s=60)
    assert owed_ids(read()) == [first.id, second.id]


def test_his_threaded_reply_answers_whatever_its_posture(rig, today):
    asked = ask(rig, ago_s=60)
    his(rig, [OTHER], "left", reply_to=asked.id, posture="none")
    assert read()["owed"] == []


def test_his_unthreaded_open_message_to_the_asker_answers(rig, today):
    ask(rig, ago_s=60)
    his(rig, [OTHER], "left")
    assert read()["owed"] == []


def test_his_unthreaded_no_reply_message_does_not_answer(rig, today):
    asked = ask(rig, ago_s=60)
    his(rig, [OTHER], "fyi", posture="none")
    assert owed_ids(read()) == [asked.id]


def test_his_message_to_someone_else_does_not_answer(rig, today):
    asked = ask(rig, ago_s=60)
    his(rig, ["planner@proj-a"], "left")
    assert owed_ids(read()) == [asked.id]


def test_his_message_before_the_ask_does_not_answer(rig, today):
    his(rig, [OTHER], "earlier word")
    asked = ask(rig)
    assert owed_ids(read()) == [asked.id]


def test_an_ask_past_the_ask_ttl_is_not_owed(rig, today):
    ask(rig, ago_s=constants.ASK_TTL + 120)
    kept = ask(rig, ago_s=constants.ASK_TTL - 120)
    assert owed_ids(read()) == [kept.id]


def test_a_labeled_author_is_not_owed(rig, today):
    rig.clock.t = time.time()
    rig.record._post("webhook", "someone", rig.names_channel,
                     machine_line.compose(["operator"], "a labeled ask", posture="once"))
    assert read()["owed"] == []


@pytest.mark.parametrize("posture", ["open", "optional", "none"])
def test_only_a_once_message_is_owed(rig, today, posture):
    post(rig, "asks nothing back", to=("operator",), posture=posture)
    assert read()["owed"] == []


def test_an_ask_that_names_him_among_others_is_owed(rig, today):
    asked = ask(rig, to=("planner@proj-a", "operator"))
    assert owed_ids(read()) == [asked.id]


def test_he_answers_under_a_configured_name_other_than_operator(rig, today):
    rename_operator("the-desk")
    answered = ask(rig, "first", ago_s=120)
    waiting = ask(rig, "second", by="planner@proj-a", ago_s=60)
    his(rig, [OTHER], "done", reply_to=answered.id, posture="none")
    body = read()
    assert owed_ids(body) == [waiting.id]
    assert "the-desk" in [entry["qualified"] for entry in body["last"]]


# --- asks between agents -------------------------------------------------------------------

def test_an_open_ask_between_agents_and_whether_it_was_read(rig, today):
    asked = ask(rig, to=(MAIN,), ago_s=30)
    assert wait_until(lambda: queued(asked.id))
    body = read()
    assert [{k: a[k] for k in ("asker", "awaited", "message_id", "read")}
            for a in body["asks"]] == [{"asker": OTHER, "awaited": MAIN,
                                        "message_id": asked.id, "read": False}]
    assert abs(body["asks"][0]["at"] - (time.time() - 30)) < 2
    drop_row(asked.id)
    assert read()["asks"][0]["read"] is None


def test_the_awaited_agents_answer_ends_the_ask(rig, today):
    asked = ask(rig, to=(MAIN,), ago_s=30)
    post(rig, "here", to=(OTHER,), by=MAIN, reply_to=asked.id, posture="none")
    assert read()["asks"] == []


def test_the_newest_ask_per_pair_is_kept(rig, today):
    ask(rig, "first", to=(MAIN,), ago_s=60)
    newer = ask(rig, "second", to=(MAIN,), ago_s=30)
    other = ask(rig, "third", to=(OTHER,), by="planner@proj-a")
    pairs = [(a["asker"], a["awaited"], a["message_id"]) for a in read()["asks"]]
    assert pairs == [(OTHER, MAIN, newer.id), ("planner@proj-a", OTHER, other.id)]


def test_an_ask_back_answers_the_ask_it_names(rig, today):
    """An ask of its own to the asker is a posture other than `[no reply]`, so by the
    daemon's rule it answers the first ask, and only its own stands."""
    ask(rig, "first", to=(MAIN,), ago_s=60)
    back = ask(rig, "and you?", to=(OTHER,), by=MAIN)
    pairs = [(a["asker"], a["awaited"], a["message_id"]) for a in read()["asks"]]
    assert pairs == [(MAIN, OTHER, back.id)]


def test_an_ask_to_him_or_to_itself_is_not_an_ask_between_agents(rig, today):
    ask(rig, to=("operator", OTHER))
    assert read()["asks"] == []


def test_an_ask_tags_its_awaited_name_by_the_one_rule(rig, today):
    """A bare tag names the global name: `main` is not `main@proj-a`."""
    asked = ask(rig, to=("main",))
    post(rig, "answered as the scoped name", to=(OTHER,), by=MAIN)
    assert [(a["awaited"], a["message_id"]) for a in read()["asks"]] == [("main", asked.id)]


# --- queued and last -----------------------------------------------------------------------

def test_queued_counts_rows_and_names_the_waiting_rows_authors_in_order(rig, today):
    one = post(rig, "a", by="planner@proj-a", ago_s=30)
    two = post(rig, "b", by=OTHER, ago_s=20)
    three = post(rig, "c", by="planner@proj-a", ago_s=10)
    for m in (one, two, three):
        assert wait_until(lambda m=m: queued(m.id))
    body = read()
    assert body["queued"] == [{"qualified": MAIN, "waiting": 3, "handed": 0,
                               "from": [agent("planner@proj-a"), agent(OTHER)]}]
    set_row(one.id, state="handed")
    set_row(two.id, state="in_digest", digested_once=1)
    assert read()["queued"] == [{"qualified": MAIN, "waiting": 1, "handed": 2,
                                 "from": [agent("planner@proj-a")]}]


def test_queued_names_an_author_from_before_the_read(rig, today):
    old = post(rig, "from long ago", by="planner@proj-a",
               ago_s=constants.ASK_TTL + 3600)
    assert wait_until(lambda: queued(old.id))
    body = read()
    assert body["queued"] == [{"qualified": MAIN, "waiting": 1, "handed": 0,
                               "from": [agent("planner@proj-a")]}]
    assert body["last"] == []


@pytest.mark.parametrize("name", ["operator", "the-desk"])
def test_queued_marks_his_own_waiting_message_as_the_operators(rig, today, name):
    if name != "operator":
        rename_operator(name)
    first = post(rig, "a", by="planner@proj-a", ago_s=30)
    mine = his(rig, [MAIN], "from the desk")
    for m in (first, mine):
        assert wait_until(lambda m=m: queued(m.id))
    assert read()["queued"] == [{"qualified": MAIN, "waiting": 2, "handed": 0,
                                 "from": [agent("planner@proj-a"),
                                          {"qualified": name, "operator": True}]}]


def test_operator_names_are_his_names_whatever_waits(rig, today):
    assert read()["operator_names"] == ["operator"]
    rename_operator("the-desk")
    assert read()["operator_names"] == ["operator", "the-desk"]


def test_last_is_each_addresss_newest_message_by_it_or_to_it(rig, today):
    post(rig, "a", to=(MAIN,), by=OTHER, ago_s=300)
    post(rig, "b", to=(MAIN, "herald"), by="planner@proj-a", ago_s=200)
    newest = time.time() - 100
    post(rig, "c", to=(OTHER,), by=MAIN, ago_s=100)
    last = {entry["qualified"]: entry["at"] for entry in read()["last"]}
    assert set(last) == {MAIN, OTHER, "planner@proj-a", "herald"}
    assert abs(last[MAIN] - newest) < 2 and abs(last[OTHER] - newest) < 2
    assert abs(last["herald"] - (time.time() - 200)) < 2


# --- with the daemon down, and the store ---------------------------------------------------

def test_the_read_asks_the_daemon_nothing_and_answers_with_it_stopped(rig, today,
                                                                     monkeypatch):
    asked = ask(rig, ago_s=60)
    rig.daemon.stop()

    def no_call(*a, **kw):
        raise AssertionError("asked the daemon")

    monkeypatch.setattr(client_mod.DaemonClient, "__init__", no_call)
    body = read()
    assert body["state"] == "up" and owed_ids(body) == [asked.id]


def test_no_store_is_up_and_empty(monkeypatch):
    monkeypatch.setattr(pagereads, "_today", lambda: dt.date(2026, 11, 15))
    body = read()
    assert (body["state"], body["queued"], body["owed"], body["asks"], body["last"],
            body["operator_names"]) == ("up", [], [], [], [], ["operator"])
    assert not comms_db().exists()


def test_a_store_that_cannot_be_read_is_state_error(monkeypatch):
    comms_db().parent.mkdir(parents=True, exist_ok=True)
    comms_db().write_bytes(b"not a database " * 100)
    body = read()
    assert (body["state"], body["detail"]) == (
        "error", pagereads.READ_ERROR.format("DatabaseError"))
    assert (body["queued"], body["owed"], body["asks"], body["last"]) == ([], [], [], [])


def test_query_keys_are_refused_by_the_route(rig, today):
    status, _ = run(get_with_query("/api/mail-state?" + urllib.parse.urlencode(
        {"day": "2026-11-15"})))
    assert status == 400
