"""The acts per form against a fake `moxi.units`: what each calls, what it writes, the
core lock around each write and the act that goes with it, the re-read after it, and
every act ending in every state."""

from __future__ import annotations

import os
import sys
import threading
from types import SimpleNamespace

import pytest

import moxi.config
import moxi.files
import moxi.paths
from moxi_services import acts, rows, state, store
from moxi_services.errors import Busy

from fake_units import Unit, plist
from services_kit import add, adopted, written

PY = sys.executable
LINUX = pytest.mark.skipif(sys.platform != "linux", reason="adopted timers are wrapped on Linux")


def key_on(name: str) -> bool:
    return state.key_on(name)


def put_key(name: str, on: bool) -> None:
    moxi.config.set("services", state.key(name), on, by="test")


def other_system(monkeypatch, platform: str = "darwin") -> None:
    """Make every row of this system read as added on another one."""
    monkeypatch.setattr(rows, "sys", SimpleNamespace(platform=platform))


# --- add, written -----------------------------------------------------------------------

def test_add_written_off_writes_the_row_then_its_unit_under_the_lock(fake):
    out = acts.add_written("web", [PY, "-m", "http.server"])
    assert out.code == 0, out
    row = store.get("web")
    assert (row.form, row.kind, row.enabled, row.argv[0]) == ("written", "daemon", False, PY)
    assert fake.written["web"].enabled is False and not fake.written["web"].active
    assert [c[0] for c in fake.calls[:2]] == ["plan", "apply"]
    assert all(held for _, held in fake.locked)
    assert "services/web added, off" in out.lines


def test_add_written_with_enable_starts_it(fake):
    out = acts.add_written("web", [PY], enable=True)
    assert out.code == 0, out
    assert fake.written["web"].enabled and fake.written["web"].active
    assert store.get("web").enabled is True


def test_add_written_resolves_argv0_on_path(fake, monkeypatch):
    monkeypatch.setenv("PATH", os.path.dirname(PY))
    out = acts.add_written("web", [os.path.basename(PY), "-V"])
    assert out.code == 0, out
    assert store.get("web").argv == (os.path.abspath(PY), "-V")


def test_add_written_refuses_argv0_not_found(fake, monkeypatch, store_path):
    monkeypatch.setenv("PATH", "")
    out = acts.add_written("web", ["no-such-program-here"])
    assert out.code == 3 and "not found on PATH" in out.lines[0]
    assert not store_path.exists() and fake.calls == []


def test_add_written_from_the_page_needs_an_absolute_argv0(fake):
    out = acts.add_written("web", ["python3"], resolve_argv=False)
    assert out.code == 2


def test_add_written_timer_takes_its_schedule(fake):
    out = acts.add_written("nightly", [PY], timer=True, at=["03:00"], days=["mon"],
                           enable=True)
    assert out.code == 0, out
    row = store.get("nightly")
    assert (row.kind, row.at, row.days) == ("timer", ("03:00",), ("mon",))
    assert fake.written["nightly"].enabled


def test_add_written_timer_without_a_schedule_is_usage(fake):
    assert acts.add_written("nightly", [PY], timer=True).code == 2


def test_add_written_refuses_a_taken_name(fake):
    add(**written("web", project="studio"))
    out = acts.add_written("web", [PY])
    assert out.code == 3 and "web is taken (project studio)" in out.lines[-1]


@pytest.mark.parametrize("plan_as, code", [
    (("refuse", "editable", "an editable install"), 3),
    (("refuse", "command", "no such script"), 3),
    (("skip", "declaration", "UNITS_FROM failed"), 1),
])
def test_add_written_deletes_its_row_when_the_core_will_not_write_it(fake, plan_as, code):
    fake.plan_as = plan_as
    out = acts.add_written("web", [PY])
    assert out.code == code
    assert store.get("web") is None
    assert any(plan_as[2] in line for line in out.lines)
    assert "services/web not added" in out.lines


def test_add_written_deletes_its_row_when_plan_raises(fake):
    fake.plan_raises = RuntimeError("the declarations timed out")
    out = acts.add_written("web", [PY])
    assert out.code == 1 and store.get("web") is None
    assert "the declarations timed out" in out.lines[0]


def test_add_written_keeps_its_row_when_apply_fails(fake):
    fake.apply_fails = True
    out = acts.add_written("web", [PY])
    assert out.code == 1 and store.get("web") is not None
    assert out.fix == "moxi services remove web"


def test_add_written_refuses_with_no_supervisor(fake, store_path):
    fake.supervisor = None
    out = acts.add_written("web", [PY])
    assert out.code == 3 and not store_path.exists()


