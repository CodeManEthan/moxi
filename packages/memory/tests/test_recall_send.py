"""memory's recall sender: the word it declares to comms, whether a session's answers
can go back by comms, one send, and the read-back of who sent a delivery."""

import tomllib
import types
from pathlib import Path

import pytest

from moxi_memory import links, recall_send, recall_start

SESSION = "0190b000-0000-7000-8000-0000000005e0"


class FakeComms:
    """comms' interface 3 as memory reaches it: `owner`, `scoped_state`, `scoped_sender`
    and `send_scoped`."""

    def __init__(self, *, parent="builder@acme", state=None, senders=None, refusal=None,
                 owner_error=None):
        self.parent = parent
        self.state = state or types.SimpleNamespace(can_send=True,
                                                    qualified="builder-subconscious@acme",
                                                    reason=None, why=None)
        self.senders = dict(senders or {})
        self.refusal = refusal
        self.owner_error = owner_error
        self.claimed = None
        self.owners_asked = 0
        self.sent = []

    def owner(self, session_id, *, timeout_s):
        self.owners_asked += 1
        if self.owner_error:
            raise self.owner_error
        return types.SimpleNamespace(session_id=session_id, identity=self.parent)

    def caller_identity(self, env=None):
        return self.claimed

    def scoped_state(self, module, parent, *, word=None):
        assert (module, word) == ("memory", "subconscious")
        return self.state

    def scoped_sender(self, name):
        return self.senders.get(name)

    def send_scoped(self, module, parent, to, text, *, word, interrupt, expires, timeout_s):
        self.sent.append(dict(module=module, parent=parent, to=to, text=text, word=word,
                              interrupt=interrupt, expires=expires))
        if self.refusal:
            return types.SimpleNamespace(outcome="refused", message_id=None,
                                         refusal=types.SimpleNamespace(
                                             code=self.refusal, text="not now"))
        return types.SimpleNamespace(outcome="sent", message_id="m-1", refusal=None)


def sender(module="memory", word="subconscious", parent="builder@acme"):
    return types.SimpleNamespace(qualified=f"x-{word}", module=module, word=word,
                                 parent=parent)


@pytest.fixture
def comms(monkeypatch):
    fake = FakeComms()
    monkeypatch.setattr(links, "comms_scoped", lambda: links.Link(links.READY, fake))
    return fake


def test_memory_declares_its_word_to_comms_by_an_entry_point():
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    declared = pyproject["project"]["entry-points"]["moxi.comms.scoped_senders"]
    assert declared == {"subconscious": "moxi_memory.recall_send:SUBCONSCIOUS"}
    assert isinstance(recall_send.SUBCONSCIOUS, str) and "\n" not in recall_send.SUBCONSCIOUS
    assert recall_send.WORD == "subconscious"


def test_a_way_back_needs_comms_with_scoped_senders(monkeypatch):
    monkeypatch.setattr(links, "comms_scoped", lambda: links.Link(
        links.NONE, reason="comms is not installed"))
    assert recall_send.way_back(SESSION).why == recall_send.NO_COMMS
    monkeypatch.setattr(links, "comms_scoped", lambda: links.Link(
        links.NONE, reason=f"comms 0.2.0 {links.NO_INTERFACE} 3"))
    assert recall_send.way_back(SESSION).why == "comms without scoped senders"
    monkeypatch.setattr(links, "comms_scoped", lambda: links.Link(
        links.FAILING, reason="loading comms failed"))
    assert recall_send.way_back(SESSION).why == recall_send.FAILING
    assert recall_send.send(SESSION, "points").taken is False


def test_a_way_back_needs_a_comms_name(comms):
    comms.parent = None
    assert recall_send.way_back(SESSION).why == "no comms name for this session"
    assert recall_send.send(SESSION, "points") == recall_send.Sent(
        taken=False, reason="no comms name for this session")


def test_a_comms_that_cant_be_asked_is_not_told_as_a_missing_name(comms):
    comms.owner_error = OSError("daemon down")
    found = recall_send.way_back(SESSION)
    assert (found.why, found.detail) == (recall_send.FAILING, "OSError: daemon down")
    assert recall_start.comms_why(SESSION) == recall_send.FAILING
    assert recall_send.send(SESSION, "points") == recall_send.Sent(
        taken=False, reason="comms can't be asked: OSError: daemon down")


