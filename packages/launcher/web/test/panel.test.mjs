import assert from "node:assert/strict";
import test from "node:test";

import {
  choiceKey, choiceOf, chosenPreset, commandLine, emptyGroups, fieldsFor, filledFields, followedOf, formPath,
  HARNESS, keepLabel, launchBody, launchesView, leaveOutChoice, NO_PRESET, olderPath, outcomeOf, parseCatalog,
  parseForm, parsePasts, parsePresets, parsePreview, pastItem, pastView, presetLines, presetProblems, presetsOf,
  quote, refusalLine, requestOf, shownValue, sourceNote, statusOf,
} from "../src/panel.ts";

const T = "2026-10-31T10:01:02.123456Z";
const field = (name, extra = {}) => ({ name, type: "string", doc: "", default: null, value_items: null,
                                      values: null, items: null, flag: `--${name}`, since: null,
                                      repeatable: false, optional_value: false, hidden: false,
                                      posture: false, requires: null, needs_absent: false, note: null,
                                      ...extra });
const LAUNCHER = [field("harness", { type: "enum", default: "claude", values: ["claude", "codex"] }),
                  field("cwd", { type: "path" }),
                  field("target", { type: "enum", default: "terminal", values: ["terminal", "headless", "slot"] }),
                  field("scratch", { type: "bool", default: "false" }),
                  field("add_dir", { type: "list", items: "path", value_items: [] })];
const CATALOG = {
  state: "up", detail: null,
  harnesses: [
    { name: "claude", fields: [...LAUNCHER, field("model"), field("effort", { type: "enum", values: ["low", "high"] }),
                               field("remote_control", { optional_value: true })] },
    { name: "codex", fields: [...LAUNCHER, field("model"), field("sandbox")] },
  ],
};
const catalog = parseCatalog(CATALOG);

// --- the catalog ---------------------------------------------------------------------------

test("the catalog reads per harness, and an error or a bad body is told apart", () => {
  assert.equal(catalog.harnesses.length, 2);
  assert.deepEqual(catalog.harnesses.map((h) => h.fields.length), [8, 7]);
  assert.deepEqual(parseCatalog({ state: "error", detail: "it broke", harnesses: [] }),
                   { state: "error", detail: "it broke", harnesses: [] });
  assert.equal(parseCatalog({ state: "up", detail: null }), null);
  assert.equal(parseCatalog("nope"), null);
});

test("a harness's form shows that harness in its harness row, each name once", () => {
  const codex = fieldsFor(catalog, "codex");
  assert.equal(codex.find((f) => f.name === HARNESS).default, "codex");
  assert.equal(fieldsFor(catalog, "claude").find((f) => f.name === HARNESS).default, "claude");
  // The catalog's own row is left as it was.
  assert.equal(catalog.harnesses[1].fields[0].default, "claude");
  const twice = parseCatalog({ ...CATALOG, harnesses: [{ name: "claude", fields: [field("a"), field("a", { doc: "2" })] }] });
  assert.deepEqual(fieldsFor(twice, "claude").map((f) => f.doc), [""]);
  assert.deepEqual(fieldsFor(catalog, "nope"), []);
  assert.deepEqual(fieldsFor(null, "claude"), []);
});

// --- the form, filled from the layers --------------------------------------------------------

const FORM_FIELDS = [
  field("harness", { type: "enum", default: "claude", values: ["claude", "codex"] }),
  field("cwd", { type: "path", default: "" }),
  field("project", { default: "" }),
  field("scratch", { type: "bool", default: "false" }),
  field("brief", { default: "" }),
  field("brief_file", { type: "path", default: "" }),
  field("target", { type: "enum", default: "terminal", values: ["terminal", "headless", "slot"] }),
  field("limit_at", { type: "enum", default: "1x", values: ["1x", "2x"] }),
  field("add_dir", { type: "list", items: "path", default: null, value_items: null }),
  field("director", { type: "list", repeatable: true, needs_absent: true, note: "works only with the comms module" }),
  field("model", { default: "" }),
  field("effort", { type: "enum", default: "", values: ["low", "high"] }),
  field("remote_control", { optional_value: true }),
  field("max_turns", { type: "int", default: "" }),
];
const row = (name, value, source = "preset", extra = {}) => ({ name, value, items: null, source, withheld: false, ...extra });
const FORM = {
  state: "up", detail: null, harness: "claude", refused: null,
  rows: [row("harness", "claude", "shipped"), row("cwd", null, "shipped"), row("project", "app"),
         row("scratch", "false", "shipped"), row("brief", null, "preset", { withheld: true }),
         row("brief_file", null, "shipped"), row("target", "slot", "defaults"), row("limit_at", "1x", "shipped"),
         row("add_dir", null, "preset", { items: ["/a", "/b"] }), row("model", "opus"),
         row("effort", null, "shipped"), row("remote_control", "true"), row("max_turns", null, "shipped")],
};
const SEAT = { kind: "seat", name: "planner" };
const filled = (form = FORM, choice = SEAT) => filledFields(FORM_FIELDS, parseForm(form), choice);
const byName = (fields) => Object.fromEntries(fields.map((f) => [f.name, f]));
/** What `<moxi-ui-form mode="submit">` gives for `fields` left as they are: a field shown off
 * sends nothing. */
