"""cache's chip in a browser, through a real shell that starts cache's real page server:
the contract check, every case of the band table, the bands on the server's clock with
the browser's clock right or 10 minutes ahead, a push update, every failure state, and
cache left out of the shell.

A test that needs the server's clock at a set time answers the chip's read with the real
page server's answer and only its `now` changed (`page_kit.pin_now`), and fixes the
browser's clock with Playwright's: the chip then counts from exact times, so a band's edge
is tested on the edge.
"""

from __future__ import annotations

import json
import re
import sys
import time

import pytest
from band_cases import CASES, NOW
from playwright.sync_api import expect

import page_kit
from page_kit import chip, place_chips, put, record

pytestmark = [pytest.mark.browser,
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="cache runs on Linux only")]

NO_RECORD = "no cache record for this session"
STOPPED = "the cache watcher has stopped; this reading may be old"
UNEXPECTED = "cache's page server hit an unexpected error (ValueError); its log has the details"
#: A session no record names.
NOBODY = "nobody"
#: The browser's clock: on the server's, or 10 minutes ahead of it.
AHEAD = pytest.mark.parametrize("ahead", [0, 600], ids=["same-clock", "browser-10-min-ahead"])

#: The colour token each look of the chip takes its text colour from.
_TONE_TOKEN = {"warm": "ok", "near": "warn", "cold": "error", "no reading": "text-dim"}

_COLOURS = """([session, token]) => {
  const span = document.querySelector(`[data-cell="${session}"] moxi-cache-chip`)
    .shadowRoot.querySelector(".chip");
  const probe = document.createElement("span");
  probe.style.color = `var(--moxi-${token})`;
  span.after(probe);
  const want = getComputedStyle(probe).color;
  probe.remove();
  return {seen: getComputedStyle(span).color, want};
}"""


@pytest.fixture
def shell(ui_shell):
    """The module's shell, with no clock record left by an earlier test. A test that
    reads records waits for the topic to hold the ones it writes (`page_kit.published`)."""
    page_kit.clear(ui_shell)
    return ui_shell


def clocked(shell, ui_contexts, at: float):
    """A page signed in on `shell` whose browser clock stands still at `at` (epoch
    seconds); its timers still run."""
    context = ui_contexts(shell, sign_in=False)
    context.clock.set_fixed_time(at)
    return shell.sign_in(context.new_page())


def test_the_chip_keeps_the_contract(shell, ui_page, ui_component_check):
    put(shell, record("s1"))
    page_kit.published(shell, page_kit.holding(["s1"]))
    # A chip's band colours come once its read answers, which the check doesn't wait for;
    # the sunken background is there from the first render, loading or not.
    check = ui_component_check(ui_page, "cache", "chip", {"session": "s1"},
                               tokens=("accent", "bg-sunken"))
    assert check.element == "moxi-cache-chip"
    assert any(page_kit.CLOCKS in url for url in check.requests), check.requests


@AHEAD
def test_every_band_case_shows_on_the_chip(shell, ui_contexts, ahead):
    sessions = [case["record"]["session"] for case in CASES]
    for case in CASES:
        put(shell, case["record"])
    page_kit.published(shell, page_kit.holding(sessions))
    page = clocked(shell, ui_contexts, NOW + ahead)
    page_kit.pin_now(page, NOW)
    place_chips(page, *sessions, NOBODY)
    for case in CASES:
        span = chip(page, case["record"]["session"])
        expect(span, case["name"]).to_have_attribute("data-state", "up")
        expect(span, case["name"]).to_have_attribute("data-shows", case["shows"])
        expect(span, case["name"]).to_have_attribute("data-dim", json.dumps(case["dim"]))
    nobody = chip(page, NOBODY)
    expect(nobody).to_have_attribute("data-shows", "no reading")
    expect(nobody).to_have_text("no reading")
    expect(nobody).to_have_attribute("title", NO_RECORD)
    for case in CASES:
        span, rec = chip(page, case["record"]["session"]), case["record"]
        if case["dim"]:
            expect(span, case["name"]).to_have_attribute("title", STOPPED)
            token = "text-faint"
        elif case["shows"] in _TONE_TOKEN:
            token = _TONE_TOKEN[case["shows"]]
        else:
            # A kind's word, with the record's code as its title.
            expect(span, case["name"]).to_have_text(case["shows"])
            expect(span, case["name"]).to_have_attribute("title", rec["code"])
            token = "text"
        colours = page.evaluate(_COLOURS, [rec["session"], token])
        assert colours["seen"] == colours["want"], (case["name"], token, colours)


