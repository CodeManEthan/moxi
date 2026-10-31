"""Delete, expiry and the interrupt mark: line zero's tokens, the verb's flags, the
daemon's `delete`, the drops at routing, release and the sweep, and `explain`'s two
cases."""

from __future__ import annotations

import pytest
from conftest import (_stamp_of, as_frames, projects_root, store_rows, synced, wait_until,
                      warm_session)

from moxi import exit as moxi_exit
from moxi_comms import api, glossary, machine_line
from moxi_comms import daemon as daemon_mod
from moxi_comms.client import DaemonClient
from moxi_comms.paths import daemon_socket

REVIEWER = "reviewer@proj-a"
MAIN = "main@proj-a"


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


# --------------------------------------------------------------------------- end to end

class Reader:
    """A bound reviewer on a bare connection with a warm session, so mail goes to it
    live: it keeps every push and opens digests and ends turns as an adapter does."""

    def __init__(self, rig):
        self.frames: list = []
        self.client = DaemonClient(daemon_socket(),
                                   on_push=as_frames(self.frames)).connect()
        self.client.bind(name="reviewer", scope="proj-a", session_id=warm_session(),
                         projects_root=str(projects_root()))
        self.rig = rig

    def handed(self) -> list:
        synced(self.client)
        return [f["payload"]["message_id"] for f in self.frames if f["op"] == "deliver"]

    def open(self, *ids) -> dict:
        return self.client.call("open_digest", {"ids": [str(i) for i in ids]})

    def turn(self, handled=(), unhandled=()) -> None:
        self.client.call("turn_ended", {"identity": REVIEWER, "turned": True,
                                        "handled": list(handled),
                                        "unhandled": list(unhandled)})
        synced(self.client)

    def look(self) -> None:
        self.client.notify("look", {"identity": REVIEWER})
        synced(self.client)


@pytest.fixture
def reader(rig):
    r = Reader(rig)
    yield r
    r.client.close()


def post(rig, recipients, text, **kw):
    return rig.record.post_as_identity(
        "planner", rig.names_channel, machine_line.compose(recipients, text, **kw)).id


def handed_rows(name, ids) -> bool:
    got = {m for m, _, st in store_rows(name) if st == "handed"}
    return set(ids) <= got


def test_an_overflowed_push_past_its_expiry_is_dropped_when_the_window_frees(
        rig, reader, monkeypatch):
    monkeypatch.setattr(daemon_mod, "WINDOW", 1)
    first = post(rig, [REVIEWER], "first")
    assert wait_until(lambda: first in reader.handed())
    late = post(rig, [REVIEWER], "late", expires_at=int(rig.clock()) + 60)
    b = rig.daemon.bindings[REVIEWER]
    assert wait_until(lambda: late in b.overflow), "the window is full: it waits for room"
    rig.clock.advance(120)
    assert reader.open(first)["ids"] == [first]
    assert late not in reader.handed()
    assert rig.daemon.books.dropped_reason(REVIEWER, late) == "expired"
    assert late not in b.window and late not in b.delivered
    assert late not in {m for m, _, _ in store_rows(REVIEWER)}


def test_a_digest_that_ends_unhandled_past_its_expiry_is_dropped_not_replayed(
        rig, reader):
    mid = post(rig, [REVIEWER], "soon stale", expires_at=int(rig.clock()) + 60)
    assert wait_until(lambda: mid in reader.handed())
    opened = reader.open(mid)
    assert opened["ids"] == [mid]
    rig.clock.advance(120)
    reader.turn(unhandled=[opened["digest"]])
    reader.look()                                         # the next release point
    assert wait_until(lambda: rig.daemon.books.dropped_reason(REVIEWER, mid) == "expired")
    assert reader.handed() == [mid], "handed once, never again"
    assert store_rows(REVIEWER) == []
    assert mid not in rig.daemon.queue.get(REVIEWER, {})


