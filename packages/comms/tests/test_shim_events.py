"""The Claude shim's frames to the daemon.

`ready` carries the SessionStart's `source`; a SessionStart before the bind is owed and
goes once after the register answer; the idle tick sends no `kind`; every turn end sends
`kind=turn` with `handled` and `unhandled`, after the cursor write; a StopFailure's ids
are `unhandled`; the cursor is clamped with its high-water mark; a digest's ids leave the
held set exactly, never by comparison.

The daemon side of these frames is tested elsewhere, so the shim talks to a fake daemon
socket here: it answers `register`, records every frame with the cursor as it stood when
the frame arrived, and pushes `deliver` frames as the daemon's release would.
"""

from __future__ import annotations

import itertools
import os
import socket
import threading
import time
from pathlib import Path

import pytest
from conftest import FakeClock, wait_until

from moxi_comms import cursors
from moxi_comms import daemon as daemon_mod
from moxi_comms.adapters import claude_shim
from moxi_comms.adapters.claude_shim import ClaudeShim
from moxi_comms.client import DaemonError, DaemonOffline
from moxi_comms.paths import runtime_root
from moxi_comms.protocol import LineConn

import shim_drive

NAME, SCOPE = "reviewer", "proj-a"
IDENT = f"{NAME}@{SCOPE}"
SESSION = "11111111-2222-3333-4444-555555555555"
T0 = 1_790_000_000.0


class FakeDaemon:
    """A unix socket that speaks enough of the claims protocol for one shim."""

    def __init__(self, path: Path):
        self.path = path
        self.frames: list[dict] = []          # every frame, with the cursor at receipt
        self.conns: list[LineConn] = []
        self.registers: list[dict] = []
        self._lock = threading.Lock()
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(path))
        self._server.listen(8)
        self._stop = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self._stop.is_set():
            try:
                sock, _ = self._server.accept()
            except OSError:
                return
            conn = LineConn(sock)
            with self._lock:
                self.conns.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        while True:
            try:
                frame = conn.recv()
            except Exception:                                     # noqa: BLE001
                return
            if frame is None:
                return
            seen = dict(frame, _cursor=cursors.read(IDENT), _conn=self.conns.index(conn))
            with self._lock:
                self.frames.append(seen)
            if frame.get("op") == "register":
                with self._lock:
                    self.registers.append(frame)
                try:
                    conn.send({"id": frame["id"], "ok": True,
                               "identity": {"qualified": IDENT, "name": NAME,
                                            "scope": SCOPE, "tier": "chosen"}})
                except OSError:
                    return

    def push(self, *mids):
        """`deliver` frames on the newest connection, as a release hands them."""
        conn = self.conns[-1]
        for mid in mids:
            conn.send({"op": "deliver", "payload": payload(mid)})

    def drop(self):
        """Close every connection, as a daemon restart does."""
        for conn in list(self.conns):
            conn.close()

    def activity(self, kind="any"):
        with self._lock:
            out = [f for f in self.frames if f.get("op") == "activity"]
        if kind == "any":
            return out
        return [f for f in out if f.get("kind") == kind]

    def close(self):
        self._stop.set()
        self._server.close()
        self.drop()


def payload(mid):
    return {"message_id": str(mid), "channel_id": "c1", "room": "#names",
            "author": {"name": "lead", "tier": "chosen", "attribution": "verified"},
            "recipients": [IDENT], "posture": "open", "backlog": False,
            "text": f"body {mid}", "reply_to": None, "audience": None,
            "audience_source": None, "timestamp": "2026-09-30T12:00:00Z"}


@pytest.fixture
def daemon():
    d = FakeDaemon(runtime_root() / "fake-daemon.sock")
    yield d
    d.close()


@pytest.fixture
def make_shim(daemon):
    made = []

    def make(start=True, **kw):
        kw.setdefault("autoreconnect", False)
        s = ClaudeShim(name=NAME, scope=SCOPE, project=SCOPE, socket_path=daemon.path,
                       injector=lambda text: None, session_id=SESSION,
                       clock=FakeClock(T0), **kw)
        made.append(s)
        if start:
            s.start()
        return s

    yield make
    for s in made:
        client = s._client
        s.stop()
        if client is not None and client._reader is not None:
            client._reader.join(2.0)      # its close lands before the tmp roots go


