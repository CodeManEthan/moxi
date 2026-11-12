"""Keep-warm cover, the record a press writes: `cover` read back after `cover_on` and
`cover_off`, for one launch and one session id only, the newest event winning; `on` as the
launch value or cover; the ask's first read under cover and without it; and the hold verbs,
which keep to the launch value."""

from __future__ import annotations

import pytest
from moxi import exit as codes
from moxi import modules
from test_keepwarm import (NEW, OTHER, SID, FakeCache, FakeSession, FakeSlots, FakeTurns, ask,
                           conn, launched, verb)

from moxi_launcher import api, keepwarm, relaunch, store

__all__ = ["conn"]

WAITS = {"kind": "subagents", "count": 2}


def off(conn, request_id="r1"):
    """The launch's value set to `off`, as a launch made with keep-warm off has it."""
    conn.execute("UPDATE launches SET values_json = '{\"keep_warm\": \"off\"}' "
                 "WHERE request_id = ?", (request_id,))


def row(conn, request_id="r1"):
    return store.get(conn, request_id)


def covered(conn, sid=SID, request_id="r1", **kw):
    kw.setdefault("turn_number", 4)
    kw.setdefault("waits", WAITS)
    return keepwarm.cover_on(conn, row(conn, request_id), sid, **kw)


def cover_events(conn, request_id="r1"):
    return [e for e in store.events(conn, request_id) if e.kind == keepwarm.COVER]


# -- the record ----------------------------------------------------------------------------


def test_cover_on_writes_one_event_and_cover_reads_it_back(conn):
    made = covered(conn)
    assert made == keepwarm.Cover("r1", SID, made.at)
    assert keepwarm.cover(conn, row(conn), SID) == made
    [event] = cover_events(conn)
    assert event.at == made.at and event.session_id == SID and event.request_id == "r1"
    assert event.detail == {"state": "on", "by": "page", "session_id": SID,
                            "turn_number": 4, "waits": {"kind": "subagents", "count": 2}}


def test_cover_on_records_who_pressed_and_only_the_kind_and_count_of_the_waits(conn):
    covered(conn, by="cli", waits={"kind": "agents", "count": 1, "names": ["x"]})
    [event] = cover_events(conn)
    assert event.detail["by"] == "cli" and event.detail["waits"] == {"kind": "agents",
                                                                      "count": 1}


def test_a_launch_with_no_cover_event_has_no_cover(conn):
    assert keepwarm.cover(conn, row(conn), SID) is None


def test_cover_off_ends_the_cover_and_says_why(conn):
    covered(conn)
    assert keepwarm.cover_off(conn, row(conn), SID, "idle") is True
    assert keepwarm.cover(conn, row(conn), SID) is None
    assert cover_events(conn)[-1].detail == {"state": "off", "why": "idle", "session_id": SID}


def test_cover_off_without_cover_writes_nothing(conn):
    assert keepwarm.cover_off(conn, row(conn), SID, "idle") is False
    covered(conn)
    assert keepwarm.cover_off(conn, row(conn), NEW, "idle") is False
    assert keepwarm.cover_off(conn, row(conn), SID, "idle") is True
    assert keepwarm.cover_off(conn, row(conn), SID, "idle") is False
    assert [e.detail["state"] for e in cover_events(conn)] == ["on", "off"]


def test_cover_belongs_to_one_session_id(conn):
    covered(conn)
    assert keepwarm.cover(conn, row(conn), NEW) is None
    assert keepwarm.cover(conn, row(conn), SID) is not None


def test_a_session_back_at_an_id_whose_cover_is_newest_reads_covered_again(conn):
    covered(conn)
    store.learn_session(conn, "r1", NEW, "clear")
    assert keepwarm.cover(conn, row(conn), NEW) is None
    assert keepwarm.cover(conn, row(conn), SID).session_id == SID


