import assert from "node:assert/strict";
import test from "node:test";

import {
  actsOf, addAgain, inUse, jobSaid, phoneActs, phoneRoute, phoneSheet, removeConfirm, runningFact, SHELL_RESTART,
  shellWord, slotsConfirm, transportSaid, UNREAD, usageConfirm, warned,
} from "../src/acts-model.ts";
import { moduleRow, row, timer } from "./fixtures.mjs";

const verdict = (state, cause) => ({ state, reason: "r", fix: null, unknown: cause === "unknown", cause });
const of = (make, cause, state, fields = {}) => make({ verdict: verdict(state, cause), ...fields });
const names = ({ closed, foot }) => [closed?.label ?? null, foot.map((a) => (a.tucked ? `(${a.label})` : a.label))];

test("the acts table, per cause and kind, his and Moxi's", () => {
  const cases = [
    [of(row, "not-running", "fail"), [ "Start", ["Start", "Turn off", "Remove"]]],
    [of(row, "stopped", "warn"), ["Start", ["Start", "Turn off", "Remove"]]],
    [of(row, "off", "off", { flag: false }), ["Start", ["Start", "Turn on", "Remove"]]],
    [of(row, "login-off", "warn", { flag: false }), ["Start", ["Start", "Turn on", "Remove"]]],
    [of(moduleRow, "stopped", "warn"), ["Start", ["Start"]]],
    [of(moduleRow, "not-running", "fail"), ["Start", ["Start"]]],
    [of(row, "running", "ok"), [null, ["Restart", "Stop", "Turn off", "Remove"]]],
    [of(row, "health", "fail"), ["Restart", ["Restart", "Stop", "Turn off", "Remove"]]],
    [of(row, "health", "warn"), [null, ["Restart", "Stop", "Turn off", "Remove"]]],
    [of(row, "no-login", "warn", { flag: false }), [null, ["Restart", "Stop", "Turn on", "Remove"]]],
    [of(moduleRow, "listening", "ok"), [null, ["Restart", "Stop"]]],
    [of(timer, "armed", "ok"), [null, ["Run now", "Turn off", "Remove", "(Run again)", "(Stop run)"]]],
    [of(timer, "last-run", "warn"), ["Run now", ["Run now", "Turn off", "Remove", "(Run again)", "(Stop run)"]]],
    [of(timer, "armed", "ok", { form: "module", module: "notes", id: "notes/sync" }),
     [null, ["Run now", "(Run again)", "(Stop run)"]]],
    [of(timer, "off", "off", { flag: false }), [null, ["Turn on", "Remove", "(Run now)"]]],
    [of(row, "missing", "fail"), [null, ["Turn off"]]],
    [of(row, "missing", "warn", { flag: false }), [null, ["Turn on"]]],
    [of(row, "missing", "fail", { form: "adopted" }), [null, ["Remove"]]],
    [of(moduleRow, "missing", "fail"), [null, []]],
    [of(timer, "not-armed", "fail"), [null, ["Turn on", "Remove"]]],
    [of(timer, "schedule-failed", "fail", { form: "module" }), [null, []]],
    [of(row, "removing", "warn"), [null, ["Remove"]]],
    [of(row, "elsewhere", "off"), [null, ["Remove"]]],
    [of(moduleRow, "no-supervisor", "off"), [null, []]],
    [of(moduleRow, "switched-off", "off"), [null, []]],
    [of(row, "unknown", "warn"), [null, ["Start", "Restart", "Stop", "Turn on", "Turn off", "Remove"]]],
    [of(timer, "new-word", "warn"), [null, ["Run now", "Run again", "Stop run", "Turn on", "Turn off", "Remove"]]],
    [of(moduleRow, null, "ok"), [null, ["Start", "Restart", "Stop"]]],
  ];
  for (const [r, expect] of cases) assert.deepEqual(names(actsOf(r)), expect, `${r.id} ${r.verdict.cause} ${r.verdict.state}`);
  // Turn on and Turn off are never a Moxi row's.
  for (const [r] of cases) {
    if (r.form === "module") assert.ok(!actsOf(r).foot.some((a) => a.body.act === "enable" || a.body.act === "disable"));
  }
});