def deliver(daemon, shim, *mids):
    daemon.push(*mids)
    assert wait_until(lambda: all(str(m) in shim.held_ids for m in mids))


def turns(daemon, count):
    assert wait_until(lambda: len(daemon.activity("turn")) >= count), daemon.frames
    return daemon.activity("turn")


# ---------------------------------------------------------------- `ready` and `source`

@pytest.mark.parametrize("source", ["startup", "resume", "clear", "compact", "fork"])
def test_ready_carries_the_session_starts_source(daemon, make_shim, source):
    s = make_shim()
    shim_drive.ready(s, source, SESSION)
    (ready,) = wait_until(lambda: daemon.activity("ready"))
    assert ready["source"] == source, "sent as Claude Code said it; the daemon maps fork"
    assert ready["session_id"] == SESSION and ready["identity"] == IDENT


def test_a_session_start_without_a_source_sends_a_ready_without_one(daemon, make_shim):
    s = make_shim()
    shim_drive.ready(s)
    (ready,) = wait_until(lambda: daemon.activity("ready"))
    assert "source" not in ready


def test_a_ready_before_the_bind_is_owed_and_sent_once_after_the_register(
        daemon, make_shim):
    """SessionStart beats the bind. The shim keeps it and sends it right after its
    register is answered, once; a later reconnect does not send it again."""
    s = make_shim(start=False)
    shim_drive.ready(s, "startup", SESSION)
    assert daemon.frames == [] and s.owed_ready == {"source": "startup"}
    s.start()
    (ready,) = wait_until(lambda: daemon.activity("ready"))
    assert ready["source"] == "startup"
    ops = [f["op"] for f in daemon.frames]
    assert ops == ["register", "activity"], "right after the register answer"
    assert s.owed_ready is None
    daemon.drop()
    wait_until(lambda: s.offline)
    s.connect()
    assert wait_until(lambda: len(daemon.registers) == 2)
    assert len(daemon.activity("ready")) == 1, "owed once, not at every register"


def test_a_failed_claim_file_write_leaves_the_owed_ready_for_the_next_bind(daemon, make_shim, monkeypatch):
    """The owed `ready` is taken only once the claim file is written. A bind whose claim
    write raises keeps it, so the next bind sends it."""
    s = make_shim(start=False)
    shim_drive.ready(s, "startup", SESSION)
    real = s.claims.bound

    def failing(resp):
        raise OSError("disk full")

    monkeypatch.setattr(s.claims, "bound", failing)
    with pytest.raises(OSError):
        s._connect_once()
    assert s.owed_ready == {"source": "startup"}, "still owed"
    assert daemon.activity("ready") == []
    monkeypatch.setattr(s.claims, "bound", real)
    assert s._connect_once()
    (ready,) = wait_until(lambda: daemon.activity("ready"))
    assert ready["source"] == "startup"
    assert s.owed_ready is None


def test_a_ready_during_an_outage_is_owed_to_the_next_register(daemon, make_shim):
    s = make_shim()
    daemon.drop()
    wait_until(lambda: s.offline)
    shim_drive.ready(s, "compact", SESSION)
    assert daemon.activity("ready") == []
    s.connect()
    (ready,) = wait_until(lambda: daemon.activity("ready"))
    assert ready["source"] == "compact" and ready["_conn"] == 1


# ---------------------------------------------------------------- the idle tick

def test_the_idle_tick_sends_no_kind_even_after_a_prompt(daemon, make_shim):
    """The tick that flushes into an idle session is not a turn."""
    s = make_shim()
    s.note_turned()                                     # the session has had a turn
    deliver(daemon, s, 101)
    assert shim_drive.idle(s) is not None
    assert s.awaiting_completion == "101"
    wait_until(lambda: daemon.activity())
    activity = [f for f in daemon.activity() if "handled" not in f]
    assert activity and all("kind" not in f for f in activity)
    assert daemon.activity("turn") == []
    assert cursors.read(IDENT) is None