def test_the_newest_cover_event_wins(conn):
    covered(conn)
    covered(conn, sid=NEW)
    assert keepwarm.cover(conn, row(conn), SID) is None
    assert keepwarm.cover(conn, row(conn), NEW) is not None
    keepwarm.cover_off(conn, row(conn), NEW, "idle")
    again = covered(conn, turn_number=9)
    assert keepwarm.cover(conn, row(conn), SID) == again


def test_events_of_another_kind_or_launch_do_not_read_as_cover(conn):
    covered(conn)
    store.add_event(conn, "recycle-told", {"state": "off"}, request_id="r1")
    assert keepwarm.cover(conn, row(conn), SID) is not None
    launched(conn, request_id="r2", session_id=OTHER, claimed=False)
    assert keepwarm.cover(conn, row(conn, "r2"), SID) is None


@pytest.mark.parametrize("link", ["relaunch_of", "resume_of"])
def test_a_relaunch_or_a_resume_starts_without_the_cover(conn, link):
    covered(conn)
    launched(conn, request_id="r2", session_id=NEW, claimed=False, **{link: "r1"})
    assert keepwarm.cover(conn, row(conn, "r2"), NEW) is None
    assert keepwarm.cover(conn, row(conn, "r2"), SID) is None


def test_on_is_the_launch_value_or_cover(conn):
    assert keepwarm.on(conn, row(conn), SID)
    off(conn)
    assert not keepwarm.on(conn, row(conn), SID)
    covered(conn)
    assert keepwarm.on(conn, row(conn), SID)
    assert not keepwarm.on(conn, row(conn), NEW)


def test_the_record_adds_no_store_step():
    assert len(store.STEPS) == 2


# -- the ask's first read ------------------------------------------------------------------


class CountingTurns(FakeTurns):
    def __init__(self, session):
        super().__init__(session)
        self.finds = 0

    def find(self, **kw):
        self.finds += 1
        return super().find(**kw)


def test_an_ask_about_a_launch_not_on_ends_off_and_calls_nothing(conn, monkeypatch):
    off(conn)
    loaded = []
    monkeypatch.setattr(modules, "load", lambda *a, **kw: loaded.append(a))
    session, cache = FakeSession(), FakeCache()
    turns = CountingTurns(session)
    verdict = keepwarm.ask(SID, conn=conn, turns=turns, slots_api=FakeSlots(), cache=cache,
                           settings={})
    assert (verdict.outcome, verdict.word) == (None, "off")
    assert turns.finds == 0 and cache.reads == [] and loaded == []
    assert session.delivered == [] and store.runs(conn, request_id="r1") == []


def test_an_ask_under_cover_with_the_launch_value_off_reads_on(conn):
    off(conn)
    covered(conn)
    got = ask(conn)
    assert (got.verdict.outcome, got.verdict.word) == ("nudged", "subagents")


def test_an_ask_about_a_session_id_the_cover_does_not_name_ends_off(conn):
    off(conn)
    covered(conn, sid=NEW)
    got = ask(conn)
    assert (got.verdict.outcome, got.verdict.word) == (None, "off")
    assert got.session.delivered == [] and got.runs == []


# -- holds keep to the launch value ----------------------------------------------------------


@pytest.mark.parametrize("fn, args", [
    (keepwarm.hold, ("x",)), (keepwarm.release, ("x",)), (keepwarm.holds, ()),
])
def test_every_hold_verb_is_refused_on_a_launch_with_cover_alone(conn, fn, args):
    off(conn)
    covered(conn)
    with pytest.raises(relaunch.Refused) as refused:
        verb(fn, conn, *args)
    assert refused.value.code == "off" and refused.value.exit_code == codes.REFUSED
    assert conn.execute("SELECT count(*) FROM warm_holds").fetchone()[0] == 0


def test_the_api_refuses_a_hold_on_a_launch_with_cover_alone(conn):
    off(conn)
    covered(conn)
    with pytest.raises(relaunch.Refused) as refused:
        api.open_hold("r1", "x", conn=conn)
    assert refused.value.code == "off"
    with pytest.raises(relaunch.Refused):
        api.release_hold("r1", "x", conn=conn)