@AHEAD
def test_the_chip_goes_warm_near_cold_at_the_edges_on_the_server_clock(shell, ui_contexts,
                                                                       ahead):
    start = float(int(time.time()))
    put(shell, record("tick", now=start, age=0, ttl=2, warn_before=1, seen=0))
    page_kit.published(shell, page_kit.holding(["tick"]))
    page = clocked(shell, ui_contexts, start + ahead)
    page_kit.pin_now(page, start)
    place_chips(page, "tick")
    span = chip(page, "tick")
    for later, shows, text in ((0, "warm", "warm 2s left"), (0.999, "warm", "warm 2s left"),
                               (1, "near", "near 1s left"), (1.999, "near", "near 1s left"),
                               (2, "cold", "cold 0s ago")):
        page.context.clock.set_fixed_time(start + ahead + later)
        expect(span, f"{later} s after the request").to_have_attribute("data-shows", shows)
        expect(span).to_have_text(text)
        expect(span).to_have_attribute("data-dim", "false")
    assert page_kit.chip_states(page, "tick") == ["loading", "up"]


def test_a_rewritten_record_turns_a_cold_chip_warm_by_push(shell, ui_page):
    put(shell, record("push", age=4230))
    page_kit.published(shell, page_kit.holding(["push"]))
    reads: list[str] = []
    ui_page.on("request", lambda request: reads.append(request.url)
               if page_kit.CLOCKS in request.url else None)
    place_chips(ui_page, "push")
    span = chip(ui_page, "push")
    expect(span).to_have_attribute("data-shows", "cold")
    expect(span).to_have_text("cold 10m ago")
    put(shell, record("push", age=60))
    # The poller looks every 2 s, and the topic carries the new record to the chip.
    expect(span).to_have_attribute("data-shows", "warm", timeout=10_000)
    expect(span).to_have_text(re.compile(r"^warm 5[89]m left$"))
    assert len(reads) == 1, reads
    assert page_kit.chip_states(ui_page, "push") == ["loading", "up"]


@pytest.mark.parametrize("answer, why", [
    ({"status": 500, "json": {"error": "boom"}}, "cache's page server answered 500"),
    ({"status": 502, "json": {"error": "start"}}, "cache's page server answered 502"),
    ({"status": 504, "body": ""}, "cache's page server answered 504"),
    ({"status": 401, "json": {"error": "who"}}, "cache's page server answered 401"),
    ({"status": 200, "body": "<p>not JSON</p>", "content_type": "text/html"},
     "cache's page server sent an answer that can't be read"),
    ({"status": 200, "json": {"now": 1.0, "state": "maybe"}},
     "cache's page server sent an answer that can't be read"),
    ({"status": 200, "json": {"state": "up", "detail": None, "clocks": []}},
     "cache's page server sent an answer that can't be read"),
    (None, "no answer from the shell"),
], ids=["500", "502", "504", "401", "not-json", "not-the-shape", "no-now", "no-answer"])
def test_a_failed_read_shows_the_failure_row(shell, ui_page, answer, why):
    ui_page.route("**" + page_kit.CLOCKS,
                  lambda route: route.abort() if answer is None else route.fulfill(**answer))
    place_chips(ui_page, "s1")
    span = chip(ui_page, "s1")
    expect(span).to_have_attribute("data-state", "failed")
    expect(span).to_have_text("cache ?")
    expect(span).to_have_attribute("title", why)


def test_a_503_is_read_again_and_the_next_answer_shows_with_no_error(shell, ui_page):
    put(shell, record("s1"))
    page_kit.published(shell, page_kit.holding(["s1"]))
    hits: list[int] = []

    def busy_once(route):
        hits.append(1)
        if len(hits) == 1:
            route.fulfill(status=503, json={"error": "busy"}, headers={"Retry-After": "1"})
        else:
            route.fallback()

    ui_page.route("**" + page_kit.CLOCKS, busy_once)
    place_chips(ui_page, "s1")
    span = chip(ui_page, "s1")
    expect(span).to_have_attribute("data-shows", "warm")
    assert len(hits) == 2
    assert page_kit.chip_states(ui_page, "s1") == ["loading", "up"]


