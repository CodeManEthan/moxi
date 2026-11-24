"""The launcher's `project-facts` on the Projects page, in a browser, through a real shell
that starts the launcher's and projects' real page servers, on two installs:

- **the full install**: core, ui, projects and the launcher. Placed alone, the component
  reports, for the projects it is given, each one's running launches and claims, and for
  the opened one its agents that run, its `Launch` and `Sessions` rows and its `Stays`
  line; it keeps the component contract. On the page, a project's line two counts what
  runs there, the opened project shows who runs where, its preset, its last launch and
  how it names sessions, Remove's step says the sessions keep running, a launch started
  or ended reaches the page on the launcher's `running` topic, and a route that fails
  drops only what it feeds.
- **the small install**: core, ui and projects. The page places nothing of the launcher,
  draws no gap where its facts would be, and asks nothing of it.

Every other module of this environment is left out of the shell's list, and its page
servers can't import it (`kit.left_out`). Without sessions no launch has a latest
message, so nothing here has an `at` or a `last active` time: the page draws none, and
never one it made up.

Projects are written to projects' store at the place the shell's environment gives it.
Each running launch is one of this test's own process, in a project and under an agent's
name, ended when the test ends. None of the launcher's routes these read asks slots or a
supervisor, so each is the real one; one test makes a route fail with `page.route`.
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from moxi import modules
from playwright.sync_api import expect

import launcher_page_kit as kit
from moxi_launcher import presets

projects_store = pytest.importorskip("moxi_projects.store", reason="projects is not installed")
WAIT_S = pytest.importorskip("moxi_ui.testing", reason="ui is not installed").WAIT_S

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="the Projects page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

#: The shell's settings route.
SAVE = "/m/ui/api/settings/set"
#: A time of day as a browser's locale writes it.
HOUR = r"\d{1,2}[:.]\d{2}(\s?[AaPp][Mm])?"
#: The `Launch` line's clause for kestrel-web's launch earlier today.
LAST = f"last launched today {HOUR}"
#: The every-project wording of the `Launch` row's second line.
EVERY = "names sessions with the template for every project"


class Project(NamedTuple):
    """One seeded project: its key, root under the shell's home, kind, lead and the
    agents who also work there."""

    key: str
    root: str
    kind: str
    lead: str | None
    associated: tuple[str, ...]


PROJECTS = (
    Project("atlas-api", "code/atlas-api", "project", "builder-1", ("planner", "reviewer")),
    Project("harbor-infra", "code/harbor-infra", "project", "ops-watch", ()),
    Project("kestrel-web", "code/kestrel-web", "project", "builder-2", ("reviewer",)),
    Project("reviewer-home", "agents/reviewer", "agent", None, ()),
)
KEYS = [project.key for project in PROJECTS]

#: kestrel-web's own preset, which launches in the terminal.
PRESETS = """\
[project.kestrel-web]
project = "kestrel-web"
harness = "claude"
model = "sonnet"
effort = "medium"
target = "terminal"
"""
#: kestrel-web's own name template, and the name it gives.
TEMPLATES = '{kestrel-web = "<project>-own"}'
OWN_NAME = "kestrel-web-own"


def root(shell: Any, key: str) -> str:
    """The project `key`'s root, absolute."""
    found = next(project for project in PROJECTS if project.key == key)
    return str(Path(shell.env["HOME"]) / found.root)


def given(shell: Any) -> list[dict[str, Any]]:
    """The `projects` attribute the page gives each facts component."""
    return [{"key": project.key, "root": root(shell, project.key), "lead": project.lead,
             "associated": list(project.associated)} for project in PROJECTS]


def add_projects(shell: Any) -> None:
    """The four projects in the shell's store, each folder made."""
    for project in PROJECTS:
        Path(root(shell, project.key)).mkdir(parents=True, exist_ok=True)

    def fill(conn: Any) -> None:
        for project in PROJECTS:
            projects_store.insert(conn, key=project.key, root=root(shell, project.key),
                                  kind=project.kind, lead=project.lead)
            for name in project.associated:
                projects_store.add_agent(conn, project.key, name)

    projects_store.write(fill, where=projects_store.path(shell.env))