def test_add_written_refuses_an_unknown_project(fake):
    out = acts.add_written("web", [PY], project="nowhere")
    assert out.code == 3 and store.get("web") is None


def test_add_waits_for_the_core_lock_then_writes_nothing(fake, monkeypatch, store_path):
    monkeypatch.setattr(acts, "LOCK_TIMEOUT", 0.2)
    held, done = threading.Event(), threading.Event()

    def hold():
        with moxi.files.lock(moxi.paths.core_lock(), nested=True):
            held.set()
            done.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert held.wait(5)
        out = acts.add_written("web", [PY])
    finally:
        done.set()
        holder.join()
    assert out.code == 75 and "core lock" in out.lines[0]
    assert not store_path.exists() and fake.calls == []


def test_a_killed_add_leaves_a_row_remove_deletes(fake):
    add(**written("web"))  # the row is in; the process died before its unit was written
    out = acts.remove("web")
    assert out.code == 0, out
    assert store.get("web") is None


# --- enable, disable, written -----------------------------------------------------------

def test_enable_written_sets_the_flag_then_applies_under_the_lock(fake):
    acts.add_written("web", [PY])
    fake.calls.clear()
    fake.locked.clear()
    out = acts.enable("web")
    assert out.code == 0, out
    assert store.get("web").enabled is True
    assert fake.written["web"].active and fake.written["web"].enabled
    assert fake.calls[0] == ("plan", "services", ("web",))
    assert all(held for _, held in fake.locked)


def test_disable_written_turns_it_off(fake):
    acts.add_written("web", [PY], enable=True)
    out = acts.disable("services/web")
    assert out.code == 0, out
    assert store.get("web").enabled is False
    assert not fake.written["web"].active and not fake.written["web"].enabled


def test_enable_written_writes_a_missing_unit_again(fake):
    add(**written("web"))
    out = acts.enable("web")
    assert out.code == 0, out
    assert fake.written["web"].active


def test_enable_written_fails_when_the_reread_does_not_match(fake):
    acts.add_written("web", [PY])
    fake.written["web"].detail = "exit 1"

    fake.apply = lambda changes, **kw: _applied_without_effect(changes)
    out = acts.enable("web")
    assert out.code == 1
    assert "services/web reads disabled" in out.lines[-1]
    assert out.fix == "its log: journalctl --user -u web"
    assert store.get("web").enabled is True  # the intent stays recorded


def _applied_without_effect(changes):
    from moxi.units import Applied

    return [Applied(c, True, ()) for c in changes]


def test_enable_reports_an_unknown_read_without_failing(fake):
    acts.add_written("web", [PY])
    fake.unknown = True
    out = acts.enable("web")
    assert out.code == 0
    assert any("unknown after the act: systemctl show failed" in line for line in out.lines)


def test_enable_written_refused_by_the_core_exits_3(fake):
    acts.add_written("web", [PY])
    fake.plan_as = ("refuse", "editable", "an editable install")
    out = acts.enable("web")
    assert out.code == 3 and any("an editable install" in line for line in out.lines)


# --- start, stop, restart ---------------------------------------------------------------

def test_stop_records_a_user_daemons_stop_and_start_clears_it(fake):
    acts.add_written("web", [PY], enable=True)
    fake.locked.clear()
    out = acts.stop("web")
    assert out.code == 0, out
    assert store.get("web").stopped is not None
    assert not fake.written["web"].active
    assert fake.locked == [("stop", True)]
    out = acts.start("web")
    assert out.code == 0, out
    assert store.get("web").stopped is None and fake.written["web"].active


def test_restart_clears_a_recorded_stop(fake):
    acts.add_written("web", [PY], enable=True)
    acts.stop("web")
    assert acts.restart("web").code == 0
    assert store.get("web").stopped is None


def test_a_daemon_start_that_does_not_read_active_fails(fake):
    acts.add_written("web", [PY])
    fake.drift = True
    out = acts.start("web")
    assert out.code == 1 and "services/web reads inactive, not running" in out.lines[-1]


def test_a_failed_act_exits_1_with_the_supervisors_line(fake):
    acts.add_written("web", [PY])
    fake.fail.add(("start", "web"))
    out = acts.start("web")
    assert out.code == 1 and out.lines == ("web: Job for web failed",)


def test_a_timers_run_now_is_judged_by_the_act_alone(fake):
    acts.add_written("nightly", [PY], timer=True, every="1h", enable=True)
    fake.drift = True
    calls = len([c for c in fake.calls if c[0] == "status"])
    out = acts.start("nightly")
    assert out.code == 0, out
    assert len([c for c in fake.calls if c[0] == "status"]) == calls
    assert store.get("nightly").stopped is None


