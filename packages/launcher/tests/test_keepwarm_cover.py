"""Keep-warm cover, the record a press writes: `cover` read back after `cover_on` and
`cover_off`, for one launch and one session id only, the newest event winning; `on` as the
launch value or cover; the ask's first read under cover and without it; the ask under
cover, through a stub comms: its signals, the cover's end only on lists read whole, the
holds counted by the launch value alone, and the cap counted from the press; and the hold
verbs, which keep to the launch value."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from moxi import exit as codes
from moxi import modules
from test_keepwarm import (NEW, OTHER, SETTINGS, SID, FakeCache, FakeSession, FakeSlots,
                           FakeTurns, agent, ask, conn, facts, launched, nudged, verb)
from test_page_waits import CAPTAIN, Comms, Turns, directed, live

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


# -- the ask under cover -----------------------------------------------------------------------

WORKER = "aaaaaaaa-0000-4000-8000-000000000001"
CODER = "coder@proj-a"


def world(*, turn_open=False, agents=0, offline=False, main=None, owner=CAPTAIN):
    """Session `SID`, owned by `captain`, which directs `coder` in session `WORKER`."""
    main = main or FakeSession()
    worker = live(WORKER, turn_open=turn_open, agents=agents)
    bound, _ = directed(CODER, WORKER)
    comms = Comms({SID: owner}, [bound], {CODER: WORKER}, offline=offline)
    return SimpleNamespace(main=main, worker=worker, comms=comms,
                           turns=Turns({SID: main, WORKER: worker}))


def ask_in(conn, w, **kw):
    kw.setdefault("cache", FakeCache())
    verdict = keepwarm.ask(SID, conn=conn, turns=w.turns, slots_api=FakeSlots(),
                           comms=w.comms, settings=kw.pop("settings", SETTINGS), **kw)
    return SimpleNamespace(verdict=verdict, session=w.main,
                           runs=store.runs(conn, request_id="r1"))


def offs(conn):
    return [e.detail for e in cover_events(conn) if e.detail["state"] == "off"]


def lines(got):
    [(text, _)] = got.session.delivered
    return text.splitlines()


def nudge_event(conn, got):
    return next(e.detail for e in store.run_events(conn, got.runs[0].run_id)
                if e.kind == "nudge")


def test_under_cover_a_live_subagent_is_nudged_with_its_text_and_comms_is_not_asked(conn):
    off(conn)
    covered(conn)
    w = world(turn_open=True, main=FakeSession(agents=[agent("a1", description="tests")]))
    got = ask_in(conn, w)
    assert (got.verdict.outcome, got.verdict.word) == ("nudged", "subagents")
    assert lines(got) == ["Keep-warm from launcher", "check", "- tests (a1)"]
    assert w.comms.calls == [] and offs(conn) == []


def test_under_cover_a_directed_agent_with_an_open_turn_gets_the_directing_nudge(conn):
    off(conn)
    covered(conn)
    w = world(turn_open=True)
    got = ask_in(conn, w)
    assert (got.verdict.outcome, got.verdict.word) == ("nudged", "director")
    assert lines(got) == ["Keep-warm from launcher", "directing", f"- {CODER}"]
    assert not any(line.startswith("Open holds") for line in lines(got))
    event = nudge_event(conn, got)
    assert event["directed"] == [CODER] and event["holds"] == [] and event["agents"] == []
    assert [name for name, _ in w.comms.calls] == ["owner", "roster", "session_of"]
    assert all(timeout == 2.0 for _, timeout in w.comms.calls)
    assert offs(conn) == [] and keepwarm.cover(conn, row(conn), SID) is not None


def test_under_cover_a_directed_agent_waiting_on_its_own_subagents_is_at_work(conn):
    off(conn)
    covered(conn)
    got = ask_in(conn, world(agents=2))
    assert (got.verdict.outcome, got.verdict.word) == ("nudged", "director")


def test_a_directed_agent_whose_subagents_turns_cannot_list_keeps_the_cover(conn):
    off(conn)
    covered(conn)
    got = ask_in(conn, world(agents=None))
    assert (got.verdict.outcome, got.verdict.word) == (None, "unreadable")
    assert "could not list" in got.verdict.detail
    assert offs(conn) == [] and got.runs == [] and got.session.delivered == []
    assert keepwarm.cover(conn, row(conn), SID) is not None


def test_nobody_at_work_on_lists_read_whole_ends_the_cover_and_the_next_ask_is_off(conn):
    off(conn)
    covered(conn)
    w = world()
    got = ask_in(conn, w)
    assert (got.verdict.outcome, got.verdict.word) == (None, "idle")
    assert offs(conn) == [{"state": "off", "why": "idle", "session_id": SID}]
    assert got.runs == [] and got.session.delivered == []
    w.comms.calls.clear()
    again = ask_in(conn, w)
    assert (again.verdict.outcome, again.verdict.word) == (None, "off")
    assert w.comms.calls == [] and len(offs(conn)) == 1


def test_a_dry_run_that_would_end_the_cover_writes_nothing(conn):
    off(conn)
    covered(conn)
    got = ask_in(conn, world(), dry_run=True)
    assert (got.verdict.outcome, got.verdict.word) == (None, "idle")
    assert offs(conn) == [] and keepwarm.cover(conn, row(conn), SID) is not None


def test_comms_down_keeps_the_cover(conn):
    off(conn)
    covered(conn)
    got = ask_in(conn, world(offline=True))
    assert (got.verdict.outcome, got.verdict.word) == (None, "unreadable")
    assert offs(conn) == [] and keepwarm.cover(conn, row(conn), SID) is not None


def test_a_join_past_its_deadline_keeps_the_cover(conn, monkeypatch):
    off(conn)
    covered(conn)
    monkeypatch.setattr(keepwarm, "ASK_JOIN_BUDGET_S", 0.0)
    w = world(turn_open=True)
    got = ask_in(conn, w)
    assert (got.verdict.outcome, got.verdict.word) == (None, "unreadable")
    assert w.comms.calls == [] and offs(conn) == []


def test_the_session_s_own_agents_unread_keeps_the_cover_and_without_cover_is_idle(conn):
    off(conn)
    covered(conn)
    main = FakeSession()
    main.entries = RuntimeError("no list")
    got = ask_in(conn, world(main=main))
    assert (got.verdict.outcome, got.verdict.word) == (None, "unreadable")
    assert "could not be read" in got.verdict.detail and offs(conn) == []
    launched(conn, request_id="r2", session_id=NEW)
    conn.execute("UPDATE launches SET endpoint = NULL WHERE request_id = 'r1'")
    plain = FakeSession(session_id=NEW, facts_seq=[facts(session_id=NEW)])
    plain.entries = RuntimeError("no list")
    unchanged = ask(conn, session=plain, which=NEW, request_id="r2")
    assert (unchanged.verdict.outcome, unchanged.verdict.word) == (None, "idle")


def test_the_session_s_own_list_incomplete_keeps_the_cover(conn):
    off(conn)
    covered(conn)
    got = ask_in(conn, world(main=FakeSession(agents=[agent("a1", state="done")],
                                              complete=False)))
    assert (got.verdict.outcome, got.verdict.word) == (None, "unreadable")
    assert "incomplete" in got.verdict.detail and offs(conn) == []


def test_an_open_turn_under_cover_is_busy_and_keeps_the_cover(conn):
    off(conn)
    covered(conn)
    w = world(main=FakeSession(facts_seq=[facts(turn_open=True)]))
    got = ask_in(conn, w)
    assert (got.verdict.outcome, got.verdict.word) == (None, "busy")
    assert w.comms.calls == [] and offs(conn) == []


def test_the_ask_loads_comms_itself_only_under_cover(conn, monkeypatch):
    w = world(turn_open=True)
    loads = []
    monkeypatch.setattr(keepwarm, "_comms", lambda: loads.append(1) or w.comms)
    keepwarm.ask(SID, conn=conn, turns=w.turns, slots_api=FakeSlots(), cache=FakeCache(),
                 settings=SETTINGS)
    assert loads == []
    off(conn)
    covered(conn)
    verdict = keepwarm.ask(SID, conn=conn, turns=w.turns, slots_api=FakeSlots(),
                           cache=FakeCache(), settings=SETTINGS)
    assert loads == [1] and (verdict.outcome, verdict.word) == ("nudged", "director")


def test_without_comms_installed_nobody_is_directed_and_the_cover_ends(conn, monkeypatch):
    off(conn)
    covered(conn)
    monkeypatch.setattr(modules, "available", lambda name: name != "comms")
    w = world()
    got = keepwarm.ask(SID, conn=conn, turns=w.turns, slots_api=FakeSlots(),
                       cache=FakeCache(), settings=SETTINGS)
    assert (got.outcome, got.word) == (None, "idle") and len(offs(conn)) == 1


def test_an_installed_comms_that_does_not_load_keeps_the_cover_and_is_unreadable(
        conn, monkeypatch):
    off(conn)
    covered(conn)
    monkeypatch.setattr(modules, "available", lambda name: True)
    real_load = modules.load
    monkeypatch.setattr(modules, "load", lambda name, **kw: (
        SimpleNamespace(ok=False, api=None) if name == "comms" else real_load(name, **kw)))
    w = world()
    got = keepwarm.ask(SID, conn=conn, turns=w.turns, slots_api=FakeSlots(),
                       cache=FakeCache(), settings=SETTINGS)
    assert (got.outcome, got.word) == (None, "unreadable") and offs(conn) == []
    assert "comms or turns could not be read" in got.detail and w.comms.calls == []


# -- holds under cover ------------------------------------------------------------------------


def test_with_a_hold_and_nobody_at_work_the_cover_ends_and_the_hold_nudges_as_today(conn):
    store.open_hold(conn, "r1", "port-cache")
    nudged(conn, number=3)
    covered(conn)
    w = world(main=FakeSession())
    got = ask_in(conn, w)
    assert [name for name, _ in w.comms.calls] == ["owner", "roster", "session_of"]
    assert offs(conn) == [{"state": "off", "why": "idle", "session_id": SID}]
    assert (got.verdict.outcome, got.verdict.word) == ("nudged", "director")
    assert lines(got) == ["Keep-warm from launcher", "recheck holds",
                          "Open holds: port-cache"]


def test_with_a_hold_and_a_directed_agent_at_work_the_hold_nudges_and_the_cover_stays(conn):
    store.open_hold(conn, "r1", "port-cache")
    covered(conn)
    got = ask_in(conn, world(turn_open=True))
    assert (got.verdict.outcome, got.verdict.word) == ("nudged", "director")
    assert lines(got) == ["Keep-warm from launcher", "reply warm"]
    assert nudge_event(conn, got)["holds"] == ["port-cache"] and offs(conn) == []


def test_with_a_hold_and_comms_down_the_hold_nudges_and_the_cover_stays(conn):
    store.open_hold(conn, "r1", "port-cache")
    covered(conn)
    got = ask_in(conn, world(offline=True))
    assert (got.verdict.outcome, got.verdict.word) == ("nudged", "director")
    assert lines(got) == ["Keep-warm from launcher", "reply warm"]
    assert offs(conn) == [] and keepwarm.cover(conn, row(conn), SID) is not None


def test_a_hold_in_the_chain_is_no_signal_for_a_launch_under_cover_alone(conn):
    store.open_hold(conn, "r1", "port-cache")
    conn.execute("UPDATE launches SET endpoint = NULL WHERE request_id = 'r1'")
    launched(conn, request_id="r2", session_id=NEW, relaunch_of="r1",
             values={"keep_warm": "off"})
    keepwarm.cover_on(conn, row(conn, "r2"), NEW, turn_number=4, waits=WAITS)
    main = FakeSession(session_id=NEW, facts_seq=[facts(session_id=NEW)])
    w = SimpleNamespace(main=main, comms=Comms(), turns=Turns({NEW: main}))
    verdict = keepwarm.ask(NEW, conn=conn, turns=w.turns, slots_api=FakeSlots(),
                           cache=FakeCache(), comms=w.comms, settings=SETTINGS)
    assert (verdict.outcome, verdict.word) == (None, "idle")
    assert main.delivered == [] and store.open_holds(conn, "r2") == ["port-cache"]


def test_a_launch_value_on_without_cover_reads_no_directed_agent_and_asks_no_comms(conn):
    w = world(turn_open=True)
    got = ask_in(conn, w)
    assert (got.verdict.outcome, got.verdict.word) == (None, "idle")
    assert w.comms.calls == [] and cover_events(conn) == []


def test_a_launch_value_on_with_a_hold_and_no_cover_nudges_as_today(conn):
    store.open_hold(conn, "r1", "port-cache")
    w = world(turn_open=True)
    got = ask_in(conn, w)
    assert lines(got) == ["Keep-warm from launcher", "reply warm"]
    assert w.comms.calls == [] and nudge_event(conn, got)["directed"] == []


# -- the cap under cover ----------------------------------------------------------------------


def test_under_cover_the_director_cap_counts_from_the_press(conn):
    off(conn)
    nudged(conn, number=1)
    nudged(conn, number=2)
    covered(conn)
    for number in range(3, 13):
        got = ask_in(conn, world(turn_open=True, main=FakeSession(
            facts_seq=[facts(number=number)])))
        assert got.verdict.outcome == "nudged", number
    capped = ask_in(conn, world(turn_open=True, main=FakeSession(
        facts_seq=[facts(number=13)])))
    assert (capped.verdict.outcome, capped.verdict.word) == ("blocked", "capped")
    assert "10 nudges in a row since the press" in capped.verdict.detail
    assert keepwarm.cover(conn, row(conn), SID) is not None and offs(conn) == []
    covered(conn, turn_number=13)
    again = ask_in(conn, world(turn_open=True, main=FakeSession(
        facts_seq=[facts(number=13)])))
    assert (again.verdict.outcome, again.verdict.word) == ("nudged", "director")
    assert lines(again)[1] == "directing"


def test_under_cover_a_session_at_its_subagent_cap_pressed_again_is_nudged_anew(conn):
    for number in (4, 5, 6):
        nudged(conn, number=number, agents=["a1"])
    capped = ask(conn, session=FakeSession(facts_seq=[facts(number=7)], agents=[agent("a1")]))
    assert capped.verdict.word == "capped"
    covered(conn, turn_number=7)
    got = ask(conn, session=FakeSession(facts_seq=[facts(number=7)],
                                        agents=[agent("a1", description="tests")]))
    assert got.verdict.outcome == "nudged"
    assert got.session.delivered[0][0].splitlines()[1:] == ["check", "- tests (a1)"]
