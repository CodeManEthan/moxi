"""The Projects (or Folders) and Models sections of usage's page in a browser, through a
real shell that starts usage's real page server: each closed row naming the dearest
few with its count as the link that opens it, the opened rows with their share and
cost, a project row setting the Project filter and opening Sessions, the one `no
project` row and the sentence whose folder filters to itself, each section's `Table`,
the plain Models rows, the addresses that open them, and an opened section at a phone's
width; then on a shell that leaves projects out, Folders written whole with no `no
project` row and a row setting the Folder filter.

The projects are added, the transcripts written and the index built in the shell's own
environment, so they sit under the shell's home where its page servers read them. No
test changes what the index holds, so the tests hold under pytest-xdist in any order.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser,
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="usage's page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

SONNET = "claude-sonnet-4-5"
HAIKU = "claude-haiku-4-5"
#: Two sessions in `proj-a`, one in `proj-b` and one short one in a folder of no project,
#: each folder under the shell's home. `proj-a` costs most and the loose folder least.
SESSIONS = [
    {"session": "0190b000-0000-7000-8000-0000000000b1", "folder": "proj-a", "model": SONNET, "requests": 3},
    {"session": "0190b000-0000-7000-8000-0000000000b2", "folder": "proj-a", "model": SONNET, "requests": 3},
    {"session": "0190b000-0000-7000-8000-0000000000b3", "folder": "proj-b", "model": HAIKU, "requests": 3},
    {"session": "0190b000-0000-7000-8000-0000000000b4", "folder": "loose", "model": SONNET, "requests": 1},
]
PROJECTS = ("proj-a", "proj-b")

#: Writes the sessions of the JSON list in argv[1] under the harness roots of the
#: environment it runs in, each in its folder under argv[2], the newest an hour old.
SEED = """
import json, sys, time
from pathlib import Path
from moxi_sessions import api as sessions
T = sessions.testing
now = time.time()
for n, spec in enumerate(json.loads(sys.argv[1])):
    cwd = sys.argv[2] + "/" + spec["folder"]
    t = T.ClaudeTranscript(Path(sessions.root("claude")), session_id=spec["session"],
                           project=cwd.replace("/", "-"), cwd=cwd, start=now - 3600 * (n + 1))
    for m in range(spec["requests"]):
        t.user("Fix the failing test.")
        t.assistant("Done.", usage=T.usage(input=200, output=1000 + m * 500),
                    request_id=f"req_{spec['session'][-2:]}{m}", model=spec["model"])
    t.write()
