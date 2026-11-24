import assert from "node:assert/strict";
import test from "node:test";

import {
  addressRoute, ageWord, arrange, cardView, cellLabel, chips, closedWords, foldsOf, footWords, holdOf, isOpenNow,
  isShown, itemMarker, itemsOf, itemView, linesShown, markAttributes, markKey, moreLabel, noLongerOpen,
  parseAddress, parseMarkKey, passageText, phoneWaiting, phraseShown, phraseText, placePopover, pointerPath, popoverWidth,
  recordQuery, revealBy, revealing, sessionAt, sheetMarks, sheetQuestions, stepHead, stepSections, stripCard, stripKey, stripStep,
  timeWord, waitingCounts, walkFoot, walkStep,
} from "../src/model.ts";

/** A clock in UTC that writes `HH:MM`. */
const UTC = {
  time: (at) => new Date(at * 1000).toISOString().slice(11, 16),
  day: (at) => new Date(at * 1000).toISOString().slice(0, 10),
};
const NOW = Date.parse("2026-11-21T12:00:00Z") / 1000;

const COLUMNS = [
  { id: "design", label: "design", part: "round" }, { id: "verify-design", label: "verify", part: "round" },
  { id: "docket", label: "docket", part: "round" }, { id: "commission", label: "commission", part: "stage" },
  { id: "build", label: "build", part: "stage" }, { id: "check", label: "check", part: "stage" },
  { id: "verify-build", label: "verify", part: "stage" }, { id: "finish", label: "finish", part: "stage" },
  { id: "ratify", label: "ratify", part: "end" }, { id: "retro", label: "retro", part: "end" },
  { id: "close", label: "close", part: "end" },
];

const cell = (state, more = {}) => ({ state, word: null, count: null, settled: state === "done" || state === "none",
                                      here: false, step_state: null, by: null, who: null, ...more });
const line = (kind, stage, cells, more = {}) => ({ kind, stage, branch: stage ? `${stage}-branch` : null, fold: null,
                                                   phrase: [], cells, ...more });
const stageCells = (states) => [null, null, null, ...states.map((one) => (one ? cell(one) : null)), null, null, null];

function round(name, group, more = {}) {
  const [project, slug] = name.split("/");
  return {
    name, project, slug, description: null, flow: "full", standing: null, version: 1,
    step: { step: "docket", stage: null, state: "ready" }, counts: { open: 0, parked: 0, not_shown: 0 },
    group, phrase: [], mine: 0, last_at: "2026-11-21T10:00:00Z", rev: "r1",
    lines: [line("round", "B1", [cell("done", { word: "v2" }), cell("done", { word: "pub" }),
                                 cell("yours", { count: 2, here: true }), ...stageCells(["waiting", "todo", "todo", "todo", "todo"]).slice(3, 8),
                                 cell("todo"), cell("todo"), cell("todo")])],
    folds: { more: 0, landed: 0, not_started: { n: 0, ready: 0, held: 0, child: 0 } }, ...more,
  };
}

function board(more = {}) {
  return {
    today: "2026-11-21", home: "/home/u", read_at: "2026-11-21T12:00:00Z", state: "up", detail: null,
    columns: COLUMNS,
    waiting: { questions: [], decisions: [], waits: [], not_shown: [], parked: 0 },
    recent: [], rounds: [], closed: [], unreadable: [], ...more,
  };
}

test("times: a clock time today, a date before, and ages in m, h and d", () => {
  assert.equal(timeWord("2026-11-21T11:40:00Z", "2026-11-21", UTC), "11:40");
  assert.equal(timeWord("2026-11-03T08:52:00Z", "2026-11-21", UTC), "Nov 3 08:52");
  assert.equal(timeWord(null, "2026-11-21", UTC), "-");
  assert.equal(ageWord("2026-11-21T11:53:00Z", NOW), "7m");
  assert.equal(ageWord("2026-11-21T12:00:30Z", NOW), "0m");
  assert.equal(ageWord("2026-11-21T05:00:00Z", NOW), "7h");
  assert.equal(ageWord("2026-11-19T13:00:00Z", NOW), "47h");
  assert.equal(ageWord("2026-11-19T12:00:00Z", NOW), "2d");
  assert.equal(ageWord(null, NOW), "");
  assert.equal(ageWord("2026-11-21T11:00:00Z", null), "");
});

