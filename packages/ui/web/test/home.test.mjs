import assert from "node:assert/strict";
import test from "node:test";

import { anyNeeds, readReport, readSide, rowMode, rowOpen, shownRows, SIDE_FROM_PX, sideMargin } from "../src/home-model.ts";

const entry = (key, order, detail) => ({ key, order, report: readReport(detail) });
const keys = (entries) => shownRows(entries).map((item) => item.key);

test("null, and anything that is not a report, is nothing to report", () => {
  for (const detail of [null, undefined, "text", 3, {}, { parts: "text" }, { text: "x" }]) {
    assert.equal(readReport(detail), null);
  }
});

test("a report: its parts in order, the heavy ones, the time, and what it leaves out", () => {
  assert.deepEqual(readReport({ parts: [{ text: "2 running", heavy: true }, { text: " · one goes cold" }, { text: 3 }, {}, null],
                                time: "read 19:08" }),
                   { parts: [{ text: "2 running", heavy: true }, { text: " · one goes cold", heavy: false }],
                     time: "read 19:08", needs: 0, opens: false, open: false });
  assert.deepEqual(readReport({ parts: [] }), { parts: [], time: "", needs: 0, opens: false, open: false });
});

test("needs counts only as a number above 0, and open only with opens", () => {
  assert.equal(readReport({ parts: [], needs: 20 }).needs, 20);
  for (const needs of [0, -1, "10", NaN, Infinity, true]) assert.equal(readReport({ parts: [], needs }).needs, 0);
  assert.equal(readReport({ parts: [], open: true }).open, false);
  assert.equal(readReport({ parts: [], opens: true, open: true }).open, true);
});

test("a row shows only once its component has reported", () => {
  const rows = [entry("a.one", 100, null), entry("b.two", 200, { parts: [{ text: "x" }] })];
  assert.deepEqual(keys(rows), ["b.two"]);
  assert.deepEqual(keys([entry("a.one", 100, null)]), []);
});

test("rows keep their fixed places while none needs the user", () => {
  const said = { parts: [{ text: "x" }] };
  assert.deepEqual(keys([entry("a", 100, said), entry("b", 200, said), entry("c", 900, said)]), ["a", "b", "c"]);
});

test("a row that needs the user sits above every row that does not, lower needs first, then by order", () => {
  const said = (needs) => ({ parts: [{ text: "x" }], needs });
  const rows = [entry("quiet", 100, said(0)), entry("runs-out", 150, said(40)), entry("broken-late", 900, said(30)),
                entry("blocked", 300, said(10)), entry("broken-early", 200, said(30)), entry("silent", 50, null)];
  assert.deepEqual(keys(rows), ["blocked", "broken-early", "broken-late", "runs-out", "quiet"]);
  assert.equal(anyNeeds(rows), true);
  assert.equal(anyNeeds([entry("quiet", 100, said(0)), entry("silent", 50, null)]), false);
});

test("a row without opens is a link, one with opens a toggle, one held open neither", () => {
  assert.equal(rowMode(readReport({ parts: [] })), "link");
  assert.equal(rowMode(readReport({ parts: [], opens: true })), "toggle");
  assert.equal(rowMode(readReport({ parts: [], opens: true, open: true })), "held");
});

test("a component is shown under its row after a click or while it holds itself open", () => {
  const toggle = readReport({ parts: [], opens: true });
  assert.equal(rowOpen(toggle, false), false);
  assert.equal(rowOpen(toggle, true), true);
  assert.equal(rowOpen(readReport({ parts: [], opens: true, open: true }), false), true);
  // A click that came before the component stopped opening shows nothing.
  assert.equal(rowOpen(readReport({ parts: [] }), true), false);
  assert.equal(rowOpen(null, true), false);
});

test("a side panel's width is a number above 0, and anything else is no panel", () => {
  assert.equal(readSide({ width: 400 }), 400);
  assert.equal(readSide({ width: 312.5 }), 312.5);
  for (const detail of [null, undefined, 400, "400", {}, { width: 0 }, { width: -400 }, { width: "400" },
                        { width: NaN }, { width: Infinity }, { width: true }]) {
    assert.equal(readSide(detail), 0);
  }
});

// The column's centre in a window `inner` px wide, where the block that holds the
// column is `block` px wide and centred in the window, as the browser works the margin out.
function columnCentre(inner, block, width) {
  const [, less] = /^calc\(50% - ([\d.]+)px\)$/.exec(sideMargin(width));
  return (inner - block) / 2 + block / 2 - Number(less) + 340;
}

test("beside a side panel the column's centre is the centre of the window less the panel", () => {
  assert.equal(sideMargin(400), "calc(50% - 540px)");
  assert.equal(columnCentre(1400, 1136, 400), (1400 - 400) / 2);
  assert.equal(columnCentre(SIDE_FROM_PX, SIDE_FROM_PX - 64, 400), (SIDE_FROM_PX - 400) / 2);
  assert.equal(columnCentre(1920, 1136, 321), (1920 - 321) / 2);
});

test("no panel leaves the column centred, and the panel moves it from 1100 px of window", () => {
  assert.equal(sideMargin(0), "");
  assert.equal(SIDE_FROM_PX, 1100);
});
