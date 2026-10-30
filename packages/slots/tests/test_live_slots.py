"""Slots against a real tmux: create, list, remove, rename, hold, release, attach and the
teardown, each test on its own private server.

Skipped, with the reason, where tmux 3.2 or newer is not on PATH.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from moxi.modules import Refused as UninstallRefused
from moxi_slots import cli, doctor, places, registry, setup, slots, tmux
from moxi_slots.errors import Refused, UsageError
from moxi_slots.model import ENDED, FREE, MISSING, OCCUPIED, Last

from slots_kit import pane_field, run_window, wait_for

pytestmark = pytest.mark.touches("packages/slots/src/moxi_slots/slots.py",
                                 "packages/slots/src/moxi_slots/panes.py",
                                 "packages/slots/src/moxi_slots/registry.py",
                                 "packages/slots/src/moxi_slots/setup.py",
                                 "packages/slots/src/moxi_slots/doctor.py",
                                 "packages/slots/src/moxi_slots/cli.py")


def state(number: int) -> str:
    return slots.show(number).state


def ended_run(number: int) -> str:
    _, pane = run_window(number, "sh", "-c", "sleep 0.3; exit 3")
    wait_for(lambda: pane_field(pane, "pane_dead") == "1", what="the run pane to die")
    return pane


def busy_home(number: int) -> None:
    """A program of yours in the slot's home shell, as when you run one there."""
    home = next(p for p in slots.panes.read()
                if p.session == f"slot-{number}" and p.home)
    tmux.call("respawn-pane", "-k", "-t", home.pane_id, "sleep 60")
    wait_for(lambda: pane_field(home.pane_id, "pane_current_command") == "sleep",
             what="sleep in the home pane")


def test_create_makes_the_lowest_free_slots_with_their_home_window(server):
    assert slots.create() == [1]
    assert slots.create(count=2) == [2, 3]
    assert slots.create([7], name="review") == [7]
    assert slots.create() == [4]
    listing = slots.list_slots()
    assert listing.ok and listing.drift == ()
    assert [(s.number, s.state, s.idle) for s in listing.slots] == [
        (1, FREE, True), (2, FREE, True), (3, FREE, True), (4, FREE, True),
        (7, FREE, True)]
    assert slots.show("review").number == 7
    target = "=slot-1:"
    assert tmux.call("show-options", "-v", "-t", target,
                     "destroy-unattached").out.strip() == "off"
    assert tmux.call("show-options", "-wv", "-t", target,
                     "remain-on-exit").out.strip() == "on"
    assert tmux.call("show-options", "-wv", "-t", target,
                     "@moxi_slot_home").out.strip() == "on"
    assert pane_field(target, "pane_current_path") == os.environ["HOME"]


def test_create_refuses_a_live_slot_and_a_taken_name(server):
    slots.create([2], name="review")
    with pytest.raises(Refused) as caught:
        slots.create([2])
    assert caught.value.reason == "in-use" and "moxi slots attach 2" in caught.value.words
    with pytest.raises(Refused) as caught:
        slots.create(name="review")
    assert caught.value.reason == "name-taken"
    with pytest.raises(UsageError):
        slots.create([1, 2], name="pair")
    with pytest.raises(UsageError):
        slots.create([1], count=2)


def test_create_makes_a_missing_slot_again(server):
    slots.create([3], name="keep")
    tmux.call("kill-session", "-t", "=slot-3")
    assert state(3) == MISSING
    drift = slots.list_slots().drift
    assert [(d.number, d.kind) for d in drift] == [(3, "no-session")]
    assert slots.create([3]) == [3]
    found = slots.show(3)
    assert found.state == FREE and found.name == "keep"


def test_one_listing_is_one_tmux_call(server, socket_fence):
    slots.create(count=3)
    ended_run(2)
    socket_fence.clear()
    listing = slots.list_slots()
    assert [argv[3] for argv in socket_fence] == ["list-panes"]
    assert [s.state for s in listing.slots] == [FREE, ENDED, FREE]


def test_states_and_the_run_pane_facts(server):
    slots.create(count=3)
    _, live = run_window(1, "sleep", "60")
    ended = ended_run(2)
    one, two, three = slots.list_slots().slots
    assert (one.state, one.launchable, one.idle) == (OCCUPIED, False, False)
    assert one.pane_id == live and one.current_command == "sleep"
    assert isinstance(one.pane_pid, int) and one.pane_pid > 0
    assert (two.state, two.launchable, two.idle) == (ENDED, True, True)
    assert two.pane_id == ended and two.dead_status == 3 and two.pane_pid is None
    assert (three.state, three.launchable) == (FREE, True)


