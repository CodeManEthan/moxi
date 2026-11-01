import assert from "node:assert/strict";
import test from "node:test";

import {
  commandLine, fieldsFor, followedOf, HARNESS, launchBody, launchesView, markTouched, olderPath,
  outcomeOf, parseCatalog, parsePasts, parsePresets, parsePreview, pastItem, pastView, presetHarness,
  presetLines, presetProblems, presetsOf, quote, rebase, refusalLine, requestValues, sentValue,
  sharedNames, statusOf,
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

/** What `<moxi-ui-form mode="submit">` gives for claude's fields left as they are. */
const UNTOUCHED = { harness: "claude", cwd: null, target: "terminal", scratch: "false", add_dir: [],
                    model: null, effort: null, remote_control: null };

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

test("the shared names are the rows every harness has", () => {
  assert.deepEqual([...sharedNames(catalog)].sort(), ["add_dir", "cwd", "harness", "model", "scratch", "target"]);
  assert.equal(sharedNames(null).size, 0);
});

// --- only what the user changed is sent ----------------------------------------------------

test("an untouched form sends no value, so the preset and the defaults stand", () => {
  const touched = markTouched(new Set(), UNTOUCHED, { ...UNTOUCHED });
  assert.equal(touched.size, 0);
  const values = requestValues(UNTOUCHED, touched, fieldsFor(catalog, "claude"), "claude", sharedNames(catalog));
  assert.deepEqual(values, {});
});

test("a changed field is sent, and stays sent when set back to its default", () => {
  let touched = markTouched(new Set(), UNTOUCHED, { ...UNTOUCHED, target: "slot" });
  assert.deepEqual([...touched], ["target"]);
  touched = markTouched(touched, UNTOUCHED, { ...UNTOUCHED, target: "terminal" });
  assert.deepEqual([...touched], ["target"]);
  const values = requestValues({ ...UNTOUCHED, target: "terminal" }, touched, fieldsFor(catalog, "claude"),
                               "claude", sharedNames(catalog));
  assert.deepEqual(values, { target: "terminal" });
});

test("a list's items count as a change", () => {
  const touched = markTouched(new Set(), UNTOUCHED, { ...UNTOUCHED, add_dir: ["/a"] });
  assert.deepEqual([...touched], ["add_dir"]);
  const emptied = markTouched(new Set(), { ...UNTOUCHED, add_dir: ["/a"] }, UNTOUCHED);
  assert.deepEqual(requestValues(UNTOUCHED, emptied, fieldsFor(catalog, "claude"), "claude", sharedNames(catalog)),
                   { add_dir: [] });
});

test("each value goes as the page server reads it", () => {
  const optional = field("remote_control", { optional_value: true });
  assert.equal(sentValue(optional, null), "false");
  assert.equal(sentValue(optional, ""), "true");
  assert.equal(sentValue(optional, "box"), "box");
  assert.equal(sentValue(field("model"), null), null);
  assert.equal(sentValue(field("model"), ""), null);
  assert.equal(sentValue(field("model"), "opus"), "opus");
  assert.deepEqual(sentValue(field("add_dir", { type: "list" }), ["/a", "/b"]), ["/a", "/b"]);
  assert.equal(sentValue(undefined, "x"), "x");
});

test("a cleared text field is left out, not sent as empty", () => {
  const touched = new Set(["cwd"]);
  assert.deepEqual(requestValues({ ...UNTOUCHED, cwd: null }, touched, fieldsFor(catalog, "claude"), "claude",
                                 sharedNames(catalog)), {});
});

test("the harness goes with a field only some harnesses have, and not with a shared one", () => {
  const fields = fieldsFor(catalog, "claude");
  const shared = sharedNames(catalog);
  assert.deepEqual(requestValues({ ...UNTOUCHED, effort: "high" }, new Set(["effort"]), fields, "claude", shared),
                   { effort: "high", harness: "claude" });
  assert.deepEqual(requestValues({ ...UNTOUCHED, model: "opus" }, new Set(["model"]), fields, "claude", shared),
                   { model: "opus" });
  // A harness the user chose goes as chosen.
  assert.deepEqual(requestValues({ ...UNTOUCHED, harness: "codex", sandbox: "x" }, new Set(["harness", "sandbox"]),
                                 fieldsFor(catalog, "codex"), "codex", shared),
                   { harness: "codex", sandbox: "x" });
});

test("new fields keep a touched field's first value and read the rest afresh", () => {
  const touched = new Set(["harness"]);
  const before = { ...UNTOUCHED };
  const after = rebase(before, { harness: "codex", cwd: null, sandbox: null }, touched);
  assert.deepEqual(after, { harness: "claude", cwd: null, sandbox: null });
  // So the harness the presets picked, untouched, doesn't read as changed.
  const picked = rebase(before, { harness: "codex", sandbox: null }, new Set());
  assert.equal(markTouched(new Set(), picked, { harness: "codex", sandbox: null }).size, 0);
});

test("a launch body leaves out what wasn't chosen", () => {
  assert.deepEqual(launchBody("", "", {}, false), { values: {} });
  assert.deepEqual(launchBody("reviewer", "app", { model: "opus" }, true),
                   { seat: "reviewer", project_preset: "app", values: { model: "opus" }, off_posture: true });
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

test("the seat's harness comes first, then the project preset's", () => {
  const presets = parsePresets(PRESETS);
  assert.equal(presetHarness(presets, "reviewer", "web"), "codex");
  assert.equal(presetHarness(presets, "", "web"), "claude");
  assert.equal(presetHarness(presets, "", "app"), null);
  assert.equal(presetHarness(null, "reviewer", ""), null);
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
  assert.deepEqual(followedOf(up({ launches: [ROW] })), { kind: "row", row: ROW });
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
