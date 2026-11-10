"""`<moxi-ui-row>` in a browser: what each attribute draws and that an unset one draws
nothing, one-line and two-line rows, which end of line one is cut when it is narrow, the
opened state, the toggle a row sends only when it carries `toggles`, and where such a
row's tab stop and button are.

The rows are put in the shell's panel by the test: the element is the client's, so no
module's page is needed to draw one."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(90)]

FULL = {
    "name": "planner",
    "where": "slot 3 · proj-a",
    "fact": "warm until 19:52",
    "lead": "working",
    "lead-more": "1 subagent busy",
    "time": "19:09",
    "said-by": "you",
    "rest": "Run the suite and report every diff.",
    "tail": "est.",
    "note": "Moxi can't read its live state",
}

_JS_ROWS = """([rows, width]) => {
  const box = document.createElement("div");
  box.id = "rows";
  if (width) box.style.width = `${width}px`;
  for (const [id, attrs, acts, body] of rows) {
    const row = document.createElement("moxi-ui-row");
    row.id = id;
    for (const [name, value] of Object.entries(attrs)) row.setAttribute(name, value);
    for (const text of acts) {
      const act = document.createElement("a");
      act.slot = "acts";
      act.href = "#";
      act.textContent = text;
      act.addEventListener("click", (event) => event.preventDefault());
      row.append(act);
    }
    if (body) {
      const inner = document.createElement("p");
      inner.className = "body";
      inner.textContent = body;
      row.append(inner);
    }
    box.append(row);
  }
  window.__toggles = [];
  box.addEventListener("moxi-row-toggle", (event) => {
    window.__toggles.push([event.target.id, event.detail, event.bubbles, event.composed]);
  });
  // Each key that reached the page, and whether the row took it from the page.
  window.__keys = [];
  box.addEventListener("keydown", (event) => window.__keys.push([event.key, event.defaultPrevented]));
  document.getElementById("moxi-panel").replaceChildren(box);
}"""


def _place(page, rows, width=None):
    """Puts rows in the panel: each `(id, attributes, acts, opened content)`."""
    page.evaluate(_JS_ROWS, [[list(row) for row in rows], width])


def _toggles(page) -> list:
    return page.evaluate("() => window.__toggles")


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


def _cut(locator) -> bool:
    """Whether the element holds more text than it shows."""
    return locator.evaluate("(node) => node.scrollWidth > node.clientWidth")


def test_every_attribute_is_drawn_in_its_place_and_an_unset_one_is_not(ui_page):
    errors: list[str] = []
    ui_page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    ui_page.on("pageerror", lambda err: errors.append(str(err)))
    _place(ui_page, [("full", FULL, ["Open"], None), ("bare", {"name": "proj-b"}, [], None),
                     ("none", {}, [], None), ("named", {"name": "fix-tooltips", "where": "proj-a"}, [], None)])
    full = ui_page.locator("#full")
    expect(full.locator(".l1")).to_have_text("plannerslot 3 · proj-awarm until 19:52")
    expect(full.locator(".l1 .where")).to_have_text("slot 3 · proj-a")
    expect(full.locator(".l1 .fact")).to_have_text("warm until 19:52")
    expect(full.locator(".l2 .said")).to_have_text(
        "working·1 subagent busy·19:09You:Run the suite and report every diff.·est.")
    expect(full.locator(".l2 .dot")).to_have_count(3)
    expect(full.locator(".l3")).to_have_text("Moxi can't read its live state")
    expect(full.get_by_role("link", name="Open")).to_be_visible()

    # The drawing's sizes and colours, from the theme's tokens.
    text, dim = _token(ui_page, "text"), _token(ui_page, "text-dim")
    assert text != dim
    assert _style(full.locator(".l1"), "font-size") == "14px"
    assert _style(full.locator(".l1"), "line-height") == "20px"
    assert _style(full.locator(".l1"), "color") == text
    for line in (".l2", ".l3"):
        assert _style(full.locator(line), "font-size") == "13px"
        assert _style(full.locator(line), "line-height") == "18px"
        assert _style(full.locator(line), "color") == dim
    for piece in (".where", ".fact", ".lead", ".lead-more", ".time", ".you", ".rest", ".tail:not(.dot)"):
        assert _style(full.locator(piece), "color") == dim, piece
        assert _style(full.locator(piece), "font-weight") == "400", piece
    assert _style(full.locator(".name"), "color") == text
    # The fact sits at the row's right end, the acts at line two's.
    edge = full.evaluate("(node) => node.getBoundingClientRect().right")
    assert abs(full.locator(".fact").evaluate("(node) => node.getBoundingClientRect().right") - edge) < 1
    assert abs(full.get_by_role("link", name="Open").evaluate(
        "(node) => node.getBoundingClientRect().right") - edge) < 1

    # A row with a name alone is one line: no second or third line, no dot, no fact.
    bare = ui_page.locator("#bare")
    expect(bare.locator(".l1")).to_have_text("proj-b")
    for piece in (".l2", ".l3", ".acts"):
        expect(bare.locator(piece)).to_be_hidden()
    for piece in (".where", ".fact", ".dot", ".lead", ".lead-more", ".time", ".you", ".rest", ".tail"):
        expect(bare.locator(piece)).to_have_count(0)
    one = bare.evaluate("(node) => node.getBoundingClientRect().height")
    assert one == 34  # 7 + 20 + 7
    assert full.evaluate("(node) => node.getBoundingClientRect().height") == one + 18 + 18
    # The where follows the name, 10 px after it, with or without a fact.
    for row in (full, ui_page.locator("#named")):
        gap = row.evaluate("""(node) => {
          const range = document.createRange();
          range.selectNodeContents(node.shadowRoot.querySelector(".where"));
          const name = node.shadowRoot.querySelector(".name").getBoundingClientRect();
          return [...range.getClientRects()].at(-1).left - name.right;
        }""")
        assert abs(gap - 10) < 1
    # A row with nothing set takes no room at all.
    assert ui_page.locator("#none").evaluate("(node) => node.getBoundingClientRect().height") == 0
    assert errors == []


def test_the_heavy_and_strong_flags_set_weight_and_colour(ui_page):
    _place(ui_page, [("loud", {"name": "coder", "fact": "goes cold at 19:22", "fact-heavy": "",
                               "lead": "waiting on 2 agents", "lead-heavy": "",
                               "lead-more": "1 message held", "rest": "Asks to run the tests",
                               "rest-strong": "", "tail": "est."}, [], None)])
    row = ui_page.locator("#loud")
    text, dim = _token(ui_page, "text"), _token(ui_page, "text-dim")
    for piece in (".fact", ".lead"):
        assert _style(row.locator(piece), "font-weight") == "600", piece
        assert _style(row.locator(piece), "color") == text, piece
    assert _style(row.locator(".rest"), "color") == text
    assert _style(row.locator(".rest"), "font-weight") == "400"
    for piece in (".lead-more", ".tail:not(.dot)"):
        assert _style(row.locator(piece), "color") == dim, piece
        assert _style(row.locator(piece), "font-weight") == "400", piece


def test_a_changed_attribute_is_drawn_again_and_a_removed_one_goes(ui_page):
    _place(ui_page, [("row", {"name": "proj-a", "time": "19:07", "rest": "Done."}, [], None)])
    row = ui_page.locator("#row")
    expect(row.locator(".l2 .said")).to_have_text("19:07Done.")
    row.evaluate("""(node) => {
      node.setAttribute("rest", "Pushed the fix.");
      node.setAttribute("lead", "2 subagents");
      node.setAttribute("fact", "cold");
    }""")
    expect(row.locator(".l2 .said")).to_have_text("2 subagents·19:07Pushed the fix.")
    expect(row.locator(".fact")).to_have_text("cold")
    row.evaluate("""(node) => {
      for (const name of ["rest", "lead", "time", "fact"]) node.removeAttribute(name);
    }""")
    expect(row.locator(".l2")).to_be_hidden()
    expect(row.locator(".fact")).to_have_count(0)
    expect(row.locator(".l1")).to_have_text("proj-a")


def test_text_is_drawn_as_text_never_as_markup(ui_page):
    _place(ui_page, [("row", {"name": "<b>proj-a</b>", "rest": "<img src=x onerror=alert(1)>"}, [], None)])
    row = ui_page.locator("#row")
    expect(row.locator(".l1")).to_have_text("<b>proj-a</b>")
    expect(row.locator(".rest")).to_have_text("<img src=x onerror=alert(1)>")
    expect(row.locator("b, img")).to_have_count(0)


def test_a_narrow_line_one_cuts_the_where_then_the_name_and_the_fact_last(ui_page):
    attrs = {"name": "a-long-session-name-that-will-not-fit", "where": "slot 3 · proj-a",
             "fact": "warm until 19:52", "time": "19:09",
             "rest": "A long line of text that is cut with an ellipsis at the row's edge."}

    def at(width):
        _place(ui_page, [("row", attrs, ["Open"], None)], width=width)
        row = ui_page.locator("#row")
        return row, row.locator(".name"), row.locator(".where"), row.locator(".fact")

    def right(locator):
        return locator.evaluate("(node) => node.getBoundingClientRect().right")

    def wide(locator):
        return locator.evaluate("(node) => node.getBoundingClientRect().width")

    row, name, where, fact = at(900)
    said = row.locator(".l2 .said")
    assert not _cut(name) and not _cut(where) and not _cut(fact) and not _cut(said)
    assert abs(right(fact) - right(row)) < 1
    full = {"name": wide(name), "where": wide(where), "fact": wide(fact)}
    assert wide(where) > 40
    assert abs(where.evaluate("(node) => node.getBoundingClientRect().left") - right(name)) < 1
    assert _style(fact, "padding-left") == "14px"

    # A little too narrow: the where is cut first; the name and the fact are whole.
    row, name, where, fact = at(round(full["name"] + 10 + full["where"] / 2 + full["fact"]))
    assert _cut(where) and not _cut(name) and not _cut(fact)
    assert _style(where, "text-overflow") == "ellipsis"
    assert abs(right(fact) - right(row)) < 1

    # Narrower: the name is cut too; the fact is whole and still at the right end.
    row, name, where, fact = at(round(full["name"] / 2 + full["fact"]))
    assert _cut(name) and not _cut(fact)
    assert _style(name, "text-overflow") == "ellipsis"
    assert wide(fact) == full["fact"]
    assert abs(right(fact) - right(row)) < 1
    # Line two is one line, cut with an ellipsis, and the act stays whole beside it.
    said = row.locator(".l2 .said")
    assert _cut(said) and _style(said, "text-overflow") == "ellipsis"
    assert wide(row.locator(".l2")) > 0
    assert row.locator(".l2").evaluate("(node) => node.getBoundingClientRect().height") == 18
    assert abs(right(row.get_by_role("link", name="Open")) - right(row)) < 1

    # Narrower than the fact itself: only now is the fact cut, and the row holds its width.
    row, name, where, fact = at(round(full["fact"] / 2))
    assert _cut(fact) and _style(fact, "text-overflow") == "ellipsis"
    assert wide(name) == 0
    assert row.locator(".l1").evaluate("(node) => node.scrollWidth <= node.clientWidth")


def _stop(row) -> dict:
    """Where a row's tab stop is: the focus attributes of its lines and of line one."""
    return row.evaluate("""(node) => Object.fromEntries([".head", ".l1"].map((sel) => {
      const part = node.shadowRoot.querySelector(sel);
      return [sel, ["tabindex", "role", "aria-expanded"].map((name) => part.getAttribute(name))];
    }))""")


