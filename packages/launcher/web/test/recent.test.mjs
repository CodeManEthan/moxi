import assert from "node:assert/strict";
import test from "node:test";

import { placeTime, parseTargets } from "../src/prompt-model.ts";
import {
  againRun, cardAct, leftOutWords, parseRecent, parseRecents, placeWords, postureWords, presetWords, ranWhere, recentBody,
  recentQuery, runsWith, sameDefault,
} from "../src/recent-model.ts";

/** A catalog field as the page server sends one. */
const field = (name, extra = {}) => ({
  name, type: "string", default: "", values: null, doc: `About ${name}.`, hidden: false, posture: false,
  group: null, sub: null, label: null, danger: [], carry: true, modes: [], shown: true, typed: true, ...extra,
});

const FIELDS = [
  field("permission_mode", { type: "enum", default: "default", values: ["default", "plan", "bypassPermissions"],
                             posture: true, label: "Permission mode", danger: ["bypassPermissions"] }),
  field("allowed_tools", { type: "list", default: null, repeatable: true, posture: true, label: "Allowed tools" }),
  field("add_dir", { type: "list", default: null, repeatable: true, label: "Extra folders" }),
  field("warn_at", { label: "Warn at", shown: false }),
];

/** `/api/recent`'s answer for a request typing `values` (`[name, value]`, a list's value its items). */
const answerOf = ({ values = [], shows = {}, seat = null, preset = null, offPosture = false } = {}) => parseRecent({
  state: "up", detail: null, refused: null,
  request: { seat, project_preset: preset, off_posture: offPosture, leave_out: ["slot"],
             values: values.map(([name, value]) => ({ name, value: Array.isArray(value) ? null : value,
                                                      items: Array.isArray(value) ? value : null })) },
  shows: { harness: "claude", model: "opus", effort: "high", prompt: "Read the design and start the build.",
           cwd: "/home/alice/work/proj-a", project_key: "proj-a", off_posture: [], danger: [], not_offered: [], ...shows },
});

const REFUSED = parseRecent({ state: "up", detail: null, request: null, shows: null,
  refused: { code: "no-preset", row: null, problem: "no preset named proj-c.", hint: "pick another preset" } });

const SLOTS = parseTargets({ state: "up", new_slot: true, default: { target: "slot", slot: 2 },
  slots: [{ number: 1, state: "occupied", launchable: false }, { number: 2, state: "free", launchable: true }] });

test("the list reads each recent with its slot as a number, and a failed read as none", () => {
  const listed = parseRecents({ state: "up", detail: null, recents: [
    { request_id: "r3", at: "2026-11-10T18:40:02Z", head: "fable · low", target: "slot", slot: "1" },
    { request_id: "r2", at: "2026-11-10T08:20:00Z", head: "Read the design and start", target: "terminal", slot: null },
    { request_id: "", head: "no id" },
  ] });
  assert.deepEqual(listed, [
    { id: "r3", at: "2026-11-10T18:40:02Z", head: "fable · low", target: "slot", slot: 1 },
    { id: "r2", at: "2026-11-10T08:20:00Z", head: "Read the design and start", target: "terminal", slot: null },
  ]);
  assert.equal(parseRecents({ state: "error", detail: "it broke", recents: [] }), null);
  assert.equal(parseRecents(null), null);
});

test("a recent is drawn by its head as the route gives it and a time of day today, else the day", () => {
  const [recent] = parseRecents({ state: "up", recents: [
    { request_id: "r1", at: "2026-11-10T08:20:00Z", head: "  Run the visual regression  ", target: "headless", slot: null }] });
  assert.equal(recent.head, "Run the visual regression");
  const hour = () => "08:20";
  const day = () => "Nov 3";
  assert.equal(placeTime(recent.at, new Date("2026-11-10T12:00:00Z"), hour, day), "08:20");
  assert.equal(placeTime(recent.at, new Date("2026-11-14T12:00:00Z"), hour, day), "Nov 3");
});

test("where a recent ran is said by its target and its slot", () => {
  assert.equal(ranWhere({ target: "slot", slot: 1 }), "Ran in slot 1");
  assert.equal(ranWhere({ target: "slot", slot: null }), "Ran in a slot");
  assert.equal(ranWhere({ target: "headless", slot: null }), "Ran headless");
  assert.equal(ranWhere({ target: "terminal", slot: null }), "Ran in your terminal");
});

test("the route is read with the button's default, its slot only when the default names one", () => {
  assert.equal(recentQuery("r1", { target: "slot", slot: 2, named: false }), "request=r1&target=slot&slot=2");
  assert.equal(recentQuery("r1", { target: "slot", slot: null, named: false }), "request=r1&target=slot");
  assert.equal(recentQuery("r 1", { target: "terminal" }), "request=r+1&target=terminal");
  assert.deepEqual(againRun({ target: "slot", slot: 2, named: false }), { target: "slot", slot: 2, named: true });
  assert.deepEqual(againRun({ target: "slot", slot: null, named: false }), { target: "slot", slot: null, named: false });
  assert.ok(sameDefault({ target: "slot", slot: 2, named: false }, { target: "slot", slot: 2, named: true }));
  assert.ok(!sameDefault({ target: "slot", slot: 2, named: false }, { target: "slot", slot: 3, named: false }));
  assert.ok(!sameDefault({ target: "slot", slot: null, named: false }, { target: "terminal" }));
});