# ---------------------------------------------------------------- `turn` frames

def test_a_stop_reports_the_digest_handled_after_the_cursor_write(daemon, make_shim):
    s = make_shim()
    deliver(daemon, s, 101, 102)
    shim_drive.turn_ended(s)                            # flushes 101-102
    (first,) = turns(daemon, 1)
    assert first["handled"] == [] and first["unhandled"] == [], "nothing had ended"
    shim_drive.turn_ended(s)                            # 101-102 complete
    second = turns(daemon, 2)[1]
    assert second["handled"] == ["101", "102"] and second["unhandled"] == []
    assert second["_cursor"] == "102", "the cursor was written before the frame"
    assert s.held_ids == set()


def test_a_stop_failure_reports_the_digest_unhandled_and_the_cursor_stays(
        daemon, make_shim):
    """A failed turn's ids are `unhandled`, StopFailure included."""
    s = make_shim()
    deliver(daemon, s, 101)
    shim_drive.turn_ended(s)                            # flushes 101
    shim_drive.turn_ended(s, failed=True)               # the API error ends it
    failed = turns(daemon, 2)[1]
    assert failed["unhandled"] == ["101"] and failed["handled"] == []
    assert failed["_cursor"] is None and cursors.read(IDENT) is None
    assert s.held_ids == set(), "it has a row now: the frame went out"
    # The daemon hands it again at a later release; the shim takes it.
    deliver(daemon, s, 101)
    assert [p["message_id"] for p in s.queue] == ["101"]
    shim_drive.turn_ended(s)                            # flushes 101 again
    shim_drive.turn_ended(s)                            # and it completes
    last = turns(daemon, 4)[3]
    assert last["handled"] == ["101"] and last["_cursor"] == "101"


def test_the_failed_ids_are_held_for_the_cursor_write_and_not_after_the_frame(
        daemon, make_shim):
    s = make_shim()
    deliver(daemon, s, 105)
    s.on_turn_boundary()                                # flushes 105
    deliver(daemon, s, 110)
    s.on_turn_boundary()                                # 105 completes, flushes 110
    assert cursors.read(IDENT) == "105"
    s.on_turn_failed()                                  # 110 fails
    turn = turns(daemon, 3)[2]
    assert turn["unhandled"] == ["110"] and cursors.read(IDENT) == "105"


# ---------------------------------------------------------------------- the clamp

def test_the_cursor_never_passes_a_held_id_and_catches_up_to_the_high_water_mark(
        daemon, make_shim):
    """A replay hands an old live id (105) while a digest through 110 is in flight. 110
    completes: the cursor stops at 104. 105 completes: it goes to the mark, 110."""
    s = make_shim()
    deliver(daemon, s, 110)
    s.on_turn_boundary()                                # flushes 110
    deliver(daemon, s, 105)                             # queued below it
    s.on_turn_boundary()                                # 110 completes; flushes 105
    assert cursors.read(IDENT) == "104"
    assert s.completed_through == "110"
    s.on_turn_boundary()                                # 105 completes
    assert cursors.read(IDENT) == "110"
    handled = [t["handled"] for t in turns(daemon, 3)]
    assert handled == [[], ["110"], ["105"]]


def test_the_clamp_writes_nothing_while_the_lowest_held_id_is_the_first(daemon,
                                                                        make_shim):
    s = make_shim()
    deliver(daemon, s, 7)
    s.on_turn_boundary()                                # flushes 7
    deliver(daemon, s, 1)
    s.on_turn_boundary()                                # 7 completes, 1 held
    assert cursors.read(IDENT) is None


# ---------------------------------------------------------------- the held-id prune

