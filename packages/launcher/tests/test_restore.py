"""`moxi launcher restore`, a plain resume of a parked session, and `moxi launcher unpark`,
against fake slots and a stand-in for the launch: each parked session goes back into its
reserved slot under the reservation, or elsewhere with the reservation released; a refusal
keeps the entry; a launch that fails after placement takes the reservation again or
closes the entry `dropped`."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from moxi import proc

import launcher_fake_slots as fake
import launcher_fake_turns as fake_turns
from moxi_launcher import api, cli, identity, parks, restore, resume, slots, store

TESTS = Path(__file__).resolve().parent
SID3 = "5f0c3a1e-7f43-4c8e-9e0a-1d2b3c4d5e6f"
SID5 = "6a1d4b2f-8a54-4d9f-8f1b-2e3c4d5e6f70"
HOLDER = "park-p1"


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

    def slot(number):
        return json.loads(script.read_text())["slots"].get(str(number))

    return SimpleNamespace(dir=slots_dir, plan=plan, slot=slot,
                           calls=lambda name=None: fake.calls(slots_dir, name))


@pytest.fixture
def conn():
    conn = store.connect()
    yield conn
    conn.close()


def parked(conn, rid, sid, slot, *, harness="claude", identity_name=None):
    store.insert(conn, request_id=rid, by="ann", harness=harness, target="slot",
                 cwd="/tmp", values={}, argv=[harness], env={}, posture="",
                 session_id=sid, slot=str(slot), identity=identity_name)
    store.set_state(conn, rid, "ready")
    return store.add_entry(conn, park_id="p1", kind="park", by="ann", slot=slot,
                           request_id=rid, session_id=sid, harness=harness,
                           outcome="exited")


def reserved(state="missing", rid="r3", holder=HOLDER):
    return {"state": state, "launchable": False,
            "hold": {"holder": holder, "until": None} if holder else None,
            "last": {"at": 1.0, "label": f"launcher:{rid}",
                     "meta": {"request_id": rid, "session_id": None, "chain": rid}}}


class Launch:
    """Stands in for `api.launch`: places the replay through fake slots' `start` (which
    ends the hold it is given), then gives `state`; `refuse` raises instead; `then` runs
    after the placement."""

    def __init__(self, state="ready", refuse=None, then=None, conn=None):
        self.state, self.refuse, self.then, self.conn = state, refuse, then, conn
        self.requests = []

    def __call__(self, request, *, env=None, warn=None):
        self.requests.append(request)
        if self.refuse is not None:
            raise self.refuse
        if self.conn is not None:  # the launch's own row, as `api.launch` writes it
            store.insert(self.conn, request_id=request.request_id, by="ann",
                         harness="claude", target="slot", cwd="/tmp", values={},
                         argv=["claude"], env={}, posture="", session_id=request.session_id,
                         slot=str(request.slot or ""), resume_of=request.resume_of)
            store.set_state(self.conn, request.request_id, self.state,
                            reason="exec-failed" if self.state == "failed" else None)
        named = request.slot if request.slot not in (None, "") else None
        placed = fake.start(named, ["claude"], env={}, cwd="/tmp",
                            label=f"launcher:{request.request_id}",
                            meta={"request_id": request.request_id}, hold=request.hold,
                            create=named is None)
        if self.then is not None:
            self.then()
        return api.LaunchResult(request_id=request.request_id, harness="claude",
                                target="slot", state=self.state,
                                exit_code=0 if self.state != "failed" else 1,
                                session_id=request.session_id, slot=str(placed.slot),
                                line=f"launch {request.request_id} {self.state} in slot "
                                     f"{placed.slot}")


def go(conn, slots_named=(), **kw):
    return restore.restore(slots_named, conn=conn, turns=None, **kw)


# -- restore ----------------------------------------------------------------------------------


def test_restore_resumes_every_parked_session_into_its_reserved_slot(conn, rig):
    three, five = parked(conn, "r3", SID3, 3), parked(conn, "r5", SID5, 5)
    rig.plan(slots={"3": reserved("missing", "r3"), "5": reserved("ended", "r5")})
    launch = Launch()
    result = go(conn, launch=launch)
    assert result.exit_code == 0, result.lines
    assert [c["numbers"] for c in rig.calls("create")] == [[3]]
    assert [(c["slot"], c["hold"]) for c in rig.calls("start")] == [(3, HOLDER),
                                                                     (5, HOLDER)]
    assert [r.resume_of for r in launch.requests] == ["r3", "r5"]
    for entry, request in zip((three, five), launch.requests, strict=True):
        now = store.get_entry(conn, entry.entry_id)
        assert (now.state, now.restore_request_id) == ("restored", request.request_id)
    assert result.lines[-1] == "restored 2 of 2 parked sessions"


def test_restore_of_named_slots_leaves_the_rest_and_refuses_a_slot_with_nothing(conn, rig):
    three, five = parked(conn, "r3", SID3, 3), parked(conn, "r5", SID5, 5)
    rig.plan(slots={"3": reserved("missing", "r3"), "5": reserved("ended", "r5")})
    result = go(conn, [5, 9], launch=Launch())
    assert store.get_entry(conn, three.entry_id).state == "open"
    assert store.get_entry(conn, five.entry_id).state == "restored"
    assert "slot 9: nothing parked: nothing parked in slot 9" in result.lines
    assert result.exit_code == 1


def test_nothing_parked_is_nothing_to_restore(conn, rig):
    rig.plan(slots={})
    result = go(conn, launch=Launch())
    assert (result.exit_code, result.lines[-1]) == (0, "nothing to restore")


def test_a_session_still_running_in_its_slot_is_left_open(conn, rig):
    entry = parked(conn, "r3", SID3, 3)
    me = proc.identity(os.getpid())
    store.set_facts(conn, "r3", pid=me.pid, pid_started=me.started)
    rig.plan(slots={"3": reserved("occupied", "r3")})
    launch = Launch()
    result = go(conn, launch=launch)
    assert launch.requests == [] and result.exit_code == 1
    assert store.get_entry(conn, entry.entry_id).state == "open"
    assert result.lines[0] == f"slot 3: {SID3}: still running: it still runs in slot 3"


def test_restore_closes_an_entry_a_later_launch_took_up(conn, rig):
    entry = parked(conn, "r3", SID3, 3)
    store.insert(conn, request_id="r9", by="ann", harness="claude", target="slot",
                 cwd="/tmp", values={}, argv=["claude"], env={}, posture="",
                 session_id=SID3, slot="8", resume_of="r3")
    rig.plan(slots={"3": reserved("missing", "r3")})
    launch = Launch()
    result = go(conn, launch=launch)
    assert launch.requests == [] and result.exit_code == 0
    now = store.get_entry(conn, entry.entry_id)
    assert (now.state, now.restore_request_id) == ("restored", "r9")
    assert rig.slot(3)["hold"] is None


def test_an_occupied_reserved_slot_falls_back_to_the_lowest_and_releases_it(conn, rig):
    entry = parked(conn, "r3", SID3, 3)
    rig.plan(slots={"3": reserved("occupied", "r-by-hand")}, placed=7)
    result = go(conn, launch=Launch())
    [start] = rig.calls("start")
    assert (start["slot"], start["hold"], start["create"]) == (None, None, True)
    now = store.get_entry(conn, entry.entry_id)
    assert now.state == "restored" and "resumed in slot 7" in now.detail
    assert rig.slot(3)["hold"] is None and result.exit_code == 0


def test_an_entry_whose_slot_another_holder_took_is_dropped_by_reconcile(conn, rig):
    entry = parked(conn, "r3", SID3, 3)
    rig.plan(slots={"3": reserved("ended", "r3", holder="someone")}, placed=7)
    launch = Launch()
    result = go(conn, launch=launch)
    assert launch.requests == [] and store.get_entry(conn, entry.entry_id).state == "dropped"
    assert f"resume it with moxi launcher resume {SID3}" in result.lines[0]
    assert rig.slot(3)["hold"] == {"holder": "someone", "until": None}


def test_comms_offline_leaves_the_entry_and_its_reservation(conn, rig):
    entry = parked(conn, "r3", SID3, 3, identity_name="fleet-helper")
    rig.plan(slots={"3": reserved("missing", "r3")})
    offline = identity.CommsOffline("fleet-helper", "no daemon")
    result = go(conn, launch=Launch(refuse=offline))
    assert result.exit_code == 1
    assert store.get_entry(conn, entry.entry_id).state == "open"
    assert rig.slot(3)["hold"]["holder"] == HOLDER
    assert result.lines[0].startswith("slot 3: fleet-helper: refused: comms is offline")
    assert result.lines[-1].endswith("run moxi launcher restore again once comms is up")


def test_a_launch_failing_after_placement_takes_the_reservation_again(conn, rig):
    entry = parked(conn, "r3", SID3, 3)
    rig.plan(slots={"3": reserved("ended", "r3")})
    launch = Launch(state="failed")
    result = go(conn, launch=launch)
    failed = launch.requests[0].request_id
    assert rig.calls("hold")[-1]["expect"] == failed
    now = store.get_entry(conn, entry.entry_id)
    assert (now.state, now.request_id) == ("open", failed)
    assert rig.slot(3)["hold"]["holder"] == HOLDER and result.exit_code == 1


def test_a_restore_after_a_failed_launch_resumes_the_session_again(conn, rig):
    entry = parked(conn, "r3", SID3, 3)
    rig.plan(slots={"3": reserved("missing", "r3")})
    first = go(conn, launch=Launch(state="failed", conn=conn))
    assert first.exit_code == 1 and store.get_entry(conn, entry.entry_id).state == "open"
    # The failed launch never claimed the session, so the session resolves to r3 again:
    # that is not a launch that took it up since the park.
    assert store.resolve(conn, SID3).request_id == "r3"
    plan = fake.script()  # its pane died: the slot reads ended, still reserved
    plan["slots"]["3"]["state"] = "ended"
    fake._save(plan)
    launch = Launch(conn=conn)
    second = go(conn, launch=launch)
    assert second.exit_code == 0, second.lines
    [request] = launch.requests
    assert (request.resume_of, request.slot, request.hold) == ("r3", 3, HOLDER)
    now = store.get_entry(conn, entry.entry_id)
    assert (now.state, now.restore_request_id) == ("restored", request.request_id)


def test_a_launch_failing_after_the_slot_was_taken_closes_the_entry_dropped(conn, rig):
    entry = parked(conn, "r3", SID3, 3)
    rig.plan(slots={"3": reserved("ended", "r3")})

    def taken():
        plan = fake.script()
        plan["slots"]["3"]["hold"] = {"holder": "someone", "until": None}
        fake._save(plan)

    result = go(conn, launch=Launch(state="failed", then=taken))
    assert store.get_entry(conn, entry.entry_id).state == "dropped"
    assert f"resume it with moxi launcher resume {SID3}" in result.lines[-2]


def test_a_dry_run_writes_nothing(conn, rig):
    entry = parked(conn, "r3", SID3, 3)
    rig.plan(slots={"3": reserved("missing", "r3")})
    launch = Launch()
    result = go(conn, dry_run=True, launch=launch)
    assert launch.requests == [] and rig.calls("create") == rig.calls("hold") == []
    assert store.get_entry(conn, entry.entry_id).state == "open"
    assert result.data["outcome"] == "dry-run"
    assert result.lines[0] == f"slot 3: {SID3}: restored: would resume it into slot 3"


# -- a plain resume of a parked session --------------------------------------------------------


def resume_it(conn, slot=None, **kw):
    return resume.resume(SID3, slot=slot, conn=conn, turns=None, **kw)


def test_a_plain_resume_of_a_parked_session_uses_its_entry_and_reservation(conn, rig):
    entry = parked(conn, "r3", SID3, 3)
    rig.plan(slots={"3": reserved("missing", "r3")})
    launch = Launch()
    result = resume_it(conn, launch=launch)
    assert result.exit_code == 0
    assert [(c["slot"], c["hold"]) for c in rig.calls("start")] == [(3, HOLDER)]
    assert store.get_entry(conn, entry.entry_id).state == "restored"
    assert parks.held_here() is None


def test_a_plain_resume_into_another_slot_releases_the_reservation(conn, rig):
    entry = parked(conn, "r3", SID3, 3)
    rig.plan(slots={"3": reserved("missing", "r3"), "8": {"state": "free",
                                                         "launchable": True}})
    resume_it(conn, slot="8", launch=Launch())
    assert [c["slot"] for c in rig.calls("start")] == ["8"]
    now = store.get_entry(conn, entry.entry_id)
    assert now.state == "restored" and "slot 8 was asked for" in now.detail
    assert rig.slot(3)["hold"] is None


def test_a_plain_resume_refused_keeps_the_entry_and_raises(conn, rig):
    entry = parked(conn, "r3", SID3, 3, identity_name="fleet-helper")
    rig.plan(slots={"3": reserved("missing", "r3")})
    with pytest.raises(identity.CommsOffline):
        resume_it(conn, launch=Launch(refuse=identity.CommsOffline("fleet-helper", None)))
    assert store.get_entry(conn, entry.entry_id).state == "open"
    assert rig.slot(3)["hold"]["holder"] == HOLDER


def crashed(rid, sid):
    return {"state": "missing", "launchable": False, "hold": None, "crashed": True,
            "last": {"at": 1.0, "label": f"launcher:{rid}",
                     "meta": {"request_id": rid, "session_id": sid, "chain": rid}}}


def launched(conn, rid, sid, slot):
    store.insert(conn, request_id=rid, by="ann", harness="claude", target="slot",
                 cwd="/tmp", values={}, argv=["claude"], env={}, posture="",
                 session_id=sid, slot=str(slot))
    store.set_state(conn, rid, "ready")


def test_the_first_plain_resume_of_a_crashed_session_goes_back_into_its_slot(conn, rig):
    launched(conn, "r3", SID3, 3)
    rig.plan(slots={"3": crashed("r3", SID3)})
    launch = Launch()
    result = resume_it(conn, launch=launch)
    assert result.exit_code == 0, result.lines
    [entry] = store.entries_log(conn)
    assert (entry.kind, entry.state) == ("crash", "restored")
    assert [(c["slot"], c["hold"]) for c in rig.calls("start")] == [(3, entry.holder)]
    assert store.open_entries(conn) == []


def test_a_resume_whose_missing_slot_is_reserved_for_another_goes_to_the_lowest(conn, rig):
    other = parked(conn, "r5", SID5, 3)
    launched(conn, "r3", SID3, 3)
    rig.plan(slots={"3": reserved("missing", "r5"), "1": {"state": "free",
                                                         "launchable": True}}, placed=1)
    result = resume_it(conn, launch=Launch())
    assert result.exit_code == 0, result.lines
    assert [(c["slot"], c["create"]) for c in rig.calls("start")] == [(None, True)]
    assert rig.calls("create") == []
    assert result.lines[0] == ("slot 3 is held by park-p1; resuming into the lowest slot "
                               "that takes it")
    assert store.get_entry(conn, other.entry_id).state == "open"
    assert rig.slot(3)["hold"]["holder"] == HOLDER


# -- unpark -----------------------------------------------------------------------------------


def test_unpark_releases_a_named_slot_and_closes_its_entry_dropped(conn, rig):
    three, five = parked(conn, "r3", SID3, 3), parked(conn, "r5", SID5, 5)
    rig.plan(slots={"3": reserved("missing", "r3"), "5": reserved("missing", "r5")})
    result = restore.unpark([3], conn=conn, turns=None)
    assert result.exit_code == 0
    assert store.get_entry(conn, three.entry_id).state == "dropped"
    assert store.get_entry(conn, five.entry_id).state == "open"
    assert rig.slot(3)["hold"] is None and rig.slot(5)["hold"]["holder"] == HOLDER
    assert result.lines[0] == (f"slot 3: {SID3}: dropped: resume it with moxi launcher "
                               f"resume {SID3}")


def test_an_unparked_slot_that_reads_crashed_is_not_reserved_again(conn, rig):
    # Park left the session running; the reboot ended it, so its slot reads crashed.
    entry = parked(conn, "r3", SID3, 3)
    rig.plan(slots={"3": {**reserved("missing", "r3"), "crashed": True}})
    result = restore.unpark([3], conn=conn, turns=None)
    assert result.exit_code == 0 and store.get_entry(conn, entry.entry_id).state == "dropped"
    assert [c["slot"] for c in rig.calls("settle")] == [3]
    assert rig.slot(3)["crashed"] is False
    found = parks.discover(conn, slots.api(), by="ann", turns=None)
    assert found == [] and store.open_entry(conn, 3) is None
    assert rig.slot(3)["hold"] is None


def test_a_restore_that_releases_a_crashed_slot_settles_it(conn, rig):
    entry = parked(conn, "r3", SID3, 3)
    store.insert(conn, request_id="r9", by="ann", harness="claude", target="slot",
                 cwd="/tmp", values={}, argv=["claude"], env={}, posture="",
                 session_id=SID3, slot="8", resume_of="r3")
    rig.plan(slots={"3": {**reserved("missing", "r3"), "crashed": True}})
    go(conn, launch=Launch())
    assert store.get_entry(conn, entry.entry_id).state == "restored"
    assert [c["slot"] for c in rig.calls("settle")] == [3]


def test_unpark_all_gives_up_every_parked_slot(conn, rig):
    parked(conn, "r3", SID3, 3), parked(conn, "r5", SID5, 5)
    rig.plan(slots={"3": reserved("missing", "r3"), "5": reserved("missing", "r5")})
    result = restore.unpark(every=True, conn=conn, turns=None)
    assert result.exit_code == 0 and store.open_entries(conn) == []
    assert result.lines[-1] == "unparked 2 of 2"


def test_unpark_of_a_slot_with_nothing_parked_says_so(conn, rig):
    rig.plan(slots={})
    result = restore.unpark([4], conn=conn, turns=None)
    assert result.exit_code == 1 and result.lines[0] == ("slot 4: nothing parked: nothing "
                                                         "parked in slot 4")


def test_the_unpark_verb_takes_slots_or_all_but_not_both(rig, capsys):
    assert cli.main(["unpark"]) == 2
    assert cli.main(["unpark", "3", "--all"]) == 2
