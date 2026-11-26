// How long a page has waited for its first read, as `WaitClock` in client.ts reports it.
// DOM-free: the clock's readings and whether its timer has fired are given.
//
// The rule: once the timer armed for `dueMs` has fired, the wait is due, whatever a clock
// reads. A browser may fire a timer a little early by a coarsened clock (Firefox jitters
// `Date.now()`), and a render the timer asked for must then still find the wait due.

/** Milliseconds waited from `sinceMs` to `nowMs` (both monotonic), and never less than
 * `dueMs` once the timer armed for `dueMs` has fired. */
export function waitedMs(sinceMs: number, nowMs: number, dueMs: number, fired: boolean): number {
  const waited = Math.max(0, nowMs - sinceMs);
  return fired ? Math.max(waited, dueMs) : waited;
}
