"""The library's side of the wire: what a call reads when the answer never comes, the
look-up of an unanswered hand-in by its ref, the shared connections, and finding across
versions. A scripted `moxi.ipc` server stands in for an endpoint where a test needs one
to misbehave; the fake harness's endpoint serves the rest."""

from __future__ import annotations

import concurrent.futures
import contextlib
import json
import os
import sys
import threading
import time

import pytest

from moxi import ipc

import moxi_turns as turns
from conftest import bare
from moxi_turns import client, protocol, rpc
from moxi_turns.results import Lost, Refused, Started

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="moxi.ipc's named pipe server comes later")

SESSION = {"harness": "fake", "pid": os.getpid(), "endpoint_pid": os.getpid(),
           "session_id": "s-1", "earlier": [], "cwd": "/", "nonce": "n",
           "started": 0.0, "endpoint_version": "0", "mod_version": None}


class Scripted:
    """A `moxi.ipc` server speaking turns' hello. `script[method]` is called with
    `(conn, params)` and returns the answer; it may close `conn` first so the answer is
    dropped, or block to hold it. Methods it does not name answer from `answers`."""

    def __init__(self, name: str, script=None, *, refuse_session=False):
        self.address = ipc.address("turns", name)
        self.script = dict(script or {})
        self.refuse_session = refuse_session
        #: Set to an unset `threading.Event` to hold every `session` answer until it is set.
        self.session_gate: threading.Event | None = None
        self.calls: list[tuple[str, object]] = []
        self.connections = 0
        self._ready = concurrent.futures.Future()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.result(5)

    def _run(self):
        from moxi import aio

        async def main():
            import asyncio

            async def handler(conn, method, params):
                self.calls.append((method, params))
                if method == "session":
                    self.connections += 1
                    gate = self.session_gate
                    if gate is not None:
                        await asyncio.to_thread(gate.wait, 10)
                    if self.refuse_session:
                        conn.close()
                    return SESSION
                fn = self.script.get(method)
                if fn is None:
                    raise ipc.RpcError(-32601, f"no {method}")
                return await asyncio.to_thread(fn, conn, params)

            self.server = await ipc.serve(self.address, handler,
                                          hello=ipc.Hello((2, 2), ("deliver", "steer")))
            self.loop = asyncio.get_running_loop()
            self._ready.set_result(None)
            await self.server.serve_forever()

        aio.run(main)

    def stop(self):
        if self._thread.is_alive():
            with contextlib.suppress(RuntimeError):     # its loop has closed
                self.loop.call_soon_threadsafe(self.server.close)
            self._thread.join(5)

    def hold_loop(self, release: threading.Event):
        """Stall the server's loop until `release` is set: its socket still accepts, and
        nothing it serves answers, `rpc.hello` included."""
        self.loop.call_soon_threadsafe(release.wait, 10)

    def methods(self):
        return [m for m, _ in self.calls if m != "session"]


@pytest.fixture
def scripted(turns_env):
    made = []

    def make(script=None, **kw):
        server = Scripted(f"scripted-{len(made)}", script, **kw)
        made.append(server)
        return server, client.Session(server.address, dict(SESSION))

    yield make
    for server in made:
        server.stop()


def _dropped(answer):
    """A script step that closes the connection and drops `answer`."""
    def step(conn, params):
        conn.close()
        return answer
    return step


def _settled(value):
    return lambda conn, params: {"delivery": "d-1", "result": value}


# -- a dead endpoint, call by call ------------------------------------------------------

def test_nothing_serving_reads_refused_on_hand_ins_and_raises_on_reads(turns_env):
    s = client.Session(ipc.address("turns", "nobody"), dict(SESSION))
    assert s.deliver("x").wait_started(1) == Refused("gone")
    assert s.deliver("x", ref="r").wait_ended(1) == Refused("gone")
    assert s.steer("x").wait_started(1) == Refused("gone")
    assert s.command("clear") == Refused("gone")
    for read in (s.facts, s.agents, s.refresh, s.watch):
        with pytest.raises(turns.TurnsError):
            read()


def test_a_server_that_closes_during_the_hello_reads_as_never_written(scripted):
    server, s = scripted(refuse_session=True)
    assert s.deliver("x").wait_started(1) == Refused("gone")
    assert s.command("clear") == Refused("gone")
    with pytest.raises(turns.TurnsError):
        s.facts()
    # A wait names a delivery an earlier call recorded: an endpoint that still listens
    # says nothing about it.
    with pytest.raises(TimeoutError):
        client.Delivery(s, "d-1", "r").wait_started(1)
    assert server.methods() == []


