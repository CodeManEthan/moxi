"""The store: where it lives, the write sequence, reads that never write, schema versions,
and the row writes' rules."""

from __future__ import annotations

import os
import sqlite3
import stat
import sys

import moxi.store
import pytest

from moxi_projects import api, store
from moxi_projects.errors import Refused, Usage


def _bytes(path) -> bytes:
    return path.read_bytes()


def _user_version(path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _set_user_version(path, version: int) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
    finally:
        conn.close()


# --- where it lives, and the first write -------------------------------------------------

def test_the_store_is_in_the_modules_data_directory(fence, store_path):
    import moxi.paths

    assert store_path == moxi.paths.Layout("projects").data_dir() / "projects.db"
    assert str(store_path).startswith(str(fence.short))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_the_first_write_creates_a_private_wal_store_at_the_current_version(store_path):
    store.write(lambda conn: store.insert(conn, key="one", root="/srv/one"))
    assert stat.S_IMODE(store_path.stat().st_mode) == 0o600
    conn = sqlite3.connect(store_path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()
    assert _user_version(store_path) == len(store.STEPS) == api._VERSION


def test_the_read_versions_include_the_current_one():
    assert api._VERSION in api._READS
    assert api._VERSION == len(store.STEPS)


# --- reads never write -------------------------------------------------------------------

def test_a_missing_store_reads_as_no_projects_and_is_not_created(store_path):
    assert api.list_projects() == []
    with pytest.raises(api.NotFound):
        api.get("anything")
    assert api.for_path("/") is None
    assert not store_path.exists()
    assert not store_path.parent.exists()


def test_a_store_no_write_has_set_up_reads_as_empty(store_path):
    store_path.parent.mkdir(parents=True)
    store_path.write_bytes(b"")
    assert api.list_projects() == []
    assert store_path.read_bytes() == b""
    assert sorted(p.name for p in store_path.parent.iterdir()) == ["projects.db"]


def test_a_read_leaves_the_files_bytes_unchanged(golden_copy, store_path):
    before = _bytes(store_path)
    api.list_projects()
    api.get("alpha")
    api.for_path(os.path.join(golden_copy, "alpha", "src"))
    api.kinds()
    assert _bytes(store_path) == before


@pytest.fixture
def next_version(monkeypatch):
    """This code as if it were one schema version on: a second step it reads and writes,
    and version 1 still read as it is."""
    monkeypatch.setattr(store, "STEPS", (*store.STEPS,
                                         "ALTER TABLE projects ADD COLUMN extra TEXT;"))
    monkeypatch.setattr(api, "_VERSION", 2)
    monkeypatch.setattr(api, "_READS", frozenset({1, 2}))


def test_a_read_of_an_older_store_answers_and_leaves_it_as_it_was(golden_copy, store_path,
                                                                  next_version):
    before = _bytes(store_path)
    assert [p.key for p in api.list_projects()] == ["alpha", "Beta", "delta.web", "gamma"]
    assert _bytes(store_path) == before
    assert _user_version(store_path) == 1


def test_a_write_migrates_an_older_store(golden_copy, store_path, next_version):
    store.write(lambda conn: store.add_agent(conn, "gamma", "new-agent"))
    assert _user_version(store_path) == 2
    assert api.get("gamma").associated == ("new-agent",)


def test_a_store_older_than_the_code_reads_is_unreadable_naming_the_install(
        golden_copy, store_path, next_version, monkeypatch):
    monkeypatch.setattr(api, "_READS", frozenset({2}))
    before = _bytes(store_path)
    with pytest.raises(api.Unreadable) as caught:
        api.list_projects()
    assert caught.value.exit_code == 1
    assert "moxi install projects" in str(caught.value)
    assert _bytes(store_path) == before


def test_a_newer_store_refuses_a_read_with_exit_3(golden_copy, store_path):
    _set_user_version(store_path, api._VERSION + 1)
    before = _bytes(store_path)
    with pytest.raises(api.TooNew) as caught:
        api.get("alpha")
    exc = caught.value
    assert isinstance(exc, api.Unreadable)
    assert isinstance(exc, moxi.store.StoreTooNew)
    assert exc.exit_code == 3
    assert "moxi install projects" in str(exc)
    assert _bytes(store_path) == before


def test_a_newer_store_refuses_a_write_with_exit_3(golden_copy, store_path):
    _set_user_version(store_path, api._VERSION + 1)
    before = _bytes(store_path)
    with pytest.raises(moxi.store.StoreTooNew) as caught:
        store.write(lambda conn: store.add_agent(conn, "gamma", "x"))
    assert isinstance(caught.value, api.TooNew)
    assert caught.value.exit_code == 3
    assert _bytes(store_path) == before


def test_a_write_stops_when_another_release_migrated_past_it_under_the_lock(
        golden_copy, store_path, monkeypatch):
    migrate = moxi.store.migrate

    def migrate_then_overtaken(conn, steps):
        done = migrate(conn, steps)
        _set_user_version(store_path, api._VERSION + 1)
        return done

    monkeypatch.setattr(moxi.store, "migrate", migrate_then_overtaken)
    with pytest.raises(api.TooNew):
        store.write(lambda conn: store.add_agent(conn, "gamma", "late"))
    monkeypatch.setattr(moxi.store, "migrate", migrate)
    _set_user_version(store_path, api._VERSION)
    assert api.get("gamma").associated == ()


def test_the_migration_runs_outside_the_writes_transaction(store_path, monkeypatch):
    seen = []
    migrate = moxi.store.migrate

    def watched(conn, steps):
        seen.append(conn.in_transaction)
        return migrate(conn, steps)

    monkeypatch.setattr(moxi.store, "migrate", watched)
    store.write(lambda conn: store.insert(conn, key="one", root="/srv/one"))
    assert seen == [False]


def test_a_write_locked_out_past_the_wait_is_busy_with_exit_75(golden_copy, store_path,
                                                                monkeypatch):
    monkeypatch.setattr(store, "TIMEOUT", 0.05)
    holder = sqlite3.connect(store_path, isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        with pytest.raises(api.Busy) as caught:
            store.write(lambda conn: store.add_agent(conn, "gamma", "x"))
        assert caught.value.exit_code == 75
        assert isinstance(caught.value, api.Unreadable)
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_a_corrupt_store_is_unreadable_with_exit_1(store_path):
    store_path.parent.mkdir(parents=True)
    store_path.write_bytes(b"this is not a database, not at all, " * 200)
    with pytest.raises(api.Unreadable) as caught:
        api.list_projects()
    assert caught.value.exit_code == 1
    assert caught.value.fix
    with pytest.raises(api.Unreadable):
        store.write(lambda conn: store.insert(conn, key="x", root="/x"))


def test_busy_errors_map_to_busy():
    err = sqlite3.OperationalError("database is locked")
    assert isinstance(api._unreadable("/s.db", err), api.Busy)


# --- the row writes ----------------------------------------------------------------------

def test_insert_returns_the_row_with_a_utc_birth_time(store_path):
    project = store.write(lambda conn: store.insert(conn, key="one", root="/srv/one/./x/..",
                                                    lead="boss"))
    assert project.key == "one" and project.root == "/srv/one" and project.kind == "project"
    assert project.lead == "boss" and project.associated == () and project.notes_root is None
    assert project.created.endswith("Z") and project.created[10] == "T"
    assert api.get("one") == project


def test_a_key_taken_ignoring_case_is_refused(golden_copy):
    with pytest.raises(Refused, match="keys ignore case"):
        store.write(lambda conn: store.insert(conn, key="ALPHA", root="/elsewhere"))


def test_a_taken_root_is_refused(golden_copy):
    root = os.path.join(golden_copy, "alpha")
    with pytest.raises(Refused, match="already project 'alpha'"):
        store.write(lambda conn: store.insert(conn, key="other", root=root + "/."))


def test_insert_applies_the_rules(store_path):
    for bad in (dict(key="con", root="/x"), dict(key="ok", root="rel"),
                dict(key="ok", root="/x", lead="two words"),
                dict(key="ok", root="/x", notes_root="rel"),
                dict(key="ok", root="/x", kind="bad kind")):
        with pytest.raises(Refused):
            store.write(lambda conn, bad=bad: store.insert(conn, **bad))
    assert api.list_projects() == []


def test_a_refused_write_leaves_nothing_behind(golden_copy, store_path):
    def two(conn):
        store.insert(conn, key="first", root="/srv/first")
        store.insert(conn, key="alpha", root="/srv/again")

    with pytest.raises(Refused):
        store.write(two)
    with pytest.raises(api.NotFound):
        api.get("first")


def test_update_changes_named_fields_and_rewrites_the_resolved_root(golden_copy, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    project = store.write(lambda conn: store.update(conn, "GAMMA", root=str(link),
                                                    lead=None, kind="agent"))
    assert (project.key, project.root, project.kind, project.lead) == (
        "gamma", str(link), "agent", None)
    conn = sqlite3.connect(api._store_path())
    try:
        assert conn.execute("SELECT root_real FROM projects WHERE key = 'gamma'"
                            ).fetchone()[0] == str(real)
    finally:
        conn.close()


def test_update_with_nothing_named_is_usage(golden_copy):
    with pytest.raises(Usage) as caught:
        store.write(lambda conn: store.update(conn, "alpha"))
    assert caught.value.exit_code == 2


def test_update_of_an_unknown_key_is_not_found(golden_copy):
    with pytest.raises(api.NotFound) as caught:
        store.write(lambda conn: store.update(conn, "nope", lead="x"))
    assert caught.value.key == "nope" and caught.value.exit_code == 3


def test_update_refuses_a_root_another_project_holds(golden_copy):
    with pytest.raises(Refused):
        store.write(lambda conn: store.update(conn, "gamma",
                                              root=os.path.join(golden_copy, "alpha")))


def test_a_lead_already_in_the_set_is_refused(golden_copy):
    with pytest.raises(Refused, match="agents remove alpha helper"):
        store.write(lambda conn: store.update(conn, "alpha", lead="helper"))


def test_the_lead_is_not_added_to_the_set(golden_copy):
    with pytest.raises(Refused, match="is the lead"):
        store.write(lambda conn: store.add_agent(conn, "alpha", "lead-a"))


def test_adding_a_pair_twice_is_refused(golden_copy):
    with pytest.raises(Refused, match="already associated"):
        store.write(lambda conn: store.add_agent(conn, "alpha", "helper"))


def test_removing_a_pair_that_is_not_there_is_refused(golden_copy):
    with pytest.raises(Refused) as caught:
        store.write(lambda conn: store.remove_agent(conn, "alpha", "stranger"))
    assert caught.value.exit_code == 3
    with pytest.raises(Refused, match="--no-lead"):
        store.write(lambda conn: store.remove_agent(conn, "alpha", "lead-a"))


def test_agents_are_kept_by_the_stored_key(golden_copy):
    project = store.write(lambda conn: store.add_agent(conn, "BETA", "watcher"))
    assert project.key == "Beta" and project.associated == ("watcher",)
    project = store.write(lambda conn: store.remove_agent(conn, "beta", "watcher"))
    assert project.associated == ()


def test_delete_refuses_while_agents_are_associated(golden_copy):
    with pytest.raises(Refused, match="agents remove alpha NAME"):
        store.write(lambda conn: store.delete(conn, "alpha"))
    assert api.get("alpha")


def test_delete_removes_the_row_and_returns_it(golden_copy):
    gone = store.write(lambda conn: store.delete(conn, "GAMMA"))
    assert gone.key == "gamma"
    with pytest.raises(api.NotFound):
        api.get("gamma")
