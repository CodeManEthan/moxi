"""The modules view in a browser: a late page servers list moves nothing already drawn.

The test shell serves `ui` and the `fixture` module (tests/conftest.py)."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(60)]

# Holds back every `ui.servers` event the push stream brings, keeping the last one, until
# `releaseServers()` passes it on.
_HOLD_SERVERS = """(() => {
  const held = window.heldServers = { on: true, frame: null, streams: [] };
  const encoder = new TextEncoder();
  const fetched = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const answer = await fetched(input, init);
    const url = typeof input === "string" ? input : input.url;
    if (!url.includes("/m/ui/api/push") || answer.body === null) return answer;
    const reader = answer.body.pipeThrough(new TextDecoderStream()).getReader();
    let rest = "";
    const body = new ReadableStream({
      start(controller) { held.streams.push(controller); },
      async pull(controller) {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) { controller.close(); return; }
          rest += value;
          const frames = rest.split("\\n\\n");
          rest = frames.pop();
          let pass = "";
          for (const frame of frames) {
            if (held.on && /^event: ui\\.servers$/m.test(frame)) held.frame = frame;
            else pass += `${frame}\\n\\n`;
          }
          if (pass) { controller.enqueue(encoder.encode(pass)); return; }
        }
      },
      cancel(reason) { return reader.cancel(reason); },
    });
    return new Response(body, { status: answer.status, statusText: answer.statusText,
                                headers: answer.headers });
  };
  window.releaseServers = () => {
    held.on = false;
    for (const controller of [...held.streams].reverse()) {
      try {
        controller.enqueue(encoder.encode(`${held.frame}\\n\\n`));
        return true;
      } catch {
        // A stream the shell's page closed takes nothing more; try the one before it.
      }
    }
    return false;
  };
})()"""

# The page box of every block, card, heading, line, link and button on the modules view,
# through each placed block's shadow root, leaving out the page servers block; and whether
# those boxes have held still for half a second.
_BOXES = """(() => {
  const boxes = window.modulesBoxes = () => {
    const out = [];
    const walk = (root) => {
      for (const el of root.querySelectorAll("*")) {
        if (el.closest("[data-block='ui.servers']")) continue;
        if (el.matches("section, .card, h1, h2, h3, p, a, button, li")) {
          const box = el.getBoundingClientRect();
          out.push([`${el.tagName.toLowerCase()} ${el.textContent.trim().slice(0, 40)}`,
                    box.x + scrollX, box.y + scrollY, box.width, box.height]);
        }
        if (el.shadowRoot) walk(el.shadowRoot);
      }
    };
    const view = document.querySelector("#moxi-panel > moxi-ui-page");
    if (view?.shadowRoot) walk(view.shadowRoot);
    return out;
  };
  let last = "", since = 0;
  window.modulesStill = () => {
    const now = JSON.stringify(boxes().map((box) => box.slice(1)));
    if (now !== last) [last, since] = [now, performance.now()];
    return performance.now() - since > 500;
  };
})()"""


def _moved(before, after):
    """The boxes in `after` not where `before` had them, to half a pixel."""
    assert len(after) == len(before), (before, after)
    return [(old, new) for old, new in zip(before, after)
            if max(abs(a - b) for a, b in zip(old[1:], new[1:])) > 0.5]


@pytest.mark.parametrize("width", [1280, 360], ids=["desk", "phone"])
def test_a_late_servers_list_moves_nothing_already_drawn_on_the_modules_view(ui_page, width):
    ui_page.set_viewport_size({"width": width, "height": 800})
    ui_page.add_init_script(_HOLD_SERVERS)
    ui_page.add_init_script(_BOXES)
    ui_page.goto("/m/ui/modules")
    view = ui_page.locator("#moxi-panel > moxi-ui-page")
    servers = view.locator("[data-block='ui.servers'] moxi-ui-servers")
    expect(servers).to_have_text("Waiting for the shell…")
    expect(view.locator("moxi-fixture-block").get_by_test_id("count")).to_have_text(
        re.compile(r"^Count: \d+$"))
    expect(view.locator(".list .card[data-module='fixture']")).to_be_visible()
    # The fixture block's read starts its page server, so the held value comes to list it.
    ui_page.wait_for_function(
        """() => (window.heldServers.frame ?? "").includes('"fixture"')""")
    # Every other block draws its own data as it comes; the servers list is the one late.
    ui_page.wait_for_function("() => window.modulesStill()", polling="raf")
    before = ui_page.evaluate("() => window.modulesBoxes()")
    assert ui_page.evaluate("() => window.releaseServers()")
    expect(servers.locator("li[data-module='fixture']")).to_be_visible()
    ui_page.evaluate("() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))")
    assert _moved(before, ui_page.evaluate("() => window.modulesBoxes()")) == []
