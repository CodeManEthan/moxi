"""services' page server in process, through loopback's strict app: the reads, the acts
as jobs, idempotency, timers, add, and the status route.

Loopback is reached as the page reaches it, through `moxi.modules.load`: services'
distribution doesn't require it, so these tests don't import it either. The app runs
behind `guard` with an upstream profile, as the runner serves it, with strict projection,
so a handler that sends a key its route doesn't declare, or a value of another type, fails
here. A new app stands for the base after it dropped a key: its key map is empty. A new
job table as well stands for the page server after a restart.

No test reaches a supervisor: the `fake` fixture puts a fake `moxi.units` in place, and
the `site` fixture shows the tests no other installed module's daemons and timers.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from moxi_services import acts, jobs, page, pagehandlers, store

from page_kit import LB, send, served

KEY = "K" * 43
OTHER_KEY = "L" * 43
PY = sys.executable


@pytest.fixture(autouse=True)
def fresh_page(monkeypatch, site, fake):
    """A job table of its own per test, no health results kept from another, no module
    rows but a test's own, and a fake supervisor."""
    table = jobs.Jobs(LB.busy)
    monkeypatch.setattr(pagehandlers, "JOBS", table)
    monkeypatch.setattr(pagehandlers, "_known", {})
    monkeypatch.setattr(pagehandlers, "_streaks", {})
    monkeypatch.setattr(pagehandlers, "_logged", set())
    yield fake
    table.close()


@pytest.fixture
def restart(monkeypatch):
    """Put a new job table in, as a page server's restart does; each one it put in is
    closed at the end, before `monkeypatch` puts the server's own table back."""
    made: list[jobs.Jobs] = []

    def _restart() -> None:
        made.append(jobs.Jobs(LB.busy))
        monkeypatch.setattr(pagehandlers, "JOBS", made[-1])

    yield _restart
    for table in made:
        table.close()


def get(path: str, query: str = "", app=None) -> tuple[int, dict | None]:
    return asyncio.run(send(app or served(), "GET", path, query=query))


def read(path: str, query: str = "") -> dict:
    status, body = get(path, query)
    assert status == 200, body
    return body


def post(path: str, body: dict | None = None, *, key: str | None = None,
         app=None) -> tuple[int, dict | None]:
    return asyncio.run(send(app or served(), "POST", path, body=body, key=key))


def accepted(answer: tuple[int, dict | None]) -> str:
    """The job id of a 202."""
    status, body = answer
    assert status == 202, answer
    path, _, job = body["status"].partition("?job=")
    assert path == page.STATUS_ROUTE and job, body
    return job


def refused(answer: tuple[int, dict | None]) -> str:
    status, body = answer
    assert status == 422, answer
    return body["refused"]


def job_status(job: str) -> dict:
    return read(page.STATUS_ROUTE, f"job={job}")


def ended(job: str, within: float = 10.0) -> dict:
    """The job's status once it is no longer running."""
    deadline = time.monotonic() + within
    while True:
        found = job_status(job)
        if found["state"] != "running":
            return found
        assert time.monotonic() < deadline, f"job {job} still running: {found}"
        time.sleep(0.01)


def a_daemon(name: str = "web", **extra) -> None:
    out = acts.add_written(name, [PY, "-m", "http.server"], **extra)
    assert out.ok, out


def a_timer(name: str = "nightly", **extra) -> None:
    out = acts.add_written(name, [PY], is_timer=True, every="1h", **extra)
    assert out.ok, out


def test_a_moxi_daemon_stopped_by_hand_reads_stopped_and_counts_as_a_warning(site, fake):
    from fake_units import Unit

    site.module("notes", 'SUMMARY = "notes"\nDAEMONS = {"sync": {"command": "moxi-notes-sync"}}\n')
    fake.modules[("notes", "sync")] = Unit("daemon", enabled=True, active=True)
    assert acts.stop("notes/sync").code == 0
    listed = read("/api/rows")["rows"]
    sync = {r["id"]: r for r in listed}["notes/sync"]
    assert (sync["verdict"]["state"], sync["verdict"]["cause"]) == ("warn", "stopped")
    assert sync["stopped"] == store.read_module_stops()["notes/sync"]
    # The summary block counts the rows by verdict state: a warning, not a failure.
    assert [r["verdict"]["state"] for r in listed] == ["warn"]


def busy_now() -> list[str]:
    status, health = get("/api/health")
    assert status == 200, health
    return health["open"]["busy"]


# -- the reads -----------------------------------------------------------------------------

def test_rows_lists_every_row_with_its_verdict_and_state():
    a_daemon(enable=True)
    a_timer()
    listed = read("/api/rows")
    assert (listed["state"], listed["detail"], listed["problems"]) == ("up", None, [])
    by_id = {r["id"]: r for r in listed["rows"]}
    assert set(by_id) == {"services/web", "services/nightly"}
    web, nightly = by_id["services/web"], by_id["services/nightly"]
    assert (web["form"], web["kind"], web["flag"], web["state"]) == (
        "written", "daemon", True, "active")
    assert web["verdict"] == {"state": "ok", "reason": web["verdict"]["reason"],
                              "fix": None, "unknown": False, "cause": "running"}
    assert web["argv"] == [PY, "-m", "http.server"] and web["has_switch"] is True
    assert (nightly["kind"], nightly["flag"], nightly["schedule"]) == ("timer", False,
                                                                      "every 1h")
    assert nightly["verdict"]["state"] == "off"


