"""Acting from rounds' page in a browser, through a real shell whose page server finds
comms installed with no daemon of its own to send through: the page records its own
kinds and reads `recorded, not told`, and an answer in the operator's own words, which
only a message carries, stays a draft with comms' words. One test per act: a pick with
its receipt, and an own-words answer kept as a draft with comms' words (the sent receipt,
which needs a message comms takes, is the in-process and Node tests'); a read-back from a
pick's receipt; a parked question opened from the foot and unparked; and a question the
page parked itself, unparked from its popover while the park's row stands, then
answered."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

import page_kit
from moxi import modules
from moxi_rounds import asking, listing, opening, ruling

pytestmark = pytest.mark.browser

SMALL = ("ui", "rounds", "projects")
ROUND = "kestrel/billing-export"


@pytest.fixture(scope="module")
def ui_shell_options():
    return {"leave_out": [found.name for found in modules.scan() if found.name not in SMALL]}


@pytest.fixture(scope="module")
def shell(ui_shell):
    """The shell, with one round of five questions under its home, the fourth parked."""
    with page_kit.under(ui_shell):
        notes = page_kit.shell_home(ui_shell) / "notes"
        page_kit.add_project("kestrel", notes)
        page_kit.write_config(page_kit.FLOWS)
        opening.open_round(ROUND, description="billing export", by="planner", flow="staged",
                           lead="atlas")
        page_kit.design(notes / "rounds" / "billing-export", ROUND, page_kit.stage("B1"))
        asking.ask(ROUND, "Which name does the export keep?", by="scout", origin=page_kit.PTR,
                   options=("old=the old name", "new=the new name"))
        asking.ask(ROUND, "How should totals round?", by="scout", origin=page_kit.PTR)
        asking.ask(ROUND, "Which header does the export keep?", by="scout", origin=page_kit.PTR,
                   options=("short=the short header", "long=the long header"))
        asking.ask(ROUND, "When does the archive go?", by="scout", origin=page_kit.PTR)
        asking.ask(ROUND, "Which archive does the export use?", by="scout", origin=page_kit.PTR,
                   options=("cold=the cold archive", "warm=the warm archive"))
        ruling.rule(ROUND, item="park:Q4", record=page_kit.PTR, by="lead")
    return ui_shell


def open_panel(page, route=""):
    page.goto(f"/m/rounds/{route}")
    panel = page.locator("#moxi-panel > moxi-rounds-page")
    expect(panel.get_by_text("Open rounds")).to_be_visible()
    return panel


def tray(panel):
    return panel.get_by_role("region", name="Your answers")


def open_in_rounds(shell) -> list[str]:
    """The ids of the round's questions rounds reads as open."""
    with page_kit.under(shell):
        found = dict(listing.waiting("kestrel", read_git=False).found)
    return [one.id for one in found.get("open", ()) if one.holder == ROUND]


def test_a_pick_reads_its_receipt_and_refused_own_words_stay_a_draft(shell, ui_page, ui_press):
    panel = open_panel(ui_page, f"question/{ROUND}/Q1")
    pop = panel.locator(".pop")
    ui_press(pop.get_by_role("button", name="old the old name"))
    ui_press(pop.get_by_role("button", name="Add to answers"))
    answers = tray(panel)
    expect(answers).to_contain_text("Pick old")
    expect(answers).to_contain_text("page records it on Send")

    panel = open_panel(ui_page, f"question/{ROUND}/Q2")
    pop = panel.locator(".pop")
    pop.get_by_role("textbox", name="Or answer in your own words").fill("Half up, to the cent.")
    ui_press(pop.get_by_role("button", name="Add to answers"))
    answers = tray(panel)
    # The tray came back from the browser's storage, with the pick still in it.
    expect(answers).to_contain_text("Pick old")
    expect(answers).to_contain_text("atlas words and records it")

    # The popover closes, as the operator turns to the tray.
    ui_page.keyboard.press("Escape")
    ui_press(answers.get_by_role("button", name="Send", exact=True))
    sent = answers.locator(".trow", has_text="Pick old")
    expect(sent).to_contain_text("Recorded by this page at")
    expect(sent).to_contain_text("page:rounds/")
    expect(sent).to_contain_text("Recorded as C")
    expect(sent).to_contain_text("Not told: ")
    expect(sent.get_by_role("button", name="Send again").first).to_be_visible()
    # The message that would carry the operator's own words was refused, so nothing
    # recorded them: the row stays a draft, with comms' words, to send again.
    words = answers.locator(".trow", has_text="Your words")
    expect(words).to_contain_text("comms did not send it")
    expect(words.get_by_role("button", name="Send now to atlas")).to_be_visible()

    panel = open_panel(ui_page, f"question/{ROUND}/Q1")
    expect(panel.locator(".pop")).to_contain_text("Recorded by this page at")


