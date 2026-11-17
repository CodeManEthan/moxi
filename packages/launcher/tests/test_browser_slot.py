"""Real slots in a browser, through a real shell whose page server launches on them: a
past slot session resumed from the launcher's page (the resume button, the job it starts,
and the new launch's row arriving by push), and a launch from the box on Home, which
starts the fake harness in a slot and names that slot under the box.

slots runs on a tmux server of its own. The test shell's `MOXI_HOME` is under its own
temporary home, slots' socket follows `MOXI_HOME`, and the server is started here on that
socket and stopped after, so the default tmux server, and any other, is never reached.
Skipped where slots is not installed or tmux 3.2 or later is not on `PATH`.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys

import pytest
from moxi_dev.testing import wait_for
from playwright.sync_api import expect

import launcher_page_kit as kit
from moxi_launcher import store

pytestmark = [pytest.mark.browser,
              pytest.mark.skipif(sys.platform == "win32", reason="slots needs tmux"),
              pytest.mark.skipif(importlib.util.find_spec("moxi_slots") is None,
                                 reason="slots is not installed"),
              pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not on PATH")]


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    return kit.Harness(tmp_path_factory.mktemp("fake-harness"))


@pytest.fixture(scope="module")
def ui_shell_options(harness):
    """The shell's options, given once the fake harness is first on `PATH`."""
    with kit.first_on_path(harness.bin):
        yield {}


@pytest.fixture(scope="module")
def own_slots(ui_shell):
    """slots' server on the shell's own socket; yields the socket."""
    with kit.own_slots(ui_shell) as socket:
        yield socket


def test_a_past_slot_session_resumes_in_its_slot_and_the_new_row_arrives_by_push(
        ui_shell, own_slots, ui_page, harness, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    sid = kit.seed(ui_shell, "past-slot", target="slot", cwd=str(work))
    panel = kit.open_panel(ui_page)
    past = panel.locator('section.past li[data-request="past-slot"]')
    expect(past).to_have_attribute("data-resume", "slot")
    resume = past.locator('button[data-action="resume"]')
    expect(resume).to_have_text("Resume")
    resume.click()
    said = past.locator('[data-field="resumed"]')
    expect(said).to_contain_text("Resuming as launch")
    expect(said.locator('[data-follow="row"]')).to_be_visible(timeout=30_000)
    with kit.launch_log(ui_shell) as conn:
        [new] = [row for row in store.log(conn) if row.resume_of == "past-slot"]
    row = panel.locator(f'section.launches li[data-request="{new.request_id}"]')
    expect(row).to_have_count(1)
    assert (new.target, new.slot, new.session_id) == ("slot", "3", sid)
    [run] = wait_for(lambda: [run for run in harness.runs() if run["cwd"] == str(work)], 20,
                     what="the fake harness's run in the slot")
    assert "--resume" in run["argv"] and sid in run["argv"], run["argv"]


def test_a_launch_from_home_starts_in_a_real_slot_and_names_it(ui_shell, own_slots, ui_page,
                                                               harness):
    piece = kit.open_home(ui_page)
    go = piece.locator(".go")
    expect(go).to_have_attribute("data-target", "slot")
    expect(go).to_have_text(re.compile(r"^Launch in (a new slot|slot \d+)$"))
    go.click()
    started = piece.locator('.said [data-line="started"]')
    expect(started).to_have_text(re.compile(r"^Started in slot \d+ · "), timeout=30_000)
    row = kit.newest_launch(ui_shell)
    assert row.target == "slot" and row.slot, row
    assert f"Started in slot {row.slot} " in started.inner_text()
    # The fake harness ran in the slot, in the folder the line above the box named.
    wait_for(lambda: [run for run in harness.runs() if run["cwd"] == row.cwd], 20,
             what="the fake harness's run in the slot")
