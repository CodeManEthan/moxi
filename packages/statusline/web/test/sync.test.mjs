import assert from "node:assert/strict";
import test from "node:test";

import { MOVED, Sync, UNREADABLE_LINE, UNREADABLE_NOTE } from "../src/sync.ts";

const FIELDS = ["dir", "branch", "model", "effort", "context", "session", "transcript", "lines", "cost"];

function wire(base, { show = ["dir", "model"], color = true, problem = null } = {}) {
  return {
    base, problem, fields: FIELDS, modules: [],
    values: { show, separator: " | ", color, dir: { parts: 2 }, context: { warn_at: 50, alert_at: 80 } },
    claude: { set: false, command: null, moxis: false, here: false, saved: false, problem: null },
  };
}

const LINE = { lines: ["~/x │ Model"], ms: 30.5, notes: [], problem: null };

/** A fake page server: each path answers from its queue, and every call is kept. */
function server(answers) {
  const calls = [];
  const api = async (path, body) => {
    calls.push({ path, body });
    const queue = answers[path];
    assert.ok(queue && queue.length, `no answer left for ${path}`);
    return queue.shift();
  };
  return { api, calls };
}

test("a 409 on preview reads the new state, keeps the changes over it, and previews again", async () => {
  const { api, calls } = server({
    "api/state": [{ status: 200, data: wire("b1") }, { status: 200, data: wire("b2", { color: false }) }],
    "api/preview": [{ status: 409, data: { conflict: "moved" } }, { status: 200, data: LINE }],
  });
  const sync = new Sync(api, () => {});
  await sync.read();
  sync.draft.set("show", ["model", "dir"]);
  await sync.previewNow();
  assert.equal(sync.draft.base, "b2");
  assert.deepEqual(sync.draft.changes, [{ key: "show", value: ["model", "dir"] }]);
  assert.equal(sync.draft.values().color, false);
  assert.deepEqual(calls.filter((c) => c.path === "api/preview").map((c) => c.body),
                   [{ base: "b1", changes: { show: ["model", "dir"] } },
                    { base: "b2", changes: { show: ["model", "dir"] } }]);
  assert.deepEqual(sync.preview, LINE);
  assert.equal(sync.message.text, MOVED);
});

test("a 409 on Save keeps the draft over the new base and writes nothing more", async () => {
  const { api, calls } = server({
    "api/state": [{ status: 200, data: wire("b1") }, { status: 200, data: wire("b2") }],
    "api/save": [{ status: 409, data: { conflict: "moved" } }],
    "api/preview": [{ status: 200, data: LINE }],
  });
  const sync = new Sync(api, () => {});
  await sync.read();
  sync.draft.set("separator", " / ");
  await sync.save();
  assert.equal(sync.draft.base, "b2");
  assert.equal(sync.draft.dirty, true);
  assert.equal(sync.message.kind, "warn");
  assert.equal(calls.filter((c) => c.path === "api/save").length, 1);
});

test("a Save that is taken makes the saved config the draft's base", async () => {
  const { api } = server({
    "api/state": [{ status: 200, data: wire("b1") }],
    "api/save": [{ status: 200, data: wire("b2", { show: ["model", "dir"] }) }],
    "api/preview": [{ status: 200, data: LINE }],
  });
  const sync = new Sync(api, () => {});
  await sync.read();
  sync.draft.set("show", ["model", "dir"]);
  await sync.save();
  assert.equal(sync.draft.dirty, false);
  assert.equal(sync.draft.base, "b2");
  assert.equal(sync.message.kind, "ok");
});

test("a refused draft says why and shows no line", async () => {
  const { api } = server({
    "api/state": [{ status: 200, data: wire("b1") }],
    "api/preview": [{ status: 422, data: { refused: "show holds 2 'row' items" } }],
  });
  const sync = new Sync(api, () => {});
  await sync.read();
  sync.draft.set("show", ["dir", "row", "model", "row", "cost"]);
  await sync.previewNow();
  assert.equal(sync.refused, "show holds 2 'row' items");
  assert.equal(sync.preview, null);
});

