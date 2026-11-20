"""How each run ended, against a real tmux: the run token and server in `last`, the
pane-died hook's `ended` record, a late hook of an earlier run, and the hook's lock wait.

Skipped, with the reason, where tmux 3.2 or newer is not on PATH.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

from moxi import files
from moxi_slots import cli, launch, places, registry, runs, slots, tmux
from moxi_slots.errors import Refused, TryAgain, UsageError
from moxi_slots.model import Last, RunEnd
from moxi_slots.registry import Registry, Row

from slots_kit import pane_field, start_server, wait_answer, wait_for

pytestmark = pytest.mark.touches("packages/slots/src/moxi_slots/runs.py",
                                 "packages/slots/src/moxi_slots/launch.py",
                                 "packages/slots/src/moxi_slots/registry.py",
                                 "packages/slots/src/moxi_slots/model.py",
                                 "packages/slots/src/moxi_slots/cli.py",
                                 "packages/slots/src/moxi_slots/panes.py",
                                 "packages/slots/src/moxi_slots/slots.py")


@pytest.fixture
def base() -> dict[str, str]:
    return {"PATH": os.environ["PATH"], "LANG": "C.UTF-8"}


def go(slot, *argv, env, cwd, **kw):
    kw.setdefault("label", "agent")
    return launch.start(slot, list(argv), env=env, cwd=str(cwd), **kw)


def ended_of(number: int, timeout: float = 10.0) -> RunEnd:
    return wait_for(lambda: registry.read().rows[number].last.ended, timeout=timeout,
                    what=f"slot {number}'s ended record")


def hold_lock(seconds: float) -> subprocess.Popen:
    places.module_lock().parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time\n"
         "from moxi import files\n"
         f"with files.lock({str(places.module_lock())!r}):\n"
         "    print('held', flush=True)\n"
         f"    time.sleep({seconds})\n"],
        stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "held"
    return proc


def test_a_placement_records_its_run_and_server(server, base, tmp_path):
    slots.create()
    placed = go(1, "sleep", "60", env=base, cwd=tmp_path)
    last = slots.show(1).last
    assert last.run and len(last.run) == 16
    assert last.server == f"{runs.boot_id()}:{server.pid}"
    assert last.status_trusted is True and last.ended is None
    hooks = tmux.call("show-hooks", "-w", "-t", placed.window_id).out
    assert runs.HOOK in hooks and last.run in hooks


def test_an_exit_with_status_0_is_recorded_with_the_pane_death_time(server, base,
                                                                    tmp_path):
    slots.create()
    before = time.time()
    placed = go(1, "sh", "-c", "sleep 0.3; exit 0", env=base, cwd=tmp_path)
    got = ended_of(1)
    assert (got.status, got.signal, got.settled) == (0, None, None)
    dead_at = int(pane_field(placed.pane_id, "pane_dead_time"))
    assert got.at == float(dead_at) and got.at >= int(before)


def test_a_kill_by_sighup_is_recorded_with_its_signal(server, base, tmp_path):
    slots.create()
    placed = go(1, "sleep", "60", env=base, cwd=tmp_path, status_trusted=False)
    os.kill(int(pane_field(placed.pane_id, "pane_pid")), signal.SIGHUP)
    got = ended_of(1)
    assert (got.status, got.signal) == (None, signal.SIGHUP)
    last = slots.show(1).last
    assert last.status_trusted is False and last.ended == got


def test_a_late_hook_of_the_run_before_writes_nothing(server, base, tmp_path):
    slots.create()
    first = go(1, "sh", "-c", "exit 3", env=base, cwd=tmp_path)
    old = ended_of(1)
    old_token = registry.read().rows[1].last.run
    assert old.status == 3
    again = launch.respawn(1, ["sleep", "60"], env=base, cwd=str(tmp_path), label="next")
    assert again.pane_id == first.pane_id
    last = registry.read().rows[1].last
    assert last.run != old_token and last.ended is None
    assert runs.main([str(places.state_dir()), "1", old_token, "0", "", "1"]) == 0
    assert registry.read().rows[1].last.ended is None
    hooks = tmux.call("show-hooks", "-w", "-t", first.window_id).out
    assert last.run in hooks and old_token not in hooks


def test_the_hook_waits_for_the_lock(server, base, tmp_path):
    slots.create()
    go(1, "sleep", "60", env=base, cwd=tmp_path)
    pane = slots.show(1).pane_id
    holder = hold_lock(1.5)
    try:
        os.kill(int(pane_field(pane, "pane_pid")), signal.SIGTERM)
        wait_for(lambda: pane_field(pane, "pane_dead") == "1", what="the pane to die")
        assert registry.read().rows[1].last.ended is None
    finally:
        holder.wait(timeout=10)
    assert ended_of(1).signal == signal.SIGTERM


def test_record_gives_up_after_its_wait_and_main_never_fails(tmux_path, tmp_path):
    reg = Registry({2: Row(1.0, last=Last(1.0, "agent", run="t-1"))})
    with registry.locked():
        registry.write(reg)
    holder = hold_lock(5)
    try:
        with pytest.raises(TryAgain):
            runs.write_ended(2, "t-1", 0, None, 5.0, timeout_s=0.2)
    finally:
        holder.kill()
        holder.wait(timeout=5)
    assert runs.LOCK_WAIT_S == 60.0
    gone = tmp_path / "gone"
    assert runs.main([str(gone), "x", "t-1", "", "", ""]) == 0
    assert "ValueError" in files.read_records(gone / places.HOOKS_LOG)[0]["error"]
    assert runs.main([]) == 0
    assert runs.write_ended(2, "t-1", 0, None, 5.0)
    assert registry.read().rows[2].last.ended == RunEnd(5.0, 0, None)
    assert not runs.write_ended(3, "t-1", 0, None, 5.0)


def test_the_hook_reaches_a_state_dir_with_awkward_characters(server, tmp_path):
    state_dir = tmp_path / "it's #{here} $HOME"
    state_dir.mkdir()
    reg = Registry({1: Row(1.0, last=Last(1.0, "agent", run="tok"))})
    registry.write(reg, state_dir / "slots.json")
    slots.create()
    window, pane = tmux.call("new-window", "-d", "-P", "-F", "#{window_id} #{pane_id}",
                             "-t", "=slot-1:", "sh", "-c", "sleep 0.3; exit 5"
                             ).out.split()
    tmux.call("set-option", "-w", "-t", window, "remain-on-exit", "on", ";",
              "set-hook", "-w", "-t", window, runs.HOOK,
              runs.hook_command(1, "tok", state_dir))
    got = wait_for(lambda: registry.read(state_dir / "slots.json").rows[1].last.ended,
                   timeout=10, what="the record in the odd directory")
    assert got.status == 5
    assert not places.hooks_log(state_dir).exists()
    assert pane_field(pane, "pane_dead") == "1"
    assert pane_field(pane, "pane_in_mode") == "0"


def test_a_hook_that_cannot_start_leaves_a_line_for_the_doctor(server, tmp_path,
                                                               monkeypatch):
    state_dir = tmp_path / "it's #{here} $HOME 100%s"
    state_dir.mkdir()
    gone = tmp_path / "moved" / "python"
    with monkeypatch.context() as patched:
        patched.setattr(runs.sys, "executable", str(gone))
        command = runs.hook_command(1, "tok", state_dir)
    slots.create()
    before = time.time()
    window = tmux.call("new-window", "-d", "-P", "-F", "#{window_id}", "-t", "=slot-1:",
                       "sh", "-c", "sleep 0.3; exit 5").out.strip()
    tmux.call("set-option", "-w", "-t", window, "remain-on-exit", "on", ";",
              "set-hook", "-w", "-t", window, runs.HOOK, command)
    log = places.hooks_log(state_dir)
    [logged] = wait_for(lambda: log.exists() and files.read_records(log), timeout=10,
                        what="the hooks log line")
    assert (logged["slot"], logged["run"]) == ("1", "tok")
    assert logged["error"] == f"the hook could not start {gone} (exit 127)"
    assert int(before) <= logged["at"] <= time.time() + 1
    assert not (state_dir / "slots.json").exists()


def test_status_trusted_is_a_bool(server, base, tmp_path):
    slots.create()
    with pytest.raises(UsageError):
        go(1, "true", env=base, cwd=tmp_path, status_trusted="no")


def test_show_prints_how_the_last_run_ended(server, base, tmp_path, capsys):
    slots.create()
    go(1, "sh", "-c", "exit 4", env=base, cwd=tmp_path)
    ended_of(1)
    assert cli.main(["show", "1"]) == 0
    assert "status 4 at" in capsys.readouterr().out
    assert cli.main(["show", "1", "--json"]) == 0
    assert '"status": 4' in capsys.readouterr().out


def restart(old: subprocess.Popen | None = None) -> subprocess.Popen:
    """Kill the server answering on the test's socket and start another."""
    tmux.attempt("kill-server")
    if old is not None:
        old.wait(timeout=10)
    wait_for(lambda: tmux.ping(timeout=0.5) is None, what="the server to go")
    proc = start_server()
    wait_answer()
    return proc


