import assert from "node:assert/strict";
import test from "node:test";

import {
  actsOf, addBody, addRefused, commandLine, counts, emptyForm, flagLine, groups, healthLine, HIDDEN, listView,
  MAY_HAVE_ACTED, NO_PROJECT, outcomeSaid, rowFacts, shownActs, SPENDS, statusLost, statusSaid, summaryView,
  timeLine, verdictLine, when,
} from "../src/views.ts";
import { moduleRow, row, rows, timer } from "./fixtures.mjs";

const fed = (value, more = {}) => ({ value, failure: null, gone: false, loading: false, ...more });

test("rows group by project, keys in order ignoring case, and the rows of none last", () => {
  const list = [row({ id: "services/a", project: "zeta" }), moduleRow(), row({ id: "services/b", project: "Alpha" }),
                row({ id: "services/c", project: "zeta" }), row({ id: "services/d", project: "beta" })];
  const found = groups(list);
  assert.deepEqual(found.map((g) => g.label), ["Alpha", "beta", "zeta", NO_PROJECT]);
  assert.deepEqual(found[2].rows.map((r) => r.id), ["services/a", "services/c"]);
  assert.equal(found[3].project, null);
  assert.deepEqual(groups([]), []);
});

test("the list view's states", () => {
  assert.equal(listView(fed(null, { loading: true })).state, "loading");
  assert.equal(listView(fed(null, { gone: true })).state, "gone");
  const failed = listView(fed(null, { failure: { status: 0, why: "no answer from the shell", said: null } }));
  assert.equal(failed.state, "failed");
  assert.equal(failed.line, "The rows can't be read: no answer from the shell.");
  const stale = listView(fed(rows([row()]), { failure: { status: 504, why: "services' page server answered 504", said: null } }));
  assert.equal(stale.state, "failed");
  assert.equal(stale.groups[0].rows.length, 1);
  assert.match(stale.line, /rows last read/);
  const error = listView(fed({ state: "error", detail: "the store is too new; fix: upgrade", rows: [], problems: [] }));
  assert.equal(error.line, "The rows can't be listed: the store is too new; fix: upgrade");
  assert.equal(listView(fed(rows([]))).state, "empty");
  const up = listView(fed(rows([row()], { problems: [{ module: "notes", reason: "UNITS_FROM failed: boom" }] })));
  assert.equal(up.state, "up");
  assert.deepEqual(up.problems, ["notes: UNITS_FROM failed: boom; its static daemons and timers are listed"]);
});

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

test("a daemon offers start, stop, restart, enable, disable and, as a user row, remove", () => {
  const acts = actsOf(row());
  assert.deepEqual(acts.map((a) => [a.name, a.path]), [
    ["start", "api/start"], ["stop", "api/stop"], ["restart", "api/restart"],
    ["enable", "api/enable"], ["disable", "api/disable"], ["remove", "api/remove"]]);
  for (const act of acts) assert.deepEqual(act.body, { id: "services/web" });
  assert.deepEqual(acts.filter((a) => a.desk).map((a) => a.name), ["remove"]);
});

test("a timer runs through /api/run in place of start and restart", () => {
  const acts = actsOf(timer());
  assert.deepEqual(acts.map((a) => [a.name, a.path, a.body]), [
    ["run", "api/run", { id: "services/backup" }], ["rerun", "api/run", { id: "services/backup", restart: true }],
    ["stop", "api/stop", { id: "services/backup" }], ["enable", "api/enable", { id: "services/backup" }],
    ["disable", "api/disable", { id: "services/backup" }], ["remove", "api/remove", { id: "services/backup" }]]);
  assert.ok(!acts.some((a) => a.path === "api/start" || a.path === "api/restart"));
});

test("a module row takes no remove, and one without a switch no enable or disable", () => {
  assert.ok(!actsOf(moduleRow()).some((a) => a.name === "remove"));
  assert.deepEqual(actsOf(moduleRow({ has_switch: false, switch: null })).map((a) => a.name), ["start", "stop", "restart"]);
  assert.match(actsOf(moduleRow()).find((a) => a.name === "enable").sheet[0], /cache\.watch\.enabled/);
});