test("each act's body for /api/act, and a run that spends usage says so", () => {
  const t = of(timer, "last-run", "warn", { launches_session: true });
  const { closed, foot } = actsOf(t);
  assert.equal(closed.label, "Run now · spends usage");
  assert.deepEqual(foot.map((a) => a.body), [
    { act: "run", id: "services/backup" }, { act: "disable", id: "services/backup" },
    { act: "remove", id: "services/backup" }, { act: "run", id: "services/backup", restart: true },
    { act: "stop", id: "services/backup" },
  ]);
  assert.equal(foot[3].label, "Run again · spends usage");
});

test("ui's shell offers Restart and no Stop", () => {
  const shell = of(moduleRow, "running", "ok", { id: "ui/shell", module: "ui", name: "shell" });
  assert.deepEqual(names(actsOf(shell)), [null, ["Restart"]]);
  const restart = actsOf(shell).foot[0];
  assert.deepEqual(phoneSheet(shell, restart, null), ["Restart shell.", SHELL_RESTART]);
});

test("the phone: no act with acts off; with them on, the desk's but Remove, through confirmed routes", () => {
  const r = of(row, "running", "ok");
  assert.deepEqual(phoneActs(r, false), { closed: null, foot: [] });
  assert.deepEqual(names(phoneActs(r, true)), [null, ["Restart", "Stop", "Turn off"]]);
  const t = of(timer, "armed", "ok");
  const routes = actsOf(t).foot.filter((a) => a.name !== "remove").map((a) => phoneRoute(a));
  assert.deepEqual(routes, [
    { path: "api/run", body: { id: "services/backup" } }, { path: "api/disable", body: { id: "services/backup" } },
    { path: "api/run", body: { id: "services/backup", restart: true } }, { path: "api/stop", body: { id: "services/backup" } },
  ]);
  const stop = actsOf(r).foot[1];
  assert.deepEqual(phoneRoute(stop, { ends: 3 }), { path: "api/stop", body: { id: "services/web", ends: 3 } });
  assert.deepEqual(phoneRoute(stop, { ends_unread: true }),
                   { path: "api/stop", body: { id: "services/web", ends_unread: true } });
  // Only a stop or restart carries what a confirm sends.
  const other = actsOf(r).foot.find((a) => a.body.act !== "stop" && a.body.act !== "restart");
  assert.deepEqual(phoneRoute(other, { ends: 3 }).body, { id: "services/web" });
  assert.deepEqual(phoneSheet(r, stop, null), ["Stop web."]);
  const usage = of(timer, "armed", "ok", { launches_session: true });
  assert.deepEqual(phoneSheet(usage, actsOf(usage).foot[0], null),
                   ["Run backup now? Its runs launch agent sessions, so this spends usage."]);
});

const SERVER = moduleRow({ id: "slots/server", module: "slots", name: "server", ends_slots: true,
                           verdict: verdict("ok", "running") });
const ends = (sessions, open = 0, jobs = 0) => ({ read: true, why: null, sessions, open, jobs });
const UNREAD_ENDS = { read: false, why: "slots couldn't be read: x", sessions: null, open: null, jobs: null };

test("the slots server's warning confirm: sessions, open slots and jobs, none, unread", () => {
  const [restart, stop] = actsOf(SERVER).foot;
  assert.equal(slotsConfirm(stop, ends([]), false), null);
  const three = ends([{ slot: 1, label: "fix the tests" }, { slot: 2, label: null }, { slot: 4, label: "docs" }], 2, 1);
  const found = slotsConfirm(stop, three, false);
  assert.equal(found.ask, "Stop the slots server and end 3 sessions?");
  assert.deepEqual(found.lines[0], { label: "Ends now", text: "fix the tests, slot 1, slot 2, docs, slot 4, 2 open slots, 1 job" });
  assert.deepEqual(found.lines.slice(1).map((l) => l.text), [
    "launching into a slot, until it is started again", "sessions outside the slots",
    "A working session is cut off mid-turn. This cannot be taken back."]);
  assert.equal(found.go, "Stop it and end 3 sessions");
  assert.equal(found.keep, "Keep it running");
  assert.equal(found.warn, true);
  assert.deepEqual(found.sends, { ends: 6 });
  const noSession = slotsConfirm(restart, ends([], 1, 2), false);
  assert.equal(noSession.go, "Restart it and end what runs in it");
  assert.equal(noSession.lines[0].text, "1 open slot, 2 jobs");
  assert.deepEqual(noSession.sends, { ends: 3 });
  assert.equal(slotsConfirm(stop, ends([{ slot: 1, label: "x" }]), true).lines[0].text, "1 session");
  // Unread: no count shown, may end sessions, in the warning colour; it sends its own
  // marker, never ends 0, which means nothing in use as read.
  const unread = slotsConfirm(stop, UNREAD_ENDS, false);
  assert.equal(unread.ask, UNREAD);
  assert.equal(unread.go, "Stop it anyway");
  assert.equal(unread.warn, true);
  assert.deepEqual(unread.sends, { ends_unread: true });
  assert.ok(!JSON.stringify(unread).match(/\b0 sessions\b/));
  assert.equal(slotsConfirm(restart, UNREAD_ENDS, false).go, "Restart it anyway");
  assert.equal(inUse(UNREAD_ENDS), null);
  assert.deepEqual([warned(null), warned(ends([])), warned(ends([], 1)), warned(UNREAD_ENDS)], [false, false, true, true]);
  assert.deepEqual(phoneSheet(SERVER, stop, three).slice(0, 2), ["Stop the slots server and end 3 sessions?",
                                                                  "Ends now: 3 sessions, 2 open slots, 1 job"]);
});

