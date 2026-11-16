// What loadout's drift block shows for each state of its data, and how the page reads the
// answer an act gives and the job it follows, worked out with no DOM so the Node tests can
// read it. Every string here goes into the page as text.

import { saidOf } from "./feed.ts";
import type { Drift, Failure } from "./feed.ts";

/** One route's data as its `Feed` holds it. */
export interface FeedState<V> {
  loading: boolean;
  gone: boolean;
  failure: Failure | null;
  value: V | null;
}

// -- the drift block ----------------------------------------------------------------------

export interface DriftView {
  /**
   * `loading`; `up`; `problem` (the page server read, but loadout's manifest, ledger or
   * config can't be read); `failed` (the route gave no readable answer; the last count
   * read is kept); or `gone` (a 404: loadout went away; nothing is drawn).
   */
  state: "loading" | "up" | "problem" | "failed" | "gone";
  /** The count of items that aren't ok, or null when none is known. */
  count: number | null;
  /** The count's line, or null when none is known. */
  line: string | null;
  /** How the count is coloured: `ok` for none, `warn` for some, `muted` when unknown. */
  tone: "ok" | "warn" | "muted";
  /** The problem or the failure, in one line. */
  detail: string | null;
  /** The harness homes' note, which the count never includes. */
  note: string | null;
}

/** "Every item is ok.", "1 item isn't ok." or "3 items aren't ok." */
export function countLine(count: number): string {
  if (count === 0) return "Every item is ok.";
  return count === 1 ? "1 item isn't ok." : `${count} items aren't ok.`;
}

export function driftView(data: FeedState<Drift>): DriftView {
  const none = { count: null, line: null, tone: "muted", detail: null, note: null } as const;
  if (data.gone) return { state: "gone", ...none };
  const value = data.value;
  const known = value !== null && value.problem === null;
  const counted = known ? { count: value.count, line: countLine(value.count),
                            tone: value.count ? "warn" : "ok" } as const
    : { count: null, line: null, tone: "muted" } as const;
  if (data.failure !== null) {
    return { state: "failed", ...counted, detail: data.failure.why, note: value?.note ?? null };
  }
  if (value === null) return { state: "loading", ...none };
  if (value.problem !== null) {
    return { state: "problem", ...counted, detail: value.problem, note: value.note };
  }
  return { state: "up", ...counted, detail: null, note: value.note };
}

// -- an act's answer and its job --------------------------------------------------------

type Obj = Record<string, unknown>;

export { saidOf };

/** The job's status path a 202 gives, relative to loadout's page (`api/job?job=...`), or
 * null when the answer names none. */
export function jobPathOf(status: unknown): string | null {
  if (typeof status !== "string") return null;
  const path = status.replace(/^\/+/, "");
  return /^api\/job\?job=[A-Za-z0-9_-]+$/.test(path) ? path : null;
}

/** What an act's answer came to: a job to follow, or a line saying why not. */
export type Started = { kind: "job"; path: string } | { kind: "refused"; text: string };

/** What a plain act's answer came to. */
export function startedOf(status: number, data: unknown): Started {
  if (status === 202) {
    const path = jobPathOf(typeof data === "object" && data !== null ? (data as Obj).status : null);
    if (path !== null) return { kind: "job", path };
    return { kind: "refused", text: "loadout's page server started a job but gave no path to follow" };
  }
  if (status === 0) return { kind: "refused", text: "no answer from the shell" };
  return { kind: "refused", text: saidOf(data) ?? `loadout's page server answered ${status}` };
}

// -- a job ----------------------------------------------------------------------------------

/** The job the page follows, as it keeps it for the tab: its status path and its label. */
export interface Kept {
  path: string;
  label: string;
}

/** The job kept for the tab in `raw`, or null when there is none or it can't be read. */
export function parseKept(raw: string | null): Kept | null {
  if (raw === null) return null;
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof data !== "object" || data === null) return null;
  const path = jobPathOf((data as Obj).path);
  if (path === null) return null;
  const label = (data as Obj).label;
  return { path, label: typeof label === "string" && label !== "" ? label : "A change" };
}

/**
 * The line that ends a job the page follows when its read is refused: a 4xx other than a
 * 404 (which means loadout went away) says the job is no more to be read, with the
 * answer's own line. Null for any other failure, or none.
 */
export function jobRefusal(failure: Failure | null): string | null {
  if (failure === null || failure.status < 400 || failure.status >= 500 || failure.status === 404) {
    return null;
  }
  return failure.said ?? failure.why;
}