#: Added to the `sitecustomize.py` `kit.left_out` writes: the left-out modules'
#: distributions are not found either, so no process loads their links with projects.
_UNFOUND = '''
from importlib.metadata import Distribution

UNFOUND = frozenset({names!r})
_discover = Distribution.discover


def _found(cls, **kwargs):
    return (dist for dist in _discover(**kwargs)
            if (dist.name or "").lower().replace("_", "-") not in UNFOUND)


Distribution.discover = classmethod(_found)
'''


def install(base: Path, *holds: str) -> dict[str, Any]:
    """The shell options of an install holding core, ui and `holds`: every other module
    left out of the shell's list, and neither importable nor found as a distribution in
    any process it starts."""
    gone = [found.name for found in modules.scan() if found.name not in ("ui", *holds)]
    site = kit.left_out(base, *gone)
    with open(site / "sitecustomize.py", "a", encoding="utf-8") as fh:
        fh.write(_UNFOUND.format(names={f"moxi-{name}" for name in gone}))
    return {"site": [str(site)], "leave_out": gone}


def record_terminal(shell: Any) -> None:
    """The core's record of a terminal whose environment is the shell's, where its page
    servers read it, so projects' page doesn't refuse a remove for want of one."""
    from moxi import paths, terminal

    terminal.write(dict(shell.env), path=paths.terminal_record(shell.env))


def made(shell: Any) -> dict[str, Any]:
    """`kit.seed`'s keys for a launch the shell's own user made from a form that recorded
    where its values came from: one the launcher's `/api/last` counts."""
    return {"by": kit.actor(shell), "values": {"sources": {}}}


@pytest.fixture(scope="module")
def full_shell(ui_shells, tmp_path_factory):
    """The full install: the four projects, kestrel-web's preset and name template, and a
    launch there earlier today that has ended."""
    shell = ui_shells(**install(tmp_path_factory.mktemp("full-install"), "projects", "launcher"))
    add_projects(shell)
    file = presets.path(env=shell.env)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(PRESETS)
    status, answer = kit.session(shell).post(SAVE, {"module": "launcher", "key": "name.templates",
                                                    "value": TEMPLATES})
    assert status == 200, (status, answer)
    kit.seed(shell, "kestrel-earlier", target="terminal", cwd=root(shell, "kestrel-web"),
             project_key="kestrel-web", identity="builder-2", **made(shell))
    record_terminal(shell)
    return shell


@pytest.fixture(scope="module")
def small_shell(ui_shells, tmp_path_factory):
    """The small install: the four projects, and no launcher."""
    shell = ui_shells(**install(tmp_path_factory.mktemp("small-install"), "projects"))
    add_projects(shell)
    return shell


class Launches:
    """The running launches one test seeds in the full install, ended when it ends."""

    def __init__(self, shell: Any):
        self.shell = shell
        self.ids: list[str] = []

    def start(self, key: str, identity: str, *, target: str, slot: str = "3") -> str:
        """A launch running now in the project `key` under the name `identity`; returns
        its claim."""
        request_id = f"{identity}-{uuid.uuid4().hex[:8]}"
        sid = kit.seed(self.shell, request_id, live=True, target=target, slot=slot,
                       cwd=root(self.shell, key), project_key=key, identity=identity,
                       **made(self.shell))
        self.ids.append(request_id)
        return f"claude/{sid}"

    def end(self) -> None:
        kit.end(self.shell, *self.ids)
        self.ids.clear()
        kit.published(self.shell, "running", lambda data: data["count"] == 0)


@pytest.fixture
def launches(full_shell):
    seeded = Launches(full_shell)
    try:
        yield seeded
    finally:
        seeded.end()


