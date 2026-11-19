"""Crash discovery: crashed slots made park entries with reservations, or settled, before
any slot placement; its read-only form; and how every placement tells slots whether the
harness's exit status can be trusted."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

import launcher_fake_slots as fake
import launcher_fake_turns as fake_turns
from launcher_fake_harness import Harness
from moxi_launcher import api, compose, first, parks, slots, store, targets
from moxi_launcher.api import LaunchRequest

TESTS = Path(__file__).resolve().parent
SID = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"


@pytest.fixture(autouse=True)
def no_turns(monkeypatch):
    fake_turns.hide(monkeypatch)


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


@pytest.fixture
def conn():
    conn = store.connect()
    yield conn
    conn.close()


@pytest.fixture
def work(tmp_path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


def launch_row(conn, rid="r3", sid=SID, *, target="slot", harness="claude", **more):
    store.insert(conn, request_id=rid, by="ann", harness=harness, target=target,
                 cwd="/tmp", values={}, argv=[harness], env={}, posture="",
                 session_id=sid, slot="3" if target == "slot" else None, **more)
    store.set_state(conn, rid, "ready")
    return store.get(conn, rid)


def crashed(rid="r3", *, state="missing", hold=None, is_crashed=True):
    return {"state": state, "launchable": False, "hold": hold, "crashed": is_crashed,
            "last": {"at": 1.0, "label": f"launcher:{rid}",
                     "meta": {"request_id": rid, "session_id": SID, "chain": rid}}}


class Turns:
    def __init__(self, found=None, error=None):
        self.found, self.error = found, error

    def find(self, *, session_id=None, pid=None):
        if self.error is not None:
            raise self.error
        return self.found


class Live:
    def close(self):
        pass


def discover(conn, *, write=True, turns=None):
    return parks.discover(conn, slots.api(), by="ann", write=write, turns=turns)


# -- what discovery does with each crashed slot ---------------------------------------------


def test_a_crashed_slot_with_its_session_is_reserved_entered_and_settled(conn, rig):
    launch_row(conn)
    rig.plan(slots={"3": crashed()})
    [found] = discover(conn)
    assert found.action == "reserve" and found.session_id == SID
    entry = store.open_entry(conn, 3)
    assert (entry.kind, entry.outcome, entry.request_id, entry.session_id) == (
        "crash", "crashed", "r3", SID)
    assert found.entry == entry
    [hold] = rig.calls("hold")
    assert (hold["holder"], hold["seconds"], hold["expect"]) == (entry.holder, None, "r3")
    assert rig.read()["slots"]["3"]["hold"] == {"holder": entry.holder, "until": None}
    [settle] = rig.calls("settle")
    assert settle["why"] == f"reserved by {entry.holder}"


def test_one_discovery_puts_every_slot_it_reserves_under_one_park_id(conn, rig):
    launch_row(conn, "r3")
    launch_row(conn, "r5", "6a1d4b2f-8a54-4d9f-8f1b-2e3c4d5e6f70")
    rig.plan(slots={"3": crashed("r3"), "5": crashed("r5")})
    discover(conn)
    assert len({e.park_id for e in store.open_entries(conn)}) == 1
    assert [e.slot for e in store.open_entries(conn)] == [3, 5]


def test_a_slot_not_crashed_is_left_alone(conn, rig):
    launch_row(conn)
    rig.plan(slots={"3": crashed(is_crashed=False)})
    assert discover(conn) == []
    assert rig.calls("hold") == rig.calls("settle") == []


def test_a_crashed_slot_with_an_open_entry_is_left_alone(conn, rig):
    launch_row(conn)
    store.add_entry(conn, park_id="p0", kind="park", by="ann", slot=3, request_id="r3",
                    session_id=SID, harness="claude")
    rig.plan(slots={"3": crashed()})
    assert discover(conn) == []


@pytest.mark.parametrize("case", ["not-the-launchers", "failed", "not-a-slot-launch",
                                  "no-session-id", "taken-up-later", "live"])
def test_a_crashed_slot_with_nothing_to_restore_is_settled_with_the_reason(conn, rig,
                                                                         case):
    turns = None
    if case == "not-the-launchers":
        slot = crashed("unknown")
    else:
        slot = crashed()
        row = launch_row(conn, sid=None if case == "no-session-id" else SID,
                         harness="codex" if case == "no-session-id" else "claude",
                         target="terminal" if case == "not-a-slot-launch" else "slot")
        if case == "failed":
            store.set_state(conn, row.request_id, "failed", reason="spawn-failed")
        if case == "taken-up-later":
            launch_row(conn, "r4", resume_of="r3")
        if case == "live":
            turns = Turns(found=Live())
    rig.plan(slots={"3": slot})
    [found] = discover(conn, turns=turns)
    assert found.action == "settle"
    assert store.open_entries(conn) == []
    [settle] = rig.calls("settle")
    assert settle["why"] == found.why
    assert rig.calls("hold") == []


def test_a_session_whose_liveness_cant_be_read_is_reserved_all_the_same(conn, rig):
    launch_row(conn)
    rig.plan(slots={"3": crashed()})
    [found] = discover(conn, turns=Turns(error=OSError("no socket")))
    assert found.action == "reserve" and "no socket" in found.unread
    assert "no socket" in store.open_entry(conn, 3).detail


def test_a_crashed_slot_another_holder_holds_gets_no_entry_and_stays_crashed(conn, rig):
    launch_row(conn)
    rig.plan(slots={"3": crashed(hold={"holder": "someone", "until": None})})
    [found] = discover(conn)
    assert found.action == "held" and "someone" in found.why
    assert store.open_entries(conn) == []
    assert rig.calls("settle") == [] and rig.calls("hold") == []


def test_a_hold_refused_since_the_listing_leaves_the_slot_without_an_entry(conn, rig,
                                                                         monkeypatch):
    launch_row(conn)
    rig.plan(slots={"3": crashed()})

    def refused(*args, **kwargs):
        raise fake.Refused("held", "slot 3 is held by late", {"holder": "late"})

    monkeypatch.setattr(fake, "hold", refused)
    [found] = discover(conn)
    assert found.action == "held" and store.open_entries(conn) == []
    assert rig.calls("settle") == []


def test_the_read_only_form_writes_nothing_and_says_what_it_would_do(conn, rig):
    launch_row(conn)
    rig.plan(slots={"3": crashed(), "4": crashed("unknown")})
    found = discover(conn, write=False)
    assert [(f.slot, f.action) for f in found] == [(3, "reserve"), (4, "settle")]
    assert store.open_entries(conn) == []
    assert rig.calls("hold") == rig.calls("settle") == []


def test_discovery_finds_nothing_without_slots_or_a_listing(conn, rig):
    launch_row(conn)
    assert parks.discover(conn, None, by="ann") == []
    rig.plan(slots={"3": crashed()}, list="error")
    assert discover(conn) == []


def test_a_whole_run_holder_reconciles_discovers_then_marks_the_lock_done(conn, rig):
    launch_row(conn)
    rig.plan(slots={"3": crashed(), "6": crashed("x", hold={"holder": "park-gone",
                                                            "until": None},
                                                 is_crashed=False)})
    with parks.whole("restore", conn, slots.api(), by="ann", turns=None) as prepared:
        assert [n.what for n in prepared.notes] == ["released"]
        assert [f.action for f in prepared.found] == ["reserve"]
        assert parks.read_holder(parks.lock_path()) == parks.LockHolder(
            pid=os.getpid(), kind="restore", done=True)


# -- discovery before a launch --------------------------------------------------------------


@pytest.fixture
def harness(tmp_path, monkeypatch) -> Harness:
    return Harness.on_path(tmp_path, monkeypatch)


def request(work, rid="r9", **typed):
    return LaunchRequest(typed={"target": "slot", **typed}, cwd=str(work), request_id=rid,
                         session_id="7b2e5c3a-9b65-4ea0-9a2c-3f4d5e6f7a81", actor="ann")


def test_a_named_launch_into_a_crashed_slot_is_refused_held_with_restore_or_unpark(
        conn, rig, work, harness):
    launch_row(conn)
    rig.plan(slots={"3": crashed()})
    with pytest.raises(targets.SlotTaken) as caught:
        api.launch(request(work, slot="3"))
    holder = store.open_entry(conn, 3).holder
    assert caught.value.problem == f"slot 3 is held by {holder}"
    assert "moxi launcher restore 3" in caught.value.hint
    assert "moxi launcher unpark 3" in caught.value.hint
    assert rig.calls("start") == []


def test_a_launch_naming_no_slot_reserves_crashed_slots_before_it_starts(conn, rig, work,
                                                                        harness):
    launch_row(conn)
    rig.plan(slots={"3": crashed()}, placed=4)
    got = api.launch(request(work))
    assert got.slot == "4"
    assert store.open_entry(conn, 3) is not None
    names = [c["call"] for c in fake.calls(rig.dir)]
    assert names.index("hold") < names.index("start")


def test_a_terminal_launch_runs_no_discovery(conn, rig, work, harness):
    launch_row(conn)
    rig.plan(slots={"3": crashed()})
    api.check(LaunchRequest(typed={"target": "slot"}, cwd=str(work), request_id="r9"))
    assert rig.calls("hold") == []
    assert store.open_entries(conn) == []


HOLD = textwrap.dedent("""
    import sys, time
    from moxi_launcher import parks
    with parks.lock(sys.argv[1]) as path:
        if sys.argv[2] == "1":
            parks.mark_done(path)
        print("held", flush=True)
        time.sleep(30)
