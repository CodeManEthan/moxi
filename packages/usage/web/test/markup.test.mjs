// The markup the page and its blocks write runs under the shell's style policy,
// `style-src 'self'`, which refuses a `style` attribute: a height or a width the page
// works out is written to the element's style instead. A `style=` anywhere in a source
// fails here.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import test from "node:test";

const SRC = new URL("../src/", import.meta.url);

test("no source writes a style attribute", () => {
  const found = [];
  for (const name of fs.readdirSync(SRC).filter((file) => file.endsWith(".ts")).sort()) {
    fs.readFileSync(new URL(name, SRC), "utf8").split("\n").forEach((line, at) => {
      if (/\bstyle\s*=|setAttribute\(\s*["']style["']|\.cssText\s*=|styleMap\(/.test(line)) found.push(`${name}:${at + 1}`);
    });
  }
  assert.deepEqual(found, []);
});
