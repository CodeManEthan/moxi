// The heat map under the chart: each day's estimated cost over the trailing 53 weeks,
// whatever the range, under the other filters. Weeks run as columns and weekdays as
// rows, Sunday at the top; a day's shade is one of five steps set by the quantiles of
// the days that cost something, so a few dear days don't wash out the rest. A day that
// was used is a button that sets the range to that day alone. Worked out from a daily
// report with no DOM, and laid out at a width.

import type { Table } from "./chart-model.ts";
import { costWords, sumRows } from "./cost-model.ts";
import { addDays, dateWord, daysOf } from "./days.ts";
import type { DayRow, Report } from "./feed.ts";
import { fmtTokens } from "./views.ts";

/** How many days the heat map holds: 53 weeks to today. */
export const HEAT_DAYS = 371;
/** The group's name at its left. */
export const HEAT_LABEL = "53 weeks";
export const HEAT_SENTENCE = "Each day's estimated cost over the last 53 weeks, whatever the range. Pick a day to see it alone.";
export const LESS = "less";
export const MORE = "more";

/** One day of the heat map. */
export interface HeatDay {
  day: string;
  /** 0 for a day nothing was used or nothing cost, else 1 (cheapest) to 5 (dearest). */
  step: number;
  /** Something was used this day: the day is a button that filters to it. */
  used: boolean;
  /** `Nov 3 2026: $5.35`, for a reader that cannot see the shade. */
  aria: string;
  /** The aria words, the day's tokens after them, then a line per token class. */
  hover: string;
}

export interface HeatModel {
  aria: string;
  days: HeatDay[];
  /** The day of the week the first day falls on, 0 for Sunday. */
  offset: number;
  sentence: string;
  /** The used days as a table, newest first: Day, Est. cost, Tokens. */
  table: Table;
}

const CLASSES: readonly [keyof DayRow & string, string][] = [
  ["fresh_input", "Input"], ["cache_write", "Cache create"], ["cache_read", "Cache read"], ["output", "Output"],
];

/** The heat map's first day: `HEAT_DAYS` days to `today`, both included. */
export function heatSince(today: string): string {
  return addDays(today, 1 - HEAT_DAYS) ?? today;
}

/** A day with its year: `Nov 3 2026`. */
function yearDay(day: string): string {
  return `${dateWord(day)} ${day.slice(0, 4)}`;
}

/** The four steps' lower bounds: the 20th, 40th, 60th and 80th percentiles of the days
 * that cost something. */
export function heatThresholds(values: number[]): number[] {
  const sorted = values.filter((value) => value > 0).sort((a, b) => a - b);
  if (sorted.length === 0) return [0, 0, 0, 0];
  const at = (q: number): number => sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))];
  return [at(0.2), at(0.4), at(0.6), at(0.8)];
}

/** A value's step against the thresholds: 0 for nothing, else 1 to 5. */
export function heatStep(value: number, thresholds: number[]): number {
  if (value <= 0) return 0;
  return Math.min(5, 1 + thresholds.filter((bound) => value > bound).length);
}

/**
 * The heat map of a daily report by day over the 53 weeks to `today`. Null when no day
 * of them was used: there is no heat map then.
 */
export function heatModel(report: Report<DayRow>, today: string): HeatModel | null {
  const since = heatSince(today);
  const rows = report.rows.filter((row) => row.period !== null && row.period >= since && row.period <= today);
  if (rows.length === 0) return null;
  const byDay = new Map<string, DayRow[]>();
  for (const row of rows) byDay.set(row.period as string, [...(byDay.get(row.period as string) ?? []), row]);
  const calendar = daysOf(since, today);
  const sums = calendar.map((day) => ({ day, rows: byDay.get(day) ?? [] }))
    .map((item) => ({ ...item, sum: sumRows(item.rows) }));
  const thresholds = heatThresholds(sums.map((item) => item.sum.cost ?? 0));
  const days = sums.map(({ day, rows: mine, sum }): HeatDay => {
    const used = sum.rows > 0;
    const aria = `${yearDay(day)}: ${used ? costWords(sum) : "nothing used"}`;
    const count = (key: keyof DayRow & string): number => mine.reduce((total, row) => total + (Number(row[key]) || 0), 0);
    const hover = !used ? aria : [`${aria}, ${fmtTokens(sum.tokens)} tokens`,
                                  ...CLASSES.map(([key, label]) => `${label} ${fmtTokens(count(key))}`)].join("\n");
    return { day, step: heatStep(sum.cost ?? 0, thresholds), used, aria, hover };
  });
  const first = new Date(`${since}T00:00:00Z`).getUTCDay();
  return {
    aria: "Estimated cost each day, last 53 weeks",
    days, offset: Number.isNaN(first) ? 0 : first, sentence: HEAT_SENTENCE,
    table: {
      columns: [{ label: "Day", num: false }, { label: "Est. cost", num: true }, { label: "Tokens", num: true }],
      rows: sums.filter((item) => item.sum.rows > 0).reverse()
        .map(({ day, sum }) => ({ key: day, cells: [yearDay(day), costWords(sum), fmtTokens(sum.tokens)] })),
    },
  };
}

// --- the heat map, placed -----------------------------------------------------------------

/** The heat map's sizes, in px: the weekday labels' column, the month labels' row, and
 * the most and the fewest px a week's column takes. A cell is its pitch less 2. */
export const HEAT_SIZE = { weekdays: 22, months: 14, pitchMax: 13, pitchMin: 9 };

export interface HeatDraw {
  width: number;
  height: number;
  cell: number;
  cells: (HeatDay & { x: number; y: number })[];
  months: { x: number; text: string }[];
  weekdays: { y: number; text: string }[];
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * The heat map at `width` px: as wide a pitch as fits, from 9 to 13 px a week; under 9
 * it keeps 9 and is wider than `width`, and its box scrolls. M, W and F name their rows.
 * A month is named over the column its first day falls in, unless that is within three
 * columns of the last name.
 */
export function heatDraw(model: HeatModel, width: number): HeatDraw {
  const size = HEAT_SIZE;
  const columns = Math.ceil((model.days.length + model.offset) / 7);
  const pitch = Math.max(size.pitchMin, Math.min(size.pitchMax, Math.floor((width - size.weekdays) / Math.max(1, columns))));
  const cell = pitch - 2;
  const cells = model.days.map((day, i) => {
    const slot = i + model.offset;
    return { ...day, x: size.weekdays + Math.floor(slot / 7) * pitch, y: size.months + (slot % 7) * pitch };
  });
  const months: HeatDraw["months"] = [];
  let lastColumn = -4;
  model.days.forEach((day, i) => {
    if (i > 0 && !day.day.endsWith("-01")) return;
    const column = Math.floor((i + model.offset) / 7);
    if (column - lastColumn < 4) return;
    lastColumn = column;
    months.push({ x: size.weekdays + column * pitch, text: MONTHS[Number(day.day.slice(5, 7)) - 1] ?? "" });
  });
  return {
    width: size.weekdays + columns * pitch, height: size.months + 7 * pitch, cell, cells, months,
    weekdays: [[1, "M"], [3, "W"], [5, "F"]].map(([row, text]) =>
      ({ y: size.months + (row as number) * pitch + cell / 2, text: text as string })),
  };
}
