"""The shared adapter base on its own, with a port that does nothing but record.

`test_adapter_seam.py` pins what the codex port does where it meets the base. These pin what the base does for every port, whichever CLI is behind it.
"""

from __future__ import annotations

import itertools
from types import SimpleNamespace

import pytest

from moxi_comms.adapters import harness
from moxi_comms.adapters.harness import TURN_COMPLETED, HarnessAdapter, TurnLedger

IDENTITY = "planner@proj-a"


def payload(mid):
    return {"message_id": str(mid), "channel_id": "c1", "room": "#names",
            "author": {"name": "captain", "tier": "chosen", "attribution": "verified"},
            "recipients": [IDENTITY], "posture": "open",
            "backlog": True, "text": f"body {mid}", "reply_to": None, "audience": None,
            "audience_source": None,
            "timestamp": "2026-09-30T12:00:00Z"}


class RecordingPort(HarnessAdapter):
    KIND = "test"

    def __init__(self):
        super().__init__(SimpleNamespace(directors=()), autoreconnect=False)
        self.delivered_batches: list = []
        #: The daemon's side of `open_digest`: each open's ids, and the ids it drops.
        self.opens: list = []
        self.drops: set = set()
        self._digest_numbers = itertools.count(100)

    def open_digest(self, ids):
        ids = [str(i) for i in ids]
        self.opens.append(ids)
        return {"digest": next(self._digest_numbers),
                "ids": [i for i in ids if i not in self.drops],
                "dropped": [{"id": i, "reason": "withdrawn"} for i in ids
                            if i in self.drops]}

    def deliver(self, text, high, identity, count):
        self.delivered_batches.append((high, count))
        return "receipt"


def test_nothing_is_delivered_while_catch_up_is_still_arriving():
    """The catch-up hold: a registration arms it, and until the daemon says the replay
    is complete, a flush delivers nothing and leaves every queued message in place. The
    replay's end releases it, and the whole backlog then goes as one digest."""
    port = RecordingPort()
    port.identity = IDENTITY
    port.replaying = True
    port._on_push("deliver", {"payload": payload(7)})
    port._on_push("deliver", {"payload": payload(5)})

    assert port.flush() is None
    assert port.delivered_batches == []
    assert [p["message_id"] for p in port.queue] == ["7", "5"]

    port._on_push("replay_complete", {"identity": IDENTITY, "cursor": "4"})
    assert port.replaying is False
    assert port.flush() is not None
    assert port.delivered_batches == [(7, 2)]
    assert port.queue == []


def test_the_turn_ledger_lives_in_the_shared_base():
    """The pending-injection ledger stays in comms for each adapter's owed ids."""
    ledger = harness.TurnLedger()
    ledger.injected("prompt-1", "12")
    assert ledger.completed({"id": "prompt-1", "status": harness.TURN_COMPLETED}) == "12"
    ledger.injected("prompt-2", "13")
    assert ledger.completed({"id": "prompt-2", "status": "error"}) is None
    assert not ledger.tracks("prompt-2")

# ------------------------------------------------------------------ the held ids

class TurnPort(RecordingPort):
    """A port that reports each digest's turn end by id, as codex and opencode do."""
    HOLDS_UNTIL_TURN_ENDS = True

    def __init__(self):
        super().__init__()
        self.ledger = TurnLedger()
        self._turn_numbers = itertools.count(1)

    def deliver(self, text, high, identity, count):
        turn = f"turn-{next(self._turn_numbers)}"
        self.ledger.injected(turn, high, self.digest_ids(high))
        self.delivered_batches.append((high, count))
        return turn

    def end_turn(self, turn, status=TURN_COMPLETED):
        through, ids = self.ledger.end({"id": turn, "status": status})
        self.release_held(ids)
        return through


def push(port, *mids):
    for mid in mids:
        port._on_push("deliver", {"payload": payload(mid)})


def queued(port):
    return [p["message_id"] for p in port.queue]


def held(port):
    return sorted(port.held_ids, key=int)


def online_port(cls=TurnPort):
    port = cls()
    port.identity = IDENTITY
    port._on_push("replay_complete", {"identity": IDENTITY, "cursor": None})
    return port


