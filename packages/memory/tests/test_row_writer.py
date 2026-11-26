"""Each row's writer: the column (schema 2), ingest filling it from sessions' `writer`,
a generation at schema 1 brought up to date by memory's next write, the rebuild's copies,
and the doctor's word on rows with no writer."""

import os
import sqlite3

import pytest
from moxi_sessions import api

from moxi_memory import doctor, index, ingest, links

T = api.testing


def config():
    return {"harnesses": ["claude", "codex", "opencode"], "index.include_subagents": False,
            "index.tail_chars": 1000, "redact.sets": ["credentials", "broad"]}


def run(mode="walk"):
    return ingest.run(mode, None, None, config=config(), launcher=links.Link(links.NONE),
                      then=lambda args: None)


def writers():
    with index.open_read() as reader:
        return {text: (kind, writer) for kind, text, writer in reader.conn.execute(
            "SELECT kind, text, writer FROM rows")}


def test_ingest_writes_each_rows_writer_from_sessions():
    t = T.ClaudeTranscript(api.root("claude"), session_id="0190b000-0000-7000-8000-00000000c1a0")
    t.user("typed at the prompt", origin="human")
    t.user("a line with no origin")
    req = t.request()
    req.tool_use("Read", {"file_path": "/work/project/widget.py"})
    t.assistant("the model answers")
    t.write()
    r = T.CodexRollout(api.root("codex"))
    r.turn()
    r.user("a codex user message")
    r.assistant("a codex answer")
    r.write()
    db = T.OpenCodeDb(os.path.dirname(api.root("opencode")))
    s = db.session()
    s.user("an opencode user message")
    a = s.assistant(agent="compaction")
    a.text("an opencode compaction summary")
    a.complete()
    db.write()

    run()
    got = writers()
    assert got["typed at the prompt"] == ("user", "person")
    assert got["a line with no origin"] == ("user", "unknown")
    assert got["the model answers"] == ("assistant", "model")
    assert got["a codex user message"] == ("user", "unknown")
    assert got["a codex answer"] == ("assistant", "model")
    assert got["an opencode user message"] == ("user", "unknown")
    assert got["an opencode compaction summary"] == ("assistant", "summary")
    tool = [w for text, w in got.items() if text.startswith("Read(")]
    assert tool == [("tool", "model")]


def test_mail_from_other_sessions_stores_rows_named_by_who_delivered_it():
    t = T.ClaudeTranscript(api.root("claude"), session_id="0190b000-0000-7000-8000-00000000c1a1")
    t.user("typed by the person", origin="human")
    t.user("mail from a peer", origin="peer")
    t.user("mail from the coordinator", origin="coordinator")
    t.user("a channel message", origin="channel")
    t.user("a plugin sent this", origin="plugin")
    t.user("a line the harness composed", isMeta=True)
    t.write()
    run()
    got = writers()
    assert got == {"typed by the person": ("user", "person"),
                   "mail from a peer": ("user", "peer"),
                   "mail from the coordinator": ("user", "coordinator"),
                   "a channel message": ("user", "channel"),
                   "a plugin sent this": ("user", "hook")}


def test_a_row_read_again_takes_its_writer():
    t = T.ClaudeTranscript(api.root("claude"), session_id="0190b000-0000-7000-8000-00000000c1a2")
    t.user("typed at the prompt", origin="human")
    t.write()
    run()
    with index.writer() as w:
        conn = w.connect()
        with conn:
            conn.execute("UPDATE rows SET writer = NULL")
        assert index.rows_without_writer(conn) == 1
        conn.close()
    run("all")
    assert writers()["typed at the prompt"] == ("user", "person")
    with index.open_read() as reader:
        assert index.rows_without_writer(reader.conn) == 0


def _schema_one_generation():
    """A generation made at schema 1 with one row, named by the pointer, as memory
    before the column left it."""
    from moxi import files, store

    index.index_dir().mkdir(parents=True, exist_ok=True)
    with files.lock(index.lock_path(1), timeout_s=0):
        pass
    conn = store.connect(index.db_path(1), create=True)
    store.migrate(conn, index.SCHEMA[:1])
    with conn:
        conn.execute("INSERT INTO rows (key, harness, session_id, kind, text, time) "
                     "VALUES ('claude|k|0', 'claude', 's', 'user', 'an old row', 1.0)")
        conn.execute("UPDATE meta SET value = '1' WHERE key = 'first_read'")
    conn.close()
    files.atomic_write(index.pointer_path(), "index-1.db\n", mode=0o600)


def test_a_schema_one_generation_reads_and_is_brought_up_to_date_by_the_next_write():
    _schema_one_generation()
    with index.open_read() as reader:
        assert index.schema_version(reader.conn) == 1
        assert not index.has_writer(reader.conn)
        assert index.rows_without_writer(reader.conn) is None
    row = {r.name: r for r in doctor.rows()}["index"]
    assert "record no writer until memory next writes" in row.detail
    with index.writer() as w:
        w.connect().close()
    with index.open_read() as reader:
        assert index.schema_version(reader.conn) == index.WRITER_SCHEMA
        assert index.meta_get(reader.conn, "schema") == "2"
        assert reader.conn.execute("SELECT text, writer FROM rows").fetchall() == [
            ("an old row", None)]
        assert index.rows_without_writer(reader.conn) == 1
    row = {r.name: r for r in doctor.rows()}["index"]
    assert "1 row stored before rows recorded their writer" in row.detail
    assert "moxi memory reindex" in row.detail


def test_a_copy_from_a_schema_one_generation_selects_no_writer():
    _schema_one_generation()
    with index.open_read() as reader:
        assert ingest._writer_column(reader.conn) == "NULL"
        assert reader.conn.execute(
            f"SELECT {ingest._writer_column(reader.conn)} FROM rows").fetchall() == [(None,)]
    with index.writer() as w:
        w.connect().close()
    with index.open_read() as reader:
        assert ingest._writer_column(reader.conn) == "writer"


def test_a_rebuild_copy_keeps_the_writer_a_row_was_stored_with(monkeypatch):
    t = T.ClaudeTranscript(api.root("claude"), session_id="0190b000-0000-7000-8000-00000000c1a3")
    t.user("kept across the rebuild", origin="human")
    path = t.write()
    run()
    os.remove(path)                    # the source is gone: a version rebuild keeps it
    import moxi_secrets

    monkeypatch.setattr(moxi_secrets, "REDACT_VERSION",
                        getattr(moxi_secrets, "REDACT_VERSION", 1) + 1, raising=False)
    run("rebuild")
    assert writers()["kept across the rebuild"] == ("user", "person")


def test_ingest_refuses_a_sessions_without_the_writer_interface(monkeypatch):
    from moxi import modules

    real = modules.load

    def load(name, *, interface=None):
        found = real(name, interface=interface)
        if name == "sessions":
            return found._replace(state="broken", api=None, reason="no interface 7")
        return found

    monkeypatch.setattr(modules, "load", load)
    with pytest.raises(RuntimeError, match="interface 7"):
        ingest._api()


def test_the_schema_step_is_plain_sql_on_a_fresh_connection():
    conn = sqlite3.connect(":memory:")
    for step in index.SCHEMA:
        conn.executescript(step)
    assert index.has_writer(conn)
    assert index.rows_without_writer(conn) == 0
