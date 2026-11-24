"""rounds' page in a browser, through a real shell that starts rounds' real page server
over `page_kit`'s rounds, seeded under the shell's own home. The shell has only ui,
rounds and projects. One test per job of the page: the contract check; no style the
shell's policy refuses; what waits on the operator and the walk through it; a question
with its options and passage from its marker; a failed check's red job, run and output
file; a build in a git repo, on the small flow, with its state and its check failed at
the tip; a build's verdict; a strip card and its stage's tabs, and the card staying
open with its build when the strip moves; the folds of a many-stage round; a stage's
name beside its line's rail, never on it; a round's
card and the key; an address that opens its popover; a ruling of a round with no
docket, under its line; a new question without a reload; the strip's way back to a line
a filter hides; a closed round's card; and the phone layout at 390 px."""

from __future__ import annotations

import re
import textwrap

import pytest
from playwright.sync_api import expect

import page_kit
from moxi import modules
from moxi_rounds import asking, opening, ruling
from page_kit import BUSY, SHUT

pytestmark = pytest.mark.browser

#: The modules the shell keeps: a small install.
SMALL = ("ui", "rounds", "projects")


@pytest.fixture(scope="module")
def ui_shell_options():
    return {"leave_out": [found.name for found in modules.scan() if found.name not in SMALL]}


@pytest.fixture(scope="module")
def shell(ui_shell):
    """The shell, with `page_kit`'s rounds under its home."""
    with page_kit.under(ui_shell):
        page_kit.seed(page_kit.shell_home(ui_shell) / "notes")
    return ui_shell


def open_panel(page, route=""):
    page.goto(f"/m/rounds/{route}")
    panel = page.locator("#moxi-panel > moxi-rounds-page")
    expect(panel.get_by_text("Open rounds")).to_be_visible()
    return panel


def mark(panel, key, *, round_=BUSY, line=0):
    return panel.locator(f'moxi-ui-rail[data-round="{round_}"][data-line="{line}"] '
                         f'moxi-ui-mark[key="{key}"]')


def popover(panel):
    return panel.locator(".pop")


def test_the_page_keeps_the_contract(shell, ui_page, ui_component_check):
    # The page's accent is on answering, in a popover and the tray, so at rest it draws
    # from the waiting line's tint and the dim text.
    check = ui_component_check(ui_page, "rounds", "page", tokens=("state-yours-soft", "text-dim"))
    assert check.element == "moxi-rounds-page"
    assert any("/m/rounds/api/rounds" in url for url in check.requests), check.requests


def test_the_operator_sees_what_waits_and_walks_it_in_order(shell, ui_page):
    panel = open_panel(ui_page)
    waiting = panel.locator(".waiting")
    expect(waiting).to_contain_text("Waiting on you")
    expect(waiting).to_contain_text("3 questions in 2 rounds")
    # B1's decision is not put to the operator while B1 is not finished.
    expect(waiting).not_to_contain_text("decision")
    expect(waiting).to_contain_text("1 job")
    expect(waiting).to_contain_text("1 ruling to read back")
    waiting.get_by_role("button", name="Go through in order").click()
    pop = popover(panel)
    expect(pop.locator(".title")).to_have_text("Question Q1")
    expect(pop).to_contain_text("1 of 5")
    expect(ui_page).to_have_url(re.compile(r"/m/rounds/walk/question/kestrel/billing-export/Q1$"))
    ui_page.keyboard.press("ArrowRight")
    expect(pop).to_contain_text("2 of 5")
    ui_page.keyboard.press("j")
    expect(pop.locator(".title")).to_have_text("Ruling C1, to read back")
    ui_page.keyboard.press("Escape")
    expect(pop).to_have_count(0)
    expect(ui_page).to_have_url(re.compile(r"/m/rounds/$"))


