import assert from "node:assert/strict";
import test from "node:test";

import { classes, runs, text } from "../src/sgr.ts";

const ESC = "\u001b";

test("plain text is one plain run", () => {
  assert.deepEqual(runs("~/a/b │ main"),
                   [{ text: "~/a/b │ main", bold: false, dim: false, color: null }]);
});

test("each colour from 31 to 37 is a run of its own, and 0 resets it", () => {
  for (let code = 31; code <= 37; code += 1) {
    const found = runs(`a${ESC}[${code}mb${ESC}[0mc`);
    assert.deepEqual(found.map((run) => [run.text, run.color]), [["a", null], ["b", code], ["c", null]]);
    assert.equal(classes(found[1]), `c${code}`);
  }
});

test("bold and dim add to the colour, and an empty parameter resets", () => {
  const found = runs(`${ESC}[1;33mwarn${ESC}[2mdim${ESC}[mplain`);
  assert.deepEqual(found, [
    { text: "warn", bold: true, dim: false, color: 33 },
    { text: "dim", bold: true, dim: true, color: 33 },
    { text: "plain", bold: false, dim: false, color: null },
  ]);
  assert.equal(classes(found[1]), "b d c33");
  assert.equal(classes(found[2]), "");
});

test("any other SGR code changes nothing", () => {
  const found = runs(`${ESC}[31ma${ESC}[4;38;5;200;44mb`);
  assert.deepEqual(found, [{ text: "ab", bold: false, dim: false, color: 31 }]);
});

test("every other escape is dropped with what it carries", () => {
  const link = `${ESC}]8;;https://example.invalid/x${ESC}\\here${ESC}]8;;${ESC}\\`;
  assert.equal(text(link), "here");
  assert.equal(text(`${ESC}]0;title\u0007x`), "x");
  assert.equal(text(`a${ESC}[2Kb${ESC}[1;1Hc`), "abc");
  assert.equal(text(`a${ESC}7b${ESC}`), "ab");
  assert.equal(text(`a${ESC}[31`), "a");
});

test("control characters are dropped and a tab kept", () => {
  assert.equal(text("a\u0007b\u0000c\td\u007f"), "abc\td");
});

test("an empty row has no run", () => {
  assert.deepEqual(runs(""), []);
  assert.deepEqual(runs(`${ESC}[31m${ESC}[0m`), []);
});
