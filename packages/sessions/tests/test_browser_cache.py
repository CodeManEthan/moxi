"""cache's clocks on sessions' page, in a browser, through a real shell that starts
sessions' real page server: a session started by hand ends its row's first line in `warm
until <time>` or `cold`, from cache's record for it, and turns cold at its time with no
read; opened, its `Cache` line says the same and links cache's page; on a running launch
launcher's cache fact wins, and cache's record speaks only where launcher gives none; a
failed read draws nothing; and a small
install, with ui and sessions only, reads nothing of cache and draws none of it.

cache's `clocks` route is answered in the browser (`CacheStub`), so each test sets the
records it needs, and the shell's push stream is kept from bringing the real cache's
topic `clocks`. The real cache publishes its topic only when its records change, which
they don't here, so the topic's path is proved by the page's unit tests. Launcher's routes are answered as `test_browser_running.py` does.
Every other route is the page servers' own.
"""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Any

import pytest
from moxi import modules
from playwright.sync_api import expect

from test_browser_list import group, open_list, row, sid, tid
from test_browser_running import SMALL, LauncherStub, labels, launch, launched_row, seed, small_shell  # noqa: F401
from test_browser_running import full_shell  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180, method="thread"),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="sessions' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed"),
              pytest.mark.skipif(not any(found.name == "cache" for found in modules.scan()),
                                 reason="moxi-cache is not installed"),
              pytest.mark.skipif(not any(found.name == "launcher" for found in modules.scan()),
                                 reason="moxi-launcher is not installed")]

#: A time as the page words one today: the hour and minute in the browser's locale.
T = r"\d{1,2}:\d\d(?: [AP]M)?"
WARM = re.compile(rf"^warm until {T}$")

#: Rewrites each event of cache's topic `clocks` in the shell's push stream, whose value is
#: the real cache's: it drops each event, so the page knows only what the stubbed route
#: answers. `window.clocksEvents` counts the events it met.
_CLOCKS_TOPIC = r"""(value) => {
  const fetched = window.fetch.bind(window);
  window.clocksEvents = 0;
  window.fetch = async (input, init) => {
    const answer = await fetched(input, init);
    const url = typeof input === "string" ? input : input.url;
    if (!url.includes("/m/ui/api/push") || answer.body === null) return answer;
    const decoder = new TextDecoder();
    const encoder = new TextEncoder();
    let held = "";
    const body = answer.body.pipeThrough(new TransformStream({
      transform(chunk, out) {
        held += decoder.decode(chunk, { stream: true });
        const blocks = held.split("\n\n");
        held = blocks.pop();
        for (const block of blocks) {
          if (!/^event: ?cache\.clocks\s*$/m.test(block)) {
            out.enqueue(encoder.encode(`${block}\n\n`));
            continue;
          }
          window.clocksEvents += 1;
          if (value !== null) out.enqueue(encoder.encode(`event: cache.clocks\ndata: ${JSON.stringify(value)}\n\n`));
        }
      },
    }));
    return new Response(body, { status: answer.status, statusText: answer.statusText, headers: answer.headers });
  };
}"""


def clocks_topic(value: dict | None) -> str:
    """The init script that puts `value` in place of each event of the topic `clocks`, or
    drops each one when `value` is None."""
    return f"({_CLOCKS_TOPIC})({json.dumps(value)})"


def record(session: str, *, cold_in: float = 20 * 60, ttl: float = 3600, harness: str = "claude") -> dict:
    """One clock record in cache's declared shape, timed from now: cold `cold_in` seconds
    from now (in the past when negative), seen a moment ago."""
    now = time.time()
    return {"session": session, "harness": harness, "kind": "warm", "last_request": now + cold_in - ttl, "ttl": ttl,
            "warn_before": 300, "seen_at": now - 1, "stale_after": 120, "code": None}


def value_of(records: list[dict]) -> dict:
    """The topic's value over `records`."""
    return {"state": "up", "detail": None, "today": time.strftime("%Y-%m-%d"), "reading": None, "clocks": records}


class CacheStub:
    """cache's `clocks` route, answered in the browser with `records` and the server's
    `now`; `status`, when set, is the status it answers with instead. `reads` counts the
    route's reads."""

    def __init__(self, page: Any, records: list[dict]) -> None:
        self.records = records
        self.status: int | None = None
        self.reads = 0
        page.add_init_script(clocks_topic(None))
        page.route("**/m/cache/api/clocks", self._clocks)

    def _clocks(self, route: Any) -> None:
        self.reads += 1
        if self.status is not None:
            route.fulfill(status=self.status, content_type="text/plain", body="down")
            return
        body = {"now": time.time(), **value_of(self.records)}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


def open_with(shell, ui_contexts, records: list[dict], launches: list[dict] | None = None):
    """A signed-in page on `shell` whose cache holds `records` and whose launcher runs
    `launches`: the page, the list and the cache stub."""
    page = ui_contexts(shell).pages[0]
    LauncherStub(page, launches or [])
    stub = CacheStub(page, records)
    return page, open_list(page), stub


def cache_reads(page) -> list[str]:
    """The address of each request the page makes under cache's page from now on."""
    reads: list[str] = []
    page.on("request", lambda request: reads.append(request.url) if "/m/cache/" in request.url else None)
    return reads


