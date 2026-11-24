import assert from "node:assert/strict";
import test from "node:test";

import {
  LOCKED, NOTHING_HERE, blocksOf, changedCount, controlOf, countOf, defaultsOf, drawnKeys, joined, labelOf, linesOf,
  listEntries, markLine, moduleOrder, moreWords, ownKeys, paneKeys, paneState, readFailed, readKeys, readonlyKey, rowOf,
  splitMore, tableFileWords, tablesOf, terminalLine, totalChanged, unreadableWords, valueWords,
} from "../src/settings-model.ts";

/** A key as `GET /api/settings` gives it: the value and default as JSON. */
function key(name, type, value, fallback, extra = {}) {
  return { name, type, json: JSON.stringify(value), default_json: JSON.stringify(fallback), source: "default",
           mark: "applies now", doc: `the ${name}`, label: null, line: null, common: false, group: null,
           unit: null, choices: null, pick: false, hurts: null, terminal: null, elsewhere: null,
           needs_absent: false, locked: false, changed: JSON.stringify(value) !== JSON.stringify(fallback), ...extra };
}

const section = (module, keys, extra = {}) => ({ module, keys, error: null, fix: null, readonly: false, file: null,
                                                 count: paneKeys(module, keys).length, ...extra });

const TERMINAL = { on: "moxi ui gateway enable", off: "moxi ui gateway disable" };

// ui's keys as its schema fills them.
const UI = [
  key("port", "int", 18460, 8460, { label: "Port", line: "Where this page is served, on this computer only.",
                                     common: true, source: "file", mark: "applies after `moxi install ui`" }),
  key("idle_s", "int", 900, 900, { label: "Idle shutdown", unit: "seconds", mark: "applies at next start" }),
  key("theme.mode", "enum", "system", "system", { values: ["system", "light", "dark"], label: "Light or dark", common: true,
    choices: [{ value: "system", words: "follow the browser" }, { value: "light", words: "light" }, { value: "dark", words: "dark" }] }),
  key("theme.palette", "enum", "plain", "plain", { values: ["plain", "midnight-neon"], label: "Look", pick: true, common: true,
    choices: [{ value: "plain", words: "Plain" }, { value: "midnight-neon", words: "Midnight neon" }] }),
  key("gateway.enabled", "bool", true, false, { label: "Phone access", terminal: TERMINAL, source: "file" }),
  key("gateway.port", "int", 18461, 8461, { label: "Phone port", source: "file", mark: "applies at next start" }),
];

const names = (keys) => keys.map((k) => k.name);

// --- which keys are drawn ---------------------------------------------------------------------

test("a key needing an absent module and one edited elsewhere are not drawn", () => {
  const keys = [key("a", "bool", false, false), key("b", "bool", false, false, { needs: "cache", needs_absent: true }),
                key("c", "string", "", "", { elsewhere: "Edited in Home's Options." }), key("d", "int", 1, 1)];
  assert.deepEqual(names(drawnKeys("demo", keys, true)), ["a", "d"]);
  assert.deepEqual(names(paneKeys("demo", keys)), ["a", "d"]);
});

test("ui's gateway keys are drawn keys only while the Phone group shows, and never pane rows", () => {
  assert.deepEqual(names(drawnKeys("ui", UI, false)), ["port", "idle_s", "theme.mode", "theme.palette"]);
  assert.deepEqual(names(drawnKeys("ui", UI, true)), names(UI));
  assert.deepEqual(names(paneKeys("ui", UI)), ["port", "idle_s", "theme.mode", "theme.palette"]);
  // Another module's gateway.* key is an ordinary key.
  assert.deepEqual(names(drawnKeys("demo", [key("gateway.port", "int", 1, 1)], false)), ["gateway.port"]);
});

