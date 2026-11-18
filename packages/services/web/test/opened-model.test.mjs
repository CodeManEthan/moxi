import assert from "node:assert/strict";
import test from "node:test";

import {
  footLinks, KNOWN, lengthWords, needsIt, NOT_KNOWN, openedLines, runWords, TASK_JUDGED, TASK_KNOWN, TASK_NOT_KNOWN,
} from "../src/opened-model.ts";
import { moduleRow, row, timer } from "./fixtures.mjs";

const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const WHEN = { now: Date.parse("2026-11-03T12:00:00Z") / 1000, clock: UTC };
const DESK = { phone: false, usage: true, modulePage: null };
const PHONE = { phone: true, usage: false, modulePage: null };
const verdict = (state, cause, more = {}) => ({ state, reason: "r", fix: null, unknown: false, cause, ...more });
const one = (more = {}) => ({ runs: [], log: null, unit: null, units: [], today: null, ...more });
const labels = (lines) => lines.map((l) => l.label);
const line = (lines, label) => lines.find((l) => l.label === label);

test("his running daemon's lines, in the drawing's order", () => {
  const r = row({ verdict: verdict("ok", "running"), since: "2026-11-03T08:19:00Z", cwd: "/home/me/docs",
                  health: ["/usr/bin/curl", "-f", "localhost"], checked: { state: "ok", line: "200", at: "2026-11-03T11:59:00Z" } });
  const lines = openedLines(r, one({ log: "journalctl --user -u moxi-web.service", unit: "moxi-web.service",
                                    units: ["moxi-web.service"] }), null, WHEN, DESK);
  assert.deepEqual(labels(lines), ["For", "State", "Health", "Log", "Command", "Unit", "Details"]);
  assert.deepEqual(line(lines, "State"), { label: "State", text: "Running since 08:19.", dim: "pid 4242", mono: null, copy: false,
                                           more: [] });
  assert.equal(line(lines, "Log").copy, true);
  assert.equal(line(lines, "Unit").copy, true);
  assert.equal(line(lines, "Health").text, "ok: 200");
  assert.equal(line(lines, "Health").dim, "checked 11:59 · your own check, run every 30 s while this page is open");
  assert.equal(line(lines, "Health").mono, "/usr/bin/curl -f localhost");
  assert.deepEqual(line(lines, "Command"), { label: "Command", text: null, mono: "/usr/bin/python3 -m http.server",
                                             dim: "keeps running · on at login", more: ["Folder /home/me/docs"] });
  assert.equal(line(lines, "Unit").dim, "a command you added; Moxi wrote the unit");
  assert.deepEqual(line(lines, "Details").more, ["r", "services/web", "moxi-web.service"]);
});

test("a down row's State is its sentence, and a fix that isn't a foot act shows as a command", () => {
  const down = row({ verdict: verdict("fail", "not-running", { fix: "moxi services start web" }), state: "failed" });
  const state = line(openedLines(down, null, null, WHEN, DESK), "State");
  assert.equal(state.text, "It failed and is not running.");
  assert.equal(state.dim, null);
  assert.equal(state.mono, null);
  const missing = moduleRow({ verdict: verdict("fail", "missing", { fix: "moxi install cache" }) });
  assert.equal(line(openedLines(missing, null, null, WHEN, DESK), "State").mono, "moxi install cache");
  assert.equal(line(openedLines(missing, null, null, WHEN, DESK), "State").copy, true);
});

test("a timer's State, Last runs and Usage", () => {
  const t = timer({ verdict: verdict("ok", "armed"), launches_session: true });
  const runs = [{ at: "2026-11-03T02:00:00Z", result: "ran", exit: 1, seconds: 12.4 },
                { at: "2026-11-03T03:00:00Z", result: "off", exit: null, seconds: null },
                { at: "2026-11-03T04:00:00Z", result: "not-started", exit: null, seconds: null },
                { at: "2026-11-03T05:00:00Z", result: "ran", exit: 0, seconds: 432 }];
  const lines = openedLines(t, one({ runs, today: { runs: 5, failed: 1, more: false } }), null, WHEN, DESK);
  assert.deepEqual(line(lines, "State").more, ["On schedule: every 5 minutes", "5 runs today, 1 failed"]);
  assert.deepEqual(line(lines, "Last runs").more,
                   ["05:00 ok · 7 min 12 s", "04:00 did not start", "03:00 held off by its switch", "02:00 exit 1 · 12.4 s"]);
  assert.deepEqual(line(lines, "Usage").link, { text: "Usage", module: "usage", route: "" });
  assert.equal(line(openedLines(t, null, null, WHEN, { ...DESK, usage: false }), "Usage").link, null);
  const more = openedLines(t, one({ today: { runs: 500, failed: 0, more: true } }), null, WHEN, DESK);
  assert.equal(line(more, "State").more[1], "500+ runs today, none failed");
  assert.equal(lengthWords(1.94), "1.9 s");
  assert.equal(lengthWords(7500), "2 h 5 min");
  assert.equal(runWords({ at: null, result: "unreadable", exit: null, seconds: null }, WHEN), "can't be read");
});