def test_stop_of_a_timer_records_nothing(fake):
    acts.add_written("nightly", [PY], timer=True, every="1h")
    assert acts.stop("nightly").code == 0
    assert store.get("nightly").stopped is None


# --- rows that allow only remove --------------------------------------------------------

@pytest.mark.parametrize("act", ["start", "stop", "restart", "enable", "disable"])
def test_a_row_being_removed_allows_only_remove(fake, act):
    add(**written("web"))
    store.write(lambda c: store.set_removing(c, "web", by="test"))
    out = getattr(acts, act)("web")
    assert out.code == 3 and "remove not finished" in out.lines[0]
    assert out.fix == "moxi services remove web"
    assert fake.acts() == []


@pytest.mark.parametrize("act", ["start", "enable"])
def test_a_row_of_another_system_allows_only_remove(fake, monkeypatch, act):
    add(**written("web"))
    other_system(monkeypatch)
    out = getattr(acts, act)("web")
    assert out.code == 3 and f"added on {sys.platform}" in out.lines[0]


@pytest.mark.parametrize("act", ["start", "stop", "enable"])
def test_no_supervisor_allows_only_remove(fake, act):
    add(**written("web"))
    fake.supervisor = None
    out = getattr(acts, act)("web")
    assert out.code == 3 and "no supervisor" in out.lines[0]


def test_an_unknown_id_is_refused(fake):
    for ident in ("ghost", "services/ghost", "nomodule/ghost", "Not An Id"):
        out = acts.start(ident)
        assert out.code == 3, ident


# --- remove, written --------------------------------------------------------------------

def test_remove_written_takes_the_unit_then_the_row(fake):
    acts.add_written("web", [PY], enable=True)
    fake.locked.clear()
    out = acts.remove("web")
    assert out.code == 0, out
    assert store.get("web") is None and "web" not in fake.written
    assert fake.locked and all(held for _, held in fake.locked)


def test_remove_keeps_the_row_marked_while_the_unit_stays_then_retries(fake):
    acts.add_written("web", [PY], enable=True)
    fake.apply_fails = True
    out = acts.remove("web")
    assert out.code == 1 and out.fix == "moxi services remove web"
    row = store.get("web")
    assert row.removing
    assert acts.start("web").code == 3
    fake.apply_fails = False
    assert acts.remove("web").code == 0
    assert store.get("web") is None


def test_remove_finishes_with_a_unit_deleted_by_hand(fake):
    acts.add_written("web", [PY])
    del fake.written["web"]
    assert acts.remove("web").code == 0 and store.get("web") is None


@LINUX
@pytest.mark.parametrize("on", [False, True])
def test_remove_finishes_with_a_wrapped_timer_deleted_by_hand(fake, on):
    fake.foreign["backup.timer"] = Unit("timer", True)
    acts.add_adopted("backup", "backup.timer", enable=on)
    del fake.foreign["backup.timer"]
    fake.calls.clear()
    out = acts.remove("backup")
    assert out.code == 0, out
    assert store.get("backup") is None and fake.wrapped == {}
    assert [c[0] for c in fake.calls if c[0] in ("disable", "unwrap")] == ["unwrap"]
    assert moxi.config.load("services", state.schema())["adopted"] == {}
    if not on:
        assert "backup.timer is gone from the supervisor, so there is no schedule to " \
               "disable" in out.lines


def test_remove_of_a_row_of_another_system_deletes_it(fake, monkeypatch):
    add(**written("web"))
    other_system(monkeypatch)
    out = acts.remove("web")
    assert out.code == 0 and store.get("web") is None
    assert "added on" in out.lines[0] and fake.calls == []


def test_remove_with_no_supervisor_deletes_the_row(fake):
    add(**written("web"))
    fake.supervisor = None
    out = acts.remove("web")
    assert out.code == 0 and store.get("web") is None
    assert "no supervisor" in out.lines[0]


def test_remove_on_an_editable_install_deletes_the_row(fake):
    add(**written("web"))
    fake.written["web"] = Unit("daemon")  # from an earlier release install
    fake.plan_as = ("refuse", "editable", "an editable install")
    out = acts.remove("web")
    assert out.code == 0 and store.get("web") is None
    assert "editable install" in out.lines[0]


def test_remove_keeps_the_row_when_plan_raises(fake):
    acts.add_written("web", [PY])
    fake.plan_raises = RuntimeError("boom")
    out = acts.remove("web")
    assert out.code == 1 and store.get("web").removing


# --- add, adopted -----------------------------------------------------------------------

