"""The announcement, as the core reads it: the launcher loads present, with the names it
declares."""

from __future__ import annotations

from importlib import metadata

import pytest

from moxi import modules
from moxi.paths import Layout

from moxi_launcher import module


@pytest.fixture
def found():
    modules.reset()
    yield modules.load("launcher")
    modules.reset()


def test_the_launcher_loads_present(found):
    assert found.state == modules.PRESENT, found.reason
    assert found.cli == "moxi_launcher.cli:main"
    assert found.settings == "settings.toml"
    assert found.announces == 1


def test_it_declares_its_optional_links_as_extras(found):
    assert set(found.optional) == {"turns", "comms", "slots", "projects"}
    extras = set(metadata.distribution("moxi-launcher").metadata.get_all("Provides-Extra"))
    assert set(found.optional) == extras


def test_it_reserves_the_console_name(found):
    assert found.reserved_names == ("console",)


def test_it_ships_its_skills_folder(found):
    assert found.skills == "skills"


def test_its_doctor_target_answers_with_rows(found):
    from moxi_launcher import doctor

    assert found.doctor == "moxi_launcher.doctor:rows"
    rows = list(doctor.rows(read=lambda argv: None))
    assert [(row.name, row.state) for row in rows] == [("catalog.claude", "off"),
                                                       ("catalog.codex", "off")]


def test_session_env_names_the_exact_stamps_not_the_prefix(found):
    assert set(found.session_env) == {"MOXI_LAUNCHER_REQUEST", "MOXI_LAUNCHER_RELAUNCH",
                                     "MOXI_LAUNCHER_KEEP_WARM",
                                     "MOXI_LAUNCHER_RECYCLE_ON_EXPIRY",
                                     "MOXI_USAGE_CROSSING_AT", "MOXI_USAGE_WARN_AT"}
    assert not any(name.endswith("_") for name in found.session_env)
    assert {"MOXI_LAUNCHER_REQUEST", "MOXI_LAUNCHER_RELAUNCH"} <= modules.session_env()


def test_the_stamps_it_reads_are_declared_internal(found):
    own = {name for name in found.session_env if name.startswith("MOXI_LAUNCHER_")}
    assert own <= set(found.internal_env)


def test_its_stores_are_the_launch_log_runs_and_handoffs(found):
    stores = found.stores
    assert set(stores) == {"launches", "runs", "handoffs"}
    assert (stores["launches"]["root"], stores["launches"]["path"],
            stores["launches"]["kind"]) == ("state", "launcher.db", "sqlite")
    assert (stores["runs"]["root"], stores["runs"]["path"],
            stores["runs"]["kind"]) == ("state", "runs", "dir")
    assert (stores["handoffs"]["root"], stores["handoffs"]["path"],
            stores["handoffs"]["kind"]) == ("data", "handoffs", "dir")
    assert all(entry["private"] for entry in stores.values())
    assert stores["runs"]["rebuildable"] is True
    assert stores["launches"]["rebuildable"] is stores["handoffs"]["rebuildable"] is False


def test_the_store_roots_sit_under_the_launchers_own_layout():
    layout = Layout("launcher")
    roots = {"state": layout.state_dir(), "data": layout.data_dir()}
    for entry in module.STORES.values():
        assert roots[entry["root"]].name == "launcher"


def test_the_run_files_declared_age_is_the_keep_days_default():
    import tomllib
    from importlib import resources

    shipped = tomllib.loads(resources.files("moxi_launcher").joinpath(
        "settings.toml").read_text())
    days = shipped["runs"]["keep_days"]["default"]
    assert module.STORES["runs"]["retention"] == f"{days}d"
