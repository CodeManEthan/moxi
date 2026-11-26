"""Park entries in the launch log (schema version 3), the park lock and reconcile."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import launcher_fake_slots as fake
from moxi_launcher import parks, slots, store

TESTS = Path(__file__).resolve().parent
T0 = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


def at(seconds: float = 0) -> str:
    return store.stamp(T0 + timedelta(seconds=seconds))


@pytest.fixture
def conn(tmp_path):
    conn = store.connect(tmp_path / "launcher.db")
    yield conn
    conn.close()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    slots_dir = tmp_path / "fake-slots"
    slots_dir.mkdir()
    monkeypatch.setenv(slots.SLOTS_ENV, "launcher_fake_slots")
    monkeypatch.setenv(fake.DIR, str(slots_dir))
    monkeypatch.syspath_prepend(str(TESTS))
    fake.reset()
    script = slots_dir / "script.json"

    def plan(**keys):
        now = json.loads(script.read_text()) if script.exists() else {}
        script.write_text(json.dumps({**now, **keys}))

    return SimpleNamespace(dir=slots_dir, plan=plan, read=lambda: json.loads(script.read_text()),
                           calls=lambda name=None: fake.calls(slots_dir, name))


def entry(conn, slot=3, request_id="r3", session_id="s3", park_id="p1", **more):
    return store.add_entry(conn, park_id=park_id, kind=more.pop("kind", "park"), by="me",
                           slot=slot, request_id=request_id, session_id=session_id,
                           harness="claude", **more)


def ended(request_id, hold=None):
    return {"state": "ended", "launchable": hold is None, "hold": hold,
            "last": {"at": 1.0, "label": "x", "meta": {"request_id": request_id}}}


# -- the store ---------------------------------------------------------------------------


def test_a_version_2_log_moves_to_3_with_an_empty_park_entries_table(tmp_path):
    db = tmp_path / "launcher.db"
    raw_conn = sqlite3.connect(db)
    for step in store.STEPS[:2]:
        raw_conn.executescript(step)
    raw_conn.execute("PRAGMA user_version = 2")
    raw_conn.execute("INSERT INTO warm_holds VALUES ('r1', 'x', 't', NULL)")
    raw_conn.commit()
    raw_conn.close()
    conn = store.connect(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(store.STEPS)
        assert conn.execute("SELECT count(*) FROM park_entries").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM warm_holds").fetchone()[0] == 1
    finally:
        conn.close()


def test_an_entry_is_written_open_under_its_parks_holder(conn):
    made = entry(conn)
    assert made.state == "open" and made.holder == "park-p1" and made.slot == 3
    assert store.open_entry(conn, 3) == made
    assert store.get_entry(conn, made.entry_id) == made


def test_a_slot_holds_at_most_one_open_entry(conn):
    entry(conn)
    with pytest.raises(store.EntryOpen):
        entry(conn, request_id="r9", park_id="p2")
    first = store.open_entry(conn, 3)
    assert store.close_entry(conn, first.entry_id, "dropped", detail="gone")
    again = entry(conn, request_id="r9", park_id="p2")
    assert store.open_entry(conn, 3) == again


def test_closing_an_entry_happens_once(conn):
    made = entry(conn)
    assert store.close_entry(conn, made.entry_id, "restored", restore_request_id="r4")
    assert not store.close_entry(conn, made.entry_id, "dropped")
    closed = store.get_entry(conn, made.entry_id)
    assert (closed.state, closed.restore_request_id) == ("restored", "r4")
    assert closed.closed_at is not None


def test_open_entries_come_in_slot_order_and_by_slot(conn):
    entry(conn, slot=5, request_id="r5")
    entry(conn, slot=2, request_id="r2")
    assert [e.slot for e in store.open_entries(conn)] == [2, 5]
    assert [e.slot for e in store.open_entries(conn, [5])] == [5]
    assert [e.slot for e in store.entries_of(conn, ["r2"], ["s3"])] == [2, 5]


def test_an_entrys_outcome_is_set_only_while_open(conn):
    made = entry(conn)
    assert store.set_entry(conn, made.entry_id, outcome="exited", detail="ok")
    assert store.get_entry(conn, made.entry_id).outcome == "exited"
    store.close_entry(conn, made.entry_id, "dropped")
    assert not store.set_entry(conn, made.entry_id, outcome="not-exited")


def test_prune_keeps_open_entries_and_deletes_old_closed_ones(conn):
    kept_open = entry(conn, slot=1, at=at(-200 * 86400))
    old = entry(conn, slot=2)
    store.close_entry(conn, old.entry_id, "dropped", at=at(-100 * 86400))
    recent = entry(conn, slot=4)
    store.close_entry(conn, recent.entry_id, "restored", at=at(-10 * 86400))
    gone = store.prune(conn, 90, at=at())
    assert gone["park_entries"] == 1
    assert store.get_entry(conn, kept_open.entry_id) is not None
    assert store.get_entry(conn, old.entry_id) is None
    assert store.get_entry(conn, recent.entry_id) is not None


# -- the park lock -------------------------------------------------------------------------


HOLD = textwrap.dedent("""
    import sys, time
    from moxi_launcher import parks
    kind, done, hold_s = sys.argv[1], sys.argv[2] == "1", float(sys.argv[3])
    with parks.lock(kind) as path:
        if done:
            parks.mark_done(path)
        print("held", flush=True)
        time.sleep(hold_s)
