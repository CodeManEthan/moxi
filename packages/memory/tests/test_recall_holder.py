"""A recall's holder, named on its record from its first write and handed on, and the
rule a record obeys while it waits for its fork: only on the subagent route, only while
its holder lives and at most a bound from its start, ordered by write sequence, measured
on a clock no wall-clock step moves where there is one, and an `at` later than now
clamped by whoever holds the session's lock."""

import os
import sys
import time

import pytest
from moxi_turns import Deny, ToolCall

from moxi_memory import (recall_child, recall_guard, recall_holder, recall_start, recalls,
                         records)

SESSION = "0190b000-0000-7000-8000-000000000007"
LINUX = sys.platform.startswith("linux")


def call(tool, agent="agent-9", **given):
    return ToolCall(harness="claude", session_id=SESSION, agent_id=agent, tool=tool,
                    input=given)


def a_record(*, pid=None, route="subagent", **fields):
    record = recall_start.begin(SESSION, "claude", start="prompt", text="x", senders=(),
                                settings=recall_start.default_settings(), cwd=None,
                                notes_dir=None, route_name=route,
                                spawn=lambda s, r: pid)
    if fields:
        with recalls.session_lock(SESSION):
            record = records.update(SESSION, record["recall"], **fields)
    return record


def found():
    return recall_guard.fork_record(SESSION, "agent-9", wait_s=0.1)


# -- the holder, named from the first write and handed on ------------------------------

def test_the_record_names_the_process_started_for_it_from_its_first_write():
    record = a_record(pid=os.getpid())
    assert record["holder"]["pid"] == os.getpid()
    assert record["holder"]["started"] == pytest.approx(recall_holder.me()["started"])
    assert recall_holder.alive(record["holder"])
    assert isinstance(record["seq"], int)


def test_the_starter_names_the_child_and_the_child_names_itself():
    record = a_record(pid=os.getpid())
    child = os.getppid()
    recall_child.detach(["--session", SESSION, "--recall", record["recall"]],
                        spawn=lambda argv: child)
    assert records.read(SESSION, record["recall"])["holder"]["pid"] == child
    recall = recall_child.Recall(records.read(SESSION, record["recall"]))
    recall_child.open_line(recall)
    assert records.read(SESSION, record["recall"])["holder"]["pid"] == os.getpid()


def test_a_starter_never_overwrites_a_child_that_named_itself():
    record = a_record(pid=None)
    with recalls.session_lock(SESSION):
        records.update(SESSION, record["recall"], holder={"pid": os.getppid(),
                                                          "started": 1.0})
    recall_child.name_holder(SESSION, record["recall"], os.getpid())
    assert records.read(SESSION, record["recall"])["holder"]["pid"] == os.getppid()


def test_a_starter_run_through_a_launcher_of_its_own_names_the_child():
    # Windows runs a console script through an .exe launcher: begin records that
    # launcher's pid, the parent of the python the starter runs in, not os.getpid().
    record = a_record(pid=os.getppid())
    assert record["holder"]["pid"] == os.getppid() != os.getpid()
    recall_child.name_holder(SESSION, record["recall"], os.getpid())
    assert records.read(SESSION, record["recall"])["holder"]["pid"] == os.getpid()


def test_a_child_whose_identity_cannot_be_read_leaves_the_record_naming_none():
    record = a_record(pid=os.getpid())
    recall_child.name_holder(SESSION, record["recall"], -1)
    assert records.read(SESSION, record["recall"])["holder"] is None


# -- the route (only the subagent route's sessions and records) ------------------------

def test_the_guards_run_only_on_the_subagent_route():
    on = {recall_start.SUBCONSCIOUS_ENV: "automatic"}
    assert recall_guard.guarded(on)
    assert recall_guard.guarded({**on, recall_start.SUBCONSCIOUS_ROUTE_ENV: "subagent"})
    assert not recall_guard.guarded({**on, recall_start.SUBCONSCIOUS_ROUTE_ENV: "slot"})
    assert not recall_guard.guarded({recall_start.SUBCONSCIOUS_ROUTE_ENV: "subagent"})


def test_a_record_of_another_route_never_awaits_a_subagent_fork():
    a_record(pid=os.getpid(), route="slot")
    assert found() is None
    assert recall_guard.judge_call(call("Write")) is None


# -- a named live holder waits at most its bound ---------------------------------------

def test_a_live_holder_waits_at_most_its_bound_from_the_recall_start():
    wait = recall_guard.holder_wait_s({"settings": recall_start.default_settings()})
    assert wait == 60 + 30 + records.STARTING_GRACE_S
    record = a_record(pid=os.getpid(), rounds=1, at=time.time() - wait + 5, at_boot=None)
    assert found()["recall"] == record["recall"]
    with recalls.session_lock(SESSION):
        records.update(SESSION, record["recall"], at=time.time() - wait - 1)
    assert found() is None
    assert recall_guard.waiting(SESSION, records.read(SESSION, record["recall"])) == (
        False, recall_guard.PAST_WAIT)


def test_the_bound_follows_the_recall_limit():
    assert recall_guard.holder_wait_s({"settings": {"limit_s": 10}}) == 10 + 30 + 30


