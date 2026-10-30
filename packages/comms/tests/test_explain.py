"""`moxi comms explain`, and the named reasons under it.

Two halves. The first gives every reason the cursor in comms.db's books stands where it
does a name and a sentence. The second is the verb that prints them next to the
binding, the adapter kind, the cursor and the last delivery -- every live fact behind a
delivery, in one command.

Nothing here is allowed to write anything down. The whole point of the command is that
it reads live facts, so every test that asserts on an answer first arranges the fact
it is asking about.
"""

from __future__ import annotations

import pytest
from conftest import cursor_of, put_cursor, wait_for_queue, wait_until

from moxi_comms.store import books
from moxi_comms.client import DaemonClient
from moxi_comms.daemon import (CASE_HANDLED, CASE_IGNORED, CASE_NOT_ADDRESSABLE,
                               CASE_REASONS, CASE_TURN_INCOMPLETE, CASE_WAITING)
from moxi_comms.paths import daemon_socket

IDENTITY = "reviewer@proj-a"


@pytest.fixture(autouse=True)
def on_the_mock(monkeypatch):
    """This suite's rig is the mock, so the process names the mock rather than fall
    through to the profile's default."""
    monkeypatch.setenv("MOXI_COMMS_BACKEND", "mock")


def ask(identity=IDENTITY, message_id=None) -> dict:
    """The daemon's answer, taken the way a caller outside the daemon has to take it."""
    client = DaemonClient(daemon_socket()).connect()
    try:
        return client.call("explain", {"identity": identity, "message_id": message_id})
    finally:
        client.close()


def deliver_one(rig, shim, text="for the reviewer"):
    rig.post_as("agent-6", [IDENTITY], text)
    wait_for_queue(shim, 1)
    message_id = shim.queue[0]["message_id"]
    # The daemon records the hand-over time after the send, so the shim can hold the
    # payload a moment before `handed_at` names it.
    assert wait_until(lambda: (IDENTITY, message_id) in rig.daemon.handed_at)
    return message_id


# ------------------------------------------------------- half one: the named reasons

def test_every_named_reason_has_a_sentence():
    """A name with no sentence is a second thing to look up, which is the problem the
    card is about."""
    names = {books.NO_CURSOR_YET, books.ALREADY_PAST, books.BEHIND, books.WAITING}
    assert names == set(books.REASONS), "every name is in the table, and only names are"
    for name, sentence in books.REASONS.items():
        assert sentence.endswith(".") and len(sentence) > 60, name
        assert name not in sentence, "the sentence explains the name, it does not repeat it"


def test_explain_compares_against_the_message_it_is_asked_about(rig):
    put_cursor(IDENTITY, "100")
    row = books.read_cursor(IDENTITY)
    assert books.explain_cursor(row, "100")["reason"] == books.ALREADY_PAST
    assert books.explain_cursor(row, "99")["reason"] == books.ALREADY_PAST
    assert books.explain_cursor(row, "101")["reason"] == books.BEHIND
    assert books.explain_cursor(row)["reason"] is None, "no message, nothing to compare"
    assert books.explain_cursor(row)["cursor"] == "100"
    assert books.explain_cursor(None)["reason"] == books.NO_CURSOR_YET


# ------------------------------------------------------------- half two: the command

def test_the_four_cases_each_have_a_sentence():
    assert set(CASE_REASONS) == {CASE_HANDLED, CASE_NOT_ADDRESSABLE, CASE_IGNORED,
                                 CASE_TURN_INCOMPLETE}


def test_explain_classifies_with_classify_over_handed_at(rig):
    """Not a second opinion. `_explain_query` calls `classify`, the one
    classifier, and its delivery facts come from `handed_at`."""
    reviewer = rig.shim(name="reviewer", scope="proj-a")
    message_id = deliver_one(rig, reviewer)
    assert (IDENTITY, message_id) in rig.daemon.handed_at

    for step in range(3):
        if step == 1:
            # the injected frame reaches the daemon after the shim's boundary returns;
            # ask once it has, or the ask and `classify` can straddle it
            assert wait_until(lambda: rig.daemon.injected_through.get(IDENTITY))
        info = ask(message_id=message_id)
        assert info["case"] == rig.daemon.classify(IDENTITY, message_id)
        assert info["delivery"]["delivered_for"] is not None
        reviewer.on_turn_boundary()


