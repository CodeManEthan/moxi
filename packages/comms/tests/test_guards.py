"""The guards: the `block` setting, 429-then-loud, the rescan."""

import itertools
import shutil
import socket
import tempfile
from pathlib import Path

import pytest
from conftest import (cursor_of, put_cursor, queued, raw_connection, send_turn, settle,
                      wait_for_queue, wait_until, write_config)

from moxi_comms import constants, daemon as daemon_mod
from moxi_comms.daemon import Daemon
from moxi_comms.record import RateLimited
from moxi_comms.mock_record import MockRecord
from moxi_comms.paths import cursors_dir, daemon_socket
from moxi_comms.sender import SendFailed, _bounded


# --------------------------------------------------------------------------- block

def test_block_is_symmetric_at_the_routing_point(rig):
    a = rig.shim(name="planner", scope="proj-a")
    b = rig.shim(name="reviewer", scope="proj-a")
    write_config(None, "block = true\n")

    rig.post_as("agent-6", ["reviewer@proj-a"], "blocked inbound")
    rig.cli(["send", "--posture", "none", "reviewer", "blocked outbound"],  name="planner", scope="proj-a")
    rig.post_as("agent-6", ["planner@proj-a"], "also blocked")
    wait_until(lambda: False, timeout=0.3)          # give routing a chance to misbehave
    assert a.queue == [] and b.queue == []

    write_config(None, "block = false\n")   # read at use time: instant, no restart
    rig.post_as("agent-6", ["reviewer@proj-a"], "now flowing")
    wait_for_queue(b, 1)
    assert b.queue[0]["text"] == "now flowing"


def test_send_verb_block_check_is_advisory_and_says_so(rig):
    rig.shim(name="reviewer", scope="proj-a")
    write_config(None, "block = true\n")
    code, out = rig.cli(["send", "--posture", "none", "reviewer", "hi"],  name="planner", scope="proj-a")
    assert code == 3
    assert "Advisory" in out and "enforcement is at daemon routing" in out
    assert rig.record.history(rig.names_channel) == []


# --------------------------------------------------------------------------- 429

def test_429_is_retried_honouring_retry_after(rig):
    refusals = [0.5, 0.5]

    def send():
        if refusals:
            raise RateLimited(refusals.pop(0))
        return rig.post_as("planner", ["nobody"], "hi")

    slept = []
    msg = _bounded(send, as_="planner", attempts=constants.RETRY_ATTEMPTS,
                   sleep=slept.append)
    assert msg is not None
    assert slept == [0.5, 0.5], "Retry-After is honoured, not guessed"


def test_429_exhaustion_is_loud_and_the_message_is_not_in_the_record(rig):
    def send():
        raise RateLimited(0.0)

    with pytest.raises(SendFailed, match="SEND FAILED"):
        _bounded(send, as_="planner", attempts=constants.RETRY_ATTEMPTS,
                 sleep=lambda _seconds: None)
    assert rig.record.history(rig.names_channel) == []


def test_the_verb_reports_the_loud_failure(rig, monkeypatch):
    rig.shim(name="reviewer", scope="proj-a")

    def fail(*_args, **_kwargs):
        raise SendFailed("SEND FAILED: the message was NOT posted")

    monkeypatch.setattr(rig.record, "post_as_identity", fail)
    code, out = rig.cli(["send", "--posture", "none", "reviewer@proj-a", "hi"],  name="planner", scope="proj-a")
    assert code == 75, "nothing was posted, so it may be tried again"
    assert "SEND FAILED" in out and "was NOT posted" in out
    assert rig.record.history(rig.names_channel) == []


# --------------------------------------------------------------------------- rescan

