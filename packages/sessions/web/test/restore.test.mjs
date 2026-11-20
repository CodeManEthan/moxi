import assert from "node:assert/strict";
import test from "node:test";

import { addressText, readAddress } from "../src/address.ts";
import { NO_FILTERS, queryOf, refresh } from "../src/list.ts";
import { depthOf, isAct, ListReader, mayWrite, nextRead, SavedScroll, unreadOpened, writeOf } from "../src/restore.ts";

// -- a stand-in for the list's two routes ---------------------------------------------------------

const UTC = {
  clock: (epoch) => new Date(epoch * 1000).toISOString().slice(11, 16),
  day: (epoch) => new Date(epoch * 1000).toISOString().slice(0, 10),
  start: (day) => Date.parse(`${day}T00:00:00Z`) / 1000,
};
const TODAY = "2026-11-08";
const DAY = "2026-11-07";
const NOW = Date.parse("2026-11-08T19:10:00Z") / 1000;

const row = (session, changed = NOW - 100) => ({
  harness: "claude", session, path: `/root/${session}.jsonl`, changed, size: 100, subagents: 0, folder: "/work/a",
  started: changed - 600, by_program: null, name: null, model: null, activity: null, prompt: null, recent: false,
});
const rows = (prefix, from, to) => Array.from({ length: Math.max(0, to - from) }, (_, at) => row(`${prefix}-${from + at}`));
const place = (prefix, at, count) => (at < count ? `${prefix}:${at}` : null);

/**
 * The server: `recent` of `sizes.recent` sessions (5 in the list answer, 30 a page), the day
 * `DAY` (8, then 30) with `sizes.program` program sessions (30 a page from their start), and
 * `earlier` of `sizes.earlier` sessions, a day of one session per read. Each `/api/list` has
 * a newer `now`. `hold(path)` may return a promise the test resolves, or a status to fail with.
 */
function server(sizes = {}, hold = () => null) {
  const size = { recent: 40, day: 10, program: 0, earlier: 3, first: "r", ...sizes };
  const asked = [];
  let lists = 0;
  const answer = (path) => {
    const url = new URL(path, "http://x/");
    const key = (name) => url.searchParams.get(name);
    if (url.pathname === "/api/list") {
      lists += 1;
      const recentRows = rows(size.first, 0, Math.min(5, size.recent));
      return {
        shape: 1, now: NOW + lists, today: TODAY, all: 99, total: 99, unreadable: 0, roots: [],
        harnesses: [{ harness: "claude", count: 99 }],
        groups: [
          { key: "recent", count: size.recent, rows: recentRows, before: place(size.first, 5, size.recent) },
          { key: DAY, count: size.day, rows: rows("d", 0, Math.min(8, size.day)), before: place("d", 8, size.day),
            program: { count: size.program } },
          ...(size.earlier > 0 ? [{ key: "earlier", count: size.earlier, oldest: NOW - 9e6, before: "e:0" }] : []),
        ],
      };
    }
    const group = key("group");
    const before = key("before");
    const at = before === null ? 0 : Number(before.split(":")[1]);
    if (group === "earlier") {
      const day = `2026-10-${String(30 - at).padStart(2, "0")}`;
      const left = size.earlier - at - 1;
      return { shape: 1, groups: [{ key: day, count: 1, rows: [row(`e-${at}`)], before: null, program: { count: 0 } }],
               before: left > 0 ? `e:${at + 1}` : null, left };
    }
    const [prefix, count] = group === "recent" ? [size.first, size.recent]
      : key("kind") === "program" ? ["p", size.program] : ["d", size.day];
    return { shape: 1, group, kind: key("kind"), rows: rows(prefix, at, Math.min(at + 30, count)),
             before: place(prefix, at + 30, count) };
  };
  const read = (path) => {
    asked.push(path);
    const held = hold(path, asked.length);
    if (typeof held === "number") return Promise.resolve({ status: held, data: { error: "no" } });
    if (held !== null) return held.then(() => ({ status: 200, data: answer(path) }));
    return Promise.resolve({ status: 200, data: answer(path) });
  };
  return { read, asked, size, more: () => asked.filter((path) => path.startsWith("api/more")) };
}

const settle = async () => {
  for (let n = 0; n < 40; n += 1) await new Promise((done) => setImmediate(done));
};