def test_a_pushed_id_already_held_is_dropped():
    """Dedupe on push: an id the adapter already has queued, or injected into a turn
    that has not ended, is not queued again when a reconnect's replay hands it in."""
    port = online_port()
    push(port, 5, 5)
    assert queued(port) == ["5"]
    assert port.flush() is not None
    push(port, 5)
    assert port.queue == [] and port.flush() is None
    assert port.delivered_batches == [(5, 1)]


def test_a_register_starts_the_binding_with_nothing_held(monkeypatch):
    """A new binding: the last one's rows went back to waiting with its end and the
    daemon hands them again, so the register sends no `holding` and the queue, the held
    ids and the digests of the last binding go before the bind's round trip."""
    registered: list = []

    class Refused(Exception):
        pass

    class Client:
        def __init__(self, socket_path, on_push=None, on_close=None):
            pass

        def connect(self):
            pass

        def bind(self, **fields):
            registered.append(fields)
            raise Refused

        def close(self):
            pass

    monkeypatch.setattr(harness, "DaemonClient", Client)
    port = online_port()
    port.agent = SimpleNamespace(directors=(), name="planner", scope="proj-a")
    push(port, 12, 9)
    port.flush()                          # 9 and 12 in turn-1, which has not ended
    push(port, 15)                        # queued
    with pytest.raises(Refused):
        port.connect_daemon()
    assert "holding" not in registered[-1]
    assert (port.queue, port.held_ids, port.digest_of) == ([], set(), {})


def test_the_flush_renders_only_the_ids_the_digest_keeps():
    """The flush opens the digest before it renders: an id the daemon drops (withdrawn
    or expired) is not rendered and stops being held; the others map to the digest."""
    port = online_port()
    port.drops = {"6"}
    push(port, 5, 6)
    text = port.flush()
    assert port.opens == [["5", "6"]]
    assert "body 5" in text and "body 6" not in text
    assert port.delivered_batches == [(5, 1)]
    assert held(port) == ["5"] and port.digest_of == {"5": 100}

    port.drops = {"7"}
    push(port, 7)
    assert port.flush() is None and port.delivered_batches == [(5, 1)]
    assert held(port) == ["5"]


def test_a_digests_turn_end_releases_exactly_its_ids():
    """The held set loses the ids of the digest whose turn ended, completed or not, and
    nothing else: a lower id still queued when a higher digest's turn completes stays
    held, so a re-push of it after a re-register is dropped, never injected twice."""
    port = online_port()
    push(port, 7)
    port.flush()                          # 7 in turn-1
    push(port, 3)                         # a lower id, queued out of order
    assert held(port) == ["3", "7"]
    assert port.end_turn("turn-1") == "7"
    assert held(port) == ["3"], "not pruned by comparing ids with the cursor"
    push(port, 3)
    assert queued(port) == ["3"]

    port.flush()                          # 3 in turn-2
    assert port.end_turn("turn-2", status="interrupted") is None
    assert held(port) == [], "an interrupted turn has ended too"


def test_the_ledger_keeps_each_turns_ids():
    ledger = TurnLedger()
    ledger.injected("turn-1", "7", ("5", "7"))
    ledger.injected("turn-1", "9", ("8", "9"))     # a second digest steered in
    ledger.injected("turn-2", "4", ("4",))
    assert ledger.ids("turn-1") == ("5", "7", "8", "9")
    assert ledger.pending("turn-1") == "9"
    assert ledger.end({"id": "turn-1", "status": "failed"}) == (None, ("5", "7", "8", "9"))
    assert ledger.end({"id": "turn-2", "status": TURN_COMPLETED}) == ("4", ("4",))
    assert ledger.end({"id": "operators", "status": TURN_COMPLETED}) == (None, ())
    assert ledger.turns == ()


def test_reregister_drops_the_queue(monkeypatch):
    """`reregister` drops the queue, whose rows wait again and are handed to the new
    binding; the register itself lets go of the rest (see the test above)."""
    port = online_port()
    push(port, 5)
    port.flush()                          # 5 in turn-1, still running
    push(port, 8)                         # queued
    seen: list = []

    def connect():
        seen.append((held(port), queued(port)))

    port.connect_daemon = connect
    port.reregister()
    assert seen == [(["5"], [])]
    push(port, 8)                         # the new binding's catch-up
    assert queued(port) == ["8"]