test("a marker's accessible name says the step, stage, round and state", () => {
  const check = COLUMNS[5];
  assert.equal(cellLabel(check, "B1", "billing-export", cell("failed", { word: "red" })),
               "check of B1, billing-export: failed, red");
  assert.equal(cellLabel(COLUMNS[0], null, "ledger", cell("done", { word: "v8" })), "design, ledger: done, v8");
  assert.equal(cellLabel(COLUMNS[2], null, "ledger", cell("yours", { count: 3 })), "docket, ledger: waiting on you, 3");
  assert.equal(cellLabel(check, "B2", "ledger", cell("owed", { word: "owed", by: "builder", who: "builder-1" })),
               "check of B2, ledger: owed by builder (builder-1)");
  assert.equal(cellLabel(COLUMNS[1], null, "ledger", cell("none", { word: "skipped" })), "verify, ledger: skipped");
  assert.equal(cellLabel(COLUMNS[3], "B3", "ledger", cell("child", { word: "child" })),
               "commission of B3, ledger: built in a round of its own, child");
  assert.equal(cellLabel(COLUMNS[4], "B3", "ledger", cell("todo", { word: "not built" })),
               "build of B3, ledger: not reached yet, not built");
});

test("a mark's attributes: flags set or left off, the key with its stage, nothing for a null cell", () => {
  const made = markAttributes(COLUMNS[5], "B1", "ledger", cell("done", { word: "green", here: true }), { open: true });
  assert.deepEqual(made, { state: "done", word: "green", count: null, label: "check of B1, ledger: done, green",
                           key: "check|B1", settled: "", here: "", open: "", quiet: null, wide: null });
  assert.equal(markAttributes(COLUMNS[2], null, "ledger", cell("yours", { count: 4 })).count, "4");
  assert.equal(markAttributes(COLUMNS[2], null, "ledger", null).state, null);
  assert.equal(markKey("docket", null), "docket");
  assert.deepEqual(parseMarkKey("check|B1"), { column: "check", stage: "B1" });
  assert.deepEqual(parseMarkKey("close"), { column: "close", stage: null });
});

test("a phrase shows its first three parts, and nothing owed when it has none", () => {
  const phrase = ["15 questions", "1 to read back", "CI linux red on B13", "check owed (builder)"]
    .map((text) => ({ text, weight: "moves" }));
  assert.equal(phraseShown(phrase).length, 3);
  assert.deepEqual(phraseShown([]), [{ text: "nothing owed", weight: "quiet" }]);
  assert.deepEqual(phraseShown([], { roundLine: false }), []);
  assert.equal(phraseText(phrase), "15 questions, 1 to read back, CI linux red on B13");
  assert.equal(phraseText([]), "nothing owed");
});

test("groups draw in order, none empty, unreadable rounds under stuck", () => {
  const value = board({ rounds: [round("kestrel/a", "moving"), round("kestrel/b", "yours"), round("kestrel/c", "wrapping")],
                        unreadable: [{ name: "kestrel/broken", reason: "its log can't be read", fix: null }] });
  const groups = arrange(value);
  assert.deepEqual(groups.map((one) => [one.label, one.rounds.map((r) => r.slug), one.unreadable.length]),
                   [["Your turn", ["b"], 0], ["Stuck or red", [], 1], ["Moving", ["a"], 0], ["Wrapping up", ["c"], 0]]);
  assert.deepEqual(chips(value).map((one) => one.label),
                   ["All", "Your turn 1", "Stuck 1", "Moving 1", "Wrapping up 1"]);
});

test("while a popover is open the groups and order hold; cells are the new value's", () => {
  const before = board({ rounds: [round("kestrel/a", "yours"), round("kestrel/b", "yours"), round("kestrel/c", "moving")] });
  const held = holdOf(arrange(before));
  const moved = round("kestrel/a", "moving", { rev: "r2" });
  const after = board({ rounds: [round("kestrel/b", "yours"), round("kestrel/d", "yours"), round("kestrel/c", "moving"), moved] });
  const groups = arrange(after, held);
  assert.deepEqual(groups.map((one) => [one.group, one.rounds.map((r) => r.slug)]),
                   [["yours", ["a", "b", "d"]], ["moving", ["c"]]]);
  assert.equal(groups[0].rounds[0].rev, "r2");
  const gone = arrange(board({ rounds: [round("kestrel/c", "moving")] }), held);
  assert.deepEqual(gone.map((one) => one.rounds.map((r) => r.slug)), [["c"]]);
  assert.deepEqual(arrange(after).map((one) => [one.group, one.rounds.map((r) => r.slug)]),
                   [["yours", ["b", "d"]], ["moving", ["c", "a"]]]);
});

test("filters hide lines, and revealing sets each one that hides a round to the value that shows it", () => {
  const a = round("kestrel/a", "stuck");
  assert.equal(isShown(a, "all", null), true);
  assert.equal(isShown(a, "yours", null), false);
  assert.equal(isShown(a, "stuck", "atlas"), false);
  assert.deepEqual(revealing(a, "yours", "atlas"), { filter: "all", project: null });
  assert.deepEqual(revealing(a, "stuck", "kestrel"), { filter: "stuck", project: "kestrel" });
  assert.deepEqual(revealing(a, "all", "atlas"), { filter: "all", project: null });
});

