"""services' page and home block in a browser, through a real shell that starts services'
real page server over a fake supervisor (`browser_kit`): the contract check on both, the
block's count of rows by verdict, the rows grouped by project with their verdicts and
lines, a confirmed act through the sheet to its status (done, and refused), Cancel
sending nothing, a lost answer, the run sheet of a row that launches a session, the add
form's 202 and the status it reads until the job ends, and the 360 px width check.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from playwright.sync_api import expect

from browser_kit import fake_site, get, moxi, seed, stored, written

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="services' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

#: An `Idempotency-Key` as the shell's confirm sheet mints one.
KEY = re.compile(r"^[A-Za-z0-9_-]{43}$")
#: What a run of a row that launches a session adds to its sheet.
SPENDS = "Each run launches an agent session, so this run spends usage."
VERDICTS = ("ok", "warn", "fail", "off")

#: The fake supervisor's units, by row name.
UNITS = {"written": {
    "web": {"kind": "daemon", "enabled": True, "active": True},
    "sync-down": {"kind": "daemon", "enabled": True, "active": False},
    "paused": {"kind": "daemon", "enabled": True, "active": False},
    "spare": {"kind": "daemon", "enabled": False, "active": False},
    "backup": {"kind": "timer", "enabled": True},
    "agent-run": {"kind": "timer", "enabled": True},
    "stopme": {"kind": "daemon", "enabled": True, "active": True},
    "lostone": {"kind": "daemon", "enabled": False, "active": False},
}}


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory) -> dict:
    """services' page server acts on the fake supervisor holding `UNITS`."""
    return {"site": [str(fake_site(tmp_path_factory.mktemp("services-units"), UNITS))]}


@pytest.fixture(scope="module")
def seeded(ui_shell) -> None:
    """The project `alpha`, and a row in each verdict: `web` running (ok, in alpha),
    `sync-down` on and not running (fail), `paused` stopped by hand (warn), `spare` off,
    `backup` a timer every 5 minutes (ok) and `agent-run` a timer whose runs launch a
    session; `elsewhere` was added on another system. `stopme` and `lostone` are the
    acts' own, so the tests run in any order."""
    root = Path(ui_shell.env["HOME"]) / "work" / "alpha"
    root.mkdir(parents=True)
    moxi(ui_shell, "projects", "add", "alpha", "--root", str(root))
    other = "darwin" if sys.platform != "darwin" else "linux"
    seed(ui_shell,
         written("web", project="alpha", doc="serves the docs"),
         written("sync-down"),
         written("paused", after={"stopped": "2026-11-03T09:00:00Z"}),
         written("spare", enabled=False),
         written("backup", kind="timer", every="5m", argv=["/usr/bin/true"]),
         written("agent-run", kind="timer", every="1h", argv=["/usr/bin/true"],
                 launches_session=True, project="alpha"),
         written("elsewhere", platform=other),
         written("stopme"),
         written("lostone", enabled=False))


def panel_of(page):
    page.goto("/m/services/")
    panel = page.locator("#moxi-panel > moxi-services-page")
    expect(panel.locator('section[data-part="list"]')).to_have_attribute("data-state", "up")
    return panel


def row(panel, ident: str):
    return panel.locator(f'li.row[data-id="{ident}"]')


def posts_to(page, path: str) -> list:
    """Every POST the page makes to services' `path`, as it is made."""
    found: list = []
    page.on("request", lambda request: found.append(request) if request.method == "POST"
            and request.url.endswith(f"/m/services/{path}") else None)
    return found


# --- the contract and the home block ------------------------------------------------------

def test_the_summary_keeps_the_contract_and_counts_the_rows_by_verdict(seeded, ui_shell, ui_page,
                                                                       ui_component_check):
    check = ui_component_check(ui_page, "services", "summary", keep=True)
    assert check.element == "moxi-services-summary"
    assert any(url.endswith("/m/services/api/rows") for url in check.requests), check.requests
    block = check.locator.locator(".summary")
    expect(block).to_have_attribute("data-state", "up")
    listed = get(ui_shell, "api/rows")
    assert listed["state"] == "up", listed
    want = {state: sum(1 for r in listed["rows"] if r["verdict"]["state"] == state) for state in VERDICTS}
    assert all(want[state] for state in VERDICTS), want
    for state in VERDICTS:
        expect(block.locator(f'li[data-verdict="{state}"]')).to_have_attribute("data-count", str(want[state]))
    expect(block).to_have_attribute("data-worst", "fail")


def test_the_page_keeps_the_contract(seeded, ui_page, ui_component_check):
    check = ui_component_check(ui_page, "services", "page", keep=True)
    assert check.element == "moxi-services-page"
    expect(check.locator.locator("h1")).to_have_text("Services")
    expect(check.locator.locator('section[data-part="list"]')).to_have_attribute("data-state", "up")


