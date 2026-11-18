import assert from "node:assert/strict";
import test from "node:test";

import {
  BUCKETS, bucketOf, commandWords, counts, countsLine, FACTS, FALLBACK, filtered, grouped, lastReadLine, lastRunLine,
  listShape, MOXI, named, NO_PROJECT, NO_SUPERVISOR, NOT_ANSWERING, NOTHING_ADDED, NOTHING_DOWN, OTHER, pageState,
  problemLines, projectKeys, READING, READING_AFTER_MS, risen, rowFact, servicesLabel, STORE_UNREAD, systemWord,
  timingWords, whereOf, YOURS,
} from "../src/list-model.ts";
import { moduleRow, row, rows, timer } from "./fixtures.mjs";

const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const WHEN = { now: Date.parse("2026-11-03T12:00:00Z") / 1000, clock: UTC };

const verdict = (state, cause, more = {}) =>
  ({ state, reason: "the reason", fix: null, unknown: cause === "unknown", cause, ...more });
/** A row of `cause` and verdict `state`, with `fields` laid over it. */
const of = (cause, state, fields = {}) => row({ verdict: verdict(state, cause), ...fields });

const SINCE = "2026-11-03T08:19:00Z";

// Every pair the verdict gives (services' README, "One verdict per row"), and what the
// page draws for it: fact, weight, rise, line two.
const TABLE = [
  ["not-running", "fail", { state: "failed" }, "down", "heavy", "fail", "It failed and is not running."],
  ["not-running", "fail", { state: "inactive" }, "down", "heavy", "fail", "It is not running."],
  ["missing", "fail", {}, "unit missing", "heavy", "fail", "Its unit is missing, so nothing runs it."],
  ["missing", "warn", {}, "off · unit missing", "text", null, null],
  ["not-armed", "fail", {}, "not scheduled", "heavy", "fail", "It is on, but its schedule is not armed."],
  ["schedule-failed", "fail", {}, "schedule failed", "heavy", "fail", "Its schedule failed, so it does not run."],
  ["health", "fail", { checked: { state: "fail", line: "connection refused", at: SINCE } },
   "failing its health check", "heavy", "fail", "connection refused"],
  ["last-run", "warn", { kind: "timer", last_run: { failed: true, text: "exit 1", at: "2026-11-03T02:00:00Z", exit: 1 } },
   "last run failed at 02:00", "text", "last-run", "python3 exited with code 1."],
  ["unknown", "warn", {}, "can't tell", "text", "unknown", "the reason"],
  ["running", "ok", { since: SINCE }, "running since 08:19", "dim", null, null],
  ["health", "warn", { since: SINCE }, "running since 08:19", "dim", null, null],
  ["listening", "ok", {}, "ready, starts on its first connection", "dim", null, null],
  ["armed", "ok", { kind: "timer", last_run: { failed: false, text: "ok", at: "2026-11-02T02:00:00Z", exit: 0 } },
   "last ran yesterday 02:00", "dim", null, null],
  ["armed", "ok", { kind: "timer", last_run: null }, "no run yet", "dim", null, null],
  ["stopped", "warn", { stopped: "2026-11-03T09:40:00Z" }, "stopped by hand at 09:40", "text", null, null],
  ["off", "off", {}, "off", "dim", null, null],
  ["switched-off", "off", {}, "off", "dim", null, null],
  ["running-off", "warn", { since: SINCE }, "running since 08:19 · off at login", "dim", null, null],
  ["no-login", "warn", { since: SINCE }, "running since 08:19 · won't start at login", "text", null, null],
  ["login-off", "warn", {}, "off, but starts at login", "text", null, null],
  ["armed-off", "warn", {}, "off, but its schedule is armed", "text", null, null],
  ["not-wrapped", "warn", {}, "runs not recorded", "text", null, null],
  ["removing", "warn", {}, "remove not finished", "text", null, null],
  ["elsewhere", "off", { platform: "darwin" }, "added on macOS", "dim", null, null],
  ["not-here", "off", {}, "not for this computer", "dim", null, null],
  ["no-supervisor", "off", {}, "no supervisor here", "dim", null, null],
];

test("every pair of the cause table has its fact, weight, rise and line two", () => {
  for (const [cause, state, fields, fact, weight, rise, line] of TABLE) {
    const found = rowFact(of(cause, state, fields), WHEN);
    assert.deepEqual(found, { fact, weight, rise, line }, `${cause} ${state}`);
  }
  const pairs = new Set(TABLE.map(([cause, state]) => `${cause} ${state}`));
  assert.deepEqual([...pairs].sort(), Object.keys(FACTS).sort());
});

