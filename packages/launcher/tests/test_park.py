"""`moxi launcher park` against fake slots and a stand-in for park's exit: it reserves each
slot (with `expect`) before it exits, records each outcome on the slot's entry, leaves
Codex and its own session running, retries a `not-exited` entry, lists what it leaves
alone, and its dry run writes nothing."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi import proc

import launcher_fake_slots as fake
import launcher_fake_turns as fake_turns
from moxi_launcher import cli, park, slots, store
from moxi_launcher.listeners import REQUEST_ENV
from moxi_launcher.relaunch import Result

TESTS = Path(__file__).resolve().parent
SID3 = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"
SID5 = "6a1d4b2f-8a54-4d9f-8f1b-2e3c4d5e6f70"
SETTINGS = {"park.wait_s": 300, "relaunch.person_s": 120, "relaunch.dialog_s": 60}


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

    return SimpleNamespace(dir=slots_dir, plan=plan,
                           read=lambda: json.loads(script.read_text()),
                           calls=lambda name=None: fake.calls(slots_dir, name))


@pytest.fixture
def conn():
    conn = store.connect()
    yield conn
    conn.close()


def launch_row(conn, rid, sid, *, slot="3", harness="claude", target="slot", **more):
    store.insert(conn, request_id=rid, by="ann", harness=harness, target=target,
                 cwd="/tmp", values={}, argv=[harness], env={}, posture="",
                 session_id=sid, slot=slot if target == "slot" else None, **more)
    store.set_state(conn, rid, "ready")
    return store.get(conn, rid)


def running(rid, *, hold=None):
    return {"state": "occupied", "launchable": False, "hold": hold,
            "last": {"at": 1.0, "label": f"launcher:{rid}",
                     "meta": {"request_id": rid, "session_id": None, "chain": rid}}}


class Exits:
    """Stands in for `exits.park_exit`: each call gives the next outcome, and notes the
    slots' holds as the exit saw them."""

    def __init__(self, rig, *outcomes):
        self.rig, self.outcomes = rig, list(outcomes) or [{"outcome": "exited"}]
        self.calls, self.holds = [], []
        self.lock = threading.Lock()

    def __call__(self, session_id, **kw):
        with self.lock:
            self.calls.append(session_id)
            self.holds.append({k: v.get("hold") for k, v in self.rig.read()["slots"].items()})
            outcome = self.outcomes[0] if len(self.outcomes) == 1 else self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return Result(0 if outcome["outcome"] == "exited" else 75, (), data=outcome)


def go(conn, exit_one=None, **kw):
    kw.setdefault("caller_env", {k: v for k, v in os.environ.items() if k != REQUEST_ENV})
    return park.park(conn=conn, turns=None, settings=SETTINGS, exit_one=exit_one, **kw)


# -- reserve, then exit -----------------------------------------------------------------------


def test_park_reserves_each_slot_before_it_exits_and_records_the_outcomes(conn, rig):
    launch_row(conn, "r3", SID3)
    launch_row(conn, "r5", SID5, slot="5", harness="codex")
    rig.plan(slots={"3": running("r3"), "5": running("r5")})
    exits = Exits(rig)
    result = go(conn, exits)
    assert result.exit_code == 0 and result.data["outcome"] == "parked"
    holder = store.park_holder(result.data["park_id"])
    assert [(c["slot"], c["holder"], c["seconds"], c["expect"]) for c in rig.calls("hold")] \
        == [(3, holder, None, "r3"), (5, holder, None, "r5")]
    # The exit ran with the slot already held: no launch can take the ended slot.
    assert exits.calls == [SID3]
    assert exits.holds[0]["3"] == {"holder": holder, "until": None}
    by_slot = {e.slot: e for e in store.open_entries(conn)}
    assert (by_slot[3].kind, by_slot[3].outcome, by_slot[3].session_id) == (
        "park", "exited", SID3)
    assert (by_slot[5].outcome, by_slot[5].harness) == ("left-running", "codex")
    assert result.lines[0] == f"slot 3: {SID3}: exited"
    assert result.lines[1] == f"slot 5: {SID5}: left-running: {park.LEFT_RUNNING}"
    assert result.lines[-1] == (f"parked 2 sessions as park-{result.data['park_id']}: "
                                "1 exited, 0 not exited, 1 left running")


def test_park_names_a_session_by_its_comms_name(conn, rig):
    launch_row(conn, "r3", SID3, identity="fleet-helper")
    rig.plan(slots={"3": running("r3")})
    assert go(conn, Exits(rig)).lines[0] == "slot 3: fleet-helper: exited"


def test_the_session_running_park_is_reserved_and_not_exited(conn, rig):
    launch_row(conn, "r3", SID3)
    rig.plan(slots={"3": running("r3")})
    exits = Exits(rig)
    result = go(conn, exits, caller_env={**os.environ, REQUEST_ENV: "r3"})
    assert exits.calls == [] and result.exit_code == 0
    entry = store.open_entry(conn, 3)
    assert (entry.outcome, entry.detail) == ("not-exited", park.OWN)
    assert rig.read()["slots"]["3"]["hold"]["holder"] == entry.holder


