// The Cache page worked out with no DOM, no Lit and no request, so the Node tests read it.
// From the clocks answer, the names answer and the time on the server's clock, it says
// what each piece of the page shows; a piece that is not drawn is null or absent, and the
// view places what it is given and decides nothing.
//
// - The clocks answer: its `today` and the watcher's `reading`, beside the chip's parse.
// - The rows: which are timed, when each goes cold, the two groups and their order, and
//   the fact of a row that is not timed. Groups, counts, states and the Watcher row are
//   worked out from the clocks answer alone; only whether a row is drawn yet waits on its
//   name.
// - The watcher: one total function of the reading, the records and the time.
// - The names: `/api/names`, asked one request at a time, and when each row is drawn.
// - An opened row's lines, and the page's last lines.
//
// Every string here goes into the page as text.

import type { ApiResult } from "/m/ui/client.js";

import type { ClockRow } from "./band.ts";
import { parseClocks } from "./feed.ts";
import type { Clocks, Failure } from "./feed.ts";
import { folderWords, harnessWords, isDay, LOCAL, lifetimeWords, newestDay, sessionName, spanWords,
         whenWord } from "./words.ts";
import type { Clock } from "./words.ts";

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

// --- the clocks answer --------------------------------------------------------------------

/** The watcher's reading: when it last ticked, when it last listed Claude Code's live
 * sessions (null until it has), and the `stale_after` it writes into its records. */
export interface WatchReading {
  tick_at: number;
  listed_at: number | null;
  stale_after: number;
}

/** `/api/clocks` (without `now`) and the topic `clocks`, as the page reads them. */
export interface PageClocks extends Clocks {
  /** The page server's local date, `YYYY-MM-DD`; null when the answer carried none. */
  today: string | null;
  reading: WatchReading | null;
}

/** The reading in an answer, or null when there is none or it is not one. */
export function parseReading(data: unknown): WatchReading | null {
  if (typeof data !== "object" || data === null) return null;
  const { tick_at, listed_at, stale_after } = data as Record<string, unknown>;
  if (!finite(tick_at) || !finite(stale_after)) return null;
  if (listed_at !== null && !finite(listed_at)) return null;
  return { tick_at, listed_at, stale_after };
}

/** The page's value of the route and the topic, or null when it isn't one. */
export function parsePageClocks(data: unknown): PageClocks | null {
  const value = parseClocks(data);
  if (value === null) return null;
  const { today, reading } = data as Record<string, unknown>;
  return {
    ...value,
    today: isDay(today) ? today : null,
    reading: value.state === "up" ? parseReading(reading) : null,
  };
}

/** The last answer that was up, and the server's time it was taken at. */
export interface Kept {
  value: PageClocks;
  at: number;
}

/** What the page holds of its feed. */
export interface PageFeed {
  loading: boolean;
  gone: boolean;
  failure: Failure | null;
  value: PageClocks | null;
}

/**
 * The answer the page draws from: the feed's value while it is up and read, else the last
 * one that was. A failed read and the page server's own error keep the rows last read.
 */
export function keep(prev: Kept | null, feed: PageFeed, now: number | null): Kept | null {
  const value = feed.value;
  if (feed.failure !== null || feed.gone || value === null || value.state !== "up" || now === null) return prev;
  return prev !== null && prev.value === value ? prev : { value, at: now };
}

// --- the rows -----------------------------------------------------------------------------

/** A row's key: its harness and session. */
export const keyOf = (row: { harness: string; session: string }): string => `${row.harness}/${row.session}`;

/** A record with a clock time: `warm` or `cold`, and `last_request`, `ttl`, `warn_before`
 * and `stale_after` all finite. The chip's rule, with its `near` folded into warm. */
export function timed(row: ClockRow): boolean {
  return (row.kind === "warm" || row.kind === "cold") && finite(row.last_request) && finite(row.ttl)
    && finite(row.warn_before) && finite(row.stale_after);
}

/** A timed record's cold time, `last_request + ttl`; null for one that is not timed. */
export function coldAt(row: ClockRow): number | null {
  return timed(row) ? (row.last_request as number) + (row.ttl as number) : null;
}

/** A timed record is cold from its cold time on, on the server's clock `now`. */
export function isCold(row: ClockRow, now: number): boolean {
  if (!timed(row)) return false;
  return Math.max(0, now - (row.last_request as number)) >= (row.ttl as number);
}

