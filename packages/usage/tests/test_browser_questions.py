"""The eight questions usage's page is there to answer, each answered from the page the
way a person would, in a browser, through a real shell that starts usage's real page
server:

1. Will a Claude limit stop me before it resets?
2. And Codex? (with why its opened row has no fill time)
3. What did today and this week cost?
4. Where did the last 30 days go? (a row filtering Sessions; the Harness and Model filters)
5. Which sessions cost most? (names, `Show`, `Earlier`, a session opened, its subagents
   shown and one opened)
6. Is caching paying off?
7. What is eating the 5-hour window? (a new reading read again)
8. How fresh is this?

Each walk starts at the page's own address and reads only what the page shows. Each runs
twice: on a small install, whose shell leaves out every module but ui, usage and
sessions, and on a full one, which holds every module of this environment. Leaving a
module out hides it from the shell only, and the page server still finds it, so what
differs between the two runs is what the browser decides: without projects the page
groups by folder (`Folder`, `Folders`, no project beside a row, no `no project` row).
Both installs read the same records, written with `moxi_sessions.testing` under each
shell's own home and timed from one moment, with the same projects added and the same
plan readings stored; a walk keeps what the page showed at each step, less what differs
by design, and the two runs must have shown the same.

The records: fourteen sessions this week, the dearest with a typed name, two subagents
and two others inside Claude's 5-hour window; a Codex thread among them on its own model,
and one Claude session on another; two sessions earlier in the month; three folders
under the home, two of them projects. Claude's 5-hour window rises towards its limit
before it resets; Codex's windows have one reading each. A walk that adds a reading or a
session runs on installs of its own, so the walks hold in any order and under
pytest-xdist.
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
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(240),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="usage's page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

PANEL = "#moxi-panel > moxi-usage-page"
#: The modules a small install holds beside the core.
SMALL = ("sessions", "ui", "usage")

SONNET = "claude-sonnet-4-5"
HAIKU = "claude-haiku-4-5"
CODEX_MODEL = "gpt-5-codex"
PROJECTS = ("proj-a", "proj-b")
FOLDERS = ("proj-a", "proj-b", "scratch")
TYPED = "fix the tests"
AGENTS = ("a0190b0000000101", "a0190b0000000102")


def session_id(n: int) -> str:
    return f"0190b000-0000-7000-8000-0000000002{n:02d}"


THREAD = "0190c000-0000-7000-8000-000000000201"
#: Seconds before the moment each session starts.
HOUR, DAY = 3600, 86400


def _week(n: int) -> dict:
    """The week's n-th dearest Claude session: fewer requests the further down."""
    return {"harness": "claude", "session": session_id(n), "requests": 16 - n,
            "folder": "proj-a" if n == 0 else "proj-b" if n == 1 else FOLDERS[n % 3],
            "ago": 40 * 60 if n == 0 else 80 * 60 if n == 1 else 3 * HOUR + 5 * HOUR * n,
            "model": HAIKU if n == 12 else SONNET, "title": TYPED if n == 0 else None,
            "agents": list(AGENTS) if n == 0 else []}


#: The sessions, dearest first within the week: the typed one and an unnamed one inside
#: the 5-hour window, eleven more over the last three days, the last of them on Haiku;
#: a Codex thread three days ago; two Claude sessions twelve and twenty days ago.
WEEK = [_week(n) for n in range(13)]
CODEX = {"harness": "codex", "session": THREAD, "requests": 3, "folder": "proj-b", "ago": 3 * DAY,
         "model": CODEX_MODEL, "title": None, "agents": []}
EARLIER = [{"harness": "claude", "session": session_id(20 + k), "requests": 3 - k, "folder": folder,
            "ago": days * DAY, "model": SONNET, "title": None, "agents": []}
           for k, (folder, days) in enumerate((("proj-b", 12), ("scratch", 20)))]
SESSIONS = WEEK + [CODEX] + EARLIER
#: A session that starts after the page has opened, for the walk that reads new sessions.
LATE = {"harness": "claude", "session": session_id(30), "requests": 2, "folder": "proj-a", "ago": 10 * 60,
        "model": SONNET, "title": None, "agents": []}

