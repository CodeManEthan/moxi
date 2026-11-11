// The Token kinds and Prices sections with no DOM, both from one cost-terms answer by
// model over the range. Token kinds: how much of the input was read from cache and the
// dearest classes, opened the four classes with their tokens and cost, then what caching
// saved, an estimate. Prices: how many of the range's models the table prices and the
// table's date, opened those models' prices per million tokens, then the acts that open
// under the table: a search of the model table, the bill's lines and the details behind
// each price. A class's cost that is not whole reads `at least`; one nothing in which
// could be priced `no price`, never a zero.

import type { Sum } from "./cost-model.ts";
import { costWords, countWords, whyNotWhole } from "./cost-model.ts";
import { dateWord } from "./days.ts";
import type { Prices, PriceSet, Rate, Report, Saving, TermsRow, TermsTotal } from "./feed.ts";
import { percentWords } from "./limits-model.ts";
import { fmtCost, fmtTokens, pricesPath, rateAsks, UNKNOWN_MODEL } from "./views.ts";

export const KINDS_LABEL = "Token kinds";
export const PRICES_LABEL = "Prices";

/** The four classes Token kinds lists: the class's key, its name, the total's token
 * count it shows, and the cost terms' classes its dollars are summed from. */
export const KINDS = [
  { key: "cache_write", name: "Cache writes", tokens: "cache_write", terms: ["cache_write_5m", "cache_write_1h"] },
  { key: "output", name: "Output", tokens: "output", terms: ["output"] },
  { key: "cache_read", name: "Cache reads", tokens: "cache_read", terms: ["cache_read"] },
  { key: "fresh_input", name: "Fresh input", tokens: "fresh_input", terms: ["input"] },
] as const;

/** One opened row of Token kinds. */
export interface KindRow {
  key: string;
  name: string;
  /** Dim words after the name: the 1-hour part of cache writes, each price tier's part
   * of the cost; null with neither. */
  note: string | null;
  /** `4.2M tokens`. */
  tokens: string;
  /** `$31.46`, `at least $3.00`, `no price`. */
  cost: string;
  /** Why the cost reads `at least` or `no price`; null for a whole one. */
  why: string | null;
}

export interface Kinds {
  label: string;
  /** `92% of input read from cache`; null when the input is not known. */
  lead: string | null;
  /** `cache writes $31.46, output $14.81`: the two dearest priced classes; null with none. */
  dearest: string | null;
  /** `71.9M tokens`: the link that opens the section. */
  count: string;
  rows: KindRow[];
  /** What caching saved, in a sentence; null when the answer cannot say. */
  saving: string | null;
}

type Terms = Report<TermsRow, TermsTotal>;

/** A class's figures over the answer: its tokens, its priced dollars and what of it has no price. */
export function kindSum(report: Terms, terms: readonly string[], tokens: number): Sum & { tiers: Map<string, number> } {
  const total = report.total;
  const split = total.split.filter((item) => terms.includes(item.class));
  const priced = split.reduce((sum, item) => sum + item.cost, 0);
  const tiers = new Map<string, number>();
  for (const item of split) if (item.tier !== null) tiers.set(item.tier, (tiers.get(item.tier) ?? 0) + item.cost);
  let unpriced = 0;
  const models = new Set<string>();
  for (const row of report.rows) {
    for (const group of row.groups) {
      for (const term of group.terms) {
        if (!terms.includes(term.class) || term.cost !== null || term.tokens <= 0) continue;
        unpriced += term.tokens;
        models.add(group.model ?? UNKNOWN_MODEL);
      }
    }
  }
  const cost = priced > 0 || unpriced === 0 ? priced : null;
  return { rows: 1, requests: total.requests, tokens, cost, complete: unpriced === 0 && total.unknown_rows === 0,
           unknown_rows: total.unknown_rows, unpriced_tokens: unpriced, unpriced_models: [...models].sort(),
           harness_cost: null, tiers };
}

/** A total's token count of `key`, or null when the index cannot say it. */
function known(total: TermsTotal, key: "input" | "fresh_input" | "cache_read" | "cache_write" | "cache_write_1h" | "output"): number | null {
  return total.unknown.includes(key) || !Number.isFinite(total[key]) ? null : total[key];
}

