"""The verdict: every case of the rule, on the state each backend gives services.

The states here are shaped as the core's status read hands them back for systemd,
launchd and Task Scheduler (a timer's state is its schedule; its last result is the
gate's run line, or the supervisor's own under "supervisor"), so each backend's output
is judged as it comes."""

from __future__ import annotations

import sys

import pytest

from moxi.units import UnitState, Wrapped
from moxi_services import rows, store, verdict
from moxi_services.rows import ModuleRow
from moxi_services.verdict import judge

from services_kit import add, adopted, written

BACKENDS = ("systemd", "launchd", "schtasks")
BOOTED = 1_700_000_000.0  # 2023-11-14T22:13:20Z


def st(kind="daemon", state="active", *, enabled=True, backend="systemd", last=None,
       detail="", wrapped=None, module="services", name="web", log="the log"):
    return UnitState(module, name, kind, backend, state, last=last, detail=detail,
                     enabled=enabled, wrapped=wrapped, log=log)


def user(**fields):
    """A row in the store; `removing` and `stopped` are set after the insert."""
    form = fields.pop("form", "written")
    removing = fields.pop("removing", False)
    stopped = fields.pop("stopped", None)
    base = written(**fields) if form == "written" else adopted(**fields)
    row = add(**base)
    if removing:
        row = store.write(lambda c: store.set_removing(c, row.name, by="test"))
    if stopped:
        row = store.write(lambda c: store.set_stopped(c, row.name, stopped, by="test"))
    return row


def module_row(kind="daemon", switch=None, name="sync"):
    return ModuleRow(module="notes", name=name, kind=kind, switch=switch, doc="",
                     platforms=None)


# --- the cases before the state ---------------------------------------------------------

def test_a_row_of_another_system_is_off(monkeypatch):
    row = user(platform="darwin" if sys.platform != "darwin" else "linux")
    found = judge(row, None, True)
    assert (found.state, found.reason) == ("off", f"added on {row.platform}")


def test_no_supervisor_is_off():
    row = user()
    assert judge(row, None, True, supervisor=False).reason == "no supervisor"
    nobackend = st(state="nobackend", backend=None, enabled=None)
    assert judge(row, nobackend, True).state == "off"


def test_being_removed_is_warn_with_remove_as_the_fix():
    row = user(removing=True)
    found = judge(row, st(), True)
    assert (found.state, found.reason, found.fix) == (
        "warn", "remove not finished", "moxi services remove web")


def test_a_module_unit_switched_off_is_off():
    found = judge(module_row(switch="sync.enabled"), st(state="inactive"), False)
    assert found.state == "off" and "sync.enabled" in found.reason


def test_an_unreadable_switch_is_unknown_shown_as_warn():
    found = judge(module_row(switch="sync.enabled"), st(), None)
    assert (found.state, found.unknown) == ("warn", True)
    assert found.reason.startswith("unknown: its switch sync.enabled")


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_failed_supervisor_read_is_unknown_on_every_backend(backend):
    row = user()
    found = judge(row, st(state="unknown", backend=backend, enabled=None,
                          detail="the read failed"), True)
    assert (found.state, found.unknown, found.reason) == (
        "warn", True, "unknown: the read failed")


def test_a_row_the_read_did_not_cover_is_unknown_with_why():
    found = judge(user(), None, True, unread="the supervisor did not answer in 3 s")
    assert found.reason == "unknown: the supervisor did not answer in 3 s"
    assert found.state == "warn"


# --- missing ----------------------------------------------------------------------------

def test_missing_written_on_fails_and_off_warns():
    on = judge(user(enabled=True), st(state="missing", enabled=None), True)
    assert (on.state, on.fix) == ("fail", "moxi services enable web")
    off = judge(user(name="api"), st(state="missing", enabled=None), False)
    assert (off.state, off.fix) == ("warn", "moxi services disable api")


def test_missing_adopted_is_fixed_by_remove():
    row = user(form="adopted", kind="daemon", unit="site.service", name="site",
               enabled=True)
    found = judge(row, st(state="missing", enabled=None, module=None), True)
    assert found.state == "fail" and found.fix == "moxi services remove site"
    assert "site.service is missing" in found.reason