""")


@pytest.fixture
def holder():
    started = []

    def hold(kind, *, done):
        proc = subprocess.Popen([sys.executable, "-c", HOLD, kind, "1" if done else "0"],
                                stdout=subprocess.PIPE, text=True)
        started.append(proc)
        assert proc.stdout.readline().strip() == "held"
        return proc

    yield hold
    for proc in started:
        proc.kill()
        proc.wait()


def test_a_launch_while_park_runs_proceeds_without_discovery(conn, rig, work, harness,
                                                             holder):
    launch_row(conn)
    rig.plan(slots={"3": crashed()}, placed=4)
    holder("park", done=True)
    got = api.launch(request(work))
    assert got.state == "started"
    assert rig.calls("hold") == [] and store.open_entries(conn) == []


def test_a_launch_meeting_another_launchs_discovery_is_refused_not_unchecked(
        conn, rig, work, harness, holder):
    launch_row(conn)
    rig.plan(slots={"3": crashed()})
    holder("discovery", done=False)
    with pytest.raises(parks.ParkRunning):
        parks.discover_before_launch(by="ann", turns=None, wait_s=0.3)
    assert store.open_entries(conn) == []


# -- whether slots may trust the exit status --------------------------------------------------


@pytest.mark.parametrize("kind", ["fresh", "resume", "restore", "relaunch", "repair"])
@pytest.mark.parametrize("harness_name, trusted", [("codex", False), ("claude", True)])
def test_every_placement_tells_slots_whether_the_status_is_trusted(
        conn, rig, work, monkeypatch, kind, harness_name, trusted):
    launch_row(conn, "r1", harness=harness_name)
    more = {"resume": {"resume_of": "r1"}, "restore": {"resume_of": "r1"},
            "relaunch": {"relaunch_of": "r1"}, "repair": {"relaunch_of": "r1"}}.get(kind, {})
    store.insert(conn, request_id="r2", by="ann", harness=harness_name, target="slot",
                 cwd=str(work), values={}, argv=[harness_name], env={}, posture="",
                 session_id=SID if kind in ("resume", "restore")
                 else "8c3f6d4b-0c76-4fb1-8b3d-4a5e6f7a8b92",
                 slot="3" if kind in ("relaunch", "repair", "restore") else None, **more)
    rig.plan(slots={"3": {"state": "ended", "launchable": True}})
    if kind == "restore":  # its reserved slot, gone with the reboot, under its reservation
        rig.plan(slots={"3": {"state": "missing", "launchable": False,
                              "hold": {"holder": "park-p1", "until": None}}})
    monkeypatch.setattr(first, "send", lambda conn, rid, *, pid=None, turns=None:
                        first.Outcome(rid, "ready", "first-sent", None, "sent"))
    values = {"cwd": str(work)}
    if kind in ("relaunch", "repair", "restore"):
        values["slot"] = "3"
    composition = compose.Composition(
        harness=harness_name, mode="host" if harness_name == "codex" else "interactive",
        target="slot", argv=(harness_name,), env={}, values=values, first=None)
    launch = targets.Launch(conn=conn, request_id="r2", composition=composition,
                            argv=composition.argv, command=harness_name, env={},
                            cwd=str(work), respawn=kind in ("relaunch", "repair"),
                            hold={"repair": "run-1", "restore": "park-p1"}.get(kind))
    targets.Slot().start(launch)
    [call] = rig.calls("respawn" if kind in ("relaunch", "repair") else "start")
    assert call["status_trusted"] is trusted
    if kind == "restore":
        assert [c["numbers"] for c in rig.calls("create")] == [[3]]
        assert (call["slot"], call["hold"]) == (3, "park-p1")
