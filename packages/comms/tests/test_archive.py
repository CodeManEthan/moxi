"""An older comms' files, read in place (`archive.py`): its record's messages in
`history` and by id, its retired names refused, and the files never changed.

The older comms kept one record and one directory, and its verbs read them: `history`,
`explain` and `reply` found every message in its record, and a name its directory
retired was refused at bind and as a recipient. Each test here builds the older files in
that comms' own shape and names them in `[archive]` before the rig's daemon starts."""

from __future__ import annotations

import hashlib
import sqlite3

import pytest
from conftest import wait_until, write_config

from moxi_comms import archive, directory
from moxi_comms.client import DaemonClient, DaemonError
from moxi_comms.mcp import tools as mcp_tools
from moxi_comms.paths import comms_db, daemon_socket
from moxi_comms.store.schema import ID_BASE

#: The older record's ids: one the store had already used when the counter moved (so
#: it is reached through history only), two above it, and the counter's last id.
SHADOWED, OLD_ASK, OLD_NOTE, LAST = ID_BASE + 1, ID_BASE + 5_000, ID_BASE + 9_000, \
    ID_BASE + 10_000

RETIRED_BIND = ("ghost@proj-old is retired: comms keeps a project suffix only to tell "
                "apart two agents of one name in different projects, and ghost@proj-old "
                "had no such twin. Bind ghost instead, with no --scope.")
RETIRED_SEND = ("ghost@proj-old is retired: comms keeps a project suffix only to tell "
                "apart two agents of one name in different projects, and ghost@proj-old "
                "had no such twin. Send to ghost instead. Nothing was sent.")


@pytest.fixture
def backend():
    """The archive is the local store's: comms.db's counter moves past its ids."""
    return "local"


def _build(older_dir):
    record, identities = older_dir / "record.db", older_dir / "directory.db"
    conn = sqlite3.connect(record)
    conn.executescript("""
        CREATE TABLE channels (channel_id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                               created_at TEXT NOT NULL);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, channel_id TEXT NOT NULL,
                               author_id TEXT NOT NULL, author_kind TEXT NOT NULL,
                               author_display TEXT NOT NULL, content TEXT NOT NULL,
                               timestamp TEXT NOT NULL);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE queue (message_id INTEGER NOT NULL, name TEXT NOT NULL,
                            PRIMARY KEY (name, message_id));
        INSERT INTO channels VALUES ('ch-1', '#names', '2026-09-23T19:13:37Z'),
                                    ('ch-2', '#notices', '2026-09-23T19:13:37Z');
    """)
    rows = [
        (SHADOWED, "ch-1", "wh-1", "webhook", "elder",
         "::to planner@proj-a [no reply]\nthe first message, before the counter moved",
         "2026-09-23T19:40:30Z"),
        (OLD_ASK, "ch-1", "wh-1", "webhook", "elder",
         "::to planner@proj-a\nwhat did the old build decide?", "2026-10-01T10:00:00Z"),
        (OLD_NOTE, "ch-2", "wh-2", "webhook", "notices", "a notice, not mail",
         "2026-10-02T10:00:00Z"),
    ]
    conn.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", rows)
    conn.execute("INSERT INTO meta VALUES ('next_id', ?)", (str(LAST),))
    conn.commit()
    conn.close()
    conn = sqlite3.connect(identities)
    conn.executescript("""
        CREATE TABLE agents (name TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'global',
                             status TEXT NOT NULL DEFAULT 'active',
                             created_at TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'agent',
                             PRIMARY KEY (name, scope));
        INSERT INTO agents VALUES ('ghost', 'proj-old', 'retired', '2026-09-01', 'agent'),
                                  ('main', 'proj-a', 'active', '2026-09-01', 'agent');
    """)
    conn.commit()
    conn.close()
    return record, identities


def _fingerprint(older_dir) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(older_dir.iterdir())}


@pytest.fixture
def older(tmp_path):
    older_dir = tmp_path / "older-comms"
    older_dir.mkdir()
    record, identities = _build(older_dir)
    write_config(None, f'[archive]\nrecord = "{record}"\nidentities = "{identities}"\n')
    before = _fingerprint(older_dir)
    yield older_dir
    assert _fingerprint(older_dir) == before, "the older files are never changed"


