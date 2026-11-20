// The chart under the filter line: one bar a day (or a month), the sentence above it and
// the table that stands in its place, worked out from a daily report with no DOM, and
// laid out at a width. By cost each bar is stacked by token class from the cost terms of
// the same days; by tokens there are two charts, the cache reads alone and the billable
// work stacked. Every word, figure and place is decided here; the view draws what it is
// given. Nothing was used in the range: there is no chart, and the page says so.

import { costWords, countWords, rangeBy, rangeDays, rangeLabel, sumRows, whyNotWhole } from "./cost-model.ts";
import type { Range, Sum } from "./cost-model.ts";
import { dateWord, dayWord, daysOf, monthWord } from "./days.ts";
import type { DayRow, Report, TermsRow } from "./feed.ts";
import { fmtCost, fmtPercent, fmtTokens } from "./views.ts";

export type Measure = "cost" | "tokens";

/** What stands in the chart's place when the range holds no request. */
export const NOTHING_USED = "Nothing was used in this range.";

/** One period of the chart. A period in which nothing was used has no bar. */
export interface ChartBar {
  /** `YYYY-MM-DD`, or `YYYY-MM` in a chart by month. */
  period: string;
  /** The axis label: `Oct 10`, `today`, `Oct 2026`. */
  label: string;
  /** Nothing was used in this period: no bar and no hover. */
  empty: boolean;
  value: number;
  /** The bar's height as a share of the axis's top, 0 to 1. */
  share: number;
  /** The value in words: `$19.54`, `at least $19.54`, `no price`, `22.0M`. */
  words: string;
  /** Today's bar (this month's, by month): drawn in the text colour, its `words` beside it. */
  now: boolean;
  /** False draws the dashed outline. */
  whole: boolean;
  /** `Nov 3: $5.35`, with `, not whole` after it, then a line for each of `segments`;
   * null for an empty period. */
  hover: string | null;
  /** The bar's parts, bottom first, each one of the chart's `series`; empty for a bar
   * drawn whole. */
  segments: Segment[];
}

/** One part of a stacked bar. */
export interface Segment {
  key: string;
  value: number;
}

/** One kind a chart's bars are stacked by, with the shade it is drawn in: 1 the
 * darkest to 4 the lightest. */
export interface Series {
  key: string;
  label: string;
  shade: 1 | 2 | 3 | 4;
}

export interface ChartModel {
  measure: Measure;
  by: "day" | "month";
  /** The chart's name for a reader that cannot see it. */
  aria: string;
  /** The grid line at the top; null when every bar is zero. */
  top: { value: number; words: string } | null;
  /** Every period of the range, oldest first. */
  bars: ChartBar[];
  /** The line above the chart; null where there is nothing to compare. */
  sentence: string | null;
  /** The chart's own name and the line under it, where the group holds more than one
   * chart: `Cache reads`. */
  name: string | null;
  caption: string | null;
  /** What the bars are stacked by, bottom first, for the legend; empty when they are not. */
  series: Series[];
}

const UNKNOWN_MODEL = "unknown model";

/** The token classes a bar by cost is stacked by, bottom first, from the terms' classes
 * (the two cache writes are one part). */
export const COST_SERIES: Series[] = [
  { key: "input", label: "Fresh input", shade: 4 }, { key: "cache_write", label: "Cache write", shade: 2 },
  { key: "cache_read", label: "Cache read", shade: 3 }, { key: "output", label: "Output", shade: 1 },
];
/** The billable work's classes, bottom first: every token that is not a cache read. */
export const BILLABLE_SERIES: Series[] = [
  { key: "fresh_input", label: "Fresh input", shade: 4 }, { key: "cache_write", label: "Cache write", shade: 2 },
  { key: "output", label: "Output", shade: 1 },
];
export const CACHE_READS = "Cache reads";
export const BILLABLE_WORK = "Billable work";
const TOKEN_CLASSES: readonly [keyof DayRow & string, string][] = [
  ["fresh_input", "Fresh input"], ["cache_read", "Cache read"], ["cache_write", "Cache write"],
  ["output", "Output"],
];

/** The smallest of 1, 2, 2.5, 5 and 10 times a power of ten that is not under `value`. */
export function niceTop(value: number): number {
  const power = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].find((mark) => mark * power >= value * (1 - 1e-9)) as number;
  return Number((step * power).toPrecision(3));
}

