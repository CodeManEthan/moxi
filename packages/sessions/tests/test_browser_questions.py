"""The four questions sessions' page is there to answer, each answered from the page the
way a person would, in a browser, through a real shell that starts sessions' real page
server:

1. What was I working in during the last hour?
2. Which session was it, and where did it stop?
3. Where is the one from last week?
4. How do I pick it up again?

Each walk starts at the page's own address and reads only what the page shows. Each runs
twice: on a small install, which holds the core, ui and sessions and nothing else, and on
a full one, which holds every module of this environment. The two shells read the same
records, written with `moxi_sessions.testing` under each shell's own home, and a walk
keeps what the page showed at each step: the two installs must show the same, piece for
piece, and the page must place no element that is not its own.

The records look like a few weeks of use: three folders; named and unnamed sessions;
sessions with a typed message and without; sessions a program started; subagents; a day
with more sessions than a day shows at first; sessions older than the days the list
opens with; and Codex and OpenCode sessions with the facts those harnesses record. Every
session is timed from the moment the test runs, a past day's at that day's local noon.

What each harness's own pieces look like one by one is in the other browser files; these
are the walks through them.
"""

from __future__ import annotations

import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
from moxi import modules
from playwright.sync_api import expect

from moxi_sessions.testing import ClaudeTranscript, CodexRollout, OpenCodeDb
from test_browser_list import (changed_at, count_line, date_words, day_key, day_label, env_of, filter_button, group,
                               keys, noon, open_list, row, sid, tid)
from test_browser_opened import open_row, opened

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="sessions' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

PANEL = "#moxi-panel > moxi-sessions-page"
#: The modules a small install holds beside the core.
SMALL = ("sessions", "ui")

#: How many sessions the records hold, and how many of them are older than the seven days
#: the list opens with.
ALL = 29
EARLIER = 4
#: How many days back the days the list opens with are, and the days `Earlier` holds.
DAYS = (0, 1, 2, 3, 4, 5, 6)
OLDER_DAYS = (10, 12, 30)

# The sessions, by what the walks know them as.
TOOLTIPS, PARSER, SCRATCH, NIGHTLY = 1, 2, 3, 4
CHANGELOG, QUIET = 10, 11
YESTERDAY_PROGRAMS = (12, 13, 14)
RELEASE = 20
BUSY_DAY = tuple(range(20, 30))
OLDER, OLDEST, OLD_PROGRAM = 40, 41, 42
CODEX, CODEX_CHILD, CODEX_OLD = 1, 2, 3

TOOLTIPS_PROMPT = "Why does the tooltip flicker on hover?"
TOOLTIPS_REPLY = "Fixed: the hide timer is cleared when the pointer comes back."
PARSER_PROMPT = "Bump the parser and run the suite."
PARSER_REPLY = "Bumped; 212 tests pass."
SCRATCH_REPLY = "Sorted the three lists."
RELEASE_PROMPT = "Write the release notes for 0.4."
RELEASE_REPLY = "Drafted; two entries need a second look."
CODEX_REPLY = "Moved the two helpers and ran the checks."
OPENCODE_REPLY = "Renamed it in four files."

#: Words of the pieces today's records carry no fact for. None may show anywhere.
LEFT_OUT = re.compile(r"\b(running|working|busy|warm|cold|waiting|slot|held|turn \d|prompts|effort|Answer|"
                      r"Project|Comms|Cache|Cost|by hand)\b|\$")

