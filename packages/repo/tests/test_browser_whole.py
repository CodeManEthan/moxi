"""The Git hooks page as a whole in a browser, through a real shell that starts repo's real
page server, on the five repos of `repo_page_kit.seed` with two of them projects' repos:
atlas-api, and quill-mobile, which has no hooks.

- Each address opens what it names: the page, a repo by its project's key or its `~`
  folder, its `Add a piece` list, a project's repo, `Add a repo` and `Pieces`; one that
  names no repo opens the page with a line at its foot.
- The states: `Reading…` while the first read is out; a read that fails after one that
  didn't keeps the last value, dim and with no acts; the listing's own error in the
  verdict's place; a 404 draws nothing.
- At 1280 and 400 px nothing reaches past the right edge, with every part open in turn.
- In the light theme and the dark: no heading of the page's own, and no colour but the
  text's and the surface's tokens, and the warning colour only in a `Turn off` block: on
  its title, its rule, its commands and their `Copy` buttons, drawn in the drawing's colour;
  an opened repo's foot draws its two acts in the text's colour and its links dim.
"""

from __future__ import annotations

import re

import pytest
import repo_page_kit as kit
from playwright.sync_api import expect

pytestmark = kit.MARKS

REPOS = "**/m/repo/api/repos"


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory, hook_env) -> dict:
    return kit.shell_options(tmp_path_factory, hook_env)


@pytest.fixture(scope="module")
def cast(ui_shell, hook_env, tmp_path_factory) -> dict:
    repos = kit.seed(ui_shell, hook_env, kit.old_site(tmp_path_factory))
    repos["quill-mobile"] = kit.make_repo(ui_shell, "code/quill-mobile")
    for key in ("atlas-api", "quill-mobile"):
        kit.moxi(ui_shell, "projects", "add", key, "--root", str(repos[key]))
    return repos


@pytest.fixture
def page(cast, ui_contexts):
    return ui_contexts().pages[0]


def press_each(buttons) -> None:
    """Presses each of `buttons` there is; a `Show` drawn only for more to show may not be."""
    for button in buttons.all():
        button.click()


# --- addresses --------------------------------------------------------------------------------

def test_each_address_opens_what_it_names(cast, page):
    atlas, harbor = cast["atlas-api"], cast["harbor-infra"]
    panel = kit.open_page(page)
    expect(panel.locator("[data-group=repos] moxi-ui-row")).to_have_count(5)
    expect(panel.locator("[data-group=repos] moxi-ui-row[open]")).to_have_count(0)
    expect(panel.locator("[data-part=add-repo]")).to_have_count(0)
    # A project's repo is named by its key, and its project's repo with no hooks is on the
    # `No hooks:` line.
    expect(kit.row(panel, atlas)).to_have_attribute("name", "atlas-api")
    expect(panel.locator("[data-line=no-hooks]")).to_contain_text("No hooks: quill-mobile")

    panel = kit.open_page(page, "repo/atlas-api")
    expect(kit.row(panel, atlas)).to_have_attribute("open", "")
    expect(kit.row(panel, atlas).locator("[data-action=project]")).to_have_text("Project atlas-api")
    expect(panel.locator("[data-part=add-piece]")).to_have_count(0)

    panel = kit.open_page(page, "repo/~!2Fcode!2Fharbor-infra")
    expect(kit.row(panel, harbor)).to_have_attribute("open", "")
    expect(kit.row(panel, harbor).locator("[data-action=project]")).to_have_count(0)

    panel = kit.open_page(page, "repo/atlas-api/add")
    expect(kit.row(panel, atlas)).to_have_attribute("open", "")
    expect(panel.locator("[data-part=add-piece]")).to_be_visible()

    # A project's address opens its repo and is written as the repo's own.
    panel = kit.open_page(page, "project/atlas-api")
    expect(kit.row(panel, atlas)).to_have_attribute("open", "")
    expect(page).to_have_url(re.compile(r"/m/repo/repo/atlas-api$"))

    panel = kit.open_page(page, "add")
    expect(panel.locator("[data-part=add-repo]")).to_be_visible()
    expect(panel.locator("[data-group=repos] moxi-ui-row[open]")).to_have_count(0)

    panel = kit.open_page(page, "pieces")
    expect(panel.locator("[data-group=pieces] [data-action=show-pieces]")).to_have_attribute(
        "aria-expanded", "true")

    for route, name in (("repo/nope", "nope"), ("project/no-such", "no-such")):
        panel = kit.open_page(page, route)
        expect(panel.locator("[data-line=unknown]")).to_have_text(f"No repo is named {name}.")
        expect(panel.locator("[data-group=repos] moxi-ui-row[open]")).to_have_count(0)
        expect(page).to_have_url(re.compile(r"/m/repo/$"))
    # Opening something takes the line away.
    kit.row(panel, atlas).locator("button[data-action=open]").click()
    expect(panel.locator("[data-line=unknown]")).to_have_count(0)
    page.wait_for_url("**/m/repo/repo/atlas-api")