@pytest.mark.parametrize("held", ["hello", "session"])
def test_a_wait_whose_connect_stalls_raises_timeout_never_lost(scripted, monkeypatch, held):
    monkeypatch.setattr(client, "CONNECT_S", 0.3)
    monkeypatch.setattr(client, "READ_S", 0.3)
    started = {"kind": "started", "turn_id": "t1", "delivery": "d-1", "ref": "r"}
    server, s = scripted({"wait": lambda conn, params: {"result": started}})
    d = client.Delivery(s, "d-1", "r")               # an earlier deliver recorded it
    release = threading.Event()
    try:
        if held == "hello":
            server.hold_loop(release)
        else:
            server.session_gate = release
        with pytest.raises(TimeoutError):
            d.wait_started(1)
        with pytest.raises(TimeoutError):
            d.wait_ended(None)
    finally:
        release.set()
    assert server.methods() == []
    # The hold clears: the same wait reads the delivery's value.
    assert d.wait_started(2) == Started("t1", "d-1", "r")
    server.stop()
    # Nothing listens any more: that, and only that, reads lost.
    assert d.wait_started(1) == Lost("unanswered", delivery="d-1", ref="r")


def test_a_wait_that_stalls_while_the_endpoint_ends_reads_lost_within_its_time(scripted):
    # An ending endpoint can accept a connect and fail its hello before it stops
    # listening. The wait asks again inside the time it was given, and the endpoint's
    # going is what it then reads.
    server, s = scripted()
    d = client.Delivery(s, "d-1", "r")
    server.refuse_session = True
    stopper = threading.Timer(0.3, server.stop)
    stopper.start()
    try:
        assert d.wait_started(5) == Lost("unanswered", delivery="d-1", ref="r")
    finally:
        stopper.cancel()
    assert server.methods() == []


def test_a_look_up_by_ref_with_no_deadline_raises_timeout_when_it_cannot_connect(scripted):
    server, s = scripted({"deliver": _dropped({"delivery": "d-1", "ref": "r"})})
    d = s.deliver("x", ref="r")
    assert d.id is None
    server.refuse_session = True                     # it listens, and fails each hello
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        waiting = pool.submit(d.wait_started, None)
        try:
            concurrent.futures.wait([waiting], 5)
            assert waiting.done(), "the wait asks again forever"
            assert isinstance(waiting.exception(), TimeoutError)
        finally:
            server.refuse_session = False
    assert server.methods() == ["deliver"]


def test_the_gone_error_reads_refused_and_lost(scripted):
    def gone(conn, params):
        raise ipc.RpcError(1001, "the endpoint is ending: bye")

    server, s = scripted({"deliver": gone, "steer": gone, "command": gone, "wait": gone,
                          "facts": gone})
    assert s.deliver("x").wait_started(1) == Refused("gone")
    assert s.steer("x").wait_started(1) == Refused("gone")
    assert s.command("clear") == Refused("gone")
    with pytest.raises(turns.TurnsError) as caught:
        s.facts()
    assert caught.value.code == 1001
    d = client.Delivery(s, "d-1")
    assert isinstance(d.wait_started(1), Lost)


# -- written, not answered ---------------------------------------------------------------

def test_a_hand_in_whose_answer_never_comes_is_unknown_not_refused(scripted):
    server, s = scripted({"deliver": _dropped({"delivery": "d-1", "ref": "r"}),
                          "command": _dropped({"result": {"kind": "ran"}})})
    d = s.deliver("x", ref="r")
    assert d.id is None and d.ref == "r"
    assert s.command("clear") == turns.Failed("unanswered")
    server.stop()
    # The reconnect finds nothing serving.
    assert d.wait_started(1) == Lost("unanswered", ref="r")


def test_an_unanswered_hand_in_with_no_ref_reads_lost_unanswered(scripted):
    server, s = scripted({"deliver": _dropped({"delivery": "d-1", "ref": None})})
    d = s.deliver("x")
    assert d.id is None
    assert d.wait_started(1) == Lost("unanswered")
    assert server.methods() == ["deliver"]           # nothing to look it up by


