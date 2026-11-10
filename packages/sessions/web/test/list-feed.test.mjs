import assert from "node:assert/strict";
import test from "node:test";

import { parseFolders, parseList, parseMore, parseSession } from "../src/list-feed.ts";

const ROW = {
  harness: "claude", session: "s1", path: "/root/s1.jsonl", changed: 1000, size: 10, subagents: 2,
  folder: "/work/proj-a", started: 900, by_program: null, name: null, model: "model-1",
  activity: { at: 1000, who: "agent", text: "Done." }, prompt: { at: 950, text: "Run it.", truncated: false },
  recent: true,
};
const LIST = {
  shape: 1, now: 1000.5, today: "2026-11-08", all: 3, total: 3, unreadable: 0, roots: [],
  harnesses: [{ harness: "claude", count: 3 }],
  groups: [
    { key: "recent", count: 1, rows: [ROW], before: null },
    { key: "2026-11-07", count: 1, rows: [{ ...ROW, session: "s2", recent: false, prompt: null }], before: null, program: { count: 1 } },
    { key: "earlier", count: 1, oldest: 10, before: "p" },
  ],
};
const SESSION = {
  shape: 1, ...ROW, reply: { at: 1000, text: "Done.", truncated: false },
  subagents: [{ description: "Read the logs", agent_type: "scout", started: 960, last_at: 990, last: "Found it." }],
  more: 0, resume: { command: "claude --resume s1", folder: "/work/proj-a" },
};

test("a list answer of shape 1 is read with its three kinds of group", () => {
  assert.deepEqual(parseList(LIST), LIST);
  assert.equal(parseList({ ...LIST, shape: 2 }), null);
  assert.equal(parseList(null), null);
  assert.equal(parseList([]), null);
});

test("a list answer is read with a harness missing, a failed root and a root listed in part", () => {
  const roots = [{ harness: "codex", root: null, reason: "no such root", parts: null },
                 { harness: "claude", root: "/root", reason: null, parts: 2 }];
  const read = parseList({ ...LIST, roots, harnesses: [], unreadable: null });
  assert.deepEqual(read.roots, roots);
  assert.equal(read.unreadable, null);
});

test("a list answer without what the model reads first is not one", () => {
  for (const over of [{ now: "soon" }, { today: "Nov 8" }, { today: null }, { all: null }, { total: "3" },
                      { roots: null }, { roots: [{ harness: 1 }] }, { harnesses: [{ harness: "claude" }] },
                      { groups: null }, { groups: [{ key: "recent" }] },
                      { groups: [{ key: "recent", count: 1, rows: [{ harness: "claude" }] }] },
                      { groups: [{ key: "recent", count: 1, rows: [{ ...ROW, prompt: "Run it." }] }] },
                      { groups: [{ key: "recent", count: 1, rows: [{ ...ROW, started: "soon" }] }] },
                      { groups: [{ key: "2026-11-07", count: 1, rows: [], program: 1 }] },
                      { groups: [{ key: "earlier", count: 1, before: 7 }] }]) {
    assert.equal(parseList({ ...LIST, ...over }), null, JSON.stringify(over));
  }
});

test("a row is read with every fact that can be null as null", () => {
  const bare = { harness: "opencode", session: "ses_1", path: null, changed: null, size: null, subagents: null,
                 folder: null, started: null, by_program: null, name: null, model: null, activity: null,
                 prompt: null, recent: false };
  const read = parseList({ ...LIST, groups: [{ key: "recent", count: 1, rows: [bare], before: null }] });
  assert.deepEqual(read.groups[0].rows, [bare]);
});

test("more is read in both its forms: a group's next rows, and the next older days", () => {
  const rows = { shape: 1, group: "2026-11-07", kind: "program", rows: [ROW], before: "p" };
  assert.deepEqual(parseMore(rows), rows);
  assert.deepEqual(parseMore({ ...rows, group: "found", kind: null, before: null }).kind, null);
  const days = { shape: 1, groups: [LIST.groups[1]], before: null, left: 0 };
  assert.deepEqual(parseMore(days), days);
  for (const bad of [{ ...rows, shape: 2 }, { ...rows, group: undefined }, { ...rows, rows: [{}] }, { ...rows, before: 4 },
                     { ...days, left: undefined }, { ...days, groups: [{ key: "x" }] }, { shape: 1, before: null }]) {
    assert.equal(parseMore(bad), null, JSON.stringify(bad));
  }
});

test("an opened session is read, with its reply, subagents, count of the rest and resume line null or absent", () => {
  assert.deepEqual(parseSession(SESSION), SESSION);
  const { subagents: _, reply: __, ...bare } = SESSION;
  const read = parseSession({ ...bare, more: null, resume: null });
  assert.deepEqual([read.reply, read.subagents, read.more, read.resume], [null, null, null, null]);
  assert.deepEqual(parseSession({ ...SESSION, resume: { command: "codex resume t1", folder: null } }).resume,
                   { command: "codex resume t1", folder: null });
  for (const over of [{ shape: 2 }, { session: null }, { reply: "Done." }, { subagents: 2 }, { more: "1" },
                      { resume: { folder: "/work" } }, { prompt: 4 }]) {
    assert.equal(parseSession({ ...SESSION, ...over }), null, JSON.stringify(over));
  }
});

test("the folders are read with their counts and how many were left off", () => {
  const folders = { shape: 1, folders: [{ folder: "/work/proj-a", count: 2, changed: 1000 },
                                        { folder: "/work/proj-b", count: 1, changed: null }], more: 3 };
  assert.deepEqual(parseFolders(folders), folders);
  for (const bad of [{ ...folders, shape: 0 }, { ...folders, more: null }, { ...folders, folders: [{ folder: "/work" }] },
                     { ...folders, folders: null }]) {
    assert.equal(parseFolders(bad), null, JSON.stringify(bad));
  }
});