test("the usage confirm and Remove's", () => {
  const t = of(timer, "armed", "ok", { launches_session: true });
  assert.equal(usageConfirm(t, actsOf(t).foot[0]).ask, "Run backup now? Its runs launch agent sessions, so this spends usage.");
  assert.equal(usageConfirm(of(timer, "armed", "ok"), actsOf(of(timer, "armed", "ok")).foot[0]), null);
  const written = removeConfirm(of(row, "running", "ok", { cwd: "/home/me/docs", project: "atlas" }));
  assert.deepEqual(written.lines.map((l) => [l.label, l.text]), [
    ["Goes", "its entry and the unit Moxi wrote"], ["Stays", "everything in /home/me/docs, its past lines in the system log"],
    [null, "Moxi keeps no copy. To have it back, add it again with this command:"]]);
  assert.equal(written.command,
               "moxi services add web --cwd /home/me/docs --project atlas --doc 'serves the docs' -- /usr/bin/python3 -m http.server");
  const adopted = removeConfirm(of(row, "running", "ok", { form: "adopted", argv: null, unit: "web.service" }));
  assert.equal(adopted.command, null);
  assert.equal(adopted.lines[2].text, "Remove takes it off this list only.");
  assert.equal(removeConfirm(of(timer, "armed", "ok", { form: "adopted", platform: "win32" })).lines[0].text,
               "its entry and the small file beside it");
  assert.equal(addAgain(timer({ doc: null, timing: { every: null, at: ["09:00", "17:30"], days: ["mon"] },
                                health: ["/bin/sh", "-c", "curl -f x"], launches_session: true })),
               "moxi services add backup --timer --at 09:00,17:30 --days mon --health '/bin/sh -c '\\''curl -f x'\\''' --launches-session -- /usr/bin/python3 -m http.server");
  assert.equal(shellWord("it's"), "'it'\\''s'");
});

test("a job's states and a press's transport words", () => {
  const [start] = actsOf(of(row, "stopped", "warn")).foot;
  assert.equal(runningFact(start), "starting");
  assert.equal(jobSaid(start, "done"), null);
  assert.deepEqual(jobSaid(start, "failed"), { heavy: "Start did not hold.", again: "Start again", reread: false });
  assert.deepEqual(jobSaid(start, "refused"), { heavy: "Not done.", again: "Start again", reread: false });
  assert.deepEqual(jobSaid(start, "unknown"), { heavy: "It may have been done.", again: null, reread: true });
  const [, stop] = actsOf(of(row, "running", "ok")).foot;
  assert.equal(runningFact(stop), "stopping");
  assert.equal(jobSaid(stop, "failed").again, "Stop");
  assert.deepEqual(transportSaid(0, null), { text: "No answer from the shell.", reread: false });
  assert.deepEqual(transportSaid(502, null), { text: "Services' page server didn't answer; it may have been done.", reread: true });
  assert.deepEqual(transportSaid(504, null).reread, true);
  assert.deepEqual(transportSaid(409, "locked"), { text: "Another change holds the lock. Try again.", reread: false });
  assert.deepEqual(transportSaid(422, "Something is running in its slots now."),
                   { text: "Something is running in its slots now.", reread: false });
});
