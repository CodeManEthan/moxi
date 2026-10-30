"""Ping ordering, and the last injected digest read back out.

Two halves. The first numbers the `activity` and `injected` pings per reporting source
so the daemon can drop a stale one instead of letting it mislead the watchdog's
classification. The second lets a live identity's last digest be read at runtime, which
is what gives an explainer the INPUT and not only the verdict.

The socket here is the real one. These are notifications, so there is nothing to wait
on -- every test that needs to know the daemon has finished with one sends a request
behind it and waits for the answer. The daemon handles one connection's messages in
order, so the answer is proof the notification ahead of it was handled.
"""

from __future__ import annotations

import contextlib

import pytest
from conftest import raw_connection, settle, wait_for_queue, wait_until

from moxi_comms.client import DaemonClient
from moxi_comms.paths import daemon_socket

IDENTITY = "reviewer@proj-a"


# --------------------------------------------------------------------------- helpers

class RecordingClient:
    """Stands in for the `moxi.ipc` client so a notification can be read as the sender
    wrote it: `{"method": ..., **params}`."""

    def __init__(self):
        self.frames: list = []

    def notify(self, method: str, params: object = None) -> None:
        self.frames.append({"method": method, **(params or {})})


def recording(client: DaemonClient) -> RecordingClient:
    client._client = RecordingClient()
    return client._client


@pytest.fixture
def raw():
    """Bare connections to the daemon socket, past the hello, closed at the end of the
    test."""
    with contextlib.ExitStack() as stack:
        yield lambda: stack.enter_context(raw_connection())


def deliver_one(rig, shim, text="for the reviewer"):
    rig.post_as("agent-6", [IDENTITY], text)
    wait_for_queue(shim, 1)
    return shim.queue[0]["message_id"]


def ask_injected(identity=IDENTITY) -> dict:
    """The read surface, taken the way a caller outside the daemon has to take it."""
    client = DaemonClient(daemon_socket()).connect()
    try:
        return client.query("injected", identity=identity)
    finally:
        client.close()


# ------------------------------------------------------------- the sender's numbering

def test_the_two_ordered_pings_share_one_strictly_increasing_count():
    client = DaemonClient("/nowhere")
    sent = recording(client)

    client.notify("activity", identity=IDENTITY)
    client.notify("injected", identity=IDENTITY, through="7", digest="hi")
    client.notify("activity", identity=IDENTITY)

    assert [f["seq"] for f in sent.frames] == [1, 2, 3], (
        "one counter per source, shared by both kinds -- the daemon keeps one "
        "last-accepted number, so the two kinds cannot be numbered separately")


def test_an_unordered_notify_is_not_numbered():
    client = DaemonClient("/nowhere")
    sent = recording(client)
    client.notify("nudge")
    assert sent.frames == [{"method": "nudge"}], "nothing orders a nudge"


def test_each_connection_counts_from_one():
    a, b = DaemonClient("/nowhere"), DaemonClient("/nowhere")
    sent_a, sent_b = recording(a), recording(b)
    a.notify("activity", identity=IDENTITY)
    a.notify("activity", identity=IDENTITY)
    b.notify("activity", identity=IDENTITY)
    assert [f["seq"] for f in sent_a.frames] == [1, 2]
    assert [f["seq"] for f in sent_b.frames] == [1], (
        "an adapter builds a fresh client on every reconnect; the count restarts with it")


# ------------------------------------------------------------------ the daemon's guard

def test_a_stale_activity_ping_is_dropped(rig):
    client = DaemonClient(daemon_socket()).connect()
    try:
        client.notify("activity", identity=IDENTITY)          # seq 1
        assert wait_until(lambda: rig.daemon.activity.get(IDENTITY))
        first = rig.daemon.activity[IDENTITY]

        rig.clock.advance(100)
        client._client.notify("activity", {"identity": IDENTITY, "seq": 1})
        client.request("ping")                                 # barrier
        assert rig.daemon.activity[IDENTITY] == first, "equal is not greater"

        client._client.notify("activity", {"identity": IDENTITY, "seq": 0})
        client.request("ping")
        assert rig.daemon.activity[IDENTITY] == first, "a late ping is not a fresh turn"

        client.notify("activity", identity=IDENTITY)           # seq 2, genuinely newer
        assert wait_until(lambda: rig.daemon.activity[IDENTITY] != first)
    finally:
        client.close()


