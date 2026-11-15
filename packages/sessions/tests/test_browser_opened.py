"""A row of the list opened in place, in a browser, through a real shell that starts
sessions' real page server: a two-line row with every piece (the typed message, the
reply, the subagents, the `Session` line, the resume command and the foot); a row with
each piece absent; a one-line row; a Codex session's subagent; each harness's resume
command and its `Copy`; `Copy id`; `Read the transcript` reaching the session's view and
the way back; the line shown while the session is read and when the read fails; a session
read again only once the list shows it changed; the 360 px width check; and the whole by
keyboard.

One shell over roots of its own written with `moxi_sessions.testing`. Every session is
timed from the moment the test runs.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect

from moxi_sessions.testing import ClaudeTranscript, CodexRollout, OpenCodeDb
from test_browser_list import changed_at, count_line, env_of, noon, open_list, row, sid, tid

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="sessions' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

ALL = 6
PROMPT = "Find out why the deploy failed."
REPLY = "Found it: the warmer starts first."
SESSION_API = re.compile(r"/api/session\?")


def full(root: Path, at: float, *, reply: str = REPLY) -> Path:
    """The session with every piece: a typed message, a reply after it, two subagents."""
    t = ClaudeTranscript(root, session_id=sid(1), cwd="/work/proj-a", project="-work-proj-a", start=at - 600)
    t.user(PROMPT, origin="human")
    for k, (kind, task, said) in enumerate((("Explore", "Read the deploy logs", "The check ran too early."),
                                            ("general-purpose", "List the callers", "Found three callers."))):
        sub = t.subagent(f"a0190b000000000{k + 1}", agent_type=kind, description=task)
        sub.user("look")
        sub.assistant(said)
    t.assistant(reply)
    path = t.write()
    changed_at(path, at)
    return path


def seed(env: dict[str, str]) -> None:
    """Six sessions:

    * the one with every piece;
    * one with no typed message and a reply: one line when closed;
    * one whose record gives no folder and no reply: a line said by nobody typing, and
      nothing after it;
    * one whose newest message is typed, so no reply follows it;
    * a Codex thread that spawned one subagent thread; an OpenCode session.
    """
    root = Path(env["CLAUDE_CONFIG_DIR"]) / "projects"
    now = time.time()
    full(root, now - 300)

    t = ClaudeTranscript(root, session_id=sid(2), cwd="/work/proj-b", project="-work-proj-b", start=now - 1500)
    t.user("A message no person typed.")
    t.assistant("Done.")
    changed_at(t.write(), now - 900)

    t = ClaudeTranscript(root, session_id=sid(3), cwd="", project="-work-nowhere", start=now - 2000)
    t.user("A message no person typed.")
    changed_at(t.write(), now - 1200)

    t = ClaudeTranscript(root, session_id=sid(4), cwd="/work/proj-a", project="-work-proj-a", start=now - 2400)
    t.user("First.", origin="human")
    t.assistant("An older reply.")
    t.user("And then this.", origin="human")
    changed_at(t.write(), now - 1500)

    codex = Path(env["CODEX_HOME"]) / "sessions"
    rollout = CodexRollout(codex, thread_id=tid(1), cwd="/work/proj-b", start=noon(4).timestamp() - 600)
    rollout.turn()
    rollout.user("Do the thing.")
    child = rollout.spawn(tid(2), "survey")
    child.turn()
    child.user("Survey it.")
    child.assistant("Surveyed.")
    changed_at(child.write(), noon(4).timestamp() - 60)
    rollout.assistant("Done.")
    changed_at(rollout.write(), noon(4).timestamp())

    db = OpenCodeDb(Path(env["XDG_DATA_HOME"]) / "opencode")
    db.session(title="a title the harness made", directory="/work/proj-c", at=noon(5).timestamp())
    db.write()


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory) -> dict:
    return env_of(tmp_path_factory.mktemp("harnesses"))


@pytest.fixture(scope="module")
def seeded(ui_shell) -> None:
    seed(ui_shell.env)


def opened(row_):
    return row_.locator('[data-part="opened"]')


def open_row(view, harness: str, session: str):
    """The row opened by a click on its first line, and what it shows once read."""
    one = row(view, harness, session)
    one.locator(".l1").click()
    expect(one).to_have_attribute("open", "")
    det = opened(one)
    expect(det.locator('[data-line="session"]')).to_be_visible()
    return one, det


def session_reads(page) -> list[str]:
    """The reads of one session the page makes from here on."""
    reads: list[str] = []
    page.on("request", lambda request: reads.append(request.url) if SESSION_API.search(request.url) else None)
    return reads


def test_an_opened_row_shows_every_piece_and_is_read_only_once_opened(seeded, ui_page):
    reads = session_reads(ui_page)
    view = open_list(ui_page)
    expect(count_line(view)).to_have_text(f"{ALL} sessions")
    assert reads == []
    one = row(view, "claude", sid(1))
    one.get_by_role("button", name="Open").click()
    det = opened(one)
    expect(det.locator('[data-line="you"]')).to_have_text(re.compile(r"^You\s*\d.*" + re.escape(PROMPT) + "$"))
    expect(det.locator('[data-line="reply"]')).to_have_text(re.compile(r"^Reply\s*\d.*" + re.escape(REPLY) + "$"))
    subs = det.locator('[data-line="subagent"]')
    expect(subs).to_have_count(2)
    expect(subs.filter(has_text="List the callers")).to_contain_text("Found three callers.")
    expect(subs.filter(has_text="Read the deploy logs")).to_have_text(
        re.compile(r"^Read the deploy logs\s*· Explore · .+ to .+The check ran too early\.$", re.S))
    expect(det.locator('[data-line="session"]')).to_have_text(re.compile(r"^Session\s*claude · \S+\s*· today .+ to .+$"))
    resume = det.locator('[data-line="resume"]')
    expect(resume.locator("code")).to_have_text(f"claude --resume {sid(1)}")
    expect(resume.locator('[data-line="where"]')).to_have_text("run it in /work/proj-a")
    expect(resume.get_by_role("button", name="Copy", exact=True)).to_be_visible()
    expect(det.get_by_role("link", name="Read the transcript")).to_have_attribute("href", f"/m/sessions/claude/{sid(1)}")
    expect(det.get_by_role("button", name="Copy id")).to_be_visible()
    expect(det.locator('[role="status"]')).to_have_count(0)
    expect(det.locator('[data-line]').first).to_have_attribute("data-line", "you")
    assert len(reads) == 1 and f"session={sid(1)}" in reads[0]

    # A click inside what the row shows does not close it, and a second row opens beside it.
    det.locator('[data-line="reply"]').click()
    expect(one).to_have_attribute("open", "")
    open_row(view, "claude", sid(2))
    expect(view.locator("moxi-ui-row[open]")).to_have_count(2)
    one.get_by_role("button", name="Close").click()
    expect(opened(one)).to_have_count(0)


def test_a_piece_whose_fact_was_not_read_is_not_drawn(seeded, ui_page):
    view = open_list(ui_page)
    # No folder, no typed message, no reply, no subagents, no model: the session line and the foot.
    _, det = open_row(view, "claude", sid(3))
    expect(det.locator('[data-line="session"]')).to_have_text(re.compile(r"^Session\s*claude\s*· today .+ to .+$"))
    for absent in ("you", "reply", "subagents", "resume"):
        expect(det.locator(f'[data-line="{absent}"]')).to_have_count(0)
    expect(det.locator(".r")).to_have_count(1)
    expect(det.get_by_role("link", name="Read the transcript")).to_be_visible()
    expect(det.get_by_role("button", name="Copy id")).to_be_visible()

    # A typed message with nothing said after it: no reply line.
    _, det = open_row(view, "claude", sid(4))
    expect(det.locator('[data-line="you"]')).to_contain_text("And then this.")
    expect(det.locator('[data-line="reply"]')).to_have_count(0)
    expect(det.locator('[data-line="subagents"]')).to_have_count(0)
    expect(det.locator('[data-line="resume"] code')).to_have_text(f"claude --resume {sid(4)}")


def test_a_one_line_row_opens_to_its_reply_its_session_line_and_its_command(seeded, ui_page):
    view = open_list(ui_page)
    one = row(view, "claude", sid(2))
    expect(one.locator(".l2")).to_be_hidden()
    expect(one.locator('[slot="acts"]')).to_have_count(0)
    _, det = open_row(view, "claude", sid(2))
    expect(det.locator('[data-line="you"]')).to_have_count(0)
    expect(det.locator('[data-line="reply"]')).to_contain_text("Done.")
    expect(det.locator('[data-line="resume"] code')).to_have_text(f"claude --resume {sid(2)}")
    expect(det.get_by_role("link", name="Read the transcript")).to_be_visible()
    expect(one.locator('[slot="acts"]')).to_have_count(0)


def test_a_codex_sessions_subagent_is_listed(seeded, ui_page):
    view = open_list(ui_page)
    _, det = open_row(view, "codex", tid(1))
    expect(det.locator('[data-line="subagent"]')).to_have_count(1)
    expect(det.locator('[data-line="subagent"]')).to_contain_text("Surveyed.")
    expect(det.locator('[data-line="subagents-unread"]')).to_have_count(0)
    expect(det.locator('[data-line="session"]')).to_have_text(re.compile(r"^Session\s*codex · "))


def test_each_harness_has_its_own_resume_command_and_copy_copies_it(seeded, ui_contexts, ui_browser):
    if ui_browser.browser_type.name != "chromium":
        pytest.skip("only Chromium grants a test the clipboard")
    page = ui_contexts(permissions=["clipboard-read", "clipboard-write"]).pages[0]
    view = open_list(page)
    opencode = view.locator('moxi-ui-row[data-key^="opencode/"]')
    expect(opencode).to_have_count(1)
    session = opencode.get_attribute("data-key").split("/", 1)[1]
    for harness, session, command, where in (
            ("claude", sid(1), f"claude --resume {sid(1)}", "run it in /work/proj-a"),
            ("codex", tid(1), f"codex resume {tid(1)}", None),
            ("opencode", session, f"opencode --session {session}", "run it in /work/proj-c")):
        _, det = open_row(view, harness, session)
        resume = det.locator('[data-line="resume"]')
        expect(resume.locator("code")).to_have_text(command)
        if where is None:
            expect(resume.locator('[data-line="where"]')).to_have_count(0)
        else:
            expect(resume.locator('[data-line="where"]')).to_have_text(where)
        resume.get_by_role("button", name="Copy", exact=True).click()
        expect(resume.get_by_role("button", name="Copied")).to_be_visible()
        assert page.evaluate("navigator.clipboard.readText()") == command
        # Only the act that was pressed says so.
        expect(det.get_by_role("button", name="Copy id")).to_be_visible()

    det = opened(row(view, "claude", sid(1)))
    det.get_by_role("button", name="Copy id").click()
    expect(det.locator('[data-action="copy-id"]')).to_have_text("Copied")
    assert page.evaluate("navigator.clipboard.readText()") == sid(1)
    expect(det.locator('[data-action="copy-resume"]')).to_have_text("Copy")
    # `Copied` gives way to the act again.
    expect(det.locator('[data-action="copy-id"]')).to_have_text("Copy id")


def test_read_the_transcript_goes_to_the_sessions_view_and_back_returns_to_the_list(seeded, ui_page):
    view = open_list(ui_page)
    _, det = open_row(view, "claude", sid(1))
    det.get_by_role("link", name="Read the transcript").click()
    expect(ui_page).to_have_url(re.compile(rf"/m/sessions/claude/{sid(1)}$"))
    panel = ui_page.locator("#moxi-panel > moxi-sessions-page")
    expect(panel.locator("moxi-sessions-list")).to_have_count(0)
    expect(panel).to_contain_text(PROMPT)
    expect(panel).to_contain_text(REPLY)
    ui_page.go_back()
    expect(ui_page).to_have_url(re.compile(r"/m/sessions/$"))
    expect(count_line(ui_page.locator("#moxi-panel > moxi-sessions-page moxi-sessions-list"))).to_have_text(
        f"{ALL} sessions")


def test_an_opened_row_says_it_is_reading_and_says_why_when_the_read_fails(seeded, ui_page):
    view = open_list(ui_page)
    expect(count_line(view)).to_have_text(f"{ALL} sessions")
    held = []
    ui_page.route(SESSION_API, lambda route: held.append(route))
    one = row(view, "claude", sid(1))
    one.get_by_role("button", name="Open").click()
    det = opened(one)
    expect(det.get_by_role("status")).to_have_text("Reading the session…")
    expect(det.locator(".r")).to_have_count(0)
    expect(det.get_by_role("link", name="Read the transcript")).to_be_visible()
    expect(det.get_by_role("button", name="Copy id")).to_be_visible()
    expect(ui_page.get_by_text("Reading the session…")).to_have_count(1)
    assert len(held) == 1
    held[0].fulfill(status=500, content_type="application/json", body="{}")
    expect(det.get_by_role("status")).to_have_text("Moxi couldn't read the session: sessions' page server answered 500")
    expect(det.locator(".r")).to_have_count(0)
    expect(det.get_by_role("link", name="Read the transcript")).to_be_visible()
    # Closed and opened again, the session is asked for again.
    ui_page.unroute(SESSION_API)
    one.get_by_role("button", name="Close").click()
    one.get_by_role("button", name="Open").click()
    expect(opened(one).locator('[data-line="reply"]')).to_contain_text(REPLY)
    expect(opened(one).get_by_role("status")).to_have_count(0)


def test_an_opened_row_is_read_again_only_when_the_list_shows_its_session_changed(seeded, ui_page, ui_shell):
    view = open_list(ui_page)
    one, det = open_row(view, "claude", sid(1))
    open_row(view, "claude", sid(2))
    reads = session_reads(ui_page)
    again = "document.dispatchEvent(new Event('visibilitychange'))"
    with ui_page.expect_response(re.compile(r"/api/list")):
        ui_page.evaluate(again)
    expect(det.locator('[data-line="reply"]')).to_contain_text(REPLY)
    assert reads == []

    root = Path(ui_shell.env["CLAUDE_CONFIG_DIR"]) / "projects"
    was = time.time() - 300
    try:
        full(root, time.time() - 5, reply="A newer reply.")
        # The page server reuses a listing for two seconds.
        ui_page.wait_for_timeout(2100)
        ui_page.evaluate(again)
        expect(det.locator('[data-line="reply"]')).to_contain_text("A newer reply.")
        expect(one).to_have_attribute("open", "")
        assert len(reads) == 1 and f"session={sid(1)}" in reads[0]
    finally:
        full(root, was)
        time.sleep(2.1)


def test_an_opened_row_fits_a_phone(seeded, ui_page, ui_width_check):
    ui_width_check(ui_page, module="sessions")
    view = ui_page.locator("#moxi-panel > moxi-sessions-page moxi-sessions-list")
    for harness, session in (("claude", sid(1)), ("claude", sid(3)), ("codex", tid(1))):
        _, det = open_row(view, harness, session)
        expect(det.get_by_role("link", name="Read the transcript")).to_be_visible()
    expect(opened(row(view, "claude", sid(1))).locator('[data-line="resume"] code')).to_be_visible()
    ui_width_check(ui_page)


def test_a_row_is_opened_read_and_left_for_its_transcript_by_keyboard(seeded, ui_page):
    view = open_list(ui_page)
    one = row(view, "claude", sid(1))
    line = one.get_by_role("button", name=re.compile(r"^proj-a · started today "))
    line.focus()
    ui_page.keyboard.press("Enter")
    det = opened(one)
    expect(det.locator('[data-line="session"]')).to_be_visible()
    expect(line).to_have_attribute("aria-expanded", "true")
    # From the line: the row's act, then what the opened row offers, in the order drawn.
    for name in ("Close", "Copy", "Read the transcript", "Copy id"):
        ui_page.keyboard.press("Tab")
        focused = one.get_by_role("link" if name == "Read the transcript" else "button", name=name, exact=True)
        expect(focused).to_be_focused()
        ring = focused.evaluate("el => { const s = getComputedStyle(el); return [el.matches(':focus-visible'),"
                                " s.outlineStyle, s.outlineWidth]; }")
        assert ring == [True, "solid", "2px"]
    ui_page.keyboard.press("Shift+Tab")
    ui_page.keyboard.press("Enter")
    expect(ui_page).to_have_url(re.compile(rf"/m/sessions/claude/{sid(1)}$"))
    expect(ui_page.locator("#moxi-panel > moxi-sessions-page")).to_contain_text(REPLY)