def test_a_question_opens_from_its_marker_with_its_options_and_passage(shell, ui_page):
    panel = open_panel(ui_page)
    mark(panel, "docket").click()
    pop = popover(panel)
    expect(pop.locator(".question")).to_have_text("Which name does the export keep?")
    expect(pop.locator(".option")).to_have_count(2)
    expect(pop.locator(".option").first).to_contain_text("the old name")
    expect(pop).to_contain_text("From design.md")
    expect(pop.locator(".passage")).to_contain_text("Q1 is asked here")
    # The answer goes into the tray: nothing is picked yet, so there is nothing to add.
    expect(pop.get_by_role("textbox", name="Or answer in your own words")).to_be_visible()
    expect(pop.get_by_role("button", name="Add to answers")).to_be_disabled()
    expect(pop).to_contain_text("1 of 3")


def test_the_page_sets_no_style_the_shells_policy_refuses(shell, ui_page):
    # The shell allows no inline style, so a style attribute is dropped with a policy
    # error: the popover kept no width of its own.
    ui_page.add_init_script(
        "window.refused = [];"
        "document.addEventListener('securitypolicyviolation',"
        " (event) => window.refused.push(event.violatedDirective));")
    panel = open_panel(ui_page)
    mark(panel, "docket").click()
    expect(popover(panel).locator(".question")).to_be_visible()
    assert popover(panel).bounding_box()["width"] == 560
    ui_page.keyboard.press("Escape")
    panel.get_by_role("button", name="1 closed round").click()
    panel.locator(f'.closed [data-name="{SHUT}"]').click()
    expect(popover(panel).locator(".title")).to_have_text("archive")
    assert popover(panel).bounding_box()["width"] == 460
    assert ui_page.evaluate("() => window.refused") == []


def test_a_failed_check_shows_its_red_job_its_run_and_its_output(shell, ui_page):
    panel = open_panel(ui_page)
    check = mark(panel, "check|B1")
    # The build has no git here, so the board can't tell its run is at the tip: the
    # marker waits, and its record still holds the red CI and the failed run.
    expect(check).to_have_attribute("label", re.compile(r"^check of B1, billing-export: "))
    check.click()
    pop = popover(panel)
    expect(pop.locator(".title")).to_have_text("check of B1")
    expect(pop).to_contain_text("CI at 1111111")
    expect(pop.locator(".t-failed").first).to_have_text("red")
    expect(pop).to_contain_text("linux")
    expect(pop.get_by_role("button", name="1 green job")).to_be_visible()
    expect(pop).to_contain_text("Checks run")
    expect(pop).to_contain_text("failed (exit 1)")
    expect(pop.locator(".mono", has_text="/scratch/check-1.log")).to_be_visible()
    expect(check).to_have_attribute("open", "")


def test_a_build_in_git_shows_its_state_and_its_check_failed_at_the_tip(shell, ui_page):
    panel = open_panel(ui_page)
    check = mark(panel, "check|B1", round_=page_kit.SMALL)
    expect(check).to_have_attribute("state", "failed")
    expect(check).to_have_attribute("label", "check of B1, nest: failed")
    check.click()
    pop = popover(panel)
    expect(pop.locator(".title")).to_have_text("check of B1")
    expect(pop).to_contain_text("failed (exit 1)")
    expect(pop.locator(".mono", has_text="/scratch/check-2.log")).to_be_visible()
    ui_page.keyboard.press("Escape")
    mark(panel, "build|B1", round_=page_kit.SMALL).click()
    expect(pop.locator(".title")).to_have_text("build of B1")
    expect(pop).to_contain_text("building")
    expect(pop).to_contain_text("branch-b1 holds work main does not")


def test_a_builds_verdict_shows_with_its_open_finding(shell, ui_page):
    panel = open_panel(ui_page)
    mark(panel, "verify-build|B1").click()
    pop = popover(panel)
    expect(pop).to_contain_text("fold-first")
    expect(pop).to_contain_text("1 open finding")
    expect(pop).to_contain_text("The header is lost.")


