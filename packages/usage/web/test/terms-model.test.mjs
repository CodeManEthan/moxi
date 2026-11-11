import assert from "node:assert/strict";
import test from "node:test";

import { termsSectionPath } from "../src/filter-model.ts";
import {
  kindsSection, NONE, OPEN, pricedWords, priceRows, pricesFor, pricesSection, priceWords, savingWords,
  updatedWords, writeWords,
} from "../src/terms-model.ts";

const sums = (extra = {}) => ({ requests: 10, input: 0, fresh_input: 0, cache_read: 0, cache_write: 0,
  cache_write_1h: 0, output: 0, tokens: 0, unpriced_tokens: 0, unknown_rows: 0, cost: 0,
  complete: true, harness_cost: null, unknown: [], ...extra });
const term = (cls, tokens, cost) => ({ class: cls, tokens, price: cost === null ? null : 1, cost, price_from: "row", unknown: [] });
const group = (model, terms, extra = {}) => ({ model, provider: null, tier: null, above: 0, period: null,
  provenance: { layer: null, step: 1, rule: "exact", row: model, snapshot: null, overridden: [], period: null,
                note: null, filled: [], variant: null },
  requests: 1, terms, cost: terms.some((t) => t.cost === null) ? null : terms.reduce((a, t) => a + t.cost, 0),
  unpriced_tokens: 0, unknown_rows: 0, rule: null, why: null, unknown: [], ...extra });
const row = (model, groups, extra = {}) => ({ ...sums(), unpriced_models: [], rules: [], model, period: null,
  agent: null, kind: null, groups, split: [], ...extra });
const split = (cls, tokens, cost, tier = null) => ({ class: cls, tier, tokens, cost });
const answer = (rows, total, extra = {}) => ({ since: null, until: null, tz: "UTC", filters: [], rows,
  total: { ...sums(), split: [], saving: null, ...total }, unpriced_models: [], rules: [],
  index: { passed: false, last_pass: 1, note: null }, today: "2026-11-08", by: "model", ...extra });

const SAVING = { input_tokens: 71_000_000, read_tokens: 65_100_000, reads_at_input: 200.09, reads_cost: 13.74,
                 write_extra: 15.52, saved: 170.83 };
const TERMS = answer([row("m-a", [group("m-a", [term("input", 1_700_000, 3.72), term("cache_read", 65_100_000, 13.74),
                                               term("cache_write_5m", 3_400_000, 21.0), term("cache_write_1h", 800_000, 10.46),
                                               term("output", 900_000, 14.81)])])],
  { input: 71_000_000, fresh_input: 1_700_000, cache_read: 65_100_000, cache_write: 4_200_000, cache_write_1h: 800_000,
    output: 900_000, tokens: 71_900_000, cost: 63.73, saving: SAVING,
    split: [split("input", 1_700_000, 3.72), split("cache_read", 65_100_000, 13.74), split("cache_write_5m", 3_400_000, 21.0),
            split("cache_write_1h", 800_000, 10.46), split("output", 700_000, 12.81), split("output", 200_000, 2.0, "above_100k")] });

test("token kinds lists the four classes by cost with tokens, the 1-hour writes and a tier's part", () => {
  const kinds = kindsSection(TERMS);
  assert.equal(kinds.label, "Token kinds");
  assert.deepEqual(kinds.rows.map((r) => [r.name, r.tokens, r.cost]), [
    ["Cache writes", "4.20M tokens", "$31.46"], ["Output", "900k tokens", "$14.81"],
    ["Cache reads", "65.1M tokens", "$13.74"], ["Fresh input", "1.70M tokens", "$3.72"]]);
  assert.equal(kinds.rows[0].note, "800k of them for 1 hour");
  assert.equal(kinds.rows[1].note, "$2.00 at the over-100k-token price");
  assert.equal(kinds.rows[2].note, null);
  assert.ok(kinds.rows.every((r) => r.why === null));
});

test("closed token kinds says the input read from cache, rounded down, and the two dearest classes", () => {
  const kinds = kindsSection(TERMS);
  assert.equal(kinds.lead, "91% of input read from cache");
  assert.equal(kinds.dearest, "cache writes $31.46, output $14.81");
  assert.equal(kinds.count, "71.9M tokens");
});