def test_record_reconnect_without_resume_triggers_a_rescan(rig):
    b = rig.shim(name="reviewer", scope="proj-a")
    before = rig.daemon.rescans
    rig.record.disconnect()
    rig.post_as("agent-6", ["reviewer@proj-a"], "posted during the gap")
    wait_until(lambda: False, timeout=0.2)
    assert b.queue == [], "the event never reached the socket"

    rig.record.reconnect(resume=False)             # fresh IDENTIFY: the gap is gone
    assert wait_until(lambda: rig.daemon.rescans > before)
    wait_for_queue(b, 1)
    assert b.queue[0]["text"] == "posted during the gap"


def test_resume_replays_the_gap_without_a_rescan(rig):
    b = rig.shim(name="reviewer", scope="proj-a")
    before = rig.daemon.rescans
    rig.record.disconnect()
    rig.post_as("agent-6", ["reviewer@proj-a"], "gap message")
    rig.record.reconnect(resume=True)
    wait_for_queue(b, 1)
    assert b.queue[0]["text"] == "gap message"
    assert rig.daemon.rescans == before


def test_boot_rescan_rebuilds_the_pending_view(rig):
    rig.post_as("agent-6", ["curator@proj-a"], "waiting")
    wait_until(lambda: queued(rig.daemon, "curator@proj-a"))
    fresh = daemon_mod.Daemon(rig.record, clock=rig.clock, start_trim_thread=False)
    rig.daemon.stop()
    fresh.start()
    try:
        assert len(queued(fresh, "curator@proj-a")) == 1
    finally:
        fresh.stop()
    rig.daemon = fresh


def test_rescan_does_not_re_pend_what_an_unbound_name_already_handled(rig):
    """The contract is "rescan #names PAST CURSORS". A chosen name that handled a message
    and then unbound gets no row for it: no reader writes a row for an id at or below
    the name's cursor. The message is posted while the record is down, so only
    the rescan reads it."""
    rig.record.disconnect()
    msg = rig.post_as("agent-6", ["curator@proj-a"], "handled before unbinding")
    put_cursor("curator@proj-a", msg.id)

    rig.daemon.rescan()
    assert queued(rig.daemon, "curator@proj-a") == set()


def test_rescan_still_pends_for_a_name_that_never_bound(rig):
    """No cursor at all means nothing was ever handled: the message stays pending."""
    rig.post_as("agent-6", ["curator@proj-a"], "nobody has ever held this name")
    assert cursor_of("curator@proj-a") is None

    rig.daemon.rescan()
    assert len(queued(rig.daemon, "curator@proj-a")) == 1


def test_rescan_still_pends_for_a_message_past_the_cursor(rig):
    """A cursor BEHIND the message proves only the earlier mail was handled. Both are
    posted while the record is down, so only the rescan reads them."""
    rig.record.disconnect()
    first = rig.post_as("agent-6", ["curator@proj-a"], "handled")
    second = rig.post_as("agent-6", ["curator@proj-a"], "arrived after the cursor")
    put_cursor("curator@proj-a", first.id)

    rig.daemon.rescan()
    assert queued(rig.daemon, "curator@proj-a") == {second.id}


def test_a_cursor_folder_an_earlier_build_left_beside_the_store_is_not_read(rig):
    """A build that still wrote cursor files left `cursors/` beside a store it made. The
    folder isn't comms 0.2's (no move fingerprinted it), so the gate leaves it where it
    is, the daemon boots past it, and nothing in it moves a cursor."""
    rig.post_as("agent-6", ["curator@proj-a"], "waiting beside a leftover cursor file")
    wait_until(lambda: queued(rig.daemon, "curator@proj-a"))
    high = max(int(m.id) for m in rig.record.history(rig.names_channel))
    cursors_dir().mkdir(parents=True, exist_ok=True)
    left = cursors_dir() / "curator@proj-a.json"
    left.write_text('{"cursor": "%d"}' % high)

    fresh = daemon_mod.Daemon(rig.record, clock=rig.clock, start_trim_thread=False)
    rig.daemon.stop()
    fresh.start()
    try:
        assert len(queued(fresh, "curator@proj-a")) == 1, "the file proves nothing"
        assert cursor_of("curator@proj-a") is None
        assert left.exists(), "left where it is, not renamed"
    finally:
        fresh.stop()
    rig.daemon = fresh