def test_adopt_without_enable_or_disable_says_what_the_supervisor_reads(fake, store_path):
    fake.foreign["backup.timer"] = Unit("timer", True,
                                        last={"supervisor": {"result": "success",
                                                             "status": "0"}})
    out = acts.add_adopted("backup", "backup.timer", enable=None)
    assert out.code == 2
    assert out.lines == ("backup.timer: schedule armed, last run ok; adopt it with "
                         "--enable to keep it on, or --disable to turn it off",)
    assert not store_path.exists()


@pytest.mark.parametrize("supervisor, ref, last, said", [
    ("schtasks", "\\Backups\\nightly",
     {"supervisor": {"last_result": 1, "last_run": "2026-10-30T02:00:00"}},
     "schedule armed, last run failed, result 0x00000001"),
    ("schtasks", "\\Backups\\nightly",
     {"supervisor": {"last_result": 0, "last_run": "2026-10-30T02:00:00"}},
     "schedule armed, last run ok"),
    ("systemd", "backup.timer", {"supervisor": {"result": "exit-code", "status": "2"}},
     "schedule armed, last run failed, exit 2"),
    ("systemd", "backup.timer", None, "schedule armed, no run known"),
])
def test_adopt_says_the_last_run_as_the_verdict_reads_it(fake, supervisor, ref, last, said):
    fake.supervisor = supervisor
    fake.foreign[ref] = Unit("timer", True, last=last)
    out = acts.add_adopted("backup", ref, enable=None)
    assert out.code == 2 and out.lines[0].startswith(f"{ref}: {said}; adopt it with")


def test_adopt_a_daemon_makes_the_unit_match_the_flag(fake):
    fake.foreign["web.service"] = Unit("daemon", False)
    out = acts.add_adopted("web", "web.service", enable=True)
    assert out.code == 0, out
    row = store.get("web")
    assert (row.form, row.kind, row.unit, row.enabled) == ("adopted", "daemon",
                                                           "web.service", True)
    assert fake.acts() == [("enable", "services", ("web.service",), True)]
    assert fake.foreign["web.service"].active
    assert fake.locked == [("enable", True)]


def test_adopt_with_disable_turns_it_off(fake):
    fake.foreign["web.service"] = Unit("daemon", True, active=True)
    out = acts.add_adopted("web", "web.service", enable=False)
    assert out.code == 0, out
    assert not fake.foreign["web.service"].active and store.get("web").enabled is False


def test_adopt_refuses_a_missing_unit(fake, store_path):
    out = acts.add_adopted("web", "web.service", enable=True)
    assert out.code == 3 and not store_path.exists()


def test_adopt_refuses_a_unit_whose_on_off_cant_be_read(fake):
    fake.foreign["web.service"] = Unit("daemon", None, detail="static")
    out = acts.add_adopted("web", "web.service", enable=True)
    assert out.code == 3 and "can't be read" in out.lines[0]


def test_adopt_refuses_moxis_own_namespace(fake):
    out = acts.add_adopted("web", "moxi-notes-sync.service", enable=True)
    assert out.code == 3 and "moxi's own unit namespace" in out.lines[0]


def test_adopt_refuses_a_unit_another_row_holds(fake):
    fake.foreign["web.service"] = Unit("daemon", True, active=True)
    add(**adopted("site", kind="daemon", unit="web.service"))
    out = acts.add_adopted("web", "web.service", enable=True)
    assert out.code == 3 and "already adopted as services/site" in out.lines[0]


def test_adopt_checks_again_under_the_lock_for_a_row_holding_its_unit(fake, monkeypatch):
    fake.foreign["web.service"] = Unit("daemon", True, active=True)
    checked = acts._adoptable

    def race(*args):
        checked(*args)
        # Another add of the same unit writes its row between the first check and the
        # lock.
        add(**adopted("site", kind="daemon", unit="web.service"))

    monkeypatch.setattr(acts, "_adoptable", race)
    out = acts.add_adopted("web", "web.service", enable=True)
    assert out.code == 3 and "already adopted as services/site" in out.lines[0]
    assert store.get("web") is None and fake.acts() == []


def test_adopt_refuses_a_timer_another_module_wrapped(fake):
    from moxi.units import Wrapped

    fake.foreign["backup.timer"] = Unit("timer", True)
    fake.wrapped["backup.timer"] = Wrapped("other", "backups.on", "backup")
    out = acts.add_adopted("backup", "backup.timer", enable=True)
    assert out.code == 3 and "already wrapped by other" in out.lines[0]