def test_drift_a_session_with_no_row_and_two_run_windows(server):
    slots.create([1])
    tmux.call("new-session", "-d", "-s", "slot-9")
    ended_run(1)
    ended_run(1)
    listing = slots.list_slots()
    assert [s.number for s in listing.unregistered] == [9]
    assert {(d.number, d.kind) for d in listing.drift} == {(9, "no-row"),
                                                           (1, "two-run-windows")}
    rows = {(r.name, r.state) for r in doctor.registry_rows()}
    assert rows == {("registry", "warn")}
    assert slots.create() == [2]


def test_remove_kills_only_idle_unoccupied_unheld_slots(server):
    slots.create(count=5)
    run_window(1, "sleep", "60")
    slots.hold(2, "run-1", 60)
    busy_home(3)
    ended_run(4)
    for number, reason in ((1, "occupied"), (2, "held"), (3, "in-use")):
        with pytest.raises(Refused) as caught:
            slots.remove([number])
        assert caught.value.reason == reason
    with pytest.raises(Refused):
        slots.remove([4, 1])
    assert state(4) == ENDED
    for number in (1, 2):
        with pytest.raises(Refused):
            slots.remove([number], force=True)
    assert slots.remove([3], force=True).removed == (3,)
    assert slots.remove([2], holder="run-1").removed == (2,)
    assert slots.remove([4, 5]).removed == (4, 5)
    assert [s.number for s in slots.list_slots().slots] == [1]
    assert tmux.attempt("has-session", "-t", "=slot-4").code == 1


def test_remove_all_free_skips_and_says_why(server):
    slots.create(count=5)
    run_window(1, "sleep", "60")
    slots.hold(2, "run-1", 60)
    busy_home(3)
    tmux.call("kill-session", "-t", "=slot-5")
    done = slots.remove(all_free=True)
    assert done.removed == (4,)
    assert dict(done.skipped) == {1: "occupied", 2: "held", 3: "in-use", 5: "missing"}
    with pytest.raises(UsageError):
        slots.remove(all_free=True, force=True)


def test_remove_a_missing_slot_drops_its_row(server):
    slots.create([2])
    tmux.call("kill-session", "-t", "=slot-2")
    assert slots.remove([2]).removed == (2,)
    assert registry.read().rows == {}


def test_rename_and_clear(server):
    slots.create(count=2)
    slots.rename(1, "review")
    assert slots.show("review").number == 1
    with pytest.raises(Refused) as caught:
        slots.rename(2, "review")
    assert caught.value.reason == "name-taken"
    slots.rename("review", None)
    assert slots.show(1).name is None
    with pytest.raises(Refused) as caught:
        slots.rename(8, "x")
    assert caught.value.reason == "no-slot"
    with pytest.raises(Refused) as caught:
        slots.rename("nope", "x")
    assert caught.value.reason == "no-slot"


def test_holds(server):
    slots.create(count=2)
    run_window(1, "sleep", "60")
    first = slots.hold(1, "run-1", 60)
    assert slots.show(1).hold == first
    with pytest.raises(Refused) as caught:
        slots.hold(1, "run-2", 60)
    assert caught.value.reason == "held"
    assert caught.value.holder == {"holder": "run-1", "until": first.until}
    again = slots.hold(1, "run-1", 120)
    assert again.until > first.until
    assert slots.show(2).launchable
    slots.hold(2, "run-2", 60)
    assert not slots.show(2).launchable
    assert slots.release(1, "run-2") is False
    assert slots.show(1).hold.holder == "run-1"
    assert slots.release(1, "run-1") is True
    assert slots.show(1).hold is None
    assert slots.release(1, "run-1") is False
    tmux.call("kill-session", "-t", "=slot-2")
    with pytest.raises(Refused) as caught:
        slots.hold(2, "run-3", 60)
    assert caught.value.reason == "missing"
    with pytest.raises(UsageError):
        slots.hold(1, "run-1", 24 * 3600 + 1)


def test_a_missing_slot_cannot_be_held(server):
    slots.create([1])
    tmux.call("kill-session", "-t", "=slot-1")
    with pytest.raises(Refused) as caught:
        slots.hold(1, "run-1", 60)
    assert caught.value.reason == "missing"


