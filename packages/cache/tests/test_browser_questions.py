"""The six questions cache's page is there to answer, each answered from the page the way
a person would, in a browser, through a real shell that starts cache's real page server:

1. Is my session still warm, and until when?
2. What goes cold next?
3. How many are warm and how many cold?
4. What does going cold cost? Nothing on the page says.
5. Is anything keeping a session warm? Nothing on the page says.
6. Can I trust these readings?

Each walk starts at the page's own address and reads only what the page shows. Each runs
twice: on a small install, which holds the core, ui, cache and sessions and nothing else,
and on a full one, which holds every module of this environment. Both shells hold the same
transcripts, written with sessions' builders under each shell's own home, and each walk
writes the same clock records and watcher's reading into the shell it runs on, timed from
one moment that the page's clock is pinned to. A walk keeps what the page showed at each
step: the two installs must show the same, piece for piece; the page places no element
but its own and the row; no session's id shows; and nothing names a cost or what keeps a
session warm.

A row's name is checked against the Sessions page's own naming rule, run on what the
page's names read answered, never against a second copy of its words.

After the walks: the waiting line, rows drawn before their names come, the names that
could not be read, a failed read and the page server's error under the foot, both links
landing, a 404 drawing nothing, and the page at 360 px with no second heading and no
colour but the text tokens.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
from moxi import modules
from moxi_sessions.api import testing
from playwright.sync_api import expect

import page_kit
from page_kit import put, record

pytestmark = [pytest.mark.browser, pytest.mark.timeout(300),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="cache runs on Linux only")]

PANEL = "#moxi-panel > moxi-cache-page"
#: The modules a small install holds beside the core.
SMALL = ("cache", "sessions", "ui")


def sid(n: int) -> str:
    return f"0190b000-0000-7000-8000-{n:012x}"


#: A session with a typed name, one named by its head alone, and two sessions holds no
#: transcript for.
NAMED, HEADED, STRANGER, OTHER = sid(1), sid(2), sid(3), sid(4)
#: Sessions whose records hold no clock time.
FRESH, UNREAD = sid(5), sid(6)
IDS = (NAMED, HEADED, STRANGER, OTHER, FRESH, UNREAD)
MODEL = "claude-model-a"
UNREAD_CODE = "no-usage"
WATCH = "moxi cache watch"
EMPTY = "No Claude Code session has a cache yet."
CACHE_LINE = re.compile(r"^A session shows here from its first request")
NAMES_FAILED = "Moxi couldn't read these sessions' names"
READ_TAIL = "not rewritten since; this reading may be old"

#: Words of pieces no fact of this page carries: a cost, and what keeps a session warm.
LEFT_OUT = re.compile(r"\$|re-read|keep warm|kept warm|keep-alive|\bslot\b|\bproject\b", re.I)

#: The Sessions page's rule for a row's name, in that page's own source.
RULE = Path(__file__).resolve().parents[2] / "sessions" / "web" / "src" / "list.ts"
_RUN_RULE = """
const { rowName } = await import(process.argv[1]);
const [rows, today] = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify(rows.map((row) => rowName(row, today))));
"""

#: What the page shows, top to bottom: each run of text, each control, each element with a
#: name of its own, and each row with the facts it was given.
SHOWN = """el => {
  const out = [];
  const walk = (node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent.replace(/\\s+/g, " ").trim();
      if (text) out.push(text);
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE || ["STYLE", "SCRIPT", "TEMPLATE"].includes(node.tagName)) return;
    const tag = node.tagName.toLowerCase();
    if (tag === "moxi-ui-row") {
      const facts = {};
      for (const name of node.getAttributeNames().sort()) {
        if (name !== "class" && name !== "style") facts[name] = node.getAttribute(name);
      }
      out.push("row " + JSON.stringify(facts));
      for (const child of node.childNodes) walk(child);
      return;
    }
    if (tag.includes("-")) out.push(`<${tag}>`);
    if (tag === "button" || tag === "a") out.push(`[${tag}]`);
    for (const child of (node.shadowRoot ?? node).childNodes) walk(child);
  };
  walk(el);
  return out;
}"""

#: Every colour the page draws with, but for the focus ring: each element's text colour,
#: background and drawn borders, in the page's shadow root and each row's; and what the
#: tokens resolve to there.
COLOURS = """el => {
  const probe = (token) => {
    const one = document.createElement("span");
    one.style.color = `var(${token})`;
    el.shadowRoot.append(one);
    const value = getComputedStyle(one).color;
    one.remove();
    return value;
  };
  const tokens = {text: probe("--moxi-text"), dim: probe("--moxi-text-dim"), border: probe("--moxi-border")};
  const seen = [];
  const walk = (root) => {
    for (const node of root.querySelectorAll("*")) {
      if (["STYLE", "SCRIPT", "TEMPLATE", "SLOT"].includes(node.tagName)) continue;
      const s = getComputedStyle(node);
      if (node.textContent.trim()) seen.push(["color", s.color]);
      seen.push(["background", s.backgroundColor]);
      for (const side of ["Top", "Right", "Bottom", "Left"]) {
        if (s[`border${side}Style`] !== "none" && parseFloat(s[`border${side}Width`]) > 0) {
          seen.push(["border", s[`border${side}Color`]]);
        }
      }
      if (node.shadowRoot) walk(node.shadowRoot);
    }
  };
  walk(el.shadowRoot);
  return {tokens, seen};
}"""


def others() -> list[str]:
    """Every installed module a small install leaves out."""
    return [found.name for found in modules.scan() if found.name not in SMALL]


def hhmm(at: float, seconds: str = "") -> str:
    """A pattern for the time of day of `at`, to the minute, as the browser's locale gives
    it: on a 24-hour clock or a 12-hour one."""
    moment = time.localtime(at)
    return (rf"(?:{time.strftime('%H:%M' + seconds, moment)}|"
            rf"{time.strftime('%I:%M' + seconds, moment)}\s?[AP]M)")


def hhmmss(at: float) -> str:
    """The same, to the second."""
    return hhmm(at, ":%S")


def seed(shell, moment: float) -> None:
    """The transcripts sessions names the rows from: one with a typed name in
    `~/code/proj-a`, and one in `~/code/proj-b` named by its folder and start."""
    home = shell.env["HOME"]
    root = Path(shell.env["CLAUDE_CONFIG_DIR"]) / "projects"
    named = testing.ClaudeTranscript(root, session_id=NAMED, cwd=f"{home}/code/proj-a",
                                     project="-home-code-proj-a", start=moment - 3000)
    named.user("Fix the tooltips.", origin="human")
    named.title("fix-tooltips")
    named.assistant("Done.", model=MODEL)
    named.write()
    headed = testing.ClaudeTranscript(root, session_id=HEADED, cwd=f"{home}/code/proj-b",
                                      project="-home-code-proj-b", start=moment - 5400)
    headed.user("Bump the parser.", origin="human")
    headed.assistant("Bumped.")
    headed.write()


@pytest.fixture(scope="module")
def moment() -> float:
    """The moment every record and reading of both installs is timed from, and the time
    the page's clock is pinned to when it reads the records."""
    return time.time()