@pytest.fixture
def busy(launches):
    """Three launches running: in atlas-api builder-1 in slot 4, then planner headless;
    in harbor-infra ops-watch in the terminal. Returns their claims by name."""
    claims = {"builder-1": launches.start("atlas-api", "builder-1", target="slot", slot="4"),
              "planner": launches.start("atlas-api", "planner", target="headless"),
              "ops-watch": launches.start("harbor-infra", "ops-watch", target="terminal")}
    kit.published(launches.shell, "running", lambda data: data["count"] == 3)
    return claims


# --- the page ------------------------------------------------------------------------------

def panel_of(page):
    return page.locator("#moxi-panel > moxi-projects-page")


def row(page, key: str):
    return panel_of(page).locator(f'moxi-ui-row[data-key="{key}"]')


def line_two(item):
    return item.locator(".l2")


def open_list(page):
    page.goto("/m/projects/")
    expect(panel_of(page).locator('section[data-part="list"]')).to_have_attribute("data-state", "up")
    return panel_of(page)


def opened(page, key: str):
    """Opens the page at `open/<key>` and gives the row, once it is open."""
    page.goto(f"/m/projects/open/{key}")
    item = row(page, key)
    expect(item).to_have_attribute("open", "")
    return item


def labels(item) -> list[str]:
    return item.locator('[data-part="opened"] .orow').evaluate_all(
        "rows => rows.map(row => row.dataset.label)")


def expect_labels(page, item, want: list[str]) -> None:
    """Wait until the opened project's labelled rows are `want`, in that order."""
    seen = labels(item)
    for _ in range(int(WAIT_S * 20)):
        if seen == want:
            break
        page.wait_for_timeout(50)
        seen = labels(item)
    assert seen == want


def lines(item, label: str):
    return item.locator(f'[data-part="opened"] [data-label="{label}"] .ol p')


# --- placed alone --------------------------------------------------------------------------

# Places the launcher's component in a hidden host on the shell's document, through the
# client as the page places it, and keeps every report it sends in `window.__reports`.
_PLACE = """async ([projects, open]) => {
  const client = await import("/m/ui/client.js");
  window.__reports = [];
  const host = document.createElement("div");
  host.hidden = true;
  document.body.append(host);
  host.addEventListener("moxi-project-facts", (event) => window.__reports.push(event.detail));
  const element = await client.place(host, "launcher", "project-facts",
                                     {projects: JSON.stringify(projects), open});
  window.__facts = element;
  return element === null ? null : element.localName;
}"""


def reported(page, passes) -> dict:
    """Wait until the component's last report `passes`, and return it."""
    last = None
    for _ in range(int(WAIT_S * 20)):
        reports = page.evaluate("() => window.__reports")
        if reports:
            last = reports[-1]
            if last is not None and passes(last):
                return last
        page.wait_for_timeout(50)
    raise AssertionError(f"the component's last report is {last!r}")


def running(*claims: str) -> list[dict]:
    return [{"text": "", "count": {"one": "running", "many": "running"}, "claims": list(claims)}]


