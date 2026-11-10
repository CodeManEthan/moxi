// An opened limit row: the lines under one plan window's row, worked out from the
// window's own read (`/api/window`) and the row the limits answer holds. No DOM here.
// Every line is the words the page shows, or absent when the answer does not carry its
// fact: no label stands over nothing.

import { costWords, countWords } from "./cost-model.ts";
import { LOCAL, seconds, whenWord } from "./days.ts";
import type { Clock } from "./days.ts";
import type { OpenedWindow, UsedRow, Window } from "./feed.ts";
import { percentWords } from "./limits-model.ts";

/** One labelled line: `Window  opened 16:34, resets 21:34 · 52% of it gone, 49% used`. */
export interface OpenedLine {
  key: "window" | "readings";
  label: string;
  text: string;
  /** The dim words after the text, drawn after ` · `; null when there are none. */
  dim: string | null;
}

/** One session that used the window. */
export interface UsedLine {
  name: string;
  /** `18 requests`. */
  requests: string;
  /** `$1.74`, `at least $1.74`, `no price`. */
  cost: string;
}

/** Who used the window since it opened. */
export interface UsedBy {
  label: string;
  /** The dearest sessions that have a name to show; none while no name is read. */
  rows: UsedLine[];
  /** `6 sessions, 63 requests since 16:34`. */
  total: string;
  cost: string;
}

export interface OpenedLimit {
  /** The word that closes the row. */
  close: string;
  /** `Reading…`, in the lines' place while the first read is out. */
  reading: string | null;
  lines: OpenedLine[];
  used: UsedBy | null;
  /** Dim lines of the row's own: why it has no fill time, and the reading's note. */
  notes: string[];
}

/** What the opened row is worked out from. */
export interface OpenedFacts {
  /** The window's read; null until it has answered. */
  window: OpenedWindow | null;
  /** No read of the window has finished. */
  loading: boolean;
  /** The row as the limits answer holds it. */
  row: Pick<Window, "full_at" | "projection" | "note"> | null;
  /** The page server's time, epoch seconds: the same the row's tick is drawn at. */
  now: number;
  /** A session's name as the page shows one; null where none is read, and then the
   * session has no line. A session is never shown by its id. */
  name?: (row: UsedRow) => string | null;
}

export const CLOSE = "Close";
export const WINDOW = "Window";
export const READINGS = "Readings";
export const USED_BY = "Used by";
const READING = "Reading…";

/** Two moments of one line: a time on today's date says `today` only when the other
 * is on another day. */
function pair(a: number | null, b: number | null, today: string, clock: Clock): (at: number) => string {
  const named = a !== null && b !== null && clock.day(a) !== clock.day(b);
  return (at) => whenWord(at, today, clock, named);
}

/**
 * The opened row at `facts.now`.
 *
 * - `Window`: when it opened and when it resets, then how much of it is gone and how
 *   much is used. A window whose start is not known says only its reset and its use.
 * - `Readings`: the period's first reading and the row's own, each a percent and its
 *   time; one reading when the period holds no earlier one. A reading with no percent
 *   is left out, and with none left the line is absent.
 * - `Used by`: the sessions the answer lists that have a name, then the count of
 *   sessions and requests since the window opened with their cost. Absent when the
 *   answer carries no such report.
 */
export function openedLimit(facts: OpenedFacts, clock: Clock = LOCAL): OpenedLimit {
  const { window, row, now } = facts;
  const notes: string[] = [];
  if (row !== null) {
    if (row.full_at === null && row.projection) notes.push(`No fill time: ${row.projection}`);
    if (row.note) notes.push(row.note);
  }
  if (window === null) {
    return { close: CLOSE, reading: facts.loading ? READING : null, lines: [], used: null, notes };
  }
  const today = window.today;
  const lines: OpenedLine[] = [];

  const opened = seconds(window.opened);
  const resets = seconds(window.resets_at);
  const used = `${percentWords(window.used_percent)} used`;
  if (resets !== null) {
    const when = pair(opened, resets, today, clock);
    const gone = opened !== null && resets > opened ? 100 * (now - opened) / (resets - opened) : null;
    lines.push({
      key: "window", label: WINDOW,
      text: opened === null ? `resets ${when(resets)}` : `opened ${when(opened)}, resets ${when(resets)}`,
      dim: gone === null ? used : `${percentWords(Math.min(100, Math.max(0, gone)))} of it gone, ${used}`,
    });
  }

  const readings = window.readings;
  if (readings !== null) {
    const found = [readings.first, readings.last]
      .map((reading) => reading === null || reading.used_percent === null ? null
        : { at: seconds(reading.at), percent: reading.used_percent })
      .filter((reading): reading is { at: number; percent: number } => reading !== null && reading.at !== null);
    if (found.length > 0) {
      const when = pair(found[0].at, found[found.length - 1].at, today, clock);
      lines.push({
        key: "readings", label: READINGS,
        text: found.map((reading) => `${percentWords(reading.percent)} at ${when(reading.at)}`).join(", "),
        dim: null,
      });
    }
  }

  let by: UsedBy | null = null;
  if (window.used !== null) {
    const since = seconds(window.used.since);
    const name = facts.name;
    const rows: UsedLine[] = [];
    for (const item of window.used.rows) {
      const called = name === undefined ? null : name(item);
      if (called === null) continue;
      rows.push({ name: called, requests: countWords(item.requests, "request"),
                  cost: costWords({ cost: item.cost, complete: item.cost !== null }) });
    }
    const counts = `${countWords(window.used.count, "session")}, ${countWords(window.used.requests, "request")}`;
    by = {
      label: USED_BY, rows,
      total: since === null ? counts : `${counts} since ${whenWord(since, today, clock)}`,
      cost: costWords(window.used),
    };
  }
  return { close: CLOSE, reading: null, lines, used: by, notes };
}

/** What of a limits row, when it changes, makes the opened row's read stale. */
export function readingKey(row: Pick<Window, "observed_at" | "used_percent" | "resets_at">): string {
  return JSON.stringify([row.observed_at, row.used_percent, row.resets_at]);
}

/** The row an opened limit stands under: the limits answer's `fresh` row of that
 * window; null when the answer holds no such row, and then the row is closed. */
export function openedRow(rows: Window[], at: { harness: string; limit: string; minutes: number }): Window | null {
  return rows.find((row) => row.harness === at.harness && row.limit_id === at.limit
    && row.window_minutes === at.minutes && row.state === "fresh") ?? null;
}

/** The path of the window's read. */
export function windowPath(at: { harness: string; limit: string; minutes: number }): string {
  const query = new URLSearchParams([["harness", at.harness], ["limit_id", at.limit], ["minutes", String(at.minutes)]]);
  return `api/window?${query.toString()}`;
}
