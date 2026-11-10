// What the Health row says of the kept doctor run, worked out with no DOM so the tests can
// read it: the row's line for Home and the entries it shows when opened.

/** The kept doctor run, as `/m/ui/api/checks` and the topic `ui.checks` give it. Through
 * the gateway `detail`, `fix` and `error` are always null. */
export interface Checks {
  /** When the kept run ended; null when none is kept. */
  at: string | null;
  running: boolean;
  rows: Array<{ module: string; name: string; state: string; detail: string | null; fix: string | null }>;
  /** Why the run gave no rows. */
  error: string | null;
}

/** The row's line, as the `detail` of a `moxi-home-row` event. */
export interface HealthLine {
  parts: Array<{ text: string; heavy?: boolean }>;
  time?: string;
  needs?: number;
  opens?: boolean;
}

/** One failing or warning check, as the opened row lists it. */
export interface HealthEntry {
  module: string;
  name: string;
  failing: boolean;
  detail: string | null;
  fix: string | null;
}

/** On the needs scale: something is broken. */
export const BROKEN = 30;

/** `at` as the time of day ui shows; "" when it is no time. */
export function clock(at: string | null): string {
  const when = at === null ? NaN : Date.parse(at);
  if (Number.isNaN(when)) return "";
  return new Date(when).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function count(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** The checks that fail, then those that warn, each group in the doctor's order. A state
 * that is not ok, off or fail reads as a warning. */
export function healthEntries(value: Checks | null): HealthEntry[] {
  const out: HealthEntry[] = [];
  for (const failing of [true, false]) {
    for (const row of value?.rows ?? []) {
      if (row.state === "ok" || row.state === "off" || (row.state === "fail") !== failing) continue;
      out.push({ module: row.module, name: row.name, failing, detail: row.detail || null, fix: row.fix || null });
    }
  }
  return out;
}

/** Whether the kept run ended with no rows: it could not run. The gateway cuts its
 * `error`, and a run that worked always has rows. */
export function runFailed(value: Checks | null): boolean {
  return value !== null && value.at !== null && value.rows.length === 0;
}

/**
 * The Health row's line. A text the gateway cut is left out together with its ` · `, so
 * the phone reads the counts alone. `desk` is false on the phone, where a run that could
 * not run opens nothing: there is nothing to press.
 */
export function healthLine(value: Checks | null, desk: boolean): HealthLine {
  if (value === null || value.at === null) return { parts: [{ text: "Checking…" }] };
  const stamp = clock(value.at);
  const time = stamp ? { time: `checked ${stamp}` } : {};
  const tail = (text: string | null): Array<{ text: string }> => (text ? [{ text: ` · ${text}` }] : []);
  if (runFailed(value)) {
    return { parts: [{ text: "The checks could not run" }, ...tail(value.error)], ...time, needs: BROKEN,
             ...(desk ? { opens: true } : {}) };
  }
  const entries = healthEntries(value);
  const fails = entries.filter((entry) => entry.failing).length;
  const warns = entries.length - fails;
  if (entries.length === 0) return { parts: [{ text: "All checks pass" }], ...time };
  const warnings = count(warns, "warning", "warnings");
  if (fails === 0) return { parts: [{ text: warnings }, ...tail(entries[0].detail)], ...time, opens: true };
  return {
    parts: [{ text: count(fails, "check failing", "checks failing"), heavy: true },
            ...(warns ? [{ text: `, ${warnings}` }] : []), ...tail(entries[0].detail)],
    ...time, needs: BROKEN, opens: true,
  };
}

/** What the opened row's foot says of the rest: "" when no other check ran. */
export function healthRest(value: Checks | null): string {
  const others = (value?.rows.length ?? 0) - healthEntries(value).length;
  return others > 0 ? "Every other check passes" : "";
}