def test_missing_module_unit_is_fixed_by_install():
    found = judge(module_row(), st(state="missing", enabled=None), True)
    assert (found.state, found.fix) == ("fail", "moxi install notes")


# --- a wrapped timer --------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "linux", reason="adopted timers are wrapped on Linux")
def test_a_wrapped_timer_whose_drop_in_is_gone_warns():
    row = user(form="adopted", name="backup")
    found = judge(row, st("timer", wrapped=None, module=None), True)
    assert found.state == "warn" and found.reason.startswith("not wrapped")
    assert found.fix == "moxi services enable backup"
    assert judge(row, st("timer", wrapped=None, module=None), False).fix == \
        "moxi services disable backup"


@pytest.mark.skipif(sys.platform != "linux", reason="adopted timers are wrapped on Linux")
def test_a_wrapped_timer_off_is_off_while_its_gate_holds_the_runs():
    row = user(form="adopted", name="backup")
    wrap = Wrapped("services", "adopted.backup", "backup")
    found = judge(row, st("timer", wrapped=wrap, module=None), False)
    assert found.state == "off"


# --- daemons ----------------------------------------------------------------------------

def test_a_daemon_on_and_running_is_ok():
    assert judge(user(enabled=True), st(), True).state == "ok"


def test_a_daemon_on_that_wont_start_at_login_warns():
    found = judge(user(enabled=True), st(enabled=False), True)
    assert (found.state, found.reason) == ("warn", "won't start at login")


def test_a_daemon_on_whose_login_start_cant_be_read_is_unknown():
    found = judge(user(enabled=True), st(enabled=None), True)
    assert found.state == "warn" and found.unknown


def test_a_daemon_on_not_running_with_no_stop_fails():
    found = judge(user(enabled=True), st(state="inactive"), True)
    assert (found.state, found.reason, found.fix) == (
        "fail", "not running", "moxi services start web")


def test_a_failed_daemon_says_the_cores_result():
    found = judge(user(enabled=True), st(state="failed", detail="failed (exit-code)"), True)
    assert found.state == "fail" and found.reason == "not running (failed: failed (exit-code))"


def test_a_stop_by_hand_warns():
    row = user(enabled=True, stopped="2030-01-01T00:00:00Z")
    found = judge(row, st(state="inactive"), True, booted=BOOTED)
    assert (found.state, found.reason) == ("warn",
                                           "stopped by hand at 2030-01-01T00:00:00Z")


def test_a_stop_from_before_the_last_boot_is_ignored():
    row = user(enabled=True, stopped="2020-01-01T00:00:00Z")
    found = judge(row, st(state="inactive"), True, booted=BOOTED)
    assert (found.state, found.reason) == ("fail", "not running")


def test_a_module_daemon_stopped_by_hand_fails_with_start_as_the_fix():
    # A module daemon's stop is not stored, so nothing tells it from a crash.
    found = judge(module_row(), st(state="inactive", module="notes", name="sync"), True)
    assert (found.state, found.reason, found.fix) == (
        "fail", "not running", "moxi services start notes/sync")


def test_a_module_daemon_idle_under_its_socket_is_ok():
    socket = st("socket", enabled=True, module="notes", name="sync")
    found = judge(module_row(), st(state="inactive", enabled=None, module="notes",
                                   name="sync"), True, socket=socket)
    assert (found.state, found.reason) == ("ok", "idle, socket listening")


@pytest.mark.parametrize("state, enabled, want, reason", [
    ("active", True, "warn", "running, off at next login"),
    ("inactive", True, "warn", "starts at login, flag off"),
    ("inactive", False, "off", "off"),
])
def test_a_daemon_off(state, enabled, want, reason):
    found = judge(user(), st(state=state, enabled=enabled), False)
    assert (found.state, found.reason) == (want, reason)
    if want == "warn":
        assert found.fix == "moxi services disable web"


def test_a_daemon_off_whose_login_start_cant_be_read_is_unknown():
    assert judge(user(), st(state="inactive", enabled=None), False).unknown


