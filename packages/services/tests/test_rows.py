"""Names, ids, foreign unit names, schedules, and the module rows read from declarations."""

from __future__ import annotations

import pytest

from moxi_services import rows
from moxi_services.errors import Refused, Usage


# --- names and ids ----------------------------------------------------------------------

@pytest.mark.parametrize("name", ["web", "portfolio-web", "a1", "x-2-y"])
def test_a_valid_name(name):
    assert rows.check_name(name) == name


@pytest.mark.parametrize("name", ["", "Web", "web_1", "-web", "web-", "a--b", "a.b",
                                  "services/web", None, 3])
def test_an_invalid_name_is_a_usage_error(name):
    with pytest.raises(Usage) as caught:
        rows.check_name(name)
    assert caught.value.exit_code == 2


@pytest.mark.parametrize("text,parsed", [
    ("web", ("services", "web")),
    ("services/web", ("services", "web")),
    ("notes/snapshot", ("notes", "snapshot")),
    ("comms/daemon", ("comms", "daemon")),
])
def test_ids_name_user_rows_and_module_rows(text, parsed):
    assert rows.parse_id(text) == parsed
    assert rows.row_id(*parsed) == (text if "/" in text else f"services/{text}")


@pytest.mark.parametrize("text", ["", "/web", "web/", "a/b/c", "Notes/x", "notes/X",
                                  "no-tes/x", None])
def test_a_malformed_id_is_refused_as_an_unknown_id(text):
    with pytest.raises(Refused) as caught:
        rows.parse_id(text)
    assert caught.value.exit_code == 3
    assert "moxi services list" in caught.value.fix


def test_is_user_names_only_the_services_half():
    assert rows.is_user("services") and not rows.is_user("notes")


# --- foreign units ----------------------------------------------------------------------

@pytest.mark.parametrize("ref,backend,timer,kind", [
    ("backup.timer", "systemd", False, "timer"),
    ("backup.timer", "systemd", True, "timer"),
    ("web.service", "systemd", False, "daemon"),
    ("com.example.web", "launchd", False, "daemon"),
    ("com.example.nightly", "launchd", True, "timer"),
    ("\\Backups\\nightly", "schtasks", False, "timer"),
])
def test_a_foreign_unit_takes_its_kind(ref, backend, timer, kind):
    assert rows.check_ref(ref, backend, timer=timer) == kind


@pytest.mark.parametrize("ref,backend", [
    ("moxi-notes-snapshot.timer", "systemd"),
    ("moxi-services-web.service", "systemd"),
    ("moxi.services.web", "launchd"),
    ("\\Moxi\\services-web", "schtasks"),
    ("\\moxi\\notes-snapshot", "schtasks"),
    ("Moxi\\x", "schtasks"),
])
def test_a_unit_in_moxis_own_namespace_is_refused(ref, backend):
    with pytest.raises(Refused) as caught:
        rows.check_ref(ref, backend)
    assert caught.value.exit_code == 3
    assert "own unit namespace" in caught.value.reason


def test_a_systemd_unit_of_another_type_is_refused():
    with pytest.raises(Refused):
        rows.check_ref("web.socket", "systemd")


def test_timer_on_a_systemd_service_is_a_usage_error():
    with pytest.raises(Usage):
        rows.check_ref("web.service", "systemd", timer=True)


@pytest.mark.parametrize("ref", ["", " web.service", "a\nb", None])
def test_an_empty_or_odd_ref_is_a_usage_error(ref):
    with pytest.raises(Usage):
        rows.check_ref(ref, "systemd")


def test_no_supervisor_refuses_adoption():
    with pytest.raises(Refused):
        rows.check_ref("web.service", None)


# --- schedules --------------------------------------------------------------------------

@pytest.mark.parametrize("schedule", [
    {"every": "1m"}, {"every": "24h"}, {"every": "90m"}, {"at": ["00:00", "23:59"]},
    {"at": ["09:00"], "days": ["mon", "sun"]},
])
def test_a_schedule_in_the_cores_form(schedule):
    rows.check_schedule(**schedule)


@pytest.mark.parametrize("schedule", [
    {}, {"every": "5m", "at": ["09:00"]}, {"every": "0m"}, {"every": "25h"},
    {"every": "1441m"}, {"every": "5s"}, {"every": 5}, {"every": "5m", "days": ["mon"]},
    {"at": []}, {"at": ["24:00"]}, {"at": "09:00"}, {"at": ["09:00"], "days": []},
    {"at": ["09:00"], "days": ["Mon"]},
])
def test_a_schedule_out_of_form_is_a_usage_error(schedule):
    with pytest.raises(Usage):
        rows.check_schedule(**schedule)


# --- module rows ------------------------------------------------------------------------

NOTES = '''
SUMMARY = "notes"
SETTINGS = None
DAEMONS = {"sync": {"command": "moxi-notes-sync", "doc": "keep notes in sync",
                    "platforms": ["linux"]}}
TIMERS = {"snapshot": {"command": "moxi-notes-snapshot", "every": "5m",
                       "switch": "snapshot.enabled", "doc": "commit a snapshot"}}
'''

BROKEN_FROM = '''
SUMMARY = "late"
DAEMONS = {"steady": {"command": "moxi-late-steady"}}
UNITS_FROM = "moxi_late.more:declared"
'''

LATE_MORE = '''
def declared():
    raise RuntimeError("the table is gone")
'''

RUNTIME = '''
SUMMARY = "extra"
UNITS_FROM = "moxi_extra.more:declared"
'''

EXTRA_MORE = '''
def declared():
    return {"daemons": {"one": {"argv": ["/bin/true"]}, "bad": {"argv": ["true"]}}}
'''


def test_module_rows_list_each_modules_daemons_and_timers(site):
    site.module("notes", NOTES)
    site.module("quiet", 'SUMMARY = "nothing to run"\n')
    found, problems = rows.module_rows()
    assert problems == []
    assert [r.id for r in found] == ["notes/snapshot", "notes/sync"]
    snapshot, sync = found
    assert (snapshot.kind, snapshot.switch, snapshot.doc, snapshot.platforms) == (
        "timer", "snapshot.enabled", "commit a snapshot", None)
    assert (sync.kind, sync.switch, sync.platforms) == ("daemon", None, ("linux",))
    assert sync.form == "module"
    assert sync.as_dict()["id"] == "notes/sync"


def test_module_rows_include_run_time_entries_and_leave_out_rejected_ones(site):
    site.module("extra", RUNTIME)
    site.write("moxi_extra/more.py", EXTRA_MORE)
    found, problems = rows.module_rows()
    assert [r.id for r in found] == ["extra/one"]
    assert problems == []


def test_a_failing_units_from_keeps_the_static_rows_and_is_a_problem(site):
    site.module("late", BROKEN_FROM)
    site.write("moxi_late/more.py", LATE_MORE)
    found, problems = rows.module_rows()
    assert [r.id for r in found] == ["late/steady"]
    assert len(problems) == 1 and problems[0][0] == "late"
    assert "the table is gone" in problems[0][1]


def test_services_own_declarations_are_never_module_rows(site):
    from services_kit import add, written

    add(**written("web"))
    found, _ = rows.module_rows()
    assert all(r.module != "services" for r in found)
    found, _ = rows.module_rows(["services"])
    assert found == []


def test_a_module_that_wont_load_is_skipped(site):
    site.module("bad", "DAEMONS = 3\n")
    site.module("notes", NOTES)
    found, problems = rows.module_rows()
    assert {r.module for r in found} == {"notes"} and problems == []
