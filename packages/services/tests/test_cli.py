"""`moxi services`: the verbs over the acts and the reads, their output, `--json` shapes
and every exit code, against a fake `moxi.units`."""

from __future__ import annotations

import json
import shutil
import sys

import pytest

import moxi.cli
import moxi.config
from moxi_services import cli, rows, store
from moxi_services.errors import Busy

from fake_units import Unit
from services_kit import add, adopted, written

PY = sys.executable

ROW_KEYS = {"id", "module", "name", "form", "kind", "project", "launches_session", "flag",
            "switch", "verdict", "state", "enabled", "pid", "since", "last_run", "health"}
VERDICT_KEYS = {"state", "reason", "fix", "unknown"}


@pytest.fixture
def no_modules(monkeypatch):
    """No installed module declares a daemon or timer."""
    monkeypatch.setattr(rows, "module_rows", lambda modules=None: ([], []))


def run(capsys, *argv):
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


# --- list -------------------------------------------------------------------------------

def test_list_groups_by_project_and_marks_session_timers(fake, no_modules, capsys):
    add(**written("web", project="studio", enabled=True))
    add(**written("nightly", kind="timer", every="1h", launches_session=True))
    fake.written["web"] = Unit("daemon", True, active=True)
    fake.written["nightly"] = Unit("timer", False)
    code, out, _ = run(capsys, "list")
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "studio"
    assert lines[1].split()[:4] == ["services/web", "daemon", "ok", "running"]
    assert lines[2] == "no project"
    assert lines[3].split()[:3] == ["services/nightly", "timer", "off"]
    assert lines[3].endswith("*")
    assert lines[-1].startswith("* launches an agent session")
    assert [c[0] for c in fake.calls].count("status") == 1


def test_list_filters_by_project_ignoring_case_and_by_kind(fake, no_modules, capsys):
    add(**written("web", project="Studio"))
    add(**written("api"))
    code, out, _ = run(capsys, "list", "--project", "studio", "--json")
    assert code == 0 and [r["id"] for r in json.loads(out)] == ["services/web"]
    code, out, _ = run(capsys, "list", "--kind", "timer", "--json")
    assert json.loads(out) == []


def test_list_json_shape(fake, no_modules, capsys):
    add(**written("web", enabled=True))
    fake.written["web"] = Unit("daemon", True, active=True)
    code, out, _ = run(capsys, "list", "--json")
    found = json.loads(out)
    assert code == 0 and set(found[0]) == ROW_KEYS
    assert set(found[0]["verdict"]) == VERDICT_KEYS
    assert found[0]["verdict"]["state"] == "ok" and found[0]["flag"] is True
    assert found[0]["health"] is None


def test_list_with_health_runs_the_checks(fake, no_modules, capsys):
    add(**written("web", enabled=True, health=[PY, "-c", "print('down'); raise SystemExit(1)"]))
    fake.written["web"] = Unit("daemon", True, active=True)
    code, out, _ = run(capsys, "list", "--health", "--json")
    found = json.loads(out)[0]
    assert found["health"] == {"state": "fail", "detail": "down"}
    assert found["verdict"]["state"] == "fail"


def test_list_with_no_rows_says_how_to_add_one(fake, no_modules, capsys):
    code, out, _ = run(capsys, "list")
    assert code == 0 and "moxi services add" in out


def test_list_shows_module_rows(fake, site, capsys):
    site.module("jotter", '''
        DAEMONS = {"sync": {"command": "moxi-jotter-sync", "doc": "sync the notes",
                            "stop": "group", "upgrade": "restart"}}
        ''')
    fake.modules[("jotter", "sync")] = Unit("daemon", True, active=False)
    code, out, _ = run(capsys, "list", "--json")
    found = {r["id"]: r for r in json.loads(out)}
    assert found["jotter/sync"]["verdict"]["reason"] == "not running"
    assert found["jotter/sync"]["verdict"]["fix"] == "moxi services start jotter/sync"


def test_a_busy_store_exits_75(fake, no_modules, capsys, monkeypatch):
    def busy(*a, **k):
        raise Busy()

    monkeypatch.setattr(store, "read_rows", busy)
    code, _, err = run(capsys, "list")
    assert code == 75 and "stayed locked" in err


# --- show -------------------------------------------------------------------------------