test("folds: +N for four or fewer, landed and not started for more, each unfolding its lines", () => {
  const more = round("kestrel/a", "moving", {
    folds: { more: 2, landed: 0, not_started: { n: 0, ready: 0, held: 0, child: 0 } },
  });
  more.lines.push(line("stage", "B2", stageCells(["done", "done", "done", "done", "done"]), { fold: "more" }),
                  line("stage", "B3", stageCells(["todo", "todo", "todo", "todo", "todo"]), { fold: "more" }));
  assert.equal(linesShown(more, new Set()).length, 1);
  assert.equal(linesShown(more, new Set(["kestrel/a|more"])).length, 3);
  assert.deepEqual(moreLabel(more, new Set()), { text: "+2", label: "Show 2 other stages of a" });
  assert.equal(moreLabel(more, new Set(["kestrel/a|more"])).label, "Fold 2 other stages of a");
  assert.equal(moreLabel(round("kestrel/b", "moving"), new Set()), null);

  const busy = round("kestrel/busy", "moving", {
    folds: { more: 0, landed: 1, not_started: { n: 16, ready: 14, held: 0, child: 2 } },
  });
  busy.lines.push(line("stage", "B2", stageCells(["done", "done", "done", "done", "done"]), { fold: "landed" }));
  for (let n = 0; n < 16; n += 1) {
    busy.lines.push(line("stage", `B${n + 3}`, stageCells([n < 14 ? "owed" : "child", "todo", "todo", "todo", "todo"]),
                         { fold: "not-started" }));
  }
  const folds = foldsOf(busy, COLUMNS);
  assert.deepEqual(folds.map((one) => [one.fold, one.text, one.cells.length, one.more]),
                   [["landed", "1 build landed", 1, 0],
                    ["not-started", "16 stages not started: 14 ready to start, 2 built in child rounds", 14, 2]]);
  assert.equal(folds[1].cells[0].state, "owed");
  assert.equal(linesShown(busy, new Set(["kestrel/busy|landed"])).length, 2);
});

test("a closed round's words: when it closed, and how when it wasn't a close", () => {
  assert.equal(closedWords({ name: "a/b", project: "a", flow: "full", ended_at: "2026-11-20T10:00:00Z", how: "closed" }, UTC),
               "closed Nov 20");
  assert.equal(closedWords({ name: "a/b", project: "a", flow: "full", ended_at: "2026-11-20T10:00:00Z", how: "dropped" }, UTC),
               "closed Nov 20, dropped");
});

function waitingBoard() {
  return board({
    rounds: [round("kestrel/b", "yours"), round("kestrel/a", "yours")],
    waiting: {
      questions: [{ holder: "kestrel/a", id: "Q1", text: "Which?" }, { holder: "kestrel/b", id: "Q3", text: "Where?" },
                  { holder: "kestrel", id: "Q9", text: "Project?" }, { holder: "kestrel/a", id: "Q2", text: "Why?" }],
      decisions: [{ holder: "kestrel/a", id: "B1.M1", text: "kept" }],
      waits: [{ round: "kestrel/b", id: "B1.W1", stage: "B1", text: "restart", report: null, by: null, at: null }],
      not_shown: [{ project: "kestrel", through: null, rows: [{ holder: "kestrel/a", id: "C1", text: "ruled" }] }],
      parked: 2,
    },
  });
}

test("the walk's order: rounds in the board's order, each its questions, read-backs, decisions, jobs; then the project's", () => {
  const items = itemsOf(waitingBoard());
  assert.deepEqual(items.map((one) => `${one.kind}:${one.holder}:${one.id}`), [
    "question:kestrel/b:Q3", "wait:kestrel/b:B1.W1",
    "question:kestrel/a:Q1", "question:kestrel/a:Q2", "shown:kestrel/a:C1", "decision:kestrel/a:B1.M1",
    "question:kestrel:Q9",
  ]);
  assert.equal(items[6].round, null);
  assert.deepEqual(itemsOf(waitingBoard(), ["kestrel/a", "kestrel/b"]).map((one) => one.id).slice(0, 2), ["Q1", "Q2"]);
  assert.deepEqual(itemMarker(items[0]), { round: "kestrel/b", key: "docket" });
  assert.deepEqual(itemMarker(items[5]), { round: "kestrel/a", key: "ratify" });
  assert.deepEqual(itemMarker(items[1]), { round: "kestrel/b", key: "close" });
  assert.equal(itemMarker(items[6]), null);
  assert.equal(walkFoot(0, 38), "1 of 38");
  assert.equal(walkStep(0, -1, 7), 0);
  assert.equal(walkStep(5, 1, 7), 6);
  assert.equal(walkStep(6, 1, 7), 6);
});

test("the waiting line counts each kind, leaving out a kind with none", () => {
  assert.deepEqual(waitingCounts(waitingBoard()), [
    { n: "4", words: "questions in 3 rounds" }, { n: "1", words: "decision" }, { n: "1", words: "job" },
    { n: "1", words: "ruling to read back" },
  ]);
  assert.deepEqual(waitingCounts(board()), []);
  assert.equal(phoneWaiting(waitingBoard()), "7 wait on you in 3 rounds");
  assert.equal(phoneWaiting(board()), "Nothing waits on you.");
});

