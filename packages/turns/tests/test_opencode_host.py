"""The OpenCode host (experimental) on a fake server: options laid on unread, posting
only into an idle session, the tie by the reply's parent, outcomes, and supervision."""

from __future__ import annotations

import itertools
import json
import os
import stat
import time

import pytest

import fake_opencode
from fake_opencode import DROP_ADMITTED, DROP_UNADMITTED, FakeOpenCode
from moxi_turns import opencode
from moxi_turns.client import TurnsError
from moxi_turns.results import (Agent, Ended, Failed, Lost, Ran, Refused, Started,
                                TurnEnded)
from conftest import bare, opener


def until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def watching(session):
    """The session's events, collected on a thread into a queue; subscribed on return."""
    import queue
    import threading

    q = queue.Queue()

    def run():
        for ev in session.watch():
            q.put(ev)

    threading.Thread(target=run, daemon=True).start()
    time.sleep(0.3)
    return q


class InProc:
    """A `Server` stand-in: one in-process fake per start, sharing the world's store."""

    def __init__(self, world, env, cwd):
        self.world, self.env, self.cwd = world, env, cwd
        self.fake: FakeOpenCode | None = None
        self.dead = False

    def start(self):
        self.fake = FakeOpenCode(password=self.env["OPENCODE_SERVER_PASSWORD"],
                                 store=self.world.store, auto=self.world.auto).start()
        self.world.servers.append(self)
        self.world.configs.append(json.loads(self.env["OPENCODE_CONFIG_CONTENT"]))
        return self

    def wait_listening(self, timeout=60.0):
        return self.fake.url

    @property
    def alive(self):
        return not self.dead

    @property
    def returncode(self):
        return None if not self.dead else -9

    def kill(self):
        self.dead = True
        self.fake.stop()

    def stop(self, grace=0):
        if self.fake is not None and not self.dead:
            self.fake.stop()
        self.dead = True


class World:
    def __init__(self, tmp):
        self.store = tmp / "sessions.json"
        self.servers: list[InProc] = []
        self.configs: list[dict] = []
        self.auto = True
        self.made = 0

    def factory(self, binary, *, env, cwd, on_line=None):
        self.made += 1
        return InProc(self, env, cwd)

    @property
    def fake(self) -> FakeOpenCode:
        return self.servers[-1].fake

    def posts(self):
        return [b for m, p, b in self.fake.requests if p.endswith("/prompt_async")]


@pytest.fixture
def host(turns_env, tmp_path):
    made = []
    world = World(tmp_path)

    def make(auto=True, **kw):
        world.auto = auto
        h = opencode.OpenCodeHarness(tmp_path, binary="/x/opencode",
                                     server_factory=world.factory,
                                     backoff_factory=lambda: itertools.repeat(0.05), **kw)
        s = opencode._run(h)
        made.append(h)
        return s, h, world

    yield make
    for h in made:
        h.endpoint.stop("the test ended")


# -- options: laid on unread, a key turns owns refused -------------------------------------

@pytest.mark.parametrize("options,error", [
    ({"instructions": ["x.md"]}, ValueError),
    ({"model": object()}, ValueError),
    (["model"], TypeError),
])
def test_bad_options_are_refused_before_anything_starts(tmp_path, options, error):
    world = World(tmp_path)
    with pytest.raises(error):
        opencode.OpenCodeHarness(tmp_path, options=options, server_factory=world.factory)
    assert world.made == 0


@pytest.mark.parametrize("name", sorted(opencode.OWNED_ENV))
def test_env_may_not_name_the_variables_the_host_sets(tmp_path, name):
    with pytest.raises(ValueError, match=name):
        opencode.OpenCodeHarness(tmp_path, env={name: "x"})


def test_inherited_copies_of_the_hosts_variables_are_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", '{"model": "stale/one"}')
    h = opencode.OpenCodeHarness(tmp_path, env={"EXTRA": "1"})
    assert "OPENCODE_CONFIG_CONTENT" not in h.env and h.env["EXTRA"] == "1"


def test_options_are_laid_unread_with_turns_instructions_at_every_server_start(host):
    options = {"model": "prov/free-one", "small_model": "prov/free-one",
               "permission": {"external_directory": "allow", "doom_loop": "deny"},
               "future_key": {"nested": [1, 2]}}
    s, h, world = host(instructions="Layer one text.", options=options)
    path = h.instructions_path
    assert path.parent == h.endpoint.dir
    assert path.read_text() == "Layer one text."
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert world.configs == [{**options, "instructions": [str(path)]}]
    world.servers[-1].kill()
    assert until(lambda: len(world.configs) == 2 and h._ready)
    assert world.configs[1] == world.configs[0]
    assert h.server.env["OPENCODE_SERVER_PASSWORD"] == h.password
    h.endpoint.stop("done")
    assert not path.exists() and not h.endpoint.dir.exists()


