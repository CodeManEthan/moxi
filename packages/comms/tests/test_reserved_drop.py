"""Mail that waited for a name before a module reserved it: dropped undelivered with a
`dropped` row whose reason is `reserved`, at the daemon's start, at the name's release, and
when `agents` or `explain` asks; `explain` and `history` say so. A conflict row keeps its
mail, and mail queued after the name is freed is delivered."""

from __future__ import annotations

from conftest import store_rows, wait_until

from moxi import exit as moxi_exit
from moxi.store import transaction
from moxi_comms import machine_line, reserved, secrets
from moxi_comms.daemon import CASE_REASONS, CASE_RESERVED, Daemon
from moxi_comms.store import open_write

NAME = "console"


def post(rig, text, to=(NAME,)):
    return rig.record.post_as_identity(
        "planner", rig.names_channel, machine_line.compose(list(to), text)).id


def queued(rig, text, to=(NAME,)):
    """A message posted to `to` while unbound, once it waits in the daemon's queue."""
    mid = post(rig, text, to)
    assert wait_until(lambda: all(mid in rig.daemon.queue.get(q, {}) for q in to))
    return mid


def reserve(rig, declared=None):
    """Reserve `console` for the launcher (or reconcile to `declared`), a second on."""
    rig.clock.advance(1)
    return reserved.reconcile(declared={"launcher": [NAME]} if declared is None
                              else declared, chain=secrets.from_config(), clock=rig.clock)


def write(sql, params=()):
    conn = open_write()
    try:
        with transaction(conn) as c:
            c.execute(sql, params)
    finally:
        conn.close()


def test_explain_drops_the_mail_and_says_reserved(rig):
    mid = queued(rig, "before the reservation")
    reserve(rig)
    assert mid in rig.daemon.queue[NAME], "nothing drops it until the daemon checks"
    info = rig.daemon._explain_query({"identity": NAME, "message_id": mid})
    assert info["case"] == CASE_RESERVED == "reserved"
    assert rig.daemon.books.dropped_reason(NAME, mid) == "reserved"
    assert NAME not in rig.daemon.queue
    assert store_rows(NAME) == []


def test_the_cli_explain_says_it_live_and_offline(rig):
    mid = queued(rig, "before the reservation")
    reserve(rig)
    sentence = "Nobody receives mail under a reserved name"
    code, out = rig.cli(["explain", NAME, mid])
    assert code == moxi_exit.OK, out
    assert "reserved" in out and sentence in " ".join(out.split())
    rig.daemon.stop()
    code, out = rig.cli(["explain", NAME, mid])
    assert code == moxi_exit.TEMPFAIL, out
    assert f"{NAME}   reserved   message {mid}" in out
    assert sentence in " ".join(out.split())
    assert CASE_REASONS[CASE_RESERVED].startswith("The message was waiting")


def test_agents_seats_no_longer_shows_the_waiting_row(rig):
    queued(rig, "before the reservation")
    code, out = rig.cli(["agents", "--seats"])
    assert code == moxi_exit.OK, out
    assert f"  {NAME} " in out
    reserve(rig)
    code, out = rig.cli(["agents", "--seats"])
    assert code == moxi_exit.OK, out
    assert f"  {NAME} " not in out


def test_history_marks_the_drop(rig):
    mid = queued(rig, "before the reservation", to=(NAME, "main@proj-a"))
    reserve(rig)
    rig.daemon._agents_query({})
    code, out = rig.cli(["history", "planner"])
    assert code == moxi_exit.OK, out
    (row,) = [ln for ln in out.splitlines() if ln.startswith(f"{mid} ")]
    assert row.endswith(f" [dropped for {NAME}: reserved]")
    assert mid in rig.daemon.queue["main@proj-a"], "the other recipient still has it"


def test_a_freed_name_never_gets_its_pre_reservation_mail(rig):
    before = queued(rig, "before the reservation")
    reserve(rig)
    reserve(rig, declared={})                 # freed: the name is ordinary again
    assert before in rig.daemon.queue[NAME], "no check ran in between"
    rig.clock.advance(1)
    after = queued(rig, "after the free")
    shim = rig.shim(name=NAME)
    assert wait_until(lambda: after in [p["message_id"] for p in shim.queue])
    assert before not in [p["message_id"] for p in shim.queue]
    assert rig.daemon.books.dropped_reason(NAME, before) == "reserved"
    assert rig.daemon.books.dropped_reason(NAME, after) is None


def test_a_restart_drops_the_rows_at_boot(rig):
    mid = queued(rig, "before the reservation")
    rig.daemon.stop()
    reserve(rig)
    rig.daemon = Daemon(rig.record, clock=rig.clock, start_trim_thread=False).start()
    assert NAME not in rig.daemon.queue
    assert rig.daemon.books.dropped_reason(NAME, mid) == "reserved"


def test_an_unowned_row_drops_queued_mail(rig):
    mid = queued(rig, "before the row")
    write("INSERT INTO reserved(name, module, token_hash, state, reserved_at, freed_at, "
          "note) VALUES (?, NULL, NULL, 'unowned', NULL, NULL, NULL)", (NAME,))
    rig.daemon._agents_query({})
    assert NAME not in rig.daemon.queue
    assert rig.daemon.books.dropped_reason(NAME, mid) == "reserved"


def test_a_conflict_row_keeps_queued_mail(rig):
    mid = queued(rig, "before the conflict")
    write("INSERT INTO reserved(name, module, token_hash, state, reserved_at, freed_at, "
          "note) VALUES (?, 'launcher', NULL, 'conflict', NULL, NULL, 'held')", (NAME,))
    rig.daemon._agents_query({})
    assert mid in rig.daemon.queue[NAME]
    assert rig.daemon.books.dropped_reason(NAME, mid) is None


def test_a_check_with_nothing_to_drop_writes_nothing(rig):
    mid = queued(rig, "an ordinary name", to=("main@proj-a",))
    reserve(rig)
    assert rig.daemon.books.drop_reserved() == []
    assert mid in rig.daemon.queue["main@proj-a"]
    assert rig.daemon.books.dropped_reason("main@proj-a", mid) is None