def test_a_stale_injected_ping_does_not_overwrite_what_was_shown(rig, raw):
    conn = raw()
    conn.notify("injected", {"identity": IDENTITY, "through": "20",
               "seq": 5, "text": "the real digest"})
    settle(conn)
    assert rig.daemon.injected_through[IDENTITY] == "20"

    conn.notify("injected", {"identity": IDENTITY, "through": "9",
               "seq": 4, "text": "a replayed digest"})
    settle(conn)
    assert rig.daemon.injected_through[IDENTITY] == "20"
    assert rig.daemon.injected_digest[IDENTITY] == "the real digest"

    conn.notify("injected", {"identity": IDENTITY, "through": "31",
               "seq": 6, "text": "the next digest"})
    settle(conn)
    assert rig.daemon.injected_through[IDENTITY] == "31"
    assert rig.daemon.injected_digest[IDENTITY] == "the next digest"


def test_a_ping_with_no_seq_is_accepted_and_leaves_the_counter_alone(rig, raw):
    """These are hints. A sender that numbers nothing is not a sender to go deaf to."""
    conn = raw()
    conn.notify("injected", {"identity": IDENTITY, "through": "5", "seq": 3})
    conn.notify("injected", {"identity": IDENTITY, "through": "6"})    # no seq
    settle(conn)
    assert rig.daemon.injected_through[IDENTITY] == "6"

    conn.notify("injected", {"identity": IDENTITY, "through": "7", "seq": 3})
    settle(conn)
    assert rig.daemon.injected_through[IDENTITY] == "6", (
        "the unnumbered frame must not have moved the counter past 3")

    conn.notify("injected", {"identity": IDENTITY, "through": "8", "seq": 4})
    settle(conn)
    assert rig.daemon.injected_through[IDENTITY] == "8"


def test_an_unreadable_seq_is_treated_as_unnumbered_not_as_a_bad_frame(rig, raw):
    conn = raw()
    conn.notify("injected", {"identity": IDENTITY, "through": "3", "seq": "later"})
    settle(conn)
    assert rig.daemon.injected_through[IDENTITY] == "3"


def test_the_count_is_per_source_so_one_connection_cannot_starve_another(rig, raw):
    """The grain is (connection, identity). A ping forged on some other connection is
    still accepted -- a known gap this guard does not close -- but it
    cannot run the real adapter's counter forward and silence it."""
    loud, real = raw(), raw()
    loud.notify("activity", {"identity": IDENTITY, "seq": 5000})
    settle(loud)
    before = rig.daemon.activity[IDENTITY]

    rig.clock.advance(100)
    real.notify("activity", {"identity": IDENTITY, "seq": 1})
    settle(real)
    assert rig.daemon.activity[IDENTITY] != before, (
        "the real adapter's first ping must not be measured against a stranger's count")


def test_two_identities_on_one_connection_are_counted_apart(rig, raw):
    conn = raw()
    conn.notify("injected", {"identity": "planner@proj-a", "through": "10", "seq": 9})
    conn.notify("injected", {"identity": IDENTITY, "through": "11", "seq": 1})
    settle(conn)
    assert rig.daemon.injected_through["planner@proj-a"] == "10"
    assert rig.daemon.injected_through[IDENTITY] == "11"