/**
 * Token kinds from `/api/terms`: the four classes by cost, dearest first, each with its
 * tokens and cost, a class the index cannot count left out; the closed row's share of
 * the input read from cache and the two dearest classes; the saving's sentence. Null
 * until the answer has come, and with no row.
 */
export function kindsSection(report: Terms | null): Kinds | null {
  if (report === null || report.rows.length === 0) return null;
  const total = report.total;
  const found = KINDS.flatMap((kind) => {
    const tokens = known(total, kind.tokens);
    if (tokens === null) return [];
    const sum = kindSum(report, kind.terms, tokens);
    const notes: string[] = [];
    const hour = kind.key === "cache_write" ? known(total, "cache_write_1h") : null;
    if (hour !== null && hour > 0) notes.push(`${fmtTokens(hour)} of them for 1 hour`);
    for (const [tier, cost] of sum.tiers) notes.push(`${fmtCost(cost)} at ${tier} prices`);
    return [{ sum, row: { key: kind.key, name: kind.name, note: notes.length ? notes.join(" · ") : null,
                          tokens: `${fmtTokens(tokens)} tokens`, cost: costWords(sum), why: whyNotWhole(sum) } }];
  });
  found.sort((one, other) => {
    const a = one.sum.cost;
    const b = other.sum.cost;
    if ((a === null) !== (b === null)) return a === null ? 1 : -1;
    return (b ?? 0) - (a ?? 0) || other.sum.tokens - one.sum.tokens;
  });
  const input = known(total, "input");
  const reads = known(total, "cache_read");
  const lead = input === null || reads === null || input <= 0 ? null
    : `${percentWords((reads / input) * 100)} of input read from cache`;
  const dear = found.filter((kind) => kind.sum.cost !== null && kind.sum.cost > 0).slice(0, 2)
    .map((kind) => `${kind.row.name.toLowerCase()} ${kind.row.cost}`);
  return { label: KINDS_LABEL, lead, dearest: dear.length ? dear.join(", ") : null,
           count: `${fmtTokens(total.tokens)} tokens`, rows: found.map((kind) => kind.row),
           saving: savingWords(total.saving) };
}

/**
 * What caching saved, in one sentence that says what it stands for: the same tokens with
 * no cache, at the same table's prices. The cache writes' clause is absent when they cost
 * the same as fresh input (none were written, or the provider bills them as input). At
 * zero or less it says what caching cost over what it saved. Null with no saving.
 */
export function savingWords(saving: Saving | null): string | null {
  if (saving === null) return null;
  const reads = `${fmtTokens(saving.read_tokens)} of ${fmtTokens(saving.input_tokens)} input tokens were read from cache.`;
  const would = `With no cache, at the same prices, those reads would have cost ${fmtCost(saving.reads_at_input)} as fresh input`;
  const writes = saving.write_extra > 0 ? `, and cache writes cost ${fmtCost(saving.write_extra)} more than fresh input`
    : saving.write_extra < 0 ? `, and cache writes cost ${fmtCost(-saving.write_extra)} less than fresh input` : "";
  const end = saving.saved > 0 ? `so caching saved about ${fmtCost(saving.saved)}`
    : `so caching cost about ${fmtCost(-saving.saved)} more than it saved`;
  return `${reads} ${would}; they cost ${fmtCost(saving.reads_cost)}${writes}, ${end}. An estimate.`;
}

// --- Prices ---------------------------------------------------------------------------------

/** The words under the opened price table, as drawn. */
export const PRICES_PARAGRAPH = "Every figure on this page is tokens from your transcripts times these prices. It is an "
  + "estimate, not what you were billed, and on a plan it is what the same work would cost at API prices. A model "
  + "with no price shows as \"no price\", never as $0.";
export const PRICES_NOTE = "a local price table, not billing data";
/** The link that opens Prices when the table states no date. */
export const OPEN = "Open";
/** The acts under the price table, each opening in place under it. */
export const FIND = "Find a model";
export const BILL = "Check against a bill";
export const DETAILS = "Details";
export type PricesPart = "find" | "bill" | "details";
export const PRICE_HEADS = ["$ per million tokens", "input", "output", "cache write", "cache read"] as const;
/** Where a price table has no price. */
export const NONE = "none";

/** One model's line of the price table. */
export interface PriceRow {
  key: string;
  /** The model's id; with its provider after it where the table lists the id twice. */
  name: string;
  cells: [string, string, string, string];
}

