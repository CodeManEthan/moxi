"""Launches against a real tmux: start, respawn, the exec verdict, no environment bleed,
wait_ended and the detached jobs, each test on its own private server.

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
from moxi_slots import cli, doctor, jobs, launch, places, registry, setup, slots, tmux
from moxi_slots.errors import Refused, TryAgain, UsageError
from moxi_slots.model import ENDED, OCCUPIED
from moxi_slots.trampoline import TERM_NAMES

from slots_kit import kill, pane_field, start_server, wait_answer, wait_for

pytestmark = pytest.mark.touches("packages/slots/src/moxi_slots/launch.py",
                                 "packages/slots/src/moxi_slots/launchfile.py",
                                 "packages/slots/src/moxi_slots/trampoline.py",
                                 "packages/slots/src/moxi_slots/jobs.py",
                                 "packages/slots/src/moxi_slots/slots.py",
                                 "packages/slots/src/moxi_slots/cli.py")

PY = sys.executable
#: A child that writes its whole environment, as JSON, to the file named by its argument.
DUMP = "import json, os, sys; json.dump(dict(os.environ), open(sys.argv[1], 'w'))"


@pytest.fixture
def base(tmp_path) -> dict[str, str]:
    return {"PATH": os.environ["PATH"], "LANG": "C.UTF-8"}


def go(slot, *argv, env, cwd, label="agent", **kw):
    return launch.start(slot, list(argv), env=env, cwd=str(cwd), label=label, **kw)


def again(slot, *argv, env, cwd, label="agent", **kw):
    return launch.respawn(slot, list(argv), env=env, cwd=str(cwd), label=label, **kw)


def ended(placed) -> None:
    wait_for(lambda: pane_field(placed.pane_id, "pane_dead") == "1", what="the pane to die")


def launch_files() -> list[str]:
    try:
        return os.listdir(places.launch_dir())
    except FileNotFoundError:
        return []


def test_start_places_a_launch_in_the_lowest_launchable_slot(server, base, tmp_path):
    slots.create(count=2)
    placed = go(None, "sleep", "60", env=base, cwd=tmp_path, label="first",
                meta={"session": "s-1"})
    assert (placed.slot, placed.exec, placed.exec_line) == (1, "started", "exec")
    assert placed.dead_status is None
    assert pane_field(placed.pane_id, "@moxi_slot_run") == "on"
    assert pane_field(placed.pane_id, "remain-on-exit") == "on"
    assert pane_field(placed.pane_id, "window_name") == "first"
    assert pane_field(placed.pane_id, "window_active") == "1"
    assert pane_field(placed.pane_id, "window_id") == placed.window_id
    one = slots.show(1)
    assert (one.state, one.label, one.pane_id) == (OCCUPIED, "first", placed.pane_id)
    assert one.last.label == "first" and one.last.meta == {"session": "s-1"}
    assert go(None, "sleep", "60", env=base, cwd=tmp_path).slot == 2
    with pytest.raises(Refused) as caught:
        go(None, "sleep", "60", env=base, cwd=tmp_path)
    assert caught.value.reason == "no-free-slot"
    third = go(None, "sleep", "60", env=base, cwd=tmp_path, create=True)
    assert (third.slot, third.exec) == (3, "started")
    assert sorted(registry.read().rows) == [1, 2, 3]
    assert launch_files() == []


def test_start_with_create_makes_the_first_slot(server, base, tmp_path):
    placed = go(None, "sleep", "60", env=base, cwd=tmp_path, create=True)
    assert (placed.slot, placed.exec) == (1, "started")
    assert slots.show(1).state == OCCUPIED


def test_a_start_naming_a_slot_never_creates_it(server, base, tmp_path):
    with pytest.raises(UsageError):
        go(4, "true", env=base, cwd=tmp_path, create=True)
    with pytest.raises(Refused) as caught:
        go(4, "true", env=base, cwd=tmp_path)
    assert caught.value.reason == "no-slot"
    slots.create([4], name="review")
    tmux.call("kill-session", "-t", "=slot-4")
    with pytest.raises(Refused) as caught:
        go("review", "true", env=base, cwd=tmp_path)
    assert caught.value.reason == "missing"
    assert "moxi slots create 4" in caught.value.words
    assert slots.create([4]) == [4]
    assert go("review", "sleep", "60", env=base, cwd=tmp_path).slot == 4


def test_a_taken_slot_is_refused_naming_who_has_it(server, base, tmp_path):
    slots.create(count=2)
    go(1, "sleep", "60", env=base, cwd=tmp_path, label="busy", meta={"run": 7})
    with pytest.raises(Refused) as caught:
        go(1, "true", env=base, cwd=tmp_path)
    assert caught.value.reason == "occupied"
    assert caught.value.holder == {"label": "busy", "meta": {"run": 7}}
    hold = slots.hold(2, "run-9", 60)
    with pytest.raises(Refused) as caught:
        go(2, "true", env=base, cwd=tmp_path, hold="run-8")
    assert caught.value.reason == "held"
    assert caught.value.holder == {"holder": "run-9", "until": hold.until}
    with pytest.raises(Refused) as caught:
        go(None, "true", env=base, cwd=tmp_path)
    assert caught.value.reason == "no-free-slot"
    placed = go(None, "sleep", "60", env=base, cwd=tmp_path, hold="run-9")
    assert placed.slot == 2
    assert registry.read().rows[2].hold is None


def test_a_start_into_an_ended_slot_reuses_its_pane(server, base, tmp_path):
    slots.create()
    first = go(1, "sh", "-c", "exit 3", env=base, cwd=tmp_path)
    assert first.exec == "started"
    ended(first)
    assert slots.show(1).state == ENDED and slots.show(1).dead_status == 3
    second = go(1, "sleep", "60", env=base, cwd=tmp_path, label="next")
    assert (second.pane_id, second.window_id) == (first.pane_id, first.window_id)
    assert pane_field(second.pane_id, "window_name") == "next"
    assert slots.show(1).state == OCCUPIED
    run_windows = [p for p in slots.panes.read() if p.session == "slot-1" and p.run]
    assert len(run_windows) == 1


def test_a_short_command_is_started_with_its_exit_status(server, base, tmp_path):
    slots.create()
    placed = go(1, "true", env=base, cwd=tmp_path)
    assert placed.exec == "started"
    assert placed.dead_status in (None, 0)


def test_respawn_keeps_the_pane_and_ends_the_hold_naming_it(server, base, tmp_path):
    slots.create()
    first = go(1, "sh", "-c", "sleep 0.3", env=base, cwd=tmp_path)
    slots.hold(1, "relaunch-1", 60)
    with pytest.raises(Refused) as caught:
        again(1, "true", env=base, cwd=tmp_path, hold="relaunch-1")
    assert caught.value.reason == "live-pane"
    assert pane_field(first.pane_id, "pane_dead") == "0"
    ended(first)
    with pytest.raises(Refused) as caught:
        again(1, "true", env=base, cwd=tmp_path)
    assert caught.value.reason == "held"
    placed = again(1, "sleep", "60", env=base, cwd=tmp_path, hold="relaunch-1",
                   label="relaunched")
    assert (placed.exec, placed.pane_id) == ("started", first.pane_id)
    found = slots.show(1)
    assert found.hold is None and found.last.label == "relaunched"
    assert registry.read().rows[1].hold is None


def test_respawn_needs_an_ended_run_pane(server, base, tmp_path):
    slots.create()
    with pytest.raises(Refused) as caught:
        again(1, "true", env=base, cwd=tmp_path)
    assert caught.value.reason == "missing"
    assert "no run pane" in caught.value.words
    with pytest.raises(Refused) as caught:
        again(2, "true", env=base, cwd=tmp_path)
    assert caught.value.reason == "no-slot"


def test_a_missing_cwd_or_unknown_program_fails_and_never_starts(server, base, tmp_path):
    slots.create()
    placed = go(1, "true", env=base, cwd=tmp_path / "gone")
    assert placed.exec == "failed"
    assert placed.exec_line.startswith(f"failed: cwd: {tmp_path / 'gone'}: ")
    assert placed.dead_status == 127
    placed = go(1, "no-such-program-here", env=base, cwd=tmp_path)
    assert placed.exec == "failed"
    assert placed.exec_line.startswith("failed: argv[0]: no-such-program-here")
    assert placed.dead_status == 127
    assert slots.show(1).state == ENDED
    assert launch_files() == []


@pytest.fixture
def sentinel_server(tmux_path, monkeypatch):
    """A private server whose own environment carries a sentinel."""
    monkeypatch.setenv("LC_MOXI_SENTINEL", "server-start")
    proc = start_server()
    try:
        wait_answer()
        yield
    finally:
        kill(tmux_path, places.socket(), proc)


def test_no_environment_bleeds_into_a_launch(sentinel_server, base, tmp_path):
    """The server starts with a sentinel and later gets a global one; launch A sets A=1;
    the respawned launch B, whose env carries a foreign TMUX_PANE, sees exactly B's env
    plus the pane's terminal names, with tmux's own TMUX_PANE."""
    tmux.call("set-environment", "-g", "LATER_SECRET", "global")
    slots.create()
    out_a, out_b = tmp_path / "a.json", tmp_path / "b.json"
    env_a = {**base, "A": "1"}
    a = go(1, PY, "-c", DUMP, str(out_a), env=env_a, cwd=tmp_path)
    assert a.exec == "started"
    ended(a)
    env_b = {**base, "B": "b-secret-value", "TMUX_PANE": "%999"}
    b = again(1, PY, "-c", DUMP, str(out_b), env=env_b, cwd=tmp_path)
    assert (b.exec, b.pane_id) == ("started", a.pane_id)
    ended(b)
    seen_a, seen_b = json.loads(out_a.read_text()), json.loads(out_b.read_text())
    for seen, env in ((seen_a, env_a), (seen_b, env_b)):
        assert {k: v for k, v in seen.items() if k not in TERM_NAMES} == \
            {k: v for k, v in env.items() if k not in TERM_NAMES}
        assert seen["TMUX_PANE"] == b.pane_id
        assert seen["TMUX"].split(",")[0] == places.socket()
        assert "TERM" in seen
    assert "A" not in seen_b
    start_command = pane_field(b.pane_id, "pane_start_command")
    assert "b-secret-value" not in start_command
    assert "moxi-slots-exec" in start_command


def test_two_starts_into_one_slot_at_once_one_wins(server, tmp_path):
    slots.create()
    code = ("import os, sys\n"
            "from moxi_slots import launch\n"
            "from moxi_slots.errors import Refused\n"
            "try:\n"
            "    p = launch.start(1, ['sleep', '60'], env={'PATH': os.environ['PATH']},\n"
            "                     cwd='/', label='race')\n"
            "    print('placed', p.exec)\n"
            "except Refused as exc:\n"
            "    print('refused', exc.reason)\n")
    procs = [subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) for _ in range(2)]
    outs = [p.communicate(timeout=30) for p in procs]
    assert all(p.returncode == 0 for p in procs), outs
    assert sorted(out.strip() for out, _ in outs) == ["placed started", "refused occupied"]
    runs = [p for p in slots.panes.read() if p.session == "slot-1" and p.run]
    assert len(runs) == 1


def test_wait_ended_wakes_when_the_pane_dies(server, base, tmp_path, monkeypatch):
    # The pane dies at 0.5 s, inside the first one-second wait. Only the pane-died hook's
    # signal ends that wait early; without it the wait runs out (TryAgain) first.
    slots.create(count=2)
    waits: list[str] = []
    real = tmux.attempt

    def counted(*args, **kw):
        if args[:1] != ("wait-for",):
            return real(*args, **kw)
        try:
            got = real(*args, **kw)
        except TryAgain:
            waits.append("ran out")
            raise
        waits.append("signalled" if got.ok else "failed")
        return got

    monkeypatch.setattr(tmux, "attempt", counted)
    placed = go(1, "sh", "-c", "sleep 0.5; exit 4", env=base, cwd=tmp_path)
    started = time.monotonic()
    got = launch.wait_ended(1, pane_id=placed.pane_id, timeout_s=10)
    assert (got.ended, got.dead_status, got.gone) == (True, 4, False)
    assert waits == ["signalled"]
    assert time.monotonic() - started < 0.95
    monkeypatch.setattr(tmux, "attempt", real)
    again_ = launch.wait_ended(1, pane_id=placed.pane_id, timeout_s=0)
    assert again_.ended and again_.dead_status == 4
    long = go(2, "sleep", "60", env=base, cwd=tmp_path)
    started = time.monotonic()
    got = launch.wait_ended(2, pane_id=long.pane_id, timeout_s=0.3)
    assert not got.ended
    assert time.monotonic() - started < 2
    with pytest.raises(UsageError):
        launch.wait_ended(1, pane_id=long.pane_id, timeout_s=0)
    tmux.call("kill-window", "-t", long.window_id)
    assert launch.wait_ended(2, pane_id=long.pane_id, timeout_s=1).gone


def test_a_label_is_shown_as_written(server, base, tmp_path):
    slots.create()
    placed = go(1, "sleep", "60", env=base, cwd=tmp_path, label="a #{pane_id} b")
    assert pane_field(placed.pane_id, "window_name") == "a #{pane_id} b"


def test_a_job_runs_detached_and_its_name_is_held_while_it_runs(server, base, tmp_path):
    job = jobs.run_job("relaunch-1", ["sleep", "60"], env=base, cwd=str(tmp_path))
    assert (job.exec, job.running, job.name) == ("started", True, "relaunch-1")
    assert job.pane_pid
    found = jobs.job("relaunch-1")
    assert found.running and found.pane_id == job.pane_id and found.pane_pid
    assert pane_field(job.pane_id, "remain-on-exit") == "off"
    assert pane_field(job.pane_id, "session_name") == "moxi-jobs"
    with pytest.raises(Refused) as caught:
        jobs.run_job("relaunch-1", ["true"], env=base, cwd=str(tmp_path))
    assert caught.value.reason == "job-running"
    quick = jobs.run_job("quick", ["true"], env=base, cwd=str(tmp_path))
    assert quick.exec == "started"
    wait_for(lambda: jobs.job("quick") is None, what="the quick job's window to go")
    failed = jobs.run_job("bad", ["true"], env=base, cwd=str(tmp_path / "gone"))
    assert failed.exec == "failed" and failed.exec_line.startswith("failed: cwd:")
    assert [j.name for j in jobs.jobs()] == ["relaunch-1"]
    assert slots.list_slots().slots == ()
    assert launch_files() == []


def test_the_teardown_refuses_while_a_job_runs(server, base, tmp_path):
    jobs.run_job("relaunch-2", ["sleep", "60"], env=base, cwd=str(tmp_path))
    with pytest.raises(UninstallRefused) as caught:
        setup.teardown()
    assert "job relaunch-2 is running" in caught.value.reason


def test_the_jobs_verb(server, base, tmp_path, capsys):
    assert cli.main(["jobs"]) == 0
    assert capsys.readouterr().out.strip() == "no jobs"
    jobs.run_job("j1", ["sleep", "60"], env=base, cwd=str(tmp_path))
    assert cli.main(["jobs", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [(j["name"], j["running"]) for j in listed] == [("j1", True)]
    assert cli.main(["jobs"]) == 0
    assert "j1" in capsys.readouterr().out


def test_the_doctor_finds_the_trampoline(server):
    row = doctor.trampoline_row()
    assert row.state == "ok", row
