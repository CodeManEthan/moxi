// The plan limits as the page draws them: one group per harness that has a row to draw,
// one row per window, and the one-line warning that leads the page while a window fills
// before it resets. Worked out from a limits answer with no DOM. Every piece is the
// words the page shows, or null when the answer does not carry its fact: the view
// places what it is given and decides nothing.

import { dayPart, LOCAL, seconds, whenWord } from "./days.ts";
import type { Clock } from "./days.ts";
import type { Limits, Window } from "./feed.ts";

/** The thin line beside a row's percent. */
export interface LimitMeter {
  /** The share used, a whole number from 0 to 100, rounded down: the line's heavy part. */
  fill: number;
  /** How far through the window the clock is, 0 to 100; null when the window's start
   * or its reset is not known, and then no tick is drawn. */
  tick: number | null;
  /** The line in words: `81% used, 60% of the window gone`. */
  title: string;
}

/** One window's row. A `reset` row is one dim line, `<label> · <resets>`, and has no
 * percent, no line and nothing to open. */
export interface LimitRow {
  /** `<harness>/<limit_id>/<minutes>`: the row's identity from one answer to the next. */
  key: string;
  harness: string;
  limit_id: string;
  minutes: number | null;
  /** `fresh`, or `reset` for a reading whose period has ended. */
  state: "fresh" | "reset";
  /** `5-hour`, `Weekly`, `Weekly Opus`, or the answer's own label. */
  label: string;
  /** `81% used`. */
  used: string | null;
  meter: LimitMeter | null;
  /** `fills tomorrow 17:51`, in the text colour: the window fills before it resets. */
  fills: string | null;
  /** `resets Wed 23:58`; on a `reset` row `reset 21:34`, or `reset` when the reading
   * gave no time. */
  resets: string | null;
  /** `read 18:40`, the row's last words, when it was read at another minute than the
   * one its group's heading says. */
  read: string | null;
  /** The page route that opens the row, `limit/<harness>/<limit_id>/<minutes>`; null
   * for a row that does not open. */
  route: string | null;
}

/** One harness's rows, those that fill early first. */
export interface LimitGroup {
  harness: string;
  /** `Claude`, `Codex`. */
  name: string;
  /** `read 19:08`: when the newest of its rows was read. */
  read: string | null;
  rows: LimitRow[];
}

/** The page's first line while a window fills before it resets. Drawn as the heavy
 * `lead`, then `, <before>` and ` · <more>`, each only when it is not null. */
export interface Warning {
  /** `Codex's 5-hour limit fills at 20:05`. */
  lead: string;
  /** `an hour before it resets`. */
  before: string | null;
  /** With two early rows the other one, `weekly Opus fills Tue 22:29`; with more,
   * `2 more fill early, the next tomorrow 17:51`. */
  more: string | null;
  /** The whole line, for a reader that takes it as one text. */
  text: string;
}

export interface LimitsModel {
  warning: Warning | null;
  groups: LimitGroup[];
}

const FIVE_HOUR = 300;
const WEEK = 10_080;
/** Each harness's main limit: its windows are labelled by their length alone. */
const MAIN: Record<string, string> = { claude: "plan", codex: "codex" };
const NAMES: Record<string, string> = { claude: "Claude", codex: "Codex", opencode: "OpenCode" };
/** The order of groups in which no row fills early; any other harness follows. */
const ORDER = ["claude", "codex"];

const capital = (text: string): string => text.charAt(0).toUpperCase() + text.slice(1);

/** A harness as the page names it. */
export function harnessName(harness: string): string {
  return NAMES[harness] ?? capital(harness);
}

/** A window's label: its length for the two plan windows, with the limit's name after
 * it for a limit that is not the harness's main one, else the answer's own. */
export function limitLabel(row: Pick<Window, "harness" | "limit_id" | "window_minutes" | "label">): string {
  const length = row.window_minutes === FIVE_HOUR ? "5-hour" : row.window_minutes === WEEK ? "Weekly" : null;
  if (length === null) return row.label;
  return row.limit_id === MAIN[row.harness] ? length : `${length} ${capital(row.limit_id)}`;
}

/** The route that opens a row. */
export function limitRoute(harness: string, limitId: string, minutes: number): string {
  return `limit/${encodeURIComponent(harness)}/${encodeURIComponent(limitId)}/${minutes}`;
}

/** A share as a whole number, rounded down so the page never says more than is: 99.6
 * is 99, and only a share that reached 100 is 100. */
const percent = (value: number): number => Math.floor(Math.min(100, Math.max(0, value)));

/** A share in words: `49%`, rounded down; `under 1%` for one above zero and below one,
 * never `0%`; `100%` only at 100 or over. */
export function percentWords(value: number): string {
  return value > 0 && value < 1 ? "under 1%" : `${percent(value)}%`;
}

/** A row with the times its place and its words come from. */
interface Drawn {
  row: LimitRow;
  harness: string;
  observed: number | null;
  full: number | null;
  resets: number | null;
  place: number;
}

