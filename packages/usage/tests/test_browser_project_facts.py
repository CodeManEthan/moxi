"""usage's facts on the Projects page, and the Usage page's project address, in a browser,
through a real shell that starts the real page servers, on two installs:

- The full one: the core, ui, projects, sessions, usage, comms and repo. atlas-api was
  worked in an hour ago and kestrel-web three days ago, and a folder in no project an hour
  ago, each a Claude Code session the shell's own `moxi usage index` read. atlas-api's
  closed row says its cost today; opened, its `Cost` row says today, 7 days and 30 days
  and `an estimate`, and its `Usage` link opens Usage with the Project filter at
  atlas-api, the range as it was and nothing opened. kestrel-web says no cost today on its
  closed row. The loose folder's row carries its 7-day cost. A read that fails after a
  good one drops what it fed, and the next good read puts it back. Usage opened at a key
  usage holds nothing under opens with the filter at `all` and the foot's line.
- The same without usage: no Cost row, no cost on a closed row, and nothing of usage's
  is read.

The transcripts are written and indexed with the shell's own environment, so they sit
under the shell's home where its page servers read them.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(240, method="thread"),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="the Projects page is checked in a browser on Linux; "
                                        "the Windows and macOS runs are owed")]

MODEL = "claude-sonnet-4-5"
#: The modules the full install holds besides the core and ui.
FULL = ("projects", "sessions", "usage", "comms", "repo")
#: A cost as usage words it.
COST = r"\$\d+\.\d\d\+?"

SEED = """
import json, sys, time
from pathlib import Path
from moxi_sessions import api as sessions
T = sessions.testing
now = time.time()
for n, spec in enumerate(json.loads(sys.argv[1])):
    t = T.ClaudeTranscript(Path(sessions.root("claude")), session_id=spec["session"],
                           project="-" + spec["cwd"].strip("/").replace("/", "-"),
                           cwd=spec["cwd"], start=now - spec["hours_ago"] * 3600)
    for m in range(3):
        t.user("Fix the failing test.")
        t.assistant("Done.", usage=T.usage(input_tokens=200, output=1000 + m * 500),
                    request_id=f"req_{n}{m}", model=spec["model"])
    t.write()
