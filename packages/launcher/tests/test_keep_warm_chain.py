"""Keep warm from the press to the cover's end, each step through the piece that does it:
the page server's `GET /api/running` and `POST /api/keep-warm`, and keep-warm's ask.

The subagent walk runs the real page server's routes and joiner over a seeded launch, the
fake turns on its seam, and the real cache over a seeded transcript and a written clock
record. The directed walk runs the same steps with the stub turns, comms and cache of the
joiner's unit tests, since the page server has no seam for comms.
"""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import launcher_fake_turns
import launcher_page_kit as kit
import pytest
from test_page import _send, post, served
from test_page_joiner import HERE, one_pass, stop, until
from test_page_keep_warm import Clock
from test_page_waits import ASKS, agents_case, facts, live, wired

from moxi_launcher import cachetimers, joiner, keepwarm, pagehandlers, slots, store

__all__ = ["wired"]

ROUTE = "/api/keep-warm"
#: The age of the session's last request at the press, in seconds: before keep-warm's
#: window opens on an hour lifetime, so the press is a promise the first ask keeps.
PRESSED_AT_S = 600.0
#: The age of the session's last request at the asks, in seconds: inside the window.
ASKED_AT_S = 2800.0
#: How long a step waits for the joiner's passes, in seconds.
PASS_WAIT_S = 20.0


@pytest.fixture(autouse=True)
def alone(monkeypatch):
    """Each test logs each failure class anew and starts its own feed; slots is not asked
    and the page server finds no comms."""
    monkeypatch.setattr(pagehandlers, "_logged", set())
    monkeypatch.setattr(joiner, "_logged", set())
    monkeypatch.setattr(pagehandlers, "_current", None)
    monkeypatch.setattr(slots, "links", lambda name: False)
    monkeypatch.setattr(slots, "api", lambda: None)
    monkeypatch.setattr(joiner, "comms_api", lambda: None)


def covers(rid: str) -> list[dict]:
    """The details of the launch's cover events, oldest first."""
    conn = store.connect()
    try:
        return [e.detail for e in store.events(conn, rid) if e.kind == keepwarm.COVER]
    finally:
        conn.close()


# -- the subagent walk, through the page server ------------------------------------------------


@pytest.fixture
def chain(tmp_path, monkeypatch):
    """A launch made with `keep_warm` off whose live session waits on one subagent, its
    turn closed, through the fake turns; its transcript's last request, on an hour
    lifetime, `PRESSED_AT_S` old; and a fresh clock record whose watcher asks keep-warm
    about it."""
    pytest.importorskip("moxi_cache.api", reason="cache is not installed")
    pytest.importorskip("moxi_sessions.api", reason="sessions is not installed")
    turns = kit.FakeTurns(tmp_path / "turns")
    for name, value in turns.options()["env"].items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(joiner, "JOIN_EVERY", 0.1)
    config = tmp_path / "claude"
    sid = kit.seed(HERE, "r1", live=True, claimed=True, config_dir=config,
                   values={"keep_warm": "off"})
    kit.transcript(config, sid, hour=True, ago_s=PRESSED_AT_S)
    kit.timed(HERE, sid, asks=[cachetimers.KEEP_WARM])
    session = kit.waiting()
    turns.script(sessions={sid: session})
    return SimpleNamespace(sid=sid, config=config, turns=turns, session=session)


async def running(feed=None) -> dict:
    """The route's entry of `r1`, read after two whole passes of `feed` (the first read
    starts the feed and waits for its first pass)."""
    if feed is not None:
        passes = feed.joiner.passes
        await until(lambda: feed.joiner.passes >= passes + 2, PASS_WAIT_S)
    status, raw = await _send(served(), "GET", "/api/running", b"", None)
    assert status == 200, raw
    [entry] = [e for e in json.loads(raw)["sessions"] if e["request_id"] == "r1"]
    return entry


def deliveries(turns: kit.FakeTurns) -> list[dict]:
    return [c for c in launcher_fake_turns.calls(turns.dir) if c["call"] == "deliver"]


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows: cache can't read a session's environment there, so its "
                           "timer asks nothing and keep-warm offers no cover")
