import assert from "node:assert/strict";
import test from "node:test";

import {
  addBody, addRefused, adoptKind, answerNow, emptyForm, fullPathHint, kindToAsk, moreLine, onOffSays, parseUnit,
  turnOnSays, unitRoute, unitSays, WRAPS_SAYS,
} from "../src/panel-model.ts";

/** `/api/unit`'s answer, with `fields` laid over a found service on systemd. */
const unit = (fields = {}) => ({
  found: true, kind: "daemon", asks_kind: false, enabled: true, active: true, wraps: false, refused: null, ...fields,
});

test("a written row starts off unless he turns it on", () => {
  assert.equal(emptyForm().enable, false);
  const form = { ...emptyForm(), name: "web", command: "/usr/bin/python3 -m http.server" };
  assert.equal(addBody(form).body.enable, false);
  assert.equal(addBody({ ...form, enable: true }).body.enable, true);
});

test("the add body: a written daemon, a timer every or at, more's fields", () => {
  const form = { ...emptyForm(), name: " web ", command: "/usr/bin/python3 -m http.server", cwd: "/srv", enable: true,
                 health: "/usr/bin/curl -f localhost", project: "alpha", launchesSession: true, doc: "docs" };
  assert.deepEqual(addBody(form), { body: { name: "web", kind: "daemon", command: "/usr/bin/python3 -m http.server",
    enable: true, cwd: "/srv", health: "/usr/bin/curl -f localhost", project: "alpha", launches_session: true, doc: "docs" } });
  const every = { ...emptyForm(), name: "backup", kind: "timer", command: "/usr/bin/true", every: "5m" };
  assert.deepEqual(addBody(every).body, { name: "backup", kind: "timer", command: "/usr/bin/true", enable: false,
                                          every: "5m" });
  const at = { ...every, schedule: "at", at: "09:00, 17:30", days: ["fri", "mon"] };
  assert.deepEqual(addBody(at).body, { name: "backup", kind: "timer", command: "/usr/bin/true", enable: false,
                                       at: ["09:00", "17:30"], days: ["mon", "fri"] });
});

test("the adopt body: the unit, on or off, and the kind the unit was read as", () => {
  const adopt = { ...emptyForm("adopt"), name: "syncthing", unit: "syncthing.service", adoptOn: "off",
                  command: "/left/from/the/other/way", enable: true };
  assert.deepEqual(addBody(adopt).body, { name: "syncthing", kind: "daemon", unit: "syncthing.service", enable: false });
  // Unread, the name's suffix says; read, the answer's kind does.
  const timer = { ...adopt, unit: "restic.timer", adoptOn: "on" };
  assert.equal(addBody(timer).body.kind, "timer");
  assert.equal(addBody(timer, unit({ kind: "timer" })).body.kind, "timer");
  assert.equal(addBody(timer).body.enable, true);
  // Where the system can't tell, the kind chosen goes.
  const asked = { ...adopt, unit: "com.example.agent", kind: "timer" };
  assert.equal(addBody(asked, unit({ asks_kind: true, kind: "timer" })).body.kind, "timer");
  assert.equal(adoptKind({ ...asked, kind: "daemon" }, unit({ asks_kind: true, kind: "daemon" })), "daemon");
});

test("the panel says what it still needs", () => {
  assert.match(addBody(emptyForm()).missing, /command/);
  assert.match(addBody({ ...emptyForm(), command: "/bin/true" }).missing, /name/);
  assert.match(addBody({ ...emptyForm(), name: "x", kind: "timer", command: "/bin/true" }).missing, /how often/);
  assert.match(addBody({ ...emptyForm(), name: "x", kind: "timer", command: "/bin/true", schedule: "at" }).missing, /times/);
  assert.match(addBody({ ...emptyForm("adopt"), name: "x" }).missing, /unit/);
  assert.match(addBody({ ...emptyForm("adopt"), unit: "a.service" }).missing, /name/);
  assert.match(addBody({ ...emptyForm("adopt"), name: "x", unit: "a.service" }).missing, /on or off/);
});

