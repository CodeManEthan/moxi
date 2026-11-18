"""services' page and home block in a browser, through a real shell that starts services'
real page server over a fake supervisor (`browser_kit`): the contract check on both, the
block's count of rows by verdict, the page's frame (no heading of its own), the first
group's risen rows (SV1), a running daemon's time (SV2), a timer's runs (SV3), a run that
spends usage (SV4), the groups by project and Moxi's (SV5), a failed daemon's state and
log (SV6), a project's address (SV7), each address and what the page writes back, the
list's states, the opened row's lines, the acts on the closed row and in the foot as plain
presses followed to their end (done, did not hold, refused, a lost answer), a Moxi
daemon's stop by hand, the run sheet of a row that launches a session, the add form's 202
and the status it reads until the job ends, and the 360 px width check.
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

#: What a run of a row that launches a session adds to its sheet.
SPENDS = "Each run launches an agent session, so this run spends usage."
VERDICTS = ("ok", "warn", "fail", "off")
#: When `paused` was stopped by hand: now, so the stop is after the last boot and still
#: holds (a stop from before the boot is dropped, and a fixed time fails on any machine
#: booted since).
STOPPED = datetime.now(timezone.utc).replace(microsecond=0)
#: When `web` started, in seconds since the epoch: a minute ago, so it reads a time today.
WEB_SINCE = time.time() - 60
#: When `backup` ran, in seconds since the epoch: 15, 10 and 5 minutes ago, oldest first.
BACKUP_RAN = [time.time() - 60 * m for m in (15, 10, 5)]
NOT_ANSWERING = "Services' page server isn't answering"

#: The fake supervisor's units: his by row name, Moxi's by id, and the acts that fail.
UNITS = {"written": {
    "web": {"kind": "daemon", "enabled": True, "active": True, "since": WEB_SINCE, "pid": 4242},
    "sync-down": {"kind": "daemon", "enabled": True, "active": False},
    "paused": {"kind": "daemon", "enabled": True, "active": False},
    "spare": {"kind": "daemon", "enabled": False, "active": False},
    "backup": {"kind": "timer", "enabled": True,
               "runs": [{"at": f"{datetime.fromtimestamp(at, timezone.utc):%Y-%m-%dT%H:%M:%SZ}", "result": "ran",
                         "exit": 0, "seconds": 1.9} for at in BACKUP_RAN]},
    "agent-run": {"kind": "timer", "enabled": True},
    "stopme": {"kind": "daemon", "enabled": True, "active": True},
    "lostone": {"kind": "daemon", "enabled": False, "active": False},
    "startme": {"kind": "daemon", "enabled": True, "active": False},
    "nostart": {"kind": "daemon", "enabled": True, "active": False},
    "queue-worker": {"kind": "daemon", "enabled": True, "active": False, "failed": True},
    "dep-audit": {"kind": "timer", "enabled": True,
                  "runs": [{"at": "2026-11-08T18:00:00Z", "result": "ran", "exit": 1}]},
}, "modules": {
    # A Moxi daemon with no switch, running, for its Stop by hand.
    "cache/watch": {"kind": "daemon", "enabled": True, "active": True},
}, "fail": [["start", "nostart"]]}


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
    `dep-audit` a timer whose last run exited 1. `stopme`, `lostone`, `startme` and
    `nostart` (whose start fails) are the acts' own, so the tests run in any order."""
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
         written("startme", after={"stopped": f"{STOPPED:%Y-%m-%dT%H:%M:%SZ}"}),
         written("nostart", after={"stopped": f"{STOPPED:%Y-%m-%dT%H:%M:%SZ}"}),
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


# --- the opened row ----------------------------------------------------------------------

def opened(panel, ident: str, name: str):
    """Opens the row `ident` by its `Open` and gives its opened part."""
    item = row(panel, ident)
    item.get_by_role("button", name=f"Open {name}", exact=True).click()
    part = item.locator('[data-part="opened"]')
    expect(part).to_be_visible()
    return part