def test_show_a_running_daemon(fake, no_modules, capsys):
    add(**written("web", enabled=True, health=[PY, "-c", "print('fine')"]))
    fake.written["web"] = Unit("daemon", True, active=True)
    code, out, _ = run(capsys, "show", "web")
    assert code == 0
    assert out.splitlines()[0] == "services/web"
    said = {line.split(None, 1)[0]: line.split(None, 1)[1].strip()
            for line in out.splitlines()[1:] if not line.startswith("    ")}
    assert said["verdict"] == "ok: running"
    assert said["checked"] == "ok: fine"
    assert said["log"] == "journalctl --user -u web"


AS_TIMER = ("judged as a timer, so if it should keep running, remove it and add it again "
            "without --timer")


@pytest.fixture
def windows_timer(fake, no_modules, monkeypatch):
    """A Windows task adopted as a timer, as every row adopted before Windows tasks
    could be daemons is, whose last run crashed."""
    from types import SimpleNamespace

    monkeypatch.setattr(rows, "sys", SimpleNamespace(platform="win32"))
    fake.supervisor = "schtasks"
    ref = "\\Vendor\\agent"
    add(**adopted("agent", unit=ref, enabled=True, platform="win32"))
    fake.foreign[ref] = Unit("timer", True, last={"supervisor": {
        "last_run": "2026-11-07T09:30:00", "last_result": 1}})
    return ref


def test_show_an_adopted_windows_timer_says_how_it_is_judged(windows_timer, capsys):
    code, out, _ = run(capsys, "show", "agent")
    assert code == 0
    lines = out.splitlines()
    assert ("  note             judged as a timer, by its schedule and last run; a task "
            "meant to keep running is adopted without --timer (remove it and add it "
            "again)") in lines
    [verdict] = [line for line in lines if line.startswith("  verdict")]
    assert verdict.endswith(AS_TIMER)


@pytest.mark.parametrize("argv", [["list"], ["list", "--health"]])
def test_list_says_a_crashed_windows_timer_is_judged_as_a_timer(windows_timer, capsys,
                                                                argv):
    code, out, _ = run(capsys, *argv)
    assert code == 0
    [line] = [line for line in out.splitlines() if line.startswith("services/agent")]
    assert line.split()[:3] == ["services/agent", "timer", "warn"]
    assert f"last run failed (result 0x00000001 at 2026-11-07T09:30:00); {AS_TIMER}" in line


def test_show_a_timer_elsewhere_says_nothing_of_how_it_is_judged(fake, no_modules, capsys):
    add(**written("nightly", kind="timer", every="1h", enabled=True))
    fake.written["nightly"] = Unit("timer", True)
    code, out, _ = run(capsys, "show", "nightly")
    assert code == 0 and "judged" not in out


def test_show_json_has_the_row_and_its_runs(fake, no_modules, capsys, monkeypatch):
    add(**written("job", kind="timer", every="5m"))
    fake.written["job"] = Unit("timer", False)
    monkeypatch.setattr(fake, "last_runs", lambda module, timer, n=1: [
        {"unit": "services/job", "at": "2026-10-30T09:00:00Z", "seconds": 1.0,
         "result": "ran", "exit": 0}])
    code, out, _ = run(capsys, "show", "services/job", "--json")
    found = json.loads(out)
    assert code == 0
    assert set(found) == ROW_KEYS | {"row", "detail", "log", "wrapped", "process", "runs"}
    assert found["row"]["every"] == "5m" and found["runs"][0]["result"] == "ran"
    assert found["process"] is None


def test_show_an_unknown_id_exits_3(fake, no_modules, capsys):
    code, _, err = run(capsys, "show", "nope")
    assert code == 3 and "no service is named nope" in err


# --- add --------------------------------------------------------------------------------

def test_add_a_command(fake, capsys):
    code, out, _ = run(capsys, "add", "web", "--health", "curl -fsS 'http://x/ok'", "--",
                       "python3", "-m", "http.server", "--bind", "127.0.0.1")
    assert code == 0, out
    row = store.get("web")
    assert row.argv[0] == shutil.which("python3")
    assert row.argv[1:] == ("-m", "http.server", "--bind", "127.0.0.1")
    assert row.health == ("curl", "-fsS", "http://x/ok")
    assert 'health check: ["curl", "-fsS", "http://x/ok"]' in out
    assert "services/web added, off" in out


def test_add_a_timer_splits_at_and_days(fake, capsys):
    code, out, err = run(capsys, "add", "job", "--timer", "--at", "09:00, 17:30", "--days",
                         "mon,fri", "--enable", "--", PY, "-c", "pass")
    assert code == 0, err
    row = store.get("job")
    assert (row.at, row.days, row.enabled) == (("09:00", "17:30"), ("mon", "fri"), True)


