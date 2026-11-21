"""rounds' page in a browser, through a real shell that starts rounds' real page server
over `page_kit`'s rounds, seeded under the shell's own home. The shell has only ui, rounds
and projects. One test per job of the page: the contract check; what waits on him and
the walk through it; a question with its options and passage from its marker; a failed
check's red job, run and output file; a build's verdict; a strip card and its stage's
tabs; the folds of a many-stage round; a round's card and the key; an address that opens
its popover; a new question without a reload; the strip's way back to a line a filter
hides; a closed round's card; and the phone layout at 390 px."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

import page_kit
from moxi import modules
from moxi_rounds import asking
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
    # The page acts on nothing, so its accent shows only on a selection or the focus;
    # at rest it draws from the waiting line's tint and the dim text.
    check = ui_component_check(ui_page, "rounds", "page", tokens=("state-yours-soft", "text-dim"))
    assert check.element == "moxi-rounds-page"
    assert any("/m/rounds/api/rounds" in url for url in check.requests), check.requests


def test_he_sees_what_waits_and_walks_it_in_order(shell, ui_page):
    panel = open_panel(ui_page)
    waiting = panel.locator(".waiting")
    expect(waiting).to_contain_text("Waiting on you")
    expect(waiting).to_contain_text("3 questions in 2 rounds")
    expect(waiting).to_contain_text("1 decision")
    expect(waiting).to_contain_text("1 job")
    expect(waiting).to_contain_text("1 ruling to read back")
    waiting.get_by_role("button", name="Go through in order").click()
    pop = popover(panel)
    expect(pop.locator(".title")).to_have_text("Question Q1")
    expect(pop).to_contain_text("1 of 6")
    expect(ui_page).to_have_url(re.compile(r"/m/rounds/walk/question/kestrel/billing-export/Q1$"))
    ui_page.keyboard.press("ArrowRight")
    expect(pop).to_contain_text("2 of 6")
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
    expect(pop).to_contain_text("To answer: tell the round's lead.")
    expect(pop.locator(".well")).to_contain_text("moxi rounds rule kestrel/billing-export Q1 --chose K")
    expect(pop).to_contain_text("1 of 3")


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
    card = cards.filter(has_text="B1").first
    card.click()
    pop = popover(panel)
    expect(card).to_have_attribute("aria-expanded", "true")
    tabs = pop.locator("moxi-ui-rail.tabs moxi-ui-mark")
    expect(tabs).not_to_have_count(0)
    pop.locator('moxi-ui-rail.tabs moxi-ui-mark[key="commission|B1"]').click()
    expect(pop.locator(".title")).to_have_text("commission of B1")
    expect(pop).to_contain_text("builder-1")
    expect(pop.get_by_role("button", name="Show its line on the board")).to_be_visible()


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


def test_show_its_line_sets_the_filter_that_hides_it_and_scrolls_to_it(shell, ui_page):
    panel = open_panel(ui_page)
    panel.get_by_role("button", name=re.compile(r"^Wrapping up")).click()
    expect(panel.locator(f'moxi-ui-rail[data-round="{BUSY}"]')).to_have_count(0)
    panel.locator(".strip .card").filter(has_text="B1").first.click()
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
