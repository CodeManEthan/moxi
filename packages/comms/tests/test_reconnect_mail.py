"""Mail waiting for a harness adapter when it reconnects to the daemon is delivered once
the new connection is installed, and only once.

At a re-register the daemon answers `bind`, hands the waiting mail and pushes
`replay_complete` right after the answer. The adapter's reader thread queues the mail and
ends the catch-up, and that can happen before the registering thread installs the new
client. The flush holds while the adapter is offline, so the digest is opened on the new
client. Opened on the old, closed one it would be dropped, and the daemon, which counts
the id as handed to the live binding, would not hand it again until the next
registration.

Each test forces that order with no load: it holds the supervisor for a second between
the bind's answer and the install, while the mail waits. Both ports that flush through
`HarnessAdapter.flush` are driven: OpenCode and Codex.

Codex has a second path. While a turn runs, its interrupt mail goes into the turn by
`steer`, and that holds while the adapter is offline too. Mail the daemon releases at a
bind is marked held and never steers, so the steer's window is live interrupt mail that
arrives after the bind's answer and before the install; its test sends it then.
"""

from __future__ import annotations

import itertools
import threading
import time

import pytest
from conftest import LIVE_S, clock_reads_warm, wait_until

from moxi_comms import codex_threads
from moxi_comms import daemon as daemon_mod
from moxi_comms.adapters import binding, opencode_roster
from moxi_comms.adapters.codex import CodexAdapter
from moxi_comms.adapters.codex_roster import CodexAgent
from moxi_comms.adapters.opencode import OpenCodeAdapter
from moxi_comms.adapters.opencode_roster import OpenCodeAgent
from fake_opencode import FakeOpenCode
from fake_turns import FakeSession, fake_find

FAST = 0.05
HOLD_S = 1.0
PASSWORD = "test-password"
MARK = "WAITING-AT-RECONNECT"


@pytest.fixture(autouse=True)
def _warm(monkeypatch):
    clock_reads_warm(monkeypatch, "codex", "opencode")


class _Handle:
    """The fake OpenCode server as the adapter's server: listening, never respawned."""

    def __init__(self, fake):
        self.fake = fake
        self.alive = True
        self.returncode = None

    def wait_listening(self, timeout=None):
        return self.fake.url

    def stop(self):
        pass


def opencode_port(rig, tmp_path, request):
    """An OpenCode adapter over a fake server, and how many of its posts carry `MARK`."""
    fake = FakeOpenCode(password=PASSWORD, default_model="opencode/minimax-m3.1-flash-free",
                        heartbeat=0.5).start()
    request.addfinalizer(fake.stop)
    fake.auto = False
    agent = OpenCodeAgent(qualified="coder@proj-a", name="coder", scope="proj-a",
                          cwd=str(tmp_path),
                          session_file=opencode_roster.session_path("coder@proj-a"),
                          models=["opencode/minimax-m3.1-flash-free"], directors=[])
    adapter = OpenCodeAdapter(agent, spawn=False, server=_Handle(fake), password=PASSWORD,
                              autoreconnect=True, backoff=itertools.repeat(FAST),
                              clock=rig.clock)
    fake.on_log = adapter.on_log_line

    def carried() -> int:
        return sum(MARK in body["parts"][0]["text"] for method, path, body in fake.requests
                   if method == "POST" and path.endswith("/prompt_async"))

    return adapter, carried


def codex_session(rig, tmp_path, request, **kw):
    """A Codex adapter over a fake hosted session, and the session (`kw` goes to
    `FakeSession`)."""
    session = FakeSession(**kw)
    agent = CodexAgent(qualified="planner@proj-a", name="planner", scope="proj-a",
                       cwd=str(tmp_path), thread_file=codex_threads.path("planner@proj-a"),
                       directors=[])
    adapter = CodexAdapter(agent, find=fake_find(session), autoreconnect=True,
                           backoff=itertools.repeat(FAST), clock=rig.clock)
    request.addfinalizer(lambda: session.gone("the test ended"))
    return adapter, session


def codex_port(rig, tmp_path, request):
    """A Codex adapter over a fake hosted session, and how many hand-ins carry `MARK`."""
    adapter, session = codex_session(rig, tmp_path, request)

    def carried() -> int:
        return sum(MARK in handed.text for handed in session.delivered)

    return adapter, carried


PORTS = {"opencode": opencode_port, "codex": codex_port}


@pytest.fixture(params=list(PORTS))
def port(request, rig, tmp_path):
    adapter, carried = PORTS[request.param](rig, tmp_path, request)
    yield adapter, carried
    adapter.stop()


