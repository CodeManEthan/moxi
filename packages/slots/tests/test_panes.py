"""Reading the slots: parsing the one list-panes call, states, idleness, drift."""

from __future__ import annotations

import time

import pytest

from moxi_slots import panes, tmux
from moxi_slots.errors import TmuxError
from moxi_slots.model import ENDED, FREE, MISSING, OCCUPIED, Hold, Last
from moxi_slots.panes import Pane
from moxi_slots.registry import Registry, Row

pytestmark = pytest.mark.touches("packages/slots/src/moxi_slots/panes.py")

NOW = 1_000_000.0


def pane(session="slot-1", window="@1", index=0, *, home=False, run=False, pane_id=None,
         pane_index=0, dead=False, status=None, pid=100, command="bash", attached=0,
         shell="/bin/bash") -> Pane:
    return Pane(session, "$1", attached, window, index, home, run,
                pane_id or f"%{window[1:]}{pane_index}", pane_index, dead, status, pid,
                shell, command)


def home(**kw) -> Pane:
    return pane(window="@1", index=0, home=True, **kw)


def run(**kw) -> Pane:
    return pane(window="@2", index=1, run=True, command=kw.pop("command", "claude"), **kw)


def line(p: Pane) -> str:
    values = [p.session, p.session_id, str(p.attached), p.window_id, str(p.window_index),
              "on" if p.home else "", "on" if p.run else "", p.pane_id, str(p.pane_index),
              "1" if p.dead else "0", "" if p.dead_status is None else str(p.dead_status),
              str(p.pid), p.shell, p.command]
    return "\t".join(values)


def test_the_format_asks_for_every_field_with_the_free_text_one_last():
    assert panes.FORMAT.count("\t") == len(panes.FIELDS) - 1
    assert panes.FIELDS[-1] == "pane_current_command"
    assert "#{@moxi_slot_run}" in panes.FORMAT and "#{@moxi_slot_home}" in panes.FORMAT


def test_parse_reads_what_it_is_given_and_skips_a_short_line():
    rows = [home(), run(dead=True, status=3, command="claude\tx")]
    out = "\n".join(line(p) for p in rows) + "\nbroken\tline\n"
    assert panes.parse(out) == rows


def test_states():
    assert panes.facts(1, []).state == MISSING
    assert panes.facts(1, [home()]).state == FREE
    assert panes.facts(1, [home(), run()]).state == OCCUPIED
    assert panes.facts(1, [home(), run(dead=True, status=0)]).state == ENDED


def test_idle_needs_no_client_only_the_home_and_a_dead_run_window_and_the_shell():
    assert panes.facts(1, [home()]).idle
    assert panes.facts(1, [home(), run(dead=True)]).idle
    assert panes.facts(1, [home(command="-bash")]).idle
    assert panes.facts(1, [home(dead=True, command="")]).idle
    assert not panes.facts(1, [home(attached=1)]).idle
    assert not panes.facts(1, [home(), run()]).idle
    assert not panes.facts(1, [home(command="vim")]).idle
    assert not panes.facts(1, [home(), home(pane_index=1, pane_id="%9")]).idle
    mine = pane(window="@5", index=4)
    assert not panes.facts(1, [home(), mine]).idle
    assert not panes.facts(1, [run(dead=True)]).idle


def test_two_run_windows_read_occupied_when_either_runs():
    second = pane(window="@3", index=2, run=True, pane_id="%30", command="claude")
    f = panes.facts(1, [home(), run(dead=True), second])
    assert f.state == OCCUPIED and f.run.pane_id == "%30" and f.run_windows == 2


def test_join_states_holds_labels_and_drift():
    reg = Registry({
        1: Row(10.0, "review", Hold("run-1", NOW + 60), Last(20.0, "agent", {"id": 1})),
        2: Row(11.0, hold=Hold("old", NOW - 1), last=Last(21.0, "gone")),
        3: Row(12.0),
    })
    live = [home(session="slot-1"), run(session="slot-1", pid=4242),
            home(session="slot-2"),
            pane(session="slot-4", window="@9", home=True),
            pane(session="other", window="@8")]
    listing = panes.join(reg, live, NOW)
    assert listing.ok
    one, two, three = listing.slots
    assert (one.number, one.name, one.state, one.launchable) == (1, "review", OCCUPIED,
                                                                  False)
    assert one.hold == Hold("run-1", NOW + 60) and one.label == "agent"
    assert one.pane_pid == 4242 and one.current_command == "claude"
    assert one.dead_status is None and one.last.meta == {"id": 1}
    assert two.state == FREE and two.hold is None and two.launchable
    assert two.label is None and two.last.label == "gone" and two.pane_id is None
    assert three.state == MISSING and not three.launchable
    assert [s.number for s in listing.unregistered] == [4]
    assert listing.unregistered[0].created is None
    assert [(d.number, d.kind) for d in listing.drift] == [(3, "no-session"), (4, "no-row")]


def test_an_ended_run_pane_gives_its_exit_status_and_no_pid():
    reg = Registry({1: Row(1.0, last=Last(2.0, "x"))})
    found = panes.join(reg, [home(), run(dead=True, status=3)], NOW).find(1)
    assert found.state == ENDED and found.dead_status == 3
    assert found.pane_pid is None and found.current_command is None
    assert found.pane_id and found.window_id == "@2" and found.label == "x"


def fake_attempt(monkeypatch, results):
    calls = []

    def attempt(*args, timeout=None, **kw):
        calls.append(args)
        return results[args[0]]

    monkeypatch.setattr(tmux, "attempt", attempt)
    return calls


def result(code=0, out="", err=""):
    return tmux.Result((), code, out, err)


def test_read_is_one_call(monkeypatch):
    calls = fake_attempt(monkeypatch, {"list-panes": result(out=line(home()) + "\n")})
    assert panes.read() == [home()]
    assert [c[0] for c in calls] == ["list-panes"]


def test_read_with_no_server_is_no_panes(monkeypatch):
    fake_attempt(monkeypatch, {"list-panes": result(1, err="no server running on /x")})
    assert panes.read() == []


def test_read_of_a_server_with_no_session_is_no_panes(monkeypatch):
    calls = fake_attempt(monkeypatch, {"list-panes": result(1, err="no current target"),
                                       "list-sessions": result(0, "")})
    assert panes.read() == []
    assert [c[0] for c in calls] == ["list-panes", "list-sessions"]


def test_read_raises_on_another_failure(monkeypatch):
    fake_attempt(monkeypatch, {"list-panes": result(1, err="bad format"),
                               "list-sessions": result(0, "$1\n")})
    with pytest.raises(TmuxError, match="bad format"):
        panes.read()


def test_join_defaults_to_now():
    reg = Registry({1: Row(1.0, hold=Hold("h", time.time() + 30))})
    assert panes.join(reg, [home()]).find(1).hold.holder == "h"