def _focused(row):
    """The class of the row's part that holds the focus, or None."""
    return row.evaluate("(node) => node.shadowRoot.activeElement?.className ?? null")


def test_a_row_does_not_toggle_without_the_attribute(ui_page):
    _place(ui_page, [("still", {"name": "proj-a", "time": "19:07", "rest": "Done."}, ["Open"], None),
                     ("shown", {"name": "proj-b", "rest": "Done.", "open": ""}, [], "The opened content.")])
    row = ui_page.locator("#still")
    head = row.locator(".head")
    assert _style(head, "cursor") != "pointer"
    # No tab stop, no role and no expanded state, with `open` set or not.
    for each in (row, ui_page.locator("#shown")):
        assert _stop(each) == {".head": [None, None, None], ".l1": [None, None, None]}
    head.click()
    row.locator(".l1").click()
    # The row is no tab stop: neither its lines nor line one can take the focus.
    ui_page.evaluate("() => { document.activeElement?.blur(); window.getSelection().removeAllRanges(); }")
    for part in (".head", ".l1"):
        row.evaluate("(node, part) => node.shadowRoot.querySelector(part).focus()", part)
        assert _focused(row) is None
    ui_page.keyboard.press("Enter")
    ui_page.keyboard.press("Space")
    assert _toggles(ui_page) == []
    assert not row.evaluate("(node) => node.hasAttribute('open')")


