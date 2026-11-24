"""The index: its schema and tokenizer, the generations behind the pointer, the readers'
shared locks, the writer lock, the rebuild lock and its entry, and the tidy a child makes
when it takes the writer lock."""

import os
import subprocess
import sys
import textwrap
import time

import pytest
from moxi import files

from moxi_memory import index, state

pytestmark = pytest.mark.skipif(sys.platform == "win32",
                                reason="Windows: memory's index locks with fcntl, so memory doesn't run there")

# -- helpers --------------------------------------------------------------------------


class Holder:
    """A child process that runs `code`, prints `ready`, and then waits until it is told
    to go (or, with `crash`, ends at once without cleaning up)."""

    def __init__(self, code: str):
        body = textwrap.indent(textwrap.dedent(code), "    ")
        script = ("import os, sys\n"
                  "from moxi_memory import index, state\n"
                  "def main():\n"
                  f"{body}\n"
                  "main()\n")
        self.proc = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True)
        line = self.proc.stdout.readline().strip()
        if line != "ready":
            self.proc.kill()
            raise AssertionError(f"holder said {line!r}: {self.proc.stderr.read()}")

    def release(self) -> int:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        return self.proc.wait(timeout=30)


WAIT = "print('ready', flush=True)\nsys.stdin.read()\n"


def add_row(conn, key, text, *, session="s1"):
    conn.execute("INSERT INTO rows (key, harness, session_id, kind, text) "
                 "VALUES (?, 'claude', ?, 'user', ?)", (key, session, text))


def ingest_once(text="hello there", key="k1"):
    with index.writer() as w:
        conn = w.connect()
        try:
            add_row(conn, key, text)
        finally:
            conn.close()


def rebuild(texts):
    """A whole rebuild in this process: a new generation with `texts`, then the switch."""
    with index.writer() as w:
        with w.rebuilding():
            n, conn = w.new_generation()
            try:
                for i, text in enumerate(texts):
                    add_row(conn, f"r{i}", text)
            finally:
                conn.close()
            removed = w.switch(n)
    return n, removed


# -- the first generation, and the reader with none -----------------------------------

def test_a_reader_with_no_index_gets_index_missing_and_makes_nothing():
    with pytest.raises(index.IndexMissing):
        index.open_read()
    assert not index.index_dir().exists() or not os.listdir(index.index_dir())


def test_the_first_child_makes_generation_one_and_points_at_it_before_reading():
    with index.writer() as w:
        assert w.made_first and w.generation == 1
        assert index.current() == 1
        assert index.pointer_path().read_text() == "index-1.db\n"
        with index.open_read() as reader:  # a search now finds a filling index
            assert reader.generation == 1
            assert index.meta_get(reader.conn, "first_read") == "0"
            assert reader.conn.execute("SELECT count(*) FROM rows").fetchone() == (0,)
    with index.writer() as w:
        assert not w.made_first and w.generation == 1
    assert index.generations() == [1]


def test_a_generation_holds_the_tables_and_the_tokenizer():
    ingest_once()
    with index.open_read() as reader:
        names = {r[0] for r in reader.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"rows", "rows_fts", "tails", "tails_fts", "sources", "meta"} <= names
        assert index.meta_get(reader.conn, "tokenizer") == \
            "porter unicode61 remove_diacritics 2"
        assert index.meta_get(reader.conn, "schema") == "2"
        sql = " ".join(r[0] for r in reader.conn.execute(
            "SELECT sql FROM sqlite_master WHERE name IN ('rows_fts', 'tails_fts')"))
        assert sql.count("tokenize = 'porter unicode61 remove_diacritics 2'") == 2
        assert "tokenchars" not in sql


def test_a_reader_cannot_write():
    ingest_once()
    import sqlite3

    with index.open_read() as reader:
        with pytest.raises(sqlite3.OperationalError):
            add_row(reader.conn, "k2", "more")


def test_meta_values_are_set_and_read_as_text():
    with index.writer() as w:
        conn = w.connect()
        index.meta_set(conn, "first_read", 1)
        index.meta_set(conn, "dropped", 3)
        assert index.meta_get(conn, "first_read") == "1"
        assert index.meta_get(conn, "nothing", "x") == "x"
        conn.close()


