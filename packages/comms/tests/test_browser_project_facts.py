"""comms' facts on the Projects page in a browser, through a real shell that starts the real
page servers, on two installs:

- The full one: the core, ui, projects, sessions, usage, comms and repo, with atlas-api led
  by builder-1 and worked on by planner. While comms says which names are held (its roster
  `up`), the opened atlas-api says `builder-1 leads · not running now` when no live address
  holds builder-1, and nothing after the lead when one does: comms never says an agent
  runs. With comms' daemon down (its roster `offline`, as in this shell) nothing is said. A
  roster read that fails after a good one drops the words, and the next good read puts
  them back.
- The same without comms: nothing after the lead, and nothing of comms' is read.

This shell runs no comms daemon, so comms' page server answers its roster `offline`. A
roster that is `up` is answered to the browser in place of the page server's, through the
page's own request routing: the component's words are what is checked, from the shape
comms' page server sends.
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

#: The modules the full install holds besides the core and ui.
FULL = ("projects", "sessions", "usage", "comms", "repo")
ROSTER = re.compile(r"/m/comms/api/roster$")
LEADS = "builder-1 leads"


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


def seed(shell) -> None:
    """atlas-api, led by builder-1, with planner working on it."""
    root = Path(shell.env["HOME"]) / "code" / "atlas-api"
    root.mkdir(parents=True)
    moxi = (sys.executable, "-m", "moxi", "projects")
    run(shell, *moxi, "add", "atlas-api", "--root", str(root), "--lead", "builder-1")
    run(shell, *moxi, "agents", "add", "atlas-api", "planner")


@pytest.fixture(scope="module")
def full(ui_shells, tmp_path_factory):
    shell = ui_shells(**install(tmp_path_factory, FULL))
    seed(shell)
    return shell


@pytest.fixture(scope="module")
def without_comms(ui_shells, tmp_path_factory):
    shell = ui_shells(**install(tmp_path_factory, tuple(name for name in FULL if name != "comms")))
    seed(shell)
    return shell


def roster(*live: str) -> dict:
    """comms' `/api/roster` answer, `up`, with a live address for each name in `live` and a
    standing one for planner."""
    def address(name: str, held: bool) -> dict:
        return {"qualified": name, "name": name, "scope": None, "live": held, "kind": None if held else "agent",
                "module": None, "tier": None, "adapter": "claude-code" if held else None,
                "harness": "claude" if held else None, "directors": [], "policy_directors": [],
                "session_id": "s-1" if held else None, "bound_at": None, "awaiting": [], "cache": None,
                "holder": None, "via": None}
    rows = [address(name, True) for name in live] + [address("planner", False)]
    return {"state": "up", "detail": None, "rows": rows, "today": "2026-11-08", "now": 1_800_000_000}


def answer(page, body: dict | None) -> None:
    """comms' roster answered with `body` from now on (a 500 for None)."""
    page.unroute(ROSTER)
    if body is None:
        page.route(ROSTER, lambda route: route.fulfill(status=500, body="no"))
    else:
        page.route(ROSTER, lambda route: route.fulfill(status=200, content_type="application/json",
                                                       body=json.dumps(body)))


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


def shown_again(page) -> None:
    """The document hidden and shown again, which makes each placed component read once."""
    page.evaluate("""() => {
      for (const state of ["hidden", "visible"]) {
        Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
        document.dispatchEvent(new Event("visibilitychange"));
      }
    }""")


def agents(item):
    return item.locator('[data-part="opened"] [data-label="Agents"] .ol')


def comms_reads(page) -> list[str]:
    return ["/" + path.split("?", 1)[0] for path in page.asked if path.startswith("m/comms/")]


def test_a_lead_no_live_address_holds_is_not_running_now(full, ui_contexts):
    page = page_on(ui_contexts, full)
    answer(page, roster())
    item = opened(page, "atlas-api")
    expect(agents(item)).to_contain_text(f"{LEADS} · not running now")
    assert set(comms_reads(page)) <= {"/m/comms/project-facts.js", "/m/comms/api/roster"}, page.asked


def test_a_lead_a_live_address_holds_says_nothing_after_the_name(full, ui_contexts):
    page = page_on(ui_contexts, full)
    answer(page, roster("builder-1"))
    item = opened(page, "atlas-api")
    expect(agents(item)).to_contain_text(LEADS)
    page.wait_for_timeout(1_000)
    expect(agents(item)).not_to_contain_text("running")


def test_with_comms_daemon_down_nothing_is_said(full, ui_contexts):
    page = page_on(ui_contexts, full)
    item = opened(page, "atlas-api")
    expect(agents(item)).to_contain_text(LEADS)
    deadline = 30_000
    while "/m/comms/api/roster" not in comms_reads(page) and deadline > 0:
        page.wait_for_timeout(100)
        deadline -= 100
    page.wait_for_timeout(1_000)
    expect(agents(item)).not_to_contain_text("running")


def test_a_read_failing_after_a_good_one_drops_the_words_and_the_next_puts_them_back(full, ui_contexts):
    page = page_on(ui_contexts, full)
    answer(page, roster())
    item = opened(page, "atlas-api")
    expect(agents(item)).to_contain_text("not running now")
    answer(page, None)
    shown_again(page)
    expect(agents(item)).not_to_contain_text("not running now")
    expect(agents(item)).to_contain_text(LEADS)
    answer(page, roster())
    shown_again(page)
    expect(agents(item)).to_contain_text(f"{LEADS} · not running now")


def test_without_comms_nothing_is_said_or_read(without_comms, ui_contexts):
    page = page_on(ui_contexts, without_comms)
    item = opened(page, "atlas-api")
    expect(agents(item)).to_contain_text(LEADS)
    page.wait_for_timeout(1_000)
    expect(agents(item)).not_to_contain_text("running")
    assert comms_reads(page) == [], page.asked
