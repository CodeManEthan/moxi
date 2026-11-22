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

A register that replaces a live connection (`reregister`) closes the old one, and a
register declined after its connect closes the connection it opened. Either connection's
close handler runs on its own reader thread, which can be after a newer connection is
installed. That close leaves the installed binding online; each test holds the handler
until the install and then sends mail.

While a reregister waits for the daemon to drop the binding it replaces, the claim is
offline, and the adapter's supervisor must not register in that gap. Its test holds the
daemon's drop and gives the supervisor its turn there, once in each order: with the old
binding dropped (a supervisor that registers would bind the name first) and with it still
held (a supervisor that registers would be refused and put the claim in standby).
"""

from __future__ import annotations

import itertools
import threading
import time

import pytest
from conftest import LIVE_S, clock_reads_warm, wait_until

from moxi_comms import claimfile, codex_threads
from moxi_comms import daemon as daemon_mod
from moxi_comms.adapters import binding, opencode_roster
from moxi_comms.adapters.codex import CodexAdapter
from moxi_comms.adapters.codex_roster import CodexAgent
from moxi_comms.adapters.opencode import OpenCodeAdapter
from moxi_comms.adapters.opencode_roster import OpenCodeAgent
from moxi_comms.client import DaemonError, DaemonOffline
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

    def steer(text, *, ref=None, **asker):
        if MARK in text:
            steered_at.append(time.monotonic())
        return session_steer(text, ref=ref, **asker)

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


def test_mail_after_a_reregister_is_delivered_when_the_old_close_lands_late(rig, port):
    """A bound adapter registers again. The close handler of the connection it replaced
    runs on that connection's reader thread, and here it is held until the new
    connection is installed. A close from a replaced connection leaves the installed
    binding online, so mail sent afterwards is delivered."""
    adapter, carried = port
    replaced: list = []                        # the connection the register replaces
    late = threading.Event()                   # set: hold the next close until the install
    ran_late: list = []                        # whether the held close found it installed
    handle_close = adapter._on_daemon_close

    def installed() -> bool:
        daemon = adapter._daemon
        return daemon is not None and daemon is not replaced[0] and not adapter.offline

    def on_daemon_close():
        if late.is_set():
            late.clear()
            ran_late.append(wait_until(installed, LIVE_S))
        handle_close()

    adapter._on_daemon_close = on_daemon_close      # read by every connect after start
    adapter.start()
    qualified = adapter.agent.qualified
    assert wait_until(lambda: not adapter.offline and not adapter.replaying, LIVE_S)

    replaced.append(adapter._daemon)
    late.set()
    adapter.reregister()
    assert wait_until(lambda: ran_late, LIVE_S), "the replaced connection's close ran"
    assert ran_late == [True], "it ran after the new connection was installed"

    code, out = rig.send(qualified, MARK, sender=dict(name="main", scope="proj-a"))
    assert code == 0, out
    assert wait_until(lambda: carried() >= 1, LIVE_S), \
        "mail sent after the late close was delivered"


class DeclineOnce:
    """Declines the next register after its connect, once armed: the connection is
    opened and the bind is never sent. `client` is the connection it declined."""

    def __init__(self, monkeypatch):
        self.armed = threading.Event()
        self.client = None
        original = binding.register_and_install

        def register_and_install(owner, *, register, **kw):
            def declining_register(client):
                if self.armed.is_set():
                    self.armed.clear()
                    self.client = client
                    raise DaemonOffline("comms offline: the test declined this register")
                return register(client)

            return original(owner, register=declining_register, **kw)

        monkeypatch.setattr(binding, "register_and_install", register_and_install)


def test_mail_is_delivered_when_a_declined_register_s_close_lands_after_a_bind(
        rig, port, monkeypatch):
    """A reconnecting adapter's first register is declined after its connect, and the
    close handler of the connection it opened is held until the next register has
    installed. That close leaves the installed binding online, so mail sent afterwards
    is delivered."""
    adapter, carried = port
    decline = DeclineOnce(monkeypatch)
    late = threading.Event()                   # set: hold the next close until the install
    ran_late: list = []                        # whether the held close found it installed
    handle_close = adapter._on_daemon_close

    def installed() -> bool:
        daemon = adapter._daemon
        return (daemon is not None and decline.client is not None
                and daemon is not decline.client and not adapter.offline)

    def on_daemon_close():
        if late.is_set():
            late.clear()
            ran_late.append(wait_until(installed, LIVE_S))
        handle_close()

    adapter._on_daemon_close = on_daemon_close      # read by every connect after start
    adapter.start()
    qualified = adapter.agent.qualified
    assert wait_until(lambda: not adapter.offline and not adapter.replaying, LIVE_S)

    rig.daemon.stop()
    assert wait_until(lambda: adapter.offline, LIVE_S)
    late.set()
    decline.armed.set()
    rig.daemon = daemon_mod.Daemon(rig.record, clock=rig.clock,
                                   start_trim_thread=False).start()
    assert wait_until(lambda: ran_late, LIVE_S), "the declined connection's close ran"
    assert ran_late == [True], "it ran after the next register installed"

    code, out = rig.send(qualified, MARK, sender=dict(name="main", scope="proj-a"))
    assert code == 0, out
    assert wait_until(lambda: carried() >= 1, LIVE_S), \
        "mail sent after the late close was delivered"


class _LockProbe:
    """The claim's register lock, telling `on_skip` when a thread other than `caller`
    tries it without blocking and finds it held: the supervisor skipping its turn."""

    def __init__(self, lock, on_skip):
        self.lock = lock
        self.on_skip = on_skip
        self.caller: threading.Thread | None = None

    def acquire(self, *, blocking=True, timeout=-1):
        got = self.lock.acquire(blocking=blocking, timeout=timeout)
        if not got and not blocking and self.caller is not None \
                and threading.current_thread() is not self.caller:
            self.on_skip()
        return got

    def release(self):
        self.lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc_info):
        self.release()


class ReregisterRace:
    """Gives the adapter's supervisor its turn in the middle of a `reregister`, once armed.

    The daemon drops the replaced binding only when it is let go (the test holds its
    handling of the old connection's close), so the reregister's first bind is refused
    while the old close and the refused one's mark the claim offline. Each retry after
    that waits until the supervisor has had its turn: it registered, or it found the
    register lock held and skipped. With `supervisor_first` the old binding is dropped
    as soon as the first bind is refused and the supervisor's register waits for that,
    so a supervisor that registers binds the name; otherwise its register comes while
    the old binding is still held and is refused, and the old binding is dropped after.
    `waited` records, per retry, whether the supervisor's turn came."""

    def __init__(self, rig, adapter, *, supervisor_first: bool):
        self.supervisor_first = supervisor_first
        self.qualified = adapter.agent.qualified
        self.caller: threading.Thread | None = None
        self.tries = 0
        self.waited: list = []
        self.let_go = threading.Event()        # the daemon may drop the old binding
        self.dropped = threading.Event()       # it did
        self.turn_over = threading.Event()     # the supervisor registered or skipped
        self._holding = False

        daemon = rig.daemon
        on_closed = daemon.host._on_closed     # `Daemon._deregister`, bound at its start

        def held_close(conn):
            held = any(b.conn is conn and b.qualified == self.qualified
                       for b in list(daemon.bindings.values()))
            if held and self._holding:
                self._holding = False
                self.let_go.wait(LIVE_S)
                on_closed(conn)
                self.dropped.set()
                return
            on_closed(conn)

        daemon.host._on_closed = held_close

        self.probe = _LockProbe(adapter.claims.register_lock, self.turn_over.set)
        adapter.claims.register_lock = self.probe
        connect = adapter.connect_daemon

        def connect_daemon():
            if self.caller is None:
                return connect()
            if threading.current_thread() is not self.caller:
                if self.supervisor_first:
                    self.dropped.wait(LIVE_S)
                try:
                    return connect()
                finally:
                    self.turn_over.set()
            self.tries += 1
            if self.tries > 1:
                self.waited.append(self.turn_over.wait(LIVE_S))
                self.let_go.set()
            try:
                return connect()
            except DaemonError:
                if self.tries == 1 and self.supervisor_first:
                    self.let_go.set()
                raise

        adapter.connect_daemon = connect_daemon

    def arm(self) -> None:
        """From now on the calling thread's registers are the reregister's."""
        self._holding = True
        self.caller = self.probe.caller = threading.current_thread()


@pytest.mark.parametrize("supervisor_first", [True, False],
                         ids=["supervisor-binds", "supervisor-refused"])
def test_a_reregister_keeps_the_name_and_its_mail_when_the_supervisor_tries_meanwhile(
        rig, port, supervisor_first):
    """The daemon drops the binding a reregister replaces late, so the claim is offline
    while the reregister retries its refused bind, and the adapter's supervisor gets its
    turn in that gap. The reregister holds the register lock, so the supervisor skips:
    it neither binds first, which would leave the reregister refused as already bound,
    nor is refused by the old binding, which would put the claim in standby under the
    reregister's binding and drop its mail. The reregister binds, the claim is held, and
    mail sent afterwards is delivered."""
    adapter, carried = port
    race = ReregisterRace(rig, adapter, supervisor_first=supervisor_first)
    adapter.start()
    qualified = adapter.agent.qualified
    assert wait_until(lambda: not adapter.offline and not adapter.replaying, LIVE_S)

    race.arm()
    try:
        adapter.reregister()
    except DaemonError as exc:
        pytest.fail(f"the reregister was refused: {exc}")
    assert race.tries > 1, "the reregister's first bind was refused"
    assert race.waited and all(race.waited), "the supervisor had its turn meanwhile"
    assert race.dropped.is_set(), "the daemon dropped the replaced binding"
    assert adapter.claims.state == claimfile.BOUND and adapter.claims.holds, \
        f"the claim is held, not {adapter.claims.state} (lost: {adapter.claims.lost})"
    assert not adapter.offline

    code, out = rig.send(qualified, MARK, sender=dict(name="main", scope="proj-a"))
    assert code == 0, out
    assert wait_until(lambda: carried() >= 1, LIVE_S), \
        "mail sent after the reregister was delivered"