def test_a_send_after_the_binding_ended_goes_to_the_name_the_recall_started_under(comms):
    comms.parent = None
    assert recall_send.send(SESSION, "- a point", comms_name="builder@acme") == (
        recall_send.Sent(taken=True, message_id="m-1"))
    assert [(m["parent"], m["to"]) for m in comms.sent] == [("builder@acme", "builder@acme")]
    # a daemon that can't say: the start name is tried, and comms' own answer stands
    comms.owner_error = OSError("daemon down")
    assert recall_send.send(SESSION, "- a point", comms_name="builder@acme").taken


def test_a_live_binding_names_the_recipient_over_the_start_name(comms):
    comms.parent = "builder-2@acme"
    recall_send.send(SESSION, "- a point", comms_name="builder@acme")
    assert [m["to"] for m in comms.sent] == ["builder-2@acme"]


def test_the_routes_comms_check_reads_the_claim_and_asks_no_daemon(comms):
    comms.claimed = "builder@acme"
    assert recall_start.comms_why(SESSION, {"ANY": "1"}) is None
    assert comms.owners_asked == 0
    comms.claimed = None
    assert recall_start.comms_why(SESSION, {"ANY": "1"}) is None
    assert comms.owners_asked == 1


def test_a_recall_sender_comms_says_cant_send_names_the_sender(comms):
    assert recall_send.way_back(SESSION) == recall_send.WayBack(
        "builder@acme", "builder-subconscious@acme", None)
    comms.state = types.SimpleNamespace(can_send=False, qualified="builder-subconscious@acme",
                                        reason="conflict", why="a session holds the name")
    found = recall_send.way_back(SESSION)
    assert (found.why, found.name, found.detail) == (
        "recall sender in conflict", "builder-subconscious@acme", "a session holds the name")
    assert recall_start.comms_why(SESSION) == (
        "recall sender in conflict: builder-subconscious@acme")
    comms.state = types.SimpleNamespace(can_send=False, qualified=None,
                                        reason="undeclared", why="no module declares it")
    assert recall_start.comms_why(SESSION) == "recall sender not declared to comms"


def test_a_send_goes_to_the_session_alone_with_interrupt_and_no_expiry(comms):
    assert recall_send.send(SESSION, "Recalled ...:\n- a point\n") == recall_send.Sent(
        taken=True, message_id="m-1")
    assert comms.sent == [dict(module="memory", parent="builder@acme", to="builder@acme",
                               text="Recalled ...:\n- a point", word="subconscious",
                               interrupt=True, expires="never")]


def test_a_refused_send_says_why(comms):
    comms.refusal = "scoped-conflict"
    assert recall_send.send(SESSION, "points") == recall_send.Sent(
        taken=False, reason="scoped-conflict: not now")


def test_a_long_answer_is_cut_on_a_line_boundary():
    lines = [f"- point {i} " + "x" * 90 for i in range(60)]
    text = recall_send.message_text("\n".join(lines))
    assert len(text) <= recall_send.MAX_CHARS
    assert text.splitlines() == lines[:len(text.splitlines())]
    assert recall_send.message_text("y" * 5000) == "y" * recall_send.MAX_CHARS


def test_only_an_active_scoped_sender_of_this_session_is_a_recall(comms):
    comms.senders = {
        "builder-subconscious@acme": sender(),
        "other-subconscious@acme": sender(parent="other@acme"),
        "builder-digest@acme": sender(module="nightly", word="digest"),
    }
    parent = "builder@acme"
    assert recall_send.from_recalls_only(["builder-subconscious@acme"], parent)
    # a name of the form comms doesn't read back as active (held by a session first)
    assert not recall_send.from_recalls_only(["held-subconscious@acme"], parent)
    assert not recall_send.from_recalls_only(["other-subconscious@acme"], parent)
    assert not recall_send.from_recalls_only(["builder-digest@acme"], parent)
    assert not recall_send.from_recalls_only(
        ["builder-subconscious@acme", "lead@acme"], parent)
    assert not recall_send.from_recalls_only([], parent)
    assert not recall_send.from_recalls_only(["builder-subconscious@acme"], None)
    assert recall_send.recall_senders(
        ["lead@acme", "builder-subconscious@acme"], parent) == ["builder-subconscious@acme"]


def test_without_scoped_senders_no_sender_is_a_recall(monkeypatch):
    monkeypatch.setattr(links, "comms_scoped", lambda: links.Link(
        links.NONE, reason="comms is not installed"))
    assert not recall_send.from_recalls_only(["builder-subconscious@acme"], "builder@acme")