@pytest.fixture(scope="module")
def installs(ui_shells, tmp_path_factory, moment) -> dict:
    """The two shells, each over the same transcripts under a home of its own: `small`
    leaves out every module but ui, cache and sessions, `full` leaves out none."""
    made = {}
    for name, leave_out in (("small", others()), ("full", [])):
        base = tmp_path_factory.mktemp(name)
        shell = ui_shells(leave_out=leave_out, env={"CLAUDE_CONFIG_DIR": str(base / "claude-home")})
        seed(shell, moment)
        made[name] = shell
    return made


def lay(shell, moment: float, *records: dict, reading: dict | None = None) -> None:
    """Make the shell's records exactly `records` and its reading `reading` (the keyword
    arguments of `page_kit.put_reading`, or none at all with None), and wait until cache's
    topic holds them."""
    page_kit.clear(shell)
    for rec in records:
        put(shell, rec)
    if reading is None:
        page_kit.clear_reading(shell)
        sent = None
    else:
        sent = page_kit.put_reading(shell, now=moment, **reading)
    wanted = sorted((rec["session"], rec["kind"], rec["last_request"], rec["seen_at"]) for rec in records)
    page_kit.published(shell, lambda data: data["state"] == "up" and data["reading"] == sent and sorted(
        (row["session"], row["kind"], row["last_request"], row["seen_at"]) for row in data["clocks"]) == wanted)


#: A reading as a watcher that ticked and listed a second ago leaves it.
FRESH_READING = {"tick": 1, "listed": 1}


def open_page(page, moment: float):
    """Cache's page at its own address, its clock pinned to `moment` at each read."""
    page_kit.pin_now(page, moment)
    page.goto("/m/cache/")
    return page.locator(PANEL)