test("the count adds the gateway keys to the read's count only while the group shows", () => {
  const ui = section("ui", UI);
  assert.equal(ui.count, 4);
  assert.equal(countOf(ui, false), 4);
  assert.equal(countOf(ui, true), 6);
  // The same set behind the count and the changed count.
  assert.equal(changedCount(ui, false), 1);
  assert.equal(changedCount(ui, true), 3);
  assert.equal(countOf(ui, true), drawnKeys("ui", UI, true).length);
});

test("a hidden key that is changed is not counted", () => {
  const keys = [key("a", "int", 2, 1), key("b", "int", 2, 1, { needs_absent: true }),
                key("c", "int", 2, 1, { elsewhere: "Edited in Home." })];
  assert.equal(changedCount(section("demo", keys), true), 1);
});

test("the list: the core, ui, then the rest A to Z", () => {
  assert.deepEqual(moduleOrder(["moxi", "usage", "comms", "ui", "cache"]), ["moxi", "ui", "cache", "comms", "usage"]);
  assert.deepEqual(moduleOrder(["moxi"]), ["moxi"]);
  assert.deepEqual(moduleOrder(["moxi", "turns", "turns"]), ["moxi", "turns"]);
});

test("the total changed is null until every listed section is in, and a section that can't be read counts none", () => {
  const listed = ["moxi", "ui", "usage"];
  const sections = new Map([["moxi", section("moxi", [key("a", "int", 2, 1)])], ["ui", section("ui", UI)]]);
  assert.equal(totalChanged(listed, sections, true), null);
  sections.set("usage", section("usage", [key("b", "int", 2, 1)], { error: "won't parse", keys: [] }));
  assert.equal(totalChanged(listed, sections, true), 4);
  assert.equal(totalChanged(listed, sections, false), 2);
});

// --- the row by type -------------------------------------------------------------------------

test("each type gets its control", () => {
  assert.deepEqual(controlOf(key("on", "bool", true, false)), { kind: "switch", on: true });
  assert.deepEqual(controlOf(key("n", "int", 8460, 8460, { unit: "seconds" })),
                   { kind: "number", text: "8460", unit: "seconds", whole: true });
  assert.deepEqual(controlOf(key("r", "float", 0.5, 1)), { kind: "number", text: "0.5", unit: null, whole: false });
  // A text held for a number that doesn't read as one shows as typed.
  assert.deepEqual(controlOf(key("n", "int", "8460a", 8460)), { kind: "number", text: "8460a", unit: null, whole: true });
  assert.deepEqual(controlOf(key("s", "string", "hi", "")), { kind: "text", text: "hi", path: false });
  assert.deepEqual(controlOf(key("p", "path", null, "")), { kind: "text", text: "", path: true });
  assert.deepEqual(controlOf(key("t", "table", { a: 1 }, {}, { value: "{a = 1}" })), { kind: "toml", text: "{a = 1}" });
});

test("an enum of two to four values sits side by side, with its words; five or more, or pick, is a pick list", () => {
  const ui = Object.fromEntries(UI.map((k) => [k.name, k]));
  assert.deepEqual(controlOf(ui["theme.mode"]), { kind: "choices", selected: "system", options: [
    { value: "system", words: "follow the browser" }, { value: "light", words: "light" }, { value: "dark", words: "dark" }] });
  assert.deepEqual(controlOf(ui["theme.palette"]), { kind: "pick", selected: "plain", options: [
    { value: "plain", words: "Plain" }, { value: "midnight-neon", words: "Midnight neon" }] });
  const five = key("e", "enum", "c", "a", { values: ["a", "b", "c", "d", "e"] });
  assert.equal(controlOf(five).kind, "pick");
  assert.deepEqual(controlOf(five).options[2], { value: "c", words: "c" });
  assert.equal(controlOf(key("e", "enum", "a", "a", { values: ["a", "b", "c", "d"] })).kind, "choices");
});