def test_a_completing_digest_prunes_exactly_its_own_ids(daemon, make_shim):
    """105 queued below a completing digest through 110 stays held, so a second
    hand of it after a reconnect is not taken twice."""
    s = make_shim()
    deliver(daemon, s, 110)
    s.on_turn_boundary()                                # flushes 110
    deliver(daemon, s, 105)                             # queued, not yet flushed
    s._complete_turn()                                  # 110 completes, no flush
    assert s.held_ids == {"105"}
    daemon.push(105)                                    # handed again ...
    deliver(daemon, s, 106)                             # ... and seen before this one
    assert [p["message_id"] for p in s.queue] == ["105", "106"], "105 deduped"


def test_holding_lists_held_and_unreported_ids_on_register(daemon, make_shim):
    s = make_shim()
    deliver(daemon, s, 101)
    s.on_turn_boundary()                                # flushes 101
    deliver(daemon, s, 102)
    daemon.drop()
    wait_until(lambda: s.offline)
    s.on_turn_boundary()                                # 101 completes, frame cannot
    assert s.unreported_handled == ["101"]              # go; 102 is flushed
    s.connect()
    assert wait_until(lambda: len(daemon.registers) == 2)
    assert daemon.registers[1]["holding"] == ["101", "102"]
    s.on_turn_boundary()                                # 102 completes
    turn = turns(daemon, 2)[-1]
    assert turn["handled"] == ["101", "102"] and turn["_conn"] == 1
    assert s.unreported_handled == []


def test_an_unhandled_frame_that_cannot_go_keeps_the_ids_held_until_one_does(
        daemon, make_shim):
    s = make_shim()
    deliver(daemon, s, 101)
    s.on_turn_boundary()                                # flushes 101
    daemon.drop()
    wait_until(lambda: s.offline)
    s.on_turn_failed()
    assert s.held_ids == {"101"} and s.unreported_unhandled == ["101"]
    s.connect()
    assert wait_until(lambda: len(daemon.registers) == 2)
    assert daemon.registers[1]["holding"] == ["101"], "the new binding counts it handed"
    s.on_turn_boundary()
    turn = turns(daemon, 2)[-1]
    assert turn["unhandled"] == ["101"] and s.held_ids == set()


# ---------------------------------------------------------------- a turn's start
#
# turns reports how a turn ended, so a turn's start asks nothing and ends nothing: a
# digest settles at its turn's end, `unhandled` when the turn did not answer.


def test_the_shim_reads_no_transcript(daemon, make_shim, monkeypatch):
    from moxi_comms import cache

    def refused(*a, **k):
        raise AssertionError("the shim reads no transcript")

    # comms holds no transcript reader; its one way to one is the cache seam's read,
    # which runs in the daemon, never in the shim.
    s = make_shim()
    monkeypatch.setattr(cache, "clock_state", refused)
    monkeypatch.setattr(cache, "module", refused)
    deliver(daemon, s, 101)
    shim_drive.turn_ended(s)                            # flushes 101
    shim_drive.turn_started(s)
    shim_drive.turn_started(s)
    shim_drive.turn_ended(s)
    assert turns(daemon, 2)[1]["handled"] == ["101"]


def test_a_turn_start_with_a_digest_awaiting_ends_nothing(daemon, make_shim):
    s = make_shim()
    deliver(daemon, s, 101)
    shim_drive.turn_ended(s)                            # flushes 101
    shim_drive.turn_started(s)                          # 101's own turn
    shim_drive.turn_started(s)                          # a prompt typed into it
    assert s.awaiting_completion == "101" and len(turns(daemon, 1)) == 1
    shim_drive.turn_ended(s)
    frame = turns(daemon, 2)[1]
    assert frame["handled"] == ["101"] and frame["unhandled"] == []
    assert frame["_cursor"] == "101"


def test_a_turn_start_with_nothing_awaiting_sends_no_turn(daemon, make_shim):
    s = make_shim()
    shim_drive.turn_started(s)
    shim_drive.turn_started(s)
    assert wait_until(lambda: len(daemon.activity()) >= 2)
    assert daemon.activity("turn") == []


