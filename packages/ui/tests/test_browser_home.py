"""Home in a browser: the main spot and its sentence, the rows Home draws from what row
components report (nothing to report, a late report, needing the user, opening in place, held
open, a script that does not load), the column moving aside for a main piece's side panel,
the Health row on a kept run and on a real one, a small install, the colours and the
layout at 360 px.

The module's one test shell serves `ui` and the `fixture` module, whose two rows report
only what a test tells them to (tests/fixture/moxi_fixture/web/rows.js). It runs no
doctor and keeps no run, so its Health row reads `Checking…`; the tests of Health start
shells of their own. The tests of the main spot install the fixture with its main piece
(tests/fixture/moxi_fixture/web/main.js)."""

from __future__ import annotations

import json
import re
import time

import pytest
from moxi import modules
from moxi_ui.checks import CHECKS_FILE
from moxi_ui.testing.shell import TestShell
from playwright.sync_api import expect

import fixture_kit
from fixture_kit import CHECK_DETAIL as DETAIL
from fixture_kit import CHECK_FIX as FIX

pytestmark = [pytest.mark.browser, pytest.mark.timeout(90)]

# The doctor runs `moxi doctor --json` in a child; give it longer than a click.
DOCTOR_MS = 30_000

SENTENCE = "Nothing needs you."
ONE, TWO, HEALTH = "fixture.row-one", "fixture.row-two", "ui.health"

FAIL = {"module": "turns", "name": "guard", "state": "fail",
        "detail": "A guard could not check a call, and the call ran unchecked",
        "fix": "moxi config set turns guard.strict false"}
WARN = {"module": "projects", "name": "roots", "state": "warn",
        "detail": "The project proj-a points at a folder that doesn't exist",
        "fix": "moxi projects set proj-a --root DIR"}
PASS = {"module": "ui", "name": "shell", "state": "ok", "detail": "The shell answers",
        "fix": None}

_SAY = """([row, report]) => window.dispatchEvent(
  new CustomEvent("moxi-fixture-row", {detail: {row, report}}))"""

_SIDE = """(detail) => window.dispatchEvent(new CustomEvent("moxi-fixture-side", {detail}))"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _home(page):
    return page.locator("#moxi-panel > moxi-ui-page moxi-ui-home")


def _entry(page, key: str):
    return _home(page).locator(f".entry[data-row='{key}']")


def _line(page, key: str):
    return _entry(page, key).locator(".row")


def _say(page, row: str, report) -> None:
    """Have the fixture's row `row` report `report` to Home."""
    expect(_home(page).locator(f"moxi-fixture-{row}")).to_be_attached()
    page.evaluate(_SAY, [row, report])


def _shown(page) -> list[str]:
    """The rows in sight, top first."""
    return _home(page).locator(".entry").evaluate_all(
        """entries => entries.filter((entry) => entry.getBoundingClientRect().height > 0)
             .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top)
             .map((entry) => entry.dataset.row)""")


def _expect_shown(page, keys: list[str]) -> None:
    for key in keys:
        expect(_line(page, key)).to_be_visible()
    expect(_home(page).locator(".entry:not([hidden])")).to_have_count(len(keys))
    assert _shown(page) == keys


def _kept(home, run: dict) -> None:
    """Write `run` as the doctor run the shell under `home` kept."""
    state = TestShell(home).state_dir("ui")
    state.mkdir(parents=True, exist_ok=True)
    (state / CHECKS_FILE).write_text(json.dumps(run))


def _others() -> list[str]:
    """Every installed module but ui. A shell that leaves them out shows Home as ui and
    the fixture make it, whatever else this environment holds: another module's main
    piece would take the spot, and its rows would join the fixture's."""
    return [found.name for found in modules.scan() if found.name != "ui"]


@pytest.fixture(scope="module")
def ui_shell_options(fixture_site):
    return {"site": [fixture_site], "leave_out": _others()}