#: Writes the sessions of the JSON list in argv[1] under the harness roots of the
#: environment it runs in, each in its folder under argv[3], timed from the moment argv[2].
SEED = """
import json, sys
from pathlib import Path
from moxi_sessions import api as sessions
T = sessions.testing
specs, moment, work = json.loads(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
for spec in specs:
    cwd = work + "/" + spec["folder"]
    start = moment - spec["ago"]
    if spec["harness"] == "codex":
        r = T.CodexRollout(Path(sessions.root("codex")), thread_id=spec["session"], cwd=cwd, start=start)
        r.turn(spec["model"])
        r.user("Move the two helpers.")
        for m in range(spec["requests"]):
            r.request(T.token_usage(input=4000, cached=3000, output=800),
                      response_id=f"resp_{spec['session'][-3:]}{m}", limits=False)
        r.turn_end()
        r.write()
        continue
    t = T.ClaudeTranscript(Path(sessions.root("claude")), session_id=spec["session"],
                           project=cwd.replace("/", "-"), cwd=cwd, start=start)
    if spec["title"]:
        t.title(spec["title"])
    for m in range(spec["requests"]):
        t.user("Fix the failing test.")
        t.assistant("Done.", usage=T.usage(input=200, cache_read=6000, cache_write=1000, output=1000),
                    request_id=f"req_{spec['session'][-3:]}{m}", model=spec["model"])
    for k, agent in enumerate(spec["agents"]):
        child = t.subagent(agent, agent_type=("Explore", "general-purpose")[k])
        for m in range(2):
            child.user("Look into it.")
            child.assistant("Found it.", usage=T.usage(input=100, output=400 * (k + 1)),
                            request_id=f"req_{agent[-4:]}{m}", model=spec["model"])
    t.write()
"""

#: Stores plan readings where the environment it runs in keeps them, timed from the
#: moment argv[1]. Claude's 5-hour window opened two hours ago and its three readings,
#: from the unnamed session argv[2], rise to 52%: it fills before it resets in three
#: hours. Claude's weekly window has one reading. Codex's two windows have one reading
#: each, from the thread argv[3], so neither has a fill time.
READINGS = """
import sys
from moxi_usage import readings
from moxi_usage.readings import Reading
moment, claude, codex = float(sys.argv[1]), sys.argv[2], sys.argv[3]
found = [
    Reading("claude", "plan", 300, "five_hour", used, moment + 3 * 3600, moment - ago, "session-usage", claude)
    for used, ago in ((30.0, 1500), (40.0, 900), (52.0, 120))
] + [
    Reading("claude", "plan", 10080, "seven_day", 60.0, moment + 4 * 86400, moment - 120, "session-usage", claude),
    Reading("codex", "codex", 300, "primary", 34.0, moment + 2 * 3600, moment - 600, "rollout", codex),
    Reading("codex", "codex", 10080, "secondary", 41.0, moment + 5 * 86400, moment - 600, "rollout", codex),
]
conn = readings.open_store()
try:
    readings.store(conn, found, now=moment, days=7)
finally:
    conn.close()
"""

#: One more reading of Claude's 5-hour window, half a minute before the moment argv[1],
#: from the typed session argv[2]: 58% used.
NEW_READING = """
import sys
from moxi_usage import readings
from moxi_usage.readings import Reading
moment, session = float(sys.argv[1]), sys.argv[2]
conn = readings.open_store()
try:
    readings.store(conn, [Reading("claude", "plan", 300, "five_hour", 58.0, moment + 3 * 3600, moment - 30,
                                  "session-usage", session)], now=moment, days=7)
finally:
    conn.close()
"""

TIME = r"\d{1,2}:\d\d(?: [AP]M)?"
#: A time with the day it may carry: `21:34`, `today 21:34`, `Wed 23:58`, `Nov 3 08:52`.
WHEN = rf"(?:\w+ (?:\d+ )?)?{TIME}"
MONEY = r"(?:at least )?\$\d[\d,]*\.\d\d"
NAMED = rf"(?:today|yesterday|\w+|\w+ \d+) {TIME}"
FIVE, WEEKLY = "claude/plan/300", "claude/plan/10080"
CODEX_FIVE, CODEX_WEEKLY = "codex/codex/300", "codex/codex/10080"

