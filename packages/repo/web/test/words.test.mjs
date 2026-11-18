import assert from "node:assert/strict";
import test from "node:test";

import {
  byGitOrder, canRefuse, counted, dayWord, eventLabel, eventWords, firstLine, homeForm, joinAnd, pieceName,
  recordSeconds, refuseWord, repoName, whenWord,
} from "../src/words.ts";
import { T, UTC } from "./fixtures.mjs";

test("an event's label and its words in a sentence", () => {
  assert.equal(eventLabel("pre-commit"), "Before a commit");
  assert.equal(eventLabel("post-merge"), "After a merge or pull");
  assert.equal(eventLabel("pre-auto-gc"), "pre-auto-gc");
  assert.equal(eventWords("commit-msg"), "on the message");
  assert.equal(eventWords("prepare-commit-msg"), "writing the message");
  assert.equal(eventWords("post-rewrite"), "after a rewrite");
  assert.equal(eventWords("fsmonitor-watchman"), "fsmonitor-watchman");
});

test("which events refuse, and what each refuses", () => {
  for (const event of ["pre-commit", "pre-push", "commit-msg", "prepare-commit-msg", "applypatch-msg",
                       "reference-transaction", "sendemail-validate", "pre-anything"]) {
    assert.equal(canRefuse(event), true, event);
  }
  for (const event of ["post-commit", "post-merge", "post-checkout", "post-rewrite"]) {
    assert.equal(canRefuse(event), false, event);
  }
  const words = Object.fromEntries(["pre-commit", "prepare-commit-msg", "commit-msg", "pre-merge-commit",
                                    "pre-push", "pre-rebase", "pre-applypatch", "applypatch-msg", "pre-auto-gc",
                                    "reference-transaction", "sendemail-validate", "pre-receive"]
    .map((event) => [event, refuseWord(event)]));
  assert.deepEqual(words, {
    "pre-commit": "commit", "prepare-commit-msg": "commit", "commit-msg": "commit", "pre-merge-commit": "merge",
    "pre-push": "push", "pre-rebase": "rebase", "pre-applypatch": "patch", "applypatch-msg": "patch",
    "pre-auto-gc": "automatic gc", "reference-transaction": "ref update", "sendemail-validate": "email",
    "pre-receive": "pre-receive",
  });
});

test("events in git's order, then the rest by name", () => {
  const events = ["zz-event", "pre-push", "post-commit", "commit-msg", "aa-event", "pre-commit", "post-merge"];
  assert.deepEqual(events.sort(byGitOrder),
                   ["pre-commit", "commit-msg", "post-commit", "post-merge", "pre-push", "aa-event", "zz-event"]);
});

test("names, lines and counts", () => {
  assert.equal(pieceName("notes/secret-gate"), "secret-gate");
  assert.equal(pieceName("odd"), "odd");
  assert.equal(firstLine("one\ntwo"), "one");
  assert.equal(joinAnd(["a", "b", "c"]), "a, b and c");
  assert.equal(counted(1, "repo"), "1 repo");
  assert.equal(counted(2, "pass", "passes"), "2 passes");
});

test("a repo is named by its project, else by its root in ~ form", () => {
  assert.equal(repoName({ project: "atlas-api", root: "/home/jp/code/atlas-api" }, "/home/jp"), "atlas-api");
  assert.equal(repoName({ project: null, root: "/home/jp/code/atlas-api" }, "/home/jp"), "~/code/atlas-api");
  assert.equal(repoName({ project: null, root: "/home/jp2/x" }, "/home/jp"), "/home/jp2/x");
  assert.equal(repoName({ project: null, root: "/srv/x" }, null), "/srv/x");
  assert.equal(homeForm("C:\\Users\\JP\\code", "c:\\users\\jp"), "~\\code");
});

test("day words against the server's today", () => {
  assert.equal(dayWord("2026-11-18", "2026-11-18"), "today");
  assert.equal(dayWord("2026-11-17", "2026-11-18"), "yesterday");
  assert.equal(dayWord("2026-11-13", "2026-11-18"), "Fri");
  assert.equal(dayWord("2026-10-10", "2026-11-18"), "Oct 10");
  assert.equal(whenWord(T("16:40"), "2026-11-18", UTC), "16:40");
  assert.equal(whenWord(T("22:10", "2026-11-17"), "2026-11-18", UTC), "yesterday 22:10");
  assert.equal(whenWord(T("08:52", "2026-10-10"), "2026-11-18", UTC), "Oct 10 08:52");
  assert.equal(whenWord(T("08:52", "2026-10-10"), null, UTC), "08:52");
});

test("a record's at is read as UTC, and one that doesn't parse is no time", () => {
  assert.equal(recordSeconds("2026-11-18T16:40:00Z"), T("16:40"));
  for (const bad of [null, "", "2026-11-18 16:40:00", "2026-11-18T16:40:00", "2026-02-30T10:00:00Z",
                     "2026-11-18T24:00:00Z", "yesterday"]) {
    assert.equal(recordSeconds(bad), null, String(bad));
  }
});
