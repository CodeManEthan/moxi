"""The chat recall: its names in the log and records, `begin`'s keyword for a chat
recall's hurry and its bounded lock wait, and the log's read of the agents' sessions."""

from __future__ import annotations

import threading

import pytest

from moxi_memory import recall_start, recalls, records

KEY = "chat-kiosk-approver-1530"


def begin(**kw):
    fields = dict(start=recalls.CHAT, text="the kiosk approver", senders=(), settings={},
                  cwd=None, notes_dir=None, route_name=recall_start.HEADLESS,
                  spawn=lambda session_id, recall_id: 4242)
    return recall_start.begin(KEY, "claude", **{**fields, **kw})


def states():
    return [r["state"] for r in records.session_records(KEY)]


def test_the_chat_names_sit_beside_the_terminals():
    assert recalls.CHAT == "chat" and recalls.HEADLESS == "headless"
    assert recalls.FENCE == "fence"
    assert recall_start.HEADLESS == "headless"
    assert recall_start.HEADLESS not in recall_start.ROUTES       # no launch stamp names it
    assert recall_start.HEADLESS not in recall_start.BY_COMMS
    assert {"headless_session", "call"} <= set(recalls.LINE_FIELDS)
    assert {"context", "call", "headless_session"} <= set(records.RECORD_FIELDS)


def test_keep_running_hurries_only_the_oldest_past_it():
    begin(keep_running=1)
    begin(keep_running=1)
    assert states() == [records.STARTING, records.STARTING]
    begin(keep_running=1)
    assert states() == [records.HURRIED, records.STARTING, records.STARTING]


def test_the_default_hurries_every_running_recall_as_before():
    begin()
    begin()
    assert states() == [records.HURRIED, records.STARTING]


def test_a_chat_recalls_record_holds_its_context_and_call_and_nothing_else_extra():
    record = begin(chat={"context": "the approver", "call": 3, "fork": "no"})
    assert record["start"] == "chat" and record["route"] == "headless"
    assert record["context"] == "the approver" and record["call"] == 3
    assert "fork" not in record or record["fork"] is None


def test_a_terminal_record_has_no_chat_fields():
    record = begin(start=recalls.PROMPT, route_name=recall_start.SUBAGENT)
    assert "context" not in record and "call" not in record


def test_lock_s_bounds_the_wait_and_writes_nothing():
    from moxi import files

    held, release = threading.Event(), threading.Event()

    def hold():
        with recalls.session_lock(KEY):
            held.set()
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    held.wait(5)
    try:
        with pytest.raises(files.LockTimeout):
            begin(lock_s=0.2)
    finally:
        release.set()
        holder.join()
    assert records.session_records(KEY) == []


def test_headless_sessions_names_every_chat_recalls_agent():
    with recalls.session_lock(KEY):
        recalls.put_line(KEY, {"recall": "c1", "state": recalls.OPEN, "harness": "claude",
                               "headless_session": "s-1"})
        recalls.put_line(KEY, {"recall": "c2", "state": recalls.OPEN, "harness": "claude"})
    assert recalls.headless_sessions() == frozenset({("claude", "s-1")})
