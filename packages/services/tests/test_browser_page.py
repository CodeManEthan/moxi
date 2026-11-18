"""services' page and home block in a browser, through a real shell that starts services'
real page server over a fake supervisor (`browser_kit`): the contract check on both, the
block's count of rows by verdict, the page's frame (no heading of its own), the first
group's risen rows (SV1), a running daemon's time (SV2), the groups by project and Moxi's
(SV5), a project's address (SV7), each address and what the page writes back, the list's
states, a confirmed act through the sheet to its status (done, and refused), Cancel
sending nothing, a lost answer, the run sheet of a row that launches a session, the add
form's 202 and the status it reads until the job ends, and the 360 px width check.
"""

from __future__ import annotations

import re
import sys
import time
from datetime import datetime, timezone
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
#: When `paused` was stopped by hand: now, so the stop is after the last boot and still
#: holds (a stop from before the boot is dropped, and a fixed time fails on any machine
#: booted since).
STOPPED = datetime.now(timezone.utc).replace(microsecond=0)
#: When `web` started, in seconds since the epoch: a minute ago, so it reads a time today.
WEB_SINCE = time.time() - 60
NOT_ANSWERING = "Services' page server isn't answering"

#: The fake supervisor's units, by row name.
UNITS = {"written": {
    "web": {"kind": "daemon", "enabled": True, "active": True, "since": WEB_SINCE, "pid": 4242},
    "sync-down": {"kind": "daemon", "enabled": True, "active": False},
    "paused": {"kind": "daemon", "enabled": True, "active": False},
    "spare": {"kind": "daemon", "enabled": False, "active": False},
    "backup": {"kind": "timer", "enabled": True},
    "agent-run": {"kind": "timer", "enabled": True},
    "stopme": {"kind": "daemon", "enabled": True, "active": True},
    "lostone": {"kind": "daemon", "enabled": False, "active": False},
    "queue-worker": {"kind": "daemon", "enabled": True, "active": False, "failed": True},
    "dep-audit": {"kind": "timer", "enabled": True,
                  "runs": [{"at": "2026-11-08T18:00:00Z", "result": "ran", "exit": 1}]},
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
    session; `elsewhere` was added on another system; `queue-worker` failed (in alpha) and
    `dep-audit` a timer whose last run exited 1. `stopme` and `lostone` are the
    acts' own, so the tests run in any order."""
    root = Path(ui_shell.env["HOME"]) / "work" / "alpha"
    root.mkdir(parents=True)
    moxi(ui_shell, "projects", "add", "alpha", "--root", str(root))
    other = "darwin" if sys.platform != "darwin" else "linux"
    seed(ui_shell,
         written("web", project="alpha", doc="serves the docs"),
         written("sync-down"),
         written("paused", after={"stopped": f"{STOPPED:%Y-%m-%dT%H:%M:%SZ}"}),
         written("spare", enabled=False),
         written("backup", kind="timer", every="5m", argv=["/usr/bin/true"]),
         written("agent-run", kind="timer", every="1h", argv=["/usr/bin/true"],
                 launches_session=True, project="alpha"),
         written("elsewhere", platform=other),
         written("stopme"),
         written("lostone", enabled=False),
         written("queue-worker", project="alpha"),
         written("dep-audit", kind="timer", every="1h", argv=["/usr/bin/false"]))


def panel_of(page):
    page.goto("/m/services/")
    panel = page.locator("#moxi-panel > moxi-services-page")
    expect(panel.locator('section[data-part="list"]')).to_have_attribute("data-state", "up")
    return panel


def row(panel, ident: str):
    """The row `ident`, with what its last act came to under it."""
    return panel.locator(f'.item[data-id="{ident}"]')


def ids(items) -> list[str]:
    return [item.get_attribute("data-id") for item in items.all()]


def list_part(panel):
    return panel.locator('section[data-part="list"]')


def expect_path(page, path: str) -> None:
    page.wait_for_function("path => location.pathname === path", arg=path)


def services(n: int) -> str:
    return "1 service" if n == 1 else f"{n} services"


def clock(page, at: float) -> re.Pattern:
    """The fact a time `at` (epoch seconds) of today reads in the browser, as a pattern
    that also takes it a minute past midnight."""
    time_words = page.evaluate(
        "at => new Date(at * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})", at)
    return re.compile(rf"(yesterday )?{re.escape(time_words)}")


def shown_without_the_topic(page) -> None:
    """From now on the shell's stream for services' topic `rows` is refused, so what a test
    gives `/api/rows` is what the page shows."""
    page.route(re.compile(r".*/m/ui/api/push\?topics=.*services\.rows.*"), lambda route: route.abort())


def as_rows(page, value: dict) -> None:
    page.route("**/m/services/api/rows", lambda route: route.fulfill(json=value))


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
    expect(check.locator.locator('section[data-part="list"]')).to_have_attribute("data-state", "up")


def test_the_blocks_link_on_the_modules_view_opens_the_page_which_it_then_marks(seeded, ui_page):
    ui_page.goto("/m/ui/modules")
    link = ui_page.locator("#moxi-panel > moxi-ui-page #home moxi-services-summary h2 a")
    expect(link).to_have_text("Services")
    link.click()
    expect(ui_page.locator('#moxi-panel > moxi-services-page section[data-part="list"]')).to_have_attribute(
        "data-state", "up")
    assert ui_page.evaluate("location.pathname") == "/m/services/"


# --- the frame, the first group and the groups ---------------------------------------------

def test_the_page_draws_no_heading_lede_or_read_again(seeded, ui_page):
    panel = panel_of(ui_page)
    # The shell's top line names the page; the rows follow the topic, read at a time.
    expect(panel.locator("h1")).to_have_count(0)
    expect(panel.locator(".lede")).to_have_count(0)
    expect(panel.get_by_role("button", name="Read again")).to_have_count(0)
    expect(panel.locator('[data-part="list"] h2, [data-part="list"] h3')).to_have_count(0)


def test_sv1_the_rows_that_need_him_rise_first_worst_first(seeded, ui_shell, ui_page):
    panel = panel_of(ui_page)
    listed = get(ui_shell, "api/rows")["rows"]
    first = panel.locator('section[data-part="first"]')
    expect(first.locator('[data-line="count"]')).to_have_text(f"{len(listed)} services")
    expect(first.get_by_role("button", name="Add a service", exact=True)).to_have_count(1)

    # Every fail by name, then failed last runs oldest first, then rows it can't tell.
    def rank(r: dict) -> tuple | None:
        cause, state = r["verdict"]["cause"], r["verdict"]["state"]
        if state == "fail":
            return (0, "", r["name"])
        if cause == "last-run":
            return (1, r["last_run"]["at"] or "~", r["name"])
        if cause == "unknown":
            return (2, "", r["name"])
        return None
    want = [r["id"] for r in sorted((r for r in listed if rank(r) is not None), key=rank)]
    assert {"services/queue-worker", "services/sync-down", "services/dep-audit"} <= set(want), want
    risen = first.locator(".item")
    expect(risen).to_have_count(len(want))
    assert ids(risen) == want
    # Risen rows replace the sentence and the counts.
    expect(first.locator('[data-line="sentence"]')).to_have_count(0)
    expect(first.locator('[data-line="counts"]')).to_have_count(0)

    # A fail reads heavy `down`, with no time, its project key dim, its sentence on line two.
    worker = first.locator('.item[data-id="services/queue-worker"] moxi-ui-row')
    expect(worker).to_have_attribute("fact", "down")
    expect(worker).to_have_attribute("fact-heavy", "")
    expect(worker).to_have_attribute("where", "alpha")
    expect(worker).to_have_attribute("lead", "It failed and is not running.")
    expect(first.locator('.item[data-id="services/sync-down"] moxi-ui-row')).to_have_attribute(
        "lead", "It is not running.")
    # A failed last run is in the text colour, not heavy.
    audit = first.locator('.item[data-id="services/dep-audit"]')
    expect(audit.locator('[data-line="fact"]')).to_contain_text("last run failed at ")
    expect(audit.locator("moxi-ui-row")).not_to_have_attribute("fact-heavy", "")
    expect(audit.locator("moxi-ui-row")).to_have_attribute("lead", "false exited with code 1.")
    # A row he stopped stays in its group.
    paused = panel.locator('section[data-part="group"] .item[data-id="services/paused"]')
    expect(paused.locator('[data-line="fact"]')).to_contain_text("stopped by hand at ")


def test_the_groups_go_by_project_then_no_project_then_moxi(seeded, ui_shell, ui_page):
    panel = panel_of(ui_page)
    groups = panel.locator('section[data-part="group"]')
    labels = groups.locator('[data-line="group"]').all_inner_texts()
    assert labels[:2] == ["alpha", "No project"] and labels[-1] == "Moxi", labels
    alpha = panel.locator('section[data-part="group"][data-project="alpha"]')
    # queue-worker rose out of it.
    assert ids(alpha.locator(".item")) == ["services/agent-run", "services/web"]
    web = alpha.locator('.item[data-id="services/web"] moxi-ui-row')
    expect(web).to_have_attribute("name", "web")
    expect(web).to_have_attribute("where", "serves the docs")
    expect(web).to_have_attribute("fact", re.compile(r"^running since "))
    expect(web).not_to_have_attribute("lead", re.compile(".*"))
    expect(alpha.locator('.item[data-id="services/agent-run"] moxi-ui-row')).to_have_attribute(
        "where", "true · every hour · spends usage")
    loose = panel.locator('section[data-part="group"][data-group="his"]')
    expect(loose.locator('.item[data-id="services/backup"] moxi-ui-row')).to_have_attribute(
        "where", "true · every 5 minutes")
    expect(loose.locator('.item[data-id="services/spare"] moxi-ui-row')).to_have_attribute("fact", "off")

    # Today's acts stay on each row until the page's own acts replace them.
    agent = row(panel, "services/agent-run")
    expect(agent.locator('[data-part="acts"] button')).to_have_text(
        ["Run now", "Run again", "Stop run", "Enable", "Disable", "Remove…"])
    expect(row(panel, "services/web").locator('[data-part="acts"] button')).to_have_text(
        ["Start", "Stop", "Restart", "Enable", "Disable", "Remove…"])


def test_sv5_moxis_own_rows_are_one_group_by_module(seeded, ui_shell, ui_page):
    panel = panel_of(ui_page)
    listed = get(ui_shell, "api/rows")["rows"]
    risen = set(ids(panel.locator('section[data-part="first"] .item')))
    mine = [r for r in listed if r["form"] == "module" and r["id"] not in risen]
    assert mine, listed
    moxi = panel.locator('section[data-part="group"]').last
    expect(moxi).to_have_attribute("data-group", "moxi")
    expect(moxi.locator('[data-line="group"]')).to_have_text("Moxi")
    # By module then name, the rows switched off last.
    want = sorted(mine, key=lambda r: (r["verdict"]["cause"] == "switched-off", r["module"], r["name"]))
    assert ids(moxi.locator(".item")) == [r["id"] for r in want]
    for r in want:
        drawn = moxi.locator(f'.item[data-id="{r["id"]}"] moxi-ui-row')
        expect(drawn).to_have_attribute("name", r["name"])
        expect(drawn).to_have_attribute("where", re.compile(rf"^{re.escape(r['module'])}\b"))
    # No row of his is in it.
    assert not [i for i in ids(moxi.locator(".item")) if i.startswith("services/")]


def test_sv2_a_running_daemon_says_since_when(seeded, ui_page):
    panel = panel_of(ui_page)
    web = row(panel, "services/web").locator("moxi-ui-row")
    since = clock(ui_page, WEB_SINCE)
    expect(web).to_have_attribute("fact", re.compile(rf"^running since {since.pattern}$"))
    expect(web).not_to_have_attribute("fact-heavy", "")


def test_sv7_a_project_address_shows_its_rows_alone_until_show_all(seeded, ui_shell, ui_page):
    ui_page.goto("/m/services/project/alpha")
    panel = ui_page.locator("#moxi-panel > moxi-services-page")
    expect(list_part(panel)).to_have_attribute("data-state", "up")
    listed = get(ui_shell, "api/rows")["rows"]
    mine = [r["id"] for r in listed if r["form"] != "module" and r["project"] == "alpha"]
    filt = panel.locator('[data-part="filter"]')
    expect(filt).to_contain_text("Project")
    expect(filt.locator('[data-line="project"]')).to_have_text("alpha")
    expect(filt.locator('[data-line="shown"]')).to_have_text(f"{len(mine)} of {services(len(listed))}")
    expect(panel.locator('section[data-part="first"] [data-line="count"]')).to_have_text(services(len(mine)))
    expect(panel.locator(".item")).to_have_count(len(mine))
    assert sorted(ids(panel.locator(".item"))) == sorted(mine)
    # The failed row of alpha still rises; Moxi's rows and the other groups are not shown.
    expect(panel.locator('section[data-part="first"] .item[data-id="services/queue-worker"]')).to_have_count(1)
    expect(panel.locator('section[data-group="moxi"], section[data-group="his"]')).to_have_count(0)
    expect_path(ui_page, "/m/services/project/alpha")

    # The menu names the projects his rows carry.
    filt.locator('[data-action="pick"]').click()
    expect(filt.get_by_role("menuitem")).to_have_text(["alpha"])
    filt.get_by_role("menuitem", name="alpha").click()
    expect(filt.get_by_role("menuitem")).to_have_count(0)
    expect_path(ui_page, "/m/services/project/alpha")

    # A row opened in it keeps the filter in the address.
    row(panel, "services/web").get_by_role("button", name="Open web", exact=True).click()
    expect_path(ui_page, "/m/services/project/alpha/row/web")
    filt.get_by_role("button", name="Show all", exact=True).click()
    expect(filt).to_have_count(0)
    expect_path(ui_page, "/m/services/row/web")
    expect(panel.locator(".item")).to_have_count(len(listed))


# --- addresses ------------------------------------------------------------------------------

@pytest.mark.parametrize(("route", "opened", "path"), [
    ("", None, "/m/services/"),
    ("row/web", "services/web", "/m/services/row/web"),
    ("row/services/web", "services/web", "/m/services/row/web"),
    ("project/alpha/row/web", "services/web", "/m/services/project/alpha/row/web"),
    ("add", None, "/m/services/add"),
    ("adopt", None, "/m/services/adopt"),
])
def test_each_address_opens_what_it_names(seeded, ui_page, route, opened, path):
    ui_page.goto(f"/m/services/{route}")
    panel = ui_page.locator("#moxi-panel > moxi-services-page")
    expect(list_part(panel)).to_have_attribute("data-state", "up")
    if opened is None:
        expect(panel.locator("moxi-ui-row[open]")).to_have_count(0)
    else:
        item = row(panel, opened)
        expect(item.locator("moxi-ui-row")).to_have_attribute("open", "")
        expect(panel.locator("moxi-ui-row[open]")).to_have_count(1)
        expect(item.locator('[data-action="open"]')).to_have_text("Close")
        expect(item).to_be_in_viewport()
    expect_path(ui_page, path)
    expect(list_part(panel).locator('[data-line="unknown"]')).to_have_count(0)
    form = panel.locator('section[data-part="add"] form')
    if route == "add":
        expect(form.get_by_label("Name", exact=True)).to_be_focused()
    elif route == "adopt":
        expect(form.get_by_label("a unit already here")).to_be_checked()
        expect(form.get_by_role("textbox", name="Unit")).to_be_focused()


def test_a_moxi_rows_address_is_its_module_and_name(seeded, ui_shell, ui_page):
    ident = next(r["id"] for r in get(ui_shell, "api/rows")["rows"] if r["form"] == "module")
    ui_page.goto(f"/m/services/row/{ident}")
    panel = ui_page.locator("#moxi-panel > moxi-services-page")
    expect(row(panel, ident).locator("moxi-ui-row")).to_have_attribute("open", "")
    expect_path(ui_page, f"/m/services/row/{ident}")


@pytest.mark.parametrize(("route", "line"), [
    ("row/nosuch", "No service is named nosuch."),
    ("row/ui/nosuch", "No service is named ui/nosuch."),
    ("project/nope", "No project is named nope."),
    ("project/nope/row/web", "No project is named nope."),
    ("bogus/x", "This address names nothing on this page."),
])
def test_an_address_naming_nothing_here_opens_the_page_and_says_so(seeded, ui_page, route, line):
    ui_page.goto(f"/m/services/{route}")
    panel = ui_page.locator("#moxi-panel > moxi-services-page")
    expect(list_part(panel)).to_have_attribute("data-state", "up")
    said = list_part(panel).locator('[data-line="unknown"]')
    expect(said).to_have_text(line)
    expect(panel.locator("moxi-ui-row[open]")).to_have_count(0)
    expect(panel.locator('[data-part="filter"]')).to_have_count(0)
    expect_path(ui_page, "/m/services/")
    # Opening a row takes the line away.
    row(panel, "services/web").get_by_role("button", name="Open web", exact=True).click()
    expect(said).to_have_count(0)


def test_open_close_and_add_rewrite_the_address_and_back_leaves_the_page(seeded, ui_page):
    ui_page.goto("/m/ui/")
    ui_page.evaluate("""async () => (await import("/m/ui/client.js")).navigate("services", "")""")
    panel = ui_page.locator("#moxi-panel > moxi-services-page")
    expect(list_part(panel)).to_have_attribute("data-state", "up")
    entries = ui_page.evaluate("() => history.length")

    web = row(panel, "services/web")
    web.get_by_role("button", name="Open web", exact=True).click()
    expect(web.locator("moxi-ui-row")).to_have_attribute("open", "")
    expect_path(ui_page, "/m/services/row/web")
    # One thing open at a time.
    row(panel, "services/spare").get_by_role("button", name="Open spare", exact=True).click()
    expect(panel.locator("moxi-ui-row[open]")).to_have_count(1)
    expect_path(ui_page, "/m/services/row/spare")
    row(panel, "services/spare").get_by_role("button", name="Close spare", exact=True).click()
    expect(panel.locator("moxi-ui-row[open]")).to_have_count(0)
    expect_path(ui_page, "/m/services/")
    # The row's own line opens it too.
    web.locator("moxi-ui-row .l1").click()
    expect_path(ui_page, "/m/services/row/web")

    panel.get_by_role("button", name="Add a service", exact=True).click()
    expect(panel.locator("moxi-ui-row[open]")).to_have_count(0)
    expect_path(ui_page, "/m/services/add")
    form = panel.locator('section[data-part="add"] form')
    expect(form.get_by_label("Name", exact=True)).to_be_focused()
    form.get_by_label("a unit already here").check()
    expect_path(ui_page, "/m/services/adopt")
    form.get_by_label("a command line").check()

    # No entry was added, so Back leaves the page.
    assert ui_page.evaluate("() => history.length") == entries
    ui_page.go_back()
    expect_path(ui_page, "/m/ui/")


# --- the list's states ----------------------------------------------------------------------

def test_the_first_read_shows_reading_until_it_answers(seeded, ui_page):
    held: list = []
    ui_page.route("**/m/services/api/rows", lambda route: held.append(route))
    ui_page.goto("/m/services/")
    panel = ui_page.locator("#moxi-panel > moxi-services-page")
    expect(list_part(panel)).to_have_attribute("data-state", "loading")
    reading = list_part(panel).locator('[data-line="loading"]')
    expect(reading).to_have_text("Reading…")
    expect(panel.locator("moxi-ui-row")).to_have_count(0)
    expect(panel.get_by_role("button", name="Add a service")).to_have_count(0)
    for route in held:
        route.continue_()
    ui_page.unroute("**/m/services/api/rows")
    expect(list_part(panel)).to_have_attribute("data-state", "up")
    expect(reading).to_have_count(0)


def test_a_page_server_that_isnt_answering_keeps_the_last_rows_dim_with_no_acts(seeded, ui_page):
    ui_page.goto("/m/services/row/web")
    panel = ui_page.locator("#moxi-panel > moxi-services-page")
    web = row(panel, "services/web").locator("moxi-ui-row")
    expect(web).to_have_attribute("open", "")
    drawn = panel.locator(".item").count()
    ui_page.route("**/m/services/api/rows", lambda route: route.abort())
    try:
        ui_page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
        expect(list_part(panel)).to_have_attribute("data-state", "down")
        first = panel.locator('section[data-part="first"]')
        expect(first.locator('[data-line="down"]')).to_have_text(NOT_ANSWERING)
        expect(first.locator('[data-line="state-more"]')).to_have_text(
            re.compile(r"^Last read .+\. It may have changed since\.$"))
        expect(panel.locator(".item")).to_have_count(drawn)
        expect(panel.locator(".grps.dim")).to_have_count(1)
        for gone in ('[data-action="open"]', '[data-part="acts"] button', 'section[data-part="first"] [data-action="add"]',
                     "moxi-ui-row[open]", "moxi-ui-row[toggles]"):
            expect(panel.locator(gone)).to_have_count(0)
        dim = panel.evaluate("""page => {
          const style = getComputedStyle(page.shadowRoot.querySelector(".grps.dim"));
          return [style.getPropertyValue("--moxi-text").trim(), style.getPropertyValue("--moxi-text-dim").trim()];
        }""")
        assert dim[0] == dim[1] and dim[0] != "", dim
    finally:
        ui_page.unroute("**/m/services/api/rows")
    ui_page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
    expect(list_part(panel)).to_have_attribute("data-state", "up")
    expect(web).to_have_attribute("open", "")


def test_a_store_that_cant_be_read_shows_its_sentence_and_no_rows(seeded, ui_page):
    shown_without_the_topic(ui_page)
    detail = "services' store is newer than this Moxi; fix: upgrade Moxi"
    as_rows(ui_page, {"state": "error", "detail": detail, "at": None, "rows": [], "problems": []})
    ui_page.goto("/m/services/")
    panel = ui_page.locator("#moxi-panel > moxi-services-page")
    expect(list_part(panel)).to_have_attribute("data-state", "error")
    expect(list_part(panel).locator('[data-line="error"]')).to_have_text(detail)
    expect(panel.locator("moxi-ui-row")).to_have_count(0)
    expect(panel.get_by_role("button", name="Add a service")).to_have_count(0)


def test_every_row_unknown_for_one_reason_says_the_supervisor_cant_be_read(seeded, ui_shell, ui_page):
    value = get(ui_shell, "api/rows")
    reason = "the supervisor didn't answer in time"
    for r in value["rows"]:
        r["verdict"] = {"state": "warn", "reason": reason, "fix": None, "unknown": True, "cause": "unknown"}
    shown_without_the_topic(ui_page)
    as_rows(ui_page, value)
    ui_page.goto("/m/services/")
    panel = ui_page.locator("#moxi-panel > moxi-services-page")
    expect(list_part(panel)).to_have_attribute("data-state", "unread")
    first = panel.locator('section[data-part="first"]')
    expect(first.locator('[data-line="unread"]')).to_have_text("Can't read the supervisor")
    expect(first.locator('[data-line="state-more"]')).to_have_text(reason)
    # None rises: each stays in its group, dim.
    expect(first.locator(".item")).to_have_count(0)
    expect(panel.locator('section[data-part="group"] .item')).to_have_count(len(value["rows"]))
    expect(panel.locator(".grps.dim")).to_have_count(1)


def test_no_row_of_his_says_what_a_service_is(seeded, ui_shell, ui_page):
    value = get(ui_shell, "api/rows")
    # Moxi's rows only, running: the fake supervisor reads no module unit, and every row
    # unknown for one reason is a state of its own.
    value["rows"] = [r for r in value["rows"] if r["form"] == "module"]
    for r in value["rows"]:
        r["verdict"] = {"state": "ok", "reason": "running", "fix": None, "unknown": False, "cause": "running"}
    value["problems"] = [{"module": "notes", "reason": "its declarations failed: boom"}]
    shown_without_the_topic(ui_page)
    as_rows(ui_page, value)
    ui_page.goto("/m/services/")
    panel = ui_page.locator("#moxi-panel > moxi-services-page")
    expect(list_part(panel)).to_have_attribute("data-state", "up")
    first = panel.locator('section[data-part="first"]')
    expect(first.locator('[data-line="sentence"]')).to_have_text(
        "Nothing is down. You have added no services yet.")
    expect(first.locator('[data-line="counts"]')).to_have_count(0)
    expect(first.locator('[data-action="add"]')).to_have_count(0)
    yours = panel.locator('section[data-part="group"][data-group="his"]')
    expect(yours.locator('[data-line="group"]')).to_have_text("Yours")
    expect(yours.locator('[data-line="what"]')).to_have_text(
        "A service is a command this computer keeps running, such as a dev server, or runs on a "
        "schedule, such as a nightly backup.")
    expect(yours.get_by_role("button", name="Add a service", exact=True)).to_have_count(1)
    expect(list_part(panel).locator('[data-line="module-problem"]')).to_have_text(
        "notes: its declarations failed: boom; its static daemons and timers are listed")


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
    # The page read the rows again: a stop by hand is recorded, and the fact says so.
    expect(item).to_have_attribute("data-verdict", "warn")
    expect(item.locator('[data-line="fact"]')).to_contain_text("stopped by hand")
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

@pytest.mark.parametrize(("name", "port", "tick", "verdict"), [
    ("notebook", 8123, False, "off"),
    ("jupyter", 8124, True, "ok"),
], ids=["left-off", "turned-on"])
def test_the_add_form_takes_a_202_and_reads_the_status_until_the_job_ends(seeded, ui_shell, ui_page,
                                                                          name, port, tick, verdict):
    """A written row starts off, as the command line leaves it; ticking "Turn it on now"
    starts it."""
    panel = panel_of(ui_page)
    form = panel.locator('section[data-part="add"] form')
    expect(form.get_by_label("Project")).to_contain_text("alpha")
    form.get_by_label("Name", exact=True).fill(name)
    form.get_by_role("textbox", name="Command line").fill(f"/usr/bin/python3 -m http.server {port}")
    form.get_by_label("Project").select_option("alpha")
    form.get_by_label("Doc").fill("a notebook server")
    turn_on = form.get_by_label("Turn it on now")
    expect(turn_on).not_to_be_checked()
    if tick:
        turn_on.check()
    statuses: list[str] = []
    ui_page.on("request", lambda r: statuses.append(r.url) if "/m/services/api/status?job=" in r.url else None)
    with ui_page.expect_response("**/m/services/api/add") as added:
        form.get_by_role("button", name="Add", exact=True).click()
    assert added.value.status == 202, added.value.text()
    assert added.value.request.post_data_json["enable"] is tick
    link = added.value.json()["status"]
    assert link.startswith("/api/status?job="), link
    said = form.locator('[data-line="said"]')
    expect(said).to_have_attribute("data-tone", "ok")
    expect(said).to_contain_text(f"Add {name}: Done.")
    assert any(url.endswith(link) for url in statuses), statuses
    assert name in stored(ui_shell)
    item = row(panel, f"services/{name}")
    expect(item).to_have_count(1)
    expect(item).to_have_attribute("data-verdict", verdict)
    expect(panel.locator(f'section[data-project="alpha"] .item[data-id="services/{name}"]')).to_have_count(1)
    # The form starts again once the add is done, the box off again.
    expect(form.get_by_label("Name", exact=True)).to_have_value("")
    expect(turn_on).not_to_be_checked()


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
    ui_width_check(ui_page, module="services", name="summary")
