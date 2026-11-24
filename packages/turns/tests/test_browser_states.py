"""turns' panel in a browser, around its rows: each address and an unknown one, `Copy id`,
`Copy` and `Settings › turns`; the states (loading, a failed route, a failed scan, empty,
first time, a session whose watch alone is down); markup drawn as text; nothing that acts
on a session and no second heading; the page at 1280 and 400 px in the light and the dark
theme, with colour from the text tokens alone.

The loading state, the failed route and the failed scan are the shell's own answers held,
replaced or changed in the browser; the page server's side of each is its unit tests'.
A watch that is down is the session's own: its endpoint, in this process, refuses it.
The shells, the stand-in `claude` and the helpers are `panel_kit`'s.
"""

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import expect

from moxi_turns.results import Agent
from panel_kit import (MARKS, T, address, copied, group, harness, key_of, on, open_panel,
                       own_panel, row, rows, small, started)

pytestmark = MARKS

__all__ = ["own_panel", "small"]

SID = "aaaaaaaa-1111-4111-8111-111111111111"
SID2 = "bbbbbbbb-2222-4222-8222-222222222222"
NOT_RUNNING = "That session isn't running now."
SCAN_DETAIL = ("turns' page server could not list the sessions (PermissionError); its log has "
               "the details")
START_SMALL = ("Interactive Claude Code sessions show here from their start. Codex and OpenCode "
               "sessions started by hand don't.")


@pytest.fixture
def panel(small, turns_env, monkeypatch):
    """The small install's shell, with this process's turns runtime root set to it."""
    return on(small, monkeypatch)


def two(panel, make_endpoint) -> tuple[str, str]:
    """Two idle sessions, listed: the keys of `kestrel-web`'s and of `parser`'s."""
    one, _ = make_endpoint(harness("/srv/work/kestrel-web"), session_id=SID)
    other, _ = make_endpoint(harness("/srv/work/parser"), session_id=SID2)
    panel.listed({key_of(one), key_of(other)})
    return key_of(one), key_of(other)


def history(page) -> int:
    return page.evaluate("() => history.length")


# --- the addresses ---------------------------------------------------------------------------

def test_an_address_opens_its_row_and_opening_one_writes_it_with_no_history(
        panel, make_endpoint, ui_contexts):
    first, second = two(panel, make_endpoint)
    page, root = open_panel(panel, ui_contexts, route=f"session/{first}")
    expect(row(root, first).locator('[data-part="opened"]')).to_have_count(1)
    expect(row(root, first)).to_have_attribute("open", "")
    expect(row(root, first).locator('[data-action="open"]')).to_have_text("Close")
    expect(row(root, first).locator('[data-action="open"]')).to_have_attribute("aria-expanded", "true")
    expect(root.locator('[data-line="note"]')).to_have_count(0)
    depth = history(page)

    row(root, second).locator('[data-action="open"]').click()
    expect(row(root, second).locator('[data-part="opened"]')).to_have_count(1)
    expect(row(root, first).locator('[data-part="opened"]')).to_have_count(0)
    assert address(page) == f"session/{second}"
    # The row's own lines open and close it too.
    row(root, second).click(position={"x": 20, "y": 12})
    expect(root.locator('[data-part="opened"]')).to_have_count(0)
    assert address(page) == ""
    assert history(page) == depth


@pytest.mark.parametrize("route", ["session/claude-gone", "sessions", "session/a/b"])
def test_an_unknown_address_opens_nothing_and_says_so(panel, make_endpoint, ui_contexts, route):
    two(panel, make_endpoint)
    _, root = open_panel(panel, ui_contexts, route=route)
    expect(rows(root)).to_have_count(2)
    expect(root.locator('[data-line="note"]')).to_have_text([NOT_RUNNING])
    expect(root.locator('[data-part="opened"]')).to_have_count(0)


# --- the copy acts and the link --------------------------------------------------------------