#: What a page shows, top to bottom: each run of text, each control, each element with a
#: name of its own, and each row with the facts the list gave it.
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
    if (tag === "button" || tag === "a" || tag === "input") out.push(`[${tag}${node.placeholder ? " " + node.placeholder : ""}]`);
    for (const child of (node.shadowRoot ?? node).childNodes) walk(child);
  };
  walk(el);
  return out;
}"""


def others() -> list[str]:
    """Every installed module a small install leaves out."""
    return [found.name for found in modules.scan() if found.name not in SMALL]


def day_word(at: float) -> str:
    """`today` or `yesterday`, as the page says the day of a moment of the last hour."""
    return "today" if datetime.fromtimestamp(at).date() == datetime.now().date() else "yesterday"


def claude(root: Path, home: str, n: int, at: float, *, folder: str = "code/proj-a", name: str | None = None,
           typed: str | None = None, reply: str | None = "Done.", program: bool = False,
           subagents: tuple[tuple[str, str, str], ...] = (), took: float = 600) -> None:
    """A Claude Code session in `folder` under the home folder, last changed at `at`:
    started in a terminal or by a program, with a typed message only when `typed` gives
    one, and one subagent per `(kind, task, what it said last)`."""
    t = ClaudeTranscript(root, session_id=sid(n), cwd=f"{home}/{folder}",
                         project="-home-" + folder.replace("/", "-"), entrypoint="sdk-cli" if program else "cli",
                         start=at - took)
    if name is not None:
        t.title(name)
    t.title("a title the harness made", ai=True)
    if typed is not None:
        t.user(typed, origin="human")
    else:
        t.user("A message no person typed.")
    for k, (kind, task, said) in enumerate(subagents):
        sub = t.subagent(f"a0190b000000000{k + 1}", agent_type=kind, description=task)
        sub.user("look")
        sub.assistant(said)
    if reply is not None:
        t.assistant(reply)
    changed_at(t.write(), at)


def codex(root: Path, home: str, n: int, at: float, *, folder: str, reply: str, child: int | None = None) -> None:
    """A Codex thread last changed at `at`, with a thread it spawned when `child` names
    one."""
    rollout = CodexRollout(root, thread_id=tid(n), cwd=f"{home}/{folder}", start=at - 600)
    rollout.turn()
    rollout.user("Move the two helpers.")
    if child is not None:
        spawned = rollout.spawn(tid(child), "survey")
        spawned.turn()
        spawned.user("Survey the callers.")
        spawned.assistant("Four callers, all in one file.")
        changed_at(spawned.write(), at - 60)
    rollout.assistant(reply)
    rollout.turn_end()
    changed_at(rollout.write(), at)


def seed(env: dict[str, str], now: float) -> str:
    """Writes the records under the roots `env` names, timed from `now`. Returns the
    OpenCode session's id.

    * The last hour: a named session with a typed message, a reply and two subagents;
      an unnamed one with a typed message; one in which nobody typed; and one a program
      started two minutes ago.
    * Yesterday: two started in a terminal, three by a program, and a Codex thread that
      spawned another.
    * Two, four and five days back: a session or two each. Three days back: an OpenCode
      session.
    * Six days back: ten sessions, the oldest of them the named one the third walk looks
      for.
    * Older than the seven days the list opens with: ten, twelve and thirty days back,
      one of them Codex's, and one a program started.
    """
    home = env["HOME"]
    root = Path(env["CLAUDE_CONFIG_DIR"]) / "projects"
    claude(root, home, TOOLTIPS, now - 300, name="fix-tooltips", typed=TOOLTIPS_PROMPT, reply=TOOLTIPS_REPLY,
           took=2100, subagents=(("Explore", "Read the tooltip code", "The hide timer is never cleared."),
                                 ("general-purpose", "List the callers", "Found three callers.")))
    claude(root, home, PARSER, now - 1500, folder="code/proj-b", typed=PARSER_PROMPT, reply=PARSER_REPLY)
    claude(root, home, SCRATCH, now - 2700, folder="scratch", reply=SCRATCH_REPLY)
    claude(root, home, NIGHTLY, now - 120, program=True, took=60)

    claude(root, home, CHANGELOG, noon(1).timestamp() + 1800, typed="Draft the changelog entry.")
    claude(root, home, QUIET, noon(1).timestamp(), folder="code/proj-b")
    for k, n in enumerate(YESTERDAY_PROGRAMS):
        claude(root, home, n, noon(1).timestamp() - 3600 * (k + 1), program=True, took=60)
    rollouts = Path(env["CODEX_HOME"]) / "sessions"
    codex(rollouts, home, CODEX, noon(1).timestamp() + 900, folder="code/proj-b", reply=CODEX_REPLY, child=CODEX_CHILD)

    claude(root, home, 15, noon(2).timestamp(), typed="Split the settings form.")
    claude(root, home, 16, noon(4).timestamp(), folder="scratch")
    claude(root, home, 17, noon(4).timestamp() + 1800, folder="code/proj-b", typed="Why is the build slow?")
    claude(root, home, 18, noon(5).timestamp(), typed="Add the missing index.")

    db = OpenCodeDb(Path(env["XDG_DATA_HOME"]) / "opencode", start=noon(3).timestamp() - 600)
    s = db.session(title="a title the harness made", directory=f"{home}/scratch")
    s.user("Rename the flag.")
    a = s.assistant()
    a.step(text=OPENCODE_REPLY, input=900, output=40)
    a.complete()
    db.write()

    claude(root, home, RELEASE, noon(6).timestamp(), folder="code/proj-b", name="release-notes",
           typed=RELEASE_PROMPT, reply=RELEASE_REPLY,
           subagents=(("Explore", "Read the merged changes", "Fourteen changes since 0.3."),))
    for k, n in enumerate(BUSY_DAY[1:], start=1):
        claude(root, home, n, noon(6).timestamp() + 600 * k, typed=f"Task {k}.")

    claude(root, home, OLDER, noon(10).timestamp(), folder="code/proj-b", typed="Try the new cache key.")
    codex(rollouts, home, CODEX_OLD, noon(12).timestamp(), folder="scratch", reply="Done.")
    claude(root, home, OLD_PROGRAM, noon(12).timestamp() - 3600, folder="code/proj-b", program=True, took=60)
    claude(root, home, OLDEST, noon(30).timestamp())
    return s.id


@pytest.fixture(scope="module")
def moment() -> float:
    """The moment every record of both installs is timed from."""
    return time.time()


@pytest.fixture(scope="module")
def installs(ui_shells, tmp_path_factory, moment) -> dict:
    """The two shells, each over the same records under a home of its own: `small` leaves
    out every module but ui and sessions, `full` leaves out none."""
    made = {}
    for name, leave_out in (("small", others()), ("full", [])):
        shell = ui_shells(leave_out=leave_out, **env_of(tmp_path_factory.mktemp(name)))
        shell.opencode = seed(shell.env, moment)
        made[name] = shell
    assert made["small"].opencode == made["full"].opencode
    return made


@pytest.fixture
def on_both(installs, ui_contexts):
    """`on_both(walk, **context_args)` runs `walk(page, shell)` on the small install and
    on the full one, each in a context of its own. A walk returns what the page showed at
    each of its steps; the two must be the same, and hold no element that is not sessions'
    own or the row. Returns what was shown."""
    def run(walk, **context_args) -> list:
        seen = {}
        for name, shell in installs.items():
            page = ui_contexts(shell, **context_args).pages[0]
            try:
                seen[name] = walk(page, shell)
            except AssertionError as failed:
                failed.add_note(f"on the {name} install")
                raise
        assert seen["small"] == seen["full"]
        for step in seen["full"]:
            placed = {line for line in step if line.startswith("<")}
            assert placed <= {"<moxi-sessions-page>", "<moxi-sessions-list>", "<moxi-ui-row>"}, placed
            said = [line for line in step if not line.startswith("row ") and LEFT_OUT.search(line)]
            assert said == []
        return seen["full"]

    return run


def shown(page) -> list[str]:
    """What sessions' page shows now, top to bottom."""
    return page.locator(PANEL).evaluate(SHOWN)