const asShown = (fields) => Object.fromEntries(fields.filter((f) => !f.needs_absent).map((f) => [f.name, shownValue(f)]));
const WITHHELD = new Set(["brief"]);

test("the form's answer reads whole, and an error, a refusal and a bad body are told apart", () => {
  const form = parseForm({ ...FORM, rows: [...FORM.rows, { name: 7 }, "x"] });
  assert.equal(form.harness, "claude");
  assert.equal(form.rows.length, FORM.rows.length);
  assert.deepEqual(form.rows.find((r) => r.name === "add_dir"), row("add_dir", null, "preset", { items: ["/a", "/b"] }));
  assert.equal(form.rows.find((r) => r.name === "brief").withheld, true);
  const refused = parseForm({ ...FORM, rows: [], refused: { code: "bad-preset", row: null, problem: "no", hint: "h", x: 1 } });
  assert.deepEqual(refused.refused, { code: "bad-preset", row: null, problem: "no", hint: "h" });
  assert.deepEqual(parseForm({ state: "error", detail: "it broke" }),
                   { state: "error", detail: "it broke", harness: "", rows: [], refused: null, modes: [] });
  assert.equal(parseForm({ state: "up", detail: null, rows: [] }), null);
  assert.equal(parseForm("nope"), null);
});

test("one preset choice: one select value, one query, and a body naming at most one preset", () => {
  assert.equal(choiceKey(SEAT), "seat:planner");
  assert.equal(choiceKey(NO_PRESET), "");
  assert.deepEqual(choiceOf("seat:planner"), SEAT);
  assert.deepEqual(choiceOf("project:a:b"), { kind: "project", name: "a:b" });
  for (const bad of ["", "seat:", ":x", "other:x", "planner"]) assert.deepEqual(choiceOf(bad), NO_PRESET);
  assert.equal(formPath(NO_PRESET), "api/form");
  assert.equal(formPath(SEAT), "api/form?seat=planner");
  assert.equal(formPath({ kind: "project", name: "a b" }, "codex"), "api/form?project_preset=a+b&harness=codex");
  const request = { values: { model: "opus" }, leave_out: ["cwd"] };
  assert.deepEqual(launchBody(SEAT, request, false), { seat: "planner", values: { model: "opus" }, leave_out: ["cwd"] });
  assert.deepEqual(launchBody({ kind: "project", name: "app" }, request, true),
                   { project_preset: "app", values: { model: "opus" }, leave_out: ["cwd"], off_posture: true });
  assert.deepEqual(launchBody(NO_PRESET, { values: {}, leave_out: [] }, false), { values: {}, leave_out: [] });
  const presets = parsePresets(PRESETS);
  assert.equal(chosenPreset(presets, { kind: "project", name: "app" }).name, "app");
  assert.equal(chosenPreset(presets, { kind: "seat", name: "app" }), null);
  assert.equal(chosenPreset(presets, NO_PRESET), null);
});

