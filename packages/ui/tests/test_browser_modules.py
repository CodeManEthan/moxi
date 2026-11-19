"""The modules view in a browser: data that arrives late moves nothing already drawn.

The test shell serves `ui` and the `fixture` module (tests/conftest.py), beside every other
module installed in this environment, whose blocks the view places too."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(60)]

# Holds back every late read the modules view's blocks make until `releaseLate()`: each
# module's own API reads (`/m/<module>/api/...`, ui's own aside), and every event the push
# stream brings but `ui.modules`, which draws the view. The held events are passed on in
# the order they came, on the stream open at release.
_HOLD_LATE = """(() => {
  const held = window.heldLate = { on: true, frames: [], reads: [], streams: [] };
  const encoder = new TextEncoder();
  const fetched = window.fetch.bind(window);
  const stream = (answer) => {
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
            const late = /^event: /m.test(frame) && !/^event: ui\\.modules$/m.test(frame);
            if (held.on && late) held.frames.push(frame);
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
  window.fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    const path = new URL(url, location.href).pathname;
    if (held.on && /^\\/m\\/[^/]+\\/api\\//.test(path) && !path.startsWith("/m/ui/")) {
      await new Promise((go) => held.reads.push(go));
    }
    const answer = await fetched(input, init);
    if (!path.startsWith("/m/ui/api/push") || answer.body === null) return answer;
    return stream(answer);
  };
  window.releaseLate = () => {
    held.on = false;
    for (const go of held.reads.splice(0)) go();
    const text = held.frames.splice(0).map((frame) => `${frame}\\n\\n`).join("");
    for (const controller of [...held.streams].reverse()) {
      try {
        if (text) controller.enqueue(encoder.encode(text));
        return true;
      } catch {
        // A stream the shell's page closed takes nothing more; try the one before it.
      }
    }
    return false;
  };
})()"""

# The view's own boxes: every block's slot, title and host, every Installed card, heading,
# line, link and button, in the view's shadow root (not inside a placed block's), leaving
# out the page servers block, which is placed last and may grow. `modulesMark()` keeps the
# elements; `modulesBoxes()` gives the page box of each kept one, or null once it is gone;
# `modulesStill()` says whether the boxes have held still for half a second.
_BOXES = """(() => {
  let kept = [];
  const view = () => document.querySelector("#moxi-panel > moxi-ui-page");
  const pick = () => [...(view()?.shadowRoot?.querySelectorAll(
      "section, section > div, .card, h1, h2, h3, p, a, button, li") ?? [])]
    .filter((el) => !el.closest("[data-block='ui.servers']"));
  const box = (el) => {
    if (!el.isConnected) return null;
    const rect = el.getBoundingClientRect();
    return [rect.x + scrollX, rect.y + scrollY, rect.width, rect.height];
  };
  window.modulesMark = () => { kept = pick(); return kept.length; };
  window.modulesBoxes = () => kept.map((el) => [
    `${el.tagName.toLowerCase()} ${el.textContent.trim().slice(0, 40)}`, box(el)]);
  let last = "", since = 0;
  window.modulesStill = () => {
    const now = JSON.stringify(pick().map(box));
    if (now !== last) [last, since] = [now, performance.now()];
    return performance.now() - since > 500;
  };
})()"""


def _moved(before, after):
    """The kept elements gone, or not where `before` had them, to half a pixel."""
    return [(old, new) for old, new in zip(before, after)
            if new[1] is None or max(abs(a - b) for a, b in zip(old[1], new[1])) > 0.5]


@pytest.mark.parametrize("width", [1280, 360], ids=["desk", "phone"])
def test_late_data_moves_nothing_already_drawn_on_the_modules_view(ui_page, width):
    ui_page.set_viewport_size({"width": width, "height": 800})
    ui_page.add_init_script(_HOLD_LATE)
    ui_page.add_init_script(_BOXES)
    ui_page.goto("/m/ui/modules")
    view = ui_page.locator("#moxi-panel > moxi-ui-page")
    servers = view.locator("[data-block='ui.servers'] moxi-ui-servers")
    expect(servers).to_have_text("Waiting for the shell…")
    expect(view.locator(".list .card[data-module='fixture']")).to_be_visible()
    expect(view.locator("#home section[data-block='fixture.block'] moxi-fixture-block")).to_be_attached()
    # Each block draws what it shows while its reads are held; then the view is still.
    ui_page.wait_for_function("() => window.modulesStill()", polling="raf")
    assert ui_page.evaluate("() => window.modulesMark()") > 0
    before = ui_page.evaluate("() => window.modulesBoxes()")
    assert ui_page.evaluate("() => window.releaseLate()")
    expect(servers.locator("li").first).to_be_visible()
    expect(view.locator("moxi-fixture-block").get_by_test_id("count")).to_have_text(
        re.compile(r"^Count: \d+$"))
    ui_page.wait_for_function("() => window.modulesStill()", polling="raf")
    assert _moved(before, ui_page.evaluate("() => window.modulesBoxes()")) == []