def test_a_strip_card_opens_its_step_and_moves_along_its_stage(shell, ui_page):
    panel = open_panel(ui_page)
    cards = panel.locator(".strip .card")
    expect(cards.first).to_be_visible()
    card = cards.filter(has_text="billing-export B1").first
    card.click()
    pop = popover(panel)
    expect(card).to_have_attribute("aria-expanded", "true")
    tabs = pop.locator("moxi-ui-rail.tabs moxi-ui-mark")
    expect(tabs).not_to_have_count(0)
    pop.locator('moxi-ui-rail.tabs moxi-ui-mark[key="commission|B1"]').click()
    expect(pop.locator(".title")).to_have_text("commission of B1")
    expect(pop).to_contain_text("builder-1")
    expect(pop.get_by_role("button", name="Show its line on the board")).to_be_visible()


def test_an_open_strip_card_stays_with_its_build_when_the_strip_moves(ui_shells,
                                                                      ui_shell_options,
                                                                      ui_contexts):
    # A shell of its own, so the build it commissions changes no other test's strip.
    own = ui_shells(**ui_shell_options)
    with page_kit.under(own):
        rounds_dir = page_kit.seed(page_kit.shell_home(own) / "notes")
    panel = open_panel(ui_contexts(own).pages[0])
    cards = panel.locator(".strip .card")
    first = cards.filter(has_text="billing-export B1").first
    expect(cards.first).to_contain_text("B1")
    first.click()
    expect(first).to_have_attribute("aria-expanded", "true")
    with page_kit.under(own):
        page_kit.append(rounds_dir / "billing-export", "build.commissioned", stage="B3",
                        repo="kestrel", branch="branch-b3", base=page_kit.BASE,
                        target="main", builder="builder-1", build="0123456789abcdef")
    expect(cards.first).to_contain_text("B3", timeout=20_000)
    expect(cards.filter(has_text="billing-export B1").first).to_have_attribute("aria-expanded", "true")
    expect(cards.first).to_have_attribute("aria-expanded", "false")
    expect(panel.locator('.strip .card[aria-expanded="true"]')).to_have_count(1)
    expect(popover(panel).locator(".title")).to_contain_text("of B1")


def test_a_many_stage_round_folds_and_unfolds(shell, ui_page):
    panel = open_panel(ui_page)
    lines = panel.locator(f'moxi-ui-rail[data-round="{BUSY}"]')
    expect(lines).to_have_count(1)
    fold = panel.get_by_role("button", name=re.compile(r"stages not started"))
    expect(fold).to_have_attribute("aria-expanded", "false")
    fold.click()
    expect(fold).to_have_attribute("aria-expanded", "true")
    expect(lines).not_to_have_count(1)
    fold.click()
    expect(lines).to_have_count(1)


#: The key of the rail mark that holds the focus, through each shadow root; null when
#: the focus is not in a mark.
FOCUSED_MARK = """() => {
  let node = document.activeElement, key = null;
  while (node) {
    if (node.localName === "moxi-ui-mark") key = node.getAttribute("key");
    node = node.shadowRoot ? node.shadowRoot.activeElement : null;
  }
  return key;
}"""


