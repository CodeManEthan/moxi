"""The Projects page as a whole in a browser, on the two installs of projects_page_kit.py:
forty projects, with the find field, the kind filter and the `Earlier` fold; the empty
list on the small install, which stops at what a project is; a report's over-long text
left out of its row; the page at 1280 and 400 px wide with nothing past the right edge;
and every word in the text's two tokens, in the light theme and the dark.
"""

from __future__ import annotations

import re
import sys
import time

import pytest
from playwright.sync_api import expect

import projects_page_kit as kit

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="projects' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

#: Thirty-three projects more than the seven: forty.
TOOLS = tuple(kit.Row(f"tool-{n:02}", f"code/tool-{n:02}", "project", None, (), f"notes/code/tool-{n:02}")
              for n in range(1, 34))
FORTY = kit.SEVEN + TOOLS
#: The tools worked in this week; those after them were last worked in 40 days ago, or
#: never.
RECENT = TOOLS[:20]
OLD = TOOLS[20:26]
DAY_S = 86_400


def open_list(page, state: str = "up"):
    page.goto("/m/projects/")
    panel = page.locator("#moxi-panel > moxi-projects-page")
    expect(panel.locator('section[data-part="list"]')).to_have_attribute("data-state", state)
    return panel


def keys_of(rows) -> list[str]:
    return rows.evaluate_all("rows => rows.map(row => row.dataset.key)")


def expect_keys(rows, keys: list[str]) -> None:
    """Wait until `rows` are those of `keys`, in any order."""
    deadline = time.monotonic() + 10
    while sorted(keys_of(rows)) != sorted(keys) and time.monotonic() < deadline:
        rows.page.wait_for_timeout(50)
    assert sorted(keys_of(rows)) == sorted(keys)


@pytest.fixture
def fixture_page(fixture_shell, ui_contexts):
    kit.reset(fixture_shell)
    yield ui_contexts(fixture_shell).pages[0]
    kit.reset(fixture_shell)
    kit.seed(fixture_shell)


@pytest.fixture
def small_page(small_shell, ui_contexts):
    yield ui_contexts(small_shell).pages[0]
    kit.seed(small_shell, with_notes=False)


# --- forty projects ---------------------------------------------------------------------------

def test_forty_projects_have_the_find_field_the_kind_filter_and_the_earlier_fold(fixture_shell, fixture_page):
    kit.seed(fixture_shell, FORTY)
    now = time.time()
    report = kit.full_report(fixture_shell, now)
    for n, tool in enumerate(RECENT):
        report["projects"][tool.key] = {"at": int(now - 3600 * (n + 1))}
    for tool in OLD:
        report["projects"][tool.key] = {"at": int(now - 40 * DAY_S)}
    kit.say(fixture_shell, report)
    panel = open_list(fixture_page)
    listed = panel.locator('section[data-part="projects"] moxi-ui-row')
    earlier = panel.locator('section[data-part="earlier"]')
    folded = [tool.key for tool in TOOLS[20:]]
    # Not worked in for 30 days, or never: folded under `Earlier`, out of the list.
    expect(earlier.locator('[data-line="earlier"]')).to_have_text(re.compile(r"^13 projects not used since \S"))
    expect_keys(listed, [row.key for row in kit.SEVEN + RECENT])
    expect(panel.locator('[data-line="count"]')).to_have_text("40 projects")
    earlier.get_by_role("button", name="Show").click()
    expect_keys(earlier.locator("moxi-ui-row"), folded)
    earlier.get_by_role("button", name="Hide").click()
    expect(earlier.locator("moxi-ui-row")).to_have_count(0)

    find = panel.get_by_label("Find a project by its name or folder")
    expect(find).to_be_visible()
    find.fill("TOOL-0")
    # Nothing folds while he looks: tool-01 to tool-09, whatever their activity.
    expect_keys(listed, [f"tool-0{n}" for n in range(1, 10)])
    expect(panel.locator('[data-line="shown"]')).to_have_text("9 of 40 projects")
    expect(panel.locator('[data-line="count"]')).to_have_text("9 projects")
    expect(earlier).to_have_count(0)
    # The folder matches too.
    find.fill("~/agents")
    expect_keys(listed, ["reviewer-home"])
    panel.get_by_role("button", name="Clear").click()
    expect(find).to_have_value("")
    expect(panel.locator('[data-line="shown"]')).to_have_count(0)
    expect_keys(listed, [row.key for row in kit.SEVEN + RECENT])

    kind = panel.locator('[data-action="kind"]')
    # In the page's kind words, with the house arrow in place of the browser's own.
    assert kind.locator("option").all_inner_texts() == ["all", "agent's home", "code project"]
    assert kind.evaluate("el => getComputedStyle(el).appearance") == "none"
    expect(panel.locator('[data-part="filters"] .sel .v')).to_have_text("▾")
    kind.select_option("agent")
    expect_keys(listed, ["reviewer-home"])
    expect(panel.locator('[data-line="shown"]')).to_have_text("1 of 40 projects")
    kind.select_option("")
    expect_keys(listed, [row.key for row in kit.SEVEN + RECENT])


