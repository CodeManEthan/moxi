"""Which project does this name lead? comms' page in a browser, on a test shell over the rig,
with projects' own page server reading a store this test writes.

With projects installed, an idle name in `Other names` that leads a project says
`leads <key>` on its shown row's line two; one that leads nothing says nothing, and an agent
row never says it. A lead changed or a project removed while the page is open shows at the
page's next read of projects, which a test drives by showing the tab again; a read that
fails shows nothing, and the next read that answers shows the leads. Without projects
nothing of it shows, and nothing asks projects.

The rig's standing names are `herald` and `main@proj-a`, `main@proj-b`; `planner` and
`reviewer` hold sessions. The store is written in this process at the place the shell's
environment gives it, so projects' page server reads what the test wrote.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import expect

import roster_kit
from conftest import cache_absent
from roster_kit import holding, show_tab
from test_browser_page import (  # noqa: F401 -- `page_shell` is a fixture the tests take
    _PUSHED, PAGE, SMALL, agent, bound, page_shell,
)
from test_reply_enforcement import PLANNER, REVIEWER, P, R

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120, method="thread"),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="comms runs on Linux only"),
              pytest.mark.parametrize("backend", ["local"], indirect=True)]

needs_projects = pytest.mark.skipif(importlib.util.find_spec("moxi_projects") is None,
                                    reason="the projects module is not installed here")

#: The small install with projects: every module but the core, ui, comms and projects left out.
SMALL_WITH_PROJECTS = [module for module in SMALL if module != "projects"]
#: Each project's key and lead: an idle standing name, an agent with a session, and none.
PROJECTS = (("atlas-api", P), ("harbor-infra", "herald"), ("reviewer-home", None))


def seed(shell: Any) -> None:
    """The shell's projects store holds `PROJECTS`, each root a folder under its home."""
    from moxi_projects import store

    home = Path(shell.env["HOME"])

    def fill(conn) -> None:
        for key, lead in PROJECTS:
            root = home / "code" / key
            root.mkdir(parents=True, exist_ok=True)
            store.insert(conn, key=key, root=str(root), kind="project", lead=lead)

    store.write(fill, where=store.path(shell.env))


def change(shell: Any, fill: Any) -> None:
    """Run `fill(conn, store)` as one write to the shell's projects store."""
    from moxi_projects import store

    store.write(lambda conn: fill(conn, store), where=store.path(shell.env))


#: Any value of an attribute: a row without `lead` fails it, so `not_to_have_attribute`
#: waits for the attribute to go.
ANY = re.compile(r".*")


def small_with_projects(page_shell: Any, rig: Any, monkeypatch: Any) -> Any:
    """The small install with projects over the rig, with planner and reviewer bound and
    the store seeded; the shell, once its roster holds them."""
    cache_absent(monkeypatch)
    rig.shim(**PLANNER)
    rig.shim(**REVIEWER)
    bound(rig, P, R)
    shell = page_shell(leave_out=SMALL_WITH_PROJECTS)
    seed(shell)
    roster_kit.current(shell, holding([P, R]))
    return shell


def open_others(shell: Any, ui_contexts: Any, before: Any = None) -> tuple[Any, list[str]]:
    """A signed-in page on the shell with `Other names` shown, `before(page)` run before it
    loads; its root, and every URL under `/m/projects/` it asked."""
    page = ui_contexts(shell).pages[0]
    if before is not None:
        before(page)
    asked: list[str] = []
    page.on("request", lambda request: asked.append(request.url) if "/m/projects/" in request.url else None)
    page.add_init_script(_PUSHED)
    page.goto(PAGE)
    page.wait_for_function("() => window.pushed.includes('ui.modules')")
    root = page.locator("#moxi-panel > moxi-comms-page")
    expect(root.locator('[data-line="others"]')).to_contain_text("herald")
    root.locator('[data-action="show-others"]').click()
    expect(root.locator('[data-group="others"] moxi-ui-row')).to_have_count(3)
    return root, asked