/** An axis value: `$20`, `$2.50`, `25M`, `2.5k`. */
export function axisWords(value: number, measure: Measure): string {
  if (measure === "cost") return Number.isInteger(value) ? `$${value.toLocaleString("en-US")}` : fmtCost(value);
  const [div, unit] = value < 1e3 ? [1, ""] : value < 1e6 ? [1e3, "k"] : value < 1e9 ? [1e6, "M"] : [1e9, "B"];
  return `${Number((value / div).toPrecision(3))}${unit}`;
}

/** A month after `month` (`YYYY-MM`). */
function nextMonth(month: string): string {
  const [year, at] = month.split("-").map(Number);
  return at === 12 ? `${year + 1}-01` : `${year}-${String(at + 1).padStart(2, "0")}`;
}

/** The periods a chart has a place for: every day of the range, or every month from
 * its first to its last (for all time, from the first month used to the last). */
function periods(rows: DayRow[], range: Range, today: string, by: "day" | "month"): string[] {
  const days = rangeDays(range, today);
  if (by === "day" && days !== null) return daysOf(days.since, days.until);
  const used = rows.map((row) => row.period).filter((period): period is string => period !== null).sort();
  const first = days === null ? used[0] : days.since.slice(0, 7);
  const last = days === null ? used[used.length - 1] : days.until.slice(0, 7);
  const out: string[] = [];
  for (let month = first; month !== undefined && month <= last && out.length < 1200; month = nextMonth(month)) {
    out.push(month);
  }
  return out;
}

const tokenWords = (sum: Sum): string => `${sum.unknown_rows > 0 ? "at least " : ""}${fmtTokens(sum.tokens)}`;

/** What a chart's bars measure: a period's value and its words from its rows and their
 * sum, whether it is whole, and its parts. */
interface Gauge {
  value: (sum: Sum, rows: DayRow[]) => number;
  words: (sum: Sum, rows: DayRow[]) => string;
  whole: (sum: Sum) => boolean;
  parts: (period: string, rows: DayRow[]) => Segment[];
  /** A part's value in words, for the hover. */
  partWords: (value: number) => string;
}

const noParts = (): Segment[] => [];
const COST_GAUGE: Gauge = {
  value: (sum) => sum.cost ?? 0, words: (sum) => costWords(sum), whole: (sum) => sum.complete, parts: noParts,
  partWords: fmtCost,
};
const TOKENS_GAUGE: Gauge = {
  value: (sum) => sum.tokens, words: (sum) => tokenWords(sum), whole: (sum) => sum.unknown_rows === 0, parts: noParts,
  partWords: fmtTokens,
};

/** A gauge of tokens summed over some columns of the rows, stacked by them when there
 * is more than one. */
function columnsGauge(series: readonly Series[]): Gauge {
  const count = (rows: DayRow[], key: string): number =>
    rows.reduce((total, row) => total + (Number(row[key as keyof DayRow]) || 0), 0);
  const value = (_sum: Sum, rows: DayRow[]): number => series.reduce((total, item) => total + count(rows, item.key), 0);
  return {
    value, whole: (sum) => sum.unknown_rows === 0, partWords: fmtTokens,
    words: (sum, rows) => `${sum.unknown_rows > 0 ? "at least " : ""}${fmtTokens(value(sum, rows))}`,
    parts: series.length < 2 ? noParts : (_period, rows) => series.map((item) => ({ key: item.key, value: count(rows, item.key) })),
  };
}

/** Each period's cost by token class from a terms report by day or by month: dollars
 * summed over its priced groups, the two cache writes as one. */
export function classCosts(terms: Report<TermsRow> | null): Map<string, Segment[]> {
  const out = new Map<string, Segment[]>();
  for (const row of terms?.rows ?? []) {
    if (row.period === null) continue;
    const of = (key: string): number => row.split
      .filter((item) => (key === "cache_write" ? item.class.startsWith("cache_write") : item.class === key))
      .reduce((total, item) => total + item.cost, 0);
    out.set(row.period, COST_SERIES.map((item) => ({ key: item.key, value: of(item.key) })));
  }
  return out;
}

/** The bars of a gauge over the periods of the range. */
function measured(dated: DayRow[], range: Range, today: string, by: "day" | "month") {
  return periods(dated, range, today, by).map((period) => {
    const rows = dated.filter((row) => row.period === period);
    return { period, rows, sum: sumRows(rows) };
  });
}

