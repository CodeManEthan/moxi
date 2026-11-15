import assert from "node:assert/strict";
import test from "node:test";

import { parseSendAnswer, parseTakeAnswer, SendBox, TEXT_MAX_CHARS, waitingIn } from "../src/box-model.ts";

const when = (at) => new Date(at * 1000).toISOString().slice(11, 16);
const at = (hhmm) => Date.parse(`2026-11-12T${hhmm}:00Z`) / 1000;

const answer = (outcome, delivery = "live", fields = {}) => ({
  outcome, message_id: outcome === "sent" ? "7301234567890123456" : null,
  recipients: delivery === null ? [] : [{ qualified: "reviewer", delivery, cold_at: delivery === "held-cold" ? at("18:35") : null }],
  refusal: null, ...fields,
});
const ok = (data) => ({ status: 200, data });

/** A stub of the page's `post`: answers in order, or holds each until `release`. */
function poster(...answers) {
  const calls = [];
  const held = [];
  const post = (path, body) => {
    calls.push({ path, body });
    const next = answers.length > 0 ? answers.shift() : null;
    if (next !== null) return Promise.resolve(next);
    return new Promise((done) => held.push(done));
  };
  return { post, calls, release: (result) => held.shift()(result) };
}

function boxOf(stub, { reply = null, hasCache = true, to = "reviewer" } = {}) {
  const changes = [];
  const acted = [];
  const box = new SendBox(to, reply, {
    post: stub.post, onChange: () => changes.push(1), onActed: (act) => acted.push(act), now: () => at("19:11"), hasCache,
  });
  return { box, changes, acted };
}

const flush = () => new Promise((done) => setImmediate(done));

test("the answers read: a check's and a take-back's, and what isn't one", () => {
  assert.deepEqual(parseSendAnswer(answer("would-send", "held-cold")), answer("would-send", "held-cold"));
  assert.deepEqual(parseSendAnswer({ ...answer("refused", null), refusal: { code: "too-long" } }).refusal,
                   { code: "too-long" });
  assert.equal(parseSendAnswer({ ...answer("sent"), message_id: 7301234567890123 }), null);
  assert.equal(parseSendAnswer({ ...answer("sent"), refusal: "too-long" }), null);
  assert.equal(parseSendAnswer({ outcome: "sent", message_id: null, recipients: [{ qualified: "x" }], refusal: null }), null);
  assert.equal(parseSendAnswer(null), null);
  const taken = { outcome: "deleted", withdrawn: ["reviewer"], delivered: [], expired: [], refusal: null };
  assert.deepEqual(parseTakeAnswer(taken), taken);
  assert.equal(parseTakeAnswer({ ...taken, withdrawn: [1] }), null);
  assert.equal(parseTakeAnswer({ ...taken, refusal: {} }), null);
});

test("the box checks as it opens, with back and forth and nothing held", async () => {
  const stub = poster(ok(answer("would-send", "live")));
  const { box } = boxOf(stub);
  await box.check();
  assert.deepEqual(stub.calls, [{ path: "api/check", body: { to: "reviewer", text: "", posture: "open", held: false } }]);
  const view = box.view({ when });
  assert.equal(view.placeholder, "Message reviewer");
  assert.equal(view.posture.word, "back and forth");
  assert.deepEqual(view.posture.menu.map((one) => [one.label, one.current]), [
    ["No reply", false], ["Only if it has something to add", false], ["One reply", false], ["Back and forth", true]]);
  assert.equal(view.posture.menu[0].says, "It reads this and owes nothing. A reply to it is refused.");
  assert.deepEqual(view.line, { key: "If sent now", heavy: null, text: "Delivered. reviewer reads it at its next turn boundary.",
                                dim: null, again: false, reread: false, keep: false });
  assert.equal(view.quote, null);
});

test("Send takes the accent only with text and a clean check, and the text is capped", async () => {
  const stub = poster(ok(answer("would-send")), ok(answer("cache-unread", null)));
  const { box } = boxOf(stub);
  assert.equal(box.view({ when }).ready, false);
  box.setText("Review from the branch head.");
  assert.equal(box.view({ when }).ready, false, "no check has answered");
  await box.check();
  assert.equal(box.view({ when }).ready, true);
  box.setText("   ");
  assert.equal(box.view({ when }).ready, false, "blank text");
  box.setText("x");
  await box.check();
  const view = box.view({ when });
  assert.equal(view.ready, false, "a refused check");
  assert.equal(view.line.heavy, "Refused: nothing would be sent.");
  assert.equal(view.line.again, true);
  box.setText("y".repeat(TEXT_MAX_CHARS + 5));
  assert.equal(box.text.length, 16_000);
});

