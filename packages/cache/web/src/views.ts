// What the chip and the page show for each state of their data, worked out with no DOM
// so the Node tests can read it. Every string here goes into the page as text.

import { NO_READING, reading, readingText } from "./band.ts";
import type { ClockRow } from "./band.ts";
import type { Clocks, Failure } from "./feed.ts";

/** A component's data as its `Feed` holds it. */
export interface FeedState {
  loading: boolean;
  gone: boolean;
  failure: Failure | null;
  value: Clocks | null;
}

/** How the chip reads at a glance; each has its own colours. */
export type Tone = "loading" | "warm" | "near" | "cold" | "word" | "muted" | "none";

/** The chip as it is drawn. */
export interface ChipView {
  /** `loading`, `up`, `error` (the server's read failed), `failed` (the request did),
   * or `gone` (a 404: the module went away; nothing is drawn). */
  state: "loading" | "up" | "error" | "failed" | "gone";
  /** With `up`: the band table's word, a band, a kind or "no reading"; else null. */
  shows: string | null;
  dim: boolean;
  tone: Tone;
  text: string;
  title: string | null;
}

export const NO_RECORD = "no cache record for this session";
export const WATCHER_STOPPED = "the cache watcher has stopped; this reading may be old";
export const SERVER_ERROR = "cache's page server hit an error";

/** The chip for `session` at the server's time `now` (null before the first read). */
export function chipView(data: FeedState, session: string, now: number | null): ChipView {
  const base = { shows: null, dim: false };
  if (data.gone) return { ...base, state: "gone", tone: "none", text: "", title: null };
  if (data.failure !== null) {
    return { ...base, state: "failed", tone: "muted", text: "cache ?", title: data.failure.why };
  }
  if (data.loading || data.value === null || now === null) {
    return { ...base, state: "loading", tone: "loading", text: "", title: null };
  }
  if (data.value.state === "error") {
    return { ...base, state: "error", tone: "muted", text: "cache ?", title: data.value.detail ?? SERVER_ERROR };
  }
  const row = data.value.clocks.find((item) => item.session === session);
  const found = reading(row, now);
  const view = { state: "up" as const, shows: found.shows, dim: found.dim };
  if (found.band !== null) {
    return { ...view, tone: found.band, text: readingText(found), title: found.dim ? WATCHER_STOPPED : null };
  }
  if (found.shows === NO_READING) return { ...view, tone: "muted", text: NO_READING, title: NO_RECORD };
  return { ...view, tone: "word", text: found.shows, title: row?.code ?? null };
}

/** The page as it is drawn, without its rows' chips, which read for themselves. */
export interface PageView {
  /** `loading`, `up`, `empty`, `error`, `failed` or `gone`. */
  state: "loading" | "up" | "empty" | "error" | "failed" | "gone";
  /** The rows to list, each with a chip; the last ones read are kept on a failure. */
  rows: ClockRow[];
  /** Every row's band is dimmed: the watcher has stopped. */
  stopped: boolean;
  /** With `error`, the server's detail; with `failed`, what went wrong. */
  detail: string | null;
}

/** The page at the server's time `now`. */
export function pageView(data: FeedState, now: number | null): PageView {
  const rows = data.value?.state === "up" ? data.value.clocks : [];
  const base = { rows, stopped: false, detail: null };
  if (data.gone) return { ...base, rows: [], state: "gone" };
  if (data.failure !== null) return { ...base, state: "failed", detail: data.failure.why };
  if (data.loading || data.value === null) return { ...base, state: "loading" };
  if (data.value.state === "error") return { ...base, state: "error", detail: data.value.detail ?? SERVER_ERROR };
  if (rows.length === 0) return { ...base, state: "empty" };
  const stopped = now !== null && rows.every((row) => reading(row, now).dim);
  return { ...base, state: "up", stopped };
}
