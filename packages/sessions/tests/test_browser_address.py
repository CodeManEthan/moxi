"""The Sessions list's address, in a browser, through a real shell that starts sessions'
real page server: Back from a transcript, a reload and a shared link each bring the list
back with its filters, find and depth (Back and a reload with its opened rows and its
scroll too); typing and picking add no history entry; the plain address; an invalid and a
stale address; a transcript's links carry the list's query; a read that lands after he
moved on writes nothing; the band's `Sessions` and another page and Back; a restore that
meets a newer list, a query change over a read, and a target carried to a transcript; with
launcher installed, a reload's scroll that lands only once launcher's running launches
have answered.

Each test runs twice: on a small install, the core with ui and sessions alone, and on
the full one. Both are over the same roots: a hundred sessions active recently in one
folder, and in `atlas` a day two days back with 45 sessions started in a terminal (more
than a day's first eight and one `Show` of thirty) and three started by a program, two
older sessions, and a Codex session four days back.
"""

from __future__ import annotations

import json
import re
import sys
from contextlib import suppress
import time
from pathlib import Path

import pytest
from moxi import modules
from playwright.sync_api import Error, expect

from moxi_sessions.testing import CodexRollout
from test_browser_list import changed_at, claude, count_line, day_key, env_of, filter_button, group, noon, row, sid, tid
from test_browser_running import running_topic

