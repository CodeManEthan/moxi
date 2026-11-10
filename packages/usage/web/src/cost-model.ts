// The Cost rows and the Range menu's figures, worked out from one daily report with no
// DOM. Every figure is a sum over whole days of that report, so each is exact for its
// days; a sum that is not whole reads `at least`, and one nothing in which could be
// priced reads `no price`, never a zero.

import { addDays, dateWord, daysFrom, LOCAL, whenWord } from "./days.ts";
import type { Clock } from "./days.ts";
import type { DayRow, Report } from "./feed.ts";
import { fmtCost, fmtTokens } from "./views.ts";

/** What a set of report rows adds up to. */
export interface Sum {
  /** How many report rows it sums; 0 means nothing was used. */
  rows: number;
  requests: number;
  tokens: number;
  /** Dollars over the priced rows: 0 with no rows, null when no row could be priced. */
  cost: number | null;
  /** False when a row holds tokens with no price or a count the harness did not report. */
  complete: boolean;
  unknown_rows: number;
  unpriced_tokens: number;
  unpriced_models: string[];
  /** The harness's own figure summed, where any row carries one. */
  harness_cost: number | null;
}

type Summed = Pick<DayRow, "requests" | "tokens" | "cost" | "complete" | "unknown_rows" | "unpriced_tokens"
  | "harness_cost"> & { unpriced_models?: string[] };

/** The sum of report rows or of a report's total. */
export function sumRows(rows: Summed[]): Sum {
  const add = (pick: (row: Summed) => number): number => rows.reduce((total, row) => total + pick(row), 0);
  const priced = rows.filter((row) => row.cost !== null);
  const harness = rows.filter((row) => row.harness_cost !== null);
  return {
    rows: rows.length,
    requests: add((row) => row.requests),
    tokens: add((row) => row.tokens),
    cost: priced.length ? priced.reduce((total, row) => total + (row.cost as number), 0) : rows.length ? null : 0,
    complete: rows.every((row) => row.complete),
    unknown_rows: add((row) => row.unknown_rows),
    unpriced_tokens: add((row) => row.unpriced_tokens),
    unpriced_models: [...new Set(rows.flatMap((row) => row.unpriced_models ?? []))].sort(),
    harness_cost: harness.length ? harness.reduce((total, row) => total + (row.harness_cost as number), 0) : null,
  };
}

/** The sum of a daily report's rows from `since` to `until`, both days included. */
export function sumDays(rows: DayRow[], since: string, until: string): Sum {
  return sumRows(rows.filter((row) => row.period !== null && row.period >= since && row.period <= until));
}

/** A sum's cost in words: `$19.54`, `at least $19.54`, `no price`. */
export function costWords(sum: Pick<Sum, "cost" | "complete">): string {
  if (sum.cost === null) return "no price";
  return sum.complete ? fmtCost(sum.cost) : `at least ${fmtCost(sum.cost)}`;
}

/** Why a sum is not whole, for the hover over an `at least` figure and a table's
 * column; null for a whole one. A token count is not whole only for a count the
 * harness did not report; a cost also for tokens with no price. */
export function whyNotWhole(sum: Sum, measure: "cost" | "tokens" = "cost"): string | null {
  const out: string[] = [];
  if (sum.unknown_rows > 0) {
    out.push(`${countWords(sum.unknown_rows, "request")} with a token count the harness didn't report`);
  }
  if (measure === "tokens") return out[0] ?? null;
  if (sum.cost === null) {
    out.push("nothing here could be priced");
  } else if (sum.unpriced_tokens > 0) {
    const models = sum.unpriced_models.length ? ` (${sum.unpriced_models.join(", ")})` : "";
    out.push(`${fmtTokens(sum.unpriced_tokens)} tokens have no price${models}`);
  }
  if (out.length === 0 && !sum.complete) out.push("some of these figures are unknown");
  return out.length ? out.join("; ") : null;
}

/** A count with its noun: `1 request`, `1,204 requests`. */
export function countWords(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
}

// --- the Cost rows ------------------------------------------------------------------------

/** One row of Cost: a period's figure, and the period before it at the right. */
export interface CostRow {
  key: "today" | "7d" | "30d";
  /** `Today`, `7 days`, `30 days`. */
  label: string;
  /** `$19.54 over 204 requests`. */
  figure: string;
  /** Why the figure reads `at least` or `no price`; null for a whole one. */
  why: string | null;
  /** `yesterday $5.24`, `the week before $7.67`, `the 30 days before $11.30`. */
  before: string;
  beforeWhy: string | null;
}

/** How many days the Cost rows' report covers: the three periods and the one before each. */
export const COST_DAYS = 90;

const PERIODS: readonly [CostRow["key"], string, number, string][] = [
  ["today", "Today", 1, "yesterday"], ["7d", "7 days", 7, "the week before"],
  ["30d", "30 days", 30, "the 30 days before"],
];

