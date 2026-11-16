import assert from "node:assert/strict";
import test from "node:test";

import {
  BUSY, NOT_FINISHED, NOTHING_ELSE, Pending, allSaved, answerWords, confirmOf, heldWords, hurtsOf, leaveAsk,
  numberValue, outcomeNote, same, shownKey, staysWarned, transportWords,
} from "../src/save-model.ts";

function key(name, type, value, fallback, extra = {}) {
  return { name, type, json: JSON.stringify(value), default_json: JSON.stringify(fallback), source: "file",
           mark: "applies now", doc: `the ${name}`, label: null, line: null, common: false, group: null, unit: null,
           choices: null, pick: false, hurts: null, terminal: null, elsewhere: null, needs_absent: false, locked: false,
           changed: JSON.stringify(value) !== JSON.stringify(fallback), ...extra };
}

const MODE = key("theme.mode", "enum", "system", "system", { values: ["system", "light", "dark"], label: "Light or dark",
  choices: [{ value: "system", words: "follow the browser" }, { value: "light", words: "light" }, { value: "dark", words: "dark" }] });
const PORT = key("port", "int", 18460, 8460, { label: "Port", mark: "applies after `moxi install ui`" });
const IDLE = key("idle_s", "int", 900, 900, { label: "Idle shutdown", mark: "applies at next start" });
const UI = [PORT, IDLE, MODE];

const GUARD = key("guards.no_nested_agents", "bool", true, true, { label: "No nested agents", hurts: {
  when: "off", says: "turns this guard off", confirm: "From its next tool call, a subagent in any session can start subagents of its own.",
  stays: false } });
const PASS = key("relaunch.pass_env", "list", ["A"], [], { label: "Carry into a relaunch", hurts: {
  when: "add", says: "carries a variable into every relaunch", confirm: "A relaunch carries it into the new session.", stays: false } });
const NAMES = key("private_names.people", "list", ["ann", "bob"], [], { label: "People", hurts: {
  when: "remove", says: "stops guarding a name", confirm: "The guard no longer refuses it.", stays: false } });
const TRUST = key("startup.trust_dialog", "bool", false, false, { label: "Answer the folder-trust screen", hurts: {
  when: "on", says: "answers the trust question for every slot launch", confirm: "Every slot launch trusts its folder.", stays: true } });
const TABLE = key("rows", "table", { backup: true }, {}, { label: "Rows", switch_table: true, entries: [
  { name: "backup", key: "rows.backup", value: true, source: "file", mark: "applies now" },
  { name: "sync", key: "rows.sync", value: false, source: "default", mark: "applies now" }] });

const result = (key_, outcome, extra = {}) => ({ key: key_, outcome, problem: null, fix: null, mark: "applies now",
                                                 warning: null, lines: [], finished: true, ...extra });

// --- held changes -----------------------------------------------------------------------------------

test("an edit is held with the value read; editing back to it, or Undo, drops it", () => {
  const p = new Pending();
  p.edit("ui", PORT, 8460);
  assert.deepEqual(p.changeOf("ui", "port"), { name: "port", value: 8460, reset: false, was: "18460" });
  assert.equal(p.count("ui"), 1);
  p.edit("ui", PORT, 18460);
  assert.equal(p.count("ui"), 0);
  p.edit("ui", PORT, 1);
  p.undo("ui", "port");
  assert.equal(p.count("ui"), 0);
  assert.deepEqual(p.modules(), []);
});

test("Reset holds the default as a reset; Discard drops the module's changes only", () => {
  const p = new Pending();
  p.reset("ui", PORT);
  assert.deepEqual(p.changeOf("ui", "port"), { name: "port", value: 8460, reset: true, was: "18460" });
  p.edit("moxi", key("a", "int", 1, 1), 2);
  p.discard("ui");
  assert.deepEqual(p.modules(), ["moxi"]);
  assert.equal(p.total(), 1);
});

test("a switch table's entry is held under its own key with its entry's value", () => {
  const p = new Pending();
  p.editEntry("demo", TABLE, "sync", true);
  assert.deepEqual(p.changeOf("demo", "rows.sync"), { name: "rows.sync", value: true, reset: false, was: "false" });
  p.editEntry("demo", TABLE, "sync", false);
  assert.equal(p.count("demo"), 0);
});