def test_a_row_with_toggles_sends_one_event_for_a_click_or_key_outside_its_acts(ui_page):
    _place(ui_page, [("first", {"name": "proj-a", "time": "19:07", "rest": "Done.", "toggles": ""},
                      ["Open"], "The opened content."),
                     ("second", {"name": "proj-b", "toggles": ""}, [], None)])
    row = ui_page.locator("#first")
    head = row.locator(".head")
    one = row.locator(".l1")
    assert _style(head, "cursor") == "pointer"

    # The whole of the lines is the click area, line two as well as line one.
    one.click()
    assert _toggles(ui_page) == [["first", None, True, True]]
    row.locator(".rest").click()
    assert len(_toggles(ui_page)) == 2
    # The element does not open itself: the page sets `open`.
    assert not row.evaluate("(node) => node.hasAttribute('open')")
    expect(row.locator("p.body")).to_be_hidden()

    # A click on an act is the act's, not the row's.
    row.get_by_role("link", name="Open").click()
    assert len(_toggles(ui_page)) == 2

    # The keyboard: line one takes the focus and shows a ring; Enter and Space toggle,
    # and a key on an act is the act's.
    ui_page.evaluate("() => { window.__toggles.length = 0; window.__keys.length = 0; }")
    one.focus()
    ui_page.keyboard.press("Tab")
    assert row.evaluate("(node) => document.activeElement.textContent") == "Open"
    ui_page.keyboard.press("Enter")
    ui_page.keyboard.press("Space")
    assert _toggles(ui_page) == []
    ui_page.keyboard.press("Shift+Tab")
    assert _focused(row) == "l1"
    assert _style(one, "outline-style") == "solid"
    assert _style(one, "outline-width") == "2px"
    assert _style(one, "outline-color") == _token(ui_page, "accent")
    ui_page.evaluate("() => window.__keys.length = 0")
    ui_page.keyboard.press("Space")
    assert _toggles(ui_page) == [["first", None, True, True]]
    ui_page.keyboard.press("Enter")
    assert len(_toggles(ui_page)) == 2
    # Both keys are taken from the page, so Space does not scroll it.
    assert ui_page.evaluate("() => window.__keys") == [[" ", True], ["Enter", True]]
    # Tab goes on from the act to the next row's line one.
    ui_page.keyboard.press("Tab")
    ui_page.keyboard.press("Tab")
    ui_page.keyboard.press("Enter")
    assert _toggles(ui_page)[-1][0] == "second"

    # Opened by the page: the content shows under the lines, and a click in it is not
    # the row's.
    row.evaluate("(node) => node.setAttribute('open', '')")
    expect(row.locator("p.body")).to_be_visible()
    lines = head.evaluate("(node) => node.getBoundingClientRect().bottom")
    assert row.locator("p.body").evaluate("(node) => node.getBoundingClientRect().top") >= lines
    before = len(_toggles(ui_page))
    row.locator("p.body").click()
    assert len(_toggles(ui_page)) == before
    # Nor is a key pressed there.
    row.locator("p.body").evaluate("(node) => { node.tabIndex = 0; node.focus(); }")
    ui_page.keyboard.press("Enter")
    ui_page.keyboard.press("Space")
    assert len(_toggles(ui_page)) == before
    row.evaluate("(node) => node.removeAttribute('open')")
    expect(row.locator("p.body")).to_be_hidden()


