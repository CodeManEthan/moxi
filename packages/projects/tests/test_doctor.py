"""projects' doctor rows, its setup, and the announcement's declarations."""

from __future__ import annotations

import os
import sqlite3
import sys

import moxi.modules
import pytest
from moxi.doctor import Row

from moxi_projects import api, doctor, module, store

TOPIC = '''
INTERFACE = 1
KINDS = {"topic": ("a notes topic", lambda fields: None)}
'''


def by_name() -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = {}
    for row in doctor.rows():
        assert isinstance(row, Row) and row.module == "projects"
        out.setdefault(row.name, []).append(row)
    return out


def one(rows: dict[str, list[Row]], name: str) -> Row:
    assert len(rows[name]) == 1, rows[name]
    return rows[name][0]


def _sql(path, *statements) -> None:
    conn = sqlite3.connect(path)
    try:
        for statement in statements:
            conn.execute(statement)
        conn.commit()
    finally:
        conn.close()


def _make_roots(store_path, base) -> None:
    """Point every golden row at a directory of its own under `base`, made. The golden
    roots' own base is shared by every test, so nothing is made there."""
    conn = sqlite3.connect(store_path)
    try:
        for (key,) in conn.execute("SELECT key FROM projects").fetchall():
            root = os.path.join(str(base), key)
            os.makedirs(root)
            conn.execute("UPDATE projects SET root = ?, root_real = ? WHERE key = ?",
                          (root, os.path.realpath(root), key))
        conn.commit()
    finally:
        conn.close()


# --- the store ---------------------------------------------------------------------------

def test_a_missing_store_is_ok_and_not_created(store_path):
    rows = by_name()
    assert one(rows, "store").state == "ok" and "first add" in one(rows, "store").detail
    assert one(rows, "contributions").state == "ok"
    assert set(rows) == {"store", "contributions"}
    assert not store_path.exists()


def test_a_healthy_store_is_ok_on_every_row(golden_copy, store_path, tmp_path):
    _make_roots(store_path, tmp_path)
    rows = by_name()
    assert set(rows) == {"store", "roots", "root spelling", "same directory", "kinds",
                         "agents", "contributions"}
    assert all(r.state == "ok" for rs in rows.values() for r in rs), rows


def test_a_newer_store_fails_naming_the_upgrade(golden_copy, store_path):
    _sql(store_path, f"PRAGMA user_version = {api._VERSION + 1}")
    row = one(by_name(), "store")
    assert row.state == "fail" and "moxi install projects" in row.fix


def test_a_store_older_than_the_code_reads_fails_naming_the_install(
        golden_copy, store_path, next_version, monkeypatch):
    monkeypatch.setattr(api, "_READS", frozenset({next_version}))
    row = one(by_name(), "store")
    assert row.state == "fail" and "moxi install projects" in row.fix


def test_an_older_store_still_read_warns_and_is_not_migrated(golden_copy, store_path,
                                                             next_version, tmp_path):
    _make_roots(store_path, tmp_path)
    before = store_path.read_bytes()
    rows = by_name()
    assert one(rows, "store").state == "ok"
    assert one(rows, "store version").state == "warn"
    assert one(rows, "root spelling").state == "ok"
    assert store_path.read_bytes() == before


def test_a_corrupt_store_fails(store_path):
    store_path.parent.mkdir(parents=True)
    store_path.write_bytes(b"not a database at all, " * 300)
    row = one(by_name(), "store")
    assert row.state == "fail" and row.fix


# --- the rows ----------------------------------------------------------------------------