def test_a_stage_name_sits_beside_the_rail_never_on_its_line(ui_shells, ui_shell_options,
                                                             ui_contexts):
    # A shell of its own, so the round it adds changes no other test's counts.
    own = ui_shells(**ui_shell_options)
    tally = "kestrel/tally"
    with page_kit.under(own):
        rounds_dir = page_kit.seed(page_kit.shell_home(own) / "notes")
        opening.open_round(tally, description="a round of three stages", by="planner",
                           flow="staged")
        page_kit.design(rounds_dir / "tally", tally,
                        "\n".join(page_kit.stage(f"B{n}") for n in (1, 2, 3)))
    page = ui_contexts(own).pages[0]
    panel = open_panel(page)
    first = panel.locator(f'moxi-ui-rail[data-round="{tally}"][data-line="0"]')
    more = panel.get_by_role("button", name=re.compile(r"^(Show|Fold) 2 other stages of tally$"))
    expect(more).to_have_accessible_name("Show 2 other stages of tally")
    expect(more).to_have_text("+2")
    # The keyboard goes from the round's name to its +N, then into the rail's markers.
    panel.get_by_role("button", name="tally", exact=True).focus()
    page.keyboard.press("Tab")
    expect(more).to_be_focused()
    page.keyboard.press("Tab")
    assert page.evaluate(FOCUSED_MARK) == first.evaluate(
        "(rail) => [...rail.children].find((mark) => mark.getAttribute('state')"
        " && !mark.hasAttribute('quiet')).getAttribute('key')")
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Enter")
    expect(more).to_have_attribute("aria-expanded", "true")
    expect(more).to_have_accessible_name("Fold 2 other stages of tally")
    expect(panel.locator(f'moxi-ui-rail[data-round="{tally}"]')).to_have_count(3)
    panel.get_by_role("button", name="1 closed round").click()
    expect(panel.locator(".line.closed")).to_have_count(1)
    expect(panel.locator("moxi-ui-mark[wide]")).to_have_count(0)
    round_line = panel.locator(".line").filter(
        has=page.locator(f'moxi-ui-rail[data-round="{tally}"][data-line="0"]'))
    expect(round_line.locator(".stagecell")).to_contain_text("B1")
    head = panel.locator(".labels")
    stage_x = head.locator(".stagecell").bounding_box()["x"]
    column_x = head.locator(".col").first.bounding_box()["x"]
    lines = panel.locator(".line").filter(has=page.locator("moxi-ui-rail"))
    assert lines.count() > 4
    for at in range(lines.count()):
        line = lines.nth(at)
        cell = line.locator(".stagecell").bounding_box()
        rail = line.locator("moxi-ui-rail").bounding_box()
        assert cell["x"] + cell["width"] <= rail["x"] + 0.5, (at, cell, rail)
        # Every line keeps the columns of the labels row.
        assert abs(cell["x"] - stage_x) < 0.5 and abs(rail["x"] - column_x) < 0.5, at
    # No line's text spills onto another's rail: the unreadable round's reason wraps
    # inside its own line.
    boxes = [box for box in (panel.locator(".line").nth(at).bounding_box()
                             for at in range(panel.locator(".line").count())) if box]
    for upper, lower in zip(boxes, boxes[1:]):
        assert upper["y"] + upper["height"] <= lower["y"] + 0.5, (upper, lower)
    reason = panel.locator(".line.unreadable .phrase").bounding_box()
    line_box = panel.locator(".line.unreadable").bounding_box()
    assert reason["y"] >= line_box["y"] and reason["y"] + reason["height"] <= line_box["y"] + line_box["height"] + 0.5


def test_a_rounds_card_and_the_key_open(shell, ui_page):
    panel = open_panel(ui_page)
    panel.get_by_role("button", name="billing-export", exact=True).click()
    pop = popover(panel)
    expect(pop.locator(".title")).to_have_text("billing-export")
    expect(pop).to_contain_text("billing export")
    expect(ui_page).to_have_url(re.compile(r"/m/rounds/round/kestrel/billing-export$"))
    panel.get_by_role("button", name="Key").click()
    expect(pop.locator(".title")).to_have_text("Key")
    expect(pop).to_contain_text("Built in a round of its own.")
    expect(pop.locator("moxi-ui-mark")).to_have_count(8)


def test_an_address_opens_its_popover_and_one_gone_says_so(shell, ui_page):
    panel = open_panel(ui_page, "step/kestrel/billing-export/check/B1")
    expect(popover(panel).locator(".title")).to_have_text("check of B1")
    panel = open_panel(ui_page, "question/kestrel/billing-export/Q9")
    expect(popover(panel)).to_have_count(0)
    expect(panel.locator(".foot")).to_contain_text("Question Q9 of kestrel/billing-export is no longer open")