def test_copy_id_copies_the_session_id_and_settings_opens_turns_settings(
        panel, make_endpoint, ui_contexts):
    first, _ = two(panel, make_endpoint)
    page, root = open_panel(panel, ui_contexts, route=f"session/{first}", copying=True)
    copy = row(root, first).locator('[data-action="copy-id"]')
    expect(copy).to_have_text("Copy id")
    copy.click()
    expect(copy).to_have_text("Copied")
    assert copied(page) == SID
    expect(copy).to_have_text("Copy id")

    root.locator('[data-action="settings"]').click()
    expect(page).to_have_url(re.compile(r"/m/ui/settings/turns$"))


# --- the states ------------------------------------------------------------------------------

def test_while_the_first_read_is_out_the_page_says_reading(panel, make_endpoint, ui_contexts):
    two(panel, make_endpoint)
    held = []
    _, root = open_panel(panel, ui_contexts, before=lambda page: page.route(
        "**/m/turns/api/sessions", lambda route: held.append(route)))
    expect(root.locator("div[data-state]")).to_have_attribute("data-state", "loading")
    expect(root.locator('[data-line="reading"]')).to_have_text("Reading…")
    expect(root.locator('[data-part="foot"]')).to_have_count(0)
    expect(rows(root)).to_have_count(0)
    assert held, "the page never asked for the list"
    held[-1].continue_()
    expect(rows(root)).to_have_count(2)
    expect(root.locator('[data-line="reading"]')).to_have_count(0)


def test_a_failed_route_leads_with_its_words_and_keeps_the_last_rows_dim(
        panel, make_endpoint, ui_contexts):
    first, second = two(panel, make_endpoint)
    failing = []

    def answer(route):
        if failing:
            route.fulfill(status=502, content_type="application/json", body="{}")
        else:
            route.continue_()

    page, root = open_panel(panel, ui_contexts, before=lambda page: page.route(
        "**/m/turns/api/sessions", answer))
    expect(rows(root)).to_have_count(2)
    failing.append(True)
    # The page reads the route again when its tab shows.
    page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
    expect(root.locator("div[data-state]")).to_have_attribute("data-state", "failed")
    alert = root.locator('[data-part="alert"]')
    expect(alert.locator('[data-line="lead"]')).to_have_text("Moxi can't read the live sessions")
    expect(alert.locator('[data-line="rest"]')).to_have_text("turns' page server answered 502.")
    expect(alert.locator('[data-line="last-read"]')).to_have_text(
        re.compile(rf"^Last read {T}\. It may have changed since\.$"))
    # The last rows stay, dim, with no act and no toggle.
    expect(rows(root)).to_have_count(2)
    for key in (first, second):
        expect(row(root, key).locator('[data-action="open"]')).to_have_count(0)
        expect(row(root, key)).not_to_have_attribute("toggles", "")
    expect(root.locator('[data-line="reach"]')).to_have_count(0)
    expect(root.locator('[data-action="settings"]')).to_have_text("Settings › turns")


def test_a_first_read_that_fails_has_no_rows_and_no_last_read(panel, ui_contexts):
    _, root = open_panel(panel, ui_contexts, before=lambda page: page.route(
        "**/m/turns/api/sessions", lambda route: route.fulfill(status=500, body="no")))
    expect(root.locator("div[data-state]")).to_have_attribute("data-state", "failed")
    expect(root.locator('[data-line="rest"]')).to_have_text("turns' page server answered 500.")
    expect(root.locator('[data-line="last-read"]')).to_have_count(0)
    expect(rows(root)).to_have_count(0)


