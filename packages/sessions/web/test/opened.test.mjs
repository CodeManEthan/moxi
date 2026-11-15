import assert from "node:assert/strict";
import test from "node:test";

import { GONE, rowView } from "../src/list.ts";
import {
  COPY, COPY_ID, OPENED_MAX, OPENED_WAITING, openedStatus, openedView, READ_TRANSCRIPT, readAgain, saidText,
  sessionLine, spanWords, subagentView,
} from "../src/opened.ts";

// -- fixtures -----------------------------------------------------------------------------------

/** A zone the tests can read: UTC, with a 24-hour clock. */
const UTC = {
  clock: (epoch) => new Date(epoch * 1000).toISOString().slice(11, 16),
  day: (epoch) => new Date(epoch * 1000).toISOString().slice(0, 10),
  start: (day) => Date.parse(`${day}T00:00:00Z`) / 1000,
};
const at = (text) => Date.parse(`${text.replace(" ", "T")}:00Z`) / 1000;

const TODAY = "2026-11-08";
const ID = "0190b000-0000-7000-8000-000000000001";

const listRow = (over = {}) => ({
  harness: "claude", session: ID, path: "/root/one.jsonl", changed: at("2026-11-08 08:55"), size: 100, subagents: 2,
  folder: "/work/proj-a", started: at("2026-11-08 07:40"), by_program: null, name: null, model: "model-1",
  activity: null, prompt: { at: at("2026-11-08 07:40"), text: "Find out why the deploy failed.", truncated: false },
  recent: false, ...over,
});
const sub = (over = {}) => ({
  description: "Read the deploy logs", agent_type: "Explore", started: at("2026-11-08 07:58"),
  last_at: at("2026-11-08 08:01"), last: "The check ran too early.", ...over,
});
const answer = (over = {}) => ({
  ...listRow(), shape: 1,
  reply: { at: at("2026-11-08 08:55"), text: "Found it: the warmer starts first.", truncated: false },
  subagents: [sub(), sub({ description: "List the callers", agent_type: "general-purpose" })],
  more: 0,
  resume: { command: `claude --resume ${ID}`, folder: "/work/proj-a" },
  ...over,
});
const read = (value) => ({ loading: false, gone: false, failure: null, refused: null, value });
const row = (over = {}, on = TODAY) => rowView(listRow(over), on, TODAY, true, UTC);
const draw = (value, { rowOver = {}, roots = [], on = TODAY } = {}) =>
  openedView(row(rowOver, on), read(value), { today: TODAY, roots, zone: UTC });

// -- the whole opened row -----------------------------------------------------------------------

test("an opened row shows the prompt, the reply, the subagents, the session line, the command and its foot", () => {
  assert.deepEqual(draw(answer()), {
    status: null,
    prompt: { label: "You", time: "07:40", text: "Find out why the deploy failed." },
    reply: { label: "Reply", time: "08:55", text: "Found it: the warmer starts first." },
    subagents: {
      label: "Subagents",
      items: [
        { name: "Read the deploy logs", facts: "Explore · 07:58 to 08:01", last: "The check ran too early." },
        { name: "List the callers", facts: "general-purpose · 07:58 to 08:01", last: "The check ran too early." },
      ],
      notice: null,
    },
    session: { label: "Session", lead: "claude · model-1", rest: "today 07:40 to 08:55" },
    resume: { label: "Resume", command: `claude --resume ${ID}`, where: "run it in /work/proj-a", copy: COPY },
    transcript: { text: READ_TRANSCRIPT, route: `claude/${ID}` },
    copyId: { text: COPY_ID, id: ID },
  });
});

test("each piece is absent when the answer does not carry its fact", () => {
  const bare = draw(answer({ prompt: null, reply: null, subagents: null, more: null, resume: null, model: null,
                             started: null, changed: null }));
  assert.equal(bare.prompt, null);
  assert.equal(bare.reply, null);
  assert.equal(bare.subagents, null);
  assert.equal(bare.resume, null);
  assert.deepEqual(bare.session, { label: "Session", lead: "claude", rest: null });
  assert.notEqual(bare.transcript, null);

  assert.equal(draw(answer({ prompt: null })).prompt, null);
  assert.notEqual(draw(answer({ prompt: null })).reply, null);
  assert.equal(draw(answer({ reply: null })).reply, null);
  assert.equal(draw(answer({ subagents: [] })).subagents, null);
  assert.equal(draw(answer({ resume: null })).resume, null);
  assert.equal(draw(answer({ prompt: { at: null, text: "   ", truncated: false } })).prompt, null);
  assert.equal(draw(answer({ reply: { at: null, text: null, truncated: false } })).reply, null);
});