def test_a_pick_is_read_back_from_its_receipt(shell, ui_page, ui_press):
    panel = open_panel(ui_page, f"question/{ROUND}/Q3")
    pop = panel.locator(".pop")
    ui_press(pop.get_by_role("button", name="long the long header"))
    ui_press(pop.get_by_role("button", name="Send now to atlas"))
    receipt = pop.locator(".receipt")
    expect(receipt).to_contain_text("Recorded as C")
    ui_press(receipt.get_by_role("button", name="That's what I said"))
    read_back = tray(panel).locator(".trow").filter(has=ui_page.locator(".top", has_text="That's what I said"))
    expect(read_back).to_contain_text("marked as read back")


def test_a_parked_question_opens_from_the_foot_and_is_unparked(shell, ui_page, ui_press):
    panel = open_panel(ui_page)
    ui_press(panel.get_by_role("button", name="1 parked question"))
    ui_press(panel.get_by_role("list", name="Parked questions").get_by_role("button", name="Q4 When does the archive go?"))
    pop = panel.locator(".pop")
    expect(pop).to_contain_text("Parked by C")
    ui_press(pop.get_by_role("button", name="Unpark"))
    answers = tray(panel)
    expect(answers).to_contain_text("Unpark")
    ui_page.keyboard.press("Escape")
    ui_press(answers.get_by_role("button", name="Send", exact=True))
    expect(answers.locator(".trow", has_text="Unpark")).to_contain_text("Q4 unparked: open again")
    # Open again, the question waits on the operator once more: rounds reads it open, and
    # the waiting line counts what rounds reads.
    open_now = open_in_rounds(shell)
    assert "Q4" in open_now
    expect(panel.locator(".waiting .counts .n").first).to_have_text(str(len(open_now)))
    expect(panel.get_by_role("button", name="1 parked question")).to_have_count(0)


def test_a_question_the_page_parked_is_unparked_and_answered_from_its_popover(shell, ui_page, ui_press):
    panel = open_panel(ui_page, f"question/{ROUND}/Q5")
    pop = panel.locator(".pop")
    ui_press(pop.get_by_role("button", name="Park"))
    answers = tray(panel)
    park = answers.locator(".trow", has_text=f"{ROUND} Q5")
    ui_page.keyboard.press("Escape")
    ui_press(park.get_by_role("button", name="Send now to atlas"))
    # comms refuses the message, so the park's row stays in the tray, recorded and not told.
    expect(park).to_contain_text("Not told: ")
    expect(park).to_contain_text("recorded, not told")

    panel = open_panel(ui_page, f"question/{ROUND}/Q5")
    pop = panel.locator(".pop")
    expect(pop).to_contain_text("Parked by C")
    expect(pop).to_contain_text("Q5 parked")
    ui_press(pop.get_by_role("button", name="Unpark"))
    ui_press(pop.get_by_role("button", name="Send now to atlas"))
    expect(pop).to_contain_text("Q5 unparked: open again")
    # Open again, it offers its answer form in the same popover, with the rows still there.
    ui_press(pop.get_by_role("button", name="warm the warm archive"))
    expect(pop.get_by_role("textbox", name="Or answer in your own words")).to_be_visible()
    ui_press(pop.get_by_role("button", name="Send now to atlas"))
    expect(pop).to_contain_text("Recorded as C")
    expect(pop.get_by_role("button", name="Add to answers")).to_have_count(0)
