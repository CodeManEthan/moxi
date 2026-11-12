"""What a running launch waits on, and whether keep-warm covers it, as the joiner reads
them (`joiner.read_waits`): its live subagents, else the agents it directs that work,
through a stub comms and fake turns, only while its own turn is closed; comms down
stopping the pass's comms reads; a slot launch read with slots never asked; and
`covered` from `keepwarm.would_nudge`."""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

import launcher_page_kit as kit
import pytest
from test_keepwarm import FakeSession, agent
from test_page_joiner import HERE, fresh, one_pass

from moxi_launcher import catalog, first, joiner, slots, store

__all__ = ["fresh"]

CAPTAIN = "captain@proj-a"


def facts(*, turn_open=False, number=4, agents=0, draft=False, session_id=None):
    return SimpleNamespace(turn_open=turn_open, turn=None, turn_number=number, draft=draft,
                           agents=agents, stale=False, session_id=session_id)


def live(sid, *, turn_open=False, entries=(), agents=0, draft=False, harness="claude"):
    """A live session turns serves, its endpoint this test's own process."""
    session = FakeSession(facts_seq=[facts(turn_open=turn_open, agents=agents, draft=draft,
                                           session_id=sid)],
                          agents=list(entries), session_id=sid)
    session.harness = harness
    return session


class Turns:
    """turns, finding each session by its id; every find recorded."""

    def __init__(self, sessions):
        self.sessions, self.asked = dict(sessions), []

    def find(self, *, session_id=None, pid=None):
        self.asked.append(session_id)
        found = self.sessions.get(session_id)
        if isinstance(found, Exception):
            raise found
        return found


class Comms:
    """comms' reads, as a launch's join makes them: who owns a session, who is bound and
    whom each says directs it, and the session behind a bound name."""

    class DaemonOffline(Exception):
        pass

    class DaemonError(Exception):
        pass

    def __init__(self, owners=None, bound=(), sessions=None, *, offline=False):
        self.owners, self.bound = dict(owners or {}), tuple(bound)
        self.sessions, self.offline = dict(sessions or {}), offline
        self.calls: list[tuple[str, float]] = []

    def _call(self, name, timeout_s):
        self.calls.append((name, timeout_s))
        if self.offline:
            raise self.DaemonOffline("the daemon is not running")

    def owner(self, session_id, *, timeout_s):
        self._call("owner", timeout_s)
        return SimpleNamespace(session_id=session_id, identity=self.owners.get(session_id))

    def roster(self, *, timeout_s):
        self._call("roster", timeout_s)
        return SimpleNamespace(bound=self.bound, standing=())

    def session_of(self, identity, *, timeout_s):
        self._call("session_of", timeout_s)
        return self.sessions.get(identity)


def directed(name, sid, *, by=CAPTAIN):
    return SimpleNamespace(qualified=name, name=name.split("@")[0], directors=(by,)), sid


@pytest.fixture
def wired(monkeypatch):
    """The pass reads only the stubs a test gives: turns, comms, a cache whose clock and
    records it states, and no sessions."""
    state = SimpleNamespace(turns=Turns({}), comms=None, cache=None)
    monkeypatch.setattr(first, "turns_api", lambda quiet=False: state.turns)
    monkeypatch.setattr(joiner, "comms_api", lambda: state.comms)
    monkeypatch.setattr(joiner, "cache_api", lambda: state.cache)
    monkeypatch.setattr(joiner, "sessions_api", lambda: None)
    monkeypatch.setattr(joiner, "cache_tick_s", lambda: 10.0)
    monkeypatch.setattr(catalog, "session_env_readable", lambda: True)
    return state


def seeded(rid="r1", *, claimed: bool = True, **options) -> str:
    return kit.seed(HERE, rid, live=True, claimed=claimed, **options)


def joined(rid="r1") -> dict:
    return one_pass().joined().get(rid, {})


# -- subagents -------------------------------------------------------------------------------


def test_a_launch_with_live_subagents_waits_on_them(wired):
    sid = seeded()
    wired.turns = Turns({sid: live(sid, entries=[agent("a1"), agent("t1", kind="teammate"),
                                                 agent("x", state="done"),
                                                 agent("s1", kind="shell")])})
    assert joined()["waits"] == {"kind": "subagents", "count": 2}


def test_an_open_own_turn_gives_no_waits_of_either_kind(wired):
    sid = seeded()
    worker = "e" * 8 + "-0000-4000-8000-" + "0" * 12
    wired.turns = Turns({sid: live(sid, turn_open=True, entries=[agent("a1")]),
                         worker: live(worker, turn_open=True)})
    found, _ = directed("coder@proj-a", worker)
    wired.comms = Comms({sid: CAPTAIN}, [found], {"coder@proj-a": worker})
    facts_ = joined()
    assert facts_["waits"] is None and facts_["covered"] is False
    assert wired.comms.calls == []


