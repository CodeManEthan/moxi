// The page's words for a time, a day, a span, a lifetime and a session's name, with no
// DOM. A day is worded against `today`, the page server's own date, which every answer
// the page words a time from carries: the same date is the time alone, the day before or
// after is `yesterday` or `tomorrow`, a day within six either side is its weekday, and
// any other is its date. The clock is passed in, so the Node tests read the same words in
// any zone and locale.

/** How the page reads a moment (epoch seconds): its time of day as shown, to the minute
 * and to the second, and its day. */
export interface Clock {
  /** The hour and minute, as ui shows a time. */
  time(at: number): string;
  /** The hour, minute and second. */
  seconds(at: number): string;
  /** The day, `YYYY-MM-DD`. */
  day(at: number): string;
}

const pad = (n: number): string => String(n).padStart(2, "0");

/** This browser's zone and its own way of writing a time. The page is read at the desk,
 * where the browser and the page server share a zone. */
export const LOCAL: Clock = {
  time: (at) => new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
  seconds: (at) => new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
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

/** Whether `day` is a day as the page's answers write one, `YYYY-MM-DD`. */
export function isDay(day: unknown): day is string {
  return typeof day === "string" && !Number.isNaN(stamp(day));
}

/** The later of two days, either of which may be missing: the newest `today` held. */
export function newestDay(...days: (string | null | undefined)[]): string | null {
  let newest: string | null = null;
  for (const day of days) if (isDay(day) && (newest === null || day > newest)) newest = day;
  return newest;
}

/** A date alone: `Oct 10`. */
export function dateWord(day: string): string {
  const at = stamp(day);
  if (Number.isNaN(at)) return day;
  const d = new Date(at);
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

/** A day against `today`: `today`, `yesterday`, `tomorrow`, the weekday within six days
 * either side (`Wed`), else the date (`Nov 3`). */
export function dayWord(day: string, today: string): string {
  const off = Math.round((stamp(day) - stamp(today)) / DAY_MS);
  if (Number.isNaN(off)) return dateWord(day);
  if (off === 0) return "today";
  if (off === -1) return "yesterday";
  if (off === 1) return "tomorrow";
  if (Math.abs(off) <= 6) return WEEKDAYS[new Date(stamp(day)).getUTCDay()];
  return dateWord(day);
}

/**
 * A moment in words: `21:34` on `today`'s date, else `tomorrow 00:10`, `Wed 23:58`,
 * `Nov 3 08:52`. With `named`, a moment on today's date reads `today 11:00`, as a
 * session's name carries it. With `exact`, the time is to the second. With no `today`
 * held the time stands alone, since no day can be said.
 */
export function whenWord(at: number, today: string | null, clock: Clock = LOCAL,
                         { named = false, exact = false }: { named?: boolean; exact?: boolean } = {}): string {
  const time = exact ? clock.seconds(at) : clock.time(at);
  if (today === null) return time;
  const day = clock.day(at);
  const part = day === today ? (named ? "today" : null) : dayWord(day, today);
  return part === null ? time : `${part} ${time}`;
}

const unit = (n: number, one: string): string => `${n} ${n === 1 ? one : `${one}s`}`;

/**
 * A length of time in whole units, by the chip's steps: seconds under a minute
 * (`45 seconds`), minutes under an hour, hours and minutes under a day (`1 hour 4
 * minutes`, and `2 hours` with no minutes over), then days. One of a unit is singular.
 * The seconds are rounded down; a caller that counts time left rounds up first.
 */
export function spanWords(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  if (total < 60) return unit(total, "second");
  if (total < 3600) return unit(Math.floor(total / 60), "minute");
  if (total < 86400) {
    const minutes = Math.floor((total % 3600) / 60);
    const hours = unit(Math.floor(total / 3600), "hour");
    return minutes ? `${hours} ${unit(minutes, "minute")}` : hours;
  }
  return unit(Math.floor(total / 86400), "day");
}

const AFTER = "after the last request";

/**
 * A clock's lifetime in words, from its `ttl`: 3600 is `one hour after the last
 * request`, 300 `five minutes …`, 60 `one minute …`, any other whole number of minutes
 * `N minutes …`, and any other value `N seconds …`. The record does not say why a
 * lifetime is what it is, so no cause is worded.
 */
export function lifetimeWords(ttl: number): string {
  if (ttl === 3600) return `one hour ${AFTER}`;
  if (ttl === 300) return `five minutes ${AFTER}`;
  if (ttl === 60) return `one minute ${AFTER}`;
  if (ttl > 0 && Number.isInteger(ttl / 60)) return `${ttl / 60} minutes ${AFTER}`;
  return `${ttl} ${ttl === 1 ? "second" : "seconds"} ${AFTER}`;
}

// --- names --------------------------------------------------------------------------------

/** Each harness as the Sessions page names one: `Claude Code`, `Codex`, `OpenCode`; one
 * the page does not know, as the record carries it. */
const HARNESS_WORDS: Record<string, string> = { claude: "Claude Code", codex: "Codex", opencode: "OpenCode" };

export function harnessWords(harness: string): string {
  return Object.hasOwn(HARNESS_WORDS, harness) ? HARNESS_WORDS[harness] : harness;
}

/** A folder's last segment: `proj-a` for `/work/proj-a/`. */
export function lastSegment(folder: string): string {
  const cut = folder.replace(/\/+$/, "");
  const last = cut.slice(cut.lastIndexOf("/") + 1);
  return last !== "" ? last : folder;
}

/**
 * A folder as it is drawn: one under the account's home folder starts with `~`
 * (`~/code/proj-a`, and `~` for the home folder itself). A folder that is not under
 * `home`, and any folder when `home` is not known, is shown as it is.
 */
export function folderWords(folder: string, home: string | null | undefined): string {
  if (typeof home !== "string") return folder;
  const base = home.replace(/\/+$/, "");
  if (base === "" || !base.startsWith("/")) return folder;
  if (folder === base || folder.replace(/\/+$/, "") === base) return "~";
  return folder.startsWith(`${base}/`) ? `~${folder.slice(base.length)}` : folder;
}

/** What a session is named from: the record's harness and what the names read gave. */
export interface Naming {
  harness: string;
  name: string | null;
  folder: string | null;
  started: number | null;
}

/** A row's first line: its name, and the dim `where` after it. */
export interface SessionName {
  name: string;
  /** The folder's last segment beside a typed name; null otherwise. */
  where: string | null;
}

/**
 * A session's name, by the Sessions page's rule. With a typed name: the name, and the
 * folder's last segment as `where`. Without one: `<folder's last segment> · started
 * <day> <time>`; with no start, the folder's last segment alone; with no folder,
 * `<harness> session` in its place. Never the id. Today's own start reads `today 11:00`.
 */
export function sessionName(ref: Naming, today: string | null, clock: Clock = LOCAL): SessionName {
  const folder = typeof ref.folder === "string" && ref.folder.trim() !== "" ? lastSegment(ref.folder.trim()) : null;
  const typed = typeof ref.name === "string" ? ref.name.replace(/\s+/g, " ").trim() : "";
  if (typed !== "") return { name: typed, where: folder };
  const what = folder ?? `${harnessWords(ref.harness)} session`;
  if (typeof ref.started !== "number" || !Number.isFinite(ref.started)) return { name: what, where: null };
  return { name: `${what} · started ${whenWord(ref.started, today, clock, { named: true })}`, where: null };
}
