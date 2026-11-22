import assert from "node:assert/strict";
import test from "node:test";

import {
  addressLine, addressText, CAPPED, encodeValue, isDay, listOrder, MEMORY_KEY, memoryOf, NO_DEPTH, parseWhen,
  queryWords, readAddress, withMemory,
} from "../src/address.ts";
import { NO_FILTERS } from "../src/list.ts";

const state = (filters = {}, more = [], program = []) => ({
  filters: { ...NO_FILTERS, ...filters },
  depth: { more: more.map(([group, pages]) => ({ group, pages })), program: program.map(([group, pages]) => ({ group, pages })) },
});

const read = (search) => {
  const { filters, depth, dropped } = readAddress(search);
  return { filters, depth, dropped };
};

// -- the round trip -----------------------------------------------------------------------------

test("each state written and read back is the same state, and the same state writes the same text", () => {
  const states = [
    state({ folder: "/home/jp/code/atlas" }),
    state({ harness: "claude" }),
    state({ harness: "opencode" }),
    state({ when: { kind: "today" } }),
    state({ when: { kind: "last", days: 7 } }),
    state({ when: { kind: "last", days: 30 } }),
    state({ when: { kind: "range", from: "2026-11-01", to: "2026-11-05" } }),
    state({ when: { kind: "range", from: "2026-11-01", to: null } }),
    state({ when: { kind: "range", from: null, to: "2026-11-05" } }),
    state({ find: "atlas api" }),
    state({ find: "a+b #c %d &e" }),
    state({ find: "café über 日本" }),
    state({}, [["recent", 1]]),
    state({ find: "atlas" }, [["found", 3]]),
    state({}, [["2026-11-09", 2], ["undated", 1], ["earlier", 4]]),
    state({}, [], [["2026-11-09", 1], ["2026-11-08", 3]]),
    state({ folder: "/work/my proj,x@y:z", harness: "codex", when: { kind: "last", days: 7 }, find: "atlas" },
          [["recent", 1], ["2026-11-09", 2], ["earlier", 1]], [["2026-11-09", 1]]),
  ];
  for (const one of states) {
    const text = addressText(one);
    const back = read(text);
    assert.deepEqual(back.dropped, [], text);
    assert.deepEqual(back.filters, one.filters, text);
    assert.deepEqual(back.depth, one.depth, text);
    assert.equal(addressText(back), text);
  }
});

// -- defaults and form --------------------------------------------------------------------------

test("nothing set writes no query, and each key is written only off its default", () => {
  assert.equal(addressText(state()), "");
  assert.equal(addressText(state({ find: "a" })), "");
  assert.equal(addressText(state({ when: { kind: "any" } })), "");
  assert.equal(addressText(state({}, [["recent", 0]])), "");
  assert.equal(addressText(state({ harness: "claude" })), "?harness=claude");
  assert.equal(addressText(state({ when: { kind: "today" } })), "?when=today");
  assert.equal(addressText(state({ when: { kind: "last", days: 7 } })), "?when=7d");
});

test("the keys come in their order, a folder keeps its slashes, a space is a plus and a range is a..b", () => {
  const text = addressText(state(
    { find: " atlas api ", when: { kind: "range", from: "2026-11-01", to: "2026-11-05" }, harness: "claude",
      folder: "/home/jp/my code" },
    [["recent", 1]], [["2026-11-09", 1]]));
  assert.equal(text, "?folder=/home/jp/my+code&harness=claude&when=2026-11-01..2026-11-05&find=atlas+api"
    + "&more=recent:1&program=2026-11-09:1");
  assert.equal(addressText(state({ when: { kind: "range", from: "2026-11-01", to: null } })), "?when=2026-11-01..");
  assert.equal(addressText(state({ when: { kind: "range", from: null, to: "2026-11-05" } })), "?when=..2026-11-05");
  assert.equal(encodeValue("a+b"), "a%2Bb");
  assert.equal(encodeValue("/a b/c:d,e@f#g&h%i"), "/a+b/c:d,e@f%23g%26h%25i");
});

test("depth items out of list order are read, and written in list order", () => {
  const back = read("?more=earlier:1,undated:2,2026-11-08:1,recent:2,2026-11-09:3&program=2026-11-08:1,2026-11-09:2");
  assert.deepEqual(back.dropped, []);
  assert.equal(addressText(back), "?more=recent:2,2026-11-09:3,2026-11-08:1,undated:2,earlier:1"
    + "&program=2026-11-09:2,2026-11-08:1");
  assert.deepEqual(["earlier", "2026-11-01", "undated", "found", "2026-11-09", "recent"].sort(listOrder),
    ["recent", "found", "2026-11-09", "2026-11-01", "undated", "earlier"]);
});

test("when=.. reads as any time, and writes no when", () => {
  const back = read("?when=..");
  assert.deepEqual(back.filters.when, { kind: "any" });
  assert.deepEqual(back.dropped, []);
  assert.equal(addressText(back), "");
});

test("a plus and %20 both read as a space; a %2B reads as a plus", () => {
  assert.equal(read("?find=atlas+api").filters.find, "atlas api");
  assert.equal(read("?find=atlas%20api").filters.find, "atlas api");
  assert.equal(read("?find=a%2Bb").filters.find, "a+b");
  assert.equal(read("find=a%2Bb").filters.find, "a+b");
});

// -- step 1: what the controls could set is taken -------------------------------------------------

test("find of one character fills the field and writes no find; 300 characters are written cut at 256", () => {
  const one = read("?find=a");
  assert.equal(one.filters.find, "a");
  assert.equal(addressText(one), "");
  const long = "x".repeat(300);
  const back = read(`?find=${long}`);
  assert.equal(back.filters.find, long);
  assert.equal(addressText(back), `?find=${"x".repeat(256)}`);
  assert.equal(queryWords(back.filters), `?find=${"x".repeat(256)}`);
});