function drawn(source: Window, place: number, now: number, today: string | null, clock: Clock): Drawn | null {
  if (source.state !== "fresh" && source.state !== "reset") return null;
  const fresh = source.state === "fresh";
  const resets = seconds(source.resets_at);
  const full = fresh ? seconds(source.full_at) : null;
  const opened = seconds(source.opened);
  const minutes = source.window_minutes;
  const share = fresh ? source.used_percent : null;
  const used = share === null ? null : `${percentWords(share)} used`;
  let meter: LimitMeter | null = null;
  if (share !== null) {
    const gone = opened !== null && resets !== null && resets > opened
      ? 100 * (now - opened) / (resets - opened) : null;
    meter = { fill: percent(share), tick: gone === null ? null : percent(gone),
              title: gone === null ? `${used}` : `${used}, ${percentWords(gone)} of the window gone` };
  }
  const row: LimitRow = {
    key: `${source.harness}/${source.limit_id}/${minutes ?? ""}`,
    harness: source.harness,
    limit_id: source.limit_id,
    minutes,
    state: fresh ? "fresh" : "reset",
    label: limitLabel(source),
    used,
    meter,
    fills: full === null ? null : `fills ${whenWord(full, today, clock)}`,
    resets: fresh ? (resets === null ? null : `resets ${whenWord(resets, today, clock)}`)
      : resets === null ? "reset" : `reset ${whenWord(resets, today, clock)}`,
    read: null,
    route: fresh && minutes !== null ? limitRoute(source.harness, source.limit_id, minutes) : null,
  };
  return { row, harness: source.harness, observed: seconds(source.observed_at), full, resets, place };
}

const soonest = (rows: Drawn[]): number =>
  Math.min(...rows.map((item) => item.full ?? Infinity));

/** Where a group stands among those in which nothing fills early. */
function rank(harness: string): number {
  const at = ORDER.indexOf(harness);
  return at < 0 ? ORDER.length : at;
}

const WORDS = ["", "a", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];

/** A whole count of a unit in words: `an hour`, `two days`, `45 minutes`. */
function count(n: number, unit: string): string {
  if (n === 1) return `${unit === "hour" ? "an" : "a"} ${unit}`;
  return `${WORDS[n] ?? String(n)} ${unit}s`;
}

/** How long before its reset a window fills, in whole units and never more than it is:
 * `40 minutes`, `an hour`, `two days`. Null for under a minute. */
export function spanWords(secs: number): string | null {
  const minutes = Math.floor(secs / 60);
  if (minutes < 1) return null;
  if (minutes < 60) return count(minutes, "minute");
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? count(hours, "hour") : count(Math.floor(hours / 24), "day");
}

/** A label inside a sentence: `weekly Opus`. */
const inSentence = (label: string): string => label.replace(/^Weekly\b/, "weekly");

function warning(early: Drawn[], today: string | null, clock: Clock): Warning | null {
  if (early.length === 0) return null;
  const [first, second] = early;
  const full = first.full as number;
  const day = dayPart(full, today, clock);
  const lead = `${harnessName(first.harness)}'s ${inSentence(first.row.label)} limit fills `
    + `${day === null ? "" : `${day} `}at ${clock.time(full)}`;
  const span = first.resets === null ? null : spanWords(first.resets - full);
  const before = span === null ? null : `${span} before it resets`;
  let more: string | null = null;
  if (early.length === 2) {
    const whose = second.harness === first.harness ? "" : `${harnessName(second.harness)} `;
    more = `${whose}${inSentence(second.row.label)} fills ${whenWord(second.full as number, today, clock)}`;
  } else if (early.length > 2) {
    const next = second.full as number;
    const when = dayPart(next, today, clock) === null ? `at ${clock.time(next)}` : whenWord(next, today, clock);
    more = `${early.length - 1} more fill early, the next ${when}`;
  }
  const text = `${lead}${before === null ? "" : `, ${before}`}${more === null ? "" : ` · ${more}`}`;
  return { lead, before, more, text };
}

/**
 * The limits as drawn, at `now` (the page server's time, epoch seconds).
 *
 * - A window that is `unavailable` has no row, and a harness with no row has no group.
 * - In a group the rows that fill early come first, the soonest first, then the others
 *   in the answer's order. The group that fills soonest is first; groups in which
 *   nothing fills early follow as Claude, then Codex.
 * - Days are worded against the answer's `today`.
 */
export function limitsModel(limits: Limits | null, now: number, clock: Clock = LOCAL): LimitsModel {
  if (limits === null) return { warning: null, groups: [] };
  const today = limits.today;
  const all = limits.rows.map((row, place) => drawn(row, place, now, today, clock))
    .filter((item): item is Drawn => item !== null);
  const byHarness = new Map<string, Drawn[]>();
  for (const item of all) byHarness.set(item.harness, [...(byHarness.get(item.harness) ?? []), item]);
  const order = (a: Drawn, b: Drawn): number =>
    (a.full ?? Infinity) - (b.full ?? Infinity) || a.place - b.place;
  const groups = [...byHarness.entries()].map(([harness, rows]) => ({ harness, rows: rows.slice().sort(order) }));
  groups.sort((a, b) => soonest(a.rows) - soonest(b.rows) || rank(a.harness) - rank(b.harness)
    || a.rows[0].place - b.rows[0].place);
  const read = (at: number): string => `read ${whenWord(at, today, clock)}`;
  return {
    warning: warning(all.filter((item) => item.full !== null).sort(order), today, clock),
    groups: groups.map(({ harness, rows }) => {
      const times = rows.map((item) => item.observed).filter((at): at is number => at !== null);
      const heading = times.length ? read(Math.max(...times)) : null;
      return {
        harness,
        name: harnessName(harness),
        read: heading,
        rows: rows.map((item) => {
          const own = item.observed === null ? null : read(item.observed);
          return { ...item.row, read: own === heading ? null : own };
        }),
      };
    }),
  };
}