def labels(view):
    return view.locator('section [data-line="label"]')


def label(days_ago: int) -> str:
    """A day's label in the list."""
    return ("Today", "Yesterday")[days_ago] if days_ago < 2 else day_label(days_ago)


def lines(det) -> list[str]:
    """The pieces an opened row shows, in order."""
    return det.locator(".r").evaluate_all("all => all.map((one) => one.dataset.line)")


def choose(view, key: str, name: str) -> None:
    """Pick `name` in the menu of the filter `key`."""
    filter_button(view, key).click()
    view.locator(f'[data-menu="{key}"]').get_by_role("menuitemradio", name=name).click()
    expect(view.locator(f'[data-menu="{key}"]')).to_have_count(0)


def clear(view) -> None:
    view.get_by_role("button", name="Clear").click()
    expect(count_line(view)).to_have_text(f"{ALL} sessions")


def test_the_small_install_holds_ui_and_sessions_and_the_full_one_every_module(installs, ui_contexts):
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


def test_the_last_hours_work_is_under_active_recently_with_no_running_group_or_live_state(on_both, moment):
    def walk(page, shell) -> list:
        view = open_list(page)
        expect(count_line(view)).to_have_text(f"{ALL} sessions")
        # The first group is the last hour's, newest first.
        expect(labels(view).first).to_have_text("Active recently")
        recent = group(view, "recent")
        assert keys(recent.locator("moxi-ui-row"), 3) == [f"claude/{sid(n)}" for n in (TOOLTIPS, PARSER, SCRATCH)]

        # A named session: its name, its folder, and what was typed last.
        named = row(view, "claude", sid(TOOLTIPS))
        expect(named.locator(".name")).to_have_text("fix-tooltips")
        expect(named.locator(".where")).to_have_text("proj-a")
        expect(named.locator(".l2")).to_have_text(
            re.compile(r"^\s*2 subagents\s*·\s*\d.*You:\s*" + re.escape(TOOLTIPS_PROMPT) + r"\s*$"))
        # An unnamed one is told by its folder and when it started.
        unnamed = row(view, "claude", sid(PARSER))
        expect(unnamed.locator(".name")).to_have_text(
            re.compile(rf"^proj-b · started {day_word(moment - 2100)} \d"))
        expect(unnamed.locator(".l2")).to_have_text(re.compile(r"You:\s*" + re.escape(PARSER_PROMPT)))
        # One nobody typed in is its name alone: no reply stands in for a typed message.
        quiet = row(view, "claude", sid(SCRATCH))
        expect(quiet.locator(".name")).to_have_text(re.compile(r"^scratch · started "))
        expect(quiet.locator(".l2")).to_be_hidden()
        expect(view.get_by_text(SCRATCH_REPLY)).to_have_count(0)

        # What a program started in the last hour is not this group's: it is counted
        # under its own day.
        expect(row(view, "claude", sid(NIGHTLY))).to_have_count(0)
        today = group(view, datetime.fromtimestamp(moment - 120).strftime("%Y-%m-%d"))
        expect(today.locator('[data-line="program"] span')).to_have_text("1 started by a program")

        # Nothing says what runs now: no group but the last hour's, the days and
        # `Earlier`, and no fact at a row's right end.
        named_groups = view.locator("section").evaluate_all("all => all.map((one) => one.dataset.group)")
        assert all(re.fullmatch(r"recent|earlier|\d{4}-\d\d-\d\d", key) for key in named_groups), named_groups
        expect(view.locator("moxi-ui-row[fact]")).to_have_count(0)
        expect(recent.locator("moxi-ui-row [slot='acts']")).to_have_text(["Open", "Open"])
        return [shown(page)]

    on_both(walk)


