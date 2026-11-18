import assert from "node:assert/strict";
import test from "node:test";

import {
  BROKEN, capped, clip, epochOf, factsReport, GATHERS, LABEL, lineOf, ORDER, PARTS_MAX, parseGiven, partOf,
  quietOf, TEXT_MAX_CHARS, VERSION, whenWord,
} from "../src/facts.ts";
import { moduleRow, row, rows, timer } from "./fixtures.mjs";

/** The test's clock: UTC, so the words read the same in any zone. */
const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const NOW = Date.parse("2026-11-16T20:00:00Z") / 1000;
const WHEN = { now: NOW, clock: UTC };
const GIVEN = [{ key: "atlas-api", root: "/home/me/code/atlas-api", lead: null, associated: [] },
               { key: "kestrel-web", root: "/home/me/code/kestrel-web", lead: "kim", associated: [] }];

const verdict = (state, fields = {}) => ({ state, reason: "x", fix: null, unknown: false, cause: null, ...fields });
/** A user daemon filed under atlas-api, with `fields` laid over it. */
const daemon = (name, fields = {}) => row({ id: `services/${name}`, name, project: "atlas-api", ...fields });
const job = (name, fields = {}) => timer({ id: `services/${name}`, name, project: "atlas-api", ...fields });
const report = (list, open = null) => factsReport(rows(list), GIVEN, open, WHEN);
const atlas = (list, open = null) => report(list, open).projects["atlas-api"];

// -- the report's frame ---------------------------------------------------------------------

test("a good read reports version 1, order 60, `services` gathered, and only given projects with a row", () => {
  const found = report([daemon("web"), daemon("api", { project: "elsewhere" }), daemon("loose", { project: null }),
                        moduleRow({ project: "atlas-api", verdict: verdict("fail", { cause: "not-running" }) })]);
  assert.deepEqual(found, { version: VERSION, order: ORDER, gathers: GATHERS, projects: { "atlas-api": {} } });
  assert.deepEqual([VERSION, ORDER, GATHERS], [1, 60, "services"]);
  assert.deepEqual(report([]), { version: 1, order: 60, gathers: "services", projects: {} });
});

test("a failed read, and a store services can't read, report null", () => {
  assert.equal(factsReport(null, GIVEN, null, WHEN), null);
  assert.equal(factsReport({ state: "error", detail: "too new", rows: [daemon("web")], problems: [] }, GIVEN, null,
                           WHEN), null);
});

test("sections go to the open project only", () => {
  const list = [daemon("web"), daemon("site", { project: "kestrel-web" })];
  const closed = report(list);
  assert.equal(closed.projects["atlas-api"].sections, undefined);
  const open = report(list, "kestrel-web");
  assert.equal(open.projects["atlas-api"].sections, undefined);
  assert.equal(open.projects["kestrel-web"].sections[0].label, LABEL);
  assert.deepEqual(open.projects["kestrel-web"].sections[0].link, { text: "Services", module: "services", route: "" });
});

// -- the judgment table, row by row ---------------------------------------------------------

test("a socket-activated idle daemon is quiet: no part, its name and reason on the last line", () => {
  const idle = daemon("sync", { state: "inactive", pid: null, since: null,
                                verdict: verdict("ok", { reason: "idle, socket listening" }) });
  const facts = atlas([idle], "atlas-api");
  assert.equal(facts.parts, undefined);
  assert.deepEqual(facts.sections, [{ label: "Services", lines: [{ parts: [{ text: "sync idle, socket listening" }] }],
                                      link: { text: "Services", module: "services", route: "" } }]);
});

test("no supervisor, nobackend and a unit not for this system are off: `<name> off` on the last line only", () => {
  for (const [state, reason] of [[null, "no supervisor"], ["nobackend", "no supervisor"],
                                  ["off", "not for this system"]]) {
    const facts = atlas([daemon("web", { state, verdict: verdict("off", { reason }) })], "atlas-api");
    assert.equal(facts.parts, undefined);
    assert.deepEqual(facts.sections[0].lines, [{ parts: [{ text: "web off" }] }]);
    assert.equal(facts.sections[0].rank, undefined);
  }
});