def test_a_waiting_message_past_its_expiry_is_dropped_at_the_bind(rig):
    mid = post(rig, [REVIEWER], "for later", expires_at=int(rig.clock()) + 60)
    assert wait_until(lambda: mid in rig.daemon.queue.get(REVIEWER, {}))
    rig.clock.advance(120)
    shim = rig.shim(name="reviewer", scope="proj-a")
    wait_until(lambda: not shim.replaying)
    after = post(rig, [REVIEWER], "fresh")
    assert wait_until(lambda: after in [p["message_id"] for p in shim.queue])
    assert mid not in [p["message_id"] for p in shim.queue]
    assert rig.daemon.books.dropped_reason(REVIEWER, mid) == "expired"


def test_delete_after_a_digest_is_already_delivered(rig, reader):
    rig.shim(name="planner")
    code, out = rig.send(REVIEWER, "read it", sender=dict(name="planner"))
    assert code == 0, out
    mid = last(rig).id
    assert wait_until(lambda: mid in reader.handed())
    reader.open(mid)
    code, out = rig.cli(["delete", mid], name="planner")
    assert code == moxi_exit.REFUSED, out
    assert f"already delivered to {REVIEWER}" in out
    assert rig.record.get_message(mid).withdrawn_at is None
    assert rig.daemon.books.dropped_reason(REVIEWER, mid) is None


def test_a_handed_row_not_yet_in_a_digest_is_still_withdrawn(rig, reader):
    rig.shim(name="planner")
    code, out = rig.send(REVIEWER, "pushed, not read", sender=dict(name="planner"))
    assert code == 0, out
    mid = last(rig).id
    assert wait_until(lambda: mid in reader.handed() and handed_rows(REVIEWER, [mid]))
    code, out = rig.cli(["delete", mid], name="planner")
    assert code == moxi_exit.OK, out
    assert f"withdrawn from {REVIEWER}" in out
    opened = reader.open(mid)
    assert opened["ids"] == [] and opened["dropped"] == [{"id": mid,
                                                          "reason": "withdrawn"}]


def test_delete_withdraws_from_some_and_says_who_had_it(rig, reader):
    rig.shim(name="planner")
    code, out = rig.send(f"{REVIEWER},{MAIN}", "half read", sender=dict(name="planner"))
    assert code == 0, out
    mid = last(rig).id
    assert wait_until(lambda: mid in reader.handed())
    opened = reader.open(mid)
    code, out = rig.cli(["delete", mid, "--json"], name="planner")
    assert code == moxi_exit.OK, out
    assert '"withdrawn": ["main@proj-a"]' in out and '"delivered": ["reviewer@proj-a"]' in out
    # The reviewer's digest ends unhandled: the message, withdrawn now, isn't replayed.
    reader.turn(unhandled=[opened["digest"]])
    reader.look()
    assert wait_until(lambda: rig.daemon.books.dropped_reason(REVIEWER, mid) == "withdrawn")
    assert reader.handed() == [mid]


# --------------------------------------------------------------------------- the API

def _sender(name):
    return _stamp_of({"MOXI_COMMS_PROJECT": "proj-a", "MOXI_COMMS_NAME": name})


def test_the_api_sends_interrupt_and_expires_and_deletes(rig):
    rig.shim(name="planner")
    with rig.session_holding(_sender("planner")) as path:
        sent = api.send(api.Sender.socket(path), MAIN, "now", posture="none",
                        interrupt=True, expires="30m")
        checked = api.check(api.Sender.socket(path), MAIN, "now", posture="none",
                            interrupt=True, expires=3600)
        assert sent.outcome == "sent", sent.lines()
        assert checked.outcome == "would-send", checked.lines()
        ml = machine_line.parse(rig.record.get_message(sent.message_id).content)
        assert ml.interrupt and ml.expires_at is not None
        assert wait_until(lambda: sent.message_id in rig.daemon.queue.get(MAIN, {}))
        gone = api.delete(api.Sender.socket(path), sent.message_id)
        again = api.delete(api.Sender.socket(path), sent.message_id)
        unknown = api.delete(api.Sender.socket(path), "4611686018427399999")
    assert isinstance(gone, api.DeleteResult)
    assert (gone.outcome, gone.exit_code) == ("deleted", moxi_exit.OK), gone.lines()
    assert gone.withdrawn == (MAIN,) and gone.delivered == () and gone.refusal is None
    assert gone.lines() == [f"withdrawn from {MAIN}"]
    assert gone.to_dict()["withdrawn"] == [MAIN]
    assert again.outcome == "deleted"
    assert unknown.outcome == "refused" and unknown.refusal is not None