def test_a_new_question_appears_without_a_reload(ui_shells, ui_shell_options, ui_contexts):
    # A shell of its own, so the question it adds changes no other test's counts.
    own = ui_shells(**ui_shell_options)
    with page_kit.under(own):
        page_kit.seed(page_kit.shell_home(own) / "notes")
    panel = open_panel(ui_contexts(own).pages[0])
    waiting = panel.locator(".waiting")
    expect(waiting).to_contain_text("3 questions in 2 rounds")
    with page_kit.under(own):
        asking.ask("kestrel/ledger", "Which ledger comes first?", by="scout", origin="comms:1")
    expect(waiting).to_contain_text("4 questions in 3 rounds", timeout=20_000)


def test_a_ruling_of_a_round_with_no_docket_opens_under_its_line(ui_shells, ui_shell_options,
                                                                 ui_contexts):
    # A shell of its own, so the round it adds changes no other test's counts.
    own = ui_shells(**ui_shell_options)
    quick = "kestrel/quick"
    with page_kit.under(own):
        page_kit.seed(page_kit.shell_home(own) / "notes")
        page_kit.write_config(page_kit.CONFIG + textwrap.dedent("""\
            [flows.quick]
            steps = [ { step = "design", by = "lead" }, { step = "build", by = "builder" },
                      { step = "finish", by = "builder" } ]
            """))
        opening.open_round(quick, description="a round with no docket", by="planner",
                           flow="quick")
        # Recorded before this second: a ruling of the second a list is read in waits
        # for the next list.
        ruling.rule(quick, text="Quick rounds skip the docket.", record=page_kit.PTR, by="lead",
                    at="2026-01-02T00:00:00Z")
    page = ui_contexts(own).pages[0]
    panel = open_panel(page)
    expect(panel.locator(f'moxi-ui-rail[data-round="{quick}"][data-line="0"] '
                         'moxi-ui-mark[key="docket"]')).to_have_count(0)
    page.goto(f"/m/rounds/shown/{quick}/C1")
    pop = popover(panel)
    expect(pop.locator(".title")).to_have_text("Ruling C1, to read back")
    expect(pop).to_be_in_viewport()


def test_show_its_line_sets_the_filter_that_hides_it_and_scrolls_to_it(shell, ui_page):
    panel = open_panel(ui_page)
    panel.get_by_role("button", name=re.compile(r"^Wrapping up")).click()
    expect(panel.locator(f'moxi-ui-rail[data-round="{BUSY}"]')).to_have_count(0)
    panel.locator(".strip .card").filter(has_text="billing-export B1").first.click()
    popover(panel).get_by_role("button", name="Show its line on the board").click()
    expect(panel.get_by_role("button", name="All", exact=True)).to_have_attribute("aria-pressed", "true")
    line = mark(panel, "check|B1")
    expect(line).to_be_in_viewport()
    expect(popover(panel).locator(".title")).to_be_visible()


def test_a_closed_rounds_name_opens_its_card(shell, ui_page):
    panel = open_panel(ui_page)
    panel.get_by_role("button", name="1 closed round").click()
    panel.locator(f'.closed [data-name="{SHUT}"]').click()
    pop = popover(panel)
    expect(pop.locator(".title")).to_have_text("archive")
    expect(pop).to_contain_text("Ended")


def test_the_phone_layout_fits_390_px_and_opens_a_sheet(shell, ui_contexts, ui_width_check):
    page = ui_contexts(viewport={"width": 390, "height": 844}).pages[0]
    ui_width_check(page, module="rounds", width=390)
    panel = open_panel_phone(page)
    expect(panel.locator(".waiting")).to_contain_text(re.compile(r"\d+ wait on you in \d+ rounds"))
    expect(panel.get_by_role("button", name="Go through in order")).to_have_count(0)
    panel.locator(f'.round[data-name="{BUSY}"]').click()
    sheet = panel.locator(".sheet")
    expect(sheet).to_contain_text("billing export")
    expect(sheet.locator("moxi-ui-rail[vertical] moxi-ui-mark").first).to_be_visible()
    expect(sheet).to_contain_text("Answer these at your desk.")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390


def open_panel_phone(page):
    page.goto("/m/rounds/")
    panel = page.locator("#moxi-panel > moxi-rounds-page")
    expect(panel.get_by_text("Your turn")).to_be_visible()
    return panel
