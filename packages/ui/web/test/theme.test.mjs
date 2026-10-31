import assert from "node:assert/strict";
import * as fs from "node:fs";
import test from "node:test";

import { themeCss } from "../gen.mjs";

const tokens = JSON.parse(fs.readFileSync(new URL("../tokens.json", import.meta.url), "utf8"));

test("every token is a --moxi- property, each colour one light-dark pair", () => {
  const css = themeCss(tokens);
  const names = [...css.matchAll(/^ {2}--moxi-([a-z0-9-]+): (.*);$/gm)];
  assert.equal(names.length, 26);
  const colours = names.filter(([, , value]) => value.startsWith("light-dark("));
  assert.equal(colours.length, 19);
  for (const [, , value] of colours) assert.match(value, /^light-dark\(#[0-9a-f]{6}, #[0-9a-f]{6}\)$/);
  assert.match(css, /:root \{\n {2}color-scheme: light dark;/);
  assert.match(css, /:root\[data-theme="light"\] \{ color-scheme: light; \}/);
  assert.match(css, /:root\[data-theme="dark"\] \{ color-scheme: dark; \}/);
});

test("font stacks quote only names with a space", () => {
  assert.match(themeCss(tokens), /--moxi-font-sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto/);
});

test("a colour without a dark value, or a dimension with one, is refused", () => {
  assert.throws(() => themeCss({ bg: { $type: "color", $value: "#ffffff" } }), /dark/);
  assert.throws(() => themeCss({ gap: { $type: "dimension", $value: "1px", $extensions: { moxi: { dark: "2px" } } } }),
                /only a colour/);
  assert.throws(() => themeCss({ Bad: { $type: "dimension", $value: "1px" } }), /not a token name/);
});