def rows(panel):
    return panel.locator("moxi-ui-row")


def group(panel, kind: str):
    return panel.locator(f'section[data-group="{kind}"]')


def row(panel, session: str):
    return panel.locator(f'moxi-ui-row[data-key="claude/{session}"]')


def keys(where, count: int) -> list[str]:
    found = where.locator("moxi-ui-row")
    expect(found).to_have_count(count)
    return [key.split("/", 1)[1] for key in found.evaluate_all("all => all.map((one) => one.dataset.key)")]


def opened(panel, session: str):
    one = row(panel, session)
    one.get_by_role("button", name="Open").click()
    expect(one).to_have_attribute("open", "")
    return one.locator('[data-part="opened"]')


def shown(page) -> list[str]:
    """What cache's page shows now, top to bottom."""
    return page.locator(PANEL).evaluate(SHOWN)


def named_as(shell, *sessions: str) -> dict[str, tuple[str, str | None]]:
    """What the Sessions page's rule names each of `sessions`, from the facts the page's
    names read gives it, or from none for a session it does not hold: `(name, where)`."""
    status, answer = page_kit.session(shell).get("/m/cache/api/names")
    assert status == 200 and answer["state"] == "up", answer
    by = {item["session"]: item for item in answer["sessions"]}
    none = {"harness": "claude", "name": None, "folder": None, "started": None}
    facts = [{key: by.get(one, none)[key] for key in none} for one in sessions]
    out = subprocess.run(["node", "--no-warnings", "--input-type=module", "-e", _RUN_RULE, str(RULE),
                          json.dumps([facts, answer["today"]])],
                         capture_output=True, text=True, timeout=60, check=True).stdout
    return {one: (said["name"], said["where"]) for one, said in zip(sessions, json.loads(out))}


def named_by_rule(panel, names: dict[str, tuple[str, str | None]]) -> None:
    for session, (name, where) in names.items():
        one = row(panel, session)
        expect(one).to_have_attribute("name", name)
        if where is None:
            expect(one).not_to_have_attribute("where", re.compile(r".*"))
        else:
            expect(one).to_have_attribute("where", where)


@pytest.fixture
def on_both(installs, ui_contexts):
    """`on_both(walk, **context_args)` runs `walk(page, shell)` on the small install and
    on the full one, each in a context of its own. A walk returns what the page showed at
    each of its steps; the two must be the same, hold no element that is not cache's page
    or the row, no session's id and no word of a cost or of keeping a session warm.
    Returns what was shown."""
    def run(walk, **context_args) -> list:
        seen = {}
        for name, shell in installs.items():
            page = ui_contexts(shell, **context_args).pages[0]
            try:
                seen[name] = walk(page, shell)
                expect(page.locator(PANEL).get_by_text(LEFT_OUT)).to_have_count(0)
            except AssertionError as failed:
                failed.add_note(f"on the {name} install")
                raise
        assert seen["small"] == seen["full"]
        for step in seen["full"]:
            placed = {line for line in step if line.startswith("<")}
            assert placed <= {"<moxi-cache-page>", "<moxi-ui-row>"}, placed
            for line in step:
                said = line
                if line.startswith("row "):
                    facts = json.loads(line[4:])
                    facts.pop("data-key")
                    said = json.dumps(facts)
                assert not any(one in said for one in IDS), line
                assert not LEFT_OUT.search(said), line
        return seen["full"]

    return run


@pytest.fixture
def clipboard(ui_browser) -> dict:
    """The context options that let a walk read the clipboard: Chromium's alone."""
    if ui_browser.browser_type.name != "chromium":
        return {}
    return {"permissions": ["clipboard-read", "clipboard-write"]}


def copied(page, button, text: str) -> None:
    """`button` puts `text` on the clipboard and reads `Copied`, then its own word again.
    It is found by its act, not its name, as its name changes."""
    word = button.inner_text()
    button.click()
    expect(button).to_have_text("Copied")
    assert page.evaluate("navigator.clipboard.readText()") == text
    expect(button).to_have_text(word)


def test_the_small_install_holds_ui_cache_and_sessions_and_the_full_one_every_module(installs, ui_contexts):
    held = {}
    for name, shell in installs.items():
        page = ui_contexts(shell).pages[0]
        page.goto("/m/ui/modules")
        cards = page.locator("#moxi-panel > moxi-ui-page [data-module]")
        expect(cards.first).to_be_visible()
        held[name] = sorted(set(cards.evaluate_all("all => all.map((one) => one.dataset.module)")))
    assert held["small"] == sorted(SMALL)
    assert set(held["full"]) == {found.name for found in modules.scan()}
    assert len(held["full"]) > len(held["small"])