test("a string over 80 characters, or holding a line break, is its first line and Edit", () => {
  const long = "x".repeat(81);
  assert.deepEqual(controlOf(key("s", "string", long, "")), { kind: "long", text: long, first: long });
  assert.deepEqual(controlOf(key("s", "string", "one\ntwo", "")), { kind: "long", text: "one\ntwo", first: "one" });
  assert.equal(controlOf(key("s", "string", "x".repeat(80), "")).kind, "text");
});

test("a list of short items is boxes, of longer items lines, of tables rows", () => {
  assert.deepEqual(controlOf(key("l", "list", ["claude", "codex"], [])), { kind: "names", items: ["claude", "codex"] });
  assert.deepEqual(controlOf(key("l", "list", [], ["a"])), { kind: "names", items: [] });
  assert.deepEqual(controlOf(key("l", "list", [1, 2], [])), { kind: "names", items: ["1", "2"] });
  assert.deepEqual(controlOf(key("l", "list", ["Codex Desktop"], [])),
                   { kind: "lines", items: ["Codex Desktop"], first: "Codex Desktop" });
  assert.equal(controlOf(key("l", "list", ["x".repeat(25)], [])).kind, "lines");
  assert.equal(controlOf(key("l", "list", ["x".repeat(24)], [])).kind, "names");
  const rows = [{ name: "claude", skills: "" }, { name: "opencode", skills: "/s", agents: "/a" }];
  assert.deepEqual(controlOf(key("harnesses", "list", rows, [])),
                   { kind: "rows", columns: ["name", "skills", "agents"], rows });
  // An empty list of tables takes its columns from the default's rows.
  assert.deepEqual(controlOf(key("harnesses", "list", [], rows)),
                   { kind: "rows", columns: ["name", "skills", "agents"], rows: [] });
});

test("a switch table is one switch per entry, each with its mark and lock", () => {
  const table = key("rows", "table", { backup: true }, {}, { switch_table: true, entries: [
    { name: "backup", key: "rows.backup", value: true, source: "file", mark: "turns the daemon on or off now" },
    { name: "sync", key: "rows.sync", value: false, source: "default", mark: "applies now", locked: true }] });
  assert.deepEqual(controlOf(table), { kind: "switches", toggles: [
    { key: "rows.backup", label: "backup", on: true, line: "Turns its daemon on or off.", locked: false },
    { key: "rows.sync", label: "sync", on: false, line: null, locked: true }] });
});

test("a switch table the read marks locked keeps its other entries open, with no Reset and no line of its own", () => {
  const table = key("rows", "table", { spare: false }, {}, { switch_table: true, locked: true, source: "file",
    changed: false, entries: [
      { name: "backup", key: "rows.backup", value: false, source: "default", mark: "applies now", locked: true },
      { name: "spare", key: "rows.spare", value: false, source: "file", mark: "applies now", locked: false }] });
  const row = rowOf(table);
  assert.equal(row.locked, false);
  assert.ok(!row.lines.includes(LOCKED));
  assert.deepEqual(row.control.toggles.map((t) => [t.key, t.locked]), [["rows.backup", true], ["rows.spare", false]]);
  assert.deepEqual(defaultsOf({ ...table, json: JSON.stringify({ backup: true }), changed: true }),
                   { words: "none", reset: false });
  // A plain key the read marks locked is off, and says so.
  const limit = rowOf(key("limit", "int", 7, 10, { locked: true, source: "file" }));
  assert.equal(limit.locked, true);
  assert.equal(limit.lines.at(-1), LOCKED);
});

test("a terminal key is its value as a word, no control, and the command for the other state", () => {
  const on = UI.find((k) => k.name === "gateway.enabled");
  assert.deepEqual(controlOf(on), { kind: "word", text: "on" });
  assert.deepEqual(terminalLine(on), { lead: "Turned off at the terminal:",
                                       command: "moxi ui gateway disable" });
  const off = { ...on, json: "false", changed: false };
  assert.deepEqual(controlOf(off), { kind: "word", text: "off" });
  assert.deepEqual(terminalLine(off), { lead: "Turned on at the terminal:",
                                        command: "moxi ui gateway enable" });
  assert.equal(terminalLine(UI[0]), null);
});