# --- health -----------------------------------------------------------------------------

def test_a_failing_health_check_makes_a_running_daemon_fail():
    found = judge(user(enabled=True), st(), True, health=("fail", "connection refused"))
    assert (found.state, found.reason, found.fix) == (
        "fail", "health check failed: connection refused", "the log")


def test_a_passing_health_check_leaves_the_verdict():
    assert judge(user(enabled=True), st(), True, health=("ok", "ok")).state == "ok"


# --- timers, per backend ----------------------------------------------------------------

def gate(result="ran", code=0, at="2026-10-30T09:00:00Z"):
    return {"unit": "services/job", "at": at, "seconds": 1.0, "result": result,
            "exit": code}


SUPERVISOR_FAILED = {
    "systemd": {"supervisor": {"result": "exit-code", "status": "2"}},
    "launchd": {"supervisor": {"status": 2}},
    "schtasks": {"supervisor": {"last_run": "2026-10-30T09:00:00", "last_result": 2}},
}
SUPERVISOR_OK = {
    "systemd": {"supervisor": {"result": "success", "status": "0"}},
    "launchd": {"supervisor": {"status": 0}},
    "schtasks": {"supervisor": {"last_run": "2026-10-30T09:00:00", "last_result": 0}},
}


def timer_row(**fields):
    return user(**{"name": "job", "kind": "timer", "every": "5m", **fields})


@pytest.mark.parametrize("backend", BACKENDS)
def test_an_idle_timer_between_runs_is_ok_on_every_backend(backend):
    # Armed and idle: launchd and Task Scheduler have no process either, and that is fine.
    found = judge(timer_row(enabled=True), st("timer", backend=backend, last=gate()), True)
    assert (found.state, found.reason) == ("ok", "armed")
    found = judge(timer_row(name="job2", enabled=True),
                  st("timer", backend=backend, last=SUPERVISOR_OK[backend]), True)
    assert found.state == "ok"


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_non_zero_last_result_warns_on_every_backend(backend):
    row = timer_row(enabled=True)
    found = judge(row, st("timer", backend=backend, last=gate(code=2)), True)
    assert found.state == "warn" and found.reason.startswith("last run failed (exit 2")
    assert found.fix == "the log"
    found = judge(row, st("timer", backend=backend, last=SUPERVISOR_FAILED[backend]), True)
    assert found.state == "warn" and found.reason.startswith("last run failed")


def test_task_schedulers_own_status_codes_are_not_failures():
    row = timer_row(enabled=True)
    for code in (0x41303, 0x41301, 0x41300):
        last = {"supervisor": {"last_run": None, "last_result": code}}
        assert judge(row, st("timer", backend="schtasks", last=last), True).state == "ok"


def test_a_gate_skip_off_is_not_a_failure():
    found = judge(timer_row(enabled=True), st("timer", last=gate("off", 0)), True)
    assert found.state == "ok"


@pytest.mark.parametrize("result, code", [("unreadable", 3), ("not-started", 127)])
def test_a_gate_that_could_not_run_the_command_warns(result, code):
    found = judge(timer_row(enabled=True), st("timer", last=gate(result, code)), True)
    assert found.state == "warn"


def test_a_timer_on_with_no_run_yet_is_ok():
    found = judge(timer_row(enabled=True), st("timer", last=None), True)
    assert (found.state, found.reason) == ("ok", "armed, no run yet")


def test_a_timer_on_whose_schedule_is_not_armed_fails():
    found = judge(timer_row(enabled=True), st("timer", state="inactive", enabled=False),
                  True)
    assert (found.state, found.reason, found.fix) == (
        "fail", "schedule not armed", "moxi services enable job")
    found = judge(module_row("timer", "snapshot.enabled", "snapshot"),
                  st("timer", state="inactive", enabled=False), True)
    assert found.fix == "moxi install notes"


def test_a_timer_is_never_judged_by_process_state():
    # Idle (no process) and armed is ok whatever the state word says.
    found = judge(timer_row(enabled=True), st("timer", state="inactive", enabled=True),
                  True)
    assert found.state == "ok"