test("a key is shown with the value held for it: a value, a switch table's entries, a table's TOML", () => {
  const p = new Pending();
  assert.equal(shownKey(p, "ui", PORT), PORT);
  p.edit("ui", PORT, 8460);
  assert.equal(shownKey(p, "ui", PORT).json, "8460");
  assert.equal(PORT.json, "18460");
  p.edit("ui", PORT, "8460a");
  assert.equal(shownKey(p, "ui", PORT).json, '"8460a"');
  p.editEntry("demo", TABLE, "sync", true);
  assert.deepEqual(shownKey(p, "demo", TABLE).entries.map((entry) => entry.value),
                   TABLE.entries.map((entry) => entry.name === "sync" ? true : entry.value));
  const toml = key("extra", "table", { a: 1 }, {}, { value: "{a = 1}", default: "{}" });
  p.edit("demo", toml, "{a = 2}");
  assert.equal(shownKey(p, "demo", toml).value, "{a = 2}");
  p.reset("demo", toml);
  assert.equal(shownKey(p, "demo", toml).value, "{}");
  assert.equal(shownKey(p, "demo", toml).json, "{}");
});

test("the save's body holds the module's changes in schema order, values as JSON, a reset's as empty", () => {
  const p = new Pending();
  p.edit("ui", MODE, "dark");
  p.reset("ui", PORT);
  p.edit("ui", IDLE, 1200);
  assert.deepEqual(p.body("ui", UI), { module: "ui", keys: ["port", "idle_s", "theme.mode"],
    values: ["", "1200", '"dark"'], resets: [true, false, false], was: ["18460", "900", '"system"'] });
  const entries = new Pending();
  entries.editEntry("demo", TABLE, "sync", true);
  entries.edit("demo", key("z", "int", 1, 1), 3);
  entries.editEntry("demo", TABLE, "backup", false);
  assert.deepEqual(entries.body("demo", [TABLE, key("z", "int", 1, 1)]).keys, ["rows.backup", "rows.sync", "z"]);
});

test("a held row says what it was, or what it can hurt", () => {
  const p = new Pending();
  p.edit("ui", MODE, "light");
  assert.deepEqual(p.heldLine("ui", UI, "theme.mode"), { text: "not saved · was follow the browser", hurts: false });
  assert.equal(p.heldLine("ui", UI, "port"), null);
  p.edit("turns", GUARD, false);
  assert.deepEqual(p.heldLine("turns", [GUARD], GUARD.name), { text: "not saved · turns this guard off", hurts: true });
  // Turning it back on cannot hurt.
  const off = { ...GUARD, json: "false" };
  p.edit("turns", off, true);
  assert.deepEqual(p.heldLine("turns", [off], GUARD.name), { text: "not saved · was off", hurts: false });
});

test("a number field sends a number when its text reads as one, else the text as typed", () => {
  assert.equal(numberValue("8460"), 8460);
  assert.equal(numberValue(" 0.5 "), 0.5);
  assert.equal(numberValue("8460a"), "8460a");
  assert.equal(numberValue(""), "");
});

test("same compares JSON values: true is not 1, and a table's key order doesn't count", () => {
  assert.equal(same(true, 1), false);
  assert.equal(same({ a: 1, b: [1] }, { b: [1], a: 1 }), true);
  assert.equal(same([1, 2], [2, 1]), false);
  assert.equal(same(null, undefined), false);
});

// --- the Save line ---------------------------------------------------------------------------------

test("the Save line at rest, with changes, and while a save runs", () => {
  const p = new Pending();
  assert.equal(heldWords(UI, p.changes("ui", UI), false), "Nothing to save");
  p.edit("ui", MODE, "light");
  p.edit("ui", PORT, 8460);
  assert.equal(heldWords(UI, p.changes("ui", UI), false), "2 not saved · Port, Light or dark");
  assert.equal(heldWords(UI, p.changes("ui", UI), true), "Saving 2…");
});

test("every change saved: Saved, then the labels grouped by mark", () => {
  const results = [result("port", "saved", { mark: "applies after `moxi install ui`" }), result("theme.mode", "saved"),
                   result("idle_s", "unchanged", { mark: "applies at next start" })];
  assert.equal(answerWords(UI, results, "19:12"),
               "Saved 19:12 · Port applies after moxi install ui · Light or dark applies now · Idle shutdown applies at the next start");
  const two = [result("theme.mode", "saved"), result("rows.backup", "saved")];
  assert.equal(answerWords([...UI, TABLE], two, "19:12"), "Saved 19:12 · Light or dark and Rows backup apply now");
  assert.equal(allSaved(results), true);
});

test("a partial save counts what was saved and points at the refused row", () => {
  const results = [result("theme.mode", "saved"), result("port", "refused", { problem: "8460a is not a whole number" })];
  assert.equal(answerWords(UI, results, "19:12"), "1 of 2 saved at 19:12 · Port was refused: it says why beside the field");
  assert.equal(allSaved(results), false);
  const more = [result("theme.mode", "busy"), result("port", "refused")];
  assert.equal(answerWords(UI, more, "19:12"),
               "0 of 2 saved at 19:12 · Light or dark and Port were not saved: each says why beside its field");
  assert.equal(answerWords(UI, [result("port", "moved", { problem: "1" })], "19:12"),
               "0 of 1 saved at 19:12 · Port was not saved: it says why beside the field");
  // Written with its daemons failing counts as saved; a command that did not finish does not.
  assert.equal(answerWords(UI, [result("port", "failed", { warning: "units failed", fix: "moxi install ui" })], "19:12"),
               "Saved 19:12 · Port applies now");
  assert.equal(answerWords(UI, [result("port", "failed", { finished: false })], "19:12"),
               "0 of 1 saved at 19:12 · Port was not saved: it says why beside the field");
  assert.equal(allSaved([result("port", "failed", { warning: "units failed" })]), false);
});

