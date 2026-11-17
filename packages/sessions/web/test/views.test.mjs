import assert from "node:assert/strict";
import test from "node:test";

import {
  AGENT_ONLY_CLAUDE, CUT, charCount, cutAt, entryView, ERROR, ERROR_FLAG, FAILED, failureWord, fmtBytes, fmtCount,
  fmtDuration, fmtTime, headerFields, headingOf, HIDDEN, itemOf, keepEarlier, LIMIT_LINE, listingView, markerLine,
  NO_SUBAGENTS, pages, parseRoute, requestLine, routeOf, sentLine, START_LINE, subagentsPath,
  textView, UNKNOWN_ROUTE, unknownLine, windowPath, windowView,
} from "../src/views.ts";

const state = (over) => ({ loading: false, gone: false, failure: null, refused: null, value: null, ...over });
const at = (offset, line = null) => ({ line, offset });
const message = (text, over = {}) => ({
  kind: "message", raw_kind: "user", at: at(0), unknown: [], role: "user", text,
  text_bytes: Buffer.byteLength(text), truncated: false, reasoning: false, flags: [], ...over,
});
const WINDOW = {
  shape: 1, harness: "claude", session: "s1", agent: null, path: "/p/s1.jsonl",
  header: { cwd: "/work/project", title: "fix the tests", version: "2.1.314", unknown: [] },
  last_activity: 1_700_000_000, turns: 5, has_earlier: true, before: "4096", ended: null,
  records: [message("turn 1", { at: at(4096) }), message("done 1", { role: "assistant", raw_kind: "assistant", at: at(4200) })],
};
const EARLIER = { ...WINDOW, has_earlier: false, before: null, ended: "start",
                  records: [message("turn 0", { at: at(0) })] };

// -- routes -------------------------------------------------------------------------------------

test("the empty route is the list", () => {
  assert.deepEqual(parseRoute(""), { view: "list" });
  assert.deepEqual(parseRoute("/"), { view: "list" });
});

test("a route names a session by harness and id, and a Claude subagent under it", () => {
  assert.deepEqual(parseRoute("claude/s1"), { view: "session", target: { harness: "claude", session: "s1", agent: null } });
  assert.deepEqual(parseRoute("claude/s1/a1"), { view: "session", target: { harness: "claude", session: "s1", agent: "a1" } });
  assert.deepEqual(parseRoute("opencode/ses_1/"), { view: "session", target: { harness: "opencode", session: "ses_1", agent: null } });
  assert.deepEqual(parseRoute("codex/t%201"), { view: "session", target: { harness: "codex", session: "t 1", agent: null } });
});

test("an agent under a harness whose subagents are sessions of their own is refused", () => {
  assert.deepEqual(parseRoute("codex/t1/a1"), { view: "unknown", line: AGENT_ONLY_CLAUDE });
  assert.deepEqual(parseRoute("opencode/ses_1/a1"), { view: "unknown", line: AGENT_ONLY_CLAUDE });
});

test("a route the page has no view for says so", () => {
  for (const route of ["claude", "gemini/s1", "claude/s1/a1/x", "claude//a1", "claude/%E0%A4%A"]) {
    assert.deepEqual(parseRoute(route), { view: "unknown", line: UNKNOWN_ROUTE }, route);
  }
});

test("a session's route encodes each part, and reads back", () => {
  const target = { harness: "claude", session: "a b", agent: "x/y" };
  assert.equal(routeOf(target), "claude/a%20b/x%2Fy");
  assert.deepEqual(parseRoute(routeOf(target)), { view: "session", target });
  assert.equal(routeOf({ harness: "codex", session: "t1", agent: null }), "codex/t1");
});

// -- queries ------------------------------------------------------------------------------------

test("the window's query names the session, the agent and before only when given", () => {
  assert.equal(windowPath({ harness: "claude", session: "s1", agent: null }), "api/window?harness=claude&session=s1");
  assert.equal(windowPath({ harness: "claude", session: "s1", agent: "a1" }, "4096"),
               "api/window?harness=claude&session=s1&agent=a1&before=4096");
  assert.equal(windowPath({ harness: "opencode", session: "ses 1", agent: null }, "msg_&1"),
               "api/window?harness=opencode&session=ses+1&before=msg_%261");
  assert.equal(subagentsPath({ harness: "codex", session: "t1", agent: null }), "api/subagents?harness=codex&session=t1");
});

