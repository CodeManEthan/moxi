"""The move of comms 0.2's files into comms.db, on a home made by comms 0.2.0.

`fixtures/comms-0.2/` holds what `make.py` beside it wrote by running comms 0.2.0: each
0.2 database as SQL, the cursor files and the operator's token file. `zero_two_home` lays
them out under the test's home as 0.2 left them.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

from moxi_comms import cli, paths, provision, store, tokens
from moxi_comms.local_record import LocalRecord, META_OPERATOR_TOKEN_HASH, token_hash
from moxi_comms.store import upgrade_0_2

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "comms-0.2"
MADE = json.loads((FIXTURE / "made.json").read_text())
IDS = {k: int(v) for k, v in MADE["ids"].items()}
#: A clock just after the fixture was made, so 0.2's bounded replay still reaches it.
SOON = MADE["made_at"] + 60


def zero_two_home():
    """comms 0.2's files, as 0.2.0 left them, under the test's home."""
    for role, dump, wal in (("record", "record.sql", True),
                            ("directory", "directory.sql", False),
                            ("bindings", "bindings.sql", True)):
        p = upgrade_0_2_paths()[role]
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(p)
        if wal:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript((FIXTURE / dump).read_text())
        conn.close()
    shutil.copytree(FIXTURE / "cursors", paths.cursors_dir())
    lay_tokens(FIXTURE / "tokens", paths.tokens_dir())
    return upgrade_0_2_paths()


def lay_tokens(source: Path, dest: Path) -> None:
    """0.2's token store as 0.2 left it: the directory 0700 and each file 0600, set here
    rather than taken from the checkout. git keeps no mode but the execute bit, so a
    fresh checkout has the fixture's files at whatever its umask gave them."""
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, tokens.DIR_MODE)
    for f in source.iterdir():
        fd = os.open(dest / f.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, tokens.FILE_MODE)
        with os.fdopen(fd, "wb") as out:
            out.write(f.read_bytes())
        os.chmod(dest / f.name, tokens.FILE_MODE)


def upgrade_0_2_paths():
    return {"record": paths.record_db(), "directory": paths.directory_db(),
            "bindings": paths.bindings_db(), "cursors": paths.cursors_dir()}


def contents():
    """Every 0.2 file's bytes, to show a refusal wrote nothing."""
    out = {}
    for role, p in upgrade_0_2_paths().items():
        if p.is_dir():
            out[role] = {str(f): f.read_bytes() for f in sorted(p.rglob("*")) if f.is_file()}
        elif p.exists():
            out[role] = p.read_bytes()
    return out


def rows(sql, *args):
    conn = sqlite3.connect(store.path())
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


@pytest.fixture
def soon(monkeypatch):
    real = upgrade_0_2.move
    monkeypatch.setattr(upgrade_0_2, "move", lambda t, e=None, **k: real(t, e, now=SOON))


# --------------------------------------------------------------------------- the move

def test_the_move_carries_every_row_and_makes_rows_for_row_less_mail(soon):
    zero_two_home()
    said = []
    store.open_write(report=said.extend).close()
    assert rows("SELECT COUNT(*) FROM messages") == [(7,)]
    assert rows("SELECT MIN(id), MAX(id) FROM messages") == [(IDS["m1"], IDS["m7"])]
    assert rows("SELECT value FROM meta WHERE key = 'next_id'") == [(str(IDS["m7"]),)]
    assert rows("SELECT room_id, name, kind FROM rooms") == [
        (MADE["names_room"], "#names", "names")]
    # 0.2's two held rows, then a row for each message 0.2 would have replayed: m3 above
    # the reviewer's cursor, and m4 and m7 for planner, who has no cursor. Nothing for
    # agent-3 or for herald, a send-only name.
    assert set(rows("SELECT name, message_id, held, state FROM queue")) == {
        ("reviewer@proj-a", IDS["m2"], 1, "waiting"), ("planner", IDS["m2"], 1, "waiting"),
        ("reviewer@proj-a", IDS["m3"], 0, "waiting"), ("planner", IDS["m4"], 0, "waiting"),
        ("planner", IDS["m7"], 0, "waiting")}
    assert rows("SELECT name, message_id FROM cursors") == [("reviewer@proj-a", IDS["m1"])]
    assert rows("SELECT id, qualified, released_at IS NOT NULL, fenced, kind FROM bindings "
                "ORDER BY id") == [(1, "reviewer@proj-a", 1, 0, "session"),
                                   (2, "planner", 1, 1, "session")]
    assert set(rows("SELECT name, scope, status, kind FROM identities")) == {
        ("reviewer", "proj-a", "active", "agent"), ("planner", "global", "active", "agent"),
        ("herald", "global", "active", "send-only"), ("old", "proj-a", "retired", "agent")}
    assert rows("SELECT name, module, state FROM reserved") == [("herald", None, "unowned")]
    assert rows("SELECT user_id, name FROM operators") == [
        (MADE["operator_user_id"], "operator")]
    assert rows("SELECT value FROM meta WHERE key = 'migrated_from'") == [("0.2",)]
    assert rows("SELECT value FROM meta WHERE key = 'writer'") == [("provisioner",)]
    assert said[0].startswith(f"moved comms 0.2's files into {store.path()}: 1 room(s), "
                              "7 message(s), 5 queue row(s) (3 made")
    assert upgrade_0_2.FENCED_LINE.format(name="planner") in said
    for p in upgrade_0_2_paths().values():
        assert not p.exists() and upgrade_0_2.moved_name(p).exists()
    assert not Path(f"{store.path()}.new").exists()