def test_my_session_is_warm_until_a_time_and_opens_to_the_second(on_both, moment, clipboard):
    def walk(page, shell) -> list:
        steps = []
        lay(shell, moment, record(NAMED, now=moment, age=600), record(HEADED, now=moment, age=1800),
            record(STRANGER, now=moment, age=2400), reading=FRESH_READING)
        panel = open_page(page, moment)
        warm = group(panel, "warm")
        expect(warm.locator('[data-line="label"]')).to_have_text("3 warm")
        assert keys(warm, 3) == [STRANGER, HEADED, NAMED]
        # Each row is named as the Sessions page names it: a typed name and its folder;
        # a folder and when it started; and a session sessions holds nothing for.
        named_by_rule(panel, named_as(shell, NAMED, HEADED, STRANGER))
        for session, age in ((NAMED, 600), (HEADED, 1800), (STRANGER, 2400)):
            one = row(panel, session)
            expect(one).to_have_attribute("fact", re.compile(rf"^warm until (.* )?{hhmm(moment - age + 3600)}$"))
            expect(one).to_have_attribute("rest", re.compile(rf"^last request (.* )?{hhmm(moment - age)}$"))
        steps.append(shown(page))

        # Opened: the times to the second, the countdown, the lifetime in words, and
        # which session it is.
        det = opened(panel, NAMED)
        expect(det.locator('[data-line="Last request"]')).to_have_text(
            re.compile(rf"^Last request\s*(.* )?{hhmmss(moment - 600)}$"))
        expect(det.locator('[data-line="Goes cold"]')).to_have_text(re.compile(
            rf"^Goes cold\s*(.* )?{hhmmss(moment + 3000)} · in 50 minutes · one hour after the last request$"))
        expect(det.locator('[data-line="session"]')).to_have_text(re.compile(
            rf"^Session\s*Claude Code · {MODEL} · started (.* )?{hhmm(moment - 3000)} · ~/code/proj-a\s*Sessions$"))
        expect(det.get_by_role("link", name="Sessions")).to_be_visible()
        expect(det.locator('[data-line="Read"]')).to_have_count(0)
        # One sessions holds nothing for is its harness alone, with no link.
        other = opened(panel, STRANGER)
        expect(other.locator('[data-line="session"]')).to_have_text(re.compile(r"^Session\s*Claude Code$"))
        expect(other.get_by_role("link", name="Sessions")).to_have_count(0)
        steps.append(shown(page))
        # The id is reached only by `Copy id`.
        if clipboard:
            copied(page, det.locator('[data-action="copy-id"]'), NAMED)
        return steps

    on_both(walk, **clipboard)


def test_what_goes_cold_next_is_first_and_turns_cold_at_its_time(on_both, moment):
    def walk(page, shell) -> list:
        steps = []
        # The first goes cold ten seconds after the pinned moment.
        lay(shell, moment, record(NAMED, now=moment, age=3590), record(HEADED, now=moment, age=600),
            record(STRANGER, now=moment, age=4000, kind="cold"), record(OTHER, now=moment, age=5000, kind="cold"),
            reading=FRESH_READING)
        panel = open_page(page, moment)
        warm, cold = group(panel, "warm"), group(panel, "cold")
        assert keys(warm, 2) == [NAMED, HEADED]
        # Cold rows last, the most recently cold first.
        assert keys(cold, 2) == [STRANGER, OTHER]
        assert panel.locator("section[data-group]").evaluate_all(
            "all => all.map((one) => one.dataset.group)") == ["warm", "cold"]
        expect(row(panel, STRANGER)).to_have_attribute("fact", re.compile(rf"^cold since (.* )?{hhmm(moment - 400)}$"))
        steps.append(shown(page))

        # Its time comes on the page's clock, with no read: it is cold, and first of them.
        expect(cold.locator('[data-line="label"]')).to_have_text("3 cold", timeout=20_000)
        expect(warm.locator('[data-line="label"]')).to_have_text("1 warm")
        assert keys(warm, 1) == [HEADED]
        assert keys(cold, 3) == [NAMED, STRANGER, OTHER]
        expect(row(panel, NAMED)).to_have_attribute("fact", re.compile(rf"^cold since (.* )?{hhmm(moment + 10)}$"))
        steps.append(shown(page))
        return steps

    on_both(walk)