def test_bind_deliver_unbind_rescan_leaves_the_pending_view_empty(rig):
    """The drain shape end to end: a one-shot drain binding takes the mail, completes the
    turn (cursor advances), unbinds -- and a reconnect rescan must not resurrect it."""
    rig.post_as("agent-6", ["curator@proj-a"], "drain me")
    curator = rig.shim(name="curator", scope="proj-a")
    wait_for_queue(curator, 1)
    wait_until(lambda: curator.replaying is False)
    delivered = curator.queue[0]["message_id"]
    curator.on_turn_boundary()
    curator.on_turn_boundary()
    assert cursor_of("curator@proj-a") == delivered

    curator.stop()
    wait_until(lambda: "curator@proj-a" not in rig.daemon.bindings)

    before = rig.daemon.rescans
    rig.record.reconnect(resume=False)
    assert wait_until(lambda: rig.daemon.rescans > before)
    wait_until(lambda: False, timeout=0.2)          # let a bad row show up
    assert queued(rig.daemon, "curator@proj-a") == set()


# --------------------------------------------------------------------------- offline

def test_shim_surfaces_comms_offline_and_reconnects(rig):
    from moxi_comms.adapters.claude_shim import OFFLINE_MESSAGE
    b = rig.shim(name="reviewer", scope="proj-a", autoreconnect=True,
                 backoff=itertools.repeat(0.02))
    rig.daemon.stop()
    assert wait_until(lambda: b.offline)
    assert any(OFFLINE_MESSAGE in s for s in b.statuses)

    fresh = daemon_mod.Daemon(rig.record, clock=rig.clock, start_trim_thread=False).start()
    rig.daemon = fresh
    assert wait_until(lambda: b.offline is False, timeout=5.0)
    assert wait_until(lambda: any("comms online" in s for s in b.statuses))
    rig.post_as("agent-6", ["reviewer@proj-a"], "after the outage")
    assert wait_until(lambda: any(p["text"] == "after the outage" for p in b.queue))


def test_backoff_shape_is_bounded_and_jittered():
    from moxi_comms.client import backoff_delays
    delays = list(itertools.islice(backoff_delays(), 12))
    assert delays[0] <= constants.BACKOFF_START * (1 + constants.BACKOFF_JITTER)
    assert all(d <= constants.BACKOFF_MAX * (1 + constants.BACKOFF_JITTER) for d in delays)
    assert delays[-1] > delays[0]


# --------------------------------------------------------------------------- posture

def test_socket_posture_is_0600_in_a_0700_dir(rig):
    import os
    from moxi_comms.paths import daemon_socket
    sock = daemon_socket()
    assert oct(os.stat(sock).st_mode & 0o777) == "0o600"
    assert oct(os.stat(sock.parent).st_mode & 0o777) == "0o700"


# ------------------------------------------------------------------ the unlink gate

