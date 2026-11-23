"""The panel that adds a service, in a browser, through a real shell that starts services'
real page server over a fake supervisor (`browser_kit`): `Add a service` opens it at the
window's right edge with the column moved aside, and Escape closes it; a command added
left off and turned on, its job followed to the row in the list, the form empty again; a
command whose first word isn't a full path, and a taken name, refused; a systemd timer
adopted, with what the system says of it, on or off asked, and the file adopting adds
named; a unit the system doesn't have, and one in Moxi's own namespace; and, where the
system can't tell a unit's kind, the kind asked with none chosen, Adopt held until he picks
one, and the unit read again as the one he picks.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from playwright.sync_api import expect

from browser_kit import fake_site, moxi, seed, stored, written

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="services' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

#: The fake supervisor's units: `web` of his, and two units of the system's own not yet
#: adopted, a timer that is on and a service that is off.
UNITS = {"written": {"web": {"kind": "daemon", "enabled": True, "active": True}},
         "foreign": {"restic-backup.timer": {"kind": "timer", "enabled": True},
                     "syncthing.service": {"kind": "daemon", "enabled": False, "active": False}}}
WIDE = {"width": 1280, "height": 800}
#: The panel's width, as drawn.
PANEL_PX = 400


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory) -> dict:
    return {"site": [str(fake_site(tmp_path_factory.mktemp("services-units"), UNITS))]}


@pytest.fixture(scope="module")
def seeded(ui_shell) -> None:
    """The project `alpha`, and `web`, running."""
    root = Path(ui_shell.env["HOME"]) / "work" / "alpha"
    root.mkdir(parents=True)
    moxi(ui_shell, "projects", "add", "alpha", "--root", str(root))
    seed(ui_shell, written("web"))


def page_of(page):
    return page.locator("#moxi-panel > moxi-services-page")


def opened(page, route: str = ""):
    """Opens services' page at `route` and gives the panel, `Add a service`'s unless the
    route opens it."""
    page.set_viewport_size(WIDE)
    page.goto(f"/m/services/{route}")
    services = page_of(page)
    expect(services.locator('section[data-part="list"]')).to_have_attribute("data-state", "up")
    if route == "":
        services.locator('section[data-part="first"]').get_by_role("button", name="Add a service").click()
    aside = services.locator('moxi-services-panel [data-part="panel"]')
    expect(aside).to_be_visible()
    return aside


def expect_path(page, path: str) -> None:
    page.wait_for_function("path => location.pathname === path", arg=path)


def said(aside):
    return aside.locator('[data-line="said"]')


def test_add_a_service_opens_the_panel_at_the_right_edge_and_escape_closes_it(seeded, ui_page):
    aside = opened(ui_page)
    expect_path(ui_page, "/m/services/add")
    box = aside.bounding_box()
    assert box is not None and round(box["x"] + box["width"]) == WIDE["width"], box
    assert round(box["width"]) == PANEL_PX, box
    # The column stands beside the panel, not under it.
    column = page_of(ui_page).bounding_box()
    assert column is not None and column["x"] + column["width"] <= box["x"] + 1, (column, box)
    expect(aside.get_by_role("radio", name="A command to run")).to_be_checked()
    expect(aside.get_by_label("Command", exact=True)).to_be_focused()
    expect(aside.locator('[data-line="full-path"]')).to_have_text(
        "The first word is a full path. command -v NAME in your terminal prints it.")
    aside.get_by_label("Command", exact=True).fill("npx vite --port 5174")
    expect(aside.locator('[data-line="full-path"]')).to_contain_text("command -v npx")
    expect(aside.locator('[data-line="more"]')).to_have_text("folder · health check · 2 more")
    # A schedule's fields show only for one that runs on a schedule.
    expect(aside.get_by_label("Every, such as 5m or 2h")).to_have_count(0)
    aside.get_by_role("radio", name="runs on a schedule").click()
    expect(aside.get_by_label("Every, such as 5m or 2h")).to_be_visible()
    ui_page.keyboard.press("Escape")
    expect(aside).to_have_count(0)
    expect_path(ui_page, "/m/services/")
    expect(page_of(ui_page).locator('section[data-part="first"] [data-action="add"]')).to_be_focused()


@pytest.mark.parametrize(("name", "port", "tick", "verdict"), [
    ("notebook", 8123, False, "off"),
    ("jupyter", 8124, True, "ok"),
], ids=["left-off", "turned-on"])
def test_a_command_added_follows_its_job_to_the_row_and_empties_the_form(seeded, ui_shell, ui_page,
                                                                         name, port, tick, verdict):
    """A written row starts off, as the command line leaves it; `Turn it on now` starts it."""
    aside = opened(ui_page)
    aside.get_by_label("Command", exact=True).fill(f"/usr/bin/python3 -m http.server {port}")
    aside.get_by_label("Name", exact=True).fill(name)
    expect(aside.get_by_label("Project")).to_contain_text("alpha")
    aside.get_by_label("Project").select_option("alpha")
    aside.get_by_role("button", name="More").click()
    aside.get_by_label("Description, one line").fill("a notebook server")
    turn_on = aside.get_by_role("switch", name="Turn it on now")
    expect(turn_on).not_to_be_checked()
    if tick:
        turn_on.check()
    statuses: list[str] = []
    ui_page.on("request", lambda r: statuses.append(r.url) if "/m/services/api/status?job=" in r.url else None)
    with ui_page.expect_response("**/m/services/api/add") as added:
        aside.get_by_role("button", name="Add service", exact=True).click()
    assert added.value.status == 202, added.value.text()
    body = added.value.request.post_data_json
    assert body == {"name": name, "kind": "daemon", "command": f"/usr/bin/python3 -m http.server {port}",
                    "enable": tick, "project": "alpha", "doc": "a notebook server"}, body
    link = added.value.json()["status"]
    expect(said(aside)).to_have_attribute("data-tone", "ok")
    expect(said(aside)).to_contain_text(f"Add {name}: Done.")
    assert any(url.endswith(link) for url in statuses), statuses
    assert name in stored(ui_shell)
    item = page_of(ui_page).locator(f'section[data-project="alpha"] .item[data-id="services/{name}"]')
    expect(item).to_have_attribute("data-verdict", verdict)
    # The panel stays, its form empty again and the switch off.
    expect(aside.get_by_label("Command", exact=True)).to_have_value("")
    expect(aside.get_by_label("Name", exact=True)).to_have_value("")
    expect(aside.get_by_role("switch", name="Turn it on now")).not_to_be_checked()


def test_a_command_whose_first_word_isnt_a_full_path_is_refused_before_any_job(seeded, ui_shell, ui_page):
    aside = opened(ui_page)
    aside.get_by_label("Command", exact=True).fill("python3 -m http.server")
    aside.get_by_label("Name", exact=True).fill("bare")
    with ui_page.expect_response("**/m/services/api/add") as added:
        aside.get_by_role("button", name="Add service", exact=True).click()
    assert added.value.status == 422
    expect(said(aside)).to_have_attribute("data-tone", "error")
    expect(said(aside)).to_contain_text("python3 is not a full path")
    expect(said(aside)).to_contain_text("command -v python3")
    # What was typed stays.
    expect(aside.get_by_label("Name", exact=True)).to_have_value("bare")
    assert "bare" not in stored(ui_shell)


def test_a_taken_name_ends_refused_and_a_missing_one_is_asked_before_any_post(seeded, ui_page):
    aside = opened(ui_page)
    aside.get_by_label("Command", exact=True).fill("/usr/bin/true")
    posts: list = []
    ui_page.on("request", lambda r: posts.append(r) if r.url.endswith("/m/services/api/add") else None)
    aside.get_by_role("button", name="Add service", exact=True).click()
    expect(said(aside)).to_have_text("Give the service a name.")
    assert posts == []
    aside.get_by_label("Name", exact=True).fill("web")
    with ui_page.expect_response("**/m/services/api/add") as added:
        aside.get_by_role("button", name="Add service", exact=True).click()
    assert added.value.status == 202
    expect(said(aside)).to_have_attribute("data-tone", "error")
    expect(said(aside)).to_contain_text("Add web: Refused; nothing changed.")
    expect(said(aside)).to_contain_text("web is taken")


def test_a_systemd_timer_adopted_says_what_the_system_says_and_names_the_file_it_adds(seeded, ui_shell, ui_page):
    aside = opened(ui_page, "adopt")
    expect(aside.get_by_role("radio", name="A unit already on this computer")).to_be_checked()
    expect(aside.locator('[data-line="more"]')).to_have_text("health check · 2 more")
    aside.get_by_label("Unit", exact=True).fill("restic-backup.timer")
    expect(aside.locator('[data-line="found"]')).to_have_text(
        "Found: a timer of your own. Moxi lists it and acts on it; its file is never rewritten.")
    expect(aside.locator('[data-line="on-off"]')).to_have_text(
        "Your system says it is on. Say which is right, so the list starts out true.")
    expect(aside.locator('[data-line="wraps"]')).to_have_text(
        "Moxi adds one small file beside the service this timer starts, so each run is recorded here and "
        "Turn off can hold it. Remove takes that file away and leaves the timer as it was.")
    # systemd tells the kind by the unit's name, so it isn't asked.
    expect(aside.get_by_role("radio", name="runs on a schedule")).to_have_count(0)
    aside.get_by_label("Name", exact=True).fill("restic-backup")
    aside.get_by_role("button", name="Adopt unit", exact=True).click()
    expect(said(aside)).to_have_text("Say whether it is on or off now.")
    aside.get_by_role("radio", name="on", exact=True).click()
    with ui_page.expect_response("**/m/services/api/add") as added:
        aside.get_by_role("button", name="Adopt unit", exact=True).click()
    assert added.value.status == 202, added.value.text()
    assert added.value.request.post_data_json == {
        "name": "restic-backup", "kind": "timer", "unit": "restic-backup.timer", "enable": True}
    expect(said(aside)).to_contain_text("Adopt restic-backup: Done.")
    assert "restic-backup" in stored(ui_shell)
    expect(page_of(ui_page).locator('.item[data-id="services/restic-backup"]')).to_have_attribute("data-kind", "timer")


@pytest.mark.parametrize(("unit", "line"), [
    ("nosuch.service", "Your system has no unit by that name."),
    ("syncthing.service", "Found: a service of your own. Moxi lists it and acts on it; its file is never rewritten."),
    ("moxi-services-web.service", re.compile(r"in moxi's own unit namespace")),
    ("thing.socket", re.compile(r"is not a \.service or a \.timer")),
], ids=["missing", "found-off", "own-namespace", "not-a-service-or-timer"])
def test_the_unit_typed_is_read_and_only_its_own_answer_drawn(seeded, ui_page, unit, line):
    aside = opened(ui_page, "adopt")
    field = aside.get_by_label("Unit", exact=True)
    field.fill(unit)
    found = aside.locator('[data-line="found"]')
    expect(found).to_have_text(line)
    expect(aside.locator('[data-line="wraps"]')).to_have_count(0)
    if unit == "syncthing.service":
        expect(aside.locator('[data-line="on-off"]')).to_have_text(
            "Your system says it is off. Say which is right, so the list starts out true.")
    # A key more, and the answer for what the field held is gone until the new one comes.
    held: list = []
    ui_page.route(re.compile(r".*/m/services/api/unit\?.*"), lambda route: held.append(route))
    field.press("End")
    field.press_sequentially("x")
    expect(found).to_have_count(0)
    ui_page.unroute(re.compile(r".*/m/services/api/unit\?.*"))
    for route in held:
        route.abort()


def test_where_the_system_cant_tell_the_kind_it_is_asked_with_none_chosen_and_read_again(seeded, ui_page):
    """On launchd or Task Scheduler `/api/unit` answers `asks_kind`; the shell's own page
    server runs on the fake systemd, so its answers here are the page's stand-ins. No kind
    is chosen for him, and Adopt sends nothing until he picks one."""
    asked: list[str] = []
    posted: list[str] = []

    def answer(route):
        url = route.request.url
        asked.append(url)
        kind = "timer" if "kind=timer" in url else "daemon"
        route.fulfill(json={"found": True, "kind": kind, "asks_kind": True, "enabled": False, "active": False,
                            "wraps": False, "refused": None})

    ui_page.route(re.compile(r".*/m/services/api/unit\?.*"), answer)
    ui_page.route("**/m/services/api/add", lambda route: route.fulfill(
        status=422, json={"refused": "the stand-in takes no add"}))
    ui_page.on("request", lambda request: posted.append(request.url) if request.url.endswith("/api/add") else None)
    aside = opened(ui_page, "adopt")
    aside.get_by_label("Unit", exact=True).fill("com.example.backup")
    expect(aside.locator('[data-line="found"]')).to_have_text(
        "Found: a unit of your own. Moxi lists it and acts on it; its file is never rewritten.")
    assert "kind=" not in asked[-1], asked
    kinds = aside.get_by_role("radiogroup", name="It", exact=True).get_by_role("radio")
    expect(kinds).to_have_count(2)
    for at in range(2):
        expect(kinds.nth(at)).not_to_be_checked()
    aside.get_by_label("Name", exact=True).fill("backup")
    aside.get_by_role("radio", name="off", exact=True).click()
    aside.get_by_role("button", name="Adopt unit", exact=True).click()
    expect(said(aside)).to_have_text("Say whether it keeps running or runs on a schedule.")
    assert posted == []
    aside.get_by_role("radio", name="runs on a schedule").click()
    expect(aside.locator('[data-line="found"]')).to_contain_text("Found: a timer of your own.")
    assert asked[-1].endswith("ref=com.example.backup&kind=timer"), asked
    with ui_page.expect_request("**/m/services/api/add") as sent:
        aside.get_by_role("button", name="Adopt unit", exact=True).click()
    assert sent.value.post_data_json == {"name": "backup", "kind": "timer", "unit": "com.example.backup",
                                         "enable": False}
    expect(said(aside)).to_have_text("the stand-in takes no add")