def test_a_hold_past_until_reads_as_absent_and_the_doctor_warns(server):
    slots.create([1])
    slots.hold(1, "run-1", 60)
    data = json.loads(places.registry().read_text())
    data["slots"]["1"]["hold"]["until"] = time.time() - 5
    places.registry().write_text(json.dumps(data))
    found = slots.show(1)
    assert found.hold is None and found.launchable
    assert [r.state for r in doctor.holds_rows()] == ["warn"]
    slots.hold(1, "run-2", 60)
    assert [r.state for r in doctor.holds_rows()] == ["ok"]


def test_attach_argv_makes_a_missing_slot_and_names_the_socket(server, monkeypatch):
    argv = slots.attach_argv(4)
    assert argv == [server.tmux, "-S", server.socket, "attach-session", "-t", "=slot-4"]
    assert state(4) == FREE
    slots.rename(4, "work")
    assert slots.attach_argv("work")[-1] == "=slot-4"
    with pytest.raises(Refused) as caught:
        slots.attach_argv("nope")
    assert caught.value.reason == "no-slot"
    env = slots.attach_env(argv, {"TMUX": "/x,1,0", "TMUX_PANE": "%1", "HOME": "/h"})
    assert env == {"HOME": "/h"}


def test_attach_from_a_pane_of_this_server_switches_the_client(server, monkeypatch):
    slots.create([1])
    monkeypatch.setenv("TMUX", f"{server.socket},{server.pid},0")
    monkeypatch.setenv("TMUX_PANE", "%0")
    assert slots.inside_this_server()
    argv = slots.attach_argv(1)
    assert argv[3:] == ["switch-client", "-t", "=slot-1"]
    assert slots.attach_env(argv)["TMUX"].startswith(server.socket)
    monkeypatch.setenv("TMUX", "/elsewhere/default,1,0")
    assert not slots.inside_this_server()


def test_two_creates_at_once_get_two_numbers(server):
    code = ("from moxi_slots import slots\n"
            "print(slots.create()[0])\n")
    procs = [subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) for _ in range(2)]
    outs = [p.communicate(timeout=30) for p in procs]
    assert all(p.returncode == 0 for p in procs), outs
    assert sorted(int(out.strip()) for out, _ in outs) == [1, 2]
    assert sorted(registry.read().rows) == [1, 2]


def test_the_teardown_refuses_while_a_slot_is_busy(server):
    assert setup.teardown() == 0
    slots.create(count=3)
    ended_run(1)
    assert setup.teardown() == 0
    run_window(2, "sleep", "60")
    reg = registry.read()
    reg.rows[2].last = Last(time.time(), "agent")
    with registry.locked():
        registry.write(reg)
    busy_home(3)
    with pytest.raises(UninstallRefused) as caught:
        setup.teardown()
    assert "slot 2 is occupied (agent)" in caught.value.reason
    assert "slot 3 is not idle" in caught.value.reason
    assert "slot 1" not in caught.value.reason


def test_the_teardown_accepts_with_no_server(tmux_path):
    assert setup.teardown() == 0


def test_list_with_no_server_reads_every_slot_missing(tmux_path):
    reg = registry.Registry({1: registry.Row(1.0)})
    with registry.locked():
        registry.write(reg)
    listing = slots.list_slots()
    assert listing.ok and [s.state for s in listing.slots] == [MISSING]


def test_the_command(server, capsys):
    assert cli.main(["create", "--count", "2"]) == 0
    assert "moxi slots attach 1" in capsys.readouterr().out
    assert cli.main(["rename", "2", "review"]) == 0
    assert cli.main(["hold", "review", "--holder", "run-1", "--for", "60"]) == 0
    capsys.readouterr()
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "SLOT" in out and "review" in out and "run-1 until" in out
    assert cli.main(["list", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] and [s["number"] for s in data["slots"]] == [1, 2]
    assert data["slots"][1]["hold"]["holder"] == "run-1"
    assert cli.main(["show", "review", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["name"] == "review"
    assert cli.main(["show", "1"]) == 0
    assert "state" in capsys.readouterr().out
    assert cli.main(["remove", "2"]) == 3
    assert capsys.readouterr().err.startswith("held: slot 2 is held by run-1")
    assert cli.main(["show", "9"]) == 3
    assert cli.main(["release", "2", "--holder", "run-1"]) == 0
    assert cli.main(["remove", "--all-free"]) == 0
    assert "slot 1 removed" in capsys.readouterr().out
    assert cli.main(["create", "0"]) == 2
    assert cli.main(["rename", "2"]) == 2
    assert cli.main(["attach", "3", "--print"]) == 0
    assert capsys.readouterr().out.strip().endswith("attach-session -t =slot-3")