def test_an_opened_row_says_which_session_it_was_and_where_it_stopped_without_the_open_turn(on_both, installs):
    def walk(page, shell) -> list:
        view = open_list(page)
        # Two rows of the last hour, opened side by side to tell them apart.
        _, first = open_row(view, "claude", sid(TOOLTIPS))
        assert lines(first) == ["you", "reply", "subagents", "session", "resume"]
        expect(first.locator('[data-line="you"]')).to_have_text(
            re.compile(r"^\s*You\s*\d.*" + re.escape(TOOLTIPS_PROMPT) + r"\s*$"))
        expect(first.locator('[data-line="reply"]')).to_have_text(
            re.compile(r"^\s*Reply\s*\d.*" + re.escape(TOOLTIPS_REPLY) + r"\s*$"))
        subs = first.locator('[data-line="subagent"]')
        expect(subs).to_have_text([re.compile(r"List the callers.*general-purpose.*Found three callers\."),
                                   re.compile(r"Read the tooltip code.*Explore.*The hide timer is never cleared\.")])
        # The `Session` line is the harness, the model and the span, and no more.
        expect(first.locator('[data-line="session"]')).to_have_text(
            re.compile(r"^\s*Session\s*claude · [^·]+ · [^·]+ to [^·]+$"))

        _, second = open_row(view, "claude", sid(SCRATCH))
        assert lines(second) == ["reply", "session", "resume"]
        expect(second.locator('[data-line="reply"]')).to_contain_text(SCRATCH_REPLY)
        expect(view.locator("moxi-ui-row[open]")).to_have_count(2)
        step_one = shown(page)

        # A Codex thread and an OpenCode session say where they stopped with what their
        # records carry: the reply, and the thread Codex spawned.
        _, thread = open_row(view, "codex", tid(CODEX))
        assert lines(thread) == ["reply", "subagents", "session", "resume"]
        expect(thread.locator('[data-line="reply"]')).to_contain_text(CODEX_REPLY)
        expect(thread.locator('[data-line="subagent"]')).to_have_text(re.compile(r"Four callers, all in one file\."))
        expect(thread.locator('[data-line="session"]')).to_have_text(re.compile(r"^\s*Session\s*codex · "))
        _, other = open_row(view, "opencode", shell.opencode)
        assert lines(other) == ["reply", "session", "resume"]
        expect(other.locator('[data-line="reply"]')).to_contain_text(OPENCODE_REPLY)
        expect(other.locator('[data-line="session"]')).to_have_text(re.compile(r"^\s*Session\s*opencode · "))

        # Each opened row ends with the same two acts, and none offers more.
        for det in (first, second, thread, other):
            expect(det.locator(".foot > *")).to_have_text(["Read the transcript", "Copy id"])
            expect(det.get_by_role("button")).to_have_text(["Copy", "Copy id"])
        return [step_one, shown(page)]

    on_both(walk)