test("a filled field shows its layered value with a note naming the source; the shipped default has none", () => {
  const f = byName(filled());
  assert.equal(f.project.value, "app");
  assert.equal(f.project.note, "set by the seat planner");
  assert.equal(f.target.value, "slot");
  assert.equal(f.target.note, "set in your [defaults]");
  assert.equal(f.cwd.value, "");
  assert.equal(f.cwd.note, null);
  assert.equal(f.scratch.value, "false");
  assert.deepEqual(f.add_dir.value_items, ["/a", "/b"]);
  assert.equal(f.model.value, "opus");
  assert.equal(f.remote_control.value, true);
  assert.equal(f.harness.value, "claude");
  assert.equal(f.harness.note, null);
  assert.equal(byName(filled(FORM, { kind: "project", name: "app" })).project.note, "set by the project preset app");
  assert.equal(sourceNote("shipped", SEAT), null);
  // The catalog's own descriptors are left as they were.
  assert.equal(FORM_FIELDS[2].value, undefined);
  assert.deepEqual(FORM_FIELDS[6].values, ["terminal", "headless", "slot"]);
});

test("a switch on with text shows the text, and off shows off", () => {
  const on = byName(filled({ ...FORM, rows: [row("remote_control", "box")] }));
  assert.equal(on.remote_control.value, "box");
  assert.equal(shownValue(on.remote_control), "box");
  const off = byName(filled({ ...FORM, rows: [row("remote_control", null, "shipped")] }));
  assert.equal(off.remote_control.value, null);
  assert.equal(shownValue(off.remote_control), null);
});

test("each enum gets its empty state: a blank choice, or leave out naming the word that launches", () => {
  const f = byName(filled());
  assert.deepEqual(f.effort.values, ["", "low", "high"]);
  assert.equal(f.effort.value, "");
  assert.deepEqual(f.target.values, ["terminal", "headless", "slot", "leave out (launches terminal)"]);
  // A row no layer sets shows its shipped word, not "leave out".
  assert.equal(f.limit_at.value, "1x");
  assert.deepEqual(f.limit_at.values, ["1x", "2x", "leave out (launches 1x)"]);
  assert.deepEqual(f.harness.values, ["claude", "codex"]);
  assert.equal(leaveOutChoice(field("recycle_at_limit", { type: "enum", default: "off" })), "leave out (launches off)");
  assert.equal(leaveOutChoice(f.effort), null);
  assert.equal(leaveOutChoice(f.harness), null);
  assert.equal(leaveOutChoice(f.model), null);
  assert.equal(leaveOutChoice(undefined), null);
});

test("a withheld text shows empty with its note, and its keep box names who sets it", () => {
  const f = byName(filled());
  assert.equal(f.brief.value, "");
  assert.equal(f.brief.note, "set by the seat planner; its text is not shown here");
  assert.equal(keepLabel(row("brief", null, "preset", { withheld: true }), SEAT), "Keep the brief the seat planner sets");
  assert.equal(keepLabel(row("instructions", null, "defaults", { withheld: true }), SEAT),
               "Keep the standing instructions your [defaults] set");
  const fromDefaults = byName(filled({ ...FORM, rows: [row("brief", null, "defaults", { withheld: true })] }, NO_PRESET));
  assert.equal(fromDefaults.brief.note, "set in your [defaults]; its text is not shown here");
});

test("the form is drawn for the answer's harness; the harness row's note only when it names that one", () => {
  const asked = byName(filled({ ...FORM, harness: "codex", rows: [row("harness", "claude")] }));
  assert.equal(asked.harness.value, "codex");
  assert.equal(asked.harness.note, null);
  const named = byName(filled({ ...FORM, harness: "codex", rows: [row("harness", "codex")] }));
  assert.equal(named.harness.note, "set by the seat planner");
});

test("a field shown off, and every field of a refused answer, keeps the catalog's default", () => {
  const f = byName(filled());
  assert.equal(f.director.note, "works only with the comms module");
  assert.equal(f.director.value_items, null);
  const refused = byName(filled({ ...FORM, rows: [], refused: { code: "bad-preset", row: null, problem: "no", hint: "" } }));
  assert.equal(shownValue(refused.target), "terminal");
  assert.equal(shownValue(refused.model), null);
  assert.equal(refused.target.note, null);
  assert.equal(refused.target.values.at(-1), "leave out (launches terminal)");
});

// --- the request, from the whole form --------------------------------------------------------

test("each field kind's value goes in values, as the page server reads it", () => {
  const fields = filled();
  const VALUE = { harness: "claude", cwd: "/w", project: "app", scratch: "true", brief: "mine", brief_file: "/b.md",
                  target: "slot", limit_at: "1x", add_dir: ["/a"], model: "opus", effort: "high",
                  remote_control: "", max_turns: "5" };
  assert.deepEqual(requestOf(fields, VALUE, WITHHELD, new Set(["brief"])), {
    values: { ...VALUE, remote_control: "true" },
    leave_out: [],
  });
  assert.deepEqual(requestOf(fields, { remote_control: "box" }, WITHHELD, new Set()),
                   { values: { remote_control: "box" }, leave_out: [] });
});