def test_the_start_moves_the_counter_past_the_archives_ids(older, rig):
    """At its start the daemon moves comms.db's counter to the older record's last id
    once, and an id in the range it skipped reads from the older record."""
    conn = sqlite3.connect(comms_db())
    try:
        low, high = archive.skipped(conn)
    finally:
        conn.close()
    assert low < OLD_ASK and high == LAST
    m = rig.post_as("planner@proj-a", ["main@proj-a"], "after the move")
    assert int(m.id) > LAST, "comms.db never writes an id the older record holds"

    old = rig.record.get_message(str(OLD_ASK))
    assert old is not None and old.content.endswith("what did the old build decide?")
    assert old.author_display == "elder"
    assert rig.record.get_message(str(SHADOWED)) is None or \
        "before the counter moved" not in rig.record.get_message(str(SHADOWED)).content, \
        "an id comms.db had already used is comms.db's alone"
    assert archive.skip_ids(rig.record) is None, "once"


def test_history_lists_the_older_record_first(older, rig):
    code, out = rig.cli(["history", "planner@proj-a"])
    assert code == 0, out
    assert f"{OLD_ASK} " in out and "what did the old build decide?" in out
    assert "before the counter moved" in out, "history reaches every older message"
    assert "a notice, not mail" not in out, "only the older #names room"
    rig.post_as("main@proj-a", ["planner@proj-a"], "a new one")
    code, out = rig.cli(["history", "planner@proj-a"])
    assert out.index("what did the old build decide?") < out.index("a new one")


def test_the_history_tool_reads_the_same_record_as_the_verb(older, rig):
    """The session's `history` tool lists what `moxi comms history` lists, older record
    first, and calls an older message `archived`: comms.db's books say nothing of how
    one went."""
    rig.post_as("main@proj-a", ["planner@proj-a"], "a new one")
    got = mcp_tools.Tools(rig.daemon).history(
        "planner@proj-a", "no-claim", {"identity": "planner@proj-a", "limit": 20})
    assert not got.is_error, got.content[0].text
    rows = {m["id"]: m for m in got.structured_content["messages"]}
    assert rows[str(OLD_ASK)]["text"] == "what did the old build decide?"
    assert rows[str(OLD_ASK)]["from"] == "elder"
    assert rows[str(OLD_ASK)]["state"] == "archived"
    newest = got.structured_content["messages"][-1]
    assert newest["text"] == "a new one" and newest["state"] != "archived"
    code, out = rig.cli(["history", "planner@proj-a", "--limit", "20"])
    assert code == 0, out
    assert got.content[0].text.splitlines() == out.splitlines()


def test_a_reply_to_an_older_message_reaches_its_author(older, rig):
    elder = rig.shim(name="elder")
    code, out = rig.cli(["reply", "--posture", "none", str(OLD_ASK), "it kept the hold."],
                        name="planner", scope="proj-a")
    assert code == 0, out
    assert "replying to elder" in out
    assert wait_until(lambda: elder.queue), "the reply reached the older author"
    assert f"re:{OLD_ASK}" in rig.record.get_message(elder.queue[0]["message_id"]).content


def test_a_name_the_archive_retired_is_refused_at_bind_and_as_a_recipient(older, rig):
    conn = directory.connect(comms_db(), read_only=True)
    try:
        assert directory.retired(conn, "ghost", "proj-old")
        assert not directory.retired(conn, "ghost", "global")
    finally:
        conn.close()
    c = DaemonClient(daemon_socket()).connect()
    try:
        with pytest.raises(DaemonError) as e:
            c.bind(name="ghost", scope="proj-old", adapter="claude-shim")
        assert str(e.value) == RETIRED_BIND
    finally:
        c.close()
    code, out = rig.cli(["send", "--posture", "none", "ghost@proj-old", "x"],
                        name="planner", scope="proj-a")
    assert code == 3 and out.strip() == RETIRED_SEND
