"""comms' roster in a browser with cache left out of the shell: its live rows carry no
chip cell, and nothing asks cache, though cache is installed beside comms."""

from __future__ import annotations

import sys

import pytest
from playwright.sync_api import expect

import roster_kit
from roster_kit import holding, put_clock, wait_for
from test_reply_enforcement import REVIEWER, R

pytestmark = [pytest.mark.browser,
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="comms runs on Linux only")]


@pytest.fixture(scope="module")
def ui_shell_options():
    return {"leave_out": ["cache"], "child_args": ["--idle-s", "1"]}


@pytest.fixture(autouse=True)
def shell(ui_shell, monkeypatch):
    """The module's shell, the rig's daemon the one its comms page server asks."""
    roster_kit.point_at(ui_shell, monkeypatch)
    return ui_shell


def test_with_cache_left_out_a_live_row_has_no_chip_and_nothing_asks_cache(
        shell, rig, ui_contexts, ui_component_check):
    reviewer = rig.shim(**REVIEWER)
    wait_for(lambda: R in rig.daemon.bindings and rig.daemon.bindings[R].session_id, 5,
             what="reviewer to bind")
    put_clock(shell, reviewer.session_id, age=60)
    roster_kit.current(shell, holding([R]))
    page = ui_contexts().pages[0]
    asked: list[str] = []
    page.context.on("request", lambda request: asked.append(request.url)
                    if "/m/cache/" in request.url else None)
    # With no `allow`, a request under /m/cache/ fails the check.
    ui_component_check(page, "comms", "roster", tokens=("accent", "bg-raised"))
    page.goto("/m/comms/")
    roster = page.locator("#moxi-panel > moxi-comms-page moxi-comms-roster div.roster")
    live = roster.locator(f'li[data-address="{R}"]')
    expect(live).to_have_attribute("data-live", "true")
    expect(live.locator('[data-field="bound"]')).to_have_count(1)
    expect(roster.locator("span.cache, moxi-cache-chip")).to_have_count(0)
    page.wait_for_timeout(300)
    assert asked == []