test("each field kind's empty state goes in leave_out, and the harness never does", () => {
  const fields = filled();
  const EMPTY = { harness: "codex", cwd: null, project: null, scratch: "false", brief: null, brief_file: null,
                  target: "leave out (launches terminal)", limit_at: "leave out (launches 1x)", add_dir: [],
                  model: null, effort: null, remote_control: null, max_turns: null };
  assert.deepEqual(requestOf(fields, EMPTY, WITHHELD, new Set()), {
    values: { harness: "codex" },
    leave_out: ["cwd", "project", "scratch", "brief", "brief_file", "target", "limit_at", "add_dir", "model",
                "effort", "remote_control", "max_turns"],
  });
  assert.deepEqual(requestOf(fields, { harness: null }, WITHHELD, new Set()), { values: {}, leave_out: [] });
});

test("a kept withheld text is in neither list; unticked it is left out; typed it is the user's own", () => {
  const fields = filled();
  assert.deepEqual(requestOf(fields, { brief: null }, WITHHELD, new Set(["brief"])), { values: {}, leave_out: [] });
  assert.deepEqual(requestOf(fields, { brief: null }, WITHHELD, new Set()), { values: {}, leave_out: ["brief"] });
  assert.deepEqual(requestOf(fields, { brief: "mine" }, WITHHELD, new Set(["brief"])),
                   { values: { brief: "mine" }, leave_out: [] });
  // The keep box is for a withheld text only: another empty text is left out.
  assert.deepEqual(requestOf(fields, { brief_file: null }, WITHHELD, new Set(["brief_file"])),
                   { values: {}, leave_out: ["brief_file"] });
});

test("the form left as filled sends every row as it shows, and a field shown off in neither list", () => {
  const fields = filled();
  const request = requestOf(fields, asShown(fields), WITHHELD, new Set(["brief"]));
  assert.deepEqual(request.values, { harness: "claude", project: "app", target: "slot", limit_at: "1x",
                                     add_dir: ["/a", "/b"], model: "opus", remote_control: "true" });
  assert.deepEqual(request.leave_out, ["cwd", "scratch", "brief_file", "effort", "max_turns"]);
  assert.ok(!("director" in request.values) && !request.leave_out.includes("director"));
});

// --- rows that exclude each other ------------------------------------------------------------

test("setting one place row empties the group's rows that still show their filled value", () => {
  const fields = filled();
  const out = emptyGroups(fields, { ...asShown(fields), cwd: "/w" }, WITHHELD, new Set(["brief"]));
  const f = byName(out.fields);
  assert.equal(f.project.value, "");
  assert.equal(shownValue(f.project), null);
  // The note still says who set the value the user replaced.
  assert.equal(f.project.note, "set by the seat planner");
  assert.equal(f.scratch, byName(fields).scratch);
  assert.equal(f.model, byName(fields).model);
  assert.deepEqual([...out.keep], ["brief"]);
  // Drawn again with the emptied row, nothing more changes.
  assert.equal(emptyGroups(out.fields, { ...asShown(out.fields), cwd: "/w" }, WITHHELD, out.keep).fields, out.fields);
  // Ticking scratch empties the project too.
  assert.equal(byName(emptyGroups(fields, { ...asShown(fields), scratch: "true" }, WITHHELD, new Set()).fields).project.value, "");
});

test("a scratch seat's ticked box is unticked when the user names a cwd", () => {
  const fields = filled({ ...FORM, rows: [row("scratch", "true"), row("cwd", null, "shipped"), row("project", null, "shipped")] });
  const out = byName(emptyGroups(fields, { ...asShown(fields), cwd: "/w" }, new Set(), new Set()).fields);
  assert.equal(out.scratch.value, "false");
  assert.equal(shownValue(out.scratch), "false");
});

test("a row the user typed himself stays when he sets another of its group", () => {
  const fields = filled();
  const now = { ...asShown(fields), project: "mine", cwd: "/w" };
  assert.equal(emptyGroups(fields, now, WITHHELD, new Set()).fields, fields);
});