def test_a_failed_scan_leads_with_its_detail_over_the_rows(panel, make_endpoint, ui_contexts):
    first, _ = two(panel, make_endpoint)
    # The page reads the list from its route and then from the `turns.sessions` topic, whose
    # first value replaces the route's: both carry the scan's detail, as the page server's do.
    held = panel.list()
    held.pop("now", None)
    held["detail"] = SCAN_DETAIL

    def answer(route):
        fetched = route.fetch()
        data = fetched.json()
        data["detail"] = SCAN_DETAIL
        route.fulfill(response=fetched, body=json.dumps(data))

    def before(page):
        page.route("**/m/turns/api/sessions", answer)
        page.add_init_script(f"window.heldTopics = Object.assign(window.heldTopics || {{}}, "
                             f"{{'turns.sessions': {json.dumps(json.dumps(held))}}});")

    page, root = open_panel(panel, ui_contexts, before=before)
    expect(root.locator("div[data-state]")).to_have_attribute("data-state", "up")
    alert = root.locator('[data-part="alert"]')
    expect(alert.locator('[data-line="lead"]')).to_have_text("Moxi couldn't look for sessions")
    expect(alert.locator('[data-line="rest"]')).to_have_text(SCAN_DETAIL)
    expect(alert.locator('[data-line="last-read"]')).to_have_count(0)
    expect(rows(root)).to_have_count(2)
    expect(row(root, first).locator('[data-action="open"]')).to_have_text("Open")
    # The alert outlasts the topic's first value.
    page.wait_for_function("() => window.pushed.includes('turns.sessions')")
    expect(alert.locator('[data-line="lead"]')).to_have_text("Moxi couldn't look for sessions")


def test_with_no_session_the_page_says_so_with_the_reach_line(panel, ui_contexts):
    panel.listed(set())
    _, root = open_panel(panel, ui_contexts)
    expect(root.locator("div[data-state]")).to_have_attribute("data-state", "empty")
    expect(root.locator('[data-line="empty"]')).to_have_text("No session is running.")
    expect(root.locator('[data-part="reach"]')).to_have_text(START_SMALL)
    foot = root.locator('[data-part="foot"]')
    expect(foot.locator('[data-line="reach"]')).to_have_count(0)
    expect(foot).to_have_text("Settings › turns")
    expect(rows(root)).to_have_count(0)


def test_the_first_time_without_claude_code_says_none_can_show_and_how_to_fix_it(
        own_panel, ui_contexts):
    first = own_panel("absent", small=True)
    first.listed(set(), reach="absent")
    assert first.stub.calls() == []
    page, root = open_panel(first, ui_contexts, copying=True)
    expect(root.locator("div[data-state]")).to_have_attribute("data-state", "first")
    expect(root.locator('[data-line="empty"]')).to_have_text("No session can show here yet.")
    reach = root.locator('[data-part="reach"]')
    expect(reach).to_have_text(re.compile(
        r"^Claude Code wasn't found when turns was installed, so its sessions can't show\. Once it "
        r"is installed, run this again: moxi install turns\s*Copy$"))
    expect(reach.locator("code")).to_have_text("moxi install turns")
    reach.locator('[data-action="copy-command"]').click()
    expect(reach.locator('[data-action="copy-command"]')).to_have_text("Copied")
    assert copied(page) == "moxi install turns"


def test_while_a_sessions_watch_alone_is_down_its_row_keeps_its_group_and_says_so(
        panel, make_endpoint, ui_contexts, monkeypatch):
    from moxi import ipc
    from moxi_turns.endpoint import Endpoint

    def refused(self, conn, first, live):
        raise ipc.RpcError(-32000, "this session takes no watch")

    # Every watch of the session is refused; its reads answer.
    monkeypatch.setattr(Endpoint, "watch", refused)
    ep, _ = make_endpoint(harness("/srv/work/unwatched"), session_id=SID)
    panel.listed({key_of(ep)})
    _, root = open_panel(panel, ui_contexts)
    unwatched = group(root, "idle").locator(f'moxi-ui-row[data-key="{key_of(ep)}"]')
    expect(unwatched).to_have_attribute("name", started("unwatched"))
    expect(unwatched).to_have_attribute("note", "read every 30 s")
    expect(unwatched).not_to_have_attribute("lead", re.compile("."))
    expect(group(root, "problem")).to_have_count(0)
    # No line for the whole panel.
    expect(root.locator('[data-line="note"]')).to_have_count(0)


# --- markup as text --------------------------------------------------------------------------

#: Text that is markup: the page draws it as text, everywhere it shows.
MARKUP = '<img src="x" onerror="window.markupRan = 1"><b>bold</b> & <script>window.markupRan = 2</script>'
#: The elements of these tags under the page, in any shadow tree.
_TAGS_UNDER = """(names) => {
  const panel = document.querySelector("#moxi-panel > moxi-turns-page");
  const found = [];
  const walk = (root) => { for (const node of root.querySelectorAll("*")) {
    if (names.includes(node.localName)) found.push(node.localName);
    if (node.shadowRoot) walk(node.shadowRoot);
  } };
  walk(panel.shadowRoot);
  return found;
}"""