// -- formats ------------------------------------------------------------------------------------

test("counts, sizes, durations and times", () => {
  assert.equal(fmtCount(1234567), "1,234,567");
  assert.equal(fmtCount(12), "12");
  assert.equal(fmtBytes(512), "512 B");
  assert.equal(fmtBytes(2048), "2.0 KB");
  assert.equal(fmtBytes(3 * 1024 * 1024), "3.0 MB");
  assert.equal(fmtDuration(850), "850 ms");
  assert.equal(fmtDuration(1200), "1.2 s");
  assert.equal(fmtDuration(184_000), "3 min 4 s");
  const d = new Date(1_700_000_000 * 1000);
  const two = (n) => String(n).padStart(2, "0");
  assert.equal(fmtTime(1_700_000_000), `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} `
    + `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`);
});

// -- the header ---------------------------------------------------------------------------------

test("the header shows what the harness said, and leaves out what it did not", () => {
  const fields = headerFields(WINDOW);
  assert.deepEqual(fields.map((f) => f.name), ["harness", "session", "title", "cwd", "last_activity", "version", "path"]);
  assert.equal(fields[0].value, "Claude Code");
  assert.equal(fields.find((f) => f.name === "cwd").code, true);
  assert.equal(fields.find((f) => f.name === "last_activity").value, fmtTime(1_700_000_000));
  const sub = headerFields({ ...WINDOW, agent: "a1", last_activity: null, path: null,
                             header: { parent: "s0", title: null, unknown: ["cwd"] } });
  assert.deepEqual(sub.map((f) => f.name), ["harness", "session", "agent", "title", "last_activity", "parent", "path"]);
  assert.equal(sub.find((f) => f.name === "title").value, HIDDEN);
  assert.equal(sub.find((f) => f.name === "last_activity").value, "none in these turns");
  assert.equal(sub.find((f) => f.name === "path").value, HIDDEN);
});

test("the heading is the title, else the harness and id", () => {
  const target = { harness: "claude", session: "s1", agent: null };
  assert.equal(headingOf(WINDOW, target), "fix the tests");
  assert.equal(headingOf(null, target), "Claude Code session s1");
  assert.equal(headingOf(WINDOW, { ...target, agent: "a1" }), "Subagent a1 of Claude Code session s1");
});

// -- text ---------------------------------------------------------------------------------------

test("a message is cut at 2,000 characters and expands to all that was sent", () => {
  const long = "x".repeat(CUT + 500);
  const cut = textView(message(long), false);
  assert.equal(cut.shown.length, CUT);
  assert.equal(cut.long, true);
  assert.equal(cut.rest, 500);
  assert.equal(cut.sentLine, null);
  const open = textView(message(long), true);
  assert.equal(open.shown, long);
  assert.equal(open.rest, 0);
  const short = textView(message("short"), false);
  assert.deepEqual(short, { shown: "short", long: false, rest: 0, sentLine: null });
});

test("a cut counts characters, so no surrogate pair is split", () => {
  const text = "😀".repeat(CUT + 1);
  assert.equal(charCount(text), CUT + 1);
  const view = textView(message(text), false);
  assert.equal(charCount(view.shown), CUT);
  assert.equal(view.rest, 1);
  assert.equal(cutAt("a😀b", 2), "a😀");
});

