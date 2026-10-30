"""moxi.store: opening, transactions, migrations and one-shot updates of SQLite stores."""

from __future__ import annotations

import logging
import os
import sqlite3
import stat
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from moxi import store

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")


def mode_of(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def pragma(conn: sqlite3.Connection, name: str):
    return conn.execute(f"PRAGMA {name}").fetchone()[0]


# -- connect -----------------------------------------------------------------------------

def test_connect_creates_a_wal_store_with_the_house_pragmas(tmp_path):
    path = tmp_path / "s" / "record.db"
    conn = store.connect(path, timeout=2.5)
    try:
        assert path.is_file()
        assert pragma(conn, "journal_mode") == "wal"
        assert pragma(conn, "synchronous") == 1          # NORMAL
        assert pragma(conn, "foreign_keys") == 1
        assert pragma(conn, "busy_timeout") == 2500
        assert conn.isolation_level is None
    finally:
        conn.close()


@POSIX
def test_a_created_store_is_0600_in_a_private_parent(tmp_path):
    path = tmp_path / "s" / "record.db"
    store.connect(path).close()
    assert mode_of(path) == 0o600
    assert mode_of(path.parent) == 0o700


@POSIX
def test_a_store_that_is_not_private_gets_an_ordinary_parent(tmp_path):
    old = os.umask(0o022)
    try:
        path = tmp_path / "shared" / "record.db"
        store.connect(path, private=False).close()
    finally:
        os.umask(old)
    assert mode_of(path.parent) == 0o755
    assert mode_of(path) == 0o600


def test_create_false_refuses_a_missing_store_and_makes_nothing(tmp_path):
    path = tmp_path / "s" / "record.db"
    with pytest.raises(store.StoreError, match="does not exist"):
        store.connect(path, create=False)
    assert not path.parent.exists()


@pytest.mark.parametrize("name", [":memory:", Path(":memory:"), "./:memory:"])
def test_the_in_memory_name_is_refused_and_makes_nothing(tmp_path, monkeypatch, name):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(store.StoreError, match="in-memory"):
        store.connect(name)
    assert list(tmp_path.iterdir()) == []


def test_an_existing_store_is_opened_as_it_is(tmp_path, caplog):
    path = tmp_path / "old.db"
    plain = sqlite3.connect(path)
    plain.execute("CREATE TABLE t (x)")
    plain.commit()
    plain.close()
    before = path.read_bytes()
    with caplog.at_level(logging.WARNING, logger="moxi.store"):
        conn = store.connect(path, create=False)
    try:
        assert pragma(conn, "journal_mode") == "delete"
        assert pragma(conn, "foreign_keys") == 1
    finally:
        conn.close()
    assert path.read_bytes() == before
    assert not Path(f"{path}-wal").exists()
    [record] = [r for r in caplog.records if r.name == "moxi.store"]
    assert "journal mode delete, not wal" in record.getMessage()


def test_an_existing_wal_store_logs_nothing(tmp_path, caplog):
    path = tmp_path / "s.db"
    store.connect(path).close()
    with caplog.at_level(logging.WARNING, logger="moxi.store"):
        store.connect(path).close()
    assert not [r for r in caplog.records if r.name == "moxi.store"]


def test_an_empty_file_is_made_a_wal_store(tmp_path, caplog):
    path = tmp_path / "s.db"
    path.touch()
    with caplog.at_level(logging.WARNING, logger="moxi.store"):
        conn = store.connect(path, create=False)
    try:
        assert pragma(conn, "journal_mode") == "wal"
    finally:
        conn.close()
    assert not caplog.records


def test_check_same_thread_is_passed_through(tmp_path):
    path = tmp_path / "s.db"
    errors: list[BaseException] = []

    def use(conn):
        try:
            conn.execute("SELECT 1").fetchone()
        except BaseException as exc:  # noqa: BLE001 - the test inspects it
            errors.append(exc)

    for same, expect_error in ((True, True), (False, False)):
        conn = store.connect(path, check_same_thread=same)
        thread = threading.Thread(target=use, args=(conn,))
        thread.start()
        thread.join()
        conn.close()
        assert bool(errors) is expect_error
        errors.clear()


# -- transaction -------------------------------------------------------------------------

def test_transaction_commits_the_block(tmp_path):
    path = tmp_path / "s.db"
    conn = store.connect(path)
    conn.execute("CREATE TABLE t (x)")
    with store.transaction(conn) as same:
        assert same is conn
        conn.execute("INSERT INTO t VALUES (1)")
    conn.close()
    other = store.connect(path)
    assert other.execute("SELECT x FROM t").fetchall() == [(1,)]
    other.close()


def test_transaction_rolls_back_and_raises_again(tmp_path):
    conn = store.connect(tmp_path / "s.db")
    conn.execute("CREATE TABLE t (x)")
    with pytest.raises(KeyError):
        with store.transaction(conn):
            conn.execute("INSERT INTO t VALUES (1)")
            raise KeyError("x")
    assert not conn.in_transaction
    assert conn.execute("SELECT count(*) FROM t").fetchone() == (0,)
    conn.close()


def test_transaction_holds_the_write_lock_from_its_start(tmp_path):
    path = tmp_path / "s.db"
    conn = store.connect(path)
    other = store.connect(path, timeout=0)
    try:
        with store.transaction(conn):
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("BEGIN IMMEDIATE")
    finally:
        other.close()
        conn.close()


def test_a_block_that_ends_its_own_transaction_is_an_error(tmp_path):
    conn = store.connect(tmp_path / "s.db")
    with pytest.raises(store.StoreError, match="ended inside"):
        with store.transaction(conn):
            conn.execute("COMMIT")
    conn.close()


def test_a_commit_that_fails_rolls_back_and_leaves_the_connection_usable(tmp_path):
    path = tmp_path / "s.db"
    conn = store.connect(path, timeout=0.05)
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.execute("CREATE TABLE t (x)")
    # A reader in a transaction keeps its shared lock, so a delete-journal COMMIT, which
    # needs the exclusive lock, is busy past the timeout.
    reader = sqlite3.connect(path, isolation_level=None)
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM t").fetchone()
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            with store.transaction(conn):
                conn.execute("INSERT INTO t VALUES (1)")
        assert not conn.in_transaction
    finally:
        reader.execute("ROLLBACK")
        reader.close()
    with store.transaction(conn):
        conn.execute("INSERT INTO t VALUES (2)")
    assert conn.execute("SELECT x FROM t").fetchall() == [(2,)]
    conn.close()


# -- migrate -----------------------------------------------------------------------------

STEPS = [
    """
    -- the first table; a comment; with a semicolon
    CREATE TABLE note (id INTEGER PRIMARY KEY, body TEXT);
    INSERT INTO note (body) VALUES ('a; b');
    """,
    lambda conn: conn.execute("ALTER TABLE note ADD COLUMN at REAL"),
    """
    CREATE TABLE seen (n INTEGER);
    CREATE TRIGGER count_notes AFTER INSERT ON note BEGIN
        INSERT INTO seen VALUES (new.id);
        INSERT INTO seen VALUES (-new.id);
    END;
    /* a closing comment */
    """,
]


def test_migrate_walks_every_step_and_returns_the_version(tmp_path):
    conn = store.connect(tmp_path / "s.db")
    assert store.migrate(conn, STEPS) == 3
    assert pragma(conn, "user_version") == 3
    assert conn.execute("SELECT body FROM note").fetchall() == [("a; b",)]
    conn.execute("INSERT INTO note (body, at) VALUES ('c', 1.0)")
    assert sorted(conn.execute("SELECT n FROM seen")) == [(-2,), (2,)]
    assert store.migrate(conn, STEPS) == 3
    assert conn.execute("SELECT count(*) FROM note").fetchone() == (2,)
    conn.close()


def test_migrate_runs_only_the_steps_past_the_version(tmp_path):
    conn = store.connect(tmp_path / "s.db")
    assert store.migrate(conn, STEPS[:1]) == 1
    ran = []
    assert store.migrate(conn, [lambda c: ran.append(1), lambda c: ran.append(2)]) == 2
    assert ran == [2]
    conn.close()


def test_migrate_accepts_a_last_statement_without_a_semicolon(tmp_path):
    conn = store.connect(tmp_path / "s.db")
    store.migrate(conn, ["CREATE TABLE a (x); CREATE TABLE b (y)"])
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    assert names == {"a", "b"}
    conn.close()


def test_a_failing_step_keeps_the_steps_before_it_and_none_of_itself(tmp_path):
    conn = store.connect(tmp_path / "s.db")
    steps = ["CREATE TABLE a (x)", "CREATE TABLE b (y); INSERT INTO nowhere VALUES (1)"]
    with pytest.raises(sqlite3.OperationalError, match="nowhere"):
        store.migrate(conn, steps)
    assert pragma(conn, "user_version") == 1
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    assert names == {"a"}
    assert not conn.in_transaction
    conn.close()


def test_a_store_newer_than_the_steps_is_too_new(tmp_path):
    conn = store.connect(tmp_path / "s.db")
    conn.execute("PRAGMA user_version = 5")
    with pytest.raises(store.StoreTooNew) as caught:
        store.migrate(conn, STEPS)
    assert caught.value.exit_code == 3
    assert (caught.value.version, caught.value.known) == (5, 3)
    assert "s.db is at schema version 5" in str(caught.value)
    assert isinstance(caught.value, store.StoreError)
    assert not conn.in_transaction
    conn.close()


def test_a_step_that_commits_itself_is_an_error(tmp_path):
    conn = store.connect(tmp_path / "s.db")
    with pytest.raises(store.StoreError, match="step 1 ended its own transaction"):
        store.migrate(conn, [lambda c: c.execute("COMMIT")])
    assert pragma(conn, "user_version") == 0
    conn.close()


MIGRATOR = textwrap.dedent("""
    import sys, time
    from moxi import store

    def step(n):
        def run(conn):
            conn.execute("INSERT INTO ran VALUES (?)", (n,))
            time.sleep(0.05)
        return run

    conn = store.connect(sys.argv[1], timeout=30)
    print(store.migrate(conn, ["CREATE TABLE ran (n INTEGER)"] + [step(n) for n in range(1, 6)]))
    conn.close()
""")


def test_processes_migrating_together_run_each_step_once(tmp_path):
    path = tmp_path / "s.db"
    store.connect(path).close()
    procs = [subprocess.Popen([sys.executable, "-c", MIGRATOR, str(path)],
                              stdout=subprocess.PIPE, text=True) for _ in range(4)]
    outs = [p.communicate(timeout=60)[0].strip() for p in procs]
    assert [p.returncode for p in procs] == [0] * 4
    assert outs == ["6"] * 4
    conn = store.connect(path)
    assert [n for (n,) in conn.execute("SELECT n FROM ran ORDER BY n")] == [1, 2, 3, 4, 5]
    conn.close()


# -- update ------------------------------------------------------------------------------

def test_update_runs_one_transaction_and_returns_its_result(tmp_path):
    path = tmp_path / "s.db"
    store.update(path, lambda c: c.execute("CREATE TABLE t (x)"))
    assert store.update(path, lambda c: c.execute("INSERT INTO t VALUES (7)").lastrowid) == 1
    assert store.update(path, lambda c: c.execute("SELECT x FROM t").fetchall()) == [(7,)]


def test_update_keeps_nothing_when_fn_raises(tmp_path):
    path = tmp_path / "s.db"
    store.update(path, lambda c: c.execute("CREATE TABLE t (x)"))

    def broken(conn):
        conn.execute("INSERT INTO t VALUES (1)")
        raise ValueError("no")

    with pytest.raises(ValueError):
        store.update(path, broken)
    assert store.update(path, lambda c: c.execute("SELECT count(*) FROM t").fetchone()) == (0,)


def test_update_passes_connect_arguments(tmp_path):
    with pytest.raises(store.StoreError, match="does not exist"):
        store.update(tmp_path / "s.db", lambda c: None, create=False)


# -- import weight -----------------------------------------------------------------------

def test_importing_the_module_loads_no_sqlite():
    code = "import sys, moxi.store; print('sqlite3' in sys.modules)"
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                          check=True)
    assert done.stdout.strip() == "False"