test("an add that wasn't taken says why, and a lost answer may have been taken", () => {
  assert.deepEqual(addRefused(422, "x is not a full path"), { tone: "error", lines: ["x is not a full path"] });
  assert.equal(addRefused(409, null).lines[0], "services is busy; try again.");
  assert.equal(addRefused(0, null).tone, "warn");
  assert.match(addRefused(504, null).lines[0], /may have been taken/);
});

test("the command's hint names the command that prints the full path of the word typed", () => {
  assert.equal(fullPathHint("").command, "command -v NAME");
  assert.equal(fullPathHint("  npx vite --port 5174").command, "command -v npx");
  assert.equal(fullPathHint("/usr/bin/npx vite").command, "command -v npx");
  assert.equal(fullPathHint("x").text, "The first word is a full path.");
});

test("Turn it on now and More say what they hold, by kind and way", () => {
  assert.equal(turnOnSays("daemon"), "Starts it now and at every login. Left off, it waits for Start.");
  assert.match(turnOnSays("timer"), /schedule/);
  assert.equal(moreLine("add"), "folder · health check · 2 more");
  assert.equal(moreLine("adopt"), "health check · 2 more");
});

test("/api/unit's answer is read, and the route asks a kind only where told to", () => {
  assert.deepEqual(parseUnit(unit()), unit());
  assert.deepEqual(parseUnit(unit({ found: null, kind: null, enabled: null, active: null })),
                   unit({ found: null, kind: null, enabled: null, active: null }));
  assert.equal(parseUnit({ ...unit(), wraps: null }), null);
  assert.equal(parseUnit({ ...unit(), kind: "socket" }), null);
  assert.equal(unitRoute(" a b.service ", null), "api/unit?ref=a%20b.service");
  assert.equal(unitRoute("com.x", "timer"), "api/unit?ref=com.x&kind=timer");
  const form = { ...emptyForm("adopt"), kind: "timer" };
  assert.equal(kindToAsk(form, false), null);
  assert.equal(kindToAsk(form, true), "timer");
});

test("what the panel says of the unit: found and what, missing, refused, unread", () => {
  assert.deepEqual(unitSays(unit({ kind: "timer" })), {
    line: "Found: a timer of your own. Moxi lists it and acts on it; its file is never rewritten.", refused: false });
  assert.match(unitSays(unit()).line, /^Found: a service of your own\./);
  assert.equal(unitSays(unit({ found: false })).line, "Your system has no unit by that name.");
  assert.deepEqual(unitSays(unit({ found: null, refused: "x is in moxi's own unit namespace" })),
                   { line: "x is in moxi's own unit namespace", refused: true });
  assert.match(unitSays(unit({ found: null })).line, /couldn't be read/);
  assert.equal(onOffSays(unit()), "Your system says it is on. Say which is right, so the list starts out true.");
  assert.equal(onOffSays(unit({ enabled: false })), "Your system says it is off. Say which is right, so the list starts out true.");
  assert.equal(onOffSays(unit({ enabled: null })), "Say which is right, so the list starts out true.");
  assert.equal(onOffSays(null), "Say which is right, so the list starts out true.");
  assert.match(WRAPS_SAYS, /^Moxi adds one small file beside the service this timer starts/);
});

test("only the answer for what the field holds now is drawn", () => {
  const form = { ...emptyForm("adopt"), unit: "a.service" };
  const asked = { unit: "a.service", kind: null, answer: unit() };
  assert.deepEqual(answerNow(asked, form), unit());
  assert.equal(answerNow(asked, { ...form, unit: "a.servic" }), null);
  assert.equal(answerNow(null, form), null);
  // Where the system asks the kind, an answer read as the other kind is old.
  const launchd = { unit: "a.service", kind: "timer", answer: unit({ asks_kind: true, kind: "timer" }) };
  assert.equal(answerNow(launchd, { ...form, kind: "daemon" }), null);
  assert.deepEqual(answerNow(launchd, { ...form, kind: "timer" }), launchd.answer);
});
