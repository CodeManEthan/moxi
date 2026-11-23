"""services' page and slots' server: `/api/ends`, what a stop or restart of the server
would end, read through a fake slots API, and the guard on its Stop and Restart, on
`/api/stop`, `/api/restart` and `/api/act`.

The fake slots module (`fake_slots`) sits in the test's site, so services loads it as it
loads the real one, and its `server` daemon is a row. No test reaches a supervisor, a slot
or tmux.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from moxi_services import jobs, page, pagehandlers

from fake_slots import FakeSlots, Job, Slot, install
from fake_units import Unit
from page_kit import LB, send, served

KEY = "K" * 43
ACT_JOB = "a" * 32
SERVER = "slots/server"


@pytest.fixture(autouse=True)
def fresh_page(monkeypatch, site, fake):
    table = jobs.Jobs(LB.busy)
    monkeypatch.setattr(pagehandlers, "JOBS", table)
    monkeypatch.setattr(pagehandlers, "_known", {})
    monkeypatch.setattr(pagehandlers, "_streaks", {})
    monkeypatch.setattr(pagehandlers, "_logged", set())
    yield fake
    table.close()


@pytest.fixture
def slots(site, fake):
    """slots installed, its server running, nothing in its slots."""
    fake.modules[("slots", "server")] = Unit("daemon", enabled=True, active=True)
    return install(site, FakeSlots())


def get(path: str, query: str = "") -> tuple[int, dict | None]:
    return asyncio.run(send(served(), "GET", path, query=query))


def post(route: str, body: dict, *, key: str = KEY) -> tuple[int, dict | None]:
    """A press on `route`: a confirmed route's path, or `act:<act>` for `/api/act`."""
    if route.startswith("act:"):
        body = {"act": route.removeprefix("act:"), "job": ACT_JOB, **body}
        return asyncio.run(send(served(), "POST", "/api/act", body=body))
    return asyncio.run(send(served(), "POST", route, body=body, key=key))


def job_of(route: str) -> str:
    return ACT_JOB if route.startswith("act:") else KEY


def ends() -> dict:
    status, body = get("/api/ends", f"id={SERVER}")
    assert status == 200, body
    return body


def ended(job: str, within: float = 10.0) -> dict:
    deadline = time.monotonic() + within
    while True:
        status, found = get(page.STATUS_ROUTE, f"job={job}")
        assert status == 200, found
        if found["state"] != "running":
            return found
        assert time.monotonic() < deadline, found
        time.sleep(0.01)


def acted(fake, act: str) -> list:
    return [c for c in fake.calls if c[0] == act and c[1] == "slots"]


# -- /api/ends --------------------------------------------------------------------------------

def test_ends_reads_nothing_in_use_when_every_slot_is_free(slots):
    slots.slots = [Slot(1), Slot(2, state="ended")]
    assert ends() == {"read": True, "why": None, "sessions": [], "open": 0, "jobs": 0}


def test_ends_counts_sessions_open_slots_and_jobs_as_slots_counts_them(slots):
    slots.slots = [Slot(1, state="occupied", idle=False, label="fix the tests"),
                   Slot(2, state="occupied", idle=True),
                   Slot(3, state="free", idle=False),
                   Slot(4, state="ended", idle=False),
                   Slot(5, state="missing", idle=False),
                   Slot(6, state="free")]
    # A live slot session with no registry row counts as slots' own check counts it.
    slots.unregistered = [Slot(9, state="occupied", label="stray"), Slot(10, idle=False)]
    slots.running = [Job("build"), Job("done", running=False), Job("lint")]
    assert ends() == {"read": True, "why": None,
                      "sessions": [{"slot": 1, "label": "fix the tests"},
                                   {"slot": 2, "label": None},
                                   {"slot": 9, "label": "stray"}],
                      "open": 3, "jobs": 2}


def test_ends_says_why_when_the_listing_could_not_be_read(slots):
    slots.ok, slots.why = False, "tmux did not answer"
    assert ends() == {"read": False, "why": "slots couldn't be read: tmux did not answer",
                      "sessions": None, "open": None, "jobs": None}
    assert slots.calls == ["list_slots"]


def test_ends_says_why_when_the_jobs_could_not_be_read(slots):
    slots.slots = [Slot(1, state="occupied", label="x")]

    class TmuxError(Exception):
        pass

    slots.jobs_raise = TmuxError("no server")
    found = ends()
    assert found == {"read": False, "why": "slots' jobs couldn't be read (TmuxError)",
                     "sessions": None, "open": None, "jobs": None}


