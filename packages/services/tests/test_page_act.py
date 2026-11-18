"""services' page server: `/api/act`, the desk's one plain act, and what every act route
refuses beyond the verb: an enable or disable of a Moxi row.

No test reaches a supervisor: the `fake` fixture puts a fake `moxi.units` in place, and
the `site` fixture shows the tests no other installed module's daemons and timers.
"""

from __future__ import annotations

import asyncio
import sys
import time

import pytest

from moxi_services import acts, jobs, page, pagehandlers, store

from fake_units import Unit
from page_kit import LB, send, served

JOB = "0123456789abcdef0123456789ABCDEF"
OTHER_JOB = "f" * 32
KEY = "K" * 43
PY = sys.executable


@pytest.fixture(autouse=True)
def fresh_page(monkeypatch, site, fake):
    table = jobs.Jobs(LB.busy)
    monkeypatch.setattr(pagehandlers, "JOBS", table)
    monkeypatch.setattr(pagehandlers, "_known", {})
    monkeypatch.setattr(pagehandlers, "_streaks", {})
    monkeypatch.setattr(pagehandlers, "_logged", set())
    yield fake
    table.close()


def get(path: str, query: str = "") -> tuple[int, dict | None]:
    return asyncio.run(send(served(), "GET", path, query=query))


def post(path: str, body: dict, *, key: str | None = None) -> tuple[int, dict | None]:
    return asyncio.run(send(served(), "POST", path, body=body, key=key))


def act(name: str, ident: str, job: str = JOB, **extra) -> tuple[int, dict | None]:
    return post("/api/act", {"act": name, "id": ident, "job": job, **extra})


def accepted(answer: tuple[int, dict | None]) -> str:
    status, body = answer
    assert status == 202, answer
    path, _, job = body["status"].partition("?job=")
    assert path == page.STATUS_ROUTE and job, body
    return job


def refused(answer: tuple[int, dict | None]) -> str:
    status, body = answer
    assert status == 422, answer
    return body["refused"]


def ended(job: str, within: float = 10.0) -> dict:
    deadline = time.monotonic() + within
    while True:
        status, found = get(page.STATUS_ROUTE, f"job={job}")
        assert status == 200, found
        if found["state"] != "running":
            return found
        assert time.monotonic() < deadline, found
        time.sleep(0.01)


def a_daemon(name: str = "web", **extra) -> None:
    assert acts.add_written(name, [PY, "-m", "http.server"], **extra).ok


def a_timer(name: str = "nightly", **extra) -> None:
    assert acts.add_written(name, [PY], is_timer=True, every="1h", **extra).ok


@pytest.fixture
def notes(site, fake):
    """A Moxi module, notes, with a daemon, running."""
    site.module("notes", 'SUMMARY = "notes"\n'
                         'DAEMONS = {"sync": {"command": "moxi-notes-sync"}}\n')
    fake.modules[("notes", "sync")] = Unit("daemon", enabled=True, active=True)
    return "notes/sync"


# -- each act -------------------------------------------------------------------------------

@pytest.mark.parametrize("name, on, active", [("start", False, True),
                                              ("stop", True, False),
                                              ("restart", True, True)])
def test_a_daemons_start_stop_and_restart(fake, name, on, active):
    a_daemon(enable=on)
    found = ended(accepted(act(name, "web")))
    assert (found["job"], found["act"], found["id"], found["state"]) == (
        JOB, name, "services/web", "done"), found
    assert (name, "services", ("web",), False) in fake.calls
    assert fake.written["web"].active is active
    if name == "stop":
        assert store.get("web").changed_by == pagehandlers.BY


@pytest.mark.parametrize("extra, verb", [({}, "start"), ({"restart": False}, "start"),
                                         ({"restart": True}, "restart")])
def test_a_timers_run_now_and_run_again(fake, extra, verb):
    a_timer(enable=True)
    found = ended(accepted(act("run", "nightly", **extra)))
    assert (found["act"], found["state"]) == ("run", "done"), found
    assert (verb, "services", ("nightly",), False) in fake.calls


def test_a_timers_stop_ends_its_run(fake):
    a_timer(enable=True)
    assert ended(accepted(act("stop", "nightly")))["state"] == "done"
    assert ("stop", "services", ("nightly",), False) in fake.calls