def test_the_link_on_the_home_opens_the_page_which_it_then_marks(seeded, ui_page):
    ui_page.goto("/m/ui/")
    link = ui_page.locator("#moxi-panel > moxi-ui-page #home moxi-services-summary h2 a")
    expect(link).to_have_text("Services")
    link.click()
    expect(ui_page.locator("#moxi-panel > moxi-services-page h1")).to_have_text("Services")
    assert ui_page.evaluate("location.pathname") == "/m/services/"


# --- the rows -------------------------------------------------------------------------------

def test_the_rows_are_grouped_by_project_with_their_verdicts_and_lines(seeded, ui_shell, ui_page):
    panel = panel_of(ui_page)
    alpha = panel.locator('.group[data-project="alpha"]')
    expect(alpha.locator("li.row")).to_have_count(2)
    expect(alpha.locator("h3")).to_have_text("alpha")
    loose = panel.locator('.group[data-project=""]')
    expect(loose.locator("h3")).to_have_text("No project")
    expect(loose.locator('li.row[data-id="services/sync-down"]')).to_have_count(1)

    listed = {r["id"]: r for r in get(ui_shell, "api/rows")["rows"]}
    for ident, state in (("services/web", "ok"), ("services/sync-down", "fail"),
                         ("services/paused", "warn"), ("services/spare", "off")):
        assert listed[ident]["verdict"]["state"] == state, listed[ident]
        item = row(panel, ident)
        expect(item).to_have_attribute("data-verdict", state)
        expect(item.locator('[data-line="verdict"]')).to_have_text(
            f"{state}: {listed[ident]['verdict']['reason']}")
    expect(row(panel, "services/web").locator('[data-line="flag"]')).to_have_text("flag: on")
    expect(row(panel, "services/spare").locator('[data-line="flag"]')).to_have_text("flag: off")
    expect(row(panel, "services/paused").locator('[data-line="time"]')).to_have_text(
        "stopped by hand at 2026-11-03 09:00 UTC")
    expect(row(panel, "services/web").locator('[data-line="doc"]')).to_have_text("serves the docs")

    # A timer runs in place of start and restart, and only a row that launches a session
    # carries the mark.
    agent = row(panel, "services/agent-run")
    expect(agent.locator('[data-mark="launches_session"]')).to_have_count(1)
    expect(row(panel, "services/backup").locator('[data-mark="launches_session"]')).to_have_count(0)
    acts = agent.locator('[data-part="acts"] button')
    expect(acts).to_have_text(["Run now", "Run again", "Stop run", "Enable", "Disable", "Remove…"])
    expect(row(panel, "services/web").locator('[data-part="acts"] button')).to_have_text(
        ["Start", "Stop", "Restart", "Enable", "Disable", "Remove…"])


# --- confirmed acts -------------------------------------------------------------------------

def test_a_confirmed_stop_goes_through_the_sheet_to_done(seeded, ui_shell, ui_page):
    panel = panel_of(ui_page)
    posts = posts_to(ui_page, "api/stop")
    item = row(panel, "services/stopme")
    sheet = ui_page.locator("moxi-ui-confirm").get_by_role("dialog")

    # Cancel sends nothing.
    item.get_by_role("button", name="Stop stopme", exact=True).click()
    expect(sheet).to_contain_text("Stops stopme until the next login or start")
    sheet.get_by_role("button", name="Cancel", exact=True).click()
    expect(ui_page.locator("moxi-ui-confirm")).to_have_count(0)
    assert posts == []

    with ui_page.expect_response(lambda r: "/m/services/api/status?job=" in r.url
                                 and r.json().get("state") != "running") as ended:
        item.get_by_role("button", name="Stop stopme", exact=True).click()
        sheet.get_by_role("button", name="Confirm", exact=True).click()
    assert ended.value.json()["state"] == "done", ended.value.json()
    said = item.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-tone", "ok")
    expect(said).to_contain_text("Stop stopme: Done.")
    assert len(posts) == 1 and KEY.match(posts[0].headers["idempotency-key"])
    assert posts[0].post_data_json == {"id": "services/stopme"}
    # The page read the rows again: a stop by hand is recorded, and the verdict says so.
    expect(item).to_have_attribute("data-verdict", "warn")
    expect(item.locator('[data-line="verdict"]')).to_contain_text("stopped by hand")
    job = posts[0].headers["idempotency-key"]
    status = get(ui_shell, f"api/status?job={job}")
    assert (status["act"], status["id"], status["state"], status["code"]) == (
        "stop", "services/stopme", "done", 0), status


def test_an_act_the_job_refuses_shows_its_lines(seeded, ui_page):
    panel = panel_of(ui_page)
    item = row(panel, "services/elsewhere")
    expect(item).to_have_attribute("data-verdict", "off")
    item.get_by_role("button", name="Start elsewhere", exact=True).click()
    ui_page.locator("moxi-ui-confirm").get_by_role("dialog").get_by_role("button", name="Confirm", exact=True).click()
    said = item.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-tone", "error")
    expect(said).to_contain_text("Start elsewhere: Refused; nothing changed.")
    expect(said).to_contain_text("elsewhere")