def test_rows_carries_the_last_health_result_and_runs_no_check(monkeypatch):
    a_daemon(enable=True, health=[PY, "-c", "pass"])
    ran = []
    monkeypatch.setattr(pagehandlers.state, "run_health",
                        lambda argv: ran.append(argv) or ("ok", "fine"))
    assert read("/api/rows")["rows"][0]["checked"] is None
    assert ran == []
    pagehandlers.gather(check_health=True)
    assert ran == [(PY, "-c", "pass")]
    checked = read("/api/rows")["rows"][0]["checked"]
    assert (checked["state"], checked["line"]) == ("ok", "fine")
    assert len(ran) == 1


def test_checked_at_is_when_the_health_pass_that_ran_the_check_began(monkeypatch):
    a_daemon(enable=True, health=[PY, "-c", "pass"])
    monkeypatch.setattr(pagehandlers.state, "run_health", lambda argv: ("ok", "fine"))
    before = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    passed = pagehandlers.gather(check_health=True)["rows"][0]["checked"]["at"]
    after = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    assert before <= passed <= after
    # A read between passes carries the kept result's pass time.
    assert read("/api/rows")["rows"][0]["checked"]["at"] == passed
    assert read("/api/row", "id=web")["row"]["checked"]["at"] == passed


# --- the row's fields beyond its verdict ----------------------------------------------

CLOCK = '''
SUMMARY = "clock"
SETTINGS = "settings.toml"
DAEMONS = {"tick": {"command": "moxi-clock-tick"}}
TIMERS = {"chime": {"command": "moxi-clock-chime", "at": ["09:00", "17:30"],
                    "days": ["mon", "fri"], "switch": "chime.enabled"},
          "nudge": {"command": "moxi-clock-nudge", "every": "15m",
                    "switch": "nudge.enabled"}}
'''

CLOCK_SETTINGS = '''
[chime.enabled]
type = "bool"
default = true
doc = "Whether the chime runs."
read = "use"

[nudge.enabled]
type = "bool"
default = true
doc = "Whether the nudge runs."
read = "use"
'''

SLOTS = '''
SUMMARY = "slots"
DAEMONS = {"server": {"command": "moxi-slots-server"},
           "keeper": {"command": "moxi-slots-keeper"}}
'''


def by_id() -> dict[str, dict]:
    return {r["id"]: r for r in read("/api/rows")["rows"]}


def clock(site) -> None:
    site.module("clock", CLOCK)
    site.write("moxi_clock/settings.toml", CLOCK_SETTINGS)


def test_timing_is_a_written_or_module_timers_schedule_as_declared(site, fake):
    from fake_units import Unit

    clock(site)
    a_daemon()
    a_timer("hourly")
    out = acts.add_written("weekly", [PY], is_timer=True, at=["09:00"], days=["mon"])
    assert out.ok, out
    fake.foreign["backup.timer"] = Unit("timer", enabled=True)
    assert acts.add_adopted("backup", "backup.timer", enable=True, is_timer=True).ok
    rows_now = by_id()
    assert rows_now["services/hourly"]["timing"] == {"every": "1h", "at": None, "days": None}
    assert rows_now["services/weekly"]["timing"] == {"every": None, "at": ["09:00"],
                                                     "days": ["mon"]}
    assert rows_now["clock/chime"]["timing"] == {"every": None, "at": ["09:00", "17:30"],
                                                 "days": ["mon", "fri"]}
    assert rows_now["clock/nudge"]["timing"] == {"every": "15m", "at": None, "days": None}
    # An adopted timer's schedule is the supervisor's, which is not read; a daemon has none.
    assert rows_now["services/backup"]["timing"] is None
    assert rows_now["services/web"]["timing"] is None
    assert rows_now["clock/tick"]["timing"] is None


def test_ends_slots_is_true_on_slots_server_only(site):
    site.module("slots", SLOTS)
    clock(site)
    a_daemon("server")
    rows_now = by_id()
    assert {i for i, r in rows_now.items() if r["ends_slots"]} == {"slots/server"}
    assert read("/api/row", "id=slots/server")["row"]["ends_slots"] is True


def test_wrapped_is_true_for_an_adopted_linux_timer_only(site):
    from services_kit import add, adopted

    clock(site)
    a_daemon()
    a_timer("hourly")
    add(**adopted("backup", platform="linux"))
    add(**adopted("nightly", unit="nightly.timer", platform="win32"))
    add(**adopted("mac", unit="mac.timer", platform="darwin"))
    add(**adopted("served", kind="daemon", unit="served.service", platform="linux"))
    rows_now = by_id()
    assert {i for i, r in rows_now.items() if r["wrapped"]} == {"services/backup"}
    assert read("/api/row", "id=backup")["row"]["wrapped"] is True
    assert read("/api/row", "id=nightly")["row"]["wrapped"] is False