const bySession = (a: ClockRow, b: ClockRow): number =>
  (a.session < b.session ? -1 : a.session > b.session ? 1 : 0) || (a.harness < b.harness ? -1 : a.harness > b.harness ? 1 : 0);

/** The records cut into the two groups and the rest. */
export interface Sorted {
  /** Soonest cold time first. */
  warm: ClockRow[];
  /** The most recently cold first. */
  cold: ClockRow[];
  /** The records with no clock time, by session id. */
  untimed: ClockRow[];
}

/** The records at `now`: warm rows soonest cold first, cold rows the most recently cold
 * first, ties in either by session id; the rest by session id. */
export function sortRows(rows: readonly ClockRow[], now: number): Sorted {
  const warm: ClockRow[] = [];
  const cold: ClockRow[] = [];
  const untimed: ClockRow[] = [];
  for (const row of rows) {
    if (!timed(row)) untimed.push(row);
    else (isCold(row, now) ? cold : warm).push(row);
  }
  const at = (row: ClockRow): number => coldAt(row) as number;
  warm.sort((a, b) => at(a) - at(b) || bySession(a, b));
  cold.sort((a, b) => at(b) - at(a) || bySession(a, b));
  untimed.sort(bySession);
  return { warm, cold, untimed };
}

/** The fact of a record with no clock time: what its kind says, and nothing more. */
export function untimedFact(row: ClockRow): string {
  switch (row.kind) {
    case "fresh": return "no request yet";
    case "no-cache": return "no cache";
    case "unknown": return "not read";
    default: return "no reading";
  }
}

/** A record is old when it was last seen longer ago than its own `stale_after`; a record
 * with no finite `stale_after` is never judged old. */
export function isOld(row: ClockRow, now: number): boolean {
  return finite(row.stale_after) && finite(row.seen_at) && now - row.seen_at > row.stale_after;
}

/** The countdown on an opened timed row: `in 41 minutes` while warm, time left rounded
 * up to the second; `12 minutes ago` once cold, time since rounded down. */
export function countdown(row: ClockRow, now: number): string | null {
  if (!timed(row)) return null;
  const ttl = row.ttl as number;
  const age = Math.max(0, now - (row.last_request as number));
  return age >= ttl ? `${spanWords(age - ttl)} ago` : `in ${spanWords(Math.ceil(ttl - age))}`;
}

// --- the watcher --------------------------------------------------------------------------

export type Outcome = "fresh" | "not-reading" | "stopped" | "no-reading";

/** How the watcher stands, and the two times the page words it with. */
export interface Watching {
  outcome: Outcome;
  /** The newer of the reading's `listed_at` and the newest `seen_at` among the records
   * with a finite `stale_after`; null when neither is there. */
  at: number | null;
  /** The newer of the reading's `tick_at` and `at`: the last moment a watcher ran. */
  last: number | null;
}

/**
 * Judges a watcher by the newest evidence, never by one record. `at` takes the newer of
 * the reading's `listed_at` and the records' `seen_at` (a record with no finite
 * `stale_after` takes no part), with the `stale_after` that came with the one taken; on
 * equal times, the larger. The first that fits:
 *
 * 1. `fresh`: `at` is not null and `now - at <= stale`;
 * 2. `not-reading`: there is a reading and `now - tick_at <= stale_after`, both its own;
 * 3. `stopped`: `last` is not null;
 * 4. `no-reading`: no reading, and no record that takes part.
 */
export function watching(reading: WatchReading | null, rows: readonly ClockRow[], now: number): Watching {
  let newest: [number, number] | null = null;
  const take = (time: number, stale: number): void => {
    if (newest === null || time > newest[0] || (time === newest[0] && stale > newest[1])) newest = [time, stale];
  };
  if (reading !== null && reading.listed_at !== null) take(reading.listed_at, reading.stale_after);
  for (const row of rows) if (finite(row.seen_at) && finite(row.stale_after)) take(row.seen_at, row.stale_after);
  const found = newest as [number, number] | null;
  const at = found === null ? null : found[0];
  const ticking = reading !== null && now - reading.tick_at <= reading.stale_after;
  const times = [reading?.tick_at ?? null, at].filter((time): time is number => time !== null);
  const last = times.length ? Math.max(...times) : null;
  let outcome: Outcome;
  if (found !== null && now - found[0] <= found[1]) outcome = "fresh";
  else if (ticking) outcome = "not-reading";
  else if (last !== null) outcome = "stopped";
  else outcome = "no-reading";
  return { outcome, at, last };
}

