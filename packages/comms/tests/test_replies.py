"""The one rule for an ask and its answer (`replies`), which the daemon's `awaiting` and
the page's mail-state read share.

The daemon's own reply and ask tests (`test_reply_enforcement.py`, `test_owner.py`)
prove the daemon decides as it did; these pin the rule itself, and that the daemon's
`awaiting` reads it rather than a copy.
"""

from __future__ import annotations

import pytest

from moxi_comms import constants, daemon, replies
from moxi_comms.replies import SentEntry

ASKER = "reviewer@proj-a"
ANSWERER = "planner@proj-a"
NOW = 1_800_000_000.0


def entry(message_id, *, tags=("reviewer",), posture="none", reply_to=None,
          at=NOW, verified=True, author=ANSWERER):
    return SentEntry(id=message_id, author=author, tags=tuple(tags), posture=posture,
                     reply_to=reply_to, at=at, verified=verified)


def scoped(message_id, **kw):
    """An entry tagged with the asker's scoped name, as a scoped answerer writes it."""
    return entry(message_id, tags=("reviewer@proj-a",), **kw)


# ------------------------------------------------------------------------- the ask

def test_a_verified_once_message_within_the_ttl_is_an_ask():
    assert replies.is_ask(entry("100", posture="once"), NOW) is True


def test_an_ask_at_exactly_the_ttl_still_stands_and_a_second_later_does_not():
    ask = entry("100", posture="once", at=NOW - constants.ASK_TTL)
    assert replies.is_ask(ask, NOW) is True
    assert replies.is_ask(ask, NOW + 1) is False


@pytest.mark.parametrize("posture", ["none", "optional", "open"])
def test_no_other_posture_is_an_ask(posture):
    assert replies.is_ask(entry("100", posture=posture), NOW) is False


def test_an_unverified_once_message_is_no_ask():
    assert replies.is_ask(entry("100", posture="once", verified=False), NOW) is False


# ---------------------------------------------------------------------- the answer

def test_a_threaded_reply_answers_whatever_its_posture():
    for posture in ("none", "optional", "once", "open"):
        reply = scoped("101", posture=posture, reply_to="100")
        assert replies.answered("100", ASKER, [reply]) is True, posture


@pytest.mark.parametrize("posture", ["optional", "once", "open"])
def test_an_unthreaded_message_to_the_asker_answers_unless_it_is_no_reply(posture):
    assert replies.answered("100", ASKER, [scoped("101", posture=posture)]) is True


def test_an_unthreaded_no_reply_message_does_not_answer():
    assert replies.answered("100", ASKER, [scoped("101", posture="none")]) is False


def test_a_message_threaded_to_another_message_is_read_as_unthreaded():
    assert replies.answered(
        "100", ASKER, [scoped("101", posture="none", reply_to="99")]) is False
    assert replies.answered(
        "100", ASKER, [scoped("101", posture="open", reply_to="99")]) is True


def test_only_a_message_after_the_ask_answers_it():
    assert replies.answered("100", ASKER, [scoped("99", reply_to="100")]) is False
    assert replies.answered("100", ASKER, [scoped("100", reply_to="100")]) is False
    assert replies.answered("100", ASKER, [scoped("101", reply_to="100")]) is True


def test_ids_compare_as_numbers_not_strings():
    """19-digit ids against shorter ones: `"99" > "100"` as strings, not as ids."""
    assert replies.answered("99", ASKER, [scoped("100", posture="open")]) is True
    big = "4611686018427388000"
    assert replies.answered(big, ASKER, [scoped(str(int(big) + 1), posture="open")])
    assert not replies.answered(big, ASKER, [scoped(str(int(big) - 1), posture="open")])


def test_an_unreadable_id_never_answers():
    assert replies.answered("100", ASKER, [scoped("x", posture="open")]) is False


def test_a_message_that_does_not_name_the_asker_does_not_answer():
    assert replies.answered(
        "100", ASKER, [entry("101", tags=("critic",), reply_to="100")]) is False


def test_the_tag_names_the_asker_by_the_one_rule():
    """A bare tag names the global name, never a scoped one, and the reverse."""
    assert replies.answered(
        "100", ASKER, [entry("101", tags=("reviewer",), reply_to="100")]) is False
    assert replies.answered(
        "100", "reviewer", [entry("101", tags=("reviewer",), reply_to="100")]) is True
    assert replies.answered(
        "100", "reviewer", [entry("101", tags=("reviewer@global",),
                                  reply_to="100")]) is True
    assert replies.answered(
        "100", "reviewer", [scoped("101", reply_to="100")]) is False


def test_one_tag_among_several_is_enough():
    reply = entry("101", tags=("critic", "reviewer@proj-a"), posture="open")
    assert replies.answered("100", ASKER, [reply]) is True


def test_any_one_answer_among_the_replies_is_enough_in_any_order():
    later = [scoped("103", posture="open"), scoped("101", posture="none")]
    assert replies.answered("100", ASKER, later) is True
    assert replies.answered("100", ASKER, list(reversed(later))) is True
    assert replies.answered("100", ASKER, []) is False


def test_the_author_is_the_callers_to_pick():
    """`answered` judges the entries it is given; it does not filter by author."""
    reply = scoped("101", posture="open", author="someone-else@proj-a")
    assert replies.answered("100", ASKER, [reply]) is True


def test_the_operator_answers_by_the_same_rule():
    """The page applies the rule with him as the answerer: his `[no reply]` message to
    the asker answers only when threaded to the ask."""
    his = entry("101", tags=("planner@proj-a",), posture="none", author="operator")
    assert replies.answered("100", "planner@proj-a", [his]) is False
    his.reply_to = "100"
    assert replies.answered("100", "planner@proj-a", [his]) is True


# -------------------------------------------------------------- the daemon reads it

def test_the_daemon_keeps_its_entries_in_the_shared_shape():
    assert daemon.SentEntry is SentEntry


def test_the_daemons_awaiting_asks_the_shared_rule(rig, monkeypatch):
    """With `answered` made to say yes, a bound name the ask tags is not awaited; with
    it saying no, it is. So `awaiting` decides by the shared function, not a copy."""
    from test_reply_enforcement import PLANNER, REVIEWER, P, R, indexed, send

    rig.shim(**REVIEWER)
    rig.shim(**PLANNER)
    ask = send(rig, P, "do it", "--posture", "once", who=REVIEWER)
    indexed(rig, ask.id)
    d = rig.daemon
    seen = []

    def yes(ask_id, asker, entries):
        seen.append((ask_id, asker))
        return True

    monkeypatch.setattr(replies, "answered", yes)
    with d._lock:
        assert d._awaiting(R, list(d.sent.get(R, ())), d.clock()) == []
    assert seen == [(ask.id, R)]
    monkeypatch.setattr(replies, "answered", lambda *a: False)
    with d._lock:
        assert d._awaiting(R, list(d.sent.get(R, ())), d.clock()) == [P]
