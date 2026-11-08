import assert from "node:assert/strict";
import * as fs from "node:fs";
import test from "node:test";

import { sets, themeCss } from "../gen.mjs";

const tokens = JSON.parse(fs.readFileSync(new URL("../tokens.json", import.meta.url), "utf8"));

/** A tokens.json with two sets and the given tokens. */
function file(more, $sets = { paper: { mode: "light" }, night: { mode: "dark" } }) {
  return { $sets, ...more };
}

const colour = (values) => ({ $type: "color", $extensions: { moxi: { sets: values } } });

test("the two sets: calm-workbench is light and midnight-neon dark", () => {
  assert.deepEqual(sets(tokens), { light: "calm-workbench", dark: "midnight-neon" });
});

test("every token is a --moxi- property, each colour the light set's and the dark set's value", () => {
  const css = themeCss(tokens);
  const names = [...css.matchAll(/^ {2}--moxi-([a-z0-9-]+): (.*);$/gm)];
  assert.equal(names.length, 39);
  const colours = names.filter(([, , value]) => value.startsWith("light-dark("));
  assert.equal(colours.length, 32);
  for (const [, name, value] of colours) {
    const values = tokens[name].$extensions.moxi.sets;
    assert.equal(value, `light-dark(${values["calm-workbench"]}, ${values["midnight-neon"]})`, name);
  }
  assert.equal(css.match(/^:root \{$/gm).length, 1);
  assert.match(css, /:root \{\n {2}color-scheme: light dark;/);
  assert.match(css, /:root\[data-theme="light"\] \{ color-scheme: light; \}/);
  assert.match(css, /:root\[data-theme="dark"\] \{ color-scheme: dark; \}/);
  assert.match(css, /Light: calm-workbench\. Dark: midnight-neon\./);
});

test("font stacks quote only names with a space", () => {
  assert.match(themeCss(tokens), /--moxi-font-sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto/);
});

test("a light and a dark set make one light-dark pair, whatever their order in $sets", () => {
  const css = themeCss(file({ bg: colour({ paper: "#ffffff", night: "#000000" }) },
                            { night: { mode: "dark" }, paper: { mode: "light" } }));
  assert.match(css, /--moxi-bg: light-dark\(#ffffff, #000000\);/);
});

test("$sets holds exactly two named sets, one light and one dark", () => {
  const bg = { bg: colour({ paper: "#ffffff", night: "#000000" }) };
  assert.throws(() => themeCss(bg), /\$sets names/);
  assert.throws(() => themeCss(file(bg, { paper: { mode: "light" } })), /exactly two/);
  assert.throws(() => themeCss(file(bg, { paper: { mode: "light" }, night: { mode: "dark" }, dusk: { mode: "dark" } })),
                /both dark/);
  assert.throws(() => themeCss(file(bg, { paper: { mode: "light" }, night: { mode: "light" } })), /both light/);
  assert.throws(() => themeCss(file(bg, { paper: { mode: "light" }, night: { mode: "dim" } })), /light or dark/);
  assert.throws(() => themeCss(file(bg, { paper: { mode: "light" }, Night: { mode: "dark" } })), /not a set name/);
});

test("a colour names every set, each #rrggbb, and nothing else", () => {
  assert.throws(() => themeCss(file({ bg: colour({ paper: "#ffffff" }) })), /night's value/);
  assert.throws(() => themeCss(file({ bg: colour({ paper: "#ffffff", night: "black" }) })), /night's value/);
  assert.throws(() => themeCss(file({ bg: colour({ paper: "#ffffff", night: "#000000", dusk: "#111111" }) })),
                /dusk is not a set/);
  assert.throws(() => themeCss(file({ bg: { $type: "color", $value: "#ffffff" } })), /not \$value or dark/);
  assert.throws(() => themeCss(file({ bg: { ...colour({ paper: "#ffffff", night: "#000000" }), $value: "#ffffff" } })),
                /not \$value or dark/);
  assert.throws(() => themeCss(file({ bg: { $type: "color", $extensions: { moxi: { dark: "#000000" } } } })),
                /not \$value or dark/);
  assert.throws(() => themeCss(file({ bg: { $type: "color" } })), /a value for each set/);
});

test("a dimension or a font names no set", () => {
  assert.throws(() => themeCss(file({ gap: { $type: "dimension", $value: "1px",
                                             $extensions: { moxi: { sets: { paper: "2px", night: "3px" } } } } })),
                /only a colour/);
  assert.throws(() => themeCss(file({ gap: { $type: "dimension", $value: "1px", $extensions: { moxi: { dark: "2px" } } } })),
                /only a colour/);
  assert.throws(() => themeCss(file({ "font-sans": { $type: "fontFamily", $value: ["serif"],
                                                     $extensions: { moxi: { sets: { paper: ["serif"] } } } } })),
                /only a colour/);
  assert.throws(() => themeCss(file({ Bad: { $type: "dimension", $value: "1px" } })), /not a token name/);
});