def label(part, name: str):
    return part.locator(f'[data-line="{name}"]')


def test_sv3_a_timer_says_when_it_last_ran_and_opened_lists_its_runs(seeded, ui_page):
    panel = panel_of(ui_page)
    last = clock(ui_page, BACKUP_RAN[-1])
    expect(row(panel, "services/backup").locator("moxi-ui-row")).to_have_attribute(
        "fact", re.compile(rf"^last ran {last.pattern}"))
    part = opened(panel, "services/backup", "backup")
    state = label(part, "State")
    expect(state.locator('[data-line="more"]').first).to_have_text("On schedule: every 5 minutes")
    expect(state.locator('[data-line="more"]').nth(1)).to_have_text(re.compile(r"^\d+ runs? today, none failed$"))
    runs = label(part, "Last runs").locator('[data-line="more"]')
    # Newest first, each `<time> ok · 1.9 s`.
    expect(runs).to_have_text([re.compile(rf"^{clock(ui_page, at).pattern} ok · 1\.9 s$") for at in reversed(BACKUP_RAN)])
    expect(label(part, "Command").locator('[data-line="mono"]')).to_have_text("/usr/bin/true")
    expect(label(part, "Command").locator('[data-line="dim"]')).to_have_text(" · on a schedule · on")
    expect_path(ui_page, "/m/services/row/backup")


def test_sv4_a_timer_whose_runs_launch_a_session_says_it_spends_usage(seeded, ui_page):
    panel = panel_of(ui_page)
    expect(row(panel, "services/agent-run").locator("moxi-ui-row")).to_have_attribute(
        "where", re.compile(r"spends usage$"))
    part = opened(panel, "services/agent-run", "agent-run")
    expect(label(part, "Usage").locator('[data-line="text"]')).to_have_text(
        "Its runs launch agent sessions, so they spend usage")
    # A row whose runs launch nothing has no Usage line.
    part = opened(panel, "services/backup", "backup")
    expect(label(part, "Last runs")).to_have_count(1)
    expect(label(part, "Usage")).to_have_count(0)


def test_sv6_a_failed_daemon_opened_says_its_state_and_how_to_read_its_log(seeded, ui_shell, ui_page):
    ui_page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    reads = []
    ui_page.on("request", lambda request: reads.append(request.url) if "/m/services/api/row?" in request.url else None)
    panel = panel_of(ui_page)
    part = opened(panel, "services/queue-worker", "queue-worker")
    one = get(ui_shell, "api/row?id=services/queue-worker")
    expect(label(part, "State").locator('[data-line="text"]')).to_have_text("It failed and is not running.")
    log = label(part, "Log")
    expect(log.locator('[data-line="mono"]')).to_have_text(one["log"])
    assert one["log"].startswith("journalctl --user -u "), one["log"]
    log.get_by_role("button", name=f"Copy {one['log']}").click()
    expect(log.get_by_role("button", name=f"Copy {one['log']}")).to_have_text("Copied")
    assert ui_page.evaluate("navigator.clipboard.readText()") == one["log"]
    unit = label(part, "Unit")
    expect(unit.locator('[data-line="mono"]')).to_have_text(one["unit"])
    expect(unit.locator('[data-line="dim"]')).to_have_text(" · a command you added; Moxi wrote the unit")
    # Details are folded until asked for, and hold the row's id and its units.
    details = label(part, "Details")
    expect(details.locator('[data-line="more"]').first).not_to_be_visible()
    details.locator("summary").click()
    expect(details.locator('[data-line="more"]', has_text="services/queue-worker")).to_be_visible()
    for ident in one["units"]:
        expect(details.locator('[data-line="more"]', has_text=ident)).to_have_count(1)
    # Its own read is of this row; closing drops it.
    assert reads and all("queue-worker" in url for url in reads), reads
    row(panel, "services/queue-worker").get_by_role("button", name="Close queue-worker", exact=True).click()
    expect(part).to_have_count(0)