def test_the_api_refuses_a_bad_expiry_as_the_parser_would(rig):
    with pytest.raises(ValueError):
        api.send(api.Sender.session(), MAIN, "x", posture="none", expires="5y")
    with pytest.raises(TypeError):
        api.delete(object(), "1")


def test_the_api_delete_offline_is_offline(rig):
    rig.shim(name="planner")
    with rig.session_holding(_sender("planner")) as path:
        rig.daemon.stop()
        got = api.delete(api.Sender.socket(path), "12345")
    assert got.outcome == "offline" and got.exit_code == moxi_exit.TEMPFAIL


# --------------------------------------------------------------------------- help

@pytest.mark.parametrize("form,term", [
    ("interrupt", "interrupt"), ("--interrupt", "interrupt"), ("[interrupt]", "interrupt"),
    ("expiry", "expiry"), ("--expires", "expiry"), ("expired", "expiry"),
    ("withdrawn", "withdrawn"), ("delete", "withdrawn"), ("withdraw", "withdrawn"),
])
def test_the_glossary_explains_the_new_words(form, term):
    entry = glossary.lookup(form)
    assert entry is not None and entry.term == term


# --------------------------------------------------------------------------- history, explain

def history_row(out, message_id):
    (row,) = [ln for ln in out.splitlines() if ln.startswith(f"{message_id} ")]
    return row


def test_history_marks_a_withdrawn_and_an_expired_message(rig):
    rig.shim(name="planner")
    code, out = rig.send(MAIN, "take it back", sender=dict(name="planner"))
    assert code == 0, out
    withdrawn = last(rig).id
    assert wait_until(lambda: withdrawn in rig.daemon.queue.get(MAIN, {}))
    code, out = rig.cli(["delete", withdrawn], name="planner")
    assert code == moxi_exit.OK, out
    stale = post(rig, [MAIN, REVIEWER], "stale", expires_at=int(rig.clock()) - 1)
    assert wait_until(lambda: rig.daemon.books.dropped_reason(REVIEWER, stale))
    kept = post(rig, [MAIN], "kept")
    code, out = rig.cli(["history", "planner"])
    assert code == 0, out
    assert history_row(out, withdrawn).endswith(" [no reply] [withdrawn]")
    assert history_row(out, stale).endswith(f" [expired for {MAIN}, {REVIEWER}]")
    assert "withdrawn" not in history_row(out, kept)
    assert "expired" not in history_row(out, kept)


def test_explain_with_the_daemon_down_reads_dropped(rig):
    rig.shim(name="planner")
    code, out = rig.send(MAIN, "take it back", sender=dict(name="planner"))
    mid = last(rig).id
    assert wait_until(lambda: mid in rig.daemon.queue.get(MAIN, {}))
    code, out = rig.cli(["delete", mid], name="planner")
    assert code == moxi_exit.OK, out
    stale = post(rig, [MAIN], "stale", expires_at=int(rig.clock()) - 1)
    assert wait_until(lambda: rig.daemon.books.dropped_reason(MAIN, stale))
    rig.daemon.stop()
    code, out = rig.cli(["explain", MAIN, mid])
    assert code == moxi_exit.TEMPFAIL, out
    assert "comms offline" in out
    assert f"{MAIN}   withdrawn   message {mid}" in out
    assert "withdrew the message" in out
    code, out = rig.cli(["explain", MAIN, stale])
    assert f"{MAIN}   expired   message {stale}" in out
    code, out = rig.cli(["explain", MAIN, "12345"])
    assert "withdrawn" not in out and "expired" not in out
