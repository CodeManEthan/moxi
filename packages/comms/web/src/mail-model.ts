// What comms' row on Home says, worked out with no DOM so the Node tests can read it: the
// answer of `/api/mail` read and checked, and the row's line for Home. The clock is passed
// in, so the tests read the same words in any zone and locale. The line holds names, a
// count and a time, never a message's text.

/** One name with mail waiting that no live session holds; `since` is the oldest
 * message's time, in epoch seconds. */
export interface MailName {
  qualified: string;
  name: string;
  count: number;
  since: number;
}

/** The answer of `/api/mail`: `state` is `up`, `offline` or `error`; `waiting` is oldest
 * `since` first. */
export interface Mail {
  state: string;
  waiting: MailName[];
}

/** The row's line, as the `detail` of a `moxi-home-row` event. */
export interface MailLine {
  parts: Array<{ text: string; heavy?: boolean }>;
  time?: string;
  needs?: number;
}

/** How the row reads a moment (epoch seconds): its time of day as shown, and its day. */
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

/** On Home's needs scale: something waits for the user. */
export const WAITS = 20;
/** The most names the row spells out; past it, the first two and how many more. */
export const NAMED = 3;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const DAY_MS = 86_400_000;

/** A day (`YYYY-MM-DD`) as milliseconds at its UTC midnight; NaN when it is not one. */
function stamp(day: string): number {
  return /^\d{4}-\d{2}-\d{2}$/.test(day) ? Date.parse(`${day}T00:00:00Z`) : NaN;
}

const isName = (data: any): data is MailName =>
  typeof data === "object" && data !== null && typeof data.qualified === "string"
  && typeof data.name === "string" && data.name !== ""
  && Number.isInteger(data.count) && data.count > 0
  && typeof data.since === "number" && Number.isFinite(data.since);

/** The answer as the route sends it, else null. A name the row cannot say is left out. */
export function parseMail(data: unknown): Mail | null {
  if (typeof data !== "object" || data === null) return null;
  const { state, waiting } = data as Record<string, any>;
  if (typeof state !== "string" || !Array.isArray(waiting)) return null;
  return { state, waiting: waiting.filter(isName).sort((a: MailName, b: MailName) => a.since - b.since) };
}

/** `a`, `a and b`, `a, b and c`, then `a, b and n more` past three names. */
export function namesWords(names: string[]): string {
  if (names.length <= 1) return names.join("");
  if (names.length > NAMED) return `${names.slice(0, NAMED - 1).join(", ")} and ${names.length - NAMED + 1} more`;
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** When the oldest message came, against the day of `now`: `14:02` today, its weekday
 * within the six days before (`Wed 14:02`), else its date (`Nov 3 14:02`). */
export function sinceWords(at: number, now: number, clock: Clock = LOCAL): string {
  const day = clock.day(at);
  const time = clock.time(at);
  const back = Math.round((stamp(clock.day(now)) - stamp(day)) / DAY_MS);
  if (back === 0 || Number.isNaN(back)) return time;
  const d = new Date(stamp(day));
  if (back > 0 && back <= 6) return `${WEEKDAYS[d.getUTCDay()]} ${time}`;
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()} ${time}`;
}

/**
 * The Comms row's line; null when nothing waits, and when the daemon is offline or
 * answered with an error, which Health reports.
 *
 * `3 messages waiting` (heavy) then ` · curator and scout aren't running`, with `since
 * 14:02`, the oldest message's time, at the right; the row needs the user (20).
 */
export function mailLine(value: Mail | null, now: number, clock: Clock = LOCAL): MailLine | null {
  if (value === null || value.state !== "up" || value.waiting.length === 0) return null;
  const count = value.waiting.reduce((sum, name) => sum + name.count, 0);
  const names = value.waiting.map((name) => name.name);
  const oldest = Math.min(...value.waiting.map((name) => name.since));
  return {
    parts: [{ text: count === 1 ? "1 message waiting" : `${count} messages waiting`, heavy: true },
            { text: ` · ${namesWords(names)} ${names.length === 1 ? "isn't" : "aren't"} running` }],
    time: `since ${sinceWords(oldest, now, clock)}`,
    needs: WAITS,
  };
}