def test_a_press_covers_a_waiting_session_whose_asks_nudge_it_until_nobody_is_at_work(chain):
    async def go():
        # 1 and 2: the seeded session waits uncovered, and a press would cover it.
        entry = await running()
        feed = pagehandlers._current
        try:
            assert entry["waits"] == {"kind": "subagents", "count": 1}
            assert entry["cache"]["state"] == "warm"
            assert entry["covered"] is False and entry["coverable"] == {"cap": 3}

            # 3: the press is not refused, and it sends the session nothing.
            data = json.dumps({"request_id": "r1"}).encode()
            status, raw = await _send(served(), "POST", ROUTE, b"", data)
            assert status == 200 and json.loads(raw) == {"refused": None}
            assert deliveries(chain.turns) == []
            assert [c["state"] for c in covers("r1")] == ["on"]

            # 4: the row reads covered.
            entry = await running(feed)
            assert entry["covered"] is True and entry["coverable"] is None

            # 5: inside the window, the ask nudges, once.
            kit.transcript(chain.config, chain.sid, hour=True, ago_s=ASKED_AT_S)
            verdict = await asyncio.to_thread(keepwarm.ask, chain.sid, comms=None)
            assert verdict.outcome == "nudged", verdict
            [nudge] = deliveries(chain.turns)
            assert nudge["text"].split("\n")[0] == keepwarm.FIRST_LINE

            # 6: the subagent is done; the next ask finds nobody at work and ends the cover.
            chain.turns.script(sessions={chain.sid: {**chain.session, "agents": [
                {"id": "a1", "kind": "subagent", "state": "done", "final": True}]}})
            verdict = await asyncio.to_thread(keepwarm.ask, chain.sid, comms=None)
            assert (verdict.outcome, verdict.word) == (None, "idle"), verdict
            assert [c["state"] for c in covers("r1")] == ["on", "off"]
            assert len(deliveries(chain.turns)) == 1

            # 7: the row is an ordinary one again.
            entry = await running(feed)
            assert entry["waits"] is None
            assert entry["covered"] is False and entry["coverable"] is None
        finally:
            stop(feed)

    asyncio.run(go())


# -- the directed walk, at the unit layer --------------------------------------------------------


def test_a_press_covers_a_director_whose_asks_nudge_it_until_its_agents_stop(wired):
    # 1: a director made with `keep_warm` off, no subagent of its own, one directed agent
    # whose turn is open; its last request before the window.
    sid, ids = agents_case(wired, ("coder@proj-a", live("x", turn_open=True)))
    wired.cache = Clock([sid], asks=ASKS, age_s=PRESSED_AT_S)
    director = wired.turns.sessions[sid]

    # 2: it waits on the agent, uncovered, and a press would cover it at the director cap.
    got = one_pass().joined()["r1"]
    assert got["waits"] == {"kind": "agents", "count": 1}
    assert got["covered"] is False and got["coverable"] == {"cap": 10}

    # 3: the press is not refused, and it sends the director nothing.
    status, body = post(ROUTE, {"request_id": "r1"})
    assert status == 200 and body == {"refused": None}
    assert director.delivered == []
    assert covers("r1") == [{"state": "on", "by": "page", "session_id": sid,
                             "turn_number": 4, "waits": {"kind": "agents", "count": 1}}]

    # 4: the row reads covered.
    got = one_pass().joined()["r1"]
    assert got["covered"] is True and got["coverable"] is None

    # 5: inside the window, the ask nudges with the directing text, the agent named.
    wired.cache.age_s = ASKED_AT_S
    verdict = keepwarm.ask(sid, turns=wired.turns, comms=wired.comms, cache=wired.cache,
                           slots_api=None)
    assert (verdict.outcome, verdict.word) == ("nudged", keepwarm.DIRECTOR), verdict
    [(text, _)] = director.delivered
    assert text.split("\n")[0] == keepwarm.FIRST_LINE
    assert text.split("\n")[-1] == "- coder@proj-a"
    assert "Open holds" not in text

    # 6: the agent's turn closes with no subagent of its own; the next ask finds nobody at
    # work and ends the cover.
    wired.turns.sessions[ids["coder@proj-a"]].facts_seq = [
        facts(turn_open=False, agents=0, session_id=ids["coder@proj-a"])]
    verdict = keepwarm.ask(sid, turns=wired.turns, comms=wired.comms, cache=wired.cache,
                           slots_api=None)
    assert (verdict.outcome, verdict.word) == (None, "idle"), verdict
    assert [c["state"] for c in covers("r1")] == ["on", "off"]
    assert len(director.delivered) == 1

    # 7: the row is an ordinary one again.
    got = one_pass().joined()["r1"]
    assert got["waits"] is None
    assert got["covered"] is False and got["coverable"] is None

