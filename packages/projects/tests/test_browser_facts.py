"""The Projects page's browser tests' setup, checked before any of them leans on it: the
fixture module's `project-facts`, placed in a shell through the client, reports what its
page server was told to say, for the projects it was given and the opened one's sections,
agents and Remove lines only; a failed read reports null and the next good one puts the
facts back; it reads only while placed; it keeps the component contract. The fixture
install holds the seven projects with their notes roots, quill-mobile's folder missing,
and the fixture, whose link with projects refuses a remove while a test holds it; the small
install holds core, ui and projects alone. See projects_page_kit.py.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

import pytest

import projects_page_kit as kit

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="projects' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

#: How long a test waits for the fixture to report, in seconds.
WAIT_S = 5.0

# Places the fixture's component in a hidden host on the shell's document, through the
# client as the page places it, and keeps every report it sends in `window.__reports`.
_PLACE = """async ([projects, open]) => {
  const client = await import("/m/ui/client.js");
  window.__reports = [];
  const host = document.createElement("div");
  host.hidden = true;
  document.body.append(host);
  host.addEventListener("moxi-project-facts", (event) => window.__reports.push(event.detail));
  const attrs = {projects: JSON.stringify(projects)};
  if (open !== null) attrs.open = open;
  const element = await client.place(host, "fixture", "project-facts", attrs);
  window.__facts = element;
  return element === null ? null : element.localName;
}"""

_SET = """([name, value]) => value === null ? window.__facts.removeAttribute(name)
                                    : window.__facts.setAttribute(name, value)"""


def _place(page, projects: list[dict[str, Any]], open_key: str | None) -> None:
    assert page.evaluate(_PLACE, [projects, open_key]) == "moxi-fixture-project-facts"


def _said(report: Any, keys: set[str], open_key: str | None) -> Any:
    """What the fixture's component says of `report` with the projects `keys` given and
    `open_key` opened."""
    if not isinstance(report, dict) or not isinstance(report.get("projects"), dict):
        return report
    projects = {}
    for key, facts in report["projects"].items():
        if key not in keys:
            continue
        projects[key] = facts if key == open_key else {
            name: value for name, value in facts.items()
            if name not in ("sections", "agents", "stays")}
    return {**report, "projects": projects}


def _reported(page, expected: Any) -> None:
    """Wait until the fixture's last report is `expected`."""
    deadline = time.monotonic() + WAIT_S
    last = None
    while time.monotonic() < deadline:
        reports = page.evaluate("() => window.__reports")
        if reports:
            last = reports[-1]
            if last == expected:
                return
        page.wait_for_timeout(50)
    raise AssertionError(f"the fixture's last report is {last!r}, not {expected!r}")


@pytest.fixture
def fixture_page(fixture_shell, ui_contexts):
    kit.reset(fixture_shell)
    yield ui_contexts(fixture_shell).pages[0]
    kit.reset(fixture_shell)


# --- the fixture's component ---------------------------------------------------------------

def test_the_fixture_reports_what_it_was_told_for_the_given_projects_and_the_open_one(
        fixture_shell, fixture_page):
    given = kit.given(fixture_shell)
    keys = {row.key for row in kit.SEVEN}
    _place(fixture_page, given, "atlas-api")
    _reported(fixture_page, None)
    report = kit.full_report(fixture_shell)
    kit.say(fixture_shell, report)
    _reported(fixture_page, _said(report, keys, "atlas-api"))
    last = fixture_page.evaluate("() => window.__reports.at(-1)")
    assert set(last["projects"]) == keys - {kit.MISSING}
    assert "sections" in last["projects"]["atlas-api"]
    assert "sections" not in last["projects"]["kestrel-web"]
    # Another project opened: its sections, agents and Remove lines go with it.
    fixture_page.evaluate(_SET, ["open", "kestrel-web"])
    _reported(fixture_page, _said(report, keys, "kestrel-web"))
    fixture_page.evaluate(_SET, ["open", None])
    _reported(fixture_page, _said(report, keys, None))
    # A project the page no longer gives is no longer reported.
    fewer = [item for item in given if item["key"] != "atlas-api"]
    fixture_page.evaluate(_SET, ["projects", json.dumps(fewer)])
    _reported(fixture_page, _said(report, keys - {"atlas-api"}, None))
    # Each report is a bubbling, composed event; a report is sent only when it changed.
    count = fixture_page.evaluate("() => window.__reports.length")
    fixture_page.wait_for_timeout(1000)
    assert fixture_page.evaluate("() => window.__reports.length") == count


