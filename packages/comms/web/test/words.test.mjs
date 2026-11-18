import assert from "node:assert/strict";
import test from "node:test";

import { checkWords, dateWord, dayWord, deliveryWords, firstPosture, fullDayWord, newestDay, POSTURES, readWords,
         sendWords, takeBackWords, transportWords, wantsWords, whenWords } from "../src/words.ts";

// A clock in UTC that writes the time as `HH:MM`, so the words are the same in any zone.
const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const at = (iso) => Date.parse(iso) / 1000;
const when = UTC.time;

// --- days -------------------------------------------------------------------------------------

test("a moment against today: the time alone, else its day, each side of midnight", () => {
  const today = "2026-11-12"; // a Thursday
  assert.equal(whenWords(at("2026-11-12T00:00:00Z"), today, UTC), "00:00");
  assert.equal(whenWords(at("2026-11-12T23:59:00Z"), today, UTC), "23:59");
  assert.equal(whenWords(at("2026-11-11T23:59:00Z"), today, UTC), "yesterday 23:59");
  assert.equal(whenWords(at("2026-11-13T00:01:00Z"), today, UTC), "tomorrow 00:01");
  assert.equal(whenWords(at("2026-11-10T21:15:00Z"), today, UTC), "Tue 21:15");
  assert.equal(whenWords(at("2026-11-06T08:00:00Z"), today, UTC), "Fri 08:00");
  assert.equal(whenWords(at("2026-11-05T08:52:00Z"), today, UTC), "Nov 5 08:52");
  assert.equal(whenWords(at("2026-11-05T08:52:00Z"), null, UTC), "08:52");
});

test("day words: today, yesterday, weekday, date; back to with its weekday", () => {
  assert.equal(dayWord("2026-11-12", "2026-11-12"), "today");
  assert.equal(dayWord("2026-11-11", "2026-11-12"), "yesterday");
  assert.equal(dayWord("2026-11-07", "2026-11-12"), "Sat");
  assert.equal(dayWord("2026-10-12", "2026-11-12"), "Oct 12");
  assert.equal(dayWord("2026-01-01", "2025-12-31"), "tomorrow");
  assert.equal(dateWord("nope"), "nope");
  assert.equal(fullDayWord("2026-10-12"), "Mon Oct 12");
  assert.equal(fullDayWord("x"), "x");
});

test("the newest today the page holds", () => {
  assert.equal(newestDay(null, "2026-11-12"), "2026-11-12");
  assert.equal(newestDay("2026-11-12", "2026-11-11"), "2026-11-12");
  assert.equal(newestDay("2026-11-12", "2026-11-13"), "2026-11-13");
  assert.equal(newestDay("2026-11-12", null), "2026-11-12");
  assert.equal(newestDay("bad", "worse"), null);
});

// --- wants ------------------------------------------------------------------------------------

test("what a message wants, and a reply's lead", () => {
  assert.equal(wantsWords("once", null), "wants one reply");
  assert.equal(wantsWords("none", null), "wants no reply");
  assert.equal(wantsWords("open", null), "wants replies");
  assert.equal(wantsWords("optional", null), "wants a reply only if it has something to add");
  assert.equal(wantsWords("none", "1000000000000000001"), "replies · wants no reply");
  assert.equal(wantsWords("once", "1000000000000000001"), "replies, and asks for one reply");
  assert.equal(wantsWords("open", "1000000000000000001"), "replies · wants replies");
  assert.equal(wantsWords("optional", "1"), "replies · wants a reply only if it has something to add");
  assert.equal(wantsWords(null, null), null);
  assert.equal(wantsWords("toString", null), null);
  assert.equal(wantsWords(null, "1"), "replies");
});

test("the postures' menu, and the box's first posture", () => {
  assert.deepEqual(POSTURES.map((p) => [p.posture, p.label]), [
    ["none", "No reply"], ["optional", "Only if it has something to add"], ["once", "One reply"],
    ["open", "Back and forth"],
  ]);
  assert.equal(POSTURES[3].says, "Closes after 8 replies or an hour with no message.");
  assert.equal(firstPosture(false), "open");
  assert.equal(firstPosture(true), "none");
});

// --- a recipient's read state -----------------------------------------------------------------