#: What a part of the page shows, top to bottom: each run of text, each control, each
#: element with a name of its own, and each row with the facts it was given but its key,
#: and its `where`, which differs between the installs by design and is checked apart.
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
        if (!["class", "style", "data-key", "where"].includes(name)) facts[name] = node.getAttribute(name);
      }
      out.push("row " + JSON.stringify(facts));
      for (const child of node.childNodes) walk(child);
      return;
    }
    if (tag.includes("-")) out.push(`<${tag}>`);
    if (tag === "button" || tag === "a" || tag === "input") out.push(`[${tag}]`);
    for (const child of (node.shadowRoot ?? node).childNodes) walk(child);
  };
  walk(el);
  return out;
}"""

#: What changes with the minute a run happens in, not with what the page was given: the
#: time of the last pass over the sessions, and how much of a window is gone.
MOVING = [(re.compile(rf"(?<=essions read ){TIME}"), "<time>"),
          (re.compile(r"(?:under 1|\d+)% of (it|the window) gone"), r"<share> of \1 gone")]


def others() -> list[str]:
    """Every installed module a small install leaves out."""
    return [found.name for found in modules.scan() if found.name not in SMALL]


def run(shell, *argv: str) -> str:
    done = subprocess.run(list(argv), env=shell.env, capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise AssertionError(f"{argv} failed ({done.returncode}):\n{done.stdout}{done.stderr}")
    return done.stdout


def work_of(shell) -> Path:
    return Path(shell.env["HOME"]) / "work"


def write(shell, specs: list[dict], moment: float) -> None:
    run(shell, sys.executable, "-c", SEED, json.dumps(specs), repr(moment), str(work_of(shell)))


def seeded(ui_shells, name: str, leave_out: list[str], moment: float):
    """One install over the records: the projects added, the sessions written and indexed,
    the readings stored."""
    shell = ui_shells(leave_out=leave_out)
    shell.small = bool(leave_out)
    shell.moment = moment
    for folder in FOLDERS:
        (work_of(shell) / folder).mkdir(parents=True, exist_ok=True)
    for key in PROJECTS:
        done = shell.moxi("projects", "add", key, "--root", str(work_of(shell) / key))
        assert done.returncode == 0, done.stdout + done.stderr
    write(shell, SESSIONS, moment)
    run(shell, sys.executable, "-m", "moxi", "usage", "index")
    run(shell, sys.executable, "-c", READINGS, repr(moment), session_id(1), THREAD)
    return shell


def two_installs(ui_shells) -> dict:
    """The small install and the full one, over the same records timed from one moment."""
    moment = time.time()
    return {name: seeded(ui_shells, name, leave_out, moment)
            for name, leave_out in (("small", others()), ("full", []))}


@pytest.fixture(scope="module")
def installs(ui_shells) -> dict:
    """The two installs the walks that change nothing share."""
    return two_installs(ui_shells)


@pytest.fixture
def on_both(ui_contexts):
    """`on_both(installs, walk)` runs `walk(page, shell)` on the small install and on the
    full one, each in a browser context of its own. A walk returns what the page showed at
    each of its steps; the two runs must have shown the same, and no step a session's id.
    Returns what was shown."""
    def go(made: dict, walk) -> list:
        seen = {}
        for name, shell in made.items():
            page = ui_contexts(shell).pages[0]
            try:
                seen[name] = walk(page, shell)
            except AssertionError as failed:
                failed.add_note(f"on the {name} install")
                raise
        assert seen["small"] == seen["full"]
        ids = [spec["session"] for spec in SESSIONS + [LATE]]
        for step in seen["full"]:
            assert not [line for line in step if any(found in line for found in ids)], step
        return seen["full"]

    return go


def shown(region) -> list[str]:
    """What a part of the page shows now, top to bottom, less what moves with the minute."""
    lines = region.evaluate_all(f"all => all.flatMap({SHOWN})")
    for pattern, put in MOVING:
        lines = [pattern.sub(put, line) for line in lines]
    return lines


def panel_of(page):
    return page.locator(PANEL)


def open_page(page, route: str = ""):
    page.goto(f"/m/usage/{route}")
    panel = panel_of(page)
    expect(panel.locator("[data-part=filters]")).to_be_visible()
    return panel


def group(panel, harness: str):
    return panel.locator(f'[data-group=limits][data-harness="{harness}"]')


def limit(panel, key: str):
    return panel.locator(f'[data-group=limits] [data-limit="{key}"]')


def opened(panel):
    return panel.locator("[data-part=opened]")


def section(panel, key: str):
    return panel.locator(f"[data-part=below] section[data-group={key}]")


def rows_of(sessions, which: str = "first"):
    return sessions.locator(f"[data-list={which}] moxi-ui-row")


def keys(rows) -> list[str]:
    return rows.evaluate_all("all => all.map((one) => one.dataset.key)")


def key(spec: dict) -> str:
    return f"{spec['harness']}/{spec['session']}"


def filter_of(panel, name: str):
    return panel.locator(f'[data-part=filters] [data-filter="{name}"]')


def pick(panel, name: str, entry: str) -> None:
    """Opens a filter's menu and picks the entry of that name."""
    filter_of(panel, name).locator("button.pick").click()
    filter_of(panel, name).get_by_role("menuitemradio", name=re.compile(rf"^{re.escape(entry)}\b")).click()
    expect(filter_of(panel, name).get_by_role("menu")).to_have_count(0)


def settle(panel, name: str, entry: str) -> None:
    """Picks `entry` in a filter's menu and waits until the filter line has every filter
    again: a filter whose list is read again under the new choice is absent until it
    answers, and a press meanwhile would land on the filter that moved into its place."""
    pick(panel, name, entry)
    expect(filter_of(panel, name).locator("button.pick")).to_have_text(f"{entry}▾")
    expect(panel.locator("[data-part=filters] [data-filter]")).to_have_count(4)


def place_words(shell) -> tuple[str, str]:
    """The filter's and the section's names on this install."""
    return ("Folder", "folders") if shell.small else ("Project", "projects")