def test_without_turns_there_is_no_waits(wired):
    seeded()
    wired.turns = None
    facts_ = joined()
    assert "waits" not in facts_ and "covered" not in facts_


def test_a_launch_not_live_or_not_claimed_has_no_waits(wired):
    sid = seeded()
    other = seeded("r2", claimed=False)
    wired.turns = Turns({other: live(other, entries=[agent("a1")])})
    got = one_pass().joined()
    assert got["r1"]["waits"] is None and got["r2"]["waits"] is None
    assert got["r1"]["covered"] is False and got["r2"]["covered"] is False
    # The unclaimed launch is refused before turns is asked for it.
    assert wired.turns.asked == [sid]


# -- agents ----------------------------------------------------------------------------------


def agents_case(wired, *workers):
    """A director with no subagent, and the agents it directs, each `(name, session)`.
    Returns the director's session id and each agent's, by name."""
    sid = seeded()
    sessions = {sid: live(sid)}
    bound, ids = [], {}
    for index, (name, session) in enumerate(workers):
        wsid = f"{index:08x}-0000-4000-8000-{index:012x}"
        session.session_id = wsid
        sessions[wsid] = session
        found, _ = directed(name, wsid)
        bound.append(found)
        ids[name] = wsid
    bound.append(SimpleNamespace(qualified="scout@proj-b", name="scout",
                                 directors=("someone@proj-b",)))
    wired.turns = Turns(sessions)
    wired.comms = Comms({sid: CAPTAIN}, bound, ids)
    return sid, ids


def test_a_director_waits_on_the_agents_it_directs_that_work(wired):
    agents_case(wired,
                ("coder@proj-a", live("x", turn_open=True)),
                ("critic@proj-a", live("x", agents=2)),
                ("pilot@proj-a", live("x", agents=0)),
                ("helper@proj-a", live("x", agents=None)))
    assert joined()["waits"] == {"kind": "agents", "count": 2}


def test_the_agents_reads_are_comms_owner_one_roster_and_session_of_each_directed(wired):
    agents_case(wired, ("coder@proj-a", live("x", turn_open=True)),
                ("critic@proj-a", live("x")))
    joined()
    assert [name for name, _ in wired.comms.calls] == ["owner", "roster", "session_of",
                                                       "session_of"]
    assert {timeout for _, timeout in wired.comms.calls} == {joiner.COMMS_TIMEOUT_S}
    assert joiner.COMMS_TIMEOUT_S == 2.0


def test_a_directed_codex_session_waiting_on_its_terminals_does_not_work(wired):
    agents_case(wired, ("coder@proj-a", live("x", agents=1, harness="codex")))
    assert joined()["waits"] is None


def test_an_agent_comms_names_no_session_for_does_not_count(wired):
    agents_case(wired, ("coder@proj-a", live("x", turn_open=True)))
    wired.comms.sessions = {}
    assert joined()["waits"] is None


def test_an_agent_whose_session_turns_does_not_find_does_not_count(wired):
    _, ids = agents_case(wired, ("coder@proj-a", live("x", turn_open=True)))
    wired.turns.sessions[ids["coder@proj-a"]] = None
    assert joined()["waits"] is None


def test_a_session_comms_does_not_know_waits_on_no_agents(wired):
    agents_case(wired, ("coder@proj-a", live("x", turn_open=True)))
    wired.comms.owners = {}
    assert joined()["waits"] is None
    assert [name for name, _ in wired.comms.calls] == ["owner"]


def test_a_turns_read_that_fails_gives_no_waits(wired):
    _, ids = agents_case(wired, ("coder@proj-a", live("x", turn_open=True)),
                         ("critic@proj-a", live("x", turn_open=True)))
    wired.turns.sessions[ids["critic@proj-a"]] = RuntimeError("the endpoint went away")
    assert joined()["waits"] is None


def test_comms_down_gives_no_waits_and_the_pass_asks_comms_nothing_more(wired,
                                                                        monkeypatch):
    monkeypatch.setattr(joiner, "JOIN_THREADS", 1)
    first_sid = seeded("r1")
    second_sid = seeded("r2")
    wired.turns = Turns({first_sid: live(first_sid), second_sid: live(second_sid)})
    wired.comms = Comms(offline=True)
    got = one_pass().joined()
    assert got["r1"]["waits"] is None and got["r2"]["waits"] is None
    assert [name for name, _ in wired.comms.calls] == ["owner"]


def test_without_comms_a_director_is_an_ordinary_session(wired):
    agents_case(wired, ("coder@proj-a", live("x", turn_open=True)))
    wired.comms = None
    assert joined()["waits"] is None


# -- a slot launch ---------------------------------------------------------------------------