def test_a_dropped_batch_and_a_returned_delivery_hold_nothing():
    """A batch the flush drops replays at the next registration, so its ids stop being
    held; a port that reports no turn end has ended the digest's turn when `deliver`
    returns."""
    class Failing(RecordingPort):
        def deliver(self, text, high, identity, count):
            raise RuntimeError("the harness went away")

    failing = online_port(Failing)
    push(failing, 4)
    with pytest.raises(RuntimeError):
        failing.flush()
    assert failing.held_ids == set()

    plain = online_port(RecordingPort)
    push(plain, 6)
    plain.flush()
    assert plain.held_ids == set()


# ------------------------------------------------------------------ the turn end

class FakeDaemon:
    """The binding's connection, as far as the turn end and `notify_activity` need it."""

    def __init__(self, connected=True):
        self.connected = connected
        self.frames: list = []

    def notify(self, method, params=None):
        """Kept as an `activity` frame, a kind sent as its own method named as `kind`."""
        from moxi_comms.adapters.claude_shim import ACTIVITY_METHODS
        kinds = {m: k for k, m in ACTIVITY_METHODS.items()}
        frame = dict(params or {}, op=method)
        if method in kinds:
            frame.update(op="activity", kind=kinds[method])
        self.frames.append(frame)

    def call(self, method, params=None, **kw):
        self.frames.append(dict(params or {}, op=method))
        return {"cursor": None}


def bound_port(connected=True):
    port = online_port()
    port.offline = False
    port._daemon = FakeDaemon(connected)
    return port


def test_a_turn_end_names_the_digests_that_held_its_ids():
    """`report_turn_end` takes message ids from the port and sends `turn_ended` with
    the digests that hold them; the ids stop being held and leave the digest map."""
    port = bound_port()
    push(port, 7)
    port.flush()                                   # 7 in turn-1, digest 100
    push(port, 3)
    port.flush()                                   # 3 in turn-2, digest 101
    _, ids = port.ledger.end({"id": "turn-1", "status": TURN_COMPLETED})
    port.report_turn_end(handled=ids)
    frame = port._daemon.frames[-1]
    assert (frame["op"], frame["handled"], frame["unhandled"]) == ("turn_ended", [100], [])
    assert held(port) == ["3"] and port.digest_of == {"3": 101}

    _, ids = port.ledger.end({"id": "turn-2", "status": "interrupted"})
    port.report_turn_end(unhandled=ids)
    frame = port._daemon.frames[-1]
    assert (frame["handled"], frame["unhandled"]) == ([], [101])
    assert held(port) == [] and port.digest_of == {}


def test_a_turn_end_that_cannot_be_sent_is_dropped():
    """With no connection the turn end goes nowhere and nothing is owed: the binding's
    end puts its rows back to waiting, and the next binding is handed them again."""
    port = bound_port(connected=False)
    push(port, 5)
    port.flush()                                   # 5 in turn-1
    _, ids = port.ledger.end({"id": "turn-1", "status": "interrupted"})
    port.report_turn_end(unhandled=ids)
    assert port._daemon.frames == []
    assert port.unhandled_owed == [] and held(port) == []


def test_owe_unhandled_names_each_digest_once():
    port = bound_port()
    push(port, 4)
    port.flush()                                   # digest 100
    push(port, 6)
    port.flush()                                   # digest 101
    port.owe_unhandled(["4", "4", 6])
    port.owe_unhandled(["6"])
    port.report_turn_end(unhandled=["4"])
    assert port._daemon.frames[-1]["unhandled"] == [100, 101]


def test_a_ready_that_cannot_be_sent_is_owed_to_the_next_register():
    """A `ready` whose moment comes while the adapter is not bound (or its
    frame cannot go out) is sent once, right after the next register's answer."""
    port = bound_port(connected=False)
    port.report_ready("compact")
    assert port._owed_ready == "compact" and port._daemon.frames == []
    unbound = online_port()
    unbound.identity = None
    unbound.report_ready("startup")
    assert unbound._owed_ready == "startup"
