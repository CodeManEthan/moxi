import assert from "node:assert/strict";
import * as fs from "node:fs";
import test from "node:test";

import { defaultTheme, manifest, themeCss, themes } from "../gen.mjs";

const tokens = JSON.parse(fs.readFileSync(new URL("../tokens.json", import.meta.url), "utf8"));

const PAPER = { paper: { mode: "light" }, night: { mode: "dark" } };
const ONE = { one: { light: "paper", dark: "night" } };

/** A tokens.json with the given tokens, sets and themes: by default one theme of two sets. */
function file(more, $sets = PAPER, $themes = ONE) {
  return { $themes, $sets, ...more };
}

const colour = (values) => ({ $type: "color", $extensions: { moxi: { sets: values } } });

/** The `--moxi-` properties of one block of `css`, as [name, value] pairs in order. */
function properties(css, selector) {
  const start = css.indexOf(`${selector} {\n`);
  assert.notEqual(start, -1, selector);
  const block = css.slice(start, css.indexOf("\n}", start));
  return [...block.matchAll(/^ {2}--moxi-([a-z0-9-]+): (.*);$/gm)].map(([, name, value]) => [name, value]);
}

test("the themes: plain is the default, and midnight-neon keeps calm-workbench and midnight-neon", () => {
  assert.deepEqual(Object.entries(themes(tokens)), [
    ["plain", { light: "plain-day", dark: "plain-night" }],
    ["midnight-neon", { light: "calm-workbench", dark: "midnight-neon" }],
  ]);
  assert.deepEqual(defaultTheme(tokens), { name: "plain", light: "plain-day", dark: "plain-night" });
  assert.deepEqual(Object.keys(tokens.$sets), ["plain-day", "plain-night", "calm-workbench", "midnight-neon"]);
});