def test_markup_from_a_session_is_drawn_as_text(panel, make_endpoint, ui_contexts):
    agents = [Agent("a1", "subagent", "running", final=False, description=MARKUP)]
    marked, _ = make_endpoint(harness(f"/srv/work/{MARKUP}", trouble=MARKUP), session_id=SID)
    busy, _ = make_endpoint(harness("/srv/work/busy", entries=agents), session_id=SID2)
    panel.listed({key_of(marked), key_of(busy)})
    page, root = open_panel(panel, ui_contexts, route=f"session/{key_of(busy)}")
    expect(row(root, key_of(marked))).to_have_attribute("rest", MARKUP)
    expect(row(root, key_of(busy))).to_have_attribute("rest", MARKUP)
    expect(row(root, key_of(busy)).locator("[data-agent]")).to_have_text([f"{MARKUP} · subagent · running"])
    row(root, key_of(marked)).locator('[data-action="open"]').click()
    expect(row(root, key_of(marked)).locator('[data-line="session"]')).to_contain_text(MARKUP)
    assert page.evaluate(_TAGS_UNDER, ["img", "b", "script"]) == []
    assert page.evaluate("() => window.markupRan") is None


# --- what the page holds, widths, themes and colour ------------------------------------------

#: The tokens a piece of the page may take a colour from: the text tokens, and the border
#: its lines are drawn in.
ALLOWED = {"text", "text-dim", "text-faint", "border"}
#: For each colour token the shell's style sheets define, the nodes under the page whose
#: colours change when that token alone changes on the page's element, and in which
#: property. The walk goes into every shadow tree under the page. Focus is taken off
#: first, and the outline's colour is left out: a focus ring takes the accent on every
#: element.
REACHED = """async () => {
  const panel = document.querySelector("#moxi-panel > moxi-turns-page");
  function* tree(root) {
    for (const node of root.querySelectorAll("*")) {
      yield node;
      if (node.shadowRoot) yield* tree(node.shadowRoot);
    }
  }
  let focused = document.activeElement;
  while (focused?.shadowRoot?.activeElement) focused = focused.shadowRoot.activeElement;
  focused?.blur?.();
  const nodes = [panel, ...tree(panel.shadowRoot)];
  const props = ["color", "background-color", "background-image", "border-top-color",
                 "border-right-color", "border-bottom-color", "border-left-color",
                 "text-decoration-color", "box-shadow", "fill", "stroke", "stop-color"];
  const frames = () => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
  const colours = () => nodes.map((node) => {
    const style = getComputedStyle(node);
    return props.map((name) => style.getPropertyValue(name));
  });
  const names = new Set();
  for (const sheet of document.styleSheets) {
    let rules;
    try { rules = sheet.cssRules; } catch { continue; }
    for (const rule of rules) {
      if (!rule.style) continue;
      for (const name of rule.style) if (name.startsWith("--moxi-")) names.add(name.slice(7));
    }
  }
  const what = (node) => node.localName + (node.dataset?.action ? `[${node.dataset.action}]` : "")
    + (node.className && typeof node.className === "string" ? `.${node.className.split(" ").join(".")}` : "");
  await frames();
  const before = colours();
  const reached = {};
  for (const token of [...names].sort()) {
    if (/^(font|radius|gap)/.test(token)) continue;
    panel.style.setProperty(`--moxi-${token}`, "rgb(1, 2, 3)");
    await frames();
    const after = colours();
    panel.style.removeProperty(`--moxi-${token}`);
    const changed = [];
    after.forEach((seen, n) => seen.forEach((value, i) => {
      if (value !== before[n][i]) changed.push(`${what(nodes[n])} ${props[i]}`);
    }));
    if (changed.length) reached[token] = [...new Set(changed)];
  }
  await frames();
  return {reached, text: getComputedStyle(panel).color};
}"""
#: The elements under the page that reach past the viewport's right edge and are not
#: inside a box that cuts them with an ellipsis (the row's own cut), and the document's
#: horizontal scroll.
PAST_THE_EDGE = """() => {
  const edge = document.documentElement.clientWidth;
  const panel = document.querySelector("#moxi-panel > moxi-turns-page");
  const up = (node) => node.parentElement ?? node.getRootNode().host ?? null;
  const cut = (node) => {
    for (let at = up(node); at !== null; at = up(at)) {
      const style = getComputedStyle(at);
      if (style.textOverflow === "ellipsis" && style.overflowX !== "visible")
        return at.getBoundingClientRect().right <= edge + 0.5;
    }
    return false;
  };
  const past = [];
  const walk = (root) => { for (const node of root.querySelectorAll("*")) {
    const box = node.getBoundingClientRect();
    if (box.width > 0 && box.right > edge + 0.5 && !cut(node)) past.push(`${node.localName}.${node.className}`);
    if (node.shadowRoot) walk(node.shadowRoot);
  } };
  walk(panel.shadowRoot);
  return {past, scroll: document.documentElement.scrollWidth - edge};
}"""
#: The page's own controls (in its shadow root, not inside a row's): each one's tag and
#: act; and every form control under it, in any shadow tree.
_CONTROLS = """() => {
  const panel = document.querySelector("#moxi-panel > moxi-turns-page");
  const own = [...panel.shadowRoot.querySelectorAll("button, a, [role=button], [tabindex]")]
    .map((node) => `${node.localName}:${node.dataset.action ?? ""}`);
  const fields = [];
  const walk = (root) => { for (const node of root.querySelectorAll("form, input, textarea, select, [contenteditable]")) {
    fields.push(node.localName);
  } for (const node of root.querySelectorAll("*")) if (node.shadowRoot) walk(node.shadowRoot); };
  walk(panel.shadowRoot);
  return {own: [...new Set(own)].sort(), fields};
}"""