@pytest.fixture(scope="module")
def kept_shell(ui_shells, fixture_site, tmp_path_factory):
    """A shell over the fixture that kept a run with one failing check, one warning and
    one that passes, and runs no doctor."""
    home = tmp_path_factory.mktemp("kept")
    _kept(home, {"at": _now(), "rows": [PASS, WARN, FAIL], "error": None})
    return ui_shells(home=home, site=[fixture_site])


# --- the spot and the rows ---------------------------------------------------------------

def test_home_is_the_sentence_and_the_health_row_until_a_row_reports(ui_page):
    home = _home(ui_page)
    expect(home.get_by_text(SENTENCE)).to_be_visible()
    _expect_shown(ui_page, [HEALTH])
    expect(_line(ui_page, HEALTH).locator(".label")).to_have_text("Health")
    expect(_line(ui_page, HEALTH).locator(".fact")).to_have_text("Checking…")
    # The fixture's rows are placed and have nothing to report.
    expect(home.locator("moxi-fixture-row-one")).to_be_attached()
    expect(home.locator("moxi-fixture-row-one")).to_be_hidden()
    expect(ui_page.locator("#moxi-pages-button")).to_contain_text("Home")
    expect(ui_page).to_have_title("Home - Moxi")
    expect(ui_page.locator("h1")).to_have_count(0)
    # The sentence sits in a spot 150 px high, and the rows start 40 px under it.
    spot = home.locator(".spot").bounding_box()
    assert spot["height"] == 150 and spot["width"] == 680
    assert home.locator(".rows").bounding_box()["y"] == spot["y"] + 150 + 40


def test_a_row_shows_what_its_component_reports_and_goes_when_it_reports_null(ui_page):
    _say(ui_page, "row-one", {"parts": [{"text": "2 running", "heavy": True},
                                        {"text": " · coder goes cold at 19:22"}],
                              "time": "read 19:08"})
    _expect_shown(ui_page, [ONE, HEALTH])
    line = _line(ui_page, ONE)
    expect(line.locator(".label")).to_have_text("Fixture one")
    expect(line.locator(".fact")).to_have_text("2 running · coder goes cold at 19:22")
    expect(line.locator(".fact .heavy")).to_have_text("2 running")
    expect(line.locator(".fact .heavy")).to_have_css("font-weight", "600")
    expect(line.locator(".time")).to_have_text("read 19:08")
    # Home draws the row: the label's column is 96 px, and the line is one line.
    label, fact = line.locator(".label").bounding_box(), line.locator(".fact").bounding_box()
    assert fact["x"] - label["x"] == 96
    assert line.bounding_box()["height"] == _line(ui_page, HEALTH).bounding_box()["height"]
    _say(ui_page, "row-one", None)
    _expect_shown(ui_page, [HEALTH])
    expect(_home(ui_page).get_by_text(SENTENCE)).to_be_visible()


def test_a_long_fact_is_cut_on_one_line(ui_page):
    _say(ui_page, "row-one", {"parts": [{"text": "a fact that runs on " * 12}], "time": "now"})
    fact = _line(ui_page, ONE).locator(".fact")
    expect(fact).to_have_css("text-overflow", "ellipsis")
    assert fact.evaluate("node => node.scrollWidth > node.clientWidth")
    assert _line(ui_page, ONE).bounding_box()["height"] == _line(ui_page, HEALTH).bounding_box()["height"]


def test_a_row_shows_from_a_first_report_that_comes_long_after_home_opened(ui_page):
    ui_page.clock.install()
    ui_page.reload()
    _expect_shown(ui_page, [HEALTH])
    expect(_home(ui_page).locator("moxi-fixture-row-two")).to_be_attached()
    ui_page.clock.fast_forward(8_000)
    _say(ui_page, "row-two", {"parts": [{"text": "here at last"}]})
    _expect_shown(ui_page, [TWO, HEALTH])