def test_four_503s_in_a_row_show_the_failure_row(shell, ui_page):
    hits: list[int] = []

    def busy(route):
        hits.append(1)
        route.fulfill(status=503, json={"error": "busy"}, headers={"Retry-After": "1"})

    ui_page.route("**" + page_kit.CLOCKS, busy)
    began = time.monotonic()
    place_chips(ui_page, "s1")
    span = chip(ui_page, "s1")
    # Three reads again, 1 s apart, stay loading; the fourth 503 is the failure.
    expect(span).to_have_attribute("data-state", "failed", timeout=10_000)
    assert time.monotonic() - began >= 2.9
    expect(span).to_have_attribute("title", "cache's page server answered 503")
    expect(span).to_have_text("cache ?")
    # Once failed, it subscribes, and the topic's first event reads the route again.
    assert len(hits) >= 4
    assert page_kit.chip_states(ui_page, "s1") == ["loading", "failed"]


def test_a_404_after_placement_renders_nothing(shell, ui_page):
    ui_page.route("**" + page_kit.CLOCKS,
                  lambda route: route.fulfill(status=404, json={"error": "no such module"}))
    place_chips(ui_page, "s1")
    element = ui_page.locator('[data-cell="s1"] moxi-cache-chip')
    expect(element).to_have_count(1)
    page_kit.wait_for(lambda: page_kit.chip_states(ui_page, "s1")[-1] == "none", 5,
                      what="the chip to empty itself")
    expect(chip(ui_page, "s1")).to_have_count(0)


def test_an_error_from_the_page_server_shows_its_detail(shell, ui_page):
    # A clock record holding a number JSON can't carry makes the page server's read fail,
    # so the route and the topic both answer `state: error`.
    put(shell, record("bad", ttl=float("inf")))
    page_kit.published(shell, lambda data: data["state"] == "error")
    place_chips(ui_page, "bad", "s1")
    for session in ("bad", "s1"):
        span = chip(ui_page, session)
        expect(span).to_have_attribute("data-state", "error")
        expect(span).to_have_text("cache ?")
        expect(span).to_have_attribute("title", UNEXPECTED)


def test_a_failed_chip_reads_again_on_a_topic_event_and_when_its_tab_is_shown(shell, ui_page):
    put(shell, record("s1"))
    page_kit.published(shell, page_kit.holding(["s1"]))
    hits: list[int] = []

    def fail(route):
        hits.append(1)
        route.fulfill(status=500, json={"error": "boom"})

    ui_page.route("**" + page_kit.CLOCKS, fail)
    place_chips(ui_page, "s1")
    span = chip(ui_page, "s1")
    expect(span).to_have_attribute("data-state", "failed")
    # Once failed it subscribes, and the topic's first event reads the route again; that
    # read fails too.
    page_kit.wait_for(lambda: ui_page.evaluate("true") and len(hits) >= 2, 5,
                      what="the topic's first event to read the route again")
    ui_page.unroute("**" + page_kit.CLOCKS)
    expect(span).to_have_attribute("data-state", "failed")
    # A changed record is the next topic event, which reads the route again.
    put(shell, record("s1", age=4230))
    expect(span).to_have_attribute("data-shows", "cold", timeout=10_000)
    ui_page.route("**" + page_kit.CLOCKS, fail)
    page_kit.show_tab(ui_page)
    expect(span).to_have_attribute("data-state", "failed")
    expect(span).to_have_attribute("title", "cache's page server answered 500")
    ui_page.unroute("**" + page_kit.CLOCKS)
    page_kit.show_tab(ui_page)
    expect(span).to_have_attribute("data-shows", "cold")
    assert page_kit.chip_states(ui_page, "s1") == ["loading", "failed", "up", "failed", "up"]


def test_with_cache_left_out_no_chip_is_placed_and_nothing_asks_cache(ui_shells, ui_contexts):
    shell = ui_shells(leave_out=["cache"])
    page = ui_contexts(shell).pages[0]
    asked: list[str] = []
    page.context.on("request", lambda request: asked.append(request.url)
                    if "/m/cache/" in request.url else None)
    assert place_chips(page, "s1", placed=False) == [False]
    expect(page.locator("#chips moxi-cache-chip")).to_have_count(0)
    page.wait_for_timeout(300)
    assert asked == []
