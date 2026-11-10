import assert from "node:assert/strict";
import test from "node:test";

import {
  DOT, FLAG_ATTRIBUTES, ROW_ATTRIBUTES, TEXT_ATTRIBUTES, TOGGLE_EVENT, YOU, isToggleKey, rowPlan,
} from "../src/row-model.ts";

/** The plan for a row with `attrs`; a flag is given as `""`, as the DOM holds it. */
const plan = (attrs) => rowPlan((name) => (name in attrs ? attrs[name] : null));
const kinds = (pieces) => pieces.map((piece) => piece.kind);
const texts = (pieces) => pieces.map((piece) => piece.text);
const dot = { kind: "dot", text: DOT };

test("the row reads its ten text attributes and five flags, and no other", () => {
  assert.deepEqual([...TEXT_ATTRIBUTES],
                   ["name", "where", "fact", "lead", "lead-more", "time", "said-by", "rest", "tail", "note"]);
  assert.deepEqual([...FLAG_ATTRIBUTES], ["fact-heavy", "lead-heavy", "rest-strong", "toggles", "open"]);
  assert.deepEqual(ROW_ATTRIBUTES, [...TEXT_ATTRIBUTES, ...FLAG_ATTRIBUTES]);
  assert.equal(TOGGLE_EVENT, "moxi-row-toggle");
});

test("a row with no attribute draws no line, does not toggle and is not open", () => {
  assert.deepEqual(plan({}), { one: [], two: [], note: null, toggles: false, open: false, stop: null });
});

test("line one is the name, where and fact, each only when set", () => {
  assert.deepEqual(plan({ name: "proj-a", where: "slot 3", fact: "warm until 19:52" }).one, [
    { kind: "name", text: "proj-a" },
    { kind: "where", text: "slot 3" },
    { kind: "fact", text: "warm until 19:52" },
  ]);
  assert.deepEqual(kinds(plan({ name: "proj-a" }).one), ["name"]);
  assert.deepEqual(kinds(plan({ where: "slot 3" }).one), ["where"]);
  assert.deepEqual(kinds(plan({ fact: "cold" }).one), ["fact"]);
  assert.deepEqual(kinds(plan({ name: "proj-a", fact: "cold" }).one), ["name", "fact"]);
});

test("a heavy fact is marked strong; the flag alone draws nothing", () => {
  assert.deepEqual(plan({ fact: "goes cold at 19:22", "fact-heavy": "" }).one,
                   [{ kind: "fact", text: "goes cold at 19:22", strong: true }]);
  assert.deepEqual(plan({ "fact-heavy": "" }).one, []);
  assert.equal("strong" in plan({ fact: "cold" }).one[0], false);
});

test("a row with a name alone is one line", () => {
  const row = plan({ name: "proj-a · started today 18:45" });
  assert.deepEqual(row.one, [{ kind: "name", text: "proj-a · started today 18:45" }]);
  assert.deepEqual(row.two, []);
  assert.equal(row.note, null);
});

test("line two holds every phrase in order, a dot between each two", () => {
  const row = plan({
    name: "proj-a", lead: "working", "lead-more": "1 subagent busy", time: "19:09", "said-by": "you",
    rest: "Run the suite.", tail: "warm until 20:08, est.",
  });
  assert.deepEqual(row.two, [
    { kind: "lead", text: "working" }, dot,
    { kind: "lead-more", text: "1 subagent busy" }, dot,
    { kind: "time", text: "19:09" },
    { kind: "you", text: YOU },
    { kind: "rest", text: "Run the suite." }, dot,
    { kind: "tail", text: "warm until 20:08, est." },
  ]);
  assert.equal(YOU, "You:");
  assert.equal(DOT, "·");
});

test("each phrase of line two is absent when unset, with no dot left behind", () => {
  const all = { lead: "working", "lead-more": "1 subagent busy", time: "19:09", rest: "Done.", tail: "est." };
  const without = (name) => {
    const attrs = { ...all };
    delete attrs[name];
    return texts(plan(attrs).two);
  };
  assert.deepEqual(without("lead"), ["1 subagent busy", DOT, "19:09", "Done.", DOT, "est."]);
  assert.deepEqual(without("lead-more"), ["working", DOT, "19:09", "Done.", DOT, "est."]);
  assert.deepEqual(without("time"), ["working", DOT, "1 subagent busy", DOT, "Done.", DOT, "est."]);
  assert.deepEqual(without("rest"), ["working", DOT, "1 subagent busy", DOT, "19:09", DOT, "est."]);
  assert.deepEqual(without("tail"), ["working", DOT, "1 subagent busy", DOT, "19:09", "Done."]);
  // One phrase alone has no dot, and a line never starts or ends with one.
  for (const name of Object.keys(all)) {
    assert.deepEqual(texts(plan({ [name]: all[name] }).two), [all[name]], name);
  }
  for (const row of [without("lead"), without("tail"), without("rest")]) {
    assert.notEqual(row[0], DOT);
    assert.notEqual(row.at(-1), DOT);
  }
});