def test_a_row_that_needs_the_user_rises_above_the_rest_and_the_sentence_goes(ui_page):
    quiet = {"parts": [{"text": "nothing much"}]}
    _say(ui_page, "row-one", quiet)
    _say(ui_page, "row-two", quiet)
    _expect_shown(ui_page, [ONE, TWO, HEALTH])
    expect(_home(ui_page).get_by_text(SENTENCE)).to_be_visible()
    _say(ui_page, "row-two", {"parts": [{"text": "waits for you"}], "needs": 20})
    _expect_shown(ui_page, [TWO, ONE, HEALTH])
    # The spot keeps its room and says nothing while a row needs the user.
    expect(_home(ui_page).get_by_text(SENTENCE)).to_have_count(0)
    assert _home(ui_page).locator(".spot").bounding_box()["height"] == 150
    # A lower number is the more pressing, whatever the row's fixed place.
    _say(ui_page, "row-one", {"parts": [{"text": "someone is blocked on you"}], "needs": 10})
    _expect_shown(ui_page, [ONE, TWO, HEALTH])
    _say(ui_page, "row-one", {"parts": [{"text": "will run out"}], "needs": 40})
    _expect_shown(ui_page, [TWO, ONE, HEALTH])
    # No row changes colour for it.
    assert (_line(ui_page, TWO).evaluate("node => getComputedStyle(node).color")
            == _line(ui_page, HEALTH).evaluate("node => getComputedStyle(node).color"))
    _say(ui_page, "row-one", quiet)
    _say(ui_page, "row-two", quiet)
    _expect_shown(ui_page, [ONE, TWO, HEALTH])
    expect(_home(ui_page).get_by_text(SENTENCE)).to_be_visible()


def test_a_row_that_opens_shows_its_component_in_place_until_hide(ui_page):
    _say(ui_page, "row-one", {"parts": [{"text": "three things"}], "time": "read 19:08",
                              "opens": True})
    _expect_shown(ui_page, [ONE, HEALTH])
    home = _home(ui_page)
    row = home.get_by_role("button", name=re.compile("Fixture one"))
    inside = home.locator("moxi-fixture-row-one").get_by_test_id("inside")
    expect(row).to_have_attribute("aria-expanded", "false")
    expect(inside).to_be_hidden()
    below = _line(ui_page, HEALTH).bounding_box()["y"]
    row.click()
    expect(row).to_have_attribute("aria-expanded", "true")
    expect(inside).to_be_visible()
    # Home told the component it is open, and shows Hide where the time was.
    expect(inside).to_have_text("Inside row-one, opened")
    expect(row.locator(".time")).to_have_text("Hide")
    # It sits under its row at the fact's column and pushes the rows below down.
    assert inside.bounding_box()["x"] == row.locator(".fact").bounding_box()["x"]
    assert inside.bounding_box()["y"] >= row.bounding_box()["y"] + row.bounding_box()["height"]
    assert _line(ui_page, HEALTH).bounding_box()["y"] > below
    # The component may report other parts while it is open.
    _say(ui_page, "row-one", {"parts": [{"text": "two things"}], "opens": True})
    expect(row.locator(".fact")).to_have_text("two things")
    expect(inside).to_be_visible()
    row.click()
    expect(inside).to_be_hidden()
    expect(inside).to_have_text("Inside row-one")
    expect(row.locator(".time")).to_have_text("")
    assert _line(ui_page, HEALTH).bounding_box()["y"] == below


def test_a_row_that_does_not_open_leads_to_its_modules_page(ui_page):
    _say(ui_page, "row-two", {"parts": [{"text": "see the page"}]})
    _home(ui_page).get_by_role("link", name=re.compile("Fixture two")).click()
    expect(ui_page.locator("#moxi-panel > moxi-fixture-page")).to_be_attached()
    assert ui_page.url.endswith("/m/fixture/")