def test_the_small_install_holds_ui_usage_and_sessions_and_the_full_one_every_module(installs, ui_contexts):
    held = {}
    for name, shell in installs.items():
        page = ui_contexts(shell).pages[0]
        page.goto("/m/ui/modules")
        cards = page.locator("#moxi-panel > moxi-ui-page [data-module]")
        expect(cards.first).to_be_visible()
        held[name] = sorted(set(cards.evaluate_all("all => all.map((one) => one.dataset.module)")))
    assert held["small"] == sorted(SMALL)
    assert set(held["full"]) == {found.name for found in modules.scan()}
    assert "projects" in held["full"]


# --- 1. Will a Claude limit stop me before it resets? -------------------------------------

def test_a_claude_limit_that_fills_before_it_resets_leads_the_page_and_its_row_says_when(installs, on_both):
    def walk(page, shell) -> list:
        panel = open_page(page)
        # The warning line comes first and says which limit fills, when, and how long
        # before it resets.
        warning = panel.locator("[data-part=warning]")
        expect(warning.locator("b")).to_have_text(re.compile(rf"^Claude's 5-hour limit fills (\w+ )?at {TIME}$"))
        expect(warning).to_have_text(re.compile(r"^Claude's 5-hour limit fills .+ before it resets$"))
        # Claude's group fills soonest, so it is first; its rows say how much is used.
        groups = panel.locator("[data-group=limits]")
        expect(groups).to_have_count(2)
        expect(groups.nth(0)).to_have_attribute("data-harness", "claude")
        expect(group(panel, "claude").locator(".gk")).to_have_text(re.compile(rf"^Claude\s*read {TIME}$"))
        five = limit(panel, FIVE)
        expect(five.locator("span").first).to_have_text("5-hour")
        expect(five.locator(".u")).to_have_text("52% used")
        expect(five.locator(".w")).to_have_text(re.compile(rf"^fills {WHEN}, resets {WHEN}$"))
        expect(five.locator("svg.m rect.tick")).to_have_count(1)
        weekly = limit(panel, WEEKLY)
        expect(weekly.locator(".u")).to_have_text("60% used")
        expect(weekly.locator(".w")).to_have_text(re.compile(rf"^resets {WHEN}$"))
        steps = [shown(panel.locator("[data-part=warning], [data-group=limits][data-harness=claude]"))]

        # Opened, the row says when the window opened, its readings and who used it.
        five.click()
        expect(page).to_have_url(re.compile(r"/m/usage/limit/claude/plan/300$"))
        lines = opened(panel)
        expect(lines.locator("[data-opened=window] .f")).to_have_text(
            re.compile(rf"^opened {WHEN}, resets {WHEN} · (under 1|\d+)% of it gone, 52% used$"))
        expect(lines.locator("[data-opened=readings] .f")).to_have_text(re.compile(
            rf"^30% at {WHEN}, 52% at {WHEN} · the last from proj-b · started {NAMED}$"))
        used = lines.locator("[data-opened=used]")
        expect(used.locator("[data-part=used-row]")).to_have_text([
            re.compile(rf"^\s*{TYPED} · \d+ requests\s*{MONEY}$"),
            re.compile(rf"^\s*proj-b · started {NAMED} · 15 requests\s*{MONEY}$")])
        expect(used.locator("[data-part=used-total]")).to_have_text(
            re.compile(rf"^\s*2 sessions, \d+ requests since {WHEN}\s*{MONEY}$"))
        expect(lines.locator("[data-opened=note]")).to_have_count(0)
        steps.append(shown(group(panel, "claude")))
        return steps

    on_both(installs, walk)


# --- 2. And Codex? ----------------------------------------------------------------------

def test_codex_limits_say_their_use_and_an_opened_row_says_why_it_has_no_fill_time(installs, on_both):
    def walk(page, shell) -> list:
        panel = open_page(page)
        codex = group(panel, "codex")
        expect(codex.locator(".gk")).to_have_text(re.compile(rf"^Codex\s*read {TIME}$"))
        assert codex.locator("[data-limit]").evaluate_all("all => all.map((one) => one.dataset.limit)") == [
            CODEX_FIVE, CODEX_WEEKLY]
        five = limit(panel, CODEX_FIVE)
        expect(five.locator(".u")).to_have_text("34% used")
        # Neither window will fill before it resets that a projection could say.
        expect(five.locator(".w")).to_have_text(re.compile(rf"^resets {WHEN}$"))
        expect(codex.locator(".w b")).to_have_count(0)
        expect(limit(panel, CODEX_WEEKLY).locator(".u")).to_have_text("41% used")
        # The warning line is Claude's alone.
        expect(panel.locator("[data-part=warning]")).not_to_contain_text("Codex")
        steps = [shown(codex)]

        five.click()
        lines = opened(panel)
        expect(lines.locator("[data-opened=window] .f")).to_have_text(
            re.compile(rf"^opened {WHEN}, resets {WHEN} · (under 1|\d+)% of it gone, 34% used$"))
        expect(lines.locator("[data-opened=readings] .f")).to_have_text(
            re.compile(rf"^34% at {WHEN} · the last from proj-b · started {NAMED}$"))
        # Why there is no fill time: a dim line under the others; then the window's own
        # note, once the stream's pull has found no Codex to ask for a newer reading.
        expect(lines.locator("[data-opened=note]")).to_have_text([
            "No fill time: 1 reading of this period in the last 60 minutes; a projection needs 3",
            re.compile(r"^no newer reading: .+")], timeout=15_000)
        # No Codex session ran since the window opened.
        expect(lines.locator("[data-opened=used] [data-part=used-row]")).to_have_count(0)
        expect(lines.locator("[data-opened=used] [data-part=used-total]")).to_have_text(
            re.compile(rf"^\s*0 sessions, 0 requests since {WHEN}\s*\$0\.00$"))
        steps.append(shown(codex))
        return steps

    on_both(installs, walk)


