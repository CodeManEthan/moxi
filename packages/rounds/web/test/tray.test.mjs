import assert from "node:assert/strict";
import test from "node:test";

import {
  ACT_ID, bodyOf, chipOf, correctionFound, draftOf, EACH, isAnswerable, kindWords, mintId, noLeadWords, readBackOf, receiptLines, RULE_LINE,
  RULE_LINE_ALONE, sendable, sendWords, Tray, trayKey,
} from "../src/tray.ts";

const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const TODAY = "2026-11-21";
const AT = "2026-11-21T11:40:00Z";

let n = 0;
const mint = () => `a${String(n++).padStart(42, "0")}`;
const LEADS = { "kestrel/billing-export": "atlas", "kestrel/ledger": null };
const leadOf = (holder) => LEADS[holder];
const each = { comms: true, to: EACH, leadOf };

/** A storage in memory, as the browser's `localStorage` behaves. */
function memory() {
  const kept = new Map();
  return { getItem: (key) => kept.get(key) ?? null, setItem: (key, value) => kept.set(key, String(value)), kept };
}

const draft = (kind, more = {}, holder = "kestrel/billing-export", item = "Q1") =>
  draftOf(holder, item, kind, more, mint);

test("an act id is 43 characters of the URL-safe alphabet", () => {
  const id = mintId();
  assert.match(id, ACT_ID);
  assert.notEqual(mintId(), id);
  assert.equal(mintId((bytes) => bytes.fill(255)), "_".repeat(42) + "8");
});

test("each kind's chip says who records it", () => {
  for (const kind of ["pick", "accept", "reject", "read-back", "done", "park", "unpark"]) {
    assert.equal(chipOf(draft(kind), each), "page records it on Send");
  }
  assert.equal(chipOf(draft("words", { text: "the second" }), each), "atlas words and records it");
  assert.equal(chipOf(draft("correct", { text: "no" }), each), "atlas words and records it");
  assert.equal(chipOf(draft("explain"), each), "goes to atlas; nothing to record");
  assert.equal(chipOf(draft("words", { text: "x" }), { ...each, to: "builder-1" }), "builder-1 words and records it");
});

test("a draft with no lead under each says to pick an agent, and is left out of Send", () => {
  const none = draft("pick", { chose: "a" }, "kestrel/ledger");
  assert.equal(chipOf(none, each), noLeadWords("kestrel/ledger"));
  assert.equal(chipOf(none, each), "No lead for kestrel/ledger: pick an agent to send it to");
  const other = draft("pick", { chose: "b" });
  assert.deepEqual(sendable([none, other], each).map((one) => one.act), [other.act]);
  assert.equal(chipOf(none, { ...each, to: "builder-1" }), "page records it on Send");
  assert.equal(sendable([none, other], { ...each, to: "builder-1" }).length, 2);
});

test("a project's draft whose lead the page can't tell goes, for the server to find", () => {
  const project = draft("pick", { chose: "a" }, "kestrel", "P1");
  assert.equal(chipOf(project, each), "page records it on Send");
  assert.equal(sendable([project], each).length, 1);
});

test("a recorded draft not told says so, with Send again", () => {
  const one = { ...draft("pick", { chose: "a" }), receipt: { act: "x", holder: "h", item: "Q1", kind: "pick",
                                                            state: "recorded-not-told", not_told: "no daemon" } };
  assert.equal(chipOf(one, each), "recorded, not told: Send again");
  assert.equal(chipOf(one, { comms: false, to: EACH, leadOf }), "recorded, not told: Send again");
});

test("with no comms the page's kinds say the page records them and own words show the command", () => {
  const alone = { comms: false, to: EACH, leadOf };
  assert.equal(chipOf(draft("park"), alone), "page records it");
  assert.equal(chipOf(draft("pick", { chose: "a" }, "kestrel/ledger"), alone), "page records it");
  assert.equal(chipOf(draft("words", { text: 'say "b"' }), alone),
               'moxi rounds rule kestrel/billing-export Q1 --text "say \\"b\\"" --record PTR');
  const mixed = [draft("words", { text: "x" }), draft("park"), draft("pick", { chose: "a" }, "kestrel/ledger")];
  assert.equal(sendable(mixed, alone).length, 2);
  assert.equal(sendWords(mixed, alone), "Record 2 answers");
  assert.equal(sendWords(mixed, each), "Send");
});

test("the rule line names what the page records and what goes to the agent", () => {
  assert.match(RULE_LINE, /^On Send the page records picks, decisions, read-backs, done jobs, parks and unparks at once\./);
  assert.match(RULE_LINE, /Your own words and explain requests go to the agent to word and record\.$/);
  assert.match(RULE_LINE_ALONE, /command shown/);
});