/** The chart of one gauge: its bars, the axis's top, and no sentence. */
function gaugeChart(dated: DayRow[], range: Range, today: string, measure: Measure, gauge: Gauge,
                    series: Series[], aria: string): ChartModel {
  const by = rangeBy(range, today);
  const current = by === "day" ? today : today.slice(0, 7);
  const sums = measured(dated, range, today, by);
  const most = Math.max(0, ...sums.map((item) => gauge.value(item.sum, item.rows)));
  const top = most > 0 ? niceTop(most) : null;
  const labels = new Map(series.map((item) => [item.key, item.label]));
  const bars = sums.map(({ period, rows, sum }): ChartBar => {
    const empty = sum.rows === 0;
    const whole = gauge.whole(sum);
    const value = gauge.value(sum, rows);
    const words = gauge.words(sum, rows);
    const segments = empty || value <= 0 ? [] : gauge.parts(period, rows);
    const lines = segments.map((part) => `\n${labels.get(part.key) ?? part.key} ${gauge.partWords(part.value)}`).join("");
    const label = by === "month" ? monthWord(period) : period === today ? dayWord(period, today) : dateWord(period);
    return {
      period, label, empty, value, share: top === null ? 0 : value / top, words, now: period === current, whole,
      hover: empty ? null : `${label}: ${words}${whole ? "" : ", not whole"}${lines}`, segments,
    };
  });
  return {
    measure, by, aria, top: top === null ? null : { value: top, words: axisWords(top, measure) }, bars,
    sentence: null, name: null, caption: null, series,
  };
}

/**
 * The chart of a daily report over `range`, asked by day or by month as `rangeBy` says.
 * Null when the range holds no request with a time: the page then shows `NOTHING_USED`.
 * By cost, with `terms` (the cost terms of the same days, by the same period) each bar is
 * stacked by token class, `COST_SERIES`, each part its class's share of the bar; a period
 * the terms hold nothing for is drawn whole.
 *
 * The sentence, by cost: `The most in these 30 days was today, $19.54. 8 days had no
 * sessions.` By tokens: `71.9M tokens in these 30 days. The most was today, 22.0M.`, or
 * `At least 71.9M tokens …` when a count is not whole.
 * A range of one period has none, since its one bar says its value.
 */
export function chartModel(report: Report<DayRow>, range: Range, measure: Measure, today: string,
                           terms: Report<TermsRow> | null = null): ChartModel | null {
  const by = rangeBy(range, today);
  const dated = report.rows.filter((row) => row.period !== null);
  if (dated.length === 0) return null;
  const split = classCosts(terms);
  const gauge: Gauge = measure === "tokens" ? TOKENS_GAUGE
    : { ...COST_GAUGE, parts: (period) => (split.get(period) ?? []).some((part) => part.value > 0) ? split.get(period) ?? [] : [] };
  const unit = by === "day" ? "day" : "month";
  const chart = gaugeChart(dated, range, today, measure, gauge, measure === "cost" && split.size ? COST_SERIES : [],
    `${measure === "cost" ? "Estimated cost" : "Tokens"} each ${unit}, ${rangeLabel(range, today)}`);

  const sums = measured(dated, range, today, by);
  const name = (period: string): string => (by === "month" ? monthWord(period) : dayWord(period, today));
  const value = (sum: Sum): number => gauge.value(sum, []);
  const words = (sum: Sum): string => gauge.words(sum, []);
  const span = `these ${countWords(sums.length, unit)}`;
  const used = sums.filter((item) => item.sum.rows > 0 && (measure === "tokens" || item.sum.cost !== null));
  const dearest = used.reduce<(typeof used)[number] | null>(
    (best, item) => (best === null || value(item.sum) >= value(best.sum) ? item : best), null);
  const parts: string[] = [];
  if (sums.length > 1) {
    const which = dearest === null ? null : `${name(dearest.period)}, ${words(dearest.sum)}`;
    if (measure === "tokens") {
      const total = tokenWords(sumRows(sums.map((item) => item.sum)));
      parts.push(`${total.charAt(0).toUpperCase()}${total.slice(1)} tokens in ${span}.`);
      if (which !== null) parts.push(`The most was ${which}.`);
    } else {
      if (which !== null) parts.push(`The most in ${span} was ${which}.`);
      const none = sums.filter((item) => item.sum.rows === 0).length;
      if (none > 0) parts.push(`${countWords(none, unit)} had no sessions.`);
    }
  }
  return { ...chart, sentence: parts.length ? parts.join(" ") : null };
}

