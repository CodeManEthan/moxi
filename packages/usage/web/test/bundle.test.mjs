// The built bundles carry the ISC notice of every d3 package they hold. d3's sources have
// no licence comment of their own, so the notice is the page entry's `/*! ... */`
// comment, which the build keeps at the end of the file.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import test from "node:test";

const BUILT = new URL("../../src/moxi_usage/web/", import.meta.url);
const read = (name) => fs.readFileSync(new URL(name, BUILT), "utf8");
const D3 = ["d3-array", "d3-color", "d3-format", "d3-hierarchy", "d3-interpolate", "d3-scale", "internmap"];

test("the page bundle's tail holds the ISC notice of each d3 package in it", () => {
  const page = read("page.js");
  const tail = page.slice(page.lastIndexOf("/*! Bundled with d3"));
  assert.ok(tail.length > 0 && tail.length < 4000, "the notice is the bundle's last comment");
  assert.match(tail, /each under the ISC licence/);
  assert.match(tail, /Permission to use, copy, modify, and\/or distribute this software for any purpose/);
  assert.match(tail, /THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES/);
  for (const name of D3) {
    const version = JSON.parse(fs.readFileSync(new URL(`../../../../node_modules/${name}/package.json`, import.meta.url), "utf8")).version;
    assert.match(tail, new RegExp(`\\* ${name} ${version.replaceAll(".", "\\.")}: Copyright [0-9-]+ Mike Bostock`), name);
  }
});

test("the limits and cost bundles hold no d3, so they need no notice", () => {
  for (const name of ["limits.js", "cost.js"]) {
    const text = read(name);
    assert.ok(!text.includes("paddingInner") && !text.includes("tickFormat"), name);
  }
});
