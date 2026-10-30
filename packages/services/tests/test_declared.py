"""Written rows as run-time declarations: the mapping field by field, what is left out,
each read failure raising, and the core reading them through `UNITS_FROM`."""

from __future__ import annotations

import sqlite3
import sys

import pytest

import moxi.units
from moxi_services import store, units
from moxi_services.errors import Busy, TooNew, Unreadable

from services_kit import add, adopted, written

OTHER = "darwin" if sys.platform != "darwin" else "linux"


def test_no_store_declares_nothing(store_path):
    assert units.declared() == {}
    assert not store_path.exists()


def test_a_store_with_no_written_row_declares_nothing():
    add(**adopted())
    assert units.declared() == {}


def test_a_daemon_maps_field_by_field():
    add(**written("web", cwd="/srv/web", doc="the site", enabled=True,
                  health=["/usr/bin/true"], project="portfolio", launches_session=True))
    assert units.declared() == {"daemons": {"web": {
        "argv": ["/usr/bin/python3", "-m", "http.server"],
        "cwd": "/srv/web",
        "platforms": [sys.platform],
        "stop": "group",
        "upgrade": "keep",
        "doc": "the site",
    }}}


def test_a_flag_that_is_off_declares_enabled_false_and_no_doc_when_empty():
    add(**written("web"))
    entry = units.declared()["daemons"]["web"]
    assert entry["enabled"] is False
    assert "doc" not in entry and "cwd" not in entry


def test_timers_carry_their_schedule_and_no_switch():
    add(**written("every", kind="timer", every="15m", enabled=True))
    add(**written("at", kind="timer", at=["02:30"]))
    add(**written("days", kind="timer", at=["09:00", "17:00"], days=["mon", "fri"],
                  enabled=True))
    timers = units.declared()["timers"]
    assert timers["every"] == {"argv": ["/usr/bin/python3", "-m", "http.server"],
                               "every": "15m", "platforms": [sys.platform]}
    assert timers["at"] == {"argv": ["/usr/bin/python3", "-m", "http.server"],
                            "at": ["02:30"], "enabled": False,
                            "platforms": [sys.platform]}
    assert timers["days"]["at"] == ["09:00", "17:00"]
    assert timers["days"]["days"] == ["mon", "fri"]
    for entry in timers.values():
        assert "switch" not in entry and "stop" not in entry and "upgrade" not in entry


def test_a_row_being_removed_and_adopted_rows_are_left_out():
    add(**written("web"))
    add(**written("gone"))
    add(**adopted())
    store.write(lambda conn: store.set_removing(conn, "gone", by="test"))
    assert set(units.declared()["daemons"]) == {"web"}


def test_a_row_of_another_system_names_that_system():
    add(**written("web", platform=OTHER))
    assert units.declared()["daemons"]["web"]["platforms"] == [OTHER]


# --- failures raise ---------------------------------------------------------------------

def test_a_store_too_new_raises(too_new):
    with pytest.raises(TooNew):
        units.declared()


def test_an_unreadable_store_raises(store_path):
    store_path.parent.mkdir(parents=True)
    store_path.write_bytes(b"not a database at all" * 100)
    with pytest.raises(Unreadable):
        units.declared()


def test_a_store_not_set_up_raises_rather_than_declaring_nothing(store_path):
    store_path.parent.mkdir(parents=True)
    store_path.write_bytes(b"")
    with pytest.raises(Unreadable):
        units.declared()


def test_a_busy_store_raises(store_path, monkeypatch):
    add(**written())
    monkeypatch.setattr(store, "TIMEOUT", 0.05)
    holder = sqlite3.connect(store_path, isolation_level=None)
    holder.execute("PRAGMA locking_mode = EXCLUSIVE")
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(Busy):
            units.declared()
    finally:
        holder.execute("ROLLBACK")
        holder.close()


# --- through the core -------------------------------------------------------------------

def test_the_core_reads_written_rows_through_units_from():
    add(**written("web", enabled=True, cwd="/srv/web", doc="the site"))
    add(**written("nightly", kind="timer", at=["02:30"], days=["mon"]))
    add(**written("often", kind="timer", every="5m", platform=OTHER))
    found = moxi.units.declarations("services")
    assert found.rejected == {}
    assert set(found.daemons) == {"web"}
    assert set(found.timers) == {"nightly", "often"}
    assert list(found.daemons["web"]["argv"]) == ["/usr/bin/python3", "-m", "http.server"]
    assert found.daemons["web"]["upgrade"] == "keep"


def test_the_core_sees_a_failed_read_as_a_declaration_error(too_new):
    with pytest.raises(moxi.units.DeclarationError) as caught:
        moxi.units.declarations("services")
    assert caught.value.module == "services"
    assert "TooNew" in caught.value.text