test("a recipient's row: unread and why, and each way it left the queue", () => {
  const row = (state, qualified = "builder-2@kestrel-web") => ({ qualified, state, held: false, takeable: true });
  const full = { bound: true, hasCache: true };
  assert.equal(readWords(row("waiting"), full), "not read yet, held until its next turn");
  assert.equal(readWords(row("waiting"), { ...full, inThread: true }), "not read yet, held until builder-2's next turn");
  assert.equal(readWords(row("waiting"), { bound: false, hasCache: true }), "not read yet, builder-2 isn't running");
  assert.equal(readWords(row("waiting"), { bound: false, hasCache: false }), "not read yet, builder-2 isn't running");
  assert.equal(readWords(row("waiting"), { bound: true, hasCache: false }), "not read yet");
  assert.equal(readWords(row("handed"), full), "not read yet");
  assert.equal(readWords(row("in_digest"), full), "not read yet");
  assert.equal(readWords(row("withdrawn"), full), "taken back");
  assert.equal(readWords(row("expired"), full), "builder-2's copy expired");
  assert.equal(readWords(row("reserved", "console"), full), "console takes no mail");
  assert.equal(readWords(row("lost"), full), null);
});

// --- check and send ---------------------------------------------------------------------------

const answer = (outcome, delivery = null, code = null, cold_at = null) => ({
  outcome, message_id: outcome === "sent" ? "1000000000000000009" : null,
  recipients: delivery === null ? [] : [{ qualified: "reviewer@atlas", delivery, cold_at }],
  refusal: code === null ? null : { code },
});