@pytest.mark.parametrize("argv, said", [
    (["add", "web"], "give the command to run after --"),
    (["add", "web", "--every", "5m", "--", PY], "add --timer"),
    (["add", "web", "--unit", "x.service", "--enable", "--cwd", "/tmp"], "--cwd is for"),
    (["add", "web", "--unit", "x.service", "--enable", "--", PY], "-- ARGV is for"),
    (["add", "web", "--health", "", "--", PY], "--health is empty"),
    (["add", "web", "--health", "'open", "--", PY], "--health"),
    (["add", "web", "--enable", "--disable", "--", PY], "not allowed with"),
    (["frobnicate"], "invalid choice"),
])
def test_usage_errors_exit_2(fake, capsys, argv, said):
    code, _, err = run(capsys, *argv)
    assert code == 2 and said in err


def test_adopting_without_enable_or_disable_exits_2_saying_what_is_there(fake, capsys):
    fake.foreign["site.service"] = Unit("daemon", True, active=True)
    code, _, err = run(capsys, "add", "site", "--unit", "site.service")
    assert code == 2
    assert "site.service: starts at login, running; adopt it with --enable" in err
    assert store.get("site") is None


def test_adopting_with_enable(fake, capsys):
    fake.foreign["site.service"] = Unit("daemon", True, active=True)
    code, out, err = run(capsys, "add", "site", "--unit", "site.service", "--enable")
    assert code == 0, err
    assert store.get("site").unit == "site.service"


# --- the acts ---------------------------------------------------------------------------

def test_several_ids_act_on_each_and_exit_with_the_worst(fake, capsys):
    add(**written("web"))
    add(**written("api"))
    fake.written["web"] = Unit("daemon", False)
    fake.written["api"] = Unit("daemon", False)
    code, out, err = run(capsys, "start", "web", "nope", "api")
    assert code == 3
    assert fake.written["web"].active and fake.written["api"].active
    assert "no service is named nope" in err
    fake.fail.add(("stop", "api"))
    code, out, err = run(capsys, "stop", "web", "api")
    assert code == 1 and "Job for api failed" in err


def test_an_act_on_a_row_of_another_system_exits_3(fake, capsys):
    add(**written("web", platform="darwin" if sys.platform != "darwin" else "linux"))
    code, _, err = run(capsys, "restart", "web")
    assert code == 3 and "only the reads and remove work" in err
    assert "fix: moxi services remove web" in err


def test_remove(fake, capsys):
    add(**written("web"))
    fake.written["web"] = Unit("daemon", False)
    code, out, _ = run(capsys, "remove", "web")
    assert code == 0 and "services/web removed" in out and store.get("web") is None


def test_a_module_switch_prints_the_cores_unit_lines_once(fake, site, capsys,
                                                           monkeypatch):
    from moxi.units import Applied, UnitChange

    site.module("jotter", '''
        DAEMONS = {"sync": {"command": "moxi-jotter-sync", "doc": "sync",
                            "stop": "group", "upgrade": "restart",
                            "switch": "sync.enabled"}}
        ''')
    change = UnitChange("jotter", "sync", "daemon", "add", None, (), None, "add")

    def config_set(module, key, value, *, by):
        print("jotter/sync: written and started")  # the core says it as it applies
        return [Applied(change, True, ("jotter/sync: written and started",))]

    monkeypatch.setattr(moxi.config, "set", config_set)
    code, out, _ = run(capsys, "enable", "jotter/sync")
    assert code == 0
    assert out.count("jotter/sync: written and started") == 1
    assert "jotter sync.enabled set to true" in out


def test_the_worst_code():
    assert cli.worst([0, 75, 3]) == 3
    assert cli.worst([3, 1, 2]) == 1
    assert cli.worst([0, 75]) == 75
    assert cli.worst([]) == 0


# --- splitting --health -----------------------------------------------------------------

@pytest.mark.parametrize("text, argv", [
    ("curl -fsS http://x", ["curl", "-fsS", "http://x"]),
    ("check 'two words' \"and more\"", ["check", "two words", "and more"]),
])
def test_health_splits_as_posix_does(text, argv):
    assert cli.split_command(text, windows=False) == argv


