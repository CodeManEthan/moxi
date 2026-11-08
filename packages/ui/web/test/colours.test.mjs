// The build's colour check: a page's sources draw colours from the theme's tokens, so the
// build refuses a colour literal in them unless its line says, in a comment holding
// `colour-fixed`, why it is fixed.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import test from "node:test";

import * as esbuild from "esbuild";

import {
  BuildError, build, checkColours, colourLiterals, colourSources, listInputs, repoRoot,
  webPackages,
} from "../../../../tools/web/build.mjs";

const found = (text) => colourLiterals(text).map(({ literal }) => literal);

test("a hex colour is found; a private name that looks like one is not", () => {
  for (const [line, literals] of [
    ["  color: #fff;", ["#fff"]],
    ["<path d=${d} fill=\"#000000\"></path>", ["#000000"]],
    ["const pair = [\"#ffffff\", \"#0a0918\"];", ["#ffffff", "#0a0918"]],
    ["background: #abcd", ["#abcd"]],
    ["border: 1px solid #11223344;", ["#11223344"]],
    ["color: #FFF", ["#FFF"]],
  ]) {
    assert.deepEqual(found(line), literals, line);
  }
  for (const line of [
    "this.#feed.close();",
    "  #feed: Feed<Row> | null = null;",
    "  #feed = null;",
    "  #add(): Promise<void> {",
    "  async #add() {",
    "this.#feed?.close();",
    "#feed.close();",
    "<a href=\"#top\">top</a>",
    "const odd = \"#abcdef0\";",
    "const ref = \"&#1234;\";",
  ]) {
    assert.deepEqual(found(line), [], line);
  }
});

test("each colour function is found, by its name and bracket", () => {
  for (const name of ["rgb", "rgba", "hsl", "hsla", "hwb", "lab", "lch", "oklab", "oklch",
                      "color", "color-mix", "RGB"]) {
    assert.deepEqual(found(`x { --a: ${name}(0 0 0); }`), [`${name}(`], name);
  }
  assert.deepEqual(found("dialog::backdrop { background: rgb(0 0 0 / 0.4); }"), ["rgb("]);
  for (const line of ["const c = colour(key, i);", "seriesColor(x)", "this.color(x)",
                      "flab(x)", "--moxi-lab(x)"]) {
    assert.deepEqual(found(line), [], line);
  }
});

test("a named or system colour in a colour property's value is found", () => {
  for (const [line, literals] of [
    ["  color: red;", ["red"]],
    ["border: 1px solid Black", ["Black"]],
    ["background: Canvas;", ["Canvas"]],
    ["box-shadow: 0 0 8px hotpink;", ["hotpink"]],
    ["outline-color: ButtonText", ["ButtonText"]],
    ["text-decoration-color: tomato", ["tomato"]],
    ["caret-color: gold; accent-color: teal;", ["gold", "teal"]],
    ["stroke: navy; fill: white", ["navy", "white"]],
    ["background-color: var(--moxi-bg, white)", ["white"]],
    ["background: color-mix(in srgb, var(--moxi-accent) 50%, white);", ["color-mix(", "white"]],
  ]) {
    assert.deepEqual(found(line), literals, line);
  }
  for (const line of [
    "color: var(--moxi-text);",
    "background: transparent; color: currentColor; border-color: inherit;",
    "outline: none; fill: none; text-decoration: underline dotted;",
    "animation-fill-mode: forwards;",
    "--moxi-border: var(--moxi-red-soft);",
    "const mode = { color: field.colour };",
    "Red is drawn with the theme's error colour",
    "const red = rows.filter((row) => row.red);",
  ]) {
    assert.deepEqual(found(line), [], line);
  }
});

test("the colour properties a hyphen once hid are read too", () => {
  for (const [line, literals] of [
    ["stop-color: white;", ["white"]],
    ["flood-color: black", ["black"]],
    ["lighting-color: red;", ["red"]],
    ["column-rule-color: gray", ["gray"]],
    ["column-rule: 1px solid silver;", ["silver"]],
    ["scrollbar-color: navy Canvas;", ["navy", "Canvas"]],
  ]) {
    assert.deepEqual(found(line), literals, line);
  }
  for (const line of [
    "stop-color: var(--moxi-accent);",
    "flood-color: currentColor; lighting-color: transparent;",
    "scrollbar-color: var(--moxi-border) transparent;",
  ]) {
    assert.deepEqual(found(line), [], line);
  }
});

test("a named colour in an attribute, a style assignment or a style key is found", () => {
  for (const [line, literals] of [
    ["<rect fill=\"red\"></rect>", ["red"]],
    ["<path stroke='black'/>", ["black"]],
    ["<rect fill=red>", ["red"]],
    ["svg`<line x1=${a} stroke=\"white\" stroke-width=\"2\">`", ["white"]],
    ["html`<font color=\"Teal\">x</font>`", ["Teal"]],
    ["<stop offset=\"0\" stop-color=\"gold\"/>", ["gold"]],
    ["<feFlood flood-color='black'/>", ["black"]],
    ["<feDiffuseLighting lighting-color=white>", ["white"]],
    ["this.style.color = \"red\";", ["red"]],
    ["el.style.backgroundColor = 'white';", ["white"]],
    ["el.style.border = \"1px solid black\";", ["black"]],
    ["styleMap({ borderColor: \"red\", outlineColor: \"Highlight\" })", ["red", "Highlight"]],
    ["styleMap({ color: \"red\" })", ["red"]],
  ]) {
    assert.deepEqual(found(line), literals, line);
  }
  for (const line of [
    "<path d=${d} fill=\"none\" stroke=\"currentColor\"></path>",
    "<rect fill=\"var(--moxi-accent)\" stroke=\"transparent\"></rect>",
    "<rect fill=none stroke=currentColor>",
    "svg`<line stroke=${colour(key)} stroke-width=\"1\">`",
    "<circle fill=\"${this.tone}\"/>",
    "el.style.color = \"var(--moxi-text)\";",
    "el.style.backgroundColor = tone;",
    "const fill = highlight;",
    "if (row.color === \"red\") return;",
    "const seriesColor = colours[i];",
    "<div data-color=\"red\" class=\"white\"></div>",
    "{ borderRadius: \"4px\", textColor: \"inherit\" }",
  ]) {
    assert.deepEqual(found(line), [], line);
  }
});