# --- the empty list ---------------------------------------------------------------------------

def test_the_small_install_with_no_project_says_what_a_project_is_and_no_more(small_shell, small_page):
    kit.seed(small_shell, (), with_notes=False)
    panel = open_list(small_page, "empty")
    empty = panel.locator('[data-part="empty"]')
    expect(empty.locator('[data-line="empty"]')).to_have_text("No projects yet.")
    expect(empty.locator('[data-line="sentence"]')).to_have_text("A project is a folder with a short name.")
    empty.get_by_role("button", name="Add a project").click()
    expect(panel.locator('moxi-projects-panel [data-part="panel"]')).to_be_visible()
    # No activity source: no folders worked in, no count and no rows.
    expect(panel.locator('[data-part="folders"]')).to_have_count(0)
    expect(panel.locator('[data-line="count"]')).to_have_count(0)
    expect(panel.locator("moxi-ui-row")).to_have_count(0)


# --- a report refused in part -----------------------------------------------------------------

def test_a_part_over_the_texts_length_is_left_out_and_the_rest_drawn(fixture_shell, fixture_page):
    messages: list[str] = []
    fixture_page.on("console", lambda message: messages.append(message.text))
    report = kit.full_report(fixture_shell)
    report["projects"]["orbit-cli"]["parts"].insert(1, {"text": "x" * 201})
    kit.say(fixture_shell, report)
    panel = open_list(fixture_page)
    orbit = panel.locator('moxi-ui-row[data-key="orbit-cli"]')
    expect(orbit).to_have_attribute("tail", "1 active recently · $1.55 today")
    expect(orbit).to_have_attribute("rest", "dep-audit's last run failed at 18:00")
    deadline = time.monotonic() + pytest.importorskip("moxi_ui.testing").WAIT_S
    while not any("over 200 characters" in text for text in messages) and time.monotonic() < deadline:
        fixture_page.wait_for_timeout(50)
    assert any("fixture's project facts refused in part" in text and "over 200 characters" in text
               for text in messages), messages


# --- widths -----------------------------------------------------------------------------------

