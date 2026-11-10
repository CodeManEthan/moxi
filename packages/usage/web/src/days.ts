// The page's words for a day and a time. A day is worded against `today`, the page
// server's own date, which every answer the page words a time from carries: the same
// date is the time alone, the day before or after is `yesterday` or `tomorrow`, a day
// within six either side is its weekday, and any other is its date. No DOM here; the
// clock is passed in, so the Node tests read the same words in any zone and locale.

/** How the page reads a moment (epoch seconds): its time of day as shown, and its day. */
export interface Clock {
  /** The hour and minute, as ui shows a time. */
  time(at: number): string;
  /** The day, `YYYY-MM-DD`. */
  day(at: number): string;
}

const pad = (n: number): string => String(n).padStart(2, "0");

/** This browser's zone and its own way of writing a time. */
export const LOCAL: Clock = {
  time: (at) => new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
  day: (at) => {
    const d = new Date(at * 1000);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  },
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const DAY_MS = 86_400_000;

/** A day as milliseconds at its UTC midnight; NaN when it is not `YYYY-MM-DD`. */
function stamp(day: string): number {
  return /^\d{4}-\d{2}-\d{2}$/.test(day) ? Date.parse(`${day}T00:00:00Z`) : NaN;
}

/** An ISO 8601 time as epoch seconds, or null. */
export function seconds(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms / 1000 : null;
}

/** `day` moved by `n` days. */
export function addDays(day: string, n: number): string {
  return new Date(stamp(day) + n * DAY_MS).toISOString().slice(0, 10);
}

/** How many days `day` is after `from`; negative when it is before. */
export function daysFrom(from: string, day: string): number {
  return Math.round((stamp(day) - stamp(from)) / DAY_MS);
}

/** Every day from `since` to `until`, both included, oldest first. */
export function daysOf(since: string, until: string): string[] {
  const count = daysFrom(since, until) + 1;
  return Array.from({ length: Math.max(0, count) }, (_, i) => addDays(since, i));
}

/** A date alone: `Oct 10`. */
export function dateWord(day: string): string {
  const at = stamp(day);
  if (Number.isNaN(at)) return day;
  const d = new Date(at);
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

/** A month (`YYYY-MM`) alone: `Oct 2026`. */
export function monthWord(month: string): string {
  const found = /^(\d{4})-(\d{2})$/.exec(month);
  const name = found === null ? undefined : MONTHS[Number(found[2]) - 1];
  return found === null || name === undefined ? month : `${name} ${found[1]}`;
}

/** A day against `today`: `today`, `yesterday`, `tomorrow`, the weekday within six days
 * either side (`Wed`), else the date (`Nov 3`). */
export function dayWord(day: string, today: string): string {
  const off = daysFrom(today, day);
  if (Number.isNaN(off)) return dateWord(day);
  if (off === 0) return "today";
  if (off === -1) return "yesterday";
  if (off === 1) return "tomorrow";
  if (Math.abs(off) <= 6) return WEEKDAYS[new Date(stamp(day)).getUTCDay()];
  return dateWord(day);
}

/** A moment's day as its words carry it: null on `today`'s date, where the time stands
 * alone, and when no `today` is held; `dayWord` otherwise. */
export function dayPart(at: number, today: string | null, clock: Clock = LOCAL): string | null {
  if (today === null) return null;
  const day = clock.day(at);
  return day === today ? null : dayWord(day, today);
}

/**
 * A moment in words: `21:34` on `today`'s date, else `tomorrow 17:51`, `Wed 23:58`,
 * `Nov 3 08:52`. With `named`, a moment on today's date reads `today 11:00`: for a line
 * or list where another day's time stands beside it. With no `today` held the time
 * stands alone, since no day can be said.
 */
export function whenWord(at: number, today: string | null, clock: Clock = LOCAL, named = false): string {
  const time = clock.time(at);
  if (today === null) return time;
  const part = dayPart(at, today, clock) ?? (named ? "today" : null);
  return part === null ? time : `${part} ${time}`;
}