def test_no_instructions_lays_the_options_alone(host):
    s, h, world = host(options={"model": "prov/m"})
    assert world.configs == [{"model": "prov/m"}]
    assert h.instructions_path is None


# -- the session ---------------------------------------------------------------------------

def test_the_host_is_an_endpoint_that_can_start_and_compact(host):
    s, h, world = host()
    assert s.harness == "opencode"
    assert s.capabilities == frozenset({"deliver", "only-if-idle", "compact", "agents"})
    assert s.session_id == h.session_id and h.session_id.startswith("ses_")
    for call, lacking in ((lambda: s.steer("x"), "steer"),
                          (lambda: s.command("clear"), "clear"),
                          (lambda: s.command("compact", stop_shells=True), "stop-shells")):
        with pytest.raises(TurnsError) as caught:
            call()
        assert caught.value.code == 1002
        assert lacking in str(caught.value)


def test_a_start_posts_under_a_minted_id_and_the_reply_ties_it(host):
    s, h, world = host()
    d = s.deliver("hello", only_if_idle=True)
    started = d.wait_started(5)
    assert isinstance(started, Started)
    [post] = world.posts()
    assert post["parts"] == [{"type": "text", "text": "hello"}]
    assert post["messageID"].startswith("msg_")
    assert started.turn_id == post["messageID"]
    assert bare(d.wait_ended(5)) == Ended(post["messageID"], "answer")
    assert s.facts().turn_number == 1 and not s.facts().turn_open


def test_a_deliver_during_a_turn_goes_in_at_its_end_and_two_are_two_turns(host):
    s, h, world = host(auto=False)
    world.fake.type(h.session_id)
    assert until(lambda: s.facts().turn_open)
    a, b = s.deliver("one"), s.deliver("two")
    time.sleep(0.2)
    assert world.posts() == []
    world.fake.finish(h.session_id)
    assert isinstance(a.wait_started(5), Started)
    world.fake.finish(h.session_id)
    assert isinstance(b.wait_started(5), Started)
    world.fake.finish(h.session_id)
    assert b.wait_ended(5).outcome == "answer"
    assert [p["parts"][0]["text"] for p in world.posts()] == ["one", "two"]


def test_a_start_meets_a_busy_status_at_the_post_and_refuses(host):
    s, h, world = host()
    busy = (200, json.dumps({h.session_id: {"type": "busy"}}))
    world.fake.status_answers += [busy, busy]    # the start's live facts, then the post
    assert bare(s.deliver("x", only_if_idle=True).wait_started(5)) == Refused("busy-turn")
    assert world.posts() == []


def test_a_deliver_meets_a_busy_status_and_waits_for_the_idle(host):
    s, h, world = host()
    world.fake.status_answers.append((200, json.dumps({h.session_id: {"type": "busy"}})))
    d = s.deliver("x")
    assert isinstance(d.wait_started(5), Started)
    assert len(world.posts()) == 1


def test_the_persons_turn_is_reported_with_no_delivery(host):
    s, h, world = host(auto=False)
    events = watching(s)
    world.fake.type(h.session_id)
    started = events.get(timeout=5)
    assert opener(started) is None
    world.fake.finish(h.session_id)
    ended = events.get(timeout=5)
    assert (ended.turn_id, ended.outcome) == (started.turn_id, "answer")


@pytest.mark.parametrize("end,outcome", [("abort", "aborted"), ("error", "error")])
def test_esc_and_an_error_are_outcomes(host, end, outcome):
    s, h, world = host(auto=False)
    d = s.deliver("x", only_if_idle=True)
    assert isinstance(d.wait_started(5), Started)
    getattr(world.fake, end)(h.session_id)
    assert d.wait_ended(5).outcome == outcome


@pytest.mark.parametrize("finish,outcome", [
    ("content-filter", "refusal"), ("error", "error"), ("length", "answer"),
    ("other", "answer"), ("stop", "answer")])