def test_the_count_dies_with_the_connection_that_fed_it(rig, raw):
    """A reconnecting adapter starts at 1 again. Keeping the old number would drop its
    whole first run of pings, which is the failure the guard exists to prevent."""
    first = raw()
    first.notify("activity", {"identity": IDENTITY, "seq": 40})
    settle(first)
    before = rig.daemon.activity[IDENTITY]
    first.close()
    assert wait_until(lambda: not rig.daemon._ping_seq)

    rig.clock.advance(100)
    second = raw()
    second.notify("activity", {"identity": IDENTITY, "seq": 1})
    settle(second)
    assert rig.daemon.activity[IDENTITY] != before


def test_the_live_shim_numbers_its_pings(rig):
    """End to end: the guard is armed on the path an actual adapter takes."""
    shim = rig.shim(name="reviewer", scope="proj-a")
    mid = deliver_one(rig, shim)
    shim.on_turn_boundary()
    assert wait_until(lambda: rig.daemon.injected_through.get(IDENTITY) == mid)
    seen = rig.daemon._ping_seq[rig.daemon.bindings[IDENTITY].conn]
    assert seen[IDENTITY] >= 1


# ------------------------------------------------------- the digest, read at runtime

def test_the_last_injected_digest_is_readable_for_a_live_identity(rig):
    shim = rig.shim(name="reviewer", scope="proj-a")
    mid = deliver_one(rig, shim, "the body an explainer has to be able to show")
    digest = shim.on_turn_boundary()
    assert wait_until(lambda: rig.daemon.injected_digest.get(IDENTITY))

    answer = ask_injected()
    assert answer["identity"] == IDENTITY
    assert answer["bound"] is True
    assert answer["through"] == mid
    assert answer["digest"] == digest, "exactly the text the model was shown"
    assert "the body an explainer has to be able to show" in answer["digest"]


def test_a_second_injection_replaces_the_first(rig):
    shim = rig.shim(name="reviewer", scope="proj-a")
    deliver_one(rig, shim, "the older one")
    first = shim.on_turn_boundary()
    assert wait_until(lambda: rig.daemon.injected_digest.get(IDENTITY) == first)

    rig.post_as("agent-6", [IDENTITY], "the newer one")
    wait_for_queue(shim, 1)
    second = shim.on_turn_boundary()
    assert wait_until(lambda: rig.daemon.injected_digest.get(IDENTITY) == second)
    assert ask_injected()["digest"] == second
    assert "the older one" not in ask_injected()["digest"]


def test_a_bound_identity_with_nothing_injected_yet_reads_empty(rig):
    rig.shim(name="reviewer", scope="proj-a")
    answer = ask_injected()
    assert (answer["identity"], answer["bound"]) == (IDENTITY, True)
    assert answer["through"] is None and answer["digest"] is None


def test_an_unbound_name_answers_rather_than_refusing(rig):
    rig.shim(name="reviewer", scope="proj-a")
    answer = ask_injected("nobody@proj-a")
    assert answer["bound"] is False
    assert answer["digest"] is None and answer["through"] is None


def test_the_digest_dies_with_the_binding(rig):
    """Runtime state for a LIVE identity, not a history. When the adapter goes, so does
    the text -- exactly as `injected_through` already behaves."""
    shim = rig.shim(name="reviewer", scope="proj-a")
    deliver_one(rig, shim)
    shim.on_turn_boundary()
    assert wait_until(lambda: rig.daemon.injected_digest.get(IDENTITY))

    shim.stop()
    assert wait_until(lambda: IDENTITY not in rig.daemon.bindings)
    assert IDENTITY not in rig.daemon.injected_digest
    answer = ask_injected()
    assert answer["bound"] is False and answer["digest"] is None


def test_the_digest_is_not_what_routing_runs_on(rig, raw):
    """A ping may carry no digest at all; that must not disturb `injected_through`,
    which is the field the watchdog actually reads."""
    conn = raw()
    conn.notify("injected", {"identity": IDENTITY, "through": "42", "seq": 1})
    settle(conn)
    assert rig.daemon.injected_through[IDENTITY] == "42"
    assert IDENTITY not in rig.daemon.injected_digest