def test_a_row_that_reports_open_is_shown_opened_with_no_click_and_no_hide(ui_page):
    held = {"parts": [{"text": "two running"}], "time": "2 running", "opens": True, "open": True}
    _say(ui_page, "row-one", held)
    home = _home(ui_page)
    inside = home.locator("moxi-fixture-row-one").get_by_test_id("inside")
    expect(inside).to_be_visible()
    expect(inside).to_have_text("Inside row-one, opened")
    line = _line(ui_page, ONE)
    expect(line.locator(".time")).to_have_text("2 running")
    expect(home.get_by_role("button", name=re.compile("Fixture one"))).to_have_count(0)
    expect(home.get_by_role("link", name=re.compile("Fixture one"))).to_have_count(0)
    line.click()
    expect(inside).to_be_visible()
    # Once it stops asking, the row is closed and opens on a click like any other.
    _say(ui_page, "row-one", {**held, "open": False})
    expect(inside).to_be_hidden()
    row = home.get_by_role("button", name=re.compile("Fixture one"))
    expect(row).to_have_attribute("aria-expanded", "false")
    row.click()
    expect(inside).to_be_visible()
    expect(row.locator(".time")).to_have_text("Hide")


def test_a_row_whose_script_does_not_load_shows_nothing_and_stays_on_the_modules_view(ui_page):
    ui_page.route(re.compile(r"/m/fixture/.*row-two\.js"), lambda route: route.abort())
    ui_page.reload()
    home = _home(ui_page)
    _say(ui_page, "row-one", {"parts": [{"text": "still here"}]})
    _expect_shown(ui_page, [ONE, HEALTH])
    expect(home.locator("moxi-fixture-row-two")).to_have_count(0)
    expect(home.get_by_text("could not")).to_have_count(0)
    ui_page.goto("/m/ui/modules")
    card = ui_page.locator("#moxi-panel > moxi-ui-page .card[data-module=fixture]")
    expect(card).to_contain_text("Fixture two")


def test_a_main_piece_takes_the_spot_in_place_of_the_sentence(ui_shells, ui_contexts,
                                                              tmp_path_factory):
    site = fixture_kit.install(tmp_path_factory.mktemp("main-site"), main=True)
    page = ui_contexts(ui_shells(site=[site], leave_out=_others())).pages[0]
    home = _home(page)
    piece = home.locator(".spot moxi-fixture-main").get_by_test_id("main")
    expect(piece).to_have_text("The fixture's main piece")
    expect(home.get_by_text(SENTENCE)).to_have_count(0)
    _expect_shown(page, [HEALTH])
    # A row that needs the user leaves the piece where it is.
    _say(page, "row-one", {"parts": [{"text": "waits"}], "needs": 20})
    _expect_shown(page, [ONE, HEALTH])
    expect(piece).to_be_visible()
    # The modules view places blocks only, not the piece and not the rows.
    page.goto("/m/ui/modules")
    blocks = page.locator("#moxi-panel > moxi-ui-page #home section[data-block]")
    expect(blocks.and_(page.locator("[data-block='fixture.block']"))).to_be_visible()
    named = blocks.evaluate_all("all => all.map((one) => one.dataset.block)")
    assert not {"fixture.main", "fixture.row-one", "fixture.row-two", "ui.health"} & set(named)


def test_the_sentence_waits_for_a_main_piece_and_shows_when_it_does_not_load(
        ui_shells, ui_contexts, tmp_path_factory):
    site = fixture_kit.install(tmp_path_factory.mktemp("slow-site"), main=True)
    shell = ui_shells(site=[site], leave_out=_others())
    context = ui_contexts(shell, sign_in=False)
    held: list = []
    # The piece's script is kept on its way, and any later try for it fails at once.
    context.route(re.compile(r"/m/fixture/main\.js"),
                  lambda route: route.abort() if held else held.append(route))
    page = shell.sign_in(context.new_page())
    home = _home(page)
    _expect_shown(page, [HEALTH])
    # While the script is on its way the spot says nothing.
    expect(home.locator(".spot")).to_have_text("")
    assert len(held) == 1
    held[0].abort()
    # It did not load: the sentence shows.
    expect(home.locator(".spot")).to_have_text(SENTENCE)