def test_line_one_of_a_row_with_toggles_is_a_button_whose_expanded_state_follows_open(ui_page):
    _place(ui_page, [("row", {"name": "proj-a", "where": "slot 3", "fact": "cold", "time": "19:07",
                              "rest": "Done.", "toggles": ""}, ["Open"], "The opened content.")])
    row = ui_page.locator("#row")
    # Line one is the stop and the button; the lines as a whole are neither.
    assert _stop(row) == {".head": [None, None, None], ".l1": ["0", "button", "false"]}
    button = row.get_by_role("button", name="proj-a slot 3 cold")
    expect(button).to_have_attribute("aria-expanded", "false")
    expect(button).to_have_class("l1")
    # The act is outside the button, so it is still a link of its own.
    expect(row.get_by_role("link", name="Open")).to_be_visible()
    assert row.evaluate("(node) => node.shadowRoot.querySelector('.l1').contains("
                        "node.shadowRoot.querySelector('slot[name=acts]'))") is False

    row.evaluate("(node) => node.setAttribute('open', '')")
    expect(button).to_have_attribute("aria-expanded", "true")
    row.evaluate("(node) => node.removeAttribute('open')")
    expect(button).to_have_attribute("aria-expanded", "false")

    # Without `toggles` it is a line again; with it, a button again.
    row.evaluate("(node) => { node.setAttribute('open', ''); node.removeAttribute('toggles'); }")
    assert _stop(row) == {".head": [None, None, None], ".l1": [None, None, None]}
    expect(row.get_by_role("button")).to_have_count(0)
    row.evaluate("(node) => node.setAttribute('toggles', '')")
    assert _stop(row) == {".head": [None, None, None], ".l1": ["0", "button", "true"]}


