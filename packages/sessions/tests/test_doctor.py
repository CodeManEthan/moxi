"""sessions' doctor rows: one per harness root, then `retention` and `guard`."""

from __future__ import annotations

import os
import sqlite3

import pytest
from conftest import write_config

from moxi_sessions import doctor
from moxi_sessions.testing import CodexRollout, OpenCodeDb, write_imports

THREAD = "0190c000-0000-7000-8000-0000000c0de1"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home of the test's own, holding no harness's root yet."""
    home = tmp_path / "own-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)
    return home


def by_name() -> dict[str, doctor.Row]:
    """The root rows by name, without the `retention` row (see `test_retention.py`) and the
    `guard` row (see `test_guard.py`)."""
    return {row.name: row for row in doctor.rows() if row.name not in ("retention", "guard")}


def test_the_rows_are_the_cores_doctor_rows():
    import moxi.doctor

    assert doctor.Row is moxi.doctor.Row
    assert all(isinstance(row, moxi.doctor.Row) for row in doctor.rows())


def test_one_row_per_harness_off_when_no_root_is_there(home):
    rows = list(doctor.rows())
    assert [(r.module, r.name, r.state) for r in rows] == [
        ("sessions", "claude", "off"), ("sessions", "codex", "off"),
        ("sessions", "opencode", "off"), ("sessions", "retention", "ok"),
        ("sessions", "guard", "off")]
    assert str(home / ".claude" / "projects") in rows[0].detail


def test_readable_roots_are_ok(home):
    (home / ".claude" / "projects").mkdir(parents=True)
    CodexRollout(home / ".codex" / "sessions").write()
    db = OpenCodeDb(home / ".local" / "share" / "opencode")
    db.session()
    db.write()
    rows = by_name()
    assert {name: row.state for name, row in rows.items()} == {
        "claude": "ok", "codex": "ok", "opencode": "ok"}
    assert "nothing was imported" in rows["codex"].detail
    assert "read only" in rows["opencode"].detail


def test_a_codex_imports_file_that_parses_is_ok_and_one_that_does_not_warns(home):
    root = home / ".codex" / "sessions"
    root.mkdir(parents=True)
    write_imports(root, [THREAD])
    row = doctor.check("codex")
    assert row.state == "ok" and "lists 1 threads" in row.detail
    write_imports(root, text="{not json")
    row = doctor.check("codex")
    assert row.state == "warn" and row.detail.startswith("imports file unreadable")


def test_a_configured_root_that_is_missing_warns(home, own_config, tmp_path):
    write_config(own_config, f'[claude]\nprojects = "{tmp_path / "gone"}"\n'
                             f'[codex]\nsessions = "{tmp_path / "gone2"}"\n'
                             f'[opencode]\ndatabase = "{tmp_path / "gone.db"}"\n')
    rows = by_name()
    assert {name: row.state for name, row in rows.items()} == {
        "claude": "warn", "codex": "warn", "opencode": "warn"}
    assert rows["claude"].detail == (f"claude.projects names {tmp_path / 'gone'}, "
                                     "which does not exist")
    assert "codex.sessions names" in rows["codex"].detail
    assert "opencode.database names" in rows["opencode"].detail


def test_a_root_that_is_not_a_directory_warns(home):
    (home / ".claude").mkdir()
    (home / ".claude" / "projects").write_text("")
    row = doctor.check("claude")
    assert row.state == "warn" and "not a directory" in row.detail


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="permission bits do not hold for root")
def test_a_root_that_cannot_be_listed_warns(home):
    root = home / ".claude" / "projects"
    root.mkdir(parents=True)
    os.chmod(root, 0)
    try:
        row = doctor.check("claude")
    finally:
        os.chmod(root, 0o755)
    assert row.state == "warn" and "cannot be listed" in row.detail


def test_an_opencode_database_without_the_columns_warns(home):
    path = home / ".local" / "share" / "opencode" / "opencode.db"
    path.parent.mkdir(parents=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    row = doctor.check("opencode")
    assert row.state == "warn" and "column" in row.detail


def test_a_file_that_is_not_a_database_warns(home):
    path = home / ".local" / "share" / "opencode" / "opencode.db"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"this is not a database at all" * 100)
    assert doctor.check("opencode").state == "warn"


def test_a_config_file_that_cannot_be_read_warns_on_every_row(home, own_config):
    write_config(own_config, "[claude]\nunknown = 1\n")
    rows = list(doctor.rows())[:3]
    assert [r.state for r in rows] == ["warn"] * 3
    assert all("config file cannot be read" in r.detail for r in rows)
    assert all(r.fix == "moxi config sessions" for r in rows)