# --- 3. What did today and this week cost? -------------------------------------------------

def test_cost_says_today_and_the_last_seven_days_with_the_period_before_and_todays_dearest(installs, on_both):
    def walk(page, shell) -> list:
        panel = open_page(page)
        cost = panel.locator("[data-group=cost]")
        rows = cost.locator("[data-cost]")
        expect(rows.locator(".pk")).to_have_text(["Today", "7 days", "30 days"])
        expect(rows.nth(0).locator(".fig")).to_have_text(re.compile(rf"^{MONEY} over \d+ requests$"))
        expect(rows.nth(0).locator(".t")).to_have_text(re.compile(rf"^yesterday {MONEY}$"))
        # The week holds the fourteen sessions of this week, and the week before one more.
        week = sum(spec["requests"] + 2 * len(spec["agents"]) for spec in WEEK + [CODEX])
        expect(rows.nth(1).locator(".fig")).to_have_text(re.compile(rf"^{MONEY} over {week} requests$"))
        expect(rows.nth(1).locator(".t")).to_have_text(re.compile(rf"^the week before {MONEY}$"))
        month = week + sum(spec["requests"] for spec in EARLIER)
        expect(rows.nth(2).locator(".fig")).to_have_text(re.compile(rf"^{MONEY} over {month} requests$"))
        # Today's two dearest sessions, by name.
        expect(cost.locator("[data-part=most]")).to_have_text(
            re.compile(rf"^most to {TYPED} {MONEY}, proj-b · started {NAMED} {MONEY}$"))
        caption = cost.locator("[data-part=caption]")
        expect(caption).to_contain_text("Estimated from a local price table. Not billing data.")
        expect(caption.locator("[data-part=read]")).to_have_text(re.compile(rf"^sessions read {TIME}$"))
        # The figures are the page's first screen: the filters move none of them.
        before = shown(cost)
        pick(panel, "range", "Today")
        expect(filter_of(panel, "range").locator("button.pick")).to_have_text("today▾")
        assert shown(cost) == before
        pick(panel, "range", "Last 30 days")
        return [before]

    on_both(installs, walk)


# --- 4. Where did the last 30 days go? -----------------------------------------------------

