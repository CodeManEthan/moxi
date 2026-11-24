// What cache's clock records say of the list's sessions, worked out with no DOM so the Node
// tests can drive it. With cache installed, at the desk, the list reads cache's
// `/api/clocks` and follows its topic `clocks`; a row takes the record of its own harness and
// session id.
//
// A row's first line then ends in a dim `warm until 19:12`, or `cold` once that time has
// passed on cache's clock; its opened row has a `Cache` line, `warm until 19:12` or `cold
// since 18:35`, with a link to cache's page. A record with no clock time says nothing, and
// neither does a row with no record, a failed read, or any row without cache installed.
//
// On a running launch launcher's cache fact wins, as Home's running rows and the Comms page
// have it, so a session reads the same on every page: the row's fact and its `Cache` line
// are launcher's where launcher gives one, and cache's only where it gives none.
//
// Which records have a clock time and when each goes cold are cache's rules, as its page
// has them; a test holds them equal where cache's source sits beside sessions'.
//
// Every string here goes into the page as text.

import type { RunningEntry } from "./running.ts";
import { caseOf, factOf, stampOf } from "./running.ts";

/** One clock record, as cache's `/api/clocks` and its topic send it. */
export interface ClockRow {
  session: string;
  harness: string;
  kind: string;
  last_request: number | null;
  ttl: number | null;
  warn_before: number | null;
  seen_at: number;
  stale_after: number | null;
  code: string | null;
}

/** What the list knows of cache's records: each by its row's key, and the server's clock
 * less this browser's, in seconds. */
export interface Clocks {
  rows: Map<string, ClockRow>;
  offset: number;
}

/** What a route answered, as the ui client's `api` gives it. */
export interface Answer {
  status: number;
  data: unknown;
}

/** The route the list reads cache's records from, relative to cache's page. */
export const CLOCKS_PATH = "api/clocks";
/** The topic of cache's stream that carries the same records, without `now`. */
export const CLOCKS_TOPIC = "clocks";

/** The opened row's label for the line, and its link's words. */
export const CACHE_LABEL = "Cache";
export const CACHE_LINK = "Cache";
/** What an old record's line adds: cache's own words for a record not rewritten lately. */
export const READ_TAIL = "not rewritten since; this reading may be old";

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

function isTable(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A row's key, the list's own: its harness and session id. */
const keyOf = (row: { harness: string; session: string }): string => `${row.harness}/${row.session}`;

/** The records of the route's or the topic's value, by key; null for a value that is not
 * one. A value whose `state` is `error` holds no record. */
export function parseClocks(data: unknown): Map<string, ClockRow> | null {
  if (!isTable(data)) return null;
  if (data.state === "error") return new Map();
  if (data.state !== "up" || !Array.isArray(data.clocks)) return null;
  const rows = new Map<string, ClockRow>();
  for (const row of data.clocks) {
    if (!isTable(row) || typeof row.session !== "string" || typeof row.harness !== "string") continue;
    rows.set(keyOf(row as { harness: string; session: string }), row as unknown as ClockRow);
  }
  return rows;
}

/** What a read of `/api/clocks` taken at `at` (this browser's clock, epoch seconds) leaves
 * the list knowing: the records and the server's clock. Anything but a readable 200 with
 * the server's `now` leaves nothing. */
export function clocksRead(answer: Answer, at: number): Clocks | null {
  if (answer.status !== 200) return null;
  const rows = parseClocks(answer.data);
  const now = isTable(answer.data) ? answer.data.now : undefined;
  if (rows === null || !finite(now)) return null;
  return { rows, offset: now - at };
}

/** A value of the topic `clocks` over what the list held: the new records on the same
 * clock, or null when the route must be read again (the value can't be read, or there was
 * no read to take the server's clock from). */
export function clocksTopic(kept: Clocks | null, data: unknown): Clocks | null {
  const rows = parseClocks(data);
  if (rows === null || kept === null) return null;
  return { rows, offset: kept.offset };
}

/** The server's time when this browser's clock reads `at`. */
export function serverNow(clocks: Clocks, at: number): number {
  return at + clocks.offset;
}

// --- cache's rules, as its page has them --------------------------------------------------

/** A record with a clock time: `warm` or `cold`, and `last_request`, `ttl`, `warn_before`
 * and `stale_after` all finite. */
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

/** A record is old when it was last seen longer ago than its own `stale_after`. */
export function isOld(row: ClockRow, now: number): boolean {
  return finite(row.stale_after) && finite(row.seen_at) && now - row.seen_at > row.stale_after;
}

// --- a row ----------------------------------------------------------------------------------

/** The record for a row, or null. */
export function clockOf(clocks: Clocks | null, row: { harness: string; session: string }): ClockRow | null {
  return clocks?.rows.get(keyOf(row)) ?? null;
}

/** The right end of a row's first line from its record: `warm until <time>`, or `cold`
 * from the cold time on; null for a record with no clock time. `when` words a moment as
 * the row's other times are worded. */
export function clockFact(row: ClockRow, now: number, when: (at: number) => string): string | null {
  const cold = coldAt(row);
  if (cold === null) return null;
  return isCold(row, now) ? "cold" : `warm until ${when(cold)}`;
}

/** The opened row's `Cache` line: its value, and a dim tail after it. */
export interface CacheLine {
  value: string;
  tail: string | null;
}

/** The `Cache` line from a record: `warm until <time>` or `cold since <time>`, and for a
 * record cache has not rewritten lately when it was read; null for a record with no clock
 * time. */
export function clockLine(row: ClockRow, now: number, when: (at: number) => string): CacheLine | null {
  const cold = coldAt(row);
  if (cold === null) return null;
  return {
    value: `${isCold(row, now) ? "cold since" : "warm until"} ${when(cold)}`,
    tail: isOld(row, now) ? `read ${when(row.seen_at)}, ${READ_TAIL}` : null,
  };
}

/** The `Cache` line from a running launch's cache, as the Comms page words launcher's:
 * `warm until <time>` (`, est.` where launcher marks it estimated) with `kept warm while …`
 * after it when covered, or `cold since <time>`; null where launcher gives no cache. */
export function launchedLine(entry: RunningEntry, when: (at: number) => string): CacheLine | null {
  const cache = entry.cache;
  if (cache === null) return null;
  const until = stampOf(cache.until);
  const at = until !== null ? ` ${when(until)}` : "";
  if (cache.state !== "warm") return { value: `cold${at !== "" ? ` since${at}` : ""}`, tail: null };
  return {
    value: `warm${at !== "" ? ` until${at}` : ""}${cache.estimated ? ", est." : ""}`,
    // A covered launch's fact is `kept warm while …`, which needs no time.
    tail: caseOf(entry) === "covered" ? factOf(entry, when)?.text ?? null : null,
  };
}

/** The soonest moment after `now` that a record now warm goes cold, so the list can be
 * drawn again then; null when none will. */
export function nextCold(clocks: Clocks | null, now: number): number | null {
  let soonest: number | null = null;
  for (const row of clocks?.rows.values() ?? []) {
    const cold = coldAt(row);
    if (cold !== null && !isCold(row, now) && (soonest === null || cold < soonest)) soonest = cold;
  }
  return soonest;
}