def test_a_reply_with_no_error_reads_its_finish(host, finish, outcome):
    s, h, world = host(auto=False)
    d = s.deliver("x", only_if_idle=True)
    assert isinstance(d.wait_started(5), Started)
    world.fake.finish(h.session_id, finish=finish)
    assert d.wait_ended(5).outcome == outcome


def test_a_finished_replys_late_update_opens_no_turn(host):
    s, h, world = host()
    assert s.deliver("x", only_if_idle=True).wait_ended(5).outcome == "answer"
    msgs = world.fake.control("sessions", {})["sessions"][h.session_id]["messages"]
    reply = next(m for m in msgs if m["role"] == "assistant")
    h.on_event({"type": "message.updated",
                "properties": {"sessionID": h.session_id, "info": reply}})
    assert not s.facts().turn_open
    assert s.deliver("y", only_if_idle=True).wait_ended(5).outcome == "answer"


def test_a_user_message_sent_again_in_its_turn_gives_no_later_turn_its_id(host):
    # The live server sends a turn's user message again mid-turn (a `summary` update).
    s, h, world = host(auto=False)
    events = watching(s)
    d = s.deliver("x", only_if_idle=True)
    first = d.wait_started(5)
    assert isinstance(first, Started)
    msgs = world.fake.control("sessions", {})["sessions"][h.session_id]["messages"]
    user = next(m for m in msgs if m["role"] == "user")
    h.on_event({"type": "message.updated",
                "properties": {"sessionID": h.session_id, "info": dict(user, summary={})}})
    world.fake.finish(h.session_id)
    assert bare(d.wait_ended(5)) == Ended(first.turn_id, "answer")
    while not isinstance(events.get(timeout=5), TurnEnded):
        pass
    # A loop that opens with no new user message seen (a cut stream, say).
    h.on_event({"type": "session.status",
                "properties": {"sessionID": h.session_id, "status": {"type": "busy"}}})
    started = events.get(timeout=5)
    assert opener(started) is None and started.turn_id != first.turn_id


def test_a_user_message_the_reconcile_found_sent_again_gives_no_later_turn_its_id(host):
    # The stream is cut after the post, so the reconcile finds the post's user message in
    # the session's messages and opens the busy turn under a minted id.
    s, h, world = host(auto=False)
    events = watching(s)
    with world.fake.lock:
        world.fake.stalled = True
    d = s.deliver("x", only_if_idle=True)
    assert until(lambda: len(world.posts()) == 1)
    mid = world.posts()[0]["messageID"]
    with world.fake.lock:
        world.fake.stalled = False
    world.fake.close_streams()
    first = d.wait_started(5)
    assert isinstance(first, Started) and first.turn_id != mid
    msgs = world.fake.control("sessions", {})["sessions"][h.session_id]["messages"]
    user = next(m for m in msgs if m["role"] == "user")
    h.on_event({"type": "message.updated",
                "properties": {"sessionID": h.session_id, "info": dict(user, summary={})}})
    world.fake.finish(h.session_id)
    assert bare(d.wait_ended(5)) == Ended(first.turn_id, "answer")
    while not isinstance(events.get(timeout=5), TurnEnded):
        pass
    h.on_event({"type": "session.status",
                "properties": {"sessionID": h.session_id, "status": {"type": "busy"}}})
    started = events.get(timeout=5)
    assert opener(started) is None and started.turn_id != mid


def test_a_compaction_is_ready_compact(host):
    s, h, world = host()
    events = watching(s)
    world.fake.compacted(h.session_id)
    ev = events.get(timeout=5)
    assert (ev.source, ev.session_id) == ("compact", h.session_id)


def _compacting(host, **kw):
    s, h, world = host(env={"MOXI_TURNS_COMMANDS": "compact"}, **kw)
    return s, h, world


def summaries(world):
    return [b for m, p, b in world.fake.requests if p.endswith("/summarize")]


def test_compact_posts_summarize_with_the_last_replys_model_and_settles_at_ready(host):
    s, h, world = _compacting(host)
    d = s.deliver("hello", only_if_idle=True)
    d.wait_ended(5)
    events = watching(s)
    assert s.command("compact", timeout=10) == Ran(None)
    assert summaries(world) == [{"providerID": fake_opencode.PROVIDER,
                                 "modelID": fake_opencode.MODEL}]
    seen = []
    until(lambda: seen.extend(_drain(events)) or any(
        getattr(e, "source", None) == "compact" for e in seen))
    assert [e.source for e in seen if type(e).__name__ == "Ready"] == ["compact"]
    assert s.facts().session_id == h.session_id


