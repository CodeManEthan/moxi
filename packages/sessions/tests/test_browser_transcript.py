"""The header over one session's turns on sessions' page, in a browser, through a real
shell that starts sessions' real page server: the way back to the list and the session's
name as the list names it, for a named and an unnamed session; the harness, the model and
the span under it; `Details`, which shows and hides the header's other facts, the folder
worded against the home folder, as the list words the resume line's and the Folder
menu's; "Earlier" and `Refresh` under the header; a subagent's
route and a spawned thread's, which keep the view's own heading and facts; the line shown
while the turns are read, for a session that is not there and for a read that fails; the
name drawn with the turns on a page server's first open, asking for no list; no line for
a harness whose sessions were not all read; and the 360 px width check.

Three shells, each over roots of its own written with `moxi_sessions.testing`: one whose
sessions can all be read, one with a Codex root that cannot be listed and a part of
Claude Code's that cannot, and one whose page server has read nothing before the test
opens a transcript. Every session is timed from the moment the test runs.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect

from moxi_sessions.testing import ClaudeTranscript, CodexRollout
from test_browser_list import changed_at, count_line, env_of, sid, tid

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="sessions' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

PANEL = "#moxi-panel > moxi-sessions-page"
AGENT = "a0190b0000000001"
TURNS = 7
WINDOW_API = re.compile(r"/api/window\?")
LIST_API = re.compile(r"/api/list\?")
SESSION_API = re.compile(r"/api/session\?")
#: The second line of the header: the harness's id, the model, and from when to when.
FACTS = re.compile(r"^claude · claude-model-x · today \d.* to \d.*$")


def named(root: Path, home: str, at: float, *, turns: int = TURNS) -> Path:
    """A session with a typed name, run in a folder under the home folder, with seven
    turns and a subagent."""
    t = ClaudeTranscript(root, session_id=sid(1), cwd=f"{home}/code/proj-a", project="-home-code-proj-a",
                         start=at - 600)
    t.title("fix-tooltips")
    t.title("a title the harness made", ai=True)
    for n in range(turns):
        t.user(f"turn {n}", origin="human")
        t.assistant(f"done {n}")
        t.turn_duration()
    sub = t.subagent(AGENT, agent_type="Explore", description="Read the deploy logs")
    sub.user("look")
    sub.assistant("looked")
    path = t.write()
    changed_at(path, at)
    return path


def seed(env: dict[str, str]) -> None:
    """A named session under the home folder, an unnamed one outside it, and a Codex
    thread that spawned another."""
    root = Path(env["CLAUDE_CONFIG_DIR"]) / "projects"
    now = time.time()
    named(root, env["HOME"], now - 300)

    t = ClaudeTranscript(root, session_id=sid(2), cwd="/work/proj-b", project="-work-proj-b", start=now - 1500)
    t.title("a title the harness made", ai=True)
    t.user("Run it.", origin="human")
    t.assistant("Done.")
    changed_at(t.write(), now - 900)

    rollout = CodexRollout(Path(env["CODEX_HOME"]) / "sessions", thread_id=tid(1), cwd="/work/proj-b",
                           start=now - 4000)
    rollout.turn()
    rollout.user("Do the thing.")
    child = rollout.spawn(tid(2), "survey")
    child.turn()
    child.user("Survey it.")
    child.assistant("Surveyed.")
    changed_at(child.write(), now - 3700)
    rollout.assistant("Done.")
    changed_at(rollout.write(), now - 3600)


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory) -> dict:
    return env_of(tmp_path_factory.mktemp("harnesses"))


@pytest.fixture(scope="module")
def seeded(ui_shell) -> None:
    seed(ui_shell.env)


@pytest.fixture(scope="module")
def broken_shell(ui_shells, tmp_path_factory):
    """A shell with one Claude Code session beside a folder of Claude Code's that cannot
    be listed, and a Codex root that cannot be listed at all."""
    shell = ui_shells(**env_of(tmp_path_factory.mktemp("broken")))
    root = Path(shell.env["CLAUDE_CONFIG_DIR"]) / "projects"
    named(root, shell.env["HOME"], time.time() - 300)
    unlisted = root / "-work-proj-c"
    unlisted.mkdir()
    unlisted.chmod(0)
    codex = Path(shell.env["CODEX_HOME"])
    codex.mkdir(parents=True)
    (codex / "sessions").write_text("not a folder")
    try:
        yield shell
    finally:
        unlisted.chmod(0o700)


def open_route(page, route: str):
    page.goto(f"/m/sessions/{route}")
    return page.locator(PANEL)


def head(panel):
    return panel.locator('[data-part="head"]')


def window(panel):
    return panel.locator('section[data-part="window"]')


def texts(panel, role: str = "user"):
    return panel.locator(f'li.msg[data-role="{role}"][data-reasoning="false"] [data-line="text"]')


def test_a_named_session_has_the_way_back_its_name_and_its_facts_on_two_lines(seeded, ui_page):
    panel = open_route(ui_page, f"claude/{sid(1)}")
    expect(window(panel)).to_have_attribute("data-state", "up")
    top = head(panel)
    expect(top.get_by_role("link", name="Sessions")).to_have_attribute("href", "/m/sessions/")
    expect(top.locator('[data-line="name"]')).to_have_text("fix-tooltips")
    expect(top.locator('[data-line="where"]')).to_have_text("proj-a")
    expect(top.locator('[data-line="facts"]')).to_have_text(FACTS)
    expect(top.get_by_role("button", name="Details")).to_have_attribute("aria-expanded", "false")
    expect(top.get_by_role("button", name="Refresh")).to_be_visible()
    # Neither the id nor a title the harness made names the session, and the page has no
    # heading over the turns: its facts wait behind Details.
    expect(top).not_to_contain_text(sid(1))
    expect(top).not_to_contain_text("a title the harness made")
    expect(panel.locator("h1")).to_have_count(0)
    expect(window(panel).locator("h2")).to_have_count(0)
    expect(panel.locator('[data-part="header"]')).to_have_count(0)
    expect(panel.locator('[data-action="another"]')).to_have_count(0)
    expect(texts(panel)).to_have_text([f"turn {n}" for n in range(TURNS - 5, TURNS)])


def test_an_unnamed_session_is_named_by_its_folder_and_when_it_started(seeded, ui_page):
    panel = open_route(ui_page, f"claude/{sid(2)}")
    top = head(panel)
    expect(top.locator('[data-line="name"]')).to_have_text(re.compile(r"^proj-b · started today \d"))
    expect(top.locator('[data-line="where"]')).to_have_count(0)
    expect(top.locator('[data-line="facts"]')).to_have_text(FACTS)
    expect(top).not_to_contain_text(sid(2))
    expect(top).not_to_contain_text("a title the harness made")


def test_details_shows_and_hides_what_the_header_leaves_out(seeded, ui_page, ui_shell):
    panel = open_route(ui_page, f"claude/{sid(1)}")
    details = head(panel).get_by_role("button", name="Details")
    details.click()
    expect(details).to_have_attribute("aria-expanded", "true")
    facts = panel.locator('[data-part="header"]')
    expect(facts.locator('[data-field="harness"]')).to_have_text("Claude Code")
    expect(facts.locator('[data-field="session"]')).to_have_text(sid(1))
    expect(facts.locator('[data-field="cwd"]')).to_have_text("~/code/proj-a")
    expect(facts.locator('[data-field="last_activity"]')).not_to_be_empty()
    expect(facts.locator('[data-field="version"]')).to_have_text("2.1.314")
    expect(facts.locator('[data-field="entrypoint"]')).to_have_text("cli")
    expect(facts.locator('[data-field="path"]')).to_contain_text(f"{sid(1)}.jsonl")
    expect(texts(panel)).to_have_count(5)
    details.click()
    expect(details).to_have_attribute("aria-expanded", "false")
    expect(facts).to_have_count(0)
    # A folder that is not under the home folder is shown as it is.
    other = open_route(ui_page, f"claude/{sid(2)}")
    head(other).get_by_role("button", name="Details").click()
    expect(other.locator('[data-field="cwd"]')).to_have_text("/work/proj-b")


def test_earlier_pages_back_under_the_header(seeded, ui_page):
    panel = open_route(ui_page, f"claude/{sid(1)}")
    expect(texts(panel)).to_have_text([f"turn {n}" for n in range(TURNS - 5, TURNS)])
    panel.get_by_role("button", name="Earlier turns").click()
    expect(texts(panel)).to_have_text([f"turn {n}" for n in range(TURNS)])
    expect(panel.locator('[data-line="start"]')).to_have_text("This is the start of the session.")
    expect(head(panel).locator('[data-line="name"]')).to_have_text("fix-tooltips")


def test_refresh_reads_the_turns_and_the_header_again(seeded, ui_page, ui_shell):
    panel = open_route(ui_page, f"claude/{sid(1)}")
    expect(texts(panel).last).to_have_text(f"turn {TURNS - 1}")
    expect(head(panel).locator('[data-line="name"]')).to_have_text("fix-tooltips")
    root = Path(ui_shell.env["CLAUDE_CONFIG_DIR"]) / "projects"
    was = time.time() - 300
    asked: list[str] = []
    ui_page.on("request", lambda request: asked.append(request.url)
               if WINDOW_API.search(request.url) or SESSION_API.search(request.url) else None)
    try:
        named(root, ui_shell.env["HOME"], time.time() - 5, turns=TURNS + 1)
        head(panel).get_by_role("button", name="Refresh").click()
        expect(texts(panel).last).to_have_text(f"turn {TURNS}")
        expect(head(panel).locator('[data-line="name"]')).to_have_text("fix-tooltips")
        assert any(WINDOW_API.search(url) for url in asked)
        assert any(SESSION_API.search(url) and f"session={sid(1)}" in url for url in asked)
    finally:
        named(root, ui_shell.env["HOME"], was)
        time.sleep(2.1)


def test_the_way_back_goes_to_the_list(seeded, ui_page):
    panel = open_route(ui_page, f"claude/{sid(1)}")
    head(panel).get_by_role("link", name="Sessions").click()
    expect(ui_page).to_have_url(re.compile(r"/m/sessions/$"))
    expect(count_line(panel.locator("moxi-sessions-list"))).to_have_text("3 sessions")
    expect(panel.locator('[data-part="head"]')).to_have_count(0)


def test_a_folder_under_the_home_folder_is_written_with_a_tilde_in_the_list(seeded, ui_page):
    view = open_route(ui_page, "").locator("moxi-sessions-list")
    one = view.locator(f'moxi-ui-row[data-key="claude/{sid(1)}"]')
    one.get_by_role("button", name="Open").click()
    resume = one.locator('[data-line="resume"]')
    expect(resume.locator('[data-line="where"]')).to_have_text("run it in ~/code/proj-a")
    expect(resume.locator("code")).to_have_text(f"claude --resume {sid(1)}")
    view.get_by_role("button", name="Folder: all").click()
    menu = view.locator('[data-menu="folder"]')
    expect(menu.get_by_role("menuitemradio", name="proj-a").locator(".d")).to_have_text("~/code")
    expect(menu.get_by_role("menuitemradio", name="proj-b").locator(".d")).to_have_text("/work")


def test_a_subagent_keeps_the_views_own_heading_and_its_facts_in_the_open(seeded, ui_page):
    panel = open_route(ui_page, f"claude/{sid(1)}")
    panel.locator(f'li[data-agent="{AGENT}"]').get_by_role("button", name="Explore").click()
    expect(ui_page).to_have_url(re.compile(rf"/m/sessions/claude/{sid(1)}/{AGENT}$"))
    top = head(panel)
    expect(top.get_by_role("link", name="Sessions")).to_be_visible()
    expect(top.locator('[data-line="heading"]')).to_have_text(f"Subagent {AGENT} of Claude Code session {sid(1)}")
    expect(top.locator('[data-line="name"]')).to_have_count(0)
    expect(top.locator('[data-line="facts"]')).to_have_count(0)
    expect(top.get_by_role("button", name="Details")).to_have_count(0)
    expect(top.get_by_role("button", name="Refresh")).to_be_visible()
    expect(panel.locator("h1")).to_have_count(0)
    expect(window(panel).locator('[data-field="agent"]')).to_have_text(AGENT)
    expect(texts(panel)).to_have_text(["look"])
    panel.get_by_role("button", name="Open the parent session").click()
    expect(ui_page).to_have_url(re.compile(rf"/m/sessions/claude/{sid(1)}$"))
    expect(head(panel).locator('[data-line="name"]')).to_have_text("fix-tooltips")


def test_a_thread_another_one_started_keeps_the_views_own_heading(seeded, ui_page):
    panel = open_route(ui_page, f"codex/{tid(2)}")
    expect(texts(panel)).to_have_text(["Survey it."])
    top = head(panel)
    expect(top.locator('[data-line="heading"]')).to_have_text(f"Codex session {tid(2)}")
    expect(top.get_by_role("button", name="Details")).to_have_count(0)
    expect(window(panel).locator('[data-field="parent"]')).to_have_text(tid(1))


def test_a_session_that_is_not_there_says_why(seeded, ui_page):
    panel = open_route(ui_page, "claude/nosuch")
    view = window(panel)
    expect(view).to_have_attribute("data-state", "refused")
    expect(view.get_by_role("status")).to_have_text(
        re.compile(r"^Moxi couldn't read the session: no claude session nosuch was found"))
    top = head(panel)
    expect(top.get_by_role("link", name="Sessions")).to_be_visible()
    expect(top.locator('[data-line="heading"]')).to_have_text("Claude Code session nosuch")
    expect(top.locator('[data-line="unread"]')).to_have_count(0)
    expect(panel.locator('[data-part="records"]')).to_have_count(0)


def test_the_turns_say_they_are_being_read_and_why_a_read_failed(seeded, ui_page):
    held = []
    ui_page.route(WINDOW_API, lambda route: held.append(route))
    panel = open_route(ui_page, f"claude/{sid(1)}")
    view = window(panel)
    expect(view.get_by_role("status")).to_have_text("Reading the session…")
    expect(view).to_have_attribute("data-state", "loading")
    # The header does not wait for the turns.
    expect(head(panel).locator('[data-line="name"]')).to_have_text("fix-tooltips")
    assert len(held) == 1
    held[0].fulfill(status=500, content_type="application/json", body="{}")
    expect(view.get_by_role("status")).to_have_text("Moxi couldn't read the session: sessions' page server answered 500")
    expect(view).to_have_attribute("data-state", "failed")
    expect(head(panel).locator('[data-line="name"]')).to_have_text("fix-tooltips")
    ui_page.unroute(WINDOW_API)
    head(panel).get_by_role("button", name="Refresh").click()
    expect(view).to_have_attribute("data-state", "up")
    expect(view.get_by_role("status")).to_have_count(0)
    expect(texts(panel)).to_have_count(5)


def test_a_transcript_says_only_its_own_reason_when_its_harness_was_not_all_read(broken_shell, ui_contexts,
                                                                                 ui_width_check):
    page = ui_contexts(broken_shell).pages[0]
    # A session of a root that could not be listed: the reason the turns give, and no
    # line of the list's.
    panel = open_route(page, f"codex/{tid(1)}")
    expect(window(panel)).to_have_attribute("data-state", "refused")
    expect(window(panel).get_by_role("status")).to_contain_text(
        f"Moxi couldn't read the session: no codex session {tid(1)} was found")
    expect(head(panel).locator('[data-line="heading"]')).to_have_text(f"Codex session {tid(1)}")
    expect(panel).not_to_contain_text("Moxi couldn't read Codex's sessions")
    ui_width_check(page)
    # A session that was read, in a harness with a part that was not: named, with no
    # sentence about the list's counts over it.
    panel = open_route(page, f"claude/{sid(1)}")
    expect(window(panel)).to_have_attribute("data-state", "up")
    expect(head(panel).locator('[data-line="name"]')).to_have_text("fix-tooltips")
    expect(texts(panel)).to_have_count(5)
    expect(panel).not_to_contain_text("the counts leave them out")
    expect(panel.locator('[data-line="unread"]')).to_have_count(0)


@pytest.fixture(scope="module")
def cold_shell(ui_shells, tmp_path_factory):
    """A shell over a named session and twenty others, whose page server has answered
    nothing yet."""
    shell = ui_shells(**env_of(tmp_path_factory.mktemp("cold")))
    root = Path(shell.env["CLAUDE_CONFIG_DIR"]) / "projects"
    now = time.time()
    named(root, shell.env["HOME"], now - 300)
    for n in range(20):
        other = ClaudeTranscript(root, session_id=sid(100 + n), cwd=f"/work/proj-{n}", project=f"-work-proj-{n}",
                                 start=now - 7200 - n * 60)
        other.user("Run it.", origin="human")
        other.assistant("Done.")
        changed_at(other.write(), now - 3600 - n * 60)
    return shell


def test_a_transcript_opened_cold_is_named_with_its_turns_and_asks_for_no_list(cold_shell, ui_contexts):
    page = ui_contexts(cold_shell).pages[0]
    asked: list[str] = []
    page.on("request", lambda request: asked.append(request.url) if "/api/" in request.url else None)
    panel = open_route(page, f"claude/{sid(1)}")
    expect(texts(panel)).to_have_count(5)
    expect(head(panel).locator('[data-line="name"]')).to_have_text("fix-tooltips", timeout=1000)
    expect(head(panel).locator('[data-line="where"]')).to_have_text("proj-a")
    expect(head(panel).locator('[data-line="facts"]')).to_have_text(FACTS)
    # The name comes from the session's own answer, not from a list of every session.
    assert any(SESSION_API.search(url) and f"session={sid(1)}" in url for url in asked)
    assert not any(LIST_API.search(url) for url in asked)


def test_the_header_fits_a_phone(seeded, ui_page, ui_width_check):
    ui_width_check(ui_page, module="sessions", route=f"claude/{sid(1)}")
    panel = ui_page.locator(PANEL)
    expect(head(panel).locator('[data-line="facts"]')).to_have_text(FACTS)
    head(panel).get_by_role("button", name="Details").click()
    expect(panel.locator('[data-field="cwd"]')).to_have_text("~/code/proj-a")
    ui_width_check(ui_page)
    ui_width_check(ui_page, module="sessions", route=f"claude/{sid(1)}/{AGENT}")
    expect(head(panel).locator('[data-line="heading"]')).to_contain_text("Subagent")
    ui_width_check(ui_page)
    ui_width_check(ui_page, module="sessions", route="claude/nosuch")
    expect(window(panel)).to_have_attribute("data-state", "refused")
    ui_width_check(ui_page)
