"""The Settings page's time on the full install: opened at its own address, the module it
shows (the core) draws within 2 s, and every listed module's section is read within 4 s.
Both are measured in the browser from the moment the page was opened (the navigation's
start), checked at each animation frame.

A `browser` and `slow` test: a plain pytest run leaves it out; `pytest -m slow -s` runs
it and prints both times with the machine's load. It is also `timed`, so `moxi-dev check`
holds it back and `moxi-dev timed` runs it."""

from __future__ import annotations

import os

import pytest
from moxi import modules
from moxi_dev.testing import timed_slack

pytestmark = [pytest.mark.browser, pytest.mark.slow, pytest.mark.timed, pytest.mark.timeout(120)]

#: Every module of the repository: the full install.
FULL = ("cache", "comms", "connector", "launcher", "loadout", "notes", "projects", "publishing",
        "repo", "rounds", "secrets", "services", "sessions", "slots", "tunnel", "turns", "ui", "usage")
DRAWN_S = 2.0
#: Set from eight runs on a lightly loaded machine, the full read at 0.70 to 0.86 s over
#: 19 listed modules (each section one `moxi config <module> show --json`, two at a time):
#: about five times the slowest, so a loaded machine passes, and a section that waits out
#: its run's 10 s limit, or reads grown several times slower, fails.
READ_S = 4.0
WAIT_MS = 60_000

# Run at each animation frame: notes the time the shown module's pane draws its rows and
# foot, and the time the list stops reading, and answers both once both are seen.
MARKS = """module => {
  const marks = window.__settingsMarks ??= {};
  const find = root => {
    const hit = root.querySelector("moxi-ui-settings");
    if (hit) return hit;
    for (const element of root.querySelectorAll("*")) {
      const found = element.shadowRoot ? find(element.shadowRoot) : null;
      if (found) return found;
    }
    return null;
  };
  const root = find(document)?.shadowRoot;
  if (!root) return null;
  if (marks.drawn === undefined
      && root.querySelector(`[data-part=pane][data-module="${module}"] [data-part=foot]`)) {
    marks.drawn = performance.now();
  }
  const list = root.querySelector("[data-part=list]");
  if (marks.read === undefined && list && !list.hasAttribute("data-reading")) {
    marks.read = performance.now();
    marks.listed = [...list.querySelectorAll("a[data-list]")].map(link => link.dataset.list);
  }
  return marks.drawn !== undefined && marks.read !== undefined ? marks : null;
}"""


@pytest.mark.skipif(not {m.name for m in modules.scan()} >= set(FULL),
                    reason="the full install's time: not every module is installed")
def test_on_the_full_install_the_shown_module_draws_within_2_s_and_every_section_is_read_within_4_s(ui_page,
                                                                                                     timed_reading):
    ui_page.goto("/m/ui/settings", wait_until="commit")
    marks = ui_page.wait_for_function(MARKS, arg="moxi", polling="raf", timeout=WAIT_MS).json_value()
    drawn_s, read_s = marks["drawn"] / 1000, marks["read"] / 1000
    load = ", ".join(f"{one:.1f}" for one in os.getloadavg())
    print(f"\nSettings on the full install ({len(marks['listed'])} modules listed): the core drawn at "
          f"{drawn_s * 1000:.0f} ms, every section read at {read_s * 1000:.0f} ms (load {load})")
    timed_reading(*max((drawn_s, DRAWN_S), (read_s, READ_S), key=lambda pair: pair[0] / pair[1]), "s")
    assert {"moxi", "ui", "launcher", "comms", "sessions", "turns", "usage"} <= set(marks["listed"])
    assert drawn_s < DRAWN_S * timed_slack()
    assert read_s < READ_S * timed_slack()