def test_a_hand_over_the_trim_dropped_reads_age_unknown(rig):
    """Past `THREAD_IDLE` the daily trim drops the hand-over time, and the page
    says "age unknown" with no other clause."""
    from moxi_comms import constants

    reviewer = rig.shim(name="reviewer", scope="proj-a")
    message_id = deliver_one(rig, reviewer)
    rig.clock.advance(constants.THREAD_IDLE + 1)
    rig.daemon.trim_record()
    assert (IDENTITY, message_id) not in rig.daemon.handed_at

    info = ask(message_id=message_id)
    assert info["delivery"]["delivered_for"] is None
    assert "alarm_window" not in info
    code, out = rig.cli(["explain", IDENTITY, message_id],  name="planner",
                        scope="proj-a")
    assert code == 0, out
    (line,) = [ln for ln in out.splitlines() if ln.lstrip().startswith("delivered")]
    assert line.split("delivered", 1)[1].strip() == "age unknown", line


def test_delivered_but_the_turn_never_completed(rig):
    reviewer = rig.shim(name="reviewer", scope="proj-a")
    message_id = deliver_one(rig, reviewer)
    reviewer.on_turn_boundary()                 # injects; the turn does not complete
    assert wait_until(lambda: rig.daemon.injected_through.get(IDENTITY))

    info = ask()
    assert info["case"] == CASE_TURN_INCOMPLETE
    assert info["bound"] is True
    assert info["binding"]["adapter"] == "claude-shim"
    assert info["binding"]["tier"] == "chosen"
    assert info["delivery"]["message_id"] == message_id
    assert info["injected_through"] == message_id
    assert info["cursor"]["reason"] == books.NO_CURSOR_YET
    assert "for the reviewer" in info["digest"], "the input, not only the verdict"


def test_handled_says_so_and_names_the_cursor_reason(rig):
    reviewer = rig.shim(name="reviewer", scope="proj-a")
    message_id = deliver_one(rig, reviewer)
    reviewer.on_turn_boundary()
    reviewer.on_turn_boundary()                 # the turn completes; the cursor moves

    info = ask()
    assert info["case"] == CASE_HANDLED
    assert info["cursor"]["cursor"] == message_id
    assert info["cursor"]["reason"] == books.ALREADY_PAST


def test_delivered_and_ignored(rig):
    reviewer = rig.shim(name="reviewer", scope="proj-a")
    deliver_one(rig, reviewer)
    info = ask()
    assert info["case"] == CASE_IGNORED
    assert info["injected_through"] is None
    assert info["digest"] is None


def test_an_unbound_name_is_still_explainable(rig):
    """The case a person asks about most: the session is gone. The binding facts went
    with it and the hand-over time did not, so there is still an answer. The unbind gave
    the handed, unhandled id a row, and a queued id reads `waiting`
    before anything else."""
    reviewer = rig.shim(name="reviewer", scope="proj-a")
    message_id = deliver_one(rig, reviewer)
    reviewer.stop()
    assert wait_until(lambda: IDENTITY not in rig.daemon.bindings)

    info = ask()
    assert info["case"] == CASE_WAITING
    assert info["cursor"]["reason"] == books.WAITING
    assert info["bound"] is False and info["binding"] is None
    assert info["delivery"]["message_id"] == message_id
    assert info["digest"] is None, "the digest dies with the binding"
    assert info["waiting"] == [message_id], "and its mail is waiting again"


def test_a_gone_name_with_nothing_queued_is_not_addressable(rig):
    """An `agent-N` gets no row at its unbind, so the mail it was handed
    and never handled is the not-addressable case: unbound, nothing waiting, the cursor
    below it."""
    ghost = rig.shim()
    assert ghost.identity == "agent-1"
    rig.post_as("agent-6", ["agent-1"], "for the ghost")
    wait_for_queue(ghost, 1)
    message_id = ghost.queue[0]["message_id"]
    ghost.stop()
    assert wait_until(lambda: "agent-1" not in rig.daemon.bindings)

    info = ask("agent-1")
    assert info["case"] == CASE_NOT_ADDRESSABLE
    assert info["bound"] is False and info["binding"] is None
    assert info["delivery"]["message_id"] == message_id
    assert info["waiting"] == []


def test_a_name_nothing_has_ever_happened_to_answers_rather_than_refusing(rig):
    info = ask("nobody@proj-a")
    assert info["bound"] is False
    assert info["delivery"] is None
    assert info["case"] is None
    assert info["cursor"]["reason"] == books.NO_CURSOR_YET


