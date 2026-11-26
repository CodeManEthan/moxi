"""The joiner of the launcher's page server: the facts of each running launch read beyond
the launch log (`joiner`), its passes, their deadline and the facts they keep, and how
the feed runs them for `GET /api/running` and the stream's topic `running`."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime
from types import SimpleNamespace

import launcher_page_kit as kit
import pytest
from test_page import _send, served
from test_page_state import quick

from moxi_dev.testing import PROC_WAIT_S
from moxi_launcher import first, joiner, pagehandlers, slots

#: The launch log of this test's own environment, as the kit's seeds take a shell.
HERE = SimpleNamespace(env=None)


@pytest.fixture
def modules_read():
    """cache and sessions themselves, for the tests that read seeded transcripts through
    them."""
    pytest.importorskip("moxi_cache.api", reason="cache is not installed")
    pytest.importorskip("moxi_sessions.api", reason="sessions is not installed")


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """Each test logs each failure class anew and starts its own feed; slots and turns are
    not asked."""
    monkeypatch.setattr(pagehandlers, "_logged", set())
    monkeypatch.setattr(joiner, "_logged", set())
    monkeypatch.setattr(pagehandlers, "_current", None)
    monkeypatch.setattr(slots, "links", lambda name: False)
    monkeypatch.setattr(slots, "api", lambda: None)
    monkeypatch.setattr(first, "turns_api", lambda quiet=False: None)


async def _nothing() -> None:
    return None


def one_pass(join: joiner.Joiner | None = None, publish=None) -> joiner.Joiner:
    """One pass of `join` (a new joiner when None), run to its end on a loop of its own."""
    join = join or joiner.Joiner()
    asyncio.run(join.run(publish or _nothing))
    return join


def epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


class Skewed:
    """A monotonic clock a test can move forward."""

    def __init__(self):
        self.skew = 0.0

    def __call__(self) -> float:
        return time.monotonic() + self.skew


# --- cache --------------------------------------------------------------------------------------

def clocked(kind: str, **more) -> SimpleNamespace:
    """A value with the fields of cache's `Clock` that the joiner reads."""
    return SimpleNamespace(**{"kind": kind, "age_s": None, "ttl_s": None,
                              "ttl_source": None, "basis": None, "code": None, **more})


def warm(stamp: float, now: float, *, ttl_s: float = 300.0, **more) -> SimpleNamespace:
    """The clock cache reads at `now` for a session whose last request was at `stamp`."""
    return clocked("warm", age_s=max(0.0, now - stamp), ttl_s=ttl_s, **more)


@pytest.mark.parametrize("stamp", [1_793_354_400.0, 1_793_354_400.5, 1_793_354_400.123456])
def test_until_is_the_same_across_reads_at_many_times(stamp):
    first_now = stamp + 1.0
    fact = joiner.cache_fact("claude", warm(stamp, first_now), first_now)
    assert fact is not None and fact["state"] == "warm"
    kept = epoch(fact["until"])
    assert abs(kept - (stamp + 300.0)) <= 0.5
    for step in range(1, 400):
        now = first_now + step * 0.731
        again = joiner.cache_fact("claude", warm(stamp, now), now, kept)
        assert again["until"] == fact["until"], now


def test_until_moves_after_a_new_request():
    stamp = 1_793_354_400.0
    fact = joiner.cache_fact("claude", warm(stamp, stamp + 5), stamp + 5)
    newer = stamp + 30.0
    moved = joiner.cache_fact("claude", warm(newer, newer + 2), newer + 2,
                              epoch(fact["until"]))
    assert epoch(moved["until"]) - epoch(fact["until"]) == 30.0


def test_estimated_is_a_claude_lifetime_from_the_setting_or_a_codex_one_measured():
    now = 1_793_354_400.0
    assert joiner.cache_fact("claude", warm(now, now, ttl_source="config"), now)["estimated"]
    assert not joiner.cache_fact("claude", warm(now, now, ttl_source="tier"), now)["estimated"]
    assert joiner.cache_fact("codex", warm(now, now, basis="measured"), now)["estimated"]
    assert not joiner.cache_fact("codex", warm(now, now, basis="documented"),
                                 now)["estimated"]


