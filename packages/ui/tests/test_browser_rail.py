"""`<moxi-ui-rail>` in a browser: each state's marker, its word, count and `here`, the line
solid through what is settled and dashed after, the cell widths, a wide mark's children,
the `moxi-rail-mark` event for a click, Enter and Space, a quiet mark, `open`, the small
and vertical rails, and the three state colours in every colour set.

The rails are put in the shell's panel by the test: the element is the client's, so no
module's page is needed to draw one."""

from __future__ import annotations

import colorsys
import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(90)]

TOKENS = json.loads((Path(__file__).resolve().parents[1] / "web" / "tokens.json").read_text())

STATES = ["done", "none", "yours", "failed", "owed", "waiting", "todo", "child"]

_JS_RAILS = """(rails) => {
  const box = document.createElement("div");
  box.id = "rails";
  for (const [id, attrs, marks] of rails) {
    const rail = document.createElement("moxi-ui-rail");
    rail.id = id;
    for (const [name, value] of Object.entries(attrs)) rail.setAttribute(name, value);
    marks.forEach(([markAttrs, inner], i) => {
      const mark = document.createElement("moxi-ui-mark");
      mark.id = `${id}-${i}`;
      for (const [name, value] of Object.entries(markAttrs)) mark.setAttribute(name, value);
      for (const [tag, text] of inner ?? []) {
        if (tag === "") {
          mark.append(text);
          continue;
        }
        const node = document.createElement(tag);
        node.textContent = text;
        if (tag === "button") {
          node.type = "button";
          node.addEventListener("click", () => window.__pressed.push(text));
        }
        mark.append(node);
      }
      rail.append(mark);
    });
    box.append(rail);
  }
  window.__marks = [];
  window.__pressed = [];
  box.addEventListener("moxi-rail-mark", (event) => {
    window.__marks.push([event.target.id, event.detail, event.bubbles, event.composed]);
  });
  document.getElementById("moxi-panel").replaceChildren(box);
}"""


def _place(page, rails):
    """Puts rails in the panel: each `(id, rail attributes, marks)`, a mark being
    `(attributes, children or None)`, each child `(tag, text)`, a text node's tag empty. Mark `i` of rail `r` has the id `r-i`."""
    page.evaluate(_JS_RAILS, [[rid, attrs, [list(mark) for mark in marks]] for rid, attrs, marks in rails])


def _marks(page) -> list:
    return page.evaluate("() => window.__marks")


def _cell(page, mark_id: str):
    return page.locator(f"#{mark_id}").locator(".cell")


def _style(locator, prop: str) -> str:
    return locator.evaluate("(node, prop) => getComputedStyle(node).getPropertyValue(prop)", prop)


def _token(page, name: str) -> str:
    """The colour `--moxi-<name>` resolves to, as a computed style gives it."""
    return page.evaluate("""(name) => {
      const probe = document.createElement("span");
      probe.style.color = `var(--moxi-${name})`;
      document.body.append(probe);
      const colour = getComputedStyle(probe).color;
      probe.remove();
      return colour;
    }""", name)


def _rgb(hex_value: str) -> str:
    return "rgb({}, {}, {})".format(*(int(hex_value[i:i + 2], 16) for i in (1, 3, 5)))


def _hue(rgb: str) -> float:
    r, g, b = (int(part) / 255 for part in re.findall(r"\d+", rgb)[:3])
    return colorsys.rgb_to_hls(r, g, b)[0] * 360


def _box(locator) -> dict:
    return locator.evaluate("(node) => { const r = node.getBoundingClientRect();"
                            " return {x: r.x, y: r.y, width: r.width, height: r.height}; }")


def _focused(page):
    """The id of the mark whose marker holds the focus, or None."""
    return page.evaluate("""() => {
      const mark = document.activeElement;
      const inner = mark?.shadowRoot?.activeElement;
      return inner && inner.classList.contains("mk") ? mark.id : null;
    }""")