test("an answer to an older draft is dropped", async () => {
  let release;
  const slow = new Promise((resolve) => { release = resolve; });
  const answers = [slow, Promise.resolve({ status: 200, data: { ...LINE, lines: ["new"] } })];
  const api = async (path) => (path === "api/state" ? { status: 200, data: wire("b1") } : answers.shift());
  const sync = new Sync(api, () => {});
  await sync.read();
  const first = sync.previewNow();
  await sync.previewNow();
  release({ status: 200, data: { ...LINE, lines: ["old"] } });
  await first;
  assert.deepEqual(sync.preview.lines, ["new"]);
});

test("use and restore are not sent while the draft differs", async () => {
  const { api, calls } = server({ "api/state": [{ status: 200, data: wire("b1") }] });
  const sync = new Sync(api, () => {});
  await sync.read();
  sync.draft.set("color", false);
  await sync.act("use");
  await sync.act("restore");
  assert.deepEqual(calls.map((c) => c.path), ["api/state"]);
});

test("coming back to the window rebases only when the file moved", async () => {
  const { api, calls } = server({
    "api/state": [{ status: 200, data: wire("b1") }, { status: 200, data: wire("b1") },
                  { status: 200, data: wire("b3") }],
    "api/preview": [{ status: 200, data: LINE }],
  });
  const sync = new Sync(api, () => {});
  await sync.read();
  sync.draft.set("color", false);
  await sync.refocus();
  assert.equal(calls.filter((c) => c.path === "api/preview").length, 0);
  await sync.refocus();
  assert.equal(sync.draft.base, "b3");
  assert.deepEqual(sync.draft.changes, [{ key: "color", value: false }]);
  assert.equal(calls.filter((c) => c.path === "api/preview").length, 1);
});

const BROKEN = "The saved config can't be read: show has 'row' first.";

test("a config that stops reading under the page shows the line Claude prints for it", async () => {
  const { api, calls } = server({
    "api/state": [{ status: 200, data: wire("b1") }, { status: 200, data: wire("b2", { problem: BROKEN }) }],
    "api/preview": [{ status: 200, data: LINE }],
  });
  const sync = new Sync(api, () => {});
  await sync.read();
  await sync.previewNow();
  assert.deepEqual(sync.preview, LINE);
  await sync.refocus();
  assert.deepEqual(sync.preview, { lines: [UNREADABLE_LINE], ms: null, notes: [UNREADABLE_NOTE], problem: null });
  assert.equal(UNREADABLE_LINE, "config?");
  assert.equal(calls.filter((c) => c.path === "api/preview").length, 1);
});

test("a page opened over a config that can't be read shows that line, not a pending one", async () => {
  const { api, calls } = server({ "api/state": [{ status: 200, data: wire("b1", { problem: BROKEN }) }] });
  const sync = new Sync(api, () => {});
  await sync.read();
  await sync.previewNow();
  assert.deepEqual(sync.preview.lines, [UNREADABLE_LINE]);
  assert.deepEqual(calls.map((c) => c.path), ["api/state"]);
});

test("a render in flight when the config stops reading is dropped", async () => {
  let release;
  const slow = new Promise((resolve) => { release = resolve; });
  const states = [wire("b1"), wire("b2", { problem: BROKEN })];
  const api = async (path) => (path === "api/state" ? { status: 200, data: states.shift() } : slow);
  const sync = new Sync(api, () => {});
  await sync.read();
  const inFlight = sync.previewNow();
  await sync.refocus();
  release({ status: 200, data: LINE });
  await inFlight;
  assert.deepEqual(sync.preview.lines, [UNREADABLE_LINE]);
});

test("once the config reads again the draft is previewed again", async () => {
  const { api } = server({
    "api/state": [{ status: 200, data: wire("b1", { problem: BROKEN }) }, { status: 200, data: wire("b2") }],
    "api/preview": [{ status: 200, data: LINE }],
  });
  const sync = new Sync(api, () => {});
  await sync.read();
  await sync.refocus();
  assert.deepEqual(sync.preview, LINE);
});

test("an act that answers a config that can't be read shows that line", async () => {
  const { api } = server({
    "api/state": [{ status: 200, data: wire("b1") }],
    "api/preview": [{ status: 200, data: LINE }],
    "api/use": [{ status: 200, data: { ...wire("b2", { problem: BROKEN }), said: ["Switched."] } }],
  });
  const sync = new Sync(api, () => {});
  await sync.read();
  await sync.previewNow();
  await sync.act("use");
  assert.deepEqual(sync.preview.lines, [UNREADABLE_LINE]);
  assert.equal(sync.message.kind, "ok");
});
