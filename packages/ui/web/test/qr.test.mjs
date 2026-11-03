import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import { alignment, dataCodewords, encode, svgPath } from "../src/qr.ts";

const URL = "https://moxi.example.com:8443/pair?n=0123456789abcdef#c=" + "Kq3".repeat(14) + "Z";

function digest(code) {
  const rows = code.dark.map((row) => row.map((on) => (on ? "1" : "0")).join("")).join("\n");
  return createHash("sha256").update(rows).digest("hex");
}

// Each matrix, as a reference encoder (level M, byte mode, the mask forced) draws it.
const PINNED = [
  [URL, 0, 6, "b9b503816756cfb587015747b234ece00734962f3e91d04f94afbdd924c91b82"],
  [URL, 1, 6, "0856328bbd1bbe11f3cc3b7d197c5b8ca94e4ce3131a72f39a201153da02b7ab"],
  [URL, 2, 6, "2ff550ec57098c923cdef65c9ea80c825da71e2ce6c05fc736558725a7a41ad1"],
  [URL, 3, 6, "ce48c0860511cd1108006876faa90c9286db5087a148a87d31f0d1bf8e0dc81c"],
  [URL, 4, 6, "9c165f46a6539bf800a006b62cee56e83e9084b7682c0938d97296dac08e4f53"],
  [URL, 5, 6, "36b06aa8c984e0b6e920e18b05d1749b38218e5004c5eb5b005121d2eff2bde5"],
  [URL, 6, 6, "707887d6b5ac4684e143120ab9ab20ca920137ef4809c298550efb4a9e7cb4e3"],
  [URL, 7, 6, "9271de7b8bcff8d42df3635456d0a68e0ba07048e69cd0d71bcf54df29fe3bb0"],
  ["x".repeat(300), 3, 13, "06540e7d3086d1a1c7ae802f6c37eb982dcabd79b88808fcc24dfcc97fd1df3b"],
  ["héllo ✓", 2, 1, "60282b551baab2e0d2ab28a51fc3d717756c7af5c37c462e792235d9132bcc35"],
  ["y".repeat(1000), 6, 26, "ee925c2b04b15b6edd27f43918184d92b79dce296078123e8590efef917edd7e"],
];

test("each matrix is the one a reference encoder draws", () => {
  for (const [text, mask, version, sha] of PINNED) {
    const code = encode(text, mask);
    assert.equal(code.version, version, `${text.slice(0, 12)} mask ${mask}`);
    assert.equal(code.size, version * 4 + 17);
    assert.equal(digest(code), sha, `${text.slice(0, 12)} mask ${mask}`);
  }
});

test("the smallest version that holds the text, and the mask with the lowest penalty", () => {
  const code = encode(URL);
  assert.equal(code.version, 6);
  assert.equal(code.mask, 2);
  assert.equal(encode("a").version, 1);
  // Level M's byte capacity: version 1 holds 14 bytes, version 40 holds 2,331.
  assert.equal(encode("a".repeat(14)).version, 1);
  assert.equal(encode("a".repeat(15)).version, 2);
  assert.equal(encode("a".repeat(2331)).version, 40);
  assert.throws(() => encode("a".repeat(2332)), RangeError);
});

test("the tables agree with the standard's capacities and alignment centres", () => {
  assert.deepEqual([1, 2, 7, 10, 40].map(dataCodewords), [16, 28, 124, 216, 2334]);
  assert.deepEqual(alignment(1), []);
  assert.deepEqual(alignment(7), [6, 22, 38]);
  assert.deepEqual(alignment(32), [6, 34, 60, 86, 112, 138]);
  assert.deepEqual(alignment(40), [6, 30, 58, 86, 114, 142, 170]);
});

test("the SVG path draws one unit square per dark module", () => {
  const code = encode("a", 0);
  const path = svgPath(code);
  const dark = code.dark.flat().filter(Boolean).length;
  assert.equal(path.match(/M/g).length, dark);
  assert.ok(path.startsWith("M0 0h1v1h-1z"));
});