def test_how_many_are_warm_and_cold_is_each_groups_label(on_both, moment):
    def walk(page, shell) -> list:
        steps = []
        untimed = (record(FRESH, now=moment, kind="fresh"),
                   record(UNREAD, now=moment, kind="unknown", code=UNREAD_CODE))
        lay(shell, moment, record(NAMED, now=moment, age=600), record(HEADED, now=moment, age=1200),
            record(STRANGER, now=moment, age=4000, kind="cold"), *untimed, reading=FRESH_READING)
        panel = open_page(page, moment)
        expect(group(panel, "warm").locator('[data-line="label"]')).to_have_text("2 warm")
        expect(group(panel, "cold").locator('[data-line="label"]')).to_have_text("1 cold")
        expect(rows(panel)).to_have_count(3)
        line = panel.locator('[data-line="untimed"]')
        expect(line).to_have_text(re.compile(r"^\s*2 with no cache reading\s*Show\s*$"))
        steps.append(shown(page))

        # The sessions with no clock time show and hide, each named the same way and
        # saying only what its record says.
        line.get_by_role("button", name="Show").click()
        expect(rows(panel)).to_have_count(5)
        named_by_rule(panel, named_as(shell, FRESH, UNREAD))
        expect(row(panel, FRESH)).to_have_attribute("fact", "no request yet")
        expect(row(panel, UNREAD)).to_have_attribute("fact", f"not read · {UNREAD_CODE}")
        steps.append(shown(page))
        line.get_by_role("button", name="Hide").click()
        expect(rows(panel)).to_have_count(3)

        # A group with none is not drawn.
        page_kit.record_dir(shell).joinpath(f"{STRANGER}.json").unlink()
        expect(group(panel, "cold")).to_have_count(0, timeout=10_000)
        expect(group(panel, "warm").locator('[data-line="label"]')).to_have_text("2 warm")
        expect(panel.get_by_text(re.compile(r"\b0 (warm|cold)\b"))).to_have_count(0)
        steps.append(shown(page))
        return steps

    on_both(walk)


def every_row_opened(panel, sessions) -> None:
    for session in sessions:
        det = opened(panel, session)
        expect(det.locator('[data-line="session"]')).not_to_contain_text("Reading…")


def test_nothing_says_what_going_cold_costs(on_both, moment):
    def walk(page, shell) -> list:
        steps = []
        lay(shell, moment, record(NAMED, now=moment, age=600), record(STRANGER, now=moment, age=4000, kind="cold"),
            record(FRESH, now=moment, kind="fresh"), reading=FRESH_READING)
        panel = open_page(page, moment)
        panel.locator('[data-line="untimed"]').get_by_role("button", name="Show").click()
        every_row_opened(panel, (NAMED, STRANGER, FRESH))
        for words in ("$", "re-read", "cost", "price"):
            expect(rows(panel).get_by_text(words)).to_have_count(0)
        expect(panel.locator('[data-line="If cold"]')).to_have_count(0)
        steps.append(shown(page))
        # Nor the empty state, nor the one with no reading.
        lay(shell, moment, reading=FRESH_READING)
        expect(panel.get_by_text(EMPTY, exact=True)).to_be_visible(timeout=10_000)
        steps.append(shown(page))
        lay(shell, moment)
        expect(panel.get_by_text("No reading yet", exact=True)).to_be_visible(timeout=10_000)
        steps.append(shown(page))
        return steps

    on_both(walk)


def test_nothing_says_what_keeps_a_session_warm(on_both, moment):
    def walk(page, shell) -> list:
        steps = []
        lay(shell, moment, record(NAMED, now=moment, age=600), record(HEADED, now=moment, age=3000),
            record(STRANGER, now=moment, age=4000, kind="cold"), reading=FRESH_READING)
        panel = open_page(page, moment)
        every_row_opened(panel, (NAMED, HEADED, STRANGER))
        expect(panel.get_by_role("button", name=re.compile("keep", re.I))).to_have_count(0)
        expect(panel.locator('[data-line="Keep warm"]')).to_have_count(0)
        # Rows are named by session alone, and the warm come by their time, not by what waits.
        assert keys(group(panel, "warm"), 2) == [HEADED, NAMED]
        steps.append(shown(page))
        # Nor the Watcher row, when the watcher stopped.
        lay(shell, moment, record(NAMED, now=moment, age=600, seen=120), reading={"tick": 120, "listed": 120})
        expect(panel.get_by_text(re.compile(r"^Stopped at "))).to_be_visible(timeout=10_000)
        steps.append(shown(page))
        return steps

    on_both(walk)