test("a value the table doesn't name reads by its verdict state alone, never by its reason", () => {
  const expect = {
    fail: { fact: "not working", weight: "heavy", rise: "fail", line: null },
    warn: { fact: "needs a look", weight: "text", rise: null, line: null },
    ok: { fact: "ok", weight: "dim", rise: null, line: null },
    off: { fact: "off", weight: "dim", rise: null, line: null },
  };
  for (const state of ["fail", "warn", "ok", "off"]) {
    for (const cause of ["sleeping", null, "down-since"]) {
      const it = of(cause, state);
      assert.equal(named(it), false);
      assert.deepEqual(rowFact(it, WHEN), expect[state], `${cause} ${state}`);
    }
    assert.deepEqual(FALLBACK[state], expect[state]);
  }
  // A named cause with a state the verdict never gives it is not the table's pair.
  assert.deepEqual(rowFact(of("running", "fail"), WHEN), expect.fail);
  assert.deepEqual(rowFact(of("stopped", "off"), WHEN), expect.off);
});

test("a time leaves out its clause when it is absent", () => {
  assert.equal(rowFact(of("running", "ok", { since: null }), WHEN).fact, "running");
  assert.equal(rowFact(of("no-login", "warn", { since: "not a time" }), WHEN).fact, "running · won't start at login");
  assert.equal(rowFact(of("stopped", "warn", { stopped: null }), WHEN).fact, "stopped by hand");
  assert.equal(rowFact(of("last-run", "warn", { last_run: { failed: true, text: "exit 1", at: null, exit: 1 } }), WHEN).fact,
               "last run failed");
  assert.equal(rowFact(of("armed", "ok", { last_run: { failed: false, text: "ok", at: null, exit: 0 } }), WHEN).fact,
               "last ran");
  assert.equal(rowFact(of("running", "ok", { since: "2026-10-20T07:05:00Z" }), WHEN).fact, "running since Oct 20 07:05");
});

test("line two goes on a row that rises only, and the phone's nulled words leave it out", () => {
  const phone = { checked: { state: "fail", line: null, at: null }, argv: null, verdict: verdict("fail", "health", { reason: null }) };
  assert.equal(rowFact(row(phone), WHEN).line, null);
  assert.equal(rowFact(row({ verdict: verdict("warn", "unknown", { reason: null }) }), WHEN).line, null);
  assert.equal(rowFact(of("stopped", "warn"), WHEN).line, null);
});

test("a failed last run's line two: the command, the phone's, a supervisor's result, a streak", () => {
  const failed = (fields = {}) => timer({ verdict: verdict("warn", "last-run"),
    last_run: { failed: true, text: "exit 2", at: "2026-11-03T02:00:00Z", exit: 2 }, ...fields });
  assert.equal(lastRunLine(failed(), WHEN), "python3 exited with code 2.");
  assert.equal(lastRunLine(failed({ argv: null }), WHEN), "Its last run exited with code 2.");
  assert.equal(lastRunLine(failed({ argv: null, last_run: { failed: true, text: "failed", at: null, exit: null } }), WHEN),
               "Its last run failed.");
  const streak = { count: 4, since: "2026-11-01T02:00:00Z", same_exit: true };
  assert.equal(lastRunLine(failed({ streak }), WHEN), "Every run since Sun 02:00 exited with code 2.");
  assert.equal(lastRunLine(failed({ streak: { ...streak, same_exit: false } }), WHEN), "Every run since Sun 02:00 failed.");
  assert.equal(lastRunLine(failed({ streak: { ...streak, since: null } }), WHEN), "Its last 4 runs exited with code 2.");
  assert.equal(lastRunLine(failed({ streak: { ...streak, count: 1 } }), WHEN), "python3 exited with code 2.");
});

test("what rises: every fail by name, then last runs oldest first, then can't tell by name", () => {
  const run = (name, at) => timer({ id: `services/${name}`, name, verdict: verdict("warn", "last-run"),
    last_run: { failed: true, text: "exit 1", at, exit: 1 } });
  const list = [
    of("unknown", "warn", { id: "services/zed", name: "zed" }),
    run("late", "2026-11-03T09:00:00Z"),
    of("not-running", "fail", { id: "services/web", name: "web" }),
    of("running", "ok", { id: "services/fine", name: "fine" }),
    run("never", null),
    of("sleeping", "fail", { id: "services/api", name: "api" }),
    run("early", "2026-11-02T09:00:00Z"),
    run("also-late", "2026-11-03T09:00:00Z"),
    of("unknown", "warn", { id: "services/alpha", name: "alpha" }),
    of("stopped", "warn", { id: "services/held", name: "held" }),
    of("missing", "fail", { id: "services/gone", name: "gone" }),
  ];
  assert.deepEqual(risen(list, WHEN).map((r) => r.name),
                   ["api", "gone", "web", "early", "also-late", "late", "never", "alpha", "zed"]);
});