def test_adopt_on_launchd_needs_the_agents_plist(fake, tmp_path, monkeypatch):
    from pathlib import Path

    fake.supervisor = "launchd"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    fake.foreign["com.example.web"] = Unit("daemon", True, active=True)
    out = acts.add_adopted("web", "com.example.web", enable=True)
    assert out.code == 3 and "LaunchAgents" in out.lines[0]
    plist(tmp_path, "com.example.web")
    out = acts.add_adopted("web", "com.example.web", enable=True)
    assert out.code == 0, out


def test_adopt_on_task_scheduler_is_a_timer(fake):
    fake.supervisor = "schtasks"
    fake.foreign["\\Backups\\nightly"] = Unit("timer", True)
    out = acts.add_adopted("nightly", "\\Backups\\nightly", enable=True)
    assert out.code == 0, out
    assert store.get("nightly").kind == "timer"
    assert not any(c[0] == "wrap" for c in fake.calls) and fake.wrapped == {}


def test_adopt_keeps_its_row_when_the_act_fails(fake):
    fake.foreign["web.service"] = Unit("daemon", False)
    fake.fail.add(("enable", "web.service"))
    out = acts.add_adopted("web", "web.service", enable=True)
    assert out.code == 1 and store.get("web") is not None
    assert out.fix == "moxi services enable web"


# --- adopted Linux timers: wrapped ------------------------------------------------------

@LINUX
def test_adopt_a_linux_timer_wraps_it_and_sets_its_key(fake):
    fake.foreign["backup.timer"] = Unit("timer", False)
    out = acts.add_adopted("backup", "backup.timer", enable=True)
    assert out.code == 0, out
    assert ("wrap", "backup.timer", "services", "adopted.backup", "backup") in fake.calls
    assert key_on("backup") is True
    # The core's line on how the key stood before it was set is left out.
    assert not any("runs are off until" in line for line in out.lines)
    assert "backup.timer: drop-in written" in out.lines
    assert "backup.timer: runs on (services adopted.backup = true)" in out.lines
    assert fake.foreign["backup.timer"].enabled  # the schedule is armed
    assert all(held for _, held in fake.locked)


@LINUX
def test_adopt_a_linux_timer_disabled_leaves_its_schedule_armed(fake):
    fake.foreign["backup.timer"] = Unit("timer", True)
    out = acts.add_adopted("backup", "backup.timer", enable=False)
    assert out.code == 0, out
    assert key_on("backup") is False
    assert fake.foreign["backup.timer"].enabled and fake.acts() == []


@LINUX
def test_a_wrap_refusal_deletes_the_row(fake):
    fake.foreign["backup.timer"] = Unit("timer", True)
    fake.wrap_refuses = "backup.service has 2 ExecStart lines; not wrapped"
    out = acts.add_adopted("backup", "backup.timer", enable=True)
    assert out.code == 3 and store.get("backup") is None
    assert "backup.service has 2 ExecStart lines; not wrapped" in out.lines


@LINUX
def test_a_wrap_failure_takes_its_drop_in_away_and_deletes_the_row(fake):
    fake.foreign["backup.timer"] = Unit("timer", True)
    fake.wrap_fails = True
    out = acts.add_adopted("backup", "backup.timer", enable=True)
    assert out.code == 1 and store.get("backup") is None
    assert fake.wrapped == {}


@LINUX
def test_adopt_refuses_a_timer_services_already_wrapped_for_another_row(fake):
    from moxi.units import Wrapped

    fake.foreign["backup.timer"] = Unit("timer", True)
    fake.wrapped["backup.timer"] = Wrapped("services", "adopted.old", "old")
    out = acts.add_adopted("backup", "backup.timer", enable=True)
    assert out.code == 3 and "services/old" in out.lines[0]


# On systemd only a foreign timer's state says it is wrapped, and only a timer's names
# what its Triggers names, so a service's own state shows nothing of the timer that
# triggers it; the fake reads them the same way.

def backup_with_its_service(fake) -> None:
    """A foreign timer and the service its Triggers names, under different names."""
    fake.foreign["backup.timer"] = Unit("timer", True, triggers="backup-run.service")
    fake.foreign["backup-run.service"] = Unit("daemon", False)


@LINUX
def test_adopt_refuses_a_service_another_rows_timer_triggers(fake):
    backup_with_its_service(fake)
    assert acts.add_adopted("backup", "backup.timer", enable=True).code == 0
    assert "backup-run.service" not in fake.wrapped
    before = fake.acts()
    out = acts.add_adopted("run", "backup-run.service", enable=True)
    assert out.code == 3, out
    assert ("backup-run.service is triggered by backup.timer, already adopted as "
            "services/backup") in out.lines[0]
    assert store.get("run") is None and fake.acts() == before
    # Read in the one status call that reads the REF.
    assert ("status", (), ("backup-run.service", "backup.timer")) in fake.calls