test("a class the index cannot count is left out, and no input leaves the lead out", () => {
  const kinds = kindsSection(answer(TERMS.rows, { ...TERMS.total, unknown: ["fresh_input", "input"] }));
  assert.deepEqual(kinds.rows.map((r) => r.key), ["cache_write", "output", "cache_read"]);
  assert.equal(kinds.lead, null);
});

test("a class with tokens no price covers reads at least, and one with none priced reads no price", () => {
  const report = answer([row("m-a", [group("m-a", [term("output", 100, 1.5), term("input", 50, 0.5)])]),
                         row("m-x", [group("m-x", [term("output", 300, null), term("input", 20, null)])])],
    { input: 70, fresh_input: 70, output: 400, tokens: 470, cost: 1.5, complete: false, unpriced_tokens: 320,
      split: [split("output", 100, 1.5)] });
  const kinds = kindsSection(report);
  const output = kinds.rows.find((r) => r.key === "output");
  const fresh = kinds.rows.find((r) => r.key === "fresh_input");
  assert.equal(output.cost, "at least $1.50");
  assert.match(output.why, /300 tokens have no price \(m-x\)/);
  assert.equal(fresh.cost, "no price");
  assert.equal(kinds.rows.at(-1).key, "fresh_input");
  assert.equal(kinds.dearest, "output at least $1.50");
});

test("no answer and an answer with no row give no section", () => {
  assert.equal(kindsSection(null), null);
  assert.equal(kindsSection(answer([], {})), null);
  assert.equal(pricesSection(null, null, "2026-11-08"), null);
  assert.equal(pricesSection(answer([], {}), null, "2026-11-08"), null);
});

test("the saving sentence says what it stands for and ends as an estimate", () => {
  assert.equal(savingWords(SAVING), "65.1M of 71.0M input tokens were read from cache. With no cache, at the same "
    + "prices, those reads would have cost $200.09 as fresh input; they cost $13.74, and cache writes cost $15.52 "
    + "more than fresh input, so caching saved about $170.83. An estimate.");
  assert.equal(kindsSection(TERMS).saving, savingWords(SAVING));
});

test("the saving sentence has no writes clause when writes cost what input does", () => {
  const words = savingWords({ ...SAVING, write_extra: 0, saved: 186.35 });
  assert.equal(words, "65.1M of 71.0M input tokens were read from cache. With no cache, at the same prices, those "
    + "reads would have cost $200.09 as fresh input; they cost $13.74, so caching saved about $186.35. An estimate.");
});

test("at zero or less the saving sentence says what caching cost over what it saved", () => {
  const less = savingWords({ ...SAVING, reads_at_input: 10, reads_cost: 1, write_extra: 12.5, saved: -3.5 });
  assert.match(less, /, and cache writes cost \$12\.50 more than fresh input, so caching cost about \$3\.50 more than it saved\. An estimate\.$/);
  assert.match(savingWords({ ...SAVING, saved: 0 }), /caching cost about \$0\.00 more than it saved\. An estimate\.$/);
});

test("a null saving has no sentence", () => {
  assert.equal(savingWords(null), null);
  assert.equal(kindsSection(answer(TERMS.rows, { ...TERMS.total, saving: null })).saving, null);
});

test("prices are per million with two decimals or more, and none where the table has none", () => {
  assert.equal(priceWords(5), "5.00");
  assert.equal(priceWords(0.3), "0.30");
  assert.equal(priceWords(0.125), "0.125");
  assert.equal(priceWords(null), NONE);
  assert.equal(writeWords({ cache_write_5m: 6, cache_write_1h: 6 }), "6.00");
  assert.equal(writeWords({ cache_write_5m: 6.25, cache_write_1h: 10 }), "6.25 / 10.00");
  assert.equal(writeWords({ cache_write_5m: null, cache_write_1h: null }), "none");
  assert.equal(writeWords({ cache_write_5m: 3.75, cache_write_1h: null }), "3.75");
});

