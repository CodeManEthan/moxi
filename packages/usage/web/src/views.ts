// What usage's page and blocks share, worked out with no DOM so the Node tests can read
// it: the formatters, the price reads' paths, why a row's figures are not whole, the cost
// terms (the bill's lines and the rates), a request's readout and the notes under its
// chart, the limits meters and the cost block. Every string here goes into the page as
// text.

import type {
  DayRow, Group, Limits, Override, Prices, Provenance, Rate, Report, RequestRow, RequestsTotal, Sums, Term,
  TermsRow, Window,
} from "./feed.ts";

/** The sum of what `pick` reads off each item. */
function sum<T>(items: T[], pick: (item: T) => number): number {
  return items.reduce((total, item) => total + pick(item), 0);
}

// --- formatters ---------------------------------------------------------------------------

const DASH = "—";

/** A token count: `950`, `12.3k`, `4.56M`, `1.20B`. */
export function fmtTokens(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return DASH;
  const size = Math.abs(n);
  if (size < 1000) return String(Math.round(n));
  const [div, unit] = size < 1e6 ? [1e3, "k"] : size < 1e9 ? [1e6, "M"] : [1e9, "B"];
  const value = n / div;
  const digits = Math.abs(value) < 10 ? 2 : Math.abs(value) < 100 ? 1 : 0;
  return `${value.toFixed(digits)}${unit}`;
}

/** Dollars: `$12.34`, `<$0.01`, `$1,234`; a dash when nothing could be priced. */
export function fmtCost(cost: number | null | undefined): string {
  if (cost === null || cost === undefined || !Number.isFinite(cost)) return DASH;
  if (cost === 0) return "$0.00";
  if (cost > 0 && cost < 0.01) return "<$0.01";
  if (cost >= 1000) return `$${Math.round(cost).toLocaleString("en-US")}`;
  return `$${cost.toFixed(2)}`;
}

/** A percent: `42%`, `4.5%` below 10. */
export function fmtPercent(p: number | null | undefined): string {
  if (p === null || p === undefined || !Number.isFinite(p)) return DASH;
  return p < 10 && p !== 0 ? `${p.toFixed(1)}%` : `${Math.round(p)}%`;
}