def test_the_newest_delivery_is_the_default_and_any_id_can_be_asked_for(rig):
    reviewer = rig.shim(name="reviewer", scope="proj-a")
    first = deliver_one(rig, reviewer, "one")
    rig.post_as("agent-6", [IDENTITY], "two")
    wait_for_queue(reviewer, 2)
    second = reviewer.queue[1]["message_id"]
    assert int(second) > int(first)

    assert ask()["delivery"]["message_id"] == second
    assert ask()["delivery"]["asked_for"] is False
    asked = ask(message_id=first)
    assert asked["delivery"]["message_id"] == first
    assert asked["delivery"]["asked_for"] is True


def test_asking_records_nothing(rig):
    """No receipts ledger, ever. Asking twice must leave the
    daemon exactly as the first ask found it."""
    reviewer = rig.shim(name="reviewer", scope="proj-a")
    deliver_one(rig, reviewer)
    before = (dict(rig.daemon.handed_at), dict(rig.daemon.injected_through),
              dict(rig.daemon.activity), {k: dict(v) for k, v in rig.daemon.queue.items()})
    first = ask()
    assert ask() == first
    assert (dict(rig.daemon.handed_at), dict(rig.daemon.injected_through),
            dict(rig.daemon.activity),
            {k: dict(v) for k, v in rig.daemon.queue.items()}) == before


# --------------------------------------------------------------------- the rendering

def test_the_verb_prints_the_facts_the_card_asked_for(rig):
    reviewer = rig.shim(name="reviewer", scope="proj-a")
    message_id = deliver_one(rig, reviewer, "the diff needs a second pair of eyes")
    reviewer.on_turn_boundary()
    assert wait_until(lambda: rig.daemon.injected_through.get(IDENTITY))

    code, out = rig.cli(["explain", IDENTITY],  name="planner", scope="proj-a")
    assert code == 0
    assert IDENTITY in out
    assert CASE_TURN_INCOMPLETE in out                         # the verdict
    assert "claude-shim" in out                                # the adapter kind
    assert message_id in out                                   # the last delivery
    assert "chosen" in out                                     # the binding
    assert books.NO_CURSOR_YET in out                        # the named reason
    assert "the cursor moves on the adapter's turn boundary" in out.lower()
    assert "the diff needs a second pair of eyes" in out       # the input


def test_a_long_digest_is_cut_and_says_how_much_it_cut(rig):
    """Never truncate quietly: the cut says how much it cut."""
    reviewer = rig.shim(name="reviewer", scope="proj-a")
    for n in range(20):
        rig.post_as("agent-6", [IDENTITY], f"message number {n}")
    wait_for_queue(reviewer, 20)
    reviewer.on_turn_boundary()
    assert wait_until(lambda: rig.daemon.injected_digest.get(IDENTITY))

    code, out = rig.cli(["explain", IDENTITY],  name="planner", scope="proj-a")
    assert code == 0
    assert "more characters" in out and "moxi comms history" in out


def test_explain_with_the_daemon_down_still_prints_the_cursor(rig):
    """The cursor is in comms.db and the one durable fact. A blank where a binding
    would be is not an answer; saying which half is unknowable is."""
    put_cursor(IDENTITY, "1500000000000009252")
    rig.daemon.stop()

    code, out = rig.cli(["explain", IDENTITY],  name="planner", scope="proj-a")
    assert code == 75                                            # TEMPFAIL
    assert "comms offline" in out
    assert "1500000000000009252" in out


def test_explain_reaches_no_credential_and_writes_nothing(rig):
    """A READING verb. The fence is the provisioner credential, and this verb
    is on the far side of it: the daemon and one cursor file, nothing else."""
    reviewer = rig.shim(name="reviewer", scope="proj-a")
    deliver_one(rig, reviewer)
    before = rig.record.history(rig.names_channel)

    code, _ = rig.cli(["explain", IDENTITY],  name="planner", scope="proj-a")
    assert code == 0
    assert rig.record.history(rig.names_channel) == before
    assert cursor_of(IDENTITY) is None, "explaining a cursor never moves it"


def test_explaining_a_retired_ephemeral_says_so(rig):
    """The daemon already refuses that name for the send verb, and the refusal is the
    whole answer to "why did my message not land". Printed as it came."""
    code, out = rig.cli(["explain", "agent-99"],  name="planner", scope="proj-a")
    assert code == 3                                            # REFUSED
    assert "never reused" in out


@pytest.mark.parametrize("seconds,expected", [(0, "0s ago"), (45, "45s ago"),
                                              (600, "10m ago"), (7200, "2.0h ago"),
                                              (None, "never")])
def test_ages_are_readable(seconds, expected):
    from moxi_comms.cli import _ago
    assert _ago(seconds) == expected