test("a line passes only when a comment on it holds colour-fixed", () => {
  assert.deepEqual(found("p { background: rgb(0 0 0 / 0.4); } /* colour-fixed: the scrim */"), []);
  assert.deepEqual(found("<rect fill=\"#ffffff\"></rect><!-- colour-fixed: for scanners -->"), []);
  assert.deepEqual(found("const field = \"#ffffff\"; // colour-fixed: the QR code's field"), []);
  assert.deepEqual(found("const note = \"colour-fixed\"; const c = \"#fff\";"), ["#fff"]);
  // The marker excuses its own line only.
  assert.deepEqual(colourLiterals("/* colour-fixed */\ncolor: #fff;\n\nfill: red;"),
                   [{ line: 2, literal: "#fff" }, { line: 4, literal: "red" }]);
});

test("the check reads web/src's .ts files and the copied .css and .html, nothing else", () => {
  const inputs = [
    "packages/a/web/build.json", "packages/a/web/gen.mjs", "packages/a/web/shell.css",
    "packages/a/web/src/page.ts", "packages/a/web/src/deep/part.ts",
    "packages/a/web/test/page.test.mjs", "packages/a/web/tokens.json",
    "packages/a/web/index.html", "packages/a/web/icon.png", "packages/b/web/src/page.ts",
    "tools/web/build.mjs",
  ];
  const configs = new Map([["a", { copy: ["shell.css", "index.html", "icon.png"] }],
                           ["b", { copy: [] }]]);
  assert.deepEqual(colourSources(inputs, configs), [
    "packages/a/web/index.html", "packages/a/web/shell.css", "packages/a/web/src/deep/part.ts",
    "packages/a/web/src/page.ts", "packages/b/web/src/page.ts",
  ]);
});

test("the check refuses naming each file, line and literal", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "moxi-colours-"));
  try {
    const write = (rel, text) => {
      fs.mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
      fs.writeFileSync(path.join(root, rel), text);
    };
    write("packages/a/web/src/page.ts", "const css = `\n  p { color: red; }\n`;\n");
    write("packages/a/web/shell.css", "body { background: var(--moxi-bg); }\n"
                                      + "a { color: #00f; }\n");
    const inputs = ["packages/a/web/shell.css", "packages/a/web/src/page.ts"];
    const configs = new Map([["a", { copy: ["shell.css"] }]]);
    assert.throws(() => checkColours(root, inputs, configs), (err) => {
      assert.ok(err instanceof BuildError);
      assert.match(err.message, /colour-fixed/);
      assert.ok(err.message.endsWith("\n  packages/a/web/shell.css:2: #00f"
                                     + "\n  packages/a/web/src/page.ts:2: red"), err.message);
      return true;
    });
    write("packages/a/web/shell.css", "a { color: var(--moxi-accent); }\n");
    write("packages/a/web/src/page.ts", "const css = `p { color: red; }`; // colour-fixed: a test\n");
    checkColours(root, inputs, configs);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("the build stops on a colour literal before it bundles anything", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "moxi-colours-build-"));
  try {
    const write = (rel, text) => {
      fs.mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
      fs.writeFileSync(path.join(root, rel), text);
    };
    write("package-lock.json", JSON.stringify({
      packages: { "node_modules/esbuild": { version: esbuild.version } } }));
    write(".gitattributes", "* text eol=lf\n");
    write("packages/a/web/build.json", JSON.stringify({ entries: { "page.js": "src/page.ts" } }));
    write("packages/a/web/src/page.ts", "export const css = \"p { color: #123456; }\";\n");
    assert.equal(spawnSync("git", ["init", "-q"], { cwd: root }).status, 0);
    await assert.rejects(build({ root }), (err) => {
      assert.ok(err instanceof BuildError);
      assert.match(err.message, /packages\/a\/web\/src\/page\.ts:1: #123456/);
      return true;
    });
    assert.equal(fs.existsSync(path.join(root, "packages/a/src")), false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("the repository's pages pass, and only the QR code and the dialog scrim are fixed", () => {
  const root = repoRoot();
  const inputs = listInputs(root);
  const configs = new Map(webPackages(inputs).map((name) => {
    const file = path.join(root, "packages", name, "web", "build.json");
    const config = JSON.parse(fs.readFileSync(file, "utf8"));
    return [name, { copy: config.copy ?? [] }];
  }));
  checkColours(root, inputs, configs);
  const fixed = [];
  for (const rel of colourSources(inputs, configs)) {
    for (const line of fs.readFileSync(path.join(root, rel), "utf8").split("\n")) {
      if (line.includes("colour-fixed")) fixed.push(rel);
    }
  }
  assert.deepEqual(fixed, [
    "packages/ui/web/src/confirm.ts", "packages/ui/web/src/gateway-desk.ts",
    "packages/ui/web/src/gateway-desk.ts",
  ]);
});
