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
    monkeypatch.setattr(pagehandlers, "_logged", set())
    yield fake
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
    out = acts.add_written(name, [PY], timer=True, every="1h", **extra)
    assert out.ok, out


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
                              "fix": None, "unknown": False}
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
    pagehandlers.gather(health=True)
    assert ran == [(PY, "-c", "pass")]
    assert read("/api/rows")["rows"][0]["checked"] == {"state": "ok", "line": "fine"}
    assert len(ran) == 1


def test_a_store_that_cannot_be_read_is_state_error_with_the_reason(too_new):
    listed = read("/api/rows")
    assert listed["state"] == "error" and listed["rows"] == []
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
    row_routes = [r.path for r in reached if r.path == "/api/row" or r.confirmed]
    assert len(row_routes) == 7, row_routes
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


def test_a_status_after_a_restart_is_unknown(monkeypatch):
    a_daemon(enable=True)
    job = accepted(post("/api/stop", {"id": "web"}, key=KEY))
    assert ended(job)["state"] == "done"
    monkeypatch.setattr(pagehandlers, "JOBS", jobs.Jobs(LB.busy))
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


def test_remove_of_a_user_row_removes_it():
    a_daemon()
    found = ended(accepted(post("/api/remove", {"id": "web"}, key=KEY)))
    assert (found["act"], found["state"]) == ("remove", "done"), found
    assert store.get("web") is None


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


def test_a_key_after_a_restart_is_acted_on_anew(fake, monkeypatch):
    a_daemon(enable=True)
    ended(accepted(post("/api/stop", {"id": "web"}, key=KEY)))
    monkeypatch.setattr(pagehandlers, "JOBS", jobs.Jobs(LB.busy))
    found = ended(accepted(post("/api/stop", {"id": "web"}, key=KEY)))
    # A stop again ends where the first did.
    assert found["state"] == "done" and not fake.written["web"].active
    assert len([c for c in fake.calls if c[0] == "stop"]) == 2


def test_a_remove_key_after_a_restart_is_refused_for_the_removed_row(monkeypatch):
    a_daemon()
    ended(accepted(post("/api/remove", {"id": "web"}, key=KEY)))
    monkeypatch.setattr(pagehandlers, "JOBS", jobs.Jobs(LB.busy))
    assert "no service is named web" in refused(post("/api/remove", {"id": "web"},
                                                     key=KEY))


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

    fake.foreign["backup.service"] = Unit("daemon", True, True)
    found = ended(accepted(post("/api/add", {"name": "backup", "kind": "daemon",
                                             "unit": "backup.service", "enable": True})))
    assert (found["state"], found["id"]) == ("done", "services/backup"), found
    assert store.get("backup").unit == "backup.service"


def test_an_adoption_without_its_on_or_off_ends_refused_saying_what_is_read_now(fake):
    from fake_units import Unit

    fake.foreign["backup.service"] = Unit("daemon", True, True)
    found = ended(accepted(post("/api/add", {"name": "backup", "kind": "daemon",
                                             "unit": "backup.service"})))
    assert (found["state"], found["code"]) == ("refused", 2)
    assert store.get("backup") is None
