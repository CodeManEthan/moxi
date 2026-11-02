// What the roster shows for each state of its data, worked out with no DOM so the Node
// tests can read it. Every string here goes into the page as text.

import type { Failure, Roster, RosterRow } from "./feed.ts";

/** The roster's data as its `Feed` holds it. */
export interface FeedState {
  loading: boolean;
  gone: boolean;
  failure: Failure | null;
  value: Roster | null;
}

/** One address as the roster draws it. */
export interface RowView {
  qualified: string;
  live: boolean;
  /** "live" or "standing", then "send-only" and "reserved by <module>" when they apply. */
  tags: string[];
  adapter: string | null;
  /** How long the session has held the name, "bound for 2h4m"; null when not live. */
  bound: string | null;
  directors: string[];
  awaiting: string[];
  /** The session a cache chip is placed for: a live row's `session_id`, else null. */
  session: string | null;
}

/** The roster as it is drawn. */
export interface RosterView {
  /** `loading`, `up`, `empty`, `offline`, `error`, `failed` (the request failed; the last
   * rows are kept) or `gone` (a 404: comms went away; nothing is drawn). */
  state: "loading" | "up" | "empty" | "offline" | "error" | "failed" | "gone";
  rows: RowView[];
  /** With `error`, the daemon's or the server's detail; with `failed`, what went wrong. */
  detail: string | null;
}

export const SERVER_ERROR = "comms' page server hit an error";

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

/** One row at the server's time `now` (null before the first read). */
export function rowView(row: RosterRow, now: number | null): RowView {
  const tags = [row.live ? "live" : "standing"];
  if (row.kind === "send-only" || row.adapter === "send-only") tags.push("send-only");
  if (row.module) tags.push(`reserved by ${row.module}`);
  const counted = row.live && typeof row.bound_at === "number" && Number.isFinite(row.bound_at) && now !== null;
  return {
    qualified: row.qualified,
    live: row.live,
    tags,
    adapter: row.adapter,
    bound: counted ? `bound for ${span(now - (row.bound_at as number))}` : null,
    directors: row.directors,
    awaiting: row.awaiting,
    session: row.live && row.session_id ? row.session_id : null,
  };
}

/** The roster at the server's time `now`. Rows keep the server's order. */
export function rosterView(data: FeedState, now: number | null): RosterView {
  const kept = data.value?.state === "up" ? data.value.rows : [];
  const rows = kept.map((row) => rowView(row, now));
  if (data.gone) return { state: "gone", rows: [], detail: null };
  if (data.failure !== null) return { state: "failed", rows, detail: data.failure.why };
  if (data.loading || data.value === null) return { state: "loading", rows: [], detail: null };
  if (data.value.state === "offline") return { state: "offline", rows: [], detail: null };
  if (data.value.state === "error") {
    return { state: "error", rows: [], detail: data.value.detail ?? SERVER_ERROR };
  }
  return { state: rows.length ? "up" : "empty", rows, detail: null };
}