@pytest.mark.parametrize("slots_here", ["none", "raising"])
def test_a_slot_launch_has_its_waits_and_slots_is_never_asked(wired, monkeypatch,
                                                              slots_here):
    asked = []

    class Raising:
        class SlotsError(Exception):
            pass

        class TryAgain(SlotsError):
            pass

        def show(self, slot):
            asked.append(slot)
            raise self.TryAgain("the lock is held")

        def job(self, name):
            asked.append(name)
            raise self.TryAgain("the lock is held")

    monkeypatch.setattr(slots, "api", (lambda: None) if slots_here == "none"
                        else (lambda: Raising()))
    sid = seeded()
    assert store.resolve(store.connect(), sid).target == "slot"
    wired.turns = Turns({sid: live(sid, entries=[agent("a1")])})
    assert joined()["waits"] == {"kind": "subagents", "count": 1}
    assert asked == []


# -- covered ---------------------------------------------------------------------------------


class Cache:
    """cache's read of every session: warm, the age and lifetime given; and the watcher
    timing the sessions given."""

    def __init__(self, timing, *, age_s=3000.0, ttl_s=3600.0):
        self.timing, self.age_s, self.ttl_s = timing, age_s, ttl_s

    def read(self, harness, session_id, **options):
        return SimpleNamespace(kind="warm", age_s=self.age_s, ttl_s=self.ttl_s,
                               ttl_source="session", basis=None)

    def clock_records(self):
        return [{"session": sid, "harness": "claude", "seen_at": time.time(),
                 "stale_after": 30} for sid in self.timing]


def test_a_waiting_session_keep_warm_will_nudge_is_covered(wired):
    sid = seeded(values={"keep_warm": "on"})
    wired.turns = Turns({sid: live(sid, entries=[agent("a1")])})
    wired.cache = Cache([sid])
    got = joined()
    assert got["waits"] == {"kind": "subagents", "count": 1} and got["covered"] is True
    assert got["cache"]["state"] == "warm"


@pytest.mark.parametrize("change", ["draft", "not timed", "keep warm off", "no cache"])
def test_a_waiting_session_keep_warm_will_not_nudge_is_not_covered(wired, change):
    sid = seeded(values={"keep_warm": "off" if change == "keep warm off" else "on"})
    wired.turns = Turns({sid: live(sid, entries=[agent("a1")], draft=change == "draft")})
    wired.cache = None if change == "no cache" else Cache([] if change == "not timed"
                                                         else [sid])
    got = joined()
    assert got["waits"] == {"kind": "subagents", "count": 1} and got["covered"] is False


def test_the_page_s_reads_write_nothing_to_the_launch_log(wired):
    sid = seeded(values={"keep_warm": "on"})
    wired.turns = Turns({sid: live(sid, entries=[agent("a1")])})
    wired.cache = Cache([sid])
    conn = store.connect()
    before = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], conn.execute(
        "SELECT COUNT(*) FROM run_events").fetchone()[0]
    joined()
    after = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], conn.execute(
        "SELECT COUNT(*) FROM run_events").fetchone()[0]
    conn.close()
    assert before == after


def test_every_session_the_read_opens_is_closed(wired):
    agents_case(wired, ("coder@proj-a", live("x", turn_open=True)),
                ("critic@proj-a", live("x")))
    joined()
    assert [s.closed for s in wired.turns.sessions.values()] == [1, 1, 1]


# -- the fake turns' script ------------------------------------------------------------------


def test_the_fake_turns_script_drives_both_kinds(wired, monkeypatch, tmp_path):
    import json

    import launcher_fake_turns as fake

    monkeypatch.setenv(fake.DIR, str(tmp_path))
    monkeypatch.setattr(first, "turns_api", lambda quiet=False: fake)
    waiting, director = seeded("r1"), seeded("r2")
    coder, critic = (f"{n:08x}-0000-4000-8000-{n:012x}" for n in (1, 2))
    me = os.getpid()
    busy = {"id": "a1", "kind": "subagent", "state": "running", "final": False}
    terminal = {"id": "t1", "kind": "shell", "state": "running", "final": False}
    script = {"sessions": {
        waiting: {"endpoint_pid": me, "turn_number": 3, "agents": [busy, terminal]},
        director: {"endpoint_pid": me, "turn_number": 7},
        coder: {"turn_open": False, "agents": [busy]},
        critic: {"harness": "codex", "turn_open": False, "agents": [terminal]},
    }}
    (tmp_path / "script.json").write_text(json.dumps(script))
    wired.comms = Comms({director: CAPTAIN},
                        [directed("coder@proj-a", coder)[0],
                         directed("critic@proj-a", critic)[0]],
                        {"coder@proj-a": coder, "critic@proj-a": critic})
    got = one_pass().joined()
    assert got["r1"]["waits"] == {"kind": "subagents", "count": 1}
    # The Codex agent's terminal is in its facts' count, and is not read as work.
    assert got["r2"]["waits"] == {"kind": "agents", "count": 1}
    assert fake.find(session_id=critic).facts().agents == 1
    assert {c["session_id"] for c in fake.calls(tmp_path) if c["call"] == "find"} >= {
        waiting, director, coder, critic}
