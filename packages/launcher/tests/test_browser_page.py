"""The launcher's page and components in a browser, through a real shell that starts the
launcher's real page server: the contract check on the panel and both components, a red
session in the error colour, a session's state followed through a clear, the sessions
block on the panel, the 360 px width check, a headless launch whose row arrives by push, a
refusal met before a launch's row, and a past terminal session's command to resume.

The shell runs with a fake `claude` first on its `PATH`, which prints a line and exits, so
a launch from the panel runs that and nothing else. Every test seeds rows of its own in
the shell's one launch log, so a test never counts on the log being empty.
"""

from __future__ import annotations

import re
import sys

import pytest
from moxi_dev.testing import wait_for
from playwright.sync_api import expect

import launcher_page_kit as kit
from moxi_launcher import targets

pytestmark = [pytest.mark.browser,
              pytest.mark.skipif(sys.platform == "win32",
                                 reason="the fake harness is a script run by its #! line")]

BRIEF = "say the word ready"
#: A directory long enough to wrap at 360 px.
LONG_CWD = "/work/" + "/".join(["a-directory-with-a-long-name"] * 6)


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    return kit.Harness(tmp_path_factory.mktemp("fake-harness"))


@pytest.fixture(scope="module")
def ui_shell_options(harness):
    """The shell's options, given once the fake harness is first on `PATH`, so the shell,
    which copies `PATH` as it starts, hands it to its page servers."""
    with kit.first_on_path(harness.bin):
        yield {}


def red_session(shell, request_id: str) -> str:
    """A live launch whose last relaunch failed, as the `sessions` topic holds it once
    published; returns its session id."""
    sid = kit.seed(shell, request_id, live=True)
    kit.failed_run(shell, request_id)
    kit.published(shell, "sessions", kit.holding_session(sid, red=True))
    return sid


def fill_launch(panel, **values: str) -> None:
    """Set each of `values` on the launch form, then preview."""
    form = panel.locator("moxi-ui-form")
    for name, value in values.items():
        found = kit.control(form, name)
        if name == "target":
            found.select_option(value)
        else:
            found.fill(value)
    form.locator("button.submit").click()


# --- the contract ---------------------------------------------------------------------------

def test_the_panel_keeps_the_contract(ui_shell, ui_page, ui_component_check):
    check = ui_component_check(ui_page, "launcher", "page")
    assert check.element == "moxi-launcher-page"
    assert any(url.endswith("/m/launcher/api/catalog") for url in check.requests), check.requests


def test_the_sessions_block_keeps_the_contract_and_shows_red_in_the_error_colour(
        ui_shell, ui_page, ui_component_check):
    sid = red_session(ui_shell, "red-block")
    check = ui_component_check(ui_page, "launcher", "sessions", keep=True)
    assert check.element == "moxi-launcher-sessions"
    row = check.locator.locator(f'li[data-session="{sid}"]')
    expect(row).to_have_attribute("data-red", "true")
    expect(row).to_have_attribute("data-word", "failed")
    word = row.locator('[data-field="word"]')
    expect(word).to_have_text("failed")
    expect(row.locator('[data-field="head"]')).to_contain_text("the new session did not start")
    kit.assert_colour(word, "error")


def test_the_state_component_keeps_the_contract_and_shows_red_in_the_error_colour(
        ui_shell, ui_page, ui_component_check):
    sid = red_session(ui_shell, "red-state")
    check = ui_component_check(ui_page, "launcher", "state", {"session": sid}, keep=True)
    assert check.element == "moxi-launcher-state"
    assert any(f"/m/launcher/api/state?session={sid}" in url for url in check.requests)
    span = check.locator.locator("span.state")
    expect(span).to_have_attribute("data-red", "true")
    expect(span).to_have_attribute("data-word", "failed")
    expect(span).to_have_attribute("title", re.compile("the new session did not start"))
    kit.assert_colour(span, "error")


# --- a session going red, by push -----------------------------------------------------------