/** The Cost rows from one daily report over the 90 days to its `today`, under no filter.
 * Empty until the report has answered with its day. */
export function costRows(daily: Report<DayRow> | null): CostRow[] {
  const today = daily?.today;
  if (!daily || !today) return [];
  return PERIODS.map(([key, label, days, earlier]) => {
    const now = sumDays(daily.rows, addDays(today, 1 - days), today);
    const before = sumDays(daily.rows, addDays(today, 1 - 2 * days), addDays(today, -days));
    return { key, label, figure: `${costWords(now)} over ${countWords(now.requests, "request")}`,
             why: whyNotWhole(now), before: `${earlier} ${costWords(before)}`, beforeWhy: whyNotWhole(before) };
  });
}

/** Today's dearest sessions, already named: `most to planner $4.21, reviewer $3.67`.
 * A session with no price is left out; null with none to name. */
export function mostTo(sessions: { name: string; cost: number | null }[]): string | null {
  const priced = sessions.filter((item) => item.cost !== null);
  if (priced.length === 0) return null;
  return `most to ${priced.map((item) => `${item.name} ${fmtCost(item.cost)}`).join(", ")}`;
}

/** The caption under the Cost rows, and the link beside it. */
export const CAPTION = "Estimated from a local price table. Not billing data.";
export const PRICES_LINK = "Prices";

/** `sessions read 19:10`: the index's last pass; null when it has never run. */
export function sessionsRead(lastPass: number | null, today: string | null, clock: Clock = LOCAL): string | null {
  return lastPass === null ? null : `sessions read ${whenWord(lastPass, today, clock)}`;
}

// --- the range ----------------------------------------------------------------------------

export type Preset = "today" | "7d" | "30d" | "90d";
/** What the filter line's Range is set to. */
export type Range = { kind: Preset } | { kind: "all" } | { kind: "custom"; since: string; until: string };

export const DEFAULT_RANGE: Range = { kind: "30d" };
const PRESET_DAYS: Record<Preset, number> = { today: 1, "7d": 7, "30d": 30, "90d": 90 };
const PRESET_LABELS: Record<Preset, string> = {
  today: "Today", "7d": "Last 7 days", "30d": "Last 30 days", "90d": "Last 90 days",
};
/** The longest range charted by day; a longer one, and all time, is charted by month. */
export const DAYS_MAX = 92;

/** A range's first and last day, both included; null for all time. */
export function rangeDays(range: Range, today: string): { since: string; until: string } | null {
  if (range.kind === "all") return null;
  if (range.kind === "custom") return { since: range.since, until: range.until };
  return { since: addDays(today, 1 - PRESET_DAYS[range.kind]), until: today };
}

/** Whether a range's chart has a bar a day or a bar a month. */
export function rangeBy(range: Range, today: string): "day" | "month" {
  const days = rangeDays(range, today);
  return days === null || daysFrom(days.since, days.until) + 1 > DAYS_MAX ? "month" : "day";
}

/** The Range button's words: `last 30 days`, `today`, `all time`, `Oct 10 to Nov 1`. */
export function rangeLabel(range: Range): string {
  if (range.kind === "all") return "all time";
  if (range.kind !== "custom") return range.kind === "today" ? "today" : PRESET_LABELS[range.kind].toLowerCase();
  return range.since === range.until ? dateWord(range.since) : `${dateWord(range.since)} to ${dateWord(range.until)}`;
}

/** One row of the Range menu. */
export interface RangeItem {
  key: Preset | "custom" | "all";
  /** `Today`, `Last 7 days`, `Last 30 days`, `Last 90 days`, `From … to …`, `All time`. */
  label: string;
  /** The range's cost under the filters in force; null where the menu shows none. */
  figure: string | null;
  /** The range in force, which carries the menu's check. */
  picked: boolean;
  /** A rule is drawn above this row. */
  ruled: boolean;
}

/**
 * The Range menu. `daily` is the 90-day daily report under the filters in force (the
 * Cost rows' own when no filter is set); `allTime` the monthly report over no range,
 * asked when the menu first opens. A figure is null until its report has answered.
 */
export function rangeMenu(range: Range, daily: Report<DayRow> | null, allTime: Report<DayRow> | null): RangeItem[] {
  const today = daily?.today ?? null;
  const presets = (Object.keys(PRESET_DAYS) as Preset[]).map((key): RangeItem => ({
    key, label: PRESET_LABELS[key],
    figure: daily === null || today === null ? null
      : costWords(sumDays(daily.rows, addDays(today, 1 - PRESET_DAYS[key]), today)),
    picked: range.kind === key, ruled: false,
  }));
  return [...presets,
    { key: "custom", label: "From … to …", figure: null, picked: range.kind === "custom", ruled: true },
    { key: "all", label: "All time", figure: allTime === null ? null : costWords(sumRows(allTime.rows.length ? [allTime.total] : [])),
      picked: range.kind === "all", ruled: false }];
}