def test_each_state_draws_its_marker_and_an_unset_or_unknown_state_draws_none(ui_page):
    errors: list[str] = []
    ui_page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    ui_page.on("pageerror", lambda err: errors.append(str(err)))
    marks = [({"state": state, "key": state}, None) for state in STATES]
    marks += [({}, None), ({"state": "bogus", "label": "x"}, None)]
    _place(ui_page, [("r", {}, marks)])
    for i, state in enumerate(STATES):
        cell = _cell(ui_page, f"r-{i}")
        expect(cell).to_have_class(re.compile(rf"\bs-{state}\b"))
        expect(cell.locator("button.mk svg")).to_have_count(1)
        # Its shape's first part carries the state's class.
        assert cell.locator("svg > *").first.get_attribute("class") == f"p-{state}"
    # The fallback names: the state in words.
    expect(ui_page.get_by_role("button", name="done", exact=True)).to_have_count(1)
    expect(ui_page.get_by_role("button", name="waiting on you", exact=True)).to_have_count(1)
    expect(ui_page.get_by_role("button", name="built in a round of its own")).to_have_count(1)
    for i in (8, 9):
        expect(_cell(ui_page, f"r-{i}").locator(".mk")).to_have_count(0)
    expect(ui_page.locator("#r").get_by_role("button")).to_have_count(8)
    assert errors == []


def test_word_count_label_and_here_are_drawn_and_text_stays_text(ui_page):
    _place(ui_page, [("r", {}, [
        ({"state": "done", "word": "v8", "label": "design: done, v8", "settled": ""}, None),
        ({"state": "yours", "count": "3", "here": "", "label": "docket: 3 waiting on you"}, None),
        ({"state": "failed", "word": "<b>red</b>"}, None),
        ({"state": "todo"}, None),
    ])])
    expect(ui_page.get_by_role("button", name="design: done, v8")).to_be_visible()
    expect(_cell(ui_page, "r-0").locator(".word")).to_have_text("v8")
    expect(_cell(ui_page, "r-1").locator(".count")).to_have_text("3")
    expect(_cell(ui_page, "r-1").locator(".here")).to_have_count(1)
    expect(_cell(ui_page, "r-0").locator(".here")).to_have_count(0)
    expect(_cell(ui_page, "r-2").locator(".word")).to_have_text("<b>red</b>")
    expect(ui_page.get_by_role("button", name="failed or sent back, <b>red</b>")).to_be_visible()
    word = _cell(ui_page, "r-0").locator(".word")
    assert _style(word, "font-size") == "11px"
    # The word sits under the marker, in the cell's middle.
    svg, under = _box(_cell(ui_page, "r-0").locator("svg")), _box(word)
    assert under["y"] >= svg["y"] + svg["height"] - 2
    assert abs((under["x"] + under["width"] / 2) - (svg["x"] + svg["width"] / 2)) < 1
    # A changed attribute is drawn again; a removed one goes.
    ui_page.locator("#r-0").evaluate("(node) => { node.setAttribute('word', 'v9'); node.removeAttribute('label'); }")
    expect(_cell(ui_page, "r-0").locator(".word")).to_have_text("v9")
    expect(ui_page.get_by_role("button", name="done, v9")).to_be_visible()
    ui_page.locator("#r-1").evaluate("(node) => node.removeAttribute('count')")
    expect(_cell(ui_page, "r-1").locator(".count")).to_have_count(0)


