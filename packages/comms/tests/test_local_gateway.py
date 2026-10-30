"""The local backend: the things only a SQLite record can get wrong.

Everything the seam shares with the other backend is covered by the parametrised
suites (conftest's `BOTH_BACKENDS`). What is here is local-only: the id counter surviving a
trim, two PROCESSES writing without collision, N subscriptions each seeing each id once,
retention, the two reads the daemon does in bursts staying cheap on a full record, and the
daemon refusing a `--gateway` flag that disagrees with the box.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest
from conftest import wait_until, write_config

from moxi_comms import config, daemon as daemon_mod, gateway as gateway_mod, paths
from moxi_comms.gateway import Forbidden, GatewayError
from moxi_comms.local_gateway import (
    ID_BASE,
    META_OPERATOR_TOKEN_HASH,
    LocalGateway,
    token_hash)
from moxi_comms.mock_gateway import MockGateway


@pytest.fixture
def gw(tmp_path):
    """A record of its own, with the pump fast enough for a test to wait on it."""
    gateway = LocalGateway(tmp_path / "record.db", poll_interval=0.02)
    try:
        yield gateway
    finally:
        gateway.close()


def post(gateway, channel_id, text, username="planner"):
    return gateway.post_as_identity(username, channel_id, text)


def test_both_backends_expose_the_remaining_posting_contract():
    active = {"post_as_user", "post_as_identity", "history", "get_message",
              "subscribe", "create_channel", "delete_channel", "register_principal"}
    retired = {"create_webhook", "resolve_secret", "execute_webhook", "webhook_channel"}
    for backend in (LocalGateway, MockGateway):
        assert all(callable(getattr(backend, name, None)) for name in active)
        assert all(not hasattr(backend, name) for name in retired)


# --------------------------------------------------------------------------- the file

def test_the_record_lives_in_state_beside_the_cursors_and_tokens():
    """STATE, per box, and not DATA."""
    assert paths.record_db() == paths.cursors_dir().parent / "record.db"
    assert paths.record_db().parent == paths.tokens_dir().parent


def test_the_file_is_wal_with_its_pragmas(gw):
    conn = gw._conn()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1        # NORMAL
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")


@POSIX
def test_a_new_record_is_created_private(tmp_path):
    gateway = LocalGateway(tmp_path / "state" / "record.db")
    try:
        assert (tmp_path / "state" / "record.db").stat().st_mode & 0o777 == 0o600
    finally:
        gateway.close()


@POSIX
def test_an_existing_record_keeps_its_mode_and_is_not_rewritten(tmp_path):
    """The record is reported, never changed: an existing file is opened as it is."""
    import sqlite3

    path = tmp_path / "record.db"
    first = LocalGateway(path)
    first.close()
    path.chmod(0o644)
    before = path.read_bytes()
    second = LocalGateway(path)
    try:
        second._conn().execute("SELECT 1")
        assert path.stat().st_mode & 0o777 == 0o644
        assert path.read_bytes() == before
    finally:
        second.close()
    with sqlite3.connect(path) as plain:
        assert plain.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_release_closes_the_calling_threads_connection_and_only_that(gw):
    import sqlite3
    import threading

    mine = gw._conn()
    theirs: list = []
    held = threading.Event()
    done = threading.Event()

    def other():
        theirs.append(gw._conn())
        held.set()
        done.wait(10)
        gw.release()

    thread = threading.Thread(target=other)
    thread.start()
    held.wait(10)
    assert len(gw._conns) == 2
    gw.release()
    assert gw._conns == [theirs[0]]
    with pytest.raises(sqlite3.ProgrammingError):
        mine.execute("SELECT 1")
    theirs[0].execute("SELECT 1")               # the other thread's is still open
    done.set()
    thread.join(10)
    assert gw._conns == []
    gw.release()                                # a thread with none: nothing happens
    again = gw._conn()                          # and the next call opens a new one
    assert again is not mine and gw._conns == [again]
    assert gw.channel_by_name("nothing") is None


def test_each_client_the_daemon_serves_closes_its_connection_when_it_leaves(rig):
    """One connection per client used to stay open for the daemon's life: each thread
    serving a client now releases its own when the client goes."""
    import sqlite3

    local = rig.backend == "local"
    released = []
    real = rig.gateway.release

    def watching():
        conn = getattr(rig.gateway._local, "conn", None) if local else None
        real()
        # Counted only once the real release has closed it, so the checks below never
        # run a statement on a connection still being closed.
        released.append(conn)

    rig.gateway.release = watching
    for n in range(3):
        rig.shim(name=f"leaver{n}", scope="proj-a").stop()
    assert wait_until(lambda: len(released) >= 3)
    if local:
        assert all(conn is not None for conn in released)   # each served on the record
        assert not any(conn in rig.gateway._conns for conn in released)
        for conn in released:
            with pytest.raises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")


def test_the_schema_has_no_retired_credential_table_or_reply_column(gw):
    conn = gw._conn()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"channels", "principals", "denied", "messages", "meta", "queue"} == tables
    columns = [r[1] for r in conn.execute("PRAGMA table_info(messages)")]
    # `Message`'s seven fields and nothing else
    assert columns == ["id", "channel_id", "author_id", "author_kind", "author_display",
                       "content", "timestamp"]
    assert "reply_to" not in columns


# --------------------------------------------------------------------------- ids

def test_the_counter_outlives_a_trim_to_empty(gw):
    """The regression: `MAX(id)+1` over a table retention also empties
    hands out an id every reader's cursors already compare as past."""
    channel = gw.create_channel("#names")
    first = post(gw, channel, "one")
    last = post(gw, channel, "two")
    assert gw.trim(retain_days=0) == 0, "0 disables trimming"
    gw.clock = lambda: time.time() + 100 * 86400        # a hundred days pass
    assert gw.trim() == 2
    assert gw.history(channel) == []
    after = post(gw, channel, "three")
    assert int(after.id) > int(last.id) > int(first.id)