test("the click's body is the request with its values as a table and no filled rows", () => {
  const { request } = answerOf({ values: [["effort", "high"], ["add_dir", ["/work/a"]], ["target", "slot"], ["slot", "2"],
                                          ["permission_mode", "bypassPermissions"]],
                                 seat: "planner", offPosture: true });
  assert.deepEqual(recentBody(request), {
    seat: "planner", values: { effort: "high", add_dir: ["/work/a"], target: "slot", slot: "2",
                               permission_mode: "bypassPermissions" },
    leave_out: ["slot"], off_posture: true,
  });
  const plain = recentBody(answerOf({ preset: "proj-a" }).request);
  assert.deepEqual(plain, { values: {}, leave_out: ["slot"], project_preset: "proj-a" });
  assert.ok(!("filled" in plain) && !("off_posture" in plain));
});

test("the card names what runs, the preset, and the place only when it is not the line's", () => {
  const { request, shows } = answerOf({ preset: "proj-a" });
  assert.equal(runsWith(shows), "claude · opus · high");
  assert.equal(runsWith({ ...shows, model: null, effort: null }), "claude · default model");
  assert.equal(presetWords(request), "preset proj-a");
  assert.equal(presetWords({ ...request, project_preset: null }), "");
  const home = "/home/alice";
  assert.equal(placeWords(shows, { kind: "project", key: "proj-a" }, home), "");
  assert.equal(placeWords(shows, { kind: "project", key: "proj-b" }, home), "in proj-a");
  assert.equal(placeWords(shows, { kind: "folder", path: "~/work" }, home), "in proj-a");
  const loose = { ...shows, project_key: null, cwd: "/home/alice/work/x" };
  assert.equal(placeWords(loose, { kind: "folder", path: "~/work/x" }, home), "");
  assert.equal(placeWords(loose, { kind: "folder", path: "/home/alice/work/x/" }, home), "");
  assert.equal(placeWords(loose, { kind: "folder", path: "~" }, home), "in ~/work/x");
});

test("the permission rows off the defaults are named, a value that can do harm marked and first", () => {
  const { request, shows } = answerOf({
    values: [["allowed_tools", ["Bash"]], ["permission_mode", "bypassPermissions"]],
    shows: { off_posture: ["allowed_tools", "permission_mode"], danger: ["permission_mode"] } });
  assert.deepEqual(postureWords(shows, request, FIELDS, ""), {
    off: [{ text: "Permission mode bypassPermissions", danger: true }, { text: "Allowed tools Bash", danger: false }],
    own: [],
  });
  // A row the preset sets is named by its label: the request does not hold its value.
  const preset = answerOf({ shows: { off_posture: ["permission_mode"], danger: [] } });
  assert.deepEqual(postureWords(preset.shows, preset.request, FIELDS, "").off, [{ text: "Permission mode", danger: false }]);
  // A value that can do harm that is the user's own default is named on a line of its own.
  const own = answerOf({ values: [["permission_mode", "bypassPermissions"]], shows: { danger: ["permission_mode"] } });
  assert.deepEqual(postureWords(own.shows, own.request, FIELDS, ""),
                   { off: [], own: [{ text: "Permission mode bypassPermissions", danger: true }] });
  assert.deepEqual(postureWords(answerOf().shows, answerOf().request, FIELDS, ""), { off: [], own: [] });
});

test("a row this install no longer offers is named Left out by its label, with no module named", () => {
  const { shows } = answerOf({ shows: { not_offered: ["warn_at", "gone_row"] } });
  // A row the catalog no longer lists is named by its name.
  assert.equal(leftOutWords(shows, FIELDS), "Left out: Warn at, gone_row");
  assert.equal(leftOutWords(answerOf().shows, FIELDS), null);
});

test("the card's last line for each default the button can have", () => {
  const answer = answerOf();
  const inOne = { target: "slot", slot: 1 };
  assert.deepEqual(cardAct(answer, SLOTS.default, inOne, SLOTS),
                   { text: "Click to run again in slot 2", beside: "slot 1 is in use", runs: true });
  // The slot it ran in is free, or is the default's own: nothing beside.
  assert.deepEqual(cardAct(answer, SLOTS.default, { target: "slot", slot: 2 }, SLOTS),
                   { text: "Click to run again in slot 2", beside: "", runs: true });
  assert.deepEqual(cardAct(answer, SLOTS.default, { target: "headless", slot: null }, SLOTS).beside, "");
  assert.deepEqual(cardAct(answer, { target: "slot", slot: null, named: false }, inOne, SLOTS),
                   { text: "Click to run again in a new slot", beside: "", runs: true });
  assert.deepEqual(cardAct(answer, { target: "terminal" }, inOne, null),
                   { text: "Click to copy its command", beside: "", runs: true });
});

test("a recent that can't run again says why and its click does nothing", () => {
  assert.deepEqual(cardAct(REFUSED, SLOTS.default, { target: "slot", slot: 1 }, SLOTS),
                   { text: "Can't run again: no preset named proj-c. pick another preset", beside: "", runs: false });
  const failed = parseRecent({ state: "error", detail: "reading a recent launch failed", request: null, shows: null,
                               refused: null });
  assert.deepEqual(cardAct(failed, { target: "terminal" }, { target: "terminal", slot: null }, null),
                   { text: "Can't run again: reading a recent launch failed", beside: "", runs: false });
  assert.equal(parseRecent(null, "no answer from the shell").refused.problem, "no answer from the shell");
});