def test_the_last_30_days_go_by_place_and_model_and_a_row_filters_sessions_to_it(installs, on_both):
    def walk(page, shell) -> list:
        steps = []
        panel = open_page(page)
        label, place = place_words(shell)
        expect(filter_of(panel, "range").locator("button.pick")).to_have_text("last 30 days▾")
        expect(filter_of(panel, "place").locator(".k")).to_have_text(label)
        chart = section(panel, "chart")
        expect(chart.locator("[data-part=sentence]")).to_have_text(
            re.compile(rf"^The most in these 30 days was .+, {MONEY}\. \d+ days had no sessions\.$"))
        # A bar for each day that had a session.
        days = {time.strftime("%Y-%m-%d", time.localtime(shell.moment - spec["ago"])) for spec in SESSIONS}
        expect(chart.locator("svg[data-part=chart] g[data-period]")).to_have_count(len(days))
        models = section(panel, "models")
        expect(models.locator("[data-part=closed]")).to_have_text(
            re.compile(rf"^{SONNET} {MONEY}, ({CODEX_MODEL}|{HAIKU}) {MONEY}$"))
        expect(models.locator("[data-part=count]")).to_have_text("3 models")
        steps.append(shown(panel.locator("[data-group=chart], [data-group=models]")))

        # Where: the projects with the folder of none, or on the small install the folders
        # written whole; a row sets the filter and opens Sessions under it.
        places = section(panel, place)
        expect(section(panel, "projects" if shell.small else "folders")).to_have_count(0)
        places.locator("[data-part=count]").click()
        expect(page).to_have_url(re.compile(rf"/m/usage/{place}$"))
        names = places.locator("[data-part=rows] .tr2 .nm")
        if shell.small:
            expect(names).to_have_text(["~/work/proj-a", "~/work/proj-b", "~/work/scratch"])
            expect(places.get_by_text("no project")).to_have_count(0)
            target = places.locator("[data-part=rows] .tr2", has_text="~/work/proj-b")
            set_to = "~/work/proj-b▾"
        else:
            expect(names).to_have_text(["proj-a", "proj-b", "no project"])
            expect(places.locator("[data-part=sentence]")).to_contain_text('"no project" is ~/work/scratch.')
            target = places.locator('a[data-row="project:proj-b"]')
            set_to = "proj-b▾"
        target.click()
        expect(page).to_have_url(re.compile(r"/m/usage/sessions$"))
        expect(filter_of(panel, "place").locator("button.pick")).to_have_text(set_to)
        sessions = section(panel, "sessions")
        in_b = [key(spec) for spec in WEEK + [CODEX] if spec["folder"] == "proj-b"]
        expect(rows_of(sessions)).to_have_count(len(in_b))
        assert sorted(keys(rows_of(sessions))) == sorted(in_b)
        expect(sessions.locator("[data-more=earlier]")).to_have_text(
            re.compile(rf"^Earlier · 1 session, \w+ \d+ to \w+ \d+, {MONEY}\s*Show$"))
        steps.append(shown(sessions))
        settle(panel, "place", "all")

        # Which model: the Model filter narrows everything below to it.
        expect(filter_of(panel, "model").locator(".k")).to_have_text("Model")
        pick(panel, "model", HAIKU)
        expect(filter_of(panel, "model").locator("button.pick")).to_have_text(f"{HAIKU}▾")
        expect(rows_of(sessions)).to_have_count(1)
        expect(rows_of(sessions).first).to_have_attribute("data-key", key(WEEK[12]))
        expect(models.locator("[data-part=closed]")).to_have_text(re.compile(rf"^{HAIKU} {MONEY}$"))
        steps.append(shown(panel.locator("[data-group=chart], [data-group=sessions], [data-group=models]")))
        settle(panel, "model", "all")

        # Which harness: the Harness filter, and under Codex alone the Model filter has one
        # model to choose, so it is not drawn.
        expect(filter_of(panel, "harness").get_by_role("button")).to_have_text("all▾")
        pick(panel, "harness", "Codex")
        expect(filter_of(panel, "harness").locator("button.pick")).to_have_text("Codex▾")
        expect(rows_of(sessions)).to_have_count(1)
        expect(rows_of(sessions).first).to_have_attribute("data-key", key(CODEX))
        expect(filter_of(panel, "model")).to_have_count(0)
        expect(models.locator("[data-part=closed]")).to_have_text(re.compile(rf"^{CODEX_MODEL} {MONEY}$"))
        steps.append(shown(panel.locator("[data-group=chart], [data-group=sessions], [data-group=models]")))
        pick(panel, "harness", "all")
        expect(filter_of(panel, "model")).to_have_count(1)
        # Without projects nothing on the page says project.
        if shell.small:
            text = " ".join(shown(panel.locator("[data-part=filters], [data-part=below]")))
            assert not re.search(r"[Pp]roject", text), text
        return steps

    on_both(installs, walk)


# --- 5. Which sessions cost most? ----------------------------------------------------------