test("each outcome's words beside its row", () => {
  const note = (r) => outcomeNote("ui", UI, r);
  assert.equal(note(result("port", "saved")), null);
  assert.equal(note(result("port", "unchanged")), null);
  assert.deepEqual(note(result("port", "refused", { problem: "8460a is not a whole number",
                                                    fix: "a port is a number from 1 to 65535" })),
                   { text: "Not saved. 8460a is not a whole number. A port is a number from 1 to 65535.", copy: null, still: true });
  assert.deepEqual(note(result("port", "moved", { problem: "20000" })),
                   { text: "Not saved: it changed since this page read it, to 20000. Save again to write yours.",
                     copy: null, still: false });
  assert.deepEqual(note(result("theme.mode", "moved", { problem: '"dark"' })).text,
                   "Not saved: it changed since this page read it, to dark. Save again to write yours.");
  assert.deepEqual(note(result("port", "busy", { problem: "locked" })), { text: BUSY, copy: null, still: false });
  assert.equal(BUSY, "The settings file was busy. Save again.");
  assert.deepEqual(note(result("port", "failed", { finished: false })), { text: NOT_FINISHED, copy: null, still: false });
  assert.equal(NOT_FINISHED, "May not be saved: moxi config did not finish. The row shows what the file holds now.");
  assert.deepEqual(note(result("port", "failed", { problem: "moxi: error: the following arguments are required" })),
                   { text: "Not saved. Moxi: error: the following arguments are required.", copy: null, still: true });
  assert.deepEqual(note(result("port", "failed", { warning: "the value is written, and applying the module's units failed",
                                                   fix: "moxi install ui" })),
                   { text: "Port is saved, and applying its daemons failed. Run moxi install ui.", copy: "moxi install ui",
                     still: false });
});

test("an answer drops the written changes and keeps the rest with their words", () => {
  const p = new Pending();
  p.edit("ui", MODE, "light");
  p.edit("ui", PORT, "8460a");
  p.edit("ui", IDLE, 60);
  p.answer("ui", UI, [result("port", "refused", { problem: "8460a is not a whole number" }), result("theme.mode", "saved"),
                      result("idle_s", "moved", { problem: "1200" })]);
  assert.deepEqual(p.changes("ui", UI).map((c) => c.name), ["port", "idle_s"]);
  assert.deepEqual(p.heldLine("ui", UI, "port"), { text: "still 18460", hurts: false });
  // A moved row keeps what was typed; its was follows the value now read, so a second Save writes it.
  assert.deepEqual(p.changeOf("ui", "idle_s"), { name: "idle_s", value: 60, reset: false, was: "1200" });
  assert.deepEqual(p.heldLine("ui", UI, "idle_s"), { text: "not saved · was 1200", hurts: false });
  assert.match(p.noteOf("ui", "idle_s").text, /to 1200\. Save again/);
  // Changing the row again clears its words.
  p.edit("ui", PORT, 8460);
  assert.equal(p.noteOf("ui", "port"), undefined);
});

test("a command that did not finish, or wrote and failed its daemons, is dropped with its words", () => {
  const p = new Pending();
  p.edit("ui", PORT, 1);
  p.edit("ui", IDLE, 2);
  p.answer("ui", UI, [result("port", "failed", { finished: false }), result("idle_s", "failed", { warning: "w" })]);
  assert.equal(p.count("ui"), 0);
  assert.equal(p.noteOf("ui", "port").text, NOT_FINISHED);
  assert.equal(p.noteOf("ui", "idle_s").copy, "moxi install ui");
});

test("the transport's words keep the changes", () => {
  assert.equal(transportWords(0), "No answer from the shell; nothing may have been saved.");
  assert.equal(transportWords(403), "This browser is signed out.");
  assert.equal(transportWords(500), "The shell answered 500.");
});

// --- a read that finds a held row moved --------------------------------------------------------------