def test_a_row_with_toggles_and_no_line_one_keeps_its_lines_as_the_stop_with_no_role(ui_page):
    _place(ui_page, [("row", {"time": "19:07", "rest": "Done.", "toggles": ""}, ["Open"], None)])
    row = ui_page.locator("#row")
    head = row.locator(".head")
    expect(row.locator(".l1")).to_be_hidden()
    assert _stop(row) == {".head": ["0", None, None], ".l1": [None, None, None]}
    expect(row.get_by_role("button")).to_have_count(0)
    row.evaluate("(node) => node.setAttribute('open', '')")
    assert _stop(row) == {".head": ["0", None, None], ".l1": [None, None, None]}

    row.locator(".rest").click()
    assert _toggles(ui_page) == [["row", None, True, True]]
    head.focus()
    assert _focused(row) == "head"
    ui_page.keyboard.press("Enter")
    ui_page.keyboard.press("Space")
    assert len(_toggles(ui_page)) == 3
    assert ui_page.evaluate("() => window.__keys") == [["Enter", True], [" ", True]]
    # The ring is on the lines, on the same token.
    ui_page.keyboard.press("Tab")
    ui_page.keyboard.press("Shift+Tab")
    assert _focused(row) == "head"
    assert _style(head, "outline-style") == "solid"
    assert _style(head, "outline-color") == _token(ui_page, "accent")

    # Given a name, line one takes the stop over; without it, the lines have it back.
    row.evaluate("(node) => node.setAttribute('name', 'proj-a')")
    assert _stop(row) == {".head": [None, None, None], ".l1": ["0", "button", "true"]}
    row.evaluate("(node) => node.removeAttribute('name')")
    assert _stop(row) == {".head": ["0", None, None], ".l1": [None, None, None]}