test("one check at a time: those asked while one is out make one more, and the last is kept", async () => {
  const stub = poster();
  const { box } = boxOf(stub);
  const first = box.check();
  box.setPosture("once");
  box.setPosture("optional");
  assert.equal(stub.calls.length, 1);
  assert.equal(box.checking, true);
  stub.release(ok(answer("would-send", "held-cold")));
  await flush();
  assert.equal(stub.calls.length, 2);
  assert.equal(stub.calls[1].body.posture, "optional");
  stub.release(ok(answer("would-send", "live")));
  await first;
  assert.equal(stub.calls.length, 2);
  assert.equal(box.checking, false);
  assert.match(box.view({ when }).line.text, /^Delivered/);
});

test("the posture and Deliver check again when they change, and only then", async () => {
  const stub = poster(ok(answer("would-send")), ok(answer("would-send")), ok(answer("would-send", "held-flag")));
  const { box } = boxOf(stub);
  box.setPosture("open");
  box.setHeld(false);
  assert.equal(stub.calls.length, 0);
  box.setPosture("once");
  await flush();
  box.setHeld(true);
  await flush();
  assert.deepEqual(stub.calls.map((call) => [call.body.posture, call.body.held]), [["once", false], ["once", true]]);
  const view = box.view({ when });
  assert.equal(view.deliver.word, "at its next turn");
  assert.deepEqual(view.deliver.menu.map((one) => [one.label, one.current]), [["now", false], ["at its next turn", true]]);
});

test("a check held for a cold cache shows Deliver at its next turn with no menu", async () => {
  const stub = poster(ok(answer("would-send", "held-cold")));
  const { box } = boxOf(stub);
  await box.check();
  const view = box.view({ when });
  assert.deepEqual(view.deliver, { word: "at its next turn", menu: null });
  assert.equal(view.line.text, "Held until reviewer's next turn. Its cache went cold at 18:35, and nothing wakes it for "
    + "mail. It reads this when you next type into it, or at a session start, /clear or compaction.");
});

test("without cache: no Deliver, and nothing is held", async () => {
  const stub = poster(ok(answer("would-send")), ok(answer("would-send")));
  const { box } = boxOf(stub, { hasCache: false });
  box.setHeld(true);
  await box.check();
  assert.equal(stub.calls.length, 1);
  assert.equal(stub.calls[0].body.held, false);
  assert.equal(box.view({ when }).deliver, null);
});

test("a box set to answer quotes the ask, wants no reply, and Message clears both", async () => {
  const owed = { message_id: "7301234567890000001", author: "planner@atlas", at: at("19:02"), text: "Ship it?" };
  const stub = poster(ok(answer("would-send")), ok(answer("would-send")));
  const { box } = boxOf(stub, { reply: owed, to: "planner@atlas" });
  await box.check();
  assert.deepEqual(stub.calls[0].body, { to: "planner@atlas", text: "", posture: "none", held: false,
                                         reply_to: "7301234567890000001" });
  assert.deepEqual(box.view({ when }).quote, { lead: "Answering planner's 19:02 message:", text: "Ship it?" });
  assert.equal(box.view({ when }).posture.word, "no reply");
  box.answer(null);
  await flush();
  assert.equal(box.view({ when }).quote, null);
  assert.equal(box.posture, "open");
  assert.equal("reply_to" in stub.calls[1].body, false);
});

