"""cache's clocks on sessions' page, in a browser, through a real shell that starts
sessions' real page server: a session started by hand ends its row's first line in `warm
until <time>` or `cold`, from cache's record for it, and turns cold at its time with no
read; opened, its `Cache` line says the same and links cache's page; a row in a day's
group says nothing of cache, closed or opened, whatever cache holds for it; on a running
launch launcher's cache fact wins, and cache's record speaks only where launcher gives
none; a failed read draws nothing; and a small install, with ui and sessions only, reads
nothing of cache and draws none of it.

cache's `clocks` route is answered in the browser (`CacheStub`), so each test sets the
records it needs, and the shell's push stream is kept from bringing the real cache's
topic `clocks`. The real cache publishes its topic only when its records change, which
they don't here, so the topic's path is proved by the page's unit tests. Launcher's routes are answered as `test_browser_running.py` does.
Every other route is the page servers' own.

Each time a test expects is worded by the page's own rule (`words_at`): a time of another
day than today carries its day, so the tests hold at any hour, near midnight too.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from moxi import modules
from playwright.sync_api import expect

from test_browser_list import claude, day_key, env_of, group, noon, open_list, row, sid, tid
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


def words_at(at: float) -> str:
    """A pattern for the moment `at` as a cache fact words it beside today: its time alone
    on today, else after its day (`yesterday 11:58 PM`, `Nov 27 12:04 AM`, with the year
    when it is not this one's)."""
    day, today = date.fromtimestamp(at), date.today()
    if day == today:
        return T
    if day == today - timedelta(days=1):
        return rf"yesterday {T}"
    words = f"{day.strftime('%b')} {day.day}"
    return rf"{re.escape(words if day.year == today.year else f'{words}, {day.year}')} {T}"


def cold_at(one: dict) -> float:
    """When a clock record goes cold."""
    return one["last_request"] + one["ttl"]


def warm_until(at: float) -> re.Pattern:
    """A row's fact for a session warm until `at`."""
    return re.compile(rf"^warm until {words_at(at)}$")

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
    warm_one = record(sid(1))
    _, view, stub = open_with(full_shell, ui_contexts, [
        warm_one, record(sid(2), cold_in=-300), record(tid(1), harness="claude")])
    first, second, codex = row(view, "claude", sid(1)), row(view, "claude", sid(2)), row(view, "codex", tid(1))
    expect(first).to_have_attribute("fact", warm_until(cold_at(warm_one)))
    expect(second).to_have_attribute("fact", "cold")
    # A Codex session is not the Claude record of the same id: it says nothing.
    expect(codex).not_to_have_attribute("fact", re.compile(""))
    # The fact is dim, and no heavier than the row's own text.
    assert first.get_attribute("fact-heavy") is None
    assert stub.reads >= 1
    assert group(view, "recent").locator("moxi-ui-row").count() == 3


def test_the_opened_row_has_the_cache_line_and_links_caches_page(full_shell, ui_contexts):
    warm_one, cold_one = record(sid(1)), record(sid(2), cold_in=-300)
    page, view, _ = open_with(full_shell, ui_contexts, [warm_one, cold_one])
    first = row(view, "claude", sid(1))
    first.get_by_role("button", name="Open").click()
    line = first.locator('[data-line="cache"]')
    expect(line).to_have_text(re.compile(rf"^Cache\s*warm until {words_at(cold_at(warm_one))}\s*Cache$"))
    # The line stands after the Session line.
    expect(first.locator('[data-line="session"] ~ [data-line="cache"]')).to_have_count(1)
    second = row(view, "claude", sid(2))
    second.get_by_role("button", name="Open").click()
    expect(second.locator('[data-line="cache"]')).to_have_text(
        re.compile(rf"^Cache\s*cold since {words_at(cold_at(cold_one))}\s*Cache$"))
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
    # The page's clock is Playwright's, so the record stays warm however slowly the page
    # opens, and goes cold when the clock is moved past its time.
    page = ui_contexts(full_shell).pages[0]
    page.clock.install()
    LauncherStub(page, [])
    warm_one = record(sid(1), cold_in=10 * 60)
    stub = CacheStub(page, [warm_one])
    view = open_list(page)
    first = row(view, "claude", sid(1))
    expect(first).to_have_attribute("fact", warm_until(cold_at(warm_one)))
    reads = stub.reads
    page.clock.fast_forward("10:30")
    expect(first).to_have_attribute("fact", "cold")
    assert stub.reads == reads


@pytest.fixture(scope="module")
def days_shell(ui_shells, tmp_path_factory):
    """A shell with one session of the last hour and two from two days back."""
    shell = ui_shells(**env_of(tmp_path_factory.mktemp("days")))
    root = Path(shell.env["CLAUDE_CONFIG_DIR"]) / "projects"
    claude(root, 1, time.time() - 600, typed="Make the build smaller.")
    claude(root, 10, noon(2).timestamp(), typed="Rotate the certificates.")
    claude(root, 11, noon(2).timestamp() + 60, typed="Trim the export.")
    return shell


def test_a_days_rows_say_nothing_of_cache(days_shell, ui_contexts):
    warm_one = record(sid(1))
    _, view, stub = open_with(days_shell, ui_contexts, [warm_one, record(sid(10)), record(sid(11), cold_in=-300)])
    expect(group(view, "recent").locator(f'moxi-ui-row[data-key="claude/{sid(1)}"]')).to_have_attribute(
        "fact", warm_until(cold_at(warm_one)))
    day = group(view, day_key(2))
    warm, cold = (day.locator(f'moxi-ui-row[data-key="claude/{sid(n)}"]') for n in (10, 11))
    for past in (warm, cold):
        expect(past).to_be_visible()
        expect(past).not_to_have_attribute("fact", re.compile(""))
    # Opened, it has no Cache line.
    warm.get_by_role("button", name="Open").click()
    expect(warm.locator('[data-line="session"]')).to_be_visible()
    expect(warm.locator('[data-line="cache"]')).to_have_count(0)
    assert stub.reads >= 1


def test_on_a_running_launch_launchers_fact_wins(full_shell, ui_contexts):
    held = launch("r-1", sid(1), "builder-1")
    bare = launch("r-2", sid(2), "planner")
    bare["cache"] = None
    warm_two = record(sid(2), cold_in=600)
    _, view, _ = open_with(full_shell, ui_contexts, [record(sid(1), cold_in=-300), warm_two], [held, bare])
    expect(labels(view)).to_have_text(["2 running", "Active recently"])
    # Launcher says warm for 20 minutes; cache's cold record is not drawn. A launch's own
    # fact gives the time alone, its Cache line the day too when it is not today.
    until = datetime.fromisoformat(held["cache"]["until"]).timestamp()
    first = launched_row(view, "r-1")
    expect(first).to_have_attribute("fact", re.compile(rf"^warm until {T}$"))
    first.get_by_role("button", name="Open").click()
    expect(first.locator('[data-line="cache"]')).to_have_text(
        re.compile(rf"^Cache\s*warm until {words_at(until)}\s*Cache$"))
    # Launcher gives the other no cache: cache's record speaks.
    second = launched_row(view, "r-2")
    expect(second).to_have_attribute("fact", warm_until(cold_at(warm_two)))
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