# --- a main piece's side panel -------------------------------------------------------------

@pytest.fixture(scope="module")
def main_shell(ui_shells, tmp_path_factory):
    """A shell whose Home shows the fixture's main piece."""
    site = fixture_kit.install(tmp_path_factory.mktemp("side-site"), main=True)
    return ui_shells(site=[site], leave_out=_others())


def _side_page(main_shell, ui_contexts, width: int):
    """Home with the fixture's main piece placed, in a window `width` px wide."""
    page = ui_contexts(main_shell).pages[0]
    page.set_viewport_size({"width": width, "height": 800})
    expect(_home(page).locator(".spot moxi-fixture-main").get_by_test_id("main")).to_be_visible()
    return page


def _expect_centre(page, centre: float) -> None:
    """The column is 680 px wide with its centre `centre` px from the window's left."""
    expect(_home(page)).to_have_js_property("offsetWidth", 680)
    # Home sets the margin as it hears the event, before the call that sent it returns.
    assert _home(page).evaluate(
        "home => { const box = home.getBoundingClientRect(); return (box.left + box.right) / 2; }"
    ) == centre


def test_a_side_panel_moves_the_column_to_the_centre_of_what_it_leaves_and_0_puts_it_back(
        main_shell, ui_contexts):
    page = _side_page(main_shell, ui_contexts, 1400)
    _expect_centre(page, 700)
    page.evaluate(_SIDE, {"width": 400})
    _expect_centre(page, (1400 - 400) / 2)
    # The piece's fixed panel is still placed in the window: its right edge is the window's.
    panel = _home(page).locator("moxi-fixture-main").get_by_test_id("side")
    expect(panel).to_be_visible()
    box = panel.bounding_box()
    assert box["width"] == 400 and box["x"] + box["width"] == 1400 and box["height"] == 800
    # The column and the rows under it clear the panel.
    spot = _home(page).locator(".spot").bounding_box()
    assert spot["x"] == 160 and spot["x"] + spot["width"] <= box["x"]
    assert _line(page, HEALTH).bounding_box()["x"] == 160
    # Another width moves it again, and 0 puts it back.
    page.evaluate(_SIDE, {"width": 300})
    _expect_centre(page, (1400 - 300) / 2)
    page.evaluate(_SIDE, {"width": 0})
    _expect_centre(page, 700)
    expect(panel).to_be_hidden()


def test_the_column_goes_back_when_the_piece_with_the_side_panel_leaves(main_shell, ui_contexts):
    page = _side_page(main_shell, ui_contexts, 1400)
    page.evaluate(_SIDE, {"width": 400})
    _expect_centre(page, 500)
    page.evaluate(_SIDE, {"leave": True})
    expect(_home(page).locator("moxi-fixture-main")).to_have_count(0)
    _expect_centre(page, 700)


def test_under_1100_px_of_window_a_side_panel_moves_nothing(main_shell, ui_contexts):
    page = _side_page(main_shell, ui_contexts, 1000)
    _expect_centre(page, 500)
    page.evaluate(_SIDE, {"width": 400})
    panel = _home(page).locator("moxi-fixture-main").get_by_test_id("side")
    expect(panel).to_be_visible()
    _expect_centre(page, 500)
    # The panel lies over the page, at the window's right edge.
    box = panel.bounding_box()
    assert box["x"] + box["width"] == 1000 and box["x"] < _home(page).bounding_box()["x"] + 680
    # From 1100 px the column stands beside the panel that is still shown.
    page.set_viewport_size({"width": 1100, "height": 800})
    _expect_centre(page, (1100 - 400) / 2)
    page.set_viewport_size({"width": 1099, "height": 800})
    _expect_centre(page, 1099 / 2)