test("the phone leaves remove out", () => {
  assert.ok(!shownActs(row(), true).some((a) => a.name === "remove"));
  assert.ok(shownActs(row(), false).some((a) => a.name === "remove"));
});

test("a run of a row that launches a session says it spends usage, and other acts don't", () => {
  const acts = actsOf(timer({ launches_session: true }));
  for (const name of ["run", "rerun"]) assert.ok(acts.find((a) => a.name === name).sheet.includes(SPENDS), name);
  for (const name of ["stop", "enable", "disable", "remove"]) assert.ok(!acts.find((a) => a.name === name).sheet.includes(SPENDS), name);
  assert.ok(!actsOf(timer()).find((a) => a.name === "run").sheet.includes(SPENDS));
  assert.match(actsOf(row({ form: "adopted", unit: "backup.service" })).find((a) => a.name === "remove").sheet[0],
               /unit stays on the system/);
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

test("a written row starts off unless the user turns it on", () => {
  assert.equal(emptyForm().enable, false);
  const form = { ...emptyForm(), name: "web", command: "/usr/bin/python3 -m http.server" };
  assert.equal(addBody(form).body.enable, false);
  assert.equal(addBody({ ...form, enable: true }).body.enable, true);
});

test("the add body: a written daemon, a timer every or at, an adopted unit", () => {
  const form = { ...emptyForm(), name: " web ", command: "/usr/bin/python3 -m http.server", cwd: "/srv", enable: true,
                 health: "/usr/bin/curl -f http://localhost:8000", project: "alpha", doc: "docs" };
  assert.deepEqual(addBody(form), { body: { name: "web", kind: "daemon", command: "/usr/bin/python3 -m http.server",
    enable: true, cwd: "/srv", health: "/usr/bin/curl -f http://localhost:8000", project: "alpha", doc: "docs" } });
  const every = { ...emptyForm(), name: "backup", kind: "timer", command: "/usr/bin/true", every: "5m", enable: false,
                  launchesSession: true };
  assert.deepEqual(addBody(every).body, { name: "backup", kind: "timer", command: "/usr/bin/true", enable: false,
                                          every: "5m", launches_session: true });
  const at = { ...every, schedule: "at", at: "17:30, 09:00", days: ["fri", "mon"], launchesSession: false };
  assert.deepEqual(addBody(at).body, { name: "backup", kind: "timer", command: "/usr/bin/true", enable: false,
                                       at: ["17:30", "09:00"], days: ["mon", "fri"] });
  const adopt = { ...emptyForm(), name: "syncthing", source: "unit", unit: "syncthing.service", adoptOn: "off",
                  cwd: "/ignored", every: "5m" };
  assert.deepEqual(addBody(adopt).body, { name: "syncthing", kind: "daemon", unit: "syncthing.service", enable: false });
});

test("the add form says what it still needs", () => {
  assert.match(addBody(emptyForm()).missing, /name/);
  assert.match(addBody({ ...emptyForm(), name: "x" }).missing, /command line/);
  assert.match(addBody({ ...emptyForm(), name: "x", kind: "timer", command: "/bin/true" }).missing, /how often/);
  assert.match(addBody({ ...emptyForm(), name: "x", kind: "timer", command: "/bin/true", schedule: "at" }).missing, /times/);
  assert.match(addBody({ ...emptyForm(), name: "x", source: "unit" }).missing, /unit/);
  assert.match(addBody({ ...emptyForm(), name: "x", source: "unit", unit: "a.service" }).missing, /on or off/);
});

test("an add that wasn't taken says why, and a lost answer may have been taken", () => {
  assert.deepEqual(addRefused(422, "x is not a full path"), { tone: "error", lines: ["x is not a full path"] });
  assert.equal(addRefused(409, null).lines[0], "services is busy; try again.");
  assert.equal(addRefused(0, null).tone, "warn");
  assert.match(addRefused(504, null).lines[0], /may have been taken/);
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