test("the counts sum to every row that doesn't rise, for every cause", () => {
  const list = [
    ...TABLE.map(([cause, state, fields], i) => of(cause, state, { id: `services/r${i}`, name: `r${i}`, ...fields })),
    ...["fail", "warn", "ok", "off"].map((state) => of("new-word", state, { id: `services/n-${state}`, name: `n-${state}` })),
    of(null, "ok", { id: "services/null", name: "null" }),
  ];
  const found = counts(list, WHEN);
  const up = risen(list, WHEN);
  assert.equal(found.reduce((sum, c) => sum + c.n, 0) + up.length, list.length);
  for (const r of list) assert.equal(bucketOf(r, WHEN) === null, up.includes(r), r.id);
  assert.deepEqual(found.map((c) => c.label), [...BUCKETS.map((b) => b.label), OTHER]);
  assert.deepEqual(Object.fromEntries(found.map((c) => [c.label, c.n])), {
    running: 4, ready: 1, "on schedule": 3, "stopped by hand": 1, off: 5, "being removed": 1, "not run here": 3,
    other: 4,
  });
  assert.equal(countsLine([{ label: "running", n: 3 }, { label: "off", n: 1 }]), "3 running · 1 off");
  assert.deepEqual(counts([of("running", "ok")], WHEN), [{ label: "running", n: 1 }]);
  assert.equal(servicesLabel(1), "1 service");
  assert.equal(servicesLabel(0), "0 services");
  assert.equal(servicesLabel(7), "7 services");
});

test("groups with projects: each key, then No project, then Moxi, risen rows lifted out", () => {
  const his = (name, project, fields = {}) => of("running", "ok", { id: `services/${name}`, name, project, ...fields });
  const moxi = (module, name, cause = "running", state = "ok") =>
    moduleRow({ id: `${module}/${name}`, module, name, verdict: verdict(state, cause) });
  const list = [
    his("web", "zeta"), his("api", "atlas"), his("loose", null), his("db", "atlas"),
    his("down", "atlas", { verdict: verdict("fail", "not-running") }),
    moxi("ui", "shell"), moxi("cache", "watch", "switched-off", "off"), moxi("cache", "clean"), moxi("slots", "server"),
  ];
  const { risen: up, groups } = grouped(list, true, WHEN);
  assert.deepEqual(up.map((r) => r.name), ["down"]);
  assert.deepEqual(groups.map((g) => [g.label, g.project, g.moxi, g.rows.map((r) => r.id)]), [
    ["atlas", "atlas", false, ["services/api", "services/db"]],
    ["zeta", "zeta", false, ["services/web"]],
    [NO_PROJECT, null, false, ["services/loose"]],
    [MOXI, null, true, ["cache/clean", "slots/server", "ui/shell", "cache/watch"]],
  ]);
});

test("groups without projects: Yours, then Moxi; no key shown; an empty group is left out", () => {
  const list = [of("running", "ok", { id: "services/b", name: "b", project: "atlas" }),
                of("running", "ok", { id: "services/a", name: "a" })];
  assert.deepEqual(grouped(list, false, WHEN).groups.map((g) => [g.label, g.rows.map((r) => r.name)]),
                   [[YOURS, ["a", "b"]]]);
  assert.deepEqual(grouped([moduleRow()], false, WHEN).groups.map((g) => g.label), [MOXI]);
  assert.deepEqual(grouped([], true, WHEN), { risen: [], groups: [] });
});

test("the timing words", () => {
  const t = (every, at = null, days = null) => timingWords({ every, at, days });
  assert.equal(t("1h"), "every hour");
  assert.equal(t("6h"), "every 6 hours");
  assert.equal(t("15m"), "every 15 minutes");
  assert.equal(t("1m"), "every minute");
  assert.equal(t("90s"), "every 90s");
  assert.equal(t(null, ["02:00"]), "daily at 02:00");
  assert.equal(t(null, ["02:00"], ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]), "daily at 02:00");
  assert.equal(t(null, ["04:30"], ["mon"]), "Mondays at 04:30");
  assert.equal(t(null, ["09:00"], ["fri", "mon"]), "Mon and Fri at 09:00");
  assert.equal(t(null, ["09:00", "17:30"], ["mon", "wed", "fri"]), "Mon, Wed and Fri at 09:00 and 17:30");
  assert.equal(t(null, []), null);
  assert.equal(timingWords(null), null);
});