def test_with_the_core_and_ui_alone_home_is_the_sentence_and_health(ui_shells, ui_contexts,
                                                                    tmp_path_factory):
    home_dir = tmp_path_factory.mktemp("small")
    _kept(home_dir, {"at": _now(), "rows": [PASS], "error": None})
    page = ui_contexts(ui_shells(home=home_dir, leave_out=_others())).pages[0]
    home = _home(page)
    expect(home.get_by_text(SENTENCE)).to_be_visible()
    _expect_shown(page, [HEALTH])
    line = _line(page, HEALTH)
    expect(line.locator(".fact")).to_have_text("All checks pass")
    expect(line.locator(".time")).to_have_text(re.compile(r"^checked \d\d?:\d\d"))
    # Nothing opens while every check passes: the row leads to the full list.
    expect(home.get_by_role("button")).to_have_count(0)
    page.locator("#moxi-pages-button").click()
    expect(page.locator("#moxi-pages a")).to_have_text(["Home", "Modules"])
    page.keyboard.press("Escape")
    home.get_by_role("link", name=re.compile("Health")).click()
    expect(page.locator("#moxi-panel > moxi-ui-page .card[data-module=ui]")).to_be_visible()
    assert page.url.endswith("/m/ui/modules")


# --- Health ------------------------------------------------------------------------------

def test_health_says_what_fails_and_opens_to_only_the_failing_and_warning_checks(
        kept_shell, ui_contexts):
    page = ui_contexts(kept_shell).pages[0]
    home = _home(page)
    _say(page, "row-one", {"parts": [{"text": "quiet"}]})
    # A failing check puts Health first, heavy, and the sentence goes.
    _expect_shown(page, [HEALTH, ONE])
    expect(home.get_by_text(SENTENCE)).to_have_count(0)
    row = home.get_by_role("button", name=re.compile("Health"))
    expect(row.locator(".fact")).to_have_text(f"1 check failing, 1 warning · {FAIL['detail']}")
    expect(row.locator(".fact .heavy")).to_have_text("1 check failing")
    expect(row.locator(".time")).to_have_text(re.compile(r"^checked \d\d?:\d\d"))
    health = home.locator("moxi-ui-health")
    expect(health).to_be_hidden()
    row.click()
    expect(row.locator(".time")).to_have_text("Hide")
    checks = health.locator(".check")
    expect(checks).to_have_count(2)
    failing, warning = checks.nth(0), checks.nth(1)
    expect(failing.locator(".line").nth(0)).to_have_text(re.compile(
        re.escape(FAIL["detail"]) + r"\s*failing$"))
    expect(failing.locator(".failing")).to_have_css("font-weight", "600")
    expect(failing.locator(".line").nth(1)).to_have_text(re.compile(r"turns · guard"))
    expect(failing.locator("code")).to_have_text(FAIL["fix"])
    expect(failing.get_by_role("button", name=re.compile("Copy"))).to_be_visible()
    expect(warning.locator(".line").nth(0)).to_have_text(re.compile(
        re.escape(WARN["detail"]) + r"\s*warning$"))
    expect(warning.locator("code")).to_have_text(WARN["fix"])
    # The check that passes is only counted in the foot.
    expect(health).not_to_contain_text(PASS["detail"])
    foot = health.locator(".foot")
    expect(foot).to_contain_text("Every other check passes")
    expect(foot).to_contain_text(re.compile(r"checked \d\d?:\d\d"))
    expect(foot.get_by_role("button", name="Check again")).to_be_visible()
    foot.get_by_role("link", name="All checks on Modules").click()
    expect(page.locator("#moxi-panel > moxi-ui-page .card[data-module=ui]")).to_be_visible()
    assert page.url.endswith("/m/ui/modules")