def test_the_readings_say_how_far_they_can_be_trusted(on_both, moment, clipboard):
    def walk(page, shell) -> list:
        steps = []
        lay(shell, moment, record(NAMED, now=moment, age=600), reading=FRESH_READING)
        panel = open_page(page, moment)
        foot = panel.locator('[data-part="foot"]')
        watcher = panel.locator('section[data-part="watcher"]')
        expect(foot.locator('[data-line="reading"]')).to_have_text(re.compile(rf"^Last reading {hhmm(moment - 1)}$"))
        expect(watcher).to_have_count(0)
        # `Last reading` follows the topic: the watcher lists again, and the foot says when.
        lay(shell, moment, record(NAMED, now=moment, age=600), reading={"tick": -90, "listed": -90})
        expect(foot.locator('[data-line="reading"]')).to_have_text(
            re.compile(rf"^Last reading {hhmm(moment + 90)}$"), timeout=10_000)
        steps.append(shown(page))

        # One record that stood still is its opened row's `Read` line, and no Watcher row.
        lay(shell, moment, record(NAMED, now=moment, age=600), record(HEADED, now=moment, age=1200, seen=120),
            reading=FRESH_READING)
        expect(rows(panel)).to_have_count(2, timeout=10_000)
        expect(watcher).to_have_count(0)
        expect(row(panel, HEADED)).not_to_have_attribute("fact", re.compile(r"old|Read"))
        det = opened(panel, HEADED)
        expect(det.locator('[data-line="Read"]')).to_have_text(
            re.compile(rf"^Read\s*(.* )?{hhmmss(moment - 120)} · {re.escape(READ_TAIL)}$"))
        expect(opened(panel, NAMED).locator('[data-line="Read"]')).to_have_count(0)
        steps.append(shown(page))

        # A watcher that runs but has not listed Claude's sessions since a time.
        lay(shell, moment, record(NAMED, now=moment, age=600, seen=120), reading={"tick": 1, "listed": 120})
        expect(watcher.locator('[data-line="watcher"]')).to_have_text(
            re.compile(rf"^Not reading since {hhmm(moment - 120)}$"), timeout=10_000)
        expect(watcher).to_contain_text("It is running but could not list Claude's sessions; its log says why.")
        expect(watcher.get_by_role("button")).to_have_count(0)
        expect(foot).to_have_text(re.compile(r"^\s*Cache settings\s*$"))
        expect(rows(panel)).to_have_count(1)
        steps.append(shown(page))

        # One that stopped: when, and the command that starts it again, to copy.
        lay(shell, moment, record(NAMED, now=moment, age=600, seen=120), reading={"tick": 120, "listed": 120})
        expect(watcher.locator('[data-line="watcher"]')).to_have_text(
            re.compile(rf"^Stopped at {hhmm(moment - 120)}$"), timeout=10_000)
        expect(watcher.locator("code")).to_have_text([WATCH])
        expect(row(panel, NAMED)).to_have_attribute("fact", re.compile(r"^warm until "))
        steps.append(shown(page))
        if clipboard:
            copied(page, watcher.locator('[data-action="copy-command"]'), WATCH)

        # No reading at all and no record: how to start it, and no empty state.
        lay(shell, moment)
        expect(watcher.locator('[data-line="watcher"]')).to_have_text("No reading yet", timeout=10_000)
        expect(watcher.locator("code")).to_have_text([WATCH, "moxi install cache"])
        expect(panel.get_by_text(EMPTY)).to_have_count(0)
        expect(panel.get_by_text(CACHE_LINE)).to_be_visible()
        expect(foot).to_have_count(0)
        steps.append(shown(page))
        if clipboard:
            copied(page, watcher.locator('[data-action="copy-command"]'), WATCH)

        # The empty state only when a fresh reading backs it ...
        lay(shell, moment, reading=FRESH_READING)
        expect(panel.get_by_text(EMPTY, exact=True)).to_be_visible(timeout=10_000)
        expect(panel.get_by_text(CACHE_LINE)).to_be_visible()
        expect(watcher).to_have_count(0)
        expect(foot).to_have_count(0)
        steps.append(shown(page))
        # ... and not over a session whose record could not be read.
        lay(shell, moment, record(UNREAD, now=moment, kind="unknown", code=UNREAD_CODE), reading=FRESH_READING)
        expect(panel.get_by_text("1 with no cache reading")).to_be_visible(timeout=10_000)
        expect(panel.get_by_text(EMPTY)).to_have_count(0)
        expect(panel.get_by_text(CACHE_LINE)).to_have_count(0)
        expect(foot.locator('[data-line="reading"]')).to_have_text(re.compile(rf"^Last reading {hhmm(moment - 1)}$"))
        steps.append(shown(page))
        return steps

    on_both(walk, **clipboard)


