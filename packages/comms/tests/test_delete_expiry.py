"""Delete, expiry and the interrupt mark: line zero's tokens, the verb's flags, the
daemon's `delete`, the drops at routing, release and the sweep, and `explain`'s two
cases."""

from __future__ import annotations

import pytest
from conftest import wait_until

from moxi import exit as moxi_exit
from moxi_comms import machine_line


def last(rig):
    return rig.record.history(rig.names_channel)[-1]


def test_line_zero_carries_interrupt_and_expiry():
    body = machine_line.compose(["a", "b"], "hi", interrupt=True, expires_at=1700000000,
                                cold_at=["-", 5])
    line = body.splitlines()[0]
    assert line == "::to a, b [cold-at:-,5] [interrupt] [expires:1700000000]"
    parsed = machine_line.parse(body)
    assert parsed.interrupt and parsed.expires_at == 1700000000
    # out of order is prose
    assert machine_line.parse("::to a [expires:5] [interrupt]\nx") is None
    plain = machine_line.parse("::to a\nx")
    assert not plain.interrupt and plain.expires_at is None


def test_the_verb_writes_the_default_expiry_and_interrupt(rig, monkeypatch):
    monkeypatch.delenv("MOXI_COMMS_DELIVERY_EXPIRY")      # the schema's default, 24h
    rig.shim(name="x")
    code, out = rig.send("x", "hi", "--interrupt", sender=dict(name="planner"),
                         clock=lambda: 1_760_000_000.0)
    assert code == 0, out
    ml = machine_line.parse(last(rig).content)
    assert ml.interrupt
    assert ml.expires_at == 1_760_000_000 + 86400


def test_expires_never_and_a_duration(rig):
    rig.shim(name="x")
    code, out = rig.send("x", "hi", "--expires", "never", sender=dict(name="planner"))
    assert code == 0, out
    assert machine_line.parse(last(rig).content).expires_at is None
    code, out = rig.send("x", "hi", "--expires", "30m", sender=dict(name="planner"),
                         clock=lambda: 1000.0)
    assert code == 0, out
    assert machine_line.parse(last(rig).content).expires_at == 1000 + 1800


@pytest.mark.parametrize("flags", [["--interrupt", "--held"], ["--expires", "5y"]])
def test_bad_flags_are_usage_errors(rig, flags):
    rig.shim(name="x")
    before = len(rig.record.history(rig.names_channel))
    code, out = rig.send("x", "hi", *flags, sender=dict(name="planner"))
    assert code == moxi_exit.USAGE, out
    assert len(rig.record.history(rig.names_channel)) == before


def test_a_waiting_row_past_its_expiry_is_swept_and_explained(rig):
    # Unbound recipient: the message waits in a row.
    now = int(rig.clock())
    rig.record.post_as_identity(
        "planner", rig.names_channel,
        machine_line.compose(["main@proj-a"], "late", expires_at=now + 60))
    wait_until(lambda: rig.daemon.queue.get("main@proj-a"))
    mid = next(iter(rig.daemon.queue["main@proj-a"]))
    rig.clock.advance(120)
    assert rig.daemon.sweep_expired() == 1
    assert not rig.daemon.queue.get("main@proj-a")
    assert rig.daemon.books.dropped_reason("main@proj-a", mid) == "expired"
    info = rig.daemon._explain_query({"identity": "main@proj-a", "message_id": mid})
    assert info["case"] == "expired"


def test_an_expired_message_is_dropped_at_routing(rig):
    now = int(rig.clock())
    msg = rig.record.post_as_identity(
        "planner", rig.names_channel,
        machine_line.compose(["main@proj-a"], "stale", expires_at=now - 1))
    wait_until(lambda: rig.daemon.books.dropped_reason("main@proj-a", msg.id))
    assert rig.daemon.books.dropped_reason("main@proj-a", msg.id) == "expired"
    assert not rig.daemon.queue.get("main@proj-a")


def test_delete_withdraws_a_waiting_message_and_refuses_a_second_author(rig):
    rig.shim(name="planner")
    code, out = rig.send("main@proj-a", "take it back", sender=dict(name="planner"))
    assert code == 0, out
    mid = last(rig).id
    wait_until(lambda: mid in rig.daemon.queue.get("main@proj-a", {}))
    rig.shim(name="other")
    code, out = rig.cli(["delete", mid], name="other")
    assert code == moxi_exit.REFUSED, out
    assert "not yours" in out
    code, out = rig.cli(["delete", mid], name="planner")
    assert code == moxi_exit.OK, out
    assert "withdrawn from main@proj-a" in out
    assert not rig.daemon.queue.get("main@proj-a")
    assert rig.record.get_message(mid).withdrawn_at
    info = rig.daemon._explain_query({"identity": "main@proj-a", "message_id": mid})
    assert info["case"] == "withdrawn"
    # A second delete answers as the first did.
    code, out = rig.cli(["delete", mid], name="planner")
    assert code == moxi_exit.OK, out


def test_delete_of_an_unknown_id_is_refused(rig):
    rig.shim(name="planner")
    code, out = rig.cli(["delete", "4611686018427399999", "--json"], name="planner")
    assert code == moxi_exit.REFUSED, out
    assert '"outcome": "refused"' in out