test("Moxi's row: its check, Switch, Unit, and the foot's links", () => {
  const m = moduleRow({ verdict: verdict("ok", "running"), has_settings: true, flag: true,
                        checked: { state: "ok", line: "watching 3 caches", at: "2026-11-03T11:58:00Z" } });
  const lines = openedLines(m, one({ unit: "moxi-cache-watch.service" }), null, WHEN, DESK);
  assert.deepEqual(labels(lines), ["For", "State", "Switch", "Unit", "Details"]);
  assert.deepEqual(line(lines, "State").more, ["healthy: watching 3 caches, checked 11:58"]);
  assert.deepEqual(line(lines, "Switch"), { label: "Switch", text: "on", dim: "cache.watch.enabled",
                                            link: { text: "Settings › cache", module: "ui", route: "settings/cache" } });
  assert.equal(line(lines, "Unit").dim, "comes with cache");
  const bare = moduleRow({ switch: null, has_switch: false, verdict: verdict("ok", "running") });
  assert.equal(line(openedLines(bare, one({ unit: "u" }), null, WHEN, DESK), "Unit").dim,
               "comes with cache · on while cache is installed");
  assert.deepEqual(footLinks(m, DESK), [{ text: "Modules", module: "ui", route: "modules" },
                                        { text: "Settings › cache", module: "ui", route: "settings/cache" }]);
  assert.deepEqual(footLinks(m, { ...DESK, modulePage: "cache" })[0], { text: "cache", module: "cache", route: "" });
  assert.deepEqual(footLinks(row(), DESK), []);
});

test("Needs it on slots' server: sessions, nothing, unread absent, the phone's count", () => {
  const ends = { read: true, why: null, sessions: [{ slot: 1, label: "fix the tests" }, { slot: 3, label: null }], open: 1, jobs: 2 };
  assert.equal(needsIt(ends, false),
               "2 sessions are running in its slots: fix the tests in slot 1, slot 3. Stopping or restarting it ends them. · 1 open slot · 2 jobs");
  assert.equal(needsIt(ends, true), "2 sessions are running in its slots. Stopping or restarting it ends them. · 1 open slot · 2 jobs");
  assert.equal(needsIt({ ...ends, sessions: [], open: 0, jobs: 0 }, false), "Nothing is running in its slots.");
  assert.equal(needsIt({ read: false, why: "x", sessions: null, open: null, jobs: null }, false), null);
  assert.equal(needsIt(null, false), null);
  const server = moduleRow({ id: "slots/server", module: "slots", name: "server", ends_slots: true,
                             verdict: verdict("ok", "running") });
  assert.ok(labels(openedLines(server, null, ends, WHEN, DESK)).includes("Needs it"));
  assert.ok(!labels(openedLines(server, null, null, WHEN, DESK)).includes("Needs it"));
});

test("the phone gets only the lines whose facts reach it", () => {
  const r = row({ doc: null, argv: null, health: null, detail: null, verdict: verdict("ok", "running", { reason: null }),
                  launches_session: true });
  const lines = openedLines(r, one({ log: null, unit: "moxi-web.service" }), null, WHEN, PHONE);
  assert.deepEqual(labels(lines), ["State", "Usage", "Unit"]);
});

test("an adopted Windows timer says what Task Scheduler keeps and what it doesn't, before its Unit", () => {
  const task = timer({ form: "adopted", platform: "win32", argv: null, doc: null, unit: "\\Backup\\PhotoSync",
                       verdict: verdict("ok", "armed") });
  const lines = openedLines(task, one({ unit: "\\Backup\\PhotoSync" }), null, WHEN, DESK);
  assert.deepEqual(labels(lines), ["State", KNOWN, NOT_KNOWN, "Unit", "Details"]);
  assert.equal(line(lines, KNOWN).text, "whether the task is enabled, when it last ran, and that run's result code");
  assert.equal(TASK_KNOWN, line(lines, KNOWN).text);
  assert.equal(line(lines, NOT_KNOWN).text, TASK_NOT_KNOWN);
  assert.ok(TASK_NOT_KNOWN.endsWith("Task Scheduler keeps neither."));
  assert.deepEqual(line(lines, NOT_KNOWN).more, [TASK_JUDGED]);
  assert.equal(line(lines, "Unit").dim, "adopted; Moxi never writes its file");
  // Not on another system, not for a daemon, and not for his written timer there.
  for (const other of [{ platform: "linux" }, { kind: "daemon" }, { form: "written" }]) {
    assert.ok(!labels(openedLines({ ...task, ...other }, null, null, WHEN, DESK)).includes(KNOWN), JSON.stringify(other));
  }
});