@pytest.mark.parametrize("text, argv", [
    (r'"C:\Program Files\x.exe" -a b', [r"C:\Program Files\x.exe", "-a", "b"]),
    (r'C:\bin\x.exe "a b" c', [r"C:\bin\x.exe", "a b", "c"]),
    (r'x.exe a\\\"b', ["x.exe", r'a\"b']),
    (r'x.exe "a\\" b', ["x.exe", "a\\", "b"]),
    (r'x.exe a\b \\server', ["x.exe", r"a\b", r"\\server"]),
    (r'x.exe "say ""hi"""', ["x.exe", 'say "hi"']),
    (r'x.exe ""', ["x.exe", ""]),
])
def test_health_splits_as_windows_does(text, argv):
    assert cli.split_command(text, windows=True) == argv


# --- watch ------------------------------------------------------------------------------

def rounds(monkeypatch, n, between=None):
    """Let watch read `n` times, calling `between(i)` after each read."""
    count = [0]

    def sleep(seconds):
        count[0] += 1
        if between is not None:
            between(count[0])
        if count[0] >= n:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_sleep", sleep)


def test_watch_prints_a_line_when_a_verdict_changes(fake, no_modules, capsys, monkeypatch):
    add(**written("web", enabled=True))
    fake.written["web"] = Unit("daemon", True, active=True)

    def between(i):
        if i == 2:
            fake.written["web"].active = False

    rounds(monkeypatch, 4, between)
    code, out, _ = run(capsys, "watch", "web", "--every", "1")
    lines = out.splitlines()
    assert code == 0 and len(lines) == 2
    assert lines[0].split("  ")[1:4] == ["services/web", "ok", "running"]
    assert lines[1].split("  ")[1:4] == ["services/web", "fail", "not running"]
    assert lines[1].endswith("(fix: moxi services start web)")


def test_watch_json_and_a_row_that_goes(fake, no_modules, capsys, monkeypatch):
    add(**written("web"))
    fake.written["web"] = Unit("daemon", False)

    def between(i):
        if i == 1:
            store.write(lambda c: store.delete(c, "web"))

    rounds(monkeypatch, 2, between)
    code, out, _ = run(capsys, "watch", "--json")
    found = [json.loads(line) for line in out.splitlines()]
    assert code == 0
    assert found[0]["id"] == "services/web" and found[0]["verdict"]["state"] == "off"
    assert found[1] == {"at": found[1]["at"], "id": "services/web", "gone": True}


def test_watch_runs_health_every_30_seconds(fake, no_modules, capsys, monkeypatch):
    add(**written("web", enabled=True, health=[PY, "-c", "pass"]))
    fake.written["web"] = Unit("daemon", True, active=True)
    from moxi_services import state

    ran = []
    monkeypatch.setattr(state, "run_health", lambda argv: ran.append(1) or ("ok", "ok"))
    now = [0.0]
    monkeypatch.setattr(cli, "_clock", lambda: now[0])

    def between(i):
        now[0] += 10.0

    rounds(monkeypatch, 7, between)
    assert run(capsys, "watch", "web")[0] == 0
    assert len(ran) == 3  # at 0, 30 and 60 s


def test_watch_says_a_modules_declaration_problem_once_and_when_it_changes(
        fake, capsys, monkeypatch):
    problems = [("notes", "UNITS_FROM failed: boom")]
    monkeypatch.setattr(rows, "module_rows", lambda modules=None: ([], list(problems)))

    def between(i):
        if i == 3:
            problems[:] = [("notes", "UNITS_FROM failed: bang")]
        elif i == 4:
            problems.clear()
        elif i == 5:
            problems[:] = [("notes", "UNITS_FROM failed: bang")]

    rounds(monkeypatch, 7, between)
    code, _, err = run(capsys, "watch")
    notes = [line for line in err.splitlines() if line.startswith("note:")]
    assert code == 0
    assert notes == [
        "note: UNITS_FROM failed: boom; notes's static daemons and timers are listed",
        "note: UNITS_FROM failed: bang; notes's static daemons and timers are listed",
        "note: UNITS_FROM failed: bang; notes's static daemons and timers are listed"]


def test_watch_refuses_an_unknown_id_before_reading(fake, no_modules, capsys):
    code, _, err = run(capsys, "watch", "nope")
    assert code == 3 and fake.calls == []


# --- through the core -------------------------------------------------------------------

def test_moxi_services_runs_through_the_cores_dispatch(fake, site, capsys):
    add(**adopted("site", kind="daemon", unit="site.service"))
    code = moxi.cli.dispatch("services", ["list", "--json"])
    out, _ = capsys.readouterr()
    assert code == 0 and json.loads(out)[0]["id"] == "services/site"