test("the body holds the drafts as lists of one length and the To choice", () => {
  const a = draft("pick", { chose: "b" });
  const b = draft("words", { text: "  mine  " });
  assert.deepEqual(bodyOf([a, b], EACH), {
    act: [a.act, b.act], holder: [a.holder, b.holder], item: ["Q1", "Q1"], kind: ["pick", "words"],
    chose: ["b", ""], text: ["", "mine"], tell: ["", ""], to: "each",
  });
});

test("the tray keeps its drafts across a reload, one per page server", () => {
  const store = memory();
  const tray = new Tray(store, trayKey("http://127.0.0.1:8800"));
  const a = draft("pick", { chose: "b", about: "Which?" });
  tray.add(a);
  tray.add(draft("park", {}, "kestrel/billing-export", "Q2"));
  const again = new Tray(store, trayKey("http://127.0.0.1:8800"));
  assert.deepEqual(again.drafts.map((one) => [one.act, one.kind, one.chose, one.about]),
                   [[a.act, "pick", "b", "Which?"], [tray.drafts[1].act, "park", null, ""]]);
  assert.equal(new Tray(store, trayKey("http://127.0.0.1:9900")).drafts.length, 0);
});

test("a second answer to one item replaces the draft never sent", () => {
  const tray = new Tray(memory(), "k");
  tray.add(draft("pick", { chose: "a" }));
  const words = draft("words", { text: "neither" });
  tray.add(words);
  assert.deepEqual(tray.drafts.map((one) => one.act), [words.act]);
});

test("a tray whose storage can't be read or written still holds its drafts", () => {
  const broken = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("full"); } };
  const tray = new Tray(broken, "k");
  tray.add(draft("park"));
  assert.equal(tray.drafts.length, 1);
  const bad = memory();
  bad.setItem("k", "{not json");
  assert.equal(new Tray(bad, "k").drafts.length, 0);
  bad.setItem("k", JSON.stringify({ drafts: [{ act: "short", holder: "h", item: "Q1", kind: "pick" }] }));
  assert.equal(new Tray(bad, "k").drafts.length, 0);
});

test("a send's answers move recorded and sent drafts to Sent and keep the rest, across a reload", () => {
  const store = memory();
  const tray = new Tray(store, "k");
  const pick = draft("pick", { chose: "b" });
  const words = draft("words", { text: "mine" }, "kestrel/billing-export", "Q2");
  const kept = draft("park", {}, "kestrel/ledger", "Q3");
  const refused = draft("done", {}, "kestrel/billing-export", "B1.W1");
  const told = draft("park", {}, "kestrel/billing-export", "Q4");
  [pick, words, kept, refused, told].forEach((one) => tray.add(one));
  const base = (one) => ({ act: one.act, holder: one.holder, item: one.item, kind: one.kind });
  tray.take([
    { ...base(pick), state: "recorded", ruling: { id: "C4", text: "b" } },
    { ...base(words), state: "sent", message: { to: "atlas", at: AT, id: "m1", delivery: "delivered" } },
    { ...base(kept), state: "kept", reason: noLeadWords("kestrel/ledger") },
    { ...base(refused), state: "refused", reason: "no wait B1.W1" },
    { ...base(told), state: "recorded-not-told", not_told: "comms is down" },
  ], [{ to: "atlas", at: AT, id: "m1", delivery: "delivered", refused: null }]);
  const again = new Tray(store, "k");
  assert.deepEqual(again.sent.map((one) => one.act), [pick.act, words.act]);
  assert.deepEqual(again.drafts.map((one) => [one.act, one.receipt.state]),
                   [[kept.act, "kept"], [refused.act, "refused"], [told.act, "recorded-not-told"]]);
  assert.equal(again.value.messages.length, 1);
  assert.equal(again.of("kestrel/billing-export", "Q1").act, pick.act);
  again.clearSent();
  assert.equal(new Tray(store, "k").sent.length, 0);
});

test("Send again gives a not-told draft a tell id, and its taken message moves it to Sent", () => {
  const tray = new Tray(memory(), "k");
  const park = draft("park");
  tray.add(park);
  const base = { act: park.act, holder: park.holder, item: "Q1", kind: "park" };
  tray.take([{ ...base, state: "recorded-not-told", record: "page:rounds/x@t", ruling: { id: "C9", text: "park:Q1" },
               not_told: "down" }]);
  const [going] = tray.toSend([park.act], mint);
  assert.match(going.tell, /^a/);
  assert.equal(tray.toSend([park.act], mint)[0].tell, going.tell);
  tray.take([{ ...base, tell: going.tell, state: "refused", reason: "comms did not send it: still down" }]);
  assert.equal(tray.drafts[0].receipt.state, "recorded-not-told");
  assert.equal(tray.drafts[0].tell, null);
  const [next] = tray.toSend([park.act], mint);
  tray.take([{ ...base, tell: next.tell, state: "recorded", message: { to: "atlas", at: AT, id: "m2", delivery: "d" } }]);
  assert.equal(tray.drafts.length, 0);
  assert.equal(tray.sent[0].receipt.record, "page:rounds/x@t");
  assert.equal(tray.sent[0].receipt.message.id, "m2");
});