def test_the_dearest_sessions_are_named_shown_further_and_one_opens_with_its_subagents(installs, on_both):
    def walk(page, shell) -> list:
        steps = []
        panel = open_page(page)
        sessions = section(panel, "sessions")
        expect(sessions.locator("[data-part=closed]")).to_have_text(re.compile(rf"^most to {TYPED}, {MONEY}$"))
        sessions.get_by_role("link", name=f"{len(SESSIONS)} sessions").click()
        expect(page).to_have_url(re.compile(r"/m/usage/sessions$"))
        expect(sessions.locator("[data-part=heading]")).to_have_text("This week, dearest first")
        rows = rows_of(sessions)
        expect(rows).to_have_count(10)
        # The dearest is named as typed; an unnamed one by its folder and start. Beside a
        # typed name stands its project, or on the small install its folder; beside an
        # unnamed one its project, or nothing, since its folder is in its name.
        typed, unnamed = rows.nth(0), sessions.locator(f'moxi-ui-row[data-key="{key(WEEK[1])}"]')
        expect(typed).to_have_attribute("data-key", key(WEEK[0]))
        expect(typed).to_have_attribute("name", TYPED)
        expect(typed).to_have_attribute("where", "proj-a")
        expect(typed).to_have_attribute("lead", re.compile(r"^\d+ requests$"))
        expect(typed).to_have_attribute("rest", SONNET)
        expect(typed).to_have_attribute("tail", re.compile(r"^context (under 1|\d+)% full$"))
        expect(unnamed).to_have_attribute("name", re.compile(rf"^proj-b · started {NAMED}$"))
        expect(unnamed).to_have_attribute("where", "" if shell.small else "proj-b")
        steps.append(shown(sessions))

        # `Show` brings the rest of the week below; `Earlier` the days before it.
        more = sessions.locator("[data-more=first]")
        expect(more).to_have_text(re.compile(rf"^4 more this week, {MONEY} between them\s*Show$"))
        first = keys(rows)
        more.get_by_role("button", name="Show").click()
        expect(rows).to_have_count(14)
        assert keys(rows)[:10] == first
        assert sorted(keys(rows)) == sorted(key(spec) for spec in WEEK + [CODEX])
        # A Codex thread has no typed name: its folder and start, and its model.
        thread = sessions.locator(f'moxi-ui-row[data-key="{key(CODEX)}"]')
        expect(thread).to_have_attribute("name", re.compile(rf"^proj-b · started {NAMED}$"))
        expect(thread).to_have_attribute("rest", CODEX_MODEL)
        earlier = sessions.locator("[data-more=earlier]")
        expect(earlier).to_have_text(re.compile(rf"^Earlier · 2 sessions, \w+ \d+ to \w+ \d+, {MONEY}\s*Show$"))
        earlier.get_by_role("button", name="Show").click()
        expect(rows_of(sessions, "earlier")).to_have_count(2)
        assert keys(rows_of(sessions, "earlier")) == [key(spec) for spec in EARLIER]
        steps.append(shown(sessions))

        # A session opened in its row: what it cost and where it went.
        typed.get_by_role("link", name="Open").click()
        expect(page).to_have_url(re.compile(rf"/m/usage/sessions/claude/{WEEK[0]['session']}$"))
        body = typed.locator("[data-part=session]")
        expect(body.locator("[data-line=requests]")).to_have_text(re.compile(r"^\s*Requests\s*16 requests by the main agent "))
        expect(body.locator("[data-line=context]")).to_have_text(re.compile(r"tokens on the last request"))
        expect(body.locator("[data-line=chart] svg[data-part=session-chart] rect.bar")).to_have_count(16)
        expect(body.locator("[data-line=went]")).to_have_text(re.compile(r"^\s*Went to\s*\S"))
        expect(body.locator("[data-line=session]")).to_have_text(
            re.compile(rf"^\s*Session\s*claude · {SONNET} · started {NAMED}$"))
        expect(body.locator("[data-line=subagents]")).to_have_text(
            re.compile(rf"^\s*Subagents\s*2 subagents · {MONEY} of the {MONEY}\s*Show$"))
        expect(body.locator("[data-part=session-acts] > *")).to_have_text(
            ["Open in Sessions", "Table", "Check against a bill", "Copy id"])

        # Its subagents shown, dearest first, and one opened under its row.
        body.locator("[data-line=subagents]").get_by_role("button", name="Show").click()
        agents = body.locator("[data-part=agents] moxi-ui-row[data-agent]")
        expect(agents).to_have_count(2)
        assert agents.evaluate_all("all => all.map((one) => one.getAttribute('name'))") == [AGENTS[1], AGENTS[0]]
        expect(agents.nth(0)).to_have_attribute("where", "subagent")
        expect(agents.nth(0)).to_have_attribute("tail", re.compile(r"^(under 1|\d+)% of the session$"))
        expect(body.locator("svg[data-part=agent-map] g[data-agent]")).to_have_count(3)
        agents.nth(0).get_by_role("link", name="Open").click()
        expect(page).to_have_url(re.compile(rf"/m/usage/sessions/claude/{WEEK[0]['session']}/{AGENTS[1]}$"))
        agent = agents.nth(0).locator("[data-part=agent]")
        expect(agent.locator("[data-line=requests]")).to_have_text(re.compile(r"^\s*Requests\s*2 requests"))
        expect(agent.locator("svg[data-part=session-chart] rect.bar")).to_have_count(2)
        steps.append(shown(sessions))
        return steps

    on_both(installs, walk)


# --- 6. Is caching paying off? -------------------------------------------------------------