test("a decision not put to the operator is neither walked nor counted", () => {
  const made = waitingBoard();
  made.waiting.decisions.push({ holder: "kestrel/a", id: "B1.M2", text: "skipped", not_put: "ratify is off" });
  assert.deepEqual(itemsOf(made).filter((one) => one.kind === "decision").map((one) => one.id), ["B1.M1"]);
  assert.deepEqual(waitingCounts(made)[1], { n: "1", words: "decision" });
});

test("addresses both ways, each segment encoded", () => {
  const cases = [
    [{ kind: "round", round: "kestrel/billing-export" }, "round/kestrel/billing-export"],
    [{ kind: "step", round: "kestrel/a", column: "check", stage: "B1" }, "step/kestrel/a/check/B1"],
    [{ kind: "step", round: "kestrel/a", column: "design", stage: null }, "step/kestrel/a/design"],
    [{ kind: "question", holder: "kestrel/a", id: "Q2" }, "question/kestrel/a/Q2"],
    [{ kind: "wait", holder: "kestrel/a", id: "B2.M1" }, "wait/kestrel/a/B2.M1"],
    [{ kind: "question", holder: "kestrel", id: "Q9" }, "question/kestrel/-/Q9"],
    [{ kind: "shown", holder: "kestrel/a b", id: "C1" }, "shown/kestrel/a%20b/C1"],
    [{ kind: "walk", item: null }, "walk"],
    [{ kind: "walk", item: { kind: "decision", holder: "kestrel/a", id: "B1.M1" } }, "walk/decision/kestrel/a/B1.M1"],
    [{ kind: "key" }, "key"],
  ];
  for (const [address, route] of cases) {
    assert.equal(addressRoute(address), route);
    assert.deepEqual(parseAddress(route), address, route);
  }
  assert.equal(addressRoute(null), "");
  for (const bad of ["", "nothing", "round/kestrel", "round/kestrel/-", "step/kestrel/a", "question/kestrel/a",
                     "walk/round/kestrel/a", "walk/nothing", "key/x", "round/%E0%A4%A"]) {
    assert.equal(parseAddress(bad), null, bad);
  }
});

test("an address's record query, and what the foot says when it is no longer open", () => {
  assert.equal(recordQuery({ kind: "step", round: "kestrel/a", column: "check", stage: "B1" }),
               "api/record?what=step&round=kestrel%2Fa&column=check&stage=B1");
  assert.equal(recordQuery({ kind: "round", round: "kestrel/a" }), "api/record?what=card&round=kestrel%2Fa");
  assert.equal(recordQuery({ kind: "question", holder: "kestrel", id: "Q9" }), "api/record?what=question&round=kestrel&id=Q9");
  assert.equal(recordQuery({ kind: "key" }), null);
  assert.equal(noLongerOpen({ kind: "step", round: "kestrel/a", column: "check", stage: "B1" }),
               "check of B1 on kestrel/a is no longer open");
  assert.equal(noLongerOpen({ kind: "question", holder: "kestrel/a", id: "Q2" }), "Question Q2 of kestrel/a is no longer open");
  assert.equal(noLongerOpen({ kind: "round", round: "kestrel/a" }), "kestrel/a is no longer open");
  const value = waitingBoard();
  assert.equal(isOpenNow({ kind: "question", holder: "kestrel/a", id: "Q2" }, value), true);
  assert.equal(isOpenNow({ kind: "question", holder: "kestrel/a", id: "Q7" }, value), false);
  assert.equal(isOpenNow({ kind: "round", round: "kestrel/gone" }, value), false);
  assert.equal(isOpenNow({ kind: "walk", item: null }, board()), false);
});

