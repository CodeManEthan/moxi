// The built bundles carry the ISC notice of every d3 package they hold. d3's sources have
// no licence comment of their own, so the notice is the page entry's `/*! ... */`
// comment, which the build keeps at the end of the file. Which packages the page holds is
// read from esbuild's own account of the bundle, its metafile, so a new import that pulls
// in another package fails here until the notice names it.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import * as esbuild from "esbuild";

import { OPTIONS } from "../../../../tools/web/build.mjs";

const ROOT = fileURLToPath(new URL("../../../../", import.meta.url));
const BUILT = new URL("../../src/moxi_usage/web/", import.meta.url);
const read = (name) => fs.readFileSync(new URL(name, BUILT), "utf8");
/** Lit's packages carry their own licence comments, which the build keeps. */
const LIT = new Set(["lit", "lit-html", "lit-element", "@lit/reactive-element"]);

/** Each package an entry's bundle holds code of, by name, with the directory it came from:
 * the inputs the metafile counts bytes of in the output, not every file esbuild read. */
async function bundled(entry) {
  const result = await esbuild.build({
    ...OPTIONS, absWorkingDir: ROOT, entryPoints: [`packages/usage/web/src/${entry}`],
    tsconfig: path.join(ROOT, "packages/usage/web/tsconfig.json"), outdir: "out",
    write: false, metafile: true, logLevel: "silent",
  });
  const found = new Map();
  const [output] = Object.values(result.metafile.outputs);
  for (const [input, { bytesInOutput }] of Object.entries(output.inputs)) {
    if (bytesInOutput === 0) continue;
    const match = /^(.*?node_modules\/)((?:@[^/]+\/)?[^/]+)\//.exec(input);
    if (match) found.set(match[2], path.join(ROOT, match[1], match[2]));
  }
  return found;
}

test("the page bundle's tail holds the ISC notice of each package in it but Lit's", async () => {
  const packages = await bundled("page.ts");
  const d3 = [...packages.keys()].filter((name) => !LIT.has(name)).sort();
  assert.deepEqual(d3, ["d3-hierarchy"], "the subagents' map is the one piece drawn with d3");
  const page = read("page.js");
  const tail = page.slice(page.lastIndexOf("/*! Bundled with d3"));
  assert.ok(tail.length > 0 && tail.length < 4000, "the notice is the bundle's last comment");
  assert.match(tail, /under the ISC licence/);
  assert.match(tail, /Permission to use, copy, modify, and\/or distribute this software for any purpose/);
  assert.match(tail, /THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES/);
  for (const name of d3) {
    const version = JSON.parse(fs.readFileSync(path.join(packages.get(name), "package.json"), "utf8")).version;
    assert.match(tail, new RegExp(`\\* ${name} ${version.replaceAll(".", "\\.")}: Copyright [0-9-]+ Mike Bostock`),
                 `the page bundles ${name} ${version}, which its notice doesn't name`);
  }
  const named = [...tail.matchAll(/^ \* (\S+) [0-9.]+: Copyright/gm)].map((match) => match[1]).sort();
  assert.deepEqual(named, d3, "the notice names exactly the packages the page bundles");
});

test("the limits and cost bundles hold no d3, so they need no notice", async () => {
  for (const entry of ["limits.ts", "cost.ts"]) {
    const packages = await bundled(entry);
    assert.deepEqual([...packages.keys()].filter((name) => !LIT.has(name)), [], entry);
    const text = read(entry.replace(".ts", ".js"));
    assert.ok(!text.includes("Mike Bostock"), entry);
  }
});