test("a question the page parked offers Unpark, and one open again its form, whatever the tray holds", () => {
  const tray = new Tray(memory(), "k");
  const park = draft("park");
  tray.add(park);
  const base = { act: park.act, holder: park.holder, item: "Q1", kind: "park" };
  tray.take([{ ...base, state: "recorded-not-told", ruling: { id: "C9", text: "park:Q1" }, not_told: "down" }]);
  const row = tray.of(park.holder, "Q1");
  assert.equal(row.receipt.state, "recorded-not-told");
  // Read before the park shows, the record still says open: no form until it is read again.
  assert.equal(isAnswerable("question", "open", row, { fresh: false }), false);
  assert.equal(isAnswerable("question", "parked", row, { fresh: true }), true);
  const unpark = draft("unpark");
  tray.add(unpark);
  tray.take([{ ...base, act: unpark.act, kind: "unpark", state: "recorded", ruling: { id: "C10", text: "revoke:C9" } }]);
  assert.equal(tray.of(park.holder, "Q1").act, park.act);
  assert.equal(isAnswerable("question", "open", tray.of(park.holder, "Q1"), { fresh: true }), true);
  assert.equal(isAnswerable("question", "open", tray.sent[0], { fresh: true }), true);
  // Ruled, it offers nothing; a question with no row offers its form.
  assert.equal(isAnswerable("question", "ruled", tray.sent[0], { fresh: true }), false);
  assert.equal(isAnswerable("question", "open", null, { fresh: true }), true);
});

test("an item other than a question offers its form until an answer of its did something", () => {
  const accept = draft("accept", {}, "kestrel/billing-export", "B1.M1");
  const as = (state) => ({ ...accept, receipt: { act: accept.act, holder: accept.holder, item: "B1.M1", kind: "accept",
                                                  state } });
  assert.equal(isAnswerable("decision", "open", null, { fresh: true }), true);
  assert.equal(isAnswerable("decision", "open", accept, { fresh: true }), true);
  for (const state of ["refused", "kept"]) assert.equal(isAnswerable("decision", "open", as(state), { fresh: true }), true);
  for (const state of ["recorded", "recorded-not-told"]) {
    assert.equal(isAnswerable("decision", "open", as(state), { fresh: true }), false);
  }
});

test("a page-recorded act's receipt reads its record, its telling and what rounds holds", () => {
  const pick = { ...draft("pick", { chose: "b" }), receipt: {
    act: "x", holder: "kestrel/billing-export", item: "Q1", kind: "pick", state: "recorded",
    record: "page:rounds/x@2026-11-21T11:40:00Z", at: AT, ruling: { id: "C4", text: "Keep the old export" },
    message: { to: "atlas", at: AT, id: "m1", delivery: "delivered" }, freed: ["B1"], left: 2 } };
  assert.deepEqual(receiptLines(pick, { today: TODAY, clock: UTC }).map((one) => one.text), [
    "Pick b", "Recorded by this page at 11:40", "page:rounds/x@2026-11-21T11:40:00Z", "Told atlas at 11:40, comms m1",
    "Recorded as C4: Keep the old export", "B1 can start",
  ]);
  assert.equal(receiptLines(pick, { today: TODAY, clock: UTC })[4].offer, "read-back");
  assert.equal(readBackOf(pick), "C4");
  const done = { ...draft("done", { text: "ran it" }, "kestrel/billing-export", "B1.W1"), receipt: {
    act: "y", holder: "h", item: "B1.W1", kind: "done", state: "recorded-not-told", at: AT, done: "B1.W1",
    closed: true, not_told: "comms is down" } };
  const lines = receiptLines(done, { today: TODAY, clock: UTC });
  assert.deepEqual(lines.map((one) => one.text), [
    'I\'ve done it: "ran it"', "Recorded by this page at 11:40", "Not told: comms is down", "B1.W1 marked done",
    "The round closed.",
  ]);
  assert.equal(lines[2].offer, "send-again");
  const park = { ...draft("park"), receipt: { act: "z", holder: "h", item: "Q1", kind: "park", state: "recorded",
                                              at: AT, left: 1 } };
  assert.deepEqual(receiptLines(park, { today: TODAY, clock: UTC }).slice(2).map((one) => one.text),
                   ["Q1 parked", "1 question left"]);
});