const rate = (model, prices, provider = null) => ({ model, asked_provider: provider, window: null, max_output: null,
  provider: null, tiers: [], provenance: group(model, []).provenance, unknown: [],
  input: null, output: null, cache_read: null, cache_write_5m: null, cache_write_1h: null, ...prices });

test("the price table names a model listed twice with its provider", () => {
  const rows = priceRows([rate("m-a", { input: 3, output: 15, cache_read: 0.3, cache_write_5m: 3.75, cache_write_1h: 6 }),
                          rate("m-b", { input: 1.25, output: 10, cache_read: 0.125 }),
                          rate("m-b", { input: 2 }, "openrouter")]);
  assert.deepEqual(rows.map((r) => [r.name, ...r.cells]), [
    ["m-a", "3.00", "15.00", "3.75 / 6.00", "0.30"],
    ["m-b (no provider)", "1.25", "10.00", "none", "0.125"],
    ["m-b (openrouter)", "2.00", "none", "none", "none"]]);
});

test("closed prices counts the models priced", () => {
  const priced = row("m-a", [], { cost: 1 });
  const not = row("m-x", [], { cost: null, unpriced_models: ["m-x"] });
  const part = row("m-y", [], { cost: 1, unpriced_models: ["m-y"] });
  assert.equal(pricedWords([priced, { ...priced, model: "m-b" }]), "2 models, all priced");
  assert.equal(pricedWords([priced, not, part]), "1 of 3 models priced");
  assert.equal(pricedWords([not, part]), "2 models, none priced");
  assert.equal(pricedWords([priced]), "1 model, priced");
  assert.equal(pricedWords([not]), "1 model, not priced");
});

test("the table's date is the link, with its year when another, and Open with none", () => {
  assert.equal(updatedWords("2026-11-02", "2026-11-08"), "updated Nov 2");
  assert.equal(updatedWords("2025-12-30", "2026-11-08"), "updated Dec 30, 2025");
  assert.equal(updatedWords(null, "2026-11-08"), OPEN);
});

test("prices reads the answer's models under their providers, or the table alone", () => {
  const report = answer([row("m-a", [group("m-a", []), group("m-a", [], { provider: "openrouter" })])], {});
  const query = new URLSearchParams(pricesFor(report).split("?")[1]);
  assert.equal(query.get("models"), "m-a,m-a");
  assert.equal(query.get("providers"), ",openrouter");
  assert.equal(pricesFor(answer([row(null, [group(null, [])])], {})), "api/prices");
});

test("opened prices has the table once read, the unpriced models and the drawn paragraph", () => {
  const report = answer([row("m-a", [], { cost: 1 }), row("m-x", [], { cost: null, unpriced_models: ["m-x"] })], {},
                        { unpriced_models: ["m-x"] });
  const prices = { effective_date: "2026-11-02", rows: 3000, layers: [], overrides: [], problems: [], matched: 0,
                   models: [rate("m-a", { input: 3 })] };
  const before = pricesSection(report, null, "2026-11-08");
  assert.equal(before.rows, null);
  assert.equal(before.count, OPEN);
  const model = pricesSection(report, prices, "2026-11-08");
  assert.equal(model.closed, "1 of 2 models priced");
  assert.equal(model.note, "a local price table, not billing data");
  assert.equal(model.count, "updated Nov 2");
  assert.deepEqual(model.rows.map((r) => r.name), ["m-a"]);
  assert.equal(model.unpriced, "No price for m-x.");
  assert.match(model.paragraph, /^Every figure on this page is tokens from your transcripts times these prices\./);
});

test("the sections' terms are read by model under every filter", () => {
  const path = termsSectionPath({ range: { kind: "7d" }, measure: "cost", harness: "codex", model: "m-a", project: "proj-a",
                                  folder: "" }, "2026-11-08");
  const query = new URLSearchParams(path.split("?")[1]);
  assert.ok(path.startsWith("api/terms?"));
  assert.equal(query.get("by"), "model");
  assert.equal(query.get("model"), "m-a");
  assert.equal(query.get("project"), "proj-a");
  assert.equal(query.get("harness"), "codex");
});