def test_only_a_warm_or_cold_cache_is_a_fact_and_a_cold_one_may_have_no_time():
    now = 1_793_354_400.0
    for kind in ("fresh", "no-cache", "unknown"):
        assert joiner.cache_fact("claude", clocked(kind), now) is None
    assert joiner.cache_fact("claude", None, now) is None
    assert joiner.cache_fact("claude", clocked("cold", code="changed"), now) == {
        "state": "cold", "until": None, "estimated": False}
    cold = joiner.cache_fact("claude", clocked("cold", age_s=400.0, ttl_s=300.0), now)
    assert cold["state"] == "cold" and epoch(cold["until"]) == now - 100.0


# --- activity -------------------------------------------------------------------------------------

def said(role: str, text: str, at: float | str = 1_793_354_400.0,
         **more) -> SimpleNamespace:
    """A value with the fields of sessions' `Message` that the joiner reads; `injected`
    unknown unless given."""
    return SimpleNamespace(**{"kind": "message", "role": role, "text": text, "time": at,
                              "reasoning": False, "injected": "unknown", **more})


def test_the_activity_is_the_newest_message_said_with_who_said_it():
    records = [said("user", "Fix the form.", injected=False),
               said("assistant", "Fixed.\nIt was the label.", at=1_793_354_401.0)]
    assert joiner.activity_of(records) == {"at": "2026-10-30T10:00:01.000Z", "by": "agent",
                                           "text": "Fixed."}
    assert joiner.activity_of(records[:1])["by"] == "you"
    assert joiner.activity_of([said("user", "A note.", injected=True)])["by"] is None
    assert joiner.activity_of([said("user", "A note.")])["by"] is None


def test_the_activity_passes_over_reasoning_empty_and_other_messages():
    records = [said("assistant", "Done it."),
               said("assistant", "Thinking.", reasoning=True),
               said("assistant", "   \n "),
               said("system", "A system line."),
               said("assistant", "No time.", at="unknown")]
    assert joiner.activity_of(records)["text"] == "Done it."
    assert joiner.activity_of(records[1:]) is None
    assert joiner.activity_of([]) is None


def test_the_activity_text_is_the_first_line_cut():
    long = "x" * 300
    assert joiner.activity_of([said("assistant", f"\n  {long}\nmore")])["text"] == "x" * 200


# --- a pass over seeded launches and transcripts -----------------------------------------------

def test_a_pass_reads_the_cache_and_the_latest_message(tmp_path, modules_read):
    config = tmp_path / "claude"
    sid = kit.seed(HERE, "r1", live=True, config_dir=config)
    kit.transcript(config, sid, asked="Fix the login form.", answered="Fixed.\nDetails.")
    published = []

    async def publish():
        published.append(dict(join.joined()))

    join = joiner.Joiner()
    one_pass(join, publish)
    [facts] = published
    entry = facts["r1"]
    assert entry["session_id"] == sid
    assert entry["cache"]["state"] == "warm" and entry["cache"]["estimated"] is True
    # The reply's request was made 60 s ago, and no request named a tier: the lifetime
    # is cache's setting.
    lifetime = joiner.cache_api().read("claude", sid,
                                       projects_root=str(config / "projects")).ttl_s
    assert abs(epoch(entry["cache"]["until"]) - (time.time() - 60 + lifetime)) < 5
    assert entry["activity"]["by"] == "agent" and entry["activity"]["text"] == "Fixed."
    assert abs(epoch(entry["activity"]["at"]) - (time.time() - 60)) < 5


def test_a_cache_tier_a_request_named_is_not_estimated(tmp_path, modules_read):
    config = tmp_path / "claude"
    sid = kit.seed(HERE, "r1", live=True, config_dir=config)
    kit.transcript(config, sid, hour=True)
    cache = one_pass().joined()["r1"]["cache"]
    assert cache["state"] == "warm" and cache["estimated"] is False
    assert abs(epoch(cache["until"]) - (time.time() - 60 + 3600)) < 5