/**
 * The two charts by tokens: the cache reads alone, since in one stack with the rest each
 * bar would be nearly all one shade, and the billable work, fresh input, cache writes
 * and output, stacked. Each names itself and says its share of the range's tokens. Null
 * as for `chartModel`.
 */
export function tokenCharts(report: Report<DayRow>, range: Range, today: string): ChartModel[] | null {
  const dated = report.rows.filter((row) => row.period !== null);
  if (dated.length === 0) return null;
  const unit = rangeBy(range, today) === "day" ? "day" : "month";
  const span = rangeLabel(range, today);
  const all = dated.reduce((total, row) => total + row.tokens, 0);
  const reads = dated.reduce((total, row) => total + row.cache_read, 0);
  const share = all > 0 ? reads / all : 0;
  const cache = gaugeChart(dated, range, today, "tokens", columnsGauge([{ key: "cache_read", label: "Cache read", shade: 3 }]),
    [], `Cache read tokens each ${unit}, ${span}`);
  const billable = gaugeChart(dated, range, today, "tokens", columnsGauge(BILLABLE_SERIES), BILLABLE_SERIES,
    `Billable tokens each ${unit}, ${span}`);
  return [
    { ...cache, name: CACHE_READS,
      caption: `${fmtPercent(share * 100)} of all tokens in this range. Shown alone because a single stack would be one shade.` },
    { ...billable, name: BILLABLE_WORK,
      caption: `Fresh input + cache writes + output: the ${fmtPercent(all > 0 ? ((all - reads) / all) * 100 : 0)} of tokens that is not a cache read.` },
  ];
}

// --- the chart's table --------------------------------------------------------------------

/** A table that stands in a chart's place. */
export interface Table {
  columns: { label: string; num: boolean }[];
  rows: { key: string; cells: string[] }[];
}

/**
 * The chart's rows as a table, newest first: under Cost a column per model, dearest
 * first; under Tokens a column per token class. The total's cell carries the harness's
 * own figure after it where the rows have one. A last column says why a row is not
 * whole, and is there only when some row is not. Requests with no time are the last
 * row, `no time`.
 */
export function chartTable(report: Report<DayRow>, measure: Measure, by: "day" | "month" = "day"): Table {
  const model = (row: DayRow): string => row.model ?? UNKNOWN_MODEL;
  const cost = (name: string): number =>
    report.rows.reduce((total, row) => total + (model(row) === name ? row.cost ?? 0 : 0), 0);
  const models = [...new Set(report.rows.map(model))].sort((a, b) => cost(b) - cost(a) || (a < b ? -1 : 1));
  const keys = [...new Set(report.rows.map((row) => row.period))]
    .sort((a, b) => (a === null ? 1 : b === null ? -1 : a < b ? 1 : -1));
  const lines = keys.map((period) => {
    const mine = report.rows.filter((row) => row.period === period);
    const sum = sumRows(mine);
    const cells = measure === "cost"
      ? models.map((name) => {
        const rows = mine.filter((row) => model(row) === name);
        return rows.length ? costWords({ cost: sumRows(rows).cost, complete: true }) : "";
      })
      : TOKEN_CLASSES.map(([key]) => fmtTokens(mine.reduce((total, row) => total + (Number(row[key]) || 0), 0)));
    const total = measure === "tokens" ? fmtTokens(sum.tokens)
      : `${costWords(sum)}${sum.harness_cost === null ? "" : ` · harness ${fmtCost(sum.harness_cost)}`}`;
    const why = whyNotWhole(sum, measure);
    const name = period === null ? "no time" : by === "month" ? monthWord(period) : dateWord(period);
    return { key: period ?? "no time", cells: [name, ...cells, total], why };
  });
  const flagged = lines.some((line) => line.why !== null);
  const heads = measure === "cost" ? models : TOKEN_CLASSES.map(([, label]) => label);
  return {
    columns: [{ label: by === "month" ? "Month" : "Day", num: false },
              ...heads.map((label) => ({ label, num: true })), { label: "Total", num: true },
              ...(flagged ? [{ label: "Not whole", num: false }] : [])],
    rows: lines.map(({ key, cells, why }) => ({ key, cells: flagged ? [...cells, why ?? ""] : cells })),
  };
}

// --- the group the chart stands in --------------------------------------------------------

/** The link that shows the chart's rows as a table in its place, and the one back. */
export const TABLE = "Table";
export const CHART = "Chart";