test("emptying a row is not setting it, and a form as filled changes nothing", () => {
  const fields = filled({ ...FORM, rows: [row("scratch", "true"), row("project", null, "shipped")] });
  assert.equal(emptyGroups(fields, { ...asShown(fields), scratch: "false" }, new Set(), new Set()).fields, fields);
  const filledForm = filled();
  const out = emptyGroups(filledForm, asShown(filledForm), WITHHELD, new Set(["brief"]));
  assert.equal(out.fields, filledForm);
  assert.deepEqual([...out.keep], ["brief"]);
});

test("setting one spelling of a text unticks a withheld sibling's keep box", () => {
  const fields = filled();
  const out = emptyGroups(fields, { ...asShown(fields), brief_file: "/b.md" }, WITHHELD, new Set(["brief"]));
  assert.deepEqual([...out.keep], []);
  assert.equal(out.fields, fields);
  // The brief now goes in leave_out, so the file's brief is the one sent.
  assert.deepEqual(requestOf(fields, { brief: null, brief_file: "/b.md" }, WITHHELD, out.keep),
                   { values: { brief_file: "/b.md" }, leave_out: ["brief"] });
  // Typing a brief of his own keeps the file the user typed.
  const typed = emptyGroups(fields, { ...asShown(fields), brief: "mine", brief_file: "/b.md" }, WITHHELD, new Set(["brief"]));
  assert.equal(typed.fields, fields);
});

// --- the presets ---------------------------------------------------------------------------

const PRESETS = {
  state: "up", detail: null,
  presets: [
    { kind: "seat", name: "reviewer", harness: "codex",
      values: [{ name: "model", value: "o3", items: null }, { name: "add_dir", value: null, items: ["/a", "/b"] },
               { name: "brief", value: null, items: null }] },
    { kind: "project", name: "app", harness: null, values: [{ name: "cwd", value: "/w/app", items: null }] },
    { kind: "project", name: "web", harness: "claude", values: [] },
  ],
  problems: [{ where: "seat.reviewer", key: "model", problem: "is odd", hint: "fix it", code: "bad-value", level: "warning" },
             { where: "project.web", key: null, problem: "is broken", hint: "mend it", code: "bad-value", level: "error" },
             { where: "file", key: null, problem: "is not TOML", hint: "fix the file", code: "bad-file", level: "error" }],
};

test("the presets read by kind, with what each sets", () => {
  const presets = parsePresets(PRESETS);
  assert.deepEqual(presetsOf(presets, "seat").map((p) => p.name), ["reviewer"]);
  assert.deepEqual(presetsOf(presets, "project").map((p) => p.name), ["app", "web"]);
  assert.deepEqual(presetLines(presets.presets[0]), ["model o3", "add_dir /a, /b", "brief (set, not shown here)"]);
  assert.equal(parsePresets({ state: "up", detail: null }), null);
  assert.deepEqual(parsePresets({ state: "error", detail: "x" }), { state: "error", detail: "x", presets: [], problems: [] });
});

test("only the chosen presets' problems and the file's are shown", () => {
  const presets = parsePresets(PRESETS);
  assert.deepEqual(presetProblems(presets, "reviewer", "").map((p) => p.where), ["seat.reviewer", "file"]);
  assert.deepEqual(presetProblems(presets, "", "web").map((p) => p.where), ["project.web", "file"]);
});

// --- the preview ---------------------------------------------------------------------------

const PREVIEW = { refused: [], harness: "claude", mode: "interactive", target: "slot",
                  argv: ["claude", "--model", "opus", "it's here"], stamps: [{ name: "MOXI_SESSION", value: "s1" }],
                  first_turn: null, not_applied: [], warnings: ["careful"], command: null, request_id: "q1",
                  ticket: "q1.123.abc", expires_at: T };

test("a preview reads whole, and a refusal keeps only its four fields", () => {
  const preview = parsePreview({ ...PREVIEW, refused: [{ code: "no-cwd", row: "cwd", problem: "no place", hint: "set cwd", x: 1 }] });
  assert.deepEqual(preview.refused, [{ code: "no-cwd", row: "cwd", problem: "no place", hint: "set cwd" }]);
  assert.deepEqual(preview.stamps, PREVIEW.stamps);
  assert.equal(preview.ticket, "q1.123.abc");
  assert.deepEqual(preview.left_out, []);
  assert.deepEqual(parsePreview({ ...PREVIEW, left_out: ["model", "add_dir", 3] }).left_out, ["model", "add_dir"]);
  assert.equal(parsePreview({ refused: [] }), null);
  assert.equal(parsePreview(null), null);
});