def test_ends_says_so_when_slots_does_not_answer_in_time(slots, monkeypatch):
    monkeypatch.setattr(pagehandlers, "STATUS_SECONDS", 0.2)
    slots.hold = threading.Event()
    try:
        started = time.monotonic()
        found = ends()
        assert time.monotonic() - started < 5
    finally:
        slots.hold.set()
    assert found == {"read": False, "why": "slots did not answer within 0.2 s",
                     "sessions": None, "open": None, "jobs": None}


def test_ends_says_why_when_slots_api_does_not_load(site, fake):
    # Installed, but offering no interface services speaks: the server is a row, and
    # its API is not read.
    fake.modules[("slots", "server")] = Unit("daemon", enabled=True, active=True)
    site.module("slots", 'SUMMARY = "slots"\n'
                         'DAEMONS = {"server": {"command": "moxi-slots-server"}}\n')
    found = ends()
    assert found["read"] is False and found["why"].startswith(
        "slots' API couldn't be loaded: ")
    assert (found["sessions"], found["open"], found["jobs"]) == (None, None, None)


def test_an_unread_count_is_not_a_number():
    # A read that didn't answer is never nothing in use: its count is not known.
    unread = pagehandlers._unread("slots couldn't be read: x")
    assert pagehandlers.in_use(unread) is None
    assert pagehandlers.in_use(
        {"read": True, "sessions": [], "open": 0, "jobs": 0}) == 0


def test_ends_without_slots_installed_names_no_row(monkeypatch):
    import moxi.modules

    real = moxi.modules.load

    def load(name, **kwargs):
        # The workspace installs slots; here it is absent, as a module never installed.
        return real("absentmodule" if name == "slots" else name, **kwargs)

    monkeypatch.setattr(moxi.modules, "load", load)
    status, body = get("/api/ends", f"id={SERVER}")
    assert status == 422 and "no daemon or timer of an installed module" in body["refused"]


def test_ends_refuses_any_row_but_slots_server(site, slots, fake):
    site.module("notes", 'SUMMARY = "notes"\n'
                         'DAEMONS = {"sync": {"command": "moxi-notes-sync"}}\n')
    fake.modules[("notes", "sync")] = Unit("daemon", enabled=True, active=True)
    assert get("/api/ends", "id=notes/sync") == (
        422, {"refused": pagehandlers.ENDS_SLOTS_ONLY})
    assert slots.calls == []


def test_the_rows_mark_slots_server_as_the_one_that_ends_the_slots(slots):
    status, listed = get("/api/rows")
    assert status == 200, listed
    assert [(r["id"], r["ends_slots"]) for r in listed["rows"]] == [(SERVER, True)]


# -- the guard on Stop and Restart --------------------------------------------------------------

@pytest.fixture(params=["/api/stop", "/api/restart", "act:stop", "act:restart"])
def route(request):
    return request.param


def _act(route: str) -> str:
    return route.rsplit("/", 1)[-1].removeprefix("act:")


def test_a_plain_stop_goes_when_nothing_is_in_use(slots, fake, route):
    status, body = post(route, {"id": SERVER})
    assert status == 202, body
    assert ended(job_of(route))["state"] == "done"
    assert acted(fake, _act(route))
    assert slots.calls == ["list_slots", "jobs"]


@pytest.mark.parametrize("busy", ["session", "open", "job"])
def test_a_plain_stop_is_refused_while_something_is_in_use(slots, fake, route, busy):
    if busy == "session":
        slots.slots = [Slot(1, state="occupied", label="x")]
    elif busy == "open":
        slots.slots = [Slot(1, idle=False)]
    else:
        slots.running = [Job("build")]
    assert post(route, {"id": SERVER}) == (422, {"refused": pagehandlers.SLOTS_IN_USE})
    assert not acted(fake, _act(route))
    assert get(page.STATUS_ROUTE, f"job={job_of(route)}")[1]["state"] == "unknown"


@pytest.mark.parametrize("shown", [3, 4])
def test_a_stop_goes_when_ends_covers_what_is_in_use(slots, fake, route, shown):
    slots.slots = [Slot(1, state="occupied", label="a"), Slot(2, state="occupied")]
    slots.running = [Job("build")]
    status, body = post(route, {"id": SERVER, "ends": shown})
    assert status == 202, body
    assert ended(job_of(route))["state"] == "done"
    assert acted(fake, _act(route))


def test_a_stop_is_refused_when_more_is_in_use_than_was_shown(slots, fake, route):
    slots.slots = [Slot(1, state="occupied", label="a"), Slot(2, state="occupied")]
    slots.running = [Job("build")]
    assert post(route, {"id": SERVER, "ends": 2}) == (
        422, {"refused": pagehandlers.SLOTS_IN_USE})
    assert not acted(fake, _act(route))