/** The chart's group as the view places it: one of the charts, their table, or the
 * sentence that nothing was used. */
export interface ChartGroup {
  /** The group's name at its left: `Each day`, `Each month`. */
  label: string;
  /** The line above the chart or the table. */
  sentence: string | null;
  /** `Table` beside the sentence while the chart shows, `Chart` while the table does;
   * null where there is only one of them to show. */
  toggle: string | null;
  /** `Nothing was used in this range.`, in the chart's place. */
  nothing: string | null;
  /** One chart by cost; by tokens the cache reads, then the billable work. Empty while
   * the table or the sentence stands in their place. */
  charts: ChartModel[];
  table: Table | null;
}

/**
 * The chart's group over a daily report of the range, its bars by cost stacked by class
 * from `terms` where they have answered. With no row the range holds nothing, and the
 * group says so. Rows that all have no time have no bar to stand on, so only their table
 * shows.
 */
export function chartGroup(report: Report<DayRow>, range: Range, measure: Measure, today: string,
                           asTable: boolean, terms: Report<TermsRow> | null = null): ChartGroup {
  const by = rangeBy(range, today);
  const label = by === "day" ? "Each day" : "Each month";
  if (report.rows.length === 0) {
    return { label, sentence: null, toggle: null, nothing: NOTHING_USED, charts: [], table: null };
  }
  const chart = chartModel(report, range, measure, today, terms);
  if (chart === null) {
    return { label, sentence: null, toggle: null, nothing: null, charts: [], table: chartTable(report, measure, by) };
  }
  if (asTable) {
    return { label, sentence: chart.sentence, toggle: CHART, nothing: null, charts: [], table: chartTable(report, measure, by) };
  }
  const charts = measure === "tokens" ? tokenCharts(report, range, today) ?? [] : [chart];
  return { label, sentence: chart.sentence, toggle: TABLE, nothing: null, charts, table: null };
}

// --- the chart, placed --------------------------------------------------------------------

/** The chart's sizes, in px. A bar stands on `base` and a bar at the axis's top reaches
 * `top`; the grid line and the axis are drawn half a pixel under those. */
export const CHART_SIZE = { height: 106, top: 8, base: 84, labels: 100, barShare: 0.565, barMax: 28,
                            barMin: 1.5, outlineMin: 4, labelGap: 56, glyph: 6.5 };

export interface DrawnBar {
  period: string;
  x: number;
  y: number;
  width: number;
  height: number;
  now: boolean;
  /** A period that is not whole: a dashed outline around the bar. */
  outline: { x: number; y: number; width: number; height: number } | null;
  /** The whole column over the period, which gives the value on hover. */
  slot: { x: number; width: number };
  hover: string;
  /** A stacked bar's parts, bottom first, a pixel apart where each is tall enough; empty
   * for a bar drawn whole. */
  parts: { key: string; y: number; height: number; shade: Series["shade"] }[];
}

export interface DrawnLabel {
  x: number;
  text: string;
  anchor: "start" | "middle" | "end";
  /** Today's (this month's) label, in the text colour. */
  now: boolean;
}

/** A chart model laid out at one width. */
export interface ChartDraw {
  width: number;
  height: number;
  /** The grid line at the axis's top with its value above it; null when every bar is zero. */
  grid: { y: number; words: string } | null;
  /** The line the bars stand on. */
  axis: number;
  bars: DrawnBar[];
  labels: DrawnLabel[];
  labelY: number;
  /** Today's value in words beside its bar. */
  value: { x: number; y: number; text: string; anchor: "start" | "end" } | null;
}

const round = (n: number): number => Math.round(n * 10) / 10;

/**
 * The chart at `width` px. Every period has a column; one in which nothing was used has
 * no bar. The axis names the first and the last period, and between them the 1st, 10th
 * and 20th of a month (each January by month) where there is room; with room for all,
 * every period. A name is centred under its period; the first is set at the chart's left
 * edge when centring it would reach more than `hang` px past that edge, and the last at
 * its right edge. Today's value stands left of its bar; where a bar beside it would
 * be under the words it stands over its own bar, and above the grid line where the bars
 * there are taller than it.
 */