""")


@pytest.fixture
def holder():
    started = []

    def hold(kind, *, done=False, hold_s=30.0):
        proc = subprocess.Popen([sys.executable, "-c", HOLD, kind, "1" if done else "0",
                                 str(hold_s)], stdout=subprocess.PIPE, text=True)
        started.append(proc)
        assert proc.stdout.readline().strip() == "held"
        return proc

    yield hold
    for proc in started:
        proc.kill()
        proc.wait()


def test_the_lock_file_names_its_holders_kind_and_pid(holder):
    proc = holder("park", done=True)
    found = parks.read_holder(parks.lock_path())
    assert found == parks.LockHolder(proc.pid, "park", done=True)


def test_a_second_whole_run_holder_waits_then_is_refused(holder):
    holder("restore")
    began = time.monotonic()
    with pytest.raises(parks.ParkRunning) as caught:
        with parks.lock("park", wait_s=0.5):
            pass
    assert time.monotonic() - began >= 0.5
    assert caught.value.problem == "park or restore is running"
    assert caught.value.holder.kind == "restore"


def test_the_lock_is_taken_once_its_holder_ends(holder):
    holder("unpark", hold_s=0.3)
    with parks.lock("park", wait_s=5) as path:
        assert parks.held_here() == "park"
        assert parks.read_holder(path).kind == "park"
    assert parks.held_here() is None


def test_a_launch_meeting_a_whole_run_holder_that_is_done_skips_discovery(holder):
    holder("park", done=True)
    with parks.discovery(wait_s=0.5) as run_it:
        assert run_it is False


@pytest.mark.parametrize("kind", ["restore", "discovery"])
def test_a_launch_meeting_a_holder_not_done_waits_then_is_refused(holder, kind):
    holder(kind)
    began = time.monotonic()
    with pytest.raises(parks.ParkRunning):
        with parks.discovery(wait_s=0.5):
            pass
    assert time.monotonic() - began >= 0.5


def test_a_launch_with_the_lock_free_runs_discovery_under_it():
    with parks.discovery() as run_it:
        assert run_it is True
        assert parks.read_holder(parks.lock_path()).kind == "discovery"
    assert parks.held_here() is None


def test_a_launch_inside_a_whole_run_holder_runs_no_discovery_of_its_own():
    with parks.lock("restore"):
        with parks.discovery(wait_s=0) as run_it:
            assert run_it is False


def test_the_holder_file_goes_when_the_lock_is_let_go():
    with parks.lock("park") as path:
        parks.mark_done(path)
        assert (path.parent / parks.HOLDER_FILE).exists()
    assert not (path.parent / parks.HOLDER_FILE).exists()


def test_another_thread_of_the_holders_process_meets_the_lock_as_another_process():
    taken, let_go, seen = threading.Event(), threading.Event(), {}

    def restore_run():
        with parks.lock("restore") as path:
            seen["here"] = parks.held_here()
            taken.set()
            let_go.wait(10)
            parks.mark_done(path)
            taken.clear()
            let_go.clear()
            taken.set()
            let_go.wait(10)

    thread = threading.Thread(target=restore_run)
    thread.start()
    try:
        assert taken.wait(10)
        assert seen["here"] == "restore" and parks.held_here() is None
        with pytest.raises(parks.ParkRunning) as caught:  # before the done mark
            with parks.discovery(wait_s=0.3):
                pass
        assert caught.value.holder.kind == "restore"
        with pytest.raises(parks.ParkRunning):
            with parks.lock("park", wait_s=0.3):
                pass
        taken.clear()
        let_go.set()
        assert taken.wait(10)
        with parks.discovery(wait_s=0.3) as run_it:  # done: the holder ran discovery
            assert run_it is False
    finally:
        let_go.set()
        thread.join(10)


def test_only_a_whole_run_holder_marks_the_lock_done():
    with parks.lock("discovery") as path, pytest.raises(RuntimeError):
        parks.mark_done(path)


# -- reconcile -----------------------------------------------------------------------------


def test_reconcile_takes_a_lost_reservation_again(conn, rig):
    made = entry(conn)
    rig.plan(slots={"3": ended("r3")})
    notes = parks.reconcile(conn, slots.api())
    assert [n.what for n in notes] == ["retaken"]
    assert rig.read()["slots"]["3"]["hold"] == {"holder": "park-p1", "until": None}
    assert rig.calls("hold")[0]["expect"] == "r3"
    assert store.open_entry(conn, 3) == made


def test_reconcile_leaves_an_entry_that_holds_its_slot(conn, rig):
    entry(conn)
    rig.plan(slots={"3": ended("r3", hold={"holder": "park-p1", "until": None})})
    assert parks.reconcile(conn, slots.api()) == []
    assert rig.calls("hold") == []


@pytest.mark.parametrize("slot_entry, why", [
    (ended("r9"), "not r3"),
    (ended("r3", hold={"holder": "someone", "until": None}), "held by someone"),
])
def test_reconcile_drops_an_entry_whose_reservation_cant_be_taken(conn, rig, slot_entry,
                                                                    why):
    made = entry(conn)
    rig.plan(slots={"3": slot_entry})
    notes = parks.reconcile(conn, slots.api())
    assert [n.what for n in notes] == ["dropped"]
    assert notes[0].resume == "moxi launcher resume s3"
    assert why in notes[0].why
    assert store.get_entry(conn, made.entry_id).state == "dropped"


def test_reconcile_drops_an_entry_whose_slot_is_gone(conn, rig):
    made = entry(conn)
    rig.plan(slots={})
    assert [n.what for n in parks.reconcile(conn, slots.api())] == ["dropped"]
    assert store.get_entry(conn, made.entry_id).state == "dropped"


def test_reconcile_releases_a_park_hold_with_no_entry(conn, rig):
    rig.plan(slots={"2": ended("r2", hold={"holder": "park-old", "until": None}),
                    "4": ended("r4", hold={"holder": "relaunch-x", "until": 9e9})})
    notes = parks.reconcile(conn, slots.api())
    assert [(n.what, n.slot) for n in notes] == [("released", 2)]
    assert rig.read()["slots"]["2"]["hold"] is None
    assert rig.read()["slots"]["4"]["hold"]["holder"] == "relaunch-x"


def test_reconcile_leaves_entries_when_slots_cant_answer(conn, rig):
    made = entry(conn)
    rig.plan(slots={"3": ended("r3")}, hold="try-again")
    assert [n.what for n in parks.reconcile(conn, slots.api())] == ["unread"]
    assert store.get_entry(conn, made.entry_id).state == "open"


def test_reconcile_does_nothing_without_slots(conn):
    entry(conn)
    assert parks.reconcile(conn, None) == []