def test_a_moxi_row_opened_links_its_module_and_settings(seeded, ui_shell, ui_page):
    listed = get(ui_shell, "api/rows")["rows"]
    mod = next(r for r in listed if r["form"] == "module")
    panel = panel_of(ui_page)
    part = opened(panel, mod["id"], mod["name"])
    expect(label(part, "State")).to_have_count(1)
    expect(label(part, "Command")).to_have_count(0)
    links = part.locator('[data-part="links"] a')
    expect(links.first).to_have_attribute("href", re.compile(rf"^/m/({mod['module']}/|ui/modules)$"))
    if mod["has_settings"]:
        expect(links.last).to_have_text(f"Settings › {mod['module']}")


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


@pytest.mark.parametrize("route", ["project/nope", "project/nope/row/web"])
def test_a_project_no_row_belongs_to_keeps_the_filter_and_says_no_service_belongs(seeded, ui_shell, ui_page, route):
    # services can't tell whether the project exists, so the page never says it doesn't.
    ui_page.goto(f"/m/services/{route}")
    panel = ui_page.locator("#moxi-panel > moxi-services-page")
    expect(list_part(panel)).to_have_attribute("data-state", "up")
    filt = panel.locator('[data-part="filter"]')
    expect(filt.locator('[data-line="project"]')).to_have_text("nope")
    listed = get(ui_shell, "api/rows")["rows"]
    expect(filt.locator('[data-line="shown"]')).to_have_text(f"0 of {services(len(listed))}")
    expect(list_part(panel).locator('[data-line="no-service"]')).to_have_text("No service belongs to nope.")
    expect(list_part(panel)).not_to_contain_text("No project")
    expect(list_part(panel).locator('[data-line="unknown"]')).to_have_count(0)
    expect(panel.locator(".item")).to_have_count(0)
    expect_path(ui_page, "/m/services/project/nope")
    filt.get_by_role("button", name="Show all", exact=True).click()
    expect(list_part(panel).locator('[data-line="no-service"]')).to_have_count(0)
    expect(panel.locator(".item")).to_have_count(len(listed))
    expect_path(ui_page, "/m/services/")