test("a step record's sections in order, with CI red first and green folded", () => {
  const record = {
    round: "kestrel/a", column: "check", stage: "B1", state: "failed", word: "red",
    facts: [{ key: "builder", value: "builder-1" }, { key: "worktree", value: "/home/u/w/a" }],
    ci: { commit: "0123456789ab", at: "2026-11-21T11:00:00Z", reading: "red",
          jobs: [{ name: "lint", reading: "green" },
                 { name: "linux", reading: "red", url: "https://github.com/o/r/actions/runs/446/job/135" },
                 { name: "mac", reading: "pending" }] },
    runs: [{ step: "check.local", exit_code: 0, duration_s: 12, commit: "0123456789ab", at: "2026-11-21T09:00:00Z",
             output: null },
           { step: "check", exit_code: 1, duration_s: 29.4, commit: "0123456789ab", at: "2026-11-21T11:00:00Z",
             output: "/home/u/.cache/check-1.log" },
           { step: "check.local", exit_code: 0, duration_s: 31, commit: "0123456789ab", at: "2026-11-21T10:00:00Z",
             output: null }],
    verdicts: [], findings: null, drift: null, gates: null, decisions: null, items: [], settled: [], close: [], fixes: [],
    next: { state: "ready", by: "builder", who: "builder-1", waits: [{ reason: "CI is red", fix: "push a fix" }],
            then: ["moxi rounds check kestrel/a B1"], reads: [], write: null, questions: [] },
    events: [1, 2, 3, 4, 5, 6].map((n) => ({ n, at: "2026-11-21T10:0" + n + ":00Z", by: "lead", type: "check.ran", words: "check ran", of: `B1 #${n}` })),
  };
  const ctx = { today: "2026-11-21", home: "/home/u", now: NOW, clock: UTC, sessionAt: (w) => (w === "/home/u/w/a" ? "claude-1" : null) };
  const sections = stepSections(record, ctx);
  assert.deepEqual(sections.map((one) => one.head),
                   [null, "CI at 0123456, read 11:00: red", "Checks run", "Next: check of B1, ready, owed by builder (builder-1)",
                    "What happened"]);
  assert.equal(sections[0].rows[0][1].session, "claude-1");
  assert.equal(sections[0].rows[1][1].text, "~/w/a");
  assert.deepEqual(sections[1].rows.map((row) => row.map((one) => one.text).join("")),
                   ["red linux  run 446 job 135", "pending mac"]);
  assert.deepEqual(sections[1].folds.map((one) => one.label), ["1 green job"]);
  assert.equal(sections[2].rows[0].map((one) => one.text).join(""), "check failed (exit 1) in 29 s at 0123456, 11:00");
  assert.equal(sections[2].rows[1][0].text, "~/.cache/check-1.log");
  assert.equal(sections[2].rows[2].map((one) => one.text).join(""), "local check passed in 31 s at 0123456, 10:00");
  assert.deepEqual(sections[2].folds.map((one) => [one.label, one.rows.length]), [["1 earlier run", 1]]);
  assert.deepEqual(sections[3].wells, ["moxi rounds check kestrel/a B1"]);
  assert.equal(sections[4].rows.length, 4);
  assert.equal(sections[4].rows[0][1].text, "check ran B1 #3");
  assert.deepEqual(sections[4].folds.map((one) => [one.label, one.rows.length]), [["All 6 events", 2]]);
  assert.deepEqual(stepHead(record, cell("failed", { word: "red" })), { title: "check of B1", sub: "kestrel/a, failed, red" });
});

test("pending CI jobs fold past three; findings open first by severity; docket items open their own popover", () => {
  const jobs = [1, 2, 3, 4].map((n) => ({ name: `j${n}`, reading: "pending" }));
  const ctx = { today: "2026-11-21", home: null, now: NOW, clock: UTC, kindOf: (_r, id) => (id.startsWith("Q") ? "question" : "shown") };
  const [ci] = stepSections({ round: "kestrel/a", column: "check", stage: "B1", ci: { commit: null, at: null, reading: "pending", jobs } }, ctx);
  assert.deepEqual(ci.folds.map((one) => one.label), ["4 jobs pending"]);
  const [findings] = stepSections({ round: "kestrel/a", findings: { rest: 2, open: [
    { id: "F2", severity: "minor", text: "small" }, { id: "F1", severity: "blocker", text: "big" }] } }, ctx);
  assert.equal(findings.head, "2 open findings");
  assert.deepEqual(findings.rows.map((row) => row[0].text.trim()), ["F1", "F2", "and 2 not open"]);
  const [items] = stepSections({ round: "kestrel/a", column: "docket", items: ["Q1", "C1"] }, ctx);
  assert.deepEqual(items.rows.map((row) => row[0].open), [{ kind: "question", holder: "kestrel/a", id: "Q1" },
                                                         { kind: "shown", holder: "kestrel/a", id: "C1" }]);
});

test("a question's popover: holds and age, its options, its passage and the command", () => {
  const view = itemView({
    what: "question", answer: "moxi rounds rule kestrel/a Q2 --chose K --record PTR", passage: "1. Compare with main.",
    item: { holder: "kestrel/a", id: "Q2", text: "Keep landing?", blocks: ["B1"], at: "2026-11-21T01:00:00Z",
            from: "file:versions/design-v3.md#Q2", options: [{ key: "a", text: "keep" }, { key: "b", text: "pause" }] },
  }, { today: "2026-11-21", home: null, now: NOW, clock: UTC });
  assert.deepEqual(view, {
    title: "Question Q2", sub: "a", text: "Keep landing?", lines: ["Holds B1; asked 11h ago."],
    options: [{ key: "a", text: "keep" }, { key: "b", text: "pause" }],
    passage: { from: "From versions/design-v3.md", text: "1. Compare with main." },
    answer: "moxi rounds rule kestrel/a Q2 --chose K --record PTR",
  });
  assert.equal(pointerPath("file:versions/design-v1.md#Q1"), "versions/design-v1.md");
});