@pytest.fixture
def shortdir():
    """A SHORT directory for socket paths: `sun_path` is 108 bytes and pytest's
    `tmp_path` spends most of them on the test's own name."""
    d = Path(tempfile.mkdtemp(prefix="cmu"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def stale_socket(path: Path) -> Path:
    """A socket file with nothing behind it -- bound, never listened, then dropped.
    Connecting to it gives ECONNREFUSED, which is the allowlist's second entry."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(path))
    s.close()
    assert path.exists()
    return path


def says_ping(path: Path) -> bool:
    """Does something at `path` answer comms' wire? A hello and a round trip, not a
    knock."""
    with raw_connection(path) as conn:
        settle(conn)
    return True


def test_stop_without_a_start_removes_nothing(shortdir):
    """The old daemon's exit deleted whatever file was at the path. A daemon that
    never bound has nothing of its own there, so it takes nothing away."""
    path = shortdir / "d.sock"
    path.write_text("someone else's")
    d = Daemon(MockRecord(), socket_path=path, start_trim_thread=False)
    assert d.host is None
    d.stop()
    assert path.exists(), "a daemon removes only the socket it bound"


def test_a_start_against_a_live_daemon_refuses_and_leaves_it_serving(rig):
    """The incident, run forwards: a second daemon on the live one's path. It must
    refuse, and neither the refusal nor its `stop()` may touch what is already there."""
    path = daemon_socket()
    inode = path.stat().st_ino
    intruder = Daemon(rig.record, clock=rig.clock, start_trim_thread=False)

    with pytest.raises(RuntimeError, match="live comms daemon"):
        intruder.start()
    assert path.stat().st_ino == inode, "the live socket file is untouched"

    intruder.stop()
    assert path.stat().st_ino == inode, "and the refused daemon's stop takes nothing"
    assert says_ping(path), "the live daemon is still serving through its own name"
    assert rig.shim(name="planner", scope="proj-a").identity == "planner@proj-a"


def test_a_stale_socket_file_is_cleared_and_the_daemon_serves(rig, shortdir):
    """Unattended recovery still works: the file a dead daemon left behind answers
    ECONNREFUSED, which is the allowlist saying `nothing is here`."""
    path = stale_socket(shortdir / "d.sock")
    d = Daemon(rig.record, clock=rig.clock, socket_path=path, start_trim_thread=False,
               history_path=shortdir / "j.db").start()
    try:
        assert d.host is not None
        assert says_ping(path)
    finally:
        d.stop()
    assert not path.exists()


def test_a_clean_stop_removes_the_socket_and_a_second_stop_does_not(rig, shortdir):
    """The regression, and the flag's reset: once the file is gone the daemon no
    longer claims it, so a second `stop()` cannot take a successor's socket with it."""
    path = shortdir / "d.sock"
    d = Daemon(rig.record, clock=rig.clock, socket_path=path, start_trim_thread=False,
               history_path=shortdir / "j.db").start()
    assert path.exists()
    d.stop()
    assert not path.exists() and d.host is None

    path.write_text("a successor's")
    d.stop()
    assert path.exists(), "the second stop removes nothing"


def test_a_refused_start_has_no_side_effects_at_all(rig, shortdir):
    """Refusing is not half-starting: no server socket, no record subscription,
    and no binding history -- `BindingHistory.open()` would have created the file and its parent."""
    history = shortdir / "fresh" / "bindings.db"
    subscribers = len(rig.record._subs)
    intruder = Daemon(rig.record, clock=rig.clock, start_trim_thread=False,
                      history_path=history)

    with pytest.raises(RuntimeError):
        intruder.start()

    assert intruder.host is None
    assert len(rig.record._subs) == subscribers, "it never subscribed"
    assert not history.exists() and not history.parent.exists()


# --------------------------------------------------------------------------- handing is
# not handling

def test_mail_handed_to_a_session_that_unbinds_before_its_cursor_moves_is_pending_again(rig):
    """Handing a message to a binding is not handling it. The cursor is the one durable
    "handled?" fact, and a session that unbinds before it moves leaves the message
    waiting in #names exactly as before -- a re-bind's replay would deliver it again --
    so the pending view must say so without waiting for a rescan."""
    b = rig.shim(name="reviewer", scope="proj-a")
    msg = rig.post_as("agent-6", ["reviewer@proj-a"], "handed, never handled")
    assert wait_until(lambda: msg.id in rig.daemon.bindings["reviewer@proj-a"].delivered)
    assert queued(rig.daemon, "reviewer@proj-a") == set(), \
        "while bound and in the adapter's hands it is not waiting for a bind"

    b.stop()                                          # no turn boundary: cursor unmoved
    assert wait_until(lambda: "reviewer@proj-a" not in rig.daemon.bindings)
    assert cursor_of("reviewer@proj-a") is None
    assert queued(rig.daemon, "reviewer@proj-a") == {msg.id}

    code, out = rig.cli(["agents", "--seats"])
    assert code == 0
    assert "waiting (" in out and "reviewer@proj-a" in out and "1 message(s)" in out, \
        "and comms agents --seats says so"

    # ...and a re-bind's release hands it, which is what the view promised; the row
    # goes when the turn holding it completes, not at the hand-off
    again = rig.shim(name="reviewer", scope="proj-a")
    assert wait_until(lambda: any(q["message_id"] == msg.id for q in again.queue))
    assert wait_until(lambda: again.replaying is False)
    again.on_turn_boundary()                          # inject
    again.on_turn_boundary()                          # complete: `handled` names it
    assert wait_until(lambda: queued(rig.daemon, "reviewer@proj-a") == set()), \
        "handed again and handled, so not waiting again"


def test_mail_a_session_handled_before_unbinding_stays_handled(rig):
    """The other half: its turn acked it, so it is not pending after the unbind."""
    b = rig.shim(name="reviewer", scope="proj-a")
    msg = rig.post_as("agent-6", ["reviewer@proj-a"], "handled")
    wait_for_queue(b, 1)
    b.on_turn_boundary()                               # inject
    b.on_turn_boundary()                               # complete: the ack
    assert cursor_of("reviewer@proj-a") == msg.id

    b.stop()
    assert wait_until(lambda: "reviewer@proj-a" not in rig.daemon.bindings)
    assert queued(rig.daemon, "reviewer@proj-a") == set()


def test_a_partial_ack_re_pends_only_what_it_did_not_handle(rig):
    b = rig.shim(name="reviewer", scope="proj-a")
    first = rig.post_as("agent-6", ["reviewer@proj-a"], "handled")
    second = rig.post_as("agent-6", ["reviewer@proj-a"], "handed only")
    assert wait_until(lambda: second.id in rig.daemon.bindings["reviewer@proj-a"].delivered)
    send_turn(b, handled=[first.id])

    b.stop()
    assert wait_until(lambda: "reviewer@proj-a" not in rig.daemon.bindings)
    assert queued(rig.daemon, "reviewer@proj-a") == {second.id}


# ------------------------------------------------------- no cursor files are written

def test_a_full_delivery_writes_nothing_under_the_cursor_folder(rig):
    """Delivery, ack, rescan, rebind and restart: the daemon keeps the cursor in its
    books, and nothing makes `STATE/comms/cursors/`."""
    curator = rig.shim(name="curator", scope="proj-a", autoreconnect=True,
                       backoff=itertools.repeat(0.1))
    msg = rig.post_as("agent-6", ["curator@proj-a"], "delivered and acked")
    wait_for_queue(curator, 1)
    curator.on_turn_boundary()
    curator.on_turn_boundary()
    assert wait_until(lambda: cursor_of("curator@proj-a") == msg.id)
    rig.daemon.rescan()
    rig.daemon.stop()
    rig.daemon = Daemon(rig.record, clock=rig.clock, start_trim_thread=False).start()
    assert wait_until(lambda: "curator@proj-a" in rig.daemon.bindings and not curator.offline)
    assert not cursors_dir().exists()


def test_no_comms_source_names_the_cursor_folder_but_the_move():
    """Only the gate and the move of comms 0.2's files name the cursor folder, which
    they look for, read and rename. No adapter or daemon code writes there."""
    import re

    import moxi_comms
    src = Path(moxi_comms.__file__).parent
    naming = set()
    for f in src.rglob("*.py"):
        text = f.read_text()
        if "cursors_dir" in text or re.search(r"""/\s*["']cursors["']""", text):
            naming.add(f.relative_to(src).as_posix())
    assert naming == {"paths.py", "store/open.py", "store/upgrade_0_2.py"}