test("would-send: one phrase per recipient by its delivery", () => {
  const words = (delivery, cold) => checkWords(answer("would-send", delivery, null, cold), when);
  assert.deepEqual(words("live"), { key: "If sent now", heavy: null, text: "Delivered. reviewer reads it at its next turn boundary.",
                                    dim: null, again: false, reread: false, keep: false });
  assert.equal(words("held-cold", at("2026-11-12T18:35:00Z")).text,
               "Held until reviewer's next turn. Its cache went cold at 18:35, and nothing wakes it for mail. "
               + "It reads this when you next type into it, or at a session start, /clear or compaction.");
  assert.match(words("held-cold", null).text, /^Held until reviewer's next turn\. Its cache went cold, and nothing/);
  assert.equal(words("held-flag").text, "Held until reviewer's next turn, as you chose.");
  assert.equal(words("waiting").text, "Held until reviewer's session can take mail.");
  assert.equal(words("unbound").text, "reviewer isn't running. It waits until a session takes the name.");
  assert.equal(words("operator").text, "");
  assert.equal(deliveryWords({ qualified: "x", delivery: "live", cold_at: null }, when),
               "Delivered. x reads it at its next turn boundary.");
});

test("would-send to a session whose turn is open: it is working and reads it when the turn ends", () => {
  const working = (qualified) => qualified === "reviewer@atlas";
  const words = (delivery, open = working) => checkWords(answer("would-send", delivery), when, null, open);
  assert.equal(words("live").text, "Delivered. reviewer is working; it reads this when its turn ends.");
  assert.equal(words("live", () => false).text, "Delivered. reviewer reads it at its next turn boundary.");
  assert.equal(words("live", (qualified) => qualified === "reviewer").text,
               "Delivered. reviewer reads it at its next turn boundary.", "the recipient's own address joins");
  // A held message says when it is read already; the open turn adds nothing to it.
  assert.equal(words("held-flag").text, "Held until reviewer's next turn, as you chose.");
  assert.equal(words("unbound").text, "reviewer isn't running. It waits until a session takes the name.");
  // Two recipients: each phrase by its own session.
  const two = { ...answer("would-send", "live"),
                recipients: [{ qualified: "reviewer@atlas", delivery: "live", cold_at: null },
                             { qualified: "planner", delivery: "live", cold_at: null }] };
  assert.equal(checkWords(two, when, null, working).text,
               "Delivered. reviewer is working; it reads this when its turn ends. "
               + "Delivered. planner reads it at its next turn boundary.");
  // A refusal's words stand whatever the turn.
  assert.equal(checkWords(answer("blocked", "live"), when, null, working).heavy, "Refused: nothing would be sent.");
  // Send's line is not the dry run's: it says nothing of the turn.
  assert.equal(sendWords(answer("sent", "live"), at("2026-11-12T19:11:00Z"), when).text,
               "Delivered. reviewer reads it at its next turn boundary.");
});

test("check: every other outcome leads with the refusal and offers Check again", () => {
  const cases = [
    [answer("cache-unread", "live"), "Comms can't read reviewer's cache right now, so it can't tell whether this would wake it cold. Check again in a moment."],
    [answer("blocked", "live"), "Comms' settings block this send."],
    [answer("offline"), "The comms daemon isn't answering, or asked to be tried again. Check again in a moment."],
    [answer("offline", null, "post-again"), "The comms daemon isn't answering, or asked to be tried again. Check again in a moment."],
    [answer("unknown"), "Comms didn't answer in time. Check again."],
    [answer("failed", null, "credential"), "Comms can't find your credential. moxi doctor comms says why."],
    [answer("failed", null, "protocol-mismatch"), "This page and the comms daemon are different versions. moxi doctor comms says what to restart."],
    [answer("failed", null, "page-error"), "Comms couldn't do it (page-error)."],
    [answer("refused", "live", "too-long"), "With its address line it is over the 16,000-character cap on one message."],
    [answer("refused", null, "operator-in-session"), "This page's server runs inside an agent's session, so it can't send as you. Start Moxi's shell from your own terminal."],
    [answer("refused", null, "operator-and-claim"), "This page's server runs inside an agent's session, so it can't send as you. Start Moxi's shell from your own terminal."],
    [answer("refused", null, "credential"), "Comms can't find your credential. moxi doctor comms says why."],
    [answer("refused", null, "operator-unknown"), "Comms can't find your credential. moxi doctor comms says why."],
    [answer("refused", "live", "send-only"), "reviewer takes no mail."],
    [answer("refused", null, "unknown-name"), "Comms refused it (unknown-name)."],
    [answer("refused", null, "request-shape"), "Comms refused it (request-shape)."],
    [answer("refused"), "Comms refused it."],
    [answer("strange"), "Comms couldn't do it (strange)."],
  ];
  for (const [given, text] of cases) {
    assert.deepEqual(checkWords(given, when),
                     { key: "If sent now", heavy: "Refused: nothing would be sent.", text, dim: null, again: true,
                       reread: false, keep: true }, JSON.stringify(given));
  }
});

test("a refusal names the box's address, since it carries no recipient", () => {
  const unread = { outcome: "cache-unread", message_id: null, recipients: [], refusal: { code: "cold-not-held" } };
  assert.match(checkWords(unread, when, "reviewer@kestrel-web").text, /^Comms can't read reviewer's cache right now/);
  assert.equal(sendWords({ ...unread, outcome: "refused", refusal: { code: "send-only" } }, 0, when, null, "console").text,
               "console takes no mail.");
  assert.match(checkWords(unread, when).text, /^Comms can't read the recipient's cache right now/);
});

test("send: refusals lead with Not sent and keep the text; unknown may have been sent", () => {
  assert.deepEqual(sendWords(answer("refused", "live", "too-long"), 0, when),
                   { key: null, heavy: "Not sent.", text: "With its address line it is over the 16,000-character cap on one message.",
                     dim: null, again: false, reread: false, keep: true });
  assert.deepEqual(sendWords(answer("unknown", null, "page-error"), 0, when),
                   { key: null, heavy: null, text: "It may have been sent.", dim: null, again: false, reread: true, keep: true });
  assert.equal(sendWords(answer("offline"), 0, when).heavy, "Not sent.");
  assert.equal(sendWords(answer("failed", null, "credential"), 0, when).text,
               "Comms can't find your credential. moxi doctor comms says why.");
});

test("sent: the time, the delivery, how many now wait, and take back", () => {
  const sent = at("2026-11-12T19:11:00Z");
  assert.deepEqual(sendWords(answer("sent", "held-flag"), sent, when, 2),
                   { key: "Sent 19:11", heavy: null, text: "Held until reviewer's next turn, as you chose.",
                     dim: "2 messages now wait for it. You can take a message back until reviewer has read it.",
                     again: false, reread: false, keep: false });
  assert.equal(sendWords(answer("sent", "waiting"), sent, when, 1).dim,
               "1 message now waits for it. You can take a message back until reviewer has read it.");
  // Live: nothing waits to count; before the queue is read again, no count.
  assert.equal(sendWords(answer("sent", "live"), sent, when, 3).dim,
               "You can take a message back until reviewer has read it.");
  assert.equal(sendWords(answer("sent", "unbound"), sent, when).dim,
               "You can take a message back until reviewer has read it.");
});

// --- take back --------------------------------------------------------------------------------

const taken = (outcome, { withdrawn = [], delivered = [], expired = [], code = null } = {}) =>
  ({ outcome, withdrawn, delivered, expired, refusal: code === null ? null : { code } });

test("take back: deleted from every recipient, or in parts", () => {
  assert.deepEqual(takeBackWords(taken("deleted", { withdrawn: ["reviewer@atlas"] })),
                   { key: null, heavy: null, text: "Taken back.", dim: null, again: false, reread: false, keep: false });
  assert.equal(takeBackWords(taken("deleted", { withdrawn: ["a"], delivered: ["b@x"], expired: ["c", "d"] })).text,
               "Taken back from a. b already had it. c and d's copy had expired.");
  assert.equal(takeBackWords(taken("deleted", { delivered: ["b"] })).text, "b already had it.");
  assert.equal(takeBackWords(taken("deleted", { withdrawn: ["a"], expired: ["c"] })).text,
               "Taken back from a. c's copy had expired.");
});

test("take back: every refusal and failure", () => {
  const cases = [
    [taken("refused", { delivered: ["reviewer@atlas"], code: "already-delivered" }), null, "reviewer already had it."],
    [taken("refused", { code: "already-delivered" }), "Not taken back.", "Comms refused it (already-delivered)."],
    [taken("refused", { code: "not-sender" }), "Not taken back.", "It isn't your message."],
    [taken("refused", { code: "unknown-message" }), "Not taken back.", "Comms doesn't hold that message any more."],
    [taken("refused", { code: "no-identity" }), "Not taken back.",
     "This page's server runs inside an agent's session, so it can't take messages back as you. Start Moxi's shell from your own terminal."],
    [taken("refused", { code: "message-id-shape" }), "Not taken back.", "Comms refused it (message-id-shape)."],
    [taken("offline", { code: "offline" }), "Not taken back.", "The comms daemon isn't answering, or asked to be tried again."],
    [taken("offline", { code: "delete-again" }), "Not taken back.", "The comms daemon isn't answering, or asked to be tried again."],
    [taken("failed", { code: "credential" }), "Not taken back.", "Comms can't find your credential. moxi doctor comms says why."],
    [taken("failed", { code: "protocol-mismatch" }), "Not taken back.",
     "This page and the comms daemon are different versions. moxi doctor comms says what to restart."],
    [taken("failed", { code: "delete-failed" }), "Not taken back.", "Comms couldn't do it (delete-failed)."],
    [taken("strange"), "Not taken back.", "Comms couldn't do it (strange)."],
  ];
  for (const [given, heavy, text] of cases) {
    const got = takeBackWords(given);
    assert.deepEqual([got.heavy, got.text, got.reread], [heavy, text, false], JSON.stringify(given));
  }
  const unknown = takeBackWords(taken("unknown", { code: "delete-no-answer" }));
  assert.deepEqual([unknown.heavy, unknown.text, unknown.reread],
                   [null, "Comms didn't answer; it may have been taken back.", true]);
});

// --- transport --------------------------------------------------------------------------------

test("when the request gets no answer: by status, for each act", () => {
  const row = (act, status) => {
    const got = transportWords(act, status);
    return [got.text, got.again, got.reread, got.keep];
  };
  assert.deepEqual(row("check", 0), ["No answer from the shell. Check again.", true, false, true]);
  assert.deepEqual(row("send", 0), ["It may have been sent.", false, true, true]);
  assert.deepEqual(row("take-back", 0), ["It may have been taken back.", false, true, false]);
  for (const status of [502, 504]) {
    assert.deepEqual(row("check", status), ["Comms' page server didn't answer. Check again.", true, false, true]);
    assert.deepEqual(row("send", status), ["It may have been sent.", false, true, true]);
    assert.deepEqual(row("take-back", status), ["It may have been taken back.", false, true, false]);
  }
  assert.deepEqual(row("check", 400), ["The shell refused the request (400).", true, false, true]);
  assert.deepEqual(row("send", 400), ["The shell refused the request (400).", false, false, true]);
  assert.deepEqual(row("take-back", 400), ["The shell refused the request (400).", false, false, false]);
  assert.deepEqual(row("check", 503), ["The shell answered 503.", true, false, true]);
  assert.deepEqual(row("send", 403), ["The shell answered 403.", false, false, true]);
  assert.deepEqual(row("take-back", 500), ["The shell answered 500.", false, false, false]);
  assert.equal(transportWords("check", 0).key, "If sent now");
  assert.equal(transportWords("send", 502).key, null);
});
