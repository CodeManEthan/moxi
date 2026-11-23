import assert from "node:assert/strict";
import test from "node:test";

import {
  commandLine, counts, flagLine, healthLine, HIDDEN,
  MAY_HAVE_ACTED, outcomeSaid, rowFacts, statusLost, statusSaid, summaryView,
  timeLine, verdictLine, when,
} from "../src/views.ts";
import { moduleRow, row, rows, timer } from "./fixtures.mjs";

const fed = (value, more = {}) => ({ value, failure: null, gone: false, loading: false, ...more });

test("a row's lines: verdict, flag or switch, since or last run, health", () => {
  assert.equal(when("2026-11-03T09:15:00Z"), "2026-11-03 09:15 UTC");
  assert.equal(when("yesterday"), "yesterday");
  assert.equal(verdictLine({ state: "fail", reason: "not running", fix: null, unknown: false }), "fail: not running");
  assert.equal(verdictLine({ state: "warn", reason: null, fix: null, unknown: false }), `warn: ${HIDDEN}`);
  assert.equal(flagLine(row()), "flag: on");
  assert.equal(flagLine(row({ flag: null })), "flag: can't be read");
  assert.equal(flagLine(moduleRow({ flag: false })), "switch cache.watch.enabled: off");
  assert.equal(flagLine(moduleRow({ has_switch: false, switch: null })), "no switch: on while its module is installed");
  assert.equal(timeLine(row()), "running since 2026-11-03 09:15 UTC");
  assert.equal(timeLine(row({ state: "inactive", stopped: "2026-11-03T11:00:00Z" })), "stopped by hand at 2026-11-03 11:00 UTC");
  assert.equal(timeLine(row({ state: "inactive", since: null })), null);
  assert.equal(timeLine(timer()), "every 5m; last run ok at 2026-11-03 10:00 UTC");
  assert.equal(timeLine(timer({ last_run: null })), "every 5m; no run recorded");
  assert.equal(healthLine(row()), null);
  assert.deepEqual(healthLine(row({ checked: { state: "fail", line: "connection refused" } })),
                   { tone: "error", text: "health fail: connection refused" });
});

test("a command line quotes the words that need it", () => {
  assert.equal(commandLine(["/bin/sh", "-c", "echo hi"]), '/bin/sh -c "echo hi"');
  assert.equal(commandLine([null]), HIDDEN);
  assert.equal(commandLine(null), null);
  const facts = rowFacts(row({ cwd: "/srv", health: ["/usr/bin/curl", "-f", "http://localhost"] }));
  assert.deepEqual(facts.map((f) => f.name), ["form", "command", "cwd", "health", "state", "platform"]);
  assert.equal(facts.find((f) => f.name === "state").value, "active, enabled, pid 4242");
});

test("each confirm outcome but accepted, cancelled and desk says something", () => {
  assert.equal(outcomeSaid({ outcome: "accepted", status: "/api/status?job=a" }), null);
  assert.equal(outcomeSaid({ outcome: "cancelled" }), null);
  assert.equal(outcomeSaid({ outcome: "desk", text: "Do this at the desk." }), null);
  assert.deepEqual(outcomeSaid({ outcome: "refused", code: 422, text: "no row web" }), { tone: "error", lines: ["no row web"] });
  assert.deepEqual(outcomeSaid({ outcome: "unknown", text: "no answer in 30 s" }),
                   { tone: "warn", lines: ["no answer in 30 s", MAY_HAVE_ACTED] });
});

test("a job's status: running, then its end with its lines and fix", () => {
  const base = { job: "k", act: "stop", id: "services/web", code: null, lines: [], fix: null };
  assert.deepEqual(statusSaid("Stop web", { ...base, state: "running" }), { tone: "muted", lines: ["Stop web: running…"] });
  assert.deepEqual(statusSaid("Stop web", { ...base, state: "done", code: 0, lines: ["services/web: stopped"] }),
                   { tone: "ok", lines: ["Stop web: Done.", "services/web: stopped"] });
  assert.deepEqual(statusSaid("Start web", { ...base, state: "refused", code: 3, lines: [null], fix: "moxi services enable web" }),
                   { tone: "error", lines: ["Start web: Refused; nothing changed.", HIDDEN, "Fix: moxi services enable web"] });
  assert.equal(statusSaid("x", { ...base, state: "failed", code: 1 }).tone, "error");
  assert.equal(statusSaid("x", { ...base, state: "unknown", lines: ["no record of this act"] }).tone, "warn");
  assert.equal(statusLost("Stop web", null).tone, "warn");
});

test("the summary counts rows by verdict, and its tone is the worst", () => {
  const list = [row(), row({ verdict: { state: "fail", reason: "not running", fix: null, unknown: false } }),
                timer({ verdict: { state: "warn", reason: "x", fix: null, unknown: true } }),
                moduleRow({ verdict: { state: "off", reason: "switch off", fix: null, unknown: false } }), row()];
  assert.deepEqual(counts(list), { ok: 2, warn: 1, fail: 1, off: 1 });
  const view = summaryView(fed(rows(list)));
  assert.equal(view.state, "up");
  assert.equal(view.worst, "fail");
  assert.equal(summaryView(fed(rows([moduleRow({ verdict: { state: "off", reason: "x", fix: null, unknown: false } })]))).worst, "off");
  const empty = summaryView(fed(rows([])));
  assert.deepEqual(empty.counts, { ok: 0, warn: 0, fail: 0, off: 0 });
  assert.equal(empty.worst, null);
  assert.equal(empty.line, "No daemon or timer yet.");
  assert.equal(summaryView(fed(null, { loading: true })).state, "loading");
  assert.equal(summaryView(fed(null, { failure: { status: 0, why: "no answer from the shell", said: null } })).state, "failed");
  assert.equal(summaryView(fed({ state: "error", detail: "bad", rows: [], problems: [] })).line, "The rows can't be listed: bad");
});