def test_the_reconnect_resolves_by_ref_and_goes_on_by_id(scripted):
    asked = []

    def wait(conn, params):
        asked.append(params)
        if "ref" in params:
            return {"delivery": "d-1", "pending": True}
        return {"result": {"kind": "started", "turn_id": "t1", "delivery": "d-1",
                           "ref": "r"}}

    server, s = scripted({"deliver": _dropped({"delivery": "d-1", "ref": "r"}),
                          "wait": wait})
    d = s.deliver("x", ref="r")
    assert d.id is None
    assert d.wait_started(2) == Started("t1", "d-1", "r")
    assert d.id == "d-1"
    assert asked[0] == {"ref": "r", "until": "started", "timeout_ms": 0}
    assert asked[1]["delivery"] == "d-1" and "ref" not in asked[1]


def test_a_ref_the_endpoint_never_recorded_reads_its_lost(scripted):
    unknown = {"kind": "lost", "reason": "unknown delivery: no delivery carries this ref",
               "delivery": None, "ref": "r"}
    server, s = scripted({"deliver": _dropped({"delivery": "d-1", "ref": "r"}),
                          "wait": lambda conn, params: {"delivery": None,
                                                        "result": unknown}})
    d = s.deliver("x", ref="r")
    lost = d.wait_started(1)
    assert isinstance(lost, Lost) and lost.reason.startswith("unknown delivery")
    assert d.wait_ended(1) == lost and server.methods() == ["deliver", "wait"]


def test_a_timeout_leaves_the_shared_connection_open_and_the_resolve_rides_it(
        scripted, monkeypatch):
    monkeypatch.setattr(client, "READ_S", 0.3)
    release = threading.Event()
    answers = iter([None, {"delivery": "d-2", "ref": "b"}])

    def deliver(conn, params):
        answer = next(answers)
        if answer is None:
            release.wait(5)                          # past the read bound
            return {"delivery": "d-1", "ref": "a"}
        return answer

    seen = []

    def wait(conn, params):
        seen.append(params)
        return {"delivery": "d-1", "result": {"kind": "started", "turn_id": "t1",
                                              "delivery": "d-1", "ref": "a"}}

    server, s = scripted({"deliver": deliver, "wait": wait})
    first = s.deliver("x", ref="a")
    assert first.id is None
    release.set()
    second = s.deliver("y", ref="b")                 # answered on the same connection
    assert second.id == "d-2"
    assert first.wait_started(2).turn_id == "t1"
    assert server.connections == 1                   # one connection carried all three
    assert seen == [{"ref": "a", "until": "started", "timeout_ms": 0}]


def test_an_unanswered_wait_raises_timeout_and_pending_does_too(scripted, monkeypatch):
    monkeypatch.setattr(client, "READ_S", 0.2)
    server, s = scripted({"wait": lambda conn, params: time.sleep(1) or {"pending": True}})
    with pytest.raises(TimeoutError):
        client.Delivery(s, "d-1").wait_started(0.1)
    server.script["wait"] = lambda conn, params: {"pending": True}
    with pytest.raises(TimeoutError):
        client.Delivery(s, "d-1").wait_started(0.1)


def test_a_wait_whose_connection_closes_reconnects_and_goes_on_by_id(scripted):
    answers = iter([None, {"result": {"kind": "started", "turn_id": "t1",
                                      "delivery": "d-1", "ref": None}}])

    def wait(conn, params):
        answer = next(answers)
        if answer is None:
            conn.close()
        return answer

    server, s = scripted({"wait": wait})
    assert client.Delivery(s, "d-1").wait_started(2) == Started("t1", "d-1")
    assert server.methods() == ["wait", "wait"]


def test_a_shared_connection_that_closed_is_replaced_before_the_next_write(scripted):
    def facts(conn, params):
        conn.close()                                  # 0b may close an idle connection
        return {}

    server, s = scripted({"deliver": lambda conn, params: {"delivery": "d-1", "ref": None},
                          "facts": facts})
    assert s.deliver("x").id == "d-1"
    with pytest.raises(turns.TurnsError):
        s.facts()
    time.sleep(0.1)
    s._short._client.close()                          # the first connection closes too
    assert s.deliver("y").id == "d-1"                 # written on a new one, not lost
    assert server.methods() == ["deliver", "facts", "deliver"]