export function chartDraw(model: ChartModel, width: number, hang = 0): ChartDraw {
  const size = CHART_SIZE;
  const count = model.bars.length;
  const slot = width / Math.max(1, count);
  const barWidth = Math.min(size.barMax, Math.max(1, slot * size.barShare));
  const centre = (i: number): number => i * slot + slot / 2;
  const bars: DrawnBar[] = [];
  let value: ChartDraw["value"] = null;
  let now: { i: number; x: number; y: number; words: string } | null = null;
  model.bars.forEach((bar, i) => {
    if (bar.empty || bar.hover === null) return;
    const full = bar.share * (size.base - size.top);
    const height = bar.value > 0 ? Math.max(size.barMin, full) : 0;
    const x = centre(i) - barWidth / 2;
    const y = size.base - height;
    const outlined = Math.max(size.outlineMin, height);
    const shades = new Map(model.series.map((item) => [item.key, item.shade]));
    const whole = bar.segments.reduce((total, part) => total + part.value, 0);
    const parts: DrawnBar["parts"] = [];
    let foot = size.base;
    for (const part of bar.segments) {
      const shade = shades.get(part.key);
      if (part.value <= 0 || whole <= 0 || shade === undefined) continue;
      const tall = (part.value / whole) * height;
      // A pixel of the background between two parts, where the part keeps some height.
      const gap = parts.length && tall > 2 ? 1 : 0;
      parts.push({ key: part.key, y: round(foot - tall), height: round(Math.max(0.5, tall - gap)), shade });
      foot -= tall;
    }
    bars.push({
      period: bar.period, x: round(x), y: round(y), width: round(barWidth), height: round(height), now: bar.now,
      outline: bar.whole ? null
        : { x: round(x) - 0.5, y: round(size.base - outlined) - 0.5, width: round(barWidth) + 1, height: round(outlined) + 1 },
      slot: { x: round(i * slot), width: round(slot) },
      hover: bar.hover, parts,
    });
    if (bar.now) now = { i, x, y, words: bar.words };
  });
  if (now !== null) {
    const { i, x, y, words } = now as { i: number; x: number; y: number; words: string };
    const left = x - 5 >= 64;
    const beside = round(Math.min(y + 9, size.base - 6));
    // The words' box beside the bar, by a width guessed from their length.
    const reach = words.length * size.glyph + 5;
    // Whether another bar stands under words that span `from` to `to` with their foot at `foot`.
    const under = (from: number, to: number, foot: number): boolean => bars.some((other) =>
      other.period !== model.bars[i].period && other.x + other.width > from && other.x < to && other.y < foot);
    const end = x + barWidth - reach >= 0;
    const [from, to] = end ? [x + barWidth - reach, x + barWidth] : [x, x + reach];
    const over = round(Math.max(size.top + 12, y - 4));
    if (!(left ? under(x - reach, x, beside) : under(x + barWidth, x + barWidth + reach, beside))) {
      value = { x: round(left ? x - 5 : x + barWidth + 5), y: beside, text: words, anchor: left ? "end" : "start" };
    } else {
      // Over the bar, or above the grid line where the bars beside it are taller than it.
      value = { x: round(end ? x + barWidth : x), y: under(from, to, over) ? size.top - 4 : over, text: words,
                anchor: end ? "end" : "start" };
    }
  }

  const marked = (period: string): boolean =>
    (model.by === "month" ? /-01$/.test(period) : /-(01|10|20)$/.test(period));
  const order = slot >= size.labelGap ? model.bars.map((_, i) => i)
    : [0, count - 1, ...model.bars.map((bar, i) => (marked(bar.period) ? i : -1)).filter((i) => i > 0 && i < count - 1)];
  const taken: number[] = [];
  for (const i of order) {
    if (i < 0 || i >= count || taken.includes(i)) continue;
    if (slot >= size.labelGap || taken.every((other) => Math.abs(centre(other) - centre(i)) >= size.labelGap)) taken.push(i);
  }
  const labels = taken.sort((a, b) => a - b).map((i): DrawnLabel => {
    const bar = model.bars[i];
    const at = centre(i);
    if (i === count - 1 && count > 1 && slot < size.labelGap) return { x: width, text: bar.label, anchor: "end", now: bar.now };
    if (at - bar.label.length * size.glyph / 2 < -hang) return { x: 0, text: bar.label, anchor: "start", now: bar.now };
    return { x: round(at), text: bar.label, anchor: "middle", now: bar.now };
  });

  return {
    width, height: size.height,
    grid: model.top === null ? null : { y: size.top + 0.5, words: model.top.words },
    axis: size.base + 0.5, bars, labels, labelY: size.labels, value,
  };
}