test("a dot stands between the lead and the rest when there is no time", () => {
  assert.deepEqual(texts(plan({ lead: "2 subagents", rest: "Found it." }).two), ["2 subagents", DOT, "Found it."]);
  assert.deepEqual(texts(plan({ lead: "2 subagents", time: "08:55", rest: "Found it." }).two),
                   ["2 subagents", DOT, "08:55", "Found it."]);
});

test("the time and the rest are one phrase: no dot parts them", () => {
  assert.deepEqual(kinds(plan({ time: "19:07", rest: "The build is smaller now." }).two), ["time", "rest"]);
});

test("You: is written only for said-by you, and only before a rest", () => {
  assert.deepEqual(kinds(plan({ time: "19:09", "said-by": "you", rest: "Run it." }).two), ["time", "you", "rest"]);
  assert.deepEqual(kinds(plan({ "said-by": "you", rest: "Run it." }).two), ["you", "rest"]);
  for (const who of ["agent", "You", "", "true"]) {
    assert.deepEqual(kinds(plan({ "said-by": who, rest: "Done." }).two), ["rest"], who);
  }
  assert.deepEqual(plan({ "said-by": "you" }).two, []);
  assert.deepEqual(kinds(plan({ "said-by": "you", time: "19:09" }).two), ["time"]);
});

test("a heavy lead and a strong rest are marked; their flags alone draw nothing", () => {
  const row = plan({ lead: "waiting on 2 agents", "lead-heavy": "", "lead-more": "1 message held",
                     rest: "Asks to run it", "rest-strong": "" });
  assert.deepEqual(row.two, [
    { kind: "lead", text: "waiting on 2 agents", strong: true }, dot,
    { kind: "lead-more", text: "1 message held" }, dot,
    { kind: "rest", text: "Asks to run it", strong: true },
  ]);
  assert.deepEqual(plan({ "lead-heavy": "", "rest-strong": "" }).two, []);
});

test("the note is line three, absent when unset or empty", () => {
  assert.equal(plan({ name: "proj-a", note: "Moxi can't read its live state" }).note,
               "Moxi can't read its live state");
  assert.equal(plan({ name: "proj-a" }).note, null);
  assert.equal(plan({ name: "proj-a", note: "" }).note, null);
});

test("an empty or blank text attribute draws nothing", () => {
  const blank = Object.fromEntries(TEXT_ATTRIBUTES.map((name) => [name, name === "said-by" ? "you" : "  "]));
  assert.deepEqual(plan(blank), { one: [], two: [], note: null, toggles: false, open: false, stop: null });
  const empty = Object.fromEntries(TEXT_ATTRIBUTES.map((name) => [name, ""]));
  assert.deepEqual(plan(empty), { one: [], two: [], note: null, toggles: false, open: false, stop: null });
});

test("text is drawn as given, not trimmed or changed", () => {
  assert.equal(plan({ rest: " <b>two  spaces</b> " }).two[0].text, " <b>two  spaces</b> ");
});

test("a row toggles only with the attribute toggles, and is open only with open", () => {
  assert.equal(plan({ name: "proj-a" }).toggles, false);
  assert.equal(plan({ name: "proj-a", open: "" }).toggles, false);
  assert.equal(plan({ name: "proj-a", toggles: "" }).toggles, true);
  assert.equal(plan({ name: "proj-a", toggles: "" }).open, false);
  assert.equal(plan({ name: "proj-a", open: "" }).open, true);
  // Present is on, whatever the value.
  assert.equal(plan({ toggles: "false" }).toggles, true);
});

test("the tab stop is line one on a row that toggles, the lines when it has no line one", () => {
  for (const one of [{ name: "proj-a" }, { where: "slot 3" }, { fact: "cold" }]) {
    assert.equal(plan({ ...one, toggles: "" }).stop, "one");
    assert.equal(plan({ ...one, rest: "Done.", toggles: "", open: "" }).stop, "one");
  }
  assert.equal(plan({ rest: "Done.", toggles: "" }).stop, "lines");
  assert.equal(plan({ note: "A note.", toggles: "" }).stop, "lines");
  assert.equal(plan({ name: "  ", rest: "Done.", toggles: "" }).stop, "lines");
  assert.equal(plan({ toggles: "" }).stop, "lines");
});

test("a row without toggles has no tab stop, open or not", () => {
  assert.equal(plan({ name: "proj-a", rest: "Done." }).stop, null);
  assert.equal(plan({ name: "proj-a", open: "" }).stop, null);
  assert.equal(plan({ rest: "Done.", open: "" }).stop, null);
});

test("Enter or Space alone toggles a row; no other key does", () => {
  assert.equal(isToggleKey({ key: "Enter" }), true);
  assert.equal(isToggleKey({ key: " " }), true);
  for (const key of ["Spacebar", "Space", "Escape", "Tab", "ArrowDown", "e", "NumpadEnter"]) {
    assert.equal(isToggleKey({ key }), false, key);
  }
  for (const key of ["Enter", " "]) {
    for (const held of ["altKey", "ctrlKey", "metaKey", "shiftKey", "repeat"]) {
      assert.equal(isToggleKey({ key, [held]: true }), false, `${key} ${held}`);
    }
  }
});