def test_token_kinds_say_how_much_input_came_from_cache_and_what_caching_saved(installs, on_both):
    def walk(page, shell) -> list:
        panel = open_page(page)
        kinds = section(panel, "kinds")
        expect(kinds.locator(".gk")).to_have_text("Token kinds")
        expect(kinds.locator("[data-part=closed]")).to_have_text(
            re.compile(rf"^\d+% of input read from cache · [a-z ]+ {MONEY}, [a-z ]+ {MONEY}$"))
        closed = shown(kinds)
        kinds.locator("[data-part=count]").click()
        expect(page).to_have_url(re.compile(r"/m/usage/kinds$"))
        rows = kinds.locator("[data-part=rows] .tr2")
        expect(rows).to_have_count(4)
        assert sorted(rows.locator(".nm").evaluate_all(
            "all => all.map((one) => one.textContent.split('·')[0].trim())")) == [
            "Cache reads", "Cache writes", "Fresh input", "Output"]
        # Every request read six thousand tokens from cache at a tenth of the input price.
        expect(kinds.locator("[data-part=saving]")).to_have_text(re.compile(
            r"^[\d.]+[kM]? of [\d.]+[kM]? input tokens were read from cache\. With no cache, at the same prices, "
            r"those reads would have cost \$[\d.,]+ as fresh input; they cost \$[\d.,]+, and cache writes cost "
            r"\$[\d.,]+ more than fresh input, so caching saved about \$[\d.,]+\. An estimate\.$"))
        return [closed, shown(kinds)]

    on_both(installs, walk)


# --- 7. What is eating the 5-hour window? --------------------------------------------------

def test_the_5_hour_window_names_who_used_it_and_reads_again_on_a_new_reading(ui_shells, on_both):
    made = two_installs(ui_shells)

    def walk(page, shell) -> list:
        panel = open_page(page, "limit/claude/plan/300")
        lines = opened(panel)
        used = lines.locator("[data-opened=used]")
        expect(used.locator(".k")).to_have_text("Used by")
        expect(used.locator("[data-part=used-row]")).to_have_count(2)
        expect(used.locator("[data-part=used-row]").nth(0)).to_contain_text(TYPED)
        expect(lines.locator("[data-opened=readings] .f")).to_have_text(re.compile(
            rf"^30% at {WHEN}, 52% at {WHEN} · the last from proj-b · started {NAMED}$"))
        steps = [shown(group(panel, "claude"))]

        # A new reading, from the typed session, is read again when the tab shows again.
        run(shell, sys.executable, "-c", NEW_READING, repr(shell.moment), WEEK[0]["session"])
        with page.expect_request(lambda request: "/m/usage/api/window?" in request.url):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(limit(panel, FIVE).locator(".u")).to_have_text("58% used")
        expect(lines.locator("[data-opened=window] .f")).to_have_text(re.compile(r", 58% used$"))
        expect(lines.locator("[data-opened=readings] .f")).to_have_text(
            re.compile(rf"^30% at {WHEN}, 58% at {WHEN} · the last from {TYPED}$"))
        expect(used.locator("[data-part=used-row]")).to_have_count(2)
        expect(page).to_have_url(re.compile(r"/m/usage/limit/claude/plan/300$"))
        steps.append(shown(group(panel, "claude")))
        return steps

    on_both(made, walk)


# --- 8. How fresh is this? -----------------------------------------------------------------

def test_each_part_says_when_it_was_read_and_read_new_sessions_brings_a_new_one_in(ui_shells, on_both):
    made = two_installs(ui_shells)
    files = r"(\d+) files: Claude (\d+), Codex 1"

    def walk(page, shell) -> list:
        panel = open_page(page)
        # The limits say when each harness was last read; Cost and the foot when the
        # sessions were.
        expect(group(panel, "claude").locator(".gk small")).to_have_text(re.compile(rf"^read {TIME}$"))
        expect(group(panel, "codex").locator(".gk small")).to_have_text(re.compile(rf"^read {TIME}$"))
        expect(panel.locator("[data-group=cost] [data-part=read]")).to_have_text(
            re.compile(rf"^sessions read {TIME}$"))
        foot = panel.locator("[data-part=foot]")
        expect(foot).to_have_attribute("data-state", "up")
        last = foot.locator(".last")
        expect(last).to_have_text(re.compile(rf"^Sessions read {TIME} · {files}$"))
        before = re.search(files, last.inner_text())
        expect(panel.locator("[data-part=lines] p")).to_have_count(0)
        steps = [shown(panel.locator("[data-group=limits] .gk, [data-part=caption], [data-part=foot]"))]

        # A session that began since: `Read new sessions` brings it in.
        write(shell, [LATE], shell.moment)
        today = panel.locator("[data-group=cost] [data-cost=today] .fig")
        was = today.inner_text()
        foot.get_by_role("button", name="Read new sessions").click()
        expect(last).to_have_text(re.compile(
            rf"^Sessions read {TIME} · {int(before[1]) + 1} files: Claude {int(before[2]) + 1}, Codex 1$"),
            timeout=30_000)
        expect(foot).to_have_attribute("data-state", "up")
        expect(today).not_to_have_text(was)
        expect(foot.get_by_role("button", name="Read new sessions")).to_be_visible()
        steps.append(shown(panel.locator("[data-group=limits] .gk, [data-group=cost], [data-part=foot]")))
        return steps

    on_both(made, walk)
