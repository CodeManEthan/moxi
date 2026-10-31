"""The store: made by the first write, never by a read; migrated; its checks; the row
writes; and each way a read or write fails."""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest

from moxi_services import store
from moxi_services.errors import Busy, Refused, TooNew, Unreadable, Usage

from services_kit import add, adopted, written


def test_a_read_never_creates_the_store(store_path):
    assert store.read_rows() == []
    assert store.get("web") is None
    assert store.of_project("p") == []
    assert not store_path.exists()
    assert not store_path.parent.exists()


def test_the_first_write_creates_a_private_store_at_the_schema_version(store_path):
    row = add(**written())
    assert store_path.exists()
    if sys.platform != "win32":
        assert store_path.stat().st_mode & 0o777 == 0o600
    conn = sqlite3.connect(store_path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.VERSION
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()
    assert store.read_rows() == [row]


def test_a_written_row_reads_back_with_its_fields():
    row = add(**written("mail", kind="timer", every="5m", cwd="/srv/mail", enabled=True,
                        health=["/usr/bin/true"], project="portfolio",
                        launches_session=True, doc="send the mail", by="moxi services"))
    assert row.id == "services/mail" and row.module == "services"
    assert (row.form, row.kind, row.every, row.at, row.days) == ("written", "timer", "5m",
                                                                 None, None)
    assert row.argv == ("/usr/bin/python3", "-m", "http.server")
    assert row.health == ("/usr/bin/true",)
    assert (row.enabled, row.removing, row.launches_session) == (True, False, True)
    assert (row.cwd, row.project, row.doc, row.stopped) == ("/srv/mail", "portfolio",
                                                            "send the mail", None)
    assert row.platform == sys.platform and row.here
    assert row.created == row.changed and row.changed_by == "moxi services"
    assert row.created.endswith("Z")
    assert row.as_dict()["argv"] == ["/usr/bin/python3", "-m", "http.server"]
    assert row.as_dict()["id"] == "services/mail"


def test_an_adopted_row_and_a_timer_at_set_times():
    one = add(**adopted())
    two = add(**written("nightly", kind="timer", at=["02:30", "14:00"],
                        days=["mon", "fri"]))
    assert (one.form, one.unit, one.argv, one.enabled) == ("adopted", "backup.timer", None,
                                                           False)
    assert (two.at, two.days, two.every) == (("02:30", "14:00"), ("mon", "fri"), None)
    assert [r.name for r in store.read_rows()] == ["backup", "nightly"]


def test_a_row_of_another_system_is_not_here():
    row = add(**written(platform="darwin" if sys.platform != "darwin" else "linux"))
    assert not row.here


def test_a_name_is_unique_across_projects_and_the_refusal_suggests_one():
    add(**written(project="studio"))
    with pytest.raises(Refused) as caught:
        add(**written(project="portfolio"))
    assert caught.value.exit_code == 3
    assert caught.value.reason == ("web is taken (project studio); names are unique "
                                   "across projects")
    assert caught.value.fix == "try portfolio-web"
    with pytest.raises(Refused) as caught:
        add(**adopted("web", unit="web.service", kind="daemon"))
    assert caught.value.fix == "pick another name"


def test_a_suggestion_is_made_only_when_it_is_a_valid_name():
    add(**written("app-web"))
    with pytest.raises(Refused) as caught:
        add(**written("app-web", project="App"))
    assert caught.value.fix == "pick another name"
    add(**written("db"))
    with pytest.raises(Refused) as caught:
        add(**written("db", project="Delta.Web"))
    assert caught.value.fix == "try delta-web-db"


@pytest.mark.parametrize("fields", [
    written("Web"),
    written("web_1"),
    written(argv=["python3"]),
    written(argv=[]),
    written(cwd="relative"),
    written(kind="timer"),
    written(kind="timer", every="0m"),
    written(kind="timer", every="25h"),
    written(kind="timer", every="5m", at=["09:00"]),
    written(kind="timer", every="5m", days=["mon"]),
    written(kind="timer", at=["9:00"]),
    written(kind="timer", at=["09:00"], days=["monday"]),
    written(every="5m"),
    written(unit="web.service"),
    written(health=[]),
    written(doc="two\nlines"),
    written(platform="sunos"),
    written(form="module"),
    written(kind="socket"),
    adopted(unit=None),
    adopted(argv=["/bin/true"]),
    adopted(every="5m"),
], ids=lambda f: f"{f['name']}-{f['form']}-{len(f)}")
def test_fields_out_of_shape_are_a_usage_error_and_write_nothing(fields, store_path):
    with pytest.raises(Usage) as caught:
        add(**fields)
    assert caught.value.exit_code == 2
    assert store.read_rows() == []


@pytest.mark.parametrize("sql", [
    "INSERT INTO services (name, form, kind, unit, platform, created, changed, changed_by)"
    " VALUES ('a', 'written', 'daemon', 'x.service', 'linux', 't', 't', 'b')",
    "INSERT INTO services (name, form, kind, argv, platform, created, changed, changed_by)"
    " VALUES ('a', 'adopted', 'daemon', '[\"/bin/true\"]', 'linux', 't', 't', 'b')",
    "INSERT INTO services (name, form, kind, argv, platform, created, changed, changed_by)"
    " VALUES ('a', 'written', 'timer', '[\"/bin/true\"]', 'linux', 't', 't', 'b')",
    "INSERT INTO services (name, form, kind, argv, every, platform, created, changed,"
    " changed_by) VALUES ('a', 'written', 'daemon', '[\"/bin/true\"]', '5m', 'linux', 't',"
    " 't', 'b')",
    "INSERT INTO services (name, form, kind, argv, every, at, platform, created, changed,"
    " changed_by) VALUES ('a', 'written', 'timer', '[\"/bin/true\"]', '5m', '[]', 'linux',"
    " 't', 't', 'b')",
    "INSERT INTO services (name, form, kind, argv, every, days, platform, created,"
    " changed, changed_by) VALUES ('a', 'written', 'timer', '[\"/bin/true\"]', '5m', '[]',"
    " 'linux', 't', 't', 'b')",
    "INSERT INTO services (name, form, kind, unit, platform, created, changed, changed_by)"
    " VALUES ('a', 'module', 'daemon', 'x.service', 'linux', 't', 't', 'b')",
    "INSERT INTO services (name, form, kind, unit, platform, created, changed, changed_by)"
    " VALUES ('a', 'adopted', 'socket', 'x.service', 'linux', 't', 't', 'b')",
    "INSERT INTO services (name, form, kind, unit, platform, created, changed, changed_by)"
    " VALUES ('a', 'adopted', 'daemon', 'x.service', 'sunos', 't', 't', 'b')",
    "INSERT INTO services (name, form, kind, unit, enabled, platform, created, changed,"
    " changed_by) VALUES ('a', 'adopted', 'daemon', 'x.service', 2, 'linux', 't', 't', 'b')",
    "INSERT INTO services (name, form, kind, unit, removing, platform, created, changed,"
    " changed_by) VALUES ('a', 'adopted', 'daemon', 'x.service', 2, 'linux', 't', 't', 'b')",
    "INSERT INTO services (name, form, kind, unit, launches_session, platform, created,"
    " changed, changed_by) VALUES ('a', 'adopted', 'daemon', 'x.service', 2, 'linux', 't',"
    " 't', 'b')",
])
def test_the_store_checks_hold_whatever_writes_to_it(sql):
    with pytest.raises(sqlite3.IntegrityError):
        store.write(lambda conn: conn.execute(sql))


def test_the_row_writes_set_their_column_and_the_change_stamp():
    add(**written())
    row = store.write(lambda conn: store.set_enabled(conn, "web", True, by="page"))
    assert row.enabled and row.changed_by == "page"
    row = store.write(lambda conn: store.set_stopped(conn, "web", "2026-10-30T10:00:00Z",
                                                     by="moxi services"))
    assert row.stopped == "2026-10-30T10:00:00Z"
    row = store.write(lambda conn: store.set_stopped(conn, "web", None, by="x"))
    assert row.stopped is None
    row = store.write(lambda conn: store.set_removing(conn, "web", by="x"))
    assert row.removing
    assert store.write(lambda conn: store.delete(conn, "web")) is True
    assert store.write(lambda conn: store.delete(conn, "web")) is False
    assert store.read_rows() == []


def test_a_row_write_on_no_such_row_is_refused():
    add(**written())
    for write in (lambda c: store.set_enabled(c, "nope", True, by="x"),
                  lambda c: store.set_removing(c, "nope", by="x"),
                  lambda c: store.set_stopped(c, "nope", None, by="x")):
        with pytest.raises(Refused):
            store.write(write)


def test_a_write_that_raises_rolls_back():
    add(**written())

    def half(conn):
        store.set_enabled(conn, "web", True, by="x")
        raise RuntimeError("stop")

    with pytest.raises(RuntimeError):
        store.write(half)
    assert store.get("web").enabled is False


def test_of_project_matches_ignoring_case():
    add(**written("a", project="Portfolio"))
    add(**written("b", project="other"))
    add(**written("c"))
    assert [r.name for r in store.of_project("portfolio")] == ["a"]


# --- failures ---------------------------------------------------------------------------

def test_a_store_too_new_is_refused_for_reads_and_writes(too_new):
    with pytest.raises(TooNew) as caught:
        store.read_rows()
    assert caught.value.exit_code == 3
    assert "moxi install services" in caught.value.fix
    with pytest.raises(TooNew):
        add(**written())


def test_too_new_is_also_the_cores_store_too_new(too_new):
    import moxi.store

    with pytest.raises(moxi.store.StoreTooNew):
        store.read_rows()


def test_a_store_that_is_not_a_database_is_unreadable(store_path):
    store_path.parent.mkdir(parents=True)
    store_path.write_bytes(b"this is not a database, not even close" * 100)
    with pytest.raises(Unreadable) as caught:
        store.read_rows()
    assert caught.value.exit_code == 1 and caught.value.path == str(store_path)
    with pytest.raises(Unreadable):
        add(**written())


def test_an_empty_store_reads_as_no_rows_unless_strict(store_path):
    store_path.parent.mkdir(parents=True)
    store_path.write_bytes(b"")
    assert store.read_rows() == []
    with pytest.raises(Unreadable) as caught:
        store.read_rows(strict=True)
    assert "not set up" in caught.value.reason
    assert store_path.read_bytes() == b""


def test_a_store_at_version_zero_reads_as_no_rows_unless_strict(store_path):
    store_path.parent.mkdir(parents=True)
    sqlite3.connect(store_path).execute("CREATE TABLE x (y)").connection.close()
    assert store.read_rows() == []
    with pytest.raises(Unreadable):
        store.read_rows(strict=True)


def test_a_store_held_locked_past_the_wait_is_busy(store_path, monkeypatch):
    add(**written())
    monkeypatch.setattr(store, "TIMEOUT", 0.05)
    holder = sqlite3.connect(store_path, isolation_level=None)
    holder.execute("PRAGMA locking_mode = EXCLUSIVE")
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(Busy) as caught:
            store.read_rows()
        assert caught.value.exit_code == 75
        with pytest.raises(Busy):
            add(**written("other"))
    finally:
        holder.execute("ROLLBACK")
        holder.close()


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="POSIX modes; root reads anything")
def test_a_store_the_user_cannot_read_says_so(store_path):
    add(**written())
    store_path.chmod(0)
    try:
        with pytest.raises(Unreadable) as caught:
            store.read_rows()
        assert "access" in caught.value.fix or "move the file" in caught.value.fix
    finally:
        store_path.chmod(0o600)


# --- migration for SETUP ----------------------------------------------------------------

def test_migrate_existing_makes_no_store(store_path):
    assert store.migrate_existing() is False
    assert not store_path.exists()


def test_migrate_existing_sets_up_a_store_a_write_left_at_version_zero(store_path):
    store_path.parent.mkdir(parents=True)
    sqlite3.connect(store_path).execute("CREATE TABLE x (y)").connection.close()
    assert store.migrate_existing() is True
    assert store.read_rows(strict=True) == []


def test_migrate_existing_refuses_a_store_too_new(too_new):
    with pytest.raises(TooNew):
        store.migrate_existing()


def test_a_next_version_migrates_a_store_forward(monkeypatch):
    add(**written())
    monkeypatch.setattr(store, "STEPS", (*store.STEPS,
                                         "ALTER TABLE services ADD COLUMN extra TEXT;"))
    monkeypatch.setattr(store, "VERSION", 2)
    monkeypatch.setattr(store, "READS", frozenset({1, 2}))
    assert store.migrate_existing() is True
    assert [r.name for r in store.read_rows()] == ["web"]