def test_the_line_is_solid_through_settled_marks_to_the_first_not_settled_and_dashed_after(ui_page):
    _place(ui_page, [("r", {}, [
        ({}, None),
        ({"state": "done", "settled": ""}, None),
        ({"state": "none", "settled": ""}, None),
        ({}, None),
        ({"state": "owed"}, None),
        ({"state": "done", "settled": ""}, None),
        ({"state": "todo"}, None),
        ({}, None),
    ])])
    done = _token(ui_page, "state-done")
    strong = _token(ui_page, "border-strong")

    def halves(i):
        cell = _cell(ui_page, f"r-{i}")
        out = []
        for part in (".before", ".after"):
            node = cell.locator(part)
            if not node.is_visible():
                out.append(None)
                continue
            style = _style(node, "border-top-style")
            colour = _style(node, "border-top-color")
            width = _style(node, "border-top-width")
            out.append({("solid", done, "3px"): "=", ("dashed", strong, "1px"): "-"}[(style, colour, width)])
        return out

    assert [halves(i) for i in range(8)] == [
        [None, None], [None, "="], ["=", "="], ["=", "="], ["=", "-"], ["-", "-"], ["-", None], [None, None],
    ]
    # Settling the owed mark carries the solid line on to the next one not settled.
    ui_page.locator("#r-4").evaluate("(node) => { node.setAttribute('state', 'done'); node.setAttribute('settled', ''); }")
    expect(_cell(ui_page, "r-6").locator(".before")).to_have_class(re.compile(r"\bsolid\b"))
    assert halves(5) == ["=", "="]
    # The solid line runs through each marker, at its middle.
    line, svg = _box(_cell(ui_page, "r-1").locator(".after")), _box(_cell(ui_page, "r-1").locator("svg"))
    assert abs((line["y"] + 1.5) - (svg["y"] + svg["height"] / 2)) <= 1


def test_cells_are_56_px_and_a_wide_one_132_and_both_follow_their_properties(ui_page):
    _place(ui_page, [("r", {}, [
        ({"state": "done", "settled": ""}, None),
        ({"wide": "", "state": "done"}, [["span", "B1"], ["", " billing-export "], ["button", "+1"]]),
        ({"state": "todo"}, None),
    ])])
    assert _box(ui_page.locator("#r-0"))["width"] == 56
    assert _box(ui_page.locator("#r-1"))["width"] == 132
    assert _box(ui_page.locator("#r-0"))["height"] == 34
    rows = _box(ui_page.locator("#r-0"))["y"], _box(ui_page.locator("#r-1"))["y"], _box(ui_page.locator("#r-2"))["y"]
    assert len(set(rows)) == 1
    # A wide mark shows its children over the line and draws no marker of its own, even
    # with a state; the line passes behind it.
    wide = _cell(ui_page, "r-1")
    expect(wide.locator(".mk")).to_have_count(0)
    expect(ui_page.locator("#r-1")).to_contain_text("B1 billing-export +1")
    expect(wide.locator(".before")).to_have_class(re.compile(r"\bsolid\b"))
    ui_page.locator("#r").evaluate("(node) => { node.style.setProperty('--moxi-rail-cell', '40px');"
                                   " node.style.setProperty('--moxi-rail-label', '100px'); }")
    assert _box(ui_page.locator("#r-0"))["width"] == 40
    assert _box(ui_page.locator("#r-1"))["width"] == 100
    # Its button stays the page's: a click on it is the page's and sends no mark event.
    ui_page.get_by_role("button", name="+1").click()
    assert ui_page.evaluate("() => window.__pressed") == ["+1"]
    assert _marks(ui_page) == []


def test_a_click_enter_or_space_on_a_marker_sends_one_event_from_the_rail_with_its_key(ui_page):
    _place(ui_page, [("r", {}, [
        ({"state": "done", "key": "design", "label": "design"}, None),
        ({"state": "failed", "key": "check|B1", "label": "check of B1"}, None),
        ({"state": "todo", "label": "finish of B1"}, None),
    ])])
    ui_page.get_by_role("button", name="design").click()
    assert _marks(ui_page) == [["r", {"key": "design"}, True, True]]
    ui_page.get_by_role("button", name="finish of B1").click()
    assert _marks(ui_page)[-1] == ["r", {"key": None}, True, True]
    ui_page.locator("#r-1").evaluate("(node) => node.focus()")
    assert _focused(ui_page) == "r-1"
    ui_page.keyboard.press("Enter")
    ui_page.keyboard.press("Space")
    assert _marks(ui_page)[2:] == [["r", {"key": "check|B1"}, True, True]] * 2
    # A key held down sends once; a key with a modifier sends nothing.
    ui_page.keyboard.down("Enter")
    ui_page.keyboard.down("Enter")
    ui_page.keyboard.up("Enter")
    ui_page.keyboard.press("Shift+Enter")
    assert len(_marks(ui_page)) == 5
    # Tab moves along the markers in order.
    ui_page.keyboard.press("Shift+Tab")
    assert _focused(ui_page) == "r-0"
    ui_page.keyboard.press("Tab")
    ui_page.keyboard.press("Tab")
    assert _focused(ui_page) == "r-2"
    # The rail opens nothing itself.
    for i in range(3):
        assert not ui_page.locator(f"#r-{i}").evaluate("(node) => node.hasAttribute('open')")