test("a decision's, a job's and a read-back's popover words", () => {
  const ctx = { today: "2026-11-21", home: null, now: NOW, clock: UTC };
  const decision = itemView({ what: "decision", answer: "x", item: { holder: "kestrel/a", id: "B1.M1", text: "kept",
                                                                     from: "verify-1.md", by: "checker", forecloses: true } }, ctx);
  assert.equal(decision.title, "Decision B1.M1");
  assert.deepEqual(decision.lines, ["A decision the build made that its design didn't, for you to accept or reject.",
                                    "From verify-1.md, found by checker.", "It closes off a capability."]);
  const job = itemView({ what: "wait", answer: "x", item: { round: "kestrel/a", id: "B1.W1", text: "restart",
                                                            by: "B1 build checker", report: "build/v1.md", at: null } }, ctx);
  assert.deepEqual([job.title, job.sub, job.lines], ["Job B1.W1", "a", ["Asked by B1 build checker in build/v1.md.",
                                                                       "It holds the round's close."]]);
  const shown = itemView({ what: "shown", answer: "x", item: { holder: "kestrel", id: "C1", text: "ruled",
                                                               record: "abc@2026", at: "2026-11-21T09:00:00Z" } }, ctx);
  assert.deepEqual([shown.title, shown.sub, shown.lines], ["Ruling C1, to read back", "kestrel, the project",
                                                           ["Recorded abc@2026.", "Ruled 09:00."]]);
});

test("a round's card, open and closed", () => {
  const ctx = { today: "2026-11-21", home: null, now: NOW, clock: UTC };
  const open = cardView({ name: "kestrel/a", project: "kestrel", description: "a thing", flow: "full", opened: null,
                          standing: "none", stages: { finished: 1, building: 1, not_started: 2 }, rulings: 3,
                          newest_rulings: [], phrase: [{ text: "2 questions", weight: "yours" }, { text: "check owed", weight: "moves" }],
                          ended: null, ends: [], events: [] }, ctx);
  assert.equal(open.title, "a");
  assert.deepEqual(open.sections.map((one) => one.head), [null, null, "Where it is"]);
  assert.equal(open.sections[2].rows[0].map((one) => one.text).join(""), "2 questions, check owed");
  const closed = cardView({ name: "kestrel/b", project: "kestrel", ended: { how: "closed", at: "2026-11-20T09:00:00Z" },
                            ends: [{ stage: "B1", how: "landed" }, { stage: "B2", how: "dropped" }] }, ctx);
  assert.equal(closed.sections[1].head, "Ended Nov 20 09:00, closed");
  assert.deepEqual(closed.sections[1].rows.map((row) => row.map((one) => one.text).join("")), ["B1 landed", "B2 dropped"]);
});

test("a strip card: its words and the step it opens", () => {
  const entry = { round: "kestrel/a", project: "kestrel", stage: "B2", build: 7, closed: false, act_at: "2026-11-21T09:00:00Z",
                  act: "CI green", builder: "builder-1", branch: "b2", steps: ["commission", "build", "check", "verify-build", "finish"],
                  cells: [cell("done"), cell("done"), cell("done", { word: "green" }), cell("owed"), cell("waiting")] };
  assert.deepEqual(stripCard(entry, NOW), { slug: "a", stage: "B2", age: "3h", act: "CI green", label: "a B2, CI green, 3h ago" });
  assert.equal(stripStep(entry), "verify-build");
  assert.equal(stripStep({ ...entry, cells: entry.cells.map(() => cell("done")) }), "finish");
});

test("a strip card is known by its build, not by where it sits in the strip", () => {
  const card = (round, stage, build) => ({ round, project: "kestrel", stage, build, closed: false, act_at: null, act: "commissioned",
                                           builder: null, branch: null, steps: [], cells: [] });
  const before = [card("kestrel/a", "B1", 4), card("kestrel/a", "B2", 9)];
  const after = [card("kestrel/b", "B1", 2), ...before];
  assert.deepEqual(after.map(stripKey).slice(1), before.map(stripKey));
  assert.equal(new Set(after.map(stripKey)).size, 3);
  // A stage commissioned again after a drop is a second build: its own card.
  assert.notEqual(stripKey(card("kestrel/a", "B2", 9)), stripKey(card("kestrel/a", "B2", 12)));
});

test("the foot: when it was read, the parked questions, and a failed read after a good one", () => {
  const at = Date.parse("2026-11-21T11:40:00Z") / 1000;
  assert.deepEqual(footWords(waitingBoard(), at, null, "2026-11-21", UTC), ["Read 11:40", "2 parked questions"]);
  assert.deepEqual(footWords(board(), at, "no answer from the shell", "2026-11-21", UTC),
                   ["Read 11:40; the last read failed: no answer from the shell"]);
  assert.deepEqual(footWords(null, null, null, "2026-11-21", UTC), []);
});