function deferred() {
  let release;
  const promise = new Promise((done) => { release = done; });
  return { promise, release };
}

/** A reader placed on `search` over `fake`, and every address its writer wrote: as the
 * list does, a write that would not change the address is skipped. */
function reader(fake, search = "", opened = []) {
  const written = [];
  const changes = [];
  let at = search;
  const list = new ListReader({
    read: fake.read, zone: UTC,
    onChange: (change) => {
      changes.push(change);
      const address = list.address();
      if (address !== null && address !== at) {
        written.push(address);
        at = address;
      }
    },
  });
  const read = readAddress(search);
  list.place(read, opened);
  return { list, written, changes };
}

const groupOf = (list, key) => list.state.groups.find((group) => group.key === key);

// -- the depth a list shows, and the plan -------------------------------------------------------

test("the plan reads one page at a time in list order, and the depth read off the list is the depth reached", async () => {
  const fake = server({ program: 40 });
  const { list, written } = reader(fake, `?more=earlier:2,recent:3,${DAY}:1&program=${DAY}:2`);
  await settle();
  const plan = fake.more().map((path) => new URL(path, "http://x/").searchParams);
  assert.deepEqual(plan.map((keys) => `${keys.get("group")}:${keys.get("kind") ?? ""}:${keys.get("before") ?? ""}`), [
    "recent:main:r:5", "recent:main:r:35", `${DAY}:main:d:8`, `${DAY}:program:`, `${DAY}:program:p:30`,
    "earlier::e:0", "earlier::e:1",
  ]);
  assert.equal(list.restore, null);
  assert.equal(groupOf(list, "recent").pages, 2, "recent:3 stops at its group's end, two pages past the answer");
  assert.deepEqual(depthOf(list.state), {
    more: [{ group: "recent", pages: 2 }, { group: DAY, pages: 1 }, { group: "earlier", pages: 2 }],
    program: [{ group: DAY, pages: 2 }],
  });
  assert.deepEqual(written, [`?more=recent:2,${DAY}:1,earlier:2&program=${DAY}:2`]);
});

test("a day Earlier brings is read once the page that brings it has landed; a group not in the list gets no read", async () => {
  const fake = server({ earlier: 5 });
  const { list, written } = reader(fake, "?more=2026-10-29:1,earlier:2,found:3,undated:1");
  await settle();
  const groups = fake.more().map((path) => new URL(path, "http://x/").searchParams.get("group"));
  assert.deepEqual(groups, ["earlier", "earlier"], "2026-10-29 has one session: no page is past it");
  assert.deepEqual(list.state.groups.map((group) => group.key), ["recent", DAY, "2026-10-30", "2026-10-29", "earlier"]);
  assert.deepEqual(written, ["?more=earlier:2"]);
});

test("nextRead over a list with nothing to read gives none, and depthOf an unpaged list is empty", () => {
  const fake = server({ recent: 3, day: 2, earlier: 0 });
  const state = refresh(null, syncAnswer(fake), queryOf(NO_FILTERS, TODAY, UTC)).state;
  assert.equal(nextRead(state, { more: [{ group: "recent", pages: 4 }, { group: "earlier", pages: 1 }], program: [] }), null);
  assert.deepEqual(depthOf(state), { more: [], program: [] });
});

/** A list answer holding every row of the stand-in's `recent` and day, with nothing past them. */
function syncAnswer(fake) {
  const size = fake.size;
  return {
    shape: 1, now: NOW, today: TODAY, all: 9, total: 9, unreadable: 0, roots: [], harnesses: [],
    groups: [{ key: "recent", count: size.recent, rows: rows("r", 0, size.recent), before: null },
             { key: DAY, count: size.day, rows: rows("d", 0, size.day), before: null, program: { count: 0 } }],
  };
}

// -- the writer --------------------------------------------------------------------------------