# -- stemming -------------------------------------------------------------------------

@pytest.mark.parametrize("query", ["run", "runs", "running"])
def test_a_stemmed_query_matches_its_other_forms(query):
    with index.writer() as w:
        conn = w.connect()
        add_row(conn, "a", "the runner was running all the tests")
        add_row(conn, "b", "it runs nightly")
        add_row(conn, "c", "nothing to see here")
        conn.close()
    with index.open_read() as reader:
        keys = {r[0] for r in reader.conn.execute(
            "SELECT rows.key FROM rows_fts JOIN rows ON rows.id = rows_fts.rowid "
            "WHERE rows_fts MATCH ?", (f'"{query}"',))}
    assert keys == {"a", "b"}


def test_diacritics_are_folded_and_tails_stem_too():
    with index.writer() as w:
        conn = w.connect()
        add_row(conn, "a", "a café résumé")
        conn.execute("INSERT INTO tails (key, harness, session_id, failed, text) "
                     "VALUES ('t', 'claude', 's1', 1, 'tests failed while compiling')")
        conn.close()
    with index.open_read() as reader:
        hit = reader.conn.execute("SELECT rowid FROM rows_fts WHERE rows_fts MATCH "
                                  "'\"resume\" AND \"cafe\"'").fetchall()
        tail = reader.conn.execute("SELECT rowid FROM tails_fts WHERE tails_fts MATCH "
                                   "'\"compile\" AND \"fail\"'").fetchall()
    assert len(hit) == 1 and len(tail) == 1


def test_a_replaced_row_is_found_by_its_new_text_only():
    with index.writer() as w:
        conn = w.connect()
        add_row(conn, "a", "first words")
        conn.execute("UPDATE rows SET text = 'second thoughts' WHERE key = 'a'")
        conn.close()
    with index.open_read() as reader:
        def count(q):
            return reader.conn.execute("SELECT count(*) FROM rows_fts WHERE rows_fts "
                                       "MATCH ?", (q,)).fetchone()[0]
        assert count('"first"') == 0 and count('"thought"') == 1


# -- generations and the pointer ------------------------------------------------------

def test_a_rebuild_switches_the_pointer_and_removes_the_old_generation():
    ingest_once("old text")
    n, removed = rebuild(["new text"])
    assert (n, removed) == (2, [1])
    assert index.current() == 2 and index.generations() == [2]
    assert not index.db_path(1).exists() and not index.lock_path(1).exists()
    with index.open_read() as reader:
        assert reader.generation == 2
        assert [r[0] for r in reader.conn.execute("SELECT text FROM rows")] == ["new text"]


def test_a_search_that_opened_the_old_generation_reads_it_to_the_end():
    ingest_once("old text")
    reader = index.open_read()
    try:
        cursor = reader.conn.execute("SELECT text FROM rows")
        rebuild(["new text"])
        assert [r[0] for r in cursor] == ["old text"]
        assert reader.conn.execute("SELECT count(*) FROM rows").fetchone() == (1,)
    finally:
        reader.close()


def test_an_old_generation_a_reader_holds_is_left_and_the_next_child_removes_it():
    ingest_once("old text")
    reader = index.open_read()
    try:
        n, removed = rebuild(["new text"])
        assert removed == [] and index.generations() == [1, 2]
        assert index.db_path(1).exists()
        assert reader.conn.execute("SELECT text FROM rows").fetchall() == [("old text",)]
        ingest_once("more", key="k2")  # the reader still holds it
        assert index.generations() == [1, 2]
    finally:
        reader.close()
    ingest_once("more again", key="k3")
    assert index.generations() == [2]
    assert not index.db_path(1).exists() and not index.lock_path(1).exists()


def test_an_old_generation_held_by_a_reader_in_another_process_is_left():
    ingest_once("old text")
    holder = Holder("""
        r = index.open_read()
        print('ready', flush=True)
        sys.stdin.read()
        r.close()
    """)
    try:
        rebuild(["new text"])
        assert index.generations() == [1, 2]
    finally:
        assert holder.release() == 0
    ingest_once("more", key="k2")
    assert index.generations() == [2]