/** The command the Watcher row's `Copy` writes. */
export const WATCH_COMMAND = "moxi cache watch";
export const NOT_READING_LINE = "It is running but could not list Claude's sessions; its log says why.";
export const STOPPED_LINE = "These times were right then. A session that has worked since is warmer than it "
  + "reads here. Start it with moxi cache watch.";
export const NO_READING_LINE = "The watcher reads live Claude Code sessions. Start it with moxi cache watch, "
  + "or moxi install cache, which keeps it running.";

/** The row above the groups when the watcher is not reading: `Watcher · <what>`, its
 * sentence, and the command its `Copy` writes when it has one. */
export interface WatcherRow {
  what: string;
  line: string;
  copy: string | null;
}

/** The Watcher row for an outcome; null when the reading is fresh. */
export function watcherRow(found: Watching, today: string | null, clock: Clock = LOCAL): WatcherRow | null {
  const when = (at: number): string => whenWord(at, today, clock);
  switch (found.outcome) {
    case "fresh":
      return null;
    case "not-reading":
      return { what: found.at === null ? "Not reading" : `Not reading since ${when(found.at)}`,
               line: NOT_READING_LINE, copy: null };
    case "stopped":
      return { what: `Stopped at ${when(found.last as number)}`, line: STOPPED_LINE, copy: WATCH_COMMAND };
    default:
      return { what: "No reading yet", line: NO_READING_LINE, copy: WATCH_COMMAND };
  }
}

// --- the names ----------------------------------------------------------------------------

/** One session of `/api/names`. */
export interface NameItem {
  session: string;
  harness: string;
  name: string | null;
  folder: string | null;
  started: number | null;
  /** True when sessions holds it; false when it holds none and its harness's root was
   * listed whole; null when the root was not. */
  known: boolean | null;
}

/** `/api/names`: `up`, or `error` with no sessions when the read raised. */
export interface NamesAnswer {
  state: "up" | "error";
  today: string | null;
  home: string | null;
  sessions: NameItem[];
}

const text = (value: unknown): string | null => (typeof value === "string" ? value : null);

/** `/api/names`' value, or null when it isn't one. */
export function parseNames(data: unknown): NamesAnswer | null {
  if (typeof data !== "object" || data === null) return null;
  const { state, today, home, sessions } = data as Record<string, unknown>;
  const day = isDay(today) ? today : null;
  if (state === "error") return { state, today: day, home: text(home), sessions: [] };
  if (state !== "up" || !Array.isArray(sessions)) return null;
  const items: NameItem[] = [];
  for (const item of sessions) {
    if (typeof item !== "object" || item === null) continue;
    const { session, harness, name, folder, started, known } = item as Record<string, unknown>;
    if (typeof session !== "string" || typeof harness !== "string") continue;
    items.push({ session, harness, name: text(name), folder: text(folder),
                 started: finite(started) ? started : null,
                 known: typeof known === "boolean" ? known : null });
  }
  return { state, today: day, home: text(home), sessions: items };
}

/** How often the names are asked again while the page is visible, in milliseconds. */
export const NAMES_EVERY_MS = 60_000;

/** The set of sessions in the records, as one string to compare. */
export function sessionSet(rows: readonly ClockRow[]): string {
  return rows.map(keyOf).sort().join("\n");
}

/**
 * `/api/names`, asked one request at a time. `ask()` while a request is out asks once
 * more when it ends. `clocks(rows)` asks when the set of sessions changes from the one
 * the last clocks answer held (the first set is the page's opening, which asks for
 * itself). `onChange` is called after every answer or failure.
 *
 * An `error` answer replaces the answer before it, so every row is named from nothing; a
 * request that gives no readable answer keeps it, and is `failed` until the next one
 * reads.
 */
export class NamesFeed {
  /** The last readable answer, or null before the first. */
  answer: NamesAnswer | null = null;
  /** The last request gave no readable answer. */
  failed = false;

  #read: () => Promise<ApiResult>;
  #onChange: () => void;
  #running: Promise<void> | null = null;
  #again = false;
  #closed = false;
  #set: string | null = null;

  constructor(read: () => Promise<ApiResult>, onChange: () => void) {
    this.#read = read;
    this.#onChange = onChange;
  }

  /** Stops every change: a later answer is dropped. */
  close(): void {
    this.#closed = true;
  }

