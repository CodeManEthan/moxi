// The page's words for an event, a piece, a repo's name, a count and a time, with no DOM
// so the Node tests read them. A day is worded against `today`, the page server's own
// date: the same date is the time alone, the day before or after is `yesterday` or
// `tomorrow`, a day within six either side is its weekday, and any other is its date. The
// clock is passed in, so the tests read the same words in any zone and locale.

import type { RepoRow } from "./feed.ts";

/** git's events in the order a repo's pieces are listed; any other comes after, by name. */
export const GIT_ORDER = [
  "pre-commit", "prepare-commit-msg", "commit-msg", "post-commit", "pre-merge-commit", "post-merge",
  "pre-rebase", "post-checkout", "post-rewrite", "pre-push",
] as const;

/** Each event as the opened repo labels its row. */
const EVENT_LABELS: Readonly<Record<string, string>> = {
  "pre-commit": "Before a commit",
  "prepare-commit-msg": "Writing the message",
  "commit-msg": "On the message",
  "post-commit": "After a commit",
  "pre-merge-commit": "Before a merge commit",
  "post-merge": "After a merge or pull",
  "pre-rebase": "Before a rebase",
  "post-checkout": "After a checkout",
  "post-rewrite": "After a rewrite",
  "pre-push": "Before a push",
};

/** The event as the opened repo labels it: `Before a commit`; any other by git's name. */
export function eventLabel(event: string): string {
  return Object.hasOwn(EVENT_LABELS, event) ? EVENT_LABELS[event] : event;
}

/** The event inside a sentence: `before a commit`, `on the message`; any other by git's
 * name. */
export function eventWords(event: string): string {
  if (!Object.hasOwn(EVENT_LABELS, event)) return event;
  const label = EVENT_LABELS[event];
  return label[0].toLowerCase() + label.slice(1);
}

/** The events whose exit status git honours though their name doesn't start `pre-`. */
const REFUSING = new Set(["prepare-commit-msg", "commit-msg", "applypatch-msg", "reference-transaction",
                          "sendemail-validate"]);

/** Whether git refuses what the event is for when its hook fails: every `pre-` event and
 * the message events, as repo's git hook runner reads it. */
export function canRefuse(event: string): boolean {
  return event.startsWith("pre-") || REFUSING.has(event);
}

/** What an event that can refuse refuses: `commit`, `merge`, `push`; git's name for any
 * other. */
const REFUSE_WORDS: Readonly<Record<string, string>> = {
  "pre-commit": "commit",
  "prepare-commit-msg": "commit",
  "commit-msg": "commit",
  "pre-merge-commit": "merge",
  "pre-push": "push",
  "pre-rebase": "rebase",
  "pre-applypatch": "patch",
  "applypatch-msg": "patch",
  "pre-auto-gc": "automatic gc",
  "reference-transaction": "ref update",
  "sendemail-validate": "email",
};

/** The word after `refuses every`: `commit` for pre-commit; git's name for an event the
 * table doesn't hold. */
export function refuseWord(event: string): string {
  return Object.hasOwn(REFUSE_WORDS, event) ? REFUSE_WORDS[event] : event;
}

/** Where an event stands in `GIT_ORDER`; any other after every one of them. */
export function eventRank(event: string): number {
  const at = (GIT_ORDER as readonly string[]).indexOf(event);
  return at < 0 ? GIT_ORDER.length : at;
}

/** Events in run order: `GIT_ORDER`, then the rest by name. */
export function byGitOrder(a: string, b: string): number {
  return eventRank(a) - eventRank(b) || (a < b ? -1 : a > b ? 1 : 0);
}

/** A piece's name: the part of its id after the first `/`, or the id when it has none. */
export function pieceName(id: string): string {
  const at = id.indexOf("/");
  return at < 0 ? id : id.slice(at + 1);
}

/** A text's first line. */
export function firstLine(text: string): string {
  const at = text.indexOf("\n");
  return at < 0 ? text : text.slice(0, at);
}

/** `A`, `A and B`, `A, B and C`. */
export function joinAnd(words: readonly string[]): string {
  if (words.length <= 1) return words[0] ?? "";
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

/** `1 repo`, `3 repos`. */
export function counted(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

function isWindowsHome(home: string): boolean {
  return /^[A-Za-z]:/.test(home) || home.startsWith("\\\\");
}

/**
 * A path as the page writes it: `~` when it is `home`, `~/rest` (`~\rest`) when it
 * continues `home` after a separator, else in full; in full too with no `home`. A Windows
 * home is compared without case, and takes either separator; any other takes `/` and is
 * compared as it is. `/home/jp2` is not under `/home/jp`.
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

/** A repo's name on the page: its project's key when it has one, else its root in `~`
 * form. */
export function repoName(repo: Pick<RepoRow, "project" | "root">, home: string | null): string {
  return repo.project ?? homeForm(repo.root, home);
}

/** How the page reads a moment (epoch seconds): its time of day as ui shows one, and its
 * day. */
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

/** A moment in words against `today`: `19:09` on that date, else `yesterday 22:10`, `Fri
 * 22:10`, `Oct 10 08:52`. With no `today`, the time alone. */
export function whenWord(at: number, today: string | null, clock: Clock = LOCAL): string {
  const time = clock.time(at);
  if (today === null) return time;
  const day = clock.day(at);
  return day === today ? time : `${dayWord(day, today)} ${time}`;
}

/** A record's `at` (`YYYY-MM-DDTHH:MM:SSZ`, UTC) in epoch seconds; null when it isn't one. */
export function recordSeconds(at: string | null): number | null {
  if (at === null) return null;
  const found = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z$/.exec(at);
  if (found === null) return null;
  const [, y, mo, d, h, mi, s] = found.map(Number);
  const ms = Date.UTC(y, mo - 1, d, h, mi, s);
  const back = new Date(ms);
  if (back.getUTCMonth() !== mo - 1 || back.getUTCDate() !== d || h > 23 || mi > 59 || s > 59) return null;
  return ms / 1000;
}
