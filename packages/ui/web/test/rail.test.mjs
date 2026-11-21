import assert from "node:assert/strict";
import test from "node:test";

import {
  HERE, MARK_ATTRIBUTES, MARK_EVENT, MARK_FLAG_ATTRIBUTES, MARK_TEXT_ATTRIBUTES, RAIL_ATTRIBUTES, STATES, STATE_WORDS,
  isMarkKey, markName, railLine, stateOf,
} from "../src/rail-model.ts";

/** The cells for a line written as one letter a cell: `s` a settled marker, `o` one not
 * settled, `.` a cell with no marker. */
const cells = (text) => [...text].map((c) => ({ state: c === "." ? null : c === "s" ? "done" : "todo",
                                                 settled: c === "s" }));
/** Each cell's line as two letters, before and after: `=` solid, `-` dashed, ` ` none. */
const drawn = (text) => railLine(cells(text)).map(({ before, after }) =>
  [before, after].map((part) => (part === "solid" ? "=" : part === "dashed" ? "-" : " ")).join(""));
/** A mark's name for `attrs`, a flag given as `""`. */
const name = (attrs) => markName((key) => (key in attrs ? attrs[key] : null));

test("a mark takes one of eight states and reads five text attributes and five flags", () => {
  assert.deepEqual([...STATES], ["done", "none", "yours", "failed", "owed", "waiting", "todo", "child"]);
  assert.deepEqual(Object.keys(STATE_WORDS), [...STATES]);
  assert.deepEqual([...MARK_TEXT_ATTRIBUTES], ["state", "word", "count", "label", "key"]);
  assert.deepEqual([...MARK_FLAG_ATTRIBUTES], ["settled", "here", "open", "quiet", "wide"]);
  assert.deepEqual(MARK_ATTRIBUTES, [...MARK_TEXT_ATTRIBUTES, ...MARK_FLAG_ATTRIBUTES]);
  assert.deepEqual(RAIL_ATTRIBUTES, ["size", "vertical"]);
  assert.equal(MARK_EVENT, "moxi-rail-mark");
  assert.equal(HERE, "◂ here");
});

test("a state that is unset, blank or none of the eight draws no marker", () => {
  for (const state of STATES) assert.equal(stateOf(state), state);
  for (const value of [null, "", " ", "Done", "unknown", "done "]) assert.equal(stateOf(value), null, String(value));
});

test("the line is solid through settled markers up to and including the first not settled", () => {
  assert.deepEqual(drawn("ssoo"), [" =", "==", "=-", "- "]);
  assert.deepEqual(drawn("sooo"), [" =", "=-", "--", "- "]);
  assert.deepEqual(drawn("so"), [" =", "= "]);
});

test("with every marker settled the line is solid to the last; with the first not settled, dashed", () => {
  assert.deepEqual(drawn("sss"), [" =", "==", "= "]);
  assert.deepEqual(drawn("oss"), [" -", "--", "- "]);
  assert.deepEqual(drawn("ooo"), [" -", "--", "- "]);
});

test("a settled marker after the first not settled does not make its stretch solid", () => {
  assert.deepEqual(drawn("sosss"), [" =", "=-", "--", "--", "- "]);
});

test("nothing is drawn before the first marker or after the last", () => {
  assert.deepEqual(drawn("..ss.."), ["  ", "  ", " =", "= ", "  ", "  "]);
  assert.deepEqual(drawn("...."), ["  ", "  ", "  ", "  "]);
  assert.deepEqual(drawn(""), []);
  assert.deepEqual(drawn(".s."), ["  ", "  ", "  "]);
});

test("a cell with no marker between two takes the stretch it stands in, on both halves", () => {
  assert.deepEqual(drawn("s..s"), [" =", "==", "==", "= "]);
  assert.deepEqual(drawn("s.o.o"), [" =", "==", "=-", "--", "- "]);
  assert.deepEqual(drawn("o..s"), [" -", "--", "--", "- "]);
});

test("the settled flag counts only on a cell with a marker", () => {
  const line = railLine([{ state: "done", settled: true }, { state: null, settled: true },
                         { state: "todo", settled: false }, { state: null, settled: true },
                         { state: "todo", settled: false }]);
  assert.deepEqual(line.map((cell) => [cell.before, cell.after]), [
    [null, "solid"], ["solid", "solid"], ["solid", "dashed"], ["dashed", "dashed"], ["dashed", null],
  ]);
});

test("a mark's name is its label as given", () => {
  assert.equal(name({ state: "failed", word: "red", label: "check of B1, billing-export: failed, red" }),
               "check of B1, billing-export: failed, red");
  assert.equal(name({ state: "done", label: "<b>design</b>" }), "<b>design</b>");
});

test("with no label, a mark's name is its state in words, then its count and its word", () => {
  assert.equal(name({ state: "done", word: "v8" }), "done, v8");
  assert.equal(name({ state: "yours", count: "3" }), "waiting on you, 3");
  assert.equal(name({ state: "yours", count: "3", word: "held" }), "waiting on you, 3, held");
  assert.equal(name({ state: "none" }), "done, nothing owed");
  assert.equal(name({ state: "waiting", label: "  ", word: " " }), "waiting on something else first");
  for (const state of STATES) assert.equal(name({ state }), STATE_WORDS[state], state);
});

test("a mark with no state has no name, label or not", () => {
  assert.equal(name({}), null);
  assert.equal(name({ label: "design", word: "v8" }), null);
  assert.equal(name({ state: "bogus", label: "design" }), null);
});

test("Enter or Space alone presses a mark; a held key, a modifier or another key does not", () => {
  assert.equal(isMarkKey({ key: "Enter" }), true);
  assert.equal(isMarkKey({ key: " " }), true);
  assert.equal(isMarkKey({ key: "Enter", repeat: true }), false);
  assert.equal(isMarkKey({ key: " ", repeat: true }), false);
  for (const mod of ["altKey", "ctrlKey", "metaKey", "shiftKey"]) {
    assert.equal(isMarkKey({ key: "Enter", [mod]: true }), false, mod);
    assert.equal(isMarkKey({ key: " ", [mod]: true }), false, mod);
  }
  for (const key of ["Escape", "Tab", "ArrowRight", "a", "Spacebar"]) assert.equal(isMarkKey({ key }), false, key);
});