@LINUX
def test_adopt_refuses_a_timer_whose_service_another_row_adopted(fake):
    backup_with_its_service(fake)
    assert acts.add_adopted("run", "backup-run.service", enable=True).code == 0
    out = acts.add_adopted("backup", "backup.timer", enable=True)
    assert out.code == 3, out
    assert ("backup.timer triggers backup-run.service, already adopted as services/run"
            in out.lines[0])
    assert store.get("backup") is None
    assert not any(c[0] == "wrap" for c in fake.calls) and fake.wrapped == {}


@LINUX
def test_adopt_refuses_a_timer_naming_no_unit_whose_own_service_another_row_adopted(fake):
    fake.foreign["nightly.timer"] = Unit("timer", True)
    fake.foreign["nightly.service"] = Unit("daemon", False)
    assert acts.add_adopted("job", "nightly.service", enable=False).code == 0
    out = acts.add_adopted("nightly", "nightly.timer", enable=True)
    assert out.code == 3 and "triggers nightly.service" in out.lines[0]


@LINUX
def test_adopt_refuses_a_second_timer_on_a_service_another_rows_timer_triggers(fake):
    backup_with_its_service(fake)
    fake.foreign["extra.timer"] = Unit("timer", True, triggers="backup-run.service")
    assert acts.add_adopted("backup", "backup.timer", enable=True).code == 0
    out = acts.add_adopted("extra", "extra.timer", enable=True)
    assert out.code == 3, out
    assert ("extra.timer triggers backup-run.service, as backup.timer does, already "
            "adopted as services/backup") in out.lines[0]
    assert "extra.timer" not in fake.wrapped


@LINUX
def test_adopt_refuses_a_timer_that_triggers_a_unit_in_moxis_own_namespace(fake):
    fake.foreign["web.timer"] = Unit("timer", True, triggers="moxi-services-web.service")
    out = acts.add_adopted("web", "web.timer", enable=True)
    assert out.code == 3 and "moxi's own namespace" in out.lines[0]
    assert store.get("web") is None and fake.wrapped == {}


@LINUX
def test_adopt_checks_triggers_again_under_the_lock(fake, monkeypatch):
    backup_with_its_service(fake)
    checked = acts._adoptable

    def race(*args):
        checked(*args)
        # Another add of the timer that triggers it writes its row between the first
        # check and the lock.
        add(**adopted("backup"))

    monkeypatch.setattr(acts, "_adoptable", race)
    out = acts.add_adopted("run", "backup-run.service", enable=True)
    assert out.code == 3, out
    assert "triggered by backup.timer, already adopted as services/backup" in out.lines[0]
    assert store.get("run") is None and fake.acts() == []
    # The timer's row was new under the lock, so its Triggers was read there.
    assert ("status", (), ("backup.timer",)) in fake.calls


@LINUX
def test_adopt_checks_a_timers_service_again_under_the_lock(fake, monkeypatch):
    backup_with_its_service(fake)
    checked = acts._adoptable

    def race(*args):
        checked(*args)
        add(**adopted("run", kind="daemon", unit="backup-run.service"))

    monkeypatch.setattr(acts, "_adoptable", race)
    out = acts.add_adopted("backup", "backup.timer", enable=True)
    assert out.code == 3, out
    assert "triggers backup-run.service, already adopted as services/run" in out.lines[0]
    assert store.get("backup") is None and fake.wrapped == {}


def test_adopt_off_systemd_compares_the_ref_alone(fake, tmp_path, monkeypatch):
    from pathlib import Path

    # On launchd a label is its own job: no state carries `triggers`, so names that would
    # pair on systemd are two units here.
    fake.supervisor = "launchd"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for label in ("backup.timer", "backup.service"):
        plist(tmp_path, label)
    fake.foreign["backup.timer"] = Unit("timer", True, triggers="backup.service")
    fake.foreign["backup.service"] = Unit("daemon", True, active=True)
    assert all(s.triggers is None for s in fake.status(
        [], foreign=["backup.timer", "backup.service"]))
    out = acts.add_adopted("backup", "backup.timer", timer=True, enable=True)
    assert out.code == 0, out
    out = acts.add_adopted("run", "backup.service", enable=True)
    assert out.code == 0, out