@pytest.fixture(params=["as-checked-out", "checked-out-wide"])
def checkout(request, monkeypatch, tmp_path):
    """The fixture as this checkout has it, and a copy whose token files are as wide as
    a checkout under umask 022 leaves them, so the lay-out is tested whatever the
    checkout's modes are."""
    if request.param == "checked-out-wide":
        wide = tmp_path / "fixture"
        shutil.copytree(FIXTURE, wide)
        os.chmod(wide / "tokens", 0o755)
        for f in (wide / "tokens").iterdir():
            os.chmod(f, 0o644)
        monkeypatch.setattr(sys.modules[__name__], "FIXTURE", wide)


def test_the_operator_token_and_provisioning_carry_over(soon, checkout):
    zero_two_home()
    record = LocalRecord()
    try:
        token = paths.tokens_dir() / "operator"
        assert stat.S_IMODE(token.stat().st_mode) == tokens.FILE_MODE, "left as 0.2 left it"
        held = token.read_text().split('"')[3]
        assert record.meta_get(META_OPERATOR_TOKEN_HASH) == token_hash(held)
        before = rows("SELECT * FROM identities ORDER BY name")
        # The daemon's start runs provisioning's step after the move; it finds the
        # carried directory and token complete and writes nothing.
        provision.first_start(tokens.TokenStore(None, env=os.environ))(
            record, store.path())
        assert record.meta_get(META_OPERATOR_TOKEN_HASH) == token_hash(held)
        assert rows("SELECT * FROM identities ORDER BY name") == before
    finally:
        record.close()


def test_a_second_open_moves_nothing(soon):
    zero_two_home()
    store.open_write().close()
    before = rows("SELECT * FROM queue ORDER BY name, message_id")
    said = []
    store.open_write(report=said.extend).close()
    assert said == [] and rows("SELECT * FROM queue ORDER BY name, message_id") == before


def test_a_name_without_a_cursor_gets_only_the_bounded_set(monkeypatch):
    zero_two_home()
    real = upgrade_0_2.move
    late = MADE["made_at"] + 25 * 3600
    monkeypatch.setattr(upgrade_0_2, "move", lambda t, e=None, **k: real(t, e, now=late))
    store.open_write().close()
    assert set(rows("SELECT name, message_id FROM queue")) == {
        ("reviewer@proj-a", IDS["m2"]), ("planner", IDS["m2"]),
        ("reviewer@proj-a", IDS["m3"])}


