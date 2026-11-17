// Every colour set in tokens.json holds these contrast floors, a set added later included:
//
// - 4.5:1 (body text) for each token that carries text, on each background and on its own
//   -soft fill, and for `text` on every soft fill;
// - 4.5:1 for `bg` on each solid fill, as on a filled button, and for `on-accent` on
//   `accent`;
// - 3:1 (a mark that isn't text) for `border-strong` and each chart series on the page's
//   backgrounds.
//
// `border` is a quiet divider and has no floor.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import test from "node:test";

const tokens = JSON.parse(fs.readFileSync(new URL("../tokens.json", import.meta.url), "utf8"));

const TEXT = ["text", "text-dim", "text-faint", "accent", "error", "danger", "warn", "ok", "live", "launching",
              "selected", "attention"];
const BACKGROUNDS = ["bg", "bg-raised", "bg-sunken", "bg-hover"];
const SOFT = ["accent-soft", "error-soft", "warn-soft", "live-soft", "launching-soft", "selected-soft",
              "attention-soft", "add-soft", "del-soft"];
const FILLS = ["accent", "error", "live", "launching", "selected", "attention"];
const MARKS = ["border-strong", "series-1", "series-2", "series-3", "series-4", "series-5", "series-6"];

/** WCAG relative luminance of `#rrggbb`. */
function luminance(hex) {
  const channel = (at) => {
    const c = parseInt(hex.slice(at, at + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}

/** WCAG contrast ratio of two `#rrggbb` colours. */
function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** Each pair a set must hold, as [foreground, background, floor]. */
function pairs() {
  const out = [];
  for (const fg of TEXT) {
    for (const bg of BACKGROUNDS) out.push([fg, bg, 4.5]);
    if (`${fg}-soft` in tokens) out.push([fg, `${fg}-soft`, 4.5]);
  }
  for (const soft of SOFT) out.push(["text", soft, 4.5]);
  for (const fill of FILLS) out.push(["bg", fill, 4.5]);
  out.push(["on-accent", "accent", 4.5]);
  for (const mark of MARKS) for (const bg of ["bg", "bg-raised"]) out.push([mark, bg, 3]);
  return out;
}

test("the ratio is WCAG's", () => {
  assert.equal(contrast("#000000", "#ffffff"), 21);
  assert.equal(contrast("#777777", "#777777"), 1);
  assert.ok(Math.abs(contrast("#767676", "#ffffff") - 4.54) < 0.01);
});

for (const set of Object.keys(tokens.$sets)) {
  test(`${set} holds every contrast floor`, () => {
    const value = (name) => {
      assert.ok(name in tokens, `${name} is a token`);
      return tokens[name].$extensions.moxi.sets[set];
    };
    const failed = [];
    for (const [fg, bg, floor] of pairs()) {
      const ratio = contrast(value(fg), value(bg));
      if (ratio < floor) failed.push(`${fg} on ${bg}: ${ratio.toFixed(2)} < ${floor}`);
    }
    assert.deepEqual(failed, []);
  });
}