def test_has_settings_is_true_for_a_module_row_whose_module_has_settings(site):
    clock(site)
    site.module("slots", SLOTS)
    a_daemon()
    rows_now = by_id()
    assert {i for i, r in rows_now.items() if r["has_settings"]} == {
        "clock/tick", "clock/chime", "clock/nudge"}
    assert read("/api/row", "id=clock/tick")["row"]["has_settings"] is True
    assert read("/api/row", "id=slots/keeper")["row"]["has_settings"] is False


def test_a_settings_schema_that_cannot_be_read_gives_no_settings(site):
    site.module("clock", CLOCK)
    assert not any(r["has_settings"] for r in by_id().values())


def test_last_run_carries_the_gate_lines_exit_code(fake):
    a_timer()
    fake.written["nightly"].runs = [
        {"at": "2026-11-18T10:00:00Z", "result": "ran", "exit": 0, "seconds": 1.0},
        {"at": "2026-11-18T11:00:00Z", "result": "ran", "exit": 3, "seconds": 2.5}]
    last = by_id()["services/nightly"]["last_run"]
    assert last == {"failed": True, "text": "exit 3", "at": "2026-11-18T11:00:00Z",
                    "exit": 3}
    found = read("/api/row", "id=nightly")
    assert [r["exit"] for r in found["runs"]] == [0, 3]


def test_since_and_pid_are_read_only_while_a_daemon_is_active(fake):
    a_daemon(enable=True)
    unit = fake.written["web"]
    unit.pid, unit.since = 4242, 1_763_460_000.0
    web = by_id()["services/web"]
    assert (web["state"], web["pid"], web["since"]) == ("active", 4242,
                                                       "2025-11-18T10:00:00Z")
    unit.active, unit.failed = False, True
    web = by_id()["services/web"]
    assert (web["state"], web["pid"], web["since"]) == ("failed", None, None)
    assert (web["verdict"]["state"], web["verdict"]["cause"]) == ("fail", "not-running")


def test_a_store_that_cannot_be_read_is_state_error_with_the_reason(too_new):
    listed = read("/api/rows")
    assert listed["state"] == "error" and listed["rows"] == [] and listed["at"] is None
    assert "newer than" in listed["detail"]
    assert listed["detail"].endswith("fix: upgrade it with `moxi install services`")


def test_an_unexpected_failure_is_state_error_naming_only_its_class(monkeypatch):
    def broken():
        raise RuntimeError("/home/someone/secret")

    monkeypatch.setattr(pagehandlers, "every_row", broken)
    listed = read("/api/rows")
    assert listed["state"] == "error"
    assert "RuntimeError" in listed["detail"] and "secret" not in listed["detail"]


def _garbage(store_path):
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_bytes(b"not a database " * 512)


def _too_new(store_path):
    store.write(lambda conn: None)
    conn = sqlite3.connect(store_path)
    conn.execute(f"PRAGMA user_version = {store.VERSION + 1}")
    conn.commit()
    conn.close()


@pytest.mark.parametrize(("spoil", "says"), [
    (_garbage, pagehandlers.STORE_UNREADABLE),
    (_too_new, pagehandlers.STORE_TOO_NEW),
    (None, "no service is named web; fix: `moxi services list` shows every row's id"),
], ids=["unreadable", "too-new", "no-row"])
def test_no_refusal_the_gateway_can_reach_names_the_store_or_the_home(store_path, caplog,
                                                                      spoil, says):
    """The gateway passes a refusal's text to the phone as it is, so none names a file."""
    if spoil is not None:
        spoil(store_path)
    hidden = {str(store_path), str(store_path.parent), os.environ["HOME"],
                str(Path.home())}
    reached = [r for r in page.PAGE.routes if r.gateway and not r.stream]
    answers: dict[str, tuple[int, dict | None]] = {}
    for n, route in enumerate(reached):
        if route.kind == "read":
            query = "&".join(f"{k}={'web' if k == 'id' else 'x'}" for k in route.query)
            answers[route.path] = get(route.path, query)
        else:
            answers[route.path] = post(route.path, {"id": "web"}, key=f"{n:043d}")
    for path, (status, body) in answers.items():
        if 400 <= status < 500:
            text = json.dumps(body)
            assert not any(s in text for s in hidden), (path, text)
    row_routes = [r.path for r in reached
                  if r.path in ("/api/row", "/api/ends") or r.confirmed]
    assert len(row_routes) == 8, row_routes
    for path in row_routes:
        assert answers[path] == (422, {"refused": says}), (path, answers[path])
    if spoil is not None:
        logged = [r for r in caplog.records if r.name == "moxi.services.page"]
        assert len(logged) == 1 and str(store_path) in logged[0].getMessage(), logged


def test_row_gives_one_row_with_its_runs_and_log():
    a_timer()
    found = read("/api/row", "id=nightly")
    assert found["row"]["id"] == "services/nightly"
    assert found["runs"] == [] and found["log"] == "journalctl --user -u nightly"