def _drain(q):
    import queue

    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def test_compact_with_no_reply_yet_uses_the_launchs_model_option(host):
    s, h, world = _compacting(host, options={"model": "prov/some-model"})
    assert s.command("compact", timeout=10) == Ran(None)
    assert summaries(world) == [{"providerID": "prov", "modelID": "some-model"}]


def test_compact_with_no_model_known_fails_and_posts_nothing(host):
    s, h, world = _compacting(host)
    assert s.command("compact", timeout=10) == Failed("no model known yet")
    assert summaries(world) == []


def test_compact_on_a_server_without_the_route_refuses_unsupported(host):
    s, h, world = _compacting(host, options={"model": "prov/m"})
    world.fake.summarize_route = False         # its answer: the web page, a 200
    assert s.command("compact", timeout=10) == Refused("unsupported")
    # The slot is free again; the rate still counts the accepted command.
    assert s.command("compact", timeout=10) == Refused("rate")
    assert not h.endpoint.is_gone


def test_compact_on_an_older_server_without_the_route_refuses_unsupported(host):
    from fake_opencode import PLAIN_404

    s, h, world = _compacting(host, options={"model": "prov/m"})
    world.fake.summarize_route = False
    world.fake.missing_route_answer = PLAIN_404
    assert s.command("compact", timeout=10) == Refused("unsupported")
    assert not h.endpoint.is_gone


def test_compact_on_a_session_gone_from_the_server_fails_and_ends_the_endpoint(host):
    s, h, world = _compacting(host, options={"model": "prov/m"})
    # The session went between the checks and the post (OpenCode's own answer for it).
    world.fake.summarize_answers.append((404, {"name": "NotFoundError", "data": {
        "message": f"Session not found: {h.session_id}"}}))
    result = s.command("compact", timeout=10)
    assert isinstance(result, Failed)
    assert "404" in result.reason and "session is gone" in result.reason
    assert "NotFoundError" in result.reason
    assert until(lambda: h.endpoint.is_gone)


def test_compact_the_server_turns_down_fails_with_its_answer(host):
    s, h, world = _compacting(host, options={"model": "prov/m"})
    world.fake.summarize_answers.append((400, {"name": "BadRequest"}))
    result = s.command("compact", timeout=10)
    assert isinstance(result, Failed) and "400" in result.reason
    assert "BadRequest" in result.reason


def test_compact_meets_a_busy_status_at_the_post_and_refuses(host):
    from moxi_turns.harness import Live

    s, h, world = _compacting(host, options={"model": "prov/m"})
    h.live = lambda: Live(0, False)      # so the one status answer meets the re-check
    world.fake.status_answers.append((200, json.dumps({h.session_id: {"type": "busy"}})))
    assert s.command("compact", timeout=10) == Refused("busy-turn")
    assert summaries(world) == []


def test_compact_needs_consent_and_an_idle_session(host):
    s, h, world = host(auto=False, options={"model": "prov/m"})
    assert s.command("compact") == Refused("not-allowed")
    s, h, world = _compacting(host, auto=False, options={"model": "prov/m"})
    world.fake.type(h.session_id)
    assert until(lambda: s.facts().turn_open)
    assert s.command("compact") == Refused("busy-turn")
    assert summaries(world) == []


def test_listeners_context_the_host_cannot_carry_is_logged_as_dropped(host, monkeypatch,
                                                                     caplog):
    from moxi_turns.endpoint import Endpoint
    ready = Endpoint.ready
    monkeypatch.setattr(Endpoint, "ready", lambda self, *a: ready(self, *a) or "ctx")
    caplog.set_level("INFO", logger="moxi.turns.opencode")
    s, h, world = host()
    events = watching(s)
    world.fake.compacted(h.session_id)
    assert events.get(timeout=5).source == "compact"

    def dropped():
        return [r.getMessage() for r in caplog.records if "was dropped" in r.getMessage()]

    # The host's event thread logs the drop after `Ready` has gone out, so the line can
    # land after the event reaches the test: wait for it, then assert on what was logged.
    until(lambda: len(dropped()) >= 2)
    assert [m.split(" (")[0] for m in dropped()] == [
        "the listeners' context at startup", "the listeners' context at compact"]
    assert all("ctx" not in m for m in dropped())