def test_a_gone_holder_is_no_fork_at_once():
    record = a_record(pid=os.getpid(), rounds=1)
    with recalls.session_lock(SESSION):
        records.update(SESSION, record["recall"],
                       holder={"pid": os.getpid(), "started": 1.0})
    assert found() is None
    assert recall_guard.waiting(SESSION, records.read(SESSION, record["recall"]))[1] == (
        recall_guard.HOLDER_GONE)


def test_a_late_fork_of_a_living_child_with_no_identity_is_still_judged():
    # the child found no identity of its own, so its line names none; the starter
    # named it on the record, and its fork starts past the starting window
    record = a_record(pid=os.getpid(), rounds=1,
                      at=time.time() - records.STARTING_GRACE_S - 15, at_boot=None)
    with recalls.session_lock(SESSION):
        recalls.put_line(SESSION, {"recall": record["recall"], "harness": "claude",
                                   "state": "open", "child": None})
    assert found()["recall"] == record["recall"]
    assert recall_guard.judge_call(call("Write")) == Deny(recall_guard.READ_ONLY)
    assert recall_guard.judge_call(call("Read", file_path="/x")) is None
    assert records.read(SESSION, record["recall"])["tools"] == 1


def test_the_doctor_lists_a_record_past_its_holder_wait(monkeypatch):
    from moxi_memory import doctor

    monkeypatch.setattr(doctor, "live_subconscious", lambda: [])
    record = a_record(pid=os.getpid(), at=time.time() - 300, at_boot=None)
    row = {r.name: r for r in doctor.rows()}["subconscious"]
    assert row.state == "warn"
    assert (f"1 recall whose holder still runs past its wait for the fork's id, so the "
            f"recall guards take it as no fork: {record['recall']} (starting, 5m old)"
            ) in row.detail


# -- order by write sequence -----------------------------------------------------------

def test_the_newest_waiting_record_is_the_last_written_whatever_its_at():
    first = a_record(pid=os.getpid(), rounds=1)
    second = a_record(pid=os.getpid(), state="starting", rounds=1)
    assert second["seq"] == first["seq"] + 1
    # the first record's `at` reads later, as after a clock step back between them
    with recalls.session_lock(SESSION):
        records.update(SESSION, first["recall"], state="starting",
                       at=second["at"] + 0.5)
    assert [r["recall"] for r in records.session_records(SESSION)] == [
        first["recall"], second["recall"]]
    assert found()["recall"] == second["recall"]


# -- the clock -------------------------------------------------------------------------

def test_an_at_later_than_now_is_clamped_by_every_write():
    now = time.time()
    record = a_record(pid=None)
    with recalls.session_lock(SESSION):
        written = records.update(SESSION, record["recall"], at=now + 3600, now=now)
    assert written["at"] == now


def test_the_judge_clamps_an_at_later_than_now():
    record = a_record(pid=os.getpid(), state="running", fork="agent-9")
    path = records.record_path(SESSION, record["recall"])
    import json

    data = json.loads(path.read_text())
    data["at"] = time.time() + 3600
    path.write_text(json.dumps(data))
    assert recall_guard.judge_call(call("Read", file_path="/x")) is None
    assert records.read(SESSION, record["recall"])["at"] <= time.time()


def test_the_day_sweep_clamps_an_at_later_than_now():
    import json

    record = a_record(pid=None)
    path = records.record_path(SESSION, record["recall"])
    data = json.loads(path.read_text())
    now = time.time()
    data["at"] = now + 3600
    path.write_text(json.dumps(data))
    assert records.sweep_old(sessions=[SESSION], now=now) == []
    assert records.read(SESSION, record["recall"])["at"] == now


def test_a_reader_without_the_lock_reads_a_future_at_as_now():
    assert recall_holder.age({"at": time.time() + 3600}, now=time.time()) == 0.0


@pytest.mark.skipif(not LINUX or recall_holder.boot_now() is None,
                    reason="no clock counting from boot here")
def test_the_age_reads_the_boot_clock_where_the_record_has_it():
    boot = recall_holder.boot_now()
    record = {"at": time.time() - 9999, "at_boot": dict(boot, s=boot["s"] - 5)}
    # a wall clock stepped by hours does not move it
    assert recall_holder.age(record) == pytest.approx(5, abs=2)
    other = {"at": time.time() - 9999, "at_boot": {"boot": "another", "s": 0.0}}
    assert recall_holder.age(other) == pytest.approx(9999, abs=2)


@pytest.mark.skipif(not LINUX, reason="no start counted in ticks since boot here")
def test_a_live_holder_read_after_a_clock_step_is_still_alive():
    me = recall_holder.me()
    assert "ticks" in me and "boot" in me
    stepped = dict(me, started=me["started"] - 3600)
    assert recall_holder.alive(stepped)
    assert not recall_holder.alive(dict(stepped, ticks=me["ticks"] + 1))
    assert not recall_holder.alive(dict(stepped, boot="another boot"))


def test_without_ticks_alive_is_the_core_test():
    me = recall_holder.me()
    plain = {"pid": me["pid"], "started": me["started"]}
    assert recall_holder.alive(plain)
    assert not recall_holder.alive(dict(plain, started=me["started"] - 3600))
    assert not recall_holder.alive({"pid": "x"})