// --- the label and the lines ----------------------------------------------------------------------

test("a key with a label leads with it, its key dim beside; without one, its key alone", () => {
  assert.equal(labelOf(UI[0]), "Port");
  assert.equal(rowOf(UI[0]).labelled, true);
  const bare = key("snapshot.timer", "bool", false, false);
  assert.equal(labelOf(bare), "snapshot.timer");
  assert.equal(rowOf(bare).labelled, false);
});

test("the line: line with a label, doc without one, the mark, the environment, the lock", () => {
  assert.deepEqual(linesOf(UI[0]), ["Where this page is served, on this computer only.", "Applies after moxi install ui."]);
  // A label with no line has no sentence; the mark still shows.
  assert.deepEqual(linesOf(UI[1]), ["Applies at the next start."]);
  assert.deepEqual(linesOf(key("a", "int", 1, 1)), ["the a"]);
  assert.deepEqual(linesOf(key("a", "int", 1, 1, { source: "env", env: "MOXI_X_A" })),
                   ["the a", "Set by MOXI_X_A in the shell's environment, which wins over the file."]);
  assert.deepEqual(linesOf(key("a", "int", 1, 1, { locked: true, label: "A" })), [LOCKED]);
  assert.equal(rowOf(key("a", "int", 1, 1, { locked: true })).locked, true);
  assert.deepEqual(linesOf(key("t", "table", {}, {}, { switch_table: true, entries: [], declarations: "unavailable",
                                                        reason: "the units could not be read." })),
                   ["the t", "the units could not be read."]);
});

test("each mark reads as a sentence, and one that applies at once has none", () => {
  assert.equal(markLine("applies now"), null);
  assert.equal(markLine("applies at next start"), "Applies at the next start.");
  assert.equal(markLine("applies after `moxi install ui`"), "Applies after moxi install ui.");
  assert.equal(markLine("turns the daemon on or off now"), "Turns its daemon on or off.");
  assert.equal(markLine("applies when the shell next starts"), "Applies when the shell next starts.");
  assert.equal(markLine("starts the shell at login; the shell starts at login now"),
               "Starts the shell at login; the shell starts at login now.");
  assert.equal(markLine(null), null);
});

// --- default and Reset --------------------------------------------------------------------------------

test("default shows on every changed key; Reset only where a write changes the value read", () => {
  assert.deepEqual(defaultsOf(UI[0]), { words: "8460", reset: true });
  assert.equal(defaultsOf(UI[1]), null);
  // Set by the environment, locked, or changed at the terminal: no Reset.
  assert.deepEqual(defaultsOf(key("a", "int", 2, 1, { source: "env", env: "MOXI_X_A" })), { words: "1", reset: false });
  assert.deepEqual(defaultsOf(key("a", "int", 2, 1, { source: "file", locked: true })), { words: "1", reset: false });
  assert.deepEqual(defaultsOf(UI[4]), { words: "off", reset: false });
  assert.deepEqual(rowOf(UI[5]).defaults, { words: "8461", reset: true });
});

test("a value in words: on and off, an enum's words, not set, none, items joined, rows by name", () => {
  const mode = UI[2];
  assert.equal(valueWords(mode, "system"), "follow the browser");
  assert.equal(valueWords(mode, "other"), "other");
  assert.equal(valueWords(UI[4], false), "off");
  assert.equal(valueWords(UI[0], 8460), "8460");
  assert.equal(valueWords(key("p", "path", null, ""), ""), "not set");
  assert.equal(valueWords(key("p", "path", null, null), null), "not set");
  assert.equal(valueWords(key("l", "list", [], []), []), "none");
  assert.equal(valueWords(key("l", "list", [], []), ["claude", "codex"]), "claude and codex");
  assert.equal(valueWords(key("l", "list", [], []), [{ name: "claude" }, { name: "codex" }, { name: "opencode" }]),
               "claude, codex and opencode");
  const table = key("rows", "table", {}, {}, { switch_table: true, entries: [] });
  assert.equal(valueWords(table, {}), "none");
  assert.equal(valueWords(table, { a: true, b: false, c: true }), "a and c");
  assert.equal(valueWords(key("t", "table", {}, {}), { a: 1 }), "a = 1");
  assert.equal(joined(["a"]), "a");
  assert.equal(joined([]), "");
});