test("a query change with depth shown writes the new query with no depth at once, and a Show landing after it changes nothing", async () => {
  const gate = deferred();
  let holding = false;
  const fake = server({}, (path) => (holding && path.startsWith("api/more") ? gate.promise : null));
  const { list, written } = reader(fake, "?more=recent:1");
  await settle();
  assert.deepEqual(written, [], "the address as opened is the depth reached");
  holding = true;
  list.showMore("recent");
  await settle();
  list.setFilters({ ...list.filters, harness: "codex" });
  assert.equal(written[written.length - 1], "?harness=codex");
  holding = false;
  gate.release();
  await settle();
  assert.equal(groupOf(list, "recent").pages, 0, "the new query's list is read from its top");
  assert.ok(fake.asked.some((path) => path.startsWith("api/list?harness=codex")));
  assert.deepEqual(written, ["?harness=codex"]);
});

test("a pick that makes the same query keeps the depth shown and the address", async () => {
  const fake = server();
  const { list, written } = reader(fake, "?harness=claude&more=recent:1");
  await settle();
  const lists = fake.asked.filter((path) => path.startsWith("api/list")).length;
  list.setFilters({ ...list.filters, harness: "claude" });
  list.setFilters({ ...list.filters, find: "a" });
  list.setFilters({ ...list.filters, find: "" });
  await settle();
  assert.equal(fake.asked.filter((path) => path.startsWith("api/list")).length, lists);
  assert.equal(groupOf(list, "recent").pages, 1);
  assert.equal(list.address(), "?harness=claude&more=recent:1");
  assert.deepEqual(written, []);
});

test("the writer writes nothing while a target is held, and the depth last written stands while a read runs", () => {
  const filters = { ...NO_FILTERS, harness: "claude" };
  const standing = { more: [{ group: "recent", pages: 3 }], program: [] };
  const fake = server();
  const state = refresh(null, syncAnswer(fake), queryOf(filters, TODAY, UTC)).state;
  assert.equal(writeOf({ filters, state, current: true, holding: true, reading: false, standing }), null);
  assert.deepEqual(writeOf({ filters, state, current: true, holding: false, reading: true, standing }).depth, standing);
  assert.deepEqual(writeOf({ filters, state, current: false, holding: false, reading: true, standing }).depth,
                   { more: [], program: [] });
  assert.deepEqual(writeOf({ filters, state, current: true, holding: false, reading: false, standing }).depth,
                   { more: [], program: [] });
  assert.equal(addressText(writeOf({ filters, state: null, current: false, holding: false, reading: false, standing })),
               "?harness=claude");
});

test("mid-chain the address keeps the depth it held, and an unchanged address is not written again", async () => {
  const gate = deferred();
  let holding = false;
  const fake = server({ recent: 100 }, (path) => (holding && path.includes("before=r%3A35") ? gate.promise : null));
  const { list, written } = reader(fake, "?more=recent:1");
  await settle();
  holding = true;
  list.showMore("recent");
  await settle();
  assert.equal(list.reading, true);
  assert.equal(list.address(), "?more=recent:1");
  gate.release();
  await settle();
  assert.deepEqual(written, ["?more=recent:2"]);
});

test("the guard: the writer writes only at the list's own path, from a list placed there", () => {
  assert.equal(mayWrite("/m/sessions/", true), true);
  assert.equal(mayWrite("/m/sessions/", false), false);
  assert.equal(mayWrite("/m/sessions/claude/abc", true), false);
  assert.equal(mayWrite("/m/usage/", true), false);
});

// -- the restore's target -----------------------------------------------------------------------

test("a newer list after the first read: refresh's read is made, then the plan goes on and reaches the target", async () => {
  const gate = deferred();
  let first = true;
  const held = server({ recent: 100 }, (path) => {
    if (path.startsWith("api/more") && first) {
      first = false;
      return gate.promise;
    }
    return null;
  });
  const { list, written } = reader(held, "?more=recent:3");
  await settle();
  // A newer list while the first read waits: its `now` is newer, so that read changes nothing.
  held.size.recent = 101;
  held.size.first = "s";
  list.readList();
  await settle();
  gate.release();
  await settle();
  assert.equal(groupOf(list, "recent").pages, 3);
  assert.equal(list.restore, null);
  assert.deepEqual(written, [], "no address before the target was reached, and then the one opened");
  assert.equal(held.more().length, 4, "the held read, then three toward the target over the newer list");
});