test("Send is pressed once; sent empties the box and says how it went, then how many wait", async () => {
  const owed = { message_id: "7301234567890000001", author: "reviewer", at: at("19:02"), text: "Ready?" };
  const stub = poster(ok(answer("would-send", "held-cold")));
  const { box, acted } = boxOf(stub, { reply: owed });
  await box.check();
  box.setText("Start with the cursor encoding commit.");
  const before = { state: "up", detail: null, messages: [], more: 0 };
  const sending = box.send(before);
  void box.send(before);
  assert.equal(box.view({ when }).sending, true);
  assert.equal(box.view({ when }).ready, false);
  box.setText("changed while out");
  assert.equal(box.text, "Start with the cursor encoding commit.");
  stub.release(ok(answer("sent", "held-cold")));
  await sending;
  assert.equal(stub.calls.filter((call) => call.path === "api/send").length, 1);
  assert.equal(stub.calls[1].body.reply_to, "7301234567890000001");
  assert.deepEqual(acted, ["send"]);
  assert.equal(box.text, "");
  assert.equal(box.reply, null);
  let line = box.view({ when, queue: before }).line;
  assert.equal(line.key, "Sent 19:11");
  assert.match(line.text, /^Held until reviewer's next turn\./);
  assert.equal(line.dim, "You can take a message back until reviewer has read it.");
  const row = (state) => ({ qualified: "reviewer", state, held: false, takeable: true });
  const msg = (id, state) => ({ message_id: id, at: at("18:50"), author: "operator", author_tier: "operator", to: ["reviewer"],
                                posture: "open", reply_to: null, text: "t", withdrawn: false, rows: [row(state)] });
  const after = { state: "up", detail: null, messages: [msg("1", "waiting"), msg("2", "handed"), msg("3", "waiting")], more: 0 };
  line = box.view({ when, queue: after }).line;
  assert.equal(line.dim, "2 messages now wait for it. You can take a message back until reviewer has read it.");
  assert.equal(box.view({ when, queue: after }).ready, false, "the box is empty again");
});

test("a Send refused keeps the text; one with no answer may have been sent and reads again", async () => {
  const stub = poster(ok(answer("would-send")), ok({ ...answer("refused", null), refusal: { code: "too-long" } }),
                      ok(answer("would-send")), { status: 502, data: null });
  const { box, acted } = boxOf(stub);
  await box.check();
  box.setText("long");
  await box.send(null);
  let line = box.view({ when }).line;
  assert.equal(line.heavy, "Not sent.");
  assert.equal(line.text, "With its address line it is over the 16,000-character cap on one message.");
  assert.equal(box.text, "long");
  await box.check();
  await box.send(null);
  line = box.view({ when }).line;
  assert.deepEqual([line.text, line.reread, line.keep], ["It may have been sent.", true, true]);
  assert.equal(box.text, "long");
  assert.deepEqual(acted, ["send", "send"]);
});

test("a check with no readable answer says so and offers Check again", async () => {
  const stub = poster({ status: 0, data: null }, ok({ outcome: 3 }));
  const { box } = boxOf(stub);
  await box.check();
  let line = box.view({ when }).line;
  assert.deepEqual([line.key, line.text, line.again], ["If sent now", "No answer from the shell. Check again.", true]);
  await box.check();
  line = box.view({ when }).line;
  assert.equal(line.text, "The shell answered 200.");
  assert.equal(box.view({ when }).ready, false);
});

test("Take back is pressed once per message, and its line says how it went", async () => {
  const stub = poster();
  const { box, acted } = boxOf(stub);
  const taking = box.takeBack("7301234567890000009");
  void box.takeBack("7301234567890000009");
  assert.equal(box.taking.has("7301234567890000009"), true);
  assert.equal(stub.calls.length, 1);
  assert.deepEqual(stub.calls[0], { path: "api/take-back", body: { message_id: "7301234567890000009" } });
  stub.release(ok({ outcome: "deleted", withdrawn: ["reviewer"], delivered: [], expired: [], refusal: null }));
  await taking;
  assert.equal(box.taking.size, 0);
  assert.equal(box.view({ when }).taken.text, "Taken back.");
  assert.deepEqual(acted, ["take-back"]);
  const again = box.takeBack("7301234567890000010");
  stub.release({ status: 504, data: null });
  await again;
  assert.deepEqual([box.view({ when }).taken.text, box.view({ when }).taken.reread], ["It may have been taken back.", true]);
});

test("a closed box drops a late answer", async () => {
  const stub = poster();
  const { box, changes } = boxOf(stub);
  const checking = box.check();
  box.close();
  const seen = changes.length;
  stub.release(ok(answer("would-send")));
  await checking;
  assert.equal(box.checked, null);
  assert.equal(changes.length, seen);
});

test("how many wait counts the address's waiting rows and those past the cap", () => {
  const msg = (state, qualified = "reviewer") => ({ rows: [{ qualified, state, held: false, takeable: false }] });
  assert.equal(waitingIn({ state: "up", messages: [msg("waiting"), msg("handed")], more: 2 }, "reviewer"), 3);
  assert.equal(waitingIn({ state: "error", messages: [], more: 0 }, "reviewer"), null);
  assert.equal(waitingIn(null, "reviewer"), null);
});