def test_both_links_land(on_both, moment):
    def walk(page, shell) -> list:
        steps = []
        lay(shell, moment, record(NAMED, now=moment, age=600), reading=FRESH_READING)
        panel = open_page(page, moment)
        steps.append(shown(page))
        opened(panel, NAMED).get_by_role("link", name="Sessions").click()
        expect(page).to_have_url(re.compile(rf"/m/sessions/claude/{NAMED}$"))
        head = page.locator('#moxi-panel > moxi-sessions-page [data-part="head"]')
        expect(head.locator('[data-line="name"]')).to_have_text("fix-tooltips")
        panel = open_page(page, moment)
        panel.get_by_role("link", name="Cache settings").click()
        expect(page).to_have_url(re.compile(r"/m/ui/settings/cache$"))
        expect(page.locator("#moxi-panel > moxi-ui-page section.card[data-module=cache]")).to_be_visible()
        return steps

    on_both(walk)


def test_at_360_px_the_page_fits_has_no_second_heading_and_only_text_colours(on_both, moment):
    def walk(page, shell) -> list:
        lay(shell, moment, record(NAMED, now=moment, age=600), record(HEADED, now=moment, age=1800, seen=120),
            record(STRANGER, now=moment, age=4000, kind="cold"),
            record(UNREAD, now=moment, kind="unknown", code=UNREAD_CODE), reading={"tick": 1, "listed": 1})
        panel = open_page(page, moment)
        panel.locator('[data-line="untimed"]').get_by_role("button", name="Show").click()
        every_row_opened(panel, (NAMED, HEADED, STRANGER, UNREAD))
        assert page.evaluate("document.documentElement.scrollWidth") <= 360
        expect(panel.get_by_role("heading")).to_have_count(0)
        found = panel.evaluate(COLOURS)
        tokens = found["tokens"]
        assert found["seen"], found
        allowed = {"color": {tokens["text"], tokens["dim"]}, "background": {"rgba(0, 0, 0, 0)"},
                   "border": {tokens["border"]}}
        wrong = [(kind, value) for kind, value in found["seen"] if value not in allowed[kind]]
        assert wrong == [], (wrong, tokens)
        return [shown(page)]

    on_both(walk, viewport={"width": 360, "height": 800})


# --- one install ----------------------------------------------------------------------------


@pytest.fixture
def small(installs):
    return installs["small"]


class Held:
    """A route handler that holds each request until `release(answer)`, which answers
    those held and every one after with `answer` (by default, the page server's own)."""

    def __init__(self) -> None:
        self.held: list = []
        self.answer = None

    def __call__(self, route) -> None:
        if self.answer is None:
            self.held.append(route)
        else:
            self.answer(route)

    def release(self, answer=lambda route: route.continue_()) -> None:
        self.answer = answer
        for route in self.held:
            answer(route)
        self.held = []


def foot_then(panel, text: str) -> None:
    """The page's last line `text` is drawn after the foot, in the text colour."""
    line = panel.locator('[data-line="last"]').filter(has_text=text)
    expect(line).to_be_visible(timeout=10_000)
    assert panel.evaluate("""(el, text) => {
      const root = el.shadowRoot;
      const foot = root.querySelector('[data-part="foot"]');
      const line = [...root.querySelectorAll('[data-line="last"]')].find((one) => one.textContent.includes(text));
      return foot !== null && (foot.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
    }""", text)


def colour_of(locator, token: str) -> bool:
    return locator.evaluate("""(el, token) => {
      const probe = document.createElement("span");
      probe.style.color = `var(${token})`;
      el.parentNode.append(probe);
      const want = getComputedStyle(probe).color;
      probe.remove();
      return getComputedStyle(el).color === want;
    }""", token)