def test_running_child_sessions_are_subagents(host):
    s, h, world = host()
    child = world.fake.create_session(parent=h.session_id)["id"]
    assert s.facts().agents == 0 and s.facts().draft is False
    world.fake.set_status(child, "busy")
    assert s.facts().agents == 1
    d = s.deliver("x", only_if_idle=True, refuse_agents=True)
    assert bare(d.wait_started(5)) == Refused("busy-agents")


def test_child_sessions_are_listed_as_agents_with_no_final_notice(host):
    s, h, world = host()
    first = s.agents()
    assert first.complete and first.entries == ()
    one = world.fake.create_session("look around", parent=h.session_id)["id"]
    two = world.fake.create_session("retry me", parent=h.session_id)["id"]
    world.fake.create_session("someone else's")              # not a child: not listed
    world.fake.set_status(one, "busy")
    world.fake.set_status(two, "retry")
    a = s.agents()
    assert a.complete
    assert sorted(a.entries, key=lambda e: e.description) == [
        Agent(one, "subagent", "running", None, "look around"),
        Agent(two, "subagent", "running", None, "retry me")]
    world.fake.set_status(two, "idle")
    a = s.agents()
    assert {e.id: e.state for e in a.entries} == {one: "running", two: "idle"}
    assert a.busy() == 1 and s.facts().agents == 1
    # OpenCode gives no final notice: an idle child reads idle, never done.
    world.fake.set_status(one, "idle")
    assert all(e.final is None and e.state == "idle" for e in s.agents().entries)
    assert s.facts().agents == 0


def test_a_child_list_that_cannot_be_read_is_not_complete(host):
    s, h, world = host()
    child = world.fake.create_session("c", parent=h.session_id)["id"]
    world.fake.set_status(child, "busy")
    h.http.json = lambda *a, **k: (500, {"name": "Broken"})
    a = s.agents()
    assert a.complete is False and a.busy() is None
    assert s.facts().agents is None


# -- posts that fail -----------------------------------------------------------------------

def test_a_5xx_post_is_tried_again(host, monkeypatch):
    monkeypatch.setattr(opencode, "FLUSH_RETRY", 0.05)
    s, h, world = host()
    world.fake.post_answers.append((503, {"name": "Busy"}))
    d = s.deliver("x")
    assert d.wait_ended(5).outcome == "answer"
    assert len(world.posts()) == 2


def test_a_4xx_post_refuses_and_ends_the_endpoint(host):
    s, h, world = host()
    world.fake.post_answers.append((400, {"name": "BadRequest"}))
    # The server turned the text down, so it never ran: `unsupported`, settled before the
    # endpoint ends. A wait that connects only after the end finds nothing serving.
    got = bare(s.deliver("x", only_if_idle=True).wait_started(5))
    assert got in (Refused("unsupported"), Lost("unanswered"))
    assert until(lambda: h.endpoint.is_gone)
    assert "400" in h.endpoint._gone


def test_an_unanswered_post_that_was_admitted_settles_once(host):
    s, h, world = host()
    world.fake.post_answers.append(DROP_ADMITTED)
    d = s.deliver("x", only_if_idle=True)
    assert d.wait_ended(5).outcome == "answer"
    assert len(world.posts()) == 1


def test_an_unanswered_post_that_was_not_admitted_goes_in_again(host):
    s, h, world = host()
    world.fake.post_answers.append(DROP_UNADMITTED)
    d = s.deliver("x")
    assert d.wait_ended(5).outcome == "answer"
    msgs = world.fake.control("sessions", {})["sessions"][h.session_id]["messages"]
    assert sum(1 for m in msgs if m["role"] == "user") == 1


def test_an_idle_with_no_reply_to_the_post_releases_the_turn_and_reads_lost(host):
    s, h, world = host(auto=False)
    world.fake.reply_to_prompts = False
    events = watching(s)
    d = s.deliver("x")
    assert until(lambda: s.facts().turn_open)
    world.fake.go_idle(h.session_id)
    lost = d.wait_started(5)
    assert isinstance(lost, Lost) and lost.reason.startswith("untied")
    started, ended = events.get(timeout=5), events.get(timeout=5)
    assert opener(started) is None and ended.outcome == "error"
    assert not s.facts().turn_open


# -- supervision ---------------------------------------------------------------------------