def test_an_unhandled_digest_keeps_the_clamp(daemon, make_shim):
    """105 completes, 110 is named unhandled. The cursor stays at 105 until 110 is
    handed again and completes."""
    s = make_shim()
    deliver(daemon, s, 105)
    shim_drive.turn_ended(s)                            # flushes 105
    deliver(daemon, s, 110)
    shim_drive.turn_ended(s)                            # 105 completes, flushes 110
    assert cursors.read(IDENT) == "105"
    shim_drive.turn_ended(s, failed=True)               # 110's turn did not answer
    frame = turns(daemon, 3)[2]
    assert frame["unhandled"] == ["110"] and frame["_cursor"] == "105"
    deliver(daemon, s, 110)
    shim_drive.turn_ended(s)                            # flushes 110 again
    shim_drive.turn_ended(s)
    assert turns(daemon, 5)[4]["handled"] == ["110"]
    assert cursors.read(IDENT) == "110"


class Gate:
    """A backoff generator the test steps one retry at a time.

    `_supervise` waits `next(delays)` between attempts, so an iterator that BLOCKS in
    `__next__` until the test says otherwise turns an unbounded retry loop into
    something a test can single-step. Paired with `gated()` below it also makes "the
    loop continues on the CURRENT backoff, re-seeded only on success" an assertion
    rather than a hope: a re-seed is a new `Gate` object.
    """

    def __init__(self):
        self.waits = 0
        self._go = threading.Event()

    def __iter__(self):
        return self

    def __next__(self) -> float:
        self.waits += 1
        self._go.wait(5.0)      # a stopped supervisor must not hang the test either
        self._go.clear()
        return 0.0

    def step(self) -> None:
        self._go.set()


def gated(monkeypatch) -> list:
    """Replace the backoff FACTORY. Returns the list of generators it has handed out."""
    seeds: list = []

    def factory(*args, **kwargs):
        seeds.append(Gate())
        return seeds[-1]

    monkeypatch.setattr(claude_shim, "backoff_delays", factory)
    return seeds


@pytest.fixture
def restart(rig):
    """Bring the daemon back on the same socket, as an operator restarting it does."""
    made: list = []

    def again():
        fresh = daemon_mod.Daemon(rig.gateway, clock=rig.clock,
                                  start_trim_thread=False).start()
        rig.daemon = fresh
        made.append(fresh)
        return fresh

    yield again
    for daemon in made:
        daemon.stop()


def test_a_name_refusal_at_the_initial_bind_waits_and_any_other_is_still_an_exit(rig):
    """At the seam tolerate passes through, `start()` itself. An unreachable daemon is swallowed. A daemon that named a holder of THIS
    name leaves the launch waiting for it, holding nothing it can send as; any other
    refusal still raises, and nothing retries it."""
    rig.shim(name="reviewer", scope="proj-a")
    loser = rig.shim(start=False,  name="reviewer", scope="proj-a",
                     autoreconnect=True, backoff=itertools.repeat(0.02))
    loser.start(tolerate_offline=True)
    assert loser.identity is None and loser.claims.state == "waiting"
    assert loser.statuses == [claude_shim.REFUSED_WAITING]
    assert loser._supervisor is not None, "it polls for the name to free"
    bad = rig.shim(start=False,  name="agent-9", scope="proj-a",
                   autoreconnect=True, backoff=itertools.repeat(0.02))
    with pytest.raises(DaemonError, match="reserved"):
        bad.start(tolerate_offline=True)
    assert bad.identity is None and bad._supervisor is None
    assert bad.statuses == []


def test_tolerate_without_autoreconnect_is_refused_at_start(rig):
    """Tolerate and the supervisor are ONE feature. Without the guard a mis-wired shim
    would neither bind, nor retry, nor exit -- strictly worse than the exit it replaces,
    and silent. The daemon is UP here: the refusal is about the wiring, not the daemon."""
    shim = rig.shim(start=False,  name="reviewer", scope="proj-a",
                    autoreconnect=False)
    with pytest.raises(ValueError, match="tolerate_offline requires autoreconnect"):
        shim.start(tolerate_offline=True)
    assert shim.identity is None, "it refused before it tried to bind"
    assert shim._supervisor is None
    assert shim.start().identity == "reviewer@proj-a", "and a plain start still binds"