@pytest.mark.parametrize(("route", "line"), [
    ("row/nosuch", "No service is named nosuch."),
    ("row/ui/nosuch", "No service is named ui/nosuch."),
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

#: A job key as the page mints one for `/api/act`.
JOB = re.compile(r"^[0-9a-f]{32}$")


def status_of(body: dict, state: str, lines: list | None = None) -> dict:
    """A job's status, as `/api/status` gives it, for the act `body` posted."""
    return {"job": body["job"], "act": body["act"], "id": body["id"], "state": state, "code": None,
            "lines": lines or [], "fix": None}


def test_the_closed_row_offers_one_act_and_the_foot_the_rest(seeded, ui_page):
    panel = panel_of(ui_page)
    expect(panel.locator('[data-part="acts"] button')).to_have_count(0)

    def closed(ident: str):
        return row(panel, ident).locator('moxi-ui-row > button[data-action]:not([data-action="open"])')

    expect(closed("services/sync-down")).to_have_text(["Start"])
    expect(closed("services/dep-audit")).to_have_text(["Run now"])
    expect(closed("services/web")).to_have_count(0)
    expect(closed("services/backup")).to_have_count(0)
    part = opened(panel, "services/web", "web")
    expect(part.locator('[data-part="foot"] button')).to_have_text(["Restart", "Stop", "Turn off", "Remove"])
    expect(part.locator('[data-line="stop-lasts"]')).to_have_text(
        "Stop lasts until the next login. Turn off keeps it off.")
    part = opened(panel, "services/backup", "backup")
    expect(part.locator('[data-part="foot"] button')).to_have_text(
        ["Run now", "Turn off", "Remove", "Run again", "Stop run"])
    expect(part.locator('[data-line="stop-lasts"]')).to_have_count(0)


def test_start_from_a_closed_row_reads_starting_then_running(seeded, ui_shell, ui_page):
    panel = panel_of(ui_page)
    posts = posts_to(ui_page, "api/act")
    item = row(panel, "services/startme")
    drawn = item.locator("moxi-ui-row")
    expect(item.locator('[data-line="fact"]')).to_contain_text("stopped by hand")
    held: list = []

    def first_running(route) -> None:
        # The first read of the job says it runs, so the page shows it doing so.
        if not held:
            held.append(route.request.url)
            route.fulfill(json=status_of(posts[0].post_data_json, "running"))
        else:
            route.continue_()

    ui_page.route("**/m/services/api/status?job=*", first_running)
    try:
        item.get_by_role("button", name="Start startme", exact=True).click()
        expect(drawn).to_have_attribute("fact", "starting")
        expect(panel.get_by_role("button", name="Open web", exact=True)).to_be_enabled()
        # The fake supervisor gives no start time, so the fact reads `running` alone.
        expect(drawn).to_have_attribute("fact", re.compile(r"^running"))
    finally:
        ui_page.unroute("**/m/services/api/status?job=*")
    assert len(posts) == 1, posts
    body = posts[0].post_data_json
    assert JOB.match(body.pop("job")) and body == {"act": "start", "id": "services/startme"}, body
    assert "idempotency-key" not in posts[0].headers
    expect(item.locator('[data-part="opened"]')).to_have_count(0)


def test_a_start_that_does_not_hold_keeps_the_row_open_with_its_lines(seeded, ui_page):
    panel = panel_of(ui_page)
    item = row(panel, "services/nostart")
    item.get_by_role("button", name="Start nostart", exact=True).click()
    part = item.locator('[data-part="opened"]')
    ended = part.locator('[data-line="ended"]')
    expect(ended.locator('[data-line="heavy"]')).to_have_text("Start did not hold.")
    expect(ended.locator('[data-line="job-line"]').first).to_contain_text("nostart")
    expect(ended.get_by_role("button", name="Start again nostart", exact=True)).to_be_visible()
    expect_path(ui_page, "/m/services/row/nostart")
    # Closing the row lets it go.
    item.get_by_role("button", name="Close nostart", exact=True).click()
    item.get_by_role("button", name="Open nostart", exact=True).click()
    expect(part.locator('[data-line="ended"]')).to_have_count(0)


def test_a_stop_from_the_foot_reads_stopped_by_hand(seeded, ui_shell, ui_page):
    panel = panel_of(ui_page)
    posts = posts_to(ui_page, "api/act")
    item = row(panel, "services/stopme")
    part = opened(panel, "services/stopme", "stopme")
    with ui_page.expect_response(lambda r: "/m/services/api/status?job=" in r.url
                                 and r.json().get("state") != "running") as ended:
        part.get_by_role("button", name="Stop stopme", exact=True).click()
    assert ended.value.json()["state"] == "done", ended.value.json()
    body = posts[0].post_data_json
    assert body["act"] == "stop" and body["id"] == "services/stopme" and JOB.match(body["job"]), body
    expect(item).to_have_attribute("data-verdict", "warn")
    # A fact in the text colour is the page's own, at line one's right.
    expect(item.locator('[data-line="fact"]')).to_contain_text("stopped by hand")
    expect(part.locator('[data-part="foot"] button').first).to_have_text("Start")
    status = get(ui_shell, f"api/status?job={body['job']}")
    assert (status["act"], status["id"], status["state"]) == ("stop", "services/stopme", "done"), status


def test_a_moxi_daemon_stopped_by_hand_reads_so_and_does_not_rise(seeded, ui_shell, ui_page):
    listed = {r["id"]: r for r in get(ui_shell, "api/rows")["rows"]}
    assert "cache/watch" in listed, sorted(listed)
    panel = panel_of(ui_page)
    item = row(panel, "cache/watch")
    part = opened(panel, "cache/watch", listed["cache/watch"]["name"])
    foot = part.locator('[data-part="foot"] button')
    expect(foot).to_have_text(["Restart", "Stop"])
    expect(part.locator('[data-line="stop-lasts"]')).to_have_count(0)
    foot.nth(1).click()
    # A fact in the text colour is the page's own, at line one's right.
    expect(item.locator('[data-line="fact"]')).to_contain_text("stopped by hand")
    expect(item).to_have_attribute("data-verdict", "warn")
    expect(item).to_have_attribute("data-cause", "stopped")
    expect(panel.locator('section[data-part="first"] .item[data-id="cache/watch"]')).to_have_count(0)
    expect(panel.locator('section[data-group="moxi"] .item[data-id="cache/watch"]')).to_have_count(1)
    now = {r["id"]: r for r in get(ui_shell, "api/rows")["rows"]}
    assert now["cache/watch"]["verdict"]["cause"] == "stopped", now["cache/watch"]


def test_run_again_and_stop_run_on_the_opened_timer(seeded, ui_page):
    panel = panel_of(ui_page)
    posts = posts_to(ui_page, "api/act")
    part = opened(panel, "services/backup", "backup")
    for name, act, extra in (("Run again", "run", {"restart": True}), ("Stop run", "stop", {})):
        with ui_page.expect_response(lambda r: "/m/services/api/status?job=" in r.url
                                     and r.json().get("state") != "running") as ended:
            part.get_by_role("button", name=f"{name} backup", exact=True).click()
        assert ended.value.json()["state"] == "done", ended.value.json()
        body = posts[-1].post_data_json
        assert JOB.match(body.pop("job")) and body == {"act": act, "id": "services/backup", **extra}, body
        expect(part.locator('[data-line="ended"]')).to_have_count(0)
    assert len({p.post_data_json["job"] for p in posts}) == 2


def test_an_act_the_job_refuses_says_not_done(seeded, ui_page):
    panel = panel_of(ui_page)
    posts = posts_to(ui_page, "api/act")
    ui_page.route("**/m/services/api/status?job=*", lambda route: route.fulfill(
        json=status_of(posts[-1].post_data_json, "refused", ["web: not now"])))
    try:
        part = opened(panel, "services/web", "web")
        part.get_by_role("button", name="Restart web", exact=True).click()
        ended = part.locator('[data-line="ended"]')
        expect(ended.locator('[data-line="heavy"]')).to_have_text("Not done.")
        expect(ended.locator('[data-line="job-line"]')).to_have_text(["web: not now"])
        expect(ended.get_by_role("button", name="Restart web", exact=True)).to_be_visible()
    finally:
        ui_page.unroute("**/m/services/api/status?job=*")


def test_a_lost_answer_says_the_act_may_have_been_done(seeded, ui_page):
    panel = panel_of(ui_page)

    def lost(route) -> None:
        route.fetch()               # the act is taken, and its answer never arrives
        route.fulfill(status=504, json={"error": "timeout"})

    ui_page.route("**/m/services/api/act", lost)
    try:
        part = opened(panel, "services/lostone", "lostone")
        part.get_by_role("button", name="Turn on lostone", exact=True).click()
        said = row(panel, "services/lostone").locator('[data-line="said"]')
        expect(said).to_have_attribute("data-tone", "warn")
        expect(said).to_have_text("Services' page server didn't answer; it may have been done.")
    finally:
        ui_page.unroute("**/m/services/api/act")


def test_a_run_of_a_row_that_launches_a_session_says_it_spends_usage(seeded, ui_page):
    panel = panel_of(ui_page)
    posts = posts_to(ui_page, "api/run")
    sheet = ui_page.locator("moxi-ui-confirm").get_by_role("dialog")
    part = opened(panel, "services/agent-run", "agent-run")
    part.get_by_role("button", name="Run now · spends usage agent-run", exact=True).click()
    expect(sheet).to_contain_text(SPENDS)
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