def test_a_dead_server_ends_its_turn_error_and_comes_back_on_the_same_session(host):
    s, h, world = host(auto=False)
    d = s.deliver("x", only_if_idle=True)
    assert isinstance(d.wait_started(5), Started)
    sid = h.session_id
    world.servers[-1].kill()
    assert bare(d.wait_ended(5)) == Ended(d.wait_started(1).turn_id, "error")
    assert until(lambda: len(world.servers) == 2 and h._ready)
    assert h.session_id == sid
    world.auto = True
    world.fake.auto = True
    assert s.deliver("again", only_if_idle=True).wait_ended(5).outcome == "answer"


def test_a_post_a_dead_server_took_unseen_goes_in_again_on_the_new_server(host):
    s, h, world = host()
    world.fake.post_answers.append((204, None))     # taken, its user message never written
    d = s.deliver("x")
    assert until(lambda: len(world.posts()) == 1)
    world.servers[-1].kill()
    assert until(lambda: len(world.servers) == 2)
    assert d.wait_ended(5).outcome == "answer"
    assert len(world.posts()) == 1                  # the new server's one post
    msgs = world.fake.control("sessions", {})["sessions"][h.session_id]["messages"]
    assert sum(1 for m in msgs if m["role"] == "user") == 1


def test_a_cut_stream_is_reopened_and_a_turn_that_ended_meanwhile_is_reported(host):
    s, h, world = host(auto=False)
    d = s.deliver("x", only_if_idle=True)
    assert isinstance(d.wait_started(5), Started)
    with world.fake.lock:
        world.fake.stalled = True
    world.fake.finish(h.session_id)
    with world.fake.lock:
        world.fake.stalled = False
    world.fake.close_streams()
    assert d.wait_ended(5).outcome == "answer"
    assert until(lambda: h._ready)


def test_a_session_gone_from_the_server_ends_the_endpoint(host):
    s, h, world = host()
    with world.fake.lock:
        world.fake.sessions.clear()
    world.fake.close_streams()
    assert until(lambda: h.endpoint.is_gone)


def test_a_dead_hosts_directory_is_swept_with_its_instructions(turns_env):
    import moxi_turns
    from moxi_turns import client, protocol
    root = protocol.v2_root(client.runtime_dir())
    root.mkdir(parents=True, exist_ok=True)
    dead = root / "opencode-dead"
    dead.mkdir()
    (dead / "hello.json").write_text(json.dumps({
        "address": protocol.address("opencode-dead"), "protocol": 2, "harness": "opencode",
        "pid": os.getpid(), "session_id": "s-1", "started": 9e12}))
    (dead / opencode.INSTRUCTIONS_FILE).write_text("layer one")
    assert moxi_turns.sessions() == []
    assert not dead.exists()


# -- a real child --------------------------------------------------------------------------

def test_launch_spawns_opencode_serve_from_path_and_stops_it(turns_env, tmp_path,
                                                             monkeypatch):
    bin_dir = tmp_path / "bin"
    fake_opencode.install(bin_dir)
    record = tmp_path / "rec"
    monkeypatch.setenv("FAKE_OPENCODE_DIR", str(record))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    s = opencode.launch(tmp_path, instructions="be brief", options={"model": "p/m"})
    try:
        [serve] = [e for e in fake_opencode.events(record) if e["event"] == "serve"]
        assert serve["argv"] == ["serve", "--hostname", "127.0.0.1", "--port", "0",
                                 "--print-logs", "--log-level", "INFO"]
        assert serve["password"] and serve["cwd"] == str(tmp_path.resolve())
        assert serve["config"]["model"] == "p/m"
        assert open(serve["config"]["instructions"][0]).read() == "be brief"
        assert s.deliver("hi", only_if_idle=True).wait_ended(10).outcome == "answer"
    finally:
        with opencode._HOSTS_LOCK:
            endpoint, harness = opencode._HOSTS.pop()
        endpoint.stop("the test ended")
    pid = serve["pid"]
    assert until(lambda: not os.path.exists(f"/proc/{pid}") or
                 open(f"/proc/{pid}/stat").read().split()[2] == "Z", 10)


def test_a_launch_whose_server_never_listens_fails_with_the_reason(turns_env, tmp_path,
                                                                   monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "opencode").write_text("#!/bin/sh\nexit 3\n")
    (bin_dir / "opencode").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    with pytest.raises(opencode.OpenCodeOffline, match="exit 3"):
        opencode.launch(tmp_path)
    assert not list((turns_env / "rt").rglob("hello.json"))


def test_the_doctor_names_the_opencode_binary(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(opencode, "INSTALL_DIR", tmp_path / "none")
    [row] = opencode.rows()
    assert row.state == "off" and "experimental" in row.name