test("the argv reads as one command line a shell would take back", () => {
  assert.equal(quote("claude"), "claude");
  assert.equal(quote("--model=opus"), "--model=opus");
  assert.equal(quote(""), "''");
  assert.equal(quote("it's"), `'it'"'"'s'`);
  assert.equal(commandLine(PREVIEW.argv), `claude --model opus 'it'"'"'s here'`);
});

test("a refusal reads as one line", () => {
  assert.equal(refusalLine({ code: "no-cwd", row: "cwd", problem: "no place", hint: "set cwd" }), "no place (cwd). set cwd");
  assert.equal(refusalLine({ code: "x", row: null, problem: "no", hint: "" }), "no");
});

// --- launching, resuming and following ------------------------------------------------------

test("a 202's status path is followed relative to the page, with its request id", () => {
  assert.deepEqual(statusOf({ status: "/api/launches?request=q%201" }), { path: "api/launches?request=q%201", request: "q 1" });
  assert.deepEqual(statusOf({ status: "api/launches?request=q1" }), { path: "api/launches?request=q1", request: "q1" });
  assert.equal(statusOf({ status: "/api/runs?run=r1" }), null);
  assert.equal(statusOf({ status: "/api/launches?request=" }), null);
  assert.equal(statusOf({ status: "//elsewhere/api/launches?request=q1&x=1" }), null);
  assert.equal(statusOf({}), null);
});

test("what a launch or resume answered", () => {
  assert.deepEqual(outcomeOf({ status: 202, data: { status: "/api/launches?request=q1" } }),
                   { kind: "started", status: { path: "api/launches?request=q1", request: "q1" } });
  assert.equal(outcomeOf({ status: 202, data: { status: "elsewhere" } }).kind, "failed");
  assert.deepEqual(outcomeOf({ status: 409, data: { conflict: "used already" } }), { kind: "used", text: "used already" });
  const terminal = outcomeOf({ status: 200, data: { refused: { code: "terminal", row: "target", problem: "p", hint: "h" },
                                                    command: "moxi launcher run --target=terminal" } });
  assert.equal(terminal.kind, "refused");
  assert.equal(terminal.refusal.code, "terminal");
  assert.equal(terminal.command, "moxi launcher run --target=terminal");
  assert.deepEqual(outcomeOf({ status: 200, data: { refused: null, command: "moxi launcher resume s1" } }),
                   { kind: "command", command: "moxi launcher resume s1" });
  assert.deepEqual(outcomeOf({ status: 0, data: null }), { kind: "failed", text: "no answer from the shell" });
  assert.deepEqual(outcomeOf({ status: 422, data: { refused: "not today" } }), { kind: "failed", text: "not today" });
  assert.deepEqual(outcomeOf({ status: 400, data: { error: "unknown key: values.x" } }),
                   { kind: "failed", text: "unknown key: values.x" });
  assert.deepEqual(outcomeOf({ status: 500, data: null }),
                   { kind: "failed", text: "the launcher's page server answered 500" });
  assert.equal(outcomeOf({ status: 200, data: { refused: null, command: null } }).kind, "failed");
});

const ROW = { request_id: "q1", created_at: T, updated_at: T, actor: "page", harness: "claude", target: "slot",
              state: "first-sent", reason: null, posture: "default", stale: false, preset_kind: "seat",
              preset_name: "reviewer", slot: "3", cwd: "/w", project_key: null, identity: null, scope: null,
              session_id: "s1", relaunch_of: null, resume_of: null };

test("a status path gives the row, a refusal from before the row, or nothing yet", () => {
  const up = (extra) => ({ status: 200, data: { state: "up", detail: null, launches: [], refused: null, ...extra } });
  assert.deepEqual(followedOf(up({ launches: [ROW] })), { kind: "row", row: ROW, why: null });
  assert.equal(followedOf(up({ launches: [ROW], why: "slot 3 is taken" })).why, "slot 3 is taken");
  assert.deepEqual(followedOf(up({ refused: { code: "no-cwd", row: null, problem: "p", hint: "h" } })),
                   { kind: "refused", refusal: { code: "no-cwd", row: null, problem: "p", hint: "h" } });
  assert.deepEqual(followedOf(up({})), { kind: "waiting" });
  assert.deepEqual(followedOf({ status: 200, data: { state: "error", detail: "it broke" } }), { kind: "failed", text: "it broke" });
  assert.equal(followedOf({ status: 503, data: null }).kind, "failed");
});