test("a text the server cut at 64 KiB says how much there was", () => {
  const view = textView(message("é".repeat(100), { truncated: true, text_bytes: 80000 }), true);
  assert.equal(view.sentLine, sentLine(80000));
  assert.match(view.sentLine, /first 64 KiB of this message's 80,000 bytes/);
});

test("a text sent as null shows as hidden", () => {
  assert.equal(textView(message("x", { text: null }), false).shown, null);
});

// -- records ------------------------------------------------------------------------------------

test("a message is its role and text; thinking is labelled so", () => {
  const user = itemOf(message("hi"));
  assert.equal(user.type, "message");
  assert.equal(user.label, "User");
  const thought = itemOf(message("hmm", { role: "assistant", reasoning: true }));
  assert.equal(thought.label, "Thinking");
  assert.equal(thought.reasoning, true);
  const flagged = itemOf(message("x", { flags: ["isMeta"], model: "m1" }));
  assert.deepEqual(flagged.tags, ["isMeta", "m1"]);
});

test("a synthetic message is a labelled rule", () => {
  const item = itemOf(message("No response requested.\nmore", { role: "assistant", synthetic: true }));
  assert.deepEqual([item.type, item.marker, item.line], ["rule", "synthetic", "Synthetic: No response requested."]);
});

test("a tool call is one line, its summary; a result is its size and error flag", () => {
  const call = itemOf({ kind: "tool_call", raw_kind: "assistant", at: at(1), unknown: [], name: "Read",
                        call_id: "c1", summary: "Read /work/notes.md" });
  assert.deepEqual([call.type, call.line], ["call", "Read /work/notes.md"]);
  const result = itemOf({ kind: "tool_result", raw_kind: "user", at: at(2), unknown: [], call_id: "c1",
                          is_error: false, size_bytes: 2048 });
  assert.deepEqual([result.type, result.line, result.error], ["result", "Result: 2.0 KB", false]);
  const failed = itemOf({ kind: "tool_result", raw_kind: "user", at: at(3), unknown: [], is_error: true, size_bytes: 10 });
  assert.deepEqual([failed.line, failed.error], ["Result: error, 10 B", true]);
});

test("a record that failed is called by a word, and no other record is", () => {
  const result = (over) => ({ kind: "tool_result", raw_kind: "user", at: at(3), unknown: [], size_bytes: 10, ...over });
  const marker = (name) => ({ kind: "marker", raw_kind: "system", at: at(4), unknown: [], marker: name });
  const failing = [
    [result({ is_error: true }), FAILED, "result"],
    [message("API Error: 529", { role: "assistant", synthetic: true, flags: [ERROR_FLAG] }), ERROR, "rule"],
    [message("API Error: 529", { role: "assistant", flags: [ERROR_FLAG, "isMeta"] }), ERROR, "message"],
  ];
  for (const [row, word, type] of failing) {
    assert.equal(failureWord(row), word);
    const item = itemOf(row);
    assert.deepEqual([item.type, item.word], [type, word]);
  }
  assert.deepEqual([FAILED, ERROR], ["Failed", "Error"]);
  // The word stands before the record's own line, which is as it was.
  assert.equal(itemOf(failing[0][0]).line, "Result: error, 10 B");
  assert.equal(itemOf(failing[1][0]).line, "Synthetic: API Error: 529");

  const ordinary = [
    result({ is_error: false }), result({ is_error: null }), result({}),
    message("hello"), message("done", { role: "assistant", flags: ["isMeta"] }),
    message("thinking", { role: "assistant", reasoning: true }),
    message("No response requested.", { role: "assistant", synthetic: true }),
    { kind: "message", raw_kind: "user", at: at(0), unknown: [], role: "user", text: "no flags sent" },
    marker("compaction"), marker("turn_aborted"), marker("rollback"), marker("turn_end"),
  ];
  for (const row of ordinary) {
    assert.equal(failureWord(row), null);
    assert.equal(itemOf(row).word, null);
  }
  // A call, a request and a note have no word: none of them says how anything ended.
  for (const row of [
    { kind: "tool_call", raw_kind: "assistant", at: at(1), unknown: [], name: "Read", call_id: "c1", summary: "Read /work/a" },
    { kind: "request", raw_kind: "assistant", at: at(5), unknown: [], request_id: "r1", model: "claude-model-x" },
    { kind: "limits", raw_kind: "event_msg", at: at(6), unknown: [], limit_id: "codex", windows: [] },
    { kind: "malformed", raw_kind: "malformed", at: at(7), unknown: [], why: "not JSON" },
    { kind: "other", raw_kind: "system/api_error", at: at(8), unknown: [], recognised: true },
  ]) {
    assert.equal(failureWord(row), null);
    assert.equal("word" in itemOf(row), false);
  }
});

test("markers are labelled rules", () => {
  const marker = (over) => ({ kind: "marker", raw_kind: "system", at: at(4), unknown: [], ...over });
  assert.equal(markerLine(marker({ marker: "compaction", trigger: "auto", pre_tokens: 150000 })),
               "Compacted (auto, 150,000 tokens before)");
  assert.equal(markerLine(marker({ marker: "compaction" })), "Compacted");
  assert.equal(markerLine(marker({ marker: "turn_end", duration_ms: 1200 })), "Turn ended after 1.2 s");
  assert.equal(markerLine(marker({ marker: "turn_aborted" })), "Turn aborted");
  assert.equal(markerLine(marker({ marker: "rollback" })), "Rolled back");
  assert.equal(markerLine(marker({ marker: "settings", model: "m1", provider: "openai" })), "Settings: model m1, provider openai");
  assert.equal(markerLine(marker({ marker: "title", title: "t", title_source: "ai" })), "Title (ai): t");
  assert.equal(markerLine(marker({ marker: "title", title: null })), `Title: ${HIDDEN}`);
  const item = itemOf(marker({ marker: "rollback" }));
  assert.deepEqual([item.type, item.marker], ["rule", "rollback"]);
});

test("a request is its model and token counts", () => {
  const row = { kind: "request", raw_kind: "assistant", at: at(5), unknown: ["cost_usd"], request_id: "r1",
                model: "claude-model-x", input_tokens: 903, cache_read_input_tokens: 900,
                cache_creation_input_tokens: null, output_tokens: 40, final: true };
  assert.equal(requestLine(row), "claude-model-x · 903 in · 900 cache read · 40 out");
  assert.equal(requestLine({ ...row, model: null, cost_usd: 0.5, synthetic: true }),
               "an unnamed model · 903 in · 900 cache read · 40 out · $0.5000 · synthetic");
  assert.equal(itemOf(row).type, "request");
});

test("limits, malformed and other records are notes", () => {
  const limits = itemOf({ kind: "limits", raw_kind: "event_msg", at: at(6), unknown: [], limit_id: "codex",
                          windows: [{ name: "primary", used_percent: 40, window_minutes: 300, resets_at: null, unknown: [] }] });
  assert.deepEqual([limits.type, limits.line], ["note", "Limits (codex): primary 40%"]);
  const bad = itemOf({ kind: "malformed", raw_kind: "", at: at(7), unknown: [], why: "undecodable", unterminated: true });
  assert.equal(bad.line, "A line that can't be read (undecodable, unterminated)");
  const other = itemOf({ kind: "other", raw_kind: "queue-operation", at: at(8), unknown: [], recognised: false });
  assert.equal(other.line, "Unrecognised record queue-operation");
});

test("what the harness did not say is named", () => {
  assert.equal(unknownLine([]), null);
  assert.equal(unknownLine(["time", "cost_usd"]), "Not said: time, cost_usd");
});

// -- the window and its pages -------------------------------------------------------------------

test("the window: loading, up, refused with its reason, failed keeping the last, gone", () => {
  assert.equal(windowView(state({ loading: true })).state, "loading");
  assert.deepEqual(windowView(state({ value: WINDOW })), { state: "up", answer: WINDOW, line: null });
  assert.deepEqual(windowView(state({ refused: "no claude session s9 was found" })),
                   { state: "refused", answer: null, line: "no claude session s9 was found" });
  const failure = { status: 500, why: "sessions' page server answered 500" };
  assert.deepEqual(windowView(state({ value: WINDOW, failure })), { state: "failed", answer: WINDOW, line: failure.why });
  assert.equal(windowView(state({ gone: true })).state, "gone");
});

test("earlier pages go before the newest window, and Earlier reads from the oldest", () => {
  const first = pages(WINDOW, []);
  assert.deepEqual(first.items.map((item) => item.row.text), ["turn 1", "done 1"]);
  assert.equal(first.hasEarlier, true);
  assert.equal(first.before, "4096");
  assert.equal(first.startLine, null);
  const both = pages(WINDOW, [EARLIER]);
  assert.deepEqual(both.items.map((item) => item.row.text), ["turn 0", "turn 1", "done 1"]);
  assert.equal(both.hasEarlier, false);
  assert.equal(both.before, null);
  assert.equal(both.startLine, START_LINE);
  assert.equal(pages({ ...WINDOW, ended: "limit" }, []).limitLine, LIMIT_LINE);
});

test("two records read from one place get keys of their own", () => {
  const twice = { ...WINDOW, records: [message("a", { at: at(9) }), message("b", { at: at(9) })] };
  const keys = pages(twice, []).items.map((item) => item.key);
  assert.equal(new Set(keys).size, 2);
});

test("a refresh keeps the earlier pages only while the window starts where it did", () => {
  assert.equal(keepEarlier(WINDOW, { ...WINDOW, records: [] }), true);
  assert.equal(keepEarlier(WINDOW, { ...WINDOW, before: "5000" }), false);
  assert.equal(keepEarlier(WINDOW, { ...WINDOW, session: "s2" }), false);
  assert.equal(keepEarlier(null, WINDOW), false);
});

// -- subagents ----------------------------------------------------------------------------------

const ENTRY = { agent_id: "a1", session: "s1", agent: "a1", route: "claude/s1/a1", path: "/p/a1.jsonl",
                size: 2048, agent_type: "general-purpose", description: "Look into the failing test", unknown: [] };
const LISTING = { shape: 1, harness: "claude", session: "s1", state: "listed", reason: null,
                  entries: [ENTRY], unreadable_parts: [] };

test("a listed subagent shows its type and description, and opens by its route", () => {
  const view = listingView(state({ value: LISTING }));
  assert.equal(view.state, "listed");
  assert.deepEqual(view.entries, [{ agentId: "a1", type: "general-purpose", description: "Look into the failing test",
                                    route: "claude/s1/a1", target: { harness: "claude", session: "s1", agent: "a1" },
                                    size: "2.0 KB" }]);
});

test("an OpenCode child and a Codex thread open as sessions of their own", () => {
  const child = entryView({ agent_id: "ses_2", session: "ses_2", agent: null, route: "opencode/ses_2",
                            path: "/d/opencode.db", size: null, unknown: [] }, "opencode");
  assert.deepEqual(child.target, { harness: "opencode", session: "ses_2", agent: null });
  assert.equal(child.type, "subagent");
  assert.equal(child.description, null);
  assert.equal(child.size, null);
  const hidden = entryView({ ...ENTRY, description: null }, "claude");
  assert.equal(hidden.description, HIDDEN);
});

test("a listing with none, an unreadable or unsupported one, and a refusal each say why", () => {
  assert.deepEqual(listingView(state({ value: { ...LISTING, state: "none", entries: [] } })),
                   { state: "none", entries: [], line: NO_SUBAGENTS, parts: [] });
  const unreadable = listingView(state({ value: { ...LISTING, state: "unreadable", entries: [],
    reason: "the subagents directory cannot be listed", unreadable_parts: [{ where: "t9", reason: "not found" }] } }));
  assert.equal(unreadable.state, "unreadable");
  assert.equal(unreadable.line, "the subagents directory cannot be listed");
  assert.deepEqual(unreadable.parts, ["t9: not found"]);
  assert.equal(listingView(state({ value: { ...LISTING, state: "unsupported", entries: [], reason: "not yet" } })).line, "not yet");
  assert.deepEqual(listingView(state({ refused: "no claude session s9 was found" })),
                   { state: "refused", entries: [], line: "no claude session s9 was found", parts: [] });
  assert.equal(listingView(state({ loading: true })).state, "loading");
  assert.equal(listingView(state({ gone: true })).state, "gone");
  const failure = { status: 500, why: "x" };
  assert.equal(listingView(state({ value: LISTING, failure })).entries.length, 1);
});