def test_a_live_session_whose_relaunch_fails_goes_red_in_the_launches_list(ui_shell, ui_page):
    sid = kit.seed(ui_shell, "turns-red", live=True)
    kit.published(ui_shell, "sessions", kit.holding_session(sid, red=False))
    panel = kit.open_panel(ui_page)
    row = panel.locator('section.launches li[data-request="turns-red"]')
    expect(row).to_have_attribute("data-live", "true")
    chip = row.locator(f'[data-session="{sid}"] moxi-launcher-state span.state')
    expect(chip).to_have_attribute("data-red", "false")
    kit.failed_run(ui_shell, "turns-red")
    # The poller sees the store move within a second, and the topic carries the state.
    expect(chip).to_have_attribute("data-red", "true", timeout=10_000)
    expect(chip).to_have_attribute("data-word", "failed")
    expect(chip).to_have_text("failed")
    kit.assert_colour(chip, "error")


def test_a_live_session_given_a_new_id_by_a_clear_keeps_its_state(ui_shell, ui_page):
    sid = kit.seed(ui_shell, "cleared", live=True)
    kit.published(ui_shell, "sessions", kit.holding_session(sid, red=False))
    panel = kit.open_panel(ui_page)
    row = panel.locator('section.launches li[data-request="cleared"]')
    chip = row.locator(f'[data-session="{sid}"] moxi-launcher-state span.state')
    expect(chip).to_have_attribute("data-state", "up")
    # A clear gives the session a new id; the launch row keeps the first, which the chip
    # was placed with, and the state comes under the new one.
    newer = kit.learned(ui_shell, "cleared")
    kit.published(ui_shell, "sessions", kit.holding_session(newer, red=False))
    kit.failed_run(ui_shell, "cleared")
    expect(chip).to_have_attribute("data-red", "true", timeout=10_000)
    expect(chip).to_have_attribute("data-state", "up")
    expect(chip).to_have_text("failed")


# --- 360 px ---------------------------------------------------------------------------------

def test_the_panel_with_its_sessions_block_and_the_block_alone_fit_a_phone(
        ui_shell, ui_page, ui_width_check, tmp_path):
    sid = red_session(ui_shell, "narrow-live")
    kit.seed(ui_shell, "narrow-past", target="terminal", cwd=LONG_CWD)
    ui_width_check(ui_page, module="launcher")
    panel = ui_page.locator("#moxi-panel > moxi-launcher-page")
    expect(panel.locator('section.launches li[data-request="narrow-live"] '
                         'moxi-launcher-state span.state')).to_have_attribute("data-red", "true")
    # The sessions block is on the panel too, with the session's recycle and keep-warm
    # lines in view, and its heading marks the page it is on.
    block = panel.locator('[data-part="sessions"] moxi-launcher-sessions')
    session = block.locator(f'li[data-session="{sid}"]')
    expect(session).to_be_visible()
    expect(session).to_have_attribute("data-red", "true")
    expect(session.locator(".lines")).to_contain_text("recycle: on expiry off")
    expect(session.locator(".lines")).to_contain_text("keep warm: off")
    expect(block.locator("h2 a")).to_have_attribute("aria-current", "page")
    ui_width_check(ui_page)
    # A terminal launch's preview, with its long command to copy.
    fill_launch(panel, cwd=str(tmp_path))
    expect(panel.locator('[data-part="preview"] [data-copy="preview"]')).to_be_visible()
    ui_width_check(ui_page)
    # A past terminal session's command to resume.
    past = panel.locator('section.past li[data-request="narrow-past"]')
    past.locator('button[data-action="resume"]').click()
    expect(past.locator("[data-copy]")).to_be_visible()
    ui_width_check(ui_page)
    ui_width_check(ui_page, module="launcher", name="sessions")


# --- what a preview sends -------------------------------------------------------------------

#: Types `value` into the form's `cwd` and has the panel draw again before the panel's own
#: look at what changed, which a busy page does when an answer lands just then.
_TYPE_THEN_DRAW = """async (panel, value) => {
  const form = panel.shadowRoot.querySelector("moxi-ui-form");
  const cwd = form.shadowRoot.querySelector('[data-control="cwd"]');
  cwd.value = value;
  cwd.dispatchEvent(new Event("input", {bubbles: true, composed: true}));
  panel.requestUpdate();
  await panel.updateComplete;
}"""


def test_a_field_typed_as_the_panel_draws_again_is_still_sent(ui_shell, ui_page, tmp_path):
    panel = kit.open_panel(ui_page)
    form = panel.locator("moxi-ui-form")
    kit.control(form, "cwd").wait_for()
    sent: list[str] = []
    ui_page.on("request", lambda request: sent.append(request.post_data)
               if request.url.endswith("/m/launcher/api/preview") else None)
    panel.evaluate(_TYPE_THEN_DRAW, str(tmp_path))
    form.locator("button.submit").click()
    preview = panel.locator('[data-part="preview"]')
    expect(preview).to_have_attribute("data-target", "terminal")
    expect(preview.locator(".refused")).to_have_count(0)
    assert sent == [f'{{"values":{{"cwd":"{tmp_path}"}}}}'], sent