def test_the_inject_file_seam_still_exits_offline(rig, tmp_path, capsys):
    """The hand-run seam keeps its honest exit. Nothing reads it as
    CONNECTION_CLOSED and no session is left comms-less by it, so tolerate never
    reaches this path. The supervisor IS wired here, exactly as `main()` wires it: what is absent is the flag."""
    rig.daemon.stop()
    args = claude_shim.build_parser().parse_args(
        ["--inject-file", str(tmp_path / "digests.jsonl"), "--name", "reviewer"])
    common = dict(name="reviewer", scope="proj-a", global_=False,
                  project="proj-a", autoreconnect=True, directors=())
    assert claude_shim._serve_inject_file(args, common) == claude_shim.EXIT_OFFLINE
    err = capsys.readouterr().err
    assert "comms offline" in err and "Traceback" not in err


def test_stop_during_a_backoff_wait_ends_the_supervisor_and_binds_nothing(rig, restart):
    rig.daemon.stop()
    shim = rig.shim(start=False,  name="reviewer", scope="proj-a",
                    autoreconnect=True, backoff=itertools.repeat(30.0))
    shim.start(tolerate_offline=True)
    assert wait_until(lambda: shim._supervisor.is_alive())
    started = time.monotonic()
    shim.stop()
    shim._supervisor.join(timeout=5.0)
    assert not shim._supervisor.is_alive()
    assert time.monotonic() - started < 2.0, "the wait is on the stop event, not a sleep"

    # A bind must not outlive `stop()`. This window is ordinary -- a session that
    # ends while the daemon is down has a supervisor running by construction -- so a
    # register that lands after the stop is closed rather than left holding a name that
    # nothing will release until the process exits.
    restart()
    late = rig.shim(start=False,  name="planner", scope="proj-a")
    late._stop.set()
    late.connect()
    assert late._client is None and late.identity is None and late.offline is True
    assert wait_until(lambda: "planner@proj-a" not in rig.daemon.bindings)


def test_a_stop_that_lands_mid_register_never_says_comms_online(rig, monkeypatch,
                                                                restart):
    """`connect()`'s stop-path releases the binding and returns having bound
    NOTHING -- and a bare `return` made that indistinguishable from a reconnect, so the
    supervisor announced "comms online" over a session shutting down unbound.

    Driven through `_supervise`, which is the only thing that could say the wrong
    thing, where a direct `connect()` call could not reach it.
    """
    seeds = gated(monkeypatch)
    rig.daemon.stop()
    shim = rig.shim(start=False,  name="reviewer", scope="proj-a",
                    autoreconnect=True, backoff=None)
    shim.start(tolerate_offline=True)
    assert wait_until(lambda: seeds and seeds[0].waits >= 1), "parked, unbound"

    restart()
    rig.gateway.max_message_id_hold = 0.4      # register stays in flight this long
    seeds[0].step()
    time.sleep(0.15)                           # the supervisor is inside `register`
    shim.stop()                                # ...and the session ends under it
    shim._supervisor.join(timeout=5.0)

    assert not shim._supervisor.is_alive(), "a stopped supervisor exits, it does not spin"
    assert claude_shim.ONLINE_MESSAGE not in shim.statuses, "it bound nothing"
    assert shim.statuses == [claude_shim.TOLERATED_OFFLINE_MESSAGE]
    assert shim._last_reported_state == "offline", "and the state machine agrees"
    assert shim.identity is None and shim.offline is True
    assert wait_until(lambda: "reviewer@proj-a" not in rig.daemon.bindings), \
        "the bind did not outlive the stop"