def test_last_weeks_session_is_found_by_day_filter_and_id_but_not_by_its_name_or_a_project(on_both):
    def walk(page, shell) -> list:
        steps = []
        view = open_list(page)
        expect(count_line(view)).to_have_text(f"{ALL} sessions")

        # By day: the days run newest first under the last hour's group, then `Earlier`.
        expect(labels(view)).to_have_text(["Active recently", *map(label, DAYS), "Earlier"])
        # Six days back was a busy day: it shows its newest eight, and `Show` the rest,
        # where the named session is.
        day = group(view, day_key(6))
        target = row(view, "claude", sid(RELEASE))
        assert keys(day.locator("moxi-ui-row"), 8) == [f"claude/{sid(n)}" for n in reversed(BUSY_DAY[2:])]
        expect(day.locator('[data-line="more"] span')).to_have_text("2 more")
        day.locator('[data-line="more"]').get_by_role("button", name="Show").click()
        expect(day.locator("moxi-ui-row")).to_have_count(10)
        expect(target.locator(".name")).to_have_text("release-notes")
        expect(target.locator(".where")).to_have_text("proj-b")
        expect(target.locator(".l2")).to_have_text(
            re.compile(r"^\s*1 subagent\s*·.*You:\s*" + re.escape(RELEASE_PROMPT) + r"\s*$"))
        steps.append(shown(page))

        # What programs started yesterday is one line under the day, opened and shut.
        yesterday = group(view, day_key(1))
        assert keys(yesterday.locator("moxi-ui-row"), 3) == [f"claude/{sid(CHANGELOG)}", f"codex/{tid(CODEX)}",
                                                             f"claude/{sid(QUIET)}"]
        line = yesterday.locator('[data-line="program"]')
        expect(line.locator("span")).to_have_text("3 started by a program")
        line.get_by_role("button", name="Show").click()
        assert keys(yesterday.locator("moxi-ui-row"), 6)[3:] == [f"claude/{sid(n)}" for n in YESTERDAY_PROGRAMS]
        steps.append(shown(page))
        line.get_by_role("button", name="Hide").click()
        expect(yesterday.locator("moxi-ui-row")).to_have_count(3)

        # Older than that: `Earlier` says how many and how far back, and brings its days.
        earlier = group(view, "earlier")
        expect(earlier.locator('[data-line="earlier"] span')).to_have_text(
            f"{EARLIER} sessions, back to {date_words(30)}")
        earlier.get_by_role("button", name="Show").click()
        expect(labels(view)).to_have_text(["Active recently", *map(label, DAYS + OLDER_DAYS)])
        expect(row(view, "codex", tid(CODEX_OLD)).locator(".name")).to_have_text(
            re.compile(rf"^scratch · started {re.escape(date_words(12))} "))
        expect(group(view, day_key(12)).locator('[data-line="program"] span')).to_have_text("1 started by a program")
        steps.append(shown(page))

        # By filter, each saying how many of all it leaves.
        choose(view, "folder", "proj-b")
        expect(count_line(view)).to_have_text(f"7 of {ALL} sessions")
        expect(target).to_be_visible()
        steps.append(shown(page))
        choose(view, "harness", "Codex")
        expect(count_line(view)).to_have_text(f"1 of {ALL} sessions")
        assert keys(view.locator("moxi-ui-row"), 1) == [f"codex/{tid(CODEX)}"]
        clear(view)
        choose(view, "harness", "OpenCode")
        assert keys(view.locator("moxi-ui-row"), 1) == [f"opencode/{shell.opencode}"]
        expect(labels(view)).to_have_text([day_label(3)])
        clear(view)
        choose(view, "when", "Last 7 days")
        expect(count_line(view)).to_have_text(f"{ALL - EARLIER} of {ALL} sessions")
        expect(group(view, "earlier")).to_have_count(0)
        steps.append(shown(page))
        clear(view)
        # There are three filters, and none is by project.
        expect(view.locator("[data-filter]")).to_have_count(3)
        expect(view.locator('[data-part="filters"]')).to_have_text(
            re.compile(rf"^\s*Folder\s*all.*Harness\s*all.*When\s*any time.*{ALL} sessions\s*$", re.S))

        # By the find field: a folder's name, then an id; a session a program started
        # weeks ago is found by its id too.
        field = view.get_by_label("Find a session")
        expect(field).to_have_attribute("placeholder", "Find a session by its id or folder")
        field.fill("scratch")
        expect(count_line(view)).to_have_text(f"4 of {ALL} sessions")
        field.fill(sid(RELEASE))
        expect(count_line(view)).to_have_text(f"1 of {ALL} sessions")
        found = view.locator("moxi-ui-row")
        expect(found).to_have_attribute("data-key", f"claude/{sid(RELEASE)}")
        expect(found.locator(".name")).to_have_text("release-notes")
        expect(found).to_have_attribute("time", re.compile(re.escape(date_words(6))))
        steps.append(shown(page))
        field.fill(sid(OLD_PROGRAM))
        expect(found).to_have_attribute("data-key", f"claude/{sid(OLD_PROGRAM)}")
        expect(found).to_have_attribute("name", re.compile(rf"^proj-b · started {re.escape(date_words(12))} "))
        steps.append(shown(page))
        # A session's name and what was typed in it are not looked through.
        for words in ("release-notes", "release notes for"):
            field.fill(words)
            expect(count_line(view)).to_have_text(f"0 of {ALL} sessions")
            expect(view.locator('[data-line="empty"]')).to_have_text("No sessions match.")
        steps.append(shown(page))
        return steps

    on_both(walk)