def test_copy_puts_a_checks_fix_on_the_clipboard(kept_shell, ui_contexts, ui_browser):
    if ui_browser.browser_type.name != "chromium":
        pytest.skip("only Chromium grants a test the clipboard")
    context = ui_contexts(kept_shell, permissions=["clipboard-read", "clipboard-write"])
    page = context.pages[0]
    home = _home(page)
    home.get_by_role("button", name=re.compile("Health")).click()
    copy = home.locator("moxi-ui-health .check").nth(0).get_by_role("button")
    copy.click()
    expect(copy).to_have_text("Copied")
    assert page.evaluate("navigator.clipboard.readText()") == FAIL["fix"]


def test_a_failing_check_of_a_real_run_shows_on_health_and_check_again_runs_the_doctor(
        ui_shells, ui_contexts, fixture_site):
    shell = ui_shells(site=[fixture_site], checks=True)
    fixture_kit.fail_check(shell)
    page = ui_contexts(shell).pages[0]
    home = _home(page)
    row = home.get_by_role("button", name=re.compile("Health"))
    expect(row.locator(".fact .heavy")).to_have_text(re.compile(r"^\d+ checks? failing$"),
                                                     timeout=DOCTOR_MS)
    assert _shown(page)[0] == HEALTH
    expect(home.get_by_text(SENTENCE)).to_have_count(0)
    row.click()
    health = home.locator("moxi-ui-health")
    mine = health.locator(".check[data-check='fixture.check']")
    expect(mine).to_contain_text(DETAIL)
    expect(mine).to_contain_text("fixture · check")
    expect(mine.locator("code")).to_have_text(FIX)
    fixture_kit.fail_check(shell, on=False)
    health.get_by_role("button", name="Check again").click()
    # While the doctor runs the foot says so and offers no second run.
    expect(health.get_by_role("status")).to_have_text("Checking…")
    expect(health.get_by_role("button", name="Check again")).to_have_count(0)
    expect(mine).to_have_count(0, timeout=DOCTOR_MS)
    expect(health.get_by_role("status")).to_have_count(0, timeout=DOCTOR_MS)


_SHOW = """(hidden) => {
  Object.defineProperty(document, "visibilityState", {configurable: true,
                        get: () => hidden ? "hidden" : "visible"});
  Object.defineProperty(document, "hidden", {configurable: true, get: () => hidden});
  document.dispatchEvent(new Event("visibilitychange"));
}"""


def test_health_asks_again_when_the_document_is_shown_and_an_old_run_starts_a_new_one(
        ui_shells, ui_contexts, fixture_site, tmp_path_factory):
    home_dir = tmp_path_factory.mktemp("old")
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    _kept(home_dir, {"at": old, "rows": [PASS], "error": None})
    shell = ui_shells(home=home_dir, site=[fixture_site], checks=True)
    fixture_kit.fail_check(shell)
    context = ui_contexts(shell, sign_in=False)
    # The read Health makes when it is placed does not arrive, so the old run stays kept.
    context.route("**/m/ui/api/checks", lambda route: route.abort())
    page = context.new_page()
    shell.sign_in(page)
    row = _line(page, HEALTH)
    expect(row.locator(".fact")).to_have_text("All checks pass")
    context.unroute("**/m/ui/api/checks")
    # Hidden asks nothing; shown again asks, and the old run starts a new one.
    asked: list[str] = []
    page.on("request", lambda r: asked.append(r.url) if r.url.endswith("/api/checks") else None)
    page.evaluate(_SHOW, True)
    with page.expect_response(lambda r: r.url.endswith("/m/ui/api/checks")) as first:
        page.evaluate(_SHOW, False)
    assert len(asked) == 1 and first.value.json()["running"] is True
    expect(row.locator(".fact .heavy")).to_have_text(re.compile(r"^\d+ checks? failing$"),
                                                     timeout=DOCTOR_MS)
    # The run it kept is fresh now: shown again, Health asks and no run starts.
    page.evaluate(_SHOW, True)
    with page.expect_response(lambda r: r.url.endswith("/m/ui/api/checks")) as second:
        page.evaluate(_SHOW, False)
    answer = second.value.json()
    assert answer["running"] is False and answer["at"] > old
    status, kept = shell.session().get("/m/ui/api/checks")
    assert status == 200 and kept["at"] == answer["at"] and kept["running"] is False