def test_a_failed_read_reports_null_and_the_next_good_read_puts_the_facts_back(
        fixture_shell, fixture_page):
    keys = {row.key for row in kit.SEVEN}
    report = kit.full_report(fixture_shell)
    kit.say(fixture_shell, report)
    _place(fixture_page, kit.given(fixture_shell), None)
    _reported(fixture_page, _said(report, keys, None))
    kit.fail(fixture_shell)
    _reported(fixture_page, None)
    kit.fail(fixture_shell, enabled=False)
    _reported(fixture_page, _said(report, keys, None))


def test_the_fixture_reads_while_placed_and_never_once_removed(fixture_shell, fixture_page):
    _place(fixture_page, kit.given(fixture_shell), None)
    _reported(fixture_page, None)
    before = kit.reads(fixture_shell)
    fixture_page.wait_for_timeout(1000)
    assert kit.reads(fixture_shell) > before
    fixture_page.evaluate("() => window.__facts.remove()")
    fixture_page.wait_for_timeout(500)
    gone = kit.reads(fixture_shell)
    fixture_page.wait_for_timeout(1000)
    assert kit.reads(fixture_shell) == gone


def test_the_fixture_keeps_the_component_contract(fixture_shell, fixture_page,
                                                   ui_component_check):
    ui_component_check(fixture_page, "fixture", "project-facts",
                       {"projects": "[]", "open": "atlas-api"}, tokens=())


# --- the two installs ----------------------------------------------------------------------

def _present(shell) -> set[str]:
    status, data = shell.session().get("/m/ui/api/modules")
    assert status == 200, data
    return {item["name"] for item in data["modules"] if item["state"] == "present"}


def _listed(shell) -> dict[str, Any]:
    status, data = shell.session().get("/m/projects/api/projects")
    assert status == 200, data
    return data


def _refusals(shell, key: str) -> list[dict[str, Any]]:
    status, data = shell.session().get(f"/m/projects/api/remove-check?key={key}")
    assert status == 200, data
    return data["refusals"]


def test_the_fixture_install_holds_the_seven_projects_notes_and_the_fixture(fixture_shell):
    assert _present(fixture_shell) == {"ui", "projects", "notes", "fixture"}
    listed = _listed(fixture_shell)
    home = str(kit.home(fixture_shell))
    assert listed["home"] == home
    assert listed["notes_installed"] is True
    rows = {item["key"]: item for item in listed["projects"]}
    assert sorted(rows) == [row.key for row in kit.SEVEN]
    for row in kit.SEVEN:
        assert rows[row.key]["root"] == kit.root(fixture_shell, row)
        assert rows[row.key]["kind"] == row.kind
        assert rows[row.key]["lead"] == row.lead
        assert rows[row.key]["associated"] == list(row.associated)
        assert rows[row.key]["notes_root"] == f"{home}/{row.notes_root}"
        assert rows[row.key]["exists"] is (row.key != kit.MISSING)


def test_the_fixtures_link_refuses_a_remove_while_it_holds(fixture_shell):
    kit.reset(fixture_shell)
    assert _refusals(fixture_shell, "reviewer-home") == []
    kit.hold(fixture_shell)
    try:
        refusals = _refusals(fixture_shell, "reviewer-home")
    finally:
        kit.hold(fixture_shell, enabled=False)
    assert [(item["module"], item["sentence"]) for item in refusals] == [
        ("fixture", kit.held_sentence("reviewer-home"))]
    assert _refusals(fixture_shell, "reviewer-home") == []


def test_the_small_install_holds_core_ui_and_projects_alone(small_shell):
    assert _present(small_shell) == {"ui", "projects"}
    listed = _listed(small_shell)
    assert listed["notes_installed"] is False
    rows = {item["key"]: item for item in listed["projects"]}
    assert sorted(rows) == [row.key for row in kit.SEVEN]
    assert all(item["notes_root"] is None for item in rows.values())
    assert [key for key, item in rows.items() if not item["exists"]] == [kit.MISSING]
    # No other module's link with projects is loaded, so nothing refuses a remove.
    assert _refusals(small_shell, "reviewer-home") == []
    status, data = small_shell.session().get("/m/projects/api/kinds")
    assert status == 200, data
    assert data["broken"] == []