def test_the_window_is_read_again_only_when_the_transcript_changed(tmp_path, monkeypatch,
                                                                    modules_read):
    config = tmp_path / "claude"
    sid = kit.seed(HERE, "r1", live=True, config_dir=config)
    written = kit.transcript(config, sid, answered="First.")
    real = joiner.sessions_api()
    windows = []

    def window(source, **options):
        windows.append(options)
        return real.window(source, **options)

    monkeypatch.setattr(joiner, "sessions_api",
                        lambda: SimpleNamespace(find=real.find, window=window,
                                                projects_root=real.projects_root))
    join = one_pass()
    assert windows == [{"turns": 1, "limit": 256 * 1024}]
    one_pass(join)
    assert len(windows) == 1
    assert join.joined()["r1"]["activity"]["text"] == "First."
    written.user("And the signup form?", origin="human")
    written.flush()
    one_pass(join)
    assert len(windows) == 2
    activity = join.joined()["r1"]["activity"]
    assert (activity["by"], activity["text"]) == ("you", "And the signup form?")


def test_without_sessions_there_is_no_activity_and_without_cache_no_cache(tmp_path,
                                                                          monkeypatch,
                                                                          modules_read):
    config = tmp_path / "claude"
    sid = kit.seed(HERE, "r1", live=True, config_dir=config)
    kit.transcript(config, sid)
    monkeypatch.setattr(joiner, "sessions_api", lambda: None)
    facts = one_pass().joined()["r1"]
    assert facts["cache"]["state"] == "warm" and "activity" not in facts
    monkeypatch.setattr(joiner, "cache_api", lambda: None)
    assert one_pass().joined()["r1"] == {"session_id": sid, "next_keep_alive_at": None}
    [entry] = pagehandlers.gather_running(one_pass().joined())["sessions"]
    assert (entry["cache"], entry["activity"]) == (None, None)


def test_a_launch_with_no_transcript_has_no_activity_and_no_cache(tmp_path, modules_read):
    kit.seed(HERE, "r1", live=True, config_dir=tmp_path / "claude")
    facts = one_pass().joined()["r1"]
    assert facts["activity"] is None and facts["cache"] is None


# --- the deadline, the keep and the bound -----------------------------------------------------

def test_a_worker_makes_no_call_after_the_deadline(monkeypatch):
    monkeypatch.setattr(joiner, "JOIN_BUDGET_S", 0.5)
    kit.seed(HERE, "slow", live=True)
    kit.seed(HERE, "quick", live=True)
    calls: list[tuple[float, float]] = []
    ended = {}

    def directing(each, launch):
        # A launch directing many agents, each read costing one slow call.
        if launch.request_id == "quick":
            return {"covered": True}
        try:
            for _ in range(20):
                started = time.monotonic()
                each.call(time.sleep, 0.2)
                calls.append((started, each.deadline))
        finally:
            ended["at"] = time.monotonic()
        return {"covered": True}

    monkeypatch.setattr(joiner, "READS", (directing,))
    published = []
    join = joiner.Joiner()

    async def publish():
        published.append((time.monotonic(), join.joined()))

    one_pass(join, publish)
    deadline = calls[0][1]
    assert calls and all(started < deadline for started, _ in calls)
    assert len(calls) < 20
    assert ended["at"] <= deadline + 0.2 + 0.15
    # The pass published at the deadline, with the launch that finished only, and
    # returned only once the slow worker had ended.
    [(at, facts)] = published
    assert at < ended["at"] and set(facts) == {"quick"}
    assert set(join.kept) == {"quick"}


def test_a_launch_whose_read_did_not_finish_keeps_its_facts_for_45_seconds(monkeypatch):
    monkeypatch.setattr(joiner, "JOIN_BUDGET_S", 0.3)
    kit.seed(HERE, "r1", live=True)
    clock = Skewed()
    join = joiner.Joiner(clock=clock)
    slow = threading.Event()

    def read(each, launch):
        if slow.is_set():
            each.call(time.sleep, 0.4)
            each.call(time.sleep, 0.0)
        return {"waits": {"kind": "subagents", "count": 2}}

    monkeypatch.setattr(joiner, "READS", (read,))
    one_pass(join)
    read_at = join.kept["r1"].read_at
    slow.set()
    one_pass(join)
    assert join.kept["r1"].read_at == read_at
    clock.skew = read_at + 44.0 - time.monotonic()
    assert join.joined()["r1"]["waits"] == {"kind": "subagents", "count": 2}
    clock.skew = read_at + 45.5 - time.monotonic()
    assert join.joined() == {}
    one_pass(join)
    assert join.kept == {}