def test_a_lost_answer_says_the_act_may_have_been_done(seeded, ui_page):
    panel = panel_of(ui_page)

    def lost(route) -> None:
        route.fetch()               # the act is taken, and its answer never arrives
        route.fulfill(status=504, json={"error": "timeout"})

    ui_page.route("**/m/services/api/enable", lost)
    try:
        item = row(panel, "services/lostone")
        item.get_by_role("button", name="Enable lostone", exact=True).click()
        ui_page.locator("moxi-ui-confirm").get_by_role("dialog").get_by_role("button", name="Confirm", exact=True).click()
        said = item.locator('[data-line="said"]')
        expect(said).to_have_attribute("data-tone", "warn")
        expect(said).to_contain_text("it may have been done")
    finally:
        ui_page.unroute("**/m/services/api/enable")


def test_a_run_of_a_row_that_launches_a_session_says_it_spends_usage(seeded, ui_page):
    panel = panel_of(ui_page)
    posts = posts_to(ui_page, "api/run")
    sheet = ui_page.locator("moxi-ui-confirm").get_by_role("dialog")
    row(panel, "services/agent-run").get_by_role("button", name="Run now agent-run", exact=True).click()
    expect(sheet).to_contain_text(SPENDS)
    sheet.get_by_role("button", name="Cancel", exact=True).click()
    row(panel, "services/backup").get_by_role("button", name="Run now backup", exact=True).click()
    expect(sheet).to_contain_text("Runs backup once now")
    expect(sheet).not_to_contain_text(SPENDS)
    sheet.get_by_role("button", name="Cancel", exact=True).click()
    expect(ui_page.locator("moxi-ui-confirm")).to_have_count(0)
    assert posts == []


# --- add ------------------------------------------------------------------------------------

def test_the_add_form_takes_a_202_and_reads_the_status_until_the_job_ends(seeded, ui_shell, ui_page):
    panel = panel_of(ui_page)
    form = panel.locator('section[data-part="add"] form')
    expect(form.get_by_label("Project")).to_contain_text("alpha")
    form.get_by_label("Name", exact=True).fill("notebook")
    form.get_by_role("textbox", name="Command line").fill("/usr/bin/python3 -m http.server 8123")
    form.get_by_label("Project").select_option("alpha")
    form.get_by_label("Doc").fill("a notebook server")
    statuses: list[str] = []
    ui_page.on("request", lambda r: statuses.append(r.url) if "/m/services/api/status?job=" in r.url else None)
    with ui_page.expect_response("**/m/services/api/add") as added:
        form.get_by_role("button", name="Add", exact=True).click()
    assert added.value.status == 202, added.value.text()
    link = added.value.json()["status"]
    assert link.startswith("/api/status?job="), link
    said = form.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-tone", "ok")
    expect(said).to_contain_text("Add notebook: Done.")
    assert any(url.endswith(link) for url in statuses), statuses
    assert "notebook" in stored(ui_shell)
    item = row(panel, "services/notebook")
    expect(item).to_have_count(1)
    expect(item).to_have_attribute("data-verdict", "ok")
    expect(panel.locator('.group[data-project="alpha"] li.row[data-id="services/notebook"]')).to_have_count(1)
    # The form starts again once the add is done.
    expect(form.get_by_label("Name", exact=True)).to_have_value("")


def test_an_add_whose_command_is_not_a_full_path_is_refused_before_any_job(seeded, ui_shell, ui_page):
    panel = panel_of(ui_page)
    form = panel.locator('section[data-part="add"] form')
    form.get_by_label("Name", exact=True).fill("bare")
    form.get_by_role("textbox", name="Command line").fill("python3 -m http.server")
    with ui_page.expect_response("**/m/services/api/add") as added:
        form.get_by_role("button", name="Add", exact=True).click()
    assert added.value.status == 422
    said = form.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-tone", "error")
    expect(said).to_contain_text("python3 is not a full path")
    expect(said).to_contain_text("command -v python3")
    assert "bare" not in stored(ui_shell)


def test_an_add_of_a_taken_name_ends_refused(seeded, ui_page):
    panel = panel_of(ui_page)
    form = panel.locator('section[data-part="add"] form')
    form.get_by_label("Name", exact=True).fill("web")
    form.get_by_role("textbox", name="Command line").fill("/usr/bin/true")
    with ui_page.expect_response("**/m/services/api/add") as added:
        form.get_by_role("button", name="Add", exact=True).click()
    assert added.value.status == 202
    said = form.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-tone", "error")
    expect(said).to_contain_text("Add web: Refused; nothing changed.")
    expect(said).to_contain_text("web is taken")


# --- width ----------------------------------------------------------------------------------

def test_the_page_and_the_block_fit_a_phone(seeded, ui_page, ui_width_check):
    ui_width_check(ui_page, module="services")
    expect(ui_page.locator("#moxi-panel > moxi-services-page section[data-part=\"list\"]")
           ).to_have_attribute("data-state", "up")
    ui_page.locator("#moxi-panel > moxi-services-page details").first.evaluate("(d) => { d.open = true; }")
    ui_width_check(ui_page)
    ui_width_check(ui_page, module="services", name="summary")