  /** The set of sessions of a clocks answer or event. */
  clocks(rows: readonly ClockRow[]): void {
    const set = sessionSet(rows);
    const changed = this.#set !== null && this.#set !== set;
    this.#set = set;
    if (changed) void this.ask();
  }

  /** Asks the names; a call while a request is out asks once more after it. */
  ask(): Promise<void> {
    if (this.#running) {
      this.#again = true;
      return this.#running;
    }
    this.#running = (async () => {
      try {
        do {
          this.#again = false;
          await this.#once();
        } while (this.#again && !this.#closed);
      } finally {
        this.#running = null;
      }
    })();
    return this.#running;
  }

  async #once(): Promise<void> {
    if (this.#closed) return;
    let answer: ApiResult;
    try {
      answer = await this.#read();
    } catch {
      answer = { status: 0, data: null };
    }
    if (this.#closed) return;
    const value = answer.status === 200 ? parseNames(answer.data) : null;
    if (value !== null) {
      this.answer = value;
      this.failed = false;
    } else {
      this.failed = true;
    }
    this.#onChange();
  }
}

/** The names the latest answer gives, by row key: only a session sessions holds. */
export function namesBy(answer: NamesAnswer | null): Map<string, NameItem> {
  const found = new Map<string, NameItem>();
  if (answer === null || answer.state !== "up") return found;
  for (const item of answer.sessions) if (item.known === true) found.set(keyOf(item), item);
  return found;
}

/** What a row is named from: the names read's parts for a session it holds, else three
 * nulls and the record's harness. Never the id. */
export function namingOf(row: ClockRow, names: Map<string, NameItem>): { harness: string; name: string | null;
                                                                          folder: string | null; started: number | null } {
  const item = names.get(keyOf(row));
  return { harness: row.harness, name: item?.name ?? null, folder: item?.folder ?? null, started: item?.started ?? null };
}

/** How long a row waits for a names answer that holds it before it is drawn without. */
export const NAME_WAIT_MS = 2000;

/**
 * When each row is drawn, one rule for the first draw and for a session that arrives
 * later by the topic: once a names answer that holds it has come, or `NAME_WAIT_MS` after
 * the clocks answer or event that first carried it, whichever is first. A row once drawn
 * stays drawn while its record is there. Times are this browser's, in milliseconds.
 */
export class Arrivals {
  #first = new Map<string, number>();
  #held = new Set<string>();

