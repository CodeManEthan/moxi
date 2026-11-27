"""The machine's check lock as a counting lock: its places, admission by memory, the
lander's place in line, `--no-wait`, the waiting lines, and rounds' read of whether a
run still lives. Every lock is in the test's own state folder; the memory reading is
injected."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path

import pytest
from moxi import proc
from moxi_dev.testing import PROC_WAIT_S, wait_for

from moxi_rounds import check_line, checking

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX sessions and environments")
PY = sys.executable


def never() -> int:
    raise AssertionError("the memory was read")


def plenty() -> int:
    return 1_000_000


def lock() -> Path:
    path = checking.lock_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def fields(name: str = "proj-a/mine") -> dict:
    return {**check_line.me(), "round": name, "stage": "B1", "step": "check",
            "repo": "proj-a"}


def hold(holder, k: int, *, memory_mib: int | None = None, record: bool = True,
         name: str = "proj-a/other"):
    """A real process holding place `k`, with a holder record naming it (and
    `memory_mib`) unless `record` is false."""
    path = check_line.slot_path(lock(), k)
    held = holder(path)
    if record:
        ident = proc.identity(held.pid)
        check_line.write_holder(path, {
            "pid": held.pid, "created": ident.started, "round": name, "stage": "B1",
            "step": "check", "repo": "proj-a", "check_lock": k,
            **({} if memory_mib is None else {"memory_mib": memory_mib})})
    return held


def take_now(**options):
    """Take a place without waiting; the `Taken`, the place let go at once."""
    with check_line.slot(lock(), fields(), wait=False, **options) as taken:
        return taken


def refused(**options) -> str:
    with pytest.raises(check_line.NotFree) as caught:
        take_now(**options)
    return caught.value.words


# -- deciding ----------------------------------------------------------------------------

def held(k: int, memory_mib: int | None) -> check_line.Held:
    return check_line.Held(k, {"round": f"r{k}", "stage": "B1",
                               **({} if memory_mib is None else {"memory_mib": memory_mib})})


def test_a_run_that_would_be_alone_takes_the_first_and_reads_no_memory():
    found = check_line.decide(None, [], [1, 2], at_once=2, need_mib=0, available=never)
    assert (found.k, found.reason) == (1, "alone")


@pytest.mark.parametrize("need_mib, have, admitted", [
    (0, 300, True), (0, 299, False), (500, 500, True), (500, 499, False)])
def test_admission_needs_the_sum_or_need_mib_to_the_mib(need_mib, have, admitted):
    found = check_line.decide(100, [held(1, 200)], [2], at_once=2, need_mib=need_mib,
                              available=lambda: have)
    assert (found.k is not None) is admitted
    assert found.reason == ("beside" if admitted else "memory")
    assert (found.available_mib, found.need_mib) == (have, need_mib or 300)


def test_with_three_places_the_need_sums_both_held():
    found = check_line.decide(100, [held(1, 200), held(2, 300)], [3], at_once=3,
                              need_mib=0, available=lambda: 600)
    assert (found.k, found.need_mib) == (3, 600)


def test_a_run_with_no_memory_mib_waits_until_it_would_be_alone():
    found = check_line.decide(None, [held(1, 200)], [2], at_once=2, need_mib=0,
                              available=never)
    assert (found.k, found.reason) == (None, "own")


@pytest.mark.parametrize("other", [held(1, None), check_line.Held(1, None)])
def test_nothing_starts_beside_a_holder_with_no_memory_mib_or_no_record(other):
    found = check_line.decide(100, [other], [2], at_once=2, need_mib=0, available=never)
    assert (found.k, found.reason) == (None, "unknown")


def test_with_every_place_held_a_run_waits_and_reads_no_memory():
    found = check_line.decide(100, [held(1, 100), held(2, 100)], [], at_once=2,
                              need_mib=0, available=never)
    assert (found.k, found.reason) == (None, "held")


# -- the places --------------------------------------------------------------------------

def test_the_places_and_their_records_are_named_beside_the_first(fenced):
    path = lock()
    assert check_line.holder_file(path).name == "check.holder.json"
    assert check_line.slot_path(path, 2).name == "check-2.lock"
    assert check_line.holder_file(check_line.slot_path(path, 2)).name == "check-2.holder.json"
    assert check_line.places(path, 2) == [1, 2]
    check_line.slot_path(path, 4).touch()
    assert check_line.places(path, 1) == [1, 2, 3, 4]


def test_one_place_behaves_as_one_lock_and_reads_no_memory(fenced):
    with check_line.slot(lock(), fields(), memory_mib=100, wait=False,
                         available=never) as taken:
        record = check_line.holder(lock())
        assert (taken.k, taken.at_once, taken.beside) == (1, 1, False)
        assert record["check_lock"] == 1 and record["memory_mib"] == 100
        assert record["run_tag"] == taken.tag and len(taken.tag) == 32
    assert not check_line.holder_file(lock()).exists()


def test_two_places_hold_two_runs_and_a_third_is_refused(fenced, holder):
    hold(holder, 1, memory_mib=100)
    with check_line.slot(lock(), fields(), memory_mib=100, at_once=2, wait=False,
                         available=plenty) as taken:
        assert (taken.k, taken.beside) == (2, True)
        assert (taken.decision.available_mib, taken.decision.need_mib) == (plenty(), 200)
        assert check_line.holder(taken.path)["check_lock"] == 2
    hold(holder, 2, memory_mib=100, name="proj-a/second")
    words = refused(memory_mib=100, at_once=2, available=never)
    assert "held by proj-a/other B1" in words and "proj-a/second B1" in words


def test_the_lowest_free_place_is_taken(fenced, holder):
    hold(holder, 2, memory_mib=100)
    assert take_now(memory_mib=100, at_once=2, available=plenty).k == 1


def test_a_place_freed_beside_a_held_one_starts_a_second_check_not_a_first(fenced, holder):
    hold(holder, 2, memory_mib=100)
    words = refused(memory_mib=100, at_once=2, available=lambda: 150)
    assert "150" not in words and "0.1G available, 0.2G needed" in words


def test_lowering_at_once_while_a_higher_place_is_held_starts_nothing(fenced, holder):
    other = hold(holder, 2, memory_mib=100)
    refused(memory_mib=100, at_once=1, available=never)
    other.stdin.close()
    other.wait(timeout=PROC_WAIT_S)
    taken = take_now(memory_mib=100, at_once=1, available=never)
    assert (taken.k, taken.beside) == (1, False)


def test_raising_at_once_lets_the_next_run_in_only_by_the_memory_test(fenced, holder):
    hold(holder, 1, memory_mib=100)
    assert "available" in refused(memory_mib=100, at_once=2, available=lambda: 10)
    assert take_now(memory_mib=100, at_once=2, available=plenty).beside


def test_a_held_place_above_at_once_counts_in_the_sum(fenced, holder):
    hold(holder, 3, memory_mib=500)
    taken = take_now(memory_mib=100, at_once=2, available=plenty)
    assert (taken.k, taken.decision.need_mib) == (1, 600)


def test_nothing_starts_beside_a_lock_held_with_no_record(fenced, holder):
    hold(holder, 1, record=False)
    words = refused(memory_mib=100, at_once=2, available=never)
    assert "no memory_mib" in words


def test_no_wait_is_refused_while_a_ticket_waits(fenced):
    check_line.take(lock(), fields("proj-a/ahead"))
    words = refused(memory_mib=100, at_once=2, available=never)
    assert words.endswith("; 1 waiting")


def test_no_wait_with_one_place_says_what_it_said_before(fenced, holder):
    other = hold(holder, 1, record=False)
    words = refused()
    assert words == (f"another check holds or waits for the machine's check lock {lock()}: "
                     f"held by pid {other.pid}; 0 waiting")


# -- the line ----------------------------------------------------------------------------

def test_a_landers_ticket_goes_ahead_of_tickets_that_came_first(fenced):
    path = lock()
    early = check_line.take(path, fields("proj-a/early"))
    lander = check_line.take(path, {**fields("proj-a/lander"), "first": True})
    later = check_line.take(path, {**fields("proj-a/lander-2"), "first": True})
    assert [one.path for one in check_line.remove_dead(path)] == [lander, later, early]
    assert check_line.remove_dead(path, own=early) != []
    assert [one.path for one in check_line.remove_dead(path, own=later)] == [lander]
    assert check_line.remove_dead(path, own=lander) == []


def test_a_waiter_says_each_reason_once_then_where_it_got_in(fenced, holder):
    hold(holder, 1, memory_mib=1024)
    reads = iter([1024, 1024, 1024])
    said: list[str] = []
    with check_line.slot(lock(), fields(), memory_mib=1024, at_once=2, say=said.append,
                         available=lambda: next(reads, 4096)) as taken:
        assert taken.k == 2
    assert len(said) == 2
    assert said[0] == ("check lock 2 of 2 is free; waiting for memory: 1.0G available, a "
                       "second check needs 2.0G (proj-a/other B1's 1.0G); read every 0.5 s")
    assert said[1].startswith("got check lock 2 of 2 after ")
    assert said[1].endswith(" s, beside proj-a/other B1 (4.0G available, 2.0G needed)")
    assert check_line.tickets(lock()) == []


def test_a_waiter_in_line_behind_a_run_waiting_on_memory_does_not_pass_it(fenced, holder):
    """The first in line waits on memory; a later, smaller run does not pass it."""
    hold(holder, 1, memory_mib=100)
    ahead = check_line.take(lock(), {**fields("proj-a/big"), "memory_mib": 4000})
    words = refused(memory_mib=64, at_once=2, available=plenty)
    assert words.endswith("; 1 waiting")
    check_line.remove(ahead)


# -- whether a run lives -----------------------------------------------------------------

SLEEP = "import time; time.sleep(120)"
LEADER = textwrap.dedent("""
    import subprocess, sys, time
    member = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    print(member.pid, flush=True)
    time.sleep(120)