// --- More and the sub-heads ------------------------------------------------------------------------

test("common keys come first and the rest behind More; no common key, no More", () => {
  const ui = paneKeys("ui", UI);
  const { first, more } = splitMore(ui);
  assert.deepEqual(names(first), ["port", "theme.mode", "theme.palette"]);
  assert.deepEqual(names(more), ["idle_s"]);
  assert.deepEqual(moreWords(more), { text: "1 setting: idle shutdown", changed: null });
  const plain = [key("a", "int", 1, 1), key("b", "int", 1, 1)];
  assert.deepEqual(splitMore(plain), { first: plain, more: null });
  // Every key common: nothing behind More.
  assert.equal(splitMore([key("a", "int", 1, 1, { common: true })]).more, null);
});

test("More names the groups in order, then the keys with no group, in lower case, and how many changed", () => {
  const more = [
    key("model", "list", [], [], { label: "Your own model prices", group: "Prices" }),
    key("index.workers", "int", 0, 0, { label: "Processes", group: "Reading" }),
    key("prices.url", "string", "u", "u", { label: "Where the price file comes from", group: "Prices" }),
    key("skills.form", "enum", "link", "auto", { label: "How skills are placed" }),
    key("harnesses", "list", [], [], { label: "Harness folders" }),
    key("bare.key", "int", 2, 1),
  ];
  assert.deepEqual(moreWords(more), {
    text: "6 settings: prices, reading, how skills are placed, harness folders, bare.key", changed: "2 changed" });
});

test("a group is drawn once, gathering its keys in file order wherever they sit in the file", () => {
  const keys = [key("ready.wait_s", "int", 1, 1, { group: "Waits" }), key("relaunch.wait_s", "int", 1, 1, { group: "Relaunch" }),
                key("first.wait_s", "int", 1, 1, { group: "Waits" }), key("loose", "int", 1, 1),
                key("relaunch.person_s", "int", 1, 1, { group: "Relaunch" }), key("startup.wait_s", "int", 1, 1, { group: "Waits" }),
                key("other", "int", 1, 1)];
  assert.deepEqual(blocksOf(keys).map((block) => [block.group, names(block.keys)]), [
    ["Waits", ["ready.wait_s", "first.wait_s", "startup.wait_s"]], ["Relaunch", ["relaunch.wait_s", "relaunch.person_s"]],
    [null, ["loose", "other"]]]);
});

test("sub-heads follow the order their groups first appear in", () => {
  const keys = [key("a", "bool", false, false, { group: "Guards" }), key("b", "bool", false, false, { group: "Guards" }),
                key("c", "list", [], [], { group: "Sessions" }), key("d", "int", 1, 1)];
  assert.deepEqual(blocksOf(keys).map((block) => [block.group, names(block.keys)]),
                   [["Guards", ["a", "b"]], ["Sessions", ["c"]], [null, ["d"]]]);
  assert.deepEqual(blocksOf([]), []);
});

// --- the frame ----------------------------------------------------------------------------