test("a message with no time has none, and a time of another day says its day", () => {
  assert.equal(draw(answer({ reply: { at: null, text: "Done.", truncated: false } })).reply.time, null);
  const older = draw(answer({ prompt: { at: at("2026-11-07 22:50"), text: "Go on.", truncated: false } }));
  assert.equal(older.prompt.time, "yesterday 22:50");
  // In a past day's group a time of that day is a clock time, and one of today says so.
  const past = draw(answer({ prompt: { at: at("2026-11-06 22:10"), text: "Go on.", truncated: false } }),
                    { on: "2026-11-06" });
  assert.equal(past.prompt.time, "22:10");
  assert.equal(past.reply.time, "today 08:55");
});

test("a session a program started shows no typed message, and still its reply", () => {
  const view = draw(answer({ by_program: true }));
  assert.equal(view.prompt, null);
  assert.equal(view.reply.text, "Found it: the warmer starts first.");
});

test("a row with one line when closed shows what its answer carries", () => {
  const one = rowView(listRow({ prompt: null, subagents: 0 }), TODAY, TODAY, true, UTC);
  assert.equal(one.lines, 1);
  const view = openedView(one, read(answer({ prompt: null, subagents: [], more: 0 })), { today: TODAY, zone: UTC });
  assert.equal(view.prompt, null);
  assert.equal(view.subagents, null);
  assert.equal(view.reply.label, "Reply");
  assert.equal(view.session.lead, "claude · model-1");
  assert.equal(view.resume.command, `claude --resume ${ID}`);
  assert.deepEqual(view.transcript, { text: READ_TRANSCRIPT, route: `claude/${ID}` });
});

// -- messages -----------------------------------------------------------------------------------

test("a message's text is one run of lines, cut with an ellipsis", () => {
  assert.equal(saidText({ at: null, text: "one\n\n  two\tthree ", truncated: false }), "one two three");
  const long = "é".repeat(OPENED_MAX + 50);
  assert.equal(saidText({ at: null, text: long, truncated: false }), `${"é".repeat(OPENED_MAX)}…`);
  assert.equal(saidText({ at: null, text: "é".repeat(OPENED_MAX), truncated: false }), "é".repeat(OPENED_MAX));
  assert.equal(saidText({ at: null, text: "cut by the server", truncated: true }), "cut by the server…");
  assert.equal(saidText(null), null);
  assert.equal(saidText({ at: 1, text: null, truncated: false }), null);
});

// -- times --------------------------------------------------------------------------------------

test("a span says each end's day only where the reader could not tell it", () => {
  const span = (a, b, on = TODAY) => spanWords(a === null ? null : at(a), b === null ? null : at(b), on, TODAY, UTC);
  assert.equal(span("2026-11-08 07:58", "2026-11-08 08:01"), "07:58 to 08:01");
  assert.equal(span("2026-11-07 22:25", "2026-11-08 00:10"), "yesterday 22:25 to today 00:10");
  assert.equal(span("2026-11-07 22:25", "2026-11-07 23:10"), "yesterday 22:25 to 23:10");
  assert.equal(span("2025-11-06 21:30", "2025-11-06 22:10", ""), "Nov 6, 2025 21:30 to 22:10");
  assert.equal(span("2026-11-08 07:58", null), "started 07:58");
  assert.equal(span(null, "2026-11-08 08:01"), "last change 08:01");
  assert.equal(span(null, null), null);
});

test("the session line names the harness by its id, the model, and both ends with their day", () => {
  assert.deepEqual(sessionLine(answer(), TODAY, UTC), { lead: "claude · model-1", rest: "today 07:40 to 08:55" });
  assert.deepEqual(sessionLine(answer({ harness: "codex", model: null, started: null }), TODAY, UTC),
                   { lead: "codex", rest: "last change today 08:55" });
  assert.deepEqual(sessionLine(answer({ changed: null }), TODAY, UTC),
                   { lead: "claude · model-1", rest: "started today 07:40" });
});

// -- subagents ----------------------------------------------------------------------------------

test("a subagent is named by what it was asked, else by its type, and draws only the facts it has", () => {
  assert.deepEqual(subagentView(sub({ description: null }), TODAY, TODAY, UTC),
                   { name: "Explore", facts: "07:58 to 08:01", last: "The check ran too early." });
  assert.deepEqual(subagentView(sub({ description: null, agent_type: null, started: null, last_at: null, last: null }),
                                TODAY, TODAY, UTC),
                   { name: "Subagent", facts: null, last: null });
  assert.deepEqual(subagentView(sub({ agent_type: null, last: "  two\nlines " }), TODAY, TODAY, UTC),
                   { name: "Read the deploy logs", facts: "07:58 to 08:01", last: "two lines" });
});

test("the subagents not listed are not counted on the page", () => {
  const view = draw(answer({ more: 12 }));
  assert.equal(view.subagents.items.length, 2);
  assert.equal(JSON.stringify(view).includes("12"), false);
});