def test_a_quiet_mark_is_drawn_and_named_but_is_no_button(ui_page):
    _place(ui_page, [("r", {}, [({"state": "done", "quiet": "", "key": "a", "label": "design"}, None),
                               ({"state": "todo", "key": "b"}, None)])])
    expect(ui_page.get_by_role("img", name="design")).to_be_visible()
    expect(ui_page.locator("#r").get_by_role("button")).to_have_count(1)
    ui_page.get_by_role("img", name="design").click()
    assert _marks(ui_page) == []
    assert _style(_cell(ui_page, "r-0").locator(".mk"), "cursor") != "pointer"


def test_open_draws_the_ring_and_aria_expanded_and_keeps_the_focus(ui_page):
    _place(ui_page, [("r", {}, [({"state": "failed", "key": "check", "label": "check"}, None)])])
    button = ui_page.get_by_role("button", name="check")
    expect(button).to_have_attribute("aria-expanded", "false")
    assert _style(_cell(ui_page, "r-0").locator(".mk"), "box-shadow") == "none"
    button.focus()
    ui_page.locator("#r-0").evaluate("(node) => node.setAttribute('open', '')")
    expect(button).to_have_attribute("aria-expanded", "true")
    accent = _token(ui_page, "accent")
    assert accent in _style(_cell(ui_page, "r-0").locator(".mk"), "box-shadow")
    assert _style(_cell(ui_page, "r-0").locator(".mk"), "background-color") == _token(ui_page, "accent-soft")
    assert _focused(ui_page) == "r-0"
    ui_page.locator("#r-0").evaluate("(node) => node.removeAttribute('open')")
    expect(button).to_have_attribute("aria-expanded", "false")
    assert _focused(ui_page) == "r-0"


def test_a_small_rail_is_a_picture_of_11_px_markers_and_7_px_stretches(ui_page):
    _place(ui_page, [("r", {"size": "small"}, [
        ({"state": "done", "settled": "", "word": "v8", "label": "design"}, None),
        ({"state": "yours", "count": "3", "here": ""}, None),
        ({"state": "todo"}, None),
    ])])
    expect(ui_page.locator("#r").get_by_role("button")).to_have_count(0)
    expect(ui_page.locator("#r").get_by_role("img")).to_have_count(3)
    for i in range(3):
        svg = _box(_cell(ui_page, f"r-{i}").locator("svg"))
        assert (svg["width"], svg["height"]) == (11, 11)
        for part in (".word", ".count", ".here"):
            expect(_cell(ui_page, f"r-{i}").locator(part)).to_have_count(0)
    stretch = _cell(ui_page, "r-1").locator(".before")
    assert _box(stretch)["width"] == 7
    assert _style(stretch, "border-top-color") == _token(ui_page, "state-done")
    expect(_cell(ui_page, "r-0").locator(".before")).to_be_hidden()
    assert _box(ui_page.locator("#r"))["width"] == 11 * 3 + 7 * 2
    ui_page.get_by_role("img", name="design").click()
    assert _marks(ui_page) == []