test("a kept open program line gets its check read from its start; a changed one is read again from its start", async () => {
  const fake = server({ program: 40 });
  const { list } = reader(fake, `?program=${DAY}:2`);
  await settle();
  assert.equal(groupOf(list, DAY).program.pages, 2);
  const before = fake.more().length;
  list.readList();
  await settle();
  const check = fake.more().slice(before);
  assert.equal(check.length, 1);
  assert.match(check[0], /kind=program/);
  assert.doesNotMatch(check[0], /before=/);
  assert.equal(groupOf(list, DAY).program.pages, 2);

  fake.size.program = 41;
  const again = fake.more().length;
  list.readList();
  await settle();
  const reread = fake.more().slice(again);
  assert.equal(reread.length, 2);
  assert.doesNotMatch(reread[0], /before=/);
  assert.match(reread[1], /before=p%3A30/);
  assert.equal(groupOf(list, DAY).program.pages, 2);
});

test("his Show mid-restore lets the target go: the read in flight changes nothing and he gets one page past what he saw", async () => {
  const gate = deferred();
  const fake = server({ recent: 200 }, (path) => (path.includes("before=r%3A35") ? gate.promise : null));
  const { list, written } = reader(fake, "?more=recent:5");
  await settle();
  assert.equal(groupOf(list, "recent").pages, 1);
  list.showMore("recent");
  assert.equal(list.restore, null);
  gate.release();
  await settle();
  assert.equal(groupOf(list, "recent").pages, 2, "the restore's page in flight was dropped; his own was added");
  const groups = fake.more().map((path) => new URL(path, "http://x/").searchParams.get("before"));
  assert.deepEqual(groups, ["r:5", "r:35", "r:35"]);
  assert.deepEqual(written, ["?more=recent:2"]);
});

test("a failed /api/more lets nothing go; a newer list, then the tick, reads on to the target with no write between", async () => {
  let fail = true;
  const fake = server({ recent: 100 }, (path) => (fail && path.includes("before=r%3A35") ? 500 : null));
  const { list, written } = reader(fake, "?more=recent:3");
  await settle();
  assert.notEqual(list.restore, null);
  assert.equal(list.restore.failed, true);
  assert.equal(list.stopped, true);
  assert.equal(list.address(), null, "the address stays as it was opened");
  assert.notEqual(list.moreStatus, null);
  list.readList();
  await settle();
  assert.equal(list.address(), null);
  fail = false;
  list.readList();
  await settle();
  assert.equal(groupOf(list, "recent").pages, 3);
  assert.deepEqual(written, []);
});

test("a failed first /api/list holds the target, and the next list answer starts the plan", async () => {
  let fail = true;
  const fake = server({}, (path) => (fail && path.startsWith("api/list") ? 500 : null));
  const { list, written } = reader(fake, "?more=recent:1");
  await settle();
  assert.equal(list.state, null);
  assert.notEqual(list.restore, null);
  assert.equal(list.stopped, true);
  assert.deepEqual(written, []);
  fail = false;
  list.readList();
  await settle();
  assert.equal(groupOf(list, "recent").pages, 1);
  assert.deepEqual(written, []);
});

test("an opened row in a group the restore pages in is read once the page that shows it lands; one never shown stays", async () => {
  const fake = server({ recent: 50 });
  const changes = [];
  const shown = [];
  const list = new ListReader({ read: fake.read, zone: UTC, onChange: (change) => {
    changes.push(change);
    if (list.state !== null) shown.push(unreadOpened(list.state, new Set()).map((one) => one.session));
  } });
  list.place(readAddress("?more=recent:1"), ["claude/r-20", "claude/gone"]);
  await settle();
  assert.deepEqual(list.state.opened, ["claude/r-20", "claude/gone"]);
  const firstShown = shown.findIndex((sessions) => sessions.includes("r-20"));
  assert.ok(firstShown >= 0);
  assert.ok(changes.includes("more"));
  assert.deepEqual(shown[shown.length - 1], ["r-20"]);
  assert.deepEqual(unreadOpened(list.state, new Set(["claude/r-20"])), []);
});

// -- the cap ------------------------------------------------------------------------------------