def test_row_refuses_an_id_that_names_no_row():
    assert "no service is named gone" in refused(get("/api/row", "id=gone"))


def _projects(monkeypatch, found):
    real = pagehandlers.modules.load

    def load(name, **kwargs):
        return found if name == "projects" else real(name, **kwargs)

    monkeypatch.setattr(pagehandlers.modules, "load", load)


def test_projects_without_projects_says_why(monkeypatch):
    _projects(monkeypatch, SimpleNamespace(ok=False, reason="not installed"))
    assert read("/api/projects") == {
        "projects": [], "reason": "projects is not installed; `moxi install services "
                                  "--with projects` adds it"}


def test_projects_lists_each_project_by_key_with_its_root(monkeypatch):
    listed = [SimpleNamespace(key="moxi", root="/src/moxi")]
    _projects(monkeypatch, SimpleNamespace(ok=True, api=SimpleNamespace(
        list_projects=lambda: listed)))
    assert read("/api/projects") == {"projects": [{"key": "moxi", "root": "/src/moxi"}],
                                     "reason": None}


def test_projects_busy_says_try_again(monkeypatch):
    class Busy(Exception):
        pass

    def busy():
        raise Busy("locked")

    _projects(monkeypatch, SimpleNamespace(ok=True, api=SimpleNamespace(
        list_projects=busy)))
    assert read("/api/projects") == {"projects": [], "reason": "projects is busy; try again"}


# -- the status route -------------------------------------------------------------------------

def test_a_status_for_a_job_this_server_holds_no_record_of_is_unknown():
    assert job_status(KEY) == {"job": KEY, "act": None, "id": None, "state": "unknown",
                               "code": None, "lines": [pagehandlers.UNKNOWN], "fix": None}


def test_a_status_after_a_restart_is_unknown(restart):
    a_daemon(enable=True)
    job = accepted(post("/api/stop", {"id": "web"}, key=KEY))
    assert ended(job)["state"] == "done"
    restart()
    assert job_status(job)["state"] == "unknown"


# -- the confirmed acts -------------------------------------------------------------------------

def test_stop_answers_202_and_its_status_is_done_with_the_acts_lines(fake):
    a_daemon(enable=True)
    job = accepted(post("/api/stop", {"id": "web"}, key=KEY))
    assert job == KEY
    found = ended(job)
    assert (found["act"], found["id"], found["state"], found["code"]) == (
        "stop", "services/web", "done", 0)
    assert found["lines"] == ["web stoped"] and found["fix"] is None
    assert not fake.written["web"].active
    assert store.get("web").changed_by == pagehandlers.BY


@pytest.mark.parametrize("path, act, on", [("/api/start", "start", False),
                                           ("/api/restart", "restart", True)])
def test_start_and_restart_of_a_daemon_run_its_verb(fake, path, act, on):
    a_daemon(enable=on)
    found = ended(accepted(post(path, {"id": "services/web"}, key=KEY)))
    assert (found["act"], found["state"]) == (act, "done"), found
    assert (act, "services", ("web",), False) in fake.calls
    assert fake.written["web"].active


@pytest.mark.parametrize("path, act, on", [("/api/enable", "enable", True),
                                           ("/api/disable", "disable", False)])
def test_enable_and_disable_set_the_flag_and_its_unit(fake, path, act, on):
    a_daemon(enable=not on)
    found = ended(accepted(post(path, {"id": "web"}, key=KEY)))
    assert (found["act"], found["state"]) == (act, "done"), found
    assert store.get("web").enabled is on and fake.written["web"].enabled is on


def test_an_act_on_an_id_that_names_no_row_is_refused_before_any_job():
    assert "no service is named gone" in refused(post("/api/stop", {"id": "gone"},
                                                      key=KEY))
    assert pagehandlers.JOBS.get(KEY) is None


def test_an_act_the_verb_refuses_ends_refused_with_its_reason_and_fix(fake):
    a_daemon()
    fake.supervisor = None
    found = ended(accepted(post("/api/start", {"id": "web"}, key=KEY)))
    assert (found["state"], found["code"]) == ("refused", 3)
    assert any("no supervisor" in line for line in found["lines"])


def test_an_act_that_fails_ends_failed(fake):
    a_daemon()
    fake.fail.add(("start", "web"))
    found = ended(accepted(post("/api/start", {"id": "web"}, key=KEY)))
    assert (found["state"], found["code"]) == ("failed", 1)


def test_an_act_that_raises_ends_failed_naming_only_the_class(monkeypatch):
    a_daemon()

    def broken(ident, *, by):
        raise RuntimeError("/home/someone/secret")

    monkeypatch.setattr(acts, "stop", broken)
    found = ended(accepted(post("/api/stop", {"id": "web"}, key=KEY)))
    assert found["state"] == "failed"
    assert found["lines"] == [jobs.RAISED.format("RuntimeError")]


# -- an act that blocks -------------------------------------------------------------------------