pytestmark = [pytest.mark.browser, pytest.mark.timeout(240),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="sessions' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

LIST = "#moxi-panel > moxi-sessions-page moxi-sessions-list"
PAGE = "#moxi-panel > moxi-sessions-page"
#: The modules a small install holds; the full one leaves out none.
SMALL = ("sessions", "ui")
#: The sessions active recently, and the atlas day's sessions started in a terminal.
RECENT = 100
DAY = 45
ALL = RECENT + DAY + 3 + 2 + 1
#: The atlas day, and its newest session and one 35 rows down, which has a subagent.
D2 = day_key(2)
TOP = sid(300 + DAY - 1)
DEEP = sid(310)
AGENT = "a0190b0000000001"
NARROWED = "?folder=/work/atlas&harness=claude&when=7d"
MORE_API = re.compile(r"/m/sessions/api/more\?")
LIST_API = re.compile(r"/m/sessions/api/list")
RUNNING_API = "**/m/launcher/api/running"
#: Launcher's answer when nothing runs.
NOTHING_RUNS = json.dumps({"state": "up", "detail": None, "count": 0, "sessions": []})
#: The entry's memory, as the list keeps it in `history.state`.
MEMORY = "() => (history.state || {})['moxi.sessions.list'] || null"
#: Every address the document was rewritten to, kept from the document's start.
WATCH = """(() => {
  const written = [];
  window.writtenAddresses = written;
  const replace = history.replaceState.bind(history);
  history.replaceState = (state, unused, url) => {
    replace(state, unused, url);
    written.push(location.pathname + location.search);
  };
})();"""


def seed(env: dict[str, str]) -> None:
    root = Path(env["CLAUDE_CONFIG_DIR"]) / "projects"
    now = time.time()
    for k in range(RECENT):
        claude(root, 100 + k, now - 600 - 10 * k, work_dir="harbor")
    for k in range(DAY):
        claude(root, 300 + k, noon(2).timestamp() + 60 * k, typed=f"Task {k}.", work_dir="atlas",
               subagents=1 if sid(300 + k) == DEEP else 0)
    for k in range(3):
        claude(root, 400 + k, noon(2).timestamp() - 60 * (k + 1), program=True, work_dir="atlas")
    for k, days in enumerate((20, 30)):
        claude(root, 500 + k, noon(days).timestamp(), work_dir="atlas")
    rollout = CodexRollout(Path(env["CODEX_HOME"]) / "sessions", thread_id=tid(1), cwd="/work/atlas",
                           start=noon(4).timestamp() - 600)
    rollout.turn()
    rollout.user("Do the thing.")
    rollout.assistant("Done.")
    changed_at(rollout.write(), noon(4).timestamp())


@pytest.fixture(scope="module", params=["small", "full"])
def shell(request, ui_shells, tmp_path_factory):
    """A shell over the seeded roots: the small install, then the full one."""
    leave_out = [found.name for found in modules.scan() if found.name not in SMALL] if request.param == "small" else []
    made = ui_shells(leave_out=leave_out, **env_of(tmp_path_factory.mktemp(request.param)))
    seed(made.env)
    return made


@pytest.fixture
def fresh(ui_contexts, shell):
    """`fresh()` is the first page of a new context on the shell, every address the
    document is rewritten to kept in `window.writtenAddresses`."""
    def page():
        context = ui_contexts(shell)
        context.add_init_script(WATCH)
        return context.pages[0]
    return page


class Held:
    """Holds the `nth` read of sessions' `/api/more` on `page` from here on, until
    `release`; the others pass."""

    def __init__(self, page, nth: int = 1):
        self.page = page
        self.nth = nth
        self.seen = 0
        self.route = None
        page.route(MORE_API, self._take)

    def _take(self, route) -> None:
        self.seen += 1
        if self.seen == self.nth:
            self.route = route
        else:
            route.continue_()

    def wait(self) -> None:
        """Until the held read has been asked."""
        for _ in range(400):
            if self.route is not None:
                return
            self.page.evaluate("new Promise((done) => setTimeout(done, 25))")
        raise AssertionError(f"read {self.nth} of /api/more was never asked")

    def release(self) -> None:
        """Lets the held read through and waits until its answer has reached the page."""
        self.wait()
        with self.page.expect_request_finished(lambda request: MORE_API.search(request.url) is not None):
            self.route.continue_()
        self.page.unroute(MORE_API)


class HeldRunning:
    """Answers launcher's `/api/running` on `page` with nothing running, and keeps the
    shell's push stream from bringing launcher's topic `running`; from `hold()` on, each
    read waits unanswered until `release()`."""

    def __init__(self, page):
        self.page = page
        self.held: list | None = None
        page.add_init_script(running_topic(None))
        page.route(RUNNING_API, self._take)

    def _take(self, route) -> None:
        if self.held is not None:
            self.held.append(route)
        else:
            route.fulfill(status=200, content_type="application/json", body=NOTHING_RUNS)

    def hold(self) -> None:
        self.held = []

    def wait(self) -> None:
        """Until a held read has been asked."""
        for _ in range(400):
            if self.held:
                return
            self.page.evaluate("new Promise((done) => setTimeout(done, 25))")
        raise AssertionError("launcher's /api/running was never asked")

    def release(self) -> None:
        """Answers each held read, and every later one at once."""
        held, self.held = self.held or [], None
        for route in held:
            # A read the reload's old document asked is gone with it.
            with suppress(Error):
                route.fulfill(status=200, content_type="application/json", body=NOTHING_RUNS)


def open_list(page, search: str = ""):
    page.goto(f"/m/sessions/{search}")
    return page.locator(LIST)


def at(page, search: str) -> None:
    """The document is at the list's own address with `search`."""
    expect(page).to_have_url(re.compile(re.escape(f"/m/sessions/{search}") + "$"))


def pick(view, key: str, name: str) -> None:
    filter_button(view, key).click()
    view.locator(f'[data-menu="{key}"]').get_by_role("menuitemradio", name=name).click()
    expect(view.locator(f'[data-menu="{key}"]')).to_have_count(0)


def narrow(view) -> None:
    pick(view, "harness", "Claude Code")
    pick(view, "when", "Last 7 days")
    pick(view, "folder", "atlas")
    expect(count_line(view)).to_have_text(f"{DAY + 3} of {ALL} sessions")


def rows(view, key: str):
    return group(view, key).locator("moxi-ui-row")


def show(view, key: str, line: str = "more") -> None:
    group(view, key).locator(f'[data-line="{line}"]').get_by_role("button", name="Show").click()


def open_row(view, session: str):
    one = row(view, "claude", session)
    one.locator(".l1").click()
    expect(one).to_have_attribute("open", "")
    return one


def narrowed(view) -> None:
    """The filters set as `narrow` sets them."""
    expect(filter_button(view, "harness")).to_have_text(re.compile("^Claude Code"))
    expect(filter_button(view, "when")).to_have_text(re.compile("^last 7 days"))
    expect(filter_button(view, "folder")).to_have_text(re.compile("^atlas"))


def scrolled_to(page, y: float, within: float) -> None:
    page.wait_for_function(f"() => Math.abs(scrollY - {y}) <= {within}")


def row_height(view) -> float:
    return row(view, "claude", sid(300)).evaluate("el => el.getBoundingClientRect().height")


def the_full_list(page):
    """Test 1's list: three filters, the atlas day shown to its end by two `Show`s, its
    program line open, two rows open, and the document scrolled to the deeper row's
    transcript link. Returns the view, the address and the scroll the memory holds."""
    view = open_list(page)
    narrow(view)
    at(page, NARROWED)
    expect(rows(view, D2)).to_have_count(8)
    show(view, D2)
    expect(rows(view, D2)).to_have_count(38)
    show(view, D2)
    expect(rows(view, D2)).to_have_count(DAY)
    show(view, D2, "program")
    expect(rows(view, D2)).to_have_count(DAY + 3)
    search = f"{NARROWED}&more={D2}:2&program={D2}:1"
    at(page, search)
    open_row(view, TOP)
    open_row(view, DEEP)
    row(view, "claude", DEEP).locator('[data-action="transcript"]').scroll_into_view_if_needed()
    page.wait_for_function(f"() => {{ const m = ({MEMORY})(); return m !== null && m.opened.length === 2 "
                           "&& m.y > 0 && m.y === scrollY; }")
    return view, search, page.evaluate(f"({MEMORY})().y")


def restored(page, view, search: str, y: float | None) -> None:
    at(page, search)
    narrowed(view)
    expect(rows(view, D2)).to_have_count(DAY + 3)
    expect(group(view, D2).locator('[data-line="program"] button')).to_have_text("Hide")
    if y is None:
        expect(row(view, "claude", TOP)).not_to_have_attribute("open", "")
        expect(row(view, "claude", DEEP)).not_to_have_attribute("open", "")
        assert page.evaluate("scrollY") == 0
    else:
        expect(row(view, "claude", TOP)).to_have_attribute("open", "")
        expect(row(view, "claude", DEEP)).to_have_attribute("open", "")
        scrolled_to(page, y, row_height(view))


# -- 1, 2, 3: Back, a reload and a shared link ---------------------------------------------

def test_back_from_a_transcript_and_a_reload_bring_the_list_back_as_it_was(fresh):
    page = fresh()
    view, search, y = the_full_list(page)
    row(view, "claude", DEEP).locator('[data-action="transcript"]').click()
    at(page, f"claude/{DEEP}{search}")
    expect(page.locator(PAGE).locator('[data-part="window"]')).to_be_visible()
    page.go_back()
    restored(page, view, search, y)
    page.reload()
    restored(page, view, search, y)


def test_a_shared_link_gives_the_filters_find_and_depth_with_no_row_open_at_the_top(fresh):
    page = fresh()
    _, search, _ = the_full_list(page)
    other = fresh()
    view = open_list(other, search)
    restored(other, view, search, None)


def test_back_brings_back_the_find_text_and_the_found_groups_depth(fresh):
    page = fresh()
    view = open_list(page)
    field = view.get_by_label("Find a session")
    field.fill("atlas")
    expect(rows(view, "found")).to_have_count(30)
    show(view, "found")
    expect(rows(view, "found")).to_have_count(DAY + 3 + 2 + 1)
    at(page, "?find=atlas&more=found:1")
    open_row(view, TOP).locator('[data-action="transcript"]').click()
    at(page, f"claude/{TOP}?find=atlas&more=found:1")
    page.go_back()
    at(page, "?find=atlas&more=found:1")
    expect(field).to_have_value("atlas")
    expect(rows(view, "found")).to_have_count(DAY + 3 + 2 + 1)
    expect(row(view, "claude", TOP)).to_have_attribute("open", "")


# -- 4, 5: history and the plain address --------------------------------------------------

def test_typing_and_picking_add_no_history_entry_and_one_back_leaves_the_list(fresh):
    page = fresh()
    view = open_list(page)
    expect(count_line(view)).to_have_text(f"{ALL} sessions")
    length = page.evaluate("history.length")
    # Eight characters, the start of every Claude Code session's id.
    view.get_by_label("Find a session").press_sequentially("0190b000")
    expect(count_line(view)).to_have_text(f"{ALL - 1} of {ALL} sessions")
    pick(view, "harness", "Claude Code")
    pick(view, "when", "Last 7 days")
    at(page, "?harness=claude&when=7d&find=0190b000")
    assert page.evaluate("history.length") == length
    page.go_back()
    expect(page).to_have_url(re.compile(r"/m/ui/$"))


def test_the_plain_address_has_no_query_and_clear_and_an_empty_find_give_it_back(fresh):
    page = fresh()
    view = open_list(page)
    expect(count_line(view)).to_have_text(f"{ALL} sessions")
    at(page, "")
    pick(view, "harness", "Claude Code")
    view.get_by_label("Find a session").fill("atlas")
    at(page, "?harness=claude&find=atlas")
    view.locator('[data-action="clear"]').click()
    at(page, "?find=atlas")
    view.locator('[data-action="clear-find"]').click()
    at(page, "")
    assert "?" not in page.url


# -- 6, 7: an invalid and a stale address -------------------------------------------------

def test_an_invalid_address_is_named_asked_without_it_and_rewritten(fresh):
    page = fresh()
    asked = []
    page.on("request", lambda request: asked.append(request.url) if LIST_API.search(request.url) else None)
    view = open_list(page, "?harness=foo&when=soon&more=recent:x,recent:1&utm=1")
    expect(view.locator('[data-line="address"]')).to_have_text(
        "This address held harness=foo, when=soon, more=recent:x and utm=1, which the list does not take; "
        "the list is shown without them.")
    expect(rows(view, "recent")).to_have_count(60)
    at(page, "?more=recent:1")
    assert asked
    for url in asked:
        assert not re.search(r"harness|since|until|utm|soon", url.split("?", 1)[-1]), url


def test_a_stale_address_narrows_to_nothing_or_reads_as_far_as_the_list_goes(fresh):
    page = fresh()
    view = open_list(page, "?folder=/no/such")
    expect(count_line(view)).to_have_text(f"0 of {ALL} sessions")
    expect(filter_button(view, "folder")).to_have_class("set")
    expect(view.locator('[data-action="clear"]')).to_be_visible()
    expect(view.locator('[data-line="address"]')).to_have_count(0)

    view = open_list(page, "?more=recent:99")
    expect(rows(view, "recent")).to_have_count(RECENT)
    at(page, "?more=recent:3")

    view = open_list(page, f"?more={day_key(60)}:2")
    expect(count_line(view)).to_have_text(f"{ALL} sessions")
    at(page, "")
    expect(view.locator('[data-line="address"]')).to_have_count(0)


# -- 8: the transcript's links -------------------------------------------------------------

def test_a_transcripts_links_carry_the_lists_query_through_its_moves(fresh):
    page = fresh()
    view = open_list(page)
    narrow(view)
    show(view, D2)
    expect(rows(view, D2)).to_have_count(38)
    search = f"{NARROWED}&more={D2}:1"
    at(page, search)
    link = open_row(view, DEEP).locator('[data-action="transcript"]')
    expect(link).to_have_attribute("href", f"/m/sessions/claude/{DEEP}{search}")
    href = link.get_attribute("href")
    link.click()
    head = page.locator(PAGE).locator('[data-action="back"]')
    expect(head).to_have_attribute("href", f"/m/sessions/{search}")
    page.locator(PAGE).locator('[data-action="subagent"]').first.click()
    at(page, f"claude/{DEEP}/{AGENT}{search}")
    page.locator(PAGE).get_by_role("button", name="Open the parent session").click()
    at(page, f"claude/{DEEP}{search}")
    head.click()
    at(page, search)
    narrowed(view)
    expect(rows(view, D2)).to_have_count(38)

    # Each link's href, opened in a page of its own, gives the same.
    other = fresh()
    other.goto(href)
    back = other.locator(PAGE).locator('[data-action="back"]')
    expect(back).to_have_attribute("href", f"/m/sessions/{search}")
    other.goto(back.get_attribute("href"))
    view = other.locator(LIST)
    narrowed(view)
    expect(rows(view, D2)).to_have_count(38)


# -- 9: the write guard --------------------------------------------------------------------

def test_a_read_landing_after_a_transcript_opened_keeps_the_transcripts_address(fresh):
    page = fresh()
    view = open_list(page)
    expect(rows(view, "recent")).to_have_count(30)
    link = open_row(view, sid(100)).locator('[data-action="transcript"]')
    held = Held(page)
    show(view, "recent")
    held.wait()
    link.click()
    at(page, f"claude/{sid(100)}")
    written = page.evaluate("window.writtenAddresses.length")
    held.release()
    page.evaluate("new Promise(requestAnimationFrame)")
    at(page, f"claude/{sid(100)}")
    assert page.evaluate("window.writtenAddresses.length") == written


def test_a_read_landing_after_the_bands_sessions_writes_neither_address_nor_memory(fresh):
    page = fresh()
    view = open_list(page)
    expect(rows(view, "recent")).to_have_count(30)
    held = Held(page)
    show(view, "recent")
    held.wait()
    page.locator("#moxi-pages-button").click()
    page.locator("#moxi-pages a[data-module=sessions]").click()
    view = page.locator(LIST)
    expect(rows(view, "recent")).to_have_count(30)
    before = page.evaluate("[location.href, JSON.stringify(history.state), window.writtenAddresses.length]")
    held.release()
    page.evaluate("new Promise(requestAnimationFrame)")
    assert page.evaluate("[location.href, JSON.stringify(history.state), window.writtenAddresses.length]") == before
    expect(rows(view, "recent")).to_have_count(30)


# -- 10: the shell and the other pages -----------------------------------------------------

def band(page, module: str) -> None:
    """Picks `module`'s page from the band. The top line scrolls with the document, so a
    pointer would first bring the document to its top; the clicks are sent where the
    button and the link are, so the scroll the list had is the one to bring back."""
    page.locator("#moxi-pages-button").dispatch_event("click")
    page.locator(f'#moxi-pages a[data-module={module}][data-route=""]').dispatch_event("click")


def test_another_page_and_back_and_the_bands_sessions_give_the_list_back(fresh):
    page = fresh()
    view, search, y = the_full_list(page)
    other = "usage" if page.locator("#moxi-pages a[data-module=usage]").count() else "ui"
    band(page, other)
    expect(page).to_have_url(re.compile(rf"/m/{other}/$"))
    page.go_back()
    restored(page, view, search, y)

    band(page, "sessions")
    restored(page, view, search, y)

    page.goto(f"/m/sessions/claude/{TOP}")
    expect(page.locator(PAGE).locator('[data-action="back"]')).to_have_attribute("href", "/m/sessions/")


# -- 11, 12, 13: a restore over a newer list, a query change, a carried target --------------

def test_a_restore_reads_on_over_a_newer_list_and_holds_its_address(fresh):
    page = fresh()
    held = Held(page, nth=2)
    view = open_list(page, "?more=recent:3")
    held.wait()
    expect(rows(view, "recent")).to_have_count(60)
    with page.expect_response(LIST_API):
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    at(page, "?more=recent:3")
    held.release()
    expect(rows(view, "recent")).to_have_count(RECENT)
    at(page, "?more=recent:3")
    assert set(page.evaluate("window.writtenAddresses")) <= {"/m/sessions/?more=recent:3"}


def test_a_query_change_over_a_read_writes_the_new_query_with_no_depth(fresh):
    page = fresh()
    view = open_list(page, "?more=recent:1")
    expect(rows(view, "recent")).to_have_count(60)
    at(page, "?more=recent:1")
    held = Held(page)
    show(view, "recent")
    held.wait()
    pick(view, "harness", "Claude Code")
    at(page, "?harness=claude")
    held.release()
    expect(count_line(view)).to_have_text(f"{ALL - 1} of {ALL} sessions")
    expect(rows(view, "recent")).to_have_count(30)
    at(page, "?harness=claude")
    # From the query change on, every write, of the address or of the entry's memory, is at
    # the new query with no depth.
    written = page.evaluate("window.writtenAddresses")
    after = written[written.index("/m/sessions/?harness=claude"):]
    assert set(after) == {"/m/sessions/?harness=claude"}, written
    page.reload()
    view = page.locator(LIST)
    expect(rows(view, "recent")).to_have_count(30)
    at(page, "?harness=claude")


def test_a_transcript_opened_mid_restore_carries_the_target_and_back_reaches_it(fresh):
    page = fresh()
    held = Held(page, nth=2)
    view = open_list(page, "?more=recent:3")
    held.wait()
    expect(rows(view, "recent")).to_have_count(60)
    open_row(view, sid(100)).locator('[data-action="transcript"]').click()
    at(page, f"claude/{sid(100)}?more=recent:3")
    expect(page.locator(PAGE).locator('[data-action="back"]')).to_have_attribute("href", "/m/sessions/?more=recent:3")
    held.release()
    page.go_back()
    view = page.locator(LIST)
    expect(rows(view, "recent")).to_have_count(RECENT)
    at(page, "?more=recent:3")


# -- 14: the landing waits for launcher's running launches --------------------------------

def frames(page, count: int) -> None:
    """Waits `count` animation frames in the page."""
    page.evaluate("(count) => new Promise((done) => { const next = () => --count > 0 "
                  "? requestAnimationFrame(next) : done(); requestAnimationFrame(next); })", count)


def test_with_launcher_a_reloads_scroll_lands_only_once_launchers_running_launches_answer(fresh, shell):
    if "launcher" in shell.leave_out or "launcher" not in {found.name for found in modules.scan()}:
        pytest.skip("the scroll waits for launcher's running launches only with launcher installed")
    page = fresh()
    running = HeldRunning(page)
    view, search, y = the_full_list(page)
    height = row_height(view)
    assert y > 2 * height
    running.hold()
    page.reload()
    view = page.locator(LIST)
    at(page, search)
    expect(rows(view, D2)).to_have_count(DAY + 3)
    # Each opened row's read has answered; only launcher's answer is still out.
    for session in (TOP, DEEP):
        one = row(view, "claude", session)
        expect(one).to_have_attribute("open", "")
        expect(one.locator(".r").first).to_be_visible()
        expect(one.get_by_role("status")).to_have_count(0)
    running.wait()
    frames(page, 10)
    assert abs(page.evaluate("scrollY") - y) > height, "the scroll landed before launcher's first answer"
    running.release()
    scrolled_to(page, y, height)