def test_placed_alone_it_reports_what_runs_in_each_project_and_the_opened_ones_rows(
        full_shell, busy, ui_contexts):
    page = ui_contexts(full_shell).pages[0]
    assert page.evaluate(_PLACE, [given(full_shell), "atlas-api"]) == "moxi-launcher-project-facts"
    report = reported(page, lambda got: "sections" in got["projects"].get("atlas-api", {})
                      and len(got["projects"]["atlas-api"]["sections"][0]["lines"][0]["parts"]) == 2)
    atlas = report["projects"]["atlas-api"]
    launch = atlas["sections"][0]
    assert re.fullmatch(LAST, launch["lines"][0]["parts"][1]["text"]), launch
    launch["lines"][0]["parts"][1]["text"] = "<last>"
    # Newest first: planner, then builder-1. Nothing has a latest message: no `at`.
    assert report == {"version": 1, "order": 10, "activity": "window", "projects": {
        "atlas-api": {
            "parts": running(busy["planner"], busy["builder-1"]),
            "sections": [
                {"label": "Launch", "lines": [{"parts": [{"text": "no preset"}, {"text": "<last>"}]},
                                              {"parts": [{"text": EVERY}]}]},
                {"label": "Sessions", "lines": [
                    {"parts": [{"text": "planner"}, {"text": "headless"}], "claims": [busy["planner"]]},
                    {"parts": [{"text": "builder-1"}, {"text": "slot 4"}], "claims": [busy["builder-1"]]}]}],
            "agents": {"planner": {"runs": True, "state": "running now", "where": "headless"},
                       "builder-1": {"runs": True, "state": "running now", "where": "in slot 4"}},
            "stays": ["the 2 running now keep running"]},
        "harbor-infra": {"parts": running(busy["ops-watch"])}}}

    # Another project opened: its rows go with it, and nothing runs there.
    page.evaluate("() => window.__facts.setAttribute('open', 'kestrel-web')")
    report = reported(page, lambda got: [len(line["parts"]) for section in
                                         got["projects"].get("kestrel-web", {}).get("sections", [])
                                         for line in section["lines"]] == [2, 1])
    assert set(report["projects"]) == {"atlas-api", "harbor-infra", "kestrel-web"}
    assert "sections" not in report["projects"]["atlas-api"]
    kestrel = report["projects"]["kestrel-web"]
    assert set(kestrel) == {"sections"}, kestrel
    [launch] = kestrel["sections"]
    first, second = launch["lines"]
    assert [part["text"] for part in first["parts"]][0] == "preset claude · sonnet · medium, in your terminal"
    assert re.fullmatch(LAST, first["parts"][1]["text"]), first
    assert second == {"parts": [{"text": f"names sessions like {OWN_NAME}, its own template"}]}


def test_it_keeps_the_component_contract(full_shell, ui_contexts, ui_component_check):
    page = ui_contexts(full_shell).pages[0]
    ui_component_check(page, "launcher", "project-facts",
                       {"projects": json.dumps(given(full_shell)), "open": "atlas-api"}, tokens=())


# --- the full install ------------------------------------------------------------------------

def test_each_row_counts_what_runs_there_and_draws_no_time_it_was_not_given(
        full_shell, busy, ui_contexts):
    panel = open_list(ui_contexts(full_shell).pages[0])
    expect(panel.locator('[data-part="facts"] > moxi-launcher-project-facts')).to_have_count(1)
    expect(line_two(row(panel.page, "atlas-api"))).to_have_text("2 running")
    expect(line_two(row(panel.page, "harbor-infra"))).to_have_text("1 running")
    for key in ("kestrel-web", "reviewer-home"):
        # Nothing runs there: a quiet row, one line, with no gap for the launcher.
        expect(line_two(row(panel.page, key))).to_be_hidden()
    for key in KEYS:
        # The launcher knows only what runs now and, here, no latest message: no time,
        # and never `not used yet`.
        assert row(panel.page, key).get_attribute("fact") is None, key


def test_the_opened_project_shows_who_runs_where_its_launch_and_its_running_sessions(
        full_shell, busy, ui_contexts):
    page = ui_contexts(full_shell).pages[0]
    item = opened(page, "atlas-api")
    expect_labels(page, item, ["Folder", "Agents", "Launch", "Sessions"])
    expect(lines(item, "Agents")).to_have_text(
        ["builder-1 leads · running now in slot 4 · planner and reviewer also work here"])
    expect(lines(item, "Launch")).to_have_text([re.compile(f"^no preset · {LAST}$"), EVERY])
    expect(lines(item, "Sessions")).to_have_text(["planner · headless", "builder-1 · slot 4"])
    # The Sessions row has no link of the launcher's.
    expect(item.locator('[data-part="opened"] [data-label="Sessions"] a')).to_have_count(0)

    item = opened(page, "harbor-infra")
    expect_labels(page, item, ["Folder", "Agents", "Launch", "Sessions"])
    expect(lines(item, "Agents")).to_have_text(["ops-watch leads · running now in your terminal"])
    expect(lines(item, "Launch")).to_have_text([re.compile(f"^no preset · {LAST}$"), EVERY])
    expect(lines(item, "Sessions")).to_have_text(["ops-watch · terminal"])


