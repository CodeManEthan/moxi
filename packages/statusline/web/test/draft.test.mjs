import assert from "node:assert/strict";
import test from "node:test";

import { canMove, Draft, moveDown, moveUp, offItems, parseState, rowsOf, turnOff, turnOn } from "../src/draft.ts";

const FIELDS = ["dir", "branch", "model", "effort", "context", "session", "transcript", "lines", "cost"];
const MODULES = [{ name: "usage", summary: "Usage." }, { name: "cache", summary: "Cache." }];

function wire({ base = "b1", show = ["dir", "model", "usage"], separator = " | ", color = true,
                parts = 2, warn = 50, alert = 80, claude = {} } = {}) {
  return {
    base, problem: null, fields: FIELDS, modules: MODULES,
    values: { show, separator, color, dir: { parts }, context: { warn_at: warn, alert_at: alert } },
    claude: { set: false, command: null, moxis: false, here: false, saved: false, problem: null, ...claude },
  };
}

const drawn = (item) => FIELDS.includes(item) || MODULES.some((m) => m.name === item);

test("the state is read with its values by dotted key", () => {
  const state = parseState(wire());
  assert.equal(state.base, "b1");
  assert.deepEqual(state.values, { show: ["dir", "model", "usage"], separator: " | ", color: true,
                                   "dir.parts": 2, "context.warn_at": 50, "context.alert_at": 80 });
  assert.equal(parseState({ base: "b" }), null);
  assert.equal(parseState(null), null);
  assert.equal(parseState({ ...wire(), values: { ...wire().values, dir: { parts: 1.5 } } }), null);
});

test("a draft counts the keys that differ, and a change back to the saved value is none", () => {
  const draft = new Draft(parseState(wire()));
  assert.equal(draft.dirty, false);
  draft.set("separator", " / ");
  draft.set("dir.parts", 3);
  assert.equal(draft.count, 2);
  draft.set("separator", " | ");
  assert.equal(draft.count, 1);
  draft.set("show", ["dir", "model", "usage"]);
  assert.equal(draft.count, 1);
  assert.equal(draft.values()["dir.parts"], 3);
  assert.equal(draft.saved["dir.parts"], 2);
});

test("discard gives back the saved values", () => {
  const draft = new Draft(parseState(wire()));
  draft.set("show", ["model", "dir", "usage"]);
  draft.set("color", false);
  draft.discard();
  assert.equal(draft.dirty, false);
  assert.deepEqual(draft.values().show, ["dir", "model", "usage"]);
  assert.equal(draft.values().color, true);
});

test("the body nests the dotted keys in the order the changes were made", () => {
  const draft = new Draft(parseState(wire()));
  draft.set("context.alert_at", 95);
  draft.set("show", ["dir"]);
  draft.set("context.warn_at", 90);
  const body = draft.body();
  assert.deepEqual(body, { base: "b1", changes: { context: { alert_at: 95, warn_at: 90 }, show: ["dir"] } });
  assert.deepEqual(Object.keys(body.changes), ["context", "show"]);
});

test("a rebase takes the new base and keeps the draft's changes over it", () => {
  const draft = new Draft(parseState(wire()));
  draft.set("separator", " / ");
  draft.set("color", false);
  draft.rebase(parseState(wire({ base: "b2", color: false, parts: 4 })));
  assert.equal(draft.base, "b2");
  // The new file already turns colour off, so that change is gone; the separator stays.
  assert.deepEqual(draft.changes, [{ key: "separator", value: " / " }]);
  assert.equal(draft.values()["dir.parts"], 4);
  assert.deepEqual(draft.body(), { base: "b2", changes: { separator: " / " } });
});

test("rows are split at row, and items the page doesn't draw are left out", () => {
  assert.deepEqual(rowsOf(["dir", "gone", "row", "cache", "model"], drawn), [["dir"], ["cache", "model"]]);
  assert.deepEqual(rowsOf(["dir", "model"], drawn), [["dir", "model"], []]);
});

test("moving up and down within a row swaps with the drawn neighbour", () => {
  assert.deepEqual(moveUp(["dir", "model", "usage"], "model", drawn), ["model", "dir", "usage"]);
  assert.deepEqual(moveDown(["dir", "model", "usage"], "dir", drawn), ["model", "dir", "usage"]);
  // An undrawn item keeps its place.
  assert.deepEqual(moveUp(["dir", "gone", "model"], "model", drawn), ["model", "gone", "dir"]);
});

test("moving down from the end of a line of one row starts row 2", () => {
  assert.deepEqual(moveDown(["dir", "model", "usage"], "usage", drawn), ["dir", "model", "row", "usage"]);
  assert.deepEqual(canMove(["dir", "model"], "model", drawn), { up: true, down: true });
  assert.deepEqual(canMove(["dir"], "dir", drawn), { up: false, down: false });
  assert.deepEqual(moveDown(["dir"], "dir", drawn), ["dir"]);
});

test("moving between the rows crosses row, and emptying row 2 removes it", () => {
  const two = ["dir", "model", "row", "usage", "cache"];
  assert.deepEqual(moveDown(two, "model", drawn), ["dir", "row", "model", "usage", "cache"]);
  assert.deepEqual(moveUp(two, "usage", drawn), ["dir", "model", "usage", "row", "cache"]);
  assert.deepEqual(moveUp(["dir", "row", "usage"], "usage", drawn), ["dir", "usage"]);
  // Row 1's one item moved down leaves row 1 empty, so the two rows become one.
  assert.deepEqual(moveDown(["dir", "row", "usage"], "dir", drawn), ["dir", "usage"]);
  assert.deepEqual(canMove(two, "cache", drawn), { up: true, down: false });
  assert.deepEqual(canMove(two, "dir", drawn), { up: false, down: true });
});

test("a row with only undrawn items is no row", () => {
  assert.deepEqual(moveUp(["dir", "row", "usage", "gone"], "usage", drawn), ["dir", "usage", "gone"]);
});

test("turning an item off removes it, and on adds it at the end of row 1", () => {
  assert.deepEqual(turnOff(["dir", "model", "row", "usage"], "usage", drawn), ["dir", "model"]);
  assert.deepEqual(turnOn(["dir", "row", "usage"], "cost", drawn), ["dir", "cost", "row", "usage"]);
  assert.deepEqual(turnOn(["dir"], "cache", drawn), ["dir", "cache"]);
  assert.deepEqual(turnOn(["dir"], "dir", drawn), ["dir"]);
});

test("the Off list is every drawn item show leaves out, fields first", () => {
  assert.deepEqual(offItems(["dir", "model", "usage", "gone"], FIELDS, MODULES),
                   ["branch", "effort", "context", "session", "transcript", "lines", "cost", "cache"]);
});