test("line one's dim words: his doc or command, Moxi's module, the timing, spends usage", () => {
  const plain = { projects: true, risen: false };
  assert.equal(whereOf(row(), plain), "serves the docs");
  assert.equal(whereOf(row({ doc: null }), plain), "python3 -m http.server");
  assert.equal(whereOf(row({ doc: null, argv: ["C:\\Tools\\sync.exe", "--all", "a b"] }), plain), 'sync.exe --all "a b"');
  // The phone gets neither: the gateway nulls both.
  assert.equal(whereOf(row({ doc: null, argv: null }), plain), null);
  assert.equal(whereOf(moduleRow({ id: "slots/server", module: "slots", name: "server" }), plain), "slots");
  assert.equal(whereOf(timer({ doc: null, launches_session: true }), plain), "python3 -m http.server · every 5 minutes · spends usage");
  // A risen row's dim is its project key; a risen Moxi row keeps its module.
  const up = { projects: true, risen: true };
  assert.equal(whereOf(row({ project: "atlas-api" }), up), "atlas-api");
  assert.equal(whereOf(row({ project: null }), up), "serves the docs");
  assert.equal(whereOf(row({ project: "atlas-api" }), { projects: false, risen: true }), "serves the docs");
  assert.equal(whereOf(moduleRow(), up), "cache");
  assert.equal(commandWords(["/bin/true"]), "true");
  assert.equal(commandWords(null), null);
  assert.equal(systemWord("win32"), "Windows");
  assert.equal(systemWord("plan9"), "plan9");
  assert.equal(systemWord(null), "another system");
});

test("the list's shape: the first group, its risen rows or sentence and counts, and the groups", () => {
  const down = of("not-running", "fail", { id: "services/queue-worker", name: "queue-worker", project: "atlas-api", state: "failed", doc: "works the queue" });
  const up = of("running", "ok", { id: "services/atlas-dev", name: "atlas-dev", project: "atlas-api", since: SINCE, doc: "serves the api" });
  const shell = moduleRow({ verdict: verdict("ok", "running"), since: SINCE });
  const AT = "2026-11-03T11:58:00Z";

  const quiet = listShape([up, shell], AT, true, WHEN);
  assert.equal(quiet.label, "2 services");
  assert.deepEqual(quiet.risen, []);
  assert.equal(quiet.sentence, NOTHING_DOWN);
  assert.equal(quiet.counts, "2 running");
  assert.equal(quiet.read, "read 11:58");
  assert.deepEqual(quiet.groups.map((g) => g.group.label), ["atlas-api", MOXI]);
  assert.deepEqual(quiet.groups[0].rows[0], { row: up, name: "atlas-dev", where: "serves the api",
                                              fact: "running since 08:19", weight: "dim", line: null });

  // A risen row replaces the sentence and the counts, leaves its group, and reads its
  // project key and line two.
  const loud = listShape([down, up, shell], null, true, WHEN);
  assert.equal(loud.label, "3 services");
  assert.equal(loud.sentence, null);
  assert.equal(loud.counts, null);
  assert.equal(loud.read, null);
  assert.deepEqual(loud.risen.map((v) => [v.name, v.where, v.fact, v.weight, v.line]),
                   [["queue-worker", "atlas-api", "down", "heavy", "It failed and is not running."]]);
  assert.deepEqual(loud.groups[0].rows.map((v) => v.name), ["atlas-dev"]);
  // Without projects the risen row keeps its own words.
  assert.equal(listShape([down], null, false, WHEN).risen[0].where, "works the queue");
  // No row: no counts.
  const none = listShape([], AT, false, WHEN);
  assert.deepEqual([none.label, none.sentence, none.counts, none.groups], ["0 services", NOTHING_DOWN, null, []]);
});

// -- the filter and the page's states ---------------------------------------------------------

test("the filter keeps one project's rows of his and counts them against every row", () => {
  const list = [of("running", "ok", { id: "services/a", name: "a", project: "atlas" }),
                of("running", "ok", { id: "services/b", name: "b", project: "beta" }),
                of("running", "ok", { id: "services/c", name: "c", project: null }),
                moduleRow({ verdict: verdict("running", "ok"), project: "atlas" })];
  const found = filtered(list, "atlas");
  assert.deepEqual(found.rows.map((r) => r.id), ["services/a"]);
  assert.equal(found.shown, "1 of 4 services");
  assert.equal(filtered(list, "none").shown, "0 of 4 services");
  assert.deepEqual(projectKeys(list), ["atlas", "beta"]);
});

