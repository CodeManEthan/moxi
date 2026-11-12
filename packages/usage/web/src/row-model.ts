// What usage's row on Home says, worked out with no DOM so the Node tests can read it: the
// answer of `/api/summary` read and checked, and the row's line for Home. The clock is
// passed in, so the tests read the same words in any zone and locale.

import { daysFrom, LOCAL, seconds, weekday, whenWord } from "./days.ts";
import type { Clock } from "./days.ts";
import { harnessName, percentWords } from "./limits-model.ts";
import { fmtCost } from "./views.ts";

/** One main plan window of the summary: `window` is `5h` or `7d`; times are ISO 8601. */
export interface SummaryWindow {
  window: string;
  used_percent: number;
  resets_at: string;
  /** When the window fills at the pace of its recent readings; null when it resets first. */
  full_at: string | null;
}

/** The main windows of the harness read last, with when it was read. */
export interface SummaryLimits {
  harness: string;
  observed_at: string;
  windows: SummaryWindow[];
}

/** Today's cost: `complete` is false when a model of today's could not be priced. */
export interface SummaryCost {
  day: string;
  dollars: number;
  complete: boolean;
}

/** The answer of `/api/summary`; `now` is the page server's clock, in epoch seconds. */
export interface Summary {
  now: number;
  limits: SummaryLimits | null;
  cost: SummaryCost | null;
}

/** The row's line, as the `detail` of a `moxi-home-row` event. */
export interface UsageLine {
  parts: Array<{ text: string; heavy?: boolean }>;
  time?: string;
  needs?: number;
}

/** On Home's needs scale: something will run out. */
export const RUNS_OUT = 40;

/** Each main window by its name in the row, in the row's order. */
const WINDOWS: Record<string, string> = { "5h": "5-hour", "7d": "weekly" };

const isWindow = (data: any): data is SummaryWindow =>
  typeof data === "object" && data !== null && typeof data.window === "string" && data.window in WINDOWS
  && typeof data.used_percent === "number" && Number.isFinite(data.used_percent)
  && seconds(data.resets_at) !== null && (data.full_at === null || seconds(data.full_at) !== null);

/** The summary as the route answers it, else null. A window the row cannot say is left
 * out, and limits with no window left are none. */
export function parseSummary(data: unknown): Summary | null {
  if (typeof data !== "object" || data === null) return null;
  const { now, limits, cost } = data as Record<string, any>;
  if (typeof now !== "number" || !Number.isFinite(now)) return null;
  let found: SummaryLimits | null = null;
  if (typeof limits === "object" && limits !== null && typeof limits.harness === "string"
      && Array.isArray(limits.windows) && seconds(limits.observed_at) !== null) {
    const windows = limits.windows.filter(isWindow)
      .sort((a: SummaryWindow, b: SummaryWindow) => Object.keys(WINDOWS).indexOf(a.window)
        - Object.keys(WINDOWS).indexOf(b.window));
    if (windows.length) found = { harness: limits.harness, observed_at: limits.observed_at, windows };
  }
  const spent = typeof cost === "object" && cost !== null && typeof cost.dollars === "number"
    && Number.isFinite(cost.dollars) && typeof cost.day === "string"
    ? { day: cost.day, dollars: cost.dollars, complete: cost.complete !== false } : null;
  return { now, limits: found, cost: spent };
}

/** A moment the row names, against `today`: `21:34` on today's date, `tomorrow 17:51` on
 * the next, the weekday up to a week on (`Wed 23:58`), else as the page words it. */
export function rowWhen(at: number, today: string, clock: Clock = LOCAL): string {
  const day = clock.day(at);
  const off = daysFrom(today, day);
  if (off >= 2 && off <= 7) return `${weekday(day)} ${clock.time(at)}`;
  return whenWord(at, today, clock);
}

/**
 * The Usage row's line; null when there is neither a limit nor a cost to say.
 *
 * - With no window filling before its reset: `Claude 5-hour 49%, resets 21:34 · weekly
 *   60% · today $19.54`, the time `read 19:08`.
 * - With one filling: the filling window takes the row, heavy, and needs the user:
 *   `Claude weekly limit fills tomorrow 17:51` then `, resets Wed 23:58 · today $19.54`.
 *   With both filling, the earlier fill leads and the other follows it (`, 5-hour fills
 *   20:40`).
 * - With the cost alone: `today $19.54`, and no time.
 *
 * A window that is not there is left out with its ` · `, and a cost with an unpriced model
 * reads `$19.54+`.
 */
export function usageLine(value: Summary | null, clock: Clock = LOCAL): UsageLine | null {
  if (value === null) return null;
  const cost = value.cost === null ? null
    : `today ${fmtCost(value.cost.dollars)}${value.cost.complete ? "" : "+"}`;
  const tail = cost === null ? [] : [{ text: ` · ${cost}` }];
  const limits = value.limits;
  if (limits === null || limits.windows.length === 0) return cost === null ? null : { parts: [{ text: cost }] };
  const today = clock.day(value.now);
  const when = (iso: string): string => rowWhen(seconds(iso) as number, today, clock);
  const name = harnessName(limits.harness);
  const time = `read ${whenWord(seconds(limits.observed_at) as number, today, clock)}`;
  const filling = limits.windows.filter((window) => window.full_at !== null)
    .sort((a, b) => (seconds(a.full_at) as number) - (seconds(b.full_at) as number));
  if (filling.length) {
    const [lead, ...rest] = filling;
    return {
      parts: [{ text: `${name} ${WINDOWS[lead.window]} limit fills ${when(lead.full_at as string)}`, heavy: true },
              { text: `, resets ${when(lead.resets_at)}` },
              ...rest.map((window) => ({ text: `, ${WINDOWS[window.window]} fills ${when(window.full_at as string)}` })),
              ...tail],
      time, needs: RUNS_OUT,
    };
  }
  const clauses = limits.windows.map((window) => window.window === "5h"
    ? `5-hour ${percentWords(window.used_percent)}, resets ${when(window.resets_at)}`
    : `${WINDOWS[window.window]} ${percentWords(window.used_percent)}`);
  return {
    parts: [{ text: `${name} ${clauses[0]}` }, ...clauses.slice(1).map((text) => ({ text: ` · ${text}` })), ...tail],
    time,
  };
}