test("an unknown warn says nothing: services can't say", () => {
  for (const cause of [null, "stopped", "last-run"]) {
    const facts = atlas([daemon("web", { verdict: verdict("warn", { unknown: true, cause, reason: "unknown: x" }) })],
                        "atlas-api");
    assert.deepEqual(facts, {});
  }
});

test("a daemon not running though its stop is from before the last boot fails heavy with its time", () => {
  const down = daemon("docs", { state: "inactive", pid: null, since: "2026-11-16T18:00:00Z",
                                stopped: "2026-11-01T09:00:00Z",
                                verdict: verdict("fail", { cause: "not-running", reason: "not running" }) });
  const facts = atlas([down], "atlas-api");
  assert.deepEqual(facts.parts, [{ text: "docs failed at 18:00", heavy: true, rank: BROKEN }]);
  assert.deepEqual(facts.sections[0].lines, [{ parts: [{ text: "docs failed at 18:00", heavy: true, rank: 30 },
                                                       { text: "not running" }] }]);
  assert.equal(facts.sections[0].rank, 30);
});

test("a down daemon's time is left out unless its state is failed or inactive and `since` parses", () => {
  const down = (fields) => partOf(daemon("docs", { verdict: verdict("fail", { cause: "not-running" }), ...fields }),
                                  WHEN).text;
  assert.equal(down({ state: "failed", since: "2026-11-15T07:05:00Z" }), "docs failed at yesterday 07:05");
  assert.equal(down({ state: "inactive", since: null }), "docs failed");
  assert.equal(down({ state: "inactive", since: "2026-11-16 18:00" }), "docs failed");
  assert.equal(down({ state: "activating", since: "2026-11-16T18:00:00Z" }), "docs failed");
  assert.equal(down({ state: null, since: "2026-11-16T18:00:00Z" }), "docs failed");
});

test("a stale stop on a running daemon warned `won't start at login` is an opened warn line, not `is stopped`", () => {
  const stale = daemon("docs", { state: "active", stopped: "2026-11-01T09:00:00Z",
                                 verdict: verdict("warn", { reason: "won't start at login" }) });
  const facts = atlas([stale], "atlas-api");
  assert.equal(facts.parts, undefined);
  assert.deepEqual(facts.sections[0].lines, [{ parts: [{ text: "docs", warn: true }, { text: "won't start at login" }] }]);
  assert.equal(facts.sections[0].rank, undefined);
});

test("a stop that holds is `<name> is stopped`, a warning", () => {
  const stopped = daemon("docs-preview", { state: "inactive", stopped: "2026-11-16T17:00:00Z",
                                           verdict: verdict("warn", { cause: "stopped",
                                                                      reason: "stopped by hand at 2026-11-16T17:00:00Z" }) });
  const facts = atlas([stopped], "atlas-api");
  assert.deepEqual(facts.parts, [{ text: "docs-preview is stopped", warn: true }]);
  assert.deepEqual(facts.sections[0].lines, [{ parts: [{ text: "docs-preview is stopped", warn: true }] }]);
});

test("a missing unit fails as `<name> failed` with no time while on, and is an opened warn line while off", () => {
  const on = daemon("web", { state: "missing", since: "2026-11-16T18:00:00Z",
                             verdict: verdict("fail", { reason: "its unit is missing" }) });
  const facts = atlas([on], "atlas-api");
  assert.deepEqual(facts.parts, [{ text: "web failed", heavy: true, rank: 30 }]);
  assert.deepEqual(facts.sections[0].lines[0].parts[1], { text: "its unit is missing" });
  const off = daemon("web", { state: "missing", flag: false, verdict: verdict("warn", { reason: "its unit is missing" }) });
  const quiet = atlas([off], "atlas-api");
  assert.equal(quiet.parts, undefined);
  assert.deepEqual(quiet.sections[0].lines, [{ parts: [{ text: "web", warn: true }, { text: "its unit is missing" }] }]);
});

test("a timer's failed last run is `<name>'s last run failed at <time>`, the time left out when it doesn't parse", () => {
  const failed = (at) => job("dep-audit", { last_run: { failed: true, text: "exit 1", at },
                                            verdict: verdict("warn", { cause: "last-run", reason: "last run failed" }) });
  assert.deepEqual(atlas([failed("2026-11-16T18:00:00Z")]).parts,
                   [{ text: "dep-audit's last run failed at 18:00", warn: true }]);
  assert.equal(partOf(failed("2026-10-30T09:00:00"), WHEN).text, "dep-audit's last run failed");
  assert.equal(partOf(failed(null), WHEN).text, "dep-audit's last run failed");
  assert.equal(partOf(failed("2026-11-14T18:00:00+01:00"), WHEN).text, "dep-audit's last run failed at Sat 17:00");
});