@pytest.mark.parametrize("name, on", [("enable", True), ("disable", False)])
def test_enable_and_disable_of_a_row_of_his(fake, name, on):
    a_daemon(enable=not on)
    found = ended(accepted(act(name, "web")))
    assert (found["act"], found["state"]) == (name, "done"), found
    assert store.get("web").enabled is on and fake.written["web"].enabled is on


def test_remove_of_a_row_of_his():
    a_daemon()
    found = ended(accepted(act("remove", "web")))
    assert (found["act"], found["state"]) == ("remove", "done"), found
    assert store.get("web") is None


def test_an_act_the_verb_refuses_ends_refused(fake):
    a_daemon()
    fake.supervisor = None
    found = ended(accepted(act("start", "web")))
    assert (found["state"], found["code"]) == ("refused", 3)


def test_an_act_on_no_row_is_refused_before_any_job():
    assert "no service is named gone" in refused(act("stop", "gone"))
    assert pagehandlers.JOBS.get(JOB) is None


# -- the job id -----------------------------------------------------------------------------------

def test_a_repeated_job_gets_the_same_link_and_acts_nothing(fake):
    a_daemon(enable=True)
    job = accepted(act("stop", "web"))
    ended(job)
    assert accepted(act("stop", "services/web")) == job == JOB
    assert len([c for c in fake.calls if c[0] == "stop"]) == 1


def test_a_job_reused_for_another_act_or_row_is_refused(fake):
    a_daemon(enable=True)
    a_daemon("api", enable=True)
    ended(accepted(act("stop", "web")))
    assert refused(act("start", "web")) == pagehandlers.KEY_REUSED
    assert refused(act("stop", "api")) == pagehandlers.KEY_REUSED
    assert len([c for c in fake.calls if c[0] == "stop"]) == 1


@pytest.mark.parametrize("job", ["0123", "g" * 32, "0" * 31 + "-"])
def test_a_job_that_isnt_32_hex_characters_is_refused(job):
    a_daemon()
    assert refused(act("start", "web", job=job)) == pagehandlers.JOB_SHAPE


def test_a_job_longer_than_32_is_refused_by_its_shape():
    a_daemon()
    status, _ = act("start", "web", job="0" * 33)
    assert status == 400 and pagehandlers.JOBS.get("0" * 33) is None


# -- what an act takes -----------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["start", "restart"])
def test_a_timers_start_and_restart_go_through_run(name):
    a_timer(enable=True)
    assert refused(act(name, "nightly")) == pagehandlers.TIMER_RUNS
    assert pagehandlers.JOBS.get(JOB) is None


def test_run_refuses_a_daemon():
    a_daemon()
    assert refused(act("run", "web")) == pagehandlers.DAEMON_STARTS


@pytest.mark.parametrize("name", ["start", "stop", "restart", "enable", "disable",
                                  "remove"])
def test_restart_goes_with_run_only(name):
    a_daemon()
    assert refused(act(name, "web", restart=True)) == pagehandlers.RESTART_RUN_ONLY


@pytest.mark.parametrize("name", ["start", "run", "enable", "disable", "remove"])
@pytest.mark.parametrize("field", [{"ends": 0}, {"ends_unread": True}])
def test_ends_goes_with_stop_and_restart_only(name, field):
    a_daemon()
    assert refused(act(name, "web", **field)) == pagehandlers.ENDS_STOP_ONLY


def test_an_act_it_doesnt_know_is_refused_by_its_shape():
    a_daemon()
    status, _ = act("wipe", "web")
    assert status == 400 and pagehandlers.JOBS.get(JOB) is None


# -- a Moxi row's on and off -----------------------------------------------------------------------

@pytest.mark.parametrize("name", ["enable", "disable"])
@pytest.mark.parametrize("route", ["/api/act", "confirmed"])
def test_enable_and_disable_of_a_moxi_row_are_refused_on_every_route(fake, notes, name,
                                                                     route):
    if route == "/api/act":
        answer = act(name, notes)
    else:
        answer = post(f"/api/{name}", {"id": notes}, key=KEY)
    assert refused(answer) == "Its on and off is in Settings › notes."
    assert pagehandlers.JOBS.get(JOB) is None and pagehandlers.JOBS.get(KEY) is None
    assert not [c for c in fake.calls if c[0] in ("enable", "disable", "plan", "apply")]


def test_a_moxi_rows_stop_and_start_still_go(fake, notes):
    assert ended(accepted(act("stop", notes)))["state"] == "done"
    assert ended(accepted(act("start", notes, job=OTHER_JOB)))["state"] == "done"