def test_a_session_still_held_at_the_end_of_the_wait_is_not_exited_and_kept(conn, rig):
    launch_row(conn, "r3", SID3)
    rig.plan(slots={"3": running("r3")})
    held = {"outcome": "held", "word": "turn", "detail": "turn 4 is open"}
    result = go(conn, Exits(rig, held))
    assert result.exit_code == 0
    entry = store.open_entry(conn, 3)
    assert entry.outcome == "not-exited" and entry.detail == "held (turn): turn 4 is open"
    assert result.lines[0] == f"slot 3: {SID3}: not-exited: held (turn): turn 4 is open"


def test_a_second_park_keeps_a_not_exited_entry_and_tries_its_exit_again(conn, rig):
    launch_row(conn, "r3", SID3)
    rig.plan(slots={"3": running("r3")})
    go(conn, Exits(rig, {"outcome": "held", "word": "turn", "detail": "open"}))
    first = store.open_entry(conn, 3)
    exits = Exits(rig)
    result = go(conn, exits)
    assert exits.calls == [SID3] and result.exit_code == 0
    entry = store.open_entry(conn, 3)
    assert entry.entry_id == first.entry_id and entry.outcome == "exited"
    assert len(rig.calls("hold")) == 1


def test_an_exit_that_stops_on_a_fault_is_not_exited(conn, rig):
    launch_row(conn, "r3", SID3)
    rig.plan(slots={"3": running("r3")})
    result = go(conn, Exits(rig, RuntimeError("boom")))
    assert store.open_entry(conn, 3).outcome == "not-exited"
    assert "boom" in result.lines[0] and result.exit_code == 0


# -- what park leaves alone -------------------------------------------------------------------


def test_a_slot_another_holder_holds_is_left_running_and_park_exits_1(conn, rig):
    launch_row(conn, "r3", SID3)
    rig.plan(slots={"3": running("r3", hold={"holder": "relaunch-run", "until": None})})
    exits = Exits(rig)
    result = go(conn, exits)
    assert result.exit_code == 1 and exits.calls == []
    assert store.open_entries(conn) == []
    assert result.lines[0] == f"slot 3: {SID3}: failed: held by relaunch-run; left running"


def test_a_slot_changed_since_it_was_found_is_left_alone(conn, rig, monkeypatch):
    launch_row(conn, "r3", SID3)
    rig.plan(slots={"3": running("r3")})

    def changed(slot, holder, seconds=None, *, expect=None):
        raise fake.Refused("changed", f"slot {slot}'s last launch is r9, not {expect}")

    monkeypatch.setattr(fake, "hold", changed)
    result = go(conn, Exits(rig))
    assert result.exit_code == 0 and store.open_entries(conn) == []
    assert result.lines[0] == f"slot 3: {SID3}: changed; left alone"
    assert result.lines[-1] == "nothing to park"


def test_runs_the_launcher_did_not_launch_and_sessions_outside_slots_are_listed(conn, rig):
    me = proc.identity(os.getpid())
    launch_row(conn, "t1", SID5, target="terminal")
    store.set_facts(conn, "t1", pid=me.pid, pid_started=me.started)
    rig.plan(slots={"4": running("not-ours")})
    exits = Exits(rig)
    result = go(conn, exits)
    assert result.exit_code == 0 and exits.calls == [] and rig.calls("hold") == []
    assert result.lines == ("slot 4: no session: not launched by the launcher; left alone",
                            f"not in a slot: {SID5}: not in a slot; left running",
                            "nothing to park")


def test_nothing_in_any_slot_is_nothing_to_park(conn, rig):
    rig.plan(slots={"3": {"state": "free", "launchable": True}})
    result = go(conn, Exits(rig))
    assert (result.exit_code, result.lines) == (0, ("nothing to park",))


# -- the dry run ------------------------------------------------------------------------------


def test_a_dry_run_writes_nothing_and_says_what_park_would_do(conn, rig):
    launch_row(conn, "r3", SID3)
    launch_row(conn, "r5", SID5, slot="5", harness="codex")
    rig.plan(slots={"3": running("r3"), "5": running("r5")})
    result = go(conn, dry_run=True, caller_env={**os.environ, REQUEST_ENV: "r3"})
    assert result.data["outcome"] == "dry-run"
    assert rig.calls("hold") == [] and store.open_entries(conn) == []
    assert result.lines[0] == f"slot 3: {SID3}: not-exited: {park.OWN}"
    assert result.lines[1] == f"slot 5: {SID5}: left-running: {park.LEFT_RUNNING}"
    assert result.lines[-1].startswith("would park 2 sessions:")


# -- the verb -----------------------------------------------------------------------------------


def test_the_verb_with_no_launch_log_has_nothing_to_park(rig, capsys):
    assert cli.main(["park", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "nothing"


def test_the_verb_refuses_a_negative_wait(rig, capsys):
    assert cli.main(["park", "--wait-s", "-1"]) == 2