def test_kept_facts_are_not_laid_over_a_launch_that_changed_session(monkeypatch):
    sid = kit.seed(HERE, "r1", live=True)

    def read(each, launch):
        each.call(time.sleep, 0.0)
        return {"covered": True}

    monkeypatch.setattr(joiner, "READS", (read,))
    join = one_pass()
    assert pagehandlers.gather_running(join.joined())["sessions"][0]["covered"] is True
    new = kit.learned(HERE, "r1")
    [entry] = pagehandlers.gather_running(join.joined())["sessions"]
    assert entry["session_id"] == new != sid and entry["covered"] is False
    # A pass that does not finish the launch's read keeps nothing of the old session.
    monkeypatch.setattr(joiner, "JOIN_BUDGET_S", 0.0)
    one_pass(join)
    assert join.kept == {}


def test_a_pass_reads_the_newest_24_launches_on_4_threads(monkeypatch):
    for number in range(26):
        kit.seed(HERE, f"r{number:02d}", live=True)
    lock = threading.Lock()
    busy, most, read = [0], [0], []

    def counted(each, launch):
        with lock:
            busy[0] += 1
            most[0] = max(most[0], busy[0])
            read.append(launch.request_id)
        each.call(time.sleep, 0.02)
        with lock:
            busy[0] -= 1
        return {"covered": True}

    monkeypatch.setattr(joiner, "READS", (counted,))
    join = one_pass()
    newest = {f"r{number:02d}" for number in range(2, 26)}
    assert set(read) == newest and len(read) == 24
    assert most[0] == 4
    entries = pagehandlers.gather_running(join.joined())["sessions"]
    assert {e["request_id"] for e in entries if e["covered"]} == newest
    assert len(entries) == 26


def test_a_read_that_raises_gives_nothing_and_the_next_read_still_runs(monkeypatch):
    sid = kit.seed(HERE, "r1", live=True)

    def broken(each, launch):
        raise OSError("the transcript went away")

    monkeypatch.setattr(joiner, "READS", (broken, lambda each, launch: {"covered": True}))
    assert one_pass().joined()["r1"] == {"covered": True, "next_keep_alive_at": None,
                                         "session_id": sid}


# --- next keep-alive ----------------------------------------------------------------------------

class Clocked:
    """cache, reading every session warm with a lifetime of an hour and its last request at
    `stamp`, its age measured `jitter` seconds long."""

    def __init__(self, stamp: float):
        self.stamp, self.jitter = stamp, 0.0

    def read(self, harness, session_id, *, now):
        return warm(self.stamp, now + self.jitter, ttl_s=3600.0)