/** A length of time: `45s`, `12m`, `3h 05m`, `2d 4h`. */
export function fmtSpan(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${String(m % 60).padStart(2, "0")}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

/** How long ago `at` was, at `now` (both epoch seconds). */
export function fmtAgo(at: number | null | undefined, now: number): string {
  if (at === null || at === undefined) return "never";
  if (now - at < 5) return "just now";
  return `${fmtSpan(now - at)} ago`;
}

/** An ISO 8601 time as epoch seconds, or null. */
export function isoSeconds(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms / 1000 : null;
}

/** A moment in this browser's zone, `YYYY-MM-DD HH:MM`. */
export function fmtMoment(at: number | null | undefined): string {
  if (at === null || at === undefined) return DASH;
  const d = new Date(at * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// --- days and paths -----------------------------------------------------------------------

/** `day` (`YYYY-MM-DD`) moved by `n` days. */
export function shiftDay(day: string, n: number): string {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

function withQuery(path: string, keys: [string, string][]): string {
  const query = new URLSearchParams(keys).toString();
  return query ? `${path}?${query}` : path;
}

/** The rates of some models, each looked up under the provider its requests gave. */
export function pricesPath(asks: { model: string; provider: string | null }[]): string {
  return withQuery("api/prices", [["models", asks.map((ask) => ask.model).join(",")],
                                  ["providers", asks.map((ask) => ask.provider ?? "").join(",")]]);
}

/** A search of the whole model table. */
export function searchPath(q: string): string {
  return withQuery("api/prices", [["q", q]]);
}

// --- completeness -------------------------------------------------------------------------

/** Why a row's figures are not whole, one line each; empty for a complete row. */
export function flags(row: Sums & { unpriced_models?: string[] }): string[] {
  const out: string[] = [];
  if (row.unknown_rows > 0) {
    out.push(`${row.unknown_rows} request${row.unknown_rows === 1 ? "" : "s"} with a token count the harness didn't report, so the sums are at least what they show`);
  }
  if (row.cost === null) {
    out.push("nothing here could be priced");
  } else if (row.unpriced_tokens > 0) {
    const models = row.unpriced_models?.length ? ` (${row.unpriced_models.join(", ")})` : "";
    out.push(`${fmtTokens(row.unpriced_tokens)} tokens have no price${models}, so the cost is the priced part`);
  }
  if (out.length === 0 && !row.complete) out.push("some of these figures are unknown");
  return out;
}

export const UNKNOWN_MODEL = "unknown model";

// --- models ---------------------------------------------------------------------------------

/** One price source, in a line. */
export function provenanceLine(p: Provenance): string {
  const parts = [p.rule];
  if (p.layer) parts.push(`layer ${p.layer}`);
  if (p.row) parts.push(`row ${p.row}`);
  if (p.variant) parts.push(`variant ${p.variant}`);
  if (p.snapshot) parts.push(`snapshot ${p.snapshot}`);
  if (p.period && (p.period.from || p.period.to)) parts.push(`from ${p.period.from ?? "the start"} to ${p.period.to ?? "now"}`);
  if (p.overridden.length) parts.push(`overrides ${p.overridden.join(", ")}`);
  if (p.filled.length) parts.push(`filled ${p.filled.join(", ")}`);
  if (p.note) parts.push(p.note);
  return parts.join(" · ");
}

// --- the cost terms ---------------------------------------------------------------------------

/** The five token classes a cost's terms price, in order, with their labels. A request
 * row names the first `fresh_input`. */
export const TERM_CLASSES = [
  ["input", "fresh input"], ["cache_read", "cache read"], ["cache_write_5m", "cache write 5m"],
  ["cache_write_1h", "cache write 1h"], ["output", "output"],
] as const;

/** Dollars per million tokens, as short as the price allows: `$3`, `$0.3`, `$3.75`. */
export function fmtRate(price: number | null | undefined): string {
  if (price === null || price === undefined || !Number.isFinite(price)) return DASH;
  return `$${Number(price.toPrecision(6))}`;
}

/** One cell of a Reconcile line: the term's dollars, and `tokens x price` in its title. */
export interface TermCell { cls: string; text: string; title: string }

/** One line of the Reconcile table. A `group` line is one price group; a `subtotal` sums a
 * model's groups when it has two or more; the `total` sums every line. */
export interface ReconcileLine {
  kind: "group" | "subtotal" | "total";
  model: string;
  pricedBy: string;
  cells: TermCell[];
  cost: number | null;
  flags: string[];
}

/** A term's cell: `1,234,567 x $8/M = $9.88`. */
export function termCell(term: Term): TermCell {
  const tokens = term.tokens.toLocaleString("en-US");
  const label = TERM_CLASSES.find(([name]) => name === term.class)?.[1] ?? term.class;
  const priced = term.price === null ? "no price" : `${fmtRate(term.price)}/M`;
  const dollars = term.cost === null ? "not priced" : fmtCost(term.cost);
  const from = term.price_from === "tier-input" ? " (from the tier's input price)" : "";
  return { cls: term.class, text: term.cost === null ? DASH : fmtCost(term.cost),
           title: `${label}: ${tokens} x ${priced} = ${dollars}${from}` };
}

function summedCells(groups: Group[], what: string): TermCell[] {
  return TERM_CLASSES.map(([cls, label]) => {
    const terms = groups.map((group) => group.terms.find((term) => term.class === cls))
      .filter((term): term is Term => term !== undefined);
    const known = terms.filter((term) => term.cost !== null);
    const cost = known.length ? sum(known, (term) => term.cost as number) : null;
    const tokens = sum(terms, (term) => term.tokens);
    return { cls, text: cost === null ? DASH : fmtCost(cost),
             title: `${label}: ${tokens.toLocaleString("en-US")} tokens over ${what} = ${cost === null ? "not priced" : fmtCost(cost)}` };
  });
}

/** The price tiers the model table names, in words for a person. */
const TIER_WORDS: Record<string, string> = {
  above_32k: "over-32k-token", above_100k: "over-100k-token", above_128k: "over-128k-token",
  above_200k: "over-200k-token", above_256k: "over-256k-token", above_272k: "over-272k-token",
  above_512k: "over-512k-token", above_200k_priority: "over-200k-token priority",
  above_272k_priority: "over-272k-token priority", above_272k_flex: "over-272k-token flex",
  priority: "priority", flex: "flex", fast: "fast",
};

/** A price tier in words: `the over-100k-token price`; a tier the map does not know is
 * its id with spaces for underscores. */
export function tierPrice(tier: string): string {
  return `the ${TIER_WORDS[tier] ?? tier.replaceAll("_", " ")} price`;
}

/** How a group was priced: where its prices came from, its tier and its provider. */
export function pricedBy(group: Group): string {
  const parts = [provenanceLine(group.provenance)];
  if (group.tier) parts.push(tierPrice(group.tier));
  if (group.above) parts.push(`above ${fmtTokens(group.above)}`);
  if (group.provider) parts.push(group.provider);
  return parts.join(" · ");
}

function groupFlags(group: Group): string[] {
  const out: string[] = [];
  if (group.cost === null) out.push(group.why ? `not priced: ${group.why}` : "not priced");
  if (group.unknown_rows > 0) out.push(`${group.unknown_rows} request${group.unknown_rows === 1 ? "" : "s"} with a token count the harness didn't report`);
  if (group.rule) out.push(group.rule);
  return out;
}

/** The Reconcile table's lines: each model's groups, a subtotal under a model of two or
 * more, then the total. The terms on each line add up to its cost. A report by agent
 * names each line by its agent and its group's model, and a subtotal by its agent. */
export function reconcileLines(report: Report<TermsRow>): ReconcileLine[] {
  const lines: ReconcileLine[] = [];
  for (const row of report.rows) {
    const byAgent = row.model === null && row.agent !== null;
    const model = byAgent ? row.agent as string : row.model ?? UNKNOWN_MODEL;
    for (const group of row.groups) {
      const name = byAgent ? `${model} · ${group.model ?? UNKNOWN_MODEL}` : model;
      lines.push({ kind: "group", model: name, pricedBy: pricedBy(group), cells: group.terms.map(termCell),
                   cost: group.cost, flags: groupFlags(group) });
    }
    if (row.groups.length > 1) {
      lines.push({ kind: "subtotal", model: `${model} total`, pricedBy: `${row.groups.length} price groups`,
                   cells: summedCells(row.groups, `${row.groups.length} groups`), cost: row.cost, flags: flags(row) });
    }
  }
  const all = report.rows.flatMap((row) => row.groups);
  lines.push({ kind: "total", model: "Total", pricedBy: "", cells: summedCells(all, "every group"),
               cost: report.total.cost, flags: flags({ ...report.total, unpriced_models: report.unpriced_models }) });
  return lines;
}

// --- the rates ---------------------------------------------------------------------------------

/** The most models the Rates section asks for at once (the route's own cap). */
export const RATES_MAX = 100;

/** The models and providers the Reconcile lines priced, for the Rates section: each
 * looked up under its requests' own provider, so the rates are the ones those groups'
 * lookup gives. */
export function rateAsks(rows: TermsRow[]): { asks: { model: string; provider: string | null }[]; more: number } {
  const seen = new Map<string, { model: string; provider: string | null }>();
  for (const group of rows.flatMap((row) => row.groups)) {
    if (group.model === null) continue;
    const key = `${group.model}\u0000${group.provider ?? ""}`;
    if (!seen.has(key)) seen.set(key, { model: group.model, provider: group.provider });
  }
  const asks = [...seen.values()];
  return { asks: asks.slice(0, RATES_MAX), more: Math.max(0, asks.length - RATES_MAX) };
}

/** A price cell of the Rates section, marked where a provider default or your config gave it. */
export interface RateCell { text: string; mark: string | null }

export const RATE_FIELDS = [
  ["input", "Input"], ["output", "Output"], ["cache_read", "Cache read"], ["cache_write_5m", "Write 5m"],
  ["cache_write_1h", "Write 1h"],
] as const;

export function rateCell(rate: Rate, field: (typeof RATE_FIELDS)[number][0]): RateCell {
  const value = rate[field];
  const mark = value === null ? null : rate.provenance.filled.includes(field) ? "provider default"
    : rate.provenance.overridden.includes(field) ? "your config" : null;
  return { text: fmtRate(value), mark };
}

/** A tier's conditions in words: `speed fast`, `service tier priority`, `above 200k`. */
export function conditionsText(c: { above_tokens: number | null; service_tier: string | null; speed: string | null }): string {
  const parts: string[] = [];
  if (c.speed) parts.push(`speed ${c.speed}`);
  if (c.service_tier) parts.push(`service tier ${c.service_tier}`);
  if (c.above_tokens) parts.push(`above ${fmtTokens(c.above_tokens)}`);
  return parts.join(", ");
}

/** A tier as the Tiers cell gives it: `fast (speed fast): $30 / $150, cache from tier input`. */
export function tierText(tier: Rate["tiers"][number]): string {
  const base = `${tier.name} (${conditionsText(tier.conditions)}): ${fmtRate(tier.prices.input)} / ${fmtRate(tier.prices.output)}`;
  return tier.from_tier_input.length ? `${base}, cache from tier input` : base;
}

/** One line of the Rates section. */
export interface RateLine {
  model: string;
  provider: string | null;
  known: boolean;
  cells: RateCell[];
  window: string;
  tiers: string[];
  period: string;
  from: string;
}

export function rateLine(rate: Rate): RateLine {
  const p = rate.provenance;
  const from = [p.layer ? `${p.layer} layer` : null, p.rule, p.snapshot ? `snapshot ${p.snapshot}` : null,
                p.row && p.row !== rate.model ? `row ${p.row}` : null].filter((part): part is string => !!part);
  return {
    model: rate.model ?? UNKNOWN_MODEL,
    provider: rate.asked_provider,
    known: p.step !== 7,
    cells: RATE_FIELDS.map(([field]) => rateCell(rate, field)),
    window: rate.window === null ? DASH : rate.window.toLocaleString("en-US"),
    tiers: rate.tiers.map(tierText),
    period: p.period && (p.period.from || p.period.to) ? `${p.period.from ?? "the start"} to ${p.period.to ?? "now"}` : "",
    from: from.join(" · "),
  };
}

/** A `[[model]]` row of your config, in a line. */
export function overrideLine(row: Override): string {
  const prices = RATE_FIELDS.filter(([field]) => row.prices[field] !== null)
    .map(([field, label]) => `${label.toLowerCase()} ${fmtRate(row.prices[field])}`);
  const parts = [`${row.id}${row.match === "exact" ? "" : ` (${row.match})`}`, ...prices];
  if (row.window !== null) parts.push(`window ${row.window.toLocaleString("en-US")}`);
  if (row.provider) parts.push(`provider ${row.provider}`);
  for (const tier of row.tiers) parts.push(`tier ${tier.name} (${conditionsText(tier.conditions)})`);
  if (row.from || row.to) parts.push(`from ${row.from ?? "the start"} to ${row.to ?? "now"}`);
  if (row.note) parts.push(row.note);
  return parts.join(" · ");
}

/** The model table's line: `model table in effect: 3,328 models, snapshot of 2026-10-30`. */
export function layerLine(prices: Prices): string {
  return `model table in effect: ${prices.rows.toLocaleString("en-US")} models, snapshot of ${prices.effective_date ?? "an unknown date"}`;
}

// --- a request ---------------------------------------------------------------------------------

/** A request row's five classes, in the order of `TERM_CLASSES`. */
export const REQUEST_CLASSES = [
  ["fresh_input", "fresh input"], ["cache_read", "cache read"], ["cache_write_5m", "cache write 5m"],
  ["cache_write_1h", "cache write 1h"], ["output", "output"],
] as const;

function round(n: number): number {
  return Math.round(n * 100) / 100;
}

/** The column a request's `unknown` names for each class it doesn't know. */
const UNKNOWN_COLUMN: Record<(typeof REQUEST_CLASSES)[number][0], string> = {
  fresh_input: "input", cache_read: "cache_read", cache_write_5m: "cache_write",
  cache_write_1h: "cache_write_1h", output: "output",
};

/** The readout line for one request. */
export function readout(row: RequestRow): string {
  const parts = [`#${row.n}`, fmtMoment(row.ts)];
  if (row.turn !== null) parts.push(`turn ${row.turn}`);
  parts.push(row.model ?? UNKNOWN_MODEL);
  if (row.synthetic) parts.push("synthetic");
  parts.push(`context ${fmtTokens(row.context)}`);
  if (row.context !== null && row.window) parts.push(`${fmtPercent((row.context / row.window) * 100)} of ${row.window.toLocaleString("en-US")}`);
  parts.push(REQUEST_CLASSES.map(([key, label]) => `${label} ${row.unknown.includes(UNKNOWN_COLUMN[key]) ? "?" : fmtTokens(row[key])}`).join(", "));
  parts.push(fmtCost(row.cost));
  return parts.join(" · ");
}

/** The notes under the charts: what the report can't show. */
export function requestNotes(rows: RequestRow[], total: RequestsTotal): string[] {
  const out: string[] = [];
  const n = total.claimed_elsewhere;
  if (n) out.push(`${n} request${n === 1 ? "" : "s"} of this agent ${n === 1 ? "is" : "are"} counted under another file`);
  const c = total.copied_marks;
  if (c) out.push(`${c} compaction${c === 1 ? "" : "s"} copied from another file ${c === 1 ? "isn't" : "aren't"} drawn`);
  if (!total.marks_known) out.push("compactions aren't read yet for a file of this agent; the next pass that can read it brings them in");
  if (rows.length && total.turns === null) out.push("this harness wrote no turns here");
  return out;
}

// --- the limits meters ----------------------------------------------------------------------

export type Tone = "ok" | "warn" | "error" | "muted";

/** A window's meter as it is drawn. */
export interface Meter {
  key: string;
  title: string;
  state: string;
  /** The bar's fill, 0 to 1. */
  fill: number;
  percent: string;
  tone: Tone;
  /** When it resets: `resets in 2h 05m (2026-11-02 18:00)`, or null. */
  reset: string | null;
  /** How old the reading is, or null. */
  age: string | null;
  /** When it fills, or why that isn't known. */
  full: string | null;
  /** The state's reason, and any note. */
  reason: string | null;
}

/** Where a meter turns to warn and to error, in percent. */
export const WARN_AT = 75;
export const ERROR_AT = 90;

export function meter(row: Window, now: number): Meter {
  const used = row.used_percent;
  const fresh = row.state === "fresh" && used !== null;
  const tone: Tone = !fresh ? "muted" : used >= ERROR_AT ? "error" : used >= WARN_AT ? "warn" : "ok";
  const resets = isoSeconds(row.resets_at);
  const observed = isoSeconds(row.observed_at);
  const full = isoSeconds(row.full_at);
  const reason = [row.reason, row.note].filter((line): line is string => !!line).join("; ");
  return {
    key: `${row.harness}/${row.limit_id}/${row.window_minutes ?? ""}`,
    title: `${row.harness} ${row.label}`,
    state: row.state,
    fill: used === null ? 0 : Math.min(1, Math.max(0, used / 100)),
    percent: fmtPercent(used),
    tone,
    reset: resets === null ? null
      : resets > now ? `resets in ${fmtSpan(resets - now)} (${fmtMoment(resets)})` : `reset ${fmtAgo(resets, now)}`,
    age: observed === null ? null : `read ${fmtAgo(observed, now)}`,
    full: full !== null ? `full in ${fmtSpan(full - now)} (${fmtMoment(full)})`
      : fresh && row.projection ? `no fill time: ${row.projection}` : null,
    reason: reason || null,
  };
}

/** The meters, and the lines that apply to all of them. */
export function meters(limits: Limits | null, now: number): { meters: Meter[]; notes: string[] } {
  if (limits === null) return { meters: [], notes: [] };
  return { meters: limits.rows.map((row) => meter(row, now)), notes: limits.notes };
}

// --- the cost block -------------------------------------------------------------------------

export interface CostDay {
  day: string;
  cost: number | null;
  tokens: number;
  complete: boolean;
}

/** A plain sum, so the cost block's bundle carries no d3. */
const add = (values: number[]): number => values.reduce((a, b) => a + b, 0);

/** Today and the 6 days before it, oldest first, the rows of `/api/cost` summed by day. */
export function costDays(report: Report<DayRow>): { today: CostDay; week: CostDay; days: CostDay[] } | null {
  const today = report.today;
  if (!today) return null;
  const days = Array.from({ length: 7 }, (_, i) => shiftDay(today, i - 6)).map((day) => {
    const mine = report.rows.filter((row) => row.period === day);
    const priced = mine.filter((row) => row.cost !== null);
    return {
      day,
      cost: priced.length ? add(priced.map((row) => row.cost as number)) : mine.length ? null : 0,
      tokens: add(mine.map((row) => row.tokens)),
      complete: mine.every((row) => row.complete),
    };
  });
  const priced = days.filter((day) => day.cost !== null);
  const week = {
    day: `${days[0].day} to ${today}`,
    cost: priced.length ? add(priced.map((day) => day.cost as number)) : null,
    tokens: add(days.map((day) => day.tokens)),
    complete: days.every((day) => day.complete),
  };
  return { today: days[6], week, days };
}

/** How old the last pass may be before the cost block calls its figures stale, in s. */
export const STALE_AFTER = 600;

/** How old the cost block's figures are: the index's last pass at `now`, flagged once it
 * is over `STALE_AFTER` old, since only the usage page and the CLI read new sessions in. */
export function costAge(lastPass: number | null, now: number): { text: string; stale: boolean } {
  if (lastPass === null) {
    return { text: "The index has never been read, so these leave out every session; opening the usage page reads them in.", stale: true };
  }
  const age = `as of ${fmtAgo(lastPass, now)}`;
  if (now - lastPass <= STALE_AFTER) return { text: age, stale: false };
  return { text: `${age}, so these may leave out what came since; opening the usage page reads it in.`, stale: true };
}
