import assert from "node:assert/strict";
import test from "node:test";

import { clock, healthEntries, healthLine, healthRest, runFailed } from "../src/health-model.ts";

const AT = "2026-10-30T19:06:00Z";
const STAMP = new Date(Date.parse(AT)).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const TIME = `checked ${STAMP}`;

const row = (module, name, state, detail = `${name} is ${state}`, fix = null) => ({ module, name, state, detail, fix });
const run = (rows, extra = {}) => ({ at: AT, running: false, rows, error: null, ...extra });
/** A run as the gateway sends it: no text of a check's own. */
const cut = (value) => ({ ...value, error: null, rows: value.rows.map((item) => ({ ...item, detail: null, fix: null })) });
const text = (line) => line.parts.map((part) => part.text).join("");

test("a time reads as the hour and minute, and no time as nothing", () => {
  assert.equal(clock(AT), STAMP);
  assert.equal(clock(null), "");
  assert.equal(clock("soon"), "");
});

test("with no run kept the row says it is checking, and needs nothing", () => {
  for (const value of [null, { at: null, running: true, rows: [], error: null },
                       { at: null, running: false, rows: [], error: null }]) {
    assert.deepEqual(healthLine(value, true), { parts: [{ text: "Checking…" }] });
  }
});

test("every check ok or off: all checks pass, with the time, and nothing to open", () => {
  const line = healthLine(run([row("ui", "shell", "ok"), row("cache", "store", "off")]), true);
  assert.deepEqual(line, { parts: [{ text: "All checks pass" }], time: TIME });
});

test("warnings only: the count and the first warning's detail; it opens and needs nothing", () => {
  const one = healthLine(run([row("ui", "shell", "ok"), row("projects", "roots", "warn", "A folder is missing")]), true);
  assert.equal(text(one), "1 warning · A folder is missing");
  assert.deepEqual([one.time, one.needs, one.opens], [TIME, undefined, true]);
  const two = healthLine(run([row("a", "x", "warn", "First"), row("b", "y", "odd", "Second")]), true);
  assert.equal(text(two), "2 warnings · First");
});

test("a failing check: the count heavy, the warnings, the first fail's detail, and it needs the user", () => {
  const line = healthLine(run([row("projects", "roots", "warn", "A folder is missing"), row("ui", "shell", "ok"),
                               row("turns", "guard", "fail", "A guard could not check a call")]), true);
  assert.deepEqual(line.parts, [{ text: "1 check failing", heavy: true }, { text: ", 1 warning" },
                                { text: " · A guard could not check a call" }]);
  assert.deepEqual([line.time, line.needs, line.opens], [TIME, 30, true]);
  const two = healthLine(run([row("a", "x", "fail", "First"), row("b", "y", "fail", "Second")]), true);
  assert.equal(text(two), "2 checks failing · First");
});

test("a run that gave no rows could not run: its reason, and it opens only at the desk", () => {
  const failed = run([], { error: "the doctor gave no answer within 60 s" });
  assert.equal(runFailed(failed), true);
  const line = healthLine(failed, true);
  assert.equal(text(line), "The checks could not run · the doctor gave no answer within 60 s");
  assert.deepEqual([line.time, line.needs, line.opens], [TIME, 30, true]);
  const phone = healthLine(cut(failed), false);
  assert.deepEqual(phone, { parts: [{ text: "The checks could not run" }], time: TIME, needs: 30 });
  assert.equal(runFailed(run([row("ui", "shell", "ok")])), false);
  assert.equal(runFailed({ at: null, running: true, rows: [], error: null }), false);
});

test("on the phone a cut detail is left out together with its separator", () => {
  const rows = [row("turns", "guard", "fail"), row("projects", "roots", "warn")];
  assert.equal(text(healthLine(cut(run(rows)), false)), "1 check failing, 1 warning");
  assert.equal(text(healthLine(cut(run(rows.slice(1))), false)), "1 warning");
  assert.equal(text(healthLine(cut(run(rows.slice(0, 1))), false)), "1 check failing");
  assert.equal(text(healthLine(cut(run([row("ui", "shell", "ok")])), false)), "All checks pass");
});

test("opened: the failing checks, then the warnings, and none that pass", () => {
  const value = run([row("projects", "roots", "warn", "A folder is missing", "moxi projects set x --root DIR"),
                     row("ui", "shell", "ok"), row("cache", "store", "off"),
                     row("turns", "guard", "fail", "A guard could not check a call"), row("ui", "page:x", "fail", "")]);
  assert.deepEqual(healthEntries(value), [
    { module: "turns", name: "guard", failing: true, detail: "A guard could not check a call", fix: null },
    { module: "ui", name: "page:x", failing: true, detail: null, fix: null },
    { module: "projects", name: "roots", failing: false, detail: "A folder is missing", fix: "moxi projects set x --root DIR" },
  ]);
  assert.equal(healthRest(value), "Every other check passes");
  assert.equal(healthRest(run([row("turns", "guard", "fail")])), "");
  assert.deepEqual(healthEntries(null), []);
});