def test_next_keep_alive_holds_across_passes_and_moves_once_per_change(monkeypatch):
    kit.seed(HERE, "r1", live=True)
    # A whole second, so a read off by under a second stays within `UNTIL_SAME_S` of the
    # `until` published.
    cache = Clocked(time.time() // 1 - 600.0)
    state = SimpleNamespace(covered=True)
    monkeypatch.setattr(joiner, "cache_api", lambda: cache)
    monkeypatch.setattr(joiner, "READS",
                        (joiner.read_cache, lambda each, launch: {"covered": state.covered}))
    join = joiner.Joiner(clock=Skewed())
    topics = pagehandlers.lb.Topics("running")
    sent = []
    real = topics.publish

    def publish(name, data):
        sent.append(data["sessions"][0]["next_keep_alive_at"])
        real(name, data)

    topics.publish = publish
    snapshot = pagehandlers.lb.Snapshot(lambda: pagehandlers.gather_running(join.joined()),
                                        topics, "running")

    async def passes(count: int) -> str | None:
        for _ in range(count):
            await join.run(snapshot.on_changed)
        return join.joined()["r1"]["next_keep_alive_at"]

    async def go():
        due = await passes(1)
        assert due is not None
        assert epoch(due) == epoch(join.joined()["r1"]["cache"]["until"]) - 900.0
        # The same clock read again with its age off by under a second: the same value.
        cache.jitter = 0.6
        assert await passes(2) == due
        cache.jitter = -0.4
        assert await passes(1) == due
        # A new request moves it by as much as the request moved.
        cache.stamp += 30.0
        moved = await passes(2)
        assert epoch(moved) - epoch(due) == 30.0
        # Covered no longer: null.
        state.covered = False
        assert await passes(2) is None
        assert sent == [due, moved, None]

    asyncio.run(go())


# --- the feed: one pass at a time, the first wait, publishing ----------------------------------

async def until(check, timeout_s: float = PROC_WAIT_S) -> None:
    async with asyncio.timeout(timeout_s):
        while not check():
            await asyncio.sleep(0.02)


def stop(feed) -> None:
    feed.task.cancel()
    feed.join_task.cancel()


def test_no_pass_starts_while_a_worker_of_the_last_runs(monkeypatch):
    quick(monkeypatch)
    monkeypatch.setattr(joiner, "JOIN_BUDGET_S", 0.2)
    monkeypatch.setattr(joiner, "JOIN_EVERY", 0.05)
    kit.seed(HERE, "r1", live=True)
    release = threading.Event()
    entered = []

    def held(each, launch):
        entered.append(launch.request_id)
        each.call(release.wait, 10)
        return {"covered": True}

    monkeypatch.setattr(joiner, "READS", (held,))

    async def go():
        feed = pagehandlers._feed()
        try:
            await until(lambda: feed.joiner.passes == 1 and entered)
            await until(feed.joined_once.is_set)
            # The poller goes on gathering while the worker is held: a new launch reaches
            # the launches topic, and wakes the joiner, which starts no pass.
            kit.seed(HERE, "r2", live=True)
            await until(lambda: any(row["request_id"] == "r2" for row in
                                    feed.topics._latest(["launches"])[0][1]["launches"]))
            await asyncio.sleep(0.5)
            assert feed.joiner.passes == 1
            release.set()
            await until(lambda: feed.joiner.passes >= 2)
            await until(lambda: "r2" in feed.joiner.kept)
        finally:
            release.set()
            stop(feed)

    asyncio.run(go())


def test_the_first_call_of_the_route_carries_the_joined_facts(tmp_path, modules_read):
    config = tmp_path / "claude"
    sid = kit.seed(HERE, "r1", live=True, config_dir=config)
    kit.transcript(config, sid, answered="Fixed it.")

    async def go():
        status, raw = await _send(served(), "GET", "/api/running", b"", None)
        stop(pagehandlers._current)
        return status, raw

    status, raw = asyncio.run(go())
    [entry] = json.loads(raw)["sessions"]
    assert status == 200
    assert entry["cache"]["state"] == "warm" and entry["activity"]["text"] == "Fixed it."


def test_a_slow_first_pass_is_waited_for_once_then_base_fields_are_answered(monkeypatch):
    monkeypatch.setattr(pagehandlers, "FIRST_JOIN_WAIT_S", 0.4)
    kit.seed(HERE, "r1", live=True)
    release = threading.Event()

    def held(each, launch):
        each.call(release.wait, 10)
        return {"covered": True}

    monkeypatch.setattr(joiner, "READS", (held,))

    async def go():
        try:
            started = time.monotonic()
            status, raw = await _send(served(), "GET", "/api/running", b"", None)
            waited = time.monotonic() - started
            started = time.monotonic()
            again, _ = await _send(served(), "GET", "/api/running", b"", None)
            later = time.monotonic() - started
        finally:
            release.set()
            stop(pagehandlers._current)
        return status, raw, waited, again, later

    status, raw, waited, again, later = asyncio.run(go())
    assert status == again == 200
    assert 0.4 <= waited < 2.0 and later < 0.3
    [entry] = json.loads(raw)["sessions"]
    assert entry["covered"] is False and entry["request_id"] == "r1"


def test_an_idle_install_publishes_nothing_after_the_facts(tmp_path, monkeypatch,
                                                            modules_read):
    quick(monkeypatch, regather=0.1)
    monkeypatch.setattr(joiner, "JOIN_EVERY", 0.05)
    config = tmp_path / "claude"
    sid = kit.seed(HERE, "r1", live=True, config_dir=config)
    kit.transcript(config, sid)

    async def go():
        feed = pagehandlers._feed()
        sent = []
        real = feed.topics.publish

        def publish(name, data):
            if name == "running":
                sent.append(data)
            real(name, data)

        feed.topics.publish = publish
        try:
            await until(lambda: feed.joiner.passes >= 3)
            settled = len(sent)
            await until(lambda: feed.joiner.passes >= 8)
            assert len(sent) == settled
            [entry] = sent[-1]["sessions"]
            assert entry["cache"]["state"] == "warm" and entry["activity"] is not None
        finally:
            stop(feed)

    asyncio.run(go())