test("a large count is taken as given", () => {
  assert.deepEqual(read("?more=earlier:9999").depth.more, [{ group: "earlier", pages: 9999 }]);
});

// -- step 2: anything else is dropped and named ---------------------------------------------------

test("each invalid value is dropped and named, as written", () => {
  const cases = [
    ["?harness=foo", "harness=foo"],
    ["?harness=", "harness="],
    ["?folder=work/atlas", "folder=work/atlas"],
    ["?when=soon", "when=soon"],
    ["?when=7", "when=7"],
    ["?when=14d", "when=14d"],
    ["?when=2026-02-30..", "when=2026-02-30.."],
    ["?when=2026-11-1..2026-11-05", "when=2026-11-1..2026-11-05"],
    ["?utm=1", "utm=1"],
  ];
  for (const [search, named] of cases) {
    const back = read(search);
    assert.deepEqual(back.dropped, [named], search);
    assert.deepEqual(back.filters, NO_FILTERS, search);
  }
});

test("a bad more or program item is dropped and the good ones beside it are kept", () => {
  const back = read("?more=recent:x,2026-11-09:2,recent:0,earlier:01,soon:1,:3,undated&program=2026-11-09:1,recent:1,found:2");
  assert.deepEqual(back.depth.more, [{ group: "2026-11-09", pages: 2 }]);
  assert.deepEqual(back.depth.program, [{ group: "2026-11-09", pages: 1 }]);
  assert.deepEqual(back.dropped, ["more=recent:x", "more=recent:0", "more=earlier:01", "more=soon:1", "more=:3",
                                  "more=undated", "program=recent:1", "program=found:2"]);
});

test("a group given twice inside more or program is dropped both times", () => {
  const back = read("?more=recent:1,earlier:2,recent:3&program=2026-11-09:1,2026-11-09:2");
  assert.deepEqual(back.depth, { more: [{ group: "earlier", pages: 2 }], program: [] });
  assert.deepEqual(back.dropped, ["more=recent:1", "more=recent:3", "program=2026-11-09:1", "program=2026-11-09:2"]);
});

test("a key given twice is dropped both times, and an unknown key is named", () => {
  const back = read("?harness=claude&harness=codex&when=today&utm=1");
  assert.equal(back.filters.harness, null);
  assert.deepEqual(back.filters.when, { kind: "today" });
  assert.deepEqual(back.dropped, ["harness=claude", "harness=codex", "utm=1"]);
});

test("the four of the browser's invalid address are dropped and recent:1 is kept", () => {
  const back = read("?harness=foo&when=soon&more=recent:x,recent:1&utm=1");
  assert.deepEqual(back.dropped, ["harness=foo", "when=soon", "more=recent:x", "utm=1"]);
  assert.equal(addressText(back), "?more=recent:1");
});

test("the line names one dropped value, several, and the cap only when it was met", () => {
  assert.equal(addressLine([]), null);
  assert.equal(addressLine(["harness=foo"]),
    "This address held harness=foo, which the list does not take; the list is shown without it.");
  assert.equal(addressLine(["harness=foo", "when=soon", "utm=1"]),
    "This address held harness=foo, when=soon and utm=1, which the list does not take; the list is shown without them.");
  assert.equal(addressLine([], { capped: true }), CAPPED);
  assert.equal(CAPPED, "This address asked for more of the list than one restore reads (40 pages); "
    + "it is shown as far as those reached.");
  assert.equal(addressLine(["utm=1"], { capped: true }),
    `This address held utm=1, which the list does not take; the list is shown without it. ${CAPPED}`);
});

test("a day is a calendar day that exists, and parseWhen takes only the menu's forms", () => {
  assert.equal(isDay("2026-11-09"), true);
  assert.equal(isDay("2024-02-29"), true);
  assert.equal(isDay("2026-02-29"), false);
  assert.equal(isDay("2026-13-01"), false);
  assert.equal(parseWhen("30d").days, 30);
  assert.equal(parseWhen("1d"), null);
  assert.deepEqual(parseWhen("2026-11-05..2026-11-01"), { kind: "range", from: "2026-11-05", to: "2026-11-01" });
});

// -- the entry's memory ---------------------------------------------------------------------------

test("the memory is read from its key of history.state and written beside what else the entry holds", () => {
  assert.deepEqual(memoryOf(null), { opened: [], y: null });
  assert.deepEqual(memoryOf({ [MEMORY_KEY]: { opened: ["claude/a", 3], y: -1 } }), { opened: ["claude/a"], y: null });
  const written = withMemory({ other: 1 }, { opened: ["claude/a"], y: 120 });
  assert.deepEqual(written, { other: 1, [MEMORY_KEY]: { opened: ["claude/a"], y: 120 } });
  assert.deepEqual(memoryOf(written), { opened: ["claude/a"], y: 120 });
});

test("a memory write that changes neither opened nor y is skipped", () => {
  const held = { [MEMORY_KEY]: { opened: ["claude/a"], y: 120 } };
  assert.equal(withMemory(held, { opened: ["claude/a"], y: 120 }), null);
  assert.equal(withMemory(null, { opened: [], y: null }), null);
  assert.notEqual(withMemory(held, { opened: ["claude/a"], y: 121 }), null);
  assert.notEqual(withMemory(held, { opened: [], y: 120 }), null);
});

test("NO_DEPTH holds no item", () => {
  assert.deepEqual(NO_DEPTH, { more: [], program: [] });
});