@pytest.mark.parametrize("width", [1280, 400])
def test_the_list_with_every_fact_an_opened_project_and_its_remove_fit_the_width(
        fixture_shell, fixture_page, ui_width_check, width):
    fixture_page.set_viewport_size({"width": width, "height": 800})
    kit.say(fixture_shell, kit.full_report(fixture_shell))
    panel = open_list(fixture_page)
    expect(panel.locator('section[data-part="folders"] moxi-ui-row')).to_have_count(1)
    ui_width_check(fixture_page, width=width)
    # Each row's time and act stay on its first line.
    for item in panel.locator('section[data-part="projects"] moxi-ui-row').all():
        act = item.locator('[slot="acts-one"]').bounding_box()
        name = item.locator(".l1").bounding_box()
        assert act["y"] < name["y"] + name["height"], (item.get_attribute("data-key"), act, name)
    # `Add a project` is drawn at every width, under the count or beside it, and opens
    # the panel.
    count = panel.locator('section[data-part="projects"] [data-line="count"]')
    add = panel.locator('section[data-part="projects"] .gk [data-action="add"]')
    expect(add).to_be_visible()
    box = add.bounding_box()
    assert box["width"] > 0 and box["x"] + box["width"] <= width, box
    assert box["y"] < count.bounding_box()["y"] + 40, (box, count.bounding_box())
    add.click()
    expect(panel.locator('moxi-projects-panel [data-part="panel"]')).to_be_visible()
    expect(fixture_page).to_have_url(re.compile(r"/m/projects/add$"))

    fixture_page.goto("/m/projects/open/atlas-api")
    expect(panel.locator('moxi-ui-row[data-key="atlas-api"] [data-part="opened"]')).to_be_visible()
    ui_width_check(fixture_page, width=width)
    fixture_page.goto("/m/projects/remove/quill-mobile")
    expect(panel.locator('[data-part="remove"]')).to_have_attribute("data-state", "ask")
    ui_width_check(fixture_page, width=width)
    fixture_page.goto("/m/projects/edit/atlas-api")
    expect(panel.locator('moxi-projects-panel [data-part="panel"]')).to_be_visible()
    ui_width_check(fixture_page, width=width)


@pytest.mark.parametrize("width", [1280, 400])
def test_the_small_install_fits_the_width(small_page, ui_width_check, width):
    small_page.set_viewport_size({"width": width, "height": 800})
    panel = open_list(small_page)
    ui_width_check(small_page, width=width)
    small_page.goto("/m/projects/open/quill-mobile")
    expect(panel.locator('moxi-ui-row[data-key="quill-mobile"] [data-part="opened"]')).to_be_visible()
    ui_width_check(small_page, width=width)


# --- themes -----------------------------------------------------------------------------------

#: Every shown element with words of its own, under the page and the rows and the panel
#: it draws, with its text colour; and the two text tokens' colours, measured there.
_COLOURS = """() => {
  const host = document.querySelector('#moxi-panel > moxi-projects-page');
  const probe = (token) => {
    const span = document.createElement('span');
    span.style.color = `var(${token})`;
    host.shadowRoot.append(span);
    const colour = getComputedStyle(span).color;
    span.remove();
    return colour;
  };
  const seen = [];
  const walk = (root) => {
    for (const el of root.querySelectorAll('*')) {
      if (el.shadowRoot) walk(el.shadowRoot);
      if (el.getClientRects().length === 0 || el.closest('[hidden]')) continue;
      const own = [...el.childNodes].some((node) => node.nodeType === 3 && node.textContent.trim() !== '');
      if (own) seen.push({ text: el.textContent.trim().slice(0, 40), colour: getComputedStyle(el).color });
    }
  };
  walk(host.shadowRoot);
  return { text: probe('--moxi-text'), dim: probe('--moxi-text-dim'), seen };
}"""


def test_every_word_is_in_a_text_token_in_the_light_theme_and_the_dark(fixture_shell, fixture_page):
    kit.say(fixture_shell, kit.full_report(fixture_shell))
    fixture_page.goto("/m/projects/open/atlas-api")
    panel = fixture_page.locator("#moxi-panel > moxi-projects-page")
    expect(panel.locator('moxi-ui-row[data-key="atlas-api"] [data-label="Services"]')).to_be_visible()
    expect(panel.locator('section[data-part="folders"] moxi-ui-row')).to_have_count(1)
    expect(panel.locator('moxi-ui-row[data-key="lumen-docs"]')).to_have_attribute("rest-strong", "")
    tokens = {}
    for scheme in ("light", "dark"):
        fixture_page.emulate_media(color_scheme=scheme)
        fixture_page.wait_for_timeout(100)
        found = fixture_page.evaluate(_COLOURS)
        tokens[scheme] = (found["text"], found["dim"])
        assert len(found["seen"]) > 50, found["seen"]
        stray = [item for item in found["seen"] if item["colour"] not in (found["text"], found["dim"])]
        assert stray == [], (scheme, tokens[scheme], stray)
    assert tokens["light"] != tokens["dark"], tokens
