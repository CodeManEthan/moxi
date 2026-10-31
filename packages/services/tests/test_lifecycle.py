"""Install and uninstall: `SETUP` migrates an existing store and makes none; `TEARDOWN`
refuses while a service the user added has its unit here, and gives every adopted Linux
timer back as it was. Also the announcement as the core reads it."""

from __future__ import annotations

import sqlite3
import sys

import pytest

import moxi.config
import moxi.modules
from moxi_services import lifecycle, store

from fake_units import Unit
from services_kit import add, adopted, written

LINUX = pytest.mark.skipif(sys.platform != "linux", reason="adopted timers are wrapped on Linux")
OTHER = "darwin" if sys.platform != "darwin" else "linux"


# --- SETUP ------------------------------------------------------------------------------

def test_setup_makes_no_store(store_path):
    assert lifecycle.setup() == 0
    assert not store_path.exists()


def test_setup_migrates_an_existing_store(store_path):
    # A store left at version 0, as by a first write that died before setting it up.
    store_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(store_path)
    conn.execute("CREATE TABLE left_behind (x)")
    conn.commit()
    conn.close()
    assert lifecycle.setup() == 0
    conn = sqlite3.connect(store_path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.VERSION
    conn.close()


def test_setup_on_a_store_too_new_exits_3(too_new, capsys):
    assert lifecycle.setup() == 3
    assert "newer than" in capsys.readouterr().err


# --- TEARDOWN ---------------------------------------------------------------------------

def test_teardown_accepts_with_no_rows(fake):
    lifecycle.teardown()


def test_teardown_refuses_while_a_written_row_is_here(fake):
    add(**written("web"))
    add(**written("api"))
    with pytest.raises(moxi.modules.Refused) as refused:
        lifecycle.teardown()
    reason = refused.value.reason
    assert "moxi services remove api" in reason and "moxi services remove web" in reason
    assert store.get("web") is not None


def test_teardown_accepts_rows_with_no_unit_here(fake, monkeypatch):
    add(**written("web", platform=OTHER))
    add(**adopted("site", kind="daemon", unit="site.service"))
    lifecycle.teardown()
    assert store.get("web") is not None and store.get("site") is not None
    fake.supervisor = None
    add(**written("api"))
    lifecycle.teardown()


def test_teardown_refuses_on_an_unreadable_store(fake, too_new):
    with pytest.raises(moxi.modules.Refused) as refused:
        lifecycle.teardown()
    assert "newer than" in refused.value.reason


@LINUX
def test_teardown_gives_a_wrapped_timer_back(fake):
    fake.foreign["backup.timer"] = Unit("timer", True)
    from moxi_services import acts

    assert acts.add_adopted("backup", "backup.timer", enable=False).code == 0
    assert "backup.timer" in fake.wrapped
    lifecycle.teardown()
    assert "backup.timer" not in fake.wrapped
    assert store.get("backup") is None
    # Its key was off, so its schedule was disabled before the gate went.
    assert ("disable", "services", ("backup.timer",), True) in fake.acts()
    assert fake.foreign["backup.timer"].enabled is False


@LINUX
def test_teardown_refuses_on_a_wrapped_timer_it_cant_give_back(fake):
    fake.foreign["backup.timer"] = Unit("timer", True)
    from moxi_services import acts

    assert acts.add_adopted("backup", "backup.timer", enable=True).code == 0
    fake.unwrap_fails = True
    with pytest.raises(moxi.modules.Refused) as refused:
        lifecycle.teardown()
    assert "services/backup could not be removed" in refused.value.reason
    assert "moxi services remove backup" in refused.value.reason
    assert store.get("backup").removing


def test_teardown_leaves_other_adopted_units_alone(fake):
    fake.foreign["site.service"] = Unit("daemon", True, active=True)
    add(**adopted("site", kind="daemon", unit="site.service"))
    lifecycle.teardown()
    assert fake.acts() == [] and store.get("site") is not None


# --- the announcement -------------------------------------------------------------------

def test_the_core_reads_the_announcement():
    found = moxi.modules.load("services")
    assert found.state == moxi.modules.PRESENT, found.reason
    assert found.cli == "moxi_services.cli:main"
    assert found.setup == "moxi_services.lifecycle:setup"
    assert found.teardown == "moxi_services.lifecycle:teardown"
    assert found.doctor == "moxi_services.doctor:rows"
    assert set(found.stores) == {"registry"}
    entry = found.stores["registry"]
    assert entry["path"] == "services.db" and entry["root"] == "data"


def test_the_store_lives_where_stores_says():
    found = moxi.modules.load("services")
    assert store.path().endswith(found.stores["registry"]["path"])