def test_a_stop_goes_unread_when_the_unread_warning_was_confirmed(slots, fake, route):
    slots.ok, slots.why = False, "tmux did not answer"
    status, body = post(route, {"id": SERVER, "ends_unread": True})
    assert status == 202, body
    assert ended(job_of(route))["state"] == "done"


def test_ends_zero_is_nothing_in_use_never_the_unread_confirm(slots, fake, route):
    # 0 means "nothing in use, as read": it goes past neither an unread read nor one that
    # finds something in use.
    slots.ok = False
    assert post(route, {"id": SERVER, "ends": 0}) == (
        422, {"refused": pagehandlers.SLOTS_UNREAD})
    slots.ok = True
    slots.running = [Job("build")]
    assert post(route, {"id": SERVER, "ends": 0}) == (
        422, {"refused": pagehandlers.SLOTS_IN_USE})
    assert not acted(fake, _act(route))


def test_the_unread_confirm_does_not_cover_a_count_read_since(slots, fake, route):
    # He confirmed the slots couldn't be read; they now read with something in use, which
    # he was never shown.
    slots.slots = [Slot(1, state="occupied", label="a")]
    assert post(route, {"id": SERVER, "ends_unread": True}) == (
        422, {"refused": pagehandlers.SLOTS_IN_USE})
    slots.slots = []
    status, body = post(route, {"id": SERVER, "ends_unread": True})
    assert status == 202, body


def test_a_stop_is_refused_when_it_confirms_both_a_count_and_unread(slots, fake, route):
    assert post(route, {"id": SERVER, "ends": 2, "ends_unread": True}) == (
        422, {"refused": pagehandlers.ENDS_ONE_CONFIRM})
    assert slots.calls == []


@pytest.mark.parametrize("unread", ["listing", "jobs", "slow"])
def test_a_plain_stop_is_refused_when_the_slots_could_not_be_read(
        slots, fake, route, unread, monkeypatch):
    # An unread count may be any number: a plain press is told so, never let through as
    # though nothing were in use.
    if unread == "listing":
        slots.ok = False
    elif unread == "jobs":
        slots.jobs_raise = RuntimeError("no server")
    else:
        monkeypatch.setattr(pagehandlers, "STATUS_SECONDS", 0.2)
        slots.hold = threading.Event()
    try:
        assert post(route, {"id": SERVER}) == (
            422, {"refused": pagehandlers.SLOTS_UNREAD})
    finally:
        if slots.hold is not None:
            slots.hold.set()
    assert not acted(fake, _act(route))


@pytest.mark.parametrize("shown", [1, 2])
def test_a_stop_shown_a_count_is_refused_when_the_slots_can_no_longer_be_read(
        slots, fake, route, shown):
    # The count he confirmed is no longer the one in use: an unread count equals none,
    # so he is asked again with the unread warning.
    slots.ok = False
    assert post(route, {"id": SERVER, "ends": shown}) == (
        422, {"refused": pagehandlers.SLOTS_UNREAD})
    assert not acted(fake, _act(route))
    assert get(page.STATUS_ROUTE, f"job={job_of(route)}")[1]["state"] == "unknown"


def test_a_stop_refused_unread_goes_once_the_unread_warning_is_confirmed(slots, fake, route):
    slots.ok = False
    assert post(route, {"id": SERVER, "ends": 2})[0] == 422
    status, body = post(route, {"id": SERVER, "ends_unread": True})
    assert status == 202, body
    assert ended(job_of(route))["state"] == "done"
    assert len(acted(fake, _act(route))) == 1


def test_a_stop_is_refused_when_its_ends_is_below_zero(slots, fake, route):
    assert post(route, {"id": SERVER, "ends": -1}) == (
        422, {"refused": pagehandlers.ENDS_NEGATIVE})
    assert slots.calls == []


def test_another_rows_stop_reads_no_slots(site, slots, fake, route):
    slots.running = [Job("build")]
    site.module("notes", 'SUMMARY = "notes"\n'
                         'DAEMONS = {"sync": {"command": "moxi-notes-sync"}}\n')
    fake.modules[("notes", "sync")] = Unit("daemon", enabled=True, active=True)
    status, body = post(route, {"id": "notes/sync"})
    assert status == 202, body
    assert ended(job_of(route))["state"] == "done"
    assert slots.calls == []


def test_a_repeated_key_gets_its_link_without_reading_the_slots_again(slots, fake, route):
    assert post(route, {"id": SERVER})[0] == 202
    ended(job_of(route))
    slots.running = [Job("build")]
    calls = len(slots.calls)
    status, body = post(route, {"id": SERVER})
    assert status == 202, body
    assert len(slots.calls) == calls
    assert len(acted(fake, _act(route))) == 1