def test_an_unanswered_stop_answer_is_logged_and_read_as_neither(scripted, caplog):
    def handle(conn, params):
        threading.Timer(0.1, conn.notify_threadsafe,
                        ("stop.asked", {"ask": 1, "facts": {"kind": "stop"}})).start()
        return {}

    server, s = scripted({"stops.handle": handle,
                          "stops.answer": _dropped({"taken": True})})
    asked = threading.Event()
    h = s.handle_stops(lambda facts: asked.set() or "go on")
    assert asked.wait(2)
    deadline = time.monotonic() + 3
    while "unknown" not in caplog.text and time.monotonic() < deadline:
        time.sleep(0.05)
    assert "whether it was taken is unknown" in caplog.text
    h.close()


# -- against the fake harness's endpoint -------------------------------------------------

def test_a_dropped_deliver_answer_resolves_to_what_the_endpoint_recorded(
        make_endpoint, monkeypatch):
    original = rpc.Rpc._deliver

    async def drop_after_record(self, conn, params):
        answer = await original(self, conn, params)
        conn.close()                                  # the answer never leaves
        return answer

    monkeypatch.setattr(rpc.Rpc, "_deliver", drop_after_record)
    ep, fake = make_endpoint()
    s = turns.find(session_id="s-1")
    d = s.deliver("hello", only_if_idle=True, ref="r-1")
    assert d.id is None
    assert bare(d.wait_started(2)) == Started("t1")
    assert d.id is not None and [x.text for x in fake.submitted] == ["hello"]


def test_facts_and_agents_ride_their_own_connection(make_endpoint):
    ep, fake = make_endpoint()
    s = turns.find(session_id="s-1")
    s.deliver("x")
    s.facts()
    assert s._short._client is not s._reads._client
    s.close()
    with pytest.raises(turns.TurnsError):
        s.facts()


def test_wait_ready_returns_once_the_ready_is_raised(make_endpoint):
    ep, fake = make_endpoint()
    with pytest.raises(TimeoutError):
        turns.wait_ready(session_id="s-1", timeout=0.3)
    threading.Timer(0.2, ep.ready, ("startup", "s-1")).start()
    s = turns.wait_ready(session_id="s-1", timeout=3)
    assert s.facts().ready.source == "startup"
    assert turns.wait_ready(pid=os.getpid(), timeout=1).session_id == "s-1"


# -- versions ---------------------------------------------------------------------------

def test_a_live_version_1_endpoint_raises_version_error_and_a_dead_one_is_skipped(
        make_endpoint, turns_env):
    from test_wire import V1Endpoint

    v1 = V1Endpoint(client.runtime_dir() / "claude-old", "s-old")
    try:
        with pytest.raises(turns.VersionError) as caught:
            turns.find(session_id="s-old")
        assert "protocol 1" in str(caught.value) and "2 to 2" in str(caught.value)
    finally:
        v1.close()
    before = sorted(os.listdir(v1.dir))
    assert turns.find(session_id="s-old") is None       # dead: skipped
    assert sorted(os.listdir(v1.dir)) == before          # and never removed
    assert turns.sessions() == []


def test_a_v2_endpoint_beside_a_v1_directory_is_found(make_endpoint):
    v1 = client.runtime_dir() / "claude-gone"
    v1.mkdir(parents=True)
    (v1 / "hello.json").write_text(json.dumps({"session_id": "s-1", "pid": 1}))
    ep, _ = make_endpoint()
    assert turns.find(session_id="s-1").dir == ep.dir
    assert v1.exists()


def test_an_endpoint_of_another_version_names_both_ranges(turns_env):
    from moxi import aio

    address = ipc.address("turns", "v3")
    ready = concurrent.futures.Future()

    async def main():
        import asyncio

        server = await ipc.serve(address, lambda c, m, p: None, hello=ipc.Hello((3, 3)))
        ready.set_result((asyncio.get_running_loop(), server))
        await server.serve_forever()

    thread = threading.Thread(target=aio.run, args=(main,), daemon=True)
    thread.start()
    loop, server = ready.result(5)
    try:
        directory = protocol.v2_root(client.runtime_dir()) / "fake-v3"
        directory.mkdir(parents=True)
        (directory / "hello.json").write_text(json.dumps(
            {"address": address, "protocol": 3, "session_id": "s-3", "pid": 1}))
        with pytest.raises(turns.VersionError) as caught:
            turns.find(session_id="s-3")
        assert caught.value.server == (3, 3) and caught.value.client == (2, 2)
        assert "upgrade moxi-turns" in str(caught.value)
    finally:
        loop.call_soon_threadsafe(server.close)
        thread.join(5)