@LINUX
def test_enable_a_wrapped_timer_wraps_it_again_when_its_drop_in_is_gone(fake):
    fake.foreign["backup.timer"] = Unit("timer", True)
    acts.add_adopted("backup", "backup.timer", enable=False)
    fake.wrapped.clear()
    fake.foreign["backup.timer"].enabled = False
    fake.locked.clear()
    out = acts.enable("backup")
    assert out.code == 0, out
    assert "backup.timer" in fake.wrapped and key_on("backup")
    assert fake.foreign["backup.timer"].enabled
    assert all(held for _, held in fake.locked)


@LINUX
def test_disable_a_wrapped_timer_sets_its_key_off_and_leaves_its_schedule(fake):
    fake.foreign["backup.timer"] = Unit("timer", True)
    acts.add_adopted("backup", "backup.timer", enable=True)
    out = acts.disable("backup")
    assert out.code == 0, out
    assert key_on("backup") is False and fake.foreign["backup.timer"].enabled


@LINUX
def test_a_wrapped_timers_run_now_with_its_key_off_says_so(fake):
    fake.foreign["backup.timer"] = Unit("timer", True)
    acts.add_adopted("backup", "backup.timer", enable=False)
    out = acts.start("backup")
    assert out.code == 0
    assert any("recorded off and nothing ran" in line for line in out.lines)
    assert fake.acts()[-1] == ("start", "services", ("backup.timer",), True)


@LINUX
def test_remove_a_wrapped_timer_with_its_key_off_disables_it_first(fake):
    fake.foreign["backup.timer"] = Unit("timer", True)
    acts.add_adopted("backup", "backup.timer", enable=False)
    fake.calls.clear()
    out = acts.remove("backup")
    assert out.code == 0, out
    names = [c[0] for c in fake.calls if c[0] in ("disable", "unwrap")]
    assert names == ["disable", "unwrap"]
    assert store.get("backup") is None and fake.wrapped == {}
    assert "adopted.backup" not in moxi.config.load("services", state.schema())["adopted"]
    assert any("without the gate it would run" in line for line in out.lines)


@LINUX
def test_remove_a_wrapped_timer_with_its_key_on_leaves_its_schedule(fake):
    fake.foreign["backup.timer"] = Unit("timer", True)
    acts.add_adopted("backup", "backup.timer", enable=True)
    fake.calls.clear()
    assert acts.remove("backup").code == 0
    assert [c[0] for c in fake.calls if c[0] in ("disable", "unwrap")] == ["unwrap"]
    assert moxi.config.load("services", state.schema())["adopted"] == {}


@LINUX
def test_remove_a_wrapped_timer_whose_config_cant_be_read_fails_and_retries(fake):
    fake.foreign["backup.timer"] = Unit("timer", True)
    acts.add_adopted("backup", "backup.timer", enable=False)
    config = moxi.paths.Layout("services").config_file()
    good = config.read_text()
    config.write_text("[adopted\n")
    fake.calls.clear()
    out = acts.remove("backup")
    assert out.code == 1 and out.fix == "moxi services remove backup"
    assert store.get("backup").removing and fake.calls == []
    config.write_text(good)
    assert acts.remove("backup").code == 0 and store.get("backup") is None


@LINUX
def test_remove_keeps_a_wrapped_row_while_its_drop_in_stays(fake):
    fake.foreign["backup.timer"] = Unit("timer", True)
    acts.add_adopted("backup", "backup.timer", enable=True)
    fake.unwrap_fails = True
    out = acts.remove("backup")
    assert out.code == 1 and store.get("backup").removing and key_on("backup")
    fake.unwrap_fails = False
    assert acts.remove("backup").code == 0 and store.get("backup") is None


@LINUX
def test_remove_a_wrapped_row_on_another_system_unsets_its_key(fake, monkeypatch):
    fake.foreign["backup.timer"] = Unit("timer", True)
    acts.add_adopted("backup", "backup.timer", enable=True)
    fake.calls.clear()
    other_system(monkeypatch)
    out = acts.remove("backup")
    assert out.code == 0, out
    assert store.get("backup") is None and not key_on("backup")
    assert fake.calls == []
    assert "drop-in on backup.timer stays on linux" in out.lines[0]


def test_remove_an_adopted_daemon_leaves_its_unit(fake):
    fake.foreign["web.service"] = Unit("daemon", True, active=True)
    acts.add_adopted("web", "web.service", enable=True)
    fake.calls.clear()
    out = acts.remove("web")
    assert out.code == 0 and store.get("web") is None
    assert fake.calls == [] and fake.foreign["web.service"].active


def test_stop_an_adopted_daemon_is_recorded_under_the_lock(fake):
    fake.foreign["web.service"] = Unit("daemon", True, active=True)
    acts.add_adopted("web", "web.service", enable=True)
    fake.locked.clear()
    assert acts.stop("web").code == 0
    assert fake.locked == [("stop", True)]
    assert store.get("web").stopped is not None
    assert fake.acts()[-1] == ("stop", "services", ("web.service",), True)