test("a new read never moves a held row's was silently", () => {
  const p = new Pending();
  p.edit("ui", PORT, 9000);
  p.edit("ui", IDLE, 60);
  p.edit("ui", MODE, "dark");
  const now = [{ ...PORT, json: "20000" }, { ...IDLE, json: "60" }, MODE];
  p.refresh("ui", now);
  // Moved: what was typed stays, was follows the read, and the row says so at once.
  assert.deepEqual(p.changeOf("ui", "port"), { name: "port", value: 9000, reset: false, was: "20000" });
  assert.equal(p.noteOf("ui", "port").text,
               "Not saved: it changed since this page read it, to 20000. Save again to write yours.");
  // The file now holds what was typed: nothing is left to save.
  assert.equal(p.changeOf("ui", "idle_s"), undefined);
  // Not moved: untouched.
  assert.deepEqual(p.changeOf("ui", "theme.mode"), { name: "theme.mode", value: "dark", reset: false, was: '"system"' });
  assert.equal(p.noteOf("ui", "theme.mode"), undefined);
});

// --- can hurt -------------------------------------------------------------------------------------

test("a change meets hurts by direction: a bool turned on or off, a list's item added or removed", () => {
  const change = (k, value) => ({ name: k.name, value, reset: false, was: k.json });
  assert.deepEqual(hurtsOf(GUARD, change(GUARD, false)).map((h) => h.ask), ["Turn off No nested agents?"]);
  assert.deepEqual(hurtsOf(TRUST, change(TRUST, true)).map((h) => h.ask), ["Turn on Answer the folder-trust screen?"]);
  assert.deepEqual(hurtsOf(PASS, change(PASS, ["A", "TOKEN", "KEY"])).map((h) => h.ask),
                   ["Add TOKEN to Carry into a relaunch?", "Add KEY to Carry into a relaunch?"]);
  assert.deepEqual(hurtsOf(PASS, change(PASS, [])), []);
  assert.deepEqual(hurtsOf(NAMES, change(NAMES, ["ann"])).map((h) => h.ask), ["Remove bob from People?"]);
  assert.deepEqual(hurtsOf(NAMES, change(NAMES, ["ann", "bob", "cy"])), []);
  assert.deepEqual(hurtsOf(PORT, change(PORT, 1)), []);
});

test("Save turns into a confirm naming each act, then that nothing else can hurt", () => {
  const p = new Pending();
  p.edit("turns", GUARD, false);
  p.edit("turns", PORT, 1);
  assert.deepEqual(confirmOf([GUARD, PORT], p.changes("turns", [GUARD, PORT])), {
    asks: [{ ask: "Turn off No nested agents?", confirm: GUARD.hurts.confirm }],
    rest: NOTHING_ELSE, button: "Turn off and save", back: "Go back" });
  assert.equal(NOTHING_ELSE, "Nothing else in this save can hurt.");
  const both = new Pending();
  both.edit("x", PASS, ["A", "B"]);
  both.edit("x", NAMES, ["ann"]);
  const confirm = confirmOf([PASS, NAMES], both.changes("x", [PASS, NAMES]));
  assert.deepEqual(confirm.asks.map((a) => a.ask), ["Add B to Carry into a relaunch?", "Remove bob from People?"]);
  assert.equal(confirm.button, "Save anyway");
  assert.equal(confirmOf(UI, []), null);
});

test("a key whose held value is the danger stays in the warning colour while it holds it", () => {
  assert.equal(staysWarned(TRUST, true), true);
  assert.equal(staysWarned(TRUST, false), false);
  assert.equal(staysWarned(GUARD, false), false);
});

// --- leaving ----------------------------------------------------------------------------------------

test("leaving with changes in the shown module asks with Save and leave", () => {
  const p = new Pending();
  p.edit("ui", MODE, "light");
  p.edit("ui", PORT, 8460);
  const labels = () => ["Light or dark", "Port"];
  assert.deepEqual(leaveAsk(p, "ui", labels), {
    lines: ["Leave Settings with 2 changes in ui not saved? Light or dark, Port."],
    buttons: ["Stay", "Leave without saving", "Save and leave"] });
  assert.equal(leaveAsk(new Pending(), "ui", labels), null);
});

test("leaving with changes in several modules, or one not shown, names each and offers no save", () => {
  const p = new Pending();
  p.edit("ui", MODE, "light");
  p.edit("ui", PORT, 8460);
  p.edit("launcher", key("runs.keep_days", "int", 30, 30, { label: "Keep run files for" }), 14);
  const labels = (module) => (module === "ui" ? ["Light or dark", "Port"] : ["Keep run files for"]);
  assert.deepEqual(leaveAsk(p, "ui", labels), {
    lines: ["Leave Settings with 3 changes not saved?", "2 in ui: Light or dark, Port.", "1 in launcher: Keep run files for.",
            "Save writes one module at a time."],
    buttons: ["Leave without saving", "Stay"] });
  const one = new Pending();
  one.edit("ui", PORT, 1);
  assert.deepEqual(leaveAsk(one, "moxi", () => ["Port"]).lines,
                   ["Leave Settings with 1 change not saved?", "1 in ui: Port.", "Save writes one module at a time."]);
});
