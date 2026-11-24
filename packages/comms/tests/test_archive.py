"""An older comms' files, read in place (`archive.py`): its record's messages in
`history` and by id, its retired names refused, and the files never changed.

The older comms kept one record and one directory, and its verbs read them: `history`,
`explain` and `reply` found every message in its record, and a name its directory
retired was refused at bind and as a recipient. Each test here builds the older files in
that comms' own shape and names them in `[archive]` before the rig's daemon starts."""

from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest
from conftest import wait_until, write_config

from moxi_comms import archive, directory
from moxi_comms.client import DaemonClient, DaemonError
from moxi_comms.mcp import tools as mcp_tools
from moxi_comms.paths import comms_db, daemon_socket
from moxi_comms.store import books
from moxi_comms.store.schema import ID_BASE
from moxi_dev.testing import toml_string

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
    # The older daemon's queue, as it stood when that comms stopped.
    conn.execute("INSERT INTO queue VALUES (?, 'main@proj-a')", (OLD_ASK,))
    conn.commit()
    conn.close()
    # The older comms' cursor files beside its record: main's short of the old ask,
    # planner's past it.
    cursors = older_dir / "cursors" / "local"
    cursors.mkdir(parents=True)
    (cursors / "main@proj-a.json").write_text(json.dumps(
        {"cursor": str(OLD_ASK - 1), "updated_at": "2026-10-01T09:00:00+00:00"}))
    (cursors / "planner@proj-a.json").write_text(json.dumps(
        {"cursor": str(OLD_ASK + 1), "updated_at": "2026-10-01T11:00:00+00:00"}))
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
    return {str(p.relative_to(older_dir)): (hashlib.sha256(p.read_bytes()).hexdigest()
                                            if p.is_file() else "dir")
            for p in sorted(older_dir.rglob("*"))}


@pytest.fixture
def older(tmp_path):
    older_dir = tmp_path / "older-comms"
    older_dir.mkdir()
    record, identities = _build(older_dir)
    write_config(None, f'[archive]\nrecord = {toml_string(record)}\nidentities = {toml_string(identities)}\n')
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


def test_the_archive_is_read_once_at_the_start_not_when_the_config_changes(older, rig):
    """`[archive]` is read at the daemon's start: a config.toml changed later leaves the
    archive it read, and the connections its threads hold, as they were."""
    first = archive.current()
    assert first is not None and first.record is not None
    write_config(None, '[archive]\nrecord = ""\n')
    assert archive.current() is first
    code, out = rig.cli(["history", "planner@proj-a"])
    assert code == 0 and "what did the old build decide?" in out, out


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


def test_the_history_tool_pages_back_to_the_older_record_with_skip(older, rig):
    """The tool reads at most `HISTORY_LIMIT` messages at once, so `skip` is its way
    further back: it passes over the newest messages first, into the older record."""
    for n in range(3):
        rig.post_as("main@proj-a", ["planner@proj-a"], f"newer {n}")
    tools = mcp_tools.Tools(rig.daemon)

    def page(**params):
        got = tools.history("planner@proj-a", "no-claim",
                            {"identity": "planner@proj-a", "limit": 2, **params})
        assert not got.is_error, got.content[0].text
        return got.structured_content["messages"]

    assert [m["text"] for m in page()] == ["newer 1", "newer 2"]
    found, skip = None, 0
    while found is None and skip <= 20:
        rows = page(skip=skip)
        assert rows, f"paged past every message at skip {skip} without the older ask"
        found = next((m for m in rows if m["id"] == str(OLD_ASK)), None)
        skip += 2
    assert found is not None, "skip reaches the older record"
    assert skip > 2 and found["state"] == "archived"
    assert page(skip=10_000) == [], "past the oldest message there is nothing"
    bad = tools.history("planner@proj-a", "no-claim", {"limit": 2, "skip": -1})
    assert bad.is_error and "skip -1" in bad.content[0].text


def test_a_reply_to_an_older_message_reaches_its_author(older, rig):
    elder = rig.shim(name="elder")
    code, out = rig.cli(["reply", "--posture", "none", str(OLD_ASK), "it kept the hold."],
                        name="planner", scope="proj-a")
    assert code == 0, out
    assert "replying to elder" in out
    assert wait_until(lambda: elder.queue), "the reply reached the older author"
    assert f"re:{OLD_ASK}" in rig.record.get_message(elder.queue[0]["message_id"]).content


def test_explain_of_an_older_message_reads_the_older_queue_and_cursor_files(older, rig):
    """The older comms' explain answered from its daemon's memory, its queue and the
    name's cursor file, which it compared with the message id. Asked about an older
    message, explain reads that queue and that cursor file for the name, compares the
    cursor with the id as that explain did, takes none of comms.db's books as the
    message's, and says plainly that the daemon's memory is gone, with the daemon up or
    down. An id in the skipped range that the older record holds as no mail is not
    called an older message."""
    c = DaemonClient(daemon_socket()).connect()
    try:
        held = c.call("explain", {"identity": "main@proj-a", "message_id": str(OLD_ASK)})
        handed = c.call("explain", {"identity": "planner@proj-a",
                                    "message_id": str(OLD_ASK)})
        notice = c.call("explain", {"identity": "main@proj-a",
                                    "message_id": str(OLD_NOTE)})
    finally:
        c.close()
    assert held["archive"]["queued"] is True
    assert held["archive"]["cursor"]["reason"] == archive.BEHIND
    assert held["archive"]["cursor"]["position"] == str(OLD_ASK - 1)
    assert handed["archive"]["queued"] is False
    assert handed["archive"]["cursor"]["reason"] == archive.ALREADY_PAST
    for answer in (held, handed):
        assert answer["case"] is None, "comms.db's classifier is not this message's"
        assert answer["cursor"]["reason"] not in (books.ALREADY_PAST, books.BEHIND)
    assert notice["archive"] is None, "a #notices id is not an older message"

    code, out = rig.cli(["explain", "main@proj-a", str(OLD_ASK)], name="planner",
                        scope="proj-a")
    assert code == 0, out
    assert out.splitlines()[0] == (f"main@proj-a   from the older comms' record   "
                                   f"message {OLD_ASK}")
    flat = " ".join(out.split())
    assert "still held it in main@proj-a's queue when it stopped" in flat
    assert (f"Its cursor file for main@proj-a stands at {OLD_ASK - 1}, written "
            "2026-10-01T09:00:00+00:00: short of this message") in flat
    assert "daemon's memory, which is gone" in flat
    code, out = rig.cli(["explain", "planner@proj-a", str(OLD_ASK)], name="planner",
                        scope="proj-a")
    assert "at or past this message" in " ".join(out.split())
    code, out = rig.cli(["explain", "main@proj-a", str(OLD_NOTE)], name="planner",
                        scope="proj-a")
    assert "from the older comms' record" not in out

    rig.daemon.stop()
    code, out = rig.cli(["explain", "main@proj-a", str(OLD_ASK)], name="planner",
                        scope="proj-a")
    assert code == 75, out
    assert f"main@proj-a   from the older comms' record   message {OLD_ASK}" in out
    assert "short of this message" in " ".join(out.split())


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