test("the list: each listed module in order, its count once its section is in, can't be read on an error", () => {
  const sections = new Map([
    ["ui", section("ui", UI)],
    ["fixture", section("fixture", [key("limit", "int", 12, 10)])],
    ["broken", section("broken", [], { error: "the file is not valid TOML", fix: "correct it" })],
  ]);
  assert.deepEqual(listEntries(["fixture", "ui", "broken", "moxi", "cache"], sections, false), [
    { module: "moxi", changed: null, unreadable: false },
    { module: "ui", changed: 1, unreadable: false },
    { module: "broken", changed: null, unreadable: true },
    { module: "cache", changed: null, unreadable: false },
    { module: "fixture", changed: 1, unreadable: false },
  ]);
  // ui's phone keys count while the Phone group shows.
  assert.equal(listEntries(["ui"], sections, true)[1].changed, 3);
});

test("the pane: reading before its section, can't be read on an error, nothing here with no drawn key, else rows", () => {
  assert.equal(paneState(undefined), "reading");
  const broken = section("broken", [], { error: "the file is not valid TOML", fix: "correct it" });
  assert.equal(paneState(broken), "unreadable");
  assert.equal(unreadableWords(broken), "broken's settings can't be read: the file is not valid TOML");
  assert.equal(paneState(section("x", [key("p", "string", "", "", { needs: "nosuch", needs_absent: true })])), "empty");
  assert.equal(paneState(section("ui", UI.filter((k) => k.name.startsWith("gateway.")))), "empty");
  assert.equal(paneState(section("ui", UI)), "rows");
  assert.equal(NOTHING_HERE, "Nothing here can be changed on this page.");
});

test("a read with no usable answer says why in today's words", () => {
  assert.equal(readFailed(0, null), "No answer from the shell, so the settings could not be read.");
  assert.equal(readFailed(403, { error: "signed out" }), "This browser is signed out.");
  assert.equal(readFailed(500, { error: "the snapshot is broken" }), "the snapshot is broken");
  assert.equal(readFailed(502, "bad gateway"), "The shell answered 502 for the settings.");
});

// --- a table a module keeps in a file of its own ---------------------------------------------

const APPLIES = "Applies to sessions launched after this. Running sessions keep their own.";
const TABLE = { table_title: "Launch defaults", table_line: "Every launch starts from these.",
                table_file: "~/.config/moxi/launcher/presets.toml", mark: APPLIES, warning: null, refused: null,
                clear_only: false };
const row = (name, type, value, fallback, extra = {}) => key(`defaults.${name}`, type, value, fallback,
                                                              { ...TABLE, label: name, ...extra });
const ROWS = [
  row("typo_row", "int", 3, null, { group: "needs a fix", source: "file", clear_only: true,
                                    refused: "typo_row is not a launcher row; moxi launcher catalog lists the rows." }),
  row("permission_mode", "enum", "plan", "default", { group: "access", source: "file",
                                                      values: ["default", "plan", "acceptEdits"] }),
  row("recycle_at_limit", "enum", "relaunch", "compact", { group: "lifecycle", source: "file", values: ["off", "compact"],
                                                           warning: "A launch with no preset is refused." }),
];
const OWN = [key("ready.wait_s", "int", 60, 60, { label: "Ready", common: true }),
             key("runs.keep_days", "int", 14, 30, { label: "Keep run files for", source: "file" })];
const ERROR_ITEM = { name: "defaults", table_title: "Launch defaults", table_line: "Every launch starts from these.",
                     table_file: "~/presets.toml", table_error: "is not valid TOML", table_fix: "correct the file" };

test("a module's table keys stay out of More, in one block per table under its title, line and file", () => {
  const keys = paneKeys("launcher", [...OWN, ...ROWS]);
  assert.deepEqual(names(ownKeys(keys)), ["ready.wait_s", "runs.keep_days"]);
  const { first, more } = splitMore(ownKeys(keys));
  assert.deepEqual([names(first), names(more)], [["ready.wait_s"], ["runs.keep_days"]]);
  const [table, ...others] = tablesOf([...OWN, ...ROWS], keys);
  assert.equal(others.length, 0);
  assert.deepEqual([table.title, table.line, table.error], ["Launch defaults", "Every launch starts from these.", null]);
  assert.deepEqual(names(table.keys), ROWS.map((k) => k.name));
  assert.deepEqual(blocksOf(table.keys).map((block) => block.group), ["needs a fix", "access", "lifecycle"]);
  assert.equal(tableFileWords(table), "Launch defaults are kept in ~/.config/moxi/launcher/presets.toml");
  assert.deepEqual(tablesOf(OWN, OWN), []);
});