// --- the lists -------------------------------------------------------------------------------

const feed = (value, extra = {}) => ({ loading: false, gone: false, failure: null, value, ...extra });
const STATE = { session_id: "s1", request_id: "q1", relaunch: null, recycle: null, keep_warm: null, red: false,
                since: null, why: null, word: null, run_id: null, unreadable: null };

test("the launches list marks the live rows and this visit's launches", () => {
  const rows = [ROW, { ...ROW, request_id: "q2", session_id: "s2", stale: true, state: "spawning", target: "headless" }];
  const view = launchesView(feed({ state: "up", detail: null, launches: rows }),
                            feed({ state: "up", detail: null, sessions: [STATE], refused: null }), new Set(["q2"]));
  assert.equal(view.state, "up");
  assert.deepEqual(view.rows.map((r) => [r.request, r.live, r.mine, r.state]),
                   [["q1", true, false, "first-sent"], ["q2", false, true, "spawning (stale)"]]);
  assert.equal(view.rows[0].label, "claude · slot 3");
  assert.equal(view.rows[0].preset, "seat reviewer");
  assert.equal(view.rows[0].when, "2026-10-31 10:01 UTC");
});

test("the launches list's own states", () => {
  assert.equal(launchesView(feed(null, { loading: true }), null).state, "loading");
  assert.equal(launchesView(feed({ state: "up", detail: null, launches: [] }), null).state, "empty");
  assert.deepEqual(launchesView(feed({ state: "error", detail: "x", launches: [] }), null),
                   { state: "error", rows: [], detail: "x" });
  const failed = launchesView(feed({ state: "up", detail: null, launches: [ROW] }, { failure: { status: 0, why: "no answer" } }), null);
  assert.equal(failed.state, "failed");
  assert.equal(failed.rows.length, 1);
  assert.equal(launchesView(feed(null, { gone: true }), null).state, "gone");
  // States that can't be read leave every row not live.
  assert.equal(launchesView(feed({ state: "up", detail: null, launches: [ROW] }), feed(null, { loading: true })).rows[0].live, false);
});

const PAST = { request_id: "q1", created_at: T, harness: "claude", target: "slot", slot: "3", cwd: "/w",
               identity: "reviewer", state: "first-sent", session_id: "s1", live: false, resumable: true,
               why: null, hint: null, unread: null, held_by: null, resume_of: null, relaunch_of: null };

test("a past session resumes in its slot, gives a terminal command, or says why not", () => {
  assert.equal(pastItem(PAST).resume, "slot");
  assert.equal(pastItem(PAST).label, "claude · slot 3 · reviewer");
  assert.equal(pastItem({ ...PAST, target: "terminal" }).resume, "terminal");
  const live = pastItem({ ...PAST, live: true, resumable: false, why: "it is live", hint: "attach to it" });
  assert.deepEqual([live.resume, live.live, live.why], ["none", "live", "it is live. attach to it"]);
  assert.equal(pastItem({ ...PAST, session_id: null }).resume, "none");
  const unread = pastItem({ ...PAST, live: null, unread: "turns could not be read" });
  assert.deepEqual([unread.live, unread.why], ["unknown", "turns could not be read"]);
});

test("the past sessions add the older pages after the first, each launch once", () => {
  const first = feed({ state: "up", detail: null, sessions: [PAST] });
  const older = [{ ...PAST }, { ...PAST, request_id: "q0", created_at: "2026-10-30T09:00:00Z" }];
  const view = pastView(first, older);
  assert.deepEqual(view.rows.map((r) => r.request), ["q1", "q0"]);
  assert.equal(olderPath(older), "api/sessions?before=2026-10-30T09%3A00%3A00Z");
  assert.equal(olderPath([]), null);
  assert.equal(pastView(feed({ state: "up", detail: null, sessions: [] })).state, "empty");
  assert.equal(pastView(feed(null, { loading: true })).state, "loading");
  assert.deepEqual(parsePasts({ state: "up", detail: null, sessions: [PAST, { nope: 1 }] }).sessions, [PAST]);
  assert.equal(parsePasts({ state: "up", detail: null }), null);
});