def test_a_run_under_a_server_that_went_down_reads_crashed(tmux_path, stop_after, base,
                                                          tmp_path, capsys):
    first = start_server()
    wait_answer()
    slots.create(count=3)
    go(1, "sleep", "60", env=base, cwd=tmp_path)
    go(2, "sh", "-c", "exit 0", env=base, cwd=tmp_path)
    go(3, "sh", "-c", "exit 0", env=base, cwd=tmp_path, status_trusted=False)
    ended_of(2)
    ended_of(3)
    assert not any(s.crashed for s in slots.list_slots().slots)
    restart(first)
    one, two, three = slots.list_slots().slots
    assert (one.state, one.crashed) == ("missing", True)
    assert (two.crashed, three.crashed) == (False, True)
    assert cli.main(["list"]) == 0
    assert "missing, crashed" in capsys.readouterr().out
    assert slots.settle(1, "nothing to restore") is True
    found = slots.show(1)
    assert not found.crashed and found.last.ended.settled == "nothing to restore"
    assert found.last.ended.status is None and found.last.ended.signal is None
    placed = go(None, "sleep", "60", env=base, cwd=tmp_path, create=True)
    assert placed.slot == 1 and not slots.show(1).crashed
    with pytest.raises(Refused) as caught:
        slots.settle(1, "x")
    assert caught.value.reason == "occupied"
    with pytest.raises(UsageError):
        slots.settle(3, "")
    tmux.attempt("kill-server")
    wait_for(lambda: tmux.ping(timeout=0.5) is None, what="the server to go")
    listing = slots.list_slots()
    assert listing.ok and not any(s.crashed for s in listing.slots)


def test_a_slot_with_no_last_launch_settles_nothing(server):
    slots.create()
    assert slots.settle(1, "nothing") is False
    with pytest.raises(Refused) as caught:
        slots.settle(4, "nothing")
    assert caught.value.reason == "no-slot"


def test_a_run_of_an_earlier_boot_reads_crashed_by_its_journal_end(server, monkeypatch):
    slots.create(count=3)
    monkeypatch.setattr(runs, "_journal_ends", {})
    monkeypatch.setattr(runs, "_journal_boots", lambda: {"0ldb00t": 5000.0})
    reg = registry.read()
    reg.rows[1].last = Last(1.0, "a", run="t1", server="0ldb00t:42")
    reg.rows[2].last = Last(1.0, "b", run="t2", server="0ldb00t:42",
                            ended=RunEnd(4950.0, None, 1))
    reg.rows[3].last = Last(1.0, "c", run="t3", server="0ldb00t:42",
                            ended=RunEnd(4000.0, None, 1))
    with registry.locked():
        registry.write(reg)
    for number in (1, 2, 3):
        tmux.call("kill-session", "-t", f"=slot-{number}")
    assert [s.crashed for s in slots.list_slots().slots] == [True, True, False]