test("earlier:9999 asks 40 reads, a retry after a failure counted, then lets the target go with the line", async () => {
  let failOnce = true;
  const fake = server({ earlier: 100000 }, (path) => {
    if (failOnce && path.includes("before=e%3A10")) {
      failOnce = false;
      return 500;
    }
    return null;
  });
  const { list, written } = reader(fake, "?more=earlier:9999");
  await settle();
  assert.equal(fake.more().length, 11, "the eleventh read failed and the restore waits for a list");
  assert.equal(list.capped, false);
  assert.deepEqual(written, []);
  list.readList();
  await settle();
  assert.equal(fake.more().length, 40);
  assert.equal(list.restore, null);
  assert.equal(list.capped, true);
  assert.equal(list.state.earlier.pages, 39, "39 of the 40 reads answered");
  assert.deepEqual(written, ["?more=earlier:39"]);
});

test("a 503 asked again counts toward the 40, and no call is made past the 40th", async () => {
  let calls = 0;
  const fake = server({ earlier: 100000 }, (path) => {
    if (!path.startsWith("api/more")) return null;
    calls += 1;
    return calls === 11 || calls >= 40 ? 503 : null;
  });
  const list = new ListReader({ read: fake.read, zone: UTC, wait: () => Promise.resolve(), onChange: () => {} });
  list.place(readAddress("?more=earlier:9999"), []);
  await settle();
  assert.equal(fake.more().length, 40, "the eleventh call's retry and the fortieth's failure are counted; nothing after");
  assert.equal(list.restore, null);
  assert.equal(list.capped, true);
  assert.equal(list.state.earlier.pages, 38, "38 of the 40 calls answered");
});

test("a target reached at the 40th read names no cap", async () => {
  const fake = server({ earlier: 100000 });
  const { list, written } = reader(fake, "?more=earlier:40");
  await settle();
  assert.equal(fake.more().length, 40);
  assert.equal(list.capped, false);
  assert.deepEqual(written, []);
});

test("at the cap, an opened row the 40th read shows has its read started before the target is let go", async () => {
  const fake = server({ earlier: 100000 });
  const started = new Set();
  let landing = null;
  const list = new ListReader({ read: fake.read, zone: UTC, onChange: (change) => {
    // As the view does: start the reads of the opened rows a page showed, then, at the
    // first stop, gather the reads the scroll's landing waits for.
    if (change === "more") for (const one of unreadOpened(list.state, started)) started.add(`${one.harness}/${one.session}`);
    if (list.stopped && landing === null) landing = new Set(started);
  } });
  list.place(readAddress("?more=earlier:9999"), ["claude/e-39"]);
  await settle();
  assert.equal(fake.more().length, 40);
  assert.equal(list.capped, true);
  assert.ok(landing.has("claude/e-39"), "the landing waits for the row the 40th read showed");
});

// -- the scroll ---------------------------------------------------------------------------------

test("the five acts drop the scroll; scroll events, a modifier alone, Alt+Left and Meta+[ do not", () => {
  for (const type of ["wheel", "touchstart", "touchmove", "pointerdown"]) assert.equal(isAct({ type }), true, type);
  assert.equal(isAct({ type: "keydown", key: "ArrowDown" }), true);
  assert.equal(isAct({ type: "keydown", key: "a" }), true);
  assert.equal(isAct({ type: "scroll" }), false);
  for (const key of ["Shift", "Control", "Alt", "Meta"]) assert.equal(isAct({ type: "keydown", key }), false, key);
  assert.equal(isAct({ type: "keydown", key: "ArrowLeft", altKey: true }), false);
  assert.equal(isAct({ type: "keydown", key: "[", metaKey: true }), false);
});

test("the saved scroll lands once, cut to the page's height, unless an act came first", () => {
  const kept = new SavedScroll(900);
  assert.equal(kept.take({ type: "scroll" }), false);
  assert.equal(kept.take({ type: "keydown", key: "ArrowLeft", altKey: true }), false);
  assert.equal(kept.settled, false);
  assert.equal(kept.saved, 900);
  assert.equal(kept.land(600), 600);
  assert.equal(kept.settled, true);
  assert.equal(kept.land(600), null, "once only");

  const dropped = new SavedScroll(900);
  assert.equal(dropped.take({ type: "wheel" }), true);
  assert.equal(dropped.settled, true);
  assert.equal(dropped.land(2000), null);

  assert.equal(new SavedScroll(null).land(2000), null);
  assert.equal(new SavedScroll(300).land(2000), 300);
});
