"""turns' panel in a browser, through a real shell that starts turns' real page server, on
the full install and on the small one: the contract check, the 360 px width check, and
the panel's questions: which sessions are running, by folder and start time (TU1); which
is idle and which working, each since when (TU2); and the planner's busy subagent on its
row, with both its agents in the opened row (TU3).

The shells, the stand-in `claude` and the helpers are `panel_kit`'s.
"""

from __future__ import annotations

import re
import sys

import pytest
from playwright.sync_api import expect

from moxi_turns.results import Agent
from panel_kit import (T, full, group, harness, key_of, keys, line, on, open_panel, own_panel, row, rows,
                       small, started)

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="turns' panel is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

__all__ = ["full", "own_panel", "small"]

SID = "aaaaaaaa-1111-4111-8111-111111111111"
SID2 = "bbbbbbbb-2222-4222-8222-222222222222"
SID3 = "cccccccc-3333-4333-8333-333333333333"
#: The reach line for `start` with launcher left out.
START_SMALL = ("Interactive Claude Code sessions show here from their start. Codex and OpenCode "
               "sessions started by hand don't.")


@pytest.fixture(params=["full", "small"])
def panel(request, full, small, turns_env, monkeypatch):
    """Each install's shell in turn, with this process's turns runtime root set to it.
    Both shells are the module's, started before the test's own fence."""
    return on(full if request.param == "full" else small, monkeypatch)


@pytest.fixture
def full_panel(full, turns_env, monkeypatch):
    return on(full, monkeypatch)


# --- the contract and the phone's width -------------------------------------------------------

def test_the_page_keeps_the_contract(full_panel, make_endpoint, ui_contexts, ui_component_check):
    ep, _ = make_endpoint(harness(), session_id=SID)
    full_panel.listed({key_of(ep)})
    page = ui_contexts(full_panel.shell).pages[0]
    # With launcher installed the page reads launcher's running launches.
    check = ui_component_check(page, "turns", "page", allow=("launcher",))
    assert check.element == "moxi-turns-page"
    assert any("/m/turns/api/sessions" in url for url in check.requests), check.requests


def test_the_page_fits_a_phone(full_panel, make_endpoint, ui_contexts, ui_width_check):
    long = "a-very-long-description-with-no-spaces-" * 6
    deep = harness("/srv/" + "deep/" * 40 + "project", trouble="t" * 200,
                   entries=[Agent("a" * 80, "subagent", "running", final=False, description=long)])
    one, _ = make_endpoint(deep, session_id="b" * 120)
    two, _ = make_endpoint(harness(), session_id=SID)
    full_panel.listed({key_of(one), key_of(two)})
    page = ui_contexts(full_panel.shell).pages[0]
    ui_width_check(page, module="turns")
    expect(rows(page.locator("#moxi-panel > moxi-turns-page"))).to_have_count(2)
    ui_width_check(page)


# --- TU1: which sessions are running, by folder and start time ---------------------------------

def test_which_sessions_are_running_named_by_cwd_and_start_time(panel, make_endpoint, ui_contexts):
    older, _ = make_endpoint(harness("/srv/work/parser"), session_id=SID)
    newer, _ = make_endpoint(harness("/srv/work/kestrel-web/"), session_id=SID2)
    data = panel.listed({key_of(older), key_of(newer)}, reach="start")
    # The page server read the reach through the stand-in, never a real `claude`.
    assert "plugin list --json" in panel.stub.calls()
    assert data["reach"]["claude"] == "start"
    page, root = open_panel(panel, ui_contexts, launches=None if panel.is_small else [])
    expect(root.locator("div[data-state]")).to_have_attribute("data-state", "up")
    expect(group(root, "idle").locator('[data-line="label"]')).to_have_text("2 idle")
    # No since-time for either: the newest start first.
    expect(rows(root)).to_have_count(2)
    assert keys(root) == [key_of(newer), key_of(older)]
    expect(row(root, key_of(newer))).to_have_attribute("name", started("kestrel-web"))
    expect(row(root, key_of(older))).to_have_attribute("name", started("parser"))
    for ep in (older, newer):
        expect(row(root, key_of(ep))).not_to_have_attribute("where", re.compile("."))
        expect(row(root, key_of(ep)).locator('[data-action="open"]')).to_have_text("Open")
    foot = root.locator('[data-part="foot"]')
    expect(foot.locator('[data-action="settings"]')).to_have_text("Settings › turns")
    reach = foot.locator('[data-line="reach"]')
    if panel.is_small:
        expect(reach).to_have_text(START_SMALL)
    else:
        expect(reach).to_contain_text("Interactive Claude Code sessions show here from their start, "
                                      "and ")
    expect(reach.locator("button")).to_have_count(0)