def test_the_page_says_reading_until_the_records_answer(small, ui_contexts, moment):
    lay(small, moment, record(NAMED, now=moment, age=600), reading=FRESH_READING)
    page = ui_contexts(small).pages[0]
    held = Held()
    page.route("**" + page_kit.CLOCKS, held)
    page.goto("/m/cache/")
    panel = page.locator(PANEL)
    waiting = panel.locator('[data-line="waiting"]')
    expect(waiting).to_have_text("Reading…")
    assert colour_of(waiting, "--moxi-text-dim")
    expect(rows(panel)).to_have_count(0)
    held.release()
    expect(rows(panel)).to_have_count(1)
    expect(waiting).to_have_count(0)


def test_a_404_draws_nothing(small, ui_contexts, moment):
    lay(small, moment, record(NAMED, now=moment, age=600), reading=FRESH_READING)
    page = ui_contexts(small).pages[0]
    held = Held()
    page.route("**" + page_kit.CLOCKS, held)
    page.goto("/m/cache/")
    panel = page.locator(PANEL)
    expect(panel.locator('[data-line="waiting"]')).to_be_visible()
    held.release(lambda route: route.fulfill(status=404, json={"error": "gone"}))
    expect(panel.locator('[data-line="waiting"]')).to_have_count(0)
    expect(panel.get_by_text(re.compile(r"\S"))).to_have_count(0)
    assert panel.evaluate("el => el.shadowRoot.querySelectorAll('*').length") == 0


def test_rows_are_drawn_before_their_names_come_and_named_when_they_do(small, ui_contexts, moment):
    lay(small, moment, record(NAMED, now=moment, age=600), record(HEADED, now=moment, age=1200),
        reading=FRESH_READING)
    unnamed = dict.fromkeys((NAMED, HEADED), named_as(small, STRANGER)[STRANGER])
    names = named_as(small, NAMED, HEADED)
    page = ui_contexts(small).pages[0]
    held = Held()
    page.route("**/m/cache/api/names", held)
    panel = open_page(page, moment)
    # The groups count every session at once; the rows come within two seconds, named
    # as a session with no name read.
    expect(group(panel, "warm").locator('[data-line="label"]')).to_have_text("2 warm")
    expect(rows(panel)).to_have_count(2)
    named_by_rule(panel, unnamed)
    held.release()
    named_by_rule(panel, names)


def test_names_that_could_not_be_read_are_one_dim_line_after_the_foot(small, ui_contexts, moment):
    lay(small, moment, record(NAMED, now=moment, age=600), reading=FRESH_READING)
    page = ui_contexts(small).pages[0]
    page.route("**/m/cache/api/names", lambda route: route.fulfill(status=500, json={"error": "no"}))
    panel = open_page(page, moment)
    foot_then(panel, NAMES_FAILED)
    assert colour_of(panel.locator('[data-line="last"]').filter(has_text=NAMES_FAILED), "--moxi-text-dim")
    named_by_rule(panel, {NAMED: named_as(small, STRANGER)[STRANGER]})


def test_a_failed_read_and_the_servers_error_go_after_the_foot_and_keep_the_rows(small, ui_contexts, moment):
    lay(small, moment, record(NAMED, now=moment, age=600), record(HEADED, now=moment, age=1200),
        reading=FRESH_READING)
    page = ui_contexts(small).pages[0]
    reads = []

    def handle(route) -> None:
        reads.append(route)
        if len(reads) > 1:
            route.fulfill(status=500, json={"error": "no"})
            return
        response = route.fetch()
        route.fulfill(response=response, json={**response.json(), "now": moment})

    page.route("**" + page_kit.CLOCKS, handle)
    page.goto("/m/cache/")
    panel = page.locator(PANEL)
    expect(rows(panel)).to_have_count(2)
    page_kit.show_tab(page)
    failed = "The clock records can't be read: cache's page server answered 500."
    foot_then(panel, failed)
    assert colour_of(panel.locator('[data-line="last"]').filter(has_text=failed), "--moxi-text")
    expect(rows(panel)).to_have_count(2)
    page.unroute("**" + page_kit.CLOCKS)

    # The page server's own error, by the topic: a record holding a number JSON can't
    # carry makes its read fail.
    other = ui_contexts(small).pages[0]
    panel = open_page(other, moment)
    expect(rows(panel)).to_have_count(2)
    put(small, record(STRANGER, now=moment, ttl=float("inf")))
    error = ("cache's page server hit an error: cache's page server hit an unexpected error (ValueError); "
             "its log has the details")
    foot_then(panel, error)
    assert colour_of(panel.locator('[data-line="last"]').filter(has_text=error), "--moxi-text")
    expect(rows(panel)).to_have_count(2)
    page_kit.clear(small)