def test_a_session_started_by_hand_says_how_long_its_cache_stays_warm(full_shell, ui_contexts):
    _, view, stub = open_with(full_shell, ui_contexts, [
        record(sid(1)), record(sid(2), cold_in=-300), record(tid(1), harness="claude")])
    first, second, codex = row(view, "claude", sid(1)), row(view, "claude", sid(2)), row(view, "codex", tid(1))
    expect(first).to_have_attribute("fact", WARM)
    expect(second).to_have_attribute("fact", "cold")
    # A Codex session is not the Claude record of the same id: it says nothing.
    expect(codex).not_to_have_attribute("fact", re.compile(""))
    # The fact is dim, and no heavier than the row's own text.
    assert first.get_attribute("fact-heavy") is None
    assert stub.reads >= 1
    assert group(view, "recent").locator("moxi-ui-row").count() == 3


def test_the_opened_row_has_the_cache_line_and_links_caches_page(full_shell, ui_contexts):
    page, view, _ = open_with(full_shell, ui_contexts, [record(sid(1)), record(sid(2), cold_in=-300)])
    first = row(view, "claude", sid(1))
    first.get_by_role("button", name="Open").click()
    line = first.locator('[data-line="cache"]')
    expect(line).to_have_text(re.compile(rf"^Cache\s*warm until {T}\s*Cache$"))
    # The line stands after the Session line.
    expect(first.locator('[data-line="session"] ~ [data-line="cache"]')).to_have_count(1)
    second = row(view, "claude", sid(2))
    second.get_by_role("button", name="Open").click()
    expect(second.locator('[data-line="cache"]')).to_have_text(re.compile(rf"^Cache\s*cold since {T}\s*Cache$"))
    # A row with no record has no line.
    codex = row(view, "codex", tid(1))
    # It has one line, and opens by it.
    codex.get_by_role("button").first.click()
    expect(codex.locator('[data-line="session"]')).to_be_visible()
    expect(codex.locator('[data-line="cache"]')).to_have_count(0)
    # Its link shows cache's page.
    first.locator('[data-line="cache"]').get_by_role("link", name="Cache").click()
    expect(page).to_have_url(re.compile(r"/m/cache/$"))
    expect(page.locator("#moxi-panel > moxi-cache-page")).to_be_attached()


def test_a_row_turns_cold_at_its_time_with_no_read(full_shell, ui_contexts):
    _, view, stub = open_with(full_shell, ui_contexts, [record(sid(1), cold_in=4)])
    first = row(view, "claude", sid(1))
    expect(first).to_have_attribute("fact", WARM)
    reads = stub.reads
    expect(first).to_have_attribute("fact", "cold", timeout=15_000)
    assert stub.reads == reads


def test_on_a_running_launch_launchers_fact_wins(full_shell, ui_contexts):
    held = launch("r-1", sid(1), "builder-1")
    bare = launch("r-2", sid(2), "planner")
    bare["cache"] = None
    _, view, _ = open_with(full_shell, ui_contexts, [record(sid(1), cold_in=-300), record(sid(2), cold_in=600)],
                           [held, bare])
    expect(labels(view)).to_have_text(["2 running", "Active recently"])
    # Launcher says warm for 20 minutes; cache's cold record is not drawn.
    first = launched_row(view, "r-1")
    expect(first).to_have_attribute("fact", WARM)
    first.get_by_role("button", name="Open").click()
    expect(first.locator('[data-line="cache"]')).to_have_text(re.compile(rf"^Cache\s*warm until {T}\s*Cache$"))
    # Launcher gives the other no cache: cache's record speaks.
    second = launched_row(view, "r-2")
    expect(second).to_have_attribute("fact", WARM)
    assert first.get_attribute("fact") != second.get_attribute("fact")


def test_a_failed_read_draws_nothing(full_shell, ui_contexts):
    page = ui_contexts(full_shell).pages[0]
    LauncherStub(page, [])
    stub = CacheStub(page, [record(sid(1))])
    stub.status = 502
    with page.expect_response(lambda response: "/m/cache/api/clocks" in response.url):
        view = open_list(page)
    first = row(view, "claude", sid(1))
    first.get_by_role("button", name="Open").click()
    expect(first.locator('[data-line="session"]')).to_be_visible()
    expect(first).not_to_have_attribute("fact", re.compile(""))
    expect(first.locator('[data-line="cache"]')).to_have_count(0)
    assert stub.reads >= 1


def test_a_small_install_reads_nothing_of_cache_and_draws_none_of_it(small_shell, ui_contexts):  # noqa: F811
    page = ui_contexts(small_shell).pages[0]
    assert page.evaluate("async () => (await import('/m/ui/client.js')).installed('cache')") is False
    reads = cache_reads(page)
    view = open_list(page)
    first = row(view, "claude", sid(1))
    expect(first).to_be_visible()
    first.get_by_role("button", name="Open").click()
    expect(first.locator('[data-line="session"]')).to_be_visible()
    for one in view.locator("moxi-ui-row").all():
        expect(one).not_to_have_attribute("fact", re.compile(""))
    expect(view.locator('[data-line="cache"]')).to_have_count(0)
    expect(view.get_by_text("warm", exact=False)).to_have_count(0)
    # The opened row's lines run on with no gap where the Cache line would stand.
    following = first.locator('[data-line="session"] + *')
    assert following.evaluate("(element) => element.dataset.line ?? element.className") in ("resume", "foot")
    assert reads == []