def test_a_vertical_rail_stacks_the_marks_with_label_and_word_and_runs_the_line_down(ui_page):
    _place(ui_page, [("r", {"vertical": ""}, [
        ({"state": "done", "settled": "", "label": "design", "word": "v3, publishable"}, None),
        ({"state": "yours", "here": "", "label": "docket", "word": "3 questions for you", "key": "docket"}, None),
        ({"state": "todo", "label": "commission of B1"}, None),
    ])])
    boxes = [_box(ui_page.locator(f"#r-{i}")) for i in range(3)]
    assert boxes[0]["x"] == boxes[1]["x"] == boxes[2]["x"]
    assert boxes[0]["y"] < boxes[1]["y"] < boxes[2]["y"]
    expect(_cell(ui_page, "r-0").locator(".text")).to_have_text("designv3, publishable")
    expect(_cell(ui_page, "r-1").locator(".text")).to_have_text("docket ◂ here3 questions for you")
    expect(_cell(ui_page, "r-1").locator(".count")).to_have_count(0)
    # The line runs down from each marker to the next: solid after the settled one.
    after = _cell(ui_page, "r-0").locator(".after")
    assert _style(after, "border-left-style") == "solid"
    assert _style(after, "border-left-color") == _token(ui_page, "state-done")
    assert _box(after)["height"] > 10
    assert _style(_cell(ui_page, "r-1").locator(".after"), "border-left-style") == "dashed"
    expect(_cell(ui_page, "r-2").locator(".after")).to_be_hidden()
    ui_page.get_by_role("button", name="docket").click()
    assert _marks(ui_page) == [["r", {"key": "docket"}, True, True]]


# The four colour sets, as the theme and mode that show each.
SETS = [("plain", "light", "plain-day"), ("plain", "dark", "plain-night"),
        ("midnight-neon", "light", "calm-workbench"), ("midnight-neon", "dark", "midnight-neon")]
# Each state colour's hue, from and to in degrees.
HUES = {"state-done": (90, 170), "state-yours": (30, 55), "state-failed": (345, 15)}


@pytest.mark.parametrize(("theme", "mode", "colour_set"), SETS)
def test_done_yours_and_failed_markers_take_their_state_colour_in_each_set(ui_page, theme, mode, colour_set):
    _place(ui_page, [("r", {}, [
        ({"state": "done", "settled": "", "word": "v8"}, None),
        ({"state": "yours", "count": "3"}, None),
        ({"state": "failed", "word": "red"}, None),
    ])])
    ui_page.evaluate("([theme, mode]) => { document.documentElement.dataset.theme = mode;"
                     " document.documentElement.dataset.themeName = theme; }", [theme, mode])
    text = _token(ui_page, "text")
    assert text == _rgb(TOKENS["text"]["$extensions"]["moxi"]["sets"][colour_set])
    for i, (state, token) in enumerate([("done", "state-done"), ("yours", "state-yours"),
                                        ("failed", "state-failed")]):
        want = _rgb(TOKENS[token]["$extensions"]["moxi"]["sets"][colour_set])
        assert _token(ui_page, token) == want, token
        shape = _cell(ui_page, f"r-{i}").locator(f".p-{state}")
        fill = _style(shape, "fill")
        assert fill == want, (state, fill, want)
        assert fill != text, state
        low, high = HUES[token]
        hue = _hue(fill)
        assert (low <= hue <= high) if low <= high else (hue >= low or hue <= high), (state, hue)
    cell = _cell(ui_page, "r-0")
    done = _rgb(TOKENS["state-done"]["$extensions"]["moxi"]["sets"][colour_set])
    assert _style(cell.locator(".word"), "color") == done
    assert _style(cell.locator(".after"), "border-top-color") == done
    assert _style(_cell(ui_page, "r-1").locator(".count"), "color") == _rgb(
        TOKENS["state-yours"]["$extensions"]["moxi"]["sets"][colour_set])
    assert _style(_cell(ui_page, "r-2").locator(".word"), "color") == _rgb(
        TOKENS["state-failed"]["$extensions"]["moxi"]["sets"][colour_set])


def test_the_other_states_stay_ink_and_grey(ui_page):
    _place(ui_page, [("r", {}, [({"state": state}, None) for state in ("none", "owed", "waiting", "todo", "child")])])
    greys = {_token(ui_page, name) for name in ("text", "text-dim", "text-faint", "border-strong", "bg", "bg-raised")}
    for i, state in enumerate(("none", "owed", "waiting", "todo", "child")):
        for part in _cell(ui_page, f"r-{i}").locator("svg > *").all():
            for prop in ("fill", "stroke"):
                value = _style(part, prop)
                assert value == "none" or value in greys, (state, prop, value)
