// The chart under the filter line: one bar a day (or a month), the sentence above it and
// the table that stands in its place, worked out from a daily report with no DOM. The
// view scales `share` to its height and places the labels; every word and figure is
// decided here. Nothing was used in the range: there is no chart, and the page says so.

import { costWords, countWords, rangeBy, rangeDays, rangeLabel, sumRows, whyNotWhole } from "./cost-model.ts";
import type { Range, Sum } from "./cost-model.ts";
import { dateWord, dayWord, daysOf, monthWord } from "./days.ts";
import type { DayRow, Report } from "./feed.ts";
import { fmtCost, fmtTokens } from "./views.ts";

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
  /** `Nov 3: $5.35`, with `, not whole` after it; null for an empty period. */
  hover: string | null;
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
}

const UNKNOWN_MODEL = "unknown model";
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

/**
 * The chart of a daily report over `range`, asked by day or by month as `rangeBy` says.
 * Null when the range holds no request with a time: the page then shows `NOTHING_USED`.
 *
 * The sentence, by cost: `The most in these 30 days was today, $19.54. 8 days had no
 * sessions.` By tokens: `71.9M tokens in these 30 days. The most was today, 22.0M.`
 * A range of one period has none, since its one bar says its value.
 */
export function chartModel(report: Report<DayRow>, range: Range, measure: Measure, today: string): ChartModel | null {
  const by = rangeBy(range, today);
  const dated = report.rows.filter((row) => row.period !== null);
  if (dated.length === 0) return null;
  const current = by === "day" ? today : today.slice(0, 7);
  const sums = periods(dated, range, today, by).map((period) =>
    ({ period, sum: sumRows(dated.filter((row) => row.period === period)) }));
  const value = (sum: Sum): number => (measure === "cost" ? sum.cost ?? 0 : sum.tokens);
  const words = (sum: Sum): string => (measure === "cost" ? costWords(sum) : tokenWords(sum));
  const most = Math.max(0, ...sums.map((item) => value(item.sum)));
  const top = most > 0 ? niceTop(most) : null;
  const name = (period: string, axis: boolean): string => {
    if (by === "month") return monthWord(period);
    return period === today || !axis ? dayWord(period, today) : dateWord(period);
  };
  const bars = sums.map(({ period, sum }): ChartBar => {
    const empty = sum.rows === 0;
    const whole = measure === "cost" ? sum.complete : sum.unknown_rows === 0;
    return {
      period, label: name(period, true), empty, value: value(sum),
      share: top === null ? 0 : value(sum) / top, words: words(sum), now: period === current, whole,
      hover: empty ? null : `${name(period, true)}: ${words(sum)}${whole ? "" : ", not whole"}`,
    };
  });

  const unit = by === "day" ? "day" : "month";
  const span = `these ${countWords(sums.length, unit)}`;
  const used = sums.filter((item) => item.sum.rows > 0 && (measure === "tokens" || item.sum.cost !== null));
  const dearest = used.reduce<(typeof used)[number] | null>(
    (best, item) => (best === null || value(item.sum) >= value(best.sum) ? item : best), null);
  const parts: string[] = [];
  if (sums.length > 1) {
    const which = dearest === null ? null : `${name(dearest.period, false)}, ${words(dearest.sum)}`;
    if (measure === "tokens") {
      parts.push(`${tokenWords(sumRows(sums.map((item) => item.sum)))} tokens in ${span}.`);
      if (which !== null) parts.push(`The most was ${which}.`);
    } else {
      if (which !== null) parts.push(`The most in ${span} was ${which}.`);
      const none = sums.filter((item) => item.sum.rows === 0).length;
      if (none > 0) parts.push(`${countWords(none, unit)} had no sessions.`);
    }
  }
  return {
    measure, by,
    aria: `${measure === "cost" ? "Estimated cost" : "Tokens"} each ${unit}, ${rangeLabel(range)}`,
    top: top === null ? null : { value: top, words: axisWords(top, measure) },
    bars,
    sentence: parts.length ? parts.join(" ") : null,
  };
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