def test_a_run_that_could_not_run_says_why_and_offers_check_again(ui_shells, ui_contexts,
                                                                  tmp_path_factory):
    home_dir = tmp_path_factory.mktemp("failed")
    why = "the doctor gave no answer within 60 s"
    _kept(home_dir, {"at": _now(), "rows": [], "error": why})
    page = ui_contexts(ui_shells(home=home_dir, leave_out=_others())).pages[0]
    home = _home(page)
    row = home.get_by_role("button", name=re.compile("Health"))
    expect(row.locator(".fact")).to_have_text(f"The checks could not run · {why}")
    expect(home.get_by_text(SENTENCE)).to_have_count(0)
    row.click()
    health = home.locator("moxi-ui-health")
    expect(health.locator(".check")).to_have_text(why)
    expect(health.locator(".foot")).not_to_contain_text("Every other check passes")
    expect(health.get_by_role("button", name="Check again")).to_be_visible()


# --- colours and width -------------------------------------------------------------------

_COLOURS = """() => {
  const home = document.querySelector("#moxi-panel > moxi-ui-page").shadowRoot.querySelector("moxi-ui-home");
  const row = home.shadowRoot.querySelector(".entry:not([hidden]) .row");
  const probe = document.createElement("div");
  const token = (name) => {
    probe.style.color = `var(--moxi-${name})`;
    home.shadowRoot.append(probe);
    const seen = getComputedStyle(probe).color;
    probe.remove();
    return seen;
  };
  return {
    text: getComputedStyle(row).color, textToken: token("text"),
    label: getComputedStyle(row.querySelector(".label")).color, dimToken: token("text-dim"),
    line: getComputedStyle(row).borderBottomColor, lineToken: token("border"),
    page: getComputedStyle(document.body).backgroundColor,
  };
}"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_home_takes_its_colours_from_the_theme_in_light_and_dark(kept_shell, ui_contexts, scheme):
    page = ui_contexts(kept_shell, color_scheme=scheme).pages[0]
    expect(_line(page, HEALTH)).to_be_visible()
    plain = page.evaluate(_COLOURS)
    assert plain["text"] == plain["textToken"]
    assert plain["label"] == plain["dimToken"] != plain["text"]
    assert plain["line"] == plain["lineToken"]
    page.evaluate("document.documentElement.dataset.themeName = 'midnight-neon'")
    neon = page.evaluate(_COLOURS)
    assert neon["text"] == neon["textToken"] and neon["line"] == neon["lineToken"]
    assert (neon["text"], neon["page"]) != (plain["text"], plain["page"])


def test_at_360_px_the_label_and_time_share_a_line_and_the_fact_wraps_under(
        kept_shell, ui_contexts, ui_width_check):
    page = ui_contexts(kept_shell).pages[0]
    page.set_viewport_size({"width": 360, "height": 800})
    _say(page, "row-one", {"parts": [{"text": "a fact that runs on " * 6}], "time": "read 19:08"})
    home = _home(page)
    home.get_by_role("button", name=re.compile("Health")).click()
    expect(home.locator("moxi-ui-health .foot")).to_be_visible()
    ui_width_check(page)
    line = _line(page, ONE)
    label, when, fact = (line.locator(f".{part}").bounding_box() for part in ("label", "time", "fact"))
    assert label["y"] == when["y"] and fact["y"] >= label["y"] + label["height"]
    assert fact["height"] > label["height"] * 2, "the fact wraps"
    # The column is the page less its 16 px margins; an opened row starts 2 px in.
    assert line.bounding_box()["x"] == 16 and line.bounding_box()["width"] == 328
    inside = home.locator("moxi-ui-health .check").first.bounding_box()
    assert inside["x"] == 18