  /** A clocks answer or event came at `nowMs`. A session that left is forgotten. */
  clocks(rows: readonly ClockRow[], nowMs: number): void {
    const keys = new Set(rows.map(keyOf));
    for (const key of [...this.#first.keys()]) if (!keys.has(key)) this.#first.delete(key);
    for (const key of keys) if (!this.#first.has(key)) this.#first.set(key, nowMs);
  }

  /** A names answer came: every session it holds an item for is drawn from now on. */
  names(answer: NamesAnswer | null): void {
    if (answer === null || answer.state !== "up") return;
    for (const item of answer.sessions) this.#held.add(keyOf(item));
  }

  /** Whether the row for `key` is drawn at `nowMs`. */
  shown(key: string, nowMs: number): boolean {
    if (this.#held.has(key)) return true;
    const first = this.#first.get(key);
    return first !== undefined && nowMs - first >= NAME_WAIT_MS;
  }

  /** Milliseconds until the next row still waiting is drawn; null when none waits. */
  due(nowMs: number): number | null {
    let soonest: number | null = null;
    for (const [key, first] of this.#first) {
      if (this.#held.has(key) || nowMs - first >= NAME_WAIT_MS) continue;
      const left = first + NAME_WAIT_MS - nowMs;
      if (soonest === null || left < soonest) soonest = left;
    }
    return soonest;
  }
}

// --- the page -----------------------------------------------------------------------------

/** One row as the row element takes it. A null piece is not drawn. */
export interface RowModel {
  key: string;
  session: string;
  harness: string;
  name: string;
  /** The folder's last segment after a typed name, dim. */
  where: string | null;
  /** `warm until 19:52`, `cold since 18:35`, or the fact of a row with no clock time. */
  fact: string;
  /** The record's code, shown dim after the fact of a row with no clock time. */
  code: string | null;
  /** Line two: `last request 18:52`; null on a row with no clock time. */
  rest: string | null;
  open: boolean;
}

/** A group: `4 warm` or `1 cold`, counted from the clocks answer, and its rows drawn so far. */
export interface GroupModel {
  kind: "warm" | "cold";
  label: string;
  count: number;
  rows: RowModel[];
}

/** The line at the foot of the list for the records with no clock time. */
export interface UntimedModel {
  /** `2 with no cache reading`. */
  line: string;
  count: number;
  act: "Show" | "Hide";
  /** The rows drawn so far while shown; none while hidden. */
  rows: RowModel[];
}

/** One of the page's last lines: dim, or in the text colour. */
export interface LastLine {
  text: string;
  dim: boolean;
}

/** The foot: `Last reading <at>` when the reading is fresh, and `Cache settings`. */
export interface FootModel {
  reading: string | null;
}

/** The page as it is drawn. */
export interface PageModel {
  /** `waiting` before the first answer, `gone` after a 404 (nothing is drawn), `failed`
   * with no answer read yet (the last lines alone), else `up`. */
  state: "waiting" | "gone" | "failed" | "up";
  /** While waiting: `Reading…` once `WAITING_MS` have gone by; null before. */
  waiting: string | null;
  watcher: WatcherRow | null;
  groups: GroupModel[];
  untimed: UntimedModel | null;
  /** The empty state's sentences, or the one on what a cache is. */
  sentences: string[];
  foot: FootModel | null;
  last: LastLine[];
}

/** Nothing under the top line for this long, then `Reading…`, in milliseconds. */
export const WAITING_MS = 400;
export const WAITING_LINE = "Reading…";
export const EMPTY_LINE = "No Claude Code session has a cache yet.";
export const CACHE_LINE = "A session shows here from its first request until it ends. Its cache keeps what it "
  + "has read ready for an hour after each request, or five minutes on an API key; a turn sent after that "
  + "reads everything again at full price.";
export const NAMES_FAILED = "Moxi couldn't read these sessions' names";
export const SERVER_ERROR_LINE = "cache's page server hit an error";

/** What the page is worked out from. Times on the server's clock are epoch seconds;
 * `nowMs` and `waitedMs` are this browser's, in milliseconds. */
export interface PageInput {
  feed: PageFeed;
  /** `keep(...)`'s answer: what the rows are drawn from. */
  kept: Kept | null;
  /** The server's time now; null before the first read. */
  now: number | null;
  names: { answer: NamesAnswer | null; failed: boolean };
  arrivals: Arrivals;
  nowMs: number;
  /** How long since the page opened. */
  waitedMs: number;
  /** The keys of the rows open. */
  opened: ReadonlySet<string>;
  /** The rows with no clock time are shown. */
  untimedShown: boolean;
  clock?: Clock;
}

/** The page's last lines, only while true: the names that could not be read, dim; a read
 * of the records that failed and the page server's own error, in the text colour. */
export function lastLines(feed: PageFeed, names: { answer: NamesAnswer | null; failed: boolean }): LastLine[] {
  const lines: LastLine[] = [];
  if (names.failed || names.answer?.state === "error") lines.push({ text: NAMES_FAILED, dim: true });
  if (feed.gone) return lines;
  if (feed.failure !== null) lines.push({ text: `The clock records can't be read: ${feed.failure.why}.`, dim: false });
  else if (feed.value?.state === "error") {
    const detail = feed.value.detail;
    lines.push({ text: detail ? `${SERVER_ERROR_LINE}: ${detail}` : `${SERVER_ERROR_LINE}.`, dim: false });
  }
  return lines;
}

/** The newest `today` the page holds: the clocks answer's or the names answer's. */
export function todayOf(kept: Kept | null, names: NamesAnswer | null): string | null {
  return newestDay(kept?.value.today, names?.today);
}

/** One row as drawn at `now`. */
export function rowModel(row: ClockRow, now: number, names: Map<string, NameItem>, today: string | null,
                         open: boolean, clock: Clock = LOCAL): RowModel {
  const { name, where } = sessionName(namingOf(row, names), today, clock);
  const base = { key: keyOf(row), session: row.session, harness: row.harness, name, where, open };
  const cold = coldAt(row);
  if (cold === null) return { ...base, fact: untimedFact(row), code: row.code ?? null, rest: null };
  const when = whenWord(cold, today, clock);
  return { ...base, fact: isCold(row, now) ? `cold since ${when}` : `warm until ${when}`, code: null,
           rest: `last request ${whenWord(row.last_request as number, today, clock)}` };
}

/**
 * The page. The groups, their counts, the line for rows with no clock time, the state and
 * the Watcher row come from the clocks answer alone; a row held back for its name is
 * already counted, and its group's label shows over the rows drawn so far.
 *
 * On a failed read or the page server's error, the rows last read are kept and still turn
 * cold by the clock; the watcher is judged as of the moment that answer was taken, the
 * last evidence the page holds.
 */
export function pageModel(input: PageInput): PageModel {
  const { feed, kept, now, names, arrivals, nowMs, opened, untimedShown } = input;
  const clock = input.clock ?? LOCAL;
  const last = lastLines(feed, names);
  const none = { waiting: null, watcher: null, groups: [], untimed: null, sentences: [], foot: null, last };
  if (feed.gone) return { ...none, state: "gone", last: [] };
  if (kept === null || now === null) {
    if (feed.failure !== null || feed.value?.state === "error") return { ...none, state: "failed" };
    return { ...none, state: "waiting", waiting: input.waitedMs >= WAITING_MS ? WAITING_LINE : null };
  }
  const broken = feed.failure !== null || feed.value?.state !== "up";
  const { clocks: rows, reading } = kept.value;
  const today = todayOf(kept, names.answer);
  const found = watching(reading, rows, broken ? kept.at : now);
  const named = namesBy(names.answer);
  const sorted = sortRows(rows, now);
  const draw = (list: ClockRow[]): RowModel[] => list.filter((row) => arrivals.shown(keyOf(row), nowMs))
    .map((row) => rowModel(row, now, named, today, opened.has(keyOf(row)), clock));
  const groups: GroupModel[] = [];
  if (sorted.warm.length) groups.push({ kind: "warm", label: `${sorted.warm.length} warm`, count: sorted.warm.length, rows: draw(sorted.warm) });
  if (sorted.cold.length) groups.push({ kind: "cold", label: `${sorted.cold.length} cold`, count: sorted.cold.length, rows: draw(sorted.cold) });
  const untimed: UntimedModel | null = sorted.untimed.length === 0 ? null : {
    line: `${sorted.untimed.length} with no cache reading`, count: sorted.untimed.length,
    act: untimedShown ? "Hide" : "Show", rows: untimedShown ? draw(sorted.untimed) : [],
  };
  const page = { ...none, state: "up" as const, groups, untimed, watcher: watcherRow(found, today, clock) };
  const timedCount = sorted.warm.length + sorted.cold.length;
  switch (found.outcome) {
    case "fresh":
      if (timedCount === 0 && sorted.untimed.every((row) => row.kind === "fresh" || row.kind === "no-cache")) {
        return { ...page, sentences: [EMPTY_LINE, CACHE_LINE] };
      }
      return { ...page, foot: { reading: `Last reading ${whenWord(found.at as number, today, clock)}` } };
    case "no-reading":
      return { ...page, sentences: [CACHE_LINE] };
    default:
      return { ...page, foot: { reading: null } };
  }
}

// --- the opened row -----------------------------------------------------------------------

/** One piece of an opened line: dim, or in the text colour. */
export interface Part {
  text: string;
  dim: boolean;
}

/** One line of an opened row: its label, its value to the second, and dim tails after. */
export interface OpenedLine {
  label: string;
  value: string;
  tails: string[];
}

/** `/api/session` for the opened row: out, or answered with a model or null. */
export type ModelRead = { state: "reading" } | { state: "read"; model: string | null };

/** `/api/session`'s value, or null when it isn't one. */
export function parseSession(data: unknown): { model: string | null } | null {
  if (typeof data !== "object" || data === null || !("model" in data)) return null;
  const { model } = data as Record<string, unknown>;
  if (model !== null && typeof model !== "string") return null;
  return { model: model === "" ? null : model };
}

/** An opened row's lines, as drawn. */
export interface OpenedModel {
  /** `Last request`, `Goes cold` or `Went cold`, and `Read` for an old record. */
  lines: OpenedLine[];
  /** The `Session` line's parts: harness, model, start, folder, each absent when null.
   * The harness leads in the text colour and the parts after it are dim. */
  session: Part[];
  /** The Sessions page's route for the session's own view, when sessions holds it. */
  sessions: string | null;
  /** What `Copy id` writes. */
  copy: string;
}

/**
 * The model of each opened row, from `/api/session`: asked when a row opens and not
 * before, `reading` while it is out, and forgotten when the row closes. An answer for a
 * row closed (or closed and opened again) since it was asked is dropped. A request that
 * gives no readable answer reads as no model, which is not drawn.
 */
export class ModelReads {
  #reads = new Map<string, ModelRead>();
  #read: (session: string) => Promise<ApiResult>;
  #onChange: () => void;

  constructor(read: (session: string) => Promise<ApiResult>, onChange: () => void) {
    this.#read = read;
    this.#onChange = onChange;
  }

  /** The row `key`, for `session`, opened: its model is asked. */
  open(key: string, session: string): void {
    const out: ModelRead = { state: "reading" };
    this.#reads.set(key, out);
    void (async () => {
      let answer: ApiResult;
      try {
        answer = await this.#read(session);
      } catch {
        answer = { status: 0, data: null };
      }
      if (this.#reads.get(key) !== out) return;
      const value = answer.status === 200 ? parseSession(answer.data) : null;
      this.#reads.set(key, { state: "read", model: value?.model ?? null });
      this.#onChange();
    })();
  }

  /** The row `key` closed: its model is forgotten. */
  close(key: string): void {
    this.#reads.delete(key);
  }

  get(key: string): ModelRead | null {
    return this.#reads.get(key) ?? null;
  }
}

export const READ_TAIL = "not rewritten since; this reading may be old";

/** A row opened at `now`. */
export function openedModel(row: ClockRow, now: number, names: Map<string, NameItem>, home: string | null,
                            model: ModelRead | null, today: string | null, clock: Clock = LOCAL): OpenedModel {
  const exact = (at: number): string => whenWord(at, today, clock, { exact: true });
  const lines: OpenedLine[] = [];
  const cold = coldAt(row);
  if (cold !== null) {
    lines.push({ label: "Last request", value: exact(row.last_request as number), tails: [] });
    lines.push({ label: isCold(row, now) ? "Went cold" : "Goes cold", value: exact(cold),
                 tails: [countdown(row, now) as string, lifetimeWords(row.ttl as number)] });
  }
  if (isOld(row, now)) lines.push({ label: "Read", value: exact(row.seen_at), tails: [READ_TAIL] });
  const item = names.get(keyOf(row));
  const session: Part[] = [{ text: harnessWords(row.harness), dim: false }];
  if (model?.state === "reading") session.push({ text: WAITING_LINE, dim: true });
  else if (model?.state === "read" && model.model !== null) session.push({ text: model.model, dim: true });
  if (item !== undefined && item.started !== null) {
    session.push({ text: `started ${whenWord(item.started, today, clock, { named: true })}`, dim: true });
  }
  if (item !== undefined && item.folder !== null && item.folder !== "") {
    session.push({ text: folderWords(item.folder, home), dim: true });
  }
  return {
    lines,
    session,
    sessions: item !== undefined ? `${encodeURIComponent(row.harness)}/${encodeURIComponent(row.session)}` : null,
    copy: row.session,
  };
}

// --- the view's words ---------------------------------------------------------------------

export const WATCHER_LABEL = "Watcher";
export const OPEN = "Open";
export const CLOSE = "Close";
export const COPY = "Copy";
export const COPY_ID = "Copy id";
/** What a copy act reads for `COPIED_MS` after it wrote to the clipboard. */
export const COPIED = "Copied";
export const COPIED_MS = 1500;
export const SETTINGS_LINK = "Cache settings";
/** The settings view's route for cache's keys, in ui. */
export const SETTINGS_ROUTE = "settings/cache";
export const SESSIONS_LINK = "Sessions";
export const SESSION_LABEL = "Session";

/** The commands a Watcher row's sentence names, drawn as code. */
const COMMANDS = /(moxi cache watch|moxi install cache)/;

/** A sentence cut into its plain words and the commands it names, in order. */
export function commandParts(line: string): { text: string; code: boolean }[] {
  return line.split(COMMANDS).map((text, at) => ({ text, code: at % 2 === 1 })).filter((part) => part.text !== "");
}

/** `/api/session`'s path for one session. */
export function sessionPath(session: string): string {
  return `api/session?session=${encodeURIComponent(session)}`;
}

/** The record's fact as the row element draws it: a row with no clock time gets its
 * record's code after the fact, as the record carries it. */
export function factText(row: RowModel): string {
  return row.code !== null && row.code !== "" ? `${row.fact} · ${row.code}` : row.fact;
}
