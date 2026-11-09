import assert from "node:assert/strict";
import test from "node:test";

import { Draft, parseState } from "../src/draft.ts";
import { HOLD, pageView, timeText } from "../src/views.ts";

const FIELDS = ["dir", "branch", "model", "effort", "context", "session", "transcript", "lines", "cost"];

function state({ show = ["dir", "model", "row", "usage"], claude = {}, problem = null } = {}) {
  return parseState({
    base: "b1", problem, fields: FIELDS, modules: [{ name: "usage", summary: "Usage limits." }],
    values: { show, separator: " | ", color: true, dir: { parts: 2 }, context: { warn_at: 50, alert_at: 80 } },
    claude: { set: true, command: "/x/moxi-statusline-render", moxis: true, here: true, saved: true,
              problem: null, ...claude },
  });
}

test("the rows and the Off list, with plain labels and the show names", () => {
  const s = state({ show: ["dir", "gone", "model", "row", "usage"] });
  const view = pageView(s, new Draft(s));
  assert.deepEqual(view.rows[0].map((item) => [item.name, item.label, item.detail]),
                   [["dir", "Folder", "dir"], ["model", "Model", "model"]]);
  assert.deepEqual(view.rows[1].map((item) => [item.name, item.detail, item.module]),
                   [["usage", "Usage limits.", true]]);
  // A show item with nothing installed behind it is drawn nowhere, not even in Off.
  assert.ok(!view.off.some((item) => item.name === "gone"));
  assert.deepEqual(view.off.map((item) => item.name),
                   ["branch", "effort", "context", "session", "transcript", "lines", "cost"]);
});

test("Save and Discard show only while the draft differs", () => {
  const s = state();
  const draft = new Draft(s);
  assert.equal(pageView(s, draft).dirty, false);
  draft.set("color", false);
  assert.equal(pageView(s, draft).dirty, true);
  assert.equal(pageView(s, draft).countText, "1 unsaved change");
  draft.set("dir.parts", 3);
  assert.equal(pageView(s, draft).countText, "2 unsaved changes");
  draft.set("color", true);
  draft.set("dir.parts", 2);
  assert.equal(pageView(s, draft).dirty, false);
});

test("Discard gives back the saved lists", () => {
  const s = state();
  const draft = new Draft(s);
  draft.set("show", ["model", "dir", "usage"]);
  assert.deepEqual(pageView(s, draft).rows[0].map((item) => item.name), ["model", "dir", "usage"]);
  draft.discard();
  const view = pageView(s, draft);
  assert.deepEqual(view.rows[0].map((item) => item.name), ["dir", "model"]);
  assert.deepEqual(view.rows[1].map((item) => item.name), ["usage"]);
});

test("use and restore are held while the draft differs", () => {
  const s = state({ claude: { moxis: false, here: false, command: "my-line.sh", saved: false } });
  const draft = new Draft(s);
  let view = pageView(s, draft);
  assert.equal(view.claude.useShown, true);
  assert.equal(view.claude.held, false);
  draft.set("separator", " / ");
  view = pageView(s, draft);
  assert.equal(view.claude.held, true);
  assert.equal(HOLD, "Save or discard first");
});

test("Claude's line: Moxi's here, Moxi's elsewhere, your own, none", () => {
  const here = pageView(state(), new Draft(state())).claude;
  assert.equal(here.text, "Claude Code uses this status line.");
  assert.equal(here.useShown, false);
  assert.equal(here.restoreShown, true);
  assert.equal(here.command, null);
  const elsewhere = state({ claude: { here: false, command: "/old/moxi-statusline-render" } });
  const away = pageView(elsewhere, new Draft(elsewhere)).claude;
  assert.equal(away.useShown, true);
  assert.equal(away.command, "/old/moxi-statusline-render");
  const own = state({ claude: { moxis: false, here: false, command: "my-line.sh" } });
  const mine = pageView(own, new Draft(own)).claude;
  assert.equal(mine.text, "Claude Code runs your own status line:");
  assert.equal(mine.restoreShown, false);
  const none = state({ claude: { set: false, moxis: false, here: false, command: null, saved: false } });
  assert.equal(pageView(none, new Draft(none)).claude.text, "Claude Code shows no status line now.");
});

test("a config that can't be read can't be edited", () => {
  const s = state({ problem: "The saved config can't be read: ..." });
  assert.equal(pageView(s, new Draft(s)).editable, false);
});

test("the time line", () => {
  assert.equal(timeText(34.25), "Rendered in 34.3 ms");
  assert.equal(timeText(null), "");
});