""")


@pytest.fixture
def started():
    """`started(argv, tag=None, setsid=False)`: a real process, killed when the test
    ends; with `tag`, its environment carries it."""
    procs = []

    def start(argv, *, tag=None, setsid=False, stdout=None):
        env = dict(os.environ)
        env.pop(proc.RUN_TAG_ENV, None)
        if tag is not None:
            env[proc.RUN_TAG_ENV] = tag
        one = subprocess.Popen(argv, env=env, start_new_session=setsid, stdout=stdout,
                               text=True)
        procs.append(one)
        wait_for(lambda: proc.identity(one.pid) is not None, PROC_WAIT_S,
                 what="the process to start")
        return one

    yield start
    for one in procs:
        if one.poll() is None:
            one.kill()
        one.wait(timeout=PROC_WAIT_S)
        if one.stdout is not None:
            one.stdout.close()


def a_tag() -> str:
    return uuid.uuid4().hex


def record(one=None, *, tag=None, started_s=None) -> dict:
    found: dict = {"run_tag": tag}
    if one is not None:
        found.update(run_pid=one.pid,
                     run_started=proc.identity(one.pid).started if started_s is None
                     else started_s)
    return found


@POSIX
def test_a_live_first_process_named_by_pid_and_start_time_is_the_run(started):
    tag = a_tag()
    assert check_line.run_alive(record(started([PY, "-c", SLEEP], tag=tag), tag=tag))


@POSIX
def test_a_tagged_process_keeps_the_run_alive_once_the_first_has_ended(started):
    tag = a_tag()
    first = started([PY, "-c", "pass"], tag=tag)
    gone = record(first, tag=tag)
    first.wait(timeout=PROC_WAIT_S)
    assert not check_line.run_alive(gone)
    started([PY, "-c", SLEEP], tag=tag, setsid=True)
    assert check_line.run_alive(gone)
    assert check_line.run_alive({"run_tag": tag})  # named before its first process was


@POSIX
def test_a_run_is_not_alive_once_all_of_it_is_gone(started):
    tag = a_tag()
    one = started([PY, "-c", SLEEP], tag=tag)
    found = record(one, tag=tag)
    one.kill()
    one.wait(timeout=PROC_WAIT_S)
    assert not check_line.run_alive(found)


@POSIX
def test_a_live_pid_with_another_start_time_is_not_the_run(started):
    one = started([PY, "-c", SLEEP])
    assert not check_line.run_alive(record(one, tag=a_tag(), started_s=12345.0))


@POSIX
def test_a_first_pid_match_carrying_another_tag_is_not_the_run(started):
    one = started([PY, "-c", SLEEP], tag=a_tag())
    assert not check_line.run_alive(record(one, tag=a_tag()))


@POSIX
def test_a_later_group_leader_under_the_pid_and_its_members_are_not_the_run(started):
    """The reused-pid case: a process that made itself a group leader under the pid
    the record names, started after the run, carries no tag; nor does a member of its
    group."""
    leader = started([PY, "-c", LEADER], setsid=True, stdout=subprocess.PIPE)
    member = int(leader.stdout.readline())
    assert os.getpgid(member) == leader.pid
    stale = record(leader, tag=a_tag(), started_s=proc.identity(leader.pid).started - 100)
    assert not check_line.run_alive(stale)


@POSIX
def test_an_env_that_cant_be_read_counts_as_no_tag(started, monkeypatch):
    tag = a_tag()
    one = started([PY, "-c", SLEEP], tag=tag)
    monkeypatch.setattr(proc, "process_env", lambda pid: None)
    assert check_line.run_alive(record(one, tag=tag))  # pid and start time decide alone
    assert not check_line.run_alive({"run_tag": tag})


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only; owed to Windows")
def test_on_windows_pid_and_start_time_alone_decide(started):
    one = started([PY, "-c", SLEEP])
    assert check_line.run_alive(record(one, tag=a_tag()))
    assert not check_line.run_alive({"run_tag": a_tag()})


@POSIX
def test_an_orphans_run_keeps_its_place_taken(fenced, started):
    tag = a_tag()
    run = started([PY, "-c", SLEEP], tag=tag, setsid=True)
    dead = started([PY, "-c", "pass"])
    dead.wait(timeout=PROC_WAIT_S)
    path = lock()
    check_line.write_holder(path, {"pid": dead.pid, "created": time.time() - 50,
                                   "round": "proj-a/gone", "stage": "B1",
                                   "check_lock": 1, "memory_mib": 100, **record(run, tag=tag)})
    assert check_line.orphan(path)["round"] == "proj-a/gone"
    refused(memory_mib=100, at_once=1, available=never)
    assert take_now(memory_mib=100, at_once=2, available=plenty).k == 2
    run.kill()
    run.wait(timeout=PROC_WAIT_S)
    assert check_line.orphan(path) is None
    assert take_now(memory_mib=100, at_once=1, available=never).k == 1
    assert not check_line.holder_file(path).exists()


def test_the_holder_record_of_each_place_is_json(fenced, holder):
    hold(holder, 2, memory_mib=100)
    found = json.loads(check_line.holder_file(check_line.slot_path(lock(), 2)).read_text())
    assert found["check_lock"] == 2