def test_a_project_with_nothing_running_shows_its_preset_last_launch_and_template(
        full_shell, ui_contexts):
    page = ui_contexts(full_shell).pages[0]
    item = opened(page, "kestrel-web")
    expect_labels(page, item, ["Folder", "Agents", "Launch"])
    expect(lines(item, "Launch")).to_have_text([
        re.compile(f"^preset claude · sonnet · medium, in your terminal · {LAST}$"),
        f"names sessions like {OWN_NAME}, its own template"])
    # Its lead has no launch running: the launcher says nothing of it.
    expect(lines(item, "Agents")).to_have_text(["builder-2 leads · reviewer also works here"])

    # Never launched there, no preset of its own: what a launch there would take.
    item = opened(page, "reviewer-home")
    expect_labels(page, item, ["Folder", "Agents", "Launch"])
    expect(lines(item, "Launch")).to_have_text(["no preset", EVERY])


def test_removes_step_says_the_running_session_keeps_running(full_shell, busy, ui_contexts):
    page = ui_contexts(full_shell).pages[0]
    page.goto("/m/projects/remove/harbor-infra")
    step = row(page, "harbor-infra").locator('[data-part="remove"]')
    expect(step).to_have_attribute("data-state", "ask")
    expect(step.locator('[data-label="Stays"] .ol p')).to_have_text([
        "the folder ~/code/harbor-infra and every file in it", "the one running now keeps running"])


def test_a_launch_started_and_ended_reaches_the_page_on_the_running_topic(
        full_shell, launches, ui_contexts):
    panel = open_list(ui_contexts(full_shell).pages[0])
    kestrel = row(panel.page, "kestrel-web")
    expect(line_two(kestrel)).to_be_hidden()
    launches.start("kestrel-web", "builder-2", target="slot", slot="2")
    expect(line_two(kestrel)).to_have_text("1 running", timeout=WAIT_S * 1000)
    launches.end()
    expect(line_two(kestrel)).to_be_hidden(timeout=WAIT_S * 1000)


def test_a_failed_catalog_read_drops_the_preset_clause_and_nothing_else(full_shell, ui_contexts):
    page = ui_contexts(full_shell).pages[0]
    # A preset shown without the values of it that can do harm would read safer than it is.
    page.route(re.compile(r"/m/launcher/api/catalog$"),
               lambda route: route.fulfill(status=503, content_type="application/json", body="{}"))
    item = opened(page, "kestrel-web")
    expect_labels(page, item, ["Folder", "Agents", "Launch"])
    expect(lines(item, "Launch")).to_have_text([
        re.compile(f"^{LAST}$"), f"names sessions like {OWN_NAME}, its own template"])


# --- the small install -----------------------------------------------------------------------

def test_the_small_install_places_nothing_of_the_launcher_and_asks_it_nothing(
        small_shell, ui_contexts):
    page = ui_contexts(small_shell).pages[0]
    asked: list[str] = []
    page.on("request", lambda request: asked.append(request.url) if "launcher" in request.url else None)
    panel = open_list(page)
    expect(panel.locator('moxi-ui-row')).to_have_count(len(PROJECTS))
    expect(panel.locator('[data-part="facts"] > *')).to_have_count(0)
    for key in KEYS:
        expect(line_two(row(page, key))).to_be_hidden()
    item = opened(page, "atlas-api")
    page.wait_for_timeout(1000)
    # No row of the launcher's, and no gap where one would be.
    assert labels(item) == ["Folder", "Agents"]
    expect(lines(item, "Agents")).to_have_text(["builder-1 leads · planner and reviewer also work here"])
    assert asked == []