test("an unarmed schedule and a failed health check are `<name> failed`, with services' reason opened", () => {
  const unarmed = job("nightly", { verdict: verdict("fail", { reason: "schedule not armed" }) });
  const sick = daemon("web", { state: "active", since: "2026-11-16T18:00:00Z",
                               verdict: verdict("fail", { reason: "health check failed: refused" }) });
  const facts = atlas([unarmed, sick], "atlas-api");
  assert.deepEqual(facts.parts, [{ text: "nightly failed", heavy: true, rank: 30 },
                                 { text: "web failed", heavy: true, rank: 30 }]);
  assert.deepEqual(facts.sections[0].lines.map((line) => line.parts.map((part) => part.text)),
                   [["nightly failed", "schedule not armed"], ["web failed", "health check failed: refused"]]);
});

test("a cause the table doesn't name takes the null row's words, and an unnamed fail the other fail's", () => {
  const login = daemon("web", { state: "active", verdict: verdict("warn", { cause: "no-login", reason: "won't start at login" }) });
  const facts = atlas([login], "atlas-api");
  assert.equal(facts.parts, undefined);
  assert.deepEqual(facts.sections[0].lines, [{ parts: [{ text: "web", warn: true }, { text: "won't start at login" }] }]);
  const unarmed = job("nightly", { state: "failed", since: "2026-11-16T18:00:00Z",
                                   verdict: verdict("fail", { cause: "not-armed", reason: "schedule not armed" }) });
  assert.deepEqual(partOf(unarmed, WHEN), { text: "nightly failed", heavy: true, rank: 30 });
});

test("ok rows on the quiet last line: a running daemon since, a timer's schedule and last run", () => {
  const web = daemon("web", { since: "2026-11-16T09:15:00Z", verdict: verdict("ok", { reason: "running" }) });
  assert.deepEqual(quietOf(web, WHEN), { text: "web running since 09:15" });
  assert.deepEqual(quietOf(daemon("web", { since: null, verdict: verdict("ok", { reason: "running" }) }), WHEN),
                   { text: "web running" });
  const ran = { failed: false, text: "ok", at: "2026-11-16T10:00:00Z" };
  assert.deepEqual(quietOf(job("backup", { last_run: ran, verdict: verdict("ok", { reason: "armed" }) }), WHEN),
                   { text: "backup every 5m, last ran 10:00" });
  assert.deepEqual(quietOf(job("backup", { last_run: null, verdict: verdict("ok", { reason: "armed, no run yet" }) }),
                           WHEN), { text: "backup every 5m" });
  assert.deepEqual(quietOf(job("backup", { schedule: null, last_run: ran, verdict: verdict("ok") }), WHEN),
                   { text: "backup last ran 10:00" });
  assert.deepEqual(quietOf(job("backup", { schedule: null, last_run: null,
                                           verdict: verdict("ok", { reason: "armed, no run yet" }) }), WHEN),
                   { text: "backup armed, no run yet" });
  assert.equal(quietOf(daemon("web", { verdict: verdict("fail") }), WHEN), null);
  assert.equal(lineOf(web, WHEN), null);
});

test("a reason the phone's gateway left out is no part", () => {
  const down = daemon("web", { verdict: verdict("fail", { reason: null }) });
  assert.deepEqual(lineOf(down, WHEN), { parts: [{ text: "web failed", heavy: true, rank: 30 }] });
  const warned = daemon("web", { verdict: verdict("warn", { reason: null }) });
  assert.deepEqual(lineOf(warned, WHEN), { parts: [{ text: "web", warn: true }] });
});

// -- order and caps -------------------------------------------------------------------------

test("failed rows' parts come first, then the warnings; lines likewise, then the quiet last line", () => {
  const list = [daemon("a", { verdict: verdict("ok", { reason: "running" }), since: null }),
                daemon("b", { verdict: verdict("warn", { cause: "stopped" }) }),
                daemon("c", { verdict: verdict("off") }),
                daemon("d", { verdict: verdict("fail") })];
  const facts = atlas(list, "atlas-api");
  assert.deepEqual(facts.parts.map((part) => part.text), ["d failed", "b is stopped"]);
  assert.deepEqual(facts.sections[0].lines.map((line) => line.parts.map((part) => part.text)),
                   [["d failed", "x"], ["b is stopped"], ["a running", "c off"]]);
});