test("a popover goes under its line, else over it, else beside the marker", () => {
  const marker = { top: 100, left: 500, width: 20, height: 20 };
  const lineBox = { top: 95, left: 0, width: 1200, height: 34 };
  const view = { width: 1280, height: 800 };
  assert.deepEqual(placePopover(marker, lineBox, { width: 460, height: 300 }, view), { top: 135, left: 280 });
  const low = { ...marker, top: 600 };
  assert.deepEqual(placePopover(low, { ...lineBox, top: 595 }, { width: 460, height: 300 }, view), { top: 289, left: 280 });
  const tall = placePopover({ ...marker, top: 300 }, { ...lineBox, top: 295 }, { width: 460, height: 500 }, view);
  assert.deepEqual(tall, { top: 60, left: 526 });
  assert.equal(placePopover({ ...marker, left: 1250 }, lineBox, { width: 460, height: 300 }, view).left, 814);
  assert.equal(popoverWidth({ kind: "question", holder: "a/b", id: "Q1" }), 560);
  assert.equal(popoverWidth({ kind: "step", round: "a/b", column: "check", stage: null }), 460);
});

test("a marker near the window's foot scrolls up so its popover opens under its line", () => {
  const marker = { top: 300, left: 380, width: 24, height: 24 };
  assert.equal(revealBy(marker, 300, 800), 0);
  assert.equal(revealBy({ ...marker, top: 556 }, 560, 800), 406);
  assert.equal(revealBy({ ...marker, top: 500 }, 200, 800), 0);
  assert.equal(revealBy({ ...marker, top: 40 }, 200, 800), -110);
});

test("the builder links to Live sessions only when exactly one session's folder is the worktree", () => {
  const sessions = [{ key: "s1", cwd: "/home/u/w/a/" }, { key: "s2", cwd: "/home/u/w/b" }, { key: "s3", cwd: null }];
  assert.equal(sessionAt("~/w/a", sessions, "/home/u"), "s1");
  assert.equal(sessionAt("/home/u/w/b/", sessions, "/home/u"), "s2");
  assert.equal(sessionAt("/home/u/w/c", sessions, "/home/u"), null);
  assert.equal(sessionAt("/home/u/w/a", [...sessions, { key: "s4", cwd: "/home/u/w/a" }], "/home/u"), null);
});

test("the phone sheet: one vertical rail, here only at the round's step, and its questions as text", () => {
  const one = round("kestrel/a", "yours");
  one.lines.push(line("build", "B2", stageCells(["done", "owed", null, "todo", "todo"])));
  const marks = sheetMarks(one, COLUMNS);
  assert.deepEqual(marks.map((m) => m.attributes.label), [
    "design", "verify", "docket", "commission of B1", "build of B1", "check of B1", "verify of B1", "finish of B1",
    "ratify", "retro", "close", "commission of B2", "build of B2", "verify of B2", "finish of B2"]);
  assert.deepEqual(marks.filter((m) => m.attributes.here !== null).map((m) => m.attributes.label), ["docket"]);
  assert.ok(marks.every((m) => m.attributes.quiet === ""));
  assert.deepEqual(sheetQuestions(waitingBoard(), "kestrel/a"), ["Q1 Which?", "Q2 Why?"]);
});

test("a passage joins the source's wrapped lines and keeps its paragraphs, lists and code", () => {
  const source = "Whether it also lives in a file is open. Every setting is a\nvariable\nalone.\n\n"
    + "- one item\n  wrapped\n- two\n1. first\n\n```\ncode line\nkept\n```\nafter";
  assert.equal(passageText(source),
               "Whether it also lives in a file is open. Every setting is a variable alone.\n\n"
               + "- one item wrapped\n- two\n1. first\n\n```\ncode line\nkept\n```\nafter");
});

// --- acting ---------------------------------------------------------------------------------

import { dropLines, dropReceipt, hasOpenBuild, leadOf, parkedGroups, parkedOf, parkedWords, parkingWords,
         preparedView } from "../src/model.ts";

test("a holder's lead: a round's own, a project's from a round its project's lead leads, else unknown", () => {
  const value = board({ rounds: [round("kestrel/a", "yours", { lead: { name: "atlas", from: "project" } }),
                                 round("kestrel/b", "moving", { lead: null })] });
  const of = leadOf(value);
  assert.equal(of("kestrel/a"), "atlas");
  assert.equal(of("kestrel/b"), null);
  assert.equal(of("kestrel"), "atlas");
  assert.equal(of("heron"), undefined);
  assert.equal(of("kestrel/gone"), undefined);
  // A round with no lead would read its project's lead if it had one, so its project has none.
  const bare = leadOf(board({ rounds: [round("heron/a", "moving", { lead: null }),
                                       round("heron/b", "moving", { lead: { name: "builder-1", from: "round" } }),
                                       round("wren/a", "moving", { lead: { name: "builder-1", from: "round" } })] }));
  assert.equal(bare("heron"), null);
  assert.equal(bare("wren"), undefined);
});

