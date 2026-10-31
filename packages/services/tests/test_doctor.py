"""services' doctor rows: the store, each user row's verdict, the project keys, and the
time they take whatever the supervisor and the health checks do."""

from __future__ import annotations

import sys
import threading
import time

import pytest

from moxi_services import doctor, projects_link, state, store
from moxi_services.errors import Busy

from fake_units import Unit
from services_kit import add, written

PY = sys.executable


def by_name(found):
    return {row.name: row for row in found}


def test_no_store_is_ok_and_creates_nothing(fake, store_path):
    found = list(doctor.rows())
    assert [(r.name, r.state) for r in found] == [("store", "ok")]
    assert not store_path.exists()


def test_a_store_newer_than_the_code_fails(fake, too_new):
    found = list(doctor.rows())
    assert [(r.name, r.state) for r in found] == [("store", "fail")]
    assert "newer than" in found[0].detail and "moxi install services" in found[0].fix


def test_an_unreadable_store_fails(fake, store_path):
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_bytes(b"this is no database, not even close" * 100)
    found = list(doctor.rows())
    assert [(r.name, r.state) for r in found] == [("store", "fail")]


def test_a_busy_store_warns(fake, monkeypatch):
    def busy(*a, **k):
        raise Busy()

    monkeypatch.setattr(store, "read_rows", busy)
    assert [(r.name, r.state) for r in doctor.rows()] == [("store", "warn")]


def test_each_user_row_is_its_verdict(fake):
    add(**written("web", enabled=True))
    add(**written("api", enabled=True))
    fake.written["web"] = Unit("daemon", True, active=True)
    found = by_name(doctor.rows())
    assert found["store"].state == "ok" and "2 rows" in found["store"].detail
    assert found["services/web"].state == "ok"
    api = found["services/api"]
    assert (api.state, api.detail, api.fix) == (
        "fail", "its unit is missing", "moxi services enable api")
    assert all(row.module == "services" for row in found.values())


def test_a_failing_health_check_fails_the_row(fake):
    add(**written("web", enabled=True, health=[PY, "-c", "print('503'); exit(1)"]))
    fake.written["web"] = Unit("daemon", True, active=True)
    row = by_name(doctor.rows())["services/web"]
    assert (row.state, row.detail) == ("fail", "health check failed: 503")


def test_the_limits_fit_inside_the_doctors():
    assert doctor.STATUS_SECONDS + state.HEALTH_SECONDS <= 8


def test_a_status_call_that_never_returns_leaves_every_row_unknown(fake, monkeypatch):
    for name in ("a", "b", "c"):
        add(**written(name, enabled=True, health=[PY, "-c", "pass"]))
    stuck = threading.Event()
    monkeypatch.setattr(fake, "status", lambda *a, **k: stuck.wait(30) and [])
    monkeypatch.setattr(doctor, "STATUS_SECONDS", 0.3)
    ran = []
    monkeypatch.setattr(state, "run_health", lambda argv: ran.append(argv) or ("ok", ""))
    began = time.monotonic()
    found = by_name(doctor.rows())
    took = time.monotonic() - began
    stuck.set()
    assert took < 2
    for name in ("a", "b", "c"):
        row = found[f"services/{name}"]
        assert (row.state, row.detail) == (
            "warn", "unknown: the supervisor did not answer in 0.3 s")
    assert ran == []


def test_wedged_health_commands_run_at_once_each_under_its_limit(fake, monkeypatch):
    monkeypatch.setattr(state, "HEALTH_SECONDS", 1)
    names = [f"d{i}" for i in range(12)]
    for name in names:
        add(**written(name, enabled=True, health=[PY, "-c", "import time; time.sleep(60)"]))
        fake.written[name] = Unit("daemon", True, active=True)
    began = time.monotonic()
    found = by_name(doctor.rows())
    took = time.monotonic() - began
    assert took < 6, took  # twelve one-second checks, side by side
    for name in names:
        row = found[f"services/{name}"]
        assert row.state == "fail" and "did not finish in 1 s" in row.detail


# --- project keys -----------------------------------------------------------------------

def test_project_keys_are_off_while_projects_is_absent(fake, monkeypatch):
    add(**written("web", project="studio"))
    add(**written("api", project="studio"))
    monkeypatch.setattr(projects_link, "known_keys", lambda: None)
    row = by_name(doctor.rows())["project:studio"]
    assert (row.state, row.detail) == ("off", "projects is not installed; api, web keep "
                                              "the key")


def test_a_key_projects_does_not_have_warns(fake, monkeypatch):
    add(**written("web", project="Studio"))
    add(**written("api", project="gone"))
    monkeypatch.setattr(projects_link, "known_keys", lambda: frozenset({"studio"}))
    found = by_name(doctor.rows())
    assert "project:Studio" not in found
    assert found["project:gone"].state == "warn"
    assert "moxi projects add gone" in found["project:gone"].fix


def test_projects_unreadable_warns_once(fake, monkeypatch):
    add(**written("web", project="studio"))

    def busy():
        raise Busy("projects' store stayed locked")

    monkeypatch.setattr(projects_link, "known_keys", busy)
    row = by_name(doctor.rows())["project keys"]
    assert row.state == "warn" and "stayed locked" in row.detail


def test_rows_with_no_project_add_no_project_row(fake):
    add(**written("web"))
    assert not [r for r in doctor.rows() if r.name.startswith("project")]


@pytest.mark.parametrize("state_", ["ok", "warn", "fail", "off"])
def test_every_row_state_is_one_the_doctor_reads(state_):
    from moxi.doctor import STATES

    assert state_ in STATES
