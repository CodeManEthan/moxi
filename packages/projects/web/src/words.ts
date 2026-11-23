// The page's words for a time, a root and a kind, with no DOM so the Node tests read them.
// A day is worded against the browser's today: the same date is the time alone, the day
// before or after is `yesterday` or `tomorrow`, a day within six either side is its
// weekday, and any other is its date. The clock is passed in, so the tests read the same
// words in any zone and locale.

/** How the page reads a moment (epoch seconds): its time of day as ui shows one, and its day. */
export interface Clock {
  /** The hour and minute. */
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

/** A date alone: `Oct 10`; the day as given when it is not a day. */
export function dateWord(day: string): string {
  const at = stamp(day);
  if (Number.isNaN(at)) return day;
  const d = new Date(at);
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

/** A day against `today`: `today`, `yesterday`, `tomorrow`, the weekday within six days
 * either side (`Fri`), else the date (`Oct 10`). */
export function dayWord(day: string, today: string): string {
  const off = Math.round((stamp(day) - stamp(today)) / DAY_MS);
  if (Number.isNaN(off)) return dateWord(day);
  if (off === 0) return "today";
  if (off === -1) return "yesterday";
  if (off === 1) return "tomorrow";
  if (Math.abs(off) <= 6) return WEEKDAYS[new Date(stamp(day)).getUTCDay()];
  return dateWord(day);
}

/** A moment in words: `19:09` on today's date, else `yesterday 22:10`, `Fri 22:10`,
 * `Oct 10 08:52`. */
export function whenWord(at: number, now: number, clock: Clock = LOCAL): string {
  const time = clock.time(at);
  const day = clock.day(at);
  const today = clock.day(now);
  return day === today ? time : `${dayWord(day, today)} ${time}`;
}

/** Line one's fact for the newest activity: `last active 19:09`, `last active Fri 22:10`. */
export function lastActive(at: number, now: number, clock: Clock = LOCAL): string {
  return `last active ${whenWord(at, now, clock)}`;
}

/** Whether `home` is a Windows path: a drive letter, or a `\\` start. */
function isWindowsHome(home: string): boolean {
  return /^[A-Za-z]:/.test(home) || home.startsWith("\\\\");
}

/**
 * A path as the page writes it: `~` when it is `home`, `~/rest` (`~\rest`) when it
 * continues `home` after a separator, else in full; in full too with no `home`. A Windows
 * home is compared without case, and takes either separator; any other takes `/` and is
 * compared as it is. `/home/you2` is not under `/home/you`.
 */
export function homeForm(path: string, home: string | null): string {
  if (home === null || home === "") return path;
  const windows = isWindowsHome(home);
  const seps = windows ? /[\\/]+$/ : /\/+$/;
  const base = home.replace(seps, "");
  if (base === "") return path;
  const same = (a: string, b: string): boolean => (windows ? a.toLowerCase() === b.toLowerCase() : a === b);
  if (path.length < base.length || !same(path.slice(0, base.length), base)) return path;
  const rest = path.slice(base.length);
  if (rest === "" || (windows ? /^[\\/]+$/ : /^\/+$/).test(rest)) return "~";
  const sep = rest[0];
  if (sep !== "/" && !(windows && sep === "\\")) return path;
  return `~${rest}`;
}

/** The kind as line one names it after the root, or null for a code project. */
export function kindWord(kind: string): string | null {
  if (kind === "project") return null;
  if (kind === "agent") return "agent's home";
  return kind;
}

/** The kind as a kind menu names it: `code project`, `agent's home`, else its name. */
export function kindName(kind: string): string {
  if (kind === "project") return "code project";
  return kindWord(kind) as string;
}

/** The kind as the opened project's `Folder` row names it. */
export function kindPhrase(kind: string): string {
  if (kind === "project") return "a code project";
  if (kind === "agent") return "an agent's home";
  return kind;
}
