"""turns' panel in a browser, through a real shell that starts turns' real page server, on
the full install and on the small one: the contract check, the 360 px width check, and
the panel's questions: which sessions are running, by folder and start time; which is
idle and which working, each since when; the planner's busy subagent on its row, with both
its agents in the opened row; a session in trouble and one whose live part did not read,
both problems that keep their state, with the groups in their order; and the reach line,
which asks for the install and then, in the same page, says the sessions show. Then a
launch from launcher naming its row and where it runs.

The shells, the stand-in `claude` and the helpers are `panel_kit`'s.
"""

from __future__ import annotations

import re
import sys

import pytest
from playwright.sync_api import expect

from moxi_turns.results import Agent
from panel_kit import (T, full, group, harness, key_of, keys, launch, line, on, open_panel, own_panel, row,
                       rows, small, started)

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="turns' panel is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

__all__ = ["full", "own_panel", "small"]

SID = "aaaaaaaa-1111-4111-8111-111111111111"
SID2 = "bbbbbbbb-2222-4222-8222-222222222222"
SID3 = "cccccccc-3333-4333-8333-333333333333"
SID4 = "eeeeeeee-5555-4555-8555-555555555555"
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


# --- which sessions are running, by folder and start time -------------------------------------

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


# --- which is idle and since when, which is working and since when ----------------------------

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


# --- the planner's busy subagent, and both its agents opened ----------------------------------

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
    # The other row has no box and no agent: no prompt box line and no agents line when it
    # opens.
    row(root, key_of(quiet)).locator('[data-action="open"]').click()
    expect(busy.locator('[data-part="opened"]')).to_have_count(0)
    expect(row(root, key_of(quiet)).locator('[data-line="session"]')).to_have_count(1)
    expect(row(root, key_of(quiet)).locator('[data-line="prompt-box"]')).to_have_count(0)
    expect(row(root, key_of(quiet)).locator('[data-line="agents"]')).to_have_count(0)


# --- what is wrong, and what it still holds ---------------------------------------------------

#: A time of day with its seconds, as `can't read since` gives it.
TS = r"\d{1,2}:\d\d:\d\d(?: [AP]M)?"


def test_a_trouble_row_and_an_unread_one_are_problems_that_keep_their_state(
        panel, make_endpoint, ui_contexts):
    linked, linked_fake = make_endpoint(
        harness("/srv/work/linked", trouble="the host is loading the thread"), session_id=SID)
    # The live part throws, and the agent list the session gives is not whole.
    unread, _ = make_endpoint(harness("/srv/work/unread", name="claude", live_fails=True,
                                      agents_complete=False), session_id=SID2)
    well, _ = make_endpoint(harness("/srv/work/well"), session_id=SID3)
    busy, busy_fake = make_endpoint(harness("/srv/work/busy"), session_id=SID4)
    panel.listed({key_of(linked), key_of(unread), key_of(well), key_of(busy)})
    linked_fake.start_turn()
    busy_fake.start_turn()
    page, root = open_panel(panel, ui_contexts, launches=None if panel.is_small else [])
    problems = group(root, "problem")
    expect(problems.locator('[data-line="label"]')).to_have_text("2 problems")
    expect(problems.locator("moxi-ui-row")).to_have_count(2)
    expect(group(root, "idle").locator('[data-line="label"]')).to_have_text("1 idle")
    expect(group(root, "working").locator('[data-line="label"]')).to_have_text("1 working")
    # Problems first, then idle, then working.
    assert root.locator("section.grp").evaluate_all("(gs) => gs.map((g) => g.dataset.group)") == [
        "problem", "idle", "working"]
    trouble = row(root, key_of(linked))
    expect(trouble).to_have_attribute("lead", "Its link to Moxi has trouble")
    expect(trouble).to_have_attribute("lead-heavy", "")
    expect(trouble).to_have_attribute("rest", "the host is loading the thread")
    expect(trouble).to_have_attribute("rest-strong", "")
    # A problem row keeps its state on line one.
    expect(trouble).to_have_attribute("fact", re.compile(rf"^working since {T}$"))
    unread_row = row(root, key_of(unread))
    expect(unread_row).to_have_attribute("lead", "Moxi can't read its live state")
    expect(unread_row).to_have_attribute("rest", "its agent list could not be read whole")
    expect(unread_row).to_have_attribute("fact", "idle")
    expect(unread_row).to_have_attribute("note", "may hold a draft")

    unread_row.locator('[data-action="open"]').click()
    opened = unread_row.locator('[data-part="opened"]')
    expect(line(opened, "now")).to_have_text("idle")
    expect(opened.locator('[data-line="not-known"]')).to_have_text(
        "not known · the list could not be read whole, so some may still run")
    expect(line(opened, "prompt-box")).to_have_text("may hold a draft")
    expect(line(opened, "details")).to_have_text(re.compile(rf" · can't read since {TS}$"))