def test_a_missing_root_warns(golden_copy):
    row = one(by_name(), "roots")
    assert row.state == "warn" and "alpha" in row.detail and "moxi projects set" in row.fix


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_stale_resolved_root_is_refreshed_through_the_write_sequence(tmp_path,
                                                                       monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    link = tmp_path / "link"
    link.symlink_to(first)
    store.write(lambda conn: store.insert(conn, key="p", root=str(link)))
    link.unlink()
    link.symlink_to(second)

    import moxi.store

    migrated = []
    migrate = moxi.store.migrate

    def watched(conn, steps):
        migrated.append(conn.in_transaction)
        return migrate(conn, steps)

    monkeypatch.setattr(moxi.store, "migrate", watched)
    row = one(by_name(), "root spelling")
    assert row.state == "ok" and "refreshed 1 root" in row.detail
    assert migrated == [False]
    assert api.for_path(str(second / "x")).key == "p"
    assert "refreshed" not in one(by_name(), "root spelling").detail


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_stale_resolved_root_in_an_older_store_warns_and_writes_nothing(
        tmp_path, store_path, monkeypatch):
    target = tmp_path / "t"
    target.mkdir()
    store.write(lambda conn: store.insert(conn, key="p", root=str(target)))
    _sql(store_path, "UPDATE projects SET root_real = '/somewhere/else'")
    monkeypatch.setattr(store, "STEPS", (*store.STEPS, "SELECT 1;"))
    monkeypatch.setattr(api, "_VERSION", len(store.STEPS))
    monkeypatch.setattr(api, "_READS", frozenset({*api._READS, len(store.STEPS)}))
    before = store_path.read_bytes()
    row = one(by_name(), "root spelling")
    assert row.state == "warn" and "moxi install projects" in row.fix
    assert store_path.read_bytes() == before


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_two_roots_naming_one_directory_warn(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "alias").symlink_to(real)

    def two(conn):
        store.insert(conn, key="zeta", root=str(real))
        store.insert(conn, key="Alpha", root=str(tmp_path / "alias"))

    store.write(two)
    row = one(by_name(), "same directory")
    assert row.state == "warn" and "Alpha and zeta" in row.detail
    assert "find picks Alpha" in row.detail


def test_a_kind_no_module_declares_warns(golden_copy, store_path, site):
    site.contribution("notes", TOPIC)
    _sql(store_path, "UPDATE projects SET kind = 'topic' WHERE key = 'gamma'",
         "UPDATE projects SET kind = 'gone' WHERE key = 'alpha'")
    row = one(by_name(), "kinds")
    assert row.state == "warn" and "'gone' (alpha)" in row.detail
    assert "topic" not in row.detail


def test_a_kind_past_an_entry_that_wont_load_names_it(golden_copy, store_path, site):
    site.contribution("broken", "INTERFACE = 9\n")
    _sql(store_path, "UPDATE projects SET kind = 'gone' WHERE key = 'alpha'")
    row = one(by_name(), "kinds")
    assert row.state == "warn" and "broken won't load" in row.detail


def test_a_hand_edited_lead_in_the_set_or_a_bad_name_fails(golden_copy, store_path):
    _sql(store_path, "INSERT INTO project_agents VALUES ('gamma', 'lead-a', 'x')",
         "UPDATE projects SET lead = 'two words' WHERE key = 'Beta'")
    row = one(by_name(), "agents")
    assert row.state == "fail"
    assert "gamma: the lead lead-a" in row.detail and "'two words'" in row.detail


def test_the_contributions_row_splits_by_cause(golden_copy, site):
    site.contribution("broken", "raise ImportError('gone')\n")
    site.contribution("notes", TOPIC)
    site.contribution("other", TOPIC)
    rows = by_name()["contributions"]
    assert all(r.state == "fail" for r in rows) and len(rows) == 2
    unloadable = [r for r in rows if r.detail.startswith("broken:")]
    collision = [r for r in rows if "'topic'" in r.detail]
    assert "blocks remove, and add or set of a kind" in unloadable[0].detail
    assert "any other add goes ahead without it" in unloadable[0].detail
    assert "moxi uninstall broken" in unloadable[0].fix
    assert "blocks add and set of kind 'topic' alone" in collision[0].detail
    assert "uninstall all but one of notes, other" in collision[0].fix


def test_a_malformed_kind_fails_for_that_kind_alone(site):
    site.contribution("notes", '''
INTERFACE = 2
KINDS = {"topic": ("doc", lambda f: None, "zip")}
''')
    row = one(by_name(), "contributions")
    assert row.state == "fail" and "'topic'" in row.detail and "moxi uninstall notes" in row.fix


def test_notes_roots_kept_without_notes_are_counted(golden_copy, store_path, site):
    _sql(store_path, "UPDATE projects SET notes_root = '/srv/n' WHERE key IN ('alpha', "
                     "'gamma')")
    row = one(by_name(), "notes root")
    assert row.state == "ok" and row.detail.startswith("2 projects keep")


# --- setup -------------------------------------------------------------------------------

def test_setup_makes_no_store(store_path):
    assert store.setup() == 0
    assert not store_path.exists()


def test_setup_migrates_an_existing_store(golden_copy, store_path, next_version):
    assert store.setup() == 0
    conn = sqlite3.connect(store_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == next_version
    finally:
        conn.close()


def test_setup_of_a_newer_store_is_exit_3(golden_copy, store_path, capsys):
    _sql(store_path, f"PRAGMA user_version = {api._VERSION + 1}")
    assert store.setup() == 3
    assert "moxi install projects" in capsys.readouterr().err


# --- the announcement --------------------------------------------------------------------

def test_the_announcement_declares_the_verb_store_doctor_and_setup():
    found = moxi.modules.load("projects", interface=1)
    assert found.ok, found.reason
    assert found.cli == "moxi_projects.cli:main"
    assert module.SETUP == "moxi_projects.store:setup"
    assert module.DOCTOR == "moxi_projects.doctor:rows"
    entry = module.STORES["projects"]
    assert entry["lazy"] is True and entry["rebuildable"] is False
    assert os.path.join(entry["path"]) == api._FILE and entry["root"] == "data"