const read = (value, more = {}) => ({ value, failure: null, gone: false, ...more });
const LOST = { status: 0, why: "no answer from the shell", said: null };

test("the page's states, each with its first row", () => {
  assert.deepEqual(pageState(read(null), 0, false, WHEN),
                   { state: "loading", line: null, more: null, rows: false, acts: false, dim: false });
  assert.equal(pageState(read(null), READING_AFTER_MS, false, WHEN).line, READING);
  assert.equal(pageState(read(null, { gone: true }), 0, false, WHEN).state, "gone");

  // Not answering: the rows last read, dim and with no act, and when they were read.
  const down = pageState(read(rows([row()]), { failure: LOST }), 0, false, WHEN);
  assert.deepEqual(down, { state: "down", line: NOT_ANSWERING, more: "Last read 10:05. It may have changed since.",
                           rows: true, acts: false, dim: true });
  assert.deepEqual(pageState(read(null, { failure: LOST }), 0, false, WHEN),
                   { state: "down", line: NOT_ANSWERING, more: null, rows: false, acts: false, dim: true });

  // The store can't be read: its sentence at the desk, the phone's own words.
  const error = rows([], { state: "error", detail: "the store is too new; fix: upgrade", at: null });
  assert.deepEqual(pageState(read(error), 0, false, WHEN),
                   { state: "error", line: "the store is too new; fix: upgrade", more: null, rows: false, acts: false,
                     dim: false });
  assert.equal(pageState(read(error), 0, true, WHEN).line, STORE_UNREAD);
  assert.equal(pageState(read({ ...error, detail: null }), 0, false, WHEN).line, STORE_UNREAD);

  // Every row unknown for one reason: the supervisor can't be read.
  const why = { verdict: verdict("warn", "unknown", { reason: "the supervisor didn't answer" }) };
  const unread = pageState(read(rows([row(why), moduleRow(why)])), 0, false, WHEN);
  assert.deepEqual(unread, { state: "unread", line: NO_SUPERVISOR, more: "the supervisor didn't answer", rows: true,
                             acts: true, dim: true });
  const two = [row(why), moduleRow({ verdict: verdict("warn", "unknown", { reason: "another" }) })];
  assert.equal(pageState(read(rows(two)), 0, false, WHEN).state, "up");
  assert.equal(pageState(read(rows([row(why), moduleRow()])), 0, false, WHEN).state, "up");
  assert.equal(pageState(read(rows([])), 0, false, WHEN).state, "up");
  assert.equal(lastReadLine(null, WHEN), null);
});

test("with the supervisor unread nothing rises, and with no row of his the first group says so", () => {
  const why = { verdict: verdict("warn", "unknown") };
  const quiet = listShape([row(why)], null, false, WHEN, { quiet: true });
  assert.deepEqual(quiet.risen, []);
  assert.deepEqual(quiet.groups.map((g) => g.group.label), [YOURS]);
  assert.equal(listShape([row(why)], null, false, WHEN).risen.length, 1);

  const shell = moduleRow({ id: "ui/shell", module: "ui", name: "shell", verdict: verdict("ok", "running") });
  const empty = listShape([shell], "2026-11-03T10:05:00Z", true, WHEN);
  assert.equal(empty.his, false);
  assert.equal(empty.label, "1 service");
  assert.equal(empty.sentence, NOTHING_DOWN);
  assert.equal(empty.more, NOTHING_ADDED);
  assert.equal(empty.counts, null);
  assert.equal(empty.read, "read 10:05");
  assert.deepEqual(empty.groups.map((g) => g.group.label), [MOXI]);
  const mine = listShape([shell, of("running", "ok")], null, true, WHEN);
  assert.equal(mine.his, true);
  assert.equal(mine.more, null);
  assert.equal(mine.counts, "2 running");
});

test("a module whose declarations failed is one line at the foot", () => {
  assert.deepEqual(problemLines(rows([], { problems: [{ module: "notes", reason: "UNITS_FROM failed: boom" },
                                                       { module: "tunnel", reason: null }] })),
                   ["notes: UNITS_FROM failed: boom; its static daemons and timers are listed",
                    "tunnel: hidden here; its static daemons and timers are listed"]);
});