def test_a_click_that_ends_a_selection_of_text_does_not_toggle(ui_page):
    _place(ui_page, [("row", {"name": "a-session-name-to-copy", "time": "19:07",
                              "rest": "A line of text long enough to drag over.", "toggles": ""},
                      [], None)], width=600)
    row = ui_page.locator("#row")

    def drag(part):
        box = row.locator(part).bounding_box()
        y = box["y"] + box["height"] / 2
        ui_page.mouse.move(box["x"] + 2, y)
        ui_page.mouse.down()
        ui_page.mouse.move(box["x"] + box["width"] - 2, y, steps=5)
        ui_page.mouse.up()
        return ui_page.evaluate("() => String(getSelection())")

    # A drag over the name selects it, and the click that ends the drag is not a toggle.
    assert drag(".name") in "a-session-name-to-copy" and len(drag(".name")) > 5
    assert _toggles(ui_page) == []
    assert len(drag(".rest")) > 5
    assert _toggles(ui_page) == []
    # A plain click clears the selection and toggles, on line one and on line two.
    row.locator(".name").click()
    assert ui_page.evaluate("() => String(getSelection())") == ""
    assert _toggles(ui_page) == [["row", None, True, True]]
    drag(".name")
    row.locator(".rest").click()
    assert len(_toggles(ui_page)) == 2
    # The keys toggle whatever is selected: a selection is the pointer's business.
    drag(".name")
    row.locator(".l1").focus()
    ui_page.keyboard.press("Enter")
    assert len(_toggles(ui_page)) == 3


def test_a_row_without_acts_leaves_them_no_room_and_one_with_acts_alone_shows_them(ui_page):
    _place(ui_page, [("plain", {"name": "proj-a", "rest": "Done."}, [], None),
                     ("acted", {"name": "proj-a", "rest": "Done."}, ["Answer", "Open"], None),
                     ("only", {"name": "proj-b"}, ["Open"], None)], width=400)
    plain, acted, only = (ui_page.locator(f"#{name}") for name in ("plain", "acted", "only"))
    expect(plain.locator(".acts")).to_be_hidden()
    assert _style(plain.locator(".l2 .said"), "padding-right") == "0px"
    assert _style(acted.locator(".l2 .said"), "padding-right") == "18px"
    assert _style(acted.locator(".acts"), "column-gap") == "18px"
    # Acts with no phrase: line two is the acts, at its right end.
    expect(only.locator(".l2")).to_be_visible()
    expect(only.locator(".l2 .said")).to_be_hidden()
    edge = only.evaluate("(node) => node.getBoundingClientRect().right")
    assert abs(only.get_by_role("link", name="Open").evaluate(
        "(node) => node.getBoundingClientRect().right") - edge) < 1
    # An act taken away is gone with its room.
    acted.evaluate("(node) => node.querySelectorAll('[slot=acts]').forEach((act) => act.remove())")
    expect(acted.locator(".acts")).to_be_hidden()
    assert _style(acted.locator(".l2 .said"), "padding-right") == "0px"


def test_rows_hold_a_phone_width_and_line_two_wraps_there(ui_page, ui_width_check):
    long = dict(FULL, name="a-long-session-name-that-will-not-fit-on-a-phone",
                rest="A long line of text that does not fit one line at a phone's width, so it wraps.")
    _place(ui_page, [("phone", long, ["Open"], "The opened content."),
                     ("open", dict(long, open=""), ["Open"], "The opened content.")])
    ui_width_check(ui_page)
    row = ui_page.locator("#phone")
    assert _style(row.locator(".l2"), "white-space") == "normal"
    assert row.locator(".l2").evaluate("(node) => node.getBoundingClientRect().height") > 18
    assert _cut(row.locator(".name")) and not _cut(row.locator(".fact"))