test("a prepared answer marks its option and says who recommends it, the lead by that name", () => {
  const prepared = { by: "atlas", model: "m", recommend: "b", sure: "high", why: "It keeps the old export.",
                     flips: null, read: [], options: [], actor: "atlas", at: null, current: true };
  assert.deepEqual(preparedView(prepared, "atlas"),
                   { recommend: "b", line: "The lead recommends b, sure: high. It keeps the old export." });
  assert.equal(preparedView(prepared, "kestrel-lead").line.startsWith("atlas recommends b"), true);
  assert.equal(preparedView({ ...prepared, current: false }, "atlas"), null);
  assert.equal(preparedView(null, "atlas"), null);
});

test("parked questions: listed round by round, each with what it waits for, and opened by address", () => {
  const value = waitingBoard();
  value.waiting.parked_questions = [{ holder: "kestrel/a", id: "Q5", text: "Later?", until: "B1 finished" },
                                    { holder: "kestrel/b", id: "Q4", text: "Park?", until: null }];
  assert.deepEqual(parkedGroups(value, ["kestrel/b", "kestrel/a"]), [
    { holder: "kestrel/b", rows: [{ id: "Q4", text: "Park?", until: null }] },
    { holder: "kestrel/a", rows: [{ id: "Q5", text: "Later?", until: "until B1 finished" }] }]);
  assert.equal(parkedWords(value), "2 parked questions");
  assert.equal(parkedOf(value, "kestrel/a", "Q5").id, "Q5");
  assert.equal(isOpenNow({ kind: "question", holder: "kestrel/a", id: "Q5" }, value), true);
  assert.equal(isOpenNow({ kind: "decision", holder: "kestrel/a", id: "Q5" }, value), false);
  const record = { what: "question", item: { holder: "kestrel/a", id: "Q5", state: "parked", until: "B1 finished" },
                   ruling: { id: "C3", text: "park:Q5", record: "page:rounds/x@t", actor: "atlas",
                             at: "2026-11-21T11:40:00Z", shown: false } };
  assert.equal(parkingWords(record, "2026-11-21", UTC), "Parked by C3 at 11:40, until B1 finished");
  assert.equal(parkingWords({ ...record, item: { ...record.item, state: "open" } }, "2026-11-21", UTC), "");
});

test("a stage has an open build to drop while its commission is done and its finish is not", () => {
  const building = round("kestrel/a", "moving", { lines: [
    line("round", "B1", [null, null, null, cell("done"), cell("waiting"), cell("todo"), cell("todo"), cell("todo"),
                         null, null, null]),
    line("build", "B2", [null, null, null, cell("done"), cell("done"), cell("done"), cell("done"), cell("done"),
                         null, null, null])] });
  assert.equal(hasOpenBuild(building, COLUMNS, "B1"), true);
  assert.equal(hasOpenBuild(building, COLUMNS, "B2"), false);
  assert.equal(hasOpenBuild(building, COLUMNS, "B3"), false);
  assert.equal(hasOpenBuild(building, COLUMNS, null), false);
});

test("the drop's confirm names what ends, what goes, what stays and what is recorded", () => {
  const facts = { round: "kestrel/a", stage: "B1", open: true, reason: null, branch: "b1-branch", builder: "builder-1",
                  at: "2026-11-21T10:00:00Z", tip: "0123456789abcdef",
                  folders: [{ kind: "worktree", path: "/home/u/wt/b1", goes: false, why: "it holds work that is not committed (notes.txt)" },
                            { kind: "scratch folder", path: "/home/u/scratch/b1", goes: true, why: null }],
                  refs: ["refs/ci/b1"] };
  const made = dropLines(facts, "2026-11-21", "/home/u", UTC);
  assert.deepEqual(made.map((one) => one.head), ["Ends", "Removes, if nothing in them would be lost",
                                                 "Removes from the remote", "Keeps", "Records"]);
  assert.equal(made[0].rows[0], "The open build of B1 on branch b1-branch, commissioned 10:00 by builder-1.");
  assert.deepEqual(made[1].rows, ["The worktree ~/wt/b1: will be kept: it holds work that is not committed (notes.txt)",
                                  "The scratch folder ~/scratch/b1: will be removed"]);
  assert.match(made[3].rows[0], /^The branch b1-branch at 0123456/);
  assert.deepEqual(dropReceipt({ state: "recorded", record: "page:rounds/x@t", removed: ["/home/u/scratch/b1"],
                                 kept: [{ path: "/home/u/wt/b1", why: "it holds work that is not committed (notes.txt)" }],
                                 not_told: "no lead for kestrel/a, so no one is told" },
                               "2026-11-21", "/home/u", UTC),
                   ["Dropped.", "Recorded with page:rounds/x@t", "Removed ~/scratch/b1", "Kept ~/wt/b1: it holds work that is not committed (notes.txt)",
                    "Not told: no lead for kestrel/a, so no one is told"]);
  assert.deepEqual(dropReceipt({ state: "refused", reason: "no open build of B1" }, "2026-11-21", null, UTC),
                   ["no open build of B1"]);
});
