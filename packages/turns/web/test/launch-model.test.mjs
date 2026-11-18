import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import test from "node:test";

import { launchesOf, parseRunning, whereText } from "../src/launch-model.ts";
import { rowLines } from "../src/rows-model.ts";

const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  seconds: (at) => new Date(at * 1000).toISOString().slice(11, 19),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const TODAY = "2026-11-18";
const at = (hhmm) => Date.parse(`${TODAY}T${hhmm}:00Z`) / 1000;
const session = (key, id, extra) => ({ key, harness: "claude", pid: 4321, session_id: id,
  cwd: `/home/u/${key}`, started: at("08:20"), capabilities: ["agents"], endpoint_version: "0.2.4",
  mod_version: null, trouble: null, agents: { entries: [], complete: true }, read_error: null, ended_at: null,
  has_box: true, unread_since: null,
  facts: { session_id: id, turn_number: 1, turn_open: false, turn_started_at: null, agents: 0, draft: false,
           stale: false, ready: null, at: at("19:00") }, ...extra });
const entry = (id, name, where) => ({ request_id: `r-${name}`, harness: "claude", session_id: id, name,
  identity: null, started_at: "2026-11-18T08:20:00Z", cache: null, waits: null, covered: false, coverable: null,
  activity: null, where: { target: "slot", slot: "3", project_key: "atlas-api", folder: "atlas-api", ...where } });
const running = (sessions, state = "up") => ({ state, detail: null, count: sessions.length, sessions });
const joined = (value, sessions) => Object.fromEntries(launchesOf(parseRunning(value), sessions));

test("a launch names the row its session id pairs it with, one to one", () => {
  const rows = [session("a", "id-a"), session("b", "id-b"), session("c", null)];
  assert.deepEqual(joined(running([entry("id-a", "planner"), entry("id-x", "elsewhere")]), rows),
                   { a: { name: "planner", where: "slot 3 · atlas-api" } });
  // The name goes on the row's line one, the where dim beside it.
  const lines = rowLines(rows[0], "idle", TODAY, { name: "planner", where: "slot 3 · atlas-api" }, UTC);
  assert.equal(lines.name, "planner");
  assert.equal(lines.where, "slot 3 · atlas-api");
  // Without a launch: the folder and the start, no where.
  const plain = rowLines(rows[1], "idle", TODAY, null, UTC);
  assert.equal(plain.name, "b · started today 08:20");
  assert.equal(plain.where, null);
});

test("an entry with no id joins no row, and neither does a row with none", () => {
  assert.deepEqual(joined(running([entry(null, "planner")]), [session("a", null), session("b", "id-b")]), {});
});

test("two entries with one id, or two rows with one id, name no row", () => {
  const twoEntries = running([entry("id-a", "planner"), entry("id-a", "resumed")]);
  assert.deepEqual(joined(twoEntries, [session("a", "id-a"), session("b", "id-b")]), {});
  const twoRows = [session("a", "id-a"), session("a2", "id-a"), session("b", "id-b")];
  assert.deepEqual(joined(running([entry("id-a", "planner"), entry("id-b", "builder")]), twoRows),
                   { b: { name: "builder", where: "slot 3 · atlas-api" } });
});

test("a fallback name is shown as launcher gives it", () => {
  assert.deepEqual(joined(running([entry("id-a", "atlas-api 08:20")]), [session("a", "id-a")]),
                   { a: { name: "atlas-api 08:20", where: "slot 3 · atlas-api" } });
});

test("a launcher read that fails, answers error or can't be read names no row", () => {
  const rows = [session("a", "id-a")];
  assert.deepEqual(Object.fromEntries(launchesOf(null, rows)), {});
  assert.deepEqual(joined(running([entry("id-a", "planner")], "error"), rows), {});
  for (const bad of [null, "x", { state: "up" }, { state: "busy", sessions: [] }]) {
    assert.equal(parseRunning(bad), null);
  }
  // An entry that is not one is left out.
  assert.deepEqual(parseRunning({ state: "up", sessions: [{ name: "no request id" }, 3] }), { state: "up", sessions: [] });
});

const WHERES = [
  { target: "slot", slot: "3", project_key: "atlas-api", folder: "atlas-api" },
  { target: "slot", slot: "3", project_key: null, folder: "atlas-api" },
  { target: "slot", slot: null, project_key: null, folder: "atlas-api" },
  { target: "terminal", slot: null, project_key: "harbor-infra", folder: "harbor" },
  { target: "terminal", slot: null, project_key: null, folder: "harbor" },
  { target: "headless", slot: null, project_key: "kestrel-web", folder: "kestrel" },
  { target: "headless", slot: null, project_key: null, folder: "kestrel" },
];

test("where: a slot, a terminal and a headless launch, with and without a project", () => {
  assert.deepEqual(WHERES.map(whereText), [
    "slot 3 · atlas-api", "slot 3 · atlas-api", "slot · atlas-api", "terminal · harbor-infra", "terminal · harbor",
    "headless · kestrel-web", "headless · kestrel",
  ]);
});

test("where is worded as launcher's own row words it", async () => {
  // Held equal to launcher's model, where its source sits beside turns' in the repository.
  const theirs = new URL("../../../launcher/web/src/running-model.ts", import.meta.url);
  if (!existsSync(theirs)) return;
  const launcher = await import(theirs.href);
  assert.deepEqual(WHERES.map(whereText), WHERES.map(launcher.whereText));
  // The same entries read through both parsers give the same words.
  const value = running(WHERES.map((where, i) => entry(`id-${i}`, `n${i}`, where)));
  assert.deepEqual(parseRunning(value).sessions.map((one) => whereText(one.where)),
                   launcher.parseRunning(value).sessions.map((one) => launcher.whereText(one.where)));
});