# --- states -----------------------------------------------------------------------------------

def test_reading_shows_after_a_moment_and_goes_once_the_read_is_in(cast, page):
    held = []
    page.route(REPOS, lambda route: held.append(route))
    page.goto("/m/repo/")
    panel = page.locator(kit.PANEL)
    section = panel.locator("section[data-part=page]")
    expect(section).to_have_attribute("data-state", "loading")
    expect(section.locator("[data-line=reading]")).to_have_text("Reading…")
    expect(panel.locator("[data-group]")).to_have_count(0)
    # Unrouting first would let the held request go on its own.
    held[0].continue_()
    page.unroute(REPOS)
    expect(section).to_have_attribute("data-state", "up")
    expect(section.locator("[data-line=reading]")).to_have_count(0)
    expect(panel.locator("[data-group=repos] moxi-ui-row")).to_have_count(5)


def test_a_read_that_fails_keeps_the_last_value_dim_with_no_acts(cast, page):
    panel = kit.open_page(page)
    section = panel.locator("section[data-part=page]")
    expect(panel.locator("[data-group=repos] [data-action=add-repo]")).to_have_count(1)
    page.route(REPOS, lambda route: route.fulfill(status=502, body="bad gateway"))
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(section).to_have_attribute("data-state", "down")
    expect(section.locator("[data-line=failed]")).to_have_text("Git hooks can't be read right now.")
    expect(section.locator(".grps.kept")).to_have_count(1)
    expect(panel.locator("[data-group=repos] moxi-ui-row")).to_have_count(5)
    for act in ("add-repo", "add"):
        expect(panel.locator(f"[data-group] [data-action={act}]")).to_have_count(0)
    # The next read that works brings the acts back.
    page.unroute(REPOS)
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(section).to_have_attribute("data-state", "up")
    expect(panel.locator("[data-group=repos] [data-action=add-repo]")).to_have_count(1)


def test_the_listings_error_stands_in_the_verdicts_place(cast, page):
    detail = "repo's list can't be read: it isn't JSON"

    def failing(route):
        value = route.fetch().json()
        route.fulfill(json={**value, "state": "error", "detail": detail, "repos": []})

    page.route(REPOS, failing)
    page.goto("/m/repo/")
    panel = page.locator(kit.PANEL)
    section = panel.locator("section[data-part=page]")
    expect(section).to_have_attribute("data-state", "error")
    expect(section.locator("[data-group=hooks] [data-line=error]")).to_have_text(detail)
    expect(section.locator("[data-line=verdict]")).to_have_count(0)
    expect(panel.locator("[data-group=repos]")).to_have_count(0)


def test_a_404_draws_nothing(cast, page):
    page.route(REPOS, lambda route: route.fulfill(status=404, body="not found"))
    page.goto("/m/repo/")
    panel = page.locator(kit.PANEL)
    panel.wait_for(state="attached")
    page.wait_for_timeout(1000)
    expect(panel.locator("section[data-part=page]")).to_have_count(0)


# --- widths -----------------------------------------------------------------------------------

@pytest.mark.parametrize("width", [1280, 400])
def test_every_part_fits_the_width(cast, page, ui_width_check, width):
    page.set_viewport_size({"width": width, "height": 900})
    panel = kit.open_page(page)
    press_each(panel.locator("[data-group=hooks] [data-action=show-warnings]"))
    ui_width_check(page, width=width)
    # An opened repo with a broken piece's record shown.
    lumen = kit.opened(panel, cast["lumen-docs"])
    expect(lumen).to_be_visible()
    press_each(panel.locator("[data-group=hooks] button[data-action=show-record]"))
    ui_width_check(page, width=width)
    # atlas-api's Rules, its commands, a Turn off block, then its Add a piece list.
    panel = kit.open_page(page, "repo/atlas-api")
    opened = kit.row(panel, cast["atlas-api"]).locator("[data-part=opened]")
    press_each(opened.locator("[data-action=show-rules]"))
    opened.locator("[data-action=show-commands]").click()
    opened.locator('[data-piece="repo/commit-rules"] [data-action=turn-off]').click()
    expect(opened.locator("[data-part=turn-off]")).to_be_visible()
    ui_width_check(page, width=width)
    panel = kit.open_page(page, "repo/atlas-api/add")
    expect(panel.locator("[data-part=add-piece]")).to_be_visible()
    ui_width_check(page, width=width)
    panel = kit.open_page(page, "add")
    panel.locator("[data-part=add-repo] [data-line=folder]").fill(str(cast["quill-mobile"]))
    expect(panel.locator("[data-part=add-repo] [data-line=folder-said]")).to_contain_text("a git repo")
    ui_width_check(page, width=width)
    panel = kit.open_page(page, "pieces")
    ui_width_check(page, width=width)