"""

MONEY = r"(at least )?\$\d[\d,]*\.\d\d"


def run(shell, *argv: str) -> str:
    done = subprocess.run(list(argv), env=shell.env, capture_output=True, text=True,
                          timeout=120)
    if done.returncode != 0:
        raise AssertionError(f"{argv} failed ({done.returncode}):\n{done.stdout}{done.stderr}")
    return done.stdout


def seeded_shell(ui_shells, *, projects: bool, **options):
    shell = ui_shells(**options)
    work = Path(shell.env["HOME"]) / "work"
    for spec in SESSIONS:
        (work / spec["folder"]).mkdir(parents=True, exist_ok=True)
    if projects:
        for key in PROJECTS:
            done = shell.moxi("projects", "add", key, "--root", str(work / key))
            assert done.returncode == 0, done.stdout + done.stderr
    run(shell, sys.executable, "-c", SEED, json.dumps(SESSIONS), str(work))
    run(shell, sys.executable, "-m", "moxi", "usage", "index")
    return shell


@pytest.fixture(scope="module")
def full(ui_shells, ui_shell_options):
    """A shell with projects, two of the folders being projects."""
    return seeded_shell(ui_shells, projects=True, **ui_shell_options)


@pytest.fixture(scope="module")
def small(ui_shells, ui_shell_options):
    """The same sessions on a shell that leaves projects out."""
    return seeded_shell(ui_shells, projects=False, **{**ui_shell_options, "leave_out": ["projects"]})


@pytest.fixture
def page(full, ui_contexts):
    return ui_contexts(full).pages[0]


def open_page(page, route: str = ""):
    page.goto(f"/m/usage/{route}")
    panel = page.locator("#moxi-panel > moxi-usage-page")
    expect(panel.locator("[data-part=filters]")).to_be_visible()
    return panel


def section_of(panel, key: str):
    return panel.locator(f"[data-part=below] section[data-group={key}]")


def rows_of(section):
    return section.locator("[data-part=rows] .tr2")


def place_button(panel):
    return panel.locator("[data-part=filters] [data-filter=place] button.pick")


# --- Projects ------------------------------------------------------------------------------

def test_projects_closed_names_the_dearest_three_and_its_count_opens_it(page):
    panel = open_page(page)
    section = section_of(panel, "projects")
    expect(section.locator(".gk")).to_have_text("Projects")
    expect(section.locator("[data-part=closed]")).to_have_text(
        re.compile(rf"^proj-a {MONEY}, proj-b {MONEY}, no project {MONEY}$"))
    count = section.locator("[data-part=count]")
    expect(count).to_have_text("3 projects")
    expect(rows_of(section)).to_have_count(0)
    # The sections follow the chart: Projects, Models, Token kinds, Prices.
    below = panel.locator("[data-part=below] > section")
    expect(below).to_have_count(6)
    order = below.evaluate_all("nodes => nodes.map((node) => node.dataset.group)")
    assert order == ["chart", "sessions", "projects", "models", "kinds", "prices"], order

    count.click()
    expect(page).to_have_url(re.compile(r"/m/usage/projects$"))
    expect(count).to_have_text("Close")
    expect(rows_of(section)).to_have_count(3)
    expect(rows_of(section).locator(".nm")).to_have_text(["proj-a", "proj-b", "no project"])
    shares = rows_of(section).locator(".n.d").all_inner_texts()
    assert all(re.fullmatch(r"\d+%|under 1%", share) for share in shares), shares
    assert sum(int(share.rstrip("%")) for share in shares if share[0].isdigit()) <= 100, shares
    for cost in rows_of(section).locator(".n:not(.d)").all_inner_texts():
        assert re.fullmatch(MONEY, cost), cost
    count.click()
    expect(page).to_have_url(re.compile(r"/m/usage/$"))
    expect(rows_of(section)).to_have_count(0)


def test_files_with_no_project_are_one_row_and_the_sentence_names_their_folder(page):
    panel = open_page(page, "projects")
    section = section_of(panel, "projects")
    loose = section.locator('[data-row="no-project"]')
    expect(loose).to_have_count(1)
    # It sets no filter, so it is no link.
    expect(section.locator('a[data-row="no-project"]')).to_have_count(0)
    expect(section.locator("[data-part=sentence]")).to_have_text(
        '"no project" is ~/work/loose. A project row opens the Sessions section above, filtered to it.')
    section.locator("[data-part=sentence] a", has_text="~/work/loose").click()
    expect(page).to_have_url(re.compile(r"/m/usage/sessions$"))
    expect(place_button(panel)).to_have_text("~/work/loose▾")
    expect(panel.locator("section[data-group=sessions] moxi-ui-row")).to_have_count(1)


def test_a_project_row_sets_the_project_filter_and_opens_sessions(page):
    panel = open_page(page, "projects")
    section = section_of(panel, "projects")
    with page.expect_request(lambda request: "/m/usage/api/sessions" in request.url
                             and "project=proj-b" in request.url):
        section.locator('a[data-row="project:proj-b"]').click()
    expect(page).to_have_url(re.compile(r"/m/usage/sessions$"))
    expect(place_button(panel)).to_have_text("proj-b▾")
    expect(panel.locator("section[data-group=sessions] moxi-ui-row")).to_have_count(1)
    # Sessions is open, so Projects is closed again.
    expect(rows_of(section)).to_have_count(0)
    # Under the filter Projects holds that project alone, the whole of what is shown.
    section.locator("[data-part=count]").click()
    expect(rows_of(section).locator(".nm")).to_have_text(["proj-b"])
    expect(rows_of(section).locator(".n.d")).to_have_text(["100%"])


def test_the_projects_table_has_every_row_with_its_folder_and_figures(page):
    panel = open_page(page, "projects")
    section = section_of(panel, "projects")
    toggle = section.locator("[data-part=table-toggle]")
    expect(toggle).to_have_text("Table")
    toggle.click()
    table = section.locator("table[data-part=table]")
    expect(table.locator("th")).to_have_text(["Project", "Folder", "Models", "Requests", "Tokens", "Cost"])
    expect(table.locator("tbody tr")).to_have_count(3)
    loose = table.locator("tbody tr").filter(has_text="no project")
    expect(loose.locator("td")).to_have_text(["no project", "~/work/loose", SONNET, "1", re.compile(r"\S"),
                                              re.compile(MONEY)])
    expect(table.locator("tbody tr").first.locator("td").nth(3)).to_have_text("6")
    expect(toggle).to_have_text("Hide table")
    toggle.click()
    expect(table).to_have_count(0)


# --- Models --------------------------------------------------------------------------------

def test_models_closed_names_the_dearest_two_and_opened_has_plain_rows(page):
    panel = open_page(page)
    section = section_of(panel, "models")
    expect(section.locator(".gk")).to_have_text("Models")
    expect(section.locator("[data-part=closed]")).to_have_text(
        re.compile(rf"^{SONNET} {MONEY}, {HAIKU} {MONEY}$"))
    expect(section.locator("[data-part=count]")).to_have_text("2 models")
    section.locator("[data-part=count]").click()
    expect(page).to_have_url(re.compile(r"/m/usage/models$"))
    expect(rows_of(section).locator(".nm")).to_have_text([SONNET, HAIKU])
    expect(section.locator("a.tr2")).to_have_count(0)
    expect(section.locator("[data-part=sentence]")).to_have_count(0)
    section.locator("[data-part=table-toggle]").click()
    expect(section.locator("table[data-part=table] th")).to_have_text(
        ["Model", "Priced by", "Requests", "Tokens", "Cost"])
    # One section is open at a time.
    expect(rows_of(section_of(panel, "projects"))).to_have_count(0)


def test_an_opened_section_fits_a_phone(full, page, ui_width_check):
    ui_width_check(page, module="usage", route="projects")
    panel = page.locator("#moxi-panel > moxi-usage-page")
    section = section_of(panel, "projects")
    expect(rows_of(section)).to_have_count(3)
    section.locator("[data-part=table-toggle]").click()
    expect(section.locator("table[data-part=table]")).to_be_visible()
    ui_width_check(page)


# --- Token kinds at a desk's width -----------------------------------------------------------

def test_a_long_token_kinds_row_keeps_its_count_where_every_rows_count_ends(full, ui_contexts):
    page = ui_contexts(full, viewport={"width": 1280, "height": 900}).pages[0]

    def long_figures(route):
        answer = route.fetch()
        body = answer.json()
        # Figures in the billions, each `at least`: the closed row's longest words.
        body["total"]["unknown_rows"] = 3
        for item in body["total"]["split"]:
            item["cost"] *= 1e7
        route.fulfill(response=answer, json=body)

    page.route(re.compile(r"/m/usage/api/terms\?(?!.*by=agent)"), long_figures)
    panel = open_page(page)
    kinds = section_of(panel, "kinds")
    expect(kinds.locator("[data-part=closed]")).to_contain_text("at least $")
    count = kinds.locator("[data-part=count]")
    expect(count).to_have_text(re.compile(r" tokens$"))
    row = kinds.locator(".one").bounding_box()
    link = count.bounding_box()
    other = section_of(panel, "models").locator("[data-part=count]").bounding_box()
    assert link["x"] + link["width"] <= row["x"] + row["width"] + 0.5, (link, row)
    assert abs((link["x"] + link["width"]) - (other["x"] + other["width"])) <= 1, (link, other)
    words = kinds.locator("[data-part=closed]").bounding_box()
    assert words["x"] + words["width"] <= link["x"], (words, link)


# --- without projects ----------------------------------------------------------------------

def test_without_projects_folders_are_written_whole_with_no_row_for_no_project(small, ui_contexts):
    page = ui_contexts(small).pages[0]
    panel = open_page(page)
    expect(section_of(panel, "projects")).to_have_count(0)
    section = section_of(panel, "folders")
    expect(section.locator(".gk")).to_have_text("Folders")
    expect(section.locator("[data-part=closed]")).to_have_text(
        re.compile(rf"^~/work/proj-a {MONEY}, ~/work/proj-b {MONEY}$"))
    expect(section.locator("[data-part=count]")).to_have_text("3 folders")

    # The Projects address opens Folders on this install, and says so.
    panel = open_page(page, "projects")
    section = section_of(panel, "folders")
    expect(page).to_have_url(re.compile(r"/m/usage/folders$"))
    expect(rows_of(section).locator(".nm")).to_have_text(["~/work/proj-a", "~/work/proj-b", "~/work/loose"])
    expect(section.get_by_text("no project")).to_have_count(0)
    expect(section.locator("[data-part=sentence]")).to_have_text(
        "A folder row opens the Sessions section above, filtered to it.")
    with page.expect_request(lambda request: "/m/usage/api/sessions" in request.url
                             and "folder=" in request.url):
        rows_of(section).nth(1).click()
    expect(page).to_have_url(re.compile(r"/m/usage/sessions$"))
    expect(panel.locator("[data-part=filters] [data-filter=place] .k")).to_have_text("Folder")
    expect(place_button(panel)).to_have_text("~/work/proj-b▾")
    expect(panel.locator("section[data-group=sessions] moxi-ui-row")).to_have_count(1)


def test_the_folders_table_has_no_project_column(small, ui_contexts):
    page = ui_contexts(small).pages[0]
    panel = open_page(page, "folders")
    section = section_of(panel, "folders")
    section.locator("[data-part=table-toggle]").click()
    expect(section.locator("table[data-part=table] th")).to_have_text(
        ["Folder", "Models", "Requests", "Tokens", "Cost"])