def test_a_connection_that_dies_during_register_is_not_a_binding(rig, monkeypatch):
    """`_on_close` runs on the reader thread. When the daemon answers `register` and
    the connection dies before this thread reaches `offline = False`, the close is
    processed while `offline` is still True -- so it emits nothing and never runs again.
    Marking online there stranded the supervisor for the life of the session: `offline`
    False over a dead client, and `_supervise` retries only while it is True.

    Forced, not waited for: the wrapper closes the client after `register` returns and
    blocks until `_on_close` has actually run, so the losing interleaving is the one
    under test rather than the one that happens to occur.
    """
    real = claude_shim.DaemonClient
    dropped: list = []
    closed = threading.Event()

    class DropsAfterRegister(real):
        def register(self, **kwargs):
            resp = super().register(**kwargs)
            if not dropped:
                dropped.append(True)
                self.close()
                closed.wait(5.0)
            return resp

    monkeypatch.setattr(claude_shim, "DaemonClient", DropsAfterRegister)
    shim = rig.shim(start=False,  name="reviewer", scope="proj-a",
                    autoreconnect=True, backoff=itertools.repeat(0.02))
    inner = shim._on_close
    shim._on_close = lambda: (inner(), closed.set())

    assert shim.connect() is False, "a client the reader thread already closed is no bind"
    assert shim.offline is True, "...so the supervisor still has something to retry"
    assert shim.identity is None and shim._client is None
    assert wait_until(lambda: "reviewer@proj-a" not in rig.daemon.bindings)

    shim.start(tolerate_offline=True)          # and nothing is stranded
    assert wait_until(lambda: shim.offline is False, timeout=5.0)
    assert shim._client is not None and shim._client.connected is True, \
        "online means a LIVE client, never one the reader thread already buried"
    assert shim.identity == "reviewer@proj-a"
    assert wait_until(lambda: "reviewer@proj-a" in rig.daemon.bindings)


def test_the_inject_file_seam_reports_a_timeout_as_offline_not_refused(
        rig, monkeypatch, tmp_path, capsys):
    """On the hand-run seam: `DaemonTimeout` is a `DaemonError`, so with `except
    DaemonError` first a slow-but-live daemon would print a refusal naming no holder on
    the one seam a human reads directly. The seam's `EXIT_OFFLINE` is what it reaches."""
    rig.gateway.max_message_id_hold = 0.3      # the mock's declared round trip
    real = claude_shim.DaemonClient
    monkeypatch.setattr(claude_shim, "DaemonClient",
                        lambda *a, **kw: real(*a, **{**kw, "timeout": 0.05}))
    args = claude_shim.build_parser().parse_args(
        ["--inject-file", str(tmp_path / "digests.jsonl"), "--name", "reviewer"])
    common = dict(name="reviewer", scope="proj-a", global_=False,
                  project="proj-a", autoreconnect=True, directors=())
    assert claude_shim._serve_inject_file(args, common) == claude_shim.EXIT_OFFLINE
    err = capsys.readouterr().err
    assert claude_shim.REFUSED_PREFIX not in err, "nothing refused anything"
    assert "timeout waiting for register" in err and "Traceback" not in err


def test_a_round_trip_that_never_landed_leaves_no_replay_flag_armed(rig):
    """`connect()` arms `replaying` before the round trip. If nothing reset it when the
    round trip failed, a tolerated launch would leave it True for the life of the
    session -- inert on the claude path, and a trap for a test author, since the
    module's own comment calls it test visibility."""
    rig.daemon.stop()
    plain = rig.shim(start=False,  name="planner", scope="proj-a")
    with pytest.raises(DaemonOffline):
        plain.connect()
    assert plain.replaying is False, "nothing landed, so nothing is replaying"

    shim = rig.shim(start=False,  name="reviewer", scope="proj-a",
                    autoreconnect=True, backoff=itertools.repeat(30.0))
    shim.start(tolerate_offline=True)
    assert wait_until(lambda: shim.replaying is False)
    assert shim.offline is True and shim.identity is None


def test_after_restart_the_first_session_to_register_keeps_the_name(
        rig, monkeypatch, restart):
    seeds = gated(monkeypatch)
    earlier = rig.shim(name="reviewer", scope="proj-a", autoreconnect=True,
                       backoff=None)
    rig.daemon.stop()
    assert wait_until(lambda: seeds and seeds[0].waits >= 1)

    restart()
    successor = rig.shim(name="reviewer", scope="proj-a")
    assert successor.identity == IDENT
    seeds[0].step()

    assert wait_until(lambda: earlier.claims.state == "standby")
    assert earlier.claims.lost == IDENT and earlier.claims.claim is None
    assert wait_until(lambda: not earlier._supervisor.is_alive())
    assert earlier.ended_because.startswith("name held by another session")