def test_a_timer_whose_armed_state_cant_be_read_is_unknown():
    assert judge(timer_row(enabled=True), st("timer", enabled=None), True).unknown


def test_a_timer_off():
    row = timer_row()
    assert judge(row, st("timer", enabled=False), False).state == "off"
    armed = judge(row, st("timer", enabled=True), False)
    assert (armed.state, armed.reason) == ("warn", "schedule armed, flag off")
    module = judge(module_row("timer", "snapshot.enabled", "snapshot"),
                   st("timer", enabled=True), False)
    assert module.state == "off"


# --- every row at once ------------------------------------------------------------------

def test_views_judge_every_row_from_one_read(fake):
    from fake_units import Unit

    user(enabled=True)
    timer_row(enabled=True)
    fake.written["web"] = Unit("daemon", True, active=True)
    fake.written["job"] = Unit("timer", True, last=gate(code=1))
    found = verdict.views(store.read_rows())
    assert {v.row.id: v.verdict.state for v in found} == {
        "services/job": "warn", "services/web": "ok"}
    assert [c[0] for c in fake.calls].count("status") == 1


def test_views_past_the_status_limit_are_unknown_and_run_no_health(fake, monkeypatch):
    import threading

    gate_ = threading.Event()
    user(enabled=True, health=["/bin/false"])
    monkeypatch.setattr(fake, "status", lambda *a, **k: gate_.wait(5) and [])
    ran = []
    monkeypatch.setattr(verdict, "healths", lambda *a, **k: ran.append(1) or {})
    found = verdict.views(store.read_rows(), health=True, status_seconds=0.2)
    gate_.set()
    assert found[0].verdict.reason == "unknown: the supervisor did not answer in 0.2 s"
    assert ran == []


def _running(n: int) -> list:
    return [(user(name=f"svc{i}", enabled=True, health=["/bin/true"]), st(name=f"svc{i}"))
            for i in range(n)]


def _counting(monkeypatch, seconds: float) -> list[int]:
    """Puts a health check in place that takes `seconds`; returns [running, most]."""
    import threading
    import time

    counts = [0, 0]
    lock = threading.Lock()

    def slow(row, st_):
        with lock:
            counts[0] += 1
            counts[1] = max(counts[1], counts[0])
        time.sleep(seconds)
        with lock:
            counts[0] -= 1
        return "ok", "fine"

    monkeypatch.setattr(verdict.state, "health", slow)
    return counts


def test_health_checks_start_all_at_once_without_a_bound(monkeypatch):
    counts = _counting(monkeypatch, 0.1)
    found = verdict.healths(_running(12), 2.0)
    assert counts[1] == 12
    assert len(found) == 12 and set(found.values()) == {("ok", "fine")}


def test_health_checks_at_once_run_no_more_than_that_many_together(monkeypatch):
    counts = _counting(monkeypatch, 0.05)
    found = verdict.healths(_running(12), 2.0, at_once=3)
    assert counts[1] == 3
    assert len(found) == 12 and set(found.values()) == {("ok", "fine")}


def test_a_check_still_waiting_its_turn_at_the_limit_fails_and_never_runs(monkeypatch):
    import threading
    import time

    gate_ = threading.Event()
    ran = []

    def stuck(row, st_):
        ran.append(row.id)
        gate_.wait(5)
        return "ok", "late"

    monkeypatch.setattr(verdict.state, "health", stuck)
    found = verdict.healths(_running(3), 0.1, at_once=1)
    gate_.set()
    assert len(ran) == 1, ran
    assert found[ran[0]] == ("fail", "did not finish in 5 s")
    rest = {"services/svc0", "services/svc1", "services/svc2"} - set(ran)
    assert len(rest) == 2
    for ident in rest:
        assert found[ident][1].startswith("did not get its turn"), found[ident]
    time.sleep(0.1)
    assert len(ran) == 1, ran


def test_worst():
    assert verdict.worst(["ok", "off", "warn"]) == "warn"
    assert verdict.worst(["fail", "ok"]) == "fail"
    assert verdict.worst([]) == "ok"


def test_rows_module_is_services():
    assert rows.MODULE == "services"