def test_an_act_that_blocks_answers_202_at_once_and_stays_running_until_it_ends(
        monkeypatch):
    a_daemon(enable=True)
    release, entered = threading.Event(), threading.Event()
    real = acts.stop

    def slow(ident, *, by):
        # Stands for an act that waits on the core lock and the supervisor past the
        # shell's 30 seconds: it ends only when the test lets it.
        entered.set()
        assert release.wait(30)
        return real(ident, by=by)

    monkeypatch.setattr(acts, "stop", slow)
    began = time.monotonic()
    job = accepted(post("/api/stop", {"id": "web"}, key=KEY))
    assert time.monotonic() - began < 5
    assert entered.wait(5)
    try:
        for _ in range(3):
            assert job_status(job)["state"] == "running"
            assert busy_now() == [jobs.HOLD]
            time.sleep(0.05)
    finally:
        release.set()
    assert ended(job)["state"] == "done"
    assert busy_now() == []


def test_an_add_that_blocks_answers_202_at_once_and_stays_running_until_it_ends(
        monkeypatch):
    release, entered = threading.Event(), threading.Event()
    real = acts.add_written

    def slow(*args, **kwargs):
        # Stands for an add that waits on the core lock and the supervisor past the
        # shell's 30 seconds: it ends only when the test lets it.
        entered.set()
        assert release.wait(30)
        return real(*args, **kwargs)

    monkeypatch.setattr(acts, "add_written", slow)
    began = time.monotonic()
    job = accepted(post("/api/add", {"name": "web", "kind": "daemon",
                                     "command": f"{PY} -m http.server"}))
    assert time.monotonic() - began < 5
    assert entered.wait(5)
    try:
        for _ in range(3):
            found = job_status(job)
            assert (found["act"], found["id"], found["state"]) == (
                "add", "services/web", "running")
            assert busy_now() == [jobs.HOLD]
            assert store.get("web") is None
            time.sleep(0.05)
    finally:
        release.set()
    assert ended(job)["state"] == "done"
    assert busy_now() == []
    assert store.get("web") is not None


def test_a_job_queued_behind_a_running_one_is_running_and_holds_the_idle_exit(monkeypatch):
    a_daemon(enable=True)
    a_daemon("api", enable=True)
    release = threading.Event()
    order = []
    real = acts.stop

    def slow(ident, *, by):
        order.append(ident)
        if ident == "services/web":
            assert release.wait(30)
        return real(ident, by=by)

    monkeypatch.setattr(acts, "stop", slow)
    first = accepted(post("/api/stop", {"id": "web"}, key=KEY))
    second = accepted(post("/api/stop", {"id": "api"}, key=OTHER_KEY))
    try:
        assert job_status(second)["state"] == "running"
        assert busy_now() == [jobs.HOLD]
    finally:
        release.set()
    assert ended(first)["state"] == ended(second)["state"] == "done"
    assert order == ["services/web", "services/api"]
    assert busy_now() == []


# -- idempotency ------------------------------------------------------------------------------

def test_a_repeated_key_gets_the_same_link_and_acts_nothing(fake):
    a_daemon(enable=True)
    app = served()
    job = accepted(post("/api/stop", {"id": "web"}, key=KEY, app=app))
    ended(job)
    assert accepted(post("/api/stop", {"id": "web"}, key=KEY, app=app)) == job
    assert [c for c in fake.calls if c[0] == "stop"] == [("stop", "services", ("web",),
                                                          False)]


def test_a_key_the_base_dropped_and_the_server_holds_gets_the_same_link(fake):
    a_daemon(enable=True)
    job = accepted(post("/api/stop", {"id": "web"}, key=KEY))
    ended(job)
    # A new app: the base's key map no longer holds the key, the job table does.
    assert accepted(post("/api/stop", {"id": "services/web"}, key=KEY)) == job
    assert len([c for c in fake.calls if c[0] == "stop"]) == 1


def test_a_held_key_for_another_act_or_row_is_refused():
    a_daemon(enable=True)
    a_daemon("api", enable=True)
    ended(accepted(post("/api/stop", {"id": "web"}, key=KEY)))
    assert refused(post("/api/start", {"id": "web"}, key=KEY)) == pagehandlers.KEY_REUSED
    assert refused(post("/api/stop", {"id": "api"}, key=KEY)) == pagehandlers.KEY_REUSED


def test_a_key_after_a_restart_is_acted_on_anew(fake, restart):
    a_daemon(enable=True)
    ended(accepted(post("/api/stop", {"id": "web"}, key=KEY)))
    restart()
    found = ended(accepted(post("/api/stop", {"id": "web"}, key=KEY)))
    # A stop again ends where the first did.
    assert found["state"] == "done" and not fake.written["web"].active
    assert len([c for c in fake.calls if c[0] == "stop"]) == 2


def test_a_remove_job_after_a_restart_is_refused_for_the_removed_row(restart):
    a_daemon()
    remove = {"act": "remove", "id": "web", "job": "0" * 32}
    ended(accepted(post("/api/act", remove)))
    restart()
    assert "no service is named web" in refused(post("/api/act", remove))