def test_the_supervisor_survives_an_unexpected_reconnect_exception(
        rig, monkeypatch, restart):
    seeds = gated(monkeypatch)
    shim = rig.shim(name="reviewer", scope="proj-a", autoreconnect=True,
                    backoff=None)
    rig.daemon.stop()
    assert wait_until(lambda: seeds and seeds[0].waits >= 1)
    restart()
    real = shim._connect_once
    calls = []

    def once():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return real()

    monkeypatch.setattr(shim, "_connect_once", once)
    seeds[0].step()
    expected = claude_shim.SUPERVISOR_ERROR.format(error="RuntimeError: boom")
    assert wait_until(lambda: expected in shim.statuses)
    assert shim._supervisor.is_alive()
    assert wait_until(lambda: seeds[0].waits >= 2)
    seeds[0].step()
    assert wait_until(lambda: shim.identity == IDENT and not shim.offline)


def test_a_finite_reconnect_backoff_falls_back_to_the_default(rig, restart):
    shim = rig.shim(name="reviewer", scope="proj-a", autoreconnect=True,
                    backoff=iter([0.01]))
    rig.daemon.stop()
    restart()
    assert wait_until(lambda: not shim.offline, timeout=5.0)
    assert shim._supervisor.is_alive()


def test_the_supervisor_records_running_absent_and_stopped(rig):
    plain = rig.shim(name="reviewer", scope="proj-a")
    assert plain._supervisor is None and plain.ended_because is None
    supervised = rig.shim(name="curator", scope="proj-a", autoreconnect=True,
                          backoff=itertools.repeat(0.02))
    assert supervised._supervisor.is_alive() and supervised.ended_because is None
    supervised.stop()
    assert wait_until(lambda: not supervised._supervisor.is_alive())
    assert supervised.ended_because == "stopped"


def test_a_transient_failure_on_the_first_bind_is_still_an_exit(
        rig, monkeypatch):
    monkeypatch.setattr(rig.daemon, "_high_water_mark",
                        lambda: ("0", RuntimeError("429 Too Many Requests")))
    shim = rig.shim(start=False, name="reviewer", scope="proj-a",
                    autoreconnect=True, backoff=itertools.repeat(0.02))
    with pytest.raises(DaemonError, match="429 Too Many Requests"):
        shim.start(tolerate_offline=True)
    assert shim.identity is None and shim._supervisor is None


def test_a_send_waits_for_the_new_token_after_a_restart(rig, monkeypatch, restart):
    from moxi_comms.claimhold import CLAIM_SOCKET_ENV
    from moxi_comms.membership import TREE, root_of

    seeds = gated(monkeypatch)
    planner = rig.shim(name="planner", scope="proj-a", autoreconnect=True,
                       backoff=None, listen_claims=True,
                       claim_roots=[root_of(os.getpid(), TREE)])
    path = planner.claims.socket_path
    rig.daemon.stop()
    assert wait_until(lambda: seeds and seeds[0].waits >= 1)
    restart()

    code, out = rig.cli(["send", "--posture", "none", "--new-name", "nobody", "before"],
        env={CLAIM_SOCKET_ENV: str(path)})
    assert code == 4 and "holds no posting credential" in out
    assert rig.gateway.history(rig.names_channel) == []

    seeds[0].step()
    assert wait_until(lambda: not planner.offline)
    code, out = rig.cli(["send", "--posture", "none", "--new-name", "nobody", "after"],
        env={CLAIM_SOCKET_ENV: str(path)})
    assert code == 0, out
    posted = rig.gateway.history(rig.names_channel)[-1]
    assert (posted.author_kind, posted.author_id) == ("identity", "planner@proj-a")