def test_a_reader_rereads_the_pointer_when_its_generation_went_away(monkeypatch):
    ingest_once("old text")
    rebuild(["new text"])
    readings = iter([1, 2])  # the first pointer read is stale: generation 1 is gone
    monkeypatch.setattr(index, "current", lambda env=None: next(readings))
    with index.open_read() as reader:
        assert reader.generation == 2


def test_a_reader_whose_generation_is_gone_twice_gets_index_missing(monkeypatch):
    ingest_once("old text")
    rebuild(["new text"])
    monkeypatch.setattr(index, "current", lambda env=None: 1)
    with pytest.raises(index.IndexMissing):
        index.open_read()


def test_a_reader_whose_database_is_gone_but_lock_remains_rereads_then_gives_up():
    ingest_once()
    index.db_path(1).unlink()
    with pytest.raises(index.IndexMissing):
        index.open_read()
    assert not index.db_path(1).exists()  # a reader never creates one


def test_a_damaged_generation_is_unreadable_not_missing():
    ingest_once()
    index.db_path(1).write_bytes(b"this is not a database at all" * 100)
    for suffix in ("-wal", "-shm"):
        index.db_path(1).with_name(f"index-1.db{suffix}").unlink(missing_ok=True)
    with pytest.raises(index.IndexUnreadable):
        index.open_read()


def test_a_generation_written_by_a_newer_memory_is_unreadable():
    ingest_once()
    with index.writer() as w:
        conn = w.connect()
        conn.execute("PRAGMA user_version = 99")
        conn.close()
    with pytest.raises(index.IndexUnreadable):
        index.open_read()


def test_a_pointer_to_nothing_is_replaced_by_a_new_generation():
    ingest_once()
    index.db_path(1).unlink()
    with index.writer() as w:
        assert w.made_first and w.generation == 2
    assert index.generations() == [2] and index.current() == 2


# -- a crash mid-rebuild --------------------------------------------------------------

def test_a_crash_mid_rebuild_leaves_the_pointer_and_the_next_child_removes_the_file():
    ingest_once("old text")
    crashed = Holder("""
        with index.writer() as w:
            with w.rebuilding():
                n, conn = w.new_generation()
                conn.execute("INSERT INTO rows (key, harness, session_id, kind, text) "
                             "VALUES ('x', 'claude', 's', 'user', 'half built')")
                print('ready', flush=True)
                os._exit(1)
    """)
    assert crashed.release() == 1
    assert index.current() == 1
    assert 2 in index.generations() and index.db_path(2).exists()
    with index.open_read() as reader:  # searches read the old generation meanwhile
        assert reader.generation == 1
    assert index.rebuild_running() is None  # the crash freed the lock
    ingest_once("after", key="k2")
    assert index.generations() == [1] and not index.db_path(2).exists()
    assert not index.lock_path(2).exists()


# -- the writer lock ------------------------------------------------------------------

def test_a_child_that_finds_the_writer_lock_held_gets_writer_busy_at_once():
    holder = Holder("""
        with index.writer():
            print('ready', flush=True)
            sys.stdin.read()
    """)
    try:
        started = time.monotonic()
        with pytest.raises(index.WriterBusy):
            with index.writer():
                pass
        assert time.monotonic() - started < 1.0
    finally:
        assert holder.release() == 0
    with index.writer():
        pass


def test_the_writer_lock_is_a_file_in_memorys_state_dir():
    from moxi_memory.settings import state_dir

    import fcntl

    with index.writer():
        assert index.writer_lock_path() == state_dir() / "writer.lock"
        fd = os.open(index.writer_lock_path(), os.O_RDONLY)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)