# -- timers -----------------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/api/start", "/api/restart"])
def test_a_timers_start_and_restart_go_through_run(path):
    a_timer(enable=True)
    assert refused(post(path, {"id": "nightly"}, key=KEY)) == pagehandlers.TIMER_RUNS
    assert pagehandlers.JOBS.get(KEY) is None


def test_run_refuses_a_daemon():
    a_daemon()
    assert refused(post("/api/run", {"id": "web"}, key=KEY)) == pagehandlers.DAEMON_STARTS


@pytest.mark.parametrize("body, act", [({}, "start"), ({"restart": False}, "start"),
                                       ({"restart": True}, "restart")])
def test_run_runs_a_timer_now_through_its_start_or_restart(fake, body, act):
    a_timer(enable=True)
    found = ended(accepted(post("/api/run", {"id": "nightly", **body}, key=KEY)))
    assert (found["act"], found["state"]) == ("run", "done"), found
    assert (act, "services", ("nightly",), False) in fake.calls


def test_a_timers_stop_goes_through_stop(fake):
    a_timer(enable=True)
    found = ended(accepted(post("/api/stop", {"id": "nightly"}, key=KEY)))
    assert found["state"] == "done"
    assert ("stop", "services", ("nightly",), False) in fake.calls


# -- add ------------------------------------------------------------------------------------------

def test_add_answers_202_and_its_status_has_the_rows_id_and_lines(fake):
    job = accepted(post("/api/add", {"name": "web", "kind": "daemon",
                                     "command": f"{PY} -m http.server"}))
    assert len(job) == 43
    found = ended(job)
    assert (found["act"], found["id"], found["state"], found["code"]) == (
        "add", "services/web", "done", 0)
    assert "services/web added, off" in found["lines"]
    row = store.get("web")
    assert row.argv == (PY, "-m", "http.server") and row.changed_by == pagehandlers.BY


def test_add_takes_a_timers_schedule_and_the_other_fields(fake):
    found = ended(accepted(post("/api/add", {
        "name": "nightly", "kind": "timer", "command": f"{PY} -c pass", "at": ["03:00"],
        "days": ["mon", "fri"], "cwd": "/", "health": f"{PY} -c 'print(1)'",
        "launches_session": True, "doc": "one line", "enable": True})))
    assert found["state"] == "done", found
    row = store.get("nightly")
    assert (row.kind, row.at, row.days, row.cwd, row.enabled) == (
        "timer", ("03:00",), ("mon", "fri"), "/", True)
    assert row.health == (PY, "-c", "print(1)") and row.launches_session


def test_a_repeated_add_is_a_second_job_refused_as_the_name_taken():
    body = {"name": "web", "kind": "daemon", "command": f"{PY} -m http.server"}
    first = accepted(post("/api/add", body))
    second = accepted(post("/api/add", body))
    assert first != second
    assert ended(first)["state"] == "done"
    found = ended(second)
    assert (found["state"], found["code"]) == ("refused", 3)
    assert any("web is taken" in line for line in found["lines"]), found


def test_add_refuses_a_command_that_does_not_start_with_a_full_path():
    text = refused(post("/api/add", {"name": "web", "kind": "daemon",
                                     "command": "python3 -m http.server"}))
    assert text == pagehandlers.not_a_full_path("python3")
    assert "give the full path; `command -v python3` in your shell prints it" in text


@pytest.mark.parametrize("body", [{}, {"command": f"{PY}", "unit": "backup.timer"}])
def test_add_takes_a_command_or_a_unit(body):
    assert "not both" in refused(post("/api/add", {"name": "web", "kind": "daemon",
                                                   **body}))


@pytest.mark.parametrize("body, says", [
    ({"name": "Web", "kind": "daemon", "command": PY}, "Web"),
    ({"name": "web", "kind": "daemon", "command": PY, "every": "5m"}, "schedule"),
    ({"name": "web", "kind": "timer", "command": PY}, "exactly one of"),
    ({"name": "web", "kind": "daemon", "command": PY, "cwd": "here"}, "cwd"),
    ({"name": "web", "kind": "daemon", "command": "'unbalanced"}, "quotes"),
    ({"name": "web", "kind": "daemon", "unit": "backup.service", "cwd": "/"}, "cwd"),
])
def test_add_refuses_fields_out_of_shape_before_any_job(body, says):
    assert says in refused(post("/api/add", body))


def test_add_adopts_a_unit_with_its_on_or_off(fake):
    from fake_units import Unit

    fake.foreign["backup.service"] = Unit("daemon", enabled=True, active=True)
    found = ended(accepted(post("/api/add", {"name": "backup", "kind": "daemon",
                                             "unit": "backup.service", "enable": True})))
    assert (found["state"], found["id"]) == ("done", "services/backup"), found
    assert store.get("backup").unit == "backup.service"


def test_an_adoption_without_its_on_or_off_ends_refused_saying_what_is_read_now(fake):
    from fake_units import Unit

    fake.foreign["backup.service"] = Unit("daemon", enabled=True, active=True)
    found = ended(accepted(post("/api/add", {"name": "backup", "kind": "daemon",
                                             "unit": "backup.service"})))
    assert (found["state"], found["code"]) == ("refused", 2)
    assert store.get("backup") is None