test("a refused or kept draft's receipt says why, and the fix the lead can run", () => {
  const busy = { ...draft("pick", { chose: "a" }), receipt: { act: "x", holder: "h", item: "Q1", kind: "pick",
    state: "refused", busy: true, reason: "the round's lock is held", fix: "moxi rounds rule h Q1 --chose a" } };
  assert.deepEqual(receiptLines(busy, { today: TODAY, clock: UTC }).map((one) => one.text), [
    "The round is busy: the round's lock is held", "The lead can run:", "moxi rounds rule h Q1 --chose a"]);
  const kept = { ...draft("park"), receipt: { act: "x", holder: "h", item: "Q1", kind: "park", state: "kept",
                                              reason: noLeadWords("h") } };
  assert.deepEqual(receiptLines(kept, { today: TODAY, clock: UTC }).map((one) => one.text), [noLeadWords("h")]);
});

test("a sent own-words draft reads recorded once a ruling with the message's pointer appears", () => {
  const sent = { ...draft("words", { text: "the second, but later" }), receipt: {
    act: "x", holder: "h", item: "Q1", kind: "words", state: "sent", command: "moxi rounds rule h Q1 --text x --record comms:m1",
    message: { to: "atlas", at: AT, id: "m1", delivery: "delivered" } } };
  const lines = (one) => receiptLines(one, { today: TODAY, clock: UTC }).map((line) => line.text);
  assert.deepEqual(lines(sent), ['"the second, but later"',
    "Sent to atlas at 11:40, comms m1. That message is the pointer.", "With atlas, not recorded yet",
    "moxi rounds rule h Q1 --text x --record comms:m1"]);
  assert.equal(readBackOf(sent), null);
  const ruled = { ...sent, found: { id: "C7", text: "b, after B1", record: "comms:m1", actor: "atlas", at: AT, shown: false } };
  assert.deepEqual(lines(ruled).slice(2), ["Recorded as C7: b, after B1", "by atlas"]);
  assert.equal(readBackOf(ruled), "C7");
  const other = { ...sent, found: { ...ruled.found, record: "comms:m9" } };
  assert.deepEqual(lines(other).slice(2), ["Ruled as C7 from comms:m9, not from this message"]);
});

test("a sent correction reads recorded once a ruling that supersedes the corrected one appears", () => {
  const sent = { ...draft("correct", { text: "the first, not the second" }, "kestrel/billing-export", "C3"), receipt: {
    act: "x", holder: "kestrel/billing-export", item: "C3", kind: "correct", state: "sent",
    command: "moxi rounds rule kestrel/billing-export Q1 --supersedes C3 --record comms:m2",
    message: { to: "atlas", at: AT, id: "m2", delivery: "delivered" } } };
  const lines = (one) => receiptLines(one, { today: TODAY, clock: UTC }).map((line) => line.text);
  const waiting = (rows) => ({ questions: [], decisions: [], waits: [], parked: 0,
                               not_shown: [{ project: "kestrel", through: null, rows }] });
  const row = (more) => ({ holder: "kestrel/billing-export", id: "C4", text: "a, the first", record: "comms:m2",
                           supersedes: "C3", at: AT, ...more });
  assert.equal(correctionFound(sent, waiting([row({ supersedes: null }), row({ holder: "kestrel/ledger" })])), null);
  assert.deepEqual(lines(sent).slice(2), ["With atlas, not recorded yet",
    "moxi rounds rule kestrel/billing-export Q1 --supersedes C3 --record comms:m2"]);
  assert.equal(readBackOf(sent), null);
  const found = correctionFound(sent, waiting([row({ id: "C4", at: "2026-11-21T11:41:00Z" }),
                                               row({ id: "C5", at: "2026-11-21T11:42:00Z" })]));
  assert.equal(found.id, "C5");
  const ruled = { ...sent, found };
  assert.deepEqual(lines(ruled).slice(2), ["Recorded as C5: a, the first"]);
  assert.equal(readBackOf(ruled), "C5");
  const other = { ...sent, found: correctionFound(sent, waiting([row({ record: "comms:m9" })])) };
  assert.deepEqual(lines(other).slice(2), ["Ruled as C4 from comms:m9, not from this message"]);
  assert.equal(correctionFound(draft("words"), waiting([row({})])), null);
});

test("an explain request's receipt says the question stays open", () => {
  const ask = { ...draft("explain"), receipt: { act: "x", holder: "h", item: "Q1", kind: "explain", state: "sent",
    message: { to: "atlas", at: AT, id: "m3", delivery: "delivered" } } };
  assert.deepEqual(receiptLines(ask, { today: TODAY, clock: UTC }).map((one) => one.text), [
    "Asked atlas to explain at 11:40, comms m3. The question stays open; the reply comes by message."]);
});

test("each kind's words", () => {
  assert.equal(kindWords({ kind: "pick", chose: "b" }), "Pick b");
  assert.equal(kindWords({ kind: "correct" }), "Not what I said");
  assert.equal(kindWords({ kind: "unpark" }), "Unpark");
});