def test_readers_never_take_the_writer_lock(monkeypatch):
    ingest_once()
    holder = Holder("""
        with index.writer():
            print('ready', flush=True)
            sys.stdin.read()
    """)
    try:
        taken = []
        real = files.lock

        def watching(path, **kw):
            taken.append(str(path))
            return real(path, **kw)

        monkeypatch.setattr(files, "lock", watching)
        with index.open_read() as reader:
            assert reader.conn.execute("SELECT count(*) FROM rows").fetchone() == (1,)
        assert index.rebuild_running() is None
        assert taken == []
    finally:
        assert holder.release() == 0


# -- the rebuild lock and its entry ---------------------------------------------------

def test_a_running_rebuild_is_seen_with_its_start_time_and_process_id():
    ingest_once()
    holder = Holder("""
        with index.writer() as w:
            with w.rebuilding() as started:
                print('ready', flush=True)
                sys.stdin.read()
    """)
    try:
        running = index.rebuild_running()
        assert running is not None
        assert running.pid == holder.proc.pid
        assert abs(running.started - time.time()) < 60
        with index.open_read() as reader:  # a search during a rebuild reads the old one
            assert reader.generation == 1
    finally:
        assert holder.release() == 0
    assert index.rebuild_running() is None
    conn = state.connect()
    try:
        assert state.read_entry(conn) is None  # cleared at the end
    finally:
        conn.close()


def test_with_no_rebuild_lock_file_no_rebuild_is_running():
    assert not index.rebuild_lock_path().exists()
    assert index.rebuild_running() is None


@pytest.mark.parametrize("whose", ["dead", "live"])
def test_a_crashed_rebuilds_entry_reads_as_none_and_the_next_child_clears_it(whose):
    ingest_once()
    if whose == "live":
        pid = os.getpid()  # a process id that belongs to a live process: this one
    else:
        done = subprocess.Popen([sys.executable, "-c", "pass"])
        done.wait()
        pid = done.pid  # a process id whose process has ended
    with files.lock(index.rebuild_lock_path(), timeout_s=0):
        pass  # the lock file is there, and free, as a crash leaves it
    conn = state.connect()
    try:
        state.write_entry(conn, time.time() - 600, pid)
        assert index.rebuild_running() is None
        ingest_once("more", key="k2")
        assert state.read_entry(conn) is None
    finally:
        conn.close()


def test_a_crashed_rebuild_child_frees_the_lock_and_leaves_its_entry():
    ingest_once()
    crashed = Holder("""
        with index.writer() as w:
            with w.rebuilding():
                print('ready', flush=True)
                os._exit(1)
    """)
    assert crashed.release() == 1
    conn = state.connect()
    try:
        assert state.read_entry(conn) is not None
        assert index.rebuild_running() is None
        ingest_once("more", key="k2")
        assert state.read_entry(conn) is None
    finally:
        conn.close()


def test_a_rebuild_waits_out_a_readers_moment_on_its_lock():
    import fcntl

    with index.writer():
        pass
    with files.lock(index.rebuild_lock_path(), timeout_s=0):
        pass
    fd = os.open(index.rebuild_lock_path(), os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_SH)  # a reader's test, caught in the middle
    try:
        with index.writer() as w:
            with pytest.raises(files.LockTimeout):
                with w.rebuilding(wait=0.1):
                    pass
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    with index.writer() as w:
        with w.rebuilding(wait=1.0) as started:
            assert started <= time.time()


# -- the session table beside the index -----------------------------------------------

def test_a_reader_attaches_the_session_table_read_only():
    import sqlite3

    from moxi_memory import table

    ingest_once()
    with index.open_read() as reader:
        assert not reader.attach_state()  # no state store yet: nothing attached, none made
    assert not state.path().exists()
    conn = state.connect()
    try:
        table.write_read(conn, "claude", "s1", newest=1.0, kind="scratch", settled=True)
    finally:
        conn.close()
    with index.open_read() as reader:
        assert reader.attach_state("st")
        kinds = reader.conn.execute(
            "SELECT rows.key, st.sessions.kind FROM rows JOIN st.sessions "
            "USING (harness, session_id)").fetchall()
        assert kinds == [("k1", "scratch")]
        with pytest.raises(sqlite3.OperationalError):
            reader.conn.execute("DELETE FROM st.sessions")
