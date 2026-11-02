"""`--replace`: the foreign config it takes over is live or copied in `displaced/` at every
moment, a crash at each step is finished by `repair` or undone by `remove`, and a hand
edit is held for the user."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from fake_tailscale import HOST
from moxi_tunnel import acts, locks, record
from moxi_tunnel import tailscale as ts
from moxi_tunnel.errors import ApplyError, Busy, Refused
from moxi_tunnel.record import State

STEPS = ["replace-copy", "replace-applying", "replace-removed", "replace-renamed",
         "add-applied"]


class Crash(BaseException):
    pass


@pytest.fixture
def foreign(alpha, tailscale):
    tailscale.foreign(8461, "http://127.0.0.1:3000")
    return ts.parts(tailscale.serve, 8461)


def crash_at(monkeypatch, step):
    def hit(name):
        if name == step:
            raise Crash
    monkeypatch.setattr(acts, "_step", hit)


def copies() -> list[dict]:
    return [dict(json.loads(d.path.read_text()), pending=d.pending)
            for d in record.listing()]


def safe(tailscale, parts) -> bool:
    """The foreign config is live, or a copy of it is in displaced/."""
    return (ts.parts(tailscale.serve, 8461) == parts
            or any(c["parts"] == parts for c in copies()))


def kept_when_gone(tailscale, parts) -> bool:
    """Wholly gone from the live config means a final copy holds it."""
    live = ts.parts(tailscale.serve, 8461)
    if any(k.endswith(":8461") and v == parts["Web"][f"{HOST}:8461"]
           for k, v in live.get("Web", {}).items()):
        return True
    return any(c["parts"] == parts and not c["pending"] for c in copies())


def test_a_replace_takes_the_port_and_keeps_a_final_copy(foreign, tailscale, yes):
    got = acts.add("alpha/gateway", replace_=True, ask=yes)
    assert got.address.port == 8461 and acts.check()[0].check == "ok"
    (copy,) = copies()
    assert copy["parts"] == foreign and not copy["pending"]
    assert copy["remove"] == "tailscale serve --https=8461 off"
    assert any("Moxi will not bring it back" in line for line in yes.asked[0])
    assert record.read().entries["alpha/gateway"].displaced == record.listing()[0].name


@pytest.mark.parametrize("step", STEPS)
def test_a_crash_at_each_step_is_finished_by_repair(foreign, tailscale, yes, monkeypatch,
                                                    step):
    crash_at(monkeypatch, step)
    with pytest.raises(Crash):
        acts.add("alpha/gateway", replace_=True, ask=yes)
    assert safe(tailscale, foreign)
    monkeypatch.setattr(acts, "_step", lambda name: None)
    acts.repair(ask=yes)
    assert kept_when_gone(tailscale, foreign)
    assert not any(c["pending"] for c in copies())
    if step == "replace-copy":
        assert record.read().entries == {} and copies() == []
        assert ts.parts(tailscale.serve, 8461) == foreign
    else:
        assert [s.check for s in acts.check()] == ["ok"]


def test_a_crash_after_the_applying_commit_undone_by_remove(foreign, tailscale, yes,
                                                            monkeypatch):
    crash_at(monkeypatch, "replace-applying")
    with pytest.raises(Crash):
        acts.add("alpha/gateway", replace_=True, ask=yes)
    monkeypatch.setattr(acts, "_step", lambda name: None)
    with pytest.raises(Refused) as caught:
        acts.add("alpha/gateway", ask=yes)
    assert "repair" in caught.value.fix and "remove" in caught.value.fix
    before = len(tailscale.changes())
    acts.remove("alpha/gateway", ask=yes)
    assert record.read().entries == {} and copies() == []
    assert ts.parts(tailscale.serve, 8461) == foreign and len(tailscale.changes()) == before


@pytest.mark.parametrize("step", ["replace-copy", "replace-applying"])
def test_a_hand_edit_after_a_crash_is_held_with_both_untouched(foreign, tailscale, yes,
                                                               monkeypatch, step):
    crash_at(monkeypatch, step)
    with pytest.raises(Crash):
        acts.add("alpha/gateway", replace_=True, ask=yes)
    monkeypatch.setattr(acts, "_step", lambda name: None)
    tailscale.foreign(8461, "http://127.0.0.1:3999")
    live, kept = tailscale.serve, copies()
    got = acts.repair(ask=yes)
    assert tailscale.serve == live and copies() == kept and kept[0]["pending"]
    if step == "replace-applying":
        assert got.code == 3
        entry = record.read().entries["alpha/gateway"]
        assert entry.state == State.APPLYING and "no longer holds" in entry.detail


def test_a_hand_edit_between_confirm_and_removal_is_rolled_back(foreign, tailscale,
                                                                monkeypatch, yes):
    def edit(name):
        if name == "replace-applying":
            tailscale.foreign(8461, "http://127.0.0.1:3999")
    monkeypatch.setattr(acts, "_step", edit)
    with pytest.raises(Busy) as caught:
        acts.add("alpha/gateway", replace_=True, ask=yes)
    assert caught.value.exit_code == 75
    assert record.read().entries == {} and copies() == []
    assert ts.parts(tailscale.serve, 8461)["Web"][f"{HOST}:8461"]["Handlers"]["/"] == \
        {"Proxy": "http://127.0.0.1:3999"}


def test_a_failed_foreign_removal_rolls_back_and_deletes_its_pending_copy(foreign, tailscale,
                                                                          yes):
    tailscale.fail("off", "error: nope\n")
    with pytest.raises(ApplyError):
        acts.add("alpha/gateway", replace_=True, ask=yes)
    assert record.read().entries == {} and copies() == []
    assert ts.parts(tailscale.serve, 8461) == foreign


def test_a_foreign_removal_that_removes_nothing_rolls_back(foreign, tailscale, yes,
                                                          monkeypatch):
    monkeypatch.setattr(ts.Tailscale, "remove_copy", lambda self, copy: None)
    with pytest.raises(ApplyError) as caught:
        acts.add("alpha/gateway", replace_=True, ask=yes)
    assert caught.value.fix is None and "removed nothing" in caught.value.reason
    assert record.read().entries == {} and copies() == []
    assert ts.parts(tailscale.serve, 8461) == foreign
    last = json.loads(record.events_path().read_text().splitlines()[-1])
    assert last["event"] == "rollback"


def test_a_removing_entry_that_names_a_pending_copy_is_undone(foreign, tailscale, yes,
                                                              monkeypatch):
    crash_at(monkeypatch, "replace-applying")
    with pytest.raises(Crash):
        acts.add("alpha/gateway", replace_=True, ask=yes)
    monkeypatch.setattr(acts, "_step", lambda name: None)
    entry = record.read().entries["alpha/gateway"]
    with locks.held(timeout=5):
        record.commit({"alpha/gateway": replace(entry, state=State.REMOVING)})
    acts.repair(ask=yes)
    assert record.read().entries == {} and copies() == []
    assert ts.parts(tailscale.serve, 8461) == foreign


def test_a_repair_waits_for_an_in_flight_replace(foreign, tailscale, yes, monkeypatch):
    import threading

    started, release, out = threading.Event(), threading.Event(), {}

    def hold(name):
        if name == "replace-applying" and threading.current_thread().name == "replace":
            started.set()
            release.wait(10)
    monkeypatch.setattr(acts, "_step", hold)
    worker = threading.Thread(name="replace", target=lambda: out.update(
        got=acts.add("alpha/gateway", replace_=True, ask=yes)))
    worker.start()
    assert started.wait(10)
    fixer = threading.Thread(target=lambda: out.update(fix=acts.repair(ask=yes)))
    fixer.start()
    fixer.join(0.5)
    assert fixer.is_alive() and copies()[0]["pending"]
    release.set()
    worker.join(10)
    fixer.join(10)
    assert out["got"].address.state == "on" and out["fix"].notes == ["nothing to repair"]
    assert not copies()[0]["pending"]


def test_a_replace_never_takes_a_port_from_an_entry_a_newer_tunnel_wrote(alpha, tailscale,
                                                                        yes):
    acts.add("alpha/gateway", port=443, ask=yes)
    raw = json.loads(record.record_path().read_text())
    raw["entries"]["alpha/gateway"]["form"] = "tcp"
    record.record_path().write_text(json.dumps(raw))
    held = ts.parts(tailscale.serve, 443)
    got = acts.add("alpha/connector", replace_=True, ask=yes)
    assert got.address.port == 8443
    assert ts.parts(tailscale.serve, 443) == held and copies() == []
