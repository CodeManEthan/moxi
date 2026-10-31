// What the launcher's components show for each state of their data, worked out with no DOM
// so the Node tests can read it. Every string here goes into the page as text.
//
// A session's lines say what `moxi launcher state` prints: whether it needs a look, its
// relaunch, its recycle and its keep-warm. Red is the server's: a component never judges a
// run itself, it shows `red` and `word` as the state sent them.

import type { Failure, LaunchRow, Launches, RunLine, SessionState, States } from "./feed.ts";

/** A component's data as its `Feed` holds it. */
export interface FeedState<V> {
  loading: boolean;
  gone: boolean;
  failure: Failure | null;
  value: V | null;
}

/** How a state reads at a glance; each has its own colours. */
export type Tone = "loading" | "ok" | "red" | "muted" | "none";

export const SERVER_ERROR = "the launcher's page server hit an error";
export const UNREADABLE = "state ?";
export const NOT_LAUNCHED = "not launched";

/** A store time (UTC ISO 8601) cut to the minute: `2026-10-31 12:04 UTC`. Anything else
 * comes back as it is. */
export function when(stamp: string | null): string {
  if (stamp === null) return "";
  const match = /^(\d{4}-\d\d-\d\d)T(\d\d:\d\d)/.exec(stamp);
  return match ? `${match[1]} ${match[2]} UTC` : stamp;
}

/** Where a run stands, in a few words: its state, or its outcome once ended, and its word. */
export function said(run: RunLine): string {
  let text = run.state !== "ended" ? run.state : (run.outcome || "ended");
  if (run.word && run.word !== text) text += ` (${run.word})`;
  return text;
}

/** The first line: `ok`, or the word, since when and why. */
export function headline(entry: SessionState): string {
  if (entry.unreadable !== null) return `the state could not be read: ${entry.unreadable}`;
  if (!entry.red) return "ok";
  const since = entry.since ? ` since ${when(entry.since)}` : "";
  return `${entry.word ?? "failed"}${since}${entry.why ? `: ${entry.why}` : ""}`;
}

/** The relaunch, recycle and keep-warm lines, as `moxi launcher state` prints them. */
export function stateLines(entry: SessionState): string[] {
  const lines: string[] = [];
  const relaunch = entry.relaunch;
  if (relaunch !== null) {
    let line = `relaunch: ${relaunch.state ?? "none"}`;
    const run = relaunch.run;
    if (run !== null && run.detail && run.state === "ended") line += `: ${run.detail}`;
    if (relaunch.note_not_taken) line += "; its note to the session was not taken";
    if (relaunch.notices_refused.length) line += `; notice refused by ${relaunch.notices_refused.join(", ")}`;
    lines.push(line);
  }
  const recycle = entry.recycle;
  if (recycle !== null) {
    let line = `recycle: on expiry ${recycle.on_expiry}, at limit ${recycle.at_limit}`;
    if (recycle.at_limit !== "off" && recycle.limit_at) line += ` (${recycle.limit_at})`;
    if (recycle.last !== null) line += `; last: ${recycle.last.kind} ${said(recycle.last)} at ${when(recycle.last.updated_at)}`;
    if (recycle.pending_compact) line += `; a compact pending since ${when(recycle.pending_compact)}`;
    if (recycle.told_at) line += `; told to recycle itself at ${when(recycle.told_at)}`;
    lines.push(line);
  }
  const warm = entry.keep_warm;
  if (warm !== null) {
    let line = `keep warm: ${!warm.on ? "off" : warm.window ? `on (${warm.window})` : "on"}`;
    if (warm.on) {
      line += `; holds: ${warm.holds.join(", ") || "none"}`;
      if (warm.nudges) line += `; nudges in a row: ${warm.nudges}`;
      if (warm.last !== null) line += `; last: ${said(warm.last)} at ${when(warm.last.updated_at)}`;
    }
    lines.push(line);
  }
  return lines;
}

/** What a session's state shows in a few words: the red word, or what is under way (an
 * open relaunch, a pending compact, keep-warm on), or `ok`. */
export function summary(entry: SessionState): string {
  if (entry.unreadable !== null) return UNREADABLE;
  if (entry.red) return entry.word ?? "failed";
  const parts: string[] = [];
  const run = entry.relaunch?.run;
  if (run && run.state !== "ended" && entry.relaunch?.state) parts.push(`relaunch ${entry.relaunch.state}`);
  if (entry.recycle?.pending_compact) parts.push("compact pending");
  if (entry.keep_warm?.on) parts.push("warm");
  return parts.join(" · ") || "ok";
}

