// The chip's band rule: what one clock record shows at a time on the server's clock. It
// is the status line segment's rule, `segment.from_record`, whole, and both run the one
// case table in packages/cache/tests/band-cases.json, so they can't drift apart.
//
// No DOM and no Lit here, so the Node tests import it as it is.

/** The three bands a counted record falls in. */
export type Band = "warm" | "near" | "cold";

/** What a record shows when it has no band to count. */
export const NO_READING = "no reading";

/** One clock record as cache's page sends it: `/api/clocks`'s and the topic's row. */
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

/** A record read at one time. */
export interface Reading {
  /** A band, the kind's word (`fresh`, `no-cache`, `unknown`), or "no reading". */
  shows: string;
  /** The band when there is one, else null. */
  band: Band | null;
  /** The watcher has stopped: seen longer ago than the record's `stale_after`. */
  dim: boolean;
  /** Seconds since the session's last request ended; null without a band. */
  age: number | null;
  /** The lifetime the clock counts; null without a band. */
  ttl: number | null;
}

/** The kinds with no band to count, each shown as its own word. */
const WORDS: readonly string[] = ["fresh", "no-cache", "unknown"];

const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);

const none = (shows: string): Reading => ({ shows, band: null, dim: false, age: null, ttl: null });

/**
 * What `row` shows at `now` (the server's clock, epoch seconds):
 *
 * 1. a `fresh`, `no-cache` or `unknown` record has no band, and shows its kind's word; a
 *    kind outside those and the two below is "no reading", as the segment shows nothing;
 * 2. a `warm` or `cold` record without a finite `warn_before`, `stale_after`,
 *    `last_request` or `ttl` has no band, and shows "no reading", as the segment shows
 *    nothing;
 * 3. otherwise the age is `max(0, now - last_request)`: `cold` from `ttl`, `near` from
 *    `max(0, ttl - warn_before)`, else `warm`; dim when `now - seen_at > stale_after`.
 *
 * No row at all (no record for the session) is "no reading" too.
 */
export function reading(row: ClockRow | null | undefined, now: number): Reading {
  if (row === null || row === undefined || typeof row.kind !== "string") return none(NO_READING);
  if (WORDS.includes(row.kind)) return none(row.kind);
  if (row.kind !== "warm" && row.kind !== "cold") return none(NO_READING);
  const { last_request: last, ttl, warn_before: warn, stale_after: stale } = row;
  if (!finite(warn) || !finite(stale) || !finite(last) || !finite(ttl)) return none(NO_READING);
  const age = Math.max(0, now - last);
  let band: Band;
  if (age >= ttl) band = "cold";
  else if (age >= Math.max(0, ttl - warn)) band = "near";
  else band = "warm";
  const dim = finite(row.seen_at) && now - row.seen_at > stale;
  return { shows: band, band, dim, age, ttl };
}

/** A length of time in whole units: `45s`, `41m`, `1h4m`, `3d`. */
export function span(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  if (total < 60) return `${total}s`;
  if (total < 3600) return `${Math.floor(total / 60)}m`;
  if (total < 86400) {
    const minutes = Math.floor((total % 3600) / 60);
    return `${Math.floor(total / 3600)}h${minutes ? `${minutes}m` : ""}`;
  }
  return `${Math.floor(total / 86400)}d`;
}

/**
 * The chip's words for a reading: "warm 41m left" and "near 6m left" count down to the
 * lifetime's end, "cold 12m ago" counts from it; a reading with no band shows its word.
 */
export function readingText(found: Reading): string {
  if (found.band === null || found.age === null || found.ttl === null) return found.shows;
  if (found.band === "cold") return `cold ${span(found.age - found.ttl)} ago`;
  return `${found.band} ${span(Math.ceil(found.ttl - found.age))} left`;
}