# --- TU2: which is idle and since when, which is working and since when ------------------------

def test_which_is_idle_and_which_working_each_since_when(panel, make_endpoint, ui_contexts):
    found, _ = make_endpoint(harness("/srv/work/found-idle"), session_id=SID)
    moving, fake = make_endpoint(harness("/srv/work/moving"), session_id=SID2)
    panel.listed({key_of(found), key_of(moving)})
    page, root = open_panel(panel, ui_contexts, launches=None if panel.is_small else [])
    expect(group(root, "idle").locator('[data-line="label"]')).to_have_text("2 idle")
    # Found idle, with no turn seen to end and no ready: no since-time to give.
    for ep in (found, moving):
        expect(row(root, key_of(ep))).not_to_have_attribute("fact", re.compile("."))

    fake.start_turn()
    working = group(root, "working")
    expect(working.locator('[data-line="label"]')).to_have_text("1 working")
    expect(working.locator(f'moxi-ui-row[data-key="{key_of(moving)}"]')).to_have_attribute(
        "fact", re.compile(rf"^since {T}$"))
    expect(group(root, "idle").locator('[data-line="label"]')).to_have_text("1 idle")

    fake.end_turn()
    expect(working).to_have_count(0)
    expect(group(root, "idle").locator('[data-line="label"]')).to_have_text("2 idle")
    # The turn ended while the panel was open: idle since it ended, and first, as the
    # latest change.
    expect(row(root, key_of(moving))).to_have_attribute("fact", re.compile(rf"^since {T}$"))
    expect(row(root, key_of(found))).not_to_have_attribute("fact", re.compile("."))
    assert keys(root) == [key_of(moving), key_of(found)]
    row(root, key_of(moving)).locator('[data-action="open"]').click()
    expect(line(root.locator('[data-part="opened"]'), "now")).to_have_text(
        re.compile(rf"^idle since {T} · turn 1$"))


# --- TU3: the planner's busy subagent, and both its agents opened ------------------------------

def test_the_planners_busy_subagent_is_on_line_two_and_both_agents_in_the_opened_row(
        panel, make_endpoint, ui_contexts):
    agents = [Agent("a1", "subagent", "running", final=False, description="Check the plan"),
              Agent("a2", "subagent", "done", final=True, description="Read the tests")]
    planner, _ = make_endpoint(harness("/srv/work/planner", name="claude", entries=agents),
                               session_id=SID3)
    quiet, _ = make_endpoint(harness("/srv/work/quiet"), session_id=SID)
    panel.listed({key_of(planner), key_of(quiet)})
    page, root = open_panel(panel, ui_contexts, launches=None if panel.is_small else [])
    busy = row(root, key_of(planner))
    expect(busy).to_have_attribute("lead", "1 subagent busy")
    expect(busy).to_have_attribute("rest", "Check the plan")
    expect(busy).not_to_have_attribute("tail", re.compile("."))
    # A row with no news is one line.
    expect(row(root, key_of(quiet))).not_to_have_attribute("lead", re.compile("."))

    busy.locator('[data-action="open"]').click()
    expect(busy.locator('[data-action="open"]')).to_have_text("Close")
    opened = busy.locator('[data-part="opened"]')
    entries = opened.locator("[data-agent]")
    expect(entries).to_have_count(2)
    expect(entries).to_have_text(["Check the plan · subagent · running",
                                  "Read the tests · subagent · done"])
    expect(entries.nth(0).locator("[data-busy]")).to_have_attribute("data-busy", "true")
    expect(entries.nth(1).locator("[data-busy]")).to_have_attribute("data-busy", "false")
    expect(opened.locator('[data-line="not-known"]')).to_have_count(0)
    expect(opened.locator('[data-line="prompt-box"] .k')).to_have_text("Prompt box")
    expect(line(opened, "prompt-box")).to_have_text("empty")
    expect(line(opened, "session")).to_have_text(
        re.compile(rf"^Claude Code · started today {T} · /srv/work/planner$"))
    expect(line(opened, "details")).to_have_text(re.compile(r"^pid \d+ · turns \S+$"))
    # The other row has no box: no prompt box line when it opens.
    row(root, key_of(quiet)).locator('[data-action="open"]').click()
    expect(busy.locator('[data-part="opened"]')).to_have_count(0)
    expect(row(root, key_of(quiet)).locator('[data-line="prompt-box"]')).to_have_count(0)