def test_every_id_comes_from_one_sequence_above_every_older_id(gw):
    channel = gw.create_channel("#names")
    principal = gw.register_principal("comms-reader")
    msg = post(gw, channel, "hello")
    assert channel.startswith("ch-")
    numbers = [int(channel.removeprefix("ch-")), int(principal.removeprefix("pr-")),
               int(msg.id)]
    assert numbers == sorted(numbers) and len(set(numbers)) == 3
    assert all(n > ID_BASE for n in numbers)
    # message ids ride the wire as bare digits, exactly as today
    assert msg.id.isdigit()


def test_two_processes_writing_never_collide_and_never_go_backwards(gw, tmp_path):
    """`BEGIN IMMEDIATE` serialises the writers on SQLite's own lock.

    A real second PROCESS, because that is the case the counter exists for: the CLI
    posting while the daemon posts a notice, two connections, one file.
    """
    channel = gw.create_channel("#names")
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD_WRITER, str(tmp_path / "record.db"), channel, "40"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    mine = [int(post(gw, channel, f"parent {i}").id) for i in range(40)]
    out, err = child.communicate(timeout=60)
    assert child.returncode == 0, err
    theirs = [int(line) for line in out.split()]

    assert mine == sorted(mine), "this process's own ids never went backwards"
    assert theirs == sorted(theirs), "the other process's ids never went backwards"
    assert not set(mine) & set(theirs), "no id was issued twice"
    everything = [int(m.id) for m in gw.history(channel)]
    assert everything == sorted(set(mine) | set(theirs))


CHILD_WRITER = """
import sys
from moxi_comms.local_gateway import LocalGateway

record, channel, count = sys.argv[1:4]
gw = LocalGateway(record, poll_interval=60)
for i in range(int(count)):
    print(gw.post_as_identity("helper", channel, "child %d" % i).id)
gw.close()
"""


def test_the_identity_post_obeys_the_cap_and_denial(gw):
    channel = gw.create_channel("#names")
    gw.deny_send("planner", channel)
    with pytest.raises(Forbidden):
        gw.post_as_user("planner", channel, "denied")


def test_the_cap_is_sixteen_thousand(gw):
    """One cap on one message, 16,000 characters."""
    channel = gw.create_channel("#names")
    assert gw.max_content_length == 16_000
    assert post(gw, channel, "x" * 16_000).content == "x" * 16_000
    with pytest.raises(GatewayError, match="cap on one message"):
        post(gw, channel, "x" * 16_001)
    assert len(gw.history(channel)) == 1


def test_an_identity_post_writes_one_stamped_row_on_both_backends(rig):
    message = rig.gateway.post_as_identity(
        "planner@proj-a", rig.names_channel, "bound sender")

    assert (message.author_kind, message.author_id, message.author_display) == \
        ("identity", "planner@proj-a", "planner@proj-a")
    assert rig.gateway.history(rig.names_channel) == [message]