class InstallHold:
    """Holds a register for `HOLD_S` between the daemon's answer and the install, once
    armed. `holding` is set when the hold starts; with `go` given, the hold first waits
    for it (at most `LIVE_S`). `released_at` is when the held register went on to
    install."""

    def __init__(self, monkeypatch):
        self.armed = threading.Event()
        self.holding = threading.Event()
        self.go: threading.Event | None = None
        self.released_at: float | None = None
        original = binding.register_and_install

        def register_and_install(owner, *, accept=None, **kw):
            inner = accept

            def held_accept(resp):
                if inner is not None:
                    inner(resp)
                if self.armed.is_set():          # read at the answer, not at the call
                    self.armed.clear()
                    self.holding.set()
                    if self.go is not None:
                        self.go.wait(LIVE_S)
                    time.sleep(HOLD_S)
                    self.released_at = time.monotonic()

            return original(owner, accept=held_accept, **kw)

        monkeypatch.setattr(binding, "register_and_install", register_and_install)


@pytest.fixture
def hold(monkeypatch):
    return InstallHold(monkeypatch)


def test_mail_waiting_at_a_reconnect_is_delivered_once_after_the_install(
        rig, port, hold):
    """A bound adapter that cannot take a digest holds one handed message when the
    daemon restarts. The new daemon hands it again at the bind, and the install is held
    a second past the answer. The message is delivered once, after the install."""
    adapter, carried = port
    gate = threading.Event()                          # set: the port may take a digest
    port_can_inject = adapter.can_inject
    adapter.can_inject = lambda: gate.is_set() and port_can_inject()
    delivered_at: list = []
    port_deliver = adapter.deliver

    def deliver(text, high, identity, count):
        if MARK in text:
            delivered_at.append(time.monotonic())
        return port_deliver(text, high, identity, count)

    adapter.deliver = deliver
    adapter.start()
    qualified = adapter.agent.qualified
    assert wait_until(lambda: not adapter.offline and not adapter.replaying, LIVE_S)

    code, out = rig.send(qualified, MARK, sender=dict(name="main", scope="proj-a"))
    assert code == 0, out
    assert wait_until(lambda: adapter.queue, LIVE_S), "the daemon handed the message"

    rig.daemon.stop()
    assert wait_until(lambda: adapter.offline, LIVE_S)
    gate.set()
    hold.armed.set()
    rig.daemon = daemon_mod.Daemon(rig.record, clock=rig.clock,
                                   start_trim_thread=False).start()

    assert wait_until(lambda: carried() >= 1, LIVE_S), \
        "the message waiting at the reconnect was delivered"
    assert hold.released_at is not None, "the install was held past the bind's answer"
    assert delivered_at and delivered_at[0] >= hold.released_at, \
        "delivered on the installed connection, not before it"
    assert not wait_until(lambda: carried() > 1, timeout=0.5), \
        f"delivered once, not {carried()} times"


def test_interrupt_mail_in_a_reconnect_s_gap_is_steered_once_after_the_install(
        rig, tmp_path, request, hold):
    """A Codex adapter mid turn reconnects. Interrupt mail sent after the bind's answer
    and before the install is handed on the new connection, and the install is held until
    the adapter has queued it and a second past that. The message goes into the running
    turn by `steer`, once, after the install."""
    adapter, session = codex_session(rig, tmp_path, request, turn_open=True)
    request.addfinalizer(adapter.stop)
    steered_at: list = []
    session_steer = session.steer

    def steer(text, *, ref=None):
        if MARK in text:
            steered_at.append(time.monotonic())
        return session_steer(text, ref=ref)

    session.steer = steer
    queued_at: list = []                    # when the adapter queued the interrupt mail
    on_push = adapter._on_push

    def push(method, params):
        on_push(method, params)
        payload = params.get("payload") or {}
        if (method == "deliver" and MARK in payload.get("text", "")
                and str(payload.get("message_id")) in adapter.held_ids):
            queued_at.append(time.monotonic())

    adapter._on_push = push                 # read by every connect after start

    def carried() -> list:
        return [handed for handed in session.delivered if MARK in handed.text]

    adapter.start()
    qualified = adapter.agent.qualified
    assert wait_until(lambda: not adapter.offline and not adapter.replaying, LIVE_S)
    assert wait_until(lambda: adapter.turn_open, LIVE_S)

    rig.daemon.stop()
    assert wait_until(lambda: adapter.offline, LIVE_S)
    hold.go = threading.Event()
    hold.armed.set()
    rig.daemon = daemon_mod.Daemon(rig.record, clock=rig.clock,
                                   start_trim_thread=False).start()
    assert hold.holding.wait(LIVE_S), "the reconnect reached the bind's answer"
    code, out = rig.send(qualified, MARK, "--interrupt",
                         sender=dict(name="main", scope="proj-a"))
    try:
        assert code == 0, out
        assert wait_until(lambda: queued_at, LIVE_S), "the interrupt mail was queued"
        assert hold.released_at is None, "it was queued while the install was held"
    finally:
        hold.go.set()

    assert wait_until(lambda: carried(), LIVE_S), \
        "the interrupt mail sent in the reconnect's gap was delivered"
    assert all(handed.steer for handed in carried()), "it went into the running turn"
    assert hold.released_at is not None, "the install was held past the bind's answer"
    assert steered_at and steered_at[0] >= hold.released_at, \
        "steered on the installed connection, not before it"
    assert not wait_until(lambda: len(carried()) > 1, timeout=0.5), \
        f"delivered once, not {len(carried())} times"