# --- a failing timer's streak ------------------------------------------------------------

def run_line(hour: int, code: int = 1, result: str = "ran") -> dict:
    return {"at": f"2026-11-18T{hour:02d}:00:00Z", "result": result, "exit": code,
            "seconds": 1.0}


def last_runs_calls(fake) -> list:
    return [c for c in fake.calls if c[0] == "last_runs"]


def failing_timer(fake, lines: list[dict]) -> None:
    a_timer(enable=True)
    fake.written["nightly"].runs = lines


def test_a_streak_counts_the_newest_failed_runs_and_when_the_first_of_them_ran(fake):
    failing_timer(fake, [run_line(9, 0), run_line(10, 1), run_line(11, 2),
                         run_line(12, 1)])
    nightly = by_id()["services/nightly"]
    assert nightly["verdict"]["cause"] == "last-run"
    assert nightly["streak"] == {"count": 3, "since": "2026-11-18T10:00:00Z",
                                 "same_exit": False}


def test_a_streak_of_one_exit_code_says_so(fake):
    failing_timer(fake, [run_line(10, 1), run_line(11, 1)])
    assert by_id()["services/nightly"]["streak"] == {
        "count": 2, "since": "2026-11-18T10:00:00Z", "same_exit": True}


def test_a_run_held_off_by_its_switch_ends_a_streak():
    lines = [run_line(10, 1), run_line(11, 0, "off"), run_line(12, 1)]
    assert pagehandlers.count_streak(lines) == {
        "count": 1, "since": "2026-11-18T12:00:00Z", "same_exit": True}


def test_a_timer_whose_last_run_passed_has_no_streak_and_reads_no_runs(fake):
    failing_timer(fake, [run_line(10, 1), run_line(11, 0)])
    assert by_id()["services/nightly"]["streak"] is None
    assert last_runs_calls(fake) == []
    assert pagehandlers.count_streak([run_line(10, 1), run_line(11, 0)]) is None
    assert pagehandlers.count_streak([]) is None


def test_a_streak_counts_back_through_at_most_fifty_run_lines(fake):
    failing_timer(fake, [run_line(h % 24, 1) for h in range(80)])
    nightly = by_id()["services/nightly"]
    assert nightly["streak"]["count"] == pagehandlers.STREAK_LINES == 50
    assert last_runs_calls(fake) == [("last_runs", "services", "nightly", 50)]


def test_a_streak_is_read_once_per_newest_run_line(fake):
    failing_timer(fake, [run_line(10, 1), run_line(11, 1)])
    by_id()
    by_id()
    assert read("/api/row", "id=nightly")["row"]["streak"]["count"] == 2
    assert len([c for c in last_runs_calls(fake) if c[3] == 50]) == 1
    fake.written["nightly"].runs.append(run_line(12, 1))
    assert by_id()["services/nightly"]["streak"]["count"] == 3
    assert len([c for c in last_runs_calls(fake) if c[3] == 50]) == 2


def test_a_daemon_has_no_streak(fake):
    a_daemon(enable=True)
    assert by_id()["services/web"]["streak"] is None


def test_rows_carry_when_the_supervisor_was_read():
    a_daemon()
    before = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    at = read("/api/rows")["at"]
    assert before <= at <= time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --- one row's units and today's runs ----------------------------------------------------

def test_a_socketless_systemd_daemons_unit_is_its_service():
    a_daemon()
    found = read("/api/row", "id=web")
    assert found["unit"] == "moxi-services-web.service"
    assert found["units"] == ["moxi-services-web.socket", "moxi-services-web.service"]
    assert found["today"] is None


def test_a_socket_daemons_unit_is_its_service_and_every_socket_is_listed(site, fake):
    site.module("slots", SLOTS)
    fake.sockets[("slots", "server")] = 1
    found = read("/api/row", "id=slots/server")
    assert found["unit"] == "moxi-slots-server.service"
    assert found["units"] == ["moxi-slots-server.socket", "moxi-slots-server-1.socket",
                              "moxi-slots-server.service"]


def test_a_timers_unit_is_its_timer():
    a_timer()
    found = read("/api/row", "id=nightly")
    assert found["unit"] == "moxi-services-nightly.timer"
    assert found["units"] == ["moxi-services-nightly.timer", "moxi-services-nightly.service"]


def test_an_adopted_rows_unit_is_its_own(fake):
    from fake_units import Unit

    fake.foreign["backup.service"] = Unit("daemon", enabled=True, active=True)
    assert acts.add_adopted("backup", "backup.service", enable=True).ok
    found = read("/api/row", "id=backup")
    assert (found["unit"], found["units"]) == ("backup.service", ["backup.service"])


@pytest.mark.parametrize(("supervisor", "unit"), [
    ("launchd", "moxi.services.web"), ("schtasks", "\\Moxi\\services\\web"),
    (None, None)])
def test_a_rows_unit_elsewhere_is_its_one_id(fake, supervisor, unit):
    a_daemon()
    fake.supervisor = supervisor
    found = read("/api/row", "id=web")
    assert found["unit"] == unit and found["units"] == ([unit] if unit else [])