def test_an_overlong_identity_post_writes_nothing_on_both_backends(rig):
    with pytest.raises(GatewayError, match="cap on one message"):
        rig.gateway.post_as_identity(
            "planner@proj-a", rig.names_channel,
            "x" * (rig.gateway.max_content_length + 1))

    assert rig.gateway.history(rig.names_channel) == []


def test_the_reader_denial_is_a_real_check(gw):
    channel = gw.create_channel("#names")
    reader = gw.register_principal("comms-reader")
    assert gw.can_send(reader, channel)
    gw.deny_send(reader, channel)
    assert not gw.can_send(reader, channel)
    gw.deny_send(reader, channel)              # idempotent, as provision calls it


# --------------------------------------------------------------------------- operator

def test_the_operator_lane_compares_the_token_against_the_stored_hash(gw):
    """The lane needs a credential like every other lane. The SENDER side of the guard
    is the send verb's; the compare is this backend's."""
    channel = gw.create_channel("#names")
    assert gw.post_as_user("900001", channel, "before provisioning").author_kind == "user"
    gw.meta_set(META_OPERATOR_TOKEN_HASH, token_hash("the-operator-token"))
    assert gw.post_as_user("900001", channel, "with the token",
                           token="the-operator-token").content == "with the token"
    with pytest.raises(Forbidden, match="bad operator token"):
        gw.post_as_user("900001", channel, "with a guess", token="guessed")
    with pytest.raises(Forbidden, match="bad operator token"):
        # A stored hash makes the token REQUIRED: omitting it is not a pass.
        gw.post_as_user("900001", channel, "without one")
    assert len(gw.history(channel)) == 2


def test_a_registered_principal_wears_its_own_display(gw):
    channel = gw.create_channel("#names")
    operator = gw.register_principal("operator")
    assert gw.post_as_user(operator, channel, "hi").author_display == "operator"
    assert gw.post_as_user("900001", channel, "hi").author_display == "operator"


# --------------------------------------------------------------------------- the record

def test_history_with_a_limit_takes_the_tail(gw):
    channel = gw.create_channel("#names")
    other = gw.create_channel("#elsewhere")
    ids = [post(gw, channel, f"m{i}").id for i in range(10)]
    post(gw, other, "another channel")
    assert [m.id for m in gw.history(channel)] == ids
    assert [m.id for m in gw.history(channel, limit=3)] == ids[-3:]
    assert [m.id for m in gw.history(channel, after=ids[6])] == ids[7:]
    assert [m.id for m in gw.history(channel, after=ids[0], limit=2)] == ids[-2:]
    assert len(gw.history(other)) == 1, "a channel's history is its own"


def test_get_message_and_max_message_id(gw):
    channel = gw.create_channel("#names")
    other = gw.create_channel("#elsewhere")
    assert gw.max_message_id() is None and gw.max_message_id(channel) is None
    msg = post(gw, channel, "one")
    assert gw.get_message(msg.id).content == "one"
    assert gw.get_message("4611686018427387000") is None
    assert gw.get_message("not-a-message-id") is None
    assert gw.max_message_id() == msg.id == gw.max_message_id(channel)
    assert gw.max_message_id(other) is None


# --------------------------------------------------------------------------- retention

def test_retention_drops_what_is_past_the_window_and_keeps_the_rest(gw):
    channel = gw.create_channel("#names")
    now = time.time()
    gw.clock = lambda: now - 120 * 86400
    ancient = post(gw, channel, "a hundred and twenty days ago")
    gw.clock = lambda: now - 10 * 86400
    recent = post(gw, channel, "ten days ago")
    gw.clock = lambda: now

    assert gw.trim(retain_days=0) == 0
    assert len(gw.history(channel)) == 2
    assert gw.trim() == 1                       # the default is 90 days
    assert [m.id for m in gw.history(channel)] == [recent.id]
    assert gw.get_message(ancient.id) is None


def test_the_daemon_trims_at_boot_and_only_where_there_is_a_record(rig):
    """The daemon is the process that runs all day, so the daily trim is its job.

    On the mock there is nothing to trim, and `trim_record` has to be a no-op rather
    than an error: one daemon, three backends.
    """
    assert rig.daemon._last_trim > 0, "the boot trim ran, or was asked and declined"
    rig.daemon.trim_record()
    assert hasattr(rig.gateway, "trim") is (rig.backend == "local")


# --------------------------------------------------------------------------- fanout

