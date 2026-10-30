"""OpenCode's database into the one shape: the read-only connection and its authorizer, the
shape check, the message and part walk, one snapshot per read, child sessions, the
backward reads and the forward cursor.

Every database here is built by `moxi_sessions.testing.OpenCodeDb` under the test's tmp.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3

import pytest

from moxi_sessions import opencode, reader
from moxi_sessions.opencode import Cursor, MissingSession, Snapshot, Unreadable, since
from moxi_sessions.shape import UNKNOWN, Malformed, Marker, Message, Other, Request
from moxi_sessions.testing import OpenCodeDb
from moxi_sessions.testing.opencode import OpenCodeAssistant


@pytest.fixture
def db(tmp_path) -> OpenCodeDb:
    return OpenCodeDb(tmp_path / "opencode")


def one_turn(db: OpenCodeDb, *, finish_second: bool = True):
    """A session: a user prompt, and an assistant message with two steps, the first with
    a thought and a tool call, the second finished only when asked."""
    s = db.session(title="fix the tests")
    user = s.user("run the tests")
    a = s.assistant()
    first = a.step_start()
    a.reasoning("which suite?")
    a.tool("bash", {"command": "pytest -q"}, output="3 passed", call_id="call_1")
    a.step_finish(input=900, output=40, reasoning=3, cache_read=1200, cache_write=7,
                  cost=0.25)
    second = a.step_start()
    a.text("all green")
    if finish_second:
        a.step_finish(input=5, output=6, cache_read=2100)
        a.complete()
    return s, user, a, first, second


def read(path, session_id, **kw):
    with Snapshot(path) as snap:
        return list(snap.records(session_id, **kw))


# --- the connection and its authorizer ------------------------------------------------

def test_a_missing_database_is_unreadable_not_empty(tmp_path):
    with pytest.raises(Unreadable) as caught:
        Snapshot(tmp_path / "nowhere" / "opencode.db")
    assert isinstance(caught.value, OSError)
    assert "missing" in caught.value.reason


def test_a_file_that_is_not_a_database_is_unreadable(tmp_path):
    path = tmp_path / "opencode.db"
    path.write_bytes(b"this is not a database at all" * 100)
    with pytest.raises(Unreadable):
        with Snapshot(path) as snap:
            snap.session("ses_x")


def test_the_authorizer_refuses_everything_off_its_list(db):
    s, *_ = one_turn(db)
    conn = opencode.connect(db.write())
    try:
        refused = [
            "SELECT * FROM account", "SELECT secret FROM credential",
            "PRAGMA table_info(credential)", "PRAGMA journal_mode",
            "SELECT json_extract(data, '$.role') FROM message",
            "SELECT max(time_updated) FROM message",
            "INSERT INTO session (id) VALUES ('x')", "DELETE FROM message",
            "UPDATE part SET data = '{}'", "CREATE TEMP TABLE t (a)",
            "ATTACH DATABASE ':memory:' AS other", "SAVEPOINT a",
        ]
        for sql in refused:
            with pytest.raises(sqlite3.DatabaseError):
                conn.execute(sql).fetchall()
        conn.execute("BEGIN")
        assert conn.execute("SELECT id FROM session").fetchall() == [(s.id,)]
        assert conn.execute("SELECT count(*) FROM message").fetchall() == [(2,)]
        conn.execute("COMMIT")
    finally:
        conn.close()


def test_the_readers_real_statements_all_run_under_its_authorizer(db, monkeypatch):
    # The rule: every statement the reader runs is one the test kit has run under the
    # authorizer. Every read path runs here with the authorizer recording each decision;
    # none may be a denial, and every statement is traced.
    parent, _user, a, *_ = one_turn(db)
    child = parent.child(title="look around")
    child.user("look")
    a.tool("task", {"prompt": "look"}, metadata={"sessionId": child.id}, call_id="c2")
    path = db.write()

    decisions, statements = [], []
    real_authorize, real_connect = opencode.authorize, opencode.connect

    def recording(*args):
        verdict = real_authorize(*args)
        decisions.append((args[:3], verdict))
        return verdict

    def traced(p):
        conn = real_connect(p)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(opencode, "authorize", recording)
    monkeypatch.setattr(opencode, "connect", traced)

    with Snapshot(path) as snap:
        snap.require(*opencode.TABLES)
        snap.has_session(parent.id)
        snap.session(parent.id)
        snap.sessions(updated_since=0)
        snap.children(parent.id)
        snap.children(child.id)
        list(snap.records(child.id))
        snap.records_back(parent.id)
        snap.last_request(parent.id)
    first = since(path)
    since(path, first.cursor, session_id=None)
    scoped = since(path, session_id=parent.id)
    a.update_part(a.text("more"), {"text": "more, changed"})
    db.write()
    since(path, scoped.cursor, session_id=parent.id)
    since(path, first.cursor)

    assert decisions and statements
    assert [d for d in decisions if d[1] != sqlite3.SQLITE_OK] == []
    words = {sql.split()[0].upper() for sql in statements}
    assert words == {"BEGIN", "COMMIT", "SELECT", "PRAGMA"}
    assert any("count(*)" in sql for sql in statements)
    assert any("NOT IN" in sql for sql in statements)
    assert any("DISTINCT message_id" in sql for sql in statements)


# --- the shape check ------------------------------------------------------------------

def test_a_missing_column_is_named(tmp_path):
    path = tmp_path / "opencode.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE session (id text, parent_id text);"
        "CREATE TABLE message (id text, session_id text, time_created integer, data text);")
    conn.close()
    with Snapshot(path) as snap:
        with pytest.raises(Unreadable, match="`session` table has no `title` column"):
            snap.session("ses_x")
        with pytest.raises(Unreadable, match="`message` table has no `time_updated`"):
            list(snap.records("ses_x"))
    with Snapshot(path) as snap:
        with pytest.raises(Unreadable, match="has no `part` table"):
            snap.require("part")


def test_the_builder_writes_every_column_the_reader_reads(db):
    db.session()
    with Snapshot(db.write()) as snap:
        snap.require(*opencode.TABLES)


# --- the session row ------------------------------------------------------------------

def test_the_session_row_is_the_header(db):
    s, *_ = one_turn(db)
    s.update(revert='{"messageID":"msg_x"}')
    with Snapshot(db.write()) as snap:
        row = snap.session(s.id)
    assert row.id == s.id and row.parent_id is None
    assert row.title == "fix the tests" and row.directory == "/work/project"
    assert row.version == "1.18.34" and row.agent == "build"
    assert row.model == {"id": "big-pickle", "providerID": "opencode",
                         "variant": "default"}
    assert row.revert_pending is True
    assert (row.tokens_input, row.tokens_output, row.tokens_reasoning,
            row.tokens_cache_read, row.tokens_cache_write) == (905, 46, 3, 3300, 7)
    assert row.cost == 0.25
    assert row.time_created == opencode_seconds(db, "session", s.id, "time_created")
    assert row.time_compacting is None


def opencode_seconds(db, table, rid, column):
    return db.rows[table][rid][column] / 1000


def test_a_session_model_that_is_not_json_is_unknown_and_a_null_is_none(db):
    bad = db.session(raw_model="not json")
    none = db.session(model=None)
    with Snapshot(db.write()) as snap:
        assert snap.session(bad.id).model is UNKNOWN
        assert snap.session(none.id).model is None
        assert snap.session(none.id).revert_pending is False


def test_a_session_with_no_row_is_missing(db):
    db.session()
    with Snapshot(db.write()) as snap:
        assert snap.has_session("ses_nothere") is False
        with pytest.raises(MissingSession) as caught:
            snap.session("ses_nothere")
    assert isinstance(caught.value, Unreadable)


def test_sessions_lists_every_row_or_those_changed_since(db):
    old = db.session(at=1_000)
    new = db.session(at=2_000)
    with Snapshot(db.write()) as snap:
        assert [r.id for r in snap.sessions()] == [old.id, new.id]
        assert [r.id for r in snap.sessions(updated_since=1_500)] == [new.id]


# --- the message and part walk --------------------------------------------------------

def test_a_turn_maps_into_the_shape(db):
    s, user, a, first, second = one_turn(db)
    records = read(db.write(), s.id)
    assert [r.kind for r in records] == ["message", "message", "message", "tool_call",
                                         "tool_result", "request", "request"]
    prompt, reply, thought, call, result, step1, step2 = records

    assert prompt.role == "user" and prompt.text == "run the tests"
    assert prompt.model == {"providerID": "opencode", "modelID": "big-pickle"}
    assert prompt.content_kinds == ("text",) and prompt.turn_id == user.id
    assert prompt.at == {"table": "message", "id": user.id}
    assert prompt.raw_kind == "message/user" and prompt.extra["agent"] == "build"

    assert reply.role == "assistant" and reply.text == "all green"
    assert reply.turn_id == user.id and reply.model is UNKNOWN
    assert reply.content_kinds == ("step-start", "reasoning", "tool", "step-finish",
                                   "step-start", "text", "step-finish")
    assert reply.extra["finish"] == "stop" and reply.extra["mode"] == "build"

    assert thought.reasoning is True and thought.text == "which suite?"
    assert call.name == "bash" and call.call_id == "call_1"
    assert call.input == {"command": "pytest -q"} and call.summary == "bash(pytest -q)"
    assert result.call_id == "call_1" and result.output == "3 passed"
    assert result.is_error is False and result.size_bytes == 8

    assert step1.request_id == first and step2.request_id == second
    assert step1.final is True and step1.sidechain is False and step1.synthetic is False
    assert (step1.model, step1.provider) == ("big-pickle", "opencode")
    assert step1.input_tokens == 900 + 1200 + 7
    assert step1.cache_read_input_tokens == 1200
    assert step1.cache_creation_input_tokens == 7
    assert step1.output_tokens == 43 and step1.reasoning_output_tokens == 3
    assert step1.cost_usd == 0.25
    assert step1.cache_creation_5m_input_tokens is UNKNOWN
    assert step1.context_window is UNKNOWN and step1.response_model is UNKNOWN
    assert step1.start_time == opencode_seconds(db, "part", first, "time_created")
    assert step1.extra["message_time"] == opencode_seconds(db, "message", a.id,
                                                           "time_created")
    assert step1.extra["reason"] == "stop" and step1.raw_kind == "part/step-finish"
    assert step1.at["table"] == "part" and step1.at["id"] != first
    assert all(r.session_id == s.id and r.agent_id is None and r.harness == "opencode"
               for r in records)


def test_an_open_step_is_not_final_and_keeps_its_id_once_it_finishes(db):
    s, _user, a, _first, second = one_turn(db, finish_second=False)
    path = db.write()
    open_ = [r for r in read(path, s.id) if r.kind == "request"][-1]
    assert open_.request_id == second and open_.final is False
    assert open_.input_tokens is UNKNOWN and open_.output_tokens is UNKNOWN
    assert open_.cost_usd is UNKNOWN and open_.time is UNKNOWN
    assert open_.start_time == opencode_seconds(db, "part", second, "time_created")
    a.step_finish(input=1, output=2)
    db.write()
    done = [r for r in read(path, s.id) if r.kind == "request"][-1]
    assert done.request_id == second and done.final is True and done.input_tokens == 1


def test_a_step_with_no_start_takes_its_finish_parts_id(db):
    s = db.session()
    s.user()
    a = s.assistant()
    finish = a.step_finish(input=3)
    path = db.write()
    (request,) = [r for r in read(path, s.id) if r.kind == "request"]
    assert request.request_id == finish and request.start_time is UNKNOWN
    assert request.final is True


def test_a_step_started_twice_leaves_the_first_open(db):
    s = db.session()
    s.user()
    a = s.assistant()
    first = a.step_start()
    second = a.step_start()
    a.step_finish(input=3)
    requests = [r for r in read(db.write(), s.id) if r.kind == "request"]
    assert [(r.request_id, r.final) for r in requests] == [(first, False), (second, True)]


def test_tool_states(db):
    s = db.session()
    s.user()
    a = s.assistant()
    a.tool("read", {"filePath": "/x"}, status="error", error="no such file", call_id="e")
    a.tool("bash", {"command": "sleep 9"}, status="running", call_id="r")
    a.tool("bash", {}, status="pending", call_id="p")
    records = read(db.write(), s.id)
    calls = [r.call_id for r in records if r.kind == "tool_call"]
    results = [r for r in records if r.kind == "tool_result"]
    assert calls == ["e", "r", "p"]
    assert [(r.call_id, r.output, r.is_error) for r in results] == [
        ("e", "no such file", True)]


def test_compaction_gives_a_marker(db):
    s = db.session()
    s.user()
    by_agent = s.assistant(agent="compaction")
    by_agent.step(input=1)
    summary = s.assistant(summary=True)
    summary.step(input=1)
    records = read(db.write(), s.id)
    markers = [r for r in records if isinstance(r, Marker)]
    assert [m.at["id"] for m in markers] == [by_agent.id, summary.id]
    assert all(m.marker == "compaction" and m.trigger is UNKNOWN for m in markers)
    flagged = [r for r in records if r.kind == "message" and r.at["id"] == summary.id]
    assert flagged[0].flags == {"summary": True}


def test_other_parts_and_roles(db):
    s = db.session()
    m = s.user()
    m.part({"type": "file", "mime": "text/plain", "url": "file:///x"})
    m.part({"type": "hologram"})
    s.message({"role": "narrator"})
    records = read(db.write(), s.id)
    others = [r for r in records if isinstance(r, Other)]
    assert [(o.raw_kind, o.recognised) for o in others] == [
        ("part/file", True), ("part/hologram", False), ("message/narrator", False)]
    assert others[0].extra == {"mime": "text/plain", "url": "file:///x"}
    assert records[0].content_kinds == ("text", "file", "hologram")


def test_rows_that_cannot_be_read_are_malformed_never_dropped(db):
    s = db.session()
    s.message("{not json")
    s.message("[1, 2]")
    s.message({"text": "no role"})
    m = s.user()
    m.part("{broken")
    m.part({"no": "type"})
    a = s.assistant()
    a.part({"type": "tool", "tool": "bash", "state": "running"})
    records = read(db.write(), s.id)
    malformed = [r for r in records if isinstance(r, Malformed)]
    assert [(r.at["table"], r.why) for r in malformed] == [
        ("message", "undecodable"), ("message", "not_object"),
        ("message", "bad_structure"), ("part", "undecodable"), ("part", "bad_structure"),
        ("part", "bad_structure")]
    assert malformed[0].raw == b"{not json"


def test_order_is_messages_by_time_then_id_and_parts_by_id(db):
    s = db.session()
    late = s.user("late", at=2_000)
    early = s.user("early", at=1_000)
    first_part = early.text("a", at=1_500)
    second_part = early.text("b", at=1_100)
    records = read(db.write(), s.id)
    assert [r.at["id"] for r in records] == [early.id, late.id]
    assert records[0].text == "early\na\nb"
    assert first_part < second_part


def test_keep_raw_gives_the_row_with_its_data_decoded(db):
    s = db.session()
    m = s.user("hi")
    (record,) = read(db.write(), s.id, keep_raw=True)
    assert record.raw["id"] == m.id and record.raw["data"]["role"] == "user"
    (plain,) = read(db.path, s.id)
    assert plain.raw is None


def test_kinds_filters_records(db):
    s, *_ = one_turn(db)
    records = read(db.write(), s.id, kinds={"request"})
    assert [r.kind for r in records] == ["request", "request"]


# --- one snapshot ---------------------------------------------------------------------

def test_a_reads_header_and_records_come_from_one_snapshot(db):
    s = db.session()
    s.user("one")
    path = db.write()
    with Snapshot(path) as snap:
        before = snap.session(s.id)
        s.user("two")
        s.update(title="renamed")
        db.write()
        records = list(snap.records(s.id))
        assert snap.session(s.id).title == before.title == "a session"
    assert [r.text for r in records] == ["one"]
    assert [r.text for r in read(path, s.id)] == ["one", "two"]


# --- child sessions -------------------------------------------------------------------

def test_child_sessions_are_the_subagents(db):
    parent = db.session()
    parent.user("look into it")
    a = parent.assistant()
    child = parent.child(title="explore the repo", agent="explore")
    grandchild = child.child(title="read one file")
    a.tool("task", {"description": "explore"}, metadata={"sessionId": child.id},
           call_id="call_task")
    child.user("explore")
    child.assistant().step(input=4)
    path = db.write()
    with Snapshot(path) as snap:
        (entry,) = snap.children(parent.id)
        (deeper,) = snap.children(child.id)
        assert snap.children(grandchild.id) == []
        records = list(snap.records(child.id))
        own = snap.last_request(child.id)
    assert entry.agent_id == entry.session_id == child.id
    assert entry.agent_type == "explore" and entry.description == "explore the repo"
    assert entry.tool_use_id == "call_task"
    assert entry.spawn_depth == 1 and entry.parent_agent_id is None
    assert deeper.agent_id == grandchild.id and deeper.spawn_depth == 2
    assert deeper.parent_agent_id == child.id and deeper.tool_use_id is None
    assert records and all(r.agent_id == child.id and r.session_id == child.id
                           for r in records)
    assert own.agent_id == child.id and own.input_tokens == 4


# --- the backward reads ---------------------------------------------------------------

def test_records_back_is_the_exact_reverse_and_ends_at_the_start(db):
    s, *_ = one_turn(db)
    path = db.write()
    with Snapshot(path) as snap:
        back = snap.records_back(s.id)
        requests = snap.records_back(s.id, kinds={"request"})
    assert back == list(reversed(read(path, s.id)))
    assert [r.kind for r in requests] == ["request", "request"]
    assert opencode.BACK_ENDED == reader.START


def test_last_request_is_the_newest_finished_step_an_open_one_left_out(db):
    s, _user, a, first, second = one_turn(db, finish_second=False)
    path = db.write()
    with Snapshot(path) as snap:
        last = snap.last_request(s.id)
        back = snap.records_back(s.id)
    assert last.request_id == first and last.final is True
    assert back[0].kind == "request" and back[0].request_id == second
    assert back[0].final is False
    a.step_finish(input=11)
    s.user("next")
    db.write()
    with Snapshot(path) as snap:
        assert snap.last_request(s.id).request_id == second


def test_last_request_on_a_session_with_none_is_none(db):
    s = db.session()
    s.user()
    s.assistant().step_start()
    with Snapshot(db.write()) as snap:
        assert snap.last_request(s.id) is None


# --- the forward cursor ---------------------------------------------------------------

def ids(batch):
    return [r.at["id"] for r in batch.records
            if r.kind == "message" and r.at["table"] == "message"]


def test_a_first_read_takes_every_message_and_an_unchanged_database_is_not_read(db):
    s, user, a, *_ = one_turn(db)
    path = db.write()
    first = since(path)
    assert ids(first) == [user.id, a.id] and first.read and not first.reset
    assert first.gone == 0 and first.cursor.m == a.id and first.cursor.count == 2
    settled = since(path, first.cursor)      # the reader's own -wal may appear once
    quiet = since(path, settled.cursor)
    assert quiet.read is False and quiet.records == () and quiet.cursor == settled.cursor


def test_a_changed_part_reads_its_whole_message_again_and_nothing_else(db):
    s, user, a, first, second = one_turn(db, finish_second=False)
    path = db.write()
    cursor = since(path).cursor
    a.step_finish(input=8)
    db.write()
    batch = since(path, cursor)
    assert ids(batch) == [a.id]
    requests = [r for r in batch.records if r.kind == "request"]
    assert [(r.request_id, r.final) for r in requests] == [(first, True), (second, True)]


def test_a_part_changed_without_its_message_still_brings_the_message_back(db):
    s, user, a, *_ = one_turn(db)
    tool = a.tool("bash", {"command": "make"}, status="running", call_id="late")
    path = db.write()
    cursor = since(path).cursor
    a.update_part(tool, {"state": {"status": "completed", "input": {"command": "make"},
                                   "output": "built"}})
    db.write()
    batch = since(path, cursor)
    assert ids(batch) == [a.id]
    assert [r.output for r in batch.records if r.kind == "tool_result"][-1] == "built"


def test_a_cursor_read_across_a_deleted_tail_and_new_rows_misses_none(db):
    # SQLite hands a deleted tail's rowids out again; the cursor keys on ids and stamps,
    # so the new rows are read and the deleted ones are counted as gone.
    s = db.session()
    kept = s.user("kept")
    tail = [s.user(f"tail {n}") for n in range(3)]
    path = db.write()
    cursor = since(path).cursor
    assert cursor.count == 4
    for message in tail:
        db.delete(message.id)
    db.write()
    fresh = [s.user(f"new {n}") for n in range(2)]
    db.write()
    batch = since(path, cursor)
    assert ids(batch) == [m.id for m in fresh]
    assert batch.gone == 3 and batch.cursor.count == 3
    assert kept.id not in ids(batch)
    again = since(path, batch.cursor)
    assert ids(again) == [] and again.gone == 0


def test_a_new_message_stamped_before_the_cursor_is_read_by_its_id(db):
    s = db.session()
    s.user("first", at=5_000)
    path = db.write()
    cursor = since(path).cursor
    stepped_back = s.user("written by a clock stepped back", at=1_000)
    db.write()
    assert ids(since(path, cursor)) == [stepped_back.id]


def test_a_row_stamped_exactly_the_cursors_time_is_read_once(db):
    s = db.session()
    one = s.user("one", at=3_000)
    path = db.write()
    cursor = since(path).cursor
    old = s.user("an old id", at=1_000)
    db.write()
    cursor = since(path, cursor).cursor
    # a part stamped exactly the cursor's newest time, on the older-id message
    old.text("same moment", at=3_000)
    db.write()
    batch = since(path, cursor)
    assert ids(batch) == [old.id]
    assert one.id not in ids(batch)
    assert ids(since(path, batch.cursor)) == []


def test_a_cursor_for_another_file_or_session_resets(db, tmp_path):
    a = db.session()
    a.user("a")
    b = db.session()
    b.user("b")
    path = db.write()
    cursor = since(path, session_id=a.id).cursor
    other = since(path, cursor, session_id=b.id)
    assert other.reset is True and [r.text for r in other.records] == ["b"]
    copy = OpenCodeDb(tmp_path / "elsewhere")
    copy.session()
    moved = since(copy.write(), cursor, session_id=a.id)
    assert moved.reset is True


def test_a_session_scoped_read_reads_only_that_session_and_its_row(db):
    a = db.session(title="mine")
    a.user("mine")
    b = db.session()
    b.user("theirs")
    path = db.write()
    batch = since(path, session_id=a.id)
    assert [r.text for r in batch.records] == ["mine"]
    assert batch.session.title == "mine" and batch.cursor.session == a.id
    b.user("theirs again")
    db.write()
    assert since(path, batch.cursor, session_id=a.id).records == ()
    db.delete_session(a.id)
    db.write()
    gone = since(path, batch.cursor, session_id=a.id)
    assert gone.session is None and gone.gone == 1


def test_records_of_a_child_session_carry_its_id_in_a_database_read(db):
    parent = db.session()
    child = parent.child()
    parent.user("p")
    child.user("c")
    batch = since(db.write())
    agents = {r.session_id: r.agent_id for r in batch.records}
    assert agents == {parent.id: None, child.id: child.id}


def test_the_cursor_round_trips_and_a_bad_one_reads_as_none(db):
    db.session().user()
    cursor = since(db.write()).cursor
    assert Cursor.from_dict(cursor.to_dict()) == cursor
    data = cursor.to_dict()
    for broken in ({**data, "t": "soon"}, {k: v for k, v in data.items() if k != "m"},
                   {**data, "wal": [1]}, {**data, "t_parts": [3]}, [], None):
        assert Cursor.from_dict(broken) is None


# --- the builder ----------------------------------------------------------------------

def test_the_builder_writes_in_one_transaction_with_synchronous_off(db, monkeypatch):
    s = db.session()
    for n in range(50):
        s.user(f"prompt {n}")
        assert isinstance(s.assistant(), OpenCodeAssistant)
    statements = []
    real = sqlite3.connect

    def traced(*args, **kw):
        conn = real(*args, **kw)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(sqlite3, "connect", traced)
    db.write()
    writes = [sql for sql in statements if sql.startswith("INSERT INTO message")]
    begin = statements.index("BEGIN")
    assert "PRAGMA synchronous=OFF" in statements[:begin]
    assert len(writes) == 100 and statements.count("COMMIT") == 1
    assert statements.index("COMMIT") > statements.index(writes[-1])
    with contextlib.closing(real(db.path)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    assert os.path.getsize(db.path) > 0