export interface PricesModel {
  label: string;
  /** `9 models, all priced`, `8 of 9 models priced`. */
  closed: string;
  note: string;
  /** `updated Nov 2`: the link that opens the section. */
  count: string;
  /** The rates of the range's models; null until the table has been read. */
  rows: PriceRow[] | null;
  /** `No price for m-x, m-y.`; null when every model is priced. */
  unpriced: string | null;
  paragraph: string;
}

/** A price per million tokens: two decimals, more where the price has them; `none`
 * where the table has no price. */
export function priceWords(price: number | null): string {
  if (price === null || !Number.isFinite(price)) return NONE;
  const exact = String(Number(price.toPrecision(6)));
  const decimals = exact.includes(".") ? exact.split(".")[1].length : 0;
  return price.toFixed(Math.max(2, Math.min(decimals, 6)));
}

/** The one cache write column: the 5-minute price, `5m / 1h` when the two differ,
 * `none` where the table has neither. */
export function writeWords(prices: Pick<PriceSet, "cache_write_5m" | "cache_write_1h">): string {
  const five = prices.cache_write_5m;
  const hour = prices.cache_write_1h;
  if (hour === null || hour === five) return priceWords(five);
  return `${priceWords(five)} / ${priceWords(hour)}`;
}

/** The price table's lines, one per rate in the order given. */
export function priceRows(rates: Rate[]): PriceRow[] {
  const ids = rates.map((rate) => rate.model ?? UNKNOWN_MODEL);
  return rates.map((rate, i) => {
    const id = ids[i];
    const twice = ids.filter((other) => other === id).length > 1;
    return {
      key: `${id}|${rate.asked_provider ?? ""}`,
      name: twice ? `${id} (${rate.asked_provider ?? "no provider"})` : id,
      cells: [priceWords(rate.input), priceWords(rate.output), writeWords(rate), priceWords(rate.cache_read)],
    };
  });
}

/** How many of the range's models the table prices: `9 models, all priced`,
 * `8 of 9 models priced`, `1 model, not priced`. A model is priced when nothing of it
 * went without a price. */
export function pricedWords(rows: Pick<TermsRow, "cost" | "unpriced_models">[]): string {
  const all = rows.length;
  const priced = rows.filter((row) => row.cost !== null && row.unpriced_models.length === 0).length;
  if (all === 1) return priced ? "1 model, priced" : "1 model, not priced";
  if (priced === all) return `${countWords(all, "model")}, all priced`;
  if (priced === 0) return `${countWords(all, "model")}, none priced`;
  return `${priced.toLocaleString("en-US")} of ${countWords(all, "model")} priced`;
}

/** The table's date as the closed row's link says it: `updated Nov 2`, with the year
 * when it is not `today`'s. */
export function updatedWords(date: string | null, today: string): string {
  if (date === null || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return OPEN;
  const year = date.slice(0, 4);
  return `updated ${dateWord(date)}${year === today.slice(0, 4) ? "" : `, ${year}`}`;
}

/** The read of the price table that Prices makes: the rates of the models the answer's
 * price groups priced, each under its requests' own provider; the table alone when none. */
export function pricesFor(report: Terms): string {
  const { asks } = rateAsks(report.rows);
  return asks.length ? pricesPath(asks) : "api/prices";
}

/** How many of the answer's models the price table leaves unlisted, past the most one read takes. */
export function pricesLeft(report: Terms): number {
  return rateAsks(report.rows).more;
}

/**
 * Prices from `/api/terms` and `/api/prices`: the closed row's count of priced models
 * and the table's date, the opened table of the range's models. Null until the terms
 * answer has come, and with no row.
 */
export function pricesSection(report: Terms | null, prices: Prices | null, today: string): PricesModel | null {
  if (report === null || report.rows.length === 0) return null;
  const unpriced = report.unpriced_models;
  return {
    label: PRICES_LABEL, closed: pricedWords(report.rows), note: PRICES_NOTE,
    count: updatedWords(prices?.effective_date ?? null, today),
    rows: prices === null ? null : priceRows(prices.models),
    unpriced: unpriced.length ? `No price for ${unpriced.join(", ")}.` : null,
    paragraph: PRICES_PARAGRAPH,
  };
}