# --- a launch -------------------------------------------------------------------------------

def test_a_headless_launch_runs_the_harness_and_its_row_arrives_by_push(ui_shell, ui_page,
                                                                       harness, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    reads: list[str] = []
    ui_page.on("request", lambda request: reads.append(request.url)
               if request.url.endswith(kit.LAUNCHES) else None)
    panel = kit.open_panel(ui_page)
    # The panel and its sessions block each read the launch log once as they open. The
    # page's events reach the list only while Playwright runs, hence the short waits.
    wait_for(lambda: ui_page.wait_for_timeout(20) or len(reads) == 2, 10,
             what=lambda: f"two reads of the launch log; {reads}")
    fill_launch(panel, target="headless", cwd=str(work), brief=BRIEF)
    preview = panel.locator('[data-part="preview"]')
    expect(preview).to_have_attribute("data-target", "headless")
    expect(preview.locator('[data-field="argv"]')).to_contain_text(BRIEF)
    expect(preview.locator(".refused")).to_have_count(0)
    preview.locator('button[data-action="launch"]').click()
    launched = panel.locator('[data-part="launched"]')
    expect(launched).to_have_attribute("data-request", re.compile(".+"))
    request_id = launched.get_attribute("data-request")
    row = panel.locator(f'section.launches li[data-request="{request_id}"]')
    expect(row).to_have_count(1, timeout=10_000)
    expect(row).to_have_class("mine")
    expect(row.locator('[data-field="cwd"]')).to_have_text(str(work))
    expect(launched.locator('[data-follow="row"]')).to_be_visible()
    # The launch log was read only as the panel opened: the row came by the topic.
    assert len(reads) == 2, reads
    # The job ran the fake harness found on the shell's PATH, in the directory, with the
    # brief; its output went to the launch's run log.
    [run] = wait_for(lambda: [run for run in harness.runs() if run["cwd"] == str(work)], 10,
                     what="the fake harness's run in the launch's directory")
    assert run["argv"][1] == "-p" and BRIEF in run["argv"][2], run["argv"]
    log = targets.run_log(request_id, env=ui_shell.env)
    wait_for(lambda: log.is_file() and "the fake harness ran" in log.read_text(), 10,
             what="the fake harness's line in the launch's run log")


def test_a_refusal_met_before_the_row_shows_under_the_launch(ui_shell, ui_page, tmp_path):
    work = tmp_path / "gone-soon"
    work.mkdir()
    panel = kit.open_panel(ui_page)
    fill_launch(panel, target="headless", cwd=str(work), brief=BRIEF)
    preview = panel.locator('[data-part="preview"]')
    expect(preview.locator('button[data-action="launch"]')).to_be_visible()
    # The directory goes between the preview and the launch: the job finds it gone before
    # it writes the row, and records the refusal under the request id.
    work.rmdir()
    preview.locator('button[data-action="launch"]').click()
    launched = panel.locator('[data-part="launched"]')
    refused = launched.locator('[data-follow="refused"]')
    expect(refused).to_have_attribute("data-code", "no-cwd", timeout=10_000)
    expect(refused).to_contain_text(f"the directory {work} does not exist")
    request_id = launched.get_attribute("data-request")
    expect(panel.locator(f'section.launches li[data-request="{request_id}"]')).to_have_count(0)


# --- a resume -------------------------------------------------------------------------------

def test_a_past_terminal_session_gives_the_command_to_resume(ui_shell, ui_page):
    sid = kit.seed(ui_shell, "past-terminal", target="terminal")
    panel = kit.open_panel(ui_page)
    past = panel.locator('section.past li[data-request="past-terminal"]')
    expect(past).to_have_attribute("data-resume", "terminal")
    resume = past.locator('button[data-action="resume"]')
    expect(resume).to_have_text("Command to resume")
    resume.click()
    expect(past.locator("[data-copy]")).to_have_text(f"moxi launcher resume {sid}")
    expect(past.locator('[data-field="resumed"]')).to_contain_text(
        "Run this in a terminal of your own")