/** One session's state as the `state` component draws it. */
export interface StateView {
  /** `loading`, `up`, `unknown` (no launch holds the id), `unreadable` (the server
   * couldn't read this launch's state), `error` (the server's read failed), `failed`
   * (the request did) or `gone` (a 404: the launcher went away; nothing is drawn). */
  state: "loading" | "up" | "unknown" | "unreadable" | "error" | "failed" | "gone";
  tone: Tone;
  red: boolean;
  /** With red, `blocked` or `failed`; else null. */
  word: string | null;
  text: string;
  /** Every line of the state, for a title; else why it shows none. */
  title: string | null;
}

/** The `state` component for `session`. */
export function stateView(data: FeedState<States>, session: string): StateView {
  const base = { red: false, word: null };
  if (data.gone) return { ...base, state: "gone", tone: "none", text: "", title: null };
  if (data.failure !== null) {
    return { ...base, state: "failed", tone: "muted", text: UNREADABLE, title: data.failure.why };
  }
  if (data.loading || data.value === null) {
    return { ...base, state: "loading", tone: "loading", text: "", title: null };
  }
  const value = data.value;
  if (value.state === "error") {
    return { ...base, state: "error", tone: "muted", text: UNREADABLE, title: value.detail ?? SERVER_ERROR };
  }
  const entry = value.sessions.find((item) => item.session_id === session);
  if (entry === undefined) {
    return { ...base, state: "unknown", tone: "muted", text: NOT_LAUNCHED,
             title: value.refused?.problem ?? `no launch holds the session ${session}` };
  }
  if (entry.unreadable !== null) {
    return { ...base, state: "unreadable", tone: "muted", text: UNREADABLE, title: headline(entry) };
  }
  return {
    state: "up",
    tone: entry.red ? "red" : "ok",
    red: entry.red,
    word: entry.red ? (entry.word ?? "failed") : null,
    text: summary(entry),
    title: [headline(entry), ...stateLines(entry)].join("\n"),
  };
}

/** One live session as the `sessions` component draws it. */
export interface SessionRow {
  session: string;
  request: string;
  /** Its harness, where it runs and its identity, from its launch row; the request id
   * while that row hasn't been read. */
  label: string;
  /** Its directory, null while its launch row hasn't been read. */
  cwd: string | null;
  red: boolean;
  word: string | null;
  tone: Tone;
  text: string;
  /** `ok`, or the red word, since when and why. */
  head: string;
  lines: string[];
}

/** The `sessions` component as it is drawn. */
export interface SessionsView {
  /** `loading`, `up`, `empty`, `error`, `failed` (the last rows are kept) or `gone`. */
  state: "loading" | "up" | "empty" | "error" | "failed" | "gone";
  rows: SessionRow[];
  /** How many rows are red. */
  red: number;
  /** With `error`, the server's detail; with `failed`, what went wrong. */
  detail: string | null;
}

/** A launch row's short label: `claude · slot 3 · reviewer`. */
export function launchLabel(row: LaunchRow): string {
  const where = row.target === "slot" && row.slot ? `slot ${row.slot}` : row.target;
  return [row.harness, where, row.identity].filter((part) => part).join(" · ");
}

/** One session's row, labelled from its launch row when it has been read. */
export function sessionRow(entry: SessionState, launch: LaunchRow | undefined): SessionRow {
  const unreadable = entry.unreadable !== null;
  return {
    session: entry.session_id,
    request: entry.request_id,
    label: launch ? launchLabel(launch) : `launch ${entry.request_id}`,
    cwd: launch ? launch.cwd : null,
    red: entry.red,
    word: entry.red ? (entry.word ?? "failed") : null,
    tone: unreadable ? "muted" : entry.red ? "red" : "ok",
    text: summary(entry),
    head: headline(entry),
    lines: stateLines(entry),
  };
}

/** The live sessions, in the server's order (newest first), each labelled from the launch
 * log when it holds the session's launch. The launch log failing leaves the labels out and
 * nothing else. */
export function sessionsView(states: FeedState<States>, launches: FeedState<Launches>): SessionsView {
  const rows = (launches.value?.state === "up" ? launches.value.launches : []);
  const byRequest = new Map(rows.map((row) => [row.request_id, row]));
  const kept = states.value?.state === "up" ? states.value.sessions : [];
  const shown = kept.map((entry) => sessionRow(entry, byRequest.get(entry.request_id)));
  const red = shown.filter((row) => row.red).length;
  if (states.gone) return { state: "gone", rows: [], red: 0, detail: null };
  if (states.failure !== null) return { state: "failed", rows: shown, red, detail: states.failure.why };
  if (states.loading || states.value === null) return { state: "loading", rows: [], red: 0, detail: null };
  if (states.value.state === "error") {
    return { state: "error", rows: [], red: 0, detail: states.value.detail ?? SERVER_ERROR };
  }
  return { state: shown.length ? "up" : "empty", rows: shown, red, detail: null };
}