test("a table key's mark is its applies words, as a sentence", () => {
  assert.equal(markLine(APPLIES), APPLIES);
  assert.deepEqual(linesOf({ ...ROWS[1], line: "Who approves a tool." }), ["Who approves a tool.", APPLIES]);
});

test("refused and warning lines ride on the row; a clear-only key is its value as a word with Reset alone", () => {
  const [typo, mode, recycle] = ROWS.map(rowOf);
  assert.equal(typo.refused, ROWS[0].refused);
  assert.equal(typo.warning, null);
  assert.deepEqual(typo.control, { kind: "word", text: "3" });
  assert.deepEqual(typo.defaults, { words: "not set", reset: true });
  assert.deepEqual([mode.refused, mode.warning], [null, null]);
  assert.equal(recycle.warning, "A launch with no preset is refused.");
  // Locked, a clear-only key has no Reset either.
  assert.equal(defaultsOf({ ...ROWS[0], locked: true }).reset, false);
});

test("an enum value its list doesn't offer stays in the list as the chosen one", () => {
  const control = controlOf(ROWS[2]);
  assert.equal(control.selected, "relaunch");
  assert.deepEqual(control.options.map((option) => option.value), ["off", "compact", "relaunch"]);
  // A value the list offers adds nothing.
  assert.deepEqual(controlOf(ROWS[1]).options.map((option) => option.value), ["default", "plan", "acceptEdits"]);
});

test("a text key holding another kind of value shows its spelling", () => {
  const flag = row("remote_control", "string", true, null, { value: "true" });
  assert.deepEqual(controlOf(flag), { kind: "text", text: "true", path: false });
});

test("a table that can't be read is its error in its block, never a row or a count", () => {
  const keys = [...OWN, ERROR_ITEM];
  assert.deepEqual(names(drawnKeys("launcher", keys, false)), ["ready.wait_s", "runs.keep_days"]);
  const [table] = tablesOf(keys, drawnKeys("launcher", keys, false));
  assert.deepEqual([table.title, table.error, table.fix, table.keys], ["Launch defaults", "is not valid TOML",
                                                                       "correct the file", []]);
  assert.equal(changedCount(section("launcher", keys), false), 1);
  assert.equal(paneState(section("launcher", [ERROR_ITEM])), "rows");
});

test("a module whose own file can't be read keeps its tables' keys read, counted and editable", () => {
  const broken = section("launcher", ROWS, { error: "the file is not valid TOML", fix: "correct it", readonly: true,
                                             count: ROWS.length });
  assert.deepEqual(names(readKeys(broken)), ROWS.map((k) => k.name));
  assert.equal(paneState(broken), "rows");
  assert.equal(readonlyKey(broken, ROWS[1]), false);
  assert.equal(readonlyKey(broken, OWN[0]), true);
  assert.equal(readonlyKey(section("launcher", OWN), OWN[0]), false);
  // Its own keys, were any held, are not read; its table keys count.
  assert.deepEqual(names(readKeys({ ...broken, keys: [...OWN, ...ROWS] })), ROWS.map((k) => k.name));
  assert.equal(changedCount(broken, false), 3);
  assert.equal(totalChanged(["launcher"], new Map([["launcher", broken]]), false), 3);
  assert.deepEqual(listEntries(["launcher"], new Map([["launcher", broken]]), false)[1],
                   { module: "launcher", changed: 3, unreadable: true });
  // With neither, the pane says it can't be read.
  assert.equal(paneState({ ...broken, keys: [] }), "unreadable");
});