def test_a_session_is_picked_up_by_its_copied_resume_command_or_read_in_full_with_no_resume_act(
        on_both, ui_browser, moment):
    clipboard = ui_browser.browser_type.name == "chromium"

    def walk(page, shell) -> list:
        steps = []
        view = open_list(page)
        home = "~"
        for harness, session, command, where in (
                ("claude", sid(TOOLTIPS), f"claude --resume {sid(TOOLTIPS)}", f"run it in {home}/code/proj-a"),
                ("codex", tid(CODEX), f"codex resume {tid(CODEX)}", None),
                ("opencode", shell.opencode, f"opencode --session {shell.opencode}", f"run it in {home}/scratch")):
            _, det = open_row(view, harness, session)
            resume = det.locator('[data-line="resume"]')
            expect(resume.locator("code")).to_have_text(command)
            expect(resume.locator('[data-line="where"]')).to_have_text([] if where is None else [where])
            copy = resume.get_by_role("button")
            expect(copy).to_have_text("Copy")
            if clipboard:
                copy.click()
                expect(copy).to_have_text("Copied")
                assert page.evaluate("navigator.clipboard.readText()") == command
                expect(copy).to_have_text("Copy")
        steps.append(shown(page))

        # Or it is read in full first: the transcript, under the name the list gave it.
        det = opened(row(view, "claude", sid(TOOLTIPS)))
        det.get_by_role("link", name="Read the transcript").click()
        expect(page).to_have_url(re.compile(rf"/m/sessions/claude/{sid(TOOLTIPS)}$"))
        panel = page.locator(PANEL)
        head = panel.locator('[data-part="head"]')
        expect(head.locator('[data-line="name"]')).to_have_text("fix-tooltips")
        expect(head.locator('[data-line="where"]')).to_have_text("proj-a")
        expect(head.locator('[data-line="facts"]')).to_have_text(
            re.compile(rf"^claude · [^·]+ · {day_word(moment - 2400)} \d[^·]* to [^·]+$"))
        window = panel.locator('[data-part="window"]')
        expect(window).to_contain_text(TOOLTIPS_PROMPT)
        expect(window).to_contain_text(TOOLTIPS_REPLY)
        expect(panel.locator('[data-part="subagents"]')).to_contain_text("Read the tooltip code")
        # The header's acts are the way back, `Refresh` and `Details`.
        expect(head.get_by_role("button")).to_have_text(["Refresh", "Details"])
        steps.append(shown(page))

        # And back, to the list from its top.
        head.get_by_role("link", name="Sessions").click()
        expect(page).to_have_url(re.compile(r"/m/sessions/$"))
        view = page.locator(f"{PANEL} moxi-sessions-list")
        expect(count_line(view)).to_have_text(f"{ALL} sessions")
        expect(view.locator("moxi-ui-row[open]")).to_have_count(0)
        steps.append(shown(page))
        return steps

    seen = on_both(walk, **({"permissions": ["clipboard-read", "clipboard-write"]} if clipboard else {}))
    for step in seen:
        assert not any(re.search(r"\bResume\b", line) and line != "Resume" for line in step)
        assert step.count("Resume") == sum(1 for line in step if line.startswith(("claude --resume ", "codex resume ",
                                                                                 "opencode --session ")))