test("the resume line's folder is worded against the home folder, and the command is the harness's own", () => {
  const under = (folder, home) => draw(answer({ home, resume: { command: `claude --resume ${ID}`, folder } })).resume;
  assert.equal(under("/home/alice/code/proj-a", "/home/alice").where, "run it in ~/code/proj-a");
  assert.equal(under("/home/alice", "/home/alice").where, "run it in ~");
  assert.equal(under("/work/proj-a", "/home/alice").where, "run it in /work/proj-a");
  assert.equal(under("/home/alice/code/proj-a", null).where, "run it in /home/alice/code/proj-a");
  assert.equal(under("/home/alice/code/proj-a", "/home/alice").command, `claude --resume ${ID}`);
});

test("a codex session's subagents carry the line for parts of codex that were not read", () => {
  const codex = { harness: "codex", resume: { command: "codex resume t1", folder: null } };
  const parts = { harness: "codex", root: "/codex", reason: null, parts: 3 };
  const short = draw(answer(codex), { rowOver: { harness: "codex" }, roots: [parts] });
  assert.equal(short.subagents.notice, "Moxi couldn't read 3 parts of Codex's sessions; the counts leave them out");
  assert.equal(short.resume.where, null);
  assert.equal(short.resume.command, "codex resume t1");
  // Not for another harness's unread parts, not for another harness's session, and not
  // without subagents to stand beside.
  assert.equal(draw(answer(codex), { roots: [{ ...parts, harness: "claude" }] }).subagents.notice, null);
  assert.equal(draw(answer(), { roots: [parts] }).subagents.notice, null);
  assert.equal(draw(answer({ ...codex, subagents: [] }), { roots: [parts] }).subagents, null);
});

// -- waiting and failing ------------------------------------------------------------------------

test("an opened row waits, fails and is refused in words, with its foot there throughout", () => {
  const waiting = openedView(row(), { ...read(null), loading: true }, { today: TODAY, zone: UTC });
  assert.deepEqual(waiting.status, { state: "waiting", line: OPENED_WAITING });
  assert.equal(waiting.session, null);
  assert.deepEqual(waiting.transcript, { text: READ_TRANSCRIPT, route: `claude/${ID}` });
  assert.deepEqual(waiting.copyId, { text: COPY_ID, id: ID });
  assert.deepEqual(openedView(row(), null, { today: TODAY }).status, { state: "waiting", line: OPENED_WAITING });

  const failed = { ...read(null), failure: { status: 500, why: "sessions' page server answered 500" } };
  assert.deepEqual(openedStatus(failed),
                   { state: "failed", line: "Moxi couldn't read the session: sessions' page server answered 500" });
  assert.deepEqual(openedStatus({ ...read(null), refused: "no such session" }),
                   { state: "refused", line: "Moxi couldn't read the session: no such session" });
  assert.deepEqual(openedStatus({ ...read(null), gone: true }), { state: "gone", line: GONE });
  assert.equal(openedStatus(read(answer())), null);

  // An answer read before a failure stays drawn under the line.
  const kept = openedView(row(), { ...failed, value: answer() }, { today: TODAY, zone: UTC });
  assert.equal(kept.status.state, "failed");
  assert.equal(kept.reply.text, "Found it: the warmer starts first.");
});

test("a row of a harness the page has no view for has no link to a transcript", () => {
  const view = openedView(row({ harness: "other" }), read(answer({ harness: "other", resume: null })),
                          { today: TODAY, zone: UTC });
  assert.equal(view.transcript, null);
  assert.deepEqual(view.copyId, { text: COPY_ID, id: ID });
  assert.equal(view.session.lead, "other · model-1");
});

// -- reading again ------------------------------------------------------------------------------

test("an opened row is read again only when the list shows its session changed", () => {
  const feed = read(answer());
  assert.equal(readAgain(feed, listRow()), false);
  assert.equal(readAgain(feed, listRow({ changed: at("2026-11-08 09:30") })), true);
  assert.equal(readAgain(feed, listRow({ subagents: 3 })), true);
  // Subagents the answer could not count say nothing against the row's count.
  assert.equal(readAgain(read(answer({ more: null })), listRow({ subagents: 3 })), false);
  assert.equal(readAgain(feed, listRow({ subagents: null })), false);
  // No answer yet: the read is tried again, unless one is running or sessions is gone.
  assert.equal(readAgain({ ...read(null), refused: "no such session" }, listRow()), true);
  assert.equal(readAgain({ ...read(null), reading: true }, listRow()), false);
  assert.equal(readAgain({ ...read(null), gone: true }, listRow()), false);
  // A row the list no longer shows, and a row never asked for.
  assert.equal(readAgain(feed, null), false);
  assert.equal(readAgain(null, listRow()), false);
});