@needs_projects
def test_an_idle_name_says_which_project_it_leads(page_shell, rig, ui_contexts, monkeypatch):
    shell = small_with_projects(page_shell, rig, monkeypatch)
    root, asked = open_others(shell, ui_contexts)

    herald = agent(root, "herald")
    expect(herald).to_have_attribute("lead", "leads harbor-infra")
    expect(herald.locator(".lead")).to_have_text("leads harbor-infra")
    # main leads nothing, on either of its addresses; the same read drew herald's.
    for qualified in ("main@proj-a", "main@proj-b"):
        assert agent(root, qualified).get_attribute("lead") is None
    # planner leads atlas-api, but it is an agent row: that row never says so.
    assert "leads" not in (agent(root, P).get_attribute("lead") or "")
    assert any("/m/projects/api/projects" in url for url in asked), asked


@needs_projects
def test_a_lead_changed_or_a_project_removed_shows_at_the_next_read(page_shell, rig, ui_contexts, monkeypatch):
    shell = small_with_projects(page_shell, rig, monkeypatch)
    root, _ = open_others(shell, ui_contexts)
    herald, main = agent(root, "herald"), agent(root, "main@proj-a")
    expect(herald).to_have_attribute("lead", "leads harbor-infra")

    # harbor-infra's lead is main now: main speaks through its first address.
    change(shell, lambda conn, store: store.update(conn, "harbor-infra", lead="main"))
    show_tab(root.page)
    expect(main).to_have_attribute("lead", "leads harbor-infra")
    expect(herald).not_to_have_attribute("lead", ANY)
    assert agent(root, "main@proj-b").get_attribute("lead") is None

    # harbor-infra is gone: nobody leads it.
    change(shell, lambda conn, store: store.delete(conn, "harbor-infra"))
    show_tab(root.page)
    expect(main).not_to_have_attribute("lead", ANY)
    expect(herald).not_to_have_attribute("lead", ANY)


@needs_projects
def test_a_failed_read_of_projects_shows_nothing_and_the_next_one_shows_the_leads(
        page_shell, rig, ui_contexts, monkeypatch):
    shell = small_with_projects(page_shell, rig, monkeypatch)
    failed: list[str] = []

    def fail(route: Any) -> None:
        failed.append(route.request.url)
        route.fulfill(status=500, content_type="application/json", body="{}")

    root, _ = open_others(shell, ui_contexts,
                          lambda page: page.route("**/m/projects/api/projects*", fail))
    herald = agent(root, "herald")
    roster_kit.wait_for(lambda: failed, 10, what="the page to read projects")
    # The read answered 500 before `Show` drew the rows: nothing of it shows, and the rest
    # of the page stands.
    for qualified in ("herald", "main@proj-a", "main@proj-b"):
        assert agent(root, qualified).get_attribute("lead") is None
    expect(agent(root, P)).to_have_count(1)
    expect(root.locator('[data-line="answering"]')).to_contain_text("Comms is answering")

    root.page.unroute("**/m/projects/api/projects*")
    show_tab(root.page)
    expect(herald).to_have_attribute("lead", "leads harbor-infra")


def test_without_projects_nothing_shows_and_nothing_asks_it(page_shell, rig, ui_contexts, monkeypatch):
    cache_absent(monkeypatch)
    rig.shim(**PLANNER)
    rig.shim(**REVIEWER)
    bound(rig, P, R)
    shell = page_shell(leave_out=SMALL)
    if importlib.util.find_spec("moxi_projects") is not None:
        # A store that names herald a lead, which a page without projects never reads.
        seed(shell)
    roster_kit.current(shell, holding([P, R]))
    root, asked = open_others(shell, ui_contexts)

    # The page would have asked projects when the folded line first drew an idle name,
    # before `Show` was pressed.
    for qualified in ("herald", "main@proj-a", "main@proj-b"):
        assert agent(root, qualified).get_attribute("lead") is None
    assert asked == []