# --- themes -----------------------------------------------------------------------------------

#: The drawing's warning colour, as the browser renders it, in each theme: ui's plain
#: theme gives it as its danger token.
WARNING = {"light": "rgb(176, 60, 6)", "dark": "rgb(255, 158, 107)"}
#: The colours a `Turn off` block renders: its rule, its title, its commands, their labels
#: and `Copy` buttons, its `Stays` line, and the page's text.
RENDERED = """(block) => {
  const colour = (node) => getComputedStyle(node).color;
  const all = (selector) => [...block.querySelectorAll(selector)].map(colour);
  const page = document.querySelector("#moxi-panel > moxi-repo-page");
  return {rule: getComputedStyle(block).borderLeftColor, title: colour(block.querySelector(".q")),
          commands: all("[data-line=command] code"), labels: all("[data-line=command] .lbl"),
          copies: all("[data-line=command] [data-action=copy]"),
          stays: colour([...block.querySelectorAll("p")].find((p) => p.textContent.startsWith("Stays"))),
          text: colour(page)};
}"""


#: The colour each of the opened repo's foot buttons renders, by its action.
FOOT = """(foot) => Object.fromEntries([...foot.querySelectorAll("button[data-action]")]
  .map((button) => [button.dataset.action, getComputedStyle(button).color]))"""


def walk(page, scheme: str) -> dict:
    """In `scheme`, each colour token the page reaches and where, with no colour reached
    but the text's, the surface's and the warning's (ui's danger token)."""
    page.emulate_media(color_scheme=scheme)
    seen = page.evaluate(f"({kit.REACHED})()")
    reached = seen["reached"]
    stray = {token: where for token, where in reached.items()
             if token not in kit.TEXT_TOKENS and token != "danger"}
    assert not stray, f"{scheme}: colour from {stray}"
    assert {"text", "text-dim"} <= set(reached), (scheme, reached)
    return seen


def test_in_both_themes_no_heading_and_no_colour_but_text_and_turn_offs_warning(cast, page):
    # Pieces shown and a repo opened each close the other, so each is walked on its own.
    panel = kit.open_page(page, "pieces")
    expect(panel.locator("[data-group=pieces] [data-action=show-pieces]")).to_have_attribute(
        "aria-expanded", "true")
    expect(panel.get_by_role("heading")).to_have_count(0)
    texts = set()
    for scheme in ("light", "dark"):
        seen = walk(page, scheme)
        texts.add(seen["text"])
        assert "danger" not in seen["reached"], (scheme, seen["reached"]["danger"])
    assert len(texts) == 2, f"the text colour is the same in both themes: {texts}"

    page.emulate_media(color_scheme="light")
    panel = kit.open_page(page, "repo/atlas-api")
    press_each(panel.locator("[data-group=hooks] [data-action=show-warnings]"))
    opened = kit.row(panel, cast["atlas-api"]).locator("[data-part=opened]")
    opened.locator('[data-piece="repo/commit-rules"] [data-action=turn-off]').click()
    expect(opened.locator("[data-part=turn-off]")).to_be_visible()
    expect(panel.get_by_role("heading")).to_have_count(0)
    block = opened.locator("[data-part=turn-off]")
    for scheme in ("light", "dark"):
        warned = walk(page, scheme)["reached"].get("danger", [])
        assert warned, f"{scheme}: the Turn off block takes no warning colour"
        assert all(where.startswith("off:") for where in warned), (scheme, warned)
        # The colour the block renders: the drawing's warning colour on its title, its
        # rule, both commands and both Copy buttons, and the text's on its other words.
        drawn = block.evaluate(RENDERED)
        warning, text = WARNING[scheme], drawn["text"]
        assert warning != text, (scheme, drawn)
        assert drawn["rule"] == warning, (scheme, drawn)
        assert drawn["title"] == warning, (scheme, drawn)
        assert drawn["commands"] == [warning, warning], (scheme, drawn)
        assert drawn["copies"] == [warning, warning], (scheme, drawn)
        assert drawn["labels"] == [text, text], (scheme, drawn)
        assert drawn["stays"] == text, (scheme, drawn)
        # The opened repo's foot: its two acts in the text's colour, its links dim.
        foot = opened.locator("[data-part=foot]").evaluate(FOOT)
        assert foot["add-piece"] == foot["refresh"] == text, (scheme, foot)
        assert foot["project"] == foot["settings"] != text, (scheme, foot)