test("every token is a --moxi- property on :root, each colour the default theme's light and dark values", () => {
  const css = themeCss(tokens);
  const names = properties(css, ":root");
  assert.equal(names.length, 41);
  assert.deepEqual(names.map(([name]) => name), Object.keys(tokens).filter((name) => !name.startsWith("$")));
  const colours = names.filter(([, value]) => value.startsWith("light-dark("));
  assert.equal(colours.length, 34);
  for (const [name, value] of colours) {
    const values = tokens[name].$extensions.moxi.sets;
    assert.equal(value, `light-dark(${values["plain-day"]}, ${values["plain-night"]})`, name);
  }
  assert.equal(css.match(/^:root \{$/gm).length, 1);
  assert.match(css, /:root \{\n {2}color-scheme: light dark;/);
  assert.match(css, /:root\[data-theme="light"\] \{ color-scheme: light; \}/);
  assert.match(css, /:root\[data-theme="dark"\] \{ color-scheme: dark; \}/);
});

test("each other theme has one block of its colours, after :root, where the root names it", () => {
  const css = themeCss(tokens);
  assert.equal(css.match(/^:root\[data-theme-name="[^"]*"\] \{$/gm).length, 1);
  assert.ok(css.indexOf(':root[data-theme-name="midnight-neon"] {') > css.indexOf(":root {"));
  assert.doesNotMatch(css, /data-theme-name="plain"/);
  const block = properties(css, ':root[data-theme-name="midnight-neon"]');
  assert.equal(block.length, 34);
  assert.deepEqual(block.map(([name]) => name),
                   Object.keys(tokens).filter((name) => tokens[name].$type === "color"));
  for (const [name, value] of block) {
    const values = tokens[name].$extensions.moxi.sets;
    assert.equal(value, `light-dark(${values["calm-workbench"]}, ${values["midnight-neon"]})`, name);
  }
});

test("the header names every theme and its sets, the default first", () => {
  const css = themeCss(tokens);
  const header = css.slice(0, css.indexOf("*/"));
  assert.match(header, /The default theme, plain: light plain-day, dark plain-night\./);
  assert.match(header, /\n {3}midnight-neon: light calm-workbench, dark midnight-neon\. $/);
});

test("midnight-neon lost nothing: every colour it had keeps its two values", () => {
  const before = {
    bg: ["#f6f4f1", "#0a0918"], "bg-sunken": ["#efece7", "#0f0d22"], "bg-raised": ["#fffefd", "#141228"],
    "bg-hover": ["#f1eee9", "#1b1836"], border: ["#e4dfd7", "#2a2650"], "border-strong": ["#8f877c", "#6e63a0"],
    text: ["#1e1a22", "#eeeaf8"], "text-dim": ["#4f4856", "#aca5cb"], "text-faint": ["#686170", "#8f88b4"],
    accent: ["#5a3ccf", "#a78bfa"], "accent-soft": ["#efebfc", "#211a44"], error: ["#a63a26", "#ff7461"],
    "error-soft": ["#fcede9", "#2b1424"], warn: ["#94520f", "#ff9f45"], "warn-soft": ["#fcf0e3", "#2a1b14"],
    ok: ["#2a7049", "#5ee6b5"], live: ["#5a3ccf", "#a78bfa"], "live-soft": ["#efebfc", "#221d43"],
    launching: ["#b3307a", "#ff4f9a"], "launching-soft": ["#fbebf4", "#2e1530"],
    selected: ["#4529a8", "#d66bff"], "selected-soft": ["#efebfc", "#2a1747"],
    attention: ["#7d5c09", "#ffd25e"], "attention-soft": ["#fbf3dc", "#1f1a14"],
    "add-soft": ["#e6f4ea", "#13261f"], "del-soft": ["#fbe9e6", "#2b1424"],
    "series-1": ["#6a4fd6", "#ff9f45"], "series-2": ["#c03c86", "#ff4f9a"], "series-3": ["#c8682a", "#c77dff"],
    "series-4": ["#a8820f", "#7b6ff0"], "series-5": ["#2a7049", "#5ee6b5"], "series-6": ["#6c6572", "#ffd25e"],
  };
  assert.equal(Object.keys(before).length, 32);
  for (const [name, [light, dark]] of Object.entries(before)) {
    const values = tokens[name].$extensions.moxi.sets;
    assert.deepEqual([values["calm-workbench"], values["midnight-neon"]], [light, dark], name);
  }
});

test("the two added tokens: text on the accent, and danger, which is midnight-neon's error there", () => {
  const sets = (name) => tokens[name].$extensions.moxi.sets;
  assert.deepEqual(sets("on-accent"), { "plain-day": "#ffffff", "plain-night": "#10131c",
                                        "calm-workbench": "#ffffff", "midnight-neon": "#0a0918" });
  assert.deepEqual(sets("danger"), { "plain-day": "#b03c06", "plain-night": "#ff9e6b",
                                     "calm-workbench": sets("error")["calm-workbench"],
                                     "midnight-neon": sets("error")["midnight-neon"] });
});

test("plain is one text colour, one accent and greys: every state token is the text colour", () => {
  const sets = (name) => tokens[name].$extensions.moxi.sets;
  for (const set of ["plain-day", "plain-night"]) {
    for (const name of ["error", "warn", "ok", "live", "launching", "selected", "attention", "series-1"]) {
      assert.equal(sets(name)[set], sets("text")[set], `${name} in ${set}`);
    }
    const grey = (hex) => {
      const [r, g, b] = [1, 3, 5].map((at) => parseInt(hex.slice(at, at + 2), 16));
      return Math.max(r, g, b) - Math.min(r, g, b) <= 4;
    };
    for (const name of Object.keys(tokens)) {
      if (tokens[name].$type !== "color" || ["accent", "accent-soft", "on-accent", "danger"].includes(name)) continue;
      assert.ok(grey(sets(name)[set]), `${name} in ${set} is a grey`);
    }
  }
});

test("font stacks lead with Adwaita and quote only names with a space", () => {
  const css = themeCss(tokens);
  assert.match(css, /--moxi-font-sans: "Adwaita Sans", ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto/);
  assert.match(css, /--moxi-font-mono: "Adwaita Mono", ui-monospace, SFMono-Regular, "SF Mono", Menlo/);
  assert.match(css, /--moxi-font-size: 15px;/);
  assert.match(css, /--moxi-font-size-sm: 13px;/);
});

test("the manifest's colour is the default theme's light background", () => {
  const value = JSON.parse(manifest(tokens));
  assert.equal(value.background_color, tokens.bg.$extensions.moxi.sets["plain-day"]);
  const two = file({ bg: colour({ paper: "#ffffff", night: "#000000", dawn: "#eeeeee", dusk: "#111111" }) },
                   { dawn: { mode: "light" }, dusk: { mode: "dark" }, ...PAPER },
                   { one: { light: "paper", dark: "night" }, two: { light: "dawn", dark: "dusk" } });
  assert.equal(JSON.parse(manifest(two)).background_color, "#ffffff");
});

test("a theme's light and dark sets make one light-dark pair, whatever their order in $sets", () => {
  const css = themeCss(file({ bg: colour({ paper: "#ffffff", night: "#000000" }) },
                            { night: { mode: "dark" }, paper: { mode: "light" } }));
  assert.match(css, /--moxi-bg: light-dark\(#ffffff, #000000\);/);
  assert.doesNotMatch(css, /data-theme-name/);
});

test("a second theme's block holds its colours and no dimension or font", () => {
  const css = themeCss(file(
    { bg: colour({ paper: "#ffffff", night: "#000000", dawn: "#eeeeee", dusk: "#111111" }),
      gap: { $type: "dimension", $value: "1px" } },
    { ...PAPER, dawn: { mode: "light" }, dusk: { mode: "dark" } },
    { two: { light: "dawn", dark: "dusk" }, one: { light: "paper", dark: "night" } }));
  assert.deepEqual(properties(css, ":root"), [["bg", "light-dark(#eeeeee, #111111)"], ["gap", "1px"]]);
  assert.deepEqual(properties(css, ':root[data-theme-name="one"]'), [["bg", "light-dark(#ffffff, #000000)"]]);
});

test("$sets names sets, each light or dark", () => {
  const bg = { bg: colour({ paper: "#ffffff", night: "#000000" }) };
  assert.throws(() => themeCss({ $themes: ONE, ...bg }), /\$sets names/);
  assert.throws(() => themeCss(file(bg, { paper: { mode: "light" }, night: { mode: "dim" } })), /light or dark/);
  assert.throws(() => themeCss(file(bg, { paper: { mode: "light" }, Night: { mode: "dark" } })), /not a set name/);
});

test("$themes names themes, each one light set and one dark set of $sets", () => {
  const bg = { bg: colour({ paper: "#ffffff", night: "#000000" }) };
  const refuses = ($themes, why, $sets = PAPER) => assert.throws(() => themeCss(file(bg, $sets, $themes)), why);
  assert.throws(() => themeCss({ $sets: PAPER, ...bg }), /\$themes names the themes/);
  refuses([], /\$themes names the themes/);
  refuses({}, /at least one theme/);
  refuses({ One: { light: "paper", dark: "night" } }, /not a theme name/);
  refuses({ one: "paper" }, /one light set and one dark set/);
  refuses({ one: { light: "paper" } }, /one light set and one dark set/);
  refuses({ one: { dark: "night" } }, /one light set and one dark set/);
  refuses({ one: { light: "paper", dark: "night", dim: "night" } }, /one light set and one dark set/);
  refuses({ one: { light: "paper", dark: "dusk" } }, /"dusk" is not a set in \$sets/);
  refuses({ one: { light: "paper", dark: 3 } }, /is not a set in \$sets/);
  refuses({ one: { light: "night", dark: "paper" } }, /night is a dark set, not a light one/);
  refuses({ one: { light: "paper", dark: "paper" } }, /paper is a light set, not a dark one/);
});

test("a set is in exactly one theme", () => {
  const four = { ...PAPER, dawn: { mode: "light" }, dusk: { mode: "dark" } };
  const bg = { bg: colour({ paper: "#ffffff", night: "#000000", dawn: "#eeeeee", dusk: "#111111" }) };
  assert.throws(() => themeCss(file(bg, four, { one: { light: "paper", dark: "night" },
                                                two: { light: "dawn", dark: "night" } })),
                /night is in one and two/);
  assert.throws(() => themeCss(file(bg, four)), /dawn is in no theme/);
  assert.deepEqual(themes(file(bg, four, { two: { light: "dawn", dark: "dusk" }, one: { light: "paper", dark: "night" } })),
                   { two: { light: "dawn", dark: "dusk" }, one: { light: "paper", dark: "night" } });
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

test("a colour with no value in another theme's set is refused", () => {
  const four = { ...PAPER, dawn: { mode: "light" }, dusk: { mode: "dark" } };
  const two = { one: { light: "paper", dark: "night" }, two: { light: "dawn", dark: "dusk" } };
  assert.throws(() => themeCss(file({ bg: colour({ paper: "#ffffff", night: "#000000", dawn: "#eeeeee" }) }, four, two)),
                /dusk's value/);
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