def test_two_subscriptions_each_see_every_id_exactly_once(gw):
    """The pump iterates `_subs` per pass and each subscription's `last_id` moves
    under the gateway's own lock."""
    channel = gw.create_channel("#names")
    first = gw.subscribe(from_id="0")
    second = gw.subscribe(from_id="0")
    assert first.next_event(timeout=1.0)["t"] == "READY"
    assert second.next_event(timeout=1.0)["t"] == "READY"

    posted = [post(gw, channel, f"m{i}").id for i in range(25)]
    for sub in (first, second):
        seen = []
        while len(seen) < len(posted):
            event = sub.next_event(timeout=2.0)
            assert event is not None, f"only saw {len(seen)} of {len(posted)}"
            assert event["t"] == "MESSAGE_CREATE"
            seen.append(event["d"]["id"])
        assert seen == posted, "in id order, each id once"
        assert sub.next_event(timeout=0.1) is None, "and nothing twice"
    gw.unsubscribe(first)
    gw.unsubscribe(second)


def test_a_subscription_says_ready_first_so_the_daemon_sees_the_shapes_it_knows(gw):
    sub = gw.subscribe()
    assert sub.next_event(timeout=1.0) == {"t": "READY", "resumed": True}
    gw.unsubscribe(sub)


def test_the_pump_finds_what_another_process_wrote(gw, tmp_path):
    """Layer 3, the safety net: no nudge arrives from another process (that is the
    daemon's socket op), and the poll finds the row anyway."""
    channel = gw.create_channel("#names")
    sub = gw.subscribe(from_id="0")
    assert sub.next_event(timeout=1.0)["t"] == "READY"
    out = subprocess.run(
        [sys.executable, "-c", CHILD_WRITER, str(tmp_path / "record.db"), channel, "1"],
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    event = sub.next_event(timeout=3.0)
    assert event["t"] == "MESSAGE_CREATE" and event["d"]["id"] == out.stdout.strip()
    gw.unsubscribe(sub)


def test_nudge_wakes_the_pump_without_waiting_out_the_interval(tmp_path):
    gateway = LocalGateway(tmp_path / "record.db", poll_interval=30)
    try:
        channel = gateway.create_channel("#names")
        sub = gateway.subscribe(from_id="0")
        assert sub.next_event(timeout=1.0)["t"] == "READY"
        # a row written behind the gateway's back: only the pump can find it, and only
        # the nudge can make it look before the 30-second interval is up
        with gateway._write() as conn:
            gateway._insert(conn, channel_id=channel, author_id="planner",
                            author_kind="identity", author_display="planner",
                            content="nudged")
        assert sub.next_event(timeout=0.3) is None
        gateway.nudge()
        event = sub.next_event(timeout=2.0)
        assert event is not None and event["d"]["content"] == "nudged"
    finally:
        gateway.close()


def test_a_reconnect_without_resume_makes_the_daemon_rescan(gw):
    channel = gw.create_channel("#names")
    sub = gw.subscribe(from_id="0")
    assert sub.next_event(timeout=1.0)["t"] == "READY"
    gw.disconnect()
    post(gw, channel, "into the gap")
    assert sub.next_event(timeout=0.2) is None
    gw.reconnect(resume=False)
    assert sub.next_event(timeout=1.0) == {"t": "READY", "resumed": False}
    assert sub.next_event(timeout=0.2) is None, "a fresh IDENTIFY loses the gap"


# --------------------------------------------------------------------------- the reads

def test_the_daemons_two_hot_reads_stay_cheap_on_a_full_record(gw):
    """`max_message_id` is the register boundary read (taken in bursts at a restart)
    and `history(limit=RESCAN_LIMIT)` is the boot rescan. Both are index walks, so the
    target is under 5 ms on 100,000 rows.

    Two claims, because a wall clock alone cannot carry this inside a suite:

      * the PLAN, which is the statement that matters and cannot flake. Neither read may
        SCAN `messages`: both walk the primary key or `messages_channel`, which is what
        makes the cost O(log n) rather than O(n).
      * the COST, the 5 ms target, measured as CPU this thread spent
        (`time.thread_time`) and not as wall clock. Wall clock here measures the box,
        not the query: run after `test_guards.py`, which leaves a `Daemon` unstopped and
        its event loop spinning, the same reads take a thousand times longer by the wall
        and exactly as long by the clock that counts work. Best of fifty passes after a
        warm-up, so a scheduler hiccup cannot fail a test about a query plan.

    Nothing here reads the channel whole to show the ratio, deliberately: building
    100,000 `Message` objects inside a process the size of this suite spends its time in
    the garbage collector, and a fixture that slow is a test that looks hung.
    """
    channel = gw.create_channel("#names")
    fill(gw, channel, 100_000)
    assert gw._conn().execute("SELECT count(*) FROM messages").fetchone()[0] == 100_000

    for sql, params in (
            ("SELECT MAX(id) FROM messages", ()),
            ("SELECT MAX(id) FROM messages WHERE channel_id = ?", (channel,)),
            ("SELECT id FROM messages WHERE channel_id = ? ORDER BY id DESC LIMIT ?",
             (channel, 500)),
            ("SELECT id FROM messages WHERE channel_id = ? AND id > ? "
             "ORDER BY id DESC LIMIT ?", (channel, 0, 500))):
        plan = " ".join(row[3] for row in
                        gw._conn().execute("EXPLAIN QUERY PLAN " + sql, params))
        assert "SCAN" not in plan, f"{sql} reads the table, not an index: {plan}"

    def best(call, passes=50):
        for _ in range(3):
            call()
        return min(_timed(call) for _ in range(passes))

    assert best(lambda: gw.max_message_id(channel)) < 0.005
    assert best(lambda: gw.max_message_id()) < 0.005
    assert best(lambda: gw.history(channel, limit=500)) < 0.005
    assert len(gw.history(channel, limit=500)) == 500


def _timed(call) -> float:
    """CPU seconds THIS thread spent in `call`.

    Not `perf_counter`: a read that costs 0.6 ms of work reads as a second of wall clock
    when another thread in the process is spinning, which measures the suite and not the
    index. Every read here is served out of the page cache, so the CPU the thread spends
    is the whole of what the query costs.
    """
    started = time.thread_time()
    call()
    return time.thread_time() - started


def fill(gateway, channel_id, count):
    """`count` rows straight into the table: this is a fixture, not a posting path.

    One statement, generated inside SQLite by a recursive CTE, rather than 100,000 tuples
    built in Python and handed to `executemany`. The Python version takes seconds alone
    and minutes inside the full suite, because every row wants the GIL back and the rig's
    daemon threads are competing for it: a fixture that slow is a test that looks hung.
    """
    with gateway._write() as conn:
        start = gateway._next_id(conn)
        conn.execute(
            "WITH RECURSIVE seq(n) AS (SELECT 0 UNION ALL SELECT n + 1 FROM seq "
            "                          WHERE n + 1 < ?) "
            "INSERT INTO messages(id, channel_id, author_id, author_kind, author_display, "
            "                     content, timestamp) "
            "SELECT ? + n, ?, 'wh-filler', 'webhook', 'filler', 'line ' || n, ? FROM seq",
            (count, start, channel_id, "2026-09-12T00:00:00Z"))
        conn.execute("UPDATE meta SET value = ? WHERE key = 'next_id'",
                     (str(start + count - 1),))


# --------------------------------------------------------------------------- selection

def test_local_is_one_of_two_backends_and_open_gateway_returns_it(monkeypatch, tmp_path):
    assert gateway_mod.BACKENDS == ("local", "mock")
    monkeypatch.setenv("MOXI_COMMS_GATEWAY", "local")
    monkeypatch.setenv("MOXI_COMMS_LOCAL_RECORD", str(tmp_path / "elsewhere.db"))
    assert gateway_mod.backend_name() == "local"
    opened = gateway_mod.open_gateway(role="reader")
    try:
        assert isinstance(opened, LocalGateway)
        assert opened.path == tmp_path / "elsewhere.db"
        assert not hasattr(opened, "role")        # selection accepts it; record does not use it
        assert opened.retain_days == 90
    finally:
        opened.close()


def test_the_local_table_is_read_and_a_token_shaped_key_in_it_is_still_refused():
    write_config(None, 'backend = "local"\n\n[local]\nrecord = "/srv/record.db"\n'
                       'retain_days = 30\n')
    loaded = config.load()
    assert (loaded.backend, loaded.local.record, loaded.local.retain_days) == \
        ("local", "/srv/record.db", 30)
    env = dict(os.environ, MOXI_COMMS_LOCAL_RECORD="/srv/other.db")
    assert config.load(env=env).local.record == "/srv/other.db"

    write_config(None, '[local]\nrecord_token = "abc"\n')
    with pytest.raises(config.ConfigError, match="looks like a secret"):
        config.load()


@pytest.mark.parametrize("value", ['"forever"', "-1"])
def test_a_retain_days_that_is_not_a_count_of_days_is_a_refusal(value):
    write_config(None, f"[local]\nretain_days = {value}\n")
    with pytest.raises(config.ConfigError) as caught:
        config.load()
    assert caught.value.key == "local.retain_days"


def test_the_retention_has_one_owner():
    """`[local] retain_days` is declared once, in the schema; the gateway's default is
    that declaration, not a second number."""
    from moxi_comms import local_gateway

    assert local_gateway.RETAIN_DAYS == config.LOCAL_RETAIN_DAYS == \
        config.schema().keys["local.retain_days"].default == 90


def test_the_cli_opens_the_local_record_in_process(monkeypatch, tmp_path):
    """Reads are direct WAL reads and writes are direct inserts, so the CLI works
    with the daemon down."""
    from moxi_comms import cli

    monkeypatch.setenv("MOXI_COMMS_LOCAL_RECORD", str(tmp_path / "record.db"))
    ctx = cli.Context(env={"MOXI_COMMS_GATEWAY": "local",
                           "MOXI_COMMS_LOCAL_RECORD": str(tmp_path / "record.db")})
    try:
        assert isinstance(ctx.gateway, LocalGateway)
    finally:
        ctx.gateway.close()


# ---------------------------------------------------------------------------

def test_the_daemon_refuses_a_gateway_flag_that_disagrees_with_the_box(monkeypatch):
    """Every adapter and the CLI resolve the backend with no flag, so a
    daemon holding a different record is a box that looks connected and delivers
    nothing."""
    monkeypatch.setenv("MOXI_COMMS_GATEWAY", "local")
    monkeypatch.setenv("MOXI_COMMS_MOCK_GATEWAY", "")
    with pytest.raises(SystemExit) as excinfo:
        daemon_mod.main(["--gateway", "mock"])
    assert "disagrees" in str(excinfo.value) and "local" in str(excinfo.value)


def test_the_daemon_takes_a_flag_that_agrees_and_mock_with_a_scratch_store(monkeypatch,
                                                                          tmp_path):
    """The exception: `mock` with a scratch store is the test and
    by-hand case, whatever the box is configured for."""
    reached: list = []

    def sentinel(*args, **kwargs):
        reached.append((args, kwargs))
        raise RuntimeError("open_gateway reached")

    monkeypatch.setenv("MOXI_COMMS_GATEWAY", "local")
    monkeypatch.setenv("MOXI_COMMS_MOCK_GATEWAY", "")
    monkeypatch.setattr(gateway_mod, "open_gateway", sentinel)
    for argv in (["--gateway", "local"], ["--gateway", "mock", "--gateway-store",
                                          str(tmp_path / "store.json")], []):
        reached.clear()
        with pytest.raises(RuntimeError, match="open_gateway reached"):
            daemon_mod.main(argv)
        assert reached, f"{argv} should have got past the flag check"


@pytest.mark.parametrize("has_sighup", [True, False])
def test_the_daemon_stops_on_sigterm_and_on_sighup_where_there_is_one(monkeypatch, tmp_path,
                                                                    has_sighup):
    """Windows has no SIGHUP; the daemon starts there all the same."""
    import signal

    installed: list = []

    class Refusing:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("stop here")

    monkeypatch.setenv("MOXI_COMMS_GATEWAY", "mock")
    monkeypatch.setenv("MOXI_COMMS_MOCK_GATEWAY", str(tmp_path / "store.json"))
    monkeypatch.setattr(gateway_mod, "open_gateway",
                        lambda *a, **k: MockGateway(tmp_path / "store.json"))
    monkeypatch.setattr(daemon_mod, "Daemon", Refusing)
    monkeypatch.setattr(signal, "signal", lambda sig, handler: installed.append(sig))
    if not has_sighup:
        monkeypatch.delattr(signal, "SIGHUP", raising=False)
    with pytest.raises(SystemExit, match="stop here"):
        daemon_mod.main([])
    expected = [signal.SIGTERM] + ([signal.SIGHUP] if has_sighup else [])
    assert installed == expected


def test_a_local_gateway_the_daemon_opens_delivers_through_the_seam(rig):
    """The whole point, said once on both backends: a post through the seam reaches the
    daemon's subscription and comes back out of the record."""
    reviewer = rig.shim(name="reviewer", scope="proj-a")
    rig.post_as("agent-6", ["reviewer@proj-a"], "hello from the record")
    wait_until(lambda: len(reviewer.queue) >= 1)
    assert reviewer.queue[0]["message_id"] in [
        m.id for m in rig.gateway.history(rig.names_channel)]
