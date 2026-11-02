// What loadout's page and drift block show for each state of their data, worked out with
// no DOM so the Node tests can read it. Every string here goes into the page as text.

import type { Drift, Failure } from "./feed.ts";

/** One route's data as its `Feed` holds it. */
export interface FeedState<V> {
  loading: boolean;
  gone: boolean;
  failure: Failure | null;
  value: V | null;
}

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
