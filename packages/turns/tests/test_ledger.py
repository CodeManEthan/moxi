import time

from moxi_turns.ledger import TurnLedger


def test_the_ledger():
    led = TurnLedger("s-1")
    r0 = led.read()
    assert (r0.turn_number, r0.turn_open, r0.session_id) == (0, False, "s-1")
    prev, turn = led.started("t1", "d1")
    assert prev is None and led.read().turn_open and led.read().turn_number == 1
    assert led.steered_into("d2") is turn and turn.steered == ["d2"]
    assert led.ended("answer") is turn and turn.outcome == "answer"
    assert led.ended("answer") is None and led.steered_into("d3") is None
    v = led.read().version
    assert not led.new_session("s-1") and led.read().version == v
    assert led.new_session("s-2")
    r = led.read()
    assert (r.session_id, r.turn_number, led.earlier) == ("s-2", 0, ["s-1"]) and r.version > v
    led.started("t2", None)
    prev, _ = led.started("t3", None)
    assert prev.turn_id == "t2" and prev.outcome == "error"


def test_an_ending_turn_stays_open_and_takes_no_steer():
    # The ending form: Codex was told the turn may end. It is still open (a start
    # is still refused busy, held mail still waits) but no steer goes into it.
    led = TurnLedger("s-1")
    _, turn = led.started("t1", "d1")
    assert led.steered_into("d2") is turn
    assert led.ending() is turn and turn.ending
    reading = led.read()
    assert reading.turn_open and reading.turn_id == "t1"
    assert led.steered_into("d3") is None and turn.steered == ["d2"]
    assert led.ended("answer") is turn
    assert led.ending() is None
    _, second = led.started("t2", None)
    assert not second.ending and led.steered_into("d4") is second


def test_the_ledger_keeps_when_the_last_turn_ended():
    led = TurnLedger("s-1")
    assert led.read().ended_at is None
    led.started("t1", None)
    assert led.read().ended_at is None
    before = time.time()
    led.ended("answer")
    first = led.read().ended_at
    assert before <= first <= time.time()
    # A turn open keeps the last end; an end missed and closed at the next start leaves it.
    led.started("t2", None)
    assert led.read().ended_at == first
    led.started("t3", None)
    assert led.read().ended_at == first
    led.ended("answer")
    assert led.read().ended_at >= first
    # A new session id clears it; the first id learned does not.
    first_id = TurnLedger(None)
    first_id.started("t1", None)
    first_id.ended("answer")
    assert first_id.new_session("s-1") and first_id.read().ended_at is not None
    assert led.new_session("s-2") and led.read().ended_at is None