def test_a_move_stopped_after_its_commit_finishes_its_renames(soon, monkeypatch):
    found = zero_two_home()
    real = upgrade_0_2._rename
    monkeypatch.setattr(upgrade_0_2, "_rename",
                        lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        store.open_write()
    assert store.state_of(store.path()) == store.MARKED
    assert all(p.exists() for p in found.values())
    monkeypatch.setattr(upgrade_0_2, "_rename", real)
    said = []
    store.open_write(report=said.extend).close()
    assert said[0].startswith("finished the move")
    assert all(upgrade_0_2.moved_name(p).exists() for p in found.values())


def test_0_2_files_changed_after_the_move_are_refused_not_renamed(soon, monkeypatch):
    found = zero_two_home()
    real = upgrade_0_2._rename
    monkeypatch.setattr(upgrade_0_2, "_rename",
                        lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        store.open_write()
    monkeypatch.setattr(upgrade_0_2, "_rename", real)
    conn = sqlite3.connect(found["record"])
    conn.execute("INSERT INTO meta(key, value) VALUES ('later', '1')")
    conn.commit()
    conn.close()
    with pytest.raises(store.StoreRefused, match="changed after they were moved"):
        store.open_write()
    assert all(p.exists() for p in found.values())


def test_a_move_killed_before_its_rename_leaves_no_store(soon, monkeypatch):
    found = zero_two_home()
    before = contents()
    real = os.replace
    monkeypatch.setattr(upgrade_0_2.os, "replace",
                        lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        store.open_write()
    assert not store.path().exists()
    monkeypatch.setattr(upgrade_0_2.os, "replace", real)
    # What a killed run leaves behind is built again from the start.
    Path(f"{store.path()}.new").write_bytes(b"half a store")
    store.open_write().close()
    assert rows("SELECT COUNT(*) FROM messages") == [(7,)]
    assert before["record"]  # the files were there to move
    assert all(not p.exists() for p in found.values())


# --------------------------------------------------------------------------- refusals

def _refused(match):
    before = contents()
    with pytest.raises(store.StoreRefused, match=match) as e:
        store.open_write()
    assert "nothing was moved" in str(e.value)
    assert not store.path().exists() and not Path(f"{store.path()}.new").exists()
    assert contents() == before
    return e.value


def test_a_corrupt_database_is_refused():
    found = zero_two_home()
    found["bindings"].write_bytes(b"this is not a database" * 100)
    assert _refused("can't be opened|can't be read|quick_check").path == found["bindings"]


def test_an_unknown_column_is_refused():
    found = zero_two_home()
    conn = sqlite3.connect(found["record"])
    conn.execute("ALTER TABLE messages DROP COLUMN author_display")
    conn.commit()
    conn.close()
    _refused("messages.author_display")


def test_an_unknown_user_version_is_refused():
    found = zero_two_home()
    conn = sqlite3.connect(found["directory"])
    conn.execute("PRAGMA user_version = 7")
    conn.close()
    _refused("user_version 7")


def test_a_record_older_than_0_2_is_refused():
    found = zero_two_home()
    conn = sqlite3.connect(found["record"])
    conn.execute("DELETE FROM meta WHERE key = 'author_stamps'")
    conn.commit()
    conn.close()
    _refused("no author_stamps marker")


def test_a_bad_cursor_is_refused():
    zero_two_home()
    (paths.cursors_dir() / "local" / "planner.json").write_text('{"cursor": "x"}')
    _refused("doesn't read as a cursor")


def test_a_live_0_2_daemon_is_refused():
    zero_two_home()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    paths.old_daemon_socket().parent.mkdir(parents=True, exist_ok=True)
    s.bind(str(paths.old_daemon_socket()))
    s.listen(1)
    try:
        e = _refused("still answers")
        assert isinstance(e, upgrade_0_2.DaemonLive)
    finally:
        s.close()


def test_another_backends_cursor_files_are_named_and_skipped(soon):
    zero_two_home()
    (paths.cursors_dir() / "planner.json").write_text('{"cursor": "1"}')
    said = []
    store.open_write(report=said.extend).close()
    assert any("planner.json: not a cursor of the local record" in line for line in said)
    assert rows("SELECT name FROM cursors") == [("reviewer@proj-a",)]


def test_older_directory_shapes_are_carried_as_0_2_read_them(soon):
    """Rooms from before one room carry with no kind; an agents table with no kind
    column reads as `agent`; a webhooks table isn't read."""
    found = zero_two_home()
    conn = sqlite3.connect(found["directory"])
    conn.executescript("""
        CREATE TABLE webhooks (webhook_id TEXT PRIMARY KEY, secret TEXT);
        ALTER TABLE agents DROP COLUMN kind;
        CREATE TABLE rooms2 (channel_id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                             kind TEXT NOT NULL, created_at TEXT NOT NULL);
        INSERT INTO rooms2 SELECT * FROM rooms;
        DROP TABLE rooms;
        ALTER TABLE rooms2 RENAME TO rooms;
        INSERT INTO rooms VALUES ('ch-9', '#conference', 'conference', '2026-01-01T00:00:00+00:00');
    """)
    conn.close()
    said = []
    store.open_write(report=said.extend).close()
    assert set(rows("SELECT name, kind FROM rooms")) == {("#names", "names"),
                                                         ("#conference", None)}
    assert rows("SELECT DISTINCT kind FROM identities") == [("agent",)]
    assert rows("SELECT COUNT(*) FROM reserved") == [(0,)]
    assert any("#conference" in line for line in said)


# --------------------------------------------------------------------------- readers

def test_migrate_dry_run_writes_nothing_and_migrate_moves(soon, capsys):
    zero_two_home()
    before = contents()
    assert cli.main(["migrate", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would move comms 0.2's files" in out and "would rename" in out
    assert contents() == before and not store.path().exists()
    assert cli.main(["migrate"]) == 0
    out = capsys.readouterr().out
    assert "moved comms 0.2's files" in out and "planner: its session was launched" in out
    assert cli.main(["migrate"]) == 0
    assert "nothing to move" in capsys.readouterr().out


def test_migrate_exits_error_on_a_refusal(capsys):
    zero_two_home()
    store_path = store.path()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(store_path)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.close()
    assert cli.main(["migrate"]) == 1
    assert "wasn't made by moving them" in capsys.readouterr().out


def test_the_doctor_rows_follow_the_move(soon):
    zero_two_home()
    rows_now = upgrade_0_2.doctor_rows()
    assert [(r.state, r.detail) for r in rows_now] == [
        ("warn", "comms' 0.2 files aren't moved yet; they move at the daemon's next start")]
    store.open_write().close()
    found = {r.detail for r in upgrade_0_2.doctor_rows()}
    assert upgrade_0_2.FENCED_LINE.format(name="planner") in found
    assert any(d.startswith("comms 0.2's moved files are still here") for d in found)
    # A refusal the move would give is a fail row.
    paths.record_db().write_bytes(b"")
    assert [r.state for r in upgrade_0_2.doctor_rows()][0] == "fail"
