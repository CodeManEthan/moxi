import assert from "node:assert/strict";
import test from "node:test";

import {
  addList, dateFormat, dateSample, keepShown, nameNote, nameQuery, parseName, PIECES, saveBody, saveSaid, tokens,
  withDate, withoutPiece, withPiece,
} from "../src/name-model.ts";

/** A catalog field as the page server sends one. */
const field = (name, extra = {}) => ({
  name, type: "string", default: "", values: null, doc: `About ${name}.`, hidden: false, posture: false,
  group: null, sub: null, label: null, danger: [], carry: true, modes: [], shown: true, typed: true, needs_absent: false,
  ...extra,
});

const SHIPPED = "<project>-<model>-<date:mmm-dd hh:mm>";
const CONTEXT = { seat: "", project: "proj-a", cwd: "", model: "opus", identity: "", scope: "", agent: "", harness: "claude" };

test("the add list offers every piece the template lacks on an install with comms and slots", () => {
  const fields = [field("identity"), field("scope"), field("slot")];
  assert.deepEqual(addList(fields, SHIPPED), ["agent", "identity", "scope", "instance", "time"]);
  assert.deepEqual(addList(fields, ""), [...PIECES]);
});

test("the add list leaves out the comms name and scope without comms, and the number without slots", () => {
  const noComms = [field("identity", { shown: false }), field("scope", { shown: false }), field("slot")];
  assert.deepEqual(addList(noComms, SHIPPED), ["agent", "instance", "time"]);
  const noSlots = [field("identity"), field("scope"), field("slot", { needs_absent: true, shown: false })];
  assert.deepEqual(addList(noSlots, SHIPPED), ["agent", "identity", "scope", "time"]);
  // A form that lacks the rows altogether is an install without either.
  assert.deepEqual(addList([], ""), ["project", "agent", "model", "date", "time"]);
});

test("Keep for is drawn only with projects and a project on the line", () => {
  assert.equal(keepShown(true, "proj-a"), true);
  assert.equal(keepShown(false, "proj-a"), false);
  assert.equal(keepShown(true, ""), false);
});

test("the read's query names only what the launch has, and an edited template with where it is kept", () => {
  assert.equal(nameQuery(CONTEXT), "project=proj-a&model=opus&harness=claude");
  const seat = { ...CONTEXT, seat: "planner", project: "", cwd: "/work/box-1", model: "" };
  assert.equal(nameQuery(seat), "seat=planner&cwd=%2Fwork%2Fbox-1&harness=claude");
  assert.equal(nameQuery(CONTEXT, { template: "<model> in <project>", keep: "project" }),
               "project=proj-a&model=opus&harness=claude&template=%3Cmodel%3E+in+%3Cproject%3E&keep=project");
  assert.equal(nameQuery(CONTEXT, { template: "<model>", keep: "global" }),
               "project=proj-a&model=opus&harness=claude&template=%3Cmodel%3E");
  // With no project on the line there is no project to keep it for.
  assert.equal(nameQuery({ ...CONTEXT, project: "" }, { template: "", keep: "project" }),
               "model=opus&harness=claude&template=");
});

test("the answer is read with its refusal, its save, and why a failed read gave nothing", () => {
  const up = { state: "up", detail: null, name: "opus in proj-a", template: "<model> in <project>", from: "project",
               refused: null, save: { key: "name.templates", value: "{proj-a = \"<model> in <project>\"}" } };
  assert.deepEqual(parseName(up, "x"), { name: "opus in proj-a", template: "<model> in <project>", from: "project",
                                         refused: null, save: up.save, failed: "" });
  const refused = parseName({ ...up, name: null, save: null,
                              refused: { code: "bad-template", row: "name", problem: "P", hint: "H" } }, "x");
  assert.deepEqual([refused.name, refused.refused, refused.save], [null, { problem: "P", hint: "H" }, null]);
  assert.equal(parseName({ state: "error", detail: "the log is busy" }, "x").failed, "the log is busy");
  assert.equal(parseName(null, "the launcher's page server answered 500").failed, "the launcher's page server answered 500");
});

test("the note says whose template it is and what the name follows", () => {
  const named = parseName({ state: "up", name: "proj-a-opus-Nov-08 12:45", template: SHIPPED, from: "project",
                            refused: null, save: null }, "");
  assert.equal(nameNote(named, "proj-a"),
               "From proj-a's template. It follows the project, the model and the clock; type over it to name this "
               + "launch yourself.");
  assert.equal(nameNote({ ...named, from: "global", template: "<agent>-<time>-<date>" }, "proj-a"),
               "From the template for every launch. It follows the seat and the clock; type over it to name this "
               + "launch yourself.");
  assert.equal(nameNote({ ...named, from: "global", template: "build" }, ""),
               "From the template for every launch. It is the same for every launch; type over it to name this "
               + "launch yourself.");
});

test("a piece is taken out with the text before it, or the first with the text after it", () => {
  assert.deepEqual(tokens(SHIPPED).map((token) => token.piece), ["project", "model", "date"]);
  assert.equal(withoutPiece(SHIPPED, 1), "<project>-<date:mmm-dd hh:mm>");
  assert.equal(withoutPiece(SHIPPED, 2), "<project>-<model>");
  assert.equal(withoutPiece(SHIPPED, 0), "<model>-<date:mmm-dd hh:mm>");
  assert.equal(withoutPiece("<model> in <project>", 0), "<project>");
  assert.equal(withoutPiece("run-<agent>", 0), "run-");
  assert.equal(withoutPiece(SHIPPED, 5), SHIPPED);
});

test("a piece is added at the end after a dash, the date with the shipped format", () => {
  assert.equal(withPiece("<project>", "agent"), "<project>-<agent>");
  assert.equal(withPiece("", "model"), "<model>");
  assert.equal(withPiece("<model>", "date"), "<model>-<date:mmm-dd hh:mm>");
});

test("the date's format is read and set on the first date piece, and shown as the name would show it", () => {
  assert.equal(dateFormat(SHIPPED), "mmm-dd hh:mm");
  assert.equal(dateFormat("<model>-<date>"), "");
  assert.equal(dateFormat("<model>-<time:hh>"), null);
  assert.equal(withDate(SHIPPED, "yyyy-mm-dd"), "<project>-<model>-<date:yyyy-mm-dd>");
  assert.equal(withDate("<date:yy>", ""), "<date>");
  const at = new Date(2026, 10, 8, 9, 5, 7);
  assert.equal(dateSample("mmm-dd hh:mm", at), "Nov-08 09:05");
  assert.equal(dateSample("", at), "20261108");
  assert.equal(dateSample("YY.MM.DD hh-mm-ss", at), "26.11.08 09-05-07");
  assert.equal(dateSample("%Y", at), "%Y");
});

test("the save is sent to the shell's settings route for the launcher, and a refusal is said in its words", () => {
  assert.deepEqual(saveBody({ key: "name.template", value: "<model>" }),
                   { module: "launcher", key: "name.template", value: "<model>" });
  assert.equal(saveSaid({ status: 200, data: { ok: true, lines: [], warning: null, fix: null } }), null);
  const locked = "launcher name.template is locked for this run of the shell, so it can't be changed from a page";
  assert.equal(saveSaid({ status: 422, data: { refused: locked } }), `Not saved: ${locked}`);
  assert.equal(saveSaid({ status: 409, data: { conflict: "settings file busy" } }), "Not saved: settings file busy");
  assert.equal(saveSaid({ status: 0, data: null }), "Not saved: no answer from the shell");
  assert.equal(saveSaid({ status: 500, data: null }), "Not saved: the shell answered 500");
});