# --- the reach line, before and after an install ----------------------------------------------

def test_the_reach_line_asks_for_the_install_then_reads_start_with_no_reload(
        own_panel, make_endpoint, ui_contexts):
    panel = own_panel("setup", small=True)
    ep, _ = make_endpoint(harness(), session_id=SID)
    panel.listed({key_of(ep)}, reach="setup")
    page, root = open_panel(panel, ui_contexts, permissions=["clipboard-read", "clipboard-write"])
    reach = root.locator('[data-part="foot"] [data-line="reach"]')
    expect(reach).to_have_text(re.compile(
        r"^Interactive Claude Code sessions can't show here yet: run moxi install turns ?Copy\. "
        r"Codex and OpenCode sessions started by hand don't\.$"))
    expect(reach.locator("code")).to_have_text("moxi install turns")
    copy = reach.locator('[data-action="copy-command"]')
    copy.click()
    expect(copy).to_have_text("Copied")
    assert page.evaluate("() => navigator.clipboard.readText()") == "moxi install turns"
    page.evaluate("() => { window.sameDocument = true; }")

    # As an install does: the runner listed first, then `endpoint.json` written. The page
    # server sees the file change at its next scan and reads the reach again, within the
    # read's bound.
    panel.stub.runner_listed()
    panel.write_endpoint_file()
    expect(reach).to_have_text(START_SMALL, timeout=20_000)
    expect(reach.locator("button")).to_have_count(0)
    assert page.evaluate("() => window.sameDocument") is True


# --- launcher's names --------------------------------------------------------------------------

def test_a_launch_names_its_row_and_where_it_runs(full_panel, make_endpoint, ui_contexts):
    slotted, _ = make_endpoint(harness("/srv/work/kestrel-web"), session_id=SID)
    termed, _ = make_endpoint(harness("/srv/work/parser"), session_id=SID2)
    plain, _ = make_endpoint(harness("/srv/work/quiet"), session_id=SID3)
    full_panel.listed({key_of(slotted), key_of(termed), key_of(plain)})
    launches = [launch(SID, "kestrel-builder"),
                launch(SID2, "parser-fix", target="terminal", slot=None, project=None, segment="parser"),
                launch("dddddddd-4444-4444-8444-444444444444", "gone-one"),
                launch(None, "no-id")]
    _, root = open_panel(full_panel, ui_contexts, launches=launches)
    expect(rows(root)).to_have_count(3)
    expect(row(root, key_of(slotted))).to_have_attribute("name", "kestrel-builder")
    expect(row(root, key_of(slotted))).to_have_attribute("where", "slot 2 · kestrel-web")
    expect(row(root, key_of(termed))).to_have_attribute("name", "parser-fix")
    expect(row(root, key_of(termed))).to_have_attribute("where", "terminal · parser")
    expect(row(root, key_of(plain))).to_have_attribute("name", started("quiet"))
    expect(row(root, key_of(plain))).not_to_have_attribute("where", re.compile("."))