def now_line(code: int = 0) -> dict:
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "result": "ran",
            "exit": code, "seconds": 1.0}


OLD_LINE = {"at": "2020-01-01T12:00:00Z", "result": "ran", "exit": 0, "seconds": 1.0}


def test_today_counts_a_timers_runs_on_this_machines_day_from_one_read(fake):
    a_timer()
    fake.written["nightly"].runs = [OLD_LINE, now_line(0), now_line(1), now_line(0)]
    found = read("/api/row", "id=nightly")
    assert found["today"] == {"runs": 3, "failed": 1, "more": False}
    assert len(found["runs"]) == 4
    reads = [c for c in fake.calls if c[0] == "last_runs"]
    assert reads == [("last_runs", "services", "nightly", pagehandlers.TODAY_LINES)]


def test_today_with_every_line_read_today_says_there_may_be_more(fake):
    a_timer()
    fake.written["nightly"].runs = [OLD_LINE] + [now_line()] * 600
    found = read("/api/row", "id=nightly")
    assert found["today"] == {"runs": 500, "failed": 0, "more": True}
    assert len(found["runs"]) == pagehandlers.RUNS_SHOWN == 5


def test_today_is_null_for_a_timer_whose_runs_no_run_line_records():
    from services_kit import add, adopted

    # An adopted timer off Linux: its runs are the supervisor's, recorded in no run line.
    add(**adopted("backup", platform="darwin" if sys.platform != "darwin" else "win32"))
    assert read("/api/row", "id=backup")["today"] is None


# --- a unit not yet adopted --------------------------------------------------------------

def unit_read(query: str) -> dict:
    return read("/api/unit", query)


def test_unit_reads_a_found_timer_which_adopting_wraps(fake):
    from fake_units import Unit

    fake.foreign["backup.timer"] = Unit("timer", enabled=True)
    assert unit_read("ref=backup.timer") == {
        "found": True, "kind": "timer", "asks_kind": False, "enabled": True,
        "active": True, "wraps": True, "refused": None}


def test_unit_reads_a_found_service(fake):
    from fake_units import Unit

    fake.foreign["web.service"] = Unit("daemon", enabled=False, active=False)
    assert unit_read("ref=web.service") == {
        "found": True, "kind": "daemon", "asks_kind": False, "enabled": False,
        "active": False, "wraps": False, "refused": None}


def test_unit_says_a_missing_unit_is_not_found():
    found = unit_read("ref=gone.service")
    assert (found["found"], found["enabled"], found["active"], found["refused"]) == (
        False, None, None, None)


@pytest.mark.parametrize(("query", "says"), [
    ("ref=moxi-notes-sync.service", "moxi's own unit namespace"),
    ("ref=backup.socket", "is not a .service or a .timer"),
    ("ref=backup.timer&kind=daemon", "backup.timer is a timer, not a service"),
    ("ref=web.service&kind=timer", "web.service is a service, not a timer"),
])
def test_unit_refuses_what_adopting_refuses(fake, query, says):
    found = unit_read(query)
    assert says in found["refused"] and found["found"] is None
    assert [c for c in fake.calls if c[0] == "status"] == []


@pytest.mark.parametrize(("query", "says"), [
    ("ref=backup.timer&kind=daemon", "backup.timer is a timer, not a service"),
    ("ref=web.service&kind=timer", "web.service is a service, not a timer"),
])
def test_unit_answers_a_kind_that_disagrees_with_the_suffix_in_the_pages_words(fake, query, says):
    assert unit_read(query)["refused"] == says


@pytest.mark.parametrize("supervisor", ["launchd", "schtasks"])
def test_unit_asks_the_kind_where_the_supervisor_cannot_tell(fake, supervisor):
    from fake_units import Unit

    fake.supervisor = supervisor
    fake.foreign["backup"] = Unit("timer", enabled=True)
    found = unit_read("ref=backup")
    assert (found["kind"], found["asks_kind"], found["wraps"]) == ("daemon", True, False)
    found = unit_read("ref=backup&kind=timer")
    assert (found["found"], found["kind"], found["asks_kind"]) == (True, "timer", True)
    assert ("status", ("timer",)) not in fake.kinds
    assert ("status", {"backup": "timer"}) in fake.kinds


def test_unit_with_no_supervisor_is_refused(fake):
    fake.supervisor = None
    assert "no supervisor here" in unit_read("ref=backup.timer")["refused"]


def test_unit_that_the_supervisor_does_not_answer_is_not_said_found(monkeypatch):
    monkeypatch.setattr(pagehandlers, "STATUS_SECONDS", 0.05)
    monkeypatch.setattr(pagehandlers.state, "read_foreign",
                        lambda ref, kind: time.sleep(1))
    found = unit_read("ref=backup.timer")
    assert (found["found"], found["enabled"], found["refused"]) == (None, None, None)


def test_unit_is_the_desks_alone():
    route = next(r for r in page.PAGE.routes if r.path == "/api/unit")
    assert route.gateway is False and route.kind == "read"