"""

#: Each module's distribution that declares no module, so a page server finds none.
_EMPTY = "Metadata-Version: 2.1\nName: moxi-{name}\nVersion: 0.0.0\n"


def install(tmp_path_factory, holds: tuple[str, ...]) -> dict:
    """`ui_shells` options for an install of the core, ui and `holds` only: the shell's
    snapshot leaves every other module out, and a site first on its path holds an empty
    distribution of each, so no process the shell starts finds them."""
    from moxi import modules

    out = [found.name for found in modules.scan() if found.name not in ("ui", *holds)]
    site = tmp_path_factory.mktemp("site")
    for name in out:
        info = site / f"moxi_{name}-0.0.0.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(_EMPTY.format(name=name), encoding="utf-8")
    return {"site": [str(site)], "leave_out": out}


def run(shell, *argv: str) -> str:
    done = subprocess.run(list(argv), env=shell.env, cwd=shell.env["HOME"], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=180)
    if done.returncode != 0:
        raise AssertionError(f"{argv} failed ({done.returncode}):\n{done.stdout}{done.stderr}")
    return done.stdout


def seed(shell, *, index: bool) -> None:
    """atlas-api and kestrel-web as projects, and three sessions: in atlas-api and the loose
    folder an hour ago, in kestrel-web three days ago; indexed by usage when `index`."""
    home = Path(shell.env["HOME"])
    for key in ("atlas-api", "kestrel-web"):
        (home / "code" / key).mkdir(parents=True)
        run(shell, sys.executable, "-m", "moxi", "projects", "add", key, "--root", str(home / "code" / key))
    (home / "code" / "finch-scripts").mkdir(parents=True)
    specs = [{"session": f"0190b000-0000-7000-8000-0000000000b{n}", "cwd": str(home / "code" / name),
              "hours_ago": ago, "model": MODEL}
             for n, (name, ago) in enumerate((("atlas-api", 1), ("kestrel-web", 72), ("finch-scripts", 1)))]
    run(shell, sys.executable, "-c", SEED, json.dumps(specs))
    if index:
        run(shell, sys.executable, "-m", "moxi", "usage", "index")


@pytest.fixture(scope="module")
def full(ui_shells, tmp_path_factory):
    shell = ui_shells(**install(tmp_path_factory, FULL))
    seed(shell, index=True)
    return shell


@pytest.fixture(scope="module")
def without_usage(ui_shells, tmp_path_factory):
    shell = ui_shells(**install(tmp_path_factory, tuple(name for name in FULL if name != "usage")))
    seed(shell, index=False)
    return shell


def page_on(ui_contexts, shell):
    """A fresh page on `shell`, keeping the path of every request it makes in `page.asked`."""
    page = ui_contexts(shell).pages[0]
    asked: list[str] = []
    page.on("request", lambda request: asked.append(request.url.split("://", 1)[1].split("/", 1)[1]))
    page.asked = asked
    return page


def row(page, key: str):
    return page.locator(f'#moxi-panel > moxi-projects-page moxi-ui-row[data-key="{key}"]')


def opened(page, key: str):
    page.goto(f"/m/projects/open/{key}")
    item = row(page, key)
    expect(item).to_have_attribute("open", "")
    return item


def cost_row(item):
    return item.locator('[data-part="opened"] [data-label="Cost"]')


def usage_reads(page) -> list[str]:
    return ["/" + path.split("?", 1)[0] for path in page.asked if path.startswith("m/usage/")]


def shown_again(page) -> None:
    """The document hidden and shown again, which makes each placed component read once."""
    page.evaluate("""() => {
      for (const state of ["hidden", "visible"]) {
        Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
        document.dispatchEvent(new Event("visibilitychange"));
      }
    }""")


def today_route(page, answer) -> str:
    """Routes usage's read of today's cost by project to `answer`; returns the pattern."""
    today = page.evaluate("""() => { const d = new Date();
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }""")
    pattern = re.compile(rf"/m/usage/api/projects\?since={today}$")
    page.route(pattern, answer)
    return pattern


def test_a_project_shows_its_cost_today_and_opened_its_cost_row_linking_to_usage(full, ui_contexts):
    page = page_on(ui_contexts, full)
    page.goto("/m/projects/")
    expect(row(page, "atlas-api")).to_contain_text(re.compile(rf"{COST} today"))
    item = opened(page, "atlas-api")
    words = cost_row(item).locator(".ol")
    expect(words).to_have_text(re.compile(rf"^{COST} today · {COST} in 7 days · {COST} in 30 days · an estimate$"))
    link = cost_row(item).locator("a")
    expect(link).to_have_text("Usage")
    expect(link).to_have_attribute("href", "/m/usage/project/atlas-api")
    # kestrel-web had no session today: no cost on its closed row, a 7-day one opened.
    expect(row(page, "kestrel-web")).not_to_contain_text("today")
    # Of usage's, the page reads only the component's script and /api/projects.
    assert set(usage_reads(page)) <= {"/m/usage/project-facts.js", "/m/usage/api/projects"}, page.asked

    link.click()
    panel = page.locator("#moxi-panel > moxi-usage-page")
    place = panel.locator('[data-filter="place"] button.pick')
    expect(place).to_contain_text("1 project")
    expect(page).to_have_url(f"{full.url}/m/usage/")
    expect(panel.locator("[data-part=lines] p")).not_to_contain_text(["There is no such view here."])


def test_kestrel_web_opened_has_its_7_day_figure_but_none_for_today(full, ui_contexts):
    page = page_on(ui_contexts, full)
    item = opened(page, "kestrel-web")
    expect(cost_row(item).locator(".ol")).to_have_text(
        re.compile(rf"^{COST} in 7 days · {COST} in 30 days · an estimate$"))


def test_a_work_dir_in_no_project_carries_its_7_day_cost(full, ui_contexts):
    page = page_on(ui_contexts, full)
    page.goto("/m/projects/")
    work_dir = str(Path(full.env["HOME"]) / "code" / "finch-scripts")
    loose = page.locator(f'#moxi-panel section[data-part="folders"] moxi-ui-row[data-folder="{work_dir}"]')
    expect(loose).to_contain_text(re.compile(COST))


def test_a_read_failing_after_a_good_one_drops_what_it_fed_and_comes_back(full, ui_contexts):
    page = page_on(ui_contexts, full)
    item = opened(page, "atlas-api")
    words = cost_row(item).locator(".ol")
    expect(words).to_contain_text("in 7 days")
    expect(words).to_contain_text("today")
    # Today's read fails: the closed row's figure and the Cost row's today figure go.
    pattern = today_route(page, lambda route: route.fulfill(status=500, body="no"))
    shown_again(page)
    expect(words).to_have_text(re.compile(rf"^{COST} in 7 days · {COST} in 30 days · an estimate$"))
    page.unroute(pattern)
    # Every read fails: the Cost row goes.
    page.route(re.compile(r"/m/usage/api/projects\?"), lambda route: route.fulfill(status=500, body="no"))
    shown_again(page)
    expect(cost_row(item)).to_have_count(0)
    page.unroute(re.compile(r"/m/usage/api/projects\?"))
    shown_again(page)
    expect(words).to_have_text(re.compile(rf"^{COST} today · {COST} in 7 days · {COST} in 30 days · an estimate$"))


def test_usage_at_a_key_it_holds_nothing_under_opens_with_the_filter_at_all_and_the_foot_line(full, ui_contexts):
    page = page_on(ui_contexts, full)
    page.goto("/m/usage/project/atlas-api")
    panel = page.locator("#moxi-panel > moxi-usage-page")
    expect(panel.locator('[data-filter="place"] button.pick')).to_contain_text("1 project")
    page.goto("/m/usage/project/no-such-key")
    panel = page.locator("#moxi-panel > moxi-usage-page")
    expect(panel.locator("[data-part=lines] p")).to_contain_text(["There is no such view here."])
    expect(panel.locator('[data-filter="place"] button.pick')).to_contain_text("All projects")
    expect(page).to_have_url(f"{full.url}/m/usage/project/no-such-key")


def test_without_usage_no_cost_is_drawn_and_nothing_of_usage_is_read(without_usage, ui_contexts):
    page = page_on(ui_contexts, without_usage)
    item = opened(page, "atlas-api")
    expect(item.locator('[data-label="Folder"]')).to_be_visible()
    page.wait_for_timeout(1_000)
    expect(cost_row(item)).to_have_count(0)
    expect(row(page, "atlas-api")).not_to_contain_text("today")
    assert usage_reads(page) == [], page.asked
