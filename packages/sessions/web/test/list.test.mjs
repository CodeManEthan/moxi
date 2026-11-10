import assert from "node:assert/strict";
import test from "node:test";

import {
  ACTIVE_RECENTLY, addMore, CLEAR, countLine, filterLine, FIND_PLACEHOLDER, folderMenu, GONE, harnessMenu, statusLine,
  unlistedLine, WAITING, whenMenu, whenOf, whenWords, dateWords, dayAfter, dayLabel, dayWords, EARLIER, earlierLine, FIND_MAX,
  foldersPath, folderWords, groupLabel, harnessWords, HIDE, isFiltered, keyOf, lastSegment, listPath, listView, LOCAL, morePath,
  NO_FILTERS, NO_MATCH, NO_SESSIONS, NO_SESSIONS_SENTENCE, noticeOf, queryOf, refresh, REST_MAX, rowName, rowView,
  sessionPath, SHOW, showMore, showMoreProgram, shownRow, START_ONE, subagentsLead, timeWords, toggleOpen, toggleProgram,
  UNDATED,
} from "../src/list.ts";

// -- fixtures -----------------------------------------------------------------------------------

/** A zone the tests can read: UTC, with a 24-hour clock. */
const UTC = {
  clock: (epoch) => new Date(epoch * 1000).toISOString().slice(11, 16),
  day: (epoch) => new Date(epoch * 1000).toISOString().slice(0, 10),
  start: (day) => Date.parse(`${day}T00:00:00Z`) / 1000,
};
const at = (text) => Date.parse(`${text.replace(" ", "T")}:00Z`) / 1000;

const TODAY = "2026-11-08";
const NOW = at("2026-11-08 19:10");
const NONE = queryOf(NO_FILTERS, TODAY, UTC);

const row = (session, changed, over = {}) => ({
  harness: "claude", session, path: `/root/${session}.jsonl`, changed, size: 100, subagents: 0,
  folder: "/work/proj-a", started: changed - 600, by_program: null, name: null, model: "model-1",
  activity: null, prompt: null, recent: false, ...over,
});
const typed = (when, text) => ({ at: when, text, truncated: false });
const day = (key, rows, over = {}) => ({ key, count: rows.length, rows, before: null, program: { count: 0 }, ...over });
const recent = (rows, over = {}) => ({ key: "recent", count: rows.length, rows, before: null, ...over });
const answer = (groups, over = {}) => ({
  shape: 1, now: NOW, today: TODAY, all: 62, total: 62, unreadable: 0, roots: [],
  harnesses: [{ harness: "claude", count: 62 }], groups, ...over,
});
const stateOf = (groups, over = {}, query = NONE) => refresh(null, answer(groups, over), query).state;
const draw = (state, options = {}) => listView(state, { zone: UTC, ...options });
const names = (rows) => rows.map((one) => one.session);

/**
 * A stand-in for the list's two routes over `sessions`, cut as the page server cuts
 * them: `recent` (30 a page), a day's rows started in a terminal (8, then 30), a day's
 * program rows (30), seven days and then `earlier`, whose days come whole, as many as
 * hold 30 sessions. A place is a strict position in the order: newest change first.
 */
function server(sessions, { now = NOW, today = TODAY } = {}) {
  const order = (a, b) => b.changed - a.changed || a.session.localeCompare(b.session);
  const place = (one) => `${one.changed}:${one.session}`;
  const after = (rows, before) => {
    if (before === null) return rows;
    const [changed, session] = [Number(before.split(":")[0]), before.slice(before.indexOf(":") + 1)];
    return rows.filter((one) => one.changed < changed || (one.changed === changed && one.session > session));
  };
  const page = (rows, before, size) => {
    const left = after(rows, before);
    const shown = left.slice(0, size);
    return { rows: shown, before: left.length > size ? place(shown[shown.length - 1]) : null };
  };
  const cut = () => {
    const all = [...sessions].sort(order)
      .map((one) => ({ ...one, recent: one.by_program !== true && now - one.changed <= 3600 }));
    const days = new Map();
    for (const one of all.filter((each) => !each.recent)) {
      const key = UTC.day(one.changed);
      if (!days.has(key)) days.set(key, { key, main: [], program: [] });
      days.get(key)[one.by_program === true ? "program" : "main"].push(one);
    }
    return { all, recent: all.filter((one) => one.recent), days: [...days.values()] };
  };
  const dayGroup = (one) => ({ key: one.key, count: one.main.length, ...page(one.main, null, 8),
                               program: { count: one.program.length } });
  const size = (one) => one.main.length + one.program.length;
  return {
    sessions,
    list() {
      const { all, recent: first, days } = cut();
      const groups = [{ key: "recent", count: first.length, ...page(first, null, 30) }, ...days.slice(0, 7).map(dayGroup)];
      const older = days.slice(7);
      if (older.length) {
        const last = older[older.length - 1];
        groups.push({ key: "earlier", count: older.reduce((sum, one) => sum + size(one), 0),
                      oldest: [...last.main, ...last.program].sort(order).pop().changed, before: `day:${older[0].key}` });
      }
      return { shape: 1, now, today, all: all.length, total: all.length, unreadable: 0, roots: [],
               harnesses: [{ harness: "claude", count: all.length }], groups };
    },
    more(request) {
      const { recent: first, days } = cut();
      if (request.group === "earlier") {
        const older = days.slice(7).filter((one) => `day:${one.key}` <= request.before);
        let held = 0;
        const taken = [];
        while (older.length && held < 30) {
          taken.push(older.shift());
          held += size(taken[taken.length - 1]);
        }
        return { shape: 1, groups: taken.map(dayGroup), before: older.length ? `day:${older[0].key}` : null,
                 left: older.reduce((sum, one) => sum + size(one), 0) };
      }
      const found = days.find((one) => one.key === request.group);
      const rows = request.group === "recent" ? first : request.kind === "program" ? found?.program ?? [] : found?.main ?? [];
      return { shape: 1, group: request.group, kind: request.kind, ...page(rows, request.before, 30) };
    },
  };
}

/** Makes every read of `reads`, each with the reads that follow it. */
function settle(state, reads, routes) {
  let next = state;
  for (const read of reads) {
    for (let request = read; request !== null;) {
      const added = addMore(next, request, routes.more(request));
      next = added.state;
      request = added.next;
    }
  }
  return next;
}

/** One `Show` on a group, with its read made. */
const show = (state, key, routes) => settle(state, [showMore(state, key)], routes);
const group = (state, key) => state.groups.find((one) => one.key === key);
/** Every session the kept list holds, with how many times. */
function held(state) {
  const seen = new Map();
  for (const one of state.groups) {
    for (const each of [...one.rows, ...(one.program?.rows ?? [])]) seen.set(each.session, (seen.get(each.session) ?? 0) + 1);
  }
  return seen;
}
const noneTwice = (state) => assert.deepEqual([...held(state)].filter(([, n]) => n > 1), []);

/** `n` sessions of the day `key`, a minute apart, the newest first: `<name>01` is newest. */
const run = (name, key, n, over = {}) => Array.from({ length: n }, (_, i) =>
  row(`${name}${String(i + 1).padStart(2, "0")}`, at(`${key} 12:00`) - i * 60, over));

// -- days and times -----------------------------------------------------------------------------

test("a day group is labelled Today, Yesterday, then by weekday and date", () => {
  assert.equal(dayLabel("2026-11-08", TODAY), "Today");
  assert.equal(dayLabel("2026-11-07", TODAY), "Yesterday");
  assert.equal(dayLabel("2026-11-06", TODAY), "Fri Nov 6");
  assert.equal(dayLabel("2026-11-02", TODAY), "Mon Nov 2");
  assert.equal(dayLabel("2026-09-27", TODAY), "Sun Sep 27");
});

test("a day of another year than the server's today carries its year", () => {
  assert.equal(dayLabel("2025-12-31", TODAY), "Wed Dec 31, 2025");
  assert.equal(dateWords("2025-09-27", TODAY), "Sep 27, 2025");
  assert.equal(dateWords("2026-09-27", TODAY), "Sep 27");
  assert.equal(dayLabel("2025-12-31", "2026-01-01"), "Yesterday");
});

test("the day words are taken against the server's day, not the browser's clock", () => {
  // The same day reads differently as the server's today moves.
  assert.equal(dayWords("2026-11-08", "2026-11-08"), "today");
  assert.equal(dayWords("2026-11-08", "2026-11-09"), "yesterday");
  assert.equal(dayWords("2026-11-08", "2026-11-10"), "Nov 8");
  assert.equal(dayWords("2026-11-09", "2026-11-08"), "Nov 9");
});