test("past five failed and warned lines, the rest lead the last line keeping their weight; past seven parts, `<n> more`", () => {
  const list = [];
  for (let i = 1; i <= 9; i += 1) list.push(daemon(`f${i}`, { verdict: verdict("fail") }));
  list.push(daemon("w", { verdict: verdict("warn") }));
  for (let i = 1; i <= 8; i += 1) list.push(daemon(`o${i}`, { verdict: verdict("off") }));
  const facts = atlas(list, "atlas-api");
  const lines = facts.sections[0].lines;
  assert.equal(lines.length, 6);
  assert.deepEqual(lines.slice(0, 5).map((line) => line.parts[0].text), ["f1 failed", "f2 failed", "f3 failed",
                                                                          "f4 failed", "f5 failed"]);
  const heavy = (text) => ({ text, heavy: true, rank: 30 });
  assert.deepEqual(lines[5].parts, [heavy("f6 failed"), heavy("f7 failed"), heavy("f8 failed"), heavy("f9 failed"),
                                    { text: "w", warn: true }, { text: "o1 off" }, { text: "o2 off" },
                                    { text: "6 more" }]);
  assert.equal(facts.parts.length, PARTS_MAX);
  assert.deepEqual(facts.parts.at(-1), heavy("2 more"));
  assert.equal(facts.sections[0].rank, 30);
});

test("caps: eight parts stay whole, a ninth folds into `<n> more` with the strongest weight", () => {
  const plain = (n) => Array.from({ length: n }, (_, i) => ({ text: `p${i}` }));
  assert.equal(capped(plain(8)).length, 8);
  assert.deepEqual(capped(plain(9)).at(-1), { text: "2 more" });
  assert.deepEqual(capped([...plain(7), { text: "a", warn: true }, { text: "b" }]).at(-1), { text: "2 more", warn: true });
  const long = "x".repeat(TEXT_MAX_CHARS + 20);
  assert.equal(Array.from(clip(long)).length, TEXT_MAX_CHARS);
  assert.ok(clip(long).endsWith("…"));
  const reasoned = lineOf(daemon("web", { verdict: verdict("fail", { reason: long }) }), WHEN);
  assert.equal(Array.from(reasoned.parts[1].text).length, TEXT_MAX_CHARS);
});

// -- words and inputs -----------------------------------------------------------------------

test("times: the hour and minute today, the day word before it otherwise", () => {
  assert.equal(whenWord(Date.parse("2026-11-16T18:00:00Z") / 1000, NOW, UTC), "18:00");
  assert.equal(whenWord(Date.parse("2026-11-15T18:00:00Z") / 1000, NOW, UTC), "yesterday 18:00");
  assert.equal(whenWord(Date.parse("2026-11-13T18:00:00Z") / 1000, NOW, UTC), "Fri 18:00");
  assert.equal(whenWord(Date.parse("2026-10-10T08:52:00Z") / 1000, NOW, UTC), "Oct 10 08:52");
  assert.equal(epochOf("2026-11-16T18:00:00Z"), Date.parse("2026-11-16T18:00:00Z") / 1000);
  assert.equal(epochOf("2026-11-16T18:00:00.5+02:00"), Date.parse("2026-11-16T16:00:00.5Z") / 1000);
  for (const bad of [null, "", "2026-11-16T18:00:00", "yesterday", "2026-13-40T99:00:00Z"]) {
    assert.equal(epochOf(bad), null, String(bad));
  }
});

test("the `projects` attribute: its projects, and none from a value that isn't a JSON list", () => {
  assert.deepEqual(parseGiven(JSON.stringify(GIVEN)), GIVEN);
  assert.deepEqual(parseGiven('[{"key": "a", "root": "/a"}, {"key": 3}, "b"]'),
                   [{ key: "a", root: "/a", lead: null, associated: [] }]);
  for (const bad of [null, "", "{", '{"key": "a"}']) assert.deepEqual(parseGiven(bad), [], String(bad));
});