@pytest.mark.parametrize("act", ["enable", "disable"])
def test_switch_an_adopted_daemon_under_the_lock(fake, act):
    fake.foreign["web.service"] = Unit("daemon", act == "disable",
                                       active=act == "disable")
    acts.add_adopted("web", "web.service", enable=act == "disable")
    fake.locked.clear()
    out = getattr(acts, act)("web")
    assert out.code == 0, out
    assert fake.locked == [(act, True)]
    assert store.get("web").enabled is (act == "enable")


def test_enable_an_adopted_daemon_clears_a_recorded_stop(fake):
    fake.foreign["web.service"] = Unit("daemon", True, active=True)
    acts.add_adopted("web", "web.service", enable=True)
    acts.stop("web")
    out = acts.enable("web")
    assert out.code == 0, out
    assert store.get("web").stopped is None


# --- module rows ------------------------------------------------------------------------

NOTES = '''
SUMMARY = "notes"
DAEMONS = {"sync": {"command": "moxi-notes-sync", "doc": "keep notes in sync"}}
TIMERS = {"snapshot": {"command": "moxi-notes-snapshot", "every": "5m",
                       "switch": "snapshot.enabled", "doc": "commit a snapshot"}}
'''


@pytest.fixture
def notes(site, fake, monkeypatch):
    site.module("notes", NOTES)
    fake.modules[("notes", "sync")] = Unit("daemon", True, active=True)
    fake.modules[("notes", "snapshot")] = Unit("timer", True)
    sets = []

    def config_set(module, key, value, *, by):
        sets.append((module, key, value, by))
        return []

    monkeypatch.setattr(moxi.config, "set", config_set)
    return sets


def test_module_row_acts_go_to_its_module(fake, notes, store_path):
    assert acts.stop("notes/sync").code == 0
    assert acts.start("notes/sync").code == 0
    assert acts.restart("notes/snapshot").code == 0
    assert fake.acts() == [("stop", "notes", ("sync",), False),
                           ("start", "notes", ("sync",), False),
                           ("restart", "notes", ("snapshot",), False)]
    assert not store_path.exists()  # a module daemon's stop is not stored


def test_module_row_enable_flips_its_switch(fake, notes):
    out = acts.enable("notes/snapshot")
    assert out.code == 0, out
    assert notes == [("notes", "snapshot.enabled", True, "moxi services")]
    assert acts.disable("notes/snapshot").code == 0
    assert notes[-1][2] is False
    assert fake.acts() == []


def test_module_row_without_a_switch_refuses_enable(fake, notes):
    out = acts.enable("notes/sync")
    assert out.code == 3 and "declares no switch" in out.lines[0]
    assert "moxi services stop notes/sync" in out.fix and "moxi uninstall notes" in out.fix
    assert notes == []


def test_module_row_switch_refused_by_config_exits_3(fake, notes, monkeypatch):
    def refuse(module, key, value, *, by):
        raise moxi.config.ConfigError("config.toml", key, "the file is malformed",
                                      "fix the file")

    monkeypatch.setattr(moxi.config, "set", refuse)
    out = acts.enable("notes/snapshot")
    assert out.code == 3 and "the file is malformed" in out.lines[0]


def test_module_daemon_switch_failure_exits_1(fake, notes, monkeypatch):
    from moxi.units import Applied, UnitChange

    change = UnitChange("notes", "sync", "daemon", "add", None, (), None, "add")
    monkeypatch.setattr(moxi.config, "set",
                        lambda *a, **k: [Applied(change, False, ("Job failed",))])
    out = acts.enable("notes/snapshot")
    # The core printed the unit's lines as it applied them; they are kept apart, so a
    # verb does not print them twice.
    assert out.code == 1 and "Job failed" in out.said and "Job failed" not in out.lines


def test_module_timer_run_now_with_its_switch_off_says_so(fake, notes, monkeypatch):
    monkeypatch.setattr(state, "switch_on", lambda module, switch: False)
    out = acts.start("notes/snapshot")
    assert out.code == 0
    assert "its switch snapshot.enabled is off" in out.lines[-1]


def test_module_rows_refuse_add_and_remove(fake, notes):
    out = acts.remove("notes/sync")
    assert out.code == 3 and out.fix == "moxi uninstall notes"


def test_busy_store_is_75(fake, monkeypatch):
    def busy(*a, **k):
        raise Busy()

    monkeypatch.setattr(store, "get", busy)
    assert acts.start("web").code == 75