test("the day before and after crosses months and years", () => {
  assert.equal(dayAfter("2026-11-01", -1), "2026-10-31");
  assert.equal(dayAfter("2026-01-01", -1), "2025-12-31");
  assert.equal(dayAfter("2028-02-28", 1), "2028-02-29");
  assert.equal(dayAfter("2026-11-08", -6), "2026-11-02");
});

test("a time is its clock time on its group's day, and says its own day on another", () => {
  assert.equal(timeWords(at("2026-11-08 19:09"), TODAY, TODAY, UTC), "19:09");
  assert.equal(timeWords(at("2026-11-07 22:50"), TODAY, TODAY, UTC), "yesterday 22:50");
  assert.equal(timeWords(at("2026-11-07 22:50"), "2026-11-07", TODAY, UTC), "22:50");
  assert.equal(timeWords(at("2026-11-06 22:10"), "2026-11-07", TODAY, UTC), "Nov 6 22:10");
  assert.equal(timeWords(at("2026-11-08 00:10"), "2026-11-07", TODAY, UTC), "today 00:10");
});

test("the browser's zone formats a time as ui does, and cuts days at local midnight", () => {
  const epoch = at("2026-11-08 19:09");
  assert.equal(LOCAL.clock(epoch), new Date(epoch * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
  const its = LOCAL.day(epoch);
  assert.match(its, /^\d{4}-\d{2}-\d{2}$/);
  assert.ok(LOCAL.start(its) <= epoch && epoch < LOCAL.start(dayAfter(its, 1)));
  assert.equal(LOCAL.day(LOCAL.start(its)), its);
  assert.ok(Number.isNaN(LOCAL.start("soon")));
});

// -- the filters and the requests ---------------------------------------------------------------

test("no filter asks for the list with no query, and is not a filtered list", () => {
  assert.deepEqual(NONE, { harness: null, since: null, until: null, folder: null, find: null });
  assert.equal(isFiltered(NONE), false);
  assert.equal(listPath(NONE), "api/list");
  assert.equal(foldersPath(NONE), "api/folders");
});

test("each When is whole calendar days of the server's today", () => {
  const when = (value) => queryOf({ ...NO_FILTERS, when: value }, TODAY, UTC);
  assert.deepEqual(when({ kind: "any" }), NONE);
  assert.equal(when({ kind: "today" }).since, at("2026-11-08 00:00"));
  assert.equal(when({ kind: "today" }).until, null);
  assert.equal(when({ kind: "last", days: 7 }).since, at("2026-11-02 00:00"));
  assert.equal(when({ kind: "last", days: 30 }).since, at("2026-10-10 00:00"));
  const range = when({ kind: "range", from: "2026-10-01", to: "2026-10-03" });
  assert.deepEqual([range.since, range.until], [at("2026-10-01 00:00"), at("2026-10-04 00:00")]);
  assert.deepEqual(when({ kind: "range", from: null, to: "2026-10-03" }).since, null);
  assert.deepEqual(when({ kind: "range", from: "2026-10-01", to: null }).until, null);
  assert.deepEqual(when({ kind: "range", from: "soon", to: null }), NONE);
});

test("the find field sends its trimmed text from two characters on, and nothing before", () => {
  const find = (text) => queryOf({ ...NO_FILTERS, find: text }, TODAY, UTC).find;
  assert.equal(find(""), null);
  assert.equal(find(" a "), null);
  assert.equal(find(" ab "), "ab");
  assert.equal(find("0190b000"), "0190b000");
  assert.equal(find("x".repeat(FIND_MAX + 9)).length, FIND_MAX);
  assert.equal(isFiltered(queryOf({ ...NO_FILTERS, find: "a" }, TODAY, UTC)), false);
  assert.equal(isFiltered(queryOf({ ...NO_FILTERS, find: "ab" }, TODAY, UTC)), true);
});

test("a set filter is a key of the list's query, and an unset one is left out", () => {
  const query = queryOf({ folder: "/work/proj a", harness: "codex", when: { kind: "today" }, find: "proj" }, TODAY, UTC);
  assert.equal(listPath(query), `api/list?harness=codex&since=${at("2026-11-08 00:00")}&folder=%2Fwork%2Fproj+a&find=proj`);
  assert.equal(foldersPath(query), `api/folders?harness=codex&since=${at("2026-11-08 00:00")}&find=proj`);
  assert.equal(listPath(queryOf({ ...NO_FILTERS, harness: "", folder: "" }, TODAY, UTC)), "api/list");
  for (const filters of [{ ...NO_FILTERS, harness: "claude" }, { ...NO_FILTERS, folder: "/work" },
                         { ...NO_FILTERS, when: { kind: "last", days: 7 } }]) {
    assert.equal(isFiltered(queryOf(filters, TODAY, UTC)), true);
  }
});

test("a read of more rows names its group, kind and place, the paged list's now and its filters", () => {
  const query = queryOf({ ...NO_FILTERS, harness: "claude" }, TODAY, UTC);
  const request = { group: "2026-11-07", kind: "main", before: "17:a/b", now: 1783000000.123456, pages: 1, check: false };
  assert.equal(morePath(request, query), "api/more?group=2026-11-07&kind=main&before=17%3Aa%2Fb&now=1783000000.123456&harness=claude");
  assert.equal(morePath({ ...request, kind: "program", before: null }, NONE), "api/more?group=2026-11-07&kind=program&now=1783000000.123456");
  assert.equal(morePath({ ...request, group: "earlier", kind: null }, NONE), "api/more?group=earlier&before=17%3Aa%2Fb&now=1783000000.123456");
  assert.equal(sessionPath("claude", "s 1"), "api/session?harness=claude&session=s+1");
});

// -- a row's name -------------------------------------------------------------------------------

test("a row is named for every combination of a name, a folder and a start that was or was not read", () => {
  const started = at("2026-11-08 18:45");
  const name = (over) => rowName(row("s1", NOW, { name: null, folder: null, started: null, ...over }), TODAY, UTC);
  const cases = [
    [{ name: "fix-tooltips", folder: "/work/proj-a", started }, { name: "fix-tooltips", where: "proj-a" }],
    [{ name: "fix-tooltips", folder: "/work/proj-a" }, { name: "fix-tooltips", where: "proj-a" }],
    [{ name: "fix-tooltips", started }, { name: "fix-tooltips", where: null }],
    [{ name: "fix-tooltips" }, { name: "fix-tooltips", where: null }],
    [{ folder: "/work/proj-a", started }, { name: "proj-a · started today 18:45", where: null }],
    [{ folder: "/work/proj-a" }, { name: "proj-a", where: null }],
    [{ started }, { name: "Claude Code session · started today 18:45", where: null }],
    [{}, { name: "Claude Code session", where: null }],
  ];
  for (const [over, want] of cases) assert.deepEqual(name(over), want, JSON.stringify(over));
});

test("a start is worded against the server's day: today, yesterday, then its date", () => {
  const name = (started, today = TODAY) => rowName(row("s1", NOW, { started }), today, UTC).name;
  assert.equal(name(at("2026-11-08 18:45")), "proj-a · started today 18:45");
  assert.equal(name(at("2026-11-07 22:25")), "proj-a · started yesterday 22:25");
  assert.equal(name(at("2026-11-06 21:30")), "proj-a · started Nov 6 21:30");
  assert.equal(name(at("2025-11-06 21:30")), "proj-a · started Nov 6, 2025 21:30");
  assert.equal(name(at("2026-11-08 18:45"), "2026-11-09"), "proj-a · started yesterday 18:45");
});

test("a blank name is no name, a folder is its last segment, and each harness has its words", () => {
  assert.deepEqual(rowName(row("s1", NOW, { name: "  ", started: null }), TODAY, UTC), { name: "proj-a", where: null });
  assert.deepEqual(rowName(row("s1", NOW, { name: " plan ", folder: "" }), TODAY, UTC), { name: "plan", where: null });
  assert.equal(lastSegment("/work/proj-a/"), "proj-a");
  assert.equal(lastSegment("proj-b"), "proj-b");
  assert.equal(lastSegment("/"), "/");
  assert.equal(rowName(row("t1", NOW, { harness: "codex", folder: null, started: null }), TODAY, UTC).name, "Codex session");
  assert.equal(rowName(row("o1", NOW, { harness: "opencode", folder: null, started: null }), TODAY, UTC).name, "OpenCode session");
  assert.equal(harnessWords("gemini"), "gemini");
});

test("a row is never named by its id or by anything said in it", () => {
  const view = rowView(row("0190b000-0000-7000-8000-00000000c1a0", NOW, {
    folder: null, started: null, activity: { at: NOW, who: "agent", text: "A title a harness made" },
  }), TODAY, TODAY, false, UTC);
  assert.equal(view.name, "Claude Code session");
  assert.equal(view.where, null);
});

// -- a row --------------------------------------------------------------------------------------

test("line two is the typed message: its time, You: and its text, with Open as the one act", () => {
  const view = rowView(row("s1", NOW, {
    folder: "/work/kestrel-web", started: at("2026-11-08 18:45"),
    prompt: typed(at("2026-11-08 19:09"), "Run the visual regression suite and report every diff over 0.5%."),
  }), TODAY, TODAY, false, UTC);
  assert.deepEqual(view, {
    key: "claude/s1", harness: "claude", session: "s1", route: "claude/s1",
    name: "kestrel-web · started today 18:45", where: null, fact: null,
    lead: null, time: "19:09", saidBy: "you",
    rest: "Run the visual regression suite and report every diff over 0.5%.",
    lines: 2, act: "Open", open: false, on: TODAY,
  });
  assert.equal(rowView(row("s1", NOW), "2026-11-06", TODAY, false, UTC).on, "2026-11-06");
});

test("a row with no typed message is one line with no act, whatever was said in it last", () => {
  for (const activity of [null, { at: NOW, who: "agent", text: "Done. I changed three files." },
                          { at: NOW, who: "you", text: "typed, says the last turn" }, { at: NOW, who: null, text: "injected" }]) {
    const view = rowView(row("s1", NOW, { activity, prompt: null }), TODAY, TODAY, false, UTC);
    assert.deepEqual([view.lines, view.lead, view.time, view.saidBy, view.rest, view.act], [1, null, null, null, null, null]);
  }
  const opened = rowView(row("s1", NOW), TODAY, TODAY, true, UTC);
  assert.deepEqual([opened.lines, opened.act, opened.open], [1, null, true]);
});

test("the subagent count leads line two, and alone still makes a second line", () => {
  assert.equal(subagentsLead(row("s1", NOW, { subagents: 2 })), "2 subagents");
  assert.equal(subagentsLead(row("s1", NOW, { subagents: 1 })), "1 subagent");
  assert.equal(subagentsLead(row("s1", NOW, { subagents: 0 })), null);
  assert.equal(subagentsLead(row("s1", NOW, { subagents: null })), null);
  const alone = rowView(row("s1", NOW, { subagents: 3 }), TODAY, TODAY, false, UTC);
  assert.deepEqual([alone.lines, alone.lead, alone.time, alone.saidBy, alone.rest, alone.act], [2, "3 subagents", null, null, null, "Open"]);
  const both = rowView(row("s1", NOW, { subagents: 2, prompt: typed(at("2026-11-08 08:55"), "Find out why.") }), TODAY, TODAY, false, UTC);
  assert.deepEqual([both.lead, both.time, both.saidBy, both.rest], ["2 subagents", "08:55", "you", "Find out why."]);
});

test("a session a program started never shows You: or a typed message", () => {
  const view = rowView(row("s1", NOW, { by_program: true, prompt: typed(NOW, "not a person's") }), TODAY, TODAY, false, UTC);
  assert.deepEqual([view.lines, view.time, view.saidBy, view.rest, view.act], [1, null, null, null, null]);
});

test("the time before line two's text is the typed message's own, with its day when that is not the group's", () => {
  const view = (when, on) => rowView(row("s1", NOW, { prompt: typed(when, "hello") }), on, TODAY, false, UTC).time;
  assert.equal(view(at("2026-11-08 19:09"), TODAY), "19:09");
  assert.equal(view(at("2026-11-07 23:58"), TODAY), "yesterday 23:58");
  assert.equal(view(at("2026-11-05 14:10"), "2026-11-06"), "Nov 5 14:10");
  assert.equal(view(null, TODAY), null);
  // A message with no time read still shows, without one.
  const untimed = rowView(row("s1", NOW, { prompt: typed(null, "hello") }), TODAY, TODAY, false, UTC);
  assert.deepEqual([untimed.saidBy, untimed.rest, untimed.lines], ["you", "hello", 2]);
});

test("line two's text is one line of at most 300 characters, and an empty one is no line", () => {
  const view = (text) => rowView(row("s1", NOW, { prompt: typed(NOW, text) }), TODAY, TODAY, false, UTC);
  assert.equal(view("  first\n\nsecond\tthird  ").rest, "first second third");
  assert.equal([...view("é".repeat(REST_MAX + 40)).rest].length, REST_MAX);
  assert.deepEqual([view(" \n ").lines, view(" \n ").saidBy, view(" \n ").time], [1, null, null]);
  assert.deepEqual([view(null).lines, view(null).rest], [1, null]);
});

test("nothing the list reads stands at the right end of line one", () => {
  const view = rowView(row("s1", NOW, { model: "model-1", size: 9, prompt: typed(NOW, "hello"), subagents: 2 }), TODAY, TODAY, false, UTC);
  assert.equal(view.fact, null);
});

test("an opened row's act reads Close, and a row of a harness the page has no view for has no route", () => {
  const prompt = typed(NOW, "hello");
  assert.equal(rowView(row("s1", NOW, { prompt }), TODAY, TODAY, true, UTC).act, "Close");
  assert.equal(rowView(row("a b", NOW, { harness: "codex" }), TODAY, TODAY, false, UTC).route, "codex/a%20b");
  assert.equal(rowView(row("s1", NOW, { harness: "gemini" }), TODAY, TODAY, false, UTC).route, null);
});

// -- the groups ---------------------------------------------------------------------------------

test("the groups are Active recently, the days newest first, then Earlier", () => {
  const view = draw(stateOf([
    recent([row("r1", at("2026-11-08 19:09"), { recent: true })]),
    day("2026-11-08", [row("a1", at("2026-11-08 17:35"))]),
    day("2026-11-07", [row("b1", at("2026-11-07 22:50"))]),
    day("2026-11-06", [row("c1", at("2026-11-06 22:10"))]),
    { key: "earlier", count: 37, oldest: at("2026-09-27 10:00"), before: "p" },
  ]));
  assert.deepEqual(view.groups.map((one) => [one.key, one.label]), [
    ["recent", "Active recently"], ["2026-11-08", "Today"], ["2026-11-07", "Yesterday"],
    ["2026-11-06", "Fri Nov 6"], ["earlier", "Earlier"],
  ]);
  assert.deepEqual(view.groups.map((one) => one.rows.map((each) => each.session)), [["r1"], ["a1"], ["b1"], ["c1"], []]);
  assert.equal(ACTIVE_RECENTLY, "Active recently");
  assert.equal(EARLIER, "Earlier");
});

test("a group with no session of either kind is not drawn", () => {
  const view = draw(stateOf([recent([]), day("2026-11-08", []), day("2026-11-07", [row("b1", at("2026-11-07 22:50"))])]));
  assert.deepEqual(view.groups.map((one) => one.key), ["2026-11-07"]);
});

test("a group that holds more than its rows ends with N more and Show, counted from its sessions started in a terminal", () => {
  const rows = run("a", "2026-11-07", 8);
  const view = draw(stateOf([day("2026-11-07", rows, { count: 20, before: "p", program: { count: 5 } })]));
  assert.deepEqual(view.groups[0].more, { text: "12 more", act: "Show" });
  // Whole in its rows: no line, whatever its count.
  assert.equal(draw(stateOf([day("2026-11-07", rows)])).groups[0].more, null);
  assert.equal(draw(stateOf([day("2026-11-07", rows, { count: 9, before: "p" })])).groups[0].more.text, "1 more");
  assert.equal(draw(stateOf([recent(run("r", "2026-11-08", 30), { count: 1234 + 30, before: "p" })])).groups[0].more.text, "1,234 more");
});

test("a day's program line gives its count with Show, and is absent at zero", () => {
  const some = draw(stateOf([day("2026-11-07", [row("b1", at("2026-11-07 22:50"))], { program: { count: 3 } })])).groups[0];
  assert.deepEqual(some.program, { text: "3 started by a program", act: "Show", open: false, rows: [], more: null });
  assert.equal(draw(stateOf([day("2026-11-07", [row("b1", at("2026-11-07 22:50"))])])).groups[0].program, null);
  const one = draw(stateOf([day("2026-11-07", [], { program: { count: 1 } })])).groups[0];
  assert.equal(one.program.text, "1 started by a program");
  assert.equal(draw(stateOf([recent([row("r1", NOW)])])).groups[0].program, null);
});

test("a day that holds only program sessions is its label and that line", () => {
  const only = draw(stateOf([day("2026-11-07", [], { program: { count: 4 } })])).groups;
  assert.equal(only.length, 1);
  assert.deepEqual([only[0].label, only[0].rows, only[0].more, only[0].program.text], ["Yesterday", [], null, "4 started by a program"]);
});

test("Show opens a day's program sessions under their line, which then reads Hide, and Hide lets them go", () => {
  const programs = run("p", "2026-11-07", 40, { by_program: true });
  const routes = server([...run("a", "2026-11-07", 3), ...programs]);
  let state = refresh(null, routes.list(), NONE).state;
  const shown = toggleProgram(state, "2026-11-07");
  assert.deepEqual(shown.read, { group: "2026-11-07", kind: "program", before: null, now: NOW, pages: 1, check: false });
  // Opened and not yet read: the line already reads Hide, with no row.
  assert.deepEqual(draw(shown.state).groups[0].program, { text: "40 started by a program", act: "Hide", open: true, rows: [], more: null });
  state = settle(shown.state, [shown.read], routes);
  let line = draw(state).groups[0].program;
  assert.deepEqual([line.text, line.act, line.rows.length, line.more], ["40 started by a program", HIDE, 30, { text: "10 more", act: SHOW }]);
  assert.deepEqual(line.rows.map((one) => one.session), names(programs.slice(0, 30)));
  assert.ok(line.rows.every((one) => one.saidBy === null));
  // The day's own count line does not move.
  assert.equal(draw(state).groups[0].more, null);
  assert.equal(draw(state).groups[0].rows.length, 3);
  state = settle(state, [showMoreProgram(state, "2026-11-07")], routes);
  line = draw(state).groups[0].program;
  assert.deepEqual([line.rows.length, line.more], [40, null]);
  assert.equal(showMoreProgram(state, "2026-11-07"), null);
  const hidden = toggleProgram(state, "2026-11-07");
  assert.equal(hidden.read, null);
  assert.deepEqual(draw(hidden.state).groups[0].program, { text: "40 started by a program", act: "Show", open: false, rows: [], more: null });
  assert.equal(toggleProgram(state, "recent").read, null);
});

test("Earlier reads N sessions, back to the oldest one's date, with Show", () => {
  const line = (over) => earlierLine({ key: "earlier", count: 37, oldest: at("2026-09-27 10:00"), ...over }, TODAY, UTC);
  assert.deepEqual(line({}), { text: "37 sessions, back to Sep 27", act: "Show" });
  assert.equal(line({ count: 1 }).text, "1 session, back to Sep 27");
  assert.equal(line({ count: 4610 }).text, "4,610 sessions, back to Sep 27");
  assert.equal(line({ oldest: at("2025-03-02 10:00") }).text, "37 sessions, back to Mar 2, 2025");
  assert.equal(line({ oldest: null }).text, "37 sessions");
  const view = draw(stateOf([{ key: "earlier", count: 37, oldest: at("2026-09-27 10:00"), before: "p" }])).groups[0];
  assert.deepEqual([view.label, view.rows, view.more, view.program, view.earlier],
                   ["Earlier", [], null, null, { text: "37 sessions, back to Sep 27", act: "Show" }]);
});

test("the find field's results are one group with no label, and the days with no change time have their own", () => {
  assert.equal(groupLabel("found", TODAY), null);
  assert.equal(groupLabel("undated", TODAY), UNDATED);
  const query = queryOf({ ...NO_FILTERS, find: "proj" }, TODAY, UTC);
  const found = { key: "found", count: 31, before: "p",
                  rows: [row("p1", at("2026-11-06 12:00"), { by_program: true }),
                         row("m1", at("2026-10-28 16:43"), { prompt: typed(at("2026-10-28 16:43"), "Rotate the certificates.") })] };
  const view = draw(stateOf([found], { total: 31 }, query));
  assert.deepEqual(view.groups.map((one) => [one.key, one.label, one.more]), [["found", null, { text: "29 more", act: "Show" }]]);
  // With no day over them, a time says its own day unless it is today's.
  assert.equal(view.groups[0].rows[1].time, "Oct 28 16:43");
  assert.equal(view.count, "31 of 62 sessions");
});

// -- the count line -----------------------------------------------------------------------------

test("the count is M sessions, and N of M sessions when a filter or the find field leaves some out", () => {
  const some = [day("2026-11-07", [row("b1", at("2026-11-07 22:50"))])];
  assert.equal(countLine(stateOf(some)), "62 sessions");
  assert.equal(countLine(stateOf(some, { all: 1, total: 1 })), "1 session");
  assert.equal(countLine(stateOf(some, { all: 5165, total: 5165 })), "5,165 sessions");
  const filtered = queryOf({ ...NO_FILTERS, harness: "claude" }, TODAY, UTC);
  assert.equal(countLine(stateOf(some, { total: 15 }, filtered)), "15 of 62 sessions");
  assert.equal(countLine(stateOf(some, { total: 0 }, filtered)), "0 of 62 sessions");
  // A filter that leaves nothing out is counted as the list with none.
  assert.equal(countLine(stateOf(some, { total: 62 }, filtered)), "62 sessions");
  assert.equal(countLine(stateOf(some, { all: 1, total: 1 }, filtered)), "1 session");
  const found = queryOf({ ...NO_FILTERS, find: "proj" }, TODAY, UTC);
  assert.equal(countLine(stateOf(some, { total: 62 }, found)), "62 sessions");
  assert.equal(countLine(stateOf(some, { total: 3 }, found)), "3 of 62 sessions");
  assert.equal(countLine(stateOf(some, { all: 5165, total: 1200 }, filtered)), "1,200 of 5,165 sessions");
  assert.equal(countLine(stateOf([], { all: 0, total: 0 })), null);
});

// -- empty states and notices -------------------------------------------------------------------

test("a machine with no session reads No sessions yet, with the link only where the launcher is", () => {
  const state = stateOf([recent([])], { all: 0, total: 0, harnesses: [] });
  assert.deepEqual(draw(state), {
    count: null, groups: [], notices: [], unlisted: null,
    empty: { line: "No sessions yet.", sentence: NO_SESSIONS_SENTENCE, start: null },
  });
  assert.equal(draw(state, { launcher: true }).empty.start, "Start one from Home");
  assert.equal(NO_SESSIONS, "No sessions yet.");
  assert.equal(START_ONE, "Start one from Home");
  assert.equal(NO_SESSIONS_SENTENCE, "Moxi lists every Claude Code, Codex and OpenCode session on this machine, "
    + "whether it was started here or in a terminal. It looked and found none.");
});

test("an empty list with a root that could not be read does not say there are no sessions", () => {
  const roots = [{ harness: "claude", root: "/root", reason: "permission denied", parts: null }];
  const view = draw(stateOf([recent([])], { all: 0, total: 0, harnesses: [], roots }), { launcher: true });
  assert.equal(view.empty, null);
  assert.equal(view.count, null);
  assert.deepEqual(view.groups, []);
  assert.deepEqual(view.notices, [{ harness: "claude", line: "Moxi couldn't read Claude Code's sessions", reason: "permission denied" }]);
});

test("a list a filter or the find field leaves empty reads No sessions match, with the count", () => {
  for (const filters of [{ ...NO_FILTERS, harness: "codex" }, { ...NO_FILTERS, find: "zz" }]) {
    const query = queryOf(filters, TODAY, UTC);
    const groups = query.find === null ? [recent([])] : [{ key: "found", count: 0, rows: [], before: null }];
    const view = draw(stateOf(groups, { total: 0 }, query), { launcher: true });
    assert.deepEqual(view.empty, { line: "No sessions match.", sentence: null, start: null });
    assert.equal(view.count, "0 of 62 sessions");
    assert.deepEqual(view.groups, []);
  }
  assert.equal(NO_MATCH, "No sessions match.");
});

test("a harness whose root could not be listed gets a line with the reason, beside the rows that were read", () => {
  const roots = [{ harness: "codex", root: "/root/codex", reason: "[Errno 13] Permission denied", parts: null },
                 { harness: "opencode", root: null, reason: null, parts: null }];
  const view = draw(stateOf([day("2026-11-07", [row("b1", at("2026-11-07 22:50"))])], { roots }));
  assert.equal(view.groups.length, 1);
  assert.equal(view.empty, null);
  assert.deepEqual(view.notices, [
    { harness: "codex", line: "Moxi couldn't read Codex's sessions", reason: "[Errno 13] Permission denied" },
    { harness: "opencode", line: "Moxi couldn't read OpenCode's sessions", reason: null },
  ]);
});

test("a root listed with parts that could not be read gets a line saying the counts leave them out", () => {
  assert.deepEqual(noticeOf({ harness: "claude", root: "/root", reason: null, parts: 3 }),
                   { harness: "claude", line: "Moxi couldn't read 3 parts of Claude Code's sessions; the counts leave them out", reason: null });
  assert.equal(noticeOf({ harness: "codex", root: "/root", reason: null, parts: 1 }).line,
               "Moxi couldn't read 1 part of Codex's sessions; the counts leave them out");
  assert.equal(noticeOf({ harness: "gemini", root: null, reason: "no such root", parts: null }).line,
               "Moxi couldn't read gemini's sessions");
});

// -- paging -------------------------------------------------------------------------------------

test("Show on N more adds the group's next rows, none twice and none missed, until the line goes", () => {
  const sessions = run("a", "2026-11-07", 75);
  const routes = server(sessions);
  let state = refresh(null, routes.list(), NONE).state;
  assert.deepEqual(names(group(state, "2026-11-07").rows), names(sessions.slice(0, 8)));
  assert.equal(draw(state).groups[0].more.text, "67 more");
  const request = showMore(state, "2026-11-07");
  assert.deepEqual(request, { group: "2026-11-07", kind: "main", before: group(state, "2026-11-07").before, now: NOW, pages: 1, check: false });
  state = show(state, "2026-11-07", routes);
  assert.equal(draw(state).groups[0].more.text, "37 more");
  state = show(state, "2026-11-07", routes);
  state = show(state, "2026-11-07", routes);
  assert.deepEqual(names(group(state, "2026-11-07").rows), names(sessions));
  assert.equal(draw(state).groups[0].more, null);
  assert.equal(showMore(state, "2026-11-07"), null);
  noneTwice(state);
});

test("Active recently pages by thirty", () => {
  const sessions = run("r", "2026-11-08", 45).map((one, i) => ({ ...one, changed: NOW - 60 - i }));
  const routes = server(sessions);
  let state = refresh(null, routes.list(), NONE).state;
  assert.deepEqual([draw(state).groups[0].label, draw(state).groups[0].rows.length, draw(state).groups[0].more.text],
                   ["Active recently", 30, "15 more"]);
  state = show(state, "recent", routes);
  assert.deepEqual([draw(state).groups[0].rows.length, draw(state).groups[0].more], [45, null]);
});

test("an answer that brings a session already shown does not add it again", () => {
  const routes = server(run("a", "2026-11-07", 20));
  const state = refresh(null, routes.list(), NONE).state;
  const request = showMore(state, "2026-11-07");
  const again = { shape: 1, group: "2026-11-07", kind: "main", before: null,
                  rows: [group(state, "2026-11-07").rows[2], row("new", at("2026-11-07 01:00"))] };
  const added = addMore(state, request, again).state;
  assert.deepEqual(names(group(added, "2026-11-07").rows).slice(8), ["new"]);
  noneTwice(added);
});

test("an answer to a read of an older list, or from a place the group has moved on from, changes nothing", () => {
  const routes = server(run("a", "2026-11-07", 75));
  const state = refresh(null, routes.list(), NONE).state;
  const request = showMore(state, "2026-11-07");
  const once = addMore(state, request, routes.more(request)).state;
  // The same Show pressed twice: its second answer is of a place already passed.
  assert.equal(addMore(once, request, routes.more(request)).state, once);
  // A newer list was read while the answer was on its way.
  const newer = refresh(state, { ...routes.list(), now: NOW + 30 }, NONE).state;
  assert.equal(addMore(newer, request, routes.more(request)).state, newer);
  // A group that is no longer there, and a program line that was hidden meanwhile.
  assert.equal(addMore(state, { ...request, group: "2026-01-01" }, routes.more(request)).state, state);
  assert.equal(addMore(state, { ...request, kind: "program", before: null }, routes.more(request)).state, state);
});

test("Show on Earlier adds its next older days whole, above it, and its line counts what is left", () => {
  const sessions = [];
  for (let n = 0; n < 12; n += 1) sessions.push(...run(`d${String(n).padStart(2, "0")}-`, dayAfter("2026-11-07", -n), 12));
  const routes = server(sessions);
  let state = refresh(null, routes.list(), NONE).state;
  let view = draw(state);
  assert.equal(view.groups.length, 8);
  assert.deepEqual(view.groups[7].earlier, { text: "60 sessions, back to Oct 27", act: "Show" });
  assert.deepEqual(showMore(state, "earlier"), { group: "earlier", kind: null, before: "day:2026-10-31", now: NOW, pages: 1, check: false });
  state = show(state, "earlier", routes);
  view = draw(state);
  // Three days hold thirty sessions between them: each arrives as a day, folded at 8.
  assert.deepEqual(view.groups.slice(7).map((one) => [one.label, one.rows.length, one.more?.text ?? null, one.earlier?.text ?? null]), [
    ["Sat Oct 31", 8, "4 more", null], ["Fri Oct 30", 8, "4 more", null], ["Thu Oct 29", 8, "4 more", null],
    ["Earlier", 0, null, "24 sessions, back to Oct 27"],
  ]);
  // A day that Earlier opened pages like any other.
  state = show(state, "2026-10-30", routes);
  assert.equal(draw(state).groups[8].rows.length, 12);
  state = show(state, "earlier", routes);
  view = draw(state);
  assert.deepEqual(view.groups.map((one) => one.key).slice(-3), ["2026-10-29", "2026-10-28", "2026-10-27"]);
  assert.equal(showMore(state, "earlier"), null);
  noneTwice(state);
});

// -- the refresh's merge ------------------------------------------------------------------------

test("a newer list replaces the rows of a group that was not opened past its first rows", () => {
  const sessions = run("a", "2026-11-07", 6);
  const routes = server(sessions);
  const kept = refresh(null, routes.list(), NONE).state;
  sessions[3] = { ...sessions[3], name: "renamed" };
  sessions.push(row("new", at("2026-11-07 13:00")));
  const { state, reads } = refresh(kept, routes.list(), NONE);
  assert.deepEqual(reads, []);
  assert.deepEqual(names(group(state, "2026-11-07").rows), ["new", ...names(sessions.slice(0, 6))]);
  assert.equal(group(state, "2026-11-07").rows[4].name, "renamed");
  noneTwice(state);
});

test("rows read past a group's first stand over a refresh that changed neither its count nor its first row", () => {
  const sessions = run("a", "2026-11-07", 75);
  const routes = server(sessions);
  let kept = refresh(null, routes.list(), NONE).state;
  kept = show(show(kept, "2026-11-07", routes), "2026-11-07", routes);
  assert.equal(group(kept, "2026-11-07").rows.length, 68);
  // A fact of a row in the first eight changed without moving it: the answer's row is the one shown.
  sessions[2] = { ...sessions[2], subagents: 4 };
  const { state, reads } = refresh(kept, { ...routes.list(), now: NOW + 30 }, NONE);
  assert.deepEqual(reads, []);
  assert.deepEqual(names(group(state, "2026-11-07").rows), names(sessions.slice(0, 68)));
  assert.equal(group(state, "2026-11-07").rows[2].subagents, 4);
  assert.equal(draw(state).groups[0].more.text, "7 more");
  // The next Show goes on from the place the kept rows ended at, in the newer list.
  const request = showMore(state, "2026-11-07");
  assert.deepEqual([request.before, request.now], [group(kept, "2026-11-07").before, NOW + 30]);
  noneTwice(state);
});

test("a session that changes leaves the rows kept past the first, and its group is read again to the same depth", () => {
  const sessions = run("a", "2026-11-07", 75);
  const routes = server(sessions);
  let kept = refresh(null, routes.list(), NONE).state;
  kept = show(show(kept, "2026-11-07", routes), "2026-11-07", routes);
  // A session from deep in the day is written to: it is active recently now.
  const moved = sessions[40].session;
  sessions[40] = { ...sessions[40], changed: NOW + 20 };
  const later = server(sessions, { now: NOW + 30 });
  const { state, reads } = refresh(kept, later.list(), NONE);
  // Before the reads land, no kept row stands in the day: only the answer's own.
  assert.equal(group(state, "2026-11-07").rows.length, 8);
  assert.deepEqual(names(group(state, "recent").rows), [moved]);
  assert.deepEqual(reads, [{ group: "2026-11-07", kind: "main", before: group(state, "2026-11-07").before, now: NOW + 30, pages: 2, check: false }]);
  const settled = settle(state, reads, later);
  const left = names(sessions).filter((name) => name !== moved);
  assert.deepEqual(names(group(settled, "2026-11-07").rows), left.slice(0, 68));
  assert.equal(draw(settled).groups[1].more.text, "6 more");
  noneTwice(settled);
  assert.equal(held(settled).get(moved), 1);
});

test("a deleted session is not shown after a refresh, and its group is read again to the same depth", () => {
  const sessions = run("a", "2026-11-07", 75);
  const routes = server(sessions);
  let kept = show(refresh(null, routes.list(), NONE).state, "2026-11-07", routes);
  assert.equal(held(kept).get("a20"), 1);
  const gone = sessions.splice(19, 1)[0].session;
  const { state, reads } = refresh(kept, { ...routes.list(), now: NOW + 30 }, NONE);
  assert.equal(held(state).has(gone), false);
  assert.equal(reads.length, 1);
  assert.equal(reads[0].pages, 1);
  const settled = settle(state, reads, server(sessions, { now: NOW + 30 }));
  assert.deepEqual(names(group(settled, "2026-11-07").rows), names(sessions.slice(0, 38)));
  assert.equal(held(settled).has(gone), false);
  assert.equal(draw(settled).groups[0].more.text, "36 more");
  noneTwice(settled);
});

test("a new first row reads the group again even when its count did not change", () => {
  const sessions = run("a", "2026-11-07", 40);
  const routes = server(sessions);
  const kept = show(refresh(null, routes.list(), NONE).state, "2026-11-07", routes);
  // One goes and one arrives: the count is the same, the first row is not.
  sessions.splice(30, 1);
  sessions.unshift(row("new", at("2026-11-07 13:00")));
  const later = server(sessions, { now: NOW + 30 });
  const { state, reads } = refresh(kept, later.list(), NONE);
  assert.equal(reads.length, 1);
  const settled = settle(state, reads, later);
  assert.deepEqual(names(group(settled, "2026-11-07").rows), names(sessions.slice(0, 38)));
  noneTwice(settled);
});

test("a group that now fits its first rows is not read again", () => {
  const sessions = run("a", "2026-11-07", 12);
  const routes = server(sessions);
  const kept = show(refresh(null, routes.list(), NONE).state, "2026-11-07", routes);
  sessions.splice(0, 6);
  const { state, reads } = refresh(kept, { ...routes.list(), now: NOW + 30 }, NONE);
  assert.deepEqual(reads, []);
  assert.deepEqual(names(group(state, "2026-11-07").rows), names(sessions));
});

test("opened program sessions stand over a refresh while a read from their start checks their first row", () => {
  const programs = run("p", "2026-11-07", 40, { by_program: true });
  const sessions = [...run("a", "2026-11-07", 3), ...programs];
  const routes = server(sessions);
  let kept = refresh(null, routes.list(), NONE).state;
  const opened = toggleProgram(kept, "2026-11-07");
  kept = settle(opened.state, [opened.read], routes);
  kept = settle(kept, [showMoreProgram(kept, "2026-11-07")], routes);
  const later = server(sessions, { now: NOW + 30 });
  const { state, reads } = refresh(kept, later.list(), NONE);
  assert.deepEqual(reads, [{ group: "2026-11-07", kind: "program", before: null, now: NOW + 30, pages: 2, check: true }]);
  assert.equal(draw(state).groups[0].program.rows.length, 40);
  assert.equal(draw(state).groups[0].program.act, "Hide");
  // The check finds the same first row: the rows stand and nothing more is read.
  const checked = addMore(state, reads[0], later.more(reads[0]));
  assert.equal(checked.next, null);
  assert.deepEqual(names(group(checked.state, "2026-11-07").program.rows), names(programs));
  noneTwice(checked.state);
});

test("opened program sessions are read again when one was replaced by another and when their count changed", () => {
  const programs = run("p", "2026-11-07", 40, { by_program: true });
  const sessions = [...run("a", "2026-11-07", 3), ...programs];
  const routes = server(sessions);
  let kept = refresh(null, routes.list(), NONE).state;
  const opened = toggleProgram(kept, "2026-11-07");
  kept = settle(opened.state, [opened.read], routes);
  kept = settle(kept, [showMoreProgram(kept, "2026-11-07")], routes);

  // One deleted and one new the same day: the count stands, the first row does not.
  const swapped = [...sessions.filter((one) => one.session !== "p35"), row("pnew", at("2026-11-07 13:00"), { by_program: true })];
  let later = server(swapped, { now: NOW + 30 });
  let next = refresh(kept, later.list(), NONE);
  assert.equal(next.reads[0].check, true);
  let settled = settle(next.state, next.reads, later);
  assert.deepEqual(names(group(settled, "2026-11-07").program.rows),
                   ["pnew", ...names(programs).filter((name) => name !== "p35")]);
  assert.equal(held(settled).has("p35"), false);
  noneTwice(settled);

  // One deleted: the count changed, so the kept rows go at once and are read again to the same depth.
  const fewer = sessions.filter((one) => one.session !== "p35");
  later = server(fewer, { now: NOW + 30 });
  next = refresh(kept, later.list(), NONE);
  assert.deepEqual(next.reads, [{ group: "2026-11-07", kind: "program", before: null, now: NOW + 30, pages: 2, check: false }]);
  assert.deepEqual(draw(next.state).groups[0].program, { text: "39 started by a program", act: "Hide", open: true, rows: [], more: null });
  settled = settle(next.state, next.reads, later);
  assert.deepEqual(names(group(settled, "2026-11-07").program.rows), names(programs).filter((name) => name !== "p35"));

  // The last of them deleted: the line and its rows go.
  later = server(sessions.filter((one) => one.by_program !== true), { now: NOW + 30 });
  next = refresh(kept, later.list(), NONE);
  assert.deepEqual(next.reads, []);
  assert.equal(draw(next.state).groups[0].program, null);
});

test("the days Earlier opened stand over a refresh that moved neither its count nor its start", () => {
  const sessions = [];
  for (let n = 0; n < 12; n += 1) sessions.push(...run(`d${String(n).padStart(2, "0")}-`, dayAfter("2026-11-07", -n), 12));
  const routes = server(sessions);
  let kept = show(refresh(null, routes.list(), NONE).state, "earlier", routes);
  kept = show(kept, "2026-10-30", routes);
  sessions.push(row("fresh", NOW + 10));
  const later = server(sessions, { now: NOW + 30 });
  const { state, reads } = refresh(kept, later.list(), NONE);
  assert.deepEqual(reads, []);
  assert.deepEqual(state.groups.map((one) => one.key), [
    "recent", "2026-11-07", "2026-11-06", "2026-11-05", "2026-11-04", "2026-11-03", "2026-11-02", "2026-11-01",
    "2026-10-31", "2026-10-30", "2026-10-29", "earlier",
  ]);
  assert.equal(group(state, "2026-10-30").rows.length, 12);
  assert.equal(draw(state).groups[11].earlier.text, "24 sessions, back to Oct 27");
  // The next Show goes on from where the opened days end, in the newer list.
  assert.deepEqual([showMore(state, "earlier").before, showMore(state, "earlier").now], ["day:2026-10-28", NOW + 30]);
  noneTwice(state);
});

test("a session that changes in a day Earlier opened drops those days, and Earlier is read again as far", () => {
  const sessions = [];
  for (let n = 0; n < 12; n += 1) sessions.push(...run(`d${String(n).padStart(2, "0")}-`, dayAfter("2026-11-07", -n), 12));
  const routes = server(sessions);
  let kept = show(show(refresh(null, routes.list(), NONE).state, "earlier", routes), "earlier", routes);
  assert.equal(held(kept).get("d08-01"), 1);
  const at8 = sessions.findIndex((one) => one.session === "d08-01");
  sessions[at8] = { ...sessions[at8], changed: NOW + 10 };
  const later = server(sessions, { now: NOW + 30 });
  const { state, reads } = refresh(kept, later.list(), NONE);
  assert.deepEqual(state.groups.map((one) => one.key).slice(-2), ["2026-11-01", "earlier"]);
  assert.deepEqual(reads, [{ group: "earlier", kind: null, before: "day:2026-10-31", now: NOW + 30, pages: 2, check: false }]);
  const settled = settle(state, reads, later);
  assert.deepEqual(settled.groups.map((one) => one.key).slice(-5), ["2026-10-31", "2026-10-30", "2026-10-29", "2026-10-28", "2026-10-27"]);
  assert.equal(held(settled).get("d08-01"), 1);
  assert.deepEqual(names(group(settled, "recent").rows), ["d08-01"]);
  noneTwice(settled);
});

test("opened rows stay open over a refresh, and a row closes again", () => {
  const sessions = run("a", "2026-11-07", 4, { prompt: typed(at("2026-11-07 11:00"), "hello") });
  const routes = server(sessions);
  let kept = toggleOpen(refresh(null, routes.list(), NONE).state, keyOf(sessions[1]));
  assert.deepEqual(draw(kept).groups[0].rows.map((one) => [one.open, one.act]),
                   [[false, "Open"], [true, "Close"], [false, "Open"], [false, "Open"]]);
  kept = refresh(kept, { ...routes.list(), now: NOW + 30 }, NONE).state;
  assert.deepEqual(draw(kept).groups[0].rows.map((one) => one.open), [false, true, false, false]);
  assert.deepEqual(draw(toggleOpen(kept, keyOf(sessions[1]))).groups[0].rows.map((one) => one.open), [false, false, false, false]);
});

test("a list asked with another filter keeps no row of the list before it", () => {
  const sessions = run("a", "2026-11-07", 75);
  const routes = server(sessions);
  const kept = show(refresh(null, routes.list(), NONE).state, "2026-11-07", routes);
  const query = queryOf({ ...NO_FILTERS, harness: "claude" }, TODAY, UTC);
  const { state, reads } = refresh(kept, { ...routes.list(), now: NOW + 1 }, query);
  assert.deepEqual(reads, []);
  assert.equal(group(state, "2026-11-07").rows.length, 8);
  assert.equal(state.query, query);
});

test("the find field's results replace the groups, page by thirty, and give way to the groups again", () => {
  const sessions = run("a", "2026-11-07", 40);
  const routes = server(sessions);
  const kept = refresh(null, routes.list(), NONE).state;
  const query = queryOf({ ...NO_FILTERS, find: "proj" }, TODAY, UTC);
  const found = { key: "found", count: 40, rows: sessions.slice(0, 30), before: "p30" };
  let state = refresh(kept, answer([found], { now: NOW + 1, total: 40 }), query).state;
  assert.deepEqual(draw(state).groups.map((one) => [one.key, one.rows.length, one.more.text]), [["found", 30, "10 more"]]);
  const request = showMore(state, "found");
  assert.deepEqual([request.kind, request.before], [null, "p30"]);
  state = addMore(state, request, { shape: 1, group: "found", kind: null, rows: sessions.slice(30), before: null }).state;
  assert.deepEqual([draw(state).groups[0].rows.length, draw(state).groups[0].more], [40, null]);
  state = refresh(state, { ...routes.list(), now: NOW + 2 }, NONE).state;
  assert.deepEqual(state.groups.map((one) => one.key), ["recent", "2026-11-07"]);
});

// -- found rows' times ---------------------------------------------------------------------------

test("in the find field's results a row's time says its day unless that is today", () => {
  const query = queryOf({ ...NO_FILTERS, find: "proj" }, TODAY, UTC);
  const said = (session, when) => row(session, at(when), { prompt: typed(at(when), "Do it.") });
  const found = { key: "found", count: 4, before: null,
                  rows: [said("a", "2026-11-08 19:09"), said("b", "2026-11-07 22:50"), said("c", "2026-11-06 22:10"),
                         said("d", "2025-11-06 22:10")] };
  const rows = draw(stateOf([found], { total: 4 }, query)).groups[0].rows;
  assert.deepEqual(rows.map((one) => one.time), ["19:09", "yesterday 22:50", "Nov 6 22:10", "Nov 6, 2025 22:10"]);
  assert.deepEqual(rows.map((one) => one.name), [
    "proj-a · started today 18:59", "proj-a · started yesterday 22:40", "proj-a · started Nov 6 22:00",
    "proj-a · started Nov 6, 2025 22:00"]);
});

// -- compressed Codex sessions -------------------------------------------------------------------

test("compressed Codex sessions left out of the list get one line, and none at zero or when not counted", () => {
  const some = [day("2026-11-07", [row("b1", at("2026-11-07 22:50"))])];
  assert.equal(draw(stateOf(some, { unreadable: 3 })).unlisted, "3 Codex sessions are compressed and not listed.");
  assert.equal(draw(stateOf(some, { unreadable: 1 })).unlisted, "1 Codex session is compressed and not listed.");
  assert.equal(draw(stateOf(some, { unreadable: 1234 })).unlisted, "1,234 Codex sessions are compressed and not listed.");
  assert.equal(draw(stateOf(some, { unreadable: 0 })).unlisted, null);
  assert.equal(draw(stateOf(some, { unreadable: null })).unlisted, null);
  assert.equal(unlistedLine(stateOf([recent([])], { all: 0, total: 0, unreadable: 2 })),
               "2 Codex sessions are compressed and not listed.");
});

// -- the filter line -----------------------------------------------------------------------------

test("the filter line reads Folder all, Harness all, When any time, and has no Clear until one is set", () => {
  const state = stateOf([day("2026-11-07", [row("b1", at("2026-11-07 22:50"))])]);
  assert.deepEqual(filterLine(state, NO_FILTERS), {
    filters: [{ key: "folder", label: "Folder", value: "all", set: false },
              { key: "harness", label: "Harness", value: "all", set: false },
              { key: "when", label: "When", value: "any time", set: false }],
    clear: null,
  });
  // The find field is not a filter of the line.
  assert.equal(filterLine(state, { ...NO_FILTERS, find: "proj" }).clear, null);
});

test("a set filter shows its value and the line gains Clear", () => {
  const state = stateOf([day("2026-11-07", [row("b1", at("2026-11-07 22:50"))])]);
  const line = filterLine(state, { folder: "/work/proj-a/", harness: "codex", when: { kind: "last", days: 7 }, find: "" });
  assert.deepEqual(line.filters.map((one) => [one.value, one.set]), [["proj-a", true], ["Codex", true], ["last 7 days", true]]);
  assert.equal(line.clear, CLEAR);
  assert.equal(filterLine(state, { ...NO_FILTERS, harness: "codex" }).clear, "Clear");
});

test("there is no filter line while no session was counted", () => {
  assert.equal(filterLine(stateOf([recent([])], { all: 0, total: 0, harnesses: [] }), NO_FILTERS), null);
});

test("When is worded as today, the last N days, or a range with either end open", () => {
  assert.equal(whenWords({ kind: "any" }, TODAY), "any time");
  assert.equal(whenWords({ kind: "today" }, TODAY), "today");
  assert.equal(whenWords({ kind: "last", days: 30 }, TODAY), "last 30 days");
  assert.equal(whenWords({ kind: "range", from: "2026-11-03", to: "2026-11-06" }, TODAY), "Nov 3 to Nov 6");
  assert.equal(whenWords({ kind: "range", from: "2026-11-03", to: "2026-11-03" }, TODAY), "Nov 3");
  assert.equal(whenWords({ kind: "range", from: "2025-12-30", to: null }, TODAY), "from Dec 30, 2025");
  assert.equal(whenWords({ kind: "range", from: null, to: "2026-11-06" }, TODAY), "up to Nov 6");
  assert.equal(whenWords({ kind: "range", from: null, to: null }, TODAY), "any time");
  assert.equal(whenWords({ kind: "range", from: "", to: "nonsense" }, TODAY), "any time");
  assert.deepEqual(whenOf({ kind: "range", from: "", to: "2026-11-06" }), { kind: "range", from: null, to: "2026-11-06" });
});

test("the Harness menu lists only harnesses with sessions, with their counts, and the one that is set", () => {
  const harnesses = [{ harness: "claude", count: 40 }, { harness: "codex", count: 0 }, { harness: "opencode", count: 1200 }];
  const state = stateOf([day("2026-11-07", [row("b1", at("2026-11-07 22:50"))])], { harnesses });
  assert.deepEqual(harnessMenu(state, NO_FILTERS), [
    { value: null, label: "All harnesses", dim: null, tail: null, checked: true },
    { value: "claude", label: "Claude Code", dim: null, tail: "40", checked: false },
    { value: "opencode", label: "OpenCode", dim: null, tail: "1,200", checked: false },
  ]);
  const set = harnessMenu(state, { ...NO_FILTERS, harness: "codex" });
  assert.deepEqual(set.map((one) => [one.label, one.tail, one.checked]),
                   [["All harnesses", null, false], ["Claude Code", "40", false], ["Codex", null, true], ["OpenCode", "1,200", false]]);
  const gone = harnessMenu(stateOf([recent([])], { harnesses: [] }), { ...NO_FILTERS, harness: "codex" });
  assert.deepEqual(gone.map((one) => [one.label, one.checked]), [["All harnesses", false], ["Codex", true]]);
});

test("the When menu offers any time, today, the last 7 and the last 30 days", () => {
  assert.deepEqual(whenMenu(NO_FILTERS).map((one) => [one.label, one.checked]),
                   [["Any time", true], ["Today", false], ["Last 7 days", false], ["Last 30 days", false]]);
  assert.deepEqual(whenMenu({ ...NO_FILTERS, when: { kind: "last", days: 30 } }).map((one) => one.checked), [false, false, false, true]);
  assert.deepEqual(whenMenu({ ...NO_FILTERS, when: { kind: "range", from: "2026-11-01", to: null } }).map((one) => one.checked),
                   [false, false, false, false]);
  assert.deepEqual(whenMenu(NO_FILTERS)[2].value, { kind: "last", days: 7 });
});

test("the Folder menu names each folder by its last segment, with the path before it and its newest change", () => {
  const folders = { shape: 1, more: 0, folders: [
    { folder: "/work/proj-a", count: 3, changed: at("2026-11-08 19:09") },
    { folder: "/home/alice/proj-a/", count: 1, changed: at("2026-11-06 10:00") },
    { folder: "/proj-b", count: 2, changed: null },
  ] };
  const menu = folderMenu(folders, { ...NO_FILTERS, folder: "/work/proj-a" }, "", TODAY, UTC);
  assert.deepEqual(menu.all, { value: null, label: "All folders", dim: null, tail: null, checked: false });
  assert.deepEqual(menu.items, [
    { value: "/work/proj-a", label: "proj-a", dim: "/work", tail: "19:09", checked: true },
    { value: "/home/alice/proj-a/", label: "proj-a", dim: "/home/alice", tail: "Nov 6", checked: false },
    { value: "/proj-b", label: "proj-b", dim: null, tail: null, checked: false },
  ]);
  assert.equal(menu.line, null);
  assert.equal(folderMenu(folders, NO_FILTERS, "", TODAY, UTC).all.checked, true);
});

test("a folder under the home folder is worded with a tilde, and any other as it is", () => {
  assert.equal(folderWords("/home/alice/code/proj-a", "/home/alice"), "~/code/proj-a");
  assert.equal(folderWords("/home/alice/code/proj-a/", "/home/alice/"), "~/code/proj-a/");
  assert.equal(folderWords("/home/alice", "/home/alice"), "~");
  assert.equal(folderWords("/home/alice/", "/home/alice"), "~");
  assert.equal(folderWords("/home/alice-old/proj-a", "/home/alice"), "/home/alice-old/proj-a");
  assert.equal(folderWords("/work/proj-a", "/home/alice"), "/work/proj-a");
  assert.equal(folderWords("/work/proj-a", null), "/work/proj-a");
  assert.equal(folderWords("/work/proj-a", undefined), "/work/proj-a");
  assert.equal(folderWords("/work/proj-a", ""), "/work/proj-a");
  assert.equal(folderWords("/work/proj-a", "/"), "/work/proj-a");
  assert.equal(folderWords("work/proj-a", "work"), "work/proj-a");
});

test("the list keeps the home folder its answer names, and null when it names none", () => {
  assert.equal(stateOf([recent([])], { home: "/home/alice" }).home, "/home/alice");
  assert.equal(stateOf([recent([])], { home: null }).home, null);
  assert.equal(stateOf([recent([])]).home, null);
});

test("the Folder menu words the path before a folder against the home folder, and narrows by either form", () => {
  const folders = { shape: 1, more: 0, folders: [
    { folder: "/home/alice/code/proj-a", count: 3, changed: null },
    { folder: "/home/alice/proj-b", count: 1, changed: null },
    { folder: "/work/proj-c", count: 1, changed: null }] };
  const menu = (narrow, home) => folderMenu(folders, NO_FILTERS, narrow, TODAY, UTC, home);
  assert.deepEqual(menu("", "/home/alice").items.map((one) => [one.label, one.dim, one.value]), [
    ["proj-a", "~/code", "/home/alice/code/proj-a"], ["proj-b", "~", "/home/alice/proj-b"], ["proj-c", "/work", "/work/proj-c"]]);
  assert.deepEqual(menu("", null).items.map((one) => one.dim), ["/home/alice/code", "/home/alice", "/work"]);
  assert.deepEqual(menu("~/code", "/home/alice").items.map((one) => one.label), ["proj-a"]);
  assert.deepEqual(menu("/home/alice/code", "/home/alice").items.map((one) => one.label), ["proj-a"]);
  assert.deepEqual(menu("~/code", null).items, []);
});

test("the Folder menu narrows by any part of the path, and says when nothing matches or folders were left off", () => {
  const folders = { shape: 1, more: 0, folders: [
    { folder: "/work/proj-a", count: 3, changed: null }, { folder: "/home/alice/proj-b", count: 1, changed: null }] };
  assert.deepEqual(folderMenu(folders, NO_FILTERS, " ALICE ", TODAY, UTC).items.map((one) => one.label), ["proj-b"]);
  assert.deepEqual(folderMenu(folders, NO_FILTERS, "proj", TODAY, UTC).items.map((one) => one.label), ["proj-a", "proj-b"]);
  assert.equal(folderMenu(folders, NO_FILTERS, "zz", TODAY, UTC).line, "No folder matches.");
  assert.equal(folderMenu({ ...folders, more: 12 }, NO_FILTERS, "", TODAY, UTC).line,
               "12 more folders not listed; narrow the list with the other filters");
  assert.equal(folderMenu({ ...folders, more: 1 }, NO_FILTERS, "", TODAY, UTC).line,
               "1 more folder not listed; narrow the list with the other filters");
  // Before the answer there is nothing to say.
  assert.deepEqual([folderMenu(null, NO_FILTERS, "", TODAY, UTC).items, folderMenu(null, NO_FILTERS, "", TODAY, UTC).line], [[], null]);
});

test("the find field's placeholder says what it finds by", () => {
  assert.equal(FIND_PLACEHOLDER, "Find a session by its id or folder");
});

// -- reading and failing -------------------------------------------------------------------------

test("the list says it is reading before its first answer, and nothing once it has one", () => {
  const feed = (over) => ({ loading: false, gone: false, failure: null, refused: null, value: null, ...over });
  assert.deepEqual(statusLine(feed({ loading: true })), { state: "waiting", line: WAITING });
  assert.equal(statusLine(feed({ value: {} })), null);
  assert.equal(statusLine(feed({ loading: true, value: {} })), null);
});

test("a read that fails, is refused or finds sessions gone says why, with a list read before it or not", () => {
  const feed = (over) => ({ loading: false, gone: false, failure: null, refused: null, value: null, ...over });
  const failure = { status: 500, why: "sessions' page server answered 500" };
  assert.deepEqual(statusLine(feed({ failure, value: {} })),
                   { state: "failed", line: "Moxi couldn't read the list: sessions' page server answered 500" });
  assert.deepEqual(statusLine(feed({ failure }), "more of the list"),
                   { state: "failed", line: "Moxi couldn't read more of the list: sessions' page server answered 500" });
  assert.deepEqual(statusLine(feed({ refused: "find is too long" }), "the folders"),
                   { state: "refused", line: "Moxi couldn't read the folders: find is too long" });
  assert.deepEqual(statusLine(feed({ gone: true, failure })), { state: "gone", line: GONE });
  assert.equal(GONE, "sessions is no longer installed.");
});

test("a shown row is found by its key among a group's rows and a day's opened program rows", () => {
  const state = stateOf([recent([row("s1", NOW)]), day("2026-11-07", [row("s2", NOW - 90000)], { program: { count: 1 } })]);
  assert.equal(shownRow(state, "claude/s2").session, "s2");
  assert.equal(shownRow(state, "claude/p1"), null);
  const opened = toggleProgram(state, "2026-11-07");
  const added = addMore(opened.state, opened.read, { shape: 1, group: "2026-11-07", kind: "program", before: null,
                                                     rows: [row("p1", NOW - 90100, { by_program: true })] });
  assert.equal(shownRow(added.state, "claude/p1").session, "p1");
});