def test_the_page_acts_on_no_session_has_no_second_heading_and_takes_colour_from_text_tokens(
        panel, make_endpoint, ui_contexts, ui_width_check):
    long = "Check the plan against every test the parser change touches " * 4
    busy, fake = make_endpoint(harness("/srv/work/kestrel-web-with-a-rather-long-folder-name", name="claude",
                                       entries=[Agent("a1", "subagent", "running", final=False,
                                                      description=long)]), session_id=SID)
    trouble, _ = make_endpoint(harness("/srv/work/parser", trouble="the host is loading the thread " * 5),
                               session_id=SID2)
    panel.listed({key_of(busy), key_of(trouble)})
    fake.start_turn()
    page, root = open_panel(panel, ui_contexts, route=f"session/{key_of(busy)}")
    expect(group(root, "working")).to_have_count(1)
    expect(row(root, key_of(busy)).locator('[data-part="opened"]')).to_have_count(1)
    expect(root.get_by_role("heading")).to_have_count(0)
    controls = page.evaluate(_CONTROLS)
    assert controls == {"own": ["a:settings", "button:copy-id", "button:open"], "fields": []}, controls
    texts = set()
    for scheme in ("light", "dark"):
        page.emulate_media(color_scheme=scheme)
        seen = page.evaluate(f"({REACHED})()")
        texts.add(seen["text"])
        reached = seen["reached"]
        refused = {token: where for token, where in reached.items() if token not in ALLOWED}
        assert not refused, f"{scheme}: colour from {refused}"
        assert {"text", "text-dim"} <= set(reached), (scheme, reached)
        # At 1280 a line is cut by the row's ellipsis, which the shared width check would
        # count; at 400 the row's lines wrap, and nothing may pass the edge.
        page.set_viewport_size({"width": 1280, "height": 800})
        assert page.evaluate(PAST_THE_EDGE) == {"past": [], "scroll": 0}, scheme
        ui_width_check(page, width=400)
    assert len(texts) == 2, f"the text colour is the same in both themes: {texts}"
    expect(row(root, key_of(busy))).to_have_attribute("name", started("kestrel-web-with-a-rather-long-folder-name"))
